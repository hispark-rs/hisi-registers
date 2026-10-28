#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compile pinned WS53 security constants and original IRQ/channel allocation C.

OS, lock-result and completion probes are explicit host stubs, not hardware.
No algorithm, key material, MMIO, reset, OTP or board access is performed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
PORT = 'src/drivers/chips/ws53/porting/security_unified/security_unified_porting.h'
IRQ = 'src/drivers/chips/ws53/include/acore/chip_core_irq.h'
DISPATCH = 'src/drivers/drivers/driver/security_unified/init/cfbb/os/crypto_drv_irq.c'
HASH = 'src/drivers/drivers/driver/security_unified/drv_cipher/drv_code/drv_hash.c'
REG = 'src/drivers/drivers/hal/security_unified/hal_cipher/spacc/hal_spacc_reg.h'
CONFIG = 'src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config'
INPUTS = (PORT, IRQ, DISPATCH, HASH, REG, CONFIG,
    'src/drivers/drivers/hal/security_unified/hal_cipher/spacc/hal_hash.c',
    'src/drivers/drivers/driver/security_unified/drv_cipher/drv_code/drv_symc.c',
    'src/drivers/drivers/driver/security_unified/init/cfbb/os/crypto_drv_init.c',
    'src/application/ws53/ws53_application/main.c')

COMMON = r'''#pragma once
#include <stddef.h>
#include <stdint.h>
typedef uint8_t td_u8; typedef uint16_t td_u16; typedef uint32_t td_u32;
typedef uint64_t td_u64; typedef int32_t td_s32; typedef int td_bool; typedef void td_void;
#define TD_TRUE 1
#define TD_FALSE 0
#define TD_SUCCESS 0
#define CRYPTO_SUCCESS 0
#define TD_FAILURE -1
#define TD_NULL NULL
#define crypto_log_dbg(...) ((void)0)
#define crypto_log_err(...) ((void)0)
#define crypto_chk_return(c,r,...) do {if(c) return (r);} while(0)
#define crypto_chk_goto(c,l,...) do {if(c) goto l;} while(0)
#define ERROR_CHN_BUSY 3
#define HASH_COMPAT_ERRNO(e) (-(e))
#define CRYPTO_INVALID_CHN_NUM 0xffffffffU
typedef struct {void *wait;} osal_wait;
typedef int (*crypto_wait_timeout_interruptible)(void);
static unsigned hash_pending,symc_pending,hash_seen,symc_seen,hash_notified,symc_notified,wakes;
static unsigned registered_irq,enabled_irq,hash_waits,symc_waits,hash_modes,symc_modes;
static unsigned lock_fail,lock_seen;
static struct {td_bool open;} g_hash_channel_ctx_list[2];
static int hal_hash_lock(unsigned n) {lock_seen |= 1u<<n; return lock_fail&(1u<<n) ? -1 : 0;}
static int hal_cipher_hash_done_try(unsigned n) {hash_seen |= 1u<<n;return hash_pending==n;}
static void hal_cipher_hash_done_notify(unsigned n) {hash_notified |= 1u<<n;}
static int hal_cipher_symc_done_try(unsigned n) {symc_seen |= 1u<<n;return symc_pending==n?0:-1;}
static void hal_cipher_symc_done_notify(unsigned n) {symc_notified |= 1u<<n;}
static void osal_wait_wakeup(osal_wait *q) {(void)q;wakes++;}
static int osal_wait_init(osal_wait *q) {q->wait=(void*)1;return 0;}
static void osal_wait_destroy(osal_wait *q) {q->wait=NULL;}
static int osal_wait_timeout_interruptible(void) {return 0;}
static int osal_irq_request(unsigned n,int(*fn)(int,void*),void*a,const char*b,void*c) {
    (void)fn;(void)a;(void)b;(void)c;registered_irq=n;return 0;
}
static void osal_irq_enable(unsigned n) {enabled_irq=n;}
static void osal_irq_disable(unsigned n) {(void)n;}
static void osal_irq_free(unsigned n,void*p) {(void)n;(void)p;}
static int hal_cipher_hash_register_wait_func(unsigned n,void*q,crypto_wait_timeout_interruptible f,unsigned t) {
    (void)q;(void)t;if(f)hash_waits|=1u<<n;return 0;
}
static int hal_cipher_symc_register_wait_func(unsigned n,void*q,crypto_wait_timeout_interruptible f,unsigned t) {
    (void)q;(void)t;if(f)symc_waits|=1u<<n;return 0;
}
static int hal_cipher_hash_wait_func_config(unsigned n,int v) {if(v)hash_modes|=1u<<n;return 0;}
static int hal_cipher_symc_wait_func_config(unsigned n,int v) {if(v)symc_modes|=1u<<n;return 0;}
'''

HARNESS = r'''#include <assert.h>
#include <stdio.h>
#include "common.h"
#include "security_unified_porting.h"
#include "chip_core_irq.h"
#include "hal_spacc_reg.h"
#include "crypto_drv_irq.c"
#include "allocator.inc"
int main(void) {
 printf("{\"bases\":[%u,%u,%u,%u,%u],\"irq\":[%u,%u,%u,%u,%u,%u,%u,%u,%u],",
 SPACC_REG_BASE_ADDR,PKE_REG_BASE_ADDR,KM_REG_BASE_ADDR,TRNG_REG_BASE_ADDR,FAPC_REG_BASE_ADDR,
 PKE_REE_IRQ_NUM,SPACC_REE_IRQ_NUM,RKP_REE_IRQ_NUM,KLAD_REE_IRQ_NUM,
 M_DMA_IRQN,SEC_INT1_IRQN,SEC_INT2_IRQN,SEC_INT3_IRQN,SEC_INT_IRQN);
 printf("\"channels\":[%u,%u,%u,%u],\"hash_ctrl_offsets\":[%u,%u],",
 CRYPTO_HASH_HARD_CHN_CNT,CRYPTO_HASH_HARD_CHN_MASK,CRYPTO_SYMC_HARD_CHANNEL_MAX,CRYPTO_SYMC_HARD_CHANNEL_MASK,
 IN_HASH_CHN_CTRL(1),IN_HASH_CHN_CTRL(2));
 assert(crypto_irq_init()==0);
 printf("\"registration\":[%u,%u,%u,%u,%u,%u],\"dispatch\":[",
 registered_irq,enabled_irq,hash_waits,symc_waits,hash_modes,symc_modes);
 for(hash_pending=0;hash_pending<=2;hash_pending++) for(symc_pending=0;symc_pending<=2;symc_pending++) {
   hash_seen=symc_seen=hash_notified=symc_notified=wakes=0;
   assert(spacc_interrupt_irq(SPACC_REE_IRQ_NUM,NULL)==0);
   printf("%s[%u,%u,%u,%u,%u,%u,%u]",hash_pending||symc_pending?",":"",
     hash_pending,symc_pending,hash_seen,symc_seen,hash_notified,symc_notified,wakes);
 }
 crypto_irq_deinit();
 printf("],\"allocator\":[");
 for(unsigned open=0;open<4;open++) for(unsigned fail=0;fail<4;fail++) {
   g_hash_channel_ctx_list[0].open=open&1;g_hash_channel_ctx_list[1].open=(open>>1)&1;
   lock_fail=fail<<1;lock_seen=0;unsigned chosen=0;
   int result=drv_hash_lock_chn(&chosen);
   printf("%s[%u,%u,%u,%d,%u]",open||fail?",":"",open,fail,chosen,result,lock_seen);
 }
 puts("]}");
 return 0;
}
'''


def extract_allocator(text):
    start = text.index('static td_s32 drv_hash_lock_chn(td_u32 *chn_num)\n{')
    return text[start:text.index('\n}\n', start)+3]


def run(args):
    assert not args.output.exists(), 'retain previous evidence'
    sdk = args.sdk.resolve()
    assert subprocess.check_output(['git','-C',str(sdk),'rev-parse','HEAD'],text=True).strip() == PIN
    hashes = {}
    for rel in INPUTS:
        actual = (sdk/rel).read_bytes()
        assert actual == subprocess.check_output(['git','-C',str(sdk),'show',f'{PIN}:{rel}']), rel
        hashes[rel] = hashlib.sha256(actual).hexdigest()
    config = (sdk/CONFIG).read_text()
    enabled = [line.split('=',1)[0] for line in config.splitlines()
        if line.endswith('=y') and any(x in line for x in ('SECURITY_UNIFIED','PKE_SUPPORT','SPACC_IRQ','PKE_IRQ'))]
    assert 'CONFIG_SPACC_IRQ_ENABLE' in enabled and 'CONFIG_PKE_IRQ_ENABLE' not in enabled
    allocator = extract_allocator((sdk/HASH).read_text())
    with tempfile.TemporaryDirectory(prefix='ws53-crypto-profile-') as temp:
        temp=Path(temp)
        (temp/'common.h').write_text(COMMON)
        for name in ('td_type.h','crypto_drv_irq.h','crypto_hash_struct.h','hal_hash.h','hal_symc.h',
                     'hal_pke.h','drv_hash.h','drv_inner.h','crypto_drv_common.h','crypto_common_macro.h'):
            (temp/name).write_text('#include "common.h"\n')
        for rel in (PORT,IRQ,REG,DISPATCH): (temp/Path(rel).name).write_bytes((sdk/rel).read_bytes())
        (temp/'allocator.inc').write_text(allocator)
        (temp/'main.c').write_text(HARNESS)
        command = ['cc','-std=c11','-Wall','-Wextra','-Werror','-DCONFIG_SPACC_IRQ_ENABLE=1','-I',str(temp),str(temp/'main.c'),'-o',str(temp/'audit')]
        subprocess.run(command,check=True,capture_output=True,text=True)
        observed = json.loads(subprocess.check_output([str(temp/'audit')],text=True))
    assert observed['bases'] == [0x52010000,0x52020000,0x52022000,0x52024000,0x53000000]
    assert observed['irq'] == [58,59,60,61,58,59,60,61,70]
    assert observed['channels'] == [2,0xfffe,2,0xfffe]
    assert observed['hash_ctrl_offsets'] == [0x5100,0x5200]
    assert observed['registration'] == [59,59,2,2,2,2]
    for h,s,hseen,sseen,hn,sn,wake in observed['dispatch']:
        assert hseen == 2 and sseen == (0 if h==1 else 2)
        assert (hn,sn,wake) == ((2,0,1) if h==1 else (0,2,1) if s==1 else (0,0,0))
    for open_,fail,chosen,rc,visited in observed['allocator']:
        available = [n for n in (1,2) if not (open_|fail)&(1<<(n-1))]
        assert chosen == (available[0] if available else 0xffffffff)
        assert rc == (0 if available else -3)
        assert visited & ~6 == 0
    report = dict(sdk_commit=PIN,input_sha256=hashes,application_config=enabled,
        compiler=subprocess.check_output(['cc','--version'],text=True).splitlines()[0],
        original_irq_translation_unit=True,original_allocator_function_sha256=hashlib.sha256(allocator.encode()).hexdigest(),
        observations=observed,irq_dispatch_cases=9,hash_allocator_cases=16,
        findings=['SPACC IRQ59 matches application SEC_INT1; RKP60/KLAD61 match SEC_INT2/3',
          'Declared PKE IRQ58 aliases application MDMA58; PKE IRQ disabled in this profile, routing unresolved',
          'Hash allocator can return channels1/2, but IRQ registration and dispatch visit only channel1',
          'SYMC IRQ registration/dispatch likewise visit only channel1; its two-channel allocator is source-inspected only',
          'Application starts control core and IPC before cipher environment init; direct exclusive ownership is not established'],
        hardware_accessed=False,algorithm_results_tested=False,rdl_or_pac_generated=False,
        limitations=['OS/wait/lock-result/completion callbacks are host stubs',
          'No silicon channel count, IRQ wiring, register side effects or reset defaults proven',
          'No keys, OTP, security settings, Flash or MMIO accessed',
          'Application ownership/coherence/init and cross-core protocols still require audit'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(passed=True,irq_cases=9,allocator_cases=16,output=str(args.output))))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args())
