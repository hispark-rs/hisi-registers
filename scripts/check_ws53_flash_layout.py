#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Source-only WS53 Flash mapping/readback contract; never access a device."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
HAL = 'src/drivers/drivers/hal/security_unified/hal_fapc/'
FILES = [HAL+'hal_fapc_reg.h', HAL+'hal_fapc.c',
         'src/drivers/chips/ws53/porting/security_unified/security_unified_porting.h',
         'src/bootloader/flashboot_ws53/startup/main.c',
         'src/drivers/drivers/hal/sfc/hal_sfc_v150.c',
         'src/drivers/drivers/hal/sfc/hal_sfc_v150_regs_def.h']


def require(ok, message):
    if not ok:
        raise ValueError(message)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', type=Path, required=True)
    p.add_argument('--svd', type=Path, required=True)
    p.add_argument('--cc', default='cc')
    args = p.parse_args()
    sdk = args.sdk.resolve()
    subprocess.run(['git', '-C', str(sdk), 'diff', '--exit-code', PIN, '--', *FILES],
                   check=True, stdout=subprocess.DEVNULL)
    tree = ET.parse(args.svd)
    sfc = tree.find("./peripherals/peripheral[name='SFC_CFG']")
    fama = tree.find("./peripherals/peripheral[name='FAMA_STATUS']")
    require(sfc is not None and fama is not None, 'missing Flash view')
    checks = []

    def reg(block, name, macro, count=1):
        node = block.find(f"registers/register[name='{name}']")
        require(node is not None, f'missing {name}')
        require(node.findtext('access') == 'read-only' and node.findtext('size') == '32',
                f'{name}: read-only 32-bit contract')
        require(node.find('resetValue') is None and node.find('resetMask') is None,
                f'{name}: unverified reset must be absent')
        require(int(node.findtext('dim', '1')) == count, f'{name}: wrong count')
        offset = int(node.findtext('addressOffset'), 0)
        if count > 1:
            require(node.findtext('dimIncrement') == '0x4', f'{name}: wrong stride')
        for index in range(count):
            checks.append(f'_Static_assert({macro.format(n=index)} == {offset+4*index}u, "{name}");')

    for name, macro, count in [
        ('FLASH_SIZE_STATUS', 'FLASH_BUS_FLASH_SIZE', 1),
        ('FLASH_BASE_STATUS[%s]', '(FLASH_BUS_BASE_ADDR_CS0+4*{n})', 2),
        ('FLASH_ALIAS_STATUS', 'FLASH_BUS_ALIAS_CS', 1),
        ('FAPC_START_STATUS[%s]', 'FAPC_CFG_START_ADDR({n})', 4),
        ('FAPC_END_STATUS[%s]', 'FAPC_CFG_END_ADDR({n})', 4),
        ('FAPC_MODE_STATUS', 'FAPC_DEC_AUTH_CFG', 1),
        ('FAPC_LOCK_STATUS', 'FAPC_ONE_WAY_LOCK', 1),
        ('FAPC_IV_START_STATUS[%s]', 'FAPC_IV_ACPU_START_ADDR({n})', 4),
    ]:
        reg(sfc, name, macro, count)
    field = sfc.find("registers/register[name='FLASH_BASE_STATUS[%s]']/fields/field[name='VALUE']")
    require(field is not None and field.findtext('bitOffset') == '16' and field.findtext('bitWidth') == '16', 'Flash base high halfword')
    source = (sdk / FILES[3]).read_text()
    definitions = dict(re.findall(r'^#define\s+(FAMA_REMAP_\w+)\s+(0x[0-9A-Fa-f]+)\b', source, re.M))
    base = int(fama.findtext('baseAddress'), 0)
    for name, define in [('SOURCE[%s]', 'FAMA_REMAP_SRC_BASE_ADDR'),
                         ('END[%s]', 'FAMA_REMAP_LEN_BASE_ADDR'),
                         ('DESTINATION[%s]', 'FAMA_REMAP_DST_BASE_ADDR')]:
        reg(fama, name, '('+str(int(definitions[define], 0)-base)+'+4*{n})', 4)
    require(base == int(definitions['FAMA_REMAP_SRC_BASE_ADDR'], 0), 'FAMA base')
    port = (sdk / FILES[2]).read_text()
    fapc_base = re.search(r'#define\s+FAPC_REG_BASE_ADDR\s+\((0x[0-9A-Fa-f]+)\)', port)
    require(fapc_base and int(fapc_base[1], 0) == int(sfc.findtext('baseAddress'), 0), 'FAPC base')
    hal = (sdk / FILES[1]).read_text()
    require('FAPC_REGION_MODE_NORMAL_VAL     0' in hal and 'FAPC_REGION_MODE_DEV_VAL        2' in hal,
            'mode values changed')
    require('((region) * 2U)' in hal and 'mode_cfg = fapc_reg_read(FAPC_DEC_AUTH_CFG)' in hal,
            'mode field stride/readback changed')
    with tempfile.TemporaryDirectory(prefix='ws53-flash-contract-') as temporary:
        work = Path(temporary)
        (work/'td_type.h').write_text('#include <stdint.h>\ntypedef uint32_t td_u32;\n')
        c = '#include '+json.dumps(str(sdk/FILES[0]))+'\n#include '+json.dumps(str(sdk/FILES[5]))+'\n'+'\n'.join(checks)+'''\n
_Static_assert(FAPC_REGION_NUM == 4, "region count");
_Static_assert(FLASH_BUS_BASE_ADDR_CS1 == FLASH_BUS_BASE_ADDR_CS0+4, "CS1");
int main(void) {
    fapc_alias_size a = {.u32=0}; a.bits.flash_size_cs1=15;
    if(a.u32 != 0xf00) return 1;
    fapc_addr_type b = {.u32=0}; b.bits.flash_apc_addr=0xffffff;
    if(b.u32 != 0xffffff00u) return 2;
    bus_base_addr_t d = {.d32=0}; d.b.bus_base_addr_high_cs=0xffff;
    if(d.d32 != 0xffff0000u) return 3;
    return 0;
}\n'''
        subprocess.run([args.cc, '-std=c11', '-Wall', '-Werror', '-I', str(work), '-x', 'c', '-', '-o', str(work/'check')],
                       input=c, text=True, check=True)
        subprocess.run([str(work/'check')], check=True)
    print(json.dumps({'sdk_commit': PIN, 'register_slot_checks': len(checks),
                      'readonly_mapping_views': True, 'hardware_verified': False,
                      'sources': {f: hashlib.sha256((sdk/f).read_bytes()).hexdigest() for f in FILES}}, indent=2))


if __name__ == '__main__':
    main()
