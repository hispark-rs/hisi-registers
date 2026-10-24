#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute original unkeyed HASH configuration/padding with transfer stubs.

The output is message-format evidence, not calculated digests or hardware DMA.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from check_ws53_crypto_profile import PIN, PORT, HASH, REG
from check_ws53_hash_control import HAL, extract_function

STRUCT = 'src/drivers/drivers/hal/security_unified/hal_cipher/include/common_include/crypto_hash_struct.h'
HAL_COMMON = 'src/drivers/drivers/hal/security_unified/hal_cipher/include/hal_include/hal_common.h'
COMMON = r'''#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
#include <stdio.h>
typedef uint8_t td_u8;typedef uint32_t td_u32;typedef int32_t td_s32;
typedef uint64_t td_u64;
typedef unsigned td_handle;typedef int td_bool;typedef void td_void;
#define TD_SUCCESS 0
#define TD_FAILURE -1
#define TD_NULL NULL
#define TD_TRUE 1
#define TD_FALSE 0
#define CRYPTO_SUCCESS 0
#define CRYPTO_FAILURE -1
#define EOK 0
#define CRYPTO_BITS_IN_BYTE 8
#define CRYPTO_WORD_WIDTH sizeof(td_u32)
#define crypto_hal_func_enter() ((void)0)
#define crypto_hal_func_exit() ((void)0)
#define crypto_param_require(c) assert(c)
#define HASH_COMPAT_ERRNO(e) (-(e))
#define ERROR_UNSUPPORT 7
#define ERROR_MEMCPY_S 8
#define crypto_chk_return(c,r,...) do {if(c)return(r);}while(0)
#define crypto_log_err(...) ((void)0)
#define crypto_print(...) ((void)0)
static unsigned crypto_cpu_to_be32(unsigned v) {return __builtin_bswap32(v);}
static int memcpy_s(void *d,size_t cap,const void *s,size_t n) {assert(n<=cap);memcpy(d,s,n);return 0;}
static int memset_s(void *d,size_t cap,int byte,size_t n) {assert(n<=cap);memset(d,byte,n);return 0;}
static unsigned char submitted[256];
static unsigned transfer_length,transfer_flags,starts,waits,flush_length;
static uintptr_t crypto_get_phys_addr(void *p) {return (uintptr_t)p;}
static void crypto_cache_flush(uintptr_t p,unsigned n) {(void)p;flush_length=n;}
static int hal_cipher_hash_add_in_node(unsigned ch,uintptr_t p,unsigned n,unsigned flags) {
 assert(ch==1 && n<=sizeof(submitted));memcpy(submitted,(const void*)p,n);
 transfer_length=n;transfer_flags=flags;return 0;
}
static int hal_cipher_hash_start(unsigned ch,int wait) {assert(ch==1&&wait==1);starts++;return 0;}
static int hal_cipher_hash_wait_done(unsigned ch,unsigned *state,unsigned n) {
 assert(ch==1&&state&&n==64);waits++;return 0;
}
'''

HARNESS = r'''#include "common.h"
#include "crypto_hash_struct.h"
#include "security_unified_porting.h"
#include "hal_spacc_reg.h"
static int g_hal_hash_initialize=1;
static unsigned regs[0xb000/4],selected[2],state_words[2][16];
static unsigned spacc_reg_read(unsigned address) {assert(address%4==0&&address<sizeof(regs));return regs[address/4];}
static void spacc_reg_write(unsigned address,unsigned value) {
 assert(address%4==0&&address<sizeof(regs));regs[address/4]=value;
 for(unsigned ch=1;ch<=2;ch++) {
   if(address==CHANN_HASH_STATE_VAL_ADDR(ch)) {assert(value<16);selected[ch-1]=value;}
   if(address==CHANN_HASH_STATE_VAL(ch)) state_words[ch-1][selected[ch-1]]=value;
 }
}
#include "original.inc"
static const struct {const char *name;crypto_hash_type type;const unsigned char *iv;unsigned size;} algorithms[]={
 {"Sha1",CRYPTO_HASH_TYPE_SHA1,g_sha1_ival,sizeof(g_sha1_ival)},
 {"Sha224",CRYPTO_HASH_TYPE_SHA224,g_sha224_ival,sizeof(g_sha224_ival)},
 {"Sha256",CRYPTO_HASH_TYPE_SHA256,g_sha256_ival,sizeof(g_sha256_ival)},
 {"Sha384",CRYPTO_HASH_TYPE_SHA384,g_sha384_ival,sizeof(g_sha384_ival)},
 {"Sha512",CRYPTO_HASH_TYPE_SHA512,g_sha512_ival,sizeof(g_sha512_ival)},
 {"Sm3",CRYPTO_HASH_TYPE_SM3,g_sm3_ival,sizeof(g_sm3_ival)}
};
static void hex(const unsigned char *p,unsigned n) {for(unsigned i=0;i<n;i++)printf("%02x",p[i]);}
int main(void) {
 const uint32_t endian=1;assert(*(const unsigned char*)&endian==1);
 assert(CRYPTO_HASH_DRV_BUFFER_SIZE==256);
 printf("FLAGS %u %u\n",IN_NODE_TYPE_FIRST,IN_NODE_TYPE_LAST);
 for(unsigned a=0;a<6;a++) {
   crypto_hash_type type=algorithms[a].type;
   unsigned block=crypto_hash_get_block_size(type)/8;
   unsigned result=crypto_hash_get_result_size(type)/8;
   unsigned suffix=crypto_hash_get_message_len(type)/8;
   hash_chn_alg_mode_e mode;hash_chn_alg_sel_e algorithm;unsigned state_size;
   assert(priv_hal_hash_get_cfg(type,&mode,&algorithm,&state_size)==0);
   assert(state_size==algorithms[a].size);
   for(unsigned ch=1;ch<=2;ch++) {
     unsigned state[16]={0};
     assert(drv_hash_get_state(type,state,sizeof(state))==0);
     memset(state_words,0,sizeof(state_words));regs[IN_HASH_CHN_KEY_CTRL(ch)/4]=0xffffffff;
     assert(hal_cipher_hash_config(ch,type,state)==0);
     in_hash_chn_key_ctrl key={.u32=regs[IN_HASH_CHN_KEY_CTRL(ch)/4]};
     assert(key.bits.hash_chn_alg_sel==algorithm&&key.bits.hash_chn_alg_mode==mode);
     assert((key.u32&~0xff0000u)==(0xffffffffu&~0xff0000u));
     for(unsigned i=0;i<state_size/4;i++) {
       const unsigned char *b=algorithms[a].iv+4*i;
       unsigned expected=b[0]|((unsigned)b[1]<<8)|((unsigned)b[2]<<16)|((unsigned)b[3]<<24);
       assert(state_words[ch-1][i]==expected);
     }
   }
   printf("IV %s %u %u %u %u %u ",algorithms[a].name,block,result,suffix,algorithm,mode);
   hex(algorithms[a].iv,state_size);puts("");
   for(unsigned tail=0;tail<block;tail++) {
     unsigned char buffer[256];hal_hash_channel_context ctx={0};
     ctx.hash_type=type;ctx.hash_ctx.data_buffer=buffer;ctx.hash_ctx.data_buffer_len=sizeof(buffer);
     ctx.hash_ctx.tail_len=tail;ctx.hash_ctx.length[1]=(block*2+tail)*8;
     for(unsigned i=0;i<tail;i++)ctx.hash_ctx.tail[i]=(i*37+11)&255;
     starts=waits=flush_length=transfer_length=transfer_flags=0;
     assert(drv_process_tail(1,&ctx)==0);
     assert(starts==1&&waits==1&&transfer_flags==(IN_NODE_TYPE_FIRST|IN_NODE_TYPE_LAST)&&flush_length==transfer_length);
     printf("PAD %s %u %u ",algorithms[a].name,tail,block*2+tail);
     hex(submitted,transfer_length);puts("");
   }
 }
 return 0;
}
'''


def run(args):
    assert not args.output.exists() and not args.vectors.exists(), 'retain earlier evidence'
    sdk = args.sdk.resolve()
    sources = {}
    for rel in (HASH, HAL, STRUCT, HAL_COMMON, PORT, REG):
        data = (sdk / rel).read_bytes()
        assert data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{rel}'])
        sources[rel] = data
    driver = sources[HASH].decode()
    hal = sources[HAL].decode()
    contexts = driver[driver.index('typedef struct {'):driver.index('} hash_state_t;')+len('} hash_state_t;')]
    constants = []
    for name in ('sha1', 'sha224', 'sha256', 'sha384', 'sha512', 'sm3'):
        match = re.search(r'static const td_u8 g_' + name + r'_ival\[\d+\] = \{.*?\n\};', driver, re.S)
        assert match, name
        constants.append(match[0])
    state_table = re.search(r'static const hash_state_t g_hash_state\[\] = \{.*?\n\};', driver, re.S)
    assert state_table
    constants.append(state_table[0])
    enum_start = hal.index('typedef enum {\n    HASH_CHN_ALG_SEL_SHA1')
    enums = hal[enum_start:hal.index('#define HASH_MAX_DEPTH', enum_start)]
    functions = {name: extract_function(source, name) for name, source in (
        ('priv_hal_hash_get_cfg', hal), ('drv_hash_get_state', driver),
        ('hal_cipher_hash_config', hal), ('drv_process_tail', driver))}
    common = sources[HAL_COMMON].decode()
    node_type = re.search(r'typedef enum \{[^{}]*IN_NODE_TYPE_FIRST[^{}]*\}[^;]*;', common, re.S)
    assert node_type, 'original input-node flags enum'
    original = node_type[0] + contexts + enums + '\n'.join(constants) + '\n' + '\n'.join(functions.values())
    with tempfile.TemporaryDirectory(prefix='ws53-hash-message-') as temp:
        temp = Path(temp)
        (temp / 'common.h').write_text(COMMON)
        (temp / 'crypto_common_struct.h').write_text('#include "common.h"\n')
        (temp / 'crypto_hash_struct.h').write_bytes(sources[STRUCT])
        (temp / 'td_type.h').write_text('#include "common.h"\n')
        (temp / 'security_unified_porting.h').write_bytes(sources[PORT])
        (temp / 'hal_spacc_reg.h').write_bytes(sources[REG])
        (temp / 'original.inc').write_text(original)
        (temp / 'main.c').write_text(HARNESS)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=undefined',
                        '-fsanitize-undefined-trap-on-error', '-I', str(temp), str(temp / 'main.c'),
                        '-o', str(temp / 'audit')], check=True)
        header, vectors = subprocess.check_output([str(temp / 'audit')], text=True).split('\n', 1)
        tag, first_flag, last_flag = header.split()
        assert tag == 'FLAGS'
    algorithms = {}
    cases = 0
    for line in vectors.splitlines():
        words = line.split()
        if words[0] == 'IV':
            _, name, block, result, suffix, alg, mode, iv = words
            algorithms[name] = dict(block=int(block), result=int(result), suffix=int(suffix),
                                    algorithm=int(alg), mode=int(mode), initial_bytes=iv)
            assert len(bytes.fromhex(iv)) in (20, 32, 64)
        else:
            assert words[0] == 'PAD'
            _, name, tail, total, hex_bytes = words
            tail, total = int(tail), int(total)
            block, suffix = algorithms[name]['block'], algorithms[name]['suffix']
            data = bytes.fromhex(hex_bytes)
            prefix = bytes((i*37+11)&255 for i in range(tail)) + b'\x80'
            expected_length = block if tail + 1 + suffix <= block else 2*block
            assert len(data) == expected_length
            assert data == prefix + bytes(expected_length-len(prefix)-suffix) + (total*8).to_bytes(suffix, 'big')
            cases += 1
    assert len(algorithms) == 6 and cases == 512
    report = dict(passed=True, sdk_commit=PIN, input_sha256={k: hashlib.sha256(v).hexdigest() for k, v in sources.items()},
        original_functions={k: hashlib.sha256(v.encode()).hexdigest() for k, v in functions.items()},
        algorithms=algorithms, padding_cases=cases, state_window_cases=12, sdk_bounce_buffer_size=256,
        node_type_enum_sha256=hashlib.sha256(node_type[0].encode()).hexdigest(),
        sdk_api_flags=dict(first=int(first_flag), last=int(last_flag)),
        descriptor_flags_note='Original API enum FIRST/LAST is distinct from packed descriptor first/last bits',
        vectors_sha256=hashlib.sha256(vectors.encode()).hexdigest(),
        state_word_encoding='four source IV bytes copied as a little-endian u32; output words copied back to bytes',
        hardware_accessed=False, digests_calculated=False, ubsan_trap_passed=True,
        limitations=['Cache, address translation, DMA start and completion are explicit host stubs',
                     'Only unkeyed source profiles and short-message lengths are exercised',
                     'SDK updates only length[1]; large streaming carry and endian handling are not qualified',
                     'This is input/configuration encoding evidence, not algorithm or hardware acceptance'])
    for path in (args.output, args.vectors):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.vectors.write_text(vectors)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=True, algorithms=6, padding_cases=cases, output=str(args.output))))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--vectors', type=Path, required=True)
    run(p.parse_args())
