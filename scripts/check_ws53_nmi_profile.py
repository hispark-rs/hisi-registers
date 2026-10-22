#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Resolve the pinned SDK's NMI component and compile its original MMIO helpers.

This is a source/configuration contract, not a complete vendor firmware build,
a CPU NMI model, or evidence about the installed board image.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
NONOS = "src/kernel/non_os/non_os/"
ROM = "src/drivers/chips/ws53/rom/app/kernel/non_os/non_os/"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    files = [NONOS+x for x in ("non_os.h", "non_os_nmi.h", "non_os_nmi.c", "CMakeLists.txt")]
    files += [ROM+"non_os.h", ROM+"CMakeLists.txt", "src/build/cmake/build_component.cmake",
              "src/drivers/chips/ws53/porting/watchdog/watchdog_porting.c",
              "src/drivers/chips/ws53/include/acore/platform_core.h"]
    with tempfile.TemporaryDirectory(prefix="ws53-nmi-profile-") as tmp:
        work = Path(tmp)
        # The original resolver deletes __pycache__ at import. Run unchanged
        # modules from a temporary copy, never importing/writing the live SDK.
        isolated = work / "sdk"
        for f in (sdk / "src/build/config").rglob("*.py"):
            dest = isolated / f.relative_to(sdk)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
        for f in ("enviroment.py", "utils/build_utils.py", "utils/__init__.py"):
            original = sdk / "src/build/script" / f
            if original.exists():
                dest = isolated / original.relative_to(sdk)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, dest)
        child = '''import json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'src/build/config'),str(root/'src/build/script')]
from enviroment import TargetEnvironment
e=TargetEnvironment('ws53_liteos_app')
loaded=sorted({str(Path(m.__file__).relative_to(root)) for m in list(sys.modules.values()) if getattr(m,'__file__',None) and Path(m.__file__).is_relative_to(root)})
print(json.dumps({'ram':e.config['ram_component'],'rom':e.config['rom_component'],'modules':loaded}))
'''
        env = dict(os.environ)
        # SDK environment overrides must not redirect the isolated resolver.
        for key in ("FBB_SDK_DIR", "FBB_BUILD_ROOT_PATH"):
            env.pop(key, None)
        result = subprocess.run([sys.executable, "-B", "-c", child, str(isolated)],
                                check=True, text=True, capture_output=True, env=env)
        profile = json.loads(result.stdout.splitlines()[-1])
        files += profile.pop("modules")
        assert "non_os" in profile['ram']
        assert all(x not in profile['ram']+profile['rom'] for x in ('non_os_rom','non_os_ram'))
        assert "watchdog_port" in profile['ram']
        # Execute the ORIGINAL component selection macro, not a reimplementation.
        probe = work / "selection.cmake"
        probe.write_text('cmake_minimum_required(VERSION 3.20)\n'
            + f'include("{sdk}/src/build/cmake/build_component.cmake")\n'
            + 'set(RAM_COMPONENT "'+ ';'.join(profile['ram'])+'")\n'
            + 'set(ROM_COMPONENT "'+ ';'.join(profile['rom'])+'")\n'
            + '''foreach(COMPONENT_NAME non_os non_os_rom non_os_ram)
component_on_rom()
if(DEFINED COMPONENT_ON_ROM)
  file(APPEND "${CMAKE_CURRENT_LIST_DIR}/selected.txt" "${COMPONENT_NAME}:${COMPONENT_ON_ROM}\\n")
endif()
endforeach()
''')
        subprocess.run(["cmake", "-P", str(probe)], check=True, capture_output=True, text=True)
        assert (work/'selected.txt').read_text() == 'non_os:false\n'
        # Check header-export conditions of the pinned real build, including that
        # target libraries receive only active RAM/ROM component interfaces.
        cmake = (sdk/files[6]).read_text()
        assert 'elseif(DEFINED COMPONENT_ON_ROM)' in cmake
        assert 'target_include_directories(${COMPONENT_NAME}_interface INTERFACE ${PUBLIC_HEADER})' in cmake
        assert 'set(TARGETS_INTERFACES ${RAM_COMPONENT} ${ROM_COMPONENT})' in cmake
        assert 'target_link_libraries(${COMPONENT_NAME} PRIVATE ${TARGETS_INTERFACES})' in cmake
        for directory in (NONOS, ROM):
            cm = (sdk/directory/'CMakeLists.txt').read_text()
            assert re.search(r'set\(PUBLIC_HEADER\s+\$\{CMAKE_CURRENT_SOURCE_DIR\}\s*\)', cm)
        # There are only these two candidate SDK headers; arbitrary user include
        # paths are outside this fixed product-profile check.
        candidates = sorted(str(f.relative_to(sdk)) for f in (sdk/'src').rglob('non_os.h'))
        assert candidates == sorted([NONOS+'non_os.h',ROM+'non_os.h'])
        selected = sdk/NONOS
        stubs = work/'stubs'; stubs.mkdir()
        (stubs/'core.h').write_text('#include <stdint.h>\n#include <stdbool.h>\n#define BIT(n) (1u << (n))\n#define cassert(x,n) _Static_assert(x, #n)\n#define CORE 0\n#define APPS 0\n')
        (stubs/'memory_config.h').write_text('')
        (stubs/'soc_osal.h').write_text('''#include <stdint.h>
extern unsigned locks, restores;
static inline uint32_t osal_irq_lock(void) { locks++; return 0xa5; }
static inline void osal_irq_restore(uint32_t v) { if(v==0xa5) restores++; }
''')
        (stubs/'chip_io.h').write_text('''#include <stdint.h>
#define NMI_CTL_REG_BASE_ADDR 0x52000700u
extern uint16_t words[5];
extern uintptr_t last_read, last_write;
static inline unsigned slot(uintptr_t a) { return (a-NMI_CTL_REG_BASE_ADDR)/2; }
static inline uint16_t reg16(uintptr_t a) { last_read=a; return words[slot(a)]; }
static inline void reg16_setbit(uintptr_t a,unsigned b) { last_write=a; words[slot(a)]|=1u<<b; }
static inline void reg16_clrbit(uintptr_t a,unsigned b) { last_write=a; words[slot(a)]&=~(1u<<b); }
''')
        source = work/'test.c'
        source.write_text('''#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include "non_os.h"
uint16_t words[5]; uintptr_t last_read,last_write; unsigned locks,restores;
int main(void) {
_Static_assert(NMI_CWDT==1, "selected watchdog NMI bit");
words[0]=0xa5a5; non_os_nmi_config(NMI_CWDT,true);
assert(words[0]==0xa5a7 && last_write==0x52000700 && last_read==0x52000700);
non_os_nmi_config(NMI_CWDT,false);
assert(words[0]==0xa5a5 && locks==2 && restores==2);
words[2]=0x12; words[4]=0x24;
assert(non_os_get_nmi_raw_status()==0x12 && last_read==0x52000704);
assert(non_os_get_nmi_mask_status()==0x24 && last_read==0x52000708);
words[4]=2; assert(non_os_get_nmi_type()==NMI_CWDT);
words[4]=4; assert(non_os_get_nmi_type()==NMI_XIP_CTRL);
words[4]=8; assert(non_os_get_nmi_type()==NMI_XIP_CACHE);
words[4]=16; assert(non_os_get_nmi_type()==NMI_MDMA);
words[4]=32; assert(non_os_get_nmi_type()==NMI_SMDMA);
words[4]=0; assert(non_os_get_nmi_type()==NMI_NONE);
return 0;
}
''')
        binary = work/'test'
        subprocess.run([args.cc,'-std=c11','-Wall','-Werror','-I',str(selected),'-I',str(stubs),
                        '-MMD','-MF',str(work/'deps.d'),str(source),str(selected/'non_os_nmi.c'),'-o',str(binary)],check=True)
        deps=(work/'deps.d').read_text()
        assert str(selected/'non_os.h') in deps and str(sdk/ROM/'non_os.h') not in deps
        subprocess.run([str(binary)],check=True)
        port = (sdk/files[7]).read_text()
        assert 'non_os_nmi_config(NMI_CWDT, true);' in port
        platform = (sdk/files[8]).read_text()
        assert re.search(r'#define\s+NMI_CTL_REG_BASE_ADDR\s+0x52000700\b',platform)
    files=sorted(set(files))
    subprocess.run(['git','-C',str(sdk),'diff','--exit-code',PIN,'--',*files],check=True,stdout=subprocess.DEVNULL)
    tree = ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    crg = tree.find("./peripherals/peripheral[name='M_CRG']")
    assert int(crg.findtext('baseAddress'), 0) == 0x52000000
    for name, offset, access in (('NMI_ENABLE',0x700,'read-write'),
                                ('NMI_RAW_STATUS',0x704,'read-only'),
                                ('NMI_MASKED_STATUS',0x708,'read-only')):
        reg = crg.find(f"registers/register[name='{name}']")
        assert int(reg.findtext('addressOffset'), 0) == offset
        assert reg.findtext('size') == '16' and reg.findtext('access') == access
        assert reg.find('resetValue') is None and reg.find('readAction') is None
        assert [(f.findtext('name'),int(f.findtext('bitOffset')),int(f.findtext('bitWidth')))
                for f in reg.findall('fields/field')] == [
                    ('WATCHDOG',1,1),('XIP_CONTROL',2,1),('XIP_CACHE',3,1),('MDMA',4,1),('SMDMA',5,1)]
    print(json.dumps({'sdk_commit':PIN,'profile':'ws53_liteos_app','selected_component':'non_os',
                      'excluded_components':['non_os_rom','non_os_ram'],'selected_header':NONOS+'non_os.h',
                      'watchdog_nmi_bit':1,'base':'0x52000700','access_bits':16,
                      'register_offsets':{'enable':0,'raw_status':4,'masked_status':8},
                      'svd_register_checks':3,'svd_field_checks':15,
                      'compiled_sdk_checks':['enable preserves neighbor bits','disable restores bit','irq save/restore',
                         'raw/masked read addresses','five NMI source classifications','no-source classification'],
                      'source_sha256':{f:hashlib.sha256((sdk/f).read_bytes()).hexdigest() for f in files},
                      'complete_vendor_build':False,'installed_firmware_verified':False,
                      'cpu_nmi_delivery_verified':False,'hardware_verified':False},indent=2))


if __name__=='__main__':
    main()
