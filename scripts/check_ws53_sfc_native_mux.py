#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned WS53 native SFC mux source contracts on host RAM only.

This establishes six boot writes and the *absence* of a generic mode getter
for raw pin numbers 24..29. It deliberately does not create register access
metadata, field widths, reset values, board pin identities, or a safe read API.
Original function bodies, enums, complete HAL maps, and port mode masks are
extracted unchanged. Only volatile address primitives are redirected/logged;
their original macro bodies still perform the access on anonymous host objects.
The temporary harness contains SDK fragments and must not be published.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = '163d9150ab94a02d09a05f623351f9facee3025e'
BOOT = 'src/bootloader/flashboot_ws53/startup/main.c'
HAL = 'src/drivers/drivers/hal/pinmux/ws53/hal_pinctrl_ws53.c'
PORT = 'src/drivers/chips/ws53/porting/pinctrl/pinctrl_porting.c'
MODES = 'src/drivers/chips/ws53/porting/include/pinctrl_porting.h'
ACORE = 'src/drivers/chips/ws53/include/acore/platform_core.h'
CCORE = 'src/drivers/chips/ws53/include/control_core/platform_core.h'
IO = 'src/middleware/utils/common_headers/chip_io.h'
BASE = 'src/include/common_def.h'
ERROR = 'src/include/errcode.h'
ADDRESSES = list(range(0x52008060, 0x52008078, 4))
# Independent expected map for positive controls; not generated from g_pin_mode_map.
MODE_ADDRESSES = {**{i: 0x52008000 + 4*i for i in range(23)},
    32: 0x570360bc, **{i: 0x57036004 + 4*(i-33) for i in range(33, 38)},
    38: 0x570360b8, 39: 0x570360c4, 40: 0x570360c8, 41: 0x570360c0,
    42: 0x57036018, 43: 0x5703601c, 45: 0x52008038, 46: 0x5200803c,
    47: 0x52008054}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def function(source, name):
    found = re.search(r'^(?:STATIC |static )?[\w* ]+\b' + name + r'\s*\([^;]*?\)\s*\{', source, re.M)
    require(found is not None, 'original function missing: ' + name)
    depth, end = 1, found.end()
    while depth:
        require(end < len(source), 'unterminated original function: ' + name)
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[found.start():end]


def declaration(source, kind, name):
    pattern = (r'typedef ' + kind + r'\s*\{[^{}]*\}\s*' + name + r';')
    found = re.search(pattern, source, re.S)
    require(found is not None, 'original declaration missing: ' + name)
    return found[0]


def macro(source, name):
    found = re.search(r'^#define\s+' + name + r'\b[^\n]*(?:\\\n[^\n]*)*', source, re.M)
    require(found is not None, 'original macro missing: ' + name)
    return found[0]


def validate_svd(path):
    def number(node, name, default=None):
        values = node.findall(name)
        require(len(values) <= 1, 'ambiguous register geometry: ' + name)
        value = values[0].text if values else default
        require(isinstance(value, str) and bool(value.strip()), 'missing register geometry: ' + name)
        return int(value, 0)

    tree = ET.parse(path)
    blocks = tree.findall("./peripherals/peripheral[name='PINMUX_MCU']")
    require(len(blocks) == 1, 'expected exactly one PINMUX_MCU')
    block = blocks[0]
    require(number(block, 'baseAddress') == 0x52008000,
            'unexpected PINMUX_MCU base')
    # This guard understands only this exporter's explicit flat layout. Never
    # silently skip nested/inherited views and then claim the native slots absent.
    require(not any(node.tag == 'cluster' or 'derivedFrom' in node.attrib for node in block.iter()),
            'unsupported cluster or derivedFrom in PINMUX_MCU')
    containers = block.findall('registers')
    require(len(containers) == 1 and len(containers[0]) > 0,
            'missing or ambiguous PINMUX_MCU register layout')
    require(all(reg.tag == 'register' for reg in containers[0]),
            'unsupported PINMUX_MCU register container child')
    occupied = []
    for reg in containers[0]:
        width = number(reg, 'size')
        offset = number(reg, 'addressOffset')
        count = number(reg, 'dim', '1')
        stride = number(reg, 'dimIncrement', '0')
        require(width in (8, 16, 32, 64), 'missing or unsupported explicit register width')
        require(offset >= 0 and 1 <= count <= 65536 and stride >= 0,
                'invalid register offset or array geometry')
        require(count == 1 or stride >= width // 8, 'missing/overlapping array stride')
        require(0x52008000 + offset + (count-1)*stride + width//8 <= 0x100000000,
                'register geometry outside 32-bit address space')
        for index in range(count):
            start = 0x52008000 + offset + index * stride
            occupied.append((start, start + width // 8))
    require(all(not any(start < address+4 and address < end for start, end in occupied)
                for address in ADDRESSES), 'unqualified native mux view added')
    return dict(sha256=sha(path.read_bytes()), native_mux_views_absent=True)


def validate_records(records):
    """Independent strict oracle for the compiler-produced access transcript."""
    expected = []
    for trial in range(2):
        for address in ADDRESSES:
            expected.append(dict(kind='write', trial=trial, address=address, width=32, value=1))
    for pin in range(48):
        if pin in MODE_ADDRESSES:
            for value in (0, 0xffffffff, 0xa5a55a5d):
                for call in ('raw-get', 'mode-get'):
                    expected.append(dict(kind='read', pin=pin, call=call, address=MODE_ADDRESSES[pin],
                                         width=32, value=value, result=value & 7))
        else:
            expected.append(dict(kind='rejected', pin=pin, raw_error=0xffffffff,
                                 output_unchanged=True, mode_result=8, accesses=0))
    for pin in range(24, 30):
        expected.append(dict(kind='mode-mask', pin=pin, mask=0, rejected_modes=list(range(8))))
    require(records == expected, 'original helper access transcript differs from strict contract')
    return dict(boot_write_count=12, getter_read_count=len(MODE_ADDRESSES)*6,
                rejected_pin_count=48-len(MODE_ADDRESSES), native_rejected_pins=list(range(24, 30)),
                native_mode_rejections=48, valid_raw_table_slots=len(MODE_ADDRESSES))


def harness(sources):
    original = {'sfc_pin_mode_set': function(sources[BOOT], 'sfc_pin_mode_set')}
    for name in ('hal_pin_get_config_map', 'hal_pin_get_config_group',
                 'hal_pin_get_config_val', 'hal_pin_ws53_get_mode'):
        original[name] = function(sources[HAL], name)
    original['pin_check_mode_is_valid'] = function(sources[PORT], 'pin_check_mode_is_valid')
    # Complete original map/config declaration block, including optional IE/ST maps.
    maps = sources[HAL][sources[HAL].index('#define HAL_MCU_PINMUX_CTL_RB_BASE_ADDR'):
                        sources[HAL].index('STATIC errcode_t hal_pin_get_config_map')]
    port_start = sources[PORT].index('#ifndef bit')
    port_end = sources[PORT].index('\nvoid get_pio_func_config')
    port_maps = sources[PORT][port_start:port_end]
    require('g_pio_pins_avaliable_mode[PIN_MAX_NUMBER]' in port_maps, 'port masks missing')
    for source in (ACORE, CCORE):
        for pin in range(24, 30):
            require(re.search(r'//\s*' + str(pin) + r'\s+S_MGPIO' + str(pin) + r'\s+not pin out', sources[source]),
                    'SDK not-pin-out statement changed')
    # Rename only each primitive macro identifier, preserving its original body.
    primitive_write = macro(sources[IO], 'writel').replace('writel', 'SDK_writel', 1)
    primitive_read = macro(sources[BASE], 'uapi_reg_read_val32').replace('uapi_reg_read_val32', 'SDK_read32', 1)
    code = [r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stddef.h>
#define STATIC static
''', '#include ' + json.dumps(str(sources['_sdk']/ERROR)),
        declaration(sources[ACORE], 'enum', 'pin_t'),
        macro(sources[ACORE], 'PIN_MAX_NUMBER'),
        declaration(sources[MODES], 'enum', 'pin_mode_t'),
        primitive_write, primitive_read, r'''
_Static_assert(sizeof(SDK_writel(0, 0)) == 4, "original boot write is u32");
_Static_assert(sizeof(SDK_read32(0)) == 4, "original getter primitive is u32");
static uint32_t slots[8];
static uint32_t seed;
static unsigned accesses, trial, current_pin;
static const char *current_call;
static void host_write(uintptr_t address, uint32_t value) {
    assert(address >= 0x52008060 && address <= 0x52008074 && (address & 3) == 0);
    SDK_writel(&slots[1 + (address-0x52008060)/4], value);
    printf("W %u %08x 32 %08x\n", trial, (unsigned)address, value);
    accesses++;
}
static uint32_t host_read(uintptr_t address) {
    assert((address & 3) == 0);
    assert((address >= 0x52008000 && address <= 0x52008058) ||
           (address >= 0x57036004 && address <= 0x570360c8));
    uint32_t result = SDK_read32(&seed);
    printf("R %u %s %08x 32 %08x %u\n", current_pin, current_call,
           (unsigned)address, result, result & 7);
    accesses++;
    return result;
}
#define writel(addr, value) host_write((addr), (value))
#define uapi_reg_read_val32(addr) host_read(addr)
''', macro(sources[BASE], 'uapi_reg_getbits'), maps, port_maps,
        *original.values(), r'''
int main(void) {
    for (trial=0; trial<2; trial++) {
        for (unsigned j=0; j<8; j++) slots[j] = trial ? 0xffffffff : 0xa5a55a5a;
        uint32_t before = slots[0]; accesses = 0;
        sfc_pin_mode_set();
        assert(accesses == 6 && slots[0] == before && slots[7] == before);
        for (unsigned j=1; j<7; j++) assert(slots[j] == 1);
    }
    for (current_pin=0; current_pin<PIN_MAX_NUMBER; current_pin++) {
        pin_t pin = (pin_t)current_pin;
        const uint32_t values[] = {0, 0xffffffff, 0xa5a55a5d};
        bool expected = current_pin <= 22 || (current_pin >=32 && current_pin <=43) || current_pin >=45;
        if (!expected) {
            uint32_t value = 0x87654321; accesses = 0;
            errcode_t result = hal_pin_get_config_val(PIN_CONFIG_TYPE_MODE, pin, &value);
            pin_mode_t mode = hal_pin_ws53_get_mode(pin);
            assert(result == ERRCODE_FAIL && value == 0x87654321 && mode == PIN_MODE_MAX && accesses == 0);
            printf("X %u %08x 1 %u %u\n", current_pin, (unsigned)result, (unsigned)mode, accesses);
        } else {
            for (unsigned j=0; j<3; j++) {
                seed = values[j]; uint32_t value = 0x87654321; accesses = 0;
                current_call = "raw-get";
                assert(hal_pin_get_config_val(PIN_CONFIG_TYPE_MODE, pin, &value) == ERRCODE_SUCC);
                assert(value == (seed & 7) && accesses == 1);
                current_call = "mode-get";
                assert(hal_pin_ws53_get_mode(pin) == (seed & 7) && accesses == 2);
            }
        }
    }
    for (unsigned pin=24; pin<30; pin++) {
        assert(g_pio_pins_avaliable_mode[pin] == 0);
        for (unsigned mode=0; mode<8; mode++) assert(!pin_check_mode_is_valid((pin_t)pin, (pin_mode_t)mode));
        printf("M %u 0\n", pin);
    }
    /* Positive control: this is the real source mask array, not a zero-filled stub. */
    assert(pin_check_mode_is_valid(S_MGPIO0, PIN_MODE_0));
    assert(pin_check_mode_is_valid(S_MGPIO0, PIN_MODE_7));
    return 0;
}
''']
    fragments = {**original, 'complete_hal_maps': maps, 'port_mode_enum_and_masks': port_maps,
                 'pin_t': declaration(sources[ACORE], 'enum', 'pin_t'),
                 'pin_mode_t': declaration(sources[MODES], 'enum', 'pin_mode_t')}
    return '\n'.join(code), {name: sha(body.encode()) for name, body in fragments.items()}


def parse_records(text):
    records = []
    for line in text.splitlines():
        p = line.split()
        if p[0] == 'W' and len(p) == 5:
            records.append(dict(kind='write', trial=int(p[1]), address=int(p[2], 16), width=int(p[3]), value=int(p[4], 16)))
        elif p[0] == 'R' and len(p) == 7:
            records.append(dict(kind='read', pin=int(p[1]), call=p[2], address=int(p[3], 16), width=int(p[4]), value=int(p[5], 16), result=int(p[6])))
        elif p[0] == 'X' and len(p) == 6:
            records.append(dict(kind='rejected', pin=int(p[1]), raw_error=int(p[2], 16), output_unchanged=p[3] == '1', mode_result=int(p[4]), accesses=int(p[5])))
        elif p[0] == 'M' and len(p) == 3:
            records.append(dict(kind='mode-mask', pin=int(p[1]), mask=int(p[2]), rejected_modes=list(range(8))))
        else:
            raise ValueError('unexpected harness output: ' + line)
    return records


def verify(sdk, svd, cc):
    sources = {}
    for path in (BOOT, HAL, PORT, MODES, ACORE, CCORE, IO, BASE, ERROR):
        data = (sdk/path).read_bytes()
        require(data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{path}']),
                'SDK source differs from pinned Git object: ' + path)
        sources[path] = data.decode('utf-8-sig')
    sdk_hashes = {path: sha((sdk/path).read_bytes()) for path in sources}
    sources['_sdk'] = sdk
    source, fragment_hashes = harness(sources)
    profiles = []
    with tempfile.TemporaryDirectory(prefix='sfc-native-mux-') as tmp:
        path = Path(tmp)
        (path/'check.c').write_text(source)
        for optional in (False, True):
            flags = ['-DCONFIG_PINCTRL_SUPPORT_IE=1', '-DCONFIG_PINCTRL_SUPPORT_ST=1'] if optional else []
            command = [cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', *flags,
                       str(path/'check.c'), '-o', str(path/'check')]
            subprocess.run(command, check=True)
            output = subprocess.check_output([str(path/'check')], text=True)
            records = parse_records(output)
            stats = validate_records(records)
            profiles.append(dict(optional_ie_st=optional, compiler_options=command[1:command.index(str(path/'check.c'))],
                                 statistics=stats, access_log=records, stdout=output,
                                 stdout_sha256=sha(output.encode())))
    require(profiles[0]['access_log'] == profiles[1]['access_log'], 'optional maps changed mode behavior')
    return dict(passed=True, evidence_class='SDK-original-functions-host-RAM', sdk_commit=PIN,
        compiler=subprocess.check_output([cc, '--version'], text=True).splitlines()[0],
        sdk_sources=sdk_hashes, original_fragments=fragment_hashes,
        generated_private_harness_sha256=sha(source.encode()), svd=validate_svd(svd), profiles=profiles,
        temporary_sdk_harness_published=False, hardware_access=False,
        silicon_read_behavior_verified=False, physical_write_width_verified=False,
        native_mux_read_view_added=False, pin_mode_enum_added=False, reset_values_added=False,
        board_pinout_verified=False, safe_handoff_verified=False, bus_drain_verified=False,
        scope='Boot six whole-word write1 requests; actual SDK mode getters reject raw24..29 before read; port masks zero. No silicon native read inference.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--svd', type=Path, default=Path(__file__).resolve().parents[1]/'generated/WS53.experimental.svd')
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    print(json.dumps(verify(args.sdk.resolve(), args.svd.resolve(), args.cc), indent=2))


if __name__ == '__main__':
    main()
