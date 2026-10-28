#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyelftools==0.32"]
# ///
"""Pinned WS53 IPC headers, ROM and patch contracts; never access hardware.

ROM/profile inputs come from bounded read-only captures, not vendor execution.
The host C probe compiles original public headers; board, clock, ring concurrency
and peer ownership remain separate qualification requirements.
"""
import argparse
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from elftools.elf.elffile import ELFFile

PIN='163d9150ab94a02d09a05f623351f9facee3025e'
ROM_SHA='2c88c7b2090d64db73307fbd45005b27882308443b092fbedc85be63586a662a'
ARCHIVE='src/drivers/chips/ws53/porting/patch/ws53_liteos_app/libplat_patch.a'
ROM_BASE=0x1f220
PORT='src/drivers/chips/ws53/rom/app/drivers/chips/ws53/porting/include/acore'
DRIVER='src/drivers/chips/ws53/rom/app/drivers/drivers/driver/ipc/ipc'
MEMORY='src/drivers/boards/ws53/memory_config/include'
REGS={'IPC_RECEIVE_ENABLE':(0x20,32,'write-only'), 'IPC_RECEIVE_STATUS':(0x28,32,'read-only'),
      'IPC_RECEIVE_ACK_COMMAND':(0x2c,32,'write-only'), 'IPC_RECEIVE_ACK_SYNC':(0x2c,32,'read-only'),
      'IPC_SEND_STATUS':(0x30,32,'read-only'), 'IPC_SEND_SET_COMMAND':(0x34,16,'write-only'),
      'IPC_RELEASE_COMMAND':(0x38,32,'write-only')}


def need(ok,message):
    if not ok:raise ValueError(message)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def svd_contract(tree):
    p=tree.find("./peripherals/peripheral[name='M_CRG']")
    need(p is not None and int(p.findtext('baseAddress'),0)==0x52000000,'M_CRG base')
    found={n.findtext('name'):n for n in p.findall('registers/register') if n.findtext('name').startswith('IPC_')}
    need(set(found)==set(REGS),'IPC register set')
    for name,(offset,width,access) in REGS.items():
        n=found[name]
        need(int(n.findtext('addressOffset'),0)==offset and int(n.findtext('size'))==width
             and n.findtext('access')==access,'IPC address/width/access '+name)
        need(not any(x.tag in ('resetValue','resetMask','readAction','modifiedWriteValues') for x in n.iter()),'unqualified side effect/reset')
        if name not in ('IPC_RECEIVE_ACK_SYNC','IPC_RELEASE_COMMAND'):
            need([(f.findtext('name'),f.findtext('bitOffset'),f.findtext('bitWidth'),f.findtext('access'))
                  for f in n.findall('fields/field')]==[('CHANNEL0','0','1',access),('CHANNEL1','1','1',access)],'channel fields')
    need(found['IPC_RECEIVE_ACK_SYNC'].findtext('alternateRegister')=='IPC_RECEIVE_ACK_COMMAND','ACK alias')
    need([(x.findtext('name'),x.findtext('value')) for x in p.findall('interrupt')]==[('APP_INT0','26'),('APP_INT1','27')],'IPC IRQs')
    limit=found['IPC_RELEASE_COMMAND'].find('fields/field/writeConstraint/range')
    need(limit is not None and limit.findtext('minimum')==limit.findtext('maximum')=='1','restricted reset release')


def run(a):
    files=[PORT+'/ipc_porting.h',DRIVER+'/ipc.h',DRIVER+'/ipc_ring.h',
           'src/middleware/utils/common_headers/platform_types.h',MEMORY+'/memory_config_common.h',
           'src/middleware/utils/common_headers/chip_definitions.h','src/include/errcode.h',
           'src/application/ws53/ws53_application/main.c','src/drivers/chips/ws53/include/acore/platform_core.h',
           'src/build/config/target_config/ws53/patch_config/acore.cfg',ARCHIVE]
    sources={}
    for n in files:
        raw=subprocess.check_output(['git','-C',str(a.sdk),'show',PIN+':'+n])
        need((a.sdk/n).read_bytes()==raw,'SDK source changed '+n);sources[n]=sha(raw)
    need(sources[ARCHIVE]=='c519a5330f1327071858324623f02ab8b751679c3238ea63053a3b260d9d91ca','SDK archive identity')
    with tempfile.TemporaryDirectory(prefix='ws53-ipc-contract-') as temp:
        d=Path(temp)
        for n in ('platform_core.h','chip_definitions.h','product.h','chip_core_definition.h'):(d/n).write_text('/* configuration-only host stub */\n')
        (d/'probe.c').write_text('''#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include "ipc_porting.h"
#include "memory_config_common.h"
_Static_assert(sizeof(ipc_memory_manage_t)==12,"IPC config ABI");
_Static_assert(offsetof(ipc_memory_manage_t,start_addr)==4,"IPC address ABI");
_Static_assert(offsetof(ipc_memory_manage_t,total_len)==8,"IPC length ABI");
int main(void) {
unsigned values[]={CORE0,CORE1,CORE_IPC_TX_PIPE_NUM,CORE_IPC_RX_PIPE_NUM,IPC_MESSAGE_MAX_LENGTH,
 IPC_MSG_UPDATE_SHARE_RANDOM_NUMBER,IPC_MSG_ID_MAX,IPC_PRIORITY_HIGHEST,IPC_PRIORITY_LOWEST,
 IPC_BUFF_SHARE_CH0_RX_LEN,IPC_BUFF_SHARE_CH1_RX_LEN,IPC_BUFF_SHARE_CH0_TX_LEN,IPC_BUFF_SHARE_CH1_TX_LEN,IPC_BUFF_SHARE_LEN,
 ACCORE_MEM_ADDR_OFFSET,IPC_CORE0_1_SHARE_MEM_CH0_START_ADDR,IPC_CORE0_1_SHARE_MEM_CH0_LENGTH};
for(unsigned i=0;i<sizeof(values)/sizeof(values[0]);i++) printf("%u\\n",values[i]);
}
''')
        argv=[a.cc,'-std=c11','-Werror','-DCHIP_WS53=1','-DCORE=APPS','-I'+str(d),*['-I'+str(a.sdk/n) for n in
              (PORT,DRIVER,MEMORY,'src/include','src/middleware/utils/common_headers')],str(d/'probe.c'),'-o',str(d/'probe')]
        compiled=subprocess.run(argv,capture_output=True,text=True)
        need(compiled.returncode==0,'original header probe: '+compiled.stderr)
        constants=list(map(int,subprocess.check_output([str(d/'probe')],text=True).split()))
        need(constants==[2,0,2,2,60,18,19,0,1,0x500,0x100,0x500,0x100,0xc00,0x10000000,0x10080000,0x800],'original header constants')
    rom=a.rom.read_bytes();need(len(rom)==0x800 and sha(rom)==ROM_SHA,'ROM identity/length')
    # Exact independent instruction encodings at SDK symbol PCs. These decode
    # the low-level register access widths and IRQ registration constants.
    pcs={0x1f2aa:(2,0x4569),0x1f2b4:(2,0x456d),0x1f834:(2,0xc31c),0x1f86c:(2,0x479c),
         0x1f884:(2,0xc7c8),0x1f894:(2,0x4b9c),0x1f8b8:(2,0xcbc8),0x1f75c:(4,0x00f10623),
         0x1f762:(4,0x00f106a3),0x1f768:(4,0x00f11723),0x1f40c:(2,0xc794),
         0x1f40e:(4,0x00079223),0x1f414:(4,0x00079323)}
    for pc,(size,word) in pcs.items():need(int.from_bytes(rom[pc-ROM_BASE:pc-ROM_BASE+size],'little')==word,f'ROM instruction {pc:#x}')
    obj=subprocess.check_output([str(a.tool_prefix)+'ar','p',str(a.sdk/ARCHIVE),'ipc_patch.c.obj'])
    elf=ELFFile(BytesIO(obj));sym=elf.get_section_by_name('.symtab')
    code=elf.get_section_by_name('.patch.text.on.sram').data()
    need(code[0x18:0x20]==bytes.fromhex('0f00f00fb7070052') and code[0x20:0x22]==bytes.fromhex('dc57'),'patched fence/readback')
    need(code[0x94:0x9a]==bytes.fromhex('b7070052cabb'),'patched TX halfword write')
    symbols={n:{'offset':sym.get_symbol_by_name(n)[0]['st_value'],'bytes':sym.get_symbol_by_name(n)[0]['st_size']}
             for n in ('irq_app_int_handler_patch','hal_ipc_set_self2x_int_patch')}
    config=(a.sdk/files[-2]).read_text()
    need('hal_ipc_set_self2x_int hal_ipc_set_self2x_int_patch' in config,'patch binding')
    profile=a.profile.read_bytes();header=a.header.read_bytes()
    need(len(profile)==0x58 and len(header)==28,'bounded memory profile')
    words=struct.unpack('<22I',profile);head=struct.unpack('<7I',header)
    need(words[:13]==(0x52000020,0x200,0x1001e300,0x500,0x10200,0x1001e800,0x100,2,0x1001e900,0x500,0x10002,0x1001ee00,0x100),'observed configured IPC profile')
    need(head[6]+0x10000000==words[2],'control header dynamic origin')
    tree=ET.parse(a.svd).getroot();svd_contract(tree);refused=[]
    for name in REGS:
        for attr,value in (('size','8'),('access','read-write' if REGS[name][2]!='read-write' else 'read-only'),('addressOffset','0x24')):
            changed=copy.deepcopy(tree);node=changed.find("./peripherals/peripheral[name='M_CRG']/registers/register[name='"+name+"']")
            node.find(attr).text=value
            try:svd_contract(changed)
            except ValueError:refused.append(name+':'+attr)
            else:raise ValueError('accepted altered IPC view')
    result=dict(passed=True,sdk_commit=PIN,sdk_sources=sources,header_constants=constants,
        rom_sha256=ROM_SHA,rom_instructions_checked=len(pcs),patch_object_sha256=sha(obj),patch_symbols=symbols,
        observed_profile_sha256=sha(profile),observed_header_sha256=sha(header),irq=[26,27],
        ring_header_bytes=12,message_header_bytes=4,message_max_bytes=60,register_views=7,mutations_refused=refused,
        svd_sha256=sha(a.svd.read_bytes()),hardware_accessed=False,vendor_code_executed=False,
        host_header_probe_executed=True,doorbell_hardware_writes_verified=False,ring_concurrency_verified=False,
        cache_coherence_verified=False,resource_handoff_verified=False)
    a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('sdk','rom','profile','header','tool-prefix','svd','output'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--cc',default='cc');run(p.parse_args())
