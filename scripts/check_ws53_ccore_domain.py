#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Pin SDK shared-domain release and reject unsafe SVD alias widening; no USB."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
HEADER = 'src/drivers/chips/ws53/include/acore/platform_core.h'
MAIN = 'src/application/ws53/ws53_application/main.c'
IO = 'src/middleware/utils/common_headers/chip_io.h'


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def validate(tree):
    block = tree.find("./peripherals/peripheral[name='SYSTEM_MEMORY']")
    need(block is not None and int(block.findtext('baseAddress'), 0) == 0x57030000, 'base')
    status = block.find("registers/register[name='CCORE_RESET_STATUS']")
    command = block.find("registers/register[name='CCORE_LOGIC_CLOCK_RELEASE_COMMAND']")
    need(status is not None and command is not None, 'split status/command required')
    aliases = [node.findtext('name') for node in block.findall('registers/register')
               if int(node.findtext('addressOffset'), 0) == 0x96c]
    need(sorted(aliases) == ['CCORE_LOGIC_CLOCK_RELEASE_COMMAND', 'CCORE_RESET_STATUS'],
         'only the two qualified views may occupy the slot')
    for node, access in ((status, 'read-only'), (command, 'write-only')):
        need(int(node.findtext('addressOffset'), 0) == 0x96c, 'word address')
        need(node.findtext('size') == '32' and node.findtext('access') == access, 'access width/direction')
        need(not any(n.tag in ('resetValue', 'resetMask', 'modifiedWriteValues', 'readAction')
                     or 'derivedFrom' in n.attrib for n in node.iter()), 'invented reset/side effects')
        fields = node.findall('fields/field')
        need(len(fields) == 1 and fields[0].findtext('bitOffset') == '0', 'single full payload')
        need(fields[0].findtext('access') == access, 'field access')
    need(status.findtext('fields/field/bitWidth') == '3', 'status layout')
    need(status.findtext('fields/field/name') == 'RELEASED_DOMAINS', 'status field identity')
    need(command.findtext('fields/field/bitWidth') == '32', 'SDK full-word write')
    need(command.findtext('fields/field/name') == 'LOGIC_CLOCK_RELEASE', 'command field identity')
    need(command.findtext('alternateRegister') == 'CCORE_RESET_STATUS', 'alias')
    limit = command.find('fields/field/writeConstraint/range')
    need(limit is not None and int(limit.findtext('minimum'), 0) == 3
         and int(limit.findtext('maximum'), 0) == 3, 'only logic/clock release permitted')


def run(args):
    sources = {}
    for name in (HEADER, MAIN, IO):
        raw = subprocess.check_output(['git', '-C', str(args.sdk), 'show', PIN+':'+name])
        need((args.sdk/name).read_bytes() == raw, 'SDK worktree source differs: '+name)
        sources[name] = dict(sha256=hashlib.sha256(raw).hexdigest(), text=raw.decode())
    header = sources[HEADER]['text']
    expected = {'HAL_SOFT_RST_2_GLB_B': 0x5703096c, 'RESET_CRG_CCORE_VALUE': 3,
                'RESET_CCORE_VALUE': 7}
    for name, value in expected.items():
        match = re.search(r'^#define\s+'+name+r'\s+\(?\s*(0x[0-9a-fA-F]+|[0-9]+)', header, re.M)
        need(match is not None and int(match[1], 0) == value, 'SDK definition '+name)
    main = re.sub(r'/\*.*?\*/|//[^\n]*', '', sources[MAIN]['text'], flags=re.S)
    need('#include "chip_io.h"' in main, 'application write macro include')
    io = re.sub(r'\s+', '', sources[IO]['text'])
    need('#ifndefUT_TEST#definewritel(addr,d)(*(volatileuint32_t*)(uintptr_t)(addr)=(uint32_t)(d))'
         in io, 'non-test SDK write is one volatile 32-bit store')
    need(re.search(r'writel\(HAL_SOFT_RST_2_GLB_B,\s*RESET_CRG_CCORE_VALUE\);\s*'
                   r'uapi_tcxo_delay_us\(5\);', main) is not None, 'SDK release and settling order')
    tree = ET.parse(args.svd).getroot()
    counts = validate_regressions(tree)
    return dict(passed=True, sdk_commit=PIN, hardware_accessed=False, **counts,
                sdk_sources={name: data['sha256'] for name, data in sources.items()},
                svd_sha256=hashlib.sha256(args.svd.read_bytes()).hexdigest(),
                control_cpu_released=False, reset_value_known=False,
                ownership_granted_by_pac=False, raw_pac_writes_constrained_at_runtime=False)


def validate_regressions(tree):
    """Exercise the generated boundary without requiring an external SDK checkout."""
    validate(tree)
    mutations = [('baseAddress', '0x57030004', 'base'),
                 ('addressOffset', '0x968', 'address'), ('access', 'read-write', 'RMW'),
                 ('size', '16', 'width'), ('fields/field/bitWidth', '3', 'partial word'),
                 ('fields/field/writeConstraint/range/maximum', '7', 'CPU release'),
                 ('fields/field/writeConstraint/range/minimum', '0', 'reset command'),
                 ('alternateRegister', 'BRAM_SHARE', 'wrong alias')]
    for path, value, label in mutations:
        changed = copy.deepcopy(tree)
        block = changed.find("./peripherals/peripheral[name='SYSTEM_MEMORY']")
        node = block if label == 'base' else block.find("registers/register[name='CCORE_LOGIC_CLOCK_RELEASE_COMMAND']")
        node.find(path).text = value
        try:
            validate(changed)
        except ValueError:
            continue
        raise AssertionError('accepted '+label)
    changed = copy.deepcopy(tree)
    node = changed.find("./peripherals/peripheral[name='SYSTEM_MEMORY']/registers/register[name='CCORE_LOGIC_CLOCK_RELEASE_COMMAND']")
    ET.SubElement(node, 'resetValue').text = '0'
    try:
        validate(changed)
    except ValueError:
        pass
    else:
        raise AssertionError('accepted invented reset')
    for label in ('extra writer', 'status RMW', 'field name'):
        changed = copy.deepcopy(tree)
        regs = changed.find("./peripherals/peripheral[name='SYSTEM_MEMORY']/registers")
        if label == 'extra writer':
            extra = copy.deepcopy(regs.find("register[name='CCORE_LOGIC_CLOCK_RELEASE_COMMAND']"))
            extra.find('name').text = 'UNQUALIFIED_COMMAND'
            regs.append(extra)
        elif label == 'status RMW':
            regs.find("register[name='CCORE_RESET_STATUS']/access").text = 'read-write'
        else:
            regs.find("register[name='CCORE_LOGIC_CLOCK_RELEASE_COMMAND']/fields/field/name").text = 'VALUE'
        try:
            validate(changed)
        except ValueError:
            continue
        raise AssertionError('accepted '+label)
    return dict(positive=1, negative=12)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--svd', type=Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2))
