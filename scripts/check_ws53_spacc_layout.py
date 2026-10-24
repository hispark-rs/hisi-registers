#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compile original WS53 SPACC headers and compare every modeled HASH view.

This is a source/layout contract, not a crypto algorithm or hardware test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from check_ws53_crypto_profile import PIN, PORT, REG, IRQ

ROOT = Path(__file__).resolve().parents[1]
HAL = 'src/drivers/drivers/hal/security_unified/hal_cipher/spacc/hal_hash.c'
SYMC = 'src/drivers/drivers/hal/security_unified/hal_cipher/spacc/hal_symc.c'

# Expression values come from the original header, not a second numeric map.
GLOBAL = {
    'INTERRUPT_ENABLE': ('SPACC_IE', 'read-write', None, {'REE': 'ie_ree', 'TEE': 'ie_tee', 'HPP': 'ie_hpp'}),
    'HASH_CLEAR_STATUS': ('SPACC_INT_RAW_HASH_CLEAR_FINISH', 'read-only', None, {'CHANNEL1': 'ch1', 'CHANNEL2': 'ch2'}),
    'HASH_CLEAR_ACK': ('SPACC_INT_RAW_HASH_CLEAR_FINISH', 'write-only', 'HASH_CLEAR_STATUS', {'CHANNEL1': 'ch1', 'CHANNEL2': 'ch2'}),
    'HASH_LOCK': ('SPACC_HASH_CHN_LOCK', 'read-write', None, {'CHANNEL1_OWNER': 'lock1', 'CHANNEL2_OWNER': 'lock2'}),
    'HASH_CLEAR_REQUEST': ('SPACC_HASH_CHN_CLEAR_REQ', 'write-only', None, {'CHANNEL1': 'ch1', 'CHANNEL2': 'ch2'}),
    'HASH_INTERRUPT_STATUS': ('HASH_CHANN_RAW_INT', 'read-only', None, {'CHANNEL1': 'ch1', 'CHANNEL2': 'ch2'}),
    'HASH_INTERRUPT_ACK': ('HASH_CHANN_RAW_INT', 'write-only', 'HASH_INTERRUPT_STATUS', {'CHANNEL1': 'ch1', 'CHANNEL2': 'ch2'}),
    'HASH_INTERRUPT_ENABLE': ('HASH_CHANN_RAW_INT_EN', 'read-write', None, {'CHANNEL1': 'ch1', 'CHANNEL2': 'ch2'}),
}
CHANNEL = {
    'CONTROL': ('IN_HASH_CHN_CTRL', 'read-write', None, {'SECURITY': 'ctrl_ss', 'ENABLE': 'ctrl_en'}),
    'KEY_CONTROL': ('IN_HASH_CHN_KEY_CTRL', 'read-write', None, {'KEYSLOT': 'key_id', 'HMAC': 'key_hmac', 'ALGORITHM': 'key_alg', 'MODE': 'key_mode'}),
    'NODE_BASE_HIGH': ('IN_HASH_CHN_NODE_START_ADDR_H', 'read-write', None, {'VALUE': 'word'}),
    'NODE_BASE_LOW': ('IN_HASH_CHN_NODE_START_ADDR_L', 'read-write', None, {'ADDRESS': 'word'}),
    'NODE_DEPTH': ('IN_HASH_CHN_NODE_LENGTH', 'read-write', None, {'VALUE': 'word'}),
    'WRITE_POINTER': ('IN_HASH_CHN_NODE_WR_POINT', 'read-only', None, {'POINTER': 'wr_ptr'}),
    'SUBMIT': ('IN_HASH_CHN_NODE_WR_POINT', 'write-only', 'WRITE_POINTER', {'VALUE': 'word'}),
    'READ_POINTER': ('IN_HASH_CHN_NODE_RD_POINT', 'read-only', None, {'POINTER': 'rd_ptr'}),
    'DATA_LENGTH': ('IN_HASH_CHN_DATA_LEN', 'read-only', None, {'VALUE': 'word'}),
}
STATE = {
    'DATA': ('CHANN_HASH_STATE_VAL', 'read-write', None, {'VALUE': 'word'}),
    'INDEX': ('CHANN_HASH_STATE_VAL_ADDR', 'write-only', None, {'VALUE': 'word'}),
}
ERROR = {
    'CAUSE': ('HASH_CALC_CTRL_CHECK_ERR', 'read-only', None, {
        'SECURITY': 'err_ss', 'ALGORITHM': 'err_alg', 'INVALID_ALGORITHM': 'err_invalid', 'SC': 'err_sc',
        'SM3_DISABLED': 'err_sm3', 'SHA1_DISABLED': 'err_sha1', 'TEE_ONLY': 'err_tee', 'AIDSP': 'err_aidsp'}),
    'STATUS': ('HASH_CALC_CTRL_CHECK_ERR_STATUS', 'read-only', None, {'CHANNEL': 'err_channel'}),
}
MASKS = {
    'ie_ree': ('spacc_ie', 'spacc_ie_ree', 1), 'ie_tee': ('spacc_ie', 'spacc_ie_tee', 1),
    'ie_hpp': ('spacc_ie', 'spacc_ie_hpp', 1),
    'ctrl_ss': ('in_hash_chn_ctrl', 'hash_chn_ss', 15), 'ctrl_en': ('in_hash_chn_ctrl', 'hash_chn_en', 1),
    'key_id': ('in_hash_chn_key_ctrl', 'hash_key_chn_id', 15), 'key_hmac': ('in_hash_chn_key_ctrl', 'hmac_vld', 1),
    'key_alg': ('in_hash_chn_key_ctrl', 'hash_chn_alg_sel', 15), 'key_mode': ('in_hash_chn_key_ctrl', 'hash_chn_alg_mode', 15),
    'wr_ptr': ('in_hash_chn_node_wr_point', 'hash_chn_node_wr_point', 255),
    'rd_ptr': ('in_hash_chn_node_rd_point', 'hash_chn_node_rd_point', 255),
    'err_ss': ('hash_calc_ctrl_check_err', 'hash_check_ss_error', 1),
    'err_alg': ('hash_calc_ctrl_check_err', 'hash_check_alg_error', 1),
    'err_invalid': ('hash_calc_ctrl_check_err', 'hash_check_alg_invld_error', 1),
    'err_sc': ('hash_calc_ctrl_check_err', 'hash_check_sc_error', 1),
    'err_sm3': ('hash_calc_ctrl_check_err', 'hash_check_sm3_disable_error', 1),
    'err_sha1': ('hash_calc_ctrl_check_err', 'hash_check_sha1_disable_error', 1),
    'err_tee': ('hash_calc_ctrl_check_err', 'hash_tonly_error', 1),
    'err_aidsp': ('hash_calc_ctrl_check_err', 'hash_aidsp_error', 1),
    'err_channel': ('hash_calc_ctrl_check_err_status', 'error_hash_chan_id', 15),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expanded_spec():
    result = dict(GLOBAL)
    for index in range(2):
        for prefix, specs in (('HASH_CHANNEL', CHANNEL), ('HASH_STATE', STATE)):
            for name, (expr, access, alias, fields) in specs.items():
                result[f'{prefix}[{index}].{name}'] = (f'{expr}({index + 1})', access, alias, fields)
        for name, (expr, access, alias, fields) in ERROR.items():
            result[f'HASH_ERROR[{index}].{name}'] = (f'{("TEE", "REE")[index]}_{expr}', access, alias, fields)
    return result


def source_probe(sdk):
    sdk = sdk.resolve()
    hashes = {}
    for rel in (PORT, REG, IRQ, HAL, SYMC):
        data = (sdk / rel).read_bytes()
        assert data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{rel}']), rel
        hashes[rel] = sha(sdk / rel)
    spec = expanded_spec()
    code = ['#include <stdio.h>\n#include "security_unified_porting.h"\n#include "hal_spacc_reg.h"\n#include "chip_core_irq.h"\nint main(void) {',
            r'printf("{\"base\":%u,\"irqs\":[%u,%u],\"offsets\":{",SPACC_REG_BASE_ADDR,SPACC_REE_IRQ_NUM,SEC_INT1_IRQN);']
    for index, (name, (expression, *_)) in enumerate(spec.items()):
        code.append('printf(' + json.dumps((',' if index else '') + json.dumps(name) + ':%u') + f',(unsigned)({expression}));')
    code.append(r'printf("},\"masks\":{\"word\":4294967295,\"ch1\":2,\"ch2\":4");')
    for name, (ctype, field, value) in MASKS.items():
        code.append(f'{{ {ctype} r={{0}}; r.bits.{field}={value}; printf(' + json.dumps(',' + json.dumps(name) + ':%u') + ',r.u32); }')
    for ch in (1, 2):
        code.append(f'{{ unsigned r=0; CHN_WHO_USED_SET(r,{ch},15); printf(' + json.dumps(f',"lock{ch}":%u') + ',r); }')
    code.append(r'puts("}}");return 0;}')
    with tempfile.TemporaryDirectory(prefix='ws53-spacc-layout-') as temp:
        temp = Path(temp)
        (temp / 'td_type.h').write_text('typedef unsigned int td_u32;\n')
        for rel in (PORT, REG, IRQ):
            (temp / Path(rel).name).write_bytes((sdk / rel).read_bytes())
        (temp / 'main.c').write_text('\n'.join(code))
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(temp),
                        str(temp / 'main.c'), '-o', str(temp / 'probe')], check=True)
        observed = json.loads(subprocess.check_output([str(temp / 'probe')], text=True))
    return observed, hashes


def validate_layout(svd, observed):
    spec = expanded_spec()
    tree = ET.parse(svd)
    block = tree.find("./peripherals/peripheral[name='SPACC']")
    assert block is not None and int(block.findtext('baseAddress'), 0) == observed['base'] == 0x52010000
    irqs = block.findall('interrupt')
    assert len(irqs) == 1 and irqs[0].findtext('name') == 'SEC_INT1'
    assert int(irqs[0].findtext('value')) == observed['irqs'][0] == observed['irqs'][1] == 59
    assert not tree.findall("./peripherals/peripheral[name='HASH']")
    actual = {r.findtext('name'): (r, int(r.findtext('addressOffset'), 0)) for r in block.findall('registers/register')}
    clusters = block.findall('registers/cluster')
    assert {c.findtext('name') for c in clusters} == {'HASH_CHANNEL[%s]', 'HASH_ERROR[%s]', 'HASH_STATE[%s]'}
    for cluster in clusters:
        assert int(cluster.findtext('dim')) == 2
        prefix = cluster.findtext('name')
        for index in range(2):
            base = int(cluster.findtext('addressOffset'), 0) + index * int(cluster.findtext('dimIncrement'), 0)
            for r in cluster.findall('register'):
                actual[(prefix % index) + '.' + r.findtext('name')] = (r, base + int(r.findtext('addressOffset'), 0))
    assert set(actual) == set(spec), (set(actual) - set(spec), set(spec) - set(actual))
    field_count = 0
    for name, (_, access, alias, fields) in spec.items():
        r, offset = actual[name]
        assert offset == observed['offsets'][name], name
        assert r.findtext('access') == access and r.findtext('size') == '32', name
        assert r.findtext('alternateRegister') == alias, name
        for tag in ('resetValue', 'resetMask', 'readAction', 'modifiedWriteValues', 'writeConstraint', 'enumeratedValues'):
            assert r.find('.//' + tag) is None, (name, tag)
        actual_fields = r.findall('fields/field')
        assert {f.findtext('name') for f in actual_fields} == set(fields), name
        for f in actual_fields:
            mask = ((1 << int(f.findtext('bitWidth'))) - 1) << int(f.findtext('bitOffset'))
            assert mask == observed['masks'][fields[f.findtext('name')]], (name, f.findtext('name'))
            assert f.findtext('access') == access
            field_count += 1
    return dict(expanded_register_views=len(actual), expanded_fields=field_count, clusters=3)


def run(args):
    assert not args.output.exists(), 'retain earlier evidence'
    observed, hashes = source_probe(args.sdk)
    layout = validate_layout(args.svd, observed)
    report = dict(passed=True, sdk_commit=PIN, input_sha256=hashes, observations=observed,
        svd_sha256=sha(args.svd), rdl_sha256=sha(ROOT / 'rdl/chips/ws53/spacc.rdl'),
        **layout,
        irq_name='SEC_INT1', irq_number=59, source_layout_only=True, hardware_accessed=False,
        unknown_reset_values_omitted=True, command_status_views_separate=True,
        limitations=['Shared ownership, clock/routing and DMA drain not established',
                     'No silicon behavior, algorithm results, keys or OTP accessed',
                     'This contract excludes unaudited SYMC/PKE/KM and error-clear paths'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=True, **layout, output=str(args.output))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--svd', type=Path, default=ROOT / 'generated/WS53.experimental.svd')
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())
