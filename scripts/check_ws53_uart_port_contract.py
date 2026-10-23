#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned WS53 UART pad/reset helpers against host event sinks, not MMIO."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from check_ws53_mdma_contract import PIN, CONFIG, require
from check_ws53_uart_dma_contract import UPORT, DRIVER, function, typedef

PLATFORM = 'src/drivers/chips/ws53/include/acore/platform_core.h'
PINS = 'src/drivers/chips/ws53/porting/include/pinctrl_porting.h'
CLOCK = 'src/drivers/chips/ws53/include/clock_recover.h'
GENERIC_CLOCK = 'src/drivers/chips/ws53/include/hal_clocks_application_private.h'
CHIP_IO = 'src/middleware/utils/common_headers/chip_io.h'


def verify(sdk, cc):
    sources = {}
    for name in (UPORT, DRIVER, PLATFORM, PINS, CLOCK, GENERIC_CLOCK, CHIP_IO, CONFIG):
        data = (sdk/name).read_bytes()
        require(data == subprocess.check_output(['git', '-C', str(sdk), 'show', f'{PIN}:{name}']),
                name + ' differs from pinned SDK')
        sources[name] = data.decode('utf-8-sig')
    port, driver, config = sources[UPORT], sources[DRIVER], sources[CONFIG]
    require(re.search(r'#define writel\(addr, d\)\s+\(\*\(volatile uint32_t\*\)\(uintptr_t\)\(addr\) = \(uint32_t\)\(d\)\)',
                      sources[CHIP_IO]), 'shared-route write width or argument order changed')
    for setting in ('# CONFIG_UART_SUPPORT_LPC is not set',
                    'CONFIG_UART_MULTI_CORE_RESUME=y', 'CONFIG_HSO_UART_SUPPORT=y',
                    'CONFIG_UART_L0_TX_PIN=19', 'CONFIG_UART_L0_RX_PIN=20'):
        require(setting+'\n' in config, 'product setting changed: '+setting)
    require(re.search(r'#if defined\(CONFIG_UART_SUPPORT_LPC\)\s*'
                      r' uart_port_clock_enable\(bus, true\);\s*#endif', driver),
            'clock enable conditional changed')
    require('uart_port_clock_enable' not in port, 'port now provides clock control; reassess')
    claim = function(driver, 'uart_claim_pins')
    require('unused(pins);' in claim and 'uart_port_config_pinmux(bus);' in claim,
            'caller pins now affect route selection; reassess')
    require('CONFIG_UART_L0_TX_PIN' not in port and 'CONFIG_UART_L0_RX_PIN' not in port,
            'numeric product pin configuration now consumed; reassess')
    # Capture the known conflict, without turning the generic enum into MMIO facts.
    require('HAL_CLOCKS_APP_UART_H1_CLK      = HAL_CLOCKS_APP_MCLK_EN1_BASE + 5' in sources[GENERIC_CLOCK],
            'generic H1 clock definition changed')
    require('HAL_CLOCKS_APP_UARTL0_CLK       = HAL_CLOCKS_APP_MCLK_EN0_BASE + 9' in sources[GENERIC_CLOCK],
            'generic L0 clock definition changed')
    require(re.search(r'uart_h0_clken\s*:\s*1;\s*uint32_t reserved2\s*:\s*1;', sources[CLOCK]),
            'clock recovery H1 reserved-bit conflict changed')
    require(re.search(r'pwm_clken\s*:\s*1;\s*uint32_t reserved3\s*:\s*3;', sources[CLOCK]),
            'clock recovery L0 reserved-bit conflict changed')
    reset_defines = '\n'.join(re.findall(r'^#define UART_SOFT_RST_\w+\s+\S+.*$', port, re.M))
    route_start = port.index('#if defined(CONFIG_UART_H0_PINSET_2) || defined(CONFIG_HSO_UART_SUPPORT)')
    route_end = port.index('void uart_port_config_pinmux', route_start)
    helpers = ['uart_port_set_pin', 'uart_port_set_reset', 'uart_port_reset',
               'uart_port_config_pinmux', 'uart_port_release_pinmux',
               'uart_port_set_clock_value', 'uart_port_get_clock_value']
    code = [r'''
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <stdio.h>
#define unused(x) ((void)(x))
#define UNUSED(x) ((void)(x))
typedef unsigned uart_bus_t;
enum { UART_BUS_0, UART_BUS_1, UART_BUS_2 };
static uart_bus_t g_sw_debug_uart;
static uint32_t g_uart_clock_value = 32000000;
struct event { unsigned kind; uint32_t a, b; };
static struct event events[16];
static unsigned count;
static uint16_t reset_a, reset_c;
static void emit(unsigned kind, uint32_t a, uint32_t b) {
    assert(count < 16); events[count++] = (struct event){kind,a,b};
}
static void uapi_pin_set_pull(uint32_t pin, unsigned pull) { emit(0,pin,pull); }
static void uapi_pin_set_mode(uint32_t pin, unsigned mode) { emit(1,pin,mode); }
static uint16_t *reset_reg(uint32_t address) {
    assert(address==0x52000168 || address==0x570000b0);
    return address==0x52000168 ? &reset_a : &reset_c;
}
static void uapi_reg_clrbit16(uint32_t a, uint32_t pos) {
    *reset_reg(a) &= ~(1u<<pos); emit(2,a,pos);
}
static void uapi_reg_setbit16(uint32_t a, uint32_t pos) {
    *reset_reg(a) |= 1u<<pos; emit(3,a,pos);
}
/* The SDK wrapper's argument order is address, value. */
static void writel(uint32_t address, uint32_t value) { emit(4,address,value); }
''', typedef(sources[PLATFORM], 'pin_t'), typedef(sources[PINS], 'pin_mode_t'),
            typedef(sources[PINS], 'pin_pull_t'), reset_defines, port[route_start:route_end]]
    code += [function(port, name) for name in helpers]
    code += [r'''
static void expect(unsigned i, unsigned kind, uint32_t a, uint32_t b) {
    assert(i<count); assert(events[i].kind==kind); assert(events[i].a==a); assert(events[i].b==b);
}
static unsigned expected_pins(unsigned bus, unsigned release) {
    unsigned n=0;
#ifdef SUPPORT_HAL_PINCTRL
    const unsigned tx[3]={12, EXPECT_H0_TX, 33};
    const unsigned rx[3]={36, EXPECT_H0_RX, 34};
    const unsigned modes[3]={2, EXPECT_H0_MODE, 1};
    expect(n++,0,tx[bus],1); expect(n++,1,tx[bus],release ? 0 : modes[bus]);
#ifdef CONFIG_UART_L0_NOT_SUPPORT_RX
    if (bus!=2)
#endif
    { expect(n++,0,rx[bus],1); expect(n++,1,rx[bus],release ? 0 : modes[bus]); }
#ifdef CONFIG_UART_MULTI_CORE_RESUME
    if (!release) expect(n++,4,0x57000048,1);
#endif
#else
    (void)bus; (void)release;
#endif
    return n;
}
int main(void) {
    unsigned tests=0;
    const uint32_t addresses[3]={0x52000168,0x52000168,0x570000b0};
    const unsigned bits[3]={1,0,7};
    for (unsigned debug=0;debug<3;debug++) for (unsigned bus=0;bus<3;bus++) {
        g_sw_debug_uart=debug;
        for (unsigned initial=0;initial<2;initial++) {
            reset_a=reset_c=initial ? 0xffff : 0;
            uint16_t old_a=reset_a, old_c=reset_c;
            count=0; uart_port_reset(bus);
            if(bus==debug) assert(count==0);
            else {
                assert(count==2); expect(0,2,addresses[bus],bits[bus]);
                expect(1,3,addresses[bus],bits[bus]);
                if(bus==2) old_c|=1u<<bits[bus]; else old_a|=1u<<bits[bus];
            }
            assert(reset_a==old_a && reset_c==old_c); tests++;
        }
        count=0; uart_port_config_pinmux(bus);
        assert(count==expected_pins(bus,0)); tests++;
        count=0; uart_port_release_pinmux(bus);
        if(bus==debug) assert(count==0);
        else {
            unsigned n=expected_pins(bus,1);
#ifdef SUPPORT_HAL_PINCTRL
            expect(n++,2,addresses[bus],bits[bus]); expect(n++,3,addresses[bus],bits[bus]);
#endif
            assert(n==count);
        }
        tests++;
    }
    /* Port frequency is global bookkeeping, NOT per-port clock provisioning. */
    count=0;
    for(unsigned bus=0;bus<3;bus++) {
        uart_port_set_clock_value(bus,24000000+bus);
        for(unsigned other=0;other<3;other++) assert(uart_port_get_clock_value(other)==24000000+bus);
        assert(count==0); tests++;
    }
    printf("%u\n",tests);
}
''']
    profiles = [
        ('product-hso', ['SUPPORT_HAL_PINCTRL', 'CONFIG_HSO_UART_SUPPORT', 'CONFIG_UART_MULTI_CORE_RESUME'], (17,18,2)),
        ('h0-pinset2', ['SUPPORT_HAL_PINCTRL', 'CONFIG_UART_H0_PINSET_2'], (17,18,2)),
        ('default-h0', ['SUPPORT_HAL_PINCTRL'], (8,9,1)),
        ('l0-tx-only', ['SUPPORT_HAL_PINCTRL', 'CONFIG_HSO_UART_SUPPORT', 'CONFIG_UART_MULTI_CORE_RESUME',
                        'CONFIG_UART_L0_NOT_SUPPORT_RX'], (17,18,2)),
        ('no-pinctrl', [], (8,9,1)),
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix='ws53-uart-port-') as tmp:
        binary = Path(tmp)/'port'
        for name, flags, route in profiles:
            command = [cc, '-std=c11', '-Wall', '-Werror', '-Wno-unused-function', '-x', 'c', '-', '-o', str(binary)]
            command += ['-D'+flag for flag in flags]
            command += [f'-DEXPECT_H0_{key}={value}' for key,value in zip(('TX','RX','MODE'),route)]
            subprocess.run(command, input='\n'.join(code), text=True, check=True)
            count = int(subprocess.check_output([str(binary)], text=True))
            require(count == 39, 'missing reset/route/clock cases')
            results.append(dict(profile=name,cases=count))
    return dict(sdk_commit=PIN, source_sha256={p:hashlib.sha256((sdk/p).read_bytes()).hexdigest() for p in sources},
        hardware_accessed=False, profiles=results, cases=sum(x['cases'] for x in results),
        application_routes=[dict(uart=0,ip='H1',tx_sdk=12,rx_sdk=36,mode=2),
            dict(uart=1,ip='H0',tx_sdk=17,rx_sdk=18,mode=2,profile='HSO or pinset2'),
            dict(uart=1,ip='H0',tx_sdk=8,rx_sdk=9,mode=1,profile='default'),
            dict(uart=2,ip='L0',tx_sdk=33,rx_sdk=34,mode=1)],
        findings=['Current product disables LPC clock-enable call; port source has no clock-enable implementation',
            'Clock getter/setter share one software variable across all ports and perform no hardware clock operation',
            'Reset clears then sets a halfword bit; debug UART skips reset/release',
            'Release selects mux0 but retains pull-up; it is not restoration of the original pad state',
            'MULTI_CORE_RESUME pin configuration writes shared route 0x57000048=1 for EVERY bus',
            'Caller pins and product L0 pin numbers 19/20 do not select routes in this source port',
            'Generic H1/L0 clock enum conflicts with reserved bits in clock_recover.h'],
        limitations=['Extracted helper execution, not a linked SDK image or a silicon test',
            'Effective firmware pin/clock configuration may differ from this source profile',
            'Clock gate identity, physical frequency, full reset effects and cross-core ownership remain unqualified'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk',type=Path,required=True)
    parser.add_argument('--cc',default='cc')
    args=parser.parse_args()
    print(json.dumps(verify(args.sdk.resolve(),args.cc),indent=2))


if __name__ == '__main__':
    main()
