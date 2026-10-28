#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compile original WS53 ROM MDMA bitfields/helpers over host RAM, never hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN='163d9150ab94a02d09a05f623351f9facee3025e'
ROM='src/drivers/chips/ws53/rom/app/'
V=ROM+'drivers/drivers/hal/dma/romable/v151/'
PORT=ROM+'drivers/chips/ws53/porting/'
CONFIG='src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config'
# Explicit SVD -> original C member/type/field names (no duplicated bit offsets).
REGS={
 'INTERRUPT_STATUS':('int_st','dma_int_st_t','int_st int_trans_st int_err_st'),
 'INTERRUPT_CLEAR':('int_clr','dma_int_clr_t','int_trans_clr int_err_clr'),
 'RAW_INTERRUPT_STATUS':('ori_int','dma_ori_int_st_t','ori_int_trans_st ori_int_err_st'),
 'ENABLED_CHANNELS':('en_chns','dma_en_chns_t','en_chns'),
 'BURST_REQUEST':('burst_req','dma_burst_req_t','last_burst_req burst_req'),
 'SINGLE_REQUEST':('single_req','dma_single_req_t','last_single_req single_req'),
 'CONFIGURATION':('configuration','dma_config_t','dma_enable master1 master2'),
 'SYNC':('sync','dma_sync_t','damc_sync'),
}
CHANNEL={
 'LINKED_LIST':('lli','dma_lli_t','dmac_lm dmac_lli'),
 'DESTINATION':('dest',None,'address'),
 'CONFIGURATION':('cfg','dma_chn_config_t','ch_enable src_per dest_per fc_tt int_err_mask tc_int_mask lock active halt'),
 'SOURCE':('src',None,'address'),
 'CONTROL':('ctrl','dma_chn_control_t','transfersize sbsize dbsize swsize dwsize src_ms_sel dest_ms_sel src_inc dest_inc protection tc_int_en'),
}
HELPERS={
 'ch_cfg_set_ch_en':('CONFIGURATION','ENABLE'),
 'ctrl_set_tranf_size':('CONTROL','TRANSFER_SIZE'),
 'ctrl_set_dst_tr_width':('CONTROL','DESTINATION_WIDTH'),
 'ctrl_set_src_tr_width':('CONTROL','SOURCE_WIDTH'),
 'ctrl_set_dest_inc':('CONTROL','DESTINATION_INCREMENT'),
 'ctrl_set_src_inc':('CONTROL','SOURCE_INCREMENT'),
 'ctrl_set_src_bsize':('CONTROL','SOURCE_BURST'),
 'ctrl_set_dest_bsize':('CONTROL','DESTINATION_BURST'),
 'ctrl_set_dest_ms_sel':('CONTROL','DESTINATION_MASTER'),
 'ctrl_set_src_ms_sel':('CONTROL','SOURCE_MASTER'),
 'ctrl_set_tc_int_en':('CONTROL','TERMINAL_INTERRUPT'),
 'cfg_set_int_err_mask':('CONFIGURATION','ERROR_INTERRUPT_MASK'),
 'cfg_set_tc_int_mask':('CONFIGURATION','TRANSFER_INTERRUPT_MASK'),
 'cfg_set_fc_tt':('CONFIGURATION','FLOW_CONTROL'),
 'cfg_set_dest_per':('CONFIGURATION','DESTINATION_REQUEST'),
 'cfg_set_src_per':('CONFIGURATION','SOURCE_REQUEST'),
 'ctrl_set_protection':('CONTROL','PROTECTION'),
 'cfg_set_lock':('CONFIGURATION','LOCK'),
}
REQUESTS='I2C0_TX I2C0_RX I2C1_TX I2C1_RX QSPI0_2CS_TX QSPI0_2CS_RX SPI_M_TX SPI_M_RX UART_H0_TX UART_H0_RX UART_H1_TX UART_H1_RX I2S_TX I2S_RX PWM SPWM'.split()


def require(ok,why):
    if not ok: raise ValueError(why)


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--sdk',type=Path,required=True); parser.add_argument('--cc',default='cc')
    a=parser.parse_args(); sdk=a.sdk.resolve(); root=Path(__file__).resolve().parents[1]
    paths=[V+'hal_dmac_v151_regs_def.h',V+'hal_dmac_v151_regs_op.h',PORT+'include/dma_porting.h',
           PORT+'dma/dma_porting.c',ROM+'drivers/drivers/hal/dma/Kconfig',
           ROM+'drivers/drivers/hal/dma/hal_dma.c',CONFIG,
           'src/drivers/chips/ws53/include/acore/platform_core.h',
           'src/drivers/chips/ws53/include/acore/chip_core_irq.h',
           'src/kernel/liteos/liteos_v208.5.0/Huawei_LiteOS/arch/riscv/include/arch/cache.h',
           'src/kernel/osal/src/liteos/osal_cache.c']
    inputs={}
    for path in paths:
        data=(sdk/path).read_bytes()
        require(data==subprocess.check_output(['git','-C',str(sdk),'show',f'{PIN}:{path}']),f'SDK changed: {path}')
        inputs[path]=data
    cfg=inputs[CONFIG].decode()
    require('CONFIG_DRIVER_SUPPORT_DMA=y' in cfg and '# CONFIG_DMA_USING_V100 is not set' in cfg,'DMA selection')
    require('# CONFIG_DMA_SUPPORT_CLOCK is not set' in cfg,'clock ownership re-audit')
    port=inputs[PORT+'dma/dma_porting.c'].decode()
    require('g_dma_base_addr = (uintptr_t)DMA_BASE_ADDR' in port and 'hal_dmac_v151_funcs_get()' in port,'ROM controller binding')
    require('g_sdma_base_addr' not in port,'new SDMA binding')
    require('depends on DRIVER_SUPPORT_DMA && DMA_USING_V100' in inputs[paths[4]].decode(),'SDMA dependency')
    svdpath=root/'generated/WS53.experimental.svd'; svd=ET.parse(svdpath)
    p=svd.find("./peripherals/peripheral[name='MDMA']"); require(p is not None,'MDMA missing')
    require(svd.find("./peripherals/peripheral[name='SDMA']") is None,'unsupported SDMA exposed')
    cluster=p.find('registers/cluster')
    require(cluster.findtext('dim')=='8' and int(cluster.findtext('dimIncrement'),0)==0x20,'channel count/stride')
    require(int(cluster.findtext('addressOffset'),0)==0x100,'channel base')
    code=['#include <assert.h>','#include <stddef.h>','#include <stdint.h>','#include <stdio.h>','#include <string.h>',
          '#include "hal_dmac_v151_regs_op.h"',
          f'#include "{sdk}/src/drivers/chips/ws53/include/acore/chip_core_irq.h"',
          'static dma_v151_regs_t bank; uintptr_t g_dma_regs=(uintptr_t)&bank;',
          '_Static_assert(B_DMA_CHANNEL_MAX_NUM==8 && DMA_CHANNEL_MAX_NUM==8,"channel count");',
          '_Static_assert(sizeof(dma_chn_arg_t)==0x20,"stride");',
          '_Static_assert(offsetof(dma_v151_regs_t,ch_config)==0x100,"channel base");',
          f'_Static_assert(M_DMA_IRQN=={p.findtext("interrupt/value")},"IRQ");']
    platform=inputs['src/drivers/chips/ws53/include/acore/platform_core.h'].decode()
    base=int(re.search(r'^#define DMA_BASE_ADDR\s+(0x[0-9a-fA-F]+)',platform,re.M).group(1),0)
    require(base==int(p.findtext('baseAddress'),0),'MDMA address')
    fields={}; checks=[]; offsets=0
    for prefix, mapping, regs, typename in [('GLOBAL',REGS,p.findall('registers/register'),'dma_v151_regs_t'),
                                           ('CHANNEL',CHANNEL,cluster.findall('register'),'dma_chn_arg_t')]:
        require({r.findtext('name') for r in regs}==set(mapping),'register coverage')
        for r in regs:
            name=r.findtext('name'); member,ctype,names=mapping[name]; fs=r.findall('fields/field')
            require(r.findtext('size')=='32' and r.find('resetValue') is None,'width/unknown reset')
            offset=int(r.findtext('addressOffset'),0); offsets+=1
            code += [f'_Static_assert(offsetof({typename},{member})=={offset},"{prefix}.{name} offset");',
                     f'_Static_assert(sizeof((({typename}*)0)->{member})==4,"access width");']
            require(len(fs)==len(names.split()),f'{name} field coverage')
            fields[prefix+'.'+name]={}
            for f,cname in zip(fs,names.split(),strict=True):
                shift,width=int(f.findtext('bitOffset')),int(f.findtext('bitWidth')); mask=((1<<width)-1)<<shift
                fields[prefix+'.'+name][f.findtext('name')]={'c_field':cname,'mask':hex(mask),'shift':shift,'width':width}
                if ctype:
                    checks += [f'{{ {ctype} v={{.d32=0}}; v.b.{cname}={mask>>shift}u; assert(v.d32=={mask}u);',
                               f'v.d32={mask}u; assert(v.b.{cname}=={mask>>shift}u); }}']
                else: require(shift==0 and width==32,'address field')
                expected='read-only' if prefix=='GLOBAL' and name in ('INTERRUPT_STATUS','RAW_INTERRUPT_STATUS','ENABLED_CHANNELS') or (prefix,name,cname)==('CHANNEL','CONFIGURATION','active') else 'write-only' if prefix=='GLOBAL' and name=='INTERRUPT_CLEAR' else 'read-write'
                require(f.findtext('access')==expected,f'{name}.{cname} access')
                require(f.findtext('modifiedWriteValues')==('oneToClear' if prefix=='GLOBAL' and name=='INTERRUPT_CLEAR' else None),'clear action')
    svd_requests='I2C0_TX I2C0_RX I2C1_TX I2C1_RX QSPI1_TX QSPI1_RX SPI0_TX SPI0_RX UART1_TX UART1_RX UART0_TX UART0_RX I2S_TX I2S_RX PWM SPWM'.split()
    for field in ('SOURCE_REQUEST','DESTINATION_REQUEST'):
        reqs=cluster.find(f"register[name='CONFIGURATION']/fields/field[name='{field}']/enumeratedValues")
        actual={v.findtext('name'):int(v.findtext('value'),0) for v in reqs.findall('enumeratedValue')}
        require(actual==dict(zip(svd_requests,range(16),strict=True)),'request identities/encodings')
    for i,cname in enumerate(REQUESTS):
        code += [f'_Static_assert(HAL_DMA_HANDSHAKING_{cname}=={i},"request identity");']
    code += ['_Static_assert(HAL_DMA_HANDSHAKING_TIE0==16,"unsupported request boundary");']
    code += ['int main(void) { unsigned operations=0;']+checks
    code += ['const uint32_t seeds[]={0,0xa55a1234u,0xffffffffu};']
    for helper,(register,field) in HELPERS.items():
        f=fields['CHANNEL.'+register][field]; mask=int(f['mask'],0); width=f['width']; shift=f['shift']; member=CHANNEL[register][0]
        code += [f'''for(unsigned ch=0;ch<8;ch++) for(unsigned n=0;n<3;n++) for(unsigned val=0;val<{1<<width};val++) {{
            memset(&bank,0x5a,sizeof(bank)); bank.ch_config[ch].{member}.d32=seeds[n];
            dma_v151_regs_t expected=bank;
            expected.ch_config[ch].{member}.d32=(seeds[n]&~{mask}u)|(val<<{shift});
            hal_dma_{helper}((dma_channel_t)ch,val);
            assert(memcmp(&bank,&expected,sizeof(bank))==0); operations++;
        }}''']
    code += ['''for(unsigned n=0;n<3;n++) for(unsigned val=0;val<2;val++) {
        bank.configuration.d32=seeds[n]; hal_dma_cfg_set_en(val);
        assert(bank.configuration.d32==((seeds[n]&~1u)|val)); operations++;
    }
    for(unsigned ch=0;ch<8;ch++) for(unsigned val=0;val<4096;val++) {
        bank.ch_config[ch].ctrl.d32=0xa55a0000u|val;
        assert(hal_dma_ctrl_get_tranf_size((dma_channel_t)ch)==val); operations++;
    }
    /* Original bitfield helpers silently truncate: HAL must reject raw inputs. */
    hal_dma_ctrl_set_tranf_size(DMA_CHANNEL_0,4096); assert(bank.ch_config[0].ctrl.b.transfersize==0);
    hal_dma_cfg_set_src_per(DMA_CHANNEL_0,16); assert(bank.ch_config[0].cfg.b.src_per==0);
    hal_dma_cfg_set_dest_per(DMA_CHANNEL_0,31); assert(bank.ch_config[0].cfg.b.dest_per==15);
    printf("{\\"helper_operations\\":%u,\\"overflow_truncation_observed\\":true}\\n",operations);
    return 0; }''']
    with tempfile.TemporaryDirectory(prefix='ws53-mdma-contract-') as t:
        work=Path(t); (work/'common_def.h').write_text('#pragma once\n')
        (work/'errcode.h').write_text('#pragma once\ntypedef unsigned errcode_t;\n')
        (work/'check.c').write_text('\n'.join(code))
        subprocess.run([a.cc,'-std=c11','-O2','-Wall','-Werror','-I'+str(work),'-I'+str(sdk/V),
                        '-I'+str(sdk/(PORT+'include')),str(work/'check.c'),'-o',str(work/'check')],check=True)
        executed=json.loads(subprocess.check_output([str(work/'check')],text=True))
    report={'sdk_commit':PIN,'register_layouts':offsets,'field_checks':sum(map(len,fields.values())),
            'channels':8,'valid_requests':16,'original_helpers':len(HELPERS)+2,'original_c_execution':executed,
            'fields':fields,'input_sha256':{n:hashlib.sha256(v).hexdigest() for n,v in inputs.items()},
            'svd_sha256':hashlib.sha256(svdpath.read_bytes()).hexdigest(),
            'sdma_exposed':False,'hardware_accessed':False,
            'unknown':['physical transfer widths and bus masters','IRQ mask polarity and ACTIVE/drain timing',
                       'external clock ownership','cache attributes and maintenance completion',
                       'SDMA applicability/ownership; inactive V100 dependency is not a usable controller']}
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
