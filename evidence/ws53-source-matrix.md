# WS53 source matrix

Status: SDK-first minimal map.

## Application-core RAM milestone (2026-10-22)

Pinned evidence: `fbb_ws53` commit
`163d9150ab94a02d09a05f623351f9facee3025e`, product
`ws53_liteos_app`, `MCU_MODE_1`. Confidence is **SDK-source**, not silicon
validation. Base addresses come from `src/drivers/chips/ws53/include/acore/platform_core.h`;
IRQ numbers from adjacent `chip_core_irq.h` are absolute mcause values (base 26).

| Instance | Evidence under `src/drivers/chips/ws53/` | Ownership/status |
|---|---|---|
| UART0/1/2 | `porting/uart/uart_porting.c`, acore platform map | Acore routing; pad/clock handoff not yet audited |
| TIMER0/1 | `porting/timer/timer_porting.c`, menuconfig `CONFIG_TIMER_MAX_NUM=2` | Acore IRQ 53/54; inherited clock configuration |
| RTC0/1 | acore platform/IRQ map | Acore routes 49/50; sleep sharing still to audit |
| GPIO0/1/2 | `porting/include/gpio_porting.h` | Group widths 23/12/3; pin ownership must be explicit |
| SYSTEM_MEMORY | `dyn_mem/dyn_mem.c`, `include/clock_recover.h` | Shared acore/ccore configuration, no exclusive HAL token |

System-memory reset values are **unknown**, so no reset value is emitted.
GPIO widths and TIMER channel count are IP parameters; WS63/BS2X defaults stay
unchanged. GPIO numeric bit positions are not physical MGPIO/AGPIO numbers.
Only confirmed timer channels 0 and 1 are exported for this WS53 profile. The
64-bit high halves, permissions and low-power registers still need modeling;
this is not a complete timer peripheral claim.

UART1/UART2 explicitly derive their SVD register layout from UART0. The exporter
checks the complete expanded register trees (including access, fields and side
effects) before accepting this property; instances keep their own base and IRQ.
This supplies one Rust register-block type without unchecked layout casts in HAL.

Clock evidence has two stages: menuconfig initializes the TIMER rate to 30 MHz,
but `src/application/ws53/ws53_application/main.c:373-385` unconditionally
changes it to **32 MHz** in `dev_work_clock_init()` (called from `main`, line
574). The RAM takeover profile uses the post-startup 32 MHz value. This is an
SDK software rate, not a measured oscillator frequency or a proven match to an
unidentified installed firmware. Clock gate/selector registers still need their
own evidence before a HAL may configure them.

The exporter now carries explicit register access and known reset masks. This
changes generated WS63/BS2X SVD metadata (not their released PACs): read-only and
write-only registers stop inheriting read-write access, reserved/unknown reset
bits are excluded. Existing imported reset values remain provisional and must
be audited; a mask does not turn inherited values into silicon evidence.

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
