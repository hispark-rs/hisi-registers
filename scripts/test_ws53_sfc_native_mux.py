#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Mutation-check the native mux contract against actual compiler transcripts.

Does not compile or execute SDK code itself. Use --report for the JSON produced
by check_ws53_sfc_native_mux.py and --svd for the current WS53 output.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from check_ws53_sfc_native_mux import parse_records, require, validate_records, validate_svd


def run(reports, svd):
    accepted = []
    rejected = []
    for path in reports:
        report = json.loads(path.read_text())
        require(report['passed'] is True and report['hardware_access'] is False, 'wrong report class')
        for profile in report['profiles']:
            records = profile['access_log']
            require(records == parse_records(profile['stdout']), 'saved transcript differs from records')
            require(hashlib.sha256(profile['stdout'].encode()).hexdigest() == profile['stdout_sha256'], 'stdout hash')
            require(validate_records(records) == profile['statistics'], 'statistics changed')
            accepted.append(dict(compiler=report['compiler'], optional_ie_st=profile['optional_ie_st']))
    records = json.loads(reports[0].read_text())['profiles'][0]['access_log']
    indices = {kind: next(i for i, row in enumerate(records) if row['kind'] == kind)
               for kind in ('write', 'read', 'mode-mask')}
    indices['native'] = next(i for i, row in enumerate(records) if row['kind'] == 'rejected' and row['pin'] == 24)
    edits = [('write', 'width', 16), ('write', 'value', 0), ('write', 'address', 0x57036060),
             ('write', 'trial', 1), ('read', 'width', 16), ('read', 'address', 0x52008060),
             ('read', 'result', 1), ('read', 'call', 'fabricated-getter'),
             ('native', 'raw_error', 0), ('native', 'output_unchanged', False),
             ('native', 'mode_result', 1), ('native', 'accesses', 1),
             ('mode-mask', 'mask', 0x87), ('mode-mask', 'rejected_modes', list(range(7)))]
    mutations = []
    for kind, field, value in edits:
        rows = deepcopy(records)
        rows[indices[kind]][field] = value
        mutations.append((kind + '.' + field, rows))
    rows = deepcopy(records); rows[0], rows[1] = rows[1], rows[0]
    mutations += [('write-order', rows), ('missing-access', records[1:]),
                  ('duplicate-access', [*records, records[0]])]
    for name, rows in mutations:
        try:
            validate_records(rows)
        except ValueError:
            rejected.append(name)
        else:
            raise AssertionError('accepted changed source access: ' + name)
    validate_svd(svd)
    with tempfile.TemporaryDirectory(prefix='sfc-native-mux-svd-') as temporary:
        temp = Path(temporary)/'changed.svd'
        for offset, width, count, stride in ((0x60, 32, 1, 0), (0x5c, 64, 1, 0), (0x58, 32, 3, 4)):
            tree = ET.parse(svd)
            regs = tree.find("./peripherals/peripheral[name='PINMUX_MCU']/registers")
            assert regs is not None
            reg = ET.SubElement(regs, 'register')
            for tag, value in [('name', 'UNQUALIFIED'), ('addressOffset', hex(offset)), ('size', width),
                               ('dim', count), ('dimIncrement', stride)]:
                ET.SubElement(reg, tag).text = str(value)
            tree.write(temp)
            try:
                validate_svd(temp)
            except ValueError:
                rejected.append('added-native-view-' + hex(offset))
            else:
                raise AssertionError('accepted unqualified native mux SVD view')
        # This is a narrow flat-layout absence guard, not an SVD inheritance
        # resolver. Unsupported layouts must fail before reporting absence.
        def change_layout(name):
            tree = ET.parse(svd)
            block = tree.find("./peripherals/peripheral[name='PINMUX_MCU']")
            assert block is not None
            regs = block.find('registers')
            assert regs is not None
            reg = regs.find('register')
            assert reg is not None
            if name == 'nested-cluster-native-0x60':
                cluster = ET.SubElement(regs, 'cluster')
                ET.SubElement(cluster, 'name').text = 'NESTED'
                ET.SubElement(cluster, 'addressOffset').text = '0'
                native = ET.SubElement(cluster, 'register')
                for tag, value in [('name', 'NATIVE'), ('addressOffset', '0x60'), ('size', '32')]:
                    ET.SubElement(native, tag).text = value
            elif name.startswith('derived-'):
                selected = block if name == 'derived-peripheral' else reg
                selected.set('derivedFrom', 'UNSUPPORTED')
            elif name == 'missing-registers':
                block.remove(regs)
            elif name == 'empty-registers':
                regs.clear()
            elif name == 'unknown-container-child':
                ET.SubElement(regs, 'UNSUPPORTED')
            elif name == 'missing-width':
                reg.remove(reg.find('size'))
            elif name == 'duplicate-width':
                ET.SubElement(reg, 'size').text = '64'
            elif name.startswith('width-'):
                reg.find('size').text = name.removeprefix('width-')
            elif name == 'missing-offset':
                reg.remove(reg.find('addressOffset'))
            elif name == 'negative-offset':
                reg.find('addressOffset').text = '-1'
            elif name.startswith('dim-'):
                ET.SubElement(reg, 'dim').text = name.removeprefix('dim-')
                ET.SubElement(reg, 'dimIncrement').text = '4'
            elif name == 'missing-stride':
                ET.SubElement(reg, 'dim').text = '2'
            elif name == 'negative-stride':
                ET.SubElement(reg, 'dim').text = '2'
                ET.SubElement(reg, 'dimIncrement').text = '-4'
            else:
                raise AssertionError(name)
            return tree

        layouts = ['nested-cluster-native-0x60', 'derived-peripheral', 'derived-register',
                   'missing-registers', 'empty-registers', 'unknown-container-child',
                   'missing-width', 'duplicate-width', 'width-', 'width-0', 'width-7', 'width--8',
                   'missing-offset', 'negative-offset', 'dim-0', 'dim--1', 'dim-65537',
                   'missing-stride', 'negative-stride']
        for name in layouts:
            change_layout(name).write(temp)
            try:
                validate_svd(temp)
            except ValueError:
                rejected.append(name)
            else:
                raise AssertionError('accepted unsupported SVD layout: ' + name)
    return dict(passed=True, hardware_access=False, model_executed=False,
                scope='host oracle mutations; no new compiler or silicon execution',
                reports={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in reports},
                accepted_compiler_profiles=accepted, rejected_cases=rejected,
                svd_sha256=hashlib.sha256(svd.read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, action='append', required=True)
    parser.add_argument('--svd', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.report, args.svd), indent=2))


if __name__ == '__main__':
    main()
