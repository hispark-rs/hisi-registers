#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyelftools==0.32"]
# ///
"""Audit SDIO clock access width in the pinned SDK object; no hardware access."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
from elftools.elf.elffile import ELFFile
from check_ws53_sdio_patch import ARCHIVE, ARCHIVE_SHA, MEMBER, OBJECT_SHA, digest


def audit_object(raw):
    assert digest(raw) == OBJECT_SHA
    elf = ELFFile(BytesIO(raw))
    assert elf['e_machine'] == 'EM_RISCV' and elf.elfclass == 32 and elf.little_endian
    symbols = elf.get_section_by_name('.symtab').get_symbol_by_name('sdio_clock_set')
    assert len(symbols) == 1
    symbol = symbols[0]
    section = elf.get_section(symbol['st_shndx'])
    assert symbol['st_value'] == 0 and symbol['st_size'] == 32
    assert section.name == '.text.sdio_clock_set' and section['sh_size'] == 32
    # lui a5,0x52000; c.lw a4,64(a5); li a3,-17; andi a0,1;
    # andshf a4,a4,a3,ror,25; orshf a4,a4,a0,sll,11; c.sw a4,64(a5);
    # c.lw a4,68(a5); andi a4,-3; orshf a0,a4,a0,sll,1; c.sw a0,68(a5); ret.
    expected = [(4, 0x520007b7), (2, 0x43b8), (2, 0x56bd), (2, 0x8905),
                (4, 0xf2d7471b), (4, 0x16a7271b), (2, 0xc3b8), (2, 0x43f8),
                (2, 0x9b75), (4, 0x02a7251b), (2, 0xc3e8), (2, 0x8082)]
    code, offset = section.data(), 0
    for size, instruction in expected:
        assert int.from_bytes(code[offset:offset+size], 'little') == instruction, offset
        offset += size
    assert offset == len(code)
    assert not any(s['sh_type'] in ('SHT_REL', 'SHT_RELA') and s['sh_info'] == symbol['st_shndx']
                   for s in elf.iter_sections())
    mask = 0xffffffef  # -17 rotated right25 clears bit11, not bit16.
    assert ((mask >> 25) | (mask << 7)) & 0xffffffff == 0xfffff7ff
    return dict(function_sha256=digest(code), function_bytes=len(code), access_bits=32,
                accesses=[dict(address=0x52000040, bit=11), dict(address=0x52000044, bit=1)],
                operation='word read/modify/write in listed order; argument bit0 selects gate value',
                upstream_clock_or_bus_drain_verified=False)


def validate_svd(tree):
    block = tree.find("./peripherals/peripheral[name='M_CRG']")
    assert block is not None and int(block.findtext('baseAddress'), 0) == 0x52000000
    assert not block.findall('.//cluster') and not any('derivedFrom' in n.attrib for n in block.iter())
    for name, offset, field, bit in (('M_CLKEN0', 0x40, 'SDIO_DEVICE_ENABLE', 11),
                                     ('M_CLKEN1', 0x44, 'SDIO_AHB_ENABLE', 1)):
        views = []
        for suffix, width in (('', 16), ('_WORD', 32)):
            matches = block.findall(f"registers/register[name='{name+suffix}']")
            assert len(matches) == 1
            reg = matches[0]
            assert int(reg.findtext('addressOffset'), 0) == offset
            assert reg.findtext('size') == str(width) and reg.findtext('access') == 'read-write'
            assert not any(n.tag in ('resetValue', 'resetMask', 'readAction', 'modifiedWriteValues') for n in reg.iter())
            assert reg.findtext('alternateRegister') == (name if suffix else None)
            f = reg.findall(f"fields/field[name='{field}']")
            assert len(f) == 1 and f[0].findtext('bitOffset') == str(bit)
            assert f[0].findtext('bitWidth') == '1' and f[0].findtext('access') == 'read-write'
            views.append(reg)
        assert ET.tostring(views[0].find('fields')) == ET.tostring(views[1].find('fields'))


def run(args):
    assert not args.output.exists()
    archive = args.sdk/ARCHIVE
    assert digest(archive.read_bytes()) == ARCHIVE_SHA
    raw = subprocess.check_output([str(args.tool_prefix)+'ar', 'p', str(archive), MEMBER])
    clock = audit_object(raw)
    validate_svd(ET.parse(args.svd))
    report = dict(passed=True, evidence_class='pinned SDK object static instruction audit',
                  hardware_accessed=False, vendor_code_executed=False, clock=clock,
                  archive_sha256=ARCHIVE_SHA, object_sha256=OBJECT_SHA,
                  svd_sha256=digest(args.svd.read_bytes()), script_sha256=digest(Path(__file__).read_bytes()))
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('sdk', 'tool-prefix', 'svd', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    run(parser.parse_args())
