#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Reject ambiguous send-command views and replay recorded synthetic RAM."""
import argparse
import base64
import copy
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import zlib
import check_ws53_sdio_announce as audit


def run(a):
    audit.validate_svd(a.svd)
    tree=ET.parse(a.svd)
    rejected=[]
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'bad.svd'
        for kind in ('missing','duplicate','offset','width','field-width','status-access',
                     'command-access','resetValue','resetMask','readAction',
                     'modifiedWriteValues','alias','derivedFrom','cluster'):
            root=copy.deepcopy(tree.getroot())
            p=root.find("./peripherals/peripheral[name='SDIO']")
            regs=p.find('registers')
            status=regs.find("register[name='FUNCTION1_SEND_STATUS']")
            command=regs.find("register[name='FUNCTION1_SEND_COMMAND']")
            length=regs.find("register[name='FUNCTION1_SEND_LENGTH']")
            if kind=='missing': regs.remove(command)
            elif kind=='duplicate': regs.append(copy.deepcopy(command))
            elif kind=='offset': command.find('addressOffset').text='0x108'
            elif kind=='width': command.find('size').text='16'
            elif kind=='field-width': length.find('fields/field/bitWidth').text='20'
            elif kind=='status-access': status.find('fields/field/access').text='read-write'
            elif kind=='command-access': command.find('fields/field/access').text='read-write'
            elif kind=='alias': command.find('alternateRegister').text='FUNCTION1_SEND_LENGTH'
            elif kind=='derivedFrom': command.set('derivedFrom','OTHER')
            elif kind=='cluster': ET.SubElement(regs,'cluster')
            else: ET.SubElement(command,kind).text='0'
            ET.ElementTree(root).write(path)
            try: audit.validate_svd(path)
            except (AssertionError,ValueError): rejected.append(kind)
            else: raise AssertionError('invalid SVD accepted: '+kind)
    observations=json.loads(a.observations.read_text())
    raw=zlib.decompress(base64.b64decode(observations['ram_zlib_base64']))
    assert observations['cases']==audit.cases()
    audit.verify(observations['cases'],raw)
    corrupt=bytearray(raw); corrupt[16+0x40]^=1
    try: audit.verify(observations['cases'],corrupt)
    except AssertionError: pass
    else: raise AssertionError('unexpected ADMA table write accepted')
    print(json.dumps(dict(passed=True,rejected_svd=rejected,recorded_rom_cases=len(observations['cases']),
        corrupted_unrelated_register_rejected=True,hardware_accessed=False,rom_reexecuted=False),indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--svd',required=True,type=Path)
    parser.add_argument('--observations',required=True,type=Path)
    run(parser.parse_args())
