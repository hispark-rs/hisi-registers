#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compile pinned WS53 SPI/QSPI headers and port on host RAM, never a board."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
HAL = 'src/drivers/drivers/hal/spi/'
V = HAL+'v151_100/'
PORT = 'src/drivers/chips/ws53/porting/'
CHIP = 'src/drivers/chips/ws53/include/'
CONFIG = 'src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config'
DMA = 'src/drivers/chips/ws53/rom/app/drivers/chips/ws53/porting/include/dma_porting.h'
MAPS = [dict(zip('ENABLE CONTROL FRAME_COUNT ENHANCED_CONTROL MICROWIRE BAUD DMA_CONTROL DMA_RX_LEVEL DMA_TX_LEVEL RX_SAMPLE_DELAY TX_DRIVE_EDGE RAW_INTERRUPT INTERRUPT_STATUS INTERRUPT_MASK SLAVE_ENABLE TX_THRESHOLD TX_LEVEL CLEAR_TX_OVERFLOW RX_THRESHOLD RX_LEVEL CLEAR_RX_UNDERFLOW STATUS CLEAR_MULTI_MASTER VERSION CLEAR_RX_OVERFLOW CLEAR_ALL'.split(),
    'spi_er spi_ctra spi_ctrb spi_enhctl spi_mcr spi_brs spi_dcr spi_drdl spi_dtdl spi_rsdr spi_tder spi_rainsr spi_insr spi_inmar spi_slenr spi_twlr spi_tlr spi_tfoficr spi_rwlr spi_rlr spi_rfficr spi_wsr spi_msticr spi_id spi_rxoicr spi_icr'.split())),
    dict(zip('CONTROL FRAME_COUNT ENABLE MICROWIRE SLAVE_ENABLE BAUD TX_THRESHOLD RX_THRESHOLD TX_LEVEL RX_LEVEL STATUS INTERRUPT_MASK INTERRUPT_STATUS RAW_INTERRUPT CLEAR_TX_OVERFLOW CLEAR_RX_OVERFLOW CLEAR_RX_UNDERFLOW CLEAR_MULTI_MASTER CLEAR_ALL DMA_CONTROL DMA_TX_LEVEL DMA_RX_LEVEL IDENTIFICATION VERSION RX_SAMPLE_DELAY ENHANCED_CONTROL TX_DRIVE_EDGE'.split(),
    'ctrlr0 ctrlr1 ssienr mwcr ser baudr txftlr rxftlr txflr rxflr sr imr isr risr txoicr rxoicr rxuicr msticr icr dmacr dmatdlr dmardlr idr ssi_version_id rx_sample_dly spi_ctrlr0 txd_drive_edge'.split()))]


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def clean(text):
    return re.sub(r'/\*.*?\*/', '', text, flags=re.S)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', required=True, type=Path)
    p.add_argument('--cc', default='cc')
    args = p.parse_args()
    sdk = args.sdk.resolve()
    paths = [V+x for x in ['hal_spi_v151_100_regs_def.h', 'hal_qspi_v151_100_regs_def.h',
        'hal_spi_v151_100_regs_op.h', 'hal_qspi_v151_100_regs_op.h', 'hal_spi_v151_100_adapt_op.h',
        'hal_spi_v151_100_regs_op.c', 'hal_spi_v151_100.c']]
    paths += [HAL+'CMakeLists.txt', HAL+'Kconfig', PORT+'spi/spi_porting.c', PORT+'include/spi_porting.h',
        CHIP+'acore/platform_core.h', CHIP+'acore/chip_core_irq.h', CHIP+'clock_recover.h', CONFIG, DMA]
    inputs = {}
    for path in paths:
        data = (sdk/path).read_bytes()
        require(data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{path}']), f'changed SDK input: {path}')
        inputs[path] = data
    cfg = inputs[CONFIG].decode()
    require('# CONFIG_DRIVER_SUPPORT_SPI is not set' in cfg, 'baseline SPI enable changed')
    require(not re.search(r'^CONFIG_SPI_USING_\w+=y$', cfg, re.M), 'explicit layout selection changed')
    cmake = inputs[HAL+'CMakeLists.txt'].decode()
    require(re.search(r'if\(NOT DEFINED CONFIG_SPI_USING_V100.*?v151_100/hal_spi_v151_100.c', cmake, re.S), 'fallback selection changed')
    platform = inputs[CHIP+'acore/platform_core.h'].decode()
    platform_stub = '\n'.join(re.findall(r'^#define\s+(?:SPI_BUS_\w+|HAL_SPI_DEVICE_MODE_SET_REG)\s+.*$', platform, re.M))
    platform_stub += '\n'+re.search(r'typedef enum \{\s*SPI_BUS_0 = 0,.*?\} spi_bus_t;', platform, re.S).group()
    dma_enum = re.search(r'typedef enum \{\s*HAL_DMA_HANDSHAKING_I2C0_TX,.*?\} hal_dma_handshaking_source_t;', inputs[DMA].decode(), re.S).group()
    svd = ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    code = ['#include <assert.h>', '#include <stddef.h>', '#include <stdint.h>', '#include <stdbool.h>', '#include <string.h>',
        '#include "hal_spi_v151_100_adapt_op.h"']
    code += ['#include '+json.dumps(str(sdk/x)) for x in [V+'hal_spi_v151_100_regs_op.c', PORT+'spi/spi_porting.c']]
    fields, field_report, counts = [], {}, []
    for bus, (name, typ, filename) in enumerate([('SPI0','spi_v151_100_regs_t','hal_spi_v151_100_regs_def.h'),
                                               ('QSPI1','ssi_regs_t','hal_qspi_v151_100_regs_def.h')]):
        header = clean(inputs[V+filename].decode())
        unions = {}
        for tag, body, typedef in re.findall(r'\bunion (\w+) \{(.*?)\}\s*(\w+_t)?\s*;', header, re.S):
            struct = re.search(r'struct \{(.*?)\} b;', body, re.S)
            if not struct: continue
            layout, shift = {}, 0
            for member, width in re.findall(r'uint32_t\s+(\w+)\s*(?::\s*(\d+))?\s*;', struct[1]):
                width = int(width or 32)
                if not member.startswith('reserved'): layout[shift] = (member, width)
                shift += width
            unions[typedef or 'union '+tag] = layout
            unions['union '+tag] = layout
        for tag, alias in re.findall(r'typedef union (\w+) (\w+);', header):
            unions[alias] = unions['union '+tag]
        peri = svd.find(f"./peripherals/peripheral[name='{name}']")
        regs = peri.findall('registers/register')
        code += [f'_Static_assert(SPI_BUS_{bus}_BASE_ADDR=={int(peri.findtext("baseAddress"),0)}, "base");',
                 f'_Static_assert({"SPI_3" if bus==0 else "QSPI0_2CS"}_IRQN=={peri.findtext("interrupt/value")}, "IRQ");']
        expanded = 0
        for reg in regs:
            rname = reg.findtext('name')
            is_fifo = rname.startswith(('RX[', 'TX[')) or rname in ('RX','TX')
            if is_fifo:
                member = 'spi_drnm' if bus == 0 else 'dr'
            else: member = MAPS[bus][rname]
            offset = int(reg.findtext('addressOffset'), 0)
            code += [f'_Static_assert(offsetof({typ},{member})=={offset},"{name}.{rname}");']
            require(reg.findtext('size') == '32' and reg.find('resetValue') is None, f'width/reset {rname}')
            count = int(reg.findtext('dim') or '1'); expanded += count
            if is_fifo:
                require(count == 36 and reg.findtext('dimIncrement') == '0x4', 'FIFO array contract')
                code += [f'_Static_assert(sizeof((({typ}*)0)->{member})==36*4,"FIFO windows");']
                access = 'read-only' if rname.startswith('RX') else 'write-only'
                require(reg.findtext('access') == access, f'FIFO access {rname}')
                if access == 'read-only':
                    require(reg.findtext('fields/field/readAction') == 'modifyExternal', 'pop semantics')
            if rname.startswith('CLEAR_'):
                require(reg.findtext('access') == 'read-only' and reg.findtext('fields/field/readAction') == 'clear', 'clear read contract')
            union = member+'_data_t'
            require(union in unions, f'unknown source union {union}')
            for f in reg.findall('fields/field'):
                shift, width = int(f.findtext('bitOffset')), int(f.findtext('bitWidth'))
                member_field, source_width = unions[union][shift]
                require(width == source_width, f'field mismatch {name}.{rname}.{f.findtext("name")}')
                value = (1 << width)-1
                fields += [f'{{ {union} v={{0}}; v.b.{member_field}={value}u; assert(v.d32=={value<<shift}u); }}']
                field_report[f'{name}.{rname}.{f.findtext("name")}'] = f'{union}.b.{member_field}'
        counts.append(expanded)
    clock_fields = {
        'GLOBAL_CLKEN': ('M_CTL_RB_GLB_CLKEN', 'u_glb_clken', {'XIP_QSPI_ENABLE':'glb_clken_xip_qspi', 'LSADC_ENABLE':'glb_clken_lasdc'}),
        'XIP_QSPI_CLOCK': ('M_CTL_RB_XIP_QSPI_CR', 'u_xip_qspi_cr', {'CONTROL':'xip_qspi_cr_ctrl'}),
        'XIP_CLOCK_ENABLE': ('M_CTL_RB_XIP_CLKEN0', 'u_xip_clken_0', {'QSPI1_DIVIDER_ENABLE':'qspi1_div_en',
            'QSPI1_DIVIDER_CLOCK_ENABLE':'qspi1_div_clken', 'XIP_BUS_ENABLE':'xip_bus_clken',
            'QSPI1_ENABLE':'qspi1_clken', 'QSPI1_XIP_ENABLE':'qspi1_xip_clken'}),
    }
    for name, (macro, typ, members) in clock_fields.items():
        reg = svd.find(f"./peripherals/peripheral[name='M_CRG']/registers/register[name='{name}']")
        require(reg is not None and reg.findtext('size') == '16' and reg.find('resetValue') is None, f'clock slot {name}')
        code += [f'_Static_assert({macro}==0x52000000+{int(reg.findtext("addressOffset"),0)},"{name}");']
        for field in reg.findall('fields/field'):
            shift, width = int(field.findtext('bitOffset')), int(field.findtext('bitWidth'))
            member = members[field.findtext('name')]
            value = (1 << width)-1
            fields += [f'{{ {typ} v={{0}}; v.bits.{member}={value}; assert(v.u16=={value<<shift}); }}']
    # Execute this complete original helper, with its original minimum macro.
    hal_source = inputs[V+'hal_spi_v151_100.c'].decode()
    code += [re.search(r'^#define SPI_MINUMUM_CLK_DIV\s+.*$', hal_source, re.M).group(),
             re.search(r'static void hal_spi_init_config_freq\(.*?\n\}', hal_source, re.S).group()]
    code += ['static void fields(void) {', *fields, '}', r'''
uintptr_t g_hal_spis_regs[2];
static uint32_t backing[2][64];
static const uintptr_t addresses[]={M_CTL_RB_M_CLKEN1,M_CTL_RB_MCU_PERP_SPI_CR,
    M_CTL_RB_XIP_CLKEN0,M_CTL_RB_XIP_QSPI_CR,M_CTL_RB_GLB_CLKEN,HAL_SPI_DEVICE_MODE_SET_REG};
static uint32_t values[6];
static unsigned reads32, reads16, writes16;
static unsigned slot(uintptr_t a) { for(unsigned i=0;i<6;i++) if(addresses[i]==a) return i; assert(0); return 0; }
uint32_t readl(uintptr_t a) { reads32++; return values[slot(a)]; }
uint16_t readw(uintptr_t a) { reads16++; return values[slot(a)]; }
void writew(uintptr_t a, uint16_t v) { writes16++; values[slot(a)]=(values[slot(a)]&0xffff0000)|v; }
void reg16_setbits(uintptr_t a,unsigned bit,unsigned width,unsigned v) {
    uint16_t old=readw(a),mask=((1u<<width)-1)<<bit; writew(a,(old&~mask)|((v<<bit)&mask));
}
int main(void) {
    fields();
    _Static_assert(SPI_BUS_MAX==0 && SPI_BUS_MAX_NUM==2 && SPI_CLK_FREQ==32000000,"port");
    assert(spi_porting_base_addr_get(0)==0x52087000 && spi_porting_base_addr_get(1)==0x5208d000);
    assert(spi_porting_max_slave_select_get(0)==0 && spi_porting_max_slave_select_get(1)==0);
    assert(spi_port_get_dma_trans_dest_handshaking(0)==6 && spi_port_get_dma_trans_src_handshaking(0)==7);
    assert(spi_port_get_dma_trans_dest_handshaking(1)==4 && spi_port_get_dma_trans_src_handshaking(1)==5);
    // Original port uses a 32-bit gate read but a halfword write for SPI0.
    values[0]=0xabcd0001; values[1]=0x12348000;
    spi_port_clock_enable(0,true); assert(values[0]==0xabcd0801 && values[1]==0x12340001);
    spi_port_clock_enable(0,false); assert(values[0]==0xabcd0001 && values[1]==0x12340000);
    assert(reads32==2 && writes16==4 && reads16==0);
    // QSPI gate chain preserves the other bits; control is a nine-bit field.
    values[2]=0x8210; values[3]=0xfeaa; values[4]=0x1001;
    spi_port_clock_enable(1,true);
    assert(values[2]==0x823a && values[3]==0xfe01 && values[4]==0x1081);
    spi_port_clock_enable(1,false);
    assert(values[2]==0x8210 && values[3]==0xfe00 && values[4]==0x1001);
    assert(reads16==6 && writes16==10);
    values[5]=0xaaaa; assert(spi_porting_set_device_mode(0,SPI_MODE_MASTER)==ERRCODE_SUCC);
    assert(values[5]==0xaaab && spi_porting_get_device_mode(0)==SPI_MODE_MASTER);
    assert(spi_porting_set_device_mode(1,SPI_MODE_SLAVE)==ERRCODE_NOT_SUPPORT);
    assert(spi_porting_get_device_mode(1)==SPI_MODE_MASTER);
    for(unsigned bus=0;bus<2;bus++) {
        g_hal_spis_regs[bus]=(uintptr_t)backing[bus];
        unsigned control=bus?0:4, status=bus?0x28:0xe4, dma=bus?0x4c:0x18;
        for(unsigned bits=3;bits<32;bits++) {
            backing[bus][control/4]=0;
            hal_spi_v151_100_ctrlr0_set_dfs_32(bus,bits);
            assert(backing[bus][control/4]==bits<<(bus?16:13));
        }
        for(unsigned mode=0;mode<4;mode++) {
            backing[bus][control/4]=0;
            hal_spi_v151_100_ctrlr0_set_scph(bus,mode&1);
            hal_spi_v151_100_ctrlr0_set_scpol(bus,mode>>1);
            assert(backing[bus][control/4]==mode<<(bus?6:3));
        }
        backing[bus][dma/4]=0;
        hal_spi_v151_100_dmacr_set_tdmae(bus,1); assert(backing[bus][dma/4]==(bus?2:1));
        hal_spi_v151_100_dmacr_set_rdmae(bus,1); assert(backing[bus][dma/4]==3);
        uint32_t data_addr=0;
        assert(hal_spi_v151_100_ctrl_get_dma_data_addr(bus,0,(uintptr_t)&data_addr)==0);
        assert(data_addr==(uint32_t)(uintptr_t)&backing[bus][(bus?0x60:0x2c)/4]);
        hal_spi_v151_100_dmatdlr_set_dmatdl(bus,4);
        hal_spi_v151_100_dmardlr_set_dmardl(bus,0);
        assert(backing[bus][(bus?0x50:0x20)/4]==4);
        assert(backing[bus][(bus?0x54:0x1c)/4]==0);
        hal_spi_v151_100_imr_set_txei(bus,1); assert(backing[bus][(bus?0x2c:0xc4)/4]==(bus?1:128));
        backing[bus][status/4]=1u<<(bus?0:15); assert(hal_spi_v151_100_sr_get_busy(bus)==1);
        backing[bus][status/4]=0; assert(hal_spi_v151_100_sr_get_busy(bus)==0);
        hal_spi_v151_100_ctrlr0_set(bus,7u<<16);
        assert(hal_spi_v151_100_ctrlr0_get_dfs_32(bus)==(bus?7:24)); // whole-word setter does NOT translate
        hal_spi_v151_100_baudr_set_sckdv(bus,3); assert(backing[bus][0x14/4]==3); // no even rounding
        hal_spi_v151_100_baudr_set_sckdv(bus,65536); assert(backing[bus][0x14/4]==0); // truncates
        hal_spi_init_config_freq(bus,32000000,10); assert(backing[bus][0x14/4]==3);
        hal_spi_init_config_freq(bus,32000000,12); assert(backing[bus][0x14/4]==2);
        hal_spi_init_config_freq(bus,32000000,1); assert(backing[bus][0x14/4]==32);
        unsigned clear=bus?0x48:0xf8;
        backing[bus][clear/4]=0xa4;
        hal_spi_v151_100_icr_set_any(bus); assert(backing[bus][clear/4]==0xa5); // SDK RMW; hardware read-clear is separate evidence
    }
    // Bad reserved declarations inflate unions; register slots remain uint32_t.
    assert(sizeof(spi_wsr_data_t)>4 && sizeof(spi_enhctl_data_t)>4 && sizeof(sr_data_t)>4);
    return 0;
}
''']
    with tempfile.TemporaryDirectory(prefix='ws53-spi-contract-') as tmp:
        work=Path(tmp)
        stubs = {
            'platform_core.h':platform_stub,
            'hal_spi.h':'typedef unsigned hal_spi_ctrl_id_t;', 'hal_spi_v151_100.h':'', 'std_def.h':'', 'pinctrl.h':'',
            'arch_port.h':'#define irq_prio(x) 0\n',
            'common_def.h':'#include <stdint.h>\n#include <stddef.h>\n#include <stdbool.h>\n#define unused(x) ((void)(x))\n#define BIT(x) (1u<<(x))\n',
            'errcode.h':'typedef unsigned errcode_t;\n#define ERRCODE_SUCC 0\n#define ERRCODE_INVALID_PARAM 1\n#define ERRCODE_NOT_SUPPORT 2\n',
            'dma_porting.h':dma_enum,
            'osal_interrupt.h':'#include <stdint.h>\ntypedef int (*osal_irq_handler)(int,void*);\nint osal_irq_request(uintptr_t,osal_irq_handler,void*,void*,void*);\nvoid osal_irq_set_priority(uintptr_t,unsigned); void osal_irq_enable(uintptr_t); void osal_irq_disable(uintptr_t);\nunsigned osal_irq_lock(void); void osal_irq_restore(unsigned);\n',
            'chip_io.h':'#include <stdint.h>\nuint32_t readl(uintptr_t); uint16_t readw(uintptr_t); void writew(uintptr_t,uint16_t); void reg16_setbits(uintptr_t,unsigned,unsigned,unsigned);\n',
            'bits/alltypes.h':'#include <stdint.h>\n',
        }
        for name,text in stubs.items():
            (work/name).parent.mkdir(parents=True,exist_ok=True)
            (work/name).write_text('#pragma once\n'+text+'\n')
        (work/'check.c').write_text('\n'.join(code))
        command=[args.cc,'-std=c11','-Wall','-Wextra','-Werror','-ffunction-sections','-fdata-sections','-DCONFIG_DRIVER_SUPPORT_SPI=1','-DCONFIG_SPI_SUPPORT_DMA=1']
        for inc in (work,sdk/V,sdk/(PORT+'include'),sdk/CHIP,sdk/(CHIP+'acore')):
            command+=['-I',str(inc)]
        subprocess.run([*command,str(work/'check.c'),'-Wl,--gc-sections','-o',str(work/'check')],check=True)
        subprocess.run([str(work/'check')],check=True,timeout=5)
    print(json.dumps({'sdk_commit':PIN,'input_sha256':{p:hashlib.sha256(d).hexdigest() for p,d in inputs.items()},
        'expanded_register_views':dict(zip(['SPI0','QSPI1'],counts)), 'field_correspondence':field_report,
        'baseline_driver_enabled':False,'test_enables_optional_spi_driver':True,
        'clock_rdl_fields_verified':sum(len(x[2]) for x in clock_fields.values()),
        'source_dividers_at_32MHz':{'1MHz':32,'10MHz':3,'12MHz':2},
        'checks':['C offsets, widths and all modeled fields','field helpers translate distinct layouts; whole-word helpers do not',
            'SPI0 gate uses read32/write16; QSPI chain uses three read16/write16 operations',
            'mode switch and master-only QSPI','different DMA/mask/busy bits; actual DATA-address adapter and threshold setters','divider truncation and no even rounding',
            'SDK clear helper performs RMW; physical read-clear not simulated','malformed reserved fields inflate three unions'],
        'fifo_depth_verified':False,'hardware_accessed':False,'installed_firmware_verified':False},indent=2))


if __name__ == '__main__':
    main()
