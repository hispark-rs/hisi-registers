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
unidentified installed firmware. The gate subset below has register evidence;
clock ownership, source/divider selection and changes still need validation.

## Pin and clock configuration blocks

`rdl/chips/ws53/pinctrl.rdl` follows
`src/drivers/drivers/hal/pinmux/ws53/hal_pinctrl_ws53.c`, rather than the
incompatible generic `hal_pinctrl_v150_regs_def.h`. Its 32-bit RMW accessor is
`uapi_reg_setbits` in `src/include/common_def.h`.

- MCU mux at `0x52008000`: 18 bonded MGPIO selections. No phantom linear slots
  for MGPIO1/6/7/11/16; those map to AGPIO6/0/9/7/8.
- AON at `0x57036000`: 12 AGPIO muxes and 28 pad controls. AGPIO12/RST_N has
  pad control only. MGPIO24..29 flash pads are excluded from this app profile.
- TZPC at `0x5703a000`: pads for MGPIO14/15/21 (SGPIO0/1/2), with their muxes
  in the MCU block. MGPIO14/15 are debug pins and require explicit reservation.
- Six pads have 4-bit drive codes split over bits 1:0 and 7:6: MGPIO0/2/3/4,
  AGPIO5/6. Other pads have only the low 2-bit code. The SDK enum alone allows
  unsupported strengths, and its setter can modify low bits before rejecting
  the high bits; the Rust configuration must validate before any MMIO.

`rdl/chips/ws53/m-crg.rdl` uses `clock_recover.h`'s `u_m_cken_0/1` and
the actual ADC/I2C/SPI port code. In particular M_CLKEN1 bit 2 is LSADC APB,
bit 11 is SPI3_MS (application SPI_BUS_0), bit 0 is TIMER. The generic
`hal_clocks_application_private.h` enum disagrees and is not used as the truth.
16-bit access is evidenced by `reg16_setbit`/`writew` in those ports; higher
register bits and undocumented selectors are not invented. MSUB gate fields
come from `hal_clocks.h`; a named SDMA gate does not prove a usable SDMA instance.

All four new blocks have unknown reset values and shared ownership. They have
SDK-source evidence only: adding a PAC register does not authorize claiming or
reconfiguring the control core's clocks, reset pad, flash pads, or SWD pins.

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

## Application SPI v151_100

The pinned `ws53_liteos_app` product selects `v151_100`. Source paths, relative
to that SDK checkout:

- `src/drivers/drivers/hal/spi/v151_100/hal_spi_v151_100_regs_def.h`,
  `hal_spi_v151_100_regs_op.h` and `hal_spi_v151_100_adapt_op.h`;
- the corresponding `hal_qspi_v151_100_*` files;
- `src/drivers/chips/ws53/porting/spi/spi_porting.c`, plus the acore IRQ map.

| PAC instance | SDK bus | Base | IRQ | Enable / control / FIFO | BUSY bit | DMA TX / RX bits |
|---|---|---|---|---|---|---|
| SPI0 | SPI_BUS_0 | 0x52087000 | 65 (SPI_3) | 0x00 / 0x04 / 0x2c | 15 | 0 / 1 |
| QSPI1 | SPI_BUS_1 | 0x5208d000 | 43 (QSPI0_2CS) | 0x08 / 0x00 / 0x60 | 0 | 1 / 0 |

The two layouts are separate RDL types. SPI0's adaptation layer translates
standard-format port constants to its custom layout; copying those constants
directly into CONTROL would program different fields. QSPI1's port is master
only. Shared v151 layout names do not establish equivalence to these instances.

Each instance exposes 36 FIFO access windows. All windows access the same FIFO,
not 36 independent storage words. RDL exports overlapping RX (read-only, pop)
and TX (write-only, push) arrays. Clear registers acknowledge interrupts on read;
SDK descriptions say writes have no effect, so no write or modify API is emitted.
No reset values or FIFO depth are inferred from the number of windows.

The current description has 23 peripheral instances, 668 expanded register
**views**, 1275 expanded fields and 17 IRQs. RX/TX views count separately despite
sharing addresses. Validation covers schema, field/layout distinctions, access
permissions and generation; SPI HAL, behavior models and silicon loopback are
still pending. Acore routing is SDK evidence, not exclusive runtime ownership.
