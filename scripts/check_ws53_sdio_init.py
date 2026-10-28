#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute original SDIO base configuration against RAM, not a peripheral model."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

ROM_START, ENTRY, ROM_SHA = 0x1e872, 0x1ec66, 'a1ac5c03c613112f973b45bdfbfd399d3f3017b315b61d1f6b0d2a07d2ce4671'
HELPER_SHA = '15b30177dd26c888b8913e7aa730909e79a08a407f6145773e95e88fc697085f'
CODE, LEAF, REGS, RECORDS, DONE = 0x28000, 0x24000, 0x20030000, 0x20010000, 0x2005fff0
STRIDE = 0x310
TRANSFORMS = {
    0x04: (0, 0xb36343), 0x18: (0xffffffff, 7), 0x24: (0xffffffff, 0x7f),
    0x28: (0xffffffff, 0x10), 0x2c: (0, 0x2f92), 0x34: (0xffffffff, 0xfff),
    0x10c: (0xff000000, 0xff8000), 0x114: (0xffffffff, 0x3fff), 0x118: (0xffffc000, 7),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_svd(tree):
    p = tree.find("./peripherals/peripheral[name='SDIO']")
    assert p is not None and int(p.findtext('baseAddress'), 0) == 0x52060000
    assert not p.findall('.//cluster') and not any('derivedFrom' in n.attrib for n in p.iter())
    required = {
        'MODE': (0x24, [('INIT_PROFILE', 0, 7)]),
        'VOLTAGE_CONTROL': (0x28, [('SWITCH_REQUESTS', 0, 3), ('INIT_BIT', 4, 1)]),
        'CONFIG_30': (0x30, [('INIT_BIT', 0, 1)]),
        'FUNCTION0_INTERRUPT_ENABLE': (0x34, [('EVENTS', 0, 11), ('EVENT11', 11, 1)]),
        'FUNCTION1_CONFIG': (0x10c, [('INIT_PROFILE', 0, 24)]),
    }
    for name, (offset, fields) in required.items():
        matches = p.findall(f"registers/register[name='{name}']")
        assert len(matches) == 1
        reg = matches[0]
        assert int(reg.findtext('addressOffset'), 0) == offset and reg.findtext('size') == '32'
        assert not any(n.tag in ('resetValue', 'resetMask', 'readAction', 'modifiedWriteValues') for n in reg.iter())
        actual = [(f.findtext('name'), int(f.findtext('bitOffset')), int(f.findtext('bitWidth')))
                  for f in reg.findall('fields/field')]
        assert actual == fields, (name, actual)
        assert all(f.findtext('access') == 'read-write' for f in reg.findall('fields/field'))


def run(args):
    assert not args.output.exists()
    assert sha(args.rom) == ROM_SHA and len(args.rom.read_bytes()) == 0x1f1e4-ROM_START
    # Independently check the final Function0 OR operand. orshf a4,a4,a3,ror,25
    # with a3=16 produces bit11. The old hand-decoded bit16 was incorrect.
    rom = args.rom.read_bytes()
    assert rom[0x1ea76-ROM_START:0x1ea78-ROM_START] == bytes.fromhex('c146')
    assert rom[0x1ead2-ROM_START:0x1ead6-ROM_START] == bytes.fromhex('1b27d7f2')
    assert ((16 >> 25) | (16 << 7)) & 0xffffffff == 0x800
    validate_svd(ET.parse(args.svd))
    helper_path = args.qemu_scripts/'ws53_branch_immediate_test.py'
    assert sha(helper_path) == HELPER_SHA
    spec = importlib.util.spec_from_file_location('init_execution', helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    li, load, store, branch = helper.li, helper.load, helper.store, helper.branch
    seeds = [0, 0xffffffff, 0xa55a5aa5, 0x5aa5a55a]+[1 << bit for bit in range(32)]
    code = li(2, 0x2005f000)+li(8, RECORDS)+li(9, RECORDS+STRIDE*len(seeds))+li(18, LEAF+ENTRY-ROM_START)
    loop = len(code)*4
    code += li(5, 0x20508)+li(6, REGS)+[store(6, 5, 0)]
    code += li(5, REGS)+[load(6, 8, 0)]
    code += [store(6, 5, offset) for offset in range(0, 0x300, 4)]
    code += [0x00000513, 0x000900e7]  # original function, bus0
    code += li(5, REGS)
    for offset in range(0, 0x300, 4):
        code += [load(6, 5, offset), store(6, 8, 16+offset)]
    code += [(STRIDE << 20) | (8 << 15) | (8 << 7) | 0x13]
    code += [branch(8, 9, 6, loop-len(code)*4)]
    code += li(5, DONE)+[0x00100313, store(6, 5, 0), 0x0000006f]
    assert len(code)*4 < 0x1000
    records = b''.join(struct.pack('<I', seed)+b'\xa5'*(STRIDE-4) for seed in seeds)
    args.output.mkdir(parents=True)
    actual = helper.execute_ram(str(args.qemu.resolve()),
        ((CODE, struct.pack('<'+'I'*len(code), *code)), (LEAF, args.rom.read_bytes()), (RECORDS, records)),
        RECORDS, len(records), DONE)
    (args.output/'register-results.bin').write_bytes(actual)
    cases = []
    for index, seed in enumerate(seeds):
        row = actual[index*STRIDE:(index+1)*STRIDE]
        assert row[:16] == struct.pack('<I', seed)+b'\xa5'*12
        expected = [seed]*192
        for offset, (keep, value) in TRANSFORMS.items():
            expected[offset//4] = (seed & keep) | value
        observed = struct.unpack('<192I', row[16:])
        assert list(observed) == expected, dict(seed=hex(seed), differences=[
            (hex(i*4), hex(got), hex(want)) for i,(got,want) in enumerate(zip(observed,expected)) if got!=want])
        cases.append(dict(seed=seed, full_register_window_sha256=hashlib.sha256(row[16:]).hexdigest()))
    report = dict(passed=True, cases=cases, register_words_per_case=192,
        entry=ENTRY, original_rom_bytes_unmodified=True, rom_sha256=ROM_SHA,
        register_surrogate='ordinary RAM', hardware_accessed=False,
        hardware_access_order_verified=False, w1c_or_irq_semantics_verified=False,
        complete_initialization_verified=False, host_protocol_or_cis_verified=False,
        qemu_sha256=sha(args.qemu), script_sha256=sha(Path(__file__)), helper_sha256=HELPER_SHA,
        svd_sha256=sha(args.svd), function0_enable_mask=0xfff,
        observations_sha256=sha(args.output/'register-results.bin'),
        transforms={hex(offset): dict(keep_mask=hex(keep), or_value=hex(value)) for offset,(keep,value) in TRANSFORMS.items()})
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('rom', 'qemu', 'qemu-scripts', 'svd', 'output'):
        parser.add_argument('--'+option, type=Path, required=True)
    run(parser.parse_args())
