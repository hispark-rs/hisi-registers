#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned ADC v155 HAL/port over host RAM; never touch physical MMIO."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from check_ws53_sdk_layout import MAPPINGS

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
V = 'src/drivers/drivers/hal/adc/v155/'
PORT = 'src/drivers/chips/ws53/porting/'
CONFIG = 'src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config'
# Explicit semantic correspondence; C bit positions are compiled, not trusted
# from a second hand-entered mask table.
UNIONS = {
    'SCAN_CONTROL': ('adc_ctrl_data_t', 'channel equ_model_sel sample_cnt satrt_cnt cast_cnt'),
    'SCAN_COMMAND': ('adc_scan_start_and_stop_data_t', 'lsadc_start lsadc_stop'),
    'ANALOG_CONTROL': ('adc_enable_data_t', 'da_lsadc_en da_lsadc_rstn'),
    'MANUAL_CONTROL': ('cha_manu_ctrl_data_t', 'lsadc_scan_ch_man lsadc_scan_ch_man_sel'),
    'OFFSET_CALIBRATION': ('adc_offset_cali_data_t', 'offset_cali_mode offset_cali_loop_sel offset_cali_inverse offset_cali_acc_cycle_sel offset_cali_range_sel offset_cali_data_spi offset_cali_adc_data_inv'),
    'OFFSET_ENABLE': ('adc_offset_cali_en_data_t', 'offset_cali_en'),
    'OFFSET_DATA_ENABLE': ('adc_cfg_offset_cali_en_data_t', 'offset_data_en'),
    'OFFSET_REFRESH': ('adc_offset_cali_spi_refresh_data_t', 'offset_cali_spi_refresh'),
    'OFFSET_STATE_CLEAR': ('adc_offset_cali_state_clr_data_t', 'offset_cali_finish_clr'),
    'OFFSET_STATUS': ('adc_offset_cali_sts_data_t', 'offset_cali_finish'),
    'OFFSET_RESULT': ('adc_rpt_offset_cali_data_t', 'offset_cali_data offset_cali_loop_cnt'),
    'CAP_CALIBRATION': ('cfg_adc_cap_cali_data_t', 'mode alg_sel acc_cycle_sel start_index weight_sel dac_setting_dly_ctrl cap_selman sh_en_man pn_sel_man'),
    'CAP_ENABLE': ('cfg_adc_cap_cali_en_data_t', 'en'),
    'CAP_REFRESH': ('cfg_cap_cali_data_spi_refresh_data_t', 'spi_refresh'),
    'CAP_FINISH_CLEAR': ('cfg_cap_cali_finish_clr_data_t', 'finish_clr'),
    'GAIN_STATE_CLEAR': ('cfg_intr_gain_state_clr_data_t', 'gain_state_clr'),
    'CAP_STATUS': ('rpt_cap_cali_sts0_data_t', 'finish error'),
    'SAMPLE_DONE': ('sample_done_data_t', 'signal'),
}
PULSES = {
    'OFFSET_REFRESH': ('hal_adc_offset_cali_data_spi_refresh', 'offset_cali_spi_refresh'),
    'OFFSET_STATE_CLEAR': ('hal_adc_offset_cali_state_clr', 'offset_cali_finish_clr'),
    'CAP_REFRESH': ('hal_adc_date_spi_refresh_clr', 'spi_refresh'),
    'CAP_FINISH_CLEAR': ('hal_adc_cfg_cap_cali_finish_clr', 'finish_clr'),
    'GAIN_STATE_CLEAR': ('hal_adc_cfg_intr_gain_state_clr', 'gain_state_clr'),
}


def require(value, why):
    if not value:
        raise ValueError(why)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    names = [V + n for n in ('hal_adc_v155.c', 'hal_adc_v155.h', 'hal_adc_v155_regs_def.h',
                             'hal_adc_v155_regs_op.c', 'hal_adc_v155_regs_op.h')]
    names += [PORT + 'adc/adc_porting.c', PORT + 'include/adc_porting.h', CONFIG,
              'src/drivers/drivers/hal/adc/CMakeLists.txt',
              'src/drivers/chips/ws53/include/acore/chip_core_irq.h',
              'src/drivers/drivers/hal/cpu_trace/hal_cpu_trace.c',
              PORT + 'cpu_trace_acore/mcpu_trace_porting.c']
    inputs = {}
    for name in names:
        data = (sdk / name).read_bytes()
        require(data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{name}']),
                f'SDK file differs from pin: {name}')
        inputs[name] = data
    cfg = inputs[CONFIG].decode()
    require('CONFIG_ADC_USING_V155=y' in cfg and 'CONFIG_ADC_SUPPORT_AUTO_SCAN=y' in cfg,
            'wrong application ADC profile')
    require('v155/hal_adc_v155.c' in inputs[names[8]].decode(), 'CMake v155 selection changed')
    root = Path(__file__).resolve().parents[1]
    svd_path = root / 'generated/WS53.experimental.svd'
    svd = ET.parse(svd_path)
    adc = svd.find("./peripherals/peripheral[name='ADC']")
    diag = svd.find("./peripherals/peripheral[name='MCU_DIAG']")
    require(int(adc.findtext('baseAddress'), 0) == 0x5208a000 and adc.findtext('interrupt/value') == '80', 'ADC address/IRQ')
    require(int(diag.findtext('baseAddress'), 0) == 0x52004100 and not diag.findall('interrupt'), 'DIAG address/IRQ')
    trace = inputs['src/drivers/drivers/hal/cpu_trace/hal_cpu_trace.c'].decode()
    apps = trace.split('#if CORE == APPS', 1)[1].split('#elif', 1)[0]
    macros = {key: int(value, 0) for key, value in re.findall(r'^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)\b', apps, re.M)}
    trace_fields = {
        ('MONITOR_SELECT', 'SELECT'): ('HAL_CFG_MONITOR_SEL_BIT', None),
        ('TRACE_SAVE_SELECT', 'SELECT'): ('HAL_CFG_CPU_TRACE_SAVE_SEL_BIT', None),
        ('CPU_TRACE', 'TRACE_ENABLE'): ('HAL_CFG_CPU_TRACE_EN_BIT', None),
        ('CPU_TRACE', 'MCPU_ENABLE'): ('HAL_MCPU_TRACE_EN_BIT', None),
        ('SAMPLE_MODE', 'MODE'): ('HAL_CFG_CPU_SAMPLE_MODE_BITS_OFFSET', 'HAL_CFG_CPU_SAMPLE_MODE_BITS_LEN'),
        ('SAMPLE_MODE', 'ENABLE'): ('HAL_CFG_CPU_SAMPLE_EN_BIT', None),
        ('SAMPLE_MODE', 'SYNC'): ('HAL_CFG_CPU_SAMPLE_SYNC_BIT', None),
    }
    for (reg, field), (shift, width) in trace_fields.items():
        f = diag.find(f"registers/register[name='{reg}']/fields/field[name='{field}']")
        require(int(f.findtext('bitOffset')) == macros[shift] and
                int(f.findtext('bitWidth')) == (macros[width] if width else 1), f'trace field {reg}.{field}')
    for name, macro in {'SAMPLE_MODE': 'HAL_CFG_CPU_SAMPLE_MODE_REG_OFFSET',
                        'CPU_TRACE': 'HAL_CFG_CPU_TRACE_REG_OFFSET',
                        'LENGTH_LOW': 'HAL_CFG_CPU_SAMPLE_LENGTH_L_REG_OFFSET',
                        'START_LOW': 'HAL_CFG_CPU_SAMPLE_START_ADDR_L_REG_OFFSET',
                        'END_HIGH': 'HAL_CFG_CPU_SAMPLE_END_ADDR_H_REG_OFFSET'}.items():
        reg = diag.find(f"registers/register[name='{name}']")
        require(0x52004100 + int(reg.findtext('addressOffset'), 0) ==
                macros['HAL_CPU_TRACE_CTL_RB_BASE_ADDR'] + macros[macro], f'trace shared address {name}')
    checks, fields = [], {}
    for block, (ctype, mapping) in MAPPINGS.items():
        regs = svd.findall(f"./peripherals/peripheral[name='{block}']/registers/register")
        require(len(regs) == len(mapping) + (block == 'ADC'), 'unexpected register additions')
        for reg in regs:
            name = reg.findtext('name')
            require(reg.findtext('size') == '32' and reg.find('resetValue') is None, f'{name} width/reset')
            require(reg.find('readAction') is None, f'{name} speculative read side effect')
            members = [(name, mapping[name])] if name in mapping else [
                (f'ANALOG_CONFIG{i}', f'da_lsadc_rwreg_{i+1}') for i in range(3)]
            for i, (label, member) in enumerate(members):
                off = int(reg.findtext('addressOffset'), 0) + i * 4
                checks.append(f'_Static_assert(offsetof({ctype}, {member}) == {off}, "{label} offset");')
            ro = name in {'OFFSET_STATUS', 'OFFSET_RESULT', 'CAP_STATUS', 'DONE_ADDRESS_LOW', 'DONE_ADDRESS_HIGH', 'SAMPLE_DONE'}
            wo = name in {*PULSES, 'SCAN_COMMAND', 'ANALOG_CONFIG[%s]'}
            access = 'read-only' if ro else 'write-only' if wo else 'read-write'
            require(reg.findtext('access') == access, f'{name} access')
            for field in reg.findall('fields/field'):
                require(field.findtext('access') == access and field.find('modifiedWriteValues') is None,
                        f'{name} speculative access/clear semantics')
            if name in UNIONS:
                union, cfields = UNIONS[name]
                require(len(cfields.split()) == len(reg.findall('fields/field')), f'{name} field count')
                fields[name] = {}
                for field, cfield in zip(reg.findall('fields/field'), cfields.split()):
                    width, shift = int(field.findtext('bitWidth')), int(field.findtext('bitOffset'))
                    value = (1 << width)-1
                    mask = value << shift
                    checks.append(f'{{ {union} v={{.d32=0}}; v.b.{cfield}={value}u; assert(v.d32=={mask}u); '
                                  f'v.d32={mask}u; assert(v.b.{cfield}=={value}u); }}')
                    fields[name][field.findtext('name')] = {'mask': hex(mask), 'c_field': cfield}
    op_header = inputs[V + 'hal_adc_v155_regs_op.h'].decode()
    for name, (func, field) in PULSES.items():
        body = re.search(r'static inline void ' + func + r'\(void\)\s*\{(.*?)\n\}', op_header, re.S).group(1)
        require(re.findall(r'data\.b\.' + field + r'\s*=\s*(\d+)', body) == ['1', '0'], f'{name} pulse sequence')
        member = MAPPINGS['ADC'][1][name]
        require(body.count(f'g_adc_regs->{member} = data.d32;') == 2, f'{name} two writes')
    enum = re.search(r'typedef enum \{\s*ADC_CHANNEL_0,.*?\} adc_channel_t;',
                     inputs[PORT + 'include/adc_porting.h'].decode(), re.S).group()
    with tempfile.TemporaryDirectory(prefix='ws53-adc-contract-') as tmp:
        work = Path(tmp)
        common = r'''#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>
#include <assert.h>
#define CONFIG_ADC_SUPPORT_AUTO_SCAN 1
#define unused(x) ((void)(x))
typedef uint32_t errcode_t;
#define ERRCODE_SUCC 0
#define ERRCODE_FAIL 1
#define ERRCODE_ADC_REG_ADDR_INVALID 2
#define ERRCODE_ADC_INVALID_CH_TYPE 3
#define POS_13 13
#define M_CTL_RB_M_CLKEN1 0x52000044u
#define M_CTL_RB_GLB_CLKEN 0x52000548u
''' + enum + r'''
typedef int afe_scan_mode_t;
#define AFE_GADC_MODE 0
typedef int adc_clock_t;
typedef int hal_adc_channel_type_t;
typedef struct { unsigned unused; } hal_adc_scan_config_t;
typedef hal_adc_scan_config_t adc_scan_config_t;
typedef void (*hal_adc_callback_t)(uint8_t, uint32_t *, uint32_t, bool *);
typedef struct {
    errcode_t (*init)(void), (*deinit)(void);
    void (*power_en)(afe_scan_mode_t, bool);
    errcode_t (*ch_set)(adc_channel_t, bool);
    errcode_t (*ch_config)(adc_channel_t, hal_adc_scan_config_t *, hal_adc_callback_t);
    errcode_t (*ch_enable)(adc_channel_t, bool);
    void (*enable)(bool);
    bool (*isenable)(void);
    int32_t (*manual)(adc_channel_t);
} hal_adc_funcs_t;
typedef int32_t (*osal_irq_handler)(int32_t, const void *);
#define hal_adc_register_funcs(...) ((void)0)
#define hal_adc_unregister_funcs(...) ((void)0)
#define osal_irq_request(...) (0)
#define osal_irq_enable(...) ((void)0)
#define osal_irq_disable(...) ((void)0)
#define osal_irq_free(...) ((void)0)
#define print_str(...) ((void)0)
#define uapi_adc_power_en(...) ((void)0)
#define uapi_adc_auto_scan_ch_enable(...) (0)
#define uapi_adc_auto_scan_ch_disable(...) (0)
#define memset_s(p,n,v,k) (assert((k)<=(n)), memset(p,v,k), 0)
static void writel(uint32_t addr, uint32_t value);
static uint32_t readl(uint32_t addr);
static void bitop(uint32_t addr, unsigned bit, bool on, unsigned width);
#define reg16_setbit(addr,bit) bitop(addr,bit,true,16)
#define reg16_clrbit(addr,bit) bitop(addr,bit,false,16)
#define reg_setbit(addr,off,bit) bitop((addr)+(off),bit,true,32)
#define reg_clrbit(addr,off,bit) bitop((addr)+(off),bit,false,32)
static void uapi_tcxo_delay_us(uint32_t us);
static void *fake_malloc(size_t n);
static void fake_free(void *p);
#define malloc(n) fake_malloc(n)
#define free(p) fake_free(p)
uintptr_t adc_port_base_addr_get(void);
uintptr_t adc_port_mcu_diag_addr_get(void);
errcode_t adc_port_channel_set(adc_channel_t, bool);
void adc_port_soft_rst(void);
void adc_port_get_cali_param(uint8_t *, uint8_t *, uint8_t *);
'''
        (work / 'common_def.h').write_text(common)
        for name in ('errcode.h', 'hal_adc.h', 'adc_porting.h', 'tcxo.h', 'osal_debug.h', 'osal_types.h',
                     'pinctrl_porting.h', 'soc_osal.h', 'pinctrl.h', 'debug_print.h', 'efuse_porting.h',
                     'chip_io.h', 'clock_recover.h'):
            (work / name).write_text('#pragma once\n#include "common_def.h"\n')
        code = ['#include <stdio.h>', '#include "common_def.h"',
                '#include ' + json.dumps(str(sdk / (PORT + 'adc/adc_porting.c'))),
                '#include ' + json.dumps(str(sdk / (V + 'hal_adc_v155_regs_op.c'))),
                '#undef ADDR_OFFSET',
                '#include ' + json.dumps(str(sdk / (V + 'hal_adc_v155.c')))]
        code += [r'''
static adc_regs_t adc_ram;
static mcu_diag_regs_t diag_ram;
static uint32_t delay_us[16], delay_count, writes, last_addr, last_value, reads;
static uint32_t bit_addr[8], bit_index[8], bit_width[8], bit_value[8], bit_count;
static bool finish_calibration, fail_allocation;
static unsigned allocations, frees, delivered;
static void writel(uint32_t addr, uint32_t value) { writes++; last_addr=addr; last_value=value; }
static uint32_t readl(uint32_t addr) {
    assert(addr == 0x20061000u + 4*(reads%2)); reads++;
    return (123u << 2) | (3u << 14);
}
static void bitop(uint32_t addr, unsigned bit, bool on, unsigned width) {
    assert(bit_count < 8); bit_addr[bit_count]=addr; bit_index[bit_count]=bit;
    bit_width[bit_count]=width; bit_value[bit_count++]=on;
}
static void uapi_tcxo_delay_us(uint32_t us) {
    assert(delay_count < 16); delay_us[delay_count++]=us;
    if (finish_calibration && (us==100 || us==128)) adc_ram.rpt_adc_offset_cali_finish_sts=1;
    if (finish_calibration && us==160) adc_ram.rpt_cap_cali_sts_0=1;
}
static void *fake_malloc(size_t n) { assert(n==8); allocations++; return fail_allocation ? NULL : (void *)0x20061000; }
static void fake_free(void *p) { assert(p==(void *)0x20061000); frees++; }
static void callback(uint8_t ch, uint32_t *buf, uint32_t len, bool *next) {
    assert(ch==3 && len==2 && buf[0]==123*1800/4096 && buf[1]==buf[0]); delivered++; (void)next;
}
static void clear_host(void) {
    memset(&adc_ram,0,sizeof(adc_ram)); memset(&diag_ram,0,sizeof(diag_ram));
    bit_count=delay_count=reads=0; finish_calibration=false;
}
int main(void) {
'''] + checks + [r'''
    assert(LSADC_IRQN==80);
    assert(adc_port_base_addr_get()==0x5208a000 && adc_port_mcu_diag_addr_get()==0x52004100);
    /* Only the two source port variables are redirected to host RAM. */
    g_adc_base_addr=(uintptr_t)&adc_ram; g_mcu_diag_addr=(uintptr_t)&diag_ram;
    assert(hal_adc_v155_init()==0 && g_adc_regs==&adc_ram && g_mcu_diag_regs==&diag_ram);
    const uint32_t pads[]={0x57036024,0x5703602c,0x57036030,0x57036044,
                           0x57036098,0x57036060,0x57036064,0x57036058};
    for (unsigned ch=0; ch<8; ch++) {
        writes=0; assert(adc_port_channel_set(ch,true)==0 && writes==1 && last_addr==pads[ch] && last_value==0);
        assert(adc_port_channel_set(ch,false)==0 && writes==1);
        adc_ram.lsadc_ctrl_0=0xc0000000;
        hal_adc_auto_scan_mode_set(ch,true);
        assert(adc_ram.lsadc_ctrl_0==(0xc0000000u | (1u<<ch) | (3u<<8) | (6u<<10) | (26u<<15) | (32u<<23)));
        hal_adc_auto_scan_mode_set(ch,false); assert(!(adc_ram.lsadc_ctrl_0 & 255));
    }
    assert(adc_port_channel_set(ADC_CHANNEL_MAX_NUM,true)==ERRCODE_ADC_INVALID_CH_TYPE);
    adc_port_power_on(true); adc_port_power_on(false);
    assert(bit_count==4);
    for (unsigned i=0; i<4; i++) {
        assert(bit_addr[i]==(i%2 ? 0x52000548 : 0x52000044));
        assert(bit_index[i]==(i%2 ? 12 : 2) && bit_width[i]==16 && bit_value[i]==(i<2));
    }
    /* Original power-on reaches calibration completion and applies source values. */
    clear_host(); finish_calibration=true; adc_ram.rpt_adc_offset_cali_data=10;
    hal_adc_v155_power_en(0,true);
    assert(bit_count==2 && bit_addr[0]==0x52000054 && bit_addr[1]==bit_addr[0]);
    assert(bit_index[0]==13 && bit_index[1]==13 && bit_width[0]==32 && bit_width[1]==32);
    assert(bit_value[0]==0 && bit_value[1]==1);
    const unsigned delays[]={1,1,5,100,128,160};
    assert(delay_count==6); for(unsigned i=0;i<6;i++) assert(delay_us[i]==delays[i]);
    assert(adc_ram.da_lsadc_rwreg_1==0x2aa6010 && adc_ram.da_lsadc_rwreg_2==0x2085 && adc_ram.da_lsadc_rwreg_3==12);
    assert(adc_ram.lsadc_ctrl_11==0x1ffff && adc_ram.cfg_adc_cap_cali==0x349);
    assert(adc_ram.cfg_adc_offset_cali==0x700 && adc_ram.cfg_adc_offset_cali_data_en==1);
    assert(!adc_ram.cfg_adc_offset_cali_en && !adc_ram.cfg_adc_cap_cali_en);
    /* Exercise every six-bit auto-result and the SDK manual correction discontinuity. */
    for(unsigned n=0;n<64;n++) {
        clear_host(); finish_calibration=true; adc_ram.rpt_adc_offset_cali_data=n;
        hal_adc_offset_manual_cali();
        unsigned expected=n<=3 ? 0 : n<=31 ? n-3 : n<=59 ? n+3 : 63;
        assert((adc_ram.cfg_adc_offset_cali >> 8)==expected);
    }
    /* Demonstrate limitations, not a recommended Rust algorithm: no error return
     * for timed-out calibration; CAP_ERROR does not prevent continued setup. */
    clear_host(); adc_ram.rpt_cap_cali_sts_0=256;
    hal_adc_v155_power_en(0,true);
    assert(adc_ram.rpt_adc_offset_cali_finish_sts==0 && adc_ram.rpt_cap_cali_sts_0==256);
    assert(delay_count==6 && adc_ram.da_lsadc_rwreg_2==0x2085);
    unsigned before=delay_count; hal_adc_v155_power_en(0,false); assert(delay_count==before);
    assert(adc_ram.lsadc_ctrl_11==0x1ffff); /* off argument alone does not power down */
    /* All8 channels and all4096 codes use the C RAM word format, not FIFO MMIO. */
    for(unsigned ch=0;ch<8;ch++) for(unsigned sample=0;sample<4096;sample++) {
        adc_fifo_data_str_t word={.d32=0xfffe0003u}; word.b.data=sample; word.b.channel=ch;
        assert(word.d32==(0xfffe0003u | sample<<2 | ch<<14));
    }
    clear_host(); diag_ram.cfg_mcu_diag_cpu_trace=3;
    assert(hal_adc_diag_sample_cfg()==0);
    assert(diag_ram.cfg_monitor_sel==1 && diag_ram.cfg_mcu_diag_trace_save_sel==1);
    assert(diag_ram.cfg_mcu_diag_monitor_clock==0x1ff && diag_ram.cfg_mcu_diag_sample_sel==6);
    assert(diag_ram.cfg_mcu_diag_sample_length_l==2 && !diag_ram.cfg_mcu_diag_sample_length_h);
    assert(diag_ram.cfg_mcu_diag_sample_start_addr_l==0x1000 && diag_ram.cfg_mcu_diag_sample_start_addr_h==0x2006);
    assert(diag_ram.cfg_mcu_diag_sample_end_addr_l==0x7fff && diag_ram.cfg_mcu_diag_sample_end_addr_h==0x2006);
    assert(diag_ram.cfg_mcu_diag_sample_mode==0x300 && diag_ram.cfg_mcu_diag_cpu_trace==3);
    hal_adc_free_buffer(); assert(g_buffer==NULL);
    unsigned saved_alloc=allocations;
    fail_allocation=true; diag_ram.cfg_mcu_diag_sample_mode=0x1234;
    assert(hal_adc_diag_sample_cfg()==ERRCODE_FAIL && diag_ram.cfg_mcu_diag_sample_mode==0x1234);
    assert(allocations==saved_alloc+1); fail_allocation=false;
    /* Completion never arrives, yet the original HAL reads2 RAM words and
     * returns success. Stop frees without disabling diagnostic capture. */
    clear_host();
    assert(hal_adc_v155_auto_scan_ch_config(3,NULL,callback)==0);
    assert(reads==2 && diag_ram.mcu_diag_sample_done==0 && hal_adc_v155_auto_scan_is_enabled());
    assert(hal_adc_v155_auto_scan_ch_enable(3,false)==0 && delivered==1);
    assert(g_buffer==NULL && !hal_adc_v155_auto_scan_is_enabled());
    assert(diag_ram.cfg_mcu_diag_sample_mode==0x300 && adc_ram.lsadc_ctrl_8==3);
    assert(hal_adc_v155_manual_sample(7)==0); /* placeholder, not a conversion */
    printf("{\"channels\":8,\"ram_word_cases\":32768,\"offset_correction_cases\":64,"
           "\"sdk_timeout_returns_success\":true,\"sdk_free_leaves_diag_enabled\":true,"
           "\"sdk_retains_existing_cpu_trace\":true,\"allocations\":%u,\"frees\":%u}\n", allocations,frees);
}
''']
        (work / 'check.c').write_text('\n'.join(code))
        subprocess.run([args.cc, '-std=c11', '-Wall', '-Werror', '-Wno-unused-function', '-Wno-unused-variable',
                        '-Wno-unused-value', '-Wno-pointer-to-int-cast', '-I' + str(work), '-I' + str(sdk / V),
                        '-I' + str(sdk / 'src/drivers/chips/ws53/include/acore'), str(work / 'check.c'),
                        '-o', str(work / 'check')], check=True)
        result = json.loads(subprocess.check_output([str(work / 'check')], text=True))
    print(json.dumps({'sdk_commit': PIN, 'fields': fields, 'field_checks': sum(map(len, fields.values())),
                      'original_c_execution': result, 'pulse_commands_checked': list(PULSES),
                      'additional_trace_field_checks': len(trace_fields),
                      'source_sha256': {name: hashlib.sha256(data).hexdigest() for name,data in inputs.items()},
                      'svd_sha256': hashlib.sha256(svd_path.read_bytes()).hexdigest(),
                      'limitations': ['host-only source oracle, no hardware validation',
                                      'no RAM-write completion or stop/drain guarantee',
                                      'SDK sample end is fixed 0x20067fff, not allocation end',
                                      'register byte offsets are relative to 0x52004100 for diagnostic structure',
                                      'IRQ80 handler and manual sampling are placeholders']}, indent=2))


if __name__ == '__main__':
    main()
