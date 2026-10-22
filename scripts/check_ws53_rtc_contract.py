#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Pinned WS53 RTC v150 source/config oracle, host RAM only, not ROM or silicon."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
HAL = "src/drivers/drivers/hal/rtc_unified/"
PORT = "src/drivers/chips/ws53/porting/rtc_unified/"
CONFIG = "src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config"
PLATFORM = "src/drivers/chips/ws53/include/acore/platform_core.h"
IRQ = "src/drivers/chips/ws53/include/acore/chip_core_irq.h"


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True, type=Path)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    paths = [HAL + f for f in ('hal_rtc.c', 'hal_rtc.h', 'v150/hal_rtc_v150.c',
             'v150/hal_rtc_v150.h', 'v150/hal_rtc_v150_regs_def.h', 'v150/hal_rtc_v150_regs_op.h')]
    paths += [PORT+'rtc_porting.c', PORT+'rtc_porting.h', CONFIG, PLATFORM, IRQ,
              'src/drivers/drivers/driver/rtc_unified/rtc.c',
              'src/build/config/target_config/ws53/target_config.py']
    inputs = {}
    for path in paths:
        data = (sdk / path).read_bytes()
        require(data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{path}']),
                f'SDK differs from pin: {path}')
        inputs[path] = data
    config = inputs[CONFIG].decode()
    config_values = dict(re.findall(r'^(CONFIG_RTC_\w+)=(\w+)$', config, re.M))
    expected = {'CONFIG_RTC_MAX_NUM': '2', 'CONFIG_RTC_0_WIDTH_64': '0',
                'CONFIG_RTC_1_WIDTH_64': '0', 'CONFIG_RTC_CLOCK_VALUE': '32768',
                'CONFIG_RTC_USING_V150': 'y', 'CONFIG_RTC_STOP_DELAY_SUPPORT': 'y',
                'CONFIG_RTC_START_DELAY_SUPPORT': 'y', 'CONFIG_RTC_STOP_DELAY_USING_TCXO': 'y',
                'CONFIG_RTC_START_DELAY_USING_TCXO': 'y'}
    require(all(config_values.get(k) == v for k, v in expected.items()), 'wrong RTC application profile')
    require('# CONFIG_RTC_SUPPORT_LPM is not set' in config, 'unexpected LPM profile')
    port = inputs[PORT+'rtc_porting.c'].decode()
    require('RTC_0_BASE_ADDR,\n    RTC_1_BASE_ADDR,' in port, 'RTC instance table changed')
    target = inputs[paths[-1]].decode()
    require("'rtc_unified_rom', 'hal_rtc_unified_rom', 'rtc_unified_port_rom'" in target,
            'RTC application ROM component declarations changed')
    platform = inputs[PLATFORM].decode()
    definitions = '\n'.join(re.findall(r'^#define\s+RTC(?:_[01])?_BASE_ADDR\s+.*$', platform, re.M))
    svd = ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    source = ['#include <stddef.h>', '#include <assert.h>', definitions,
              '#include '+json.dumps(str(sdk/IRQ)),
              '#include '+json.dumps(str(sdk/(HAL+'hal_rtc.c'))),
              '#include '+json.dumps(str(sdk/(HAL+'v150/hal_rtc_v150.c')))]
    for index in (0, 1):
        p = svd.find(f"./peripherals/peripheral[name='RTC{index}']")
        base = int(p.findtext('baseAddress'), 0)
        irq = int(p.findtext('interrupt/value'))
        source += [f'_Static_assert(RTC_{index}_BASE_ADDR == {base}, "base");',
                   f'_Static_assert(RTC_{index}_IRQN == {irq}, "irq");']
        regs = p.findall('registers/register')
        require(len(regs) == 8, 'unexpected RTC slot count')
        for reg in regs:
            name = reg.findtext('name').lower()
            off = int(reg.findtext('addressOffset'), 0)
            require(reg.findtext('size') == '32' and reg.find('resetValue') is None, 'width/reset differs')
            source.append(f'_Static_assert(offsetof(rtc_v150_regs_t,{name}) == {off}, "offset");')
    source += [r'''
static rtc_v150_regs_t backing[2];
static rtcs_v150_regs_t common;
static unsigned delays[4], delay_count, callbacks;
uintptr_t rtc_porting_base_addr_get(rtc_index_t i) { return i < 2 ? (uintptr_t)&backing[i] : 0; }
uintptr_t rtc_porting_comm_addr_get(void) { return (uintptr_t)&common; }
void uapi_tcxo_delay_us(uint32_t us) { assert(delay_count < 4); delays[delay_count++] = us; }
static void callback(rtc_index_t i) { assert(backing[i].eoi_ren == 1); callbacks++; }
int main(void) {
    _Static_assert(CONFIG_RTC_MAX_NUM == 2 && RTC_MAX_NUM == 4, "config versus port capacity");
    _Static_assert(sizeof(rtc_v150_regs_t) == 32, "channel span");
    _Static_assert(RTC_V150_MODE_ONE_SHOT == 0 && RTC_V150_MODE_PERIODIC == 1 &&
                   RTC_V150_MODE_ONE_SHOT_1 == 2 && RTC_V150_MODE_FREERUN == 3, "modes");
    rtc_v150_control_reg_data_t control = {.d32 = 0};
    control.b.enable = 1; assert(control.d32 == 1);
    control.d32 = 0; control.b.mode = 3; assert(control.d32 == 6);
    control.d32 = 0; control.b.int_mask = 1; assert(control.d32 == 8);
    control.d32 = 0; control.b.rstfsm = 1; assert(control.d32 == 16);
    control.d32 = 0; control.b.cnt_req = 1; assert(control.d32 == 32);
    control.d32 = 0; control.b.cnt_lock = 1; assert(control.d32 == 64);
    for (unsigned i = 0; i < 2; ++i) {
        assert(hal_rtc_v150_init(i, callback) == 0);
        backing[i].control = 0xa5000006;
        backing[i].load_count1 = 0xfeedface; common.lock = 0x12345678;
        hal_rtc_v150_config_load(i, UINT64_C(0xdeadbeef12345678));
        assert(backing[i].control == 0xa5000000);
        assert(backing[i].load_count0 == 0x12345678);
        assert(backing[i].load_count1 == 0xfeedface && common.lock == 0x12345678);
        hal_rtc_v150_start(i);
        assert(backing[i].control == 0xa5000001 && delays[i*2] == 65);
        backing[i].current_value0 = 0xfedcba98; backing[i].current_value1 = 0xdeadbeef;
        backing[i].control = 0xa5000041;
        bool enabled = false;
        assert(hal_rtc_v150_get_current_value(i, &enabled) == 0xfedcba98);
        assert(backing[i].control == 0xa5000061 && !enabled);
        /* Original helper never sets *is_enable=true on the active path. */
        backing[i].control = 1;
        assert(hal_rtc_v150_get_current_value(i, &enabled) == 0); /* bounded timeout */
        assert(backing[i].control == 33);
        backing[i].current_value0 = 0; backing[i].control = 65;
        assert(hal_rtc_v150_get_current_value(i, &enabled) == 0); /* valid zero indistinguishable */
        hal_rtc_v150_stop(i);
        assert(!(backing[i].control & 1) && delays[i*2+1] == 32);
        enabled = true;
        assert(hal_rtc_v150_get_current_value(i, &enabled) == 0 && !enabled);
        backing[i].eoi_ren = 0;
        hal_rtc_v150_irq_handler(i);
        assert(callbacks == i+1 && hal_rtc_v150_get_int_cnt_record() == i+1);
        uint32_t preserved = backing[i].control;
        hal_rtc_v150_deinit(i);
        assert(g_rtc_regs[i] == 0 && backing[i].control == preserved);
    }
    assert(delay_count == 4);
    return 0;
}
''']
    with tempfile.TemporaryDirectory(prefix='ws53-rtc-sdk-') as tmp:
        work = Path(tmp)
        (work/'errcode.h').write_text('#pragma once\n#include <stdint.h>\ntypedef uint32_t errcode_t;\n'
            '#define ERRCODE_SUCC 0\n#define ERRCODE_RTC_REG_ADDR_INVALID 1\n')
        (work/'common_def.h').write_text('#pragma once\n#include <stddef.h>\n#define STATIC static\n'
            '#define unused(x) ((void)(x))\n#define unlikely(x) (x)\n')
        (work/'tcxo_porting.h').write_text('#pragma once\n')
        (work/'tcxo.h').write_text('#pragma once\n#include <stdint.h>\nvoid uapi_tcxo_delay_us(uint32_t);\n')
        (work/'check.c').write_text('\n'.join(source))
        defines = [f'-D{k}={1 if v == "y" else v}' for k, v in expected.items()]
        subprocess.run([args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', *defines,
                        '-I', str(work), '-I', str(sdk/PORT), '-I', str(sdk/HAL),
                        str(work/'check.c'), '-o', str(work/'check')], check=True)
        subprocess.run([str(work/'check')], check=True, timeout=5)
    print(json.dumps({'sdk_commit': PIN, 'input_sha256': {k: hashlib.sha256(v).hexdigest() for k,v in inputs.items()},
        'instances': [{'base': '0x57029100', 'irq': 49}, {'base': '0x57029200', 'irq': 50}],
        'configured_count_bits': 32, 'port_enum_capacity_not_instances': 4,
        'reference_clock_hz': 32768, 'start_delay_us': 65, 'stop_delay_us': 32,
        'source_snapshot_poll_limit': 65535,
        'checks': ['16 channel offsets and widths', 'six control field encodings', 'four mode values',
                   '32-bit load does not unlock common bank or change high word',
                   'start/stop synchronization delay calls', 'snapshot low-word width',
                   'disabled and timeout and successful zero ambiguity', 'active path leaves enabled output unchanged',
                   'EOI write before callback', 'deinit leaves hardware unchanged'],
        'rom_execution_verified': False, 'hardware_accessed': False,
        'clock_source_or_common_bank_semantics_verified': False}, indent=2))


if __name__ == '__main__':
    main()
