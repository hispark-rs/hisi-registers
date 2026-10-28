#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned I2S DMA C helpers with host-only allocation/MMIO/wait mocks."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from check_ws53_uart_dma_contract import function, typedef
from check_ws53_mdma_contract import PIN, PORT, CONFIG, require

DRIVER = 'src/drivers/drivers/driver/i2s/i2s.c'
IPORT = 'src/drivers/chips/ws53/porting/sio/sio_porting.c'
HAL = 'src/drivers/drivers/hal/sio/'
V = HAL+'hal_sio_v151/'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', type=Path, required=True)
    p.add_argument('--cc', default='cc')
    a = p.parse_args(); sdk = a.sdk.resolve()
    paths = [DRIVER, IPORT, HAL+'hal_sio.h', V+'hal_sio_v151.c',
             V+'hal_sio_v151_regs_op.h', V+'hal_sio_v151_regs_def.h', CONFIG,
             'src/include/driver/i2s.h', 'src/include/driver/dma.h',
             'src/drivers/chips/ws53/include/acore/platform_core.h',
             PORT+'include/dma_porting.h','src/build/config/target_config/common_config.py']
    src = {}
    for path in paths:
        data = (sdk/path).read_bytes()
        require(data == subprocess.check_output(['git','-C',str(sdk),'show',f'{PIN}:{path}']), 'changed SDK: '+path)
        src[path] = data.decode()
    require('# CONFIG_I2S_SUPPORT_DMA is not set' in src[CONFIG], 'reference DMA configuration changed')
    require('CONFIG_SIO_USING_V151=y' in src[CONFIG], 'reference IP changed')
    require("'-fno-strict-aliasing'" in src['src/build/config/target_config/common_config.py'], 'SDK aliasing flags changed')
    svd = Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd'
    per = ET.parse(svd).find("./peripherals/peripheral[name='I2S']")
    base = int(per.findtext('baseAddress'),0)
    addresses = {}
    for name,access,off in [('I2S_DUAL_TX_CHN','write-only',0xc0),('I2S_DUAL_RX_CHN','read-only',0xa0)]:
        reg = per.find(f"registers/register[name='{name}']")
        require(int(reg.findtext('addressOffset'),0)==off and reg.findtext('access')==access, 'merge DATA access')
        require(reg.findtext('size')=='32' and reg.find('resetValue') is None, 'merge DATA width/reset')
        if access=='read-only':require(reg.findtext('readAction')=='modifyExternal','consuming RX')
        addresses[name] = base+off
    c = ['#include <stdint.h>','#include <stdbool.h>','#include <stddef.h>','#include <assert.h>',
         '#include <stdio.h>','#include <string.h>',
         '#include '+json.dumps(str(sdk/(V+'hal_sio_v151_regs_def.h'))),
         'typedef unsigned sio_bus_t; typedef unsigned osal_semaphore; typedef unsigned errcode_t;',
         '#define I2S_MAX_NUMBER 1\n#define CONFIG_I2S_BUS_MAX_NUM 1\n#define SIO_BUS_0 0\n#define I2S_MODE 0',
         '#define unlikely(x) (x)\n#define unused(x) ((void)(x))\n#define OSAL_SUCCESS 0',
         '#define ERRCODE_SUCC 0\n#define ERRCODE_FAIL 1\n#define ERRCODE_INVALID_PARAM 2\n#define ERRCODE_I2S_NOT_INIT 3\n#define ERRCODE_NOT_SUPPORT 4',
         typedef(src[PORT+'include/dma_porting.h'],'hal_dma_handshaking_source_t'),
         typedef(src['src/include/driver/dma.h'],'dma_ch_user_peripheral_config_t'),
         typedef(src['src/include/driver/i2s.h'],'i2s_dma_config_t'),
         typedef(src['src/include/driver/i2s.h'],'i2s_dma_attr_t'),
         typedef(src[HAL+'hal_sio.h'],'hal_i2s_dma_attr_t'),
         typedef(src[DRIVER],'i2s_dma_trans_inf_t')]
    for text,pattern in [(src[DRIVER],r'I2S_DMA_\w+'),(src[IPORT],r'I2S_(?:BUS_0_REAL_BASE|MERGE_[TR]X_DATA_ADDR)'),
                         (src['src/drivers/chips/ws53/include/acore/platform_core.h'],r'I2S_BUS_0_BASE_ADDR'),
                         (src[V+'hal_sio_v151.c'],r'SIO_(?:LOOP_INTMASK|[RT]X_THRESHOLD|INTMASK_[TR]X_DISABLED)'),
                         (src[V+'hal_sio_v151_regs_op.h'],r'SIO_RX_FIFO_SHIFT_MASK')]:
        c += re.findall(r'^#define '+pattern+r'\s+[^\n]+',text,re.M)
    for field in ('tx_dma_enable','tx_int_threshold','rx_dma_enable','rx_int_threshold'):
        c.append(f'_Static_assert(offsetof(i2s_dma_attr_t,{field})==offsetof(hal_i2s_dma_attr_t,{field}),"attribute ABI");')
    c += ['_Static_assert(sizeof(i2s_dma_attr_t)==sizeof(hal_i2s_dma_attr_t),"attribute ABI size");',
          f'_Static_assert(I2S_MERGE_TX_DATA_ADDR=={addresses["I2S_DUAL_TX_CHN"]},"TX SVD");',
          f'_Static_assert(I2S_MERGE_RX_DATA_ADDR=={addresses["I2S_DUAL_RX_CHN"]},"RX SVD");',r'''
static i2s_dma_trans_inf_t g_tx_dma_trans[1],g_rx_dma_trans[1];
static bool g_i2s_inited;
static sio_v151_regs_t regs;
static sio_v151_regs_t *sios_v151_regs(sio_bus_t bus) { assert(bus==0);return &regs; }
static unsigned configs,starts,waits,ups,clock_on,tx_on,rx_on,live,hal_configs;
static unsigned fail_config,fail_start,fail_wait,completion_error,block_reads;
static unsigned events[32],event_count;
static dma_ch_user_peripheral_config_t captured;
static uint32_t storage[8];
static void (*callback)(uint8_t,uint8_t,uintptr_t);
static void event(unsigned n) { assert(event_count<32);events[event_count++]=n; }
static errcode_t uapi_dma_configure_peripheral_transfer_single(dma_ch_user_peripheral_config_t *cfg,
    uint8_t *ch,void (*cb)(uint8_t,uint8_t,uintptr_t),uintptr_t arg) {
    assert(arg==0x1234);captured=*cfg;callback=cb;configs++;*ch=3;event(1);return fail_config;
}
static errcode_t uapi_dma_start_transfer(uint8_t ch) {
    assert(ch==3);starts++;event(2);if(!fail_start)live=1;return fail_start;
}
static int osal_sem_down(osal_semaphore *sem) {
    assert(sem==&g_tx_dma_trans[0].dma_sem || sem==&g_rx_dma_trans[0].dma_sem);
    waits++;event(5);if(fail_wait)return 1;
    assert(live);live=0;callback(completion_error?1:0,3,0x1234);return 0;
}
static void osal_sem_up(osal_semaphore *sem) {
    assert(sem==&g_tx_dma_trans[0].dma_sem || sem==&g_rx_dma_trans[0].dma_sem);ups++;
}
static unsigned uapi_dma_get_block_ts(uint8_t ch) { assert(ch==3);block_reads++;event(6);return 7; }
static void hal_sio_set_crg_clock_enable(sio_bus_t b,bool en) { assert(b==0 && en);clock_on=1;event(3); }
static void hal_sio_set_tx_enable(sio_bus_t b,unsigned en) { assert(b==0 && en==1);tx_on=1;event(4); }
static void hal_sio_set_rx_enable(sio_bus_t b,unsigned en) { assert(b==0 && en==1);rx_on=1;event(4); }
// Explicit initialization stub: only baseline mask/threshold effects are emulated.
// The original DMA cfg and its register helpers below are executed unchanged.
static void hal_i2s_config(sio_bus_t b,unsigned mode) {
    assert(b==0 && mode==I2S_MODE);hal_configs++;
    regs.intmask=SIO_LOOP_INTMASK;regs.fifo_threshold=SIO_TX_THRESHOLD|(SIO_RX_THRESHOLD<<8);
}
''']
    for name in ('i2s_port_get_dma_trans_src_handshaking','i2s_port_get_dma_trans_dest_handshaking',
                 'i2s_porting_tx_merge_data_addr_get','i2s_porting_rx_merge_data_addr_get'):
        c.append(function(src[IPORT],name))
    for name in ('hal_sio_v151_pos_merge_set_en','hal_sio_fifo_threshold_set_tx_fifo_threshold',
                 'hal_sio_fifo_threshold_set_rx_fifo_threshold','hal_sio_set_intmask'):
        c.append(function(src[V+'hal_sio_v151_regs_op.h'],name))
    c.append(function(src[V+'hal_sio_v151.c'],'hal_sio_v151_dma_cfg'))
    c += ['static struct { void (*dma_cfg)(sio_bus_t,uintptr_t); } mock_funcs={hal_sio_v151_dma_cfg};',
          '#define g_hal_funcs (&mock_funcs)']
    for name in ('uapi_i2s_dma_config','i2s_dma_tx_isr','i2s_dma_rx_isr',
                 'i2s_dma_peripheral_tx_config','i2s_dma_peripheral_rx_config',
                 'uapi_i2s_merge_write_by_dma','uapi_i2s_merge_read_by_dma'):
        c.append(function(src[DRIVER],name))
    c += [r'''
static void reset(void) {
    memset(&regs,0,sizeof(regs));memset(g_tx_dma_trans,0,sizeof(g_tx_dma_trans));
    memset(g_rx_dma_trans,0,sizeof(g_rx_dma_trans));memset(&captured,0,sizeof(captured));
    g_i2s_inited=true;configs=starts=waits=ups=clock_on=tx_on=rx_on=live=hal_configs=0;
    fail_config=fail_start=fail_wait=completion_error=block_reads=event_count=0;
}
static int transfer(unsigned rx,unsigned bus,const void *buf,uint32_t count,i2s_dma_config_t *cfg,bool block) {
    return rx?uapi_i2s_merge_read_by_dma(bus,buf,count,cfg,0x1234,block):
              uapi_i2s_merge_write_by_dma(bus,buf,count,cfg,0x1234,block);
}
int main(void) {
    unsigned configurations=0,sequences=0,attributes=0;
    assert(i2s_port_get_dma_trans_dest_handshaking(0)==12);
    assert(i2s_port_get_dma_trans_src_handshaking(0)==13);
    const uint32_t counts[]={0,1,4,4095,4096,65535,65536,65537};
    for(unsigned rx=0;rx<2;rx++)for(unsigned sw=0;sw<3;sw++)for(unsigned dw=0;dw<3;dw++)
    for(unsigned burst=0;burst<4;burst++)for(unsigned priority=0;priority<4;priority++)for(unsigned n=0;n<8;n++) {
        dma_ch_user_peripheral_config_t cfg={.src=0x12345678,.dest=0xabcdef00};
        i2s_dma_config_t attr={sw,dw,burst,priority};
        if(rx)i2s_dma_peripheral_rx_config(&cfg,&attr,counts[n]);
        else i2s_dma_peripheral_tx_config(&cfg,&attr,counts[n]);
        assert(cfg.src==0x12345678 && cfg.dest==0xabcdef00 && cfg.transfer_num==(uint16_t)counts[n]);
        assert(cfg.src_handshaking==(rx?13:0) && cfg.dest_handshaking==(rx?0:12));
        assert(cfg.trans_dir==rx && cfg.trans_type==rx+1 && cfg.protection==1);
        assert(cfg.src_increment==(rx?2:0) && cfg.dest_increment==(rx?0:2));
        assert(cfg.src_width==sw && cfg.dest_width==dw && cfg.burst_length==burst && cfg.priority==priority);
        configurations++;
    }
    const unsigned thresholds[]={0,1,7,15,16,31,255};
    for(unsigned flags=0;flags<4;flags++)for(unsigned t=0;t<7;t++)for(unsigned r=0;r<7;r++) {
        reset();regs.i2s_pos_merge_en=0xa55a0040;
        i2s_dma_attr_t attr={flags&1,thresholds[t],flags&2,thresholds[r]};
        assert(uapi_i2s_dma_config(0,&attr)==0 && hal_configs==1);
        assert(regs.i2s_pos_merge_en==0xa55a0041);
        assert(regs.intmask==(0x3cu|((flags&1)?0x32u:0)|((flags&2)?0xdu:0)));
        assert(regs.fifo_threshold==(((flags&2)?thresholds[r]:7)<<8 | ((flags&1)?thresholds[t]:4)));
        attributes++;
    }
    reset();i2s_dma_attr_t attr={0};g_i2s_inited=false;
    assert(uapi_i2s_dma_config(0,&attr)==ERRCODE_I2S_NOT_INIT && !hal_configs);
    g_i2s_inited=true;assert(uapi_i2s_dma_config(1,&attr)==ERRCODE_INVALID_PARAM && !hal_configs);
    for(unsigned rx=0;rx<2;rx++) {
        // Six preflight cases must not reach DMA or peripheral mutation.
        for(unsigned bad=0;bad<6;bad++) {
            reset();i2s_dma_config_t cfg={2,2,0,1};if(bad==0)g_i2s_inited=false;
            int result=transfer(rx,bad==1?1:0,bad==2?NULL:storage,bad==3?0:8,bad==4?NULL:&cfg,true);
            if(bad<5)assert(result==(bad==0?ERRCODE_I2S_NOT_INIT:ERRCODE_INVALID_PARAM) && !configs && !starts);
            else assert(result==7 && configs==1 && starts==1);
            sequences++;
        }
        // Normal, config/start failure, semaphore failure, DMA error, nonblocking.
        for(unsigned fault=0;fault<6;fault++) {
            reset();i2s_dma_config_t cfg={2,2,0,1};
            fail_config=fault==1;fail_start=fault==2;fail_wait=fault==3;completion_error=fault==4;
            int result=transfer(rx,0,storage,8,&cfg,fault!=5);
            assert(result==(fault==0?7:fault==5?0:ERRCODE_FAIL));
            assert(configs==1 && starts==(fault!=1) && waits==(fault==0 || fault==3 || fault==4));
            assert(live==(fault==3 || fault==5) && tx_on==(!rx && (fault==0 || fault>=3)));
            assert(rx_on==(rx && (fault==0 || fault>=3)) && clock_on==tx_on);
            assert(captured.src==(rx?I2S_MERGE_RX_DATA_ADDR:(uint32_t)(uintptr_t)storage));
            assert(captured.dest==(rx?(uint32_t)(uintptr_t)storage:I2S_MERGE_TX_DATA_ADDR));
            assert(block_reads==(fault==0));
            assert(events[0]==1);if(fault!=1)assert(events[1]==2);
            if(fault==0 || fault>=3) {
                assert(events[2]==(rx?4:3));if(!rx)assert(events[3]==4);
            }
            if(fault==0) {
                // Completion is sticky and the old channel remains associated.
                callback(1,3,0);assert(ups==2);
                assert((rx?g_rx_dma_trans[0]:g_tx_dma_trans[0]).trans_succ);
            }
            sequences++;
        }
        // uint32 lengths and raw config values are not validated by this layer.
        reset();i2s_dma_config_t cfg={255,255,255,255};
        assert(transfer(rx,0,storage,65536,&cfg,false)==0);
        assert(captured.transfer_num==0 && captured.src_width==255 && captured.dest_width==255);
        assert(captured.burst_length==255 && captured.priority==255 && live);
        sequences++;
        // Reset channel records are zero; a stray ch0 completion wakes that record.
        reset();if(rx)i2s_dma_rx_isr(0,0,0);else i2s_dma_tx_isr(0,0,0);assert(ups==1);
        sequences++;
    }
    printf("%u %u %u\n",configurations,sequences,attributes);
}
''']
    with tempfile.TemporaryDirectory(prefix='ws53-i2s-dma-contract-') as temp:
        work=Path(temp);(work/'check.c').write_text('\n'.join(c))
        subprocess.run([a.cc,'-std=c11','-Wall','-Wextra','-Werror','-O2','-fno-strict-aliasing',str(work/'check.c'),'-o',str(work/'check')],check=True)
        configurations,sequences,attributes=map(int,subprocess.check_output([str(work/'check')],timeout=5).split())
    print(json.dumps({'sdk_commit':PIN,'config_executions':configurations,'driver_sequences':sequences,
        'dma_attribute_executions':attributes,'routes':{'tx':12,'rx':13},
        'addresses':{k:hex(v) for k,v in addresses.items()},
        'source_profile':{'flow_tx_rx':[1,2],'protection_tx_rx':[1,1],
            'width_burst_priority':'user supplied, no validation by I2S layer',
            'merge_enable':'set even when both DMA flags are false',
            'dma_flags':'select threshold and IRQ masking, not a dedicated request-enable register'},
        'source_hazards':['uint32 length narrows to uint16; 65536 becomes zero, 4096 exceeds MDMA12-bit count',
            'semaphore wait is unbounded; no channel stop or peripheral disable on failure/completion',
            'TX enables CRG after DMA start; RX does not enable CRG here',
            'completion returns DMA block count without wire-idle or FIFO drain proof',
            'stale or zero channel records accept callbacks; error does not clear previous success',
            'DMA cfg unconditionally reinitializes I2S and enables merge; false flags do not implement disable',
            'DMA cfg lacks a null-attribute guard (source inspection; not dereferenced in host tests)'],
        'mocks':['DMA allocation/config/start capture and immediate scheduled callback; no data movement',
            'osal_sem_down returns injected error or immediate completion; no real blocking',
            'clock/TX/RX setters only record calls; no hardware request or FIFO behavior',
            'hal_i2s_config stub applies source baseline mask/threshold only; reset/divider side effects not executed',
            'DMA cfg, merge/threshold/mask helpers and port getters execute original C against host RAM'],
        'not_verified':['physical request polarity/threshold relation or burst ACK',
            'merge data order/packing and legal DMA access widths','short RX tail and FIFO underrun/overrun',
            'cache/control-core ownership/physical drain','safe dynamic switching between PIO and DMA'],
        'compiler_flags':['-std=c11','-Wall','-Wextra','-Werror','-O2','-fno-strict-aliasing'],
        'aliasing_note':'SDK common_config disables strict aliasing; public/HAL attribute structs are distinct types with checked layout',
        'reference_dma_enabled':False,'hardware_accessed':False,
        'input_sha256':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in src.items()},
        'svd_sha256':hashlib.sha256(svd.read_bytes()).hexdigest()},indent=2))


if __name__=='__main__':main()
