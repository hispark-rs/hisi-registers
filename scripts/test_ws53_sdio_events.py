#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fail-closed SVD oracle checks; no vendor binaries, QEMU or hardware."""
from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET
from check_ws53_sdio_events import validate_svd, EVENTS

tree = ET.parse(Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
validate_svd(tree)
count = 0


def reject(change):
    global count
    candidate = deepcopy(tree)
    sdio = candidate.find("./peripherals/peripheral[name='SDIO']")
    change(sdio)
    try:
        validate_svd(candidate)
    except (AssertionError, KeyError, ValueError, AttributeError):
        count += 1
    else:
        raise AssertionError('corrupted SDIO layout accepted')


for reg in ('FUNCTION1_INTERRUPT_STATUS', 'FUNCTION1_INTERRUPT_ENABLE'):
    prefix = f"registers/register[name='{reg}']"
    for name, _ in EVENTS.values():
        path = prefix+f"/fields/field[name='{name.upper()}']"
        for key, value in (('bitOffset', '31'), ('bitWidth', '2'), ('access', 'write-only')):
            reject(lambda p, path=path, key=key, value=value: setattr(p.find(path+'/'+key), 'text', value))
    for key in ('resetValue', 'resetMask', 'readAction', 'modifiedWriteValues'):
        reject(lambda p, prefix=prefix, key=key: ET.SubElement(p.find(prefix), key))
    reject(lambda p, prefix=prefix: p.find(prefix).set('derivedFrom', 'OTHER'))
    reject(lambda p, prefix=prefix: p.find(prefix+'/fields').append(deepcopy(p.find(prefix+'/fields/field'))))
reject(lambda p: ET.SubElement(p.find('registers'), 'cluster'))
reject(lambda p: setattr(p.find("registers/register[name='FUNCTION1_INTERRUPT_ACK']/fields/field/access"), 'text', 'read-write'))
reject(lambda p: setattr(p.find("registers/register[name='FUNCTION1_INTERRUPT_STATUS']/fields/field[name='UNCLASSIFIED']/bitWidth"), 'text', '17'))
print(f'SDIO event SVD oracle: 1 valid and {count} rejected mutations')
