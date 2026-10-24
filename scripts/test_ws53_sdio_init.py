#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyelftools==0.32"]
# ///
"""Reject the old wrong Function0 bit and inconsistent SDIO access views."""
from copy import deepcopy
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from check_ws53_sdio_init import validate_svd as init
from check_ws53_sdio_clock import validate_svd as clock


class SdioContracts(unittest.TestCase):
    def test_valid(self):
        tree = ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
        init(tree)
        clock(tree)

    def test_wrong_field_width_access_and_alias_rejected(self):
        original = ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
        paths = [(init, 'SDIO', n) for n in ('MODE', 'VOLTAGE_CONTROL', 'CONFIG_30',
                  'FUNCTION0_INTERRUPT_ENABLE', 'FUNCTION1_CONFIG')]
        paths += [(clock, 'M_CRG', n) for n in ('M_CLKEN0', 'M_CLKEN0_WORD', 'M_CLKEN1', 'M_CLKEN1_WORD')]
        count = 0
        for validate, block, name in paths:
            path = f"./peripherals/peripheral[name='{block}']/registers/register[name='{name}']"
            reg = original.find(path)
            mutations = [('size', '8'), ('addressOffset', '0xff00'),
                         ('fields/field/bitOffset', '16'), ('fields/field/bitWidth', '32'),
                         ('fields/field/access', 'write-only')]
            if name.endswith('_WORD'):
                mutations += [('alternateRegister', 'wrong')]
            for target, value in mutations:
                tree = deepcopy(original)
                tree.find(path+'/'+target).text = value
                with self.assertRaises(AssertionError):
                    validate(tree)
                count += 1
            for target in (path, path+'/fields/field'):
                for tag in ('resetValue', 'resetMask', 'readAction', 'modifiedWriteValues'):
                    tree = deepcopy(original)
                    ET.SubElement(tree.find(target), tag).text = '0'
                    with self.assertRaises(AssertionError):
                        validate(tree)
                    count += 1
            tree = deepcopy(original)
            tree.find(path).set('derivedFrom', 'wrong')
            with self.assertRaises(AssertionError):
                validate(tree)
            count += 1
            tree = deepcopy(original)
            tree.find(f"./peripherals/peripheral[name='{block}']/registers").append(deepcopy(reg))
            with self.assertRaises(AssertionError):
                validate(tree)
            count += 1
        # This exact former RDL error must not recur even when fields are renamed.
        tree = deepcopy(original)
        path = "./peripherals/peripheral[name='SDIO']/registers/register[name='FUNCTION0_INTERRUPT_ENABLE']/fields/field[name='EVENT11']"
        field = tree.find(path)
        field.find('name').text = 'EVENT16'
        field.find('bitOffset').text = '16'
        with self.assertRaises(AssertionError):
            init(tree)
        count += 1
        self.assertEqual(count, 138)
        print(f'{count} incompatible SVD mutations rejected')


if __name__ == '__main__':
    unittest.main()
