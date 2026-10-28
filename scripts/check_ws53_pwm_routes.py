#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Check PWM0P routes against pinned SDK hardware guide and sample config."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
GUIDE = 'docs/zh-CN/hardware/SoC Wi-Fi、BLE和SLE Combo芯片 硬件用户指南/SoC Wi-Fi、BLE和SLE Combo芯片 硬件用户指南.md'
CONFIG = 'src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_daq.config'
PORT = 'src/drivers/chips/ws53/porting/pwm/pwm_porting.c'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    sdk = parser.parse_args().sdk.resolve()
    inputs = {}
    for name in (GUIDE, CONFIG, PORT):
        data = (sdk/name).read_bytes()
        pinned = subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{name}'])
        if data != pinned:
            raise ValueError(f'SDK differs from pin: {name}')
        inputs[name] = data
    rows = [html.unescape(re.sub('<[^>]*>', '', row)) for row in
            re.findall(r'<tr\b.*?</tr>', inputs[GUIDE].decode(), re.S)]
    routes = [('MGPIO10', 4), ('MGPIO21', 1), ('AGPIO2', 2)]
    for pad, mode in routes:
        matching = [row for row in rows if f'复用信号0：{pad}\n' in row]
        if len(matching) != 1 or f'复用信号{mode}：PWM0P' not in matching[0]:
            raise ValueError(f'unexpected PWM0P route: {pad}')
    config = inputs[CONFIG].decode()
    for key, value in [('CHANNEL', 0), ('GROUP_ID', 0), ('PIN', 47), ('PIN_MODE', 1)]:
        if f'CONFIG_PWM_{key}={value}\n' not in config:
            raise ValueError(f'sample configuration changed: {key}')
    print(json.dumps(dict(sdk_commit=PIN,
        input_sha256={k: hashlib.sha256(v).hexdigest() for k, v in inputs.items()},
        routes=[dict(chip_pad=pad, mux_mode=mode, signal='PWM0P') for pad, mode in routes],
        sample=dict(sdk_pin=47, mux_mode=1, channel=0, group=0),
        hardware_accessed=False, clock_tree_proven=False,
        note='Source routes only. SDK reports 32MHz; measured A-board handoff differs. '
             'See WS53 PWM0 HIL evidence; no new register layout inferred.'), indent=2))


if __name__ == '__main__':
    main()
