#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute original WS53 TX enable/PTIM/ack helpers; hardware evidence separate."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from check_ws53_uart_dma_contract import UART, function, typedef
from check_ws53_mdma_contract import PIN, require

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--sdk',type=Path,required=True)
p.add_argument('--svd',type=Path,required=True)
a=p.parse_args()
paths=[UART+'hal_uart_v151.c',UART+'hal_uart_v151_regs_def.h',UART+'hal_uart_v151_regs_op.h']
sources={}
for path in paths:
 data=(a.sdk/path).read_bytes()
 require(data==subprocess.check_output(['git','-C',str(a.sdk),'show',f'{PIN}:{path}']),path+' differs from pinned SDK')
 sources[path]=data.decode()
source,header,ops=(sources[n] for n in paths)
require('then if FIFO' in header and 'this bit will be asserted when the TX FIFO is full' in header,'PTIM line status semantics changed')
tree=ET.parse(a.svd)
uart=tree.find("./peripherals/peripheral[name='UART0']")
require(uart.findtext("registers/register[name='INTR_ID']/fields/field[name='INTR_ID']/readAction")=='modifyExternal','IIR acknowledgement missing')
code='''#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <stdio.h>
#define CONFIG_UART_SUPPORT_TX_INT 1
#define unused(x) ((void)(x))
#include "regs.h"
static uart_reg_t banks[3];
uart_reg_t *g_hal_uart_reg[]={&banks[0],&banks[1],&banks[2]};
static unsigned cleared[3];
static void hal_uart_clear_pending(uart_bus_t uart) { cleared[uart]++; }
'''+typedef(ops,'hal_uart_interrupt_t')+'\n'
code+='\n'.join(function(source,n) for n in ('hal_uart_set_ptim_en','hal_uart_enable_interrupt','hal_uart_clear_interrupt'))
code+='''
int main(void) {
 unsigned cases=0;
 for(unsigned port=0;port<3;port++) for(unsigned old=0;old<32;old++) for(unsigned set=0;set<2;set++) {
   banks[port].intr_en=0xa5000000u|old;
   hal_uart_enable_interrupt(port,HAL_UART_INTERRUPT_TX,set);
   assert(banks[port].intr_en==(((0xa5000000u|old)&~8u)|(set?8u:0)));
   banks[port].intr_en=0xa5000000u|old;
   hal_uart_set_ptim_en(port,set);
   assert(banks[port].intr_en==(((0xa5000000u|old)&~16u)|(set?16u:0)));
   cases+=2;
 }
 for(unsigned port=0;port<3;port++) {
   banks[port].intr_id=2;
   hal_uart_clear_interrupt(port,HAL_UART_INTERRUPT_TX);
   assert(cleared[port]==1);
   // Host RAM cannot simulate IIR read-clear. Never mistake this for HIL.
   assert(banks[port].intr_id==2);
 }
 printf("%u\\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='ws53-tx-source-') as tmp:
 work=Path(tmp)
 (work/'regs.h').write_text(header)
 (work/'uart_porting.h').write_text('#include <stdint.h>\n#define UART_BUS_MAX_NUMBER 3\ntypedef uint32_t uart_bus_t;\n')
 subprocess.run(['cc','-std=c11','-Wall','-Werror','-I',str(work),'-x','c','-','-o',str(work/'test')],input=code,text=True,check=True)
 cases=int(subprocess.check_output([str(work/'test')]))
 require(cases==384,'missing original helper cases')
print(json.dumps(dict(sdk_commit=PIN,source_sha256={n:hashlib.sha256(sources[n].encode()).hexdigest() for n in paths},
 helper_cases=cases,acknowledgement_helper_ports=3,svd_read_action='modifyExternal',hardware_verified=False,
 hardware_evidence='evidence/ws53-uart-tx-irq-hil.json',
 not_proven=['Host RAM does not model IIR read side effects','UART0/1 TX IRQ and other thresholds need physical tests','Clock loss, exact interrupt latency and arbitrary concurrent traffic unqualified']),indent=2))
