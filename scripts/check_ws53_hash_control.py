#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Trace pinned SDK HASH lock/clear/release C with explicit host MMIO stubs.

This checks source control flow and compiler layout, not silicon, DMA drain,
algorithm results, cross-core ownership, or register reset defaults.
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

HAL = 'src/drivers/drivers/hal/security_unified/hal_cipher/spacc/hal_hash.c'
FUNCTIONS = ('inner_hash_irq_enable', 'hal_hash_clear_channel',
             'hal_cipher_hash_init', 'hal_cipher_hash_deinit', 'hal_hash_lock',
             'hal_hash_unlock', 'hal_cipher_hash_done_try')

COMMON = r'''#pragma once
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t td_u8; typedef uint16_t td_u16; typedef uint32_t td_u32;
typedef uint64_t td_u64; typedef int32_t td_s32; typedef int td_bool; typedef void td_void;
typedef int (*crypto_wait_timeout_interruptible)(void *, int (*)(const void *), void *, unsigned);
#define TD_TRUE 1
#define TD_FALSE 0
#define TD_SUCCESS 0
#define TD_FAILURE -1
#define TD_NULL NULL
/* Explicit error/CPU identity stubs: only symbolic comparisons are audited. */
#define ERROR_CHN_BUSY 3
#define HASH_COMPAT_ERRNO(e) (-(e))
#define CRYPTO_CPU_TYPE_SCPU 1
#define crypto_hal_func_enter() ((void)0)
#define crypto_hal_func_exit() ((void)0)
#define crypto_log_err(...) ((void)0)
#define crypto_chk_return(c,r,...) do {if(c) return (r);} while(0)
static int memset_s(void *p,size_t capacity,int byte,size_t n) {
 assert(n<=capacity); memset(p,byte,n); return 0;
}
'''

HARNESS = r'''#include "common.h"
#include "security_unified_porting.h"
#include "hal_spacc_reg.h"
static unsigned regs[0x10000/4], writes[32][2], nw, nr, delays, clear_reads;
static unsigned clear_after, clear_mask, race, secure, frees, allocated;
static _Alignas(32) unsigned char pool[64];
static unsigned spacc_reg_read(unsigned offset) {
 assert(offset%4==0 && offset<sizeof(regs)); nr++;
 if(offset==SPACC_INT_RAW_HASH_CLEAR_FINISH) {
   clear_reads++; return clear_after && clear_reads>=clear_after ? clear_mask : 0;
 }
 return regs[offset/4];
}
static void spacc_reg_write(unsigned offset,unsigned value) {
 assert(offset%4==0 && offset<sizeof(regs) && nw<32);
 writes[nw][0]=offset; writes[nw++][1]=value;
 if(offset==SPACC_HASH_CHN_LOCK && race) return;
 /* ACK clears are a harness input, not a discovered hardware property. */
 if(offset==HASH_CHANN_RAW_INT) regs[offset/4]&=~value;
 else regs[offset/4]=value;
}
static void crypto_udelay(unsigned n) {assert(n==1);delays++;}
static unsigned crypto_get_cpu_type(void) {return secure?CRYPTO_CPU_TYPE_SCPU:0;}
static void *crypto_malloc_mmz(unsigned n) {assert(n==sizeof(pool));allocated++;return pool;}
static void crypto_free_coherent(void *p) {assert(p==pool);frees++;}
static unsigned crypto_get_phys_addr(void *p) {
 uintptr_t address=(uintptr_t)p, base=(uintptr_t)pool;
 assert(address>=base && address<base+sizeof(pool));
 return 0x20050000u+(unsigned)(address-base);
}
td_s32 hal_hash_unlock(td_u32);
td_u32 hal_cipher_hash_done_try(td_u32);
#include "original.inc"
static void setup(void) {
 memset(regs,0,sizeof(regs)); nw=nr=delays=clear_reads=0;
 clear_after=clear_mask=race=secure=frees=allocated=0;
 g_hal_hash_initialize=TD_FALSE;
 assert(hal_cipher_hash_init()==0 && allocated==1);
 assert(hal_cipher_hash_init()==0 && allocated==1);
 memset(pool,0xa5,sizeof(pool));
}
static void trace(const char *name,unsigned chn,int result,int comma) {
 printf("%s{\"name\":\"%s\",\"channel\":%u,\"secure_stub\":%u,\"return\":%d,"
 "\"reads\":%u,\"clear_reads\":%u,\"delay_calls\":%u,\"frees\":%u,\"lock\":%u,\"writes\":[",
 comma?",":"",name,chn,secure,result,nr,clear_reads,delays,frees,regs[SPACC_HASH_CHN_LOCK/4]);
 for(unsigned i=0;i<nw;i++) printf("%s[%u,%u]",i?",":"",writes[i][0],writes[i][1]);
 printf("]}");
}
int main(void) {
 assert(sizeof(hash_entry_in)==16 && HASH_NODE_SIZE==32);
 hash_entry_in descriptor={0}; unsigned words[4];
 descriptor.hash_first_node=1;descriptor.hash_last_node=1;
 descriptor.hash_alg_length=0x12345678;descriptor.hash_start_addr_l=0x20012340;
 descriptor.hash_start_addr_h=0xf;memcpy(words,&descriptor,sizeof(words));
 assert(words[0]==3 && words[1]==0x12345678 && words[2]==0x20012340 && (words[3]&15)==15);
 in_hash_chn_ctrl control={0};control.bits.hash_chn_ss=0xf;control.bits.hash_chn_en=1;
 in_hash_chn_key_ctrl key={0};key.bits.hash_key_chn_id=15;key.bits.hmac_vld=1;
 key.bits.hash_chn_alg_sel=15;key.bits.hash_chn_alg_mode=15;
 in_hash_chn_node_wr_point wp={0};wp.bits.hash_chn_node_wr_point=255;
 in_hash_chn_node_rd_point rp={0};rp.bits.hash_chn_node_rd_point=255;
 assert(control.u32==0x80000f00 && key.u32==0x00ff020f && wp.u32==255 && rp.u32==255);
 printf("{\"descriptor_bytes\":%zu,\"node_depth\":%u,\"context_count\":%u,"
 "\"clear_timeout_polls\":%u,\"layout_words\":[%u,%u,%u,%u],\"cases\":[",
 sizeof(hash_entry_in),HASH_MAX_DEPTH,CRYPTO_HASH_HARD_CHN_CNT,HASH_TIME_OUT_US,
 control.u32,key.u32,wp.u32,rp.u32);
 unsigned count=0;
 for(unsigned ch=1;ch<=2;ch++) for(unsigned cpu=0;cpu<2;cpu++) for(unsigned mode=0;mode<3;mode++) {
   setup(); secure=cpu;clear_mask=1u<<ch;clear_after=mode==0?1:mode==1?4:0;
   regs[SPACC_HASH_CHN_LOCK/4]=0xa0000000;regs[HASH_CHANN_RAW_INT_EN/4]=0xa55a00ff;
   regs[HASH_CHANN_RAW_INT/4]=0x6;
   int result=hal_hash_lock(ch);assert(result==0);
   assert(clear_reads==(mode==2?HASH_TIME_OUT_US:clear_after));
   assert(delays==(mode==2?HASH_TIME_OUT_US:clear_after-1));
   assert(regs[SPACC_HASH_CHN_LOCK/4]==(0xa0000000|((cpu?2u:1u)<<(ch*4))));
   assert(regs[IN_HASH_CHN_CTRL(ch)/4]==(cpu?0x80000500u:0x80000a00u));
   assert(regs[IN_HASH_CHN_NODE_START_ADDR_L(ch)/4]==0x20050000+(ch-1)*32);
   assert(regs[IN_HASH_CHN_NODE_START_ADDR_H(ch)/4]==0 && regs[IN_HASH_CHN_NODE_LENGTH(ch)/4]==2);
   assert(regs[HASH_CHANN_RAW_INT_EN/4]==(0xa55a00ff&~(1u<<ch)));
   for(unsigned i=0;i<64;i++) assert(pool[i]==(i/32==ch-1?0:0xa5));
   trace(mode==2?"lock-clear-timeout-ignored":mode==1?"lock-delayed-clear":"lock-ready",ch,result,count++);
 }
 for(unsigned ch=1;ch<=2;ch++) for(unsigned owner=1;owner<=5;owner++) {
   setup();regs[SPACC_HASH_CHN_LOCK/4]=owner<<(ch*4);
   int result=hal_hash_lock(ch);assert(result==HASH_COMPAT_ERRNO(ERROR_CHN_BUSY));
   assert(nr==1 && nw==0);trace("lock-busy",ch,result,count++);
 }
 for(unsigned ch=1;ch<=2;ch++) {
   setup();race=1;int result=hal_hash_lock(ch);
   assert(result==HASH_COMPAT_ERRNO(ERROR_CHN_BUSY) && nr==2 && nw==1);
   trace("lock-readback-rejected",ch,result,count++);
   setup();clear_mask=1u<<ch;result=hal_hash_clear_channel(ch);
   assert(result==TD_FAILURE && delays==HASH_TIME_OUT_US && nw==2);
   trace("direct-clear-timeout",ch,result,count++);
 }
 for(unsigned ch=0;ch<=3;ch+=3) {
   setup();int result=hal_hash_lock(ch);assert(result==TD_FAILURE && nw==0 && nr==0);
   trace("lock-invalid",ch,result,count++);
 }
 for(unsigned ch=1;ch<=2;ch++) for(unsigned raw=0;raw<8;raw++) {
   setup();regs[HASH_CHANN_RAW_INT/4]=raw;unsigned result=hal_cipher_hash_done_try(ch);
   assert(result==(raw&(1u<<ch)) && nw==1 && writes[0][0]==HASH_CHANN_RAW_INT && writes[0][1]==result);
   assert(regs[HASH_CHANN_RAW_INT/4]==(raw&~(1u<<ch)));
   trace("done-mask-and-ack",ch,(int)result,count++);
 }
 setup();regs[SPACC_HASH_CHN_LOCK/4]=0x215;
 int result=hal_cipher_hash_deinit();
 assert(result==0 && frees==1 && nr==2 && nw==2 && regs[SPACC_HASH_CHN_LOCK/4]==0x200);
 assert(writes[0][0]==SPACC_HASH_CHN_LOCK && writes[0][1]==0x210);
 assert(writes[1][0]==SPACC_HASH_CHN_LOCK && writes[1][1]==0x200);
 for(unsigned i=0;i<64;i++) assert(pool[i]==0);
 trace("deinit-unlocks-0-and-1-retains-2",0,result,count++);
 assert(hal_cipher_hash_deinit()==0 && frees==1 && nr==2 && nw==2);
 printf("],\"case_count\":%u}\n",count);
 return 0;
}
'''

TARGET_LAYOUT = r'''
_Static_assert(sizeof(hash_entry_in)==16, "HASH descriptor size");
_Static_assert(__builtin_offsetof(hash_entry_in,hash_alg_length)==4, "length word");
_Static_assert(__builtin_offsetof(hash_entry_in,hash_start_addr_l)==8, "address word");
const struct {
 hash_entry_in entry;
 in_hash_chn_ctrl control;
 in_hash_chn_key_ctrl key;
 in_hash_chn_node_wr_point wp;
 in_hash_chn_node_rd_point rp;
} layout __attribute__((section(".ws53_hash_layout"),used)) = {
 .entry={.hash_first_node=1,.hash_last_node=1,.hash_alg_length=0x12345678,
         .hash_start_addr_l=0x20012340,.hash_start_addr_h=15},
 .control.bits={.hash_chn_ss=15,.hash_chn_en=1},
 .key.bits={.hash_key_chn_id=15,.hmac_vld=1,.hash_chn_alg_sel=15,.hash_chn_alg_mode=15},
 .wp.bits={.hash_chn_node_wr_point=255}, .rp.bits={.hash_chn_node_rd_point=255}
};
'''


def extract_function(text, name):
    match = re.search(r'^(?:static )?(?:void|td_s32|td_u32) ' + name + r'\([^;]*?\)\n\{', text, re.M)
    assert match, name
    return text[match.start():text.index('\n}\n', match.end()) + 3]


def run(args):
    assert not args.output.exists(), 'retain previous evidence'
    sdk = args.sdk.resolve()
    assert subprocess.check_output(['git', '-C', str(sdk), 'rev-parse', 'HEAD'], text=True).strip() == PIN
    hashes = {}
    for rel in (HAL, PORT, REG):
        data = (sdk / rel).read_bytes()
        assert data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{rel}']), rel
        hashes[rel] = hashlib.sha256(data).hexdigest()
    source = (sdk / HAL).read_text()
    declarations = source[source.index('typedef struct {'):source.index('static void inner_hash_irq_enable')]
    constants = source[source.index('#define HASH_SS_SECURE_VAL'):source.index('td_s32 hal_hash_lock(')]
    functions = {name: extract_function(source, name) for name in FUNCTIONS}
    original = declarations + constants + '\n'.join(functions.values())
    with tempfile.TemporaryDirectory(prefix='ws53-hash-control-') as temp:
        temp = Path(temp)
        (temp / 'common.h').write_text(COMMON)
        (temp / 'td_type.h').write_text('#include "common.h"\n')
        for rel in (PORT, REG):
            (temp / Path(rel).name).write_bytes((sdk / rel).read_bytes())
        (temp / 'original.inc').write_text(original)
        (temp / 'main.c').write_text(HARNESS)
        command = ['cc', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
                   '-fsanitize=undefined', '-fsanitize-undefined-trap-on-error',
                   '-I', str(temp), str(temp / 'main.c'), '-o', str(temp / 'audit')]
        subprocess.run(command, check=True)
        observed = json.loads(subprocess.check_output([str(temp / 'audit')], text=True))
        # Compile the original descriptor and register unions for the actual
        # RV32IMFC / ilp32f ABI. No target executable or device is involved.
        descriptor = source[source.index('typedef struct {'):source.index('} hash_entry_in;') + len('} hash_entry_in;')]
        target_dir = temp / 'rv32'
        target_dir.mkdir()
        (target_dir / 'td_type.h').write_text('#pragma once\ntypedef unsigned int td_u32;\n')
        (target_dir / 'hal_spacc_reg.h').write_bytes((sdk / REG).read_bytes())
        (target_dir / 'layout.c').write_text('#include "hal_spacc_reg.h"\n' + descriptor + TARGET_LAYOUT)
        subprocess.run([args.target_cc, '-march=rv32imfc', '-mabi=ilp32f', '-ffreestanding', '-nostdinc',
                        '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(target_dir), '-c',
                        str(target_dir / 'layout.c'), '-o', str(target_dir / 'layout.o')], check=True)
        subprocess.run([args.target_objcopy, '--dump-section',
                        '.ws53_hash_layout=' + str(target_dir / 'layout.bin'),
                        str(target_dir / 'layout.o')], check=True)
        target_bytes = (target_dir / 'layout.bin').read_bytes()
        target_words = list(struct.unpack('<8I', target_bytes))
        assert target_words == [3, 0x12345678, 0x20012340, 15, *observed['layout_words']]
    assert observed['case_count'] == len(observed['cases']) == 45
    report = dict(sdk_commit=PIN, input_sha256=hashes,
        selected_original_function_sha256={k: hashlib.sha256(v.encode()).hexdigest() for k, v in functions.items()},
        original_declarations_sha256=hashlib.sha256(declarations.encode()).hexdigest(),
        compiler=subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
        target_compiler=subprocess.check_output([args.target_cc, '--version'], text=True).splitlines()[0],
        target_abi='RV32IMFC ilp32f', target_layout_words=target_words,
        target_layout_sha256=hashlib.sha256(target_bytes).hexdigest(),
        undefined_behavior_sanitizer=True, sanitizer_failure_mode='trap', observations=observed,
        hardware_accessed=False, algorithm_results_tested=False, rdl_or_pac_generated=False,
        findings=[
            'Original direct clear reports timeout after 10000000 reads/delay calls; original lock ignores that failure and still enables/publishes the descriptor table and returns success',
            'Original deinit unlocks channels0/1 and leaves channel2 owner set, then zeroes/frees both context tables without issuing clear or checking drain',
            'Original done_try writes only the selected raw status bit back, including a zero write when not pending',
            'Original descriptor is16 bytes and tables contain2 entries; RV32IMFC ilp32f object bytes match the host field probe'],
        limitations=[
            'Reads, ACK effects, lock arbitration, allocation, physical mapping and CPU identity are explicit host stubs',
            'Zero-filled stub MMIO is a test input, not a hardware reset value',
            'No silicon channels, cross-core exclusion, bus drain, IRQ routing, algorithms or security settings qualified',
            'Cross-compiled layout does not establish silicon DMA addressability, cache behavior or safe reclamation'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=True, cases=len(observed['cases']), output=str(args.output))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--target-cc', default='riscv64-linux-gnu-gcc')
    parser.add_argument('--target-objcopy', default='riscv64-linux-gnu-objcopy')
    run(parser.parse_args())
