"""Optional local SDK comparison; ordinary generation/build does not require SDK.

Run: uv run --locked scripts/check_ws53_sdk_layout.py --sdk /path/to/fbb_ws53
Compare ADC/diagnostic C offsetof and TSENSOR addresses with the pinned SDK.
"""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
HEADER = "src/drivers/drivers/hal/adc/v155/hal_adc_v155_regs_def.h"
TSENSOR_SOURCE = "src/drivers/chips/ws53/porting/tsensor/tsensor_porting.c"
MAPPINGS = {
    "ADC": ("adc_regs_t", {
        "SCAN_CONTROL": "lsadc_ctrl_0", "SCAN_COMMAND": "lsadc_ctrl_8",
        "ANALOG_CONTROL": "lsadc_ctrl_11", "MANUAL_CONTROL": "lsadc_ctrl_12",
        "OFFSET_CALIBRATION": "cfg_adc_offset_cali", "OFFSET_ENABLE": "cfg_adc_offset_cali_en",
        "OFFSET_DATA_ENABLE": "cfg_adc_offset_cali_data_en",
        "OFFSET_REFRESH": "cfg_adc_offset_cali_data_spi_refresh",
        "OFFSET_STATE_CLEAR": "cfg_adc_offset_cali_state_clr",
        "OFFSET_STATUS": "rpt_adc_offset_cali_finish_sts", "OFFSET_RESULT": "rpt_adc_offset_cali_data",
        "CAP_CALIBRATION": "cfg_adc_cap_cali", "CAP_ENABLE": "cfg_adc_cap_cali_en",
        "CAP_REFRESH": "cfg_cap_cali_data_spi_refresh", "CAP_FINISH_CLEAR": "cfg_cap_cali_finish_clr",
        "GAIN_STATE_CLEAR": "cfg_intr_gain_state_clr", "CAP_STATUS": "rpt_cap_cali_sts_0",
    }),
    "MCU_DIAG": ("mcu_diag_regs_t", {
        "MONITOR_SELECT": "cfg_monitor_sel", "TRACE_SAVE_SELECT": "cfg_mcu_diag_trace_save_sel",
        "CPU_TRACE": "cfg_mcu_diag_cpu_trace", "MONITOR_CLOCK": "cfg_mcu_diag_monitor_clock",
        "SAMPLE_SELECT": "cfg_mcu_diag_sample_sel", "SAMPLE_MODE": "cfg_mcu_diag_sample_mode",
        "LENGTH_LOW": "cfg_mcu_diag_sample_length_l", "LENGTH_HIGH": "cfg_mcu_diag_sample_length_h",
        "START_LOW": "cfg_mcu_diag_sample_start_addr_l", "START_HIGH": "cfg_mcu_diag_sample_start_addr_h",
        "END_LOW": "cfg_mcu_diag_sample_end_addr_l", "END_HIGH": "cfg_mcu_diag_sample_end_addr_h",
        "DONE_ADDRESS_LOW": "mcu_diag_sample_done_addr_l", "DONE_ADDRESS_HIGH": "mcu_diag_sample_done_addr_h",
        "SAMPLE_DONE": "mcu_diag_sample_done",
    }),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cc", default="cc", help="C compiler executable (no shell)")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    subprocess.run(["git", "-C", str(sdk), "diff", "--exit-code", PIN, "--", HEADER, TSENSOR_SOURCE],
                   check=True, stdout=subprocess.DEVNULL)
    root = Path(__file__).resolve().parents[1]
    tree = ET.parse(root / "generated/WS53.experimental.svd")
    source = ['#include <stddef.h>', '#include <stdint.h>', 'typedef uint32_t errcode_t;',
              '#include ' + json.dumps(str(sdk / HEADER))]
    checks = 0
    for block, (ctype, mappings) in MAPPINGS.items():
        registers = {r.findtext("name"): r for r in tree.findall(
            f"./peripherals/peripheral[name='{block}']/registers/register")}
        for name, member in mappings.items():
            offset = int(registers[name].findtext("addressOffset"), 0)
            source.append(f'_Static_assert(offsetof({ctype}, {member}) == {offset}, "{block}.{name}");')
            checks += 1
        if block == "ADC":
            array = registers["ANALOG_CONFIG[%s]"]
            if int(array.findtext("dim")) != 3:
                raise ValueError("unexpected analog configuration array size")
            for index in range(3):
                offset = int(array.findtext("addressOffset"), 0) + index * int(array.findtext("dimIncrement"), 0)
                source.append(f'_Static_assert(offsetof(adc_regs_t, da_lsadc_rwreg_{index + 1}) == {offset}, "analog array");')
                checks += 1
    with tempfile.TemporaryDirectory(prefix="ws53-sdk-layout-") as work:
        subprocess.run([args.cc, "-x", "c", "-std=c11", "-Wall", "-Werror", "-fsyntax-only", "-"],
                       input="\n".join(source), text=True, check=True,
                       env={**os.environ, "CCACHE_DIR": work})
    tsensor = tree.find("./peripherals/peripheral[name='TSENSOR']")
    base = int(tsensor.findtext("baseAddress"), 0)
    c_addresses = dict(re.findall(r"^#define\s+(TSENSOR_\w+)\s+(0x[0-9A-Fa-f]+)\s*$",
                                 (sdk / TSENSOR_SOURCE).read_text(), re.MULTILINE))
    address_checks = 0
    for name, macro in {"START": "TSENSOR_START", "CONTROL": "TSENSOR_CTRL",
                        "AUTO_STATUS": "TSENSOR_AUTO_STS", "SAMPLE_TIME": "TSENSOR_CTRL1",
                        "AUTO_REFRESH_PERIOD": "TSENSOR_AUTO_REFRESH_PERIOD",
                        "AUTO_REFRESH_CONFIG": "TSENSOR_AUTO_REFRESH_CFG"}.items():
        offset = int(tsensor.findtext(f"registers/register[name='{name}']/addressOffset"), 0)
        if base + offset != int(c_addresses[macro], 0):
            raise ValueError(f"TSENSOR.{name} differs from SDK {macro}")
        address_checks += 1
    print(json.dumps({"sdk_commit": PIN, "header": HEADER, "offset_checks": checks,
                      "tsensor_source": TSENSOR_SOURCE, "tsensor_address_checks": address_checks,
                      "layout_verified": True, "hardware_verified": False}, indent=2))


if __name__ == "__main__":
    main()
