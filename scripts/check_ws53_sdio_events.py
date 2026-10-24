#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute retained WS53 SDIO IRQ code against a RAM register surrogate.

This establishes ROM callback/counter mapping, not hardware IRQ or W1C semantics.
No USB, debugger, SDIO host or real device is opened. Private ROM stays private.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

ROM_START, ROM_END = 0x1e872, 0x1f1e4
ROM_SHA = 'a1ac5c03c613112f973b45bdfbfd399d3f3017b315b61d1f6b0d2a07d2ce4671'
HEADER = 'src/drivers/chips/ws53/rom/app/drivers/drivers/hal/sdio/hal_sdio.h'
HEADER_SHA = '9f01eda40115173ba3cfa5b5097f74b859601943218fd38fab87f754f4b5ccea'
REFERENCE_SHA = '15b30177dd26c888b8913e7aa730909e79a08a407f6145773e95e88fc697085f'
EVENTS = {
    0: ('host_write_complete', 'fn1_wr_over'), 1: ('host_read_complete', 'fn1_rd_over'),
    2: ('host_read_error', 'fn1_rd_error'), 3: ('host_read_start', 'fn1_rd_start'),
    4: ('host_write_start', 'fn1_wr_start'), 5: ('adma_end', 'fn1_adma_end_int'),
    6: ('adma_interrupt', 'fn1_adma_int'), 7: ('adma_error', 'fn1_adma_err'),
    8: ('function_reset', 'fn1_rst'), 9: ('host_message', 'fn1_msg_rdy'),
    10: ('suspend', 'fn1_suspend'), 11: ('resume', 'fn1_resume'),
    12: ('device_message_ack', 'fn1_ack_to_arm_int_cnt'), 13: ('function_enable', 'fn1_en_int'),
}
CODE, LEAF, STUB, DATA, REGS, TRACE, DONE = 0x28000, 0x24000, 0x27000, 0x20010000, 0x20030000, 0x20031000, 0x2005fff0
INFO, CALLBACKS, BASE_PTR, STATE = 0x20918, 0x20998, 0x20508, 0x20914
STRIDE = 1056


def validate_svd(tree):
    sdio = tree.find("./peripherals/peripheral[name='SDIO']")
    assert sdio is not None and sdio.findtext('baseAddress') == '0x52060000'
    assert not sdio.findall('.//cluster') and not any('derivedFrom' in node.attrib for node in sdio.iter())
    regs = {reg.findtext('name'): reg for reg in sdio.findall('registers/register')}
    assert len(regs) == len(sdio.findall('registers/register'))
    for name, offset, access in (('FUNCTION1_INTERRUPT_STATUS', 0x110, 'read-only'),
                                 ('FUNCTION1_INTERRUPT_ENABLE', 0x114, 'read-write')):
        reg = regs[name]
        assert int(reg.findtext('addressOffset'), 0) == offset and reg.findtext('size') == '32'
        assert not any(node.tag in ('resetValue', 'resetMask', 'modifiedWriteValues', 'readAction') for node in reg.iter())
        fields = {f.findtext('name'): f for f in reg.findall('fields/field')}
        expected = {event.upper(): (bit, 1) for bit, (event, _) in EVENTS.items()}
        if name.endswith('STATUS'):
            expected['UNCLASSIFIED'] = (14, 18)
        assert set(fields) == set(expected) and len(fields) == len(reg.findall('fields/field'))
        for field, (bit, width) in expected.items():
            f = fields[field]
            assert f.findtext('bitOffset') == str(bit) and f.findtext('bitWidth') == str(width)
            assert f.findtext('access') == access
    ack = regs['FUNCTION1_INTERRUPT_ACK']
    assert ack.findtext('alternateRegister') == 'FUNCTION1_INTERRUPT_STATUS'
    assert ack.findtext('addressOffset') == '0x110' and ack.findtext('size') == '32'
    f = ack.find('fields/field')
    assert len(ack.findall('fields/field')) == 1 and f.findtext('name') == 'VALUE'
    assert f.findtext('bitOffset') == '0' and f.findtext('bitWidth') == '32' and f.findtext('access') == 'write-only'
    assert not any(node.tag in ('resetValue', 'resetMask', 'modifiedWriteValues', 'readAction') for node in ack.iter())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sdk_layout(sdk, output):
    path = sdk/HEADER
    assert sha(path) == HEADER_SHA, 'SDK header identity changed'
    header = path.read_text()
    fields = [field for _, field in EVENTS.values()]+['fn1_msg_isr']
    text = '#include <stdint.h>\n#include <stddef.h>\n#include <stdio.h>\n#define D2H_MSG_COUNT 32\n'
    for name in ('hal_sdio_status', 'hal_sdio_chan_info', 'hal_sdio_msg', 'hal_sdio_info'):
        found = re.findall(r'typedef struct '+name+r'\s*\{.*?\}\s*'+name+r'_t;', header, re.S)
        assert len(found) == 1, name
        text += found[0]+'\n'
    text += 'int main(void) {\n'
    for field in fields:
        text += f'printf("{field} %zu\\n", offsetof(hal_sdio_info_t, sdio_status.{field}));\n'
    text += 'printf("info_size %zu\\n", sizeof(hal_sdio_info_t));\n}\n'
    source = output/'sdk-layout.c'
    source.write_text(text)
    results = []
    for compiler in ('gcc', 'clang'):
        binary = output/('sdk-layout-'+compiler)
        subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)], check=True)
        actual = subprocess.check_output([str(binary.resolve())], text=True)
        (output/(compiler+'-layout.txt')).write_text(actual)
        results.append({key: int(value) for key, value in (line.split() for line in actual.splitlines())})
    assert results[0] == results[1] and results[0]['info_size'] == 128
    return results[0]


def execute(qemu, helper, image, cases, entry):
    li, load, store, branch = helper.li, helper.load, helper.store, helper.branch
    code = li(2, 0x2005f000)+li(8, DATA)+li(9, DATA+STRIDE*len(cases))
    code += li(18, LEAF+entry-ROM_START)
    loop = len(code)*4
    # Restore independent initial state for each case; the whole retained ROM
    # is relocated away from its absolute software data at 0x20508/0x20918.
    code += li(5, BASE_PTR)+li(6, REGS)+[store(6, 5, 0)]
    code += li(5, INFO)
    code += [store(0, 5, off) for off in range(0, 128, 4)]
    code += li(5, STATE)+[store(0, 5, 0)]
    code += li(5, REGS)
    code += [store(0, 5, off) for off in range(0, 0x300, 4)]
    code += [load(6, 8, 0), store(6, 5, 0x110)]
    for off, value in ((0x120, 0xa5e00000), (0x128, 0x12345678), (0x258, 32)):
        code += li(6, value)+[store(6, 5, off)]
    code += li(5, TRACE)+[store(0, 5, off) for off in range(0, 128, 4)]
    code += li(5, CALLBACKS)
    for index in range(8):
        code += li(6, STUB+index*64)+[store(6, 5, index*4)]
    code += [0x00000513, 0x000900e7]  # a0=bus0; call retained function
    for origin, target, size in ((INFO, 16, 128), (STATE, 144, 4), (REGS, 160, 0x300), (TRACE, 928, 128)):
        code += li(5, origin)
        for off in range(0, size, 4):
            code += [load(6, 5, off), store(6, 8, target+off)]
    # Record the pointer after deinit separately; untouched record words are guards.
    code += li(5, BASE_PTR)+[load(6, 5, 0), store(6, 8, 4)]
    code += [(STRIDE << 20) | (8 << 15) | (8 << 7) | 0x13]
    code += [branch(8, 9, 6, loop-len(code)*4)]
    code += li(5, DONE)+[0x00100313, store(6, 5, 0), 0x0000006f]
    assert len(code)*4 < 0x1000 and -4096 < loop-(len(code)-6)*4
    stubs = bytearray()
    for index in range(8):
        words = li(5, TRACE+index*16)+[load(6, 5, 0), 0x00130313, store(6, 5, 0),
                store(10, 5, 4), store(11, 5, 8), 0x00000513, 0x00008067]
        stubs += struct.pack('<'+'I'*len(words), *words).ljust(64, b'\0')
    records = bytearray()
    for status in cases:
        record = bytearray(b'\xa5'*STRIDE)
        struct.pack_into('<I', record, 0, status)
        records += record
    regions = ((CODE, struct.pack('<'+'I'*len(code), *code)), (LEAF, image), (STUB, stubs), (DATA, records))
    actual = helper.execute_ram(str(qemu.resolve()), regions, DATA, len(records), DONE)
    return [actual[i*STRIDE:(i+1)*STRIDE] for i in range(len(cases))]


def run(args):
    assert not args.output.exists(), 'use a new output directory'
    args.output.mkdir(parents=True)
    image = args.rom.read_bytes()
    assert len(image) == ROM_END-ROM_START and sha(args.rom) == ROM_SHA
    reference = args.qemu_scripts/'ws53_branch_immediate_test.py'
    assert sha(reference) == REFERENCE_SHA
    spec = importlib.util.spec_from_file_location('ws53_execution', reference)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    layout = sdk_layout(args.sdk, args.output)
    validate_svd(ET.parse(args.svd))
    cases = [0]+[1 << bit for bit in range(32)]+[0x3fff, 0xffffffff, 0x1555, 0x2aaa]
    rows = execute(args.qemu, helper, image, cases, 0x1efb4)
    (args.output/'ram-surrogate-results.bin').write_bytes(b''.join(rows))
    observed = []
    for status, row in zip(cases, rows, strict=True):
        assert len(row) == STRIDE and struct.unpack_from('<II', row) == (status, REGS)
        assert row[8:16] == b'\xa5'*8 and row[148:160] == b'\xa5'*12
        info = bytearray(128)
        for bit, (_, field) in EVENTS.items():
            if status & (1 << bit):
                struct.pack_into('<H', info, layout[field], 1)
        if status & (1 << 9):
            struct.pack_into('<H', info, layout['fn1_msg_isr'], 1)
        assert row[16:144] == info, f'counter mapping {status:#x}: actual {row[16:144].hex()} expected {info.hex()}'
        assert struct.unpack_from('<I', row, 144)[0] == (5 if status & (1 << 13) else 0)
        registers = bytearray(0x300)
        for off, value in ((0x110, status), (0x11c, int(bool(status & (1 << 13)))),
                           (0x120, 0xa5e00200 if status & 8 else 0xa5e00000),
                           (0x128, 0x12345678), (0x258, 32)):
            struct.pack_into('<I', registers, off, value)
        assert row[160:928] == registers, f'RAM surrogate register changes {status:#x}'
        callbacks = {}
        for index, bit in enumerate((3, 1, 2, 4, 0, 9, -1, 12)):
            count, a0, a1, guard = struct.unpack_from('<IIII', row, 928+16*index)
            assert count == int(bit >= 0 and bool(status & (1 << bit))) and guard == 0
            if count and index in (0, 3):
                assert (a0, a1) == (512, 0x2050c)
            if count and index == 5:
                assert a0 == 0x12345678
            callbacks[index] = dict(count=count, a0=a0, a1=a1)
        observed.append(dict(status=status, counters=info.hex(), callbacks=callbacks,
                             state=struct.unpack_from('<I', row, 144)[0], registers=registers.hex()))
    recovery = {}
    for name, entry in (('deinit', 0x1e9f8), ('soft_reset', 0x1f1c8)):
        row = execute(args.qemu, helper, image, [0x3fff], entry)[0]
        expected_pointer = 0 if name == 'deinit' else REGS
        assert struct.unpack_from('<II', row) == (0x3fff, expected_pointer)
        expected = bytearray(0x300)
        for off, value in ((0x110, 0x3fff), (0x120, 0xa5e00000), (0x128, 0x12345678), (0x258, 32)):
            struct.pack_into('<I', expected, off, value)
        assert row[16:148] == bytes(132) and row[160:928] == expected and row[928:] == bytes(128)
        recovery[name] = dict(base_pointer_cleared=expected_pointer == 0, surrogate_registers_unchanged=True,
                              info_and_callbacks_unchanged=True, bus_drain_proven=False)
    (args.output/'observations.json').write_text(json.dumps(observed, indent=2)+'\n')
    report = dict(passed=True, irq_cases=len(cases), recovery_calls=2, events={bit: dict(name=name, counter=field,
                  counter_offset=layout[field]) for bit, (name, field) in EVENTS.items()}, recovery=recovery,
                  rom_sha256=ROM_SHA, sdk_header_sha256=HEADER_SHA, qemu_sha256=sha(args.qemu),
                  script_sha256=sha(Path(__file__)), helper_sha256=REFERENCE_SHA,
                  observations_sha256=sha(args.output/'observations.json'),
                  svd_sha256=sha(args.svd), source_layout_validated=True,
                  original_rom_bytes_unmodified=True, register_surrogate='ordinary RAM; no W1C emulation',
                  hardware_accessed=False, hardware_irq_verified=False, dma_transfer_or_drain_verified=False)
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('sdk', 'rom', 'qemu', 'qemu-scripts', 'output'):
        parser.add_argument('--'+option, type=Path, required=True)
    parser.add_argument('--svd', type=Path, default=Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    run(parser.parse_args())
