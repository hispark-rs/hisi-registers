#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute original WS53 SDIO request/descriptor-tail code in ordinary RAM.

No device is opened. Register images and callback stubs only prove source-level
behavior: neither command acceptance by silicon nor ADMA completion/drain.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

ROM_START, ROM_END = 0x1e872, 0x1f1e4
ROM_SHA = 'a1ac5c03c613112f973b45bdfbfd399d3f3017b315b61d1f6b0d2a07d2ce4671'
HELPER_SHA = '15b30177dd26c888b8913e7aa730909e79a08a407f6145773e95e88fc697085f'
CODE, LEAF, STUB = 0x28000, 0x24000, 0x27000
DATA, REGS, TRACE, DONE = 0x20010000, 0x20030000, 0x20031000, 0x2005fff0
BASE_PTR, TABLE, INFO, CALLBACKS = 0x20508, 0x2050c, 0x20918, 0x20998
STRIDE, SEED = 2032, 0xa5a5a5a5
ENTRIES = {'write': 0x1ee4a, 'read': 0x1ee9e, 'complete': 0x1e9e2, 'pad': 0x1e9fa}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_svd(path):
    tree = ET.parse(path)
    sdio = tree.find("./peripherals/peripheral[name='SDIO']")
    assert sdio is not None and sdio.findtext('baseAddress') == '0x52060000'
    for name, offset, field, width, access in (
        ('ADMA_TABLE', 0x40, 'ADDRESS', 32, 'write-only'),
        ('FUNCTION1_SEND_LENGTH', 0x120, 'BYTES', 21, 'read-write'),
        ('FUNCTION1_BLOCK_SIZE', 0x258, 'BYTES', 12, 'read-only')):
        r = sdio.find(f"./registers/register[name='{name}']")
        assert r is not None and int(r.findtext('addressOffset'), 0) == offset
        assert r.findtext('size') == '32'
        f = r.find(f"./fields/field[name='{field}']")
        assert f is not None and f.findtext('bitOffset') == '0'
        assert f.findtext('bitWidth') == str(width) and f.findtext('access') == access
        assert not any(n.tag in ('resetValue', 'resetMask', 'modifiedWriteValues') for n in r.iter())


def records(cases):
    result = bytearray()
    for c in cases:
        row = bytearray(b'\xa5' * STRIDE)
        struct.pack_into('<8I', row, 0, c['entry'], *c['args'], c['callback'], c['present'], 0, 0)
        result.extend(row)
    return result


def execute(qemu, helper, image, cases):
    li, load, store, branch = helper.li, helper.load, helper.store, helper.branch
    code = li(2, 0x2005f000) + li(8, DATA) + li(9, DATA + STRIDE * len(cases))
    loop = CODE + 4 * len(code)
    code += li(5, BASE_PTR) + li(6, REGS) + [store(6, 5, 0)]
    code += li(6, SEED)
    for origin, size in ((REGS, 0x300), (TABLE, 1032)):
        code += li(5, origin) + [store(6, 5, off) for off in range(0, size, 4)]
    for origin, size in ((INFO, 128), (TRACE, 32), (CALLBACKS, 32)):
        code += li(5, origin) + [store(0, 5, off) for off in range(0, size, 4)]
    # Only the read/write start callbacks are installed. Their output remains
    # a caller-owned ordinary RAM table; the surrogate performs no DMA access.
    code += [load(6, 8, 20), branch(6, 0, 0, 20)]
    code += li(6, STUB) + [store(6, 5, 0), store(6, 5, 12)]
    code += li(5, TRACE) + [load(6, 8, 16), store(6, 5, 24)]
    code += [load(10, 8, 4), load(11, 8, 8), load(12, 8, 12), load(18, 8, 0)]
    code += [0x000900e7, store(10, 8, 1992)]
    for origin, dest, size in ((REGS, 32, 0x300), (INFO, 800, 128),
                                (TABLE, 928, 1032), (TRACE, 1960, 32)):
        code += li(5, origin)
        for off in range(0, size, 4):
            code += [load(6, 5, off), store(6, 8, dest + off)]
    code += [(STRIDE << 20) | (8 << 15) | (8 << 7) | 0x13]
    # Unrolled full-window snapshots exceed a short conditional branch range.
    code += li(5, loop) + [branch(8, 9, 7, 8), 0x00028067]
    code += li(5, DONE) + [0x00100313, store(6, 5, 0), 0x0000006f]
    assert len(code) * 4 < 0x8000 and DATA + STRIDE * len(cases) < REGS
    stub = li(5, TRACE) + [load(6, 5, 0), 0x00130313, store(6, 5, 0),
                           store(10, 5, 4), store(11, 5, 8)]
    stub += li(6, REGS) + [load(7, 6, 0x40), store(7, 5, 12),
                           load(7, 6, 0x120), store(7, 5, 16), load(10, 5, 24), 0x00008067]
    pack = lambda words: struct.pack('<' + 'I' * len(words), *words)
    regions = ((CODE, pack(code)), (LEAF, image), (STUB, pack(stub)), (DATA, records(cases)))
    raw = helper.execute_ram(str(qemu.resolve()), regions, DATA, STRIDE * len(cases), DONE)
    return [raw[i*STRIDE:(i+1)*STRIDE] for i in range(len(cases))]


def case(kind, args, callback=1, present=1):
    return dict(kind=kind, entry=LEAF + ENTRIES[kind] - ROM_START,
                args=args, callback=callback, present=present)


def scenarios():
    cases = []
    for kind in ('read', 'write'):
        # Handler direction and command direction/function are independent
        # inputs. Prove what ROM ignores, rather than calling ignored bits valid.
        for function in range(8):
            for direction in (0, 1):
                for address in (0, 1, 0x1ffff):
                    for increment in (0, 1):
                        argument = (direction << 31) | (function << 28) | (increment << 26) | (address << 9) | 1
                        cases.append(case(kind, [0, 512, argument]))
        for block_size in (0, 1, 512, 4095):
            for mode in (0, 1):
                for count in (0, 1, 511):
                    for present, callback in ((0, 0), (1, 0), (1, 1), (1, 0x80000001)):
                        argument = (int(kind == 'write') << 31) | (1 << 28) | (mode << 27) | count
                        cases.append(case(kind, [0, block_size, argument], callback, present))
    for kind in ('complete', 'pad'):
        for table in (0, TABLE):
            for index in (0, 1, 128, 129, 0xffffffff):
                for length in ((4,) if kind == 'complete' else (0, 1, 65535, 65536, 0xffffffff)):
                    cases.append(case(kind, [table, index, length], 0, 0))
    return cases


def check(c, row):
    assert row[:32] == records([c])[:32] and row[1996:] == b'\xa5' * (STRIDE - 1996)
    registers = bytearray(b'\xa5' * 0x300)
    info = bytearray(128)
    table = bytearray(b'\xa5' * 1032)
    trace = [0] * 8
    trace[6] = c['callback']
    kind = c['kind']
    detail = {}
    if kind in ('read', 'write'):
        _, block_size, argument = c['args']
        eligible = argument & 0x07fffe00 == 0
        count = argument & 0x1ff
        length = ((count * block_size) or 0xffffffff) if argument & (1 << 27) else (count or 512)
        if eligible and kind == 'read':
            struct.pack_into('<I', registers, 0x120, (SEED & 0xffe00000) | (length & 0x1fffff))
        if eligible and c['present']:
            trace[:5] = [1, length, TABLE, SEED, struct.unpack_from('<I', registers, 0x120)[0]]
            if c['callback']:
                struct.pack_into('<I', registers, 0x40, TABLE)
        if not eligible and kind == 'write':
            struct.pack_into('<H', info, 10, 1)
        detail = dict(callback_invoked=bool(trace[0]), callback_length=trace[1],
                      pointer_published=eligible and bool(c['present']) and bool(c['callback']),
                      send_length=struct.unpack_from('<I', registers, 0x120)[0])
    else:
        pointer, index, length = c['args']
        address = 0x20bb8 if kind == 'complete' else 0x209b8
        length = 4 if kind == 'complete' else length
        accepted = pointer != 0 and index < 129 and length != 0
        assert struct.unpack_from('<I', row, 1992)[0] == (0 if accepted else 0x80003251)
        if accepted:
            struct.pack_into('<HHI', table, index * 8, 0x21, length & 0xffff, address)
        detail = dict(accepted=accepted, flags=0x21 if accepted else None,
                      encoded_length=length & 0xffff if accepted else None,
                      address=address if accepted else None, end_bit_set=False)
    assert row[32:800] == registers, (c, 'register image differs')
    assert row[800:928] == info, (c, 'counter image differs')
    assert row[928:1960] == table, (c, 'descriptor table differs')
    assert row[1960:1992] == struct.pack('<8I', *trace), (c, 'callback trace differs')
    return c | detail | dict(observation_sha256=hashlib.sha256(row).hexdigest())


def run(args):
    assert not args.output.exists(), 'use a fresh output directory'
    image = args.rom.read_bytes()
    assert len(image) == ROM_END - ROM_START and sha(args.rom) == ROM_SHA
    path = args.qemu_scripts / 'ws53_branch_immediate_test.py'
    assert sha(path) == HELPER_SHA
    spec = importlib.util.spec_from_file_location('ws53_transfer_executor', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    validate_svd(args.svd)
    args.output.mkdir(parents=True)
    cases = scenarios()
    rows = []
    for start in range(0, len(cases), 48):
        batch = execute(args.qemu, helper, image, cases[start:start+48])
        for c, row in zip(cases[start:start+48], batch, strict=True):
            check(c, row)
        rows.extend(batch)
    observations = [check(c, row) for c, row in zip(cases, rows, strict=True)]
    (args.output / 'observations.json').write_text(json.dumps(observations, indent=2) + '\n')
    (args.output / 'ram-results.bin').write_bytes(b''.join(rows))
    result = dict(passed=True, cases=len(cases), counts={k: sum(c['kind'] == k for c in cases) for k in ENTRIES},
                  rom_sha256=ROM_SHA, qemu_sha256=sha(args.qemu), helper_sha256=HELPER_SHA,
                  script_sha256=sha(Path(__file__)), svd_sha256=sha(args.svd),
                  observations_sha256=sha(args.output / 'observations.json'),
                  ram_results_sha256=sha(args.output / 'ram-results.bin'),
                  original_rom_unmodified=True, register_surrogate='ordinary RAM', callback_stubbed=True,
                  hardware_accessed=False, host_protocol_verified=False, dma_transfer_or_drain_verified=False,
                  complete_send_sets_end_bit=False, complete_send_appends_ordinary_four_byte_transfer=True,
                  rom_start_handlers_check_function_or_direction=False,
                  zero_block_length_can_reach_callback_as_u32_max=True)
    (args.output / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('rom', 'qemu', 'qemu-scripts', 'output'):
        parser.add_argument('--' + option, type=Path, required=True)
    parser.add_argument('--svd', type=Path, default=Path(__file__).resolve().parents[1] / 'generated/WS53.experimental.svd')
    run(parser.parse_args())
