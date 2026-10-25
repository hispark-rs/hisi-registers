#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compare KM RDL/SVD against pinned C headers on host and RV32IMFC ABI."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from check_ws53_crypto_profile import PIN, PORT, IRQ
from check_ws53_km_control import SLOT, KLAD, HAL

ROOT = Path(__file__).resolve().parents[1]
REGS = (HAL + 'hal_km/hal_keyslot_reg.h', HAL + 'hal_km/hal_klad_reg.h')
MASKS = {
    'address': ('hkl_key_addr', 'key_addr', 1023),
    'port': ('hkl_key_cfg', 'port_sel', 7),
    'engine': ('hkl_key_cfg', 'dsc_code', 255),
    'encrypt': ('hkl_key_cfg', 'key_enc', 1),
    'decrypt': ('hkl_key_cfg', 'key_dec', 1),
    'flash': ('hkl_key_cfg', 'kl_flash_sel', 3),
    'secure_key': ('hkl_key_sec_cfg', 'key_sec', 1),
    'source_nonsecure': ('hkl_key_sec_cfg', 'src_nsec', 1),
    'source_secure': ('hkl_key_sec_cfg', 'src_sec', 1),
    'destination_nonsecure': ('hkl_key_sec_cfg', 'dest_nsec', 1),
    'destination_secure': ('hkl_key_sec_cfg', 'dest_sec', 1),
    'same_master_only': ('hkl_key_sec_cfg', 'master_only', 1),
    'irq_common': ('kl_int_raw', 'com_kl_int_raw', 1),
    'irq_index': ('kl_int_raw', 'kl_int_num', 31),
    'irq_clear': ('kl_int_raw', 'clr_kl_int_raw', 1),
    'lock_busy': ('hkl_com_lock_info', 'kl_com_lock_busy', 3),
    'lock_fail': ('hkl_com_lock_info', 'kl_com_lock_fail', 3),
    'unlock_fail': ('hkl_com_lock_info', 'kl_com_unlock_fail', 3),
    'lock_index': ('hkl_com_lock_info', 'kl_com_lock_num', 7),
    'common_owner': ('hkl_com_lock_status', 'kl_com_lock_stat', 255),
    'clear_start': ('kl_clr_ctrl', 'kl_clr_start', 1),
    'key_size': ('kl_clr_ctrl', 'kl_clr_key_size', 3),
    'hmac_block': ('kl_clr_ctrl', 'kl_clr_key_cnt', 7),
    'flush_busy': ('kc_flush_busy', 'flush_busy', 1),
    'flush_fail': ('kc_flush_busy', 'flush_unlock_fail', 1),
    'flush_timeout': ('kc_flush_busy', 'flush_timeout_err', 1),
    'slot': ('kc_rd_slot_num', 'slot_num_cfg', 1023),
    'hmac': ('kc_rd_slot_num', 'slot_cfg_type', 1),
    'slot_owner': ('kc_rd_lock_status', 'rd_lock_status', 7),
    # Commands have whole-word WO views, but still check their source encodings.
    'command_slot': ('kc_cpu_lock_cmd', 'key_slot_num', 1023),
    'command_hmac': ('kc_cpu_lock_cmd', 'flush_hmac_kslot_ind', 1),
    'command_lock': ('kc_cpu_lock_cmd', 'lock_cmd', 1),
}
SPEC = {f'DATA_IN[{i}]': (f'KL_DATA_IN({i})', 'write-only', None, {'VALUE': 'word'}) for i in range(4)}
SPEC.update({
    'KEY_ADDRESS': ('KL_KEY_ADDR', 'read-write', None, {'ADDRESS': 'address'}),
    'KEY_CONFIG': ('KL_KEY_CFG', 'read-write', None, {'PORT': 'port', 'ENGINE': 'engine',
        'ENCRYPT': 'encrypt', 'DECRYPT': 'decrypt', 'FLASH_SELECTION': 'flash'}),
    'KEY_SECURITY': ('KL_KEY_SEC_CFG', 'read-write', None, {n.upper(): n for n in
        ('secure_key', 'source_nonsecure', 'source_secure', 'destination_nonsecure', 'destination_secure', 'same_master_only')}),
    'LOADER_ERROR': ('KL_ERROR', 'read-only', None, {'VALUE': 'word'}),
    'CONTROLLER_ERROR': ('KC_ERROR', 'read-only', None, {'VALUE': 'word'}),
    'INTERRUPT_STATUS': ('KL_INT_RAW', 'read-only', None, {'COMMON': 'irq_common', 'INDEX': 'irq_index', 'CLEAR_ROUTE': 'irq_clear'}),
    'INTERRUPT_ACK': ('KL_INT_RAW', 'write-only', 'INTERRUPT_STATUS', {'VALUE': 'word'}),
    'COMMON_LOCK': ('KL_LOCK_CTRL', 'write-only', None, {'VALUE': 'word'}),
    'COMMON_UNLOCK': ('KL_UNLOCK_CTRL', 'write-only', None, {'VALUE': 'word'}),
    'COMMON_LOCK_INFO': ('KL_COM_LOCK_INFO', 'read-only', None, {'BUSY': 'lock_busy', 'LOCK_FAILURE': 'lock_fail',
        'UNLOCK_FAILURE': 'unlock_fail', 'INDEX': 'lock_index'}),
    'COMMON_OWNER': ('KL_COM_LOCK_STATUS', 'read-only', None, {'OWNER': 'common_owner'}),
    'CLEAR_CONTROL': ('KL_CLR_CTRL', 'read-only', None, {'START': 'clear_start', 'KEY_SIZE': 'key_size', 'HMAC_BLOCK': 'hmac_block'}),
    'CLEAR_SUBMIT': ('KL_CLR_CTRL', 'write-only', 'CLEAR_CONTROL', {'VALUE': 'word'}),
    'SLOT_SELECT': ('KC_RD_SLOT_NUM', 'read-write', None, {'SLOT': 'slot', 'HMAC': 'hmac'}),
    'SLOT_OWNER': ('KC_RD_LOCK_STATUS', 'read-only', None, {'OWNER': 'slot_owner'}),
})
for cpu in ('TEE', 'REE'):
    SPEC.update({
        f'{cpu}_SLOT_READ': (f'KC_{cpu}CPU_LOCK_CMD', 'read-only', None, {'VALUE': 'word'}),
        f'{cpu}_SLOT_COMMAND': (f'KC_{cpu}CPU_LOCK_CMD', 'write-only', f'{cpu}_SLOT_READ', {'VALUE': 'word'}),
        f'{cpu}_FLUSH_STATUS': (f'KC_{cpu}CPU_FLUSH_BUSY', 'read-only', None,
            {'BUSY': 'flush_busy', 'UNLOCK_FAILURE': 'flush_fail', 'TIMEOUT_ERROR': 'flush_timeout'}),
    })


def source_probe(sdk, target_cc, objcopy):
    sources = {}
    for rel in (PORT, IRQ, SLOT, KLAD, *REGS):
        data = (sdk / rel).read_bytes()
        assert data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{rel}']), rel
        sources[rel] = data
    includes = '#include "security_unified_porting.h"\n#include "chip_core_irq.h"\n#include "hal_keyslot_reg.h"\n#include "hal_klad_reg.h"\n'
    code = ['#include <stdio.h>\n' + includes, 'int main(void) {',
        r'printf("{\"base\":%u,\"irqs\":[%u,%u],\"offsets\":{",KM_REG_BASE_ADDR,KLAD_REE_IRQ_NUM,SEC_INT3_IRQN);']
    for index, (name, (expression, *_)) in enumerate(SPEC.items()):
        code.append('printf(' + json.dumps((',' if index else '') + json.dumps(name) + ':%u') + f',(unsigned)({expression}));')
    code.append(r'printf("},\"masks\":{\"word\":4294967295");')
    for name, (ctype, field, value) in MASKS.items():
        code.append(f'{{{ctype} r={{0}};r.bits.{field}={value};printf(' + json.dumps(',' + json.dumps(name) + ':%u') + ',r.u32);}')
    code.append(r'printf("},\"union_sizes\":{");')
    types = tuple(dict.fromkeys(v[0] for v in MASKS.values()))
    for index, ctype in enumerate(types):
        code.append('printf(' + json.dumps((',' if index else '') + json.dumps(ctype) + ':%zu') + f',sizeof({ctype}));')
    code.append('puts("}}");return 0;}')
    with tempfile.TemporaryDirectory(prefix='ws53-km-layout-') as tmp:
        temp = Path(tmp)
        (temp / 'td_type.h').write_text('typedef unsigned int td_u32;\n')
        for rel in (PORT, IRQ, *REGS):
            (temp / Path(rel).name).write_bytes(sources[rel])
        (temp / 'main.c').write_text('\n'.join(code))
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(temp),
            str(temp / 'main.c'), '-o', str(temp / 'probe')], check=True)
        observed = json.loads(subprocess.check_output([str(temp / 'probe')], text=True))
        attr = '__attribute__((used,section(".ws53_km_layout")))'
        target = includes + f'{attr} const unsigned offsets[]={{' + ','.join(v[0] for v in SPEC.values()) + '};\n'
        expected = [observed['offsets'][name] for name in SPEC]
        for name, (ctype, field, value) in MASKS.items():
            size = observed['union_sizes'][ctype]
            assert size == (8 if ctype == 'kl_int_raw' else 4)
            target += f'_Static_assert(sizeof({ctype})=={size},"{ctype} ABI");\n'
            target += f'{attr} const {ctype} mask_{name}={{.bits.{field}={value}}};\n'
            expected += [observed['masks'][name]] + [0] * (size // 4 - 1)
        (temp / 'target.c').write_text(target)
        subprocess.run([target_cc, '-march=rv32imfc', '-mabi=ilp32f', '-ffreestanding', '-nostdinc',
            '-std=c11', '-fno-toplevel-reorder', '-Wall', '-Wextra', '-Werror', '-I', str(temp),
            '-c', str(temp / 'target.c'), '-o', str(temp / 'target.o')], check=True)
        subprocess.run([objcopy, '--dump-section', '.ws53_km_layout=' + str(temp / 'target.bin'), str(temp / 'target.o')], check=True)
        actual = list(struct.unpack('<' + str(len(expected)) + 'I', (temp / 'target.bin').read_bytes()))
        assert actual == expected
    observed['target_abi'] = dict(name='RV32IMFC ilp32f', offsets=len(SPEC), bitfield_masks=len(MASKS),
        matches_host=True, compiler=subprocess.check_output([target_cc, '--version'], text=True).splitlines()[0])
    return observed, {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}


def validate_layout(svd, observed):
    tree = ET.parse(svd)
    block = tree.find("./peripherals/peripheral[name='KM']")
    assert block is not None and int(block.findtext('baseAddress'), 0) == observed['base'] == 0x52022000
    for name in ('KLAD', 'KEYSLOT'):
        assert tree.find(f"./peripherals/peripheral[name='{name}']") is None
    irqs = block.findall('interrupt')
    assert len(irqs) == 1 and irqs[0].findtext('name') == 'SEC_INT3'
    assert int(irqs[0].findtext('value')) == observed['irqs'][0] == observed['irqs'][1] == 61
    assert not block.findall('registers/cluster')
    actual = {}
    arrays = 0
    for reg in block.findall('registers/register'):
        name, offset = reg.findtext('name'), int(reg.findtext('addressOffset'), 0)
        if reg.find('dim') is not None:
            assert name == 'DATA_IN[%s]' and reg.findtext('dim') == '4' and int(reg.findtext('dimIncrement'), 0) == 4
            arrays += 1
            for i in range(4):
                actual[name % i] = reg, offset + 4 * i
        else:
            assert name not in actual
            actual[name] = reg, offset
    assert arrays == 1 and set(actual) == set(SPEC)
    fields = 0
    for name, (_, access, alias, expected) in SPEC.items():
        reg, offset = actual[name]
        assert offset == observed['offsets'][name]
        assert reg.findtext('access') == access and reg.findtext('size') == '32'
        assert reg.findtext('alternateRegister') == alias
        for tag in ('resetValue', 'resetMask', 'readAction', 'modifiedWriteValues', 'writeConstraint', 'enumeratedValues'):
            assert reg.find('.//' + tag) is None
        fs = reg.findall('fields/field')
        assert {f.findtext('name') for f in fs} == set(expected)
        for f in fs:
            assert f.findtext('access') == access
            mask = ((1 << int(f.findtext('bitWidth'))) - 1) << int(f.findtext('bitOffset'))
            assert mask == observed['masks'][expected[f.findtext('name')]]
            fields += 1
    return dict(expanded_register_views=len(actual), expanded_fields=fields, register_arrays=arrays)


def run(args):
    assert not args.output.exists(), 'retain earlier evidence'
    observed, sources = source_probe(args.sdk, args.target_cc, args.target_objcopy)
    layout = validate_layout(args.svd, observed)
    report = dict(passed=True, sdk_commit=PIN, observations=observed, input_sha256=sources, **layout,
        svd_sha256=hashlib.sha256(args.svd.read_bytes()).hexdigest(),
        rdl_sha256=hashlib.sha256((ROOT / 'rdl/chips/ws53/km.rdl').read_bytes()).hexdigest(),
        hardware_accessed=False, source_layout_only=True, unknown_resets_omitted=True,
        command_status_views_separate=True, source_raw_interrupt_union_size=8,
        register_access_width=32, irq_number=61,
        limitations=['No shared-resource handoff, silicon IRQ or key erasure proof',
                    'No root derivation, permanent policy, PCPU/AIDSP commands or reset guesses'])
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=True, **layout, target_masks=len(MASKS))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--svd', type=Path, default=ROOT / 'generated/WS53.experimental.svd')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--target-cc', default='riscv64-linux-gnu-gcc')
    parser.add_argument('--target-objcopy', default='riscv64-linux-gnu-objcopy')
    run(parser.parse_args())
