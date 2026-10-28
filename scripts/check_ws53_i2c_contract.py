#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned WS53 I2C v151 source on host RAM, never a physical bus."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from check_ws53_uart_dma_contract import function

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
HAL = 'src/drivers/drivers/hal/i2c/'
PORT = 'src/drivers/chips/ws53/porting/'
CHIP = 'src/drivers/chips/ws53/include/'
CONFIG = 'src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config'


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', required=True, type=Path)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    paths = [HAL+p for p in ('hal_i2c.h', 'v151/hal_i2c_v151.h',
        'v151/hal_i2c_v151_regs_def.h', 'v151/hal_i2c_v151_regs_op.h',
        'v151/hal_i2c_v151_regs_op.c', 'v151/hal_i2c_v151_master.c',
        'v151/hal_i2c_v151_master.h', 'v151/hal_i2c_v151_comm.h', 'v151/hal_i2c_v151_comm.c')]
    paths += [PORT+'i2c/i2c_porting.c', PORT+'include/i2c_porting.h',
        CHIP+'acore/platform_core.h', CHIP+'acore/chip_core_irq.h', CHIP+'clock_recover.h',
        CONFIG, 'src/build/config/target_config/ws53/target_config.py']
    inputs = {}
    for path in paths:
        data = (sdk/path).read_bytes()
        require(data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{path}']),
                f'SDK differs from pin: {path}')
        inputs[path] = data
    cfg = inputs[CONFIG].decode()
    values = dict(re.findall(r'^(CONFIG_I2C_\w+)=(\w+)$', cfg, re.M))
    expected = {'CONFIG_I2C_SUPPORT_MASTER':'y','CONFIG_I2C_USING_V151':'y',
        'CONFIG_I2C_SUPPORT_LPC':'y','CONFIG_I2C_SUPPORT_LPM':'y',
        'CONFIG_I2C_NOT_SUPPORT_HIGH_SPEED':'y','CONFIG_I2C_SUPPORT_FAST_PLUS_SPEED':'y',
        'CONFIG_I2C_WAIT_CONDITION_TIMEOUT':'0x3E8'}
    require(all(values.get(k) == v for k,v in expected.items()), 'I2C application profile changed')
    for feature in ('SLAVE','INT','DMA'):
        require(f'# CONFIG_I2C_SUPPORT_{feature} is not set' in cfg, f'unexpected {feature}')
    require('CONFIG_I2C_IP_VERSION_V151_PRO' not in cfg, 'PRO layout is not this profile')
    platform = inputs[CHIP+'acore/platform_core.h'].decode()
    platform_stub = '\n'.join(re.findall(r'^#define\s+I2C_(?:BUS_[01]_BASE_ADDR|BUS_MAX_NUMBER)\s+.*$', platform, re.M))
    platform_stub += '\n'+re.search(r'typedef enum \{\s*I2C_BUS_0,.*?\} i2c_bus_t;', platform, re.S).group()
    svd = ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    source = ['#include <assert.h>', '#include <stddef.h>', '#include <stdint.h>',
        '#include <stdbool.h>', '#include <string.h>']
    source += ['#include '+json.dumps(str(sdk/p)) for p in
        (HAL+'v151/hal_i2c_v151_regs_op.c', HAL+'v151/hal_i2c_v151_master.c', PORT+'i2c/i2c_porting.c')]
    common = inputs[HAL+'v151/hal_i2c_v151_comm.c'].decode()
    require('# CONFIG_I2C_ADDR_VALID_CHECK is not set' in cfg, 'address permission profile changed')
    source += [re.search(r'^#define HAL_I2C_VALID_ADDR_VALUE_MASK\s+.*$', common, re.M).group(),
               re.search(r'^static hal_i2c_v151_ctrl_info_t g_hal_i2c_ctrl_info.*?;', common, re.M).group()]
    for name in ('hal_i2c_v151_get_data_addr', 'hal_i2c_v151_get_address_attr',
                 'hal_i2c_v151_get_address_cfg', 'hal_i2c_v151_get_ctrl_info',
                 'hal_i2c_v151_com_clr_int', 'hal_i2c_v151_com_prepare_int',
                 'hal_i2c_v151_com_restore_int', 'hal_i2c_v151_prepare_int_tx',
                 'hal_i2c_v151_prepare_int_rx', 'hal_i2c_v151_restore_int_rx'):
        source += [function(common, name)]
    field_checks = []
    count = 0
    # Parse original C unions to find member spellings (including ss_b/fs_b).
    header = re.sub(r'/\*.*?\*/', '', inputs[HAL+'v151/hal_i2c_v151_regs_def.h'].decode(), flags=re.S)
    unions = {}
    for body, typename in re.findall(r'typedef union \w+ \{(.*?)\} (\w+_t);', header, re.S):
        for struct, member in re.findall(r'struct \{(.*?)\} (\w+);', body, re.S):
            for cfield in re.findall(r'uint32_t\s+(\w+)\s*(?::\s*\d+)?\s*;', struct):
                if not cfield.startswith('reserved'):
                    unions.setdefault(cfield, []).append((typename, member))
    for bus in (0,1):
        peripheral = svd.find(f"./peripherals/peripheral[name='I2C{bus}']")
        regs = peripheral.findall('registers/register')
        source += [f'_Static_assert(I2C_BUS_{bus}_BASE_ADDR == {int(peripheral.findtext("baseAddress"),0)}, "base");',
            f'_Static_assert(I2C_{bus}_IRQN == {peripheral.findtext("interrupt/value")}, "IRQ");']
        for reg in regs:
            name = reg.findtext('name')
            member = name[3:].lower()
            if name in ('IC_DATA_READ', 'IC_DATA_WRITE'):
                member = 'data_cmd'
            source += [f'_Static_assert(offsetof(i2c_v151_regs_t,{member}) == {int(reg.findtext("addressOffset"),0)}, "{name}");',
                f'_Static_assert(sizeof(((i2c_v151_regs_t*)0)->{member}) == 4, "width");']
            require(reg.find('resetValue') is None and reg.findtext('size') == '32', 'unknown reset/width')
            if name in ('IC_STATUS','IC_TXFLR','IC_RXFLR','IC_ENABLE_STATUS',
                        'IC_TX_ABRT_SOURCE','IC_TX_ABRT_SLV_INTX','IC_RAW_INTR_STAT','IC_INTR_STAT'):
                require(reg.findtext('access') == 'read-only', f'{name} is not RO')
            if name in ('IC_CLR_INT','IC_CLR_INTR','IC_DATA_WRITE'):
                require(reg.findtext('access') == 'write-only', f'{name} is not WO')
            if name == 'IC_DATA_READ':
                require(reg.findtext('access') == 'read-only', 'FIFO must be RO')
                require(reg.findtext("fields/field[name='DAT']/readAction") == 'modifyExternal', 'FIFO pop not described')
            for field in reg.findall('fields/field'):
                fname = field.findtext('name').lower()
                if fname == 'value':
                    require(name in ('IC_CLR_INTR','IC_INTR_STAT_ALL'), f'unexpected unknown {name}')
                    continue
                candidates = unions.get(fname, [])
                if len(candidates) > 1:
                    suffix = {'IC_RAW_INTR_STAT':'raw_intr_stat', 'IC_STATUS':'status'}.get(name)
                    if suffix:
                        candidates = [c for c in candidates if c[0] == f'ic_v151_{suffix}_data_t']
                require(len(candidates) == 1, f'ambiguous field {name}.{fname}: {candidates}')
                typename, cmember = candidates[0]
                width, shift = int(field.findtext('bitWidth')), int(field.findtext('bitOffset'))
                value = (1 << width)-1
                if bus == 0:
                    field_checks += [f'{{ {typename} v={{.d32=0}}; v.{cmember}.{fname}={value}u; assert(v.d32=={value<<shift}u); }}']
                    count += 1
        require(len(regs) == 36, f'I2C register view count {len(regs)}')
    source += ['static void fields(void) {', *field_checks, '}', r'''
static i2c_v151_regs_t backing[2];
static uint16_t gate;
void reg16_setbit(uintptr_t a,unsigned b) { assert(a==M_CTL_RB_M_CLKEN1); gate|=1u<<b; }
void reg16_clrbit(uintptr_t a,unsigned b) { assert(a==M_CTL_RB_M_CLKEN1); gate&=~(1u<<b); }
int main(void) {
    fields();
    _Static_assert(I2C_BUS_MAX_NUM==2 && CONFIG_I2C_TX_BUFFER_DEPTH==8 && CONFIG_I2C_RX_BUFFER_DEPTH==8,"instances/fifos");
    _Static_assert(sizeof(i2c_v151_regs_t)==0xc8,"span");
    gate=0x8001;
    i2c_port_clock_enable(I2C_BUS_0,true); assert(gate==0x8041);
    i2c_port_clock_enable(I2C_BUS_1,true); assert(gate==0x80c1);
    i2c_port_clock_enable(I2C_BUS_0,false); assert(gate==0x8081);
    i2c_port_clock_enable(I2C_BUS_1,false); assert(gate==0x8001);
    for (unsigned bus=0;bus<2;bus++) {
        assert(i2c_port_get_clock_value(bus)==32000000);
        g_i2c_base_addrs[bus]=(uintptr_t)&backing[bus];
    }
    hal_i2c_v151_regs_init();
    for (unsigned bus=0;bus<2;bus++) {
        uint32_t data_addr=0;
        assert(hal_i2c_v151_get_data_addr(bus,(uintptr_t)&data_addr)==ERRCODE_SUCC);
        assert(data_addr==(uint32_t)(uintptr_t)&backing[bus].data_cmd);
        // Original RMW helpers, including preservation of the other enable.
        backing[bus].dma_cr=0xa5a50000;
        hal_i2c_v151_set_receive_dma_en(bus,true); assert(backing[bus].dma_cr==0xa5a50001);
        hal_i2c_v151_set_transmit_dma_en(bus,true); assert(backing[bus].dma_cr==0xa5a50003);
        hal_i2c_v151_set_receive_dma_en(bus,false); assert(backing[bus].dma_cr==0xa5a50002);
        hal_i2c_v151_set_transmit_dma_en(bus,false); assert(backing[bus].dma_cr==0xa5a50000);
        hal_i2c_v151_set_transmit_dma_level(bus,HAL_I2C_V151_TX_DMA_TL);
        hal_i2c_v151_set_receive_dma_level(bus,HAL_I2C_V151_RX_DMA_TL-1);
        assert(backing[bus].dma_tdlr==4 && backing[bus].dma_rdlr==3);
        // Actual master prepare/restore, including address configuration.
        // Ordinary target addresses permit both source permission values;
        // the read helper's SLAVE spelling does not switch controller role.
        for(unsigned ten=0;ten<2;ten++) {
            hal_i2c_prepare_config_t cfg={.operation_type=I2C_DATA_OPERATION_TYPE_DMA,
                                         .addr=ten?0x7a50:0x50};
            backing[bus].dma_cr=0x80000000;
            assert(hal_i2c_v151_master_write_prepeare(bus,(uintptr_t)&cfg)==ERRCODE_SUCC);
            assert(backing[bus].dma_cr==0x80000002 && backing[bus].dma_tdlr==4);
            assert((backing[bus].tar&0x3ff)==(ten?0x250:0x50));
            assert(((backing[bus].con>>4)&1)==ten && (backing[bus].enable&1));
            assert(hal_i2c_v151_get_ctrl_info(bus)->write_operate_type==I2C_DATA_OPERATION_TYPE_DMA);
            assert(hal_i2c_v151_master_write_restore(bus,0)==ERRCODE_SUCC);
            assert(backing[bus].dma_cr==0x80000000);
            assert(hal_i2c_v151_master_read_prepeare(bus,(uintptr_t)&cfg)==ERRCODE_SUCC);
            assert(backing[bus].dma_cr==0x80000003 && backing[bus].dma_tdlr==4 && backing[bus].dma_rdlr==3);
            assert((backing[bus].tar&0x3ff)==(ten?0x250:0x50));
            assert(hal_i2c_v151_get_ctrl_info(bus)->read_operate_type==I2C_DATA_OPERATION_TYPE_DMA);
            assert(hal_i2c_v151_master_read_restore(bus,0)==ERRCODE_SUCC);
            assert(backing[bus].dma_cr==0x80000000);
        }
        hal_i2c_prepare_config_t bad={.operation_type=I2C_DATA_OPERATION_TYPE_DMA,.addr=0x8000};
        i2c_v151_regs_t before=backing[bus];
        assert(hal_i2c_v151_master_read_prepeare(bus,(uintptr_t)&bad)==ERRCODE_I2C_PERMISSION_INVALID);
        assert(memcmp(&before,&backing[bus],sizeof(before))==0);
        hal_i2c_v151_clr_all_int(bus); assert(backing[bus].clr_int==1);
        hal_i2c_v151_unmask_int(bus,I2C_V151_INT_TX_ABRT);
        assert(backing[bus].intr_mask==64);
        hal_i2c_v151_mask_int(bus,I2C_V151_INT_TX_ABRT); assert(!backing[bus].intr_mask);
        for (unsigned first=0;first<2;first++) for (unsigned restart=0;restart<2;restart++)
        for (unsigned last=0;last<2;last++) for (unsigned stop=0;stop<2;stop++) {
            unsigned cmd=(first&&restart ? 0x400:0)|(last&&stop ? 0x200:0);
            assert(hal_i2c_v151_get_data_read_cmd(first,restart,last,stop)==(cmd|0x100));
            assert(hal_i2c_v151_get_data_write_cmd(first,restart,last,stop)==cmd);
            hal_i2c_v151_set_transmit_data(bus,cmd,0xa5);
            assert(backing[bus].data_cmd==(cmd|0xa5));
        }
        // The selective-clear union is malformed. Record the executed behavior
        // rather than silently pretending it matches the interrupt status map.
        assert(sizeof(ic_v151_clr_intr_data_t)>4);
        hal_i2c_v151_clr_intr_set_clr_tx_abrt(bus); assert(backing[bus].clr_intr==0x10000000);
        hal_i2c_v151_clr_intr_set_clr_stop_det(bus); assert(backing[bus].clr_intr==0x80000000);
        hal_i2c_v151_clr_intr_set_clr_start_det(bus); assert(backing[bus].clr_intr==0);
        backing[bus].enable_status=7;
        hal_i2c_v151_set_clr_intr(bus,I2C_V151_INT_TX_ABRT);
        assert(backing[bus].enable_status==0); // wrong slot and right shift
        // Register helpers preserve unrelated bits, but enable RMW can replay commands.
        backing[bus].enable=2; hal_i2c_v151_set_ic_enable(bus); assert(backing[bus].enable==3);
    }
    const uint32_t speeds[]={100000,400000,1000000};
    const uint32_t high[]={153,32,6},low[]={159,40,21};
    for (unsigned i=0;i<3;i++) {
        hal_i2c_speed_mode_t mode=hal_i2c_v151_get_speed_mode(speeds[i]); assert((unsigned)mode==i);
        hal_i2c_v151_scl_cnt_calc_input_t in={.freq=32000000,.baudrate=speeds[i],.speed_mode=mode};
        hal_i2c_v151_scl_cnt_calc_output_t out={0};
        hal_i2c_v151_calc_scl_cnt(&in,&out); assert(out.hcnt==high[i] && out.lcnt==low[i]);
        hal_i2c_v151_set_scl_cnt(0,mode,&out);
    }
    assert(backing[0].ss_scl_hcnt==153 && backing[0].fs_scl_hcnt==32 && backing[0].hs_scl_hcnt==6);
    assert(hal_i2c_v151_get_speed_mode(1000001)==I2C_SPEED_MODE_HS);
    hal_i2c_v151_set_speed_mode(0,I2C_SPEED_MODE_HS+1); assert(((backing[0].con>>1)&3)==0);
    hal_i2c_v151_set_ss_scl_hcnt(0,65536); assert(backing[0].ss_scl_hcnt==0);
    hal_i2c_v151_regs_deinit(); assert(!g_i2c_v151_regs[0] && !g_i2c_v151_regs[1]);
    return 0;
}
''']
    with tempfile.TemporaryDirectory(prefix='ws53-i2c-contract-') as tmp:
        work = Path(tmp)
        errors = sorted(set(re.findall(r'ERRCODE_\w+', '\n'.join(v.decode() for v in inputs.values()))))
        stubs = {
            'errcode.h':'#include <stdint.h>\ntypedef uint32_t errcode_t;\n'+''.join(f'#define {n} {i}\n' for i,n in enumerate(errors)),
            'platform_core.h':platform_stub+'\n',
            'common_def.h':'#include <stddef.h>\n#define unused(x) ((void)(x))\n#define bit(x) (1u<<(x))\n'
                '#define uapi_reg_write32(a,v) (*(volatile uint32_t*)(a)=(v))\n',
            'soc_osal.h':'#include <stdint.h>\n#include <stdbool.h>\ntypedef int (*osal_irq_handler)(int,const void*);\n'
                'unsigned osal_irq_lock(void); void osal_irq_restore(unsigned);\n'
                'int osal_irq_request(uintptr_t,osal_irq_handler,void*,void*,void*);\n'
                'void osal_irq_enable(uintptr_t); void osal_irq_free(uintptr_t,void*);\n',
            'osal_interrupt.h':'#include "soc_osal.h"\n',
            'securec.h':'#include <string.h>\n',
            'chip_io.h':'#include <stdint.h>\nvoid reg16_setbit(uintptr_t,unsigned); void reg16_clrbit(uintptr_t,unsigned);\n',
            'bits/alltypes.h':'#include <stdint.h>\n',
        }
        for name,content in stubs.items():
            (work/name).parent.mkdir(parents=True,exist_ok=True)
            (work/name).write_text('#pragma once\n'+content)
        (work/'check.c').write_text('\n'.join(source))
        command=[args.cc,'-std=c11','-Wall','-Wextra','-Werror','-ffunction-sections','-fdata-sections']
        command += [f'-D{k}={1 if v=="y" else v}' for k,v in expected.items()]
        for inc in (work,sdk/HAL,sdk/(HAL+'v151'),sdk/(PORT+'include'),sdk/CHIP,sdk/(CHIP+'acore')):
            command += ['-I',str(inc)]
        subprocess.run([*command,str(work/'check.c'),'-Wl,--gc-sections','-o',str(work/'check')],check=True)
        subprocess.run([str(work/'check')],check=True,timeout=5)
    print(json.dumps({'sdk_commit':PIN,'input_sha256':{p:hashlib.sha256(d).hexdigest() for p,d in inputs.items()},
        'instances':2,'register_views_per_instance':36,'verified_named_fields_per_instance':count,
        'clock_hz':32000000,'fifo_depth':8,'irqs':[62,63],
        'source_speed_counts':{'100000':[153,159],'400000':[32,40],'1000000':[6,21]},
        'checks':['source offsets/widths/bitfields versus SVD','read/pop versus write/push aliases',
                  'no inferred reset values','clock gates preserve unrelated halfword bits',
                  'all restart/stop command flag combinations','write-one clear-all command',
                  'malformed selective clear masks','wrong-slot selective clear helper',
                  'enable RMW replays abort','out-of-range speed/16-bit count truncation'],
        'dma_helpers':{'data_offset':'0x1c','tx_enable_bit':1,'rx_enable_bit':0,
                       'tx_threshold':4,'rx_threshold':3,'other_enable_preserved':True,
                       'master_prepare_restore_sequences':8,'address_permission_failures':2,
                       'physical_handshake_verified':False},
        'selective_clear_layout_verified':False,'hardware_accessed':False,
        'rom_or_installed_firmware_verified':False},indent=2))


if __name__ == '__main__':
    main()
