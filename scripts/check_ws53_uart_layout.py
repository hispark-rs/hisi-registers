#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compare generated UART slots/fields to pinned SDK C types, without MMIO."""
import argparse
import json
import re
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
HAL = "src/drivers/drivers/hal/uart/v151/"
HEADER = HAL + "hal_uart_v151_regs_def.h"
PORT = "src/drivers/chips/ws53/porting/uart/uart_porting.c"


def check_port_controls(sdk, tree):
    """Check reset constants against the actual port, not the legacy clock enum."""
    source = (sdk / PORT).read_text()
    definitions = dict(re.findall(r"^#define\s+(UART_SOFT_RST_\w+)\s+(0[xX][0-9a-fA-F]+|[0-9]+)\b", source, re.M))
    reset_checks = 0
    for block, address, names in (
        ("M_CRG", "UART_SOFT_RST_A", (("UART_H0_RESET_N", "H0"), ("UART_H1_RESET_N", "H1"))),
        ("GLB_CTL", "UART_SOFT_RST_C", (("UART_L0_RESET_N", "L0"),)),
    ):
        peripheral = tree.find(f"./peripherals/peripheral[name='{block}']")
        reg = peripheral.find("registers/register[name='UART_SOFT_RESET']")
        assert int(peripheral.findtext("baseAddress"), 0) + int(reg.findtext("addressOffset"), 0) == int(definitions[address], 0)
        assert reg.findtext("size") == "16" and reg.findtext("access") == "read-write"
        for field, name in names:
            assert int(reg.findtext(f"fields/field[name='{field}']/bitOffset")) == int(definitions[f"UART_SOFT_RST_{name}_POS"], 0)
            reset_checks += 1
    assert re.search(r"uapi_reg_clrbit16\(reg, pos\);\s*uapi_reg_setbit16\(reg, pos\);", source)
    assert re.search(r"if \(bus == g_sw_debug_uart\)\s*\{\s*return;\s*\}", source)
    route = tree.find("./peripherals/peripheral[name='GLB_CTL']/registers/register[name='UART_L0_IRQ_ROUTE_COMMAND']")
    address = 0x57000000 + int(route.findtext("addressOffset"), 0)
    value = int(route.findtext("fields/field/writeConstraint/range/minimum"))
    assert value == int(route.findtext("fields/field/writeConstraint/range/maximum"))
    assert re.search(rf"writel\(0x{address:x},\s*0x{value:x}\)", source, re.I)
    assert route.findtext("access") == "write-only" and route.findtext("size") == "32"
    return {"reset_field_checks": reset_checks, "reset_access_bits": 16,
            "reset_sequence": "clear then set; debug UART bypasses reset",
            "routing_command": {"address": hex(address), "word": value},
            "clock_tree_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    files = [HEADER, HAL + "hal_uart_v151.c", HAL + "hal_uart_v151_regs_op.c",
             PORT, "src/include/common_def.h",
             "src/drivers/chips/ws53/include/acore/platform_core.h",
             "src/drivers/chips/ws53/include/clock_recover.h",
             "src/drivers/chips/ws53/include/hal_clocks_application_private.h",
             "src/drivers/chips/ws53/clock_recover/clock_recover.c",
             "src/drivers/drivers/driver/uart/uart.c",
             "src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config",
             "src/application/ws53/ws53_application/main.c"]
    subprocess.run(["git", "-C", str(sdk), "diff", "--exit-code", PIN, "--", *files],
                   check=True, stdout=subprocess.DEVNULL)
    tree = ET.parse(Path(__file__).resolve().parents[1] / "generated/WS53.experimental.svd")
    controls = check_port_controls(sdk, tree)
    uart = tree.find("./peripherals/peripheral[name='UART0']")
    source = ['#include <stddef.h>', '#include <stdint.h>',
              '#include ' + json.dumps(str(sdk / HEADER))]
    fields = []
    registers = uart.findall("registers/register")
    for reg in registers:
        name = reg.findtext("name")
        member = "data" if name in ("RX", "TX") else name.lower()
        offset = int(reg.findtext("addressOffset"), 0)
        bits = int(reg.findtext("size"))
        source += [f'_Static_assert(offsetof(uart_reg_t, {member}) == {offset}, "{name} offset");',
                   f'_Static_assert(sizeof(((uart_reg_t *)0)->{member}) * 8 == {bits}, "{name} width");']
        for field in reg.findall("fields/field"):
            field_name = field.findtext("name").lower()
            sdk_field = field_name
            if name == "FIFO_STATUS":
                sdk_field = {"rx_not_empty": "rx_fifo_full", "rx_full": "rx_fifo_empty"}.get(field_name, field_name)
            width = int(field.findtext("bitWidth"))
            low = int(field.findtext("bitOffset"))
            value = (1 << width) - 1
            fields.append(f'{{ {member}_t v = {{.d32 = 0}}; v.{member}.{sdk_field} = {value}u; '
                          f'if (v.d32 != {value << low}u) return {len(fields) + 1}; }}')
    source += ["int main(void) {", *fields, "return 0; }"]
    with tempfile.TemporaryDirectory(prefix="ws53-uart-sdk-") as work:
        work = Path(work)
        # Stub only the platform types imported by the register header. The
        # complete original register/union definitions are compiled unchanged.
        (work / "uart_porting.h").write_text("#include <stdint.h>\n#define UART_BUS_MAX_NUMBER 3\ntypedef uint32_t uart_bus_t;\n")
        binary = work / "layout"
        subprocess.run([args.cc, "-std=c11", "-Wall", "-Werror", "-I", str(work),
                        "-x", "c", "-", "-o", str(binary)], input="\n".join(source),
                       text=True, check=True)
        subprocess.run([str(binary)], check=True)
    print(json.dumps({"sdk_commit": PIN, "source_files": files,
                      "register_offset_and_width_checks": len(registers),
                      "field_encoding_checks": len(fields), "layout_verified": True,
                      "port_controls": controls,
                      "hardware_verified": False}, indent=2))


if __name__ == "__main__":
    main()
