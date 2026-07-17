#!/usr/bin/env python3
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "WS63.experimental.svd": {
        "SPI0": 0x44020000,
        "SPI1": 0x44021000,
    },
    "BS2X.experimental.svd": {
        "SPI0": 0x52087000,
        "SPI1": 0x52088000,
        "SPI2": 0x52089000,
    },
}

failed = False
for filename, expected in EXPECTED.items():
    path = ROOT / "generated" / filename
    tree = ET.parse(path)
    peripherals = {
        p.findtext("name"): int(p.findtext("baseAddress"), 0)
        for p in tree.findall("./peripherals/peripheral")
    }
    for name, address in expected.items():
        actual = peripherals.get(name)
        if actual != address:
            print(f"{filename}: {name} expected {address:#x}, got {actual!r}")
            failed = True

if failed:
    sys.exit(1)
print("generated register maps match the tracer-bullet contract")
