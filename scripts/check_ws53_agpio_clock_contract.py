#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Bind AGPIO clock halfword/word views to the pinned WS53 SDK."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

PIN='163d9150ab94a02d09a05f623351f9facee3025e'
FILES=('src/application/ws53/ws53_application/main.c',
    'src/drivers/chips/ws53/include/acore/platform_core.h',
    'src/drivers/chips/ws53/include/clock_recover.h',
    'src/drivers/chips/ws53/clock_recover/clock_recover.c',
    'src/middleware/chips/ws53/pm/pm_porting.c')


def require(ok,message):
    if not ok:raise ValueError(message)


def run(a):
    inputs={}
    for name in FILES:
        data=subprocess.check_output(['git','-C',str(a.sdk),'show',f'{PIN}:{name}'])
        require((a.sdk/name).read_bytes()==data,'SDK file differs: '+name)
        inputs[name]=data.decode()
    main=inputs[FILES[0]]
    require('#define FPGA_AGPIO_CLK_CFG_REG_ADDR 0x57030024' in main
        and '#define FPGA_AGPIO_CLK_EN_BIT 0' in main
        and 'reg16_setbit(FPGA_AGPIO_CLK_CFG_REG_ADDR, FPGA_AGPIO_CLK_EN_BIT);' in main,
        'SDK application initialization changed')
    header=inputs[FILES[1]]
    require('HAL_GPIO_ULP_AON_PCLK_INT_EN_BIT        0' in header
        and 'HAL_GPIO_ULP_AON_PCLK_INT_CLK_SEL_BIT   1' in header,'SDK field positions changed')
    require('#define ULP_GPIO_CLK_CFG    0x57030024' in inputs[FILES[2]],'SDK clock address changed')
    for value in ('0x3','0x1'):
        require(f'writel(ULP_GPIO_CLK_CFG, {value});' in inputs[FILES[3]],'SDK sleep source write changed')
    require('writel(ULP_GPIO_CLK_CFG, 0x1);' in inputs[FILES[4]],'SDK wakeup source write changed')
    tree=ET.parse(a.svd)
    block=tree.find("./peripherals/peripheral[name='SYSTEM_MEMORY']")
    require(block is not None and int(block.findtext('baseAddress'),0)==0x57030000,'system bank changed')
    for name,width in (('GPIO_CLOCK',16),('GPIO_CLOCK_WORD',32)):
        reg=block.find(f"registers/register[name='{name}']")
        require(reg is not None and int(reg.findtext('addressOffset'),0)==0x24
            and int(reg.findtext('size'))==width and reg.findtext('access')=='read-write'
            and reg.find('resetValue') is None and not reg.findall('.//readAction'),'clock access/reset metadata differs')
        for field,bit in (('ENABLE',0),('SLOW_CLOCK_SELECT',1)):
            found=reg.find(f"fields/field[name='{field}']")
            require(found is not None and int(found.findtext('bitOffset'))==bit
                and found.findtext('bitWidth')=='1','clock field differs')
    root=Path(__file__).resolve().parents[1]
    paths=[Path(__file__),a.svd,root/'rdl/chips/ws53/system-memory.rdl',*(a.sdk/name for name in FILES)]
    result=dict(passed=True,sdk_commit=PIN,hardware_accessed=False,controlled_gate_silicon_verified=False,
        low_power_verified=False,reset_value_known=False,
        inputs_sha256={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(passed=True,views=2,fields=2)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('sdk','svd','output'):p.add_argument('--'+name,type=Path,required=True)
    run(p.parse_args())
