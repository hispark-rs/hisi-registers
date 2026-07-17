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
        "peripherals": 13,
        "interrupts": 0,
        "addresses": {
            "WDT": 0x5702B000,
            "SFC_CFG": 0x53000000,
            "PWM": 0x52082000,
            "UART0": 0x52081000,
            "I2C0": 0x52083000,
            "I2C1": 0x52084000,
            "RTC0": 0x57029100,
            "I2S": 0x52030000,
            "TIMER": 0x52002000,
            "TCXO": 0x57000200,
            "GPIO0": 0x5700C000,
            "GPIO1": 0x57028000,
            "GPIO2": 0x57010000,
        },
        "source_arrays": 0,
        "registers_expanded": 349,
        "fields_expanded": 664,
        "write_constraints": 2,
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
    "sio_v151_regs": {"ws53": 1, "ws63": 1, "bs2x": 1},
    "uart_v151_basic_regs": {"ws53": 1, "ws63": 3, "bs2x": 3},
    "timer_v150_regs": {"ws53": 1, "ws63": 1, "bs2x": 1},
    "tcxo_v150_data16_regs": {"ws53": 1, "ws63": 1, "bs2x": 1},
    "gpio_v150_basic_regs": {"ws53": 3, "ws63": 4, "bs2x": 6},
    "i2c_v151_ws53_bs2x_regs": {"ws53": 2, "bs2x": 2},
    "rtc_v150_ws53_bs2x_regs": {"ws53": 1, "bs2x": 1},
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

    registers = tree.findall("./peripherals/peripheral/registers/register")
    fields = tree.findall("./peripherals/peripheral/registers/register/fields/field")
    if len(registers) != expected["registers_expanded"]:
        print(
            f"{filename}: expected {expected['registers_expanded']} expanded registers, "
            f"got {len(registers)}"
        )
        failed = True
    if len(fields) != expected["fields_expanded"]:
        print(
            f"{filename}: expected {expected['fields_expanded']} expanded fields, "
            f"got {len(fields)}"
        )
        failed = True
    empty_registers = [r.findtext("name") for r in registers if not r.findall("./fields/field")]
    if empty_registers:
        print(f"{filename}: registers without fields: {empty_registers[:8]}")
        failed = True

    # The bootstrap importer expands SVD arrays. Keep this visible until a
    # controlled compaction pass restores the source PAC array shape.
    output_arrays = tree.findall(".//register[dim]")
    if output_arrays:
        print(f"{filename}: unexpected partially compacted register arrays")
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
        "registers_expanded": len(registers),
        "fields_expanded": len(fields),
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
