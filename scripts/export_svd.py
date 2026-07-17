#!/usr/bin/env python3
"""Deterministic SystemRDL to CMSIS-SVD exporter for the bootstrap model.

The exporter intentionally supports the subset represented by the imported
WS63/BS2X baselines. Unsupported node shapes and properties fail closed instead
of producing a plausible but semantically wrong SVD.

Register arrays are currently emitted as expanded registers because the
PeakRDL SVD importer expands them. This preserves addresses and field behavior,
but not the original PAC array API. ``check.py`` keeps that limitation visible.
"""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from systemrdl import RDLCompiler
from systemrdl.node import AddrmapNode, FieldNode, RegNode, SignalNode
from systemrdl.rdltypes import AccessType, OnReadType, OnWriteType


ACCESS = {
    AccessType.r: "read-only",
    AccessType.rw: "read-write",
    AccessType.w: "write-only",
    AccessType.rw1: "read-write",
    AccessType.w1: "write-only",
}

READ_ACTION = {
    OnReadType.rclr: "clear",
    OnReadType.rset: "set",
}

MODIFIED_WRITE = {
    OnWriteType.woset: "oneToSet",
    OnWriteType.woclr: "oneToClear",
    OnWriteType.wot: "oneToToggle",
    OnWriteType.wzs: "zeroToSet",
    OnWriteType.wzc: "zeroToClear",
    OnWriteType.wzt: "zeroToToggle",
    OnWriteType.wclr: "clear",
    OnWriteType.wset: "set",
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


def add_write_constraint(parent: ET.Element, node: RegNode | FieldNode) -> None:
    minimum = node.get_property("hisi_write_min")
    maximum = node.get_property("hisi_write_max")
    if minimum is None and maximum is None:
        return
    if minimum is None or maximum is None or int(minimum) > int(maximum):
        raise ValueError(f"invalid write constraint: {node.get_path()}")
    constraint = ET.SubElement(parent, "writeConstraint")
    value_range = ET.SubElement(constraint, "range")
    add(value_range, "minimum", int(minimum))
    add(value_range, "maximum", int(maximum))


def add_enumerated_values(parent: ET.Element, field: FieldNode) -> None:
    encoded = field.get_property("encode")
    if encoded is None:
        return
    values = ET.SubElement(parent, "enumeratedValues")
    for member in encoded:
        value = ET.SubElement(values, "enumeratedValue")
        add(value, "name", member.name)
        if member.rdl_desc:
            add(value, "description", member.rdl_desc)
        add(value, "value", int(member.value))


def add_side_effects(parent: ET.Element, field: FieldNode) -> None:
    onread = field.get_property("onread")
    if onread is not None:
        if onread not in READ_ACTION:
            raise ValueError(f"unsupported onread {onread}: {field.get_path()}")
        add(parent, "readAction", READ_ACTION[onread])

    onwrite = field.get_property("onwrite")
    if onwrite is not None:
        if onwrite not in MODIFIED_WRITE:
            raise ValueError(f"unsupported onwrite {onwrite}: {field.get_path()}")
        add(parent, "modifiedWriteValues", MODIFIED_WRITE[onwrite])


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

    irq_by_owner: dict[str, list[SignalNode]] = {}
    peripheral_nodes: list[AddrmapNode] = []
    for child in root_node.children():
        if isinstance(child, AddrmapNode):
            peripheral_nodes.append(child)
        elif isinstance(child, SignalNode):
            owner = child.get_property("hisi_irq_owner")
            number = child.get_property("hisi_irq_number")
            if not owner or number is None:
                raise ValueError(f"incomplete IRQ signal metadata: {child.get_path()}")
            irq_by_owner.setdefault(owner.upper(), []).append(child)
        else:
            raise TypeError(f"unsupported top-level node: {child.get_path()}")

    known_owners = {p.inst_name.upper() for p in peripheral_nodes}
    orphan_owners = set(irq_by_owner) - known_owners
    if orphan_owners:
        raise ValueError(f"IRQ owners do not name a peripheral: {sorted(orphan_owners)}")

    for peripheral in peripheral_nodes:

        registers = list(peripheral.children())
        if not registers or any(not isinstance(reg, RegNode) for reg in registers):
            raise TypeError(f"{peripheral.get_path()} must contain only registers")

        pxml = ET.SubElement(peripherals, "peripheral")
        add(pxml, "name", peripheral.inst_name.upper())
        add(pxml, "description", peripheral.get_property("name") or peripheral.inst_name)
        add(pxml, "baseAddress", f"0x{peripheral.absolute_address:08X}")
        for irq in sorted(
            irq_by_owner.get(peripheral.inst_name.upper(), []),
            key=lambda node: int(node.get_property("hisi_irq_number")),
        ):
            ixml = ET.SubElement(pxml, "interrupt")
            add(ixml, "name", irq.inst_name.upper())
            add(ixml, "description", irq.get_property("hisi_evidence") or irq.inst_name)
            add(ixml, "value", int(irq.get_property("hisi_irq_number")))
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
            add_write_constraint(rxml, reg)
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
                add_write_constraint(fxml, field)
                add_side_effects(fxml, field)
                add_enumerated_values(fxml, field)

    ET.indent(device, space="  ")
    destination.write_bytes(ET.tostring(device, encoding="utf-8", xml_declaration=True))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} INPUT.rdl OUTPUT.svd")
    export(Path(sys.argv[1]), Path(sys.argv[2]))
