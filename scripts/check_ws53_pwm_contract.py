#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned original WS53 PWM C against host RAM/stubs, never a board."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
HAL = 'src/drivers/drivers/hal/pwm/'
PORT = 'src/drivers/chips/ws53/porting/'
CHIP = 'src/drivers/chips/ws53/include/'
DRIVER = 'src/drivers/drivers/driver/pwm/pwm.c'
CONFIG = 'src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config'


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    paths = [HAL+p for p in ('hal_pwm.c', 'hal_pwm.h', 'v151/hal_pwm_v151.c',
             'v151/hal_pwm_v151.h', 'v151/hal_pwm_v151_regs_def.h',
             'v151/hal_pwm_v151_regs_op.h')]
    paths += [PORT+'pwm/pwm_porting.c', PORT+'include/pwm_porting.h', DRIVER,
              'src/include/driver/pwm.h', CHIP+'acore/platform_core.h',
              CHIP+'acore/chip_core_irq.h', CHIP+'clock_recover.h', CONFIG]
    inputs = {}
    for path in paths:
        data = (sdk/path).read_bytes()
        require(data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{path}']),
                f'SDK differs from source pin: {path}')
        inputs[path] = data
    cfg = inputs[CONFIG].decode()
    defines = {'CONFIG_PWM_USING_V151': 'y', 'CONFIG_PWM_GROUP_NUM': '4',
               'CONFIG_PWM_CHANNEL_NUM': '8', 'CONFIG_PWM_SUPPORT_LPM': 'y'}
    values = dict(re.findall(r'^(CONFIG_PWM_\w+)=(\w+)$', cfg, re.M))
    require(all(values.get(k) == v for k, v in defines.items()), 'wrong PWM profile')
    require('# CONFIG_PWM_PRELOAD is not set' in cfg, 'unexpected preload profile')
    tree = ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    pwm = tree.find("./peripherals/peripheral[name='PWM']")
    regs = pwm.findall('registers/register')
    require(len(regs) == 115, 'expected four groups, eight channels, seven common slots')
    source = ['#include <assert.h>', '#include <stddef.h>', '#include <string.h>']
    source += ['#include '+json.dumps(str(sdk/p)) for p in
               (HAL+'hal_pwm.c', HAL+'v151/hal_pwm_v151.c', PORT+'pwm/pwm_porting.c', DRIVER)]
    source += [f'_Static_assert(PWM_BASE_ADDR == {int(pwm.findtext("baseAddress"), 0)}, "base");']
    for irq in pwm.findall('interrupt'):
        source += [f'_Static_assert({irq.findtext("name")}_IRQN == {irq.findtext("value")}, "IRQ");']
    fields = []
    for reg in regs:
        name = reg.findtext('name').lower()
        off = int(reg.findtext('addressOffset'), 0)
        require(reg.findtext('size') == '32' and reg.find('resetValue') is None, 'PWM width/reset mismatch')
        if off < 0x100:
            prefix, index = name[:-1], int(name[-1])
            require(index < 4, 'group outside configured profile')
            member = f'pwm_v151_sel[{index}].{prefix}'
            cfield = prefix+'_i'
        elif off < 0x500:
            prefix, index = name[:-1], int(name[-1])
            require(index < 8, 'channel outside configured profile')
            member = f'pwm_ip[{index}].{prefix}'
            cfield = prefix+'_j'
        else:
            prefix, member, cfield = name, name, name
        source += [f'_Static_assert(offsetof(pwm_v151_regs_t,{member}) == {off}, "{name} offset");',
                   f'_Static_assert(sizeof(((pwm_v151_regs_t*)0)->{member}) == 4, "{name} width");']
        field = reg.find('fields/field')
        width, shift = int(field.findtext('bitWidth')), int(field.findtext('bitOffset'))
        fields += [f'{{ {prefix}_data_t v = {{.d32=0}}; v.b.{cfield} = {(1<<width)-1}; '
                   f'assert(v.d32 == {((1<<width)-1)<<shift}u); }}']
    crg = tree.find("./peripherals/peripheral[name='M_CRG']")
    div = crg.find("registers/register[name='PWM_M_DIV3']")
    require(div.findtext('size') == '16' and div.find('resetValue') is None, 'PWM divider width/reset')
    require({f.findtext('name'): (int(f.findtext('bitOffset')), int(f.findtext('bitWidth')))
             for f in div.findall('fields/field')} == {'ENABLE': (0,1), 'LOAD': (1,1), 'DIVIDER': (2,5)},
            'divider fields disagree with clock oracle')
    source += [f'_Static_assert(M_CTL_RB_PWM_M_DIV3 == {int(crg.findtext("baseAddress"),0)+int(div.findtext("addressOffset"),0)}, "divider address");',
               'static void check_fields(void) {', *fields, '}']
    source += [r'''
static pwm_v151_regs_t backing;
static uint16_t gate, divider;
static unsigned clocks, delays, delay_group, callbacks;
static bool irqs[128];
static uint16_t *clock_slot(uintptr_t addr) {
    clocks++;
    if (addr == M_CTL_RB_M_CLKEN0) return &gate;
    assert(addr == M_CTL_RB_PWM_M_DIV3); return &divider;
}
void reg16_setbit(uintptr_t a, unsigned b) { *clock_slot(a) |= 1u<<b; }
void reg16_clrbit(uintptr_t a, unsigned b) { *clock_slot(a) &= ~(1u<<b); }
void reg16_setbits(uintptr_t a, unsigned b, unsigned n, unsigned v) {
    assert(b==2 && n==5 && v==16 && (divider&3)==1);
    uint16_t *s=clock_slot(a); *s=(*s & ~(((1u<<n)-1)<<b)) | (v<<b);
}
void writew(uintptr_t a, uint16_t v) { *clock_slot(a)=v; }
unsigned osal_irq_lock(void) { return 0; }
void osal_irq_unlock(void) {}
void osal_irq_restore(unsigned old) { assert(old==0); }
int osal_irq_request(uintptr_t n, osal_irq_handler h, void *a, void *b, void *c) {
    assert((n==71 || n==72) && h && !a && !b && !c); return 0;
}
void osal_irq_enable(uintptr_t n) { assert(n==71 || n==72); irqs[n]=true; }
void osal_irq_disable(uintptr_t n) { assert(n==71 || n==72); irqs[n]=false; }
void osal_irq_free(uintptr_t n, void *p) { assert((n==71 || n==72) && !p); }
void osal_printk(const char *fmt, ...) { assert(fmt); }
void uapi_tcxo_delay_us(uint32_t us) {
    assert(us==1 && delay_group<4);
    assert(backing.pwm_v151_sel[delay_group].pwm_startclrcnt_en==1);
    assert(backing.pwm_v151_sel[delay_group].pwm_start==1);
    assert(backing.pwm_ip[delay_group*2].pwm_en==0);
    assert(backing.pwm_ip[delay_group*2+1].pwm_en==1);
    delays++;
}
static errcode_t callback(uint8_t ch) {
    assert(ch==7 && backing.pwm_cfg_int_clr0==0); callbacks++; return 0;
}
int main(void) {
    check_fields();
    _Static_assert(sizeof(pwm_v151_regs_t)==0x51c, "span");
    _Static_assert(PWM_NONE==8 && PWM_CYCLES_MAX_NUMBER==32767, "profile limits");
    assert(pwm_porting_base_addr_get()==PWM_BASE_ADDR);
    /* Redirect the original port's static base; no physical address dereference. */
    g_pwm_base_addr=(uintptr_t)&backing;
    gate=0x8000; divider=0x8000;
    assert(uapi_pwm_init()==0 && clocks==5);
    assert(gate==0x8080 && divider==0x8043 && backing.pwm_int_mask==3);
    for (unsigned ch=0; ch<8; ch++) {
        assert(uapi_pwm_get_frequency(ch)==32000000);
        backing.pwm_ip[ch].pwm_freq_h=0xa5a5;
        backing.pwm_ip[ch].pwm_offset_h=0x5a5a;
        backing.pwm_ip[ch].pwm_duty_h=0xbeef;
        pwm_config_t cfg={.low_time=200, .high_time=55, .offset_time=200, .cycles=32767, .repeat=true};
        assert(uapi_pwm_open(ch,&cfg)==0);
        assert(backing.pwm_ip[ch].pwm_freq_l==255 && backing.pwm_ip[ch].pwm_duty_l==55);
        assert(backing.pwm_ip[ch].pwm_offset_l==200 && backing.pwm_ip[ch].pwm_period_val==32767);
        assert(backing.pwm_ip[ch].pwm_freq_h==0xa5a5 && backing.pwm_ip[ch].pwm_offset_h==0x5a5a);
        assert(backing.pwm_ip[ch].pwm_duty_h==0xbeef);
    }
    assert(uapi_pwm_get_frequency(8)==0);
    pwm_config_t bad={.low_time=200,.high_time=56};
    assert(pwm_port_param_check(&bad)==ERRCODE_PWM_INVALID_PARAMETER);
    bad.high_time=55; bad.offset_time=201;
    assert(pwm_port_param_check(&bad)==ERRCODE_PWM_INVALID_PARAMETER);
    bad=(pwm_config_t){.low_time=0,.high_time=0};
    assert(pwm_port_param_check(&bad)==0); /* zero period accepted by SDK */
    bad=(pwm_config_t){.low_time=UINT32_MAX,.high_time=1};
    assert(pwm_port_param_check(&bad)==0); /* addition wraps; Rust must reject */
    /* Group staging is observable at the original one-microsecond delay call. */
    for (unsigned gr=0; gr<4; gr++) {
        delay_group=gr;
        hal_pwm_v151_set_group(gr, 3u<<(2*gr));
        hal_pwm_v151_set_cycles(gr*2,3); hal_pwm_v151_set_cycles(gr*2+1,0);
        hal_pwm_v151_set_action(gr,PWM_ACTION_CONTINUE_CLR);
        unsigned before=delays;
        hal_pwm_v151_start(gr);
        assert(delays==before+1 && backing.pwm_v151_sel[gr].pwm_startclrcnt_en==0);
        assert(backing.pwm_ip[gr*2].pwm_en==0 && backing.pwm_ip[gr*2+1].pwm_en==1);
        hal_pwm_v151_set_action(gr,PWM_ACTION_CONTINUE_SET);
        hal_pwm_v151_start(gr);
        assert(delays==before+1 && backing.pwm_ip[gr*2].pwm_en==1);
        hal_pwm_v151_stop(gr);
        assert(backing.pwm_ip[gr*2].pwm_en==0 && backing.pwm_ip[gr*2+1].pwm_en==0);
    }
    /* Last configured channel changes the global repeat policy of all groups. */
    pwm_config_t cfg={.low_time=100,.high_time=100,.cycles=2,.repeat=false};
    assert(uapi_pwm_open(0,&cfg)==0);
    cfg.repeat=true; assert(uapi_pwm_open(1,&cfg)==0);
    unsigned before=delays; hal_pwm_v151_start(0);
    assert(delays==before && backing.pwm_ip[0].pwm_en==1);
    /* Source group helper accepts a channel outside configured 0..7 range. */
    uint8_t channel8=8;
    assert(uapi_pwm_set_group(3,&channel8,1)==0);
    assert(backing.pwm_v151_sel[3].pwm_sel==256);
    hal_pwm_v151_register_callback(7,callback);
    backing.pwm_ip[7].pwm_periodload_flag=1;
    backing.pwm_abnor_state0=4; backing.pwm_abnor_state1=8;
    hal_pwm_v151_irq_handler();
    assert(callbacks==1 && backing.pwm_cfg_int_clr0==128);
    assert(backing.pwm_abnor_state_clr0==4 && backing.pwm_abnor_state_clr1==8);
    pwm_port_register_irq(0); assert(irqs[71] && irqs[72]);
    pwm_port_unregister_irq(7); assert(!irqs[71] && !irqs[72]); /* shared IRQs */
    hal_pwm_regs_deinit(); assert(!g_pwm_regs && backing.pwm_int_mask==3);
    pwm_port_clock_enable(false); assert(clocks==7 && gate==0x8000 && divider==0);
    return 0;
}
''']
    with tempfile.TemporaryDirectory(prefix='ws53-pwm-source-') as temp:
        work = Path(temp)
        error_names = ['SUCC','INVALID_PARAM','PWM_REG_ADDR_INVALID','PWM_INVALID_PARAMETER','PWM_NOT_INIT','PWM_NOT_OPEN']
        stubs = {
            'errcode.h': '#include <stdint.h>\ntypedef uint32_t errcode_t;\n'+''.join(
                f'#define ERRCODE_{name} {i}\n' for i,name in enumerate(error_names)),
            'common_def.h': '#include <stddef.h>\n#undef NULL\n#define NULL 0\n#define unused(x) ((void)(x))\n'
                '#define unlikely(x) (x)\n#define bit(x) (1u<<(x))\n',
            'tcxo.h': '#include <stdint.h>\nvoid uapi_tcxo_delay_us(uint32_t);\n',
            'osal_debug.h': 'void osal_printk(const char*, ...);\n',
            'soc_osal.h': '#include <stdint.h>\ntypedef int (*osal_irq_handler)(int,const void*);\n'
                'unsigned osal_irq_lock(void); void osal_irq_unlock(void); void osal_irq_restore(unsigned);\n'
                'int osal_irq_request(uintptr_t,osal_irq_handler,void*,void*,void*);\n'
                'void osal_irq_enable(uintptr_t); void osal_irq_disable(uintptr_t); void osal_irq_free(uintptr_t,void*);\n',
            'osal_interrupt.h': '#include "soc_osal.h"\n',
            'chip_io.h': '#include <stdint.h>\nvoid reg16_setbit(uintptr_t,unsigned);\n'
                'void reg16_clrbit(uintptr_t,unsigned); void reg16_setbits(uintptr_t,unsigned,unsigned,unsigned);\n'
                'void writew(uintptr_t,uint16_t);\n',
            'securec.h': '#include <string.h>\nstatic inline int memcpy_s(void*d,size_t n,const void*s,size_t z) '
                '{ assert(z<=n); memcpy(d,s,z); return 0; }\n',
            'platform_core.h': '\n'.join(re.findall(r'^#define\s+PWM_BASE_ADDR\s+.*$', inputs[CHIP+'acore/platform_core.h'].decode(), re.M))+'\n',
            'bits/alltypes.h': '#include <stdint.h>\n',
        }
        for name, content in stubs.items():
            (work/name).parent.mkdir(parents=True, exist_ok=True)
            (work/name).write_text('#pragma once\n'+content)
        (work/'check.c').write_text('\n'.join(source))
        command = [args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror']
        command += [f'-D{k}={1 if v=="y" else v}' for k,v in defines.items()]
        for inc in (work, sdk/HAL, sdk/(HAL+'v151'), sdk/(PORT+'include'), sdk/'src/include/driver', sdk/CHIP, sdk/(CHIP+'acore')):
            command += ['-I', str(inc)]
        subprocess.run([*command, str(work/'check.c'), '-o', str(work/'check')], check=True)
        subprocess.run([str(work/'check')], check=True, timeout=5)
    print(json.dumps({'sdk_commit': PIN, 'input_sha256': {k:hashlib.sha256(v).hexdigest() for k,v in inputs.items()},
        'groups': 4, 'channels': 8, 'register_slots': 115, 'base': '0x52082000', 'irqs': [71,72],
        'reference_clock_hz': 32000000, 'source_period_limit': 255, 'source_cycles_limit': 32767,
        'checks': ['all register offsets/widths/bitfields', '16-bit clock enable/load sequence and preserve unrelated bits',
                   'low-word configuration leaves high words unchanged', '1us staging delay and group controls',
                   'shared repeat flag interference', 'zero/overflow accepted by source parameter helper',
                   'group accepts out-of-range configured channel8', 'callback before clear, shared IRQ disable',
                   'register deinit does not quiesce peripheral'],
        'hardware_accessed': False, 'waveform_verified': False, 'command_self_clear_verified': False,
        'rom_or_installed_firmware_verified': False}, indent=2))


if __name__ == '__main__':
    main()
