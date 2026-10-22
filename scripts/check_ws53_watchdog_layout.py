#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compile pinned SDK WDT declarations/helpers and compare the audited SVD views."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
HAL = "src/drivers/drivers/hal/watchdog/v151/"
PORT = "src/drivers/chips/ws53/porting/watchdog/"


def function(source, name):
    # These two leaf helpers have no nested braces. Compile their original bodies.
    return re.search(rf"STATIC void {name}\(void\)\s*\{{[^{{}}]*\}}", source)[0].replace("STATIC", "static", 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    files = [HAL + x for x in ("hal_watchdog_v151_regs_def.h", "hal_watchdog_v151_regs_op.h", "hal_watchdog_v151.c")]
    files += [PORT + "watchdog_porting.c", PORT + "watchdog_porting.h",
              "src/drivers/chips/ws53/include/acore/platform_core.h",
              "src/drivers/chips/ws53/include/acore/chip_core_irq.h",
              "src/application/ws53/ws53_application/main.c",
              "src/kernel/non_os/non_os/non_os.h", "src/kernel/non_os/non_os/non_os_nmi.c",
              "src/drivers/chips/ws53/rom/app/kernel/non_os/non_os/non_os.h",
              "src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config"]
    subprocess.run(["git", "-C", str(sdk), "diff", "--exit-code", PIN, "--", *files],
                   check=True, stdout=subprocess.DEVNULL)
    contents = {f: (sdk / f).read_text() for f in files}
    assert "CONFIG_WATCHDOG_USING_V151=y" in contents[files[-1]]
    platform = contents[files[5]]
    base = int(re.search(r"#define\s+CHIP_WDT_BASE_ADDRESS\s+(0x[\da-fA-F]+)", platform)[1], 0)
    tree = ET.parse(Path(__file__).resolve().parents[1] / "generated/WS53.experimental.svd")
    wdt = tree.find("./peripherals/peripheral[name='WDT']")
    assert int(wdt.findtext("baseAddress"), 0) == base
    assert not wdt.findall("interrupt"), "Do not invent a local IRQ from the control-core B_WDT IRQ"
    source = ['#include <stddef.h>', '#include <stdint.h>',
              '#include ' + json.dumps(str(sdk / files[1])),
              'uintptr_t g_watchdog_regs;', 'static watchdog_v151_regs_t backing;']
    hal = contents[files[2]]
    for macro in ("HAL_WDT_RESTART_KEY", "ENABLE"):
        source.append(re.search(rf"^#define\s+{macro}\s+[^\n]+", hal, re.M)[0])
    source += [function(hal, "hal_watchdog_v151_kick"), function(hal, "hal_watchdog_v151_clear_interrupt")]
    fields = []
    regs = wdt.findall("registers/register")
    assert len(regs) == 13
    aliases = {"LOCK_STATUS": "wdt_lock", "LOCK_COMMAND": "wdt_lock",
               "CCVR_STATUS": "wdt_ccvr_en", "CCVR_REQUEST": "wdt_ccvr_en"}
    field_aliases = {"KEY": "wdt_lock", "VALID": "wdt_ccvr_lock", "REQUEST": "wdt_ccvr_req"}
    for reg in regs:
        name = reg.findtext("name")
        member = aliases.get(name, name.lower())
        offset = int(reg.findtext("addressOffset"), 0)
        assert reg.findtext("size") == "32" and reg.find("resetValue") is None
        source += [f'_Static_assert(offsetof(watchdog_v151_regs_t, {member}) == {offset}, "{name} offset");',
                   f'_Static_assert(sizeof(((watchdog_v151_regs_t *)0)->{member}) == 4, "{name} access width");']
        if name == "LOCK_STATUS":
            # SDK union is a 32-bit key, status encoding is documented separately.
            assert reg.findtext("fields/field/bitWidth") == "1"
            continue
        union = member.replace("wdt_", "wdt_v151_", 1) + "_data_t"
        for f in reg.findall("fields/field"):
            sdk_field = field_aliases.get(f.findtext("name"), f.findtext("name").lower())
            width, low = int(f.findtext("bitWidth")), int(f.findtext("bitOffset"))
            value = (1 << width) - 1
            fields.append(f'{{ {union} v = {{ .d32 = 0 }}; v.b.{sdk_field} = {value}u; '
                          f'if (v.d32 != {value << low}u) return {len(fields) + 1}; }}')
    source += ['_Static_assert(sizeof(watchdog_v151_regs_t) == 0x2c, "WDT span");',
               'int main(void) {', *fields,
               'g_watchdog_regs = (uintptr_t)&backing;',
               'hal_watchdog_v151_kick();',
               'if (backing.wdt_restart != 0x5a5a5a5a) return 100;',
               'hal_watchdog_wdt_lock_set(0x5a5a5a5a);',
               'if (backing.wdt_lock != 0x5a5a5a5a) return 101;',
               'backing.wdt_lock = 1;',
               'if (hal_watchdog_wdt_lock_get() != 1) return 102;',
               'hal_watchdog_v151_clear_interrupt();',
               'if (backing.wdt_eoi != 1) return 103;',
               'hal_watchdog_wdt_ccvr_en_set_wdt_ccvr_req(1);',
               'if (backing.wdt_ccvr_en != 1) return 104;',
               'backing.wdt_ccvr_en = 2;',
               'if (hal_watchdog_wdt_ccvr_en_get_wdt_ccvr_lock() != 1) return 105;',
               'return 0; }']
    with tempfile.TemporaryDirectory(prefix="ws53-wdt-sdk-") as work:
        work = Path(work)
        # Stub only the unused port dependency; original register definitions and
        # operation helpers are compiled untouched. Local RAM is not an IP model.
        (work / "watchdog_porting.h").write_text("#include <stdint.h>\n")
        binary = work / "layout"
        subprocess.run([args.cc, "-std=c11", "-Wall", "-Werror", "-I", str(work), "-x", "c", "-", "-o", str(binary)],
                       input="\n".join(source), text=True, check=True)
        subprocess.run([str(binary)], check=True)
    clock = int(re.search(r"#define\s+CONFIG_WDT_CLOCK\s+(\d+)", contents[files[4]])[1])
    nmi = {f: int(re.search(r"NMI_CWDT\s*=\s*(\d+)", contents[f])[1]) for f in (files[8], files[10])}
    assert set(nmi.values()) == {1, 2}, "Revisit NMI evidence: SDK headers changed"
    print(json.dumps({"sdk_commit": PIN, "source_sha256": {f: hashlib.sha256((sdk/f).read_bytes()).hexdigest() for f in files},
                      "base": hex(base), "physical_register_slots": 11, "register_view_checks": len(regs),
                      "field_encoding_checks": len(fields), "compiled_sdk_helpers": ["kick", "lock", "EOI read/write", "counter latch"],
                      "restart_key": "0x5a5a5a5a", "sdk_default_clock_hz": clock,
                      "nmi_header_values": nmi, "nmi_route_verified": False, "clock_verified": False,
                      "local_ram_checks_are_not_peripheral_behavior": True, "hardware_verified": False}, indent=2))


if __name__ == "__main__":
    main()
