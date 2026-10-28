#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned KM keyslot/clear-loader C against deterministic host recorders.

No hardware, OTP, production keys or root derivation is accessed. The mock records
what source asks hardware to do; its replies are not inferred silicon semantics.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from check_ws53_crypto_handoff import function
from check_ws53_crypto_profile import PIN, PORT

HAL = 'src/drivers/drivers/hal/security_unified/'
DRV = 'src/drivers/drivers/driver/security_unified/'
SLOT = HAL + 'hal_km/hal_keyslot.c'
KLAD = HAL + 'hal_km/hal_klad.c'
DSLOT = DRV + 'drv_km/drv_keyslot.c'
DKLAD = DRV + 'drv_km/drv_klad.c'
HEADERS = tuple(HAL + 'hal_cipher/include/' + name for name in (
    'common_include/crypto_common_def.h', 'common_include/crypto_common_macro.h',
    'common_include/crypto_errno.h', 'common_include/crypto_km_struct.h',
    'hal_include/hal_common.h', 'hal_include/hal_keyslot.h',
    'hal_include/hal_klad.h', 'hal_include/hal_rkp.h')) + (
    HAL + 'hal_km/hal_keyslot_reg.h', HAL + 'hal_km/hal_klad_reg.h', PORT)
SLOT_FUNCTIONS = ('inner_kslot_chn_is_locked', 'inner_chk_kslot_busy',
    'inner_kslot_is_busy', 'inner_kslot_is_unlock_failed', 'inner_kslot_wait_idle',
    'inner_get_keyslot_type_reg_value', 'hal_keyslot_lock', 'hal_keyslot_unlock')
KLAD_FUNCTIONS = ('inner_klad_to_be_lock', 'hal_klad_lock', 'hal_klad_unlock',
    'hal_klad_set_key_crypto_cfg', 'hal_klad_set_key_dest_cfg',
    'hal_klad_set_key_secure_cfg', 'hal_klad_set_key_addr', 'hal_klad_clear_data',
    'hal_klad_set_data', 'inner_klad_set_key_odd', 'inner_klad_error_check',
    'inner_klad_wait_clr_route_done', 'inner_klad_symc_start_clr_route',
    'inner_klad_hmac_start_clr_route', 'hal_klad_start_clr_route')
DSLOT_FUNCTIONS = ('drv_keyslot_init', 'drv_keyslot_deinit', 'drv_keyslot_create',
    'inner_keyslot_handle_chk', 'drv_keyslot_destroy')
DKLAD_FUNCTIONS = ('drv_klad_create', 'drv_klad_destroy', 'drv_klad_attach',
    'drv_klad_detach', 'drv_klad_set_attr', 'drv_klad_set_clear_key')

COMMON = r'''#pragma once
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t td_u8;typedef uint16_t td_u16;typedef uint32_t td_u32;
typedef int32_t td_s32;typedef uint64_t td_u64;typedef int td_bool;
typedef void td_void;typedef unsigned long td_ulong;typedef unsigned td_handle;
typedef uintptr_t td_phys_addr_t;typedef void crypto_wait_t;
#define TD_SUCCESS 0
#define TD_FAILURE -1
#define TD_NULL NULL
#define TD_TRUE 1
#define TD_FALSE 0
#define EOK 0
#define CRYPTO_ERROR_ENV 4
#define crypto_log_err(...) ((void)0)
#include "crypto_common_def.h"
#include "crypto_errno.h"
#include "crypto_common_macro.h"
static int memcpy_s(void*d,size_t cap,const void*s,size_t n) {
 if(n>cap)return -1;
 memcpy(d,s,n);return 0;
}
static int memset_s(void*d,size_t cap,int v,size_t n) {
 assert(n<=cap);memset(d,v,n);return 0;
}
'''

HARNESS = r'''#include "common.h"
#include "security_unified_porting.h"
#include "hal_keyslot_reg.h"
#include "hal_klad_reg.h"
#include "hal_common.h"
#include "hal_keyslot.h"
#include "hal_klad.h"
static unsigned regs[0x2000/4],owners[2][1024],cpu,reads,writes,delay;
static unsigned lock_result,slot_commands,unlock_pending,flush_reads,flush_after,flush_error;
static unsigned common_commands,common_after,common_info,common_owner,common_unlocks;
static unsigned starts,route_reads,route_after,route_fail,route_timeout,route_errors;
static unsigned frames[8][6],trace[128][2],ntrace,ack_writes;
static unsigned flush_addr(void) {return cpu==CRYPTO_CPU_TYPE_SCPU?KC_TEECPU_FLUSH_BUSY:KC_REECPU_FLUSH_BUSY;}
static unsigned cmd_addr(void) {return cpu==CRYPTO_CPU_TYPE_SCPU?KC_TEECPU_LOCK_CMD:KC_REECPU_LOCK_CMD;}
static unsigned owner_code(void) {return cpu==CRYPTO_CPU_TYPE_SCPU?2:1;}
static unsigned km_reg_read(unsigned addr) {
 assert(addr%4==0&&addr<sizeof(regs));reads++;
 if(addr==KC_RD_LOCK_STATUS){unsigned s=regs[KC_RD_SLOT_NUM/4];return owners[(s>>15)&1][s&1023];}
 if(addr==flush_addr()&&unlock_pending){flush_reads++;return (flush_after&&flush_reads>=flush_after?0:1)|flush_error;}
 if(addr==KL_COM_LOCK_INFO)return common_info;
 if(addr==KL_COM_LOCK_STATUS)return common_after&&common_commands>=common_after?common_owner:0;
 if(addr==KL_CLR_CTRL&&starts&&(regs[addr/4]&1)){
  route_reads++;
  if(route_after&&route_reads>=route_after&&starts!=route_timeout)regs[addr/4]&=~1u;
 }
 if(addr==KL_ERROR||addr==KC_ERROR){route_errors++;return starts==route_fail?(addr==KL_ERROR?1u:0x80000000u):regs[addr/4];}
 return regs[addr/4];
}
static void km_reg_write(unsigned addr,unsigned value) {
 assert(addr%4==0&&addr<sizeof(regs));writes++;
 if(ntrace<128){trace[ntrace][0]=addr;trace[ntrace++][1]=value;}
 regs[addr/4]=value;
 if(addr==KC_REECPU_LOCK_CMD||addr==KC_TEECPU_LOCK_CMD){
  slot_commands++;
  if(value&(1u<<20)){
   if(lock_result!=1)owners[(value>>15)&1][value&1023]=lock_result==2?7:owner_code();
  }else{unlock_pending=1;flush_reads=0;} /* owner deliberately retained: verify whether source rereads */
 }
 if(addr==KL_LOCK_CTRL)common_commands++;
 if(addr==KL_UNLOCK_CTRL)common_unlocks++;
 if(addr==KL_INT_RAW)ack_writes++;
 if(addr==KL_CLR_CTRL&&(value&1)){
  assert(starts<8);for(unsigned i=0;i<4;i++)frames[starts][i]=regs[KL_DATA_IN_0/4+i];
  frames[starts][4]=regs[KL_KEY_ADDR/4];frames[starts][5]=value;starts++;route_reads=0;
 }
}
static crypto_cpu_type crypto_get_cpu_type(void) {return cpu;}
static void crypto_udelay(unsigned n) {assert(n==1);delay++;}
#include "original.inc"
static void setup(void) {
 memset(regs,0,sizeof(regs));memset(owners,0,sizeof(owners));memset(frames,0,sizeof(frames));
 memset(&g_klad_ctx,0,sizeof(g_klad_ctx));
 cpu=CRYPTO_CPU_TYPE_ACPU;reads=writes=delay=lock_result=slot_commands=unlock_pending=flush_reads=flush_error=0;
 common_commands=common_info=common_unlocks=starts=route_reads=route_fail=route_timeout=route_errors=ntrace=ack_writes=0;
 flush_after=common_after=route_after=1;common_owner=0xaa;
}
static void cleared(void) {for(unsigned i=0;i<4;i++)assert(regs[KL_DATA_IN_0/4+i]==0);}
static unsigned word(const unsigned char *p) {return p[0]|((unsigned)p[1]<<8)|((unsigned)p[2]<<16)|((unsigned)p[3]<<24);}
static unsigned count_writes(unsigned addr) {unsigned n=0;for(unsigned i=0;i<ntrace;i++)n+=trace[i][0]==addr;return n;}
int main(void) {
 const unsigned endian=1;assert(*(unsigned char*)&endian==1);
 assert(KM_REG_BASE_ADDR==0x52022000&&KSLOT_TIMEOUT==1000000&&HKL_LOCK_TIMEOUT==10000&&KLAD_CLR_ROUTE_TIMEOUT==1000000);
 unsigned slot_cases=0,common_cases=0,config_cases=0,data_cases=0,symc_cases=0,hmac_cases=0,driver_cases=0;
 const unsigned seeds[]={0,0xffffffff,0xa5a55a5a,0x12345678};
 const unsigned ids[]={0,1,2,3,4,5,6,7,511,1023,1024,0xffffffff};
 _Alignas(16) unsigned char key[132];for(unsigned i=0;i<sizeof(key);i++)key[i]=(unsigned char)(i*17+3);
 for(unsigned c=0;c<2;c++)for(unsigned t=0;t<2;t++)for(unsigned k=0;k<12;k++)for(unsigned s=0;s<4;s++){
  setup();cpu=c?CRYPTO_CPU_TYPE_SCPU:CRYPTO_CPU_TYPE_ACPU;
  regs[KC_RD_SLOT_NUM/4]=regs[cmd_addr()/4]=seeds[s];unsigned id=ids[k];
  assert(hal_keyslot_lock(id,t)==0&&slot_commands==1&&owners[t][id&1023]==owner_code());
  unsigned expected=(seeds[s]&~0x1083ffu)|(t<<15)|(id&1023)|(1u<<20);
  assert(regs[cmd_addr()/4]==expected&&regs[KC_RD_SLOT_NUM/4]==((seeds[s]&~0x83ffu)|(t<<15)|(id&1023)));
  assert(hal_keyslot_unlock(id,t)==0&&slot_commands==2&&flush_reads==2);
  assert(regs[cmd_addr()/4]==(expected&~(1u<<20))&&owners[t][id&1023]==owner_code());slot_cases+=2;
 }
 for(unsigned c=0;c<2;c++)for(unsigned t=0;t<2;t++){
  for(unsigned o=1;o<8;o++){setup();cpu=c;owners[t][3]=o;assert(hal_keyslot_lock(3,t)==-1&&slot_commands==0);slot_cases++;}
  for(unsigned fail=1;fail<=2;fail++){setup();cpu=c;lock_result=fail;assert(hal_keyslot_lock(3,t)==-1&&slot_commands==1);slot_cases++;}
  setup();cpu=c;regs[flush_addr()/4]=1;assert(hal_keyslot_lock(3,t)==-1&&slot_commands==0);slot_cases++;
  setup();cpu=c;assert(hal_keyslot_unlock(3,t)==0&&slot_commands==0);slot_cases++;
  for(unsigned err=0;err<8;err+=2){setup();cpu=c;owners[t][3]=7;flush_error=err;flush_after=4;
   assert(hal_keyslot_unlock(3,t)==((err&2)?-1:0));assert(delay==3&&slot_commands==1&&owners[t][3]==7);slot_cases++;
  }
  setup();cpu=c;owners[t][3]=owner_code();flush_after=0;
  assert(hal_keyslot_unlock(3,t)==-1&&delay==1000000&&flush_reads==1000000);slot_cases++;
 }
 for(unsigned c=2;c<=CRYPTO_CPU_TYPE_INVALID;c++){setup();cpu=c;assert(hal_keyslot_lock(0,0)==-1&&slot_commands==0);slot_cases++;
  assert(hal_keyslot_unlock(0,0)==-1&&slot_commands==0);slot_cases++;}
 for(unsigned t=2;t<5;t++){setup();assert(hal_keyslot_lock(0,t)==-1&&reads==0&&writes==0);slot_cases++;
  assert(hal_keyslot_unlock(0,t)==-1&&reads==0&&writes==0);slot_cases++;}
 for(unsigned info=0;info<4;info++)for(unsigned ready=0;ready<3;ready++){
  setup();common_info=info<<2;common_after=ready==2?0:ready+1;int expected=(info==1||!common_after)?-1:0;
  assert(hal_klad_lock()==expected);assert(common_commands==(expected?10000:common_after));
  assert(delay==(expected?10000:common_after-1));common_cases++;
 }
 for(unsigned c=0;c<=CRYPTO_CPU_TYPE_INVALID;c++){setup();cpu=c;int ret=hal_klad_lock();
  assert(ret==(c==CRYPTO_CPU_TYPE_ACPU?0:-1));assert(common_commands==(c==CRYPTO_CPU_TYPE_ACPU?1:0));common_cases++;}
 for(unsigned owner=0;owner<256;owner++)for(unsigned fail=0;fail<4;fail++){
  setup();common_commands=1;common_owner=owner;common_info=fail<<4;
  assert(hal_klad_unlock()==(owner==0xaa&&fail==1?-1:0));assert(common_unlocks==(owner==0xaa));common_cases++;
 }
 const unsigned engines[]={0x20,0x50,0xa0,0xa1,0xa2};
 for(unsigned s=0;s<4;s++)for(unsigned e=0;e<5;e++)for(unsigned flags=0;flags<4;flags++){
  setup();regs[KL_KEY_CFG/4]=seeds[s];assert(hal_klad_set_key_crypto_cfg(flags&1,flags>>1,engines[e])==0);
  assert(regs[KL_KEY_CFG/4]==((seeds[s]&~0x30ff0u)|(engines[e]<<4)|(flags<<16)));config_cases++;
 }
 for(unsigned e=0;e<256;e++){unsigned ok=0;for(unsigned i=0;i<5;i++)ok|=e==engines[i];if(ok)continue;
  setup();assert(hal_klad_set_key_crypto_cfg(1,1,e)==-1&&writes==0);config_cases++;}
 for(unsigned s=0;s<4;s++)for(unsigned d=0;d<4;d++)for(unsigned f=0;f<4;f++){
  setup();regs[KL_KEY_CFG/4]=seeds[s];int ok=d<2||(d==2&&f==0);
  assert(hal_klad_set_key_dest_cfg(d,f)==(ok?0:-1));
  assert(writes==(unsigned)ok);if(ok)assert(regs[KL_KEY_CFG/4]==((seeds[s]&~(d==2?0xc0007u:7u))|(d==2?7u:1u)));config_cases++;
 }
 for(unsigned s=0;s<4;s++)for(unsigned mask=0;mask<64;mask++){
  setup();regs[KL_KEY_SEC_CFG/4]=seeds[s];crypto_klad_key_secure_config cfg={.key_sec=mask&1,
   .src_buf_non_sec_support=(mask>>1)&1,.src_buf_sec_support=(mask>>2)&1,
   .dest_buf_non_sec_support=(mask>>3)&1,.dest_buf_sec_support=(mask>>4)&1,.master_only_enable=(mask>>5)&1};
  assert(hal_klad_set_key_secure_cfg(&cfg)==0&&regs[KL_KEY_SEC_CFG/4]==((seeds[s]&~63u)|mask));config_cases++;
 }
 setup();assert(hal_klad_set_key_secure_cfg(NULL)==-1&&writes==0);config_cases++;
 for(unsigned s=0;s<4;s++)for(unsigned d=0;d<4;d++)for(unsigned i=0;i<12;i++){
  setup();regs[KL_KEY_ADDR/4]=seeds[s];assert(hal_klad_set_key_addr(d,ids[i])==(d<2?0:-1));
  if(d<2)assert(regs[KL_KEY_ADDR/4]==((seeds[s]&~1023u)|((ids[i]<<(d==0))&1023)));else assert(writes==0);config_cases++;
 }
 for(unsigned n=0;n<33;n++)for(unsigned null=0;null<2;null++){
  setup();int ok=n==0||(!null&&n<=16&&n%4==0);assert(hal_klad_set_data(null?NULL:key,n)==(ok?0:-1));
  assert(writes==(unsigned)(ok&&n?4:0));if(ok&&n)for(unsigned i=0;i<4;i++)assert(regs[KL_DATA_IN_0/4+i]==(i*4<n?word(key+i*4):0));data_cases++;
 }
 for(unsigned s=0;s<4;s++)for(unsigned n=16;n<=32;n+=8)for(unsigned odd=0;odd<2;odd++)for(unsigned dest=0;dest<3;dest+=2){
  setup();regs[KL_CLR_CTRL/4]=seeds[s]&~1u;regs[KL_KEY_ADDR/4]=0xa5a5003e;
  regs[KL_INT_RAW/4]=seeds[s];route_after=3;
  crypto_klad_clear_key clear={key,n,odd,0};assert(hal_klad_start_clr_route(dest,&clear)==0);
  unsigned blocks=n==16?1:2;assert(starts==blocks&&ack_writes==blocks&&delay==2*blocks&&route_errors==2*blocks);
  for(unsigned b=0;b<blocks;b++){
   assert(frames[b][4]==(0xa5a5003eu|(n==16?odd:b)));
   assert(frames[b][5]==((seeds[s]&~13u)|(((n-8)/8)<<2)|1));
   for(unsigned i=0;i<4;i++)assert(frames[b][i]==(b*16+i*4<n?word(key+b*16+i*4):0));
  }
  assert(regs[KL_INT_RAW/4]==(seeds[s]|(1u<<14))&&common_commands==0&&slot_commands==0);
  assert(count_writes(KL_DATA_IN_0)==blocks+1&&count_writes(KL_LOCK_CTRL)==0);cleared();symc_cases++;
 }
 for(unsigned n=0;n<=40;n++){if(n==16||n==24||n==32)continue;setup();
  crypto_klad_clear_key clear={key,n,0,0};assert(hal_klad_start_clr_route(0,&clear)==-1&&writes==0);symc_cases++;}
 for(unsigned n=16;n<=32;n+=8){setup();crypto_klad_clear_key clear={NULL,n,0,0};
  assert(hal_klad_start_clr_route(0,&clear)==-1&&writes==0);symc_cases++;}
 setup();assert(hal_klad_start_clr_route(0,NULL)==-1&&writes==0);symc_cases++;
 for(unsigned fail=1;fail<=2;fail++)for(unsigned timeout=0;timeout<2;timeout++){
  setup();if(timeout)route_timeout=fail;else route_fail=fail;
  crypto_klad_clear_key clear={key,32,0,0};int ret=hal_klad_start_clr_route(0,&clear);
  assert(ret==(timeout?(int)KM_COMPAT_ERRNO(ERROR_KLAD_TIMEOUT):-1)&&starts==fail);
  assert(delay==(timeout?1000000:0)&&ack_writes==fail-timeout);
  assert(((regs[KL_CLR_CTRL/4]&1)!=0)==(timeout!=0));cleared();symc_cases++;
 }
 const unsigned lengths[]={0,1,15,16,63,64,65,127,128,129};
 for(unsigned type=0;type<=0x31;type++)for(unsigned l=0;l<10;l++)for(unsigned null=0;null<2;null++){
  setup();unsigned len=lengths[l],blocks=type==0x23||type==0x24?8:4;int ok=type<0x31&&(null||len<=128);
  crypto_klad_clear_key clear={null?NULL:key,len,0,type};assert(hal_klad_start_clr_route(1,&clear)==(ok?0:-1));
  if(ok){assert(starts==blocks&&ack_writes==blocks);for(unsigned b=0;b<blocks;b++){
   assert(frames[b][5]==((b<<13)|1));unsigned char expected[16]={0};
   for(unsigned i=0;i<16;i++)if(!null&&b*16+i<len)expected[i]=key[b*16+i];
   for(unsigned i=0;i<4;i++)assert(frames[b][i]==word(expected+i*4));
  }cleared();}else assert(writes==0);hmac_cases++;
 }
 for(unsigned stage=1;stage<=8;stage++)for(unsigned timeout=0;timeout<2;timeout++){
  setup();if(timeout)route_timeout=stage;else route_fail=stage;
  crypto_klad_clear_key clear={key,128,0,0x24};assert(hal_klad_start_clr_route(1,&clear)==(timeout?(int)KM_COMPAT_ERRNO(ERROR_KLAD_TIMEOUT):-1));
  assert(starts==stage&&ack_writes==stage-timeout);cleared();hmac_cases++;
 }
 for(unsigned t=0;t<2;t++)for(unsigned free=0;free<=(t?2:8);free++){
  setup();unsigned max=t?2:8;for(unsigned i=0;i<free;i++)owners[t][i]=7;
  td_handle handle=0xdeadbeef;assert(drv_keyslot_create(&handle,t)==(free==max?-1:0));
  assert(handle==(free==max?0xdeadbeef:(0x55000000u|(t<<16)|free)));driver_cases++;
 }
 setup();td_handle h=99;assert(drv_keyslot_create(&h,2)==0&&h==0&&writes==0);owners[0][0]=7;
 assert(drv_keyslot_destroy(0)==0&&slot_commands==1);driver_cases++;
 const unsigned bad_handles[]={1,0x55000008,0x55010002,0x55020000,0x54000000,0xffffffff};
 for(unsigned i=0;i<6;i++){setup();assert(drv_keyslot_destroy(bad_handles[i])==-1&&writes==0);driver_cases++;}
 for(unsigned attached=0;attached<2;attached++){
  setup();td_handle handle;assert(drv_klad_create(&handle)==0&&handle==KLAD_VALID_HANDLE);
  if(attached)assert(drv_klad_attach(handle,0,0x55000003)==0&&regs[KL_KEY_ADDR/4]==6);
  crypto_klad_attr attr={0};attr.key_cfg.engine=CRYPTO_KLAD_ENGINE_AES;attr.key_cfg.encrypt_support=1;
  assert(drv_klad_set_attr(handle,&attr)==0);crypto_klad_clear_key clear={key,16,0,0};
  assert(drv_klad_set_clear_key(handle,&clear)==0&&common_commands==0&&common_unlocks==0&&slot_commands==0&&starts==1);
  assert(frames[0][4]==(attached?6u:0u));unsigned before=writes;
  if(attached)assert(drv_klad_detach(handle,0,0x55000003)==0);
  assert(drv_klad_destroy(handle)==0&&writes==before&&!g_klad_ctx.is_open);driver_cases++;
 }
 setup();assert(drv_keyslot_init()==0&&drv_keyslot_deinit()==0&&writes==0);driver_cases++;
 setup();assert(drv_klad_create(NULL)==-1&&drv_keyslot_create(NULL,0)==-1&&writes==0);driver_cases++;
 printf("{\"slot_cases\":%u,\"common_lock_cases\":%u,\"configuration_cases\":%u,\"data_cases\":%u,\"symc_route_cases\":%u,\"hmac_route_cases\":%u,\"driver_cases\":%u,\"raw_interrupt_union_size\":%zu,\"clear_timeout_error\":%u}\n",
 slot_cases,common_cases,config_cases,data_cases,symc_cases,hmac_cases,driver_cases,sizeof(kl_int_raw),(unsigned)KM_COMPAT_ERRNO(ERROR_KLAD_TIMEOUT));
 return 0;
}
'''


def run(args):
    assert not args.output.exists(), 'retain previous evidence'
    sources = {}
    for rel in (SLOT, KLAD, DSLOT, DKLAD, *HEADERS):
        data = (args.sdk / rel).read_bytes()
        assert data == subprocess.check_output(['git', '-C', str(args.sdk), 'show', f'{PIN}:{rel}']), rel
        sources[rel] = data
    functions = {}
    for rel, names in ((SLOT, SLOT_FUNCTIONS), (KLAD, KLAD_FUNCTIONS),
                       (DSLOT, DSLOT_FUNCTIONS), (DKLAD, DKLAD_FUNCTIONS)):
        for name in names:
            functions[name] = function(sources[rel].decode(), name)
    slot = sources[SLOT].decode()
    klad = sources[KLAD].decode().split('static td_s32 inner_klad_wait_com_route_done')[0]
    dslot = sources[DSLOT].decode()
    dklad = sources[DKLAD].decode().split('td_s32 drv_klad_set_effective_key')[0]
    original = '\n'.join(re.sub(r'^#include[^\n]*\n', '', s, flags=re.M) for s in (slot, klad, dslot, dklad))
    for name, body in functions.items():
        assert original.count(body) == 1, name
    with tempfile.TemporaryDirectory(prefix='ws53-km-source-') as tmp:
        temp = Path(tmp)
        (temp / 'common.h').write_text(COMMON)
        for name in ('td_type.h', 'crypto_type.h', 'crypto_osal_lib.h'):
            (temp / name).write_text('#include "common.h"\n')
        for rel in HEADERS:
            (temp / Path(rel).name).write_bytes(sources[rel])
        (temp / 'original.inc').write_text(original)
        (temp / 'main.c').write_text(HARNESS)
        subprocess.run(['cc', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
            '-fsanitize=undefined', '-fsanitize-undefined-trap-on-error', '-I', str(temp),
            str(temp / 'main.c'), '-o', str(temp / 'probe')], check=True)
        result = json.loads(subprocess.check_output([str(temp / 'probe')], text=True))
    counts = dict(slot_cases=462, common_lock_cases=1042, configuration_cases=844,
        data_cases=66, symc_route_cases=94, hmac_route_cases=1016, driver_cases=23)
    assert all(result[name] == expected for name, expected in counts.items())
    assert result['raw_interrupt_union_size'] == 8
    result.update(passed=True, sdk_commit=PIN, hardware_accessed=False,
        unmodified_source_functions={n: hashlib.sha256(s.encode()).hexdigest() for n, s in functions.items()},
        input_sha256={n: hashlib.sha256(s).hexdigest() for n, s in sources.items()},
        original_translation_unit_sha256=hashlib.sha256(original.encode()).hexdigest(),
        compiler=subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
        undefined_behavior_sanitizer_trap=True, cryptographic_results_tested=False,
        synthetic_keys_only=True,
        limitations=['Host MMIO, CPU identity, delays and secure-memory routines are substitutes, not silicon proof',
            'No root derivation, OTP, eFuse, persistent policy, real keys or board access',
            'Common KLAD lock does not establish clear-loader cross-core exclusion',
            'C driver-layer tests are not a claim about all higher-level KAPI validation',
            'Source clearing DATA_IN after timeout is not evidence that the route is quiescent',
            'HMAC padding stack lifetime/zeroization is not measured; caller alignment is provided by harness'])
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k.endswith('_cases')} | {'passed': True, 'source_functions': len(functions)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())
