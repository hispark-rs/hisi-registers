#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned WS53 I2C DMA driver helpers with explicit host-only mocks."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from check_ws53_uart_dma_contract import function, typedef
from check_ws53_mdma_contract import PIN, ROM, PORT, CONFIG, require

DRIVER = 'src/drivers/drivers/driver/i2c/i2c.c'
HAL = 'src/drivers/drivers/hal/i2c/hal_i2c.h'
IPORT = 'src/drivers/chips/ws53/porting/i2c/i2c_porting.c'
PLATFORM = 'src/drivers/chips/ws53/include/acore/platform_core.h'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', type=Path, required=True)
    p.add_argument('--cc', default='cc')
    a = p.parse_args(); sdk = a.sdk.resolve()
    paths = [DRIVER, HAL, IPORT, PLATFORM, CONFIG, 'src/include/driver/i2c.h',
             'src/include/driver/dma.h', PORT+'include/dma_porting.h',
             ROM+'drivers/drivers/hal/dma/romable/hal_dma.h']
    sources = {}
    for path in paths:
        data = (sdk/path).read_bytes()
        require(data == subprocess.check_output(['git','-C',str(sdk),'show',f'{PIN}:{path}']), 'changed SDK: '+path)
        sources[path] = data.decode()
    require('# CONFIG_I2C_SUPPORT_DMA is not set' in sources[CONFIG], 'DMA reference profile changed')
    require('# CONFIG_I2C_SUPPORT_SLAVE is not set' in sources[CONFIG], 'slave reference profile changed')
    driver = sources[DRIVER]
    require('static uint16_t read_cmd = 0x100;' in function(driver,'i2c_transmit_read_cmd_by_dma'),
            'fixed read-command source changed')
    root = Path(__file__).resolve().parents[1]
    svd = root/'generated/WS53.experimental.svd'; tree = ET.parse(svd)
    addresses = []
    for bus in range(2):
        per = tree.find(f"./peripherals/peripheral[name='I2C{bus}']")
        for name, access in [('IC_DATA_READ','read-only'), ('IC_DATA_WRITE','write-only')]:
            reg = per.find(f"registers/register[name='{name}']")
            require(int(reg.findtext('addressOffset'),0) == 0x1c and reg.findtext('access') == access, 'DATA views')
        addresses.append(int(per.findtext('baseAddress'),0)+0x1c)
    c = ['#include <assert.h>','#include <stdbool.h>','#include <stdint.h>','#include <stdio.h>',
         '#include <string.h>', 'typedef unsigned errcode_t; typedef unsigned i2c_bus_t; typedef unsigned osal_semaphore;',
         '#define I2C_BUS_0 0\n#define I2C_BUS_1 1\n#define I2C_BUS_NONE 2\n#define I2C_BUS_MAX_NUM 2\n#define I2C_BUS_MAX_NUMBER 2',
         '#define CONFIG_I2C_SUPPORT_DMA 1\n#define CONFIG_I2C_WAIT_CONDITION_TIMEOUT 1000\n#define OSAL_SUCCESS 0',
         '#define unused(x) ((void)(x))\n#define bit(x) (1u<<(x))']
    errors = sorted(set(re.findall(r'ERRCODE_\w+',driver)) - {'ERRCODE_SUCC'})
    c += ['#define ERRCODE_SUCC 0'] + [f'#define {name} {i+1}' for i,name in enumerate(errors)]
    c += [typedef(sources[PORT+'include/dma_porting.h'],'hal_dma_handshaking_source_t')]
    dma = sources[ROM+'drivers/drivers/hal/dma/romable/hal_dma.h']
    for typ in ('hal_dma_trans_type_t','hal_dma_protection_control_t','hal_dma_address_inc_t',
                'dma_ch_transfer_dir_t','hal_dma_data_width_t','hal_dma_burst_transaction_length_t','hal_dma_ch_priority_t'):
        c.append(typedef(dma,typ))
    c += [typedef(sources['src/include/driver/dma.h'],'dma_ch_user_peripheral_config_t')]
    for typ in ('hal_i2c_data_operation_type_t','hal_i2c_ctrl_id_t','hal_i2c_prepare_config_t','hal_i2c_buffer_wrap_t'):
        c.append(typedef(sources[HAL],typ))
    for typ in ('i2c_data_t','i2c_wait_condition_t'):
        c.append(typedef(sources['src/include/driver/i2c.h'],typ))
    c += [typedef(driver,'i2c_ctrl_block_info_t'),typedef(driver,'i2c_dma_trans_inf_t'),
          '\n'.join(re.findall(r'^#define DMA_HANDSHAKE_I2C_BUS_[01]_[TR]X\s+.*$',sources[PLATFORM],re.M)),
          f'static const uint32_t addresses[2]={{0x{addresses[0]:x},0x{addresses[1]:x}}};', r'''
static i2c_ctrl_block_info_t g_i2c_ctrl[2];
static i2c_dma_trans_inf_t g_dma_trans[2];
static dma_ch_user_peripheral_config_t captured[4];
static unsigned bus_under_test,configs,starts,waits,requests,restores,flushes,cpu_writes,wire_waits,sem_ups;
static unsigned fail_config,fail_start,fail_wait,fail_addr,fail_flush,fail_cpu,completion_error,live;
static unsigned events[32],n_events;
static uint8_t storage[32];
static void i2c_dma_isr(uint8_t,uint8_t,uintptr_t);
static void event(unsigned e) { assert(n_events<32);events[n_events++]=e; }
static errcode_t uapi_dma_configure_peripheral_transfer_single(dma_ch_user_peripheral_config_t *cfg,uint8_t *ch,
        void (*cb)(uint8_t,uint8_t,uintptr_t),uintptr_t arg) {
    (void)arg;assert(cb==i2c_dma_isr);assert(configs<4);captured[configs++]=*cfg;
    *ch=cfg->trans_dir?1:0;event(10+*ch);
    return configs==fail_config?ERRCODE_I2C_DMA_CONFIG_ERROR:0;
}
static errcode_t uapi_dma_start_transfer(uint8_t ch) {
    event(20+ch);if(++starts==fail_start)return ERRCODE_I2C_DMA_CONFIG_ERROR;
    live|=1u<<ch;return 0;
}
static void osal_sem_up(osal_semaphore *sem) { assert(sem==&g_dma_trans[bus_under_test].dma_sem);sem_ups++; }
static int osal_sem_down(osal_semaphore *sem) {
    assert(sem==&g_dma_trans[bus_under_test].dma_sem);event(30+waits);waits++;
    if(waits==fail_wait)return 1;
    // Explicit schedule: TX completion first if present, otherwise RX.
    unsigned ch=(live&1)?0:1;assert(live&(1u<<ch));live&=~(1u<<ch);
    i2c_dma_isr(completion_error?1:0,ch,0);return 0;
}
static errcode_t hal_i2c_ctrl(i2c_bus_t b,hal_i2c_ctrl_id_t id,uintptr_t p) {
    assert(b==bus_under_test);
    if(id==I2C_CTRL_GET_DMA_DATA_ADDR) { if(!fail_addr)*(uint32_t*)p=addresses[b];return fail_addr; }
    if(id==I2C_CTRL_WRITE_PREPARE || id==I2C_CTRL_READ_PREPARE) {
        hal_i2c_prepare_config_t *cfg=(void*)p;
        assert(cfg->addr==0x50 && cfg->operation_type==I2C_DATA_OPERATION_TYPE_DMA);
        requests=id==I2C_CTRL_WRITE_PREPARE?2:3;event(40+requests);return 0;
    }
    if(id==I2C_CTRL_WRITE_RESTORE || id==I2C_CTRL_READ_RESTORE) { requests=0;restores++;event(50);return 0; }
    assert(id==I2C_CTRL_FLUSH_RX_FIFO);
    hal_i2c_buffer_wrap_t *buf=(void*)p;assert(buf->len==1 && buf->stop_flag);
    flushes++;event(60);return fail_flush?ERRCODE_I2C_DMA_TRANSFER_ERROR:0;
}
static errcode_t i2c_write(i2c_bus_t b,hal_i2c_buffer_wrap_t *buf) {
    assert(b==bus_under_test && buf->len==1 && buf->stop_flag && !buf->restart_flag);
    assert(buf->buffer>=storage && buf->buffer<storage+sizeof(storage));cpu_writes++;event(61);
    return fail_cpu?ERRCODE_I2C_DMA_TRANSFER_ERROR:0;
}
static errcode_t i2c_wait(i2c_bus_t b,i2c_wait_condition_t *cond,uint32_t timeout) {
    assert(b==bus_under_test && cond->ctrl_mask==(1u<<I2C_CTRL_CHECK_TX_PROCESS_DONE) && timeout==1000);
    wire_waits++;event(62);return 0;
}
static void i2c_mutex_lock(i2c_bus_t b) { assert(b==bus_under_test); }
static void i2c_mutex_unlock(i2c_bus_t b) { assert(b==bus_under_test); }
static errcode_t i2c_poll_read(i2c_bus_t b,uint16_t a,hal_i2c_buffer_wrap_t *w,bool stop) {
    (void)b;(void)a;(void)w;(void)stop;assert(0);return 1;
}
''']
    for name in ('i2c_port_get_dma_trans_dest_handshaking','i2c_port_get_dma_trans_src_handshaking'):
        c.append(function(sources[IPORT],name))
    for name in ('i2c_bus_init_check','i2c_read_pre_check','i2c_ctrl_get_mask','i2c_int_mode_down',
                 'i2c_int_mode_up','i2c_dma_isr','i2c_write_by_dma_config','i2c_read_by_dma_config',
                 'i2c_start_transmit_dma','i2c_transmit_dma','i2c_transmit_read_cmd_by_dma','i2c_start_recv_dma',
                 'i2c_write_by_dma','i2c_read_by_dma','i2c_read_do_send_cmd','uapi_i2c_master_read'):
        c.append(function(driver,name))
    c += [r'''
static void reset(unsigned b) {
    bus_under_test=b;configs=starts=waits=requests=restores=flushes=cpu_writes=wire_waits=sem_ups=live=n_events=0;
    fail_config=fail_start=fail_wait=fail_addr=fail_flush=fail_cpu=completion_error=0;
    memset(g_dma_trans,0,sizeof(g_dma_trans));memset(captured,0,sizeof(captured));
    g_i2c_ctrl[b]=(i2c_ctrl_block_info_t){.init_flag=true,.master_flag=true,.operate_type=I2C_DATA_OPERATION_TYPE_DMA};
}
int main(void) {
    unsigned config_cases=0,sequences=0;
    const uint32_t counts[]={0,1,3,4,5,4095,4096,65535,65536,65537};
    for(unsigned b=0;b<2;b++) {
        assert(i2c_port_get_dma_trans_dest_handshaking(b)==b*2);
        assert(i2c_port_get_dma_trans_src_handshaking(b)==b*2+1);
        for(unsigned mode=0;mode<3;mode++) for(unsigned n=0;n<10;n++) {
            reset(b);dma_ch_user_peripheral_config_t cfg={0};
            if(mode==0)i2c_write_by_dma_config(b,(void*)storage,counts[n],&cfg);
            else if(mode==1)i2c_read_by_dma_config(b,storage,counts[n],&cfg);
            else {hal_i2c_buffer_wrap_t w={.buffer=storage,.len=counts[n]};
                assert(i2c_transmit_read_cmd_by_dma(b,&w)==0);cfg=captured[0];}
            bool rx=mode==1;
            assert(cfg.transfer_num==(uint16_t)counts[n] && cfg.src_width==(mode==2) && cfg.dest_width==(mode==2));
            assert(cfg.burst_length==1 && cfg.priority==0 && cfg.protection==(rx?2:1));
            assert(cfg.trans_type==(rx?2:1) && cfg.trans_dir==rx);
            assert(cfg.src_increment==(mode?2:0) && cfg.dest_increment==(rx?0:2));
            assert(cfg.src_handshaking==(rx?b*2+1:0) && cfg.dest_handshaking==(rx?0:b*2));
            assert((rx?cfg.src:cfg.dest)==addresses[b]);
            if(mode!=2)assert((rx?cfg.dest:cfg.src)==(uint32_t)(uintptr_t)storage);
            config_cases++;
        }
        reset(b);fail_addr=1;dma_ch_user_peripheral_config_t cfg={0};
        i2c_write_by_dma_config(b,(void*)storage,17,&cfg);assert(cfg.dest==0);
        i2c_read_by_dma_config(b,storage,17,&cfg);assert(cfg.src==0);
        // Execute public API precheck: zero length is rejected before subtracting one.
        reset(b);i2c_data_t data={.receive_buf=storage};
        assert(uapi_i2c_master_read(b,0x50,&data)==ERRCODE_I2C_RECEIVE_PARAM_INVALID && configs==0);
        const unsigned lengths[]={1,3,4,5,17};
        for(unsigned i=0;i<5;i++) {
            reset(b);data.receive_len=lengths[i];assert(uapi_i2c_master_read(b,0x50,&data)==0);
            assert(configs==(lengths[i]>1?2:1) && waits==configs && starts==configs);
            assert(captured[0].transfer_num==lengths[i] && captured[0].trans_dir==1);
            if(lengths[i]>1)assert(captured[1].transfer_num==lengths[i]-1 && captured[1].src_width==1);
            assert(flushes==1 && restores==1 && requests==0 && live==0);
            assert(g_dma_trans[b].read_channel==2 && g_dma_trans[b].write_channel==(lengths[i]>1));
            if(lengths[i]==17){const unsigned order[]={43,11,21,10,20,30,60,31,50};
                assert(n_events==9 && memcmp(order,events,sizeof(order))==0);}
            sequences++;
        }
        for(unsigned fault=1;fault<=8;fault++) {
            reset(b);data.receive_len=17;
            if(fault<=2)fail_config=fault;
            else if(fault<=4)fail_start=fault-2;
            else if(fault<=6)fail_wait=fault-4;
            else if(fault==7)completion_error=1;
            else fail_flush=1;
            int result=uapi_i2c_master_read(b,0x50,&data);
            // Both DMA error callbacks and the final-command HAL error are ignored.
            assert(result==(fault<=4?ERRCODE_I2C_DMA_CONFIG_ERROR:fault<=6?ERRCODE_I2C_DMA_TRANSFER_ERROR:0));
            assert(restores==(fault>=6) && requests==(fault>=6?0:3));
            assert(live==(fault==2 || fault==4 || fault==6?2:fault==5?3:0));
            if(fault==7)assert(!g_dma_trans[b].trans_succ && sem_ups==2);
            sequences++;
        }
        for(unsigned fault=0;fault<6;fault++) {
            reset(b);hal_i2c_buffer_wrap_t w={.buffer=storage,.len=17,.stop_flag=true};
            if(fault==1)fail_config=1;else if(fault==2)fail_start=1;else if(fault==3)fail_wait=1;
            else if(fault==4)completion_error=1;else if(fault==5)fail_cpu=1;
            int result=i2c_write_by_dma(b,0x50,&w);
            assert(result==(fault==0?0:fault<=2?ERRCODE_I2C_DMA_CONFIG_ERROR:ERRCODE_I2C_DMA_TRANSFER_ERROR));
            assert(captured[0].transfer_num==16 && captured[0].src_width==0);
            assert(restores==(fault==0 || fault==5) && requests==(fault==0 || fault==5?0:2));
            assert(live==(fault==3?1:0) && cpu_writes==(fault==0 || fault==5) && wire_waits==(fault==0));
            sequences++;
        }
        // Shared ISR success flag is sticky: an error cannot clear earlier success.
        reset(b);g_dma_trans[b].read_channel=2;g_dma_trans[b].write_channel=1;
        i2c_dma_isr(0,0,0);i2c_dma_isr(1,1,0);
        assert(g_dma_trans[b].trans_succ && sem_ups==2);
        // Stale records after successful master read can wake the old bus again.
        reset(b);data.receive_len=17;assert(uapi_i2c_master_read(b,0x50,&data)==0);
        i2c_dma_isr(0,1,0);assert(sem_ups==3);
    }
    assert(i2c_port_get_dma_trans_dest_handshaking(2)==HAL_DMA_HANDSHAKING_MAX_NUM);
    assert(i2c_port_get_dma_trans_src_handshaking(2)==HAL_DMA_HANDSHAKING_MAX_NUM);
    printf("%u %u\n",config_cases,sequences);return 0;
}
''']
    with tempfile.TemporaryDirectory(prefix='ws53-i2c-dma-contract-') as tmp:
        work = Path(tmp); (work/'check.c').write_text('\n'.join(c))
        subprocess.run([a.cc,'-std=c11','-Wall','-Wextra','-Werror','-O2',str(work/'check.c'),'-o',str(work/'check')],check=True)
        configs,sequences = map(int,subprocess.check_output([str(work/'check')],timeout=5).split())
    print(json.dumps({'sdk_commit':PIN,'config_executions':configs,'driver_sequences':sequences,
        'routes':[{'instance':f'I2C{b}','tx':b*2,'rx':b*2+1,'data':hex(addresses[b])} for b in range(2)],
        'source_profile':{'tx_width_bits':8,'rx_width_bits':8,'read_command_width_bits':16,
            'read_command':'0x100','read_command_storage':'static, fixed address',
            'burst_encoding':1,'burst_beats':4,'tx_protection':1,'rx_protection':2,'flow_tx_rx':[1,2]},
        'source_hazards':['length cast to uint16: 65536 becomes zero; 4096 exceeds MDMA12-bit counter',
            'DATA-provider errors ignored, permitting address zero',
            'DMA semaphore wait uses unbounded osal_sem_down, not timeout',
            'early setup/start/wait failures leave requests enabled; started peer channel is not stopped',
            'master read returns success after DMA error callbacks and ignores final READ-command HAL failure',
            'master read leaves channel records after success; shared ISR success flag does not distinguish channel or clear earlier success on error'],
        'zero_length_public_read_rejected':True,
        'mocks':['DMA allocation/config/start and scheduled completion callbacks; no actual transfer',
                 'semaphore returns injected error or scheduled callback immediately; no real wait',
                 'HAL prepare/restore and final read/write commands record sequencing, not FIFO or wire behavior',
                 'HAL DATA address uses SVD; actual provider and DMA register setters checked separately by check_ws53_i2c_contract.py'],
        'not_verified':['slave API','physical DMA byte/halfword lanes and HPROT','burst ACK and short RX tail handshake',
                        'cache/clock/control-core ownership or bus quiescence'],
        'reference_dma_enabled':False,'hardware_accessed':False,
        'input_sha256':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()},
        'svd_sha256':hashlib.sha256(svd.read_bytes()).hexdigest()},indent=2))


if __name__ == '__main__': main()
