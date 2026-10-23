#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned UART DMA configuration helpers over host RAM, never hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from check_ws53_mdma_contract import PIN, ROM, PORT, CONFIG, require

UART = 'src/drivers/drivers/hal/uart/v151/'
UPORT = 'src/drivers/chips/ws53/porting/uart/uart_porting.c'
DRIVER = 'src/drivers/drivers/driver/uart/uart.c'


def function(source, name):
    match = re.search(r'^(?:STATIC |static )?[\w* ]+\b' + name + r'\s*\([^;]*?\)\s*\{', source, re.M)
    require(match is not None, 'missing original function: ' + name)
    depth = 1
    end = match.end()
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end].replace('STATIC ', 'static ', 1)


def typedef(source, name):
    matches = re.findall(r'typedef\s+(?:struct|enum)\b[^{}]*\{[^{}]*\}\s*' + name + r'\s*;', source, re.S)
    require(len(matches) == 1, 'missing/ambiguous original type: ' + name)
    return matches[0]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', type=Path, required=True)
    p.add_argument('--cc', default='cc')
    a = p.parse_args(); sdk = a.sdk.resolve()
    paths = [UPORT, DRIVER, UART+'hal_uart_v151_regs_def.h', UART+'hal_uart_v151_regs_op.c',
             UART+'hal_uart_v151.c', PORT+'include/dma_porting.h',
             ROM+'drivers/drivers/hal/dma/romable/hal_dma.h',
             'src/include/driver/uart.h', 'src/include/driver/dma.h', CONFIG]
    sources = {}
    for path in paths:
        data = (sdk/path).read_bytes()
        require(data == subprocess.check_output(['git','-C',str(sdk),'show',f'{PIN}:{path}']), path+' differs from pin')
        sources[path] = data.decode()
    root = Path(__file__).resolve().parents[1]
    svd = root/'generated/WS53.experimental.svd'; tree = ET.parse(svd)
    uart = tree.find("./peripherals/peripheral[name='UART0']")
    parameter = uart.find("registers/register[name='UART_PARAMETER']")
    dma = parameter.find("fields/field[name='DMA_MODE']")
    require(parameter.findtext('addressOffset') == '0x60' and dma.findtext('access') == 'read-only', 'DMA capability must remain RO')
    require(int(dma.findtext('bitOffset')) == 11, 'DMA_EXTRA location')
    for name, access in [('RX','read-only'),('TX','write-only')]:
        reg = uart.find(f"registers/register[name='{name}']")
        require(int(reg.findtext('addressOffset'),0) == 4 and reg.findtext('access') == access, 'DATA views')
    header = sources[UART+'hal_uart_v151_regs_def.h']
    require(re.search(r'dma_mode\s*:\s*1;\s*/\*\s*!<\s*RO,\s*DMA_EXTRA', header), 'original DMA_EXTRA access')
    require('# CONFIG_UART_SUPPORT_DMA is not set' in sources[CONFIG], 'reference product enables UART DMA')
    driver = sources[DRIVER]
    defines = '\n'.join(re.findall(r'^#define UART_DMA_\w+\s+\d+\s*$', driver, re.M))
    code = ['#include <assert.h>','#include <stddef.h>','#include <stdint.h>','#include <stdio.h>','#include <string.h>',
            '#define ERRCODE_SUCC 0','#define UART_CTRL_GET_DMA_DATA_ADDR 0','typedef unsigned errcode_t;',
            '#include '+json.dumps(str(sdk/(UART+'hal_uart_v151_regs_def.h'))),
            typedef(sources[PORT+'include/dma_porting.h'],'hal_dma_handshaking_source_t'),
            typedef(sources[ROM+'drivers/drivers/hal/dma/romable/hal_dma.h'],'hal_dma_protection_control_t'),
            typedef(sources['src/include/driver/uart.h'],'uart_write_dma_config_t'),
            typedef(sources['src/include/driver/dma.h'],'dma_ch_user_peripheral_config_t'), defines,
            'static uart_reg_t banks[3]; uart_reg_t *g_hal_uart_reg[3]={banks,banks+1,banks+2};',
            'static unsigned mock_failure;',
            'static const uint32_t mock_data_addresses[]={0x52081004,0x52080004,0x57014004};',
            'static errcode_t hal_uart_ctrl(uart_bus_t b,unsigned id,uintptr_t p) { (void)id; *(uint32_t*)p=mock_data_addresses[b]; return mock_failure; }',
            function(sources[UPORT],'uart_port_get_dma_trans_dest_handshaking'),
            function(sources[UPORT],'uart_port_get_dma_trans_src_handshaking'),
            function(sources[UART+'hal_uart_v151_regs_op.c'],'hal_uart_set_dmaen'),
            function(driver,'uart_write_by_dma_config'), function(driver,'uart_read_by_dma_config'),
            '_Static_assert(offsetof(uart_reg_t,data)==4,"DATA offset");',
            '_Static_assert(offsetof(uart_reg_t,uart_parameter)==0x60,"PARAMETER offset");',
            'int main(void) { unsigned cases=0; const uint32_t counts[]={1,17,4095,4096,65535,65536,65537};',
            '''for (unsigned bus=0;bus<3;bus++) {
                unsigned tx=uart_port_get_dma_trans_dest_handshaking(bus), rx=uart_port_get_dma_trans_src_handshaking(bus);
                assert(tx==(bus==0?10:bus==1?8:HAL_DMA_HANDSHAKING_MAX_NUM));
                assert(rx==(bus==0?11:bus==1?9:HAL_DMA_HANDSHAKING_MAX_NUM));
                for(unsigned width=0;width<3;width++) for(unsigned i=0;i<7;i++) for(unsigned dir=0;dir<2;dir++) {
                    uart_write_dma_config_t cfg={.src_width=width,.dest_width=width,.burst_length=0,.priority=0};
                    dma_ch_user_peripheral_config_t out={0};
                    uint32_t length=counts[i]<<width;
                    int ret=dir?uart_read_by_dma_config(bus,(void*)(uintptr_t)0x20020000,length,&cfg,&out):
                                uart_write_by_dma_config(bus,(void*)(uintptr_t)0x20020000,length,&cfg,&out);
                    assert(ret==0 && out.transfer_num==(uint16_t)counts[i]);
                    assert(out.src==(dir?mock_data_addresses[bus]:0x20020000));
                    assert(out.dest==(dir?0x20020000:mock_data_addresses[bus]));
                    assert(out.src_handshaking==(dir?rx:0) && out.dest_handshaking==(dir?0:tx));
                    assert(out.trans_type==(dir?2:1) && out.trans_dir==dir);
                    assert(out.src_width==width && out.dest_width==width && out.burst_length==0);
                    assert(out.src_increment==(dir?2:0) && out.dest_increment==(dir?0:2));
                    assert(out.protection==1); cases++;
                }
                for(unsigned seed=0;seed<3;seed++) for(unsigned val=0;val<2;val++) {
                    uint32_t old=seed==0?0:seed==1?0xa55a1234:0xffffffff;
                    memset(banks,0x5a,sizeof(banks)); banks[bus].uart_parameter=old;
                    uart_reg_t expected[3]; memcpy(expected,banks,sizeof(banks));
                    expected[bus].uart_parameter=(old&~0x800u)|(val<<11);
                    hal_uart_set_dmaen(bus,val); assert(memcmp(expected,banks,sizeof(banks))==0);
                }
            }
            mock_failure=1; uart_write_dma_config_t cfg={0}; dma_ch_user_peripheral_config_t out={0};
            assert(uart_read_by_dma_config(0,(void*)(uintptr_t)0x20020000,17,&cfg,&out)==-1);
            assert(uart_write_by_dma_config(0,(void*)(uintptr_t)0x20020000,17,&cfg,&out)==-1);
            assert(HAL_DMA_PROTECTION_CONTROL_BUFFERABLE==2);
            assert(UART_DMA_PROTECTION_CONTROL_BUFFERABLE==1);
            printf("%u\\n",cases); return 0; }''']
    with tempfile.TemporaryDirectory(prefix='ws53-uart-dma-contract-') as temp:
        work = Path(temp)
        (work/'uart_porting.h').write_text('#include <stdint.h>\n#define UART_BUS_MAX_NUMBER 3\ntypedef uint32_t uart_bus_t;\nenum {UART_BUS_0,UART_BUS_1,UART_BUS_2};\n')
        (work/'check.c').write_text('\n'.join(code))
        subprocess.run([a.cc,'-std=c11','-O2','-Wall','-Werror','-I'+str(work),str(work/'check.c'),'-o',str(work/'check')],check=True)
        cases = int(subprocess.check_output([str(work/'check')]))
    print(json.dumps({'sdk_commit':PIN,'original_config_executions':cases,'uart_data_offset':4,
        'routes':[{'uart':0,'tx':10,'rx':11},{'uart':1,'tx':8,'rx':9},{'uart':2,'tx':None,'rx':None}],
        'source_conflicts':['DMA_EXTRA header and SVD are RO; original setter writes bit11 of UART_PARAMETER',
                            'UART BUFFERABLE constant=1, DMA HAL BUFFERABLE enum=2; do not silently equate'],
        'truncation':'original config retains4096 (exceeds12-bit MDMA count);65536 becomes0 before MDMA',
        'product_uart_dma_enabled':False,'hardware_verified':False,
        'unknown':['DMA_EXTRA physical capability and write behavior','request level/threshold/ack protocol',
                   'peripheral bus access width/HPROT behavior','actual cache, clock and ownership'],
        'input_sha256':{n:hashlib.sha256(v.encode()).hexdigest() for n,v in sources.items()},
        'svd_sha256':hashlib.sha256(svd.read_bytes()).hexdigest()},indent=2))


if __name__ == '__main__': main()
