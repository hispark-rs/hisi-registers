#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Check WS53 SFC clock source accesses on anonymous host RAM, never hardware.

Executes two original SDK function bodies with the original chip_io.h macros.
Only their register address and delay functions are host substitutes. Linux
MAP_32BIT is required because the original reg macro narrows pointers to u32.
This checks source encodings/preservation, not clock timing or safe switching.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
COMMON = 'src/drivers/chips/ws53/clock_recover/clock_recover.c'
BOOT = 'src/bootloader/flashboot_ws53/startup/main.c'
CLOCK = 'src/drivers/chips/ws53/include/clock_recover.h'
IO = 'src/middleware/utils/common_headers/chip_io.h'
ACORE = 'src/drivers/chips/ws53/clock_recover/acore/acore_clock_recover.c'
NAME = 'XIP_SFC_CLOCK'
FIELDS = {'ENABLE': (0, 1), 'SOURCE_CONFIG': (1, 3)}


def require(ok, why):
    if not ok:
        raise ValueError(why)


def function(source, name):
    match = re.search(r'^(?:static )?[\w* ]+\b' + name + r'\s*\([^;]*?\)\s*\{', source, re.M)
    require(match is not None, 'missing original helper: ' + name)
    depth, end = 1, match.end()
    while depth:
        require(end < len(source), 'unterminated original helper: ' + name)
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end]


def define(source, name):
    match = re.search(r'^#define\s+' + name + r'\s+(0x[\da-fA-F]+|\d+)\s*$', source, re.M)
    require(match is not None, 'missing original constant: ' + name)
    return match[0], int(match[1], 0)


def validate_layout(svd):
    tree = ET.parse(svd)
    block = tree.find("./peripherals/peripheral[name='M_CRG']")
    require(block is not None and int(block.findtext('baseAddress'), 0) == 0x52000000,
            'SFC clock block/base changed')
    regs = {r.findtext('name'): r for r in block.findall('registers/register')}
    require(NAME in regs and NAME + '_WORD' in regs, 'SFC clock access views missing')
    for name, width in ((NAME, 16), (NAME + '_WORD', 32)):
        reg = regs[name]
        require(int(reg.findtext('addressOffset'), 0) == 0x570, name + ' offset')
        require(reg.findtext('size') == str(width), name + ' width')
        require(reg.findtext('access') == 'read-write', name + ' access')
        require(reg.findtext('alternateRegister') == (NAME if width == 32 else None),
                name + ' alternateRegister')
        for tag in ('resetValue', 'resetMask', 'readAction', 'modifiedWriteValues',
                    'writeConstraint', 'enumeratedValues'):
            require(reg.find('.//' + tag) is None, name + ' invented reset/side effect/value domain: ' + tag)
        fields = reg.findall('fields/field')
        require(len(fields) == 2, name + ' unexpected field count')
        observed = {f.findtext('name'): (int(f.findtext('bitOffset')), int(f.findtext('bitWidth')))
                    for f in fields}
        require(observed == FIELDS, name + ' fields')
        require(all(f.findtext('access') == 'read-write' for f in fields), name + ' field access')
    require(ET.tostring(regs[NAME].find('fields')) == ET.tostring(regs[NAME + '_WORD'].find('fields')),
            'SFC clock aliases diverge')
    qspi = regs.get('XIP_QSPI_CLOCK')
    require(qspi is not None and int(qspi.findtext('addressOffset'), 0) == 0x574,
            'distinct QSPI neighbor missing/moved')
    return dict(address=0x52000570, fields=FIELDS, access_widths=[16, 32],
                distinct_qspi_address=0x52000574)


def verify(sdk, svd, cc):
    layout = validate_layout(svd)
    sources = {}
    for path in (COMMON, BOOT, CLOCK, IO, ACORE):
        data = (sdk/path).read_bytes()
        require(data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{path}']),
                'SDK source differs from pin: ' + path)
        sources[path] = data.decode('utf-8-sig')
    for path, name in ((COMMON, 'XIP_SFC_CR'), (BOOT, 'AON_XIP_SFC_CR')):
        require(define(sources[path], name)[1] == layout['address'], 'SDK SFC address changed')
    originals = {'sfc_switch_clock': function(sources[COMMON], 'sfc_switch_clock'),
                 'boot_flash_clk_init': function(sources[BOOT], 'boot_flash_clk_init')}
    code = [r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdio.h>
#include <sys/mman.h>
''', '#include ' + json.dumps(str(sdk/IO)),
        define(sources[CLOCK], 'DELAY_1_US')[0], define(sources[CLOCK], 'DATA_WIDTH_3')[0], r'''
/* sizeof observes original macro lvalue types without dereferencing address 0. */
_Static_assert(sizeof(reg_clrbit(0, 0, POS_0)) == 4, "enable clear uses word RMW");
_Static_assert(sizeof(reg_setbit(0, 0, POS_0)) == 4, "enable set uses word RMW");
_Static_assert(sizeof(reg16_setbits(0, POS_1, DATA_WIDTH_3, 6)) == 2, "config uses halfword RMW");
_Static_assert(sizeof(reg16_clrbits(0, POS_1, DATA_WIDTH_3)) == 2, "config clear uses halfword RMW");
_Static_assert(sizeof(readl(0)) == 4, "readback uses word");
static volatile uint32_t *host;
#define XIP_SFC_CR ((uintptr_t)&host[1])
#define AON_XIP_SFC_CR XIP_SFC_CR
static unsigned delays;
static uint32_t at_delay[3];
static void record_delay(unsigned us) {
    assert(us == 1 && delays < 3);
    at_delay[delays++] = host[1];
}
static void cpu32m_udelay(unsigned us) { record_delay(us); }
static void udelay(unsigned us) { record_delay(us); }
''', *originals.values(), r'''
int main(void) {
    void *p = mmap(NULL, 4096, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS|MAP_32BIT, -1, 0);
    assert(p != MAP_FAILED && (uintptr_t)p <= UINT32_MAX - 4096); host = p;
    const uint32_t upper[] = {0, 0xfffffff0, 0xa5a55a50, 0x80000000, 0xffff0000, 0x0000fff0};
    unsigned cases = 0, delay_points = 0;
    for (unsigned s = 0; s < sizeof(upper)/sizeof(upper[0]); s++) {
        for (uint32_t low = 0; low < 16; low++) {
            for (unsigned which = 0; which < 3; which++) {
                uint32_t before = upper[s] | low;
                host[0] = 0x11223344; host[1] = before; host[2] = 0xa55a66cc;
                delays = 0;
                if (which < 2) {
                    sfc_switch_clock(which == 1);
                    assert(delays == 1);
                    assert(at_delay[0] == ((before & ~15u) | (which == 1 ? 12u : 0u)));
                } else {
                    boot_flash_clk_init();
                    assert(delays == 3);
                    assert(at_delay[0] == (before & ~1u));
                    assert(at_delay[1] == ((before & ~15u) | 12u));
                    assert(at_delay[2] == ((before & ~15u) | 13u));
                }
                uint32_t expected = (before & ~15u) | (which == 0 ? 1u : 13u);
                assert(host[1] == expected);
                assert((host[1] & ~15u) == (before & ~15u));
                assert(host[0] == 0x11223344 && host[2] == 0xa55a66cc);
                delay_points += delays;
                cases++;
            }
        }
    }
    printf("CASES %u DELAY_POINTS %u ENABLE_MASK %u SOURCE_MASK %u\n", cases, delay_points,
           (unsigned)(1u << POS_0), (unsigned)(((1u << DATA_WIDTH_3) - 1) << POS_1));
    assert(munmap(p, 4096) == 0);
    return 0;
}
''']
    with tempfile.TemporaryDirectory(prefix='ws53-sfc-clock-') as tmp:
        path = Path(tmp)
        source = '\n'.join(code)
        (path/'check.c').write_text(source)
        subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-fno-strict-aliasing',
                        str(path/'check.c'), '-o', str(path/'check')], check=True)
        output = subprocess.check_output([str(path/'check')], text=True)
    require(output == 'CASES 288 DELAY_POINTS 480 ENABLE_MASK 1 SOURCE_MASK 14\n',
            'SDK helper output changed: ' + output)
    require(FIELDS['ENABLE'] == (0, 1) and FIELDS['SOURCE_CONFIG'] == (1, 3), 'field masks mismatch')
    return dict(passed=True, evidence_class='SDK-source-host-RAM', sdk_commit=PIN,
                compiler=subprocess.check_output([cc, '--version'], text=True).splitlines()[0],
                sdk_sources={p: hashlib.sha256((sdk/p).read_bytes()).hexdigest() for p in sources},
                original_helpers={n: hashlib.sha256(body.encode()).hexdigest() for n, body in originals.items()},
                svd_sha256=hashlib.sha256(svd.read_bytes()).hexdigest(), layout=layout,
                helper_cases=288, delay_stub_observation_points=480, macro_access_type_assertions=5,
                field_masks={'ENABLE': 1, 'SOURCE_CONFIG': 14},
                shared_memory_host_only=True, hardware_access=False, physical_delays_verified=False,
                physical_access_widths_verified=False, frequency_formula_verified=False,
                reset_values_verified=False, safe_live_switching_verified=False,
                ownership_verified=False, physical_bus_drain_proven=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--svd', type=Path, default=Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    print(json.dumps(verify(args.sdk.resolve(), args.svd.resolve(), args.cc), indent=2))


if __name__ == '__main__':
    main()
