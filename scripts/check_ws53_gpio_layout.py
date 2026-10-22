#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Check GPIO basic-bank SVD slots against the pinned original SDK C type."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
HAL = "src/drivers/drivers/hal/gpio/v150/"
PORT = "src/drivers/chips/ws53/porting/"
CONFIG = "src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    sources = [HAL + f for f in ("hal_gpio_v150_regs_def.h", "hal_gpio_v150_regs_op.h",
                "hal_gpio_v150.c", "hal_gpio_v150_comm.c", "hal_gpio_v150_regs_op.c")]
    sources += [PORT + "gpio/gpio_porting.c", PORT + "include/gpio_porting.h",
                "src/drivers/chips/ws53/include/acore/platform_core.h", CONFIG]
    subprocess.run(["git", "-C", str(sdk), "diff", "--exit-code", PIN, "--", *sources],
                   check=True, stdout=subprocess.DEVNULL)
    assert "# CONFIG_GPIO_SUPPORT_MULTISYSTEM is not set" in (sdk / CONFIG).read_text()
    port = (sdk / (PORT + "include/gpio_porting.h")).read_text()
    platform = (sdk / sources[-2]).read_text()
    tree = ET.parse(Path(__file__).resolve().parents[1] / "generated/WS53.experimental.svd")
    program = ["#include <stddef.h>", "#include " + json.dumps(str(sdk / sources[0]))]
    views = 0
    banks = []
    for bank in range(3):
        p = tree.find(f"./peripherals/peripheral[name='GPIO{bank}']")
        base = int(re.search(rf"#define\s+GPIO{bank}_BASE_ADDR\s+(0x[\da-fA-F]+)", platform)[1], 0)
        width = int(re.search(rf"#define\s+GPIO_CHANNEL_{bank}_GROUP_0_PIN_NUM\s+(\d+)", port)[1])
        assert int(p.findtext("baseAddress"), 0) == base
        regs = p.findall("registers/register")
        assert len(regs) == 14
        for reg in regs:
            name = reg.findtext("name").lower()
            offset = int(reg.findtext("addressOffset"), 0)
            bits = int(reg.findtext("size"))
            program += [f'_Static_assert(offsetof(gpio_group_regs_t, {name}) == {offset}, "offset {name}");',
                        f'_Static_assert(sizeof(((gpio_group_regs_t *)0)->{name}) * 8 == {bits}, "width {name}");']
            assert reg.findtext("fields/field/bitOffset") == "0"
            assert int(reg.findtext("fields/field/bitWidth")) == width
            assert reg.find("resetValue") is None
            views += 1
        banks.append({"name": f"GPIO{bank}", "base": hex(base), "bit_span": width})
    program += ['_Static_assert(sizeof(gpio_group_regs_t) == 0x40, "basic group stride");',
                '_Static_assert(offsetof(gpio_v150_regs_t, gpio_lock) == 0x200, "unmodeled lock");',
                '_Static_assert(offsetof(gpio_v150_regs_t, gpio_lp_state) == 0x204, "unmodeled LP state");',
                'int main(void) { return 0; }']
    with tempfile.TemporaryDirectory(prefix="ws53-gpio-sdk-") as work:
        binary = Path(work) / "layout"
        subprocess.run([args.cc, "-std=c11", "-Wall", "-Werror", "-x", "c", "-", "-o", str(binary)],
                       input="\n".join(program), text=True, check=True)
        subprocess.run([str(binary)], check=True)
    print(json.dumps({"sdk_commit": PIN, "source_files": sources, "banks": banks,
                      "register_offset_width_checks": views, "basic_group_stride": 64,
                      "multisystem": False, "lock_and_lp_semantics_modeled": False,
                      "width_is_not_bonded_pad_count": True, "hardware_verified": False}, indent=2))


if __name__ == "__main__":
    main()
