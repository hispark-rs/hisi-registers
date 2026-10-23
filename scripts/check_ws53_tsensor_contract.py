#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned WS53 temperature C port over host stubs; no physical MMIO."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
SOURCE = 'src/drivers/chips/ws53/porting/tsensor/tsensor_porting.c'
SELECTION = ('src/drivers/chips/ws53/porting/tsensor/CMakeLists.txt',
             'src/build/config/target_config/ws53/target_config.py',
             'src/application/ws53/ws53_application/main.c')


def require(value, why):
    if not value:
        raise ValueError(why)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', type=Path, required=True)
    p.add_argument('--cc', default='cc')
    p.add_argument('--curve-output', type=Path)
    a = p.parse_args()
    sdk = a.sdk.resolve()
    source = subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{SOURCE}'])
    require(source == (sdk/SOURCE).read_bytes(), 'local SDK port differs from pin')
    inputs = {SOURCE: source}
    for name in SELECTION:
        inputs[name] = subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{name}'])
        require(inputs[name] == (sdk/name).read_bytes(), f'local SDK selection differs: {name}')
    require(b'"tsensor_port"' in inputs[SELECTION[0]] and b'/tsensor_porting.c' in inputs[SELECTION[0]], 'wrong source component')
    require(b"'tsensor_port'" in inputs[SELECTION[1]] and b'uapi_tsensor_init();' in inputs[SELECTION[2]], 'application selection changed')
    svd = ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    sensor = svd.find("./peripherals/peripheral[name='TSENSOR']")
    require(sensor is not None and int(sensor.findtext('baseAddress'), 0) == 0x59006300, 'base differs')
    require(not sensor.findall('interrupt'), 'IRQ has no source evidence')
    expected = {'START': (0, 'write-only'), 'CONTROL': (4, 'read-write'),
                'AUTO_STATUS': (8, 'read-only'), 'SAMPLE_TIME': (16, 'write-only'),
                'AUTO_REFRESH_PERIOD': (48, 'write-only'), 'AUTO_REFRESH_CONFIG': (52, 'write-only')}
    regs = sensor.findall('registers/register')
    require(len(regs) == len(expected), 'unexpected register count')
    for reg in regs:
        require((int(reg.findtext('addressOffset'), 0), reg.findtext('access')) == expected[reg.findtext('name')], 'register contract differs')
        require(reg.findtext('size') == '32' and reg.find('resetValue') is None, 'unsupported width/reset')
    status = sensor.find("./registers/register[name='AUTO_STATUS']")
    require([(f.findtext('name'), f.findtext('bitOffset'), f.findtext('bitWidth')) for f in status.findall('fields/field')]
            == [('READY', '1', '1'), ('CODE', '2', '10')], 'status must be one atomic snapshot with no write/clear field')
    with tempfile.TemporaryDirectory(prefix='ws53-tsensor-source-') as temp:
        work = Path(temp)
        (work/'common_def.h').write_text('''#pragma once
#include <stdint.h>
#include <stdbool.h>
typedef uint32_t errcode_t;
#define ERRCODE_SUCC 0
static uint32_t readl(uint32_t addr);
static void writel(uint32_t addr, uint32_t value);
''')
        for name in ['tcxo.h', 'chip_io.h', 'debug_print.h', 'tsensor_porting.h']:
            (work/name).write_text('#pragma once\n')
        (work/'check.c').write_text('''#include <assert.h>
#include <stdio.h>
''' + '#include ' + json.dumps(str(sdk/SOURCE)) + '''
static unsigned writes, reads, ready_after, code;
static uint32_t control, addr_trace[5], value_trace[5];
static void writel(uint32_t addr, uint32_t value) {
    assert(writes < 5); addr_trace[writes] = addr; value_trace[writes++] = value;
    if (addr == TSENSOR_CTRL) control = value;
}
static uint32_t readl(uint32_t addr) {
    if (addr == TSENSOR_CTRL) return control;
    assert(addr == TSENSOR_AUTO_STS); reads++;
    assert(reads <= ready_after); /* fail fast instead of running the SDK infinite wait */
    return (code << 2) | (reads == ready_after ? 2 : 0);
}
static void check_init(void) {
    const uint32_t addrs[] = {0x59006330, 0x59006304, 0x59006310, 0x59006334, 0x59006300};
    const uint32_t values[] = {160, 3, 2, 0, 1};
    assert(writes == 5);
    for (unsigned i = 0; i < 5; i++) { assert(addr_trace[i] == addrs[i] && value_trace[i] == values[i]); }
}
int main(void) {
    assert(uapi_tsensor_init() == 0); check_init();
    writes = reads = 0; control = 0; ready_after = 3; code = 118;
    int8_t temp;
    assert(uapi_tsensor_get_current_temp(&temp) == 0 && temp == -40 && reads == 3); check_init();
    /* Nonzero CONTROL is blindly trusted by SDK; the Rust HAL must reject unknown profiles. */
    control = 0xdead; writes = reads = 0; ready_after = 1; code = 933;
    assert(uapi_tsensor_get_current_temp(&temp) == 0 && temp == 125 && writes == 0);
    control = 3;
    for (code = 0; code < 1024; code++) {
        reads = 0; assert(uapi_tsensor_get_current_temp(&temp) == 0 && reads == 1 && writes == 0);
        printf("%d\\n", temp);
    }
    return 0;
}
''')
        subprocess.run([a.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(work), str(work/'check.c'), '-o', str(work/'check')], check=True)
        curve = subprocess.check_output([str(work/'check')], timeout=5)
    values = [int(x) for x in curve.splitlines()]
    require(len(values) == 1024, 'missing source results')
    def trunc(x, d):
        return (abs(x)//d) * (-1 if x < 0 else 1)
    require(values == [min(trunc(trunc((code-118)*16500, 815)-4000, 100), 125) for code in range(1024)], 'C conversion differs')
    if a.curve_output:
        a.curve_output.write_bytes(curve)
    print(json.dumps({'sdk_commit': PIN, 'source': SOURCE, 'source_sha256': hashlib.sha256(source).hexdigest(),
        'input_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()},
        'registers': 6, 'source_code_cases': 1024, 'curve_sha256': hashlib.sha256(curve).hexdigest(),
        'observations': ['five writes in source order', 'CONTROL zero initializes', 'SDK trusts any nonzero CONTROL',
                         'one coherent READY/code read per poll', 'SDK unbounded poll; no delay',
                         'C signed truncation then upper-only saturation', 'no stop/IRQ/status-clear source evidence'],
        'reference_code_endpoints': [118, 933], 'sdk_output_at_endpoints': [-40, 125],
        'sdk_output_at_code_extremes': [values[0], values[-1]], 'hardware_accessed': False}, indent=2))


if __name__ == '__main__':
    main()
