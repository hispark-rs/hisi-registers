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

At the SPI milestone (`66e519d`), the description had 23 peripheral instances,
668 expanded register **views**, 1275 expanded fields and 17 IRQs. RX/TX views
count separately despite sharing addresses. Validation covers schema, field/layout distinctions, access
permissions and generation; SPI HAL, behavior models and silicon loopback are
still pending. Acore routing is SDK evidence, not exclusive runtime ownership.

## Application MDMA v151

The app ROM component selects `hal_dmac_v151_funcs_get()` through its DMA port,
even though the generic `CONFIG_DMA_USING_V151` menu option is unset. The ROM
component and binding are stronger evidence than that unused generic selector.
Paths under `src/drivers/chips/ws53/rom/app/` in the same pinned SDK:

- `drivers/drivers/hal/dma/romable/v151/hal_dmac_v151_regs_def.h` and
  `hal_dmac_v151_regs_op.h`: offsets and bit fields;
- `drivers/drivers/hal/dma/CMakeLists.txt`: v151 ROM/flash component split;
- `drivers/chips/ws53/porting/dma/dma_porting.c` and
  `drivers/chips/ws53/porting/include/dma_porting.h`: controller, channels, routes;
- `src/drivers/chips/ws53/include/acore/{platform_core,chip_core_irq}.h`
  (relative to the SDK root): base `0x52070000`, IRQ 58 (`M_DMA`).

There are eight channels at `+0x100 + channel*0x20`. Within each channel,
LLI/destination/configuration/source/control are at 0/4/8/16/20. This is not
the WS63 DMA layout. No reset values are asserted. ACTIVE is exposed only as a
status reader based on its SDK role; its physical write behavior and timing
have not been tested. HAL cancellation must still establish hardware quiescence and
cannot equate clearing ENABLE with completed bus transactions.

On 2026-10-22 A-board J-Link `23121302`, UART `5CB7019921`, a read-only ROM
capture of `0x1ac60..0x1b3a0` was checked against SDK `rom_config/acore/acore.sym`:

- SHA-256: `0082ef85ff9d4ad0c447d74cc02f0f37ba732a012dc4c343bdf0b5cc3381886a`.
- `hal_dma_reset` at `0x1accc` loops eight times, clearing channel ENABLE at
  `base+0x108+(channel<<5)`; this confirms layout, not a safe cancellation protocol.
- TC/error status functions read `base+4`, bits 8–15 / 16–23 respectively.
- Clear functions at `0x1b338` and `0x1b358` read `base+8`, OR the selected
  channel bit (shifted by 8 for errors), and write it back. RDL provides a
  write-one-to-clear **command-only view**, with no generated read/modify API.
  This restriction does not claim the physical register is unreadable or that
  readback is zero. Actual interrupt clearing still needs a DMA transfer test.
- `hal_dma_regs_data_get_ch_en` reads the low eight bits at `base+0x10`.
- CPU resume and vendor `AT` responses succeeded before and after capture.
  No RAM program or DMA operation was run; no Flash was written.

Only 4-bit request values 0–15 are modeled. The port enum explicitly labels
16 and subsequent entries nonexistent. App routes are:

| Request | SDK route | App instance |
|---|---|---|
| 0 / 1 | I2C0 TX / RX | I2C0 |
| 2 / 3 | I2C1 TX / RX | I2C1 |
| 4 / 5 | QSPI0_2CS TX / RX | QSPI1 / SPI_BUS_1 |
| 6 / 7 | SPI_M TX / RX | SPI0 / SPI_BUS_0 |
| 8 / 9 | UART_H0 TX / RX | UART1 |
| 10 / 11 | UART_H1 TX / RX | UART0 |
| 12 / 13 | I2S TX / RX | I2S, integration pending |
| 14 / 15 | PWM / SPWM | Port declarations; endpoint behavior pending |

The legacy `UART_L` alias points at UART_H1, but the real UART port returns
an invalid request for UART2. It is not evidence of UART2 DMA support.
The mux helpers in the DMA port are no-ops, so no invented mux register is
exported. The separate `SDMA_BASE_ADDR` macro and clock bit still do not
establish an accessible app instance; SDMA is not generated.

The MDMA milestone (`d8de2dc`) brought the map to 24 peripherals, 716 expanded
register views, 1483 expanded fields and 18 IRQs. It supplies register access and evidence,
not DMA ownership, a safe HAL, a transfer model or silicon transfer validation.

## ADC v155 and shared diagnostic capture

The same SDK commit selects `CONFIG_ADC_USING_V155=y` for `ws53_liteos_app`.
Sources, relative to the SDK root:

- `src/drivers/drivers/hal/adc/v155/hal_adc_v155_regs_def.h`,
  `hal_adc_v155_regs_op.{h,c}`, `hal_adc_v155.c`;
- `src/drivers/chips/ws53/porting/adc/adc_porting.c` and
  `porting/include/adc_porting.h`;
- `src/drivers/drivers/hal/cpu_trace/hal_cpu_trace.{h,c}`, **CORE == APPS**;
- `src/drivers/chips/ws53/porting/cpu_trace_acore/mcpu_trace_porting.c`.

ADC is at `0x5208a000`, IRQ 80 (`LSADC`). MCU_DIAG's ADC struct view starts at
`0x52004100`; the CPU trace driver uses base `0x52004000` plus offsets 0x100
and above, reaching the same registers. Both resources need an explicit shared
ownership handoff, not an exclusive ADC token by itself.

Important differences from v153/v154 and stale port declarations:

- `lsadc_ctrl_8` is at +0x1c, ctrl_11 at +0x24 and ctrl_12 at +0x28. Missing
  suffix numbers do not reserve holes in the SDK struct. Host C static asserts
  against the original header confirmed these and calibration/diagnostic offsets.
- Eight channel-enable bits are described. The manual selector has only two
  bits; the selected implementation sets it to zero and does not implement a
  usable manual sample API. Do not advertise eight-channel manual conversion.
- Acquisition uses MCU_DIAG to write RAM. Legacy `HAL_ADC_SCAN_CH*` constants
  at 0x4400d01c and the all-zero pin macros are not used by v155; no FIFO MMIO
  at those addresses is generated. Data words in RAM carry 12-bit samples at
  bits 2–13 and a three-bit channel at bits 14–16.
- IRQ 80 is routed in the platform, but `hal_adc_irq_handler()` is empty and
  says interrupt mode is unsupported. No interrupt mask/status/ack semantics
  have been invented. The selected path polls diagnostic SAMPLE_DONE.
- Five refresh/clear controls are explicitly pulsed **1 then 0** by the SDK.
  They are command-only views with no read/modify API; they are not marked W1C
  or self-clearing. Start/stop and opaque analog configuration are also exposed
  only as write commands, without claiming their physical readback is known.
- The three analog configuration words at +0xf4/+0xf8/+0xfc have only opaque
  write values in the SDK. Unexplained header slots, other filter/gain controls,
  unknown reset values and unsupported FIFO/IRQ behavior remain omitted.
- M_CRG reset bit 13 at `0x52000054` uses 32-bit accesses, unlike the 16-bit
  clock gates. SDK asserts low, waits 1 us, releases high, waits 1 us.

The selected ADC port clears the following pad registers when enabling a channel:

| ADC channel | Pad identity | Pad address |
|---|---|---|
| 0 | AGPIO1 | 0x57036024 |
| 1 | AGPIO3 | 0x5703602c |
| 2 | AGPIO4 | 0x57036030 |
| 3 | AGPIO6 / MGPIO1 | 0x57036044 |
| 4 | MGPIO22 | 0x57036098 |
| 5 | MGPIO8 | 0x57036060 |
| 6 | MGPIO9 | 0x57036064 |
| 7 | AGPIO0 / MGPIO6 | 0x57036058 |

These are chip-pad identities, not board MIO/AIO connector numbers. No pad was
changed during this audit. The SDK's 1800 mV conversion constant is not proof
of board input voltage limits or calibration accuracy.

CPU trace establishes SAMPLE_MODE at `0x52004204`: mode[1:0], enable[8], sync[9].
The ADC struct and getter place SAMPLE_DONE at `0x52004228`; the CPU-trace port
mistakenly aliases its DONE macro to the done-address-low register at 0x52004220.
RDL keeps the ADC flag and address registers separate; silicon flag semantics
still need a controlled capture test.

A-board read-only preflight on 2026-10-23 found `CPU_TRACE=3`,
`SAMPLE_MODE=0x301` and range `0x200223ec..0x200227d0`, while MDMA enabled
channels remained zero. Thus there is an enabled diagnostic RAM writer even
with no MDMA channel enabled. CPU halt alone is not a quiescence proof. The
range is disjoint from the proposed RAM ELF, but takeover must explicitly
account for the writer. Resume and vendor AT liveness succeeded afterward;
ADC/calibration/capture were not started or stopped by this audit.

Remaining ADC HAL requirements include bounded calibration failures, CAP error
handling, exclusive diagnostic-buffer lifetime/cache maintenance, verified stop
and drain before releasing memory, and restoration of borrowed trace state.
The SDK silently breaks out of calibration/completion polling and frees its
buffer after issuing stop without proving drain; these paths must not be copied
as safe Rust behavior. No ADC conversion or IRQ has been validated on silicon.

Current map: 26 peripherals, 752 expanded register views, 1545 expanded fields,
19 IRQ numbers. ADC/MCU_DIAG describe only the evidenced subset, not complete
ADC register coverage or a supported HAL.

## WS53 temperature sensor polling subset

Pinned SDK `163d9150ab94a02d09a05f623351f9facee3025e`:
`src/drivers/chips/ws53/porting/tsensor/tsensor_porting.c` and its CMakeLists;
`src/application/ws53/ws53_application/main.c` initializes this port directly.
`src/middleware/services/wifi_service/service/soc_wifi_service_api.c` calls the
same getter for Wi-Fi temperature queries. Application access is evidenced;
exclusive ownership across firmware users/cores is not established.

Six 32-bit accesses are modeled at base `0x59006300`: START +0, CONTROL +4,
AUTO_STATUS +8, SAMPLE_TIME +0x10, AUTO_REFRESH_PERIOD +0x30 and
AUTO_REFRESH_CONFIG +0x34. `tsensor_reg_t` describes READY bit 1 and a 10-bit
CODE at bits 11:2. Bit 0 is named `auto_clr`, but the implementation never
writes it or explains its semantics; it is deliberately omitted. No reset
values, IRQ, clear-on-read or W1C behavior are inferred. Four registers expose
only the writes actually evidenced by the port; this is an API restriction,
not a claim that physical readback is impossible. Opaque 32-bit fields record
SDK access width, not the number of implemented configuration bits.

The init sequence writes period 160, control 3, sample-time 2, refresh-config
0 and start 1. SDK comments identify 5 ms at 32 kHz, 16-point average cyclic
reporting and 0.256 ms per sample. These are source descriptions, not measured
clock/timing facts. The polling code uses integer truncation toward zero:
`(((code - 118) * 16500 / (933 - 118) - 4000) / 100)` degrees Celsius.
It caps only the upper limit at 125 and casts to int8_t; a future HAL must
handle the lower bound explicitly and use bounded readiness polling. There
is no stop/deinit implementation here, and the generic tsensor.h declarations
for IRQ, multiple thresholds and calibration do not prove WS53 support.

The optional SDK layout checker compares all six absolute addresses with the
pinned C source. Export tests verify the status bitfield and restricted access
shape. No TSENSOR register was accessed on hardware in this audit; no HAL or
QEMU behavior is claimed. Current map: 27 peripherals, 758 expanded register
views, 1552 fields, 19 IRQ numbers. WS63/BS2X output is unchanged.

## SDIO v151 device interface: ROM-backed layout audit

SDK `163d9150ab94a02d09a05f623351f9facee3025e`, selected
`ws53_liteos_app.config`, enables DRIVER_SUPPORT_SDIO, SDIO_USING_V151 and
SDIO_EXTEND_INFO; memory-card mode and tuning are disabled. There is one bus
in `rom/app/drivers/chips/ws53/porting/include/sdio_porting.h`. The referenced
v151 C/register headers are absent from this SDK release. We did not substitute
WS63 SDIO, an SD host controller, or host-visible CCCR addresses.

A-board J-Link 23121302 / UART 5CB7019921 was checked with AT, halted, and ROM
`[0x1e872,0x1f1e4)` read only, then resumed. AT remained healthy. Capture length
2418, SHA256 `a1ac5c03c613112f973b45bdfbfd399d3f3017b315b61d1f6b0d2a07d2ce4671`.
No SDIO MMIO read/write, ROM function call, DMA transfer, RAM load or Flash
write was performed. Symbols are from `rom_config/acore/acore.sym`; full
control-flow inspection uses the SDK's `riscv32-linux-musl-objdump`, since
stock objdump misdecodes proprietary compressed/48-bit WS53 instructions.
The raw ROM is not redistributed. `scripts/check_ws53_sdio_rom.py` optionally
checks the exact capture hash, decodes the address immediates of 28 SVD views,
and checks base/IRQ. Ordinary builds/generation do not depend on the capture.

| Evidence | ROM location | Result |
|---|---|---|
| sdio_porting_base_addr_get | 0x1f176 | LUI a0,0x52060; SDIO base 0x52060000 |
| sdio_port_register_irq table | 0x1f1dc; direct completion at 0x1f0e4 | IRQ 46, matching acore/chip_core_irq.h |
| host clock ready | 0x1e8e2 | word at +0x44, bit 0 |
| interrupt address getter | 0x1f11e | selector 0 -> +0x38, 1 -> +0x1c, 2 -> +0x110 |
| interrupt getter/setter | 0x1f150 / 0x1f162 | word read / word write, same register address |
| global/function0/function1 enables | 0x1ea44 / 0x1ea6a / 0x1eada | +0x18 bits0..2 / +0x34 bits0..10,16 / +0x114 bits0..13 |
| CCCR getter/setter | 0x1f140 / 0x1f158 | capability word at +4; init writes 0xb36343 |
| function1 FBR init | 0x1eb68 | +0x118; IO code[3:0], CSA[4], extended code[12:5], SPS[13] |
| ADMA submit | 0x1ee98 / 0x1eef2 | dummy read then table-pointer write to +0x40 after callback |
| prepare send | 0x1ea12 | +0x120 length low21 bits; +0x104 read then OR1 write |
| host/device messages | 0x1ef9c / 0x1e924 | +0x128 read / +0x12c dummy read then write |
| command/block length | 0x1f094 | +0x254 CMD53 argument; +0x258 low12 block size |
| port soft reset | 0x1f1c8 | RET only: not a hardware reset or quiescence primitive |

Public `hal_sdio.h` defines CMD53 fields and global interrupt masks;
`sdio_porting.h` defines CCCR/FBR capabilities and the ADMA format. ROM descriptor
construction at 0x1e8f4 rejects null table/address, zero length and index >=129,
then stores an 8-byte descriptor (u16 flags=0x21, u16 length, u32 address).
It does not reject u32 lengths beyond u16 before storing. Descriptor bounds,
zero-length semantics, alignment, host block mode, cache ownership and stopping
an in-flight transfer must be handled by the future HAL rather than inheriting
these unchecked behaviors. CMD53 count=0 in byte mode means 512; the ROM rejects
zero block-count multiplication as invalid. This does not prove every protocol
mode or all host behavior is supported.

The RDL includes 23 distinct word offsets / 28 access views. Three interrupt
acknowledgements, reset command and send command use complementary RO/WO views
so no misleading modify() is generated. ROM writes captured status back to
acknowledge; the precise hardware clear mechanism is not yet tested, so W1C,
read-clear and reset values are not invented. Opaque configuration at +0x24,
+0x28, +0x2c, +0x30 and +0x10c is preserved without speculative bit meanings.
The modeled subset does not include the undocumented 64-word extended window,
CIS data, complete reset/clock controls or host voltage/tuning support.

SDIO ADMA is an independent RAM bus master, not an MDMA channel. Hence
MDMA ENABLED_CHANNELS=0 cannot authorize RAM takeover. Current vendor gateway
firmware ownership, active SDIO/ADMA state and stop/drain conditions remain
unverified. The source HCC device backend binds callbacks and owns descriptor/
RX/TX buffers; application visibility does not imply Rust-exclusive tokens.
No HAL, SDIO model, data transfer or IRQ-delivery test is claimed yet.

Current map: 28 peripherals, 786 expanded register views, 1606 fields, 20 IRQs.
The SDIO layout is ROM-derived evidence, distinct from a transfer HIL result.
