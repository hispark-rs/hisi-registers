#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyelftools==0.32"]
# ///
"""Audit the actual SDK SDIO reset patch; never execute vendor code or access USB."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
from elftools.elf.elffile import ELFFile

ARCHIVE = 'src/drivers/chips/ws53/porting/patch/ws53_liteos_app/libplat_patch.a'
ARCHIVE_SHA = 'c519a5330f1327071858324623f02ab8b751679c3238ea63053a3b260d9d91ca'
OBJECT_SHA = '4c7337bc5bbafa21d2666315c27431e9e5dc948c89bc34aca862a7da0f9a1671'
CONFIG = 'src/build/config/target_config/ws53/patch_config/acore.cfg'
MEMBER = 'sdio_patch.c.obj'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def audit_object(raw):
    assert digest(raw) == OBJECT_SHA, 'SDK SDIO member changed'
    elf = ELFFile(BytesIO(raw))
    assert elf['e_machine'] == 'EM_RISCV' and elf.elfclass == 32 and elf.little_endian
    symbols = elf.get_section_by_name('.symtab')
    found = symbols.get_symbol_by_name('sdio_port_soft_reset_patch')
    assert len(found) == 1
    symbol = found[0]
    assert symbol['st_value'] == 0 and symbol['st_size'] == 40
    section = elf.get_section(symbol['st_shndx'])
    assert section.name == '.patch.text.on.sram' and section['sh_size'] == 40
    code = section.data()
    # Decode this straight-line function's exact instructions, not objdump text.
    # push; lui s0,0x57000; lw a5,0xc8(s0); li a0,5; andi a5,-3;
    # sw a5,0xc8(s0); call delay; lw a5,0xc8(s0); ori a5,2; sw; popret.
    expected = [(2, 0x8028), (4, 0x57000437), (4, 0x0c842783),
                (2, 0x4515), (2, 0x9bf5), (4, 0x0cf42423),
                (4, 0x00000097), (4, 0x000080e7), (4, 0x0c842783),
                (4, 0x0027e793), (4, 0x0cf42423), (2, 0x8024)]
    offset = 0
    for size, value in expected:
        assert int.from_bytes(code[offset:offset+size], 'little') == value, hex(offset)
        offset += size
    assert offset == len(code)
    relocations = []
    for relsec in elf.iter_sections():
        if relsec['sh_type'] != 'SHT_RELA' or relsec['sh_info'] != symbol['st_shndx']:
            continue
        for relocation in relsec.iter_relocations():
            relocations.append((relocation['r_offset'], relocation['r_info_type'],
                                symbols.get_symbol(relocation['r_info_sym']).name, relocation['r_addend']))
    # R_RISCV_CALL=18, RELAX=51. No other call or relocation exists in this entry.
    assert relocations == [(0x12, 18, 'uapi_tcxo_delay_us', 0), (0x12, 51, '', 0)]
    return dict(function_sha256=digest(code), function_bytes=len(code), address=0x570000c8,
                access_bits=32, field_bit=1, operation='read; clear bit1; write; delay_us(5); read; set bit1; write',
                call_relocations=relocations, status_poll_or_completion_check=False)


def validate_svd(tree):
    peripheral = tree.find("./peripherals/peripheral[name='GLB_CTL']")
    assert peripheral is not None and int(peripheral.findtext('baseAddress'), 0) == 0x57000000
    assert not peripheral.findall('.//cluster') and not any('derivedFrom' in n.attrib for n in peripheral.iter())
    matches = peripheral.findall("registers/register[name='SDIO_SOFT_RESET']")
    assert len(matches) == 1
    register = matches[0]
    assert int(register.findtext('addressOffset'), 0) == 0xc8
    assert register.findtext('size') == '32'
    assert not any(n.tag in ('resetValue', 'resetMask', 'readAction', 'modifiedWriteValues') or
                   'derivedFrom' in n.attrib for n in register.iter())
    fields = register.findall('fields/field')
    assert len(fields) == 1
    field = fields[0]
    assert field.findtext('name') == 'SDIO_RESET_N'
    assert field.findtext('bitOffset') == '1' and field.findtext('bitWidth') == '1'
    assert field.findtext('access') == 'read-write'


def run(args):
    archive = args.sdk/ARCHIVE
    assert digest(archive.read_bytes()) == ARCHIVE_SHA
    config = args.sdk/CONFIG
    assert [line.split() for line in config.read_text().splitlines()
            if line.strip().startswith('sdio_port_soft_reset ')] == [
                ['sdio_port_soft_reset', 'sdio_port_soft_reset_patch']]
    assert not (config.parent/'ws53_liteos_app.cfg').exists()
    raw = subprocess.check_output([str(args.tool_prefix)+'ar', 'p', str(archive), MEMBER])
    reset = audit_object(raw)
    validate_svd(ET.parse(args.svd))
    report = dict(passed=True, evidence_class='pinned SDK relocatable object and patch config; static audit',
                  hardware_accessed=False, vendor_code_executed=False, reset_waveform_verified=False,
                  dma_drain_proven=False, installed_patch_table_verified=False,
                  archive_sha256=ARCHIVE_SHA, object_sha256=OBJECT_SHA,
                  config_sha256=digest(config.read_bytes()), svd_sha256=digest(args.svd.read_bytes()),
                  script_sha256=digest(Path(__file__).read_bytes()), reset=reset)
    assert not args.output.exists()
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('sdk', 'tool-prefix', 'svd', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    run(parser.parse_args())
