#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run unmodified prepare-send ROM code against a complete ordinary-RAM window.

This proves source-level masking/result bytes, not notification delivery, self
clearing, CIS enumeration, transaction ordering on hardware or DMA drain.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zlib

ROM_START, ROM_END, ENTRY = 0x1e872, 0x1f1e4, 0x1ea12
ROM_SHA = 'a1ac5c03c613112f973b45bdfbfd399d3f3017b315b61d1f6b0d2a07d2ce4671'
HELPER_SHA = '15b30177dd26c888b8913e7aa730909e79a08a407f6145773e95e88fc697085f'
CODE, LEAF, REGS, DATA, DONE = 0x28000, 0x24000, 0x20030000, 0x20010000, 0x2005fff0
STRIDE = 0x310


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_svd(path):
    p = ET.parse(path).find("./peripherals/peripheral[name='SDIO']")
    assert p is not None and int(p.findtext('baseAddress'),0)==0x52060000
    assert not p.findall('.//cluster') and not any('derivedFrom' in n.attrib for n in p.iter())
    for name, offset, width, access in (
            ('FUNCTION1_SEND_LENGTH',0x120,21,'read-write'),
            ('FUNCTION1_SEND_STATUS',0x104,32,'read-only'),
            ('FUNCTION1_SEND_COMMAND',0x104,32,'write-only')):
        found=p.findall(f"registers/register[name='{name}']")
        assert len(found)==1
        r=found[0]; fields=r.findall('fields/field')
        assert int(r.findtext('addressOffset'),0)==offset and r.findtext('size')=='32'
        assert len(fields)==1 and fields[0].findtext('bitOffset')=='0'
        assert int(fields[0].findtext('bitWidth'))==width and fields[0].findtext('access')==access
        assert not any(n.tag in ('resetValue','resetMask','modifiedWriteValues','readAction') for n in r.iter())
    assert p.findtext("registers/register[name='FUNCTION1_SEND_COMMAND']/alternateRegister")=='FUNCTION1_SEND_STATUS'


def cases():
    lengths=sorted({0,1,2,3,4,511,512,65535,65536,0x4ffff,0x50000,0x1ffffe,
                    0x1fffff,0x200000,0x200001,0xffffffff,*[1<<n for n in range(32)]})
    return [dict(seed=s,length=n) for s in (0,0xffffffff,0xa55a5aa5,0x5aa5a55a) for n in lengths]


def verify(rows, raw):
    assert len(raw)==STRIDE*len(rows)
    for i,c in enumerate(rows):
        row=raw[i*STRIDE:(i+1)*STRIDE]
        assert row[:16]==struct.pack('<II',c['seed'],c['length'])+b'\xa5'*8
        expected=[c['seed']]*192
        expected[0x120//4]=(c['seed']&0xffe00000)|(c['length']&0x1fffff)
        expected[0x104//4]=c['seed']|1
        assert row[16:]==struct.pack('<192I',*expected),(i,c)


def run(a):
    assert not a.output.exists()
    assert sha(a.rom)==ROM_SHA and a.rom.stat().st_size==ROM_END-ROM_START
    validate_svd(a.svd)
    symbol=a.sdk/'src/drivers/chips/ws53/rom_config/acore/acore.sym'
    assert 'hal_sdio_v151_prepare_send_data = 0x01ea12;' in symbol.read_text()
    helper_path=a.qemu_scripts/'ws53_branch_immediate_test.py'
    assert sha(helper_path)==HELPER_SHA
    spec=importlib.util.spec_from_file_location('announce_executor',helper_path)
    helper=importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    li,ld,st,br=helper.li,helper.load,helper.store,helper.branch
    rows=cases(); results=bytearray()
    # Bound batches away from the ordinary-RAM register window.
    for start in range(0,len(rows),64):
        batch=rows[start:start+64]
        records=b''.join(struct.pack('<II',c['seed'],c['length'])+b'\xa5'*(STRIDE-8) for c in batch)
        code=li(2,0x2005f000)+li(8,DATA)+li(9,DATA+len(records))+li(18,LEAF+ENTRY-ROM_START)
        loop=len(code)*4
        code+=li(5,0x20508)+li(6,REGS)+[st(6,5,0)]+li(5,REGS)+[ld(6,8,0)]
        code+=[st(6,5,o) for o in range(0,0x300,4)]
        code+=[0x00000513,ld(11,8,4),0x000900e7]+li(5,REGS)
        for off in range(0,0x300,4): code += [ld(6,5,off),st(6,8,16+off)]
        code += [(STRIDE<<20)|(8<<15)|(8<<7)|0x13,br(8,9,6,loop-len(code)*4-4)]
        code += li(5,DONE)+[0x00100313,st(6,5,0),0x0000006f]
        assert len(code)*4<0x1000 and DATA+len(records)<REGS
        raw=helper.execute_ram(str(a.qemu.resolve()),
            ((CODE,struct.pack('<'+'I'*len(code),*code)),(LEAF,a.rom.read_bytes()),(DATA,records)),
            DATA,len(records),DONE)
        verify(batch,raw); results.extend(raw)
    verify(rows,results)
    a.output.mkdir(parents=True)
    observations=dict(cases=rows,stride=STRIDE,ram_zlib_base64=base64.b64encode(zlib.compress(results,9)).decode(),
        ram_sha256=hashlib.sha256(results).hexdigest(),hardware_accessed=False)
    (a.output/'observations.json').write_text(json.dumps(observations,indent=2)+'\n')
    report=dict(passed=True,cases=len(rows),register_words_per_case=192,entry=ENTRY,
        original_rom_unmodified=True,rom_sha256=ROM_SHA,symbol_sha256=sha(symbol),
        script_sha256=sha(Path(__file__)),helper_sha256=HELPER_SHA,qemu_sha256=sha(a.qemu),
        svd_sha256=sha(a.svd),observations_sha256=sha(a.output/'observations.json'),
        zero_length_accepted=True,overwide_length_truncated=True,send_length_mask=0x1fffff,
        send_status_written_as_readback_or_one=True,adma_table_written=False,
        register_surrogate='ordinary RAM',hardware_accessed=False,host_notification_verified=False,
        self_clear_verified=False,cis_or_host_protocol_verified=False,dma_drain_verified=False)
    (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('rom','sdk','qemu','qemu-scripts','svd','output'):
        parser.add_argument('--'+key,type=Path,required=True)
    run(parser.parse_args())
