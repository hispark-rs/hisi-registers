#!/usr/bin/env python3
from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "WS63.experimental.svd": {
        "peripherals": 38,
        "interrupts": 46,
        "addresses": {"SPI0": 0x44020000, "SPI1": 0x44021000},
        "source_arrays": 39,
        "registers_expanded": 1248,
        "fields_expanded": 2104,
        "write_constraints": 6,
    },
    "BS2X.experimental.svd": {
        "peripherals": 32,
        "interrupts": 52,
        "addresses": {
            "SPI0": 0x52087000,
            "SPI1": 0x52088000,
            "SPI2": 0x52089000,
        },
        "source_arrays": 11,
        "registers_expanded": 1017,
        "fields_expanded": 2154,
        "write_constraints": 6,
    },
    "WS53.experimental.svd": {
        "peripherals": 32,
        "interrupts": 20,
        "addresses": {
            "SYSTEM_MEMORY": 0x57030000,
            "M_CRG": 0x52000000,
            "GLB_CTL": 0x57000000,
            "PINMUX_MCU": 0x52008000,
            "PINMUX_AON": 0x57036000,
            "PINMUX_TZPC": 0x5703A000,
            "SPI0": 0x52087000,
            "QSPI1": 0x5208D000,
            "MDMA": 0x52070000,
            "ADC": 0x5208A000,
            "MCU_DIAG": 0x52004100,
            "TSENSOR": 0x59006300,
            "SDIO": 0x52060000,
            "TRNG": 0x52024000,
            "EFUSE": 0x57024000,
            "WDT": 0x5702B000,
            "SFC_CFG": 0x53000000,
            "PWM": 0x52082000,
            "UART0": 0x52081000,
            "UART1": 0x52080000,
            "UART2": 0x57014000,
            "I2C0": 0x52083000,
            "I2C1": 0x52084000,
            "RTC0": 0x57029100,
            "RTC1": 0x57029200,
            "I2S": 0x52030000,
            "AUDIO_CMU": 0x57008600,
            "TIMER": 0x52002000,
            "TCXO": 0x57000200,
            "GPIO0": 0x5700C000,
            "GPIO1": 0x57028000,
            "GPIO2": 0x57010000,
        },
        "source_arrays": 0,
        "output_arrays": 6,
        "cluster_arrays": 1,
        "registers_expanded": 945,
        "fields_expanded": 1997,
        "write_constraints": 20,
    },
}

failed = False

shared_spi = ROOT / "rdl" / "ip" / "spi-v151.rdl"
if "addrmap spi_v151_regs" not in shared_spi.read_text():
    print("shared SPI v151 definition is missing its canonical type")
    failed = True
for chip, instances in {"ws63": 2, "bs2x": 3}.items():
    chip_map = (ROOT / "rdl" / "chips" / f"{chip}.rdl").read_text()
    if chip_map.count("spi_v151_regs spi") != instances:
        print(f"{chip}: expected {instances} shared SPI v151 instances")
        failed = True
    private_spi = ROOT / "rdl" / "chips" / chip / "peripherals" / "spi0.rdl"
    if private_spi.exists():
        print(f"{chip}: private SPI definition would fork the shared IP truth")
        failed = True

shared_contracts = {
    "watchdog_v151_regs": {"ws53": 1, "ws63": 1, "bs2x": 1},
    "sfc_v150_regs": {"ws53": 1, "ws63": 1, "bs2x": 1},
    "pwm_v151_regs": {"ws53": 1, "ws63": 1, "bs2x": 1},
    "sio_v151_regs": {"ws63": 1, "bs2x": 1},
    "ws53_sio_v151_regs": {"ws53": 1},
    "uart_v151_basic_regs": {"ws53": 3, "ws63": 3, "bs2x": 3},
    "timer_v150_regs": {"ws53": 1, "ws63": 1, "bs2x": 1},
    "tcxo_v150_data16_regs": {"ws53": 1, "ws63": 1, "bs2x": 1},
    "gpio_v150_basic_regs": {"ws53": 3, "ws63": 4, "bs2x": 6},
    "i2c_v151_ws53_bs2x_regs": {"ws53": 2, "bs2x": 2},
    "rtc_v150_ws53_bs2x_regs": {"ws53": 2, "bs2x": 1},
}
for type_name, chips in shared_contracts.items():
    for chip, instances in chips.items():
        chip_map = (ROOT / "rdl" / "chips" / f"{chip}.rdl").read_text()
        if chip_map.count(f"{type_name} ") != instances:
            print(f"{chip}: expected {instances} {type_name} instance(s)")
            failed = True

for chip in ("ws63", "bs2x"):
    for filename in (
        "wdt.rdl",
        "sfc_cfg.rdl",
        "pwm.rdl",
        "i2s.rdl",
        "timer.rdl",
        "tcxo.rdl",
        "gpio0.rdl",
        "uart0.rdl",
    ):
        private_ip = ROOT / "rdl" / "chips" / chip / "peripherals" / filename
        if private_ip.exists():
            print(f"{chip}: {filename} would fork a confirmed three-chip shared IP")
            failed = True

for filename in ("uart0.rdl", "i2c0.rdl", "rtc.rdl"):
    private_ip = ROOT / "rdl" / "chips" / "bs2x" / "peripherals" / filename
    if private_ip.exists():
        print(f"bs2x: {filename} would fork a confirmed shared family variant")
        failed = True

def weighted_registers(container, multiplier=1):
    """Include each cluster dimension when counting expanded register views."""
    for child in container:
        if child.tag not in ("register", "cluster"):
            continue
        count = multiplier * int(child.findtext("dim", "1"))
        if child.tag == "cluster":
            yield from weighted_registers(child, count)
        else:
            yield child, count


summary = {}
for filename, expected in EXPECTED.items():
    path = ROOT / "generated" / filename
    tree = ET.parse(path)
    peripheral_nodes = tree.findall("./peripherals/peripheral")
    peripherals = {
        p.findtext("name"): int(p.findtext("baseAddress"), 0)
        for p in peripheral_nodes
    }
    if len(peripheral_nodes) != expected["peripherals"]:
        print(
            f"{filename}: expected {expected['peripherals']} peripherals, "
            f"got {len(peripheral_nodes)}"
        )
        failed = True

    irq_count = sum(len(p.findall("interrupt")) for p in peripheral_nodes)
    if irq_count != expected["interrupts"]:
        print(f"{filename}: expected {expected['interrupts']} IRQs, got {irq_count}")
        failed = True

    for name, address in expected["addresses"].items():
        actual = peripherals.get(name)
        if actual != address:
            print(f"{filename}: {name} expected {address:#x}, got {actual!r}")
            failed = True

    # Count effective instance registers, including checked derived peripherals.
    by_name = {p.findtext("name"): p for p in peripheral_nodes}
    if filename == "WS53.experimental.svd":
        for instance in ("RTC0", "RTC1"):
            rtc = by_name[instance]
            regs = {r.findtext("name"): r for r in rtc.findall("registers/register")}
            assert len(regs) == 8
            for i, (name, access) in enumerate((
                    ("LOAD_COUNT0", "read-write"), ("LOAD_COUNT1", "read-write"),
                    ("CURRENT_VALUE0", "read-only"), ("CURRENT_VALUE1", "read-only"),
                    ("CONTROL", None), ("EOI_REN", "write-only"),
                    ("RAW_INTR", "read-only"), ("INTR", "read-only"))):
                reg = regs[name]
                assert int(reg.findtext("addressOffset"), 0) == 4 * i
                assert reg.findtext("size") == "32" and reg.findtext("access") == access
                assert reg.find("resetValue") is None and reg.find("resetMask") is None
                assert reg.find("readAction") is None
            assert regs["CONTROL"].findtext("fields/field[name='CNT_LOCK']/access") == "read-only"
            assert [(f.findtext("name"), f.findtext("bitOffset"), f.findtext("bitWidth"))
                    for f in regs["CONTROL"].findall("fields/field")] == [
                ("ENABLE", "0", "1"), ("MODE", "1", "2"), ("INT_MASK", "3", "1"),
                ("RSTFSM", "4", "1"), ("CNT_REQ", "5", "1"), ("CNT_LOCK", "6", "1")]
            values = regs["CONTROL"].findall("fields/field[name='MODE']/enumeratedValues/enumeratedValue/value")
            assert [int(v.text) for v in values] == [0, 1, 2, 3]
            limit = regs["EOI_REN"].find("fields/field/writeConstraint/range")
            assert limit.findtext("minimum") == limit.findtext("maximum") == "1"
        # SDK-backed access contracts: a readable key or mixed request/status
        # word would allow misleading read-modify-write APIs in downstream PACs.
        wdt = by_name["WDT"]
        wregs = {r.findtext("name"): r for r in wdt.findall("registers/register")}
        assert len(wregs) == 13 and not wdt.findall("interrupt")
        for name, offset, access in (
            ("LOCK_STATUS", 0, "read-only"), ("LOCK_COMMAND", 0, "write-only"),
            ("WDT_LOAD", 4, "read-write"), ("WDT_RESTART", 8, "write-only"),
            ("WDT_EOI", 12, "read-only"), ("WDT_CR", 16, "read-write"),
            ("WDT_CNT", 20, "read-only"), ("WDT_RAW_INTR", 24, "read-only"),
            ("WDT_INTR", 28, "read-only"), ("WDT_LPIF_STATE", 32, "read-only"),
            ("WDT_STATUS", 36, "read-only"), ("CCVR_STATUS", 40, "read-only"),
            ("CCVR_REQUEST", 40, "write-only"),
        ):
            r = wregs[name]
            assert int(r.findtext("addressOffset"), 0) == offset, name
            assert r.findtext("access") == access and r.findtext("size") == "32", name
            assert r.find("resetValue") is None and r.find("resetMask") is None, name
        assert wregs["LOCK_COMMAND"].findtext("alternateRegister") == "LOCK_STATUS"
        assert wregs["CCVR_REQUEST"].findtext("alternateRegister") == "CCVR_STATUS"
        assert wregs["WDT_EOI"].findtext("readAction") == "modifyExternal"
        assert wregs["WDT_CNT"].findtext("readAction") == "modifyExternal"
        for name, value in (("WDT_RESTART", 0x5a5a5a5a), ("CCVR_REQUEST", 1)):
            c = wregs[name].find("fields/field/writeConstraint/range")
            assert int(c.findtext("minimum"), 0) == int(c.findtext("maximum"), 0) == value
        assert "0 free, 1 busy" in wregs["WDT_STATUS"].findtext("fields/field/description")
        modes = wregs["WDT_CR"].findall("fields/field[name='WDT_MODE']/enumeratedValues/enumeratedValue")
        assert [(v.findtext("name"), v.findtext("value")) for v in modes] == [("ResetOnFirst", "0"), ("ResetOnSecond", "1")]
        for name, offset, access in (("NMI_ENABLE", 0x700, "read-write"),
                                    ("NMI_RAW_STATUS", 0x704, "read-only"),
                                    ("NMI_MASKED_STATUS", 0x708, "read-only")):
            reg = by_name["M_CRG"].find(f"registers/register[name='{name}']")
            assert int(reg.findtext("addressOffset"), 0) == offset
            assert reg.findtext("size") == "16" and reg.findtext("access") == access
            assert reg.find("resetValue") is None and reg.find("readAction") is None
            assert [(f.findtext("name"), f.findtext("bitOffset")) for f in reg.findall("fields/field")] == [
                ("WATCHDOG", "1"), ("XIP_CONTROL", "2"), ("XIP_CACHE", "3"), ("MDMA", "4"), ("SMDMA", "5")]
        for name, offset in (("XIP_QSPI_CLOCK", 0x574), ("XIP_CLOCK_ENABLE", 0x1240)):
            reg = by_name["M_CRG"].find(f"registers/register[name='{name}']")
            assert int(reg.findtext("addressOffset"), 0) == offset
            assert reg.findtext("size") == "16" and reg.findtext("access") == "read-write"
            assert reg.find("resetValue") is None and reg.find("resetMask") is None
    for p in peripheral_nodes:
        if base := p.get("derivedFrom"):
            import copy
            p.append(copy.deepcopy(by_name[base].find("registers")))
    weighted = [item for p in peripheral_nodes for item in weighted_registers(p.find("registers"))]
    registers = [r for r, _ in weighted]
    register_count = sum(count for _, count in weighted)
    field_count = sum(count * len(r.findall("fields/field")) for r, count in weighted)
    if register_count != expected["registers_expanded"]:
        print(
            f"{filename}: expected {expected['registers_expanded']} expanded registers, "
            f"got {register_count}"
        )
        failed = True
    if field_count != expected["fields_expanded"]:
        print(
            f"{filename}: expected {expected['fields_expanded']} expanded fields, "
            f"got {field_count}"
        )
        failed = True
    empty_registers = [r.findtext("name") for r in registers if not r.findall("./fields/field")]
    if empty_registers:
        print(f"{filename}: registers without fields: {empty_registers[:8]}")
        failed = True

    # The bootstrap importer expands SVD arrays. Keep this visible until a
    # controlled compaction pass restores the source PAC array shape.
    output_arrays = tree.findall(".//register[dim]")
    if len(output_arrays) != expected.get("output_arrays", 0):
        print(f"{filename}: unexpected register array count {len(output_arrays)}")
        failed = True
    cluster_arrays = tree.findall(".//cluster[dim]")
    if len(cluster_arrays) != expected.get("cluster_arrays", 0):
        print(f"{filename}: unexpected cluster array count {len(cluster_arrays)}")
        failed = True

    constraints = tree.findall(".//writeConstraint")
    if len(constraints) != expected["write_constraints"]:
        print(
            f"{filename}: expected {expected['write_constraints']} expanded write "
            f"constraints, got {len(constraints)}"
        )
        failed = True

    summary[filename] = {
        "peripherals": len(peripheral_nodes),
        "registers_expanded": register_count,
        "fields_expanded": field_count,
        "register_arrays": len(output_arrays),
        "cluster_arrays": len(cluster_arrays),
        "interrupts": irq_count,
        "enumerated_values": len(tree.findall(".//enumeratedValue")),
        "read_actions": len(tree.findall(".//readAction")),
        "modified_write_values": len(tree.findall(".//modifiedWriteValues")),
        "write_constraints": len(constraints),
        "known_source_arrays_not_yet_compacted": expected["source_arrays"],
    }

if failed:
    sys.exit(1)
print(json.dumps(summary, indent=2, sort_keys=True))
print("generated register maps match the full-chip bootstrap contract")
