#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Check SDK MSUB word accesses on anonymous host RAM; no device/MMIO access.

The optional SDK check requires Linux/x86-64 MAP_32BIT because the original
reg(base, offset) macro narrows addresses to uint32_t. It neither tests silicon
halfword writes nor establishes DMA gating, bus drain or clock ownership.
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
CLOCK = 'src/drivers/chips/ws53/include/clock_recover.h'
ENUM = 'src/drivers/chips/ws53/include/hal_clocks.h'
IO = 'src/middleware/utils/common_headers/chip_io.h'
ACORE = 'src/drivers/chips/ws53/clock_recover/acore/acore_clock_recover.c'
COMMON = 'src/drivers/chips/ws53/clock_recover/clock_recover.c'
PM = 'src/middleware/chips/ws53/pm/pm_sleep/pm_sleep_porting.c'
FIELDS = {
    'CPU_ENABLE': 'MCPU_CLKEN', 'CPU_HCLK_ENABLE': 'MCPU_HCLKEN',
    'MCU_BUS_ENABLE': 'MBUS_CLKEN', 'MDMA_ENABLE': 'MDMA_CLKEN',
    'SDMA_ENABLE': 'SDMA_CLKEN', 'COMMON_BUS_ENABLE': 'COM_BUS_CLKEN',
    'MCU_CORE_CHANNEL': 'MCU_CORE_CH_SEL', 'COMMON_BUS_CHANNEL': 'COM_BUS_CH_SEL',
    'HIFI_CHANNEL': 'HIFI_CH_SEL', 'CODEC_CHANNEL': 'CODEC_CH_SEL',
}


def require(ok, why):
    if not ok:
        raise ValueError(why)


def function(source, name):
    match = re.search(r'^(?:static )?[\w* ]+\b' + name + r'\s*\([^;]*?\)\s*\{', source, re.M)
    require(match is not None, 'missing original helper: ' + name)
    depth, end = 1, match.end()
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end]


def validate_layout(svd):
    tree = ET.parse(svd)
    block = tree.find("./peripherals/peripheral[name='M_CRG']")
    require(block is not None and int(block.findtext('baseAddress'), 0) == 0x52000000,
            'MSUB base changed')
    result = {}
    for name, offset in (('MSUB_NORMAL', 0x3e0), ('MSUB_SLEEP', 0x3e4)):
        half = block.find(f"registers/register[name='{name}']")
        word = block.find(f"registers/register[name='{name}_WORD']")
        require(half is not None and word is not None, name + ' access views missing')
        require(half.findtext('size') == '16' and word.findtext('size') == '32', name + ' widths')
        require(word.findtext('alternateRegister') == name, name + ' alternateRegister')
        for reg in (half, word):
            require(int(reg.findtext('addressOffset'), 0) == offset, name + ' offset')
            require(reg.findtext('access') == 'read-write', name + ' access')
            require(all(reg.find(tag) is None for tag in ('resetValue', 'resetMask', 'readAction', 'modifiedWriteValues')),
                    name + ' invented reset/side effects')
        require(ET.tostring(half.find('fields')) == ET.tostring(word.find('fields')),
                name + ' fields differ across aliases')
        fields = {f.findtext('name'): int(f.findtext('bitOffset')) for f in word.findall('fields/field')}
        expected = set(FIELDS) - ({'MCU_BUS_ENABLE'} if name == 'MSUB_NORMAL' else set())
        require(set(fields) == expected, name + ' field set')
        require(all(f.findtext('bitWidth') == '1' and f.findtext('access') == 'read-write'
                    for f in word.findall('fields/field')), name + ' field width/access')
        require(all(bit < 16 for bit in fields.values()), name + ' invented upper-half fields')
        result[name] = dict(address=0x52000000 + offset, fields=fields)
    return result


def verify(sdk, svd, cc):
    layout = validate_layout(svd)
    sources = {}
    for path in (CLOCK, ENUM, IO, ACORE, COMMON, PM):
        data = (sdk/path).read_bytes()
        require(data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{path}']),
                'SDK source differs from pin: ' + path)
        sources[path] = data.decode('utf-8-sig')
    for name, sdk_name in (('MSUB_NORMAL', 'MSUB_NOR_CFG'), ('MSUB_SLEEP', 'MSUB_SLP_CFG')):
        match = re.search(r'^#define\s+' + sdk_name + r'\s+(0x[\da-fA-F]+)\s*$', sources[CLOCK], re.M)
        require(match is not None and int(match[1], 0) == layout[name]['address'], 'SDK MSUB address')
    pm_access = 'reg32_setbit(MSUB_SLP_CFG, 0x0);'
    require(sources[PM].count(pm_access) == 2, 'SDK PM word-access sites changed')
    enums = re.findall(r'typedef enum\s*\{[^{}]*\}\s*hal_clocks_sub_(?:nor|slp)_cfg_t;', sources[ENUM])
    enums = [e for e in enums if 'HAL_CLOCKS_MCPU_' in e]
    require(len(enums) == 2, 'SDK application normal/sleep enums changed')
    code = [r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdio.h>
#include <sys/mman.h>
''', '#include ' + json.dumps(str(sdk/IO)), r'''
_Static_assert(sizeof(reg(0, 0)) == 4, "original reg access must be word");
_Static_assert(sizeof(reg32(0)) == 4, "original reg32 access must be word");
static volatile uint32_t *host;
#define MSUB_NOR_CFG ((uintptr_t)&host[0])
#define MSUB_SLP_CFG ((uintptr_t)&host[1])
#define COM_BUS_CR_CH1 ((uintptr_t)&host[2])
#define MCU_CORE_CR_CH0 0
#define MCU_CORE_CR_CH1 1
#define DELAY_1_US 1
static unsigned delays, selected, switches;
static void switch_ch_clock(bool pll, uint32_t ch) {
    (void)pll; selected = ch; switches++;
}
static void cpu32m_udelay(unsigned us) {
    assert(us == 1); assert(!(host[0] & 128) && !(host[1] & 128));
    assert(host[2] == (delays ? 0x59 : 0)); delays++;
}
''', *enums,
        function(sources[ACORE], 'switch_clock_from_ch0_to_ch1'),
        function(sources[ACORE], 'switch_clock_from_ch1_to_ch0'),
        function(sources[ACORE], 'ccore_switch_xo_clock'),
        function(sources[COMMON], 'ccore_switch_pll_clock'),
        'static void pm_word_access(void) { ' + pm_access + ' }', r'''
int main(void) {
    void *p = mmap(NULL, 4096, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS|MAP_32BIT, -1, 0);
    assert(p != MAP_FAILED && (uintptr_t)p <= UINT32_MAX - 4096); host = p;
    const uint32_t seeds[] = {0, 0xffffffff, 0xa5a55a5a, 0x3ff, 0x80000000, 0xffff0000};
    unsigned cases = 0;
    for (unsigned n = 0; n < sizeof(seeds)/sizeof(seeds[0]); n++) {
        uint32_t a = seeds[n], b = ~a;
        for (unsigned which = 0; which < 7; which++) {
            host[0]=a; host[1]=b; host[2]=0x11223344; host[3]=0x55aa33cc;
            delays=selected=switches=0;
            switch (which) {
            case 0: case 1:
                switch_clock_from_ch0_to_ch1(which != 0);
                assert(selected==1 && switches==1);
                assert(host[0]==(a|64) && host[1]==(b|64)); break;
            case 2: case 3:
                switch_clock_from_ch1_to_ch0(which != 2);
                assert(selected==0 && switches==1);
                assert(host[0]==(a&~64u) && host[1]==(b&~64u)); break;
            case 4:
                ccore_switch_xo_clock();
                assert(host[0]==(a&~128u) && host[1]==(b&~128u)); break;
            case 5:
                ccore_switch_pll_clock();
                assert(host[0]==(a|128) && host[1]==(b|128) && delays==2); break;
            case 6:
                pm_word_access(); assert(host[0]==a && host[1]==(b|1)); break;
            }
            assert(host[3]==0x55aa33cc);
            if (which != 5) { assert(host[2]==0x11223344); }
            cases++;
        }
    }
    printf("CASES %u\n", cases);
''']
    for name, profile in layout.items():
        suffix = 'NOR' if name == 'MSUB_NORMAL' else 'SLP'
        for field, sdk_field in FIELDS.items():
            if field in profile['fields']:
                code.append(f'printf("FIELD {name} {field} %u\\n", (unsigned)HAL_CLOCKS_{sdk_field}_{suffix});')
    code.append('assert(munmap(p, 4096)==0); return 0; }')
    with tempfile.TemporaryDirectory(prefix='ws53-msub-') as tmp:
        path = Path(tmp); (path/'check.c').write_text('\n'.join(code))
        subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', str(path/'check.c'), '-o', str(path/'check')], check=True)
        output = subprocess.check_output([str(path/'check')], text=True)
    lines = output.splitlines()
    require(lines[0] == 'CASES 42', 'SDK helper case count')
    observed = {}
    for line in lines[1:]:
        kind, name, field, position = line.split()
        require(kind == 'FIELD' and (name, field) not in observed, 'unexpected/duplicate SDK enum result')
        observed[name, field] = int(position)
    expected = {(name, field): bit for name, p in layout.items() for field, bit in p['fields'].items()}
    require(observed == expected, 'SVD fields disagree with compiled SDK enums')
    return dict(passed=True, evidence_class='SDK-source-host-RAM', sdk_commit=PIN,
                compiler=subprocess.check_output([cc, '--version'], text=True).splitlines()[0],
                sdk_sources={p: hashlib.sha256((sdk/p).read_bytes()).hexdigest() for p in sources},
                svd_sha256=hashlib.sha256(svd.read_bytes()).hexdigest(), layout=layout,
                helper_cases=42, field_positions=len(expected),
                hardware_access=False, halfword_write_qualified=False,
                sdma_gate_clearing_verified=False, physical_bus_drain_proven=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--svd', type=Path, default=Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    print(json.dumps(verify(args.sdk.resolve(), args.svd.resolve(), args.cc), indent=2))


if __name__ == '__main__':
    main()
