#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Bind selective SPI status read-clear metadata to pinned SDK documentation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

PIN='163d9150ab94a02d09a05f623351f9facee3025e'
SDK_DIR='src/drivers/drivers/hal/spi/v151_100/'
FACTS=(
    ('SPI0','hal_spi_v151_100_regs_def.h','spi_wsr_data_t','dcfe_tfee','COLLISION_OR_TX_ERROR',0),
    ('QSPI1','hal_qspi_v151_100_regs_def.h','sr_data_t','txe','TX_ERROR',5),
    ('QSPI1','hal_qspi_v151_100_regs_def.h','sr_data_t','dcol','COLLISION',6),
)


def require(ok,message):
    if not ok:raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(a):
    root=Path(__file__).resolve().parents[1]
    inputs={}
    for name in ('hal_spi_v151_100_regs_def.h','hal_qspi_v151_100_regs_def.h',
            'hal_spi_v151_100_regs_op.h','hal_qspi_v151_100_regs_op.h','hal_spi_v151_100.c'):
        path=a.sdk/SDK_DIR/name
        expected=subprocess.check_output(['git','-C',str(a.sdk),'show',f'{PIN}:{SDK_DIR+name}'])
        require(path.read_bytes()==expected,'SDK source changed: '+name)
        inputs[name]=expected.decode()
    tree=ET.parse(a.svd)
    facts=[]
    for block,header,union,member,field,bit in FACTS:
        text=inputs[header]
        body=re.search(r'typedef\s+union\s+'+re.escape(union[:-2])+r'\s*\{(.*?)\}\s*'+union+r'\s*;',text,re.S)
        require(body is not None,'SDK status union missing')
        comment=re.search(r'uint32_t\s+'+member+r'\s*:\s*1;\s*/\*!(.*?)\*/',body[1],re.S)
        require(comment is not None and 'cleared when read' in ' '.join(comment[1].split()),
            'SDK status read-clear documentation changed')
        if member=='txe':
            require('only when' in comment[1] and 'slave device' in comment[1], 'TXE slave-only qualification missing')
        register=tree.find(f"./peripherals/peripheral[name='{block}']/registers/register[name='STATUS']")
        found=register.find(f"fields/field[name='{field}']")
        require(register.findtext('access')=='read-only' and register.findtext('readAction')=='modify'
            and register.find('resetValue') is None,'selective status must not clear the whole register')
        require(found.findtext('readAction')=='clear' and int(found.findtext('bitOffset'))==bit
            and found.findtext('bitWidth')=='1' and found.findtext('access')=='read-only', 'status field metadata differs')
        for flag in ('BUSY','TX_EMPTY','TX_NOT_FULL','RX_FULL','RX_NOT_EMPTY'):
            require(register.findtext(f"fields/field[name='{flag}']/readAction") is None,
                'ordinary FIFO/live status must not gain a field read-clear effect')
        facts.append(dict(instance=block,register='STATUS',field=field,bit=bit,access='read-only',
            read_action='clear',source=f'{SDK_DIR+header}:{text[:text.index(comment[0])].count(chr(10))+1}',
            evidence_level='SDK header documentation; no silicon collision/slave-error validation'))
    # Accessors perform one volatile status word read; they do not read the
    # separate RAW_INTERRUPT or interrupt-clear register. This does NOT prove
    # the hardware's cross-register latch relationship.
    helpers=(('hal_spi_v151_100_regs_op.h','hal_spi0_v151_100_spi_wsr_get_dcfe_tfee','spi_wsr'),
        ('hal_qspi_v151_100_regs_op.h','hal_qspi_v151_100_sr_get_txe','sr'),
        ('hal_qspi_v151_100_regs_op.h','hal_qspi_v151_100_sr_get_dcol','sr'))
    for header,helper,member in helpers:
        body=re.search(r'\b'+helper+r'\([^)]*\)\s*\{(.*?)\n\}',inputs[header],re.S)
        require(body is not None and len(re.findall(r'->'+member+r'\s*;',body[1]))==1,
            'original status helper changed')
    for block in ('SPI0','QSPI1'):
        for name in ('RAW_INTERRUPT','INTERRUPT_STATUS'):
            register=tree.find(f"./peripherals/peripheral[name='{block}']/registers/register[name='{name}']")
            require(register.findtext('access')=='read-only' and not register.findall('.//readAction'),
                'raw/masked IRQ must not acquire an invented status-read acknowledgement')
    report=dict(passed=True,sdk_commit=PIN,facts=facts,volatile_status_helpers=len(helpers),
        hardware_accessed=False,read_clear_silicon_verified=False,irq_latch_relationship_proven=False,
        inputs_sha256={str(path.resolve()):digest(path) for path in (Path(__file__),a.svd,
            root/'rdl/chips/ws53/spi-v151-100.rdl',*(a.sdk/SDK_DIR/name for name in inputs))})
    a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(passed=True,status_fields=len(facts),volatile_helpers=len(helpers),silicon_verified=False)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk',type=Path,required=True)
    p.add_argument('--svd',type=Path,default=Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())
