#!/usr/bin/env python3
"""Deterministic SystemRDL to CMSIS-SVD exporter for the bootstrap model.

The exporter intentionally supports the subset represented by the imported
WS63/BS2X baselines. Unsupported node shapes and properties fail closed instead
of producing a plausible but semantically wrong SVD.

One-dimensional register arrays preserve their stride and dimensions. Imported
legacy WS63/BS2X maps remain expanded until separately reviewed and compacted.
"""

from copy import deepcopy
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from systemrdl import RDLCompiler
from systemrdl.node import AddrmapNode, FieldNode, RegNode, RegfileNode, SignalNode
from systemrdl.rdltypes import AccessType, OnReadType, OnWriteType


ACCESS = {
    AccessType.r: "read-only",
    AccessType.rw: "read-write",
    AccessType.w: "write-only",
    AccessType.rw1: "read-writeOnce",
    AccessType.w1: "writeOnce",
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


def order_children(node: ET.Element, order: str) -> None:
    """CMSIS-SVD XSD uses sequences; parser acceptance alone is insufficient."""
    positions = {tag: index for index, tag in enumerate(order.split())}
    unexpected = {child.tag for child in node} - positions.keys()
    if unexpected:
        raise ValueError(f"unhandled SVD elements in {node.tag}: {unexpected}")
    node[:] = sorted(node, key=lambda child: positions[child.tag])


def verified_field_reset(field: FieldNode) -> int | None:
    node = field
    while isinstance(node, (AddrmapNode, RegfileNode, RegNode, FieldNode)):
        if node.get_property("hisi_reset_unverified") is True:
            return None
        node = node.parent
    return field.get_property("reset")


def reset_value(reg: RegNode) -> int | None:
    value = 0
    found = False
    for field in reg.fields():
        reset = verified_field_reset(field)
        if reset is not None:
            value |= int(reset) << field.low
            found = True
    return value if found else None


def reset_mask(reg: RegNode) -> int:
    """Unknown/reserved reset bits must not become known zero bits in SVD."""
    return sum(((1 << field.width) - 1) << field.low
               for field in reg.fields() if verified_field_reset(field) is not None)


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


def read_action(field: FieldNode) -> str | None:
    onread = field.get_property("onread")
    explicit = field.get_property("hisi_svd_read_action")
    if explicit is not None:
        if onread != OnReadType.ruser or explicit not in ("modify", "modifyExternal"):
            raise ValueError(f"invalid explicit read action: {field.get_path()}")
        return explicit
    if onread is not None:
        if onread not in READ_ACTION:
            raise ValueError(f"unsupported onread {onread}: {field.get_path()}")
        return READ_ACTION[onread]
    return None


def add_side_effects(parent: ET.Element, field: FieldNode) -> None:
    action = read_action(field)
    if action is not None:
        add(parent, "readAction", action)

    onwrite = field.get_property("onwrite")
    if onwrite is not None:
        if onwrite not in MODIFIED_WRITE:
            raise ValueError(f"unsupported onwrite {onwrite}: {field.get_path()}")
        add(parent, "modifiedWriteValues", MODIFIED_WRITE[onwrite])


def export_registers(parent: ET.Element, registers: list) -> None:
    regs_by_name = {r.inst_name.upper(): r for r in registers}
    exported_names = set(regs_by_name)

    for reg in registers:
        if isinstance(reg, RegfileNode):
            if reg.is_array and len(reg.array_dimensions) != 1:
                raise ValueError(f"multidimensional register-file arrays are not implemented: {reg.get_path()}")
            cluster = ET.SubElement(parent, "cluster")
            if reg.is_array:
                add(cluster, "dim", reg.array_dimensions[0])
                add(cluster, "dimIncrement", f"0x{reg.array_stride:X}")
            add(cluster, "name", reg.inst_name.upper() + ("[%s]" if reg.is_array else ""))
            add(cluster, "description", reg.get_property("name") or reg.inst_name)
            add(cluster, "addressOffset", f"0x{reg.raw_address_offset:X}")
            export_registers(cluster, list(reg.children()))
            continue
        if not isinstance(reg, RegNode):
            raise TypeError(f"unsupported register-file child: {reg.get_path()}")
        if reg.is_array and len(reg.array_dimensions) != 1:
            raise ValueError(f"multidimensional register arrays are not implemented: {reg.get_path()}")
        if reg.is_alias:
            raise ValueError(f"RDL hardware aliases need explicit semantic support: {reg.get_path()}")
        if reg.get_property("accesswidth") != reg.get_property("regwidth"):
            raise ValueError(f"split-width register access is not implemented: {reg.get_path()}")
        rxml = ET.SubElement(parent, "register")
        if reg.is_array:
            add(rxml, "dim", reg.array_dimensions[0])
            add(rxml, "dimIncrement", f"0x{reg.array_stride:X}")
        suffix = "[%s]" if reg.is_array else ""
        add(rxml, "name", reg.inst_name.upper() + suffix)
        add(rxml, "description", reg.get_property("name") or reg.inst_name)
        if alternate := reg.get_property("hisi_svd_alternate_register"):
            other = regs_by_name.get(alternate.upper())
            if (not isinstance(other, RegNode) or other == reg or
                other.get_property("hisi_svd_alternate_register") is not None or
                other.raw_address_offset != reg.raw_address_offset or
                other.get_property("regwidth") != reg.get_property("regwidth") or
                other.get_property("accesswidth") != reg.get_property("accesswidth") or
                other.array_dimensions != reg.array_dimensions or other.array_stride != reg.array_stride or
                ({f.get_property("sw") for f in reg.fields()}, {f.get_property("sw") for f in other.fields()})
                not in (({AccessType.r}, {AccessType.w}), ({AccessType.w}, {AccessType.r}))):
                raise ValueError(f"invalid alternate register view: {reg.get_path()}")
            add(rxml, "alternateRegister", alternate.upper() + suffix)
        add(rxml, "addressOffset", f"0x{reg.raw_address_offset:X}")
        add(rxml, "size", reg.get_property("regwidth"))
        reset = reset_value(reg)
        if reset is not None:
            add(rxml, "resetValue", f"0x{reset:08X}")
            add(rxml, "resetMask", f"0x{reset_mask(reg):08X}")
        accesses = {field.get_property("sw") for field in reg.fields()}
        # SVD register access defaults to read-write. Field-only access
        # metadata otherwise gives write-only EOI registers a read API.
        if len(accesses) == 1:
            access = next(iter(accesses))
            if access not in ACCESS:
                raise ValueError(f"unsupported access {access}: {reg.get_path()}")
            add(rxml, "access", ACCESS[access])
        actions = [read_action(f) for f in reg.fields()]
        if any(actions):
            # Whole-register Debug must not accidentally pop a FIFO or ack
            # an interrupt. Preserve per-field details below as well.
            add(rxml, "readAction", actions[0] if len(set(actions)) == 1 else "modify")
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

        if (word_name := reg.get_property("hisi_svd_word_view")) is not None:
            # SDK uses both ldrh/strh and ldr/str on the same clock slot. RDL
            # cannot overlap two RW declarations of unequal sizes; preserve
            # one field source and emit an explicitly evidenced SVD view.
            # Do not generalize this into arbitrary overlapping MMIO.
            if (not re.fullmatch(r"[A-Z][A-Z0-9_]*", word_name) or
                word_name in exported_names or reg.is_array or
                not isinstance(reg.parent, AddrmapNode) or
                reg.get_property("regwidth") != 16 or
                reg.absolute_address % 4 != 0 or
                reg.get_property("hisi_svd_alternate_register") is not None or
                reg.get_property("hisi_write_min") is not None or
                reg.get_property("hisi_write_max") is not None or
                not reg.get_property("hisi_evidence") or
                accesses != {AccessType.rw} or reset is not None or
                any(f.get_property("onread") is not None or
                    f.get_property("onwrite") is not None for f in reg.fields())):
                raise ValueError(f"invalid word access view: {reg.get_path()}")
            start = reg.raw_address_offset
            extent = max(r.raw_address_offset + r.total_size for r in registers)
            if start + 4 > extent or any(
                other != reg and other.raw_address_offset < start + 4 and
                other.raw_address_offset + other.total_size > start
                for other in registers
            ):
                raise ValueError(f"word access view exceeds vacant slot: {reg.get_path()}")
            word = deepcopy(rxml)
            word.find("name").text = word_name
            word.find("description").text = (
                "32-bit little-endian access view of " + reg.inst_name.upper() +
                "; same low-half fields, upper bits unmodeled; preserve them on RMW. " +
                reg.get_property("hisi_evidence")
            )
            word.find("size").text = "32"
            add(word, "alternateRegister", reg.inst_name.upper())
            parent.append(word)
            exported_names.add(word_name)

    for register in parent.findall("register"):
        for field in register.findall("fields/field"):
            order_children(field, "name description bitOffset bitWidth access modifiedWriteValues writeConstraint readAction enumeratedValues")
        order_children(register, "dim dimIncrement name description alternateRegister addressOffset size access resetValue resetMask writeConstraint readAction fields")

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
        if peripheral.is_array:
            raise ValueError(f"peripheral arrays are not implemented: {peripheral.get_path()}")

        registers = list(peripheral.children())
        if not registers or any(not isinstance(reg, (RegNode, RegfileNode)) for reg in registers):
            raise TypeError(f"{peripheral.get_path()} must contain registers or register files")

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
        # Elaboration's size can include a trailing ispresent=false register.
        # Export only the represented register window, retaining array strides.
        extent = max(reg.raw_address_offset + reg.total_size for reg in registers)
        add(block, "size", f"0x{extent:X}")
        add(block, "usage", "registers")
        rxmls = ET.SubElement(pxml, "registers")
        export_registers(rxmls, registers)
        order_children(pxml, "name description baseAddress addressBlock interrupt registers")

    # Derivation is opt-in and checked after all register trees have been built,
    # so declaration/address ordering cannot hide an invalid reference. Compare
    # every field/access/side-effect, not just the RDL type's name or byte size.
    by_name = {p.findtext("name"): p for p in peripherals}
    derivatives = {}
    for node in peripheral_nodes:
        base = node.get_property("hisi_svd_derived_from")
        if base:
            derivatives[node.inst_name.upper()] = base.upper()
    for name, base in derivatives.items():
        if base not in by_name or base == name or base in derivatives:
            raise ValueError(f"invalid/chained peripheral derivation: {name} -> {base}")
        own_regs = by_name[name].find("registers")
        base_regs = by_name[base].find("registers")
        if ET.tostring(own_regs) != ET.tostring(base_regs):
            raise ValueError(f"non-equivalent peripheral derivation: {name} -> {base}")
    for name, base in derivatives.items():
        by_name[name].set("derivedFrom", base)
        by_name[name].remove(by_name[name].find("registers"))

    ET.indent(device, space="  ")
    destination.write_bytes(ET.tostring(device, encoding="utf-8", xml_declaration=True))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} INPUT.rdl OUTPUT.svd")
    export(Path(sys.argv[1]), Path(sys.argv[2]))
