# WS53 source matrix

Status: SDK-first minimal map.

No PAC SVD baseline is imported. The initial WS53 map contains only IP blocks
whose register definitions are proven reusable from `fbb_ws53` and the other SDKs:

- watchdog v151 at acore base `0x5702B000`;
- SFC v150 at `0x53000000`;
- PWM v151 at `0x52082000`;
- UART v151 at acore `UART0` base `0x52081000`;
- I2C v151 at acore `I2C0/1` bases `0x52083000` and `0x52084000`;
- RTC v150 channel 0 at acore base `0x57029100`;
- SIO v151 at register-block base `0x52030000` (the SDK HAL view starts at
  `I2S_BUS_0_BASE_ADDR = 0x5203003c`).

UART basic, Timer, TCXO data16, GPIO basic and SIO reuse three-chip shared
models. I2C v151 and RTC v150 reuse WS53/BS2X family variants; WS63 selects I2C
v150 and RTC v100 instead.

WS53 has separate acore/control-core ownership and product configurations that
can select different SPI implementations (`v151` or `v151_100`). Future additions
must therefore identify the core and product profile rather than creating one
ambiguous flat map.
