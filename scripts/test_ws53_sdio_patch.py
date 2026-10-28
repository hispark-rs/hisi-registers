#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyelftools==0.32"]
# ///
"""Reject unsafe/reset-width changes in the SDIO patch-derived SVD view."""
from copy import deepcopy
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from check_ws53_sdio_patch import validate_svd


class ResetView(unittest.TestCase):
    def test_valid(self):
        validate_svd(ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd'))

    def test_rejects_mutations(self):
        original = ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
        path = "./peripherals/peripheral[name='GLB_CTL']"
        reg = path+"/registers/register[name='SDIO_SOFT_RESET']"
        mutations = [(path+'/baseAddress', '0x52000000'), (reg+'/addressOffset', '0xb0'),
                     (reg+'/size', '16'), (reg+'/fields/field/name', 'VALUE'),
                     (reg+'/fields/field/bitOffset', '0'), (reg+'/fields/field/bitWidth', '32'),
                     (reg+'/fields/field/access', 'write-only')]
        count = 0
        for target, value in mutations:
            tree = deepcopy(original)
            tree.find(target).text = value
            with self.assertRaises(AssertionError):
                validate_svd(tree)
            count += 1
        for target in (path, reg, reg+'/fields/field'):
            tree = deepcopy(original)
            tree.find(target).set('derivedFrom', 'unverified')
            with self.assertRaises(AssertionError):
                validate_svd(tree)
            count += 1
        for target in (reg, reg+'/fields/field'):
            for name in ('resetValue', 'resetMask', 'readAction', 'modifiedWriteValues'):
                tree = deepcopy(original)
                ET.SubElement(tree.find(target), name).text = '0'
                with self.assertRaises(AssertionError):
                    validate_svd(tree)
                count += 1
        tree = deepcopy(original)
        tree.find(path+'/registers').append(deepcopy(tree.find(reg)))
        with self.assertRaises(AssertionError):
            validate_svd(tree)
        count += 1
        self.assertEqual(count, 19)
        print(f'{count} unsafe SVD mutations rejected')


if __name__ == '__main__':
    unittest.main()
