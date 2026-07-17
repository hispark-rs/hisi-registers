#!/usr/bin/env python3
"""Minimal, deterministic SystemRDL to CMSIS-SVD exporter for the tracer bullet.

This deliberately supports only flat addrmap -> register -> field structures.
Unsupported node shapes fail closed instead of producing a plausible wrong SVD.
"""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from systemrdl import RDLCompiler
from systemrdl.node import AddrmapNode, FieldNode, RegNode
from systemrdl.rdltypes import AccessType


ACCESS = {
    AccessType.r: "read-only",
    AccessType.rw: "read-write",
    AccessType.w: "write-only",
    AccessType.rw1: "read-write",
    AccessType.w1: "write-only",
}


def add(parent: ET.Element, tag: str, text: object) -> ET.Element:
    node = ET.SubElement(parent, tag)
    node.text = str(text)
    return node


def reset_value(reg: RegNode) -> int | None:
    value = 0
    found = False
    for field in reg.fields():
        reset = field.get_property("reset")
        if reset is not None:
            value |= int(reset) << field.low
            found = True
    return value if found else None


def export(source: Path, destination: Path) -> None:
    compiler = RDLCompiler()
    compiler.compile_file(str(source))
    root_node = compiler.elaborate().top

    device = ET.Element(
        "device",
        {"schemaVersion": "1.3"},
    )
    add(device, "name", root_node.inst_name.upper())
    add(device, "version", "0.1.0-experimental")
    add(device, "description", root_node.get_property("name") or root_node.inst_name)
    add(device, "addressUnitBits", 8)
    add(device, "width", 32)
    peripherals = ET.SubElement(device, "peripherals")

    for peripheral in root_node.children():
        if not isinstance(peripheral, AddrmapNode):
            raise TypeError(f"unsupported top-level node: {peripheral.get_path()}")

        registers = list(peripheral.children())
        if not registers or any(not isinstance(reg, RegNode) for reg in registers):
            raise TypeError(f"{peripheral.get_path()} must contain only registers")

        pxml = ET.SubElement(peripherals, "peripheral")
        add(pxml, "name", peripheral.inst_name.upper())
        add(pxml, "description", peripheral.get_property("name") or peripheral.inst_name)
        add(pxml, "baseAddress", f"0x{peripheral.absolute_address:08X}")
        block = ET.SubElement(pxml, "addressBlock")
        add(block, "offset", "0x0")
        add(block, "size", f"0x{peripheral.size:X}")
        add(block, "usage", "registers")
        rxmls = ET.SubElement(pxml, "registers")

        for reg in registers:
            rxml = ET.SubElement(rxmls, "register")
            add(rxml, "name", reg.inst_name.upper())
            add(rxml, "description", reg.get_property("name") or reg.inst_name)
            add(rxml, "addressOffset", f"0x{reg.address_offset:X}")
            add(rxml, "size", reg.get_property("regwidth"))
            reset = reset_value(reg)
            if reset is not None:
                add(rxml, "resetValue", f"0x{reset:08X}")
            fxmls = ET.SubElement(rxml, "fields")

            for field in reg.children():
                if not isinstance(field, FieldNode):
                    raise TypeError(f"unsupported register child: {field.get_path()}")
                access = field.get_property("sw")
                if access not in ACCESS:
                    raise ValueError(f"unsupported access {access}: {field.get_path()}")
                fxml = ET.SubElement(fxmls, "field")
                add(fxml, "name", field.inst_name.upper())
                add(fxml, "description", field.get_property("desc") or field.inst_name)
                add(fxml, "bitOffset", field.low)
                add(fxml, "bitWidth", field.width)
                add(fxml, "access", ACCESS[access])

    ET.indent(device, space="  ")
    destination.write_bytes(ET.tostring(device, encoding="utf-8", xml_declaration=True))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} INPUT.rdl OUTPUT.svd")
    export(Path(sys.argv[1]), Path(sys.argv[2]))
