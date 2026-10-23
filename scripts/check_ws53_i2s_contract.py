#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compile pinned WS53 I2S register definitions and port against host stubs only."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
HAL = 'src/drivers/drivers/hal/sio/'
V = HAL + 'hal_sio_v151/'
PORT = 'src/drivers/chips/ws53/porting/'
CHIP = 'src/drivers/chips/ws53/include/acore/'
CONFIG = 'src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config'
ALIASES = {'left_tx': 'i2s_left_xd', 'right_tx': 'i2s_right_xd',
           'left_rx': 'i2s_left_rd', 'right_rx': 'i2s_right_rd', 'i2s_pos_flag': 'pos_flag'}


def require(ok, why):
    if not ok:
        raise ValueError(why)


def clean(text):
    return re.sub(r'/\*.*?\*/', '', text, flags=re.S)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True, type=Path)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    paths = [V + name for name in ['hal_sio_v151_regs_def.h', 'hal_sio_v151_regs_op.h',
                                   'hal_sio_v151_regs_op.c', 'hal_sio_v151.c']]
    paths += [HAL + 'CMakeLists.txt', HAL + 'hal_sio.h', PORT + 'sio/sio_porting.c',
              PORT + 'include/sio_porting.h', CHIP + 'platform_core.h', CHIP + 'chip_core_irq.h',
              'src/include/common_def.h', CONFIG, PORT + 'pwm/pwm_porting.c',
              'src/drivers/chips/ws53/include/clock_recover.h']
    inputs = {}
    for path in paths:
        data = (sdk / path).read_bytes()
        require(data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{path}']),
                f'changed SDK source: {path}')
        inputs[path] = data
    cfg = inputs[CONFIG].decode()
    require('CONFIG_SIO_USING_V151=y' in cfg and 'CONFIG_DRIVER_SUPPORT_I2S=y' in cfg,
            'baseline I2S selection changed')
    require('# CONFIG_I2S_SUPPORT_DMA is not set' in cfg, 'baseline DMA changed')
    require('hal_sio_v151/hal_sio_v151.c' in inputs[HAL + 'CMakeLists.txt'].decode(),
            'CMake no longer selects v151')
    svd_path = Path(__file__).resolve().parents[1] / 'generated/WS53.experimental.svd'
    svd = ET.parse(svd_path)
    peri = svd.find("./peripherals/peripheral[name='I2S']")
    require(peri is not None, 'I2S absent')
    regs = peri.findall('registers/register')
    require(len(regs) == 25, 'register count')
    header = clean(inputs[V + 'hal_sio_v151_regs_def.h'].decode())
    unions = {}
    for tag, body in re.findall(r'typedef union sio_v151_(\w+)_data \{(.*?)\} sio_v151_\w+_data_t;', header, re.S):
        struct = re.search(r'struct \{(.*?)\} b;', body, re.S).group(1)
        fields, shift = {}, 0
        for name, width in re.findall(r'uint32_t\s+(\w+)\s*:\s*(\d+)\s*;', struct):
            width = int(width)
            if not name.startswith('reserved'):
                fields[shift] = (name, width)
            shift += width
        unions[tag] = fields
    code = ['#include <assert.h>', '#include <stddef.h>', '#include <stdint.h>',
            '#include <stdbool.h>', '#include <stdio.h>',
            '#include ' + json.dumps(str(sdk / (V + 'hal_sio_v151_regs_def.h'))),
            '#include ' + json.dumps(str(sdk / (CHIP + 'chip_core_irq.h'))),
            '#include ' + json.dumps(str(sdk / (PORT + 'sio/sio_porting.c')))]
    code += [f'_Static_assert(I2S_BUS_0_BASE_ADDR == {int(peri.findtext("baseAddress"), 0)} + 0x3c, "base");',
             f'_Static_assert(I2S_IRQN == {peri.findtext("interrupt/value")}, "IRQ");']
    field_checks, report = [], {}
    rx = {'LEFT_RX', 'RIGHT_RX', 'I2S_DUAL_RX_CHN'}
    tx = {'LEFT_TX', 'RIGHT_TX', 'I2S_DUAL_TX_CHN'}
    for reg in regs:
        name = reg.findtext('name')
        member = name.lower()
        offset = int(reg.findtext('addressOffset'), 0)
        code += [f'_Static_assert(offsetof(sio_v151_regs_t, {member}) + 0x3c == {offset}, "{name} offset");',
                 f'_Static_assert(sizeof(((sio_v151_regs_t *)0)->{member}) == 4, "{name} width");']
        require(reg.findtext('size') == '32' and reg.find('resetValue') is None, f'{name} width/reset')
        expected_access = ('write-only' if name in tx | {'CT_SET', 'CT_CLR', 'INTCLR'} else
                           'read-only' if name in rx | {'INTSTATUS', 'RX_STA', 'TX_STA'} else 'read-write')
        require(reg.findtext('access', 'read-write') == expected_access, f'{name} access')
        require(reg.findtext('readAction') == ('modifyExternal' if name in rx else None), f'{name} read action')
        tag = ALIASES.get(member, member)
        layout = unions[tag]
        fields = reg.findall('fields/field')
        require(len(fields) == len(layout), f'{name} incomplete fields')
        report[name] = {}
        for field in fields:
            fname = field.findtext('name')
            field_access = 'read-only' if (name, fname) == ('VERSION', 'VERSION') else expected_access
            require(field.findtext('access') == field_access, f'{name}.{fname} access')
            shift, width = int(field.findtext('bitOffset')), int(field.findtext('bitWidth'))
            require(shift in layout and layout[shift][1] == width, f'{name}.{fname} layout')
            cfield = layout[shift][0]
            mask = ((1 << width) - 1) << shift
            field_checks += [f'{{ sio_v151_{tag}_data_t v = {{.d32=0}}; v.b.{cfield} = {mask >> shift}u;',
                             f'assert(v.d32 == {mask}u); v.d32 = {mask}u; assert(v.b.{cfield} == {mask >> shift}u); }}']
            require(field.findtext('modifiedWriteValues') == ('oneToClear' if name == 'INTCLR' else None),
                    f'{name}.{fname} write action')
            report[name][fname] = {'c_field': cfield, 'mask': hex(mask)}
    platform = inputs[CHIP + 'platform_core.h'].decode()
    defines = '\n'.join(re.findall(r'^#define\s+(?:I2S_BUS_0_BASE_ADDR|I2S_MAX_NUMBER|M_CTL_RB_BASE)\s+.*$', platform, re.M))
    defines += '\n' + '\n'.join(re.findall(r'^#define\s+\w+\s+\d.*$', inputs[PORT + 'include/sio_porting.h'].decode(), re.M))
    common = inputs['src/include/common_def.h'].decode()
    require('(*(volatile unsigned int*)(uintptr_t)(addr))' in common and
            '#define uapi_reg_setbit(addr, pos)' in common, 'clock operation width requires re-audit')
    # Execute the original bit-set/clear macro bodies; only physical addresses
    # are redirected to host RAM. This checks upper-half preservation as well.
    macros = '\n'.join(re.findall(r'^#define uapi_reg_(?:setbit|clrbit)\(addr, pos\).*$', common, re.M))
    require(len(macros.splitlines()) == 2, 'clock macro definitions changed')
    defines += '\n' + macros
    require('reg16_setbit(M_CTL_RB_M_CLKEN0, PWM_CLKEN0_OFFSET)' in
            inputs[PORT + 'pwm/pwm_porting.c'].decode(), 'PWM halfword evidence changed')
    recovery = clean(inputs['src/drivers/chips/ws53/include/clock_recover.h'].decode('utf-8-sig'))
    union = re.search(r'typedef union \{.*?\} u_m_cken_0;', recovery, re.S).group()
    code += [union]
    cfields = dict(zip('MDIAG_ENABLE I2S_ENABLE MTOP_GLUE_TRIGGER_ENABLE PWM_ENABLE SDIO_DEVICE_ENABLE SECURE_PWM_ENABLE'.split(),
                      'mdiag_clken i2s_clken mtop_glue_trigger_clken pwm_clken sdio_dev_clken sec_pwm_clken'.split()))
    clock = svd.find("./peripherals/peripheral[name='M_CRG']")
    for name, width in [('M_CLKEN0', '16'), ('M_CLKEN0_WORD', '32')]:
        reg = clock.find(f"registers/register[name='{name}']")
        require(reg.findtext('size') == width and int(reg.findtext('addressOffset'), 0) == 0x40,
                f'{name} access width/offset')
        require(reg.findtext('access') == 'read-write' and reg.find('resetValue') is None, f'{name} semantics')
        fields = reg.findall('fields/field')
        require(len(fields) == 6, 'clock field count')
        for field in fields:
            mask = 1 << int(field.findtext('bitOffset'))
            require(field.findtext('bitWidth') == '1', 'clock field width')
            field_checks += [f'{{ u_m_cken_0 v = {{.u32=0}}; v.bits.{cfields[field.findtext("name")]}=1; assert(v.u32=={mask}u); }}']
    require(clock.findtext("registers/register[name='M_CLKEN0_WORD']/alternateRegister") == 'M_CLKEN0', 'same slot alias')
    cmu = svd.find("./peripherals/peripheral[name='AUDIO_CMU']")
    require(int(cmu.findtext('baseAddress'), 0) == 0x57008600 and not cmu.findall('interrupt'), 'CMU base/IRQ')
    cfg0 = cmu.find("registers/register[name='NEW_CFG0']")
    require(cfg0.findtext('size') == '32' and cfg0.findtext('access') == 'read-write' and
            cfg0.find('resetValue') is None and cfg0.findtext('addressOffset') == '0x0', 'CMU access')
    require(len(cfg0.findall('fields/field')) == 1 and cfg0.findtext('fields/field/bitOffset') == '0' and
            cfg0.findtext('fields/field/bitWidth') == '1', 'CMU field')
    with tempfile.TemporaryDirectory(prefix='ws53-i2s-source-') as temp:
        work = Path(temp)
        stub = '''#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#define unused(x) ((void)(x))
typedef unsigned sio_bus_t;
#define SIO_BUS_0 0
typedef void (*osal_irq_handler)(void);
void irq_sio0_handler(void);
_Static_assert(sizeof(unsigned int)==4, "SDK bit macros require word access");
static unsigned clock_addr[4], clock_count;
static volatile unsigned cmu_value=0xa55a1230u, mclk_value=0x5aa54081u;
static volatile unsigned *clock_ref(unsigned addr) {
    assert(clock_count < 4); clock_addr[clock_count++]=addr;
    if (addr==0x57008600) return &cmu_value;
    assert(addr==0x52000040); return &mclk_value;
}
#define uapi_reg_read_val32(addr) (*clock_ref(addr))
#define hal_sio_register_funcs(...) ((void)0)
#define hal_sio_unregister_funcs(...) ((void)0)
#define osal_irq_request(...) ((void)0)
#define osal_irq_enable(...) ((void)0)
#define osal_irq_disable(...) ((void)0)
#define osal_irq_clear(...) ((void)0)
#define hal_sio_v151_irq_handler(...) ((void)0)
#define uapi_pin_set_mode(...) ((void)0)
'''
        (work / 'common_def.h').write_text(stub + defines + '\n')
        for name in ['hal_sio_v151.h', 'oal_interface.h', 'soc_osal.h', 'interrupt.h',
                     'pinctrl_porting.h', 'pinctrl.h', 'sio_porting.h']:
            (work / name).write_text('#pragma once\n')
        code += ['int main(void) {'] + field_checks + ['''
    assert(sio_porting_base_addr_get(0) == 0x5203003c);
    assert(i2s_porting_tx_left_data_addr_get(0) == 0x5203004c);
    assert(i2s_porting_tx_right_data_addr_get(0) == 0x52030050);
    assert(i2s_porting_rx_left_data_addr_get(0) == 0x52030054);
    assert(i2s_porting_rx_right_data_addr_get(0) == 0x52030058);
    assert(i2s_porting_rx_merge_data_addr_get(0) == 0x520300a0);
    assert(i2s_porting_tx_merge_data_addr_get(0) == 0x520300c0);
    sio_porting_clock_enable(true);
    assert(cmu_value==0xa55a1231u && mclk_value==0x5aa54085u);
    sio_porting_clock_enable(false);
    assert(cmu_value==0xa55a1230u && mclk_value==0x5aa54081u);
    assert(clock_count == 4);
    for (unsigned i=0; i<4; i++) {
        assert(clock_addr[i] == (i%2 ? 0x52000040 : 0x57008600));
    }
    const unsigned widths[] = {16,18,20,24,32}, channels[] = {2,4,8,16};
    for (unsigned w=0; w<5; w++) for (unsigned c=0; c<4; c++) {
        unsigned div=sio_porting_get_bclk_div_num(widths[w],channels[c]);
        assert(div == 32000/(25*widths[w]*channels[c]));
        printf("%u %u %u\\n", widths[w], channels[c], div);
    }
    return 0;
}''']
        (work / 'check.c').write_text('\n'.join(code))
        subprocess.run([args.cc, '-std=c11', '-Wall', '-Werror', '-Wno-unused-variable',
                        '-I' + str(work), '-I' + str(sdk / CHIP), str(work / 'check.c'),
                        '-o', str(work / 'check')], check=True)
        rows = subprocess.check_output([str(work / 'check')], text=True)
    print(json.dumps({'sdk_commit': PIN, 'registers': len(regs),
                      'field_count': sum(map(len, report.values())), 'fields': report,
                      'additional_clock_fields': 13,
                      'clock_rmw_original_macros_preserve_upper_and_neighbor_bits': True,
                      'divider_cases': [dict(zip(['width', 'channels', 'divider'], map(int, r.split()))) for r in rows.splitlines()],
                      'clock_writes': ['32-bit 0x57008600 bit0 set', '32-bit 0x52000040 bit2 set',
                                       '32-bit 0x57008600 bit0 clear', '32-bit 0x52000040 bit2 clear'],
                      'limitations': ['host C layout and port evidence, not silicon',
                                      'CT_SET/CLR readback and zero-write effects withheld',
                                      'audio clock ownership and upstream frequency require handoff',
                                      'SDK divider floors integer division before float conversion'],
                      'source_sha256': {p: hashlib.sha256(d).hexdigest() for p, d in inputs.items()},
                      'svd_sha256': hashlib.sha256(svd_path.read_bytes()).hexdigest()}, indent=2))


if __name__ == '__main__':
    main()
