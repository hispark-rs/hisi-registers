#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute original WS53 SPI DMA config and duplex sequencing with explicit mocks."""
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

DRIVER = 'src/drivers/drivers/driver/spi/spi.c'
SPI_PORT = 'src/drivers/chips/ws53/porting/'
HAL = 'src/drivers/drivers/hal/spi/hal_spi.h'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', required=True, type=Path)
    p.add_argument('--cc', default='cc')
    a = p.parse_args(); sdk = a.sdk.resolve()
    paths = [DRIVER, SPI_PORT+'spi/spi_porting.c', SPI_PORT+'include/spi_porting.h',
             PORT+'include/dma_porting.h', ROM+'drivers/drivers/hal/dma/romable/hal_dma.h',
             HAL, 'src/include/driver/spi.h', 'src/include/driver/dma.h', CONFIG]
    src = {}
    for name in paths:
        data = (sdk/name).read_bytes()
        require(data == subprocess.check_output(['git','-C',str(sdk),'show',f'{PIN}:{name}']), 'changed SDK: '+name)
        src[name] = data.decode()
    require('# CONFIG_DRIVER_SUPPORT_SPI is not set' in src[CONFIG], 'SPI reference configuration changed')
    root = Path(__file__).resolve().parents[1]
    svd = root/'generated/WS53.experimental.svd'; tree = ET.parse(svd)
    addresses = []
    for name, off in [('SPI0',0x2c),('QSPI1',0x60)]:
        per = tree.find(f"./peripherals/peripheral[name='{name}']")
        # FIFO arrays have independent consuming RX and write-only TX views.
        fifo = [r for r in per.findall('registers/register') if int(r.findtext('addressOffset'),0) == off]
        require({r.findtext('access') for r in fifo} == {'read-only','write-only'}, 'FIFO access views')
        addresses.append(int(per.findtext('baseAddress'),0)+off)
    driver = src[DRIVER]; dma = src[ROM+'drivers/drivers/hal/dma/romable/hal_dma.h']
    c = ['#include <stdint.h>','#include <stdbool.h>','#include <stddef.h>','#include <stdio.h>',
         '#include <string.h>','#include <assert.h>', 'typedef unsigned errcode_t; typedef unsigned spi_bus_t; typedef unsigned osal_semaphore;',
         '#define SPI_BUS_MAX_NUM 2\n#define SPI_BUS_0 0\n#define SPI_BUS_1 1\n#define DMA_CHANNEL_NONE 8',
         '#define ERRCODE_SUCC 0\n#define ERRCODE_INVALID_PARAM 1\n#define ERRCODE_SPI_DMA_CONFIG_ERROR 2\n#define ERRCODE_SPI_DMA_TRANSFER_ERROR 3\n#define OSAL_SUCCESS 0',
         '#define SPI_CTRL_GET_DMA_DATA_ADDR 1\n#define SPI_CTRL_SET_DMA_CFG 2\n#define bit(x) (1u<<(x))\n#define unused(x) ((void)(x))',
         typedef(src[PORT+'include/dma_porting.h'],'hal_dma_handshaking_source_t')]
    for typ in ['hal_dma_trans_type_t','hal_dma_protection_control_t','hal_dma_address_inc_t','dma_ch_transfer_dir_t']:
        c.append(typedef(dma, typ))
    c += [typedef(src['src/include/driver/dma.h'],'dma_ch_user_peripheral_config_t'),
          typedef(src['src/include/driver/spi.h'],'spi_dma_config_t'),
          typedef(src[HAL],'hal_spi_xfer_data_t'), 'typedef hal_spi_xfer_data_t spi_xfer_data_t;',
          typedef(src[HAL],'hal_spi_dma_cfg_param_t'), typedef(driver,'spi_dma_trans_inf_t'),
          '\n'.join(re.findall(r'^#define DMA_HANDSHAKE_SPI_BUS_[01]_[TR]X\s+.*$',src[SPI_PORT+'include/spi_porting.h'],re.M)),
          '\n'.join(re.findall(r'^#define SPI_DMA_TX_DATA_LEVEL_4\s+.*$',src[SPI_PORT+'spi/spi_porting.c'],re.M)),
          '''static spi_dma_trans_inf_t g_dma_trans_tx[2],g_dma_trans_rx[2];
static spi_dma_config_t g_dma_cfg[2];
static dma_ch_user_peripheral_config_t captured;
static unsigned config_calls, fail_config, start_calls, fail_start, wait_calls, fail_wait, ends, requests, request_history;
static unsigned addr_failure, log_count, events[32];
static void event(unsigned e) { assert(log_count<32); events[log_count++]=e; }
static void spi_dma_isr(uint8_t t,uint8_t ch,uintptr_t arg) { (void)t;(void)ch;(void)arg; }
static errcode_t uapi_dma_configure_peripheral_transfer_single(dma_ch_user_peripheral_config_t *cfg,uint8_t *ch,
        void (*cb)(uint8_t,uint8_t,uintptr_t),uintptr_t arg) {
    (void)cb; config_calls++; captured=*cfg; event(arg?10:11);
    if(config_calls==fail_config) return 1;
    *ch=arg?1:0; return 0;
}
static errcode_t uapi_dma_start_transfer(uint8_t ch) { event(ch?20:21);return ++start_calls==fail_start?1:0; }
static void uapi_dma_end_transfer(uint8_t ch) { event(ch?40:41); ends|=1u<<ch; }
static int osal_sem_down_timeout(osal_semaphore *sem,uint32_t timeout) {
    (void)sem;(void)timeout;event(30+wait_calls);wait_calls++;
    for(unsigned b=0;b<2;b++){g_dma_trans_tx[b].trans_succ=true;g_dma_trans_rx[b].trans_succ=true;}
    return wait_calls==fail_wait?1:0;
}
static unsigned spi_porting_lock(spi_bus_t b) { (void)b;return 0; }
static void spi_porting_unlock(spi_bus_t b,unsigned s) { (void)b;(void)s; }
''',f'static const uint32_t mock_data_addresses[2]={{0x{addresses[0]:x},0x{addresses[1]:x}}};',
          '''static errcode_t hal_spi_ctrl(spi_bus_t b,unsigned id,uintptr_t p) {
    if(id==SPI_CTRL_GET_DMA_DATA_ADDR) {if(!addr_failure)*(uint32_t*)p=mock_data_addresses[b];return addr_failure;}
    hal_spi_dma_cfg_param_t *cfg=(void*)p;
    assert(cfg->dma_tx_level==4 && cfg->dma_rx_level==0);
    requests=cfg->is_enable;request_history=(request_history<<1)|requests;event(requests?51:50);return 0;
}''']
    for name in ['spi_port_get_dma_trans_dest_handshaking','spi_port_get_dma_trans_src_handshaking',
                 'spi_port_tx_data_level_get','spi_port_rx_data_level_get']:
        c.append(function(src[SPI_PORT+'spi/spi_porting.c'],name))
    for name in ['spi_dma_common_config','spi_dma_tx_config','spi_dma_rx_config','spi_read_dma_config',
                 'spi_write_dma_config','spi_writeread_dma_clear_trans','spi_dma_request_enable','spi_writeread_dma']:
        c.append(function(driver,name))
    c += ['''static void reset(void) {
    config_calls=start_calls=wait_calls=ends=requests=request_history=log_count=0;
    fail_config=fail_start=fail_wait=addr_failure=0;
    memset(g_dma_trans_tx,0,sizeof(g_dma_trans_tx));memset(g_dma_trans_rx,0,sizeof(g_dma_trans_rx));
}
int main(void) {
    unsigned cases=0;const uint32_t counts[]={1,17,4095,4096,16383,16384,32768,65535,65536,65537};
    for(unsigned bus=0;bus<2;bus++) {
        assert(spi_port_get_dma_trans_dest_handshaking(bus)==(bus?4:6));
        assert(spi_port_get_dma_trans_src_handshaking(bus)==(bus?5:7));
        assert(spi_port_tx_data_level_get(bus)==4 && spi_port_rx_data_level_get(bus)==0);
        for(unsigned width=0;width<3;width++) for(unsigned i=0;i<10;i++) for(unsigned rx=0;rx<2;rx++) {
            reset();g_dma_cfg[bus]=(spi_dma_config_t){.src_width=width,.dest_width=width,.burst_length=0,.priority=0};
            uint32_t n=counts[i]<<width;
            spi_xfer_data_t data={.tx_buff=(void*)(uintptr_t)0x20020000,.rx_buff=(void*)(uintptr_t)0x20030000,.tx_bytes=n,.rx_bytes=n};
            uint8_t ch=255;
            assert((rx?spi_read_dma_config(bus,&ch,&data):spi_write_dma_config(bus,&ch,&data))==0);
            assert(config_calls==1 && ch==(rx?1:0));
            assert(captured.transfer_num==((uint16_t)n>>width));
            assert(captured.src==(rx?mock_data_addresses[bus]:0x20020000));
            assert(captured.dest==(rx?0x20030000:mock_data_addresses[bus]));
            assert(captured.src_width==width && captured.dest_width==width);
            assert(captured.src_increment==(rx?2:0) && captured.dest_increment==(rx?0:2));
            assert(captured.src_handshaking==(rx?(bus?5:7):0));
            assert(captured.dest_handshaking==(rx?0:(bus?4:6)));
            assert(captured.trans_type==(rx?2:1) && captured.trans_dir==rx && captured.protection==2);
            if(width){data.tx_bytes=data.rx_bytes=3;assert((rx?spi_read_dma_config(bus,&ch,&data):spi_write_dma_config(bus,&ch,&data))==ERRCODE_INVALID_PARAM);}
            cases++;
        }
        // Original DATA-provider failures are ignored and permit address zero.
        reset();addr_failure=1;g_dma_cfg[bus]=(spi_dma_config_t){0};
        spi_xfer_data_t data={.tx_buff=(void*)(uintptr_t)0x20020000,.rx_buff=(void*)(uintptr_t)0x20030000,.tx_bytes=17,.rx_bytes=17};
        uint8_t ch;assert(spi_write_dma_config(bus,&ch,&data)==0 && captured.dest==0);
        assert(spi_read_dma_config(bus,&ch,&data)==0 && captured.src==0);
        // Distinct widths are accepted as supplied; no frame-size compatibility guard.
        reset();g_dma_cfg[bus]=(spi_dma_config_t){.src_width=0,.dest_width=2};
        assert(spi_read_dma_config(bus,&ch,&data)==0 && captured.src_width==0 && captured.dest_width==2);
        g_dma_cfg[bus]=(spi_dma_config_t){0};
        for(unsigned fault=0;fault<7;fault++) {
            reset();if(fault<3)fail_config=fault;else if(fault<5)fail_start=fault-2;else fail_wait=fault-4;
            int ret=spi_writeread_dma(bus,&data,9);
            if(!fault){unsigned expected[]={10,50,20,11,21,51,30,31};assert(ret==0 && log_count==8 && memcmp(events,expected,sizeof(expected))==0);}
            else assert(ret==(fault>=5?ERRCODE_SPI_DMA_TRANSFER_ERROR:ERRCODE_SPI_DMA_CONFIG_ERROR));
            assert(ends==((fault==2 || fault==4)?2: fault>=5?3:0));
            assert(requests==(fault==0 || fault>=5)); // timeout does not disable peripheral requests
            if(fault==4)assert(g_dma_trans_rx[bus].channel==2); // ended channel remains recorded
        }
    }
    assert(spi_port_get_dma_trans_dest_handshaking(2)==HAL_DMA_HANDSHAKING_MAX_NUM);
    assert(spi_port_get_dma_trans_src_handshaking(2)==HAL_DMA_HANDSHAKING_MAX_NUM);
    printf("%u\\n",cases);return 0;
}''']
    with tempfile.TemporaryDirectory(prefix='ws53-spi-dma-contract-') as tmp:
        work = Path(tmp); (work/'check.c').write_text('\n'.join(c))
        subprocess.run([a.cc,'-std=c11','-Wall','-Wextra','-Werror','-O2',str(work/'check.c'),'-o',str(work/'check')],check=True)
        cases = int(subprocess.check_output([str(work/'check')],timeout=5))
    print(json.dumps({'sdk_commit':PIN,'config_executions':cases,'duplex_sequences':14,
        'routes':[{'instance':'SPI0','tx':6,'rx':7,'data':hex(addresses[0])},
                  {'instance':'QSPI1','tx':4,'rx':5,'data':hex(addresses[1])}],
        'source_profile':{'tx_level':4,'rx_level':0,'protection':2,'flow_tx_rx':[1,2]},
        'source_hazards':['length cast to uint16 before shifting: 65536 bytes becomes zero; count4096 survives but exceeds MDMA12-bit',
            'DATA-provider errors ignored, permitting address zero','different widths accepted without frame compatibility check',
            'duplex timeouts request DMA stop but leave peripheral DMA enables set',
            'TX-start failure ends RX channel but leaves its software channel field recorded'],
        'mocks':['DMA config/start/stop and semaphore completions; no transfer occurs',
                 'HAL DATA provider uses SVD address; injected failure returns no address',
                 'SPI request control records sequence, not bus-level ACK or drain'],
        'automatic_width_branch_verified':False,'reference_driver_enabled':False,'hardware_accessed':False,
        'input_sha256':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in src.items()},
        'svd_sha256':hashlib.sha256(svd.read_bytes()).hexdigest()},indent=2))


if __name__ == '__main__': main()
