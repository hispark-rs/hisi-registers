#!/usr/bin/env python3
"""Bootstrap split SystemRDL sources from an audited SVD snapshot.

This is a migration tool, not an ongoing truth pipeline. Its output is committed,
reviewed, and then maintained as canonical SystemRDL. Empty SVD registers are
represented by a full-width VALUE field so SystemRDL does not silently drop them.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET


# Confirmed by byte-identical register definitions and register-operation
# sources in fbb_ws63 and fbb_bs2x. The canonical block is maintained manually
# after the initial WS63 bootstrap; chip imports must never fork it again.
SHARED_PERIPHERALS = {
    "SPI0": ("../ip/spi-v151.rdl", "spi_v151_regs"),
    "WDT": ("../ip/watchdog-v151.rdl", "watchdog_v151_regs"),
    "SFC_CFG": ("../ip/sfc-v150.rdl", "sfc_v150_regs"),
    "PWM": ("../ip/pwm-v151.rdl", "pwm_v151_regs"),
    "I2S": ("../ip/sio-v151.rdl", "sio_v151_regs"),
    "TIMER": ("../ip/timer-v150.rdl", "timer_v150_regs"),
    "TCXO": ("../ip/tcxo-v150-data16.rdl", "tcxo_v150_data16_regs"),
    "GPIO0": ("../ip/gpio-v150-basic.rdl", "gpio_v150_basic_regs"),
    "UART0": ("../ip/uart-v151-basic.rdl", "uart_v151_basic_regs"),
}

# Family variants whose version name is shared only by a subset of chips.
# Keeping these out of SHARED_PERIPHERALS prevents a WS63 re-import from
# silently selecting the WS53/BS2X I2C/RTC layouts.
CHIP_SHARED_PERIPHERALS = {
    "bs2x": {
        "I2C0": ("../ip/i2c-v151-ws53-bs2x.rdl", "i2c_v151_ws53_bs2x_regs"),
        "RTC": ("../ip/rtc-v150-ws53-bs2x.rdl", "rtc_v150_ws53_bs2x_regs"),
    },
}

CHIP_EXTRA_INSTANCES = {
    "bs2x": [
        ("sfc_v150_regs", "sfc_cfg", "0x90000000"),
        ("sio_v151_regs", "i2s", "0x52030000"),
    ],
}


def text(node: ET.Element, tag: str, default: str = "") -> str:
    value = node.findtext(tag)
    return value if value is not None else default


def inherited(node: ET.Element, parents: list[ET.Element], tag: str, default: str) -> str:
    value = node.findtext(tag)
    if value is not None:
        return value
    for parent in parents:
        value = parent.findtext(tag)
        if value is not None:
            return value
    return default


def normalize_svd(tree: ET.ElementTree) -> list[str]:
    root = tree.getroot()
    notes: list[str] = []
    for peripheral in root.findall("./peripherals/peripheral"):
        registers = peripheral.find("registers")
        if registers is None:
            continue

        offsets: dict[int, ET.Element] = {}
        for register in list(registers.findall("register")):
            fields = register.find("fields")
            if fields is None or not fields.findall("field"):
                fields = fields or ET.SubElement(register, "fields")
                field = ET.SubElement(fields, "field")
                ET.SubElement(field, "name").text = "VALUE"
                ET.SubElement(field, "description").text = (
                    "Whole-register value; individual fields are not yet documented"
                )
                ET.SubElement(field, "bitOffset").text = "0"
                ET.SubElement(field, "bitWidth").text = inherited(
                    register, [peripheral, root], "size", "32"
                )
                ET.SubElement(field, "access").text = inherited(
                    register, [peripheral, root], "access", "read-write"
                )

            offset = int(text(register, "addressOffset"), 0)
            previous = offsets.get(offset)
            if previous is None:
                offsets[offset] = register
                continue

            # The current WS63 SVD has CHIP_RESET and AON_SOFT_RST_CTL at the
            # same physical word. Merge their non-overlapping fields into one
            # register and record the lost alias explicitly for later modeling.
            previous_fields = previous.find("fields")
            assert previous_fields is not None
            for field in register.findall("./fields/field"):
                previous_fields.append(field)
            notes.append(
                f"{text(peripheral, 'name')}: merged {text(register, 'name')} into "
                f"{text(previous, 'name')} at {offset:#x}; model an alias after review"
            )
            registers.remove(register)
    return notes


def replace_top_type(source: str, old: str, new: str) -> str:
    replaced, count = re.subn(
        rf"^addrmap\s+{re.escape(old)}\s*\{{", f"addrmap {new} {{", source, count=1
    )
    if count != 1:
        raise RuntimeError(f"could not rename top addrmap {old} to {new}")
    return replaced


def preserve_write_constraints(source: str, peripheral: ET.Element) -> str:
    """Translate the SVD range subset that PeakRDL's importer omits."""
    insertions: list[tuple[int, str]] = []
    for register in peripheral.findall("./registers/register"):
        value_range = register.find("./writeConstraint/range")
        if value_range is None:
            continue
        name = text(register, "name")
        minimum = int(text(value_range, "minimum"), 0)
        maximum = int(text(value_range, "maximum"), 0)
        marker = re.search(rf"^    \}} {re.escape(name)} @ [^;]+;$", source, re.MULTILINE)
        if marker is None:
            raise RuntimeError(f"could not preserve writeConstraint for {name}")
        body_start = source.rfind("    reg {\n", 0, marker.start())
        if body_start < 0:
            raise RuntimeError(f"could not find register body for {name}")
        insertions.append(
            (
                body_start + len("    reg {\n"),
                f"        hisi_write_min = {minimum};\n"
                f"        hisi_write_max = {maximum};\n",
            )
        )
    for offset, addition in sorted(insertions, reverse=True):
        source = source[:offset] + addition + source[offset:]
    return source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chip", required=True)
    parser.add_argument("--svd", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()

    tree = ET.parse(args.svd)
    root = tree.getroot()
    notes = normalize_svd(tree)
    peripherals = root.findall("./peripherals/peripheral")
    by_name = {text(p, "name"): p for p in peripherals}
    shared_peripherals = {
        **SHARED_PERIPHERALS,
        **CHIP_SHARED_PERIPHERALS.get(args.chip, {}),
    }
    definitions = [
        p
        for p in peripherals
        if "derivedFrom" not in p.attrib and text(p, "name") not in shared_peripherals
    ]

    peripheral_dir = args.output / "peripherals"
    peripheral_dir.mkdir(parents=True, exist_ok=True)
    for stale in peripheral_dir.glob("*.rdl"):
        stale.unlink()

    with tempfile.TemporaryDirectory() as tmp:
        normalized = Path(tmp) / f"{args.chip}.normalized.svd"
        tree.write(normalized, encoding="utf-8", xml_declaration=True)
        for peripheral in definitions:
            name = text(peripheral, "name")
            type_name = f"{args.chip}_{name.lower()}_regs"
            destination = peripheral_dir / f"{name.lower()}.rdl"
            imported = Path(tmp) / f"{name}.rdl"
            subprocess.run(
                [
                    "peakrdl",
                    "systemrdl",
                    str(normalized),
                    "--svd-peripheral",
                    name,
                    "-o",
                    str(imported),
                ],
                check=True,
            )
            body = replace_top_type(imported.read_text(), name, type_name)
            body = preserve_write_constraints(body, peripheral)
            destination.write_text(
                "// Bootstrap source: " + args.evidence + "\n"
                "// Imported once; this RDL is now reviewed canonical source.\n\n"
                + body
            )

    include_lines = ["`include \"../properties.rdl\""]
    include_lines.extend(
        f'`include "{path}"' for path, _ in shared_peripherals.values()
    )
    include_lines.extend(
        f"`include \"{args.chip}/peripherals/{text(p, 'name').lower()}.rdl\""
        for p in definitions
    )

    map_lines = [*include_lines, "", f"addrmap {args.chip} {{"]
    for peripheral in peripherals:
        name = text(peripheral, "name")
        base_name = peripheral.attrib.get("derivedFrom", name)
        if base_name not in by_name:
            raise RuntimeError(f"{name} derives from missing {base_name}")
        if base_name in shared_peripherals:
            _, type_name = shared_peripherals[base_name]
        else:
            type_name = f"{args.chip}_{base_name.lower()}_regs"
        map_lines.append(
            f"    {type_name} {name.lower()} @ {text(peripheral, 'baseAddress')};"
        )
    for type_name, name, address in CHIP_EXTRA_INSTANCES.get(args.chip, []):
        map_lines.append(f"    {type_name} {name} @ {address};")

    for peripheral in peripherals:
        owner = text(peripheral, "name")
        for irq in peripheral.findall("interrupt"):
            irq_name = text(irq, "name")
            irq_number = int(text(irq, "value"), 0)
            map_lines.extend(
                [
                    "",
                    "    signal {",
                    f"        hisi_irq_number = {irq_number};",
                    f'        hisi_irq_owner = "{owner}";',
                    f'        hisi_evidence = "{args.evidence}";',
                    f"    }} {irq_name};",
                ]
            )
    map_lines.append("};")
    (args.output.parent / f"{args.chip}.rdl").write_text("\n".join(map_lines) + "\n")

    digest = hashlib.sha256(args.svd.read_bytes()).hexdigest()
    manifest = [
        f"chip = {args.chip}",
        f"source = {args.evidence}",
        f"sha256 = {digest}",
        f"peripherals = {len(peripherals)}",
        f"definitions = {len(definitions)}",
        f"shared_definitions = {len(shared_peripherals)}",
        f"interrupts = {sum(len(p.findall('interrupt')) for p in peripherals)}",
        "normalization_notes:",
    ]
    manifest.extend(f"  - {note}" for note in notes)
    (args.output / "IMPORT-MANIFEST.txt").write_text("\n".join(manifest) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
