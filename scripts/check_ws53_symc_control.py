#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned SYMC source with recording MMIO/cache/clock substitutes.

This audits source/descriptor ABI, not encryption results or hardware quiescence.
The original functions and configuration tables are extracted without edits.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from check_ws53_crypto_profile import PIN, PORT, REG
from check_ws53_crypto_handoff import function

BASE = 'src/drivers/drivers/hal/security_unified/hal_cipher/'
SYMC = BASE + 'spacc/hal_symc.c'
COMMON_C = 'src/drivers/drivers/driver/security_unified/drv_cipher/drv_code/crypto_drv_common.c'
COMMON_H = 'src/drivers/drivers/driver/security_unified/drv_cipher/drv_code/crypto_drv_common.h'
HEADERS = (BASE+'include/common_include/crypto_common_struct.h',
           BASE+'include/common_include/crypto_common_def.h',
           BASE+'include/common_include/crypto_symc_struct.h',
           BASE+'include/hal_include/hal_common.h',
           BASE+'include/hal_include/hal_symc.h')
FUNCTIONS = ('inner_symc_irq_enable', 'hal_cipher_symc_clear_channel',
    'hal_cipher_symc_dfa_config', 'hal_cipher_symc_set_entry_node',
    'hal_cipher_symc_lock_chn', 'hal_cipher_symc_unlock_chn',
    'hal_cipher_symc_attach', 'hal_cipher_symc_set_key', 'hal_cipher_symc_set_iv',
    'hal_cipher_symc_get_iv', 'inner_symc_config_key_ctrl', 'priv_set_in_node',
    'add_normal_in_node', 'hal_cipher_symc_start', 'hal_cipher_symc_done_try',
    'hal_cipher_symc_out_node_done_try', 'hal_wait_in_node_done',
    'hal_wait_out_node_done', 'hal_cipher_symc_add_out_node')

COMMON = r'''#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
typedef uint8_t td_u8;typedef uint16_t td_u16;typedef uint32_t td_u32;
typedef int32_t td_s32;typedef uint64_t td_u64;typedef int td_bool;
typedef void td_void;typedef unsigned long td_ulong;typedef unsigned td_handle;
typedef uintptr_t td_phys_addr_t;typedef void crypto_wait_t;
#define TD_SUCCESS 0
#define TD_FAILURE -1
#define TD_NULL NULL
#define TD_TRUE 1
#define TD_FALSE 0
#define CRYPTO_SUCCESS 0
#define CRYPTO_FAILURE -1
#define EOK 0
#define ERROR_INVALID_PHYS_ADDR 9
#define HAL_COMPAT_ERRNO(m,e) (-(e))
#define crypto_unused(x) ((void)(x))
#define crypto_hal_func_enter() ((void)0)
#define crypto_hal_func_exit() ((void)0)
#define crypto_log_err(...) ((void)0)
#define crypto_log_dbg(...) ((void)0)
#define crypto_chk_return(c,r,...) do {if(c)return(r);}while(0)
#define crypto_array_size(a) (sizeof(a)/sizeof((a)[0]))
static int memcpy_s(void*d,size_t cap,const void*s,size_t n) {
 if(n>cap){return -7;}memcpy(d,s,n);return 0;
}
static int memset_s(void*d,size_t cap,int v,size_t n) {assert(n<=cap);memset(d,v,n);return 0;}
#include "crypto_common_def.h"
'''

HARNESS = r'''#include "common.h"
#include "security_unified_porting.h"
#include "hal_spacc_reg.h"
#include "hal_symc.h"
static unsigned regs[0x10000/4],reads,writes,delay_us,delay_ms,clear_reads;
static unsigned clear_after,clear_mask,secure,race,ev[96][3],ne;
static void event(unsigned kind,unsigned addr,unsigned value) {
 assert(ne<96);ev[ne][0]=kind;ev[ne][1]=addr;ev[ne++][2]=value;
}
static unsigned spacc_reg_read(unsigned addr) {
 assert(addr%4==0&&addr<sizeof(regs));reads++;
 if(addr==SPACC_INT_RAW_SYM_CLEAR_FINISH) {
  clear_reads++;return clear_after&&clear_reads>=clear_after?clear_mask:0;
 }
 return regs[addr/4];
}
static void spacc_reg_write(unsigned addr,unsigned value) {
 assert(addr%4==0&&addr<sizeof(regs));writes++;event(1,addr,value);
 if(addr!=SPACC_SYM_CHN_LOCK||!race)regs[addr/4]=value;
}
static void uapi_tcxo_delay_us(unsigned n) {assert(n==1);delay_us++;}
static void uapi_tcxo_delay_ms(unsigned n) {assert(n==1);delay_ms++;}
static crypto_cpu_type crypto_get_cpu_type(void) {return secure?CRYPTO_CPU_TYPE_SCPU:CRYPTO_CPU_TYPE_ACPU;}
static int crypto_wait_func_is_support(void) {return 1;}
static uintptr_t crypto_get_phys_addr(void*p);
static void crypto_cache_flush(uintptr_t p,unsigned n) {event(3,crypto_get_phys_addr((void*)p),n);}
static void crypto_cache_all(void) {event(4,0,0);}
#define crypto_memory_barrier() event(2,0,0)
static td_s32 hal_cipher_symc_out_node_done_try(td_u32 ch);
#include "original.inc"
static _Alignas(32) crypto_symc_entry_in ins[2][2];
static _Alignas(32) crypto_symc_entry_out outs[2][2];
static uintptr_t crypto_get_phys_addr(void*p) {
 uintptr_t x=(uintptr_t)p,i=(uintptr_t)ins,o=(uintptr_t)outs;
 if(x>=i&&x<i+sizeof(ins))return 0x20050000u+x-i;
 assert(x>=o&&x<o+sizeof(outs));return 0x20050100u+x-o;
}
static void setup(void) {
 memset(regs,0,sizeof(regs));memset(ins,0,sizeof(ins));memset(outs,0,sizeof(outs));
 memset(g_symc_hard_context,0,sizeof(g_symc_hard_context));
 for(unsigned i=0;i<2;i++){g_symc_hard_context[i].entry_in=ins[i];g_symc_hard_context[i].entry_out=outs[i];}
 reads=writes=delay_us=delay_ms=clear_reads=clear_after=clear_mask=secure=race=ne=0;
}
static unsigned find_write(unsigned addr,unsigned nth) {
 for(unsigned i=0;i<ne;i++)if(ev[i][0]==1&&ev[i][1]==addr){if(nth--==0)return ev[i][2];}
 assert(0);return 0;
}
static void trace(const char*name,unsigned ch,int result,unsigned count) {
 printf("%s{\"name\":\"%s\",\"channel\":%u,\"return\":%d,\"reads\":%u,\"clear_reads\":%u,\"delay_us_calls\":%u,\"delay_ms_calls\":%u,\"events\":[",
 count?",":"",name,ch,result,reads,clear_reads,delay_us,delay_ms);
 for(unsigned i=0;i<ne;i++){printf("%s[%u,%u,%u]",i?",":"",ev[i][0],ev[i][1],ev[i][2]);}printf("]}");
}
int main(void) {
 const unsigned endian=1;assert(*(const unsigned char*)&endian==1);
 assert(sizeof(crypto_symc_entry_in)==32&&sizeof(crypto_symc_entry_out)==16);
 assert(IN_NODE_TYPE_FIRST==1&&IN_NODE_TYPE_LAST==4&&CRYPTO_SYMC_MAX_LIST_NUM==2);
 unsigned count=0,configs=0,rejected=0,descriptors=0,submissions=0,ivcases=0,acks=0;
 printf("{\"control_cases\":[");
 for(unsigned ch=1;ch<=2;ch++)for(unsigned cpu=0;cpu<2;cpu++)for(unsigned mode=0;mode<3;mode++) {
  setup();secure=cpu;clear_after=mode==0?1:mode==1?4:0;clear_mask=1u<<ch;
  regs[SPACC_SYM_CHN_LOCK/4]=0xa0000000u;regs[SPACC_SYM_CHN_CLEAR_REQ/4]=0x12340008;
  regs[IN_SYM_CHN_CTRL(ch)/4]=0x12345678;regs[OUT_SYM_CHAN_RAW_LAST_NODE_INT_EN/4]=0xaffe;
  regs[IN_SYM_CHN_NODE_LENGTH(ch)/4]=regs[OUT_SYM_CHN_NODE_LENGTH(ch)/4]=0xaabbccff;
  memset(ins,0xa5,sizeof(ins));memset(outs,0xa5,sizeof(outs));
  int ret=hal_cipher_symc_lock_chn(ch);
  if(mode==2) {
   assert(ret==-1&&clear_reads==12000&&delay_us==2002&&delay_ms==9998);
   assert(regs[SPACC_SYM_CHN_LOCK/4]==0xa0000000u&&regs[IN_SYM_CHN_CTRL(ch)/4]==0x12345678);
   assert(writes==4); /* claim, clear, clear in unlock, release despite timeout */
  }else{
   assert(ret==0&&clear_reads==clear_after&&delay_us==clear_after-1&&delay_ms==0);
   assert(regs[SPACC_SYM_CHN_LOCK/4]==(0xa0000000u|((cpu?2u:1u)<<(ch*4))));
   assert(regs[IN_SYM_CHN_CTRL(ch)/4]==((0x12345678u&~0x8000ff00u)|(cpu?0x80005500u:0x8000aa00u)));
   assert(find_write(SPACC_INT_RAW_SYM_CLEAR_FINISH,0)==(0x12340008u|(1u<<ch)));
   assert(regs[IN_SYM_CHN_NODE_LENGTH(ch)/4]==0xaabbcc02&&regs[OUT_SYM_CHN_NODE_LENGTH(ch)/4]==0xaabbcc02);
   assert(regs[IN_SYM_CHN_NODE_START_ADDR_L(ch)/4]==0x20050000u+(ch-1)*64);
   assert(regs[OUT_SYM_CHN_NODE_START_ADDR_L(ch)/4]==0x20050100u+(ch-1)*32);
   assert(regs[IN_SYM_CHN_NODE_START_ADDR_H(ch)/4]==0&&regs[OUT_SYM_CHN_NODE_START_ADDR_H(ch)/4]==0);
   for(unsigned i=0;i<sizeof(ins);i++)assert(((unsigned char*)ins)[i]==(i/64==ch-1?0:0xa5));
   for(unsigned i=0;i<sizeof(outs);i++)assert(((unsigned char*)outs)[i]==(i/32==ch-1?0:0xa5));
  }
  trace(mode==2?"lock-timeout-releases-owner":mode==1?"lock-delayed":"lock-ready",ch,ret,count++);
 }
 for(unsigned ch=1;ch<=2;ch++) {
  for(unsigned owner=1;owner<=15;owner++){
   setup();regs[SPACC_SYM_CHN_LOCK/4]=owner<<(ch*4);int ret=hal_cipher_symc_lock_chn(ch);
   assert(ret==-1&&reads==1&&writes==0);trace("lock-busy",ch,ret,count++);
  }
  setup();race=1;int ret=hal_cipher_symc_lock_chn(ch);assert(ret==-1&&reads==2&&writes==1);
  trace("lock-readback-rejected",ch,ret,count++);
  setup();regs[SPACC_SYM_CHN_LOCK/4]=1u<<(ch*4);ret=hal_cipher_symc_unlock_chn(ch);
  assert(ret==-1&&clear_reads==6000&&regs[SPACC_SYM_CHN_LOCK/4]==0);
  trace("unlock-timeout-releases-owner",ch,ret,count++);
  setup();regs[SPACC_SYM_CHN_LOCK/4]=2u<<(ch*4);ret=hal_cipher_symc_unlock_chn(ch);
  assert(ret==0&&writes==0);trace("unlock-other-owner",ch,ret,count++);
 }
 printf("],\"control_case_count\":%u,",count);
 const unsigned modes[]={1,3,6,7,8,6,11,13,12};
 for(unsigned ch=1;ch<=2;ch++)for(unsigned seed=0;seed<2;seed++)
 for(unsigned alg=1;alg<=2;alg++)for(unsigned mode=0;mode<9;mode++)
 for(unsigned key=0;key<4;key++)for(unsigned width=1;width<=3;width++)for(unsigned dec=0;dec<2;dec++) {
  setup();unsigned old=seed?0xa5a5ffff:0;regs[IN_SYM_CHN_KEY_CTRL(ch)/4]=old;
  hal_symc_config_t cfg={.symc_alg=alg,.work_mode=mode,.symc_key_length=key,.symc_bit_width=width,.is_decrypt=dec};
  assert(inner_symc_config_key_ctrl(ch,&cfg)==0&&reads==1&&writes==1);
  unsigned expected=(old&~0x1fff0000u)|((alg==1?2u:5u)<<16)|(modes[mode]<<20)|(key<<24)|((width==3?0u:1u)<<26)|(dec<<28);
  assert(regs[IN_SYM_CHN_KEY_CTRL(ch)/4]==expected);configs++;
 }
 for(unsigned ch=1;ch<=2;ch++)for(unsigned kind=0;kind<4;kind++)for(unsigned value=0;value<2;value++) {
  setup();hal_symc_config_t cfg={.symc_alg=CRYPTO_SYMC_ALG_AES,.work_mode=CRYPTO_SYMC_WORK_MODE_ECB,
   .symc_key_length=CRYPTO_SYMC_KEY_128BIT,.symc_bit_width=CRYPTO_SYMC_BIT_WIDTH_128BIT};
  if(kind==0)cfg.symc_alg=value?CRYPTO_SYMC_ALG_DMA:CRYPTO_SYMC_ALG_LEA;
  if(kind==1)cfg.work_mode=value?CRYPTO_SYMC_WORK_MODE_INVALID:CRYPTO_SYMC_WORK_MODE_MAX;
  if(kind==2)cfg.symc_key_length=value?CRYPTO_SYMC_KEY_LENGTH_INVALID:CRYPTO_SYMC_KEY_LENGTH_MAX;
  if(kind==3)cfg.symc_bit_width=value?CRYPTO_SYMC_BIT_WIDTH_MAX:CRYPTO_SYMC_BIT_WIDTH_1BIT;
  assert(inner_symc_config_key_ctrl(ch,&cfg)==-1&&writes==0);rejected++;
 }
 unsigned slots[]={0,1,15,31,255,511,512,0xffffffff};
 for(unsigned ch=1;ch<=2;ch++)for(unsigned i=0;i<crypto_array_size(slots);i++){
  setup();regs[IN_SYM_CHN_KEY_CTRL(ch)/4]=0xa5a5a5a5;
  assert(hal_cipher_symc_attach(ch,slots[i])==0&&regs[IN_SYM_CHN_KEY_CTRL(ch)/4]==((0xa5a5a5a5u&~511u)|(slots[i]&511)));
 }
 for(unsigned ch=1;ch<=2;ch++)for(unsigned len=0;len<=32;len+=8){
  setup();unsigned char key[32]={0};assert(hal_cipher_symc_set_key(ch,key,len)==-1&&reads==0&&writes==0);
 }
 unsigned lengths[]={0,1,15,16,17,128,0xffffffff},addresses[]={0,0x20010001,0xfffffff0};
 for(unsigned ch=1;ch<=2;ch++)for(unsigned idx=0;idx<2;idx++)for(unsigned flags=0;flags<8;flags++)
 for(unsigned l=0;l<crypto_array_size(lengths);l++)for(unsigned a=0;a<crypto_array_size(addresses);a++){
  setup();g_symc_hard_context[ch-1].idx_in=idx;ins[ch-1][idx].odd_even=1;
  assert(add_normal_in_node(ch,addresses[a],lengths[l],flags)==0&&reads==0&&writes==0);
  unsigned words[8];memcpy(words,&ins[ch-1][idx],32);
  assert(words[0]==(512u|(flags&1)|((flags&4)>>1))&&words[1]==lengths[l]&&words[2]==addresses[a]&&words[3]==0);
  assert(g_symc_hard_context[ch-1].idx_in==(idx+1)%2&&g_symc_hard_context[ch-1].cnt_in==1);descriptors++;
 }
 for(unsigned ch=1;ch<=2;ch++)for(unsigned idx=0;idx<2;idx++)for(unsigned l=0;l<crypto_array_size(lengths);l++){
  setup();crypto_symc_hard_context*ctx=&g_symc_hard_context[ch-1];ctx->idx_out=idx;ctx->idx_in=1;ins[ch-1][0].sym_last_node=1;
  assert(hal_cipher_symc_add_out_node(ch,0x20030001,lengths[l])==0&&reads==0&&writes==0);
  unsigned words[4];memcpy(words,&outs[ch-1][idx],16);
  assert(words[0]==0&&words[1]==lengths[l]&&words[2]==0x20030001&&words[3]==0);
  assert(ctx->idx_out==(idx+1)%2&&ctx->cnt_out==1);descriptors++;
 }
 for(unsigned ch=1;ch<=2;ch++)for(unsigned idx=0;idx<2;idx++)for(unsigned n=0;n<=17;n++){
  setup();unsigned char iv[20],got[20];for(unsigned i=0;i<20;i++)iv[i]=(i*31+7)&255;
  g_symc_hard_context[ch-1].idx_in=idx;int ret=hal_cipher_symc_set_iv(ch,iv,n);
  assert(ret==(n<=16?0:-7));if(n<=16){assert(memcmp(ins[ch-1][idx].iv,iv,n)==0);for(unsigned i=n;i<16;i++)assert(((unsigned char*)ins[ch-1][idx].iv)[i]==0);}
  for(unsigned i=0;i<4;i++)regs[(CHANN_CIPHER_IVOUT(ch)+i*4)/4]=0x11223300+i;
  memset(got,0xa5,sizeof(got));assert(hal_cipher_symc_get_iv(ch,got,n)==0);
  for(unsigned i=0;i<4;i++){unsigned word;memcpy(&word,got+i*4,4);assert(word==0x11223300+i);}
  for(unsigned i=16;i<20;i++){assert(got[i]==0xa5);}ivcases++;
 }
 for(unsigned ch=1;ch<=2;ch++)for(unsigned wp=0;wp<4;wp++)for(unsigned in=0;in<4;in++)for(unsigned out=0;out<4;out++){
  setup();crypto_symc_hard_context*ctx=&g_symc_hard_context[ch-1];ctx->cnt_in=in;ctx->cnt_out=out;
  regs[IN_SYM_CHN_NODE_WR_POINT(ch)/4]=0xaabbcc00|wp;regs[OUT_SYM_CHN_NODE_WR_POINT(ch)/4]=0x11223300|wp;
  regs[OUT_SYM_CHAN_RAW_LAST_NODE_INT_EN/4]=0xffffffff;
  assert(hal_cipher_symc_start(ch)==0&&ctx->cnt_in==0&&ctx->cnt_out==0);
  assert(regs[IN_SYM_CHN_NODE_WR_POINT(ch)/4]==(0xaabbcc00u|((wp+in)%2)));
  assert(regs[OUT_SYM_CHN_NODE_WR_POINT(ch)/4]==(0x11223300u|((wp+out)%2)));
  /* Actual source publishes output before descriptor cache flush and input. */
  assert(ne==8&&ev[0][0]==1&&ev[1][0]==2&&ev[2][1]==OUT_SYM_CHN_NODE_WR_POINT(ch));
  assert(ev[3][0]==2&&ev[4][0]==3&&ev[4][2]==64&&ev[5][0]==3&&ev[5][2]==32);
  assert(ev[6][0]==4&&ev[7][1]==IN_SYM_CHN_NODE_WR_POINT(ch));submissions++;
 }
 for(unsigned ch=1;ch<=2;ch++)for(unsigned mode=0;mode<2;mode++){
  setup();regs[IN_SYM_CHN_NODE_WR_POINT(ch)/4]=1;regs[IN_SYM_CHN_NODE_RD_POINT(ch)/4]=mode?0:1;
  assert(hal_wait_in_node_done(ch)==(mode?-1:0));assert(reads==(mode?12000:2)&&delay_us==0);
  reads=0;regs[OUT_SYM_CHN_NODE_WR_POINT(ch)/4]=1;regs[OUT_SYM_CHN_NODE_RD_POINT(ch)/4]=mode?0:1;
  assert(hal_wait_out_node_done(ch)==(mode?-1:0));assert(reads==(mode?12000:2)&&delay_us==(mode?6000:0));
 }
 for(unsigned ch=1;ch<=2;ch++)for(unsigned seed=0;seed<4;seed++)for(unsigned kind=0;kind<2;kind++){
  setup();unsigned raw[]={0,6,0xffffffff,0xa5a50000},addr=kind?SYM_CHANN_RAW_INT:OUT_SYM_CHAN_RAW_LAST_NODE_INT;
  regs[addr/4]=raw[seed];int ret=kind?hal_cipher_symc_out_node_done_try(ch):hal_cipher_symc_done_try(ch);
  assert(ret==((raw[seed]&(1u<<ch))?0:-1));assert(reads==1&&writes==1);
  assert(find_write(addr,0)==((raw[seed]&0xffff0000u)|(raw[seed]&(1u<<ch))));acks++;
 }
 printf("\"configuration_encodings\":%u,\"rejected_configurations\":%u,\"descriptor_cases\":%u,\"submission_cases\":%u,\"iv_cases\":%u,\"ack_cases\":%u,\"attach_cases\":16,\"direct_key_rejections\":10,\"wait_cases\":8}\n",
 configs,rejected,descriptors,submissions,ivcases,acks);return 0;
}
'''

TARGET = r'''
_Static_assert(sizeof(crypto_symc_entry_in)==32,"input ABI");
_Static_assert(sizeof(crypto_symc_entry_out)==16,"output ABI");
__attribute__((used,section(".ws53_symc_layout"))) const crypto_symc_entry_in input={
 .sym_first_node=1,.sym_last_node=1,.odd_even=1,.sym_alg_length=0x12345678,
 .sym_start_addr=0x20012340,.sym_start_high=0,.iv={0x11223344,0x55667788,0x99aabbcc,0xddeeff00}};
__attribute__((used,section(".ws53_symc_layout"))) const crypto_symc_entry_out output={
 .sym_alg_length=0x12345678,.sym_start_addr=0x20023450,.sym_start_high=0};
'''

def run(args):
    assert not args.output.exists(), 'retain previous evidence'
    sdk = args.sdk.resolve()
    sources = {}
    for rel in (SYMC, COMMON_C, COMMON_H, PORT, REG, *HEADERS):
        data = (sdk/rel).read_bytes()
        assert data == subprocess.check_output(['git','-C',str(sdk),'show',f'{PIN}:{rel}']), rel
        sources[rel] = data
    source = sources[SYMC].decode()
    functions = {name: function(source,name) for name in FUNCTIONS}
    functions['crypto_get_value_by_index'] = function(sources[COMMON_C].decode(),'crypto_get_value_by_index')
    # Preserve original descriptors, software context, and translation tables.
    declarations = source[source.index('typedef struct {'):source.index('#if defined(CRYPTO_CONFIG_ROMBOOT_ENV)')]
    definitions = source[source.index('#define CRYPTO_SYMC_MAX_LIST_NUM'):source.index('/* ! spacc symc int entry')]
    tables = source[source.index('#define SYMC_KEY_64BIT_VAL'):source.index('static td_s32 hal_cipher_symc_ccm_get_s0')]
    item = re.search(r'typedef struct \{[^{}]*\} crypto_table_item;',sources[COMMON_H].decode(),re.S)
    assert item
    original = definitions+declarations+'\nstatic crypto_symc_hard_context g_symc_hard_context[CRYPTO_SYMC_HARD_CHANNEL_MAX];\n'
    original += item[0]+'\n'+tables+functions['crypto_get_value_by_index']+'\n'+'\n'.join(functions[n] for n in FUNCTIONS)
    with tempfile.TemporaryDirectory(prefix='ws53-symc-source-') as temp:
        temp=Path(temp)
        (temp/'common.h').write_text(COMMON)
        for name in ('td_type.h','crypto_type.h','crypto_osal_lib.h'):
            (temp/name).write_text('#include "common.h"\n')
        for rel in (PORT,REG,*HEADERS): (temp/Path(rel).name).write_bytes(sources[rel])
        (temp/'original.inc').write_text(original)
        (temp/'main.c').write_text(HARNESS)
        subprocess.run(['cc','-std=c11','-O1','-Wall','-Wextra','-Werror',
            '-fsanitize=undefined','-fsanitize-undefined-trap-on-error','-I',str(temp),
            str(temp/'main.c'),'-o',str(temp/'probe')],check=True)
        report=json.loads(subprocess.check_output([str(temp/'probe')],text=True))
        target=temp/'target';target.mkdir()
        descriptors=declarations[:declarations.index('typedef struct {\n    crypto_symc_entry_in *entry_in;')]
        (target/'layout.c').write_text('typedef unsigned int td_u32;\n#define CRYPTO_AES_IV_SIZE_IN_WORD 4\n'+descriptors+TARGET)
        subprocess.run([args.target_cc,'-march=rv32imfc','-mabi=ilp32f','-ffreestanding','-nostdinc',
            '-std=c11','-fno-toplevel-reorder','-Wall','-Wextra','-Werror','-c',str(target/'layout.c'),'-o',str(target/'layout.o')],check=True)
        subprocess.run([args.target_objcopy,'--dump-section','.ws53_symc_layout='+str(target/'layout.bin'),str(target/'layout.o')],check=True)
        words=list(struct.unpack('<12I',(target/'layout.bin').read_bytes()))
        assert words==[515,0x12345678,0x20012340,0,0x11223344,0x55667788,0x99aabbcc,0xddeeff00,0,0x12345678,0x20023450,0]
    expected={'control_case_count':48,'configuration_encodings':1728,'rejected_configurations':16,
        'descriptor_cases':700,'submission_cases':128,'iv_cases':72,'ack_cases':16,'attach_cases':16,
        'direct_key_rejections':10,'wait_cases':8}
    for key,value in expected.items(): assert report[key]==value,(key,report[key],value)
    report.update(passed=True,sdk_commit=PIN,input_sha256={p:hashlib.sha256(d).hexdigest() for p,d in sources.items()},
        source_functions={n:hashlib.sha256(s.encode()).hexdigest() for n,s in functions.items()},
        descriptor_abi='RV32IMFC ilp32f little endian',descriptor_words=words,
        compiler=subprocess.check_output(['cc','--version'],text=True).splitlines()[0],
        target_compiler=subprocess.check_output([args.target_cc,'--version'],text=True).splitlines()[0],
        hardware_accessed=False,algorithm_results_verified=False,
        limitations=['MMIO/cache/delay/CPU identity are host recording substitutes; no silicon or drain evidence',
        'The configuration table accepts combinations that are not necessarily valid crypto operations',
        'DFA and optional RAW IRQ branches are not enabled in this source exercise',
        'Only normal input/output descriptors are exercised; no CCM/GCM compound processing',
        'Direct set_key always fails; attach truncates a slot ID and does not own/load a key',
        'Clear timeout releases ownership in SDK; this must not authorize Rust memory reclamation',
        'Output pointer is published before descriptor cache flush; safe handoff must be audited independently'])
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(passed=True,original_functions=len(functions),**expected)))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--target-cc',default='riscv64-linux-gnu-gcc')
    p.add_argument('--target-objcopy',default='riscv64-linux-gnu-objcopy')
    run(p.parse_args())
