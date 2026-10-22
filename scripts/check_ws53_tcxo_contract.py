#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Optional pinned-SDK TCXO field/helper oracle; runs only on host RAM, not hardware/ROM."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
HAL = "src/drivers/drivers/hal/tcxo/"
PORT = "src/drivers/chips/ws53/porting/tcxo/"
FILES = [HAL + name for name in ("hal_tcxo.h", "hal_tcxo_v150.h", "hal_tcxo_v150.c",
         "hal_tcxo_v150_regs_op.h", "hal_tcxo_v150_regs_def.h", "hal_tcxo_v150_regs_op.c")]
FILES += [PORT + "tcxo_porting.h", PORT + "tcxo_porting.c",
          "src/drivers/chips/ws53/include/acore/platform_core.h",
          "src/drivers/chips/ws53/include/control_core/platform_core.h",
          "src/application/ws53/ws53_application/main.c",
          "src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config",
          "src/build/config/target_config/ws53/target_config.py"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    contents = {}
    for path in FILES:
        data = subprocess.check_output(["git", "-C", str(sdk), "show", f"{PIN}:{path}"])
        require(data == (sdk / path).read_bytes(), f"SDK source differs from pin: {path}")
        contents[path] = data.decode()
    port = contents[PORT + "tcxo_porting.h"]
    polls = re.search(r"^#define\s+TCXO_LOCK_GET_ATTE\s+(\S+)", port, re.M)[1]
    require(int(polls, 0) == 0xffffffff, "SDK polling budget changed")
    require("#define TCXO_TICKS_PER_U_SECOND_DEFAULT 32" in port, "clock profile changed")
    cfg = contents[FILES[-2]]
    require("# CONFIG_TCXO_WITH_TWO_DATA_REGS is not set" in cfg, "wrong data layout profile")
    require("CONFIG_TCXO_SUPPORT_LPM=y" in cfg, "sleep compensation profile changed")
    require("'tcxo_rom', 'hal_tcxo_rom', 'tcxo_port_rom'" in contents[FILES[-1]], "selected ROM components changed")
    tree = ET.parse(Path(__file__).resolve().parents[1] / "generated/WS53.experimental.svd")
    tcxo = tree.find("./peripherals/peripheral[name='TCXO']")
    require(tcxo is not None and int(tcxo.findtext("baseAddress"), 0) == 0x57000200, "TCXO base differs")
    require(not tcxo.findall("interrupt"), "unexpected TCXO IRQ")
    source = ['#include <stddef.h>', '#include <assert.h>',
              '#include ' + json.dumps(str(sdk / (HAL + "hal_tcxo_v150_regs_op.c"))),
              '#include ' + json.dumps(str(sdk / (HAL + "hal_tcxo_v150.c"))),
              'static tcxo_regs_t backing;',
              'uintptr_t tcxo_porting_base_addr_get(void) { return (uintptr_t)&backing; }']
    regs = tcxo.findall("registers/register")
    require(len(regs) == 5, "unexpected register count")
    for index, reg in enumerate(regs):
        member = "status" if index == 0 else f"count{index-1}"
        require(reg.findtext("size") == "32" and reg.find("resetValue") is None, "width/reset contract changed")
        off = int(reg.findtext("addressOffset"), 0)
        source.append(f'_Static_assert(offsetof(tcxo_regs_t, {member}) == {off}, "slot offset");')
        if index:
            require(reg.findtext("fields/field/bitWidth") == "16", "data width differs")
    source += ['int main(void) {',
        'backing.status = 0xa5000000;',
        'assert(hal_tcxo_init() == 0);',
        'assert(backing.status == 0xa5000006); /* enable + explicit clear, preserving other bits */',
        'backing.count0 = 0xaaaadef0; backing.count1 = 0xbbbb9abc;',
        'backing.count2 = 0xcccc5678; backing.count3 = 0xdddd1234;',
        'assert(hal_tcxo_reg_count_get() == UINT64_C(0x123456789abcdef0));',
        'backing.status = 0xa5000014; /* ready latch in host RAM, no latch simulation */',
        'assert(hal_tcxo_get() == UINT64_C(0x123456789abcdef0));',
        'assert(backing.status == 0xa5000015);',
        'assert(hal_tcxo_deinit() == 0);',
        'assert(g_tcxo_regs == NULL && backing.status == 0xa5000015);',
        'return 0; }']
    with tempfile.TemporaryDirectory(prefix="ws53-tcxo-sdk-") as work:
        work = Path(work)
        (work / "errcode.h").write_text('#pragma once\n#include <stdint.h>\ntypedef uint32_t errcode_t;\n'
            '#define ERRCODE_SUCC 0\n#define ERRCODE_TCXO_REG_ADDR_INVALID 1\n')
        (work / "common_def.h").write_text('#pragma once\n#include <stddef.h>\n')
        (work / "tcxo_porting.h").write_text('#pragma once\n#include <stdint.h>\n'
            'uintptr_t tcxo_porting_base_addr_get(void);\n' + f'#define TCXO_LOCK_GET_ATTE {polls}\n')
        (work / "check.c").write_text('\n'.join(source) + '\n')
        subprocess.run([args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(work),
                        str(work / 'check.c'), '-o', str(work / 'check')], check=True)
        subprocess.run([str(work / 'check')], check=True, timeout=5)
    print(json.dumps({"sdk_commit": PIN, "input_sha256": {name: hashlib.sha256(text.encode()).hexdigest()
          for name, text in contents.items()}, "register_slots": 5, "access_bits": 32,
          "data_bits_per_slot": 16, "sdk_status_poll_limit": int(polls, 0),
          "reference_ticks_per_us": 32, "checks": ["enable then clear", "reserved data halves excluded",
          "ready snapshot path", "deinit does not disable/reset hardware", "SVD width/reset/offset contracts"],
          "sdk_application_uses_rom_components": True, "oracle_is_source_not_rom_execution": True,
          "timeout_loop_executed": False, "hardware_accessed": False}, indent=2))


if __name__ == "__main__":
    main()
