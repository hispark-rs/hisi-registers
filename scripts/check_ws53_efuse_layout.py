#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Optional pinned-SDK eFuse layout check; compiles/runs address arithmetic only.

No MMIO, device access or SDK dependency is introduced into ordinary generation.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
PORT = "src/drivers/chips/ws53/porting/efuse/efuse_porting"
HAL = "src/drivers/drivers/hal/efuse/v151/hal_efuse_v151"


def function(source, name):
    match = re.search(r"^(?:static )?(?:uint32_t|uint16_t|hal_efuse_region_t) "
                      + name + r"\([^)]*\)\n\{.*?^\}", source, re.M | re.S)
    if not match:
        raise ValueError(f"pinned SDK function not found: {name}")
    return match[0]


def define(source, name):
    match = re.search(r"^#define\s+" + name + r"\s+[^\n]+", source, re.M)
    if not match:
        raise ValueError(f"pinned SDK definition not found: {name}")
    return match[0]


def global_address(source, name):
    match = re.search(r"^uint32_t " + name + r"(?:\[1\])?\s*=.*?;", source, re.M)
    if not match:
        raise ValueError(f"pinned SDK address not found: {name}")
    return match[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    files = [PORT + ".c", PORT + ".h", HAL + ".c", HAL + "_reg_def.h"]
    subprocess.run(["git", "-C", str(sdk), "diff", "--exit-code", PIN, "--", *files],
                   check=True, stdout=subprocess.DEVNULL)
    tree = ET.parse(Path(__file__).resolve().parents[1] / "generated/WS53.experimental.svd")
    peripheral = tree.find("./peripherals/peripheral[name='EFUSE']")
    base = int(peripheral.findtext("baseAddress"), 0)
    regs = {r.findtext("name"): r for r in peripheral.findall("registers/register")}
    data = regs["DATA_READ[%s]"]
    offset = int(data.findtext("addressOffset"), 0)
    count = int(data.findtext("dim"))
    stride = int(data.findtext("dimIncrement"), 0)
    if count != 128 or stride != 4 or data.findtext("size") != "32":
        raise ValueError("unexpected OTP read-window shape")
    port, hal = ((sdk / (name + ".c")).read_text() for name in (PORT, HAL))
    source = ["#include <stdint.h>", "#include <stddef.h>",
              '#include ' + json.dumps(str(sdk / (PORT + ".h"))),
              '#include ' + json.dumps(str(sdk / (HAL + "_reg_def.h")))]
    source += [define(port, name) for name in ("EFUSE0_BASE_ADDR", "HAL_EFUSE0_READ_BASE_ADDR")]
    source += [define(hal, name) for name in ("HAL_EFUSE_REG_SHIFT", "HAL_EFUSE_READ_MODE")]
    source += [global_address(port, name) for name in
               ("g_efuse_region_read_address", "g_efuse_base_addr", "g_efuse_boot_done_addr")]
    source += [function(port, "hal_efuse_get_region"), function(port, "hal_efuse_get_byte_offset"),
               function(hal, "hal_efuse_get_writeread_addr"),
               f'_Static_assert(EFUSE0_BASE_ADDR == {base}, "base");',
               '_Static_assert(EFUSE_REGION_NUM == 1 && EFUSE_MAX_BYTES == 256, "region");',
               '_Static_assert(sizeof(efuse_sts_t) == 4 && sizeof(efuse_ctl_t) == 4, "access width");']
    for bound in ("minimum", "maximum"):
        value = int(regs["READ_MODE_COMMAND"].findtext("fields/field/writeConstraint/range/" + bound))
        source.append(f'_Static_assert(HAL_EFUSE_READ_MODE == {value}, "read mode {bound}");')
    source.append("int main(void) {")
    boot = base + int(regs["BOOT_STATUS"].findtext("addressOffset"), 0)
    source.append(f"if (g_efuse_boot_done_addr != {boot}u) return 3;")
    for name, member in (("MODE_STATUS", "efuse_ctl_data"), ("READ_MODE_COMMAND", "efuse_ctl_data"),
                         ("CLOCK_PERIOD", "efuse_clk_period_data")):
        address = base + int(regs[name].findtext("addressOffset"), 0)
        source.append(f"if (g_efuse_base_addr[0] + offsetof(efuse_regs_t, {member}) != {address}u) return 4;")
    source.append(f"for (uint32_t b = 0; b < EFUSE_MAX_BYTES; b++) {{\n"
                  f"  if (hal_efuse_get_writeread_addr(b) != {base + offset}u + (b / 2) * {stride}) return 1;\n"
                  "}")
    for field, member, value in (("MANUAL_STATUS", "efuse_man_sts", 3),
                                 ("BOOT0_DONE", "efuse_boot0_done", 1),
                                 ("BOOT1_DONE", "efuse_boot1_done", 1),
                                 ("BOOT2_DONE", "efuse_boot2_done", 1)):
        shift = int(regs["BOOT_STATUS"].findtext(f"fields/field[name='{field}']/bitOffset"))
        source.append(f"{{ efuse_sts_t s = {{.d32 = 0}}; s.b.{member} = {value}; "
                      f"if (s.d32 != {value << shift}u) return 2; }}")
    source.append("return 0; }")
    with tempfile.TemporaryDirectory(prefix="ws53-efuse-layout-") as work:
        binary = str(Path(work) / "check")
        subprocess.run([args.cc, "-x", "c", "-std=c11", "-Wall", "-Werror", "-o", binary, "-"],
                       input="\n".join(source), text=True, check=True)
        subprocess.run([binary], check=True)
    print(json.dumps({"sdk_commit": PIN, "files": files, "logical_byte_addresses_checked": 256,
                      "status_fields_checked": 4, "layout_verified": True,
                      "hardware_verified": False, "mmio_performed": False}, indent=2))


if __name__ == "__main__":
    main()
