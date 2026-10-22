#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compare generated UART slots/fields to pinned SDK C types, without MMIO."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
HAL = "src/drivers/drivers/hal/uart/v151/"
HEADER = HAL + "hal_uart_v151_regs_def.h"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    files = [HEADER, HAL + "hal_uart_v151.c", HAL + "hal_uart_v151_regs_op.c",
             "src/drivers/chips/ws53/porting/uart/uart_porting.c"]
    subprocess.run(["git", "-C", str(sdk), "diff", "--exit-code", PIN, "--", *files],
                   check=True, stdout=subprocess.DEVNULL)
    tree = ET.parse(Path(__file__).resolve().parents[1] / "generated/WS53.experimental.svd")
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
                      "hardware_verified": False}, indent=2))


if __name__ == "__main__":
    main()
