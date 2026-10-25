#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Audit original WS53 crypto build, allocator/cache callbacks and clock writes.

All external allocation, cache instructions, PM and MMIO are host recorders.
This does not establish physical coherence, bus reachability or core ownership.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
DRIVER = 'src/drivers/drivers/driver/security_unified/'
HAL = 'src/drivers/drivers/hal/security_unified/'
INIT = DRIVER+'init/cfbb/os/crypto_drv_init.c'
COMMON = DRIVER+'drv_cipher/drv_code/crypto_drv_common.c'
TYPES = DRIVER+'drv_cipher/drv_include/drv_common.h'
HAL_TYPES = HAL+'hal_cipher/include/hal_include/hal_common.h'
OS = HAL+'crypto_osal/cfbb/os/crypto_osal_lib.c'
BOOT = HAL+'crypto_osal/cfbb/boot/crypto_osal_lib.c'
ALLOC = 'src/kernel/osal/src/liteos/osal_addr.c'
CACHE = 'src/kernel/osal/src/liteos/osal_cache.c'
ADAPT = 'src/kernel/osal/adapt/liteos/osal_debug_adapt.c'
CLOCK = 'src/drivers/chips/ws53/clock_recover/clock_recover.c'
IO = 'src/middleware/utils/common_headers/chip_io.h'
CONFIG = 'src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config'
TARGET = 'src/build/config/target_config/ws53/target_config.py'
PRODUCT = 'src/build/config/target_config/ws53/config.py'
PM = 'src/middleware/chips/ws53/pm/pm_veto/pm_veto_porting.h'
CONFIGS = {n:'src/build/config/target_config/ws53/menuconfig/acore/ws53_'+n+'.config'
           for n in ('romboot','ssb','loaderboot','flashboot')}
INPUTS = (INIT,COMMON,TYPES,HAL_TYPES,OS,BOOT,ALLOC,CACHE,ADAPT,CLOCK,IO,CONFIG,TARGET,PRODUCT,
          HAL+'CMakeLists.txt',DRIVER+'CMakeLists.txt',
          DRIVER+'drv_cipher/drv_code/crypto_osal_adapt.h',
          HAL+'hal_cipher/spacc/hal_spacc_reg.h',
          'src/application/ws53/ws53_application/main.c',PM,*CONFIGS.values(),
          HAL+'crypto_osal/cfbb/os/crypto_osal_lib.h')


def function(source, name):
    matches = list(re.finditer(r'^(?:static )?(?:td_\w+|crypto_\w+|void|unsigned long)'
                   r'[^\n;{}]*\b'+re.escape(name)+r'\([^;{}]*\)\s*\{',source,re.M))
    assert len(matches)==1, 'missing/ambiguous function '+name
    match=matches[0]
    end,depth = match.end(),1
    while depth:
        assert end < len(source)
        depth += (source[end]=='{')-(source[end]=='}')
        end += 1
    return source[match.start():end]


def constant(source, name):
    match=re.search(r'^#define\s+'+name+r'\s+(0x[\da-fA-F]+|\d+)\s*$',source,re.M)
    assert match,name
    return int(match[1],0)


def entry(source, name):
    """Select a literal-key dictionary from the original Python AST, not exec SDK."""
    found=[v for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Dict)
           for k,v in zip(n.keys,n.values) if isinstance(k,ast.Constant) and k.value==name]
    assert len(found)==1 and isinstance(found[0],ast.Dict),name
    return {k.value:v for k,v in zip(found[0].keys,found[0].values) if isinstance(k,ast.Constant)}


def run_cmake(directory, source, *, application, defines, configuration):
    script=directory/'select.cmake'
    # The original CMake files only declare component lists; build_component is
    # replaced with a recorder. CMAKE_CURRENT_SOURCE_DIR is a stable virtual root.
    code=['cmake_minimum_required(VERSION 3.20)', 'set(CMAKE_CURRENT_SOURCE_DIR "/SDK_COMPONENT")',
          'set(APPLICATION "'+application+'")','set(DEFINES [==['+';'.join(defines)+']==])']
    code += ['set('+name+' 1)' for name,value in configuration.items() if value=='y']
    code += ['function(build_component)',
             'file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/sources.txt" "${SOURCES}")',
             'file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/defines.txt" "${PUBLIC_DEFINES}")',
             'file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/environment.txt" "${CRYPTO_BUILD_ENV}")',
             'endfunction()', 'include("'+str(source)+'")']
    script.write_text('\n'.join(code)+'\n')
    result=subprocess.run(['cmake','-P',str(script)],cwd=directory,check=True,capture_output=True)
    assert not result.stderr,result.stderr.decode()
    return dict(environment=(directory/'environment.txt').read_text(),
                sources=[s.removeprefix('/SDK_COMPONENT/') for s in (directory/'sources.txt').read_text().split(';') if s],
                defines=[s for s in (directory/'defines.txt').read_text().split(';') if s])


PREAMBLE=r'''#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
typedef void td_void;typedef unsigned td_u32;typedef int td_s32;typedef int td_bool;
typedef uintptr_t td_phys_addr_t;typedef uintptr_t UINTPTR;
#define TD_NULL NULL
#define TD_SUCCESS 0
#define TD_FAILURE -1
#define TD_TRUE 1
#define TD_FALSE 0
#define OSAL_GFP_KERNEL 0
#define CACHE_LINE_SIZE 32
#define crypto_unused(x) ((void)(x))
#define osal_unused(x) ((void)(x))
static _Alignas(32) unsigned char allocation[32];
static unsigned alloc_size,alloc_alignment,alloc_calls,free_calls;
static int alloc_fail;
static void *m_aucSysMem0=allocation;
static void *LOS_MemAllocAlign(void *pool,unsigned size,unsigned boundary) {
 assert(pool==m_aucSysMem0);alloc_size=size;alloc_alignment=boundary;alloc_calls++;
 return alloc_fail?NULL:allocation;
}
static int LOS_MemFree(void *pool,void *p) {assert(pool==m_aucSysMem0&&p==allocation);free_calls++;return 0;}
static unsigned clean_calls,inv_calls,all_calls,cache_size;
static uintptr_t cache_address;
static void ArchDCacheCleanByVa(uintptr_t a,uint32_t s) {cache_address=a;cache_size=s;clean_calls++;}
static void ArchDCacheInvByVa(uintptr_t a,uint32_t s) {cache_address=a;cache_size=s;inv_calls++;}
static void ArchDCacheFlush(void) {all_calls++;}
static unsigned mem_type_seen;
static td_bool table_callback(unsigned long *t,unsigned long *r,unsigned long *w) {*t=1;*r=2;*w=3;return 1;}
static td_bool crypto_smmu_support(void);
typedef struct {int value;} crypto_atomic;
static crypto_atomic g_deepsleep_vote={0};
static unsigned veto_add,veto_remove;
static int crypto_atomic_read(crypto_atomic *a) {return a->value;}
static void crypto_atomic_inc(crypto_atomic *a) {a->value++;}
static void crypto_atomic_dec(crypto_atomic *a) {a->value--;}
/* ORIGINAL_PM_ENUM */
static void uapi_pm_add_sleep_veto(unsigned id) {assert(id==PM_SECURITY_VETO_ID);veto_add++;}
static void uapi_pm_remove_sleep_veto(unsigned id) {assert(id==PM_SECURITY_VETO_ID);veto_remove++;}
'''

HARNESS=r'''
static void *record_alloc(unsigned size,crypto_mem_type type) {mem_type_seen=type;return inner_malloc_coherent(size,type);}
int main(void) {
 const unsigned sizes[]={0,1,31,32,33,256,4096,UINT32_MAX};
 const uintptr_t addresses[]={0,0x20010000,0x20010001,0x2005fff0,0xffffffc0,0xffffffff};
 unsigned alloc_cases=0,cache_cases=0,pm_cases=0;
#if !AUDIT_BOOT
 assert(drv_cipher_register_func(NULL)==TD_FAILURE);
 assert(crypto_malloc_coherent(32)==NULL&&crypto_malloc_mmz(32)==NULL);
 assert(crypto_get_phys_addr((void*)0x20010001)==0x20010001);
 assert(crypto_get_cpu_type()==CRYPTO_CPU_TYPE_ACPU);
 assert(!crypto_smmu_support());
 crypto_drv_func callbacks={0};
 callbacks.malloc_coherent=record_alloc;callbacks.free_coherent=inner_free_coherent;
 callbacks.get_phys_addr=inner_get_phys_addr;callbacks.get_cpu_type=liteos_get_cpu_type;
 assert(drv_cipher_register_func(&callbacks)==TD_SUCCESS);
 for(unsigned smmu=0;smmu<2;smmu++) {
  callbacks.get_smmu_table_addr=smmu?table_callback:NULL;
  assert(drv_cipher_register_func(&callbacks)==TD_SUCCESS);
  assert(crypto_smmu_support()==(int)smmu);
  for(unsigned fail=0;fail<2;fail++)for(unsigned api=0;api<2;api++)for(unsigned n=0;n<8;n++) {
   alloc_fail=fail;unsigned before=alloc_calls;
   void *p=api?crypto_malloc_mmz(sizes[n]):crypto_malloc_coherent(sizes[n]);
   assert(alloc_calls==before+1&&alloc_size==sizes[n]&&alloc_alignment==32);
   assert(mem_type_seen==(!api&&smmu?CRYPTO_MEM_TYPE_SMMU:CRYPTO_MEM_TYPE_MMZ));
   assert(p==(fail?NULL:allocation));
   if(p) {unsigned before_free=free_calls;crypto_free_coherent(p);assert(free_calls==before_free+1);}
   alloc_cases++;
  }
 }
#endif
 for(unsigned a=0;a<6;a++) {
#if !AUDIT_BOOT
  assert(crypto_get_phys_addr((void*)addresses[a])==addresses[a]);
#endif
  for(unsigned n=0;n<8;n++) {
   clean_calls=inv_calls=all_calls=0;cache_address=cache_size=0;
   crypto_cache_flush(addresses[a],sizes[n]);
#if AUDIT_BOOT
   assert(clean_calls==0&&cache_address==0&&cache_size==0);
#else
   assert(clean_calls==1&&cache_address==addresses[a]&&cache_size==sizes[n]);
#endif
   crypto_cache_inv(addresses[a],sizes[n]);
#if AUDIT_BOOT
   assert(inv_calls==0);
#else
   assert(inv_calls==1&&cache_address==addresses[a]&&cache_size==sizes[n]);
#endif
   crypto_cache_all();assert(all_calls==!AUDIT_BOOT);cache_cases++;
  }
 }
#if !AUDIT_BOOT
 assert(crypto_get_cpu_type()==CRYPTO_CPU_TYPE_ACPU);
 callbacks=(crypto_drv_func){0};assert(drv_cipher_register_func(&callbacks)==TD_SUCCESS);
 assert(crypto_malloc_coherent(32)==NULL&&crypto_malloc_mmz(32)==NULL);
 unsigned before_free=free_calls;crypto_free_coherent(allocation);assert(free_calls==before_free);
 const int votes[]={0,1,2,INT_MAX-1,INT_MAX};
 for(unsigned i=0;i<5;i++)for(unsigned pre=0;pre<2;pre++) {
  g_deepsleep_vote.value=votes[i];veto_add=veto_remove=0;
  if(pre) crypto_preprocess();else crypto_postprocess();
  assert(g_deepsleep_vote.value==(pre?(votes[i]<INT_MAX?votes[i]+1:INT_MAX):(votes[i]>0?votes[i]-1:0)));
  assert(veto_add==(pre&&votes[i]==0));assert(veto_remove==(!pre&&votes[i]<=1));pm_cases++;
 }
 g_deepsleep_vote.value=0;veto_add=veto_remove=0;
 for(unsigned i=0;i<3;i++)crypto_preprocess();
 assert(veto_add==1&&veto_remove==0&&g_deepsleep_vote.value==3);
 for(unsigned i=0;i<3;i++)crypto_postprocess();
 assert(veto_add==1&&veto_remove==1&&g_deepsleep_vote.value==0);pm_cases++;
#endif
 printf("{\"allocation_cases\":%u,\"cache_cases\":%u,\"cache_callbacks_per_operation\":%d,\"identity_addresses\":%u,\"sequential_pm_cases\":%u,\"security_veto_id\":%u}\n",alloc_cases,cache_cases,!AUDIT_BOOT,AUDIT_BOOT?0:6,pm_cases,PM_SECURITY_VETO_ID);
 return 0;
}
'''


def run(args):
    assert not args.output.exists(),'retain earlier evidence'
    sdk=args.sdk.resolve()
    assert subprocess.check_output(['git','-C',str(sdk),'rev-parse','HEAD'],text=True).strip()==PIN
    sources={}
    for path in INPUTS:
        raw=(sdk/path).read_bytes()
        assert raw==subprocess.check_output(['git','-C',str(sdk),'show',PIN+':'+path]),path
        sources[path]=raw.decode('utf-8-sig')
    config=dict(re.findall(r'^(CONFIG_\w+)=(.+)$',sources[CONFIG],re.M))
    assert '# CONFIG_SM_EFUSE_CHECK is not set' in sources[CONFIG]
    assert config['CONFIG_MCU_MODE_1']=='y' and config['CONFIG_SECURITY_UNIFIED_SUPPORT_DEEP_SLEEP']=='y'
    product=entry(sources[PRODUCT],'ws53_liteos_app')
    assert ast.literal_eval(product['base_target_name'])=='target_application_rom_template'
    target=entry(sources[TARGET],'target_application_rom_template')
    defines=ast.literal_eval(target['defines'])+ast.literal_eval(product['defines'])
    assert 'BUILD_APPLICATION_STANDARD' in defines and 'USE_LITEOS' in defines
    functions={}
    assert function('    if (f()) { x(); }\nvoid f(void) { return; }','f')=='void f(void) { return; }'
    def take(path,name):
        body=function(sources[path],name);functions[path+':'+name]=hashlib.sha256(body.encode()).hexdigest();return body
    cpu_enum=re.search(r'typedef enum \{[^{}]*CRYPTO_CPU_TYPE_SCPU[^{}]*\} crypto_cpu_type;',sources[HAL_TYPES])
    assert cpu_enum
    pm_enum=re.search(r'typedef enum pm_veto_id \{[^{}]*\} pm_veto_id_t;',sources[PM])
    assert pm_enum
    types=sources[TYPES][sources[TYPES].index('typedef enum {'):sources[TYPES].index('} crypto_drv_func;')+len('} crypto_drv_func;')]
    common=[take(ALLOC,'osal_kmalloc_align'),take(ALLOC,'osal_kfree'),
            take(INIT,'inner_malloc_coherent'),take(INIT,'inner_free_coherent'),
            take(INIT,'inner_get_phys_addr'),take(INIT,'liteos_get_cpu_type'),
            'static crypto_drv_func g_drv_func={0};',
            *[take(COMMON,n) for n in ('crypto_smmu_support','crypto_malloc_coherent','crypto_malloc_mmz',
              'crypto_free_coherent','crypto_get_phys_addr','drv_cipher_register_func','crypto_get_cpu_type')]]
    common += [take(OS,'crypto_preprocess'),take(OS,'crypto_postprocess')]
    reports={};builds={}
    with tempfile.TemporaryDirectory(prefix='ws53-crypto-handoff-') as temp:
        directory=Path(temp)
        for boot in (False,True):
            callbacks=[take(CACHE,'osal_dcache_region_clean'),take(CACHE,'osal_dcache_region_inv'),
                       take(ADAPT,'osal_dcache_flush_all'),
                       *[take(BOOT if boot else OS,n) for n in ('crypto_cache_flush','crypto_cache_inv','crypto_cache_all')]]
            code='\n'.join([PREAMBLE.replace('/* ORIGINAL_PM_ENUM */',pm_enum[0]),cpu_enum[0],types,*common,*callbacks,HARNESS])
            source=directory/'audit.c';source.write_text(code)
            binary=directory/'audit'
            subprocess.run([args.cc,'-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
                '-fsanitize=undefined','-fsanitize-undefined-trap-on-error',f'-DAUDIT_BOOT={int(boot)}',
                str(source),'-o',str(binary)],check=True,capture_output=True)
            reports['boot_cache_only' if boot else 'os']=json.loads(subprocess.check_output([str(binary)],text=True))
        for name,app,defs in [('app','application',defines),('romboot','romboot',[]),('ssb','ssb',[]),
                              ('loaderboot','loaderboot',[]),('flashboot','flashboot',[])]:
            builds[name]={}
            stage_config=config if name=='app' else dict(re.findall(r'^(CONFIG_\w+)=(.+)$',sources[CONFIGS[name]],re.M))
            for layer,prefix in (('hal',HAL),('driver',DRIVER)):
                builds[name][layer]=run_cmake(directory,sdk/(prefix+'CMakeLists.txt'),
                    application=app,defines=defs,configuration=stage_config)
            expected='os' if name=='app' else 'boot'
            assert builds[name]['hal']['environment']==expected
            assert f'crypto_osal/cfbb/{expected}/crypto_osal_lib.c' in builds[name]['hal']['sources']
        assert 'init/cfbb/os/crypto_drv_init.c' in builds['app']['driver']['sources']
        assert 'hal_cipher/spacc/hal_symc.c' in builds['app']['hal']['sources']
    # Capture the source-level whole-word auto-gating sequence, not bit meaning
    # or a claim that zero is a clock-disable command.
    clock_body=take(CLOCK,'system_crg_cfg')
    writes=re.findall(r'writel\((\w+),\s*(0x[\da-fA-F]+)\)',clock_body)
    assert len(writes)==12
    clock_writes=[dict(symbol=n,address=constant(sources[CLOCK],n),value=int(v,0)) for n,v in writes]
    assert clock_writes[9]['address']==0x52001114 and clock_writes[11]['address']==0x5200111c
    assert all(v['value']==0 for v in clock_writes[2:])
    with tempfile.TemporaryDirectory(prefix='ws53-crypto-clock-') as temp:
        directory=Path(temp)
        code=['#define _GNU_SOURCE', '#include <assert.h>', '#include <stdio.h>', '#include <sys/mman.h>',
              '#include '+json.dumps(str(sdk/IO)),
              '_Static_assert(sizeof(writel(0,0)) == 4, "SDK whole-word access");',
              'static volatile uint32_t *host;']
        code += [f'#define {v["symbol"]} ((uintptr_t)&host[{3*i+1}])' for i,v in enumerate(clock_writes)]
        code += [clock_body,r'''
int main(void) {
 void *p=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS|MAP_32BIT,-1,0);
 assert(p!=MAP_FAILED&&(uintptr_t)p<=UINT32_MAX-4096);host=p;
 const uint32_t seeds[]={0,0xffffffff,0xa5a55a5a,0x12345678};
 for(unsigned n=0;n<4;n++) {
  for(unsigned i=0;i<64;i++)host[i]=seeds[n];
  system_crg_cfg();
  for(unsigned i=0;i<64;i++) {
   uint32_t expected=seeds[n];
   if(i%3==1&&i/3<12)expected=i==1?0x40:i==4?0xa00:0;
   assert(host[i]==expected);
  }
 }
 assert(munmap(p,4096)==0);
 puts("{\"whole_word_writes\":48,\"seeds\":4,\"guard_words_per_case\":52}");return 0;
}''']
        source=directory/'clock.c';source.write_text('\n'.join(code))
        binary=directory/'clock'
        subprocess.run([args.cc,'-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-fsanitize-undefined-trap-on-error',str(source),'-o',str(binary)],check=True,capture_output=True)
        clock_host=json.loads(subprocess.check_output([str(binary)],text=True))
    # This is an audit flag, not a synthetic register description: no use of
    # SPACC_CALC_CRG_CFG exists in the pinned SDK beyond its macro definition.
    refs=subprocess.check_output(['git','-C',str(sdk),'grep','-n','SPACC_CALC_CRG_CFG',PIN,'--','src'],text=True).splitlines()
    assert len(refs)==1 and '#define SPACC_CALC_CRG_CFG' in refs[0]
    report=dict(passed=True,sdk_commit=PIN,profile='ws53_liteos_app / acore / MCU_MODE_1',
        build_selection=builds,callbacks=reports,source_function_count=len(functions),
        original_functions_sha256=functions,auto_gate_writes=clock_writes,clock_host=clock_host,
        sm_efuse_check_enabled=False,deep_sleep_veto_enabled=True,spacc_calc_crg_used=False,
        tools=dict(compiler=subprocess.check_output([args.cc,'--version'],text=True).splitlines()[0],
                   cmake=subprocess.check_output(['cmake','--version'],text=True).splitlines()[0]),
        host_profile=dict(ub_sanitizer_trap=True,allocator_dfx_caller_branch=False,
                          pm_atomic_operations='sequential recorders; no concurrent or cross-core test',
                          boot_audit='weak cache defaults only; OS allocator is not a boot allocator'),
        source_sha256={p:hashlib.sha256((sdk/p).read_bytes()).hexdigest() for p in INPUTS},
        host_only=True,hardware_accessed=False,physical_coherence_proved=False,
        dma_reachability_proved=False,ownership_proved=False,reset_or_gate_proved=False,
        limitations=['Allocator is aligned ordinary LiteOS heap, mem_type ignored by port',
          'Identity pointer translation does not establish DMA reachability',
          'OS cache callbacks forward to ArchDCache*; actual instruction implementation not executed',
          'Boot weak no-op cache hooks can be overridden; this is not proof of the linked board image',
          'Auto-gating zero writes are source-level settings, not stop/drain or gate-disable proof',
          'No physical memory, secret/key material, eFuse or permanent settings accessed'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cc',default='cc')
    args=parser.parse_args()
    try:
        print(json.dumps(run(args),indent=2))
    except subprocess.CalledProcessError as error:
        if error.stderr:
            sys.stderr.write(error.stderr.decode() if isinstance(error.stderr,bytes) else error.stderr)
        raise
