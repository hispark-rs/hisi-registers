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

The pinned `ws53_liteos_app` disables the optional SPI driver. Its CMake fallback
selects `v151_100` when no version option is set; the WS53 port includes that
adaptation layer explicitly. Source paths, relative to that SDK checkout:

- `src/drivers/drivers/hal/spi/v151_100/hal_spi_v151_100_regs_def.h`,
  `hal_spi_v151_100_regs_op.h` and `hal_spi_v151_100_adapt_op.h`;
- the corresponding `hal_qspi_v151_100_*` files;
- `src/drivers/chips/ws53/porting/spi/spi_porting.c`, plus the acore IRQ map.

| PAC instance | SDK bus | Base | IRQ | Enable / control / FIFO | BUSY bit | DMA TX / RX bits |
|---|---|---|---|---|---|---|
| SPI0 | SPI_BUS_0 | 0x52087000 | 65 (SPI_3) | 0x00 / 0x04 / 0x2c | 15 | 0 / 1 |
| QSPI1 | SPI_BUS_1 | 0x5208d000 | 43 (QSPI0_2CS) | 0x08 / 0x00 / 0x60 | 0 | 1 / 0 |

The two layouts are separate RDL types. SPI0's field-specific adaptation helpers
translate semantic settings to its custom layout. Whole-word setters do not
translate; copying QSPI-format constants into CONTROL programs different fields. QSPI1's port is master
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

## Unified-security TRNG: source-address and shared-service audit

SDK `163d9150ab94a02d09a05f623351f9facee3025e`,
`build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config`, enables
SECURITY_UNIFIED_SUPPORT_TRNG and TRNG_RING_ENABLE; ENTROPY_SOURCES_FROM_FRO is
unset. `target_config.py` selects BUILD_APPLICATION_STANDARD and
`acore_trng_buffer`. The application branch of
`drivers/drivers/hal/security_unified/CMakeLists.txt` builds `hal_trng.c` and
`crypto_osal/cfbb/os/crypto_osal_lib.c`. The OS header includes the WS53
`security_unified_porting.h` and adds `TRNG_REG_BASE_ADDR` for every TRNG access.
Thus this source path uses **0x52024000** (SEC_SUBSYS_BASE 0x52010000 + 0x14000),
not the legacy **0x52009800** TRNG_RB_BASE in platform_core.h. A different
ROM/boot configuration is not assumed to use this application path.

| RDL register | Source evidence | Access/meaning |
|---|---|---|
| TRNG.FIFO_DATA +0x100 | hal_cipher/trng/hal_trng_reg.h and hal_trng.c | 32-bit RO FIFO consumption, readAction modifyExternal |
| TRNG.FIFO_READY +0x104 | same, is_trng_ready() | 32-bit RO, data_ready bit0 AND done bit1 required |
| TRNG.FRO_SAMPLE_CLK_SEL +0x174 | optional FRO init branch | 32-bit RW, bit0=1 selects internal FRO; branch disabled in selected config |
| TRNG.FRO_DIV_CNT +0x178 | optional FRO init branch | 32-bit RW, low8 encoding; source uses 0x1b and describes divide by54, no general formula inferred |
| M_CRG.TRNG_RING_CONTROL +0x1600 | TRNG_SEC_CFB macro + inner_trng_ring_enable() | 16-bit RW bit0; shared volatile ring control, not OTP/security provisioning |

No reset values or TRNG interrupt are invented. DATA_ST +0x108 is a macro only,
without demonstrated access/field semantics, and is omitted. CONFIG_TRNG_CLK_OPEN
is not enabled in the selected application; its optional TRNG_SAMPLE_CKDIV address
is not defined by this WS53 port, so it is also omitted. SDK init enables the ring
and waits 200 us, deinit disables it; neither operation is safe to perform behind
another core/service's back. No TRNG MMIO read or ring-control write was performed.

The SDK reads FIFO_READY twice, then consumes two FIFO words per candidate. It
rejects a candidate of zero/all-ones or equal to its comparison word. This is a
source-level filter, not an entropy-quality proof. The timeout macro is named
IN_US, its comment says 10ms, but the actual body counts 1,000,000 polling
iterations with no delay; do not expose this as a measured microsecond timeout.

`drivers/chips/ws53/porting/security_unified/trng_ipc_acore_buffer.c` registers
IPC_MSG_UPDATE_SHARE_RANDOM_NUMBER. Application main calls the registration.
The callback reads type/address/length from the message, adds **0x10000000** to
the control-core address and fills the entire or one half of the requested buffer
with application-generated random bytes. The source does not validate payload
length, mapped range, arithmetic overflow or alignment before these operations;
those checks and ownership must be designed explicitly for Rust. Its target
buffer is dynamic, not the static IPC message queue. The control-core companion
named by CMake is absent from this release. The installed custom gateway firmware
and its buffer protocol are not established by this SDK audit.

This proves an application-facing TRNG service with cross-core consumers; it
does not prove exclusive application ownership, a safe handover, or permission
to overwrite RAM used by either service. Future HAL must coordinate the existing
service or use an explicitly verified handover. No HAL, model, hardware random
read, statistical/cryptographic-quality test or stable API is claimed here.
Current map: **29 peripherals, 791 register views, 1612 fields, 20 IRQs**.

## eFuse v151 read-service subset

SDK `163d9150ab94a02d09a05f623351f9facee3025e`, selected application config:
DRIVER_SUPPORT_EFUSE, EFUSE_USING_V151, EFUSE_SWITCH_EN, EFUSE_REGION_NUM=1.
Sources are `drivers/chips/ws53/porting/efuse/efuse_porting.{c,h}` and
`drivers/drivers/hal/efuse/v151/hal_efuse_v151{,_reg_def,_reg_op}.{c,h}`.
Port base is **0x57024000**. Boot status at +0x2c has manual state[1:0] and
boot0/1/2-done bits2/3/4; only region0 is configured. No IRQ or reset values
are inferred. The inactive-region status fields do not imply extra OTP regions.

The 2048-bit region contains 256 logical bytes. Reads use **32-bit MMIO** at
`base + 0x800 + (byte_index / 2) * 4`, then choose bits7:0 for an even index or
bits15:8 for an odd index. The RDL `DATA_READ[128]` preserves this stride and
two-byte payload; final slot offset is +0x9fc. It is a read-only *service view*,
not a claim that the physical address can never be programmed. No OTP write
view, AVDD programming control (+0x3c), system programming switch (0x57030258),
MICLDO sequencing or burn helper is exposed. Raw unsafe pointer writes cannot
be made safe by a PAC and are outside this read-only contract.

The SDK read path is not a passive memory dump: init sets CLOCK_PERIOD +0x34
low8 to 0x1f; each read writes 0x5a5a to mode-control +0x30 low16. Both preserve
unmodeled upper bits via 32-bit accesses. RDL exposes MODE_STATUS (RO) and
READ_MODE_COMMAND (WO alias), with a field constraint of exactly 0x5a5a.
No misleading modify() exists on either view. A future driver must explicitly
preserve upper bits and coordinate other users before issuing the command;
write_with_zero is not automatically a correct hardware sequence. This
restricted command view is not the physical register's full RW/programming
interface. Clock changes, read-mode selection and all actual hardware accesses
remain unperformed in this audit.

Identity layouts are not collapsed into an unsupported universal interpretation:

| SDK interface | Source-defined interpretation | Unresolved difference |
|---|---|---|
| efuse_read_item(EFUSE_CHIP_ID) | bit0 length8, i.e. byte0 | generic HAL get_chip_id callback is NULL |
| efuse_read_item(EFUSE_DIE_ID) | bit8 length128, i.e. bytes1..16 | generic get_die_id uses EFUSE_DIE_ID_BASE_BYTE_ADDR=0 instead |
| efuse_read_mac | slots at bits1728/1776/1824, lock bits840/841/842, newest locked slot | header MAC_ADDR_1/2/LAST macros and g_efuse_cfg MAC item names use different offsets |
| version/security fields | named item-table bit ranges | interpretation of installed gateway provisioning not verified |

No chip-identity, MAC or security-field typed HAL API is promised from those
conflicting names. The raw bounded read layout is established; semantic identity
requires a selected, evidenced schema. The generic driver also performs stateful
initialization, so application visibility does not prove exclusive ownership or
that clocks/timing can be changed while control-core/boot services use the IP.

`scripts/check_ws53_efuse_layout.py --sdk /path/to/fbb_ws53` checks the four source
files against the exact SDK commit, compiles their original pure address helpers
and C layouts, and verifies all **256 logical byte addresses**, status bitfields,
port control/status addresses and the read-mode value against generated SVD.
It runs no MMIO function and requires no hardware. Normal generation remains
independent of the SDK/compiler. Export tests require RO data/status, the WO
command alias/range, 32-bit slots and absent invented resets/programming views.
No eFuse HAL, behavior model, physical read or permanent write was performed.
Current map: **30 peripherals, 923 register views, 1875 fields, 20 IRQs**.

## TIMER / TCXO integration correction (2026-10-23)

SDK `163d9150ab94a02d09a05f623351f9facee3025e`: timer/v150
`hal_timer_v150_regs_def.h`, `hal_timer_v150_regs_op.h`, `hal_timer_v150.c`,
WS53 timer porting and selected `ws53_liteos_app` config. This profile uses
**two 32-bit hardware channels**, bases 0x52002100/0x52002200, IRQ53/54.
MAX_TIMERS_NUM=8 is the software timer pool, not eight hardware channels.
Main application changes the port's 30 MHz default to CLOCK_32M. That source
configuration is not a measurement of the installed custom firmware's clock.

The channel structure places low load/current/control/EOI/raw/masked status
at +0/+8/+0x10/+0x14/+0x18/+0x1c. All use 32-bit MMIO. High words +4/+0xc
belong to the optional 64-bit path, which is disabled in the selected profile;
no 64-bit timer API is promised. Snapshot requests poll CNT_LOCK with a finite
0xffff budget. The SDK clears EOI by writing one. Its read-side comment is
ambiguous: a write-one acknowledgement does **not** prove reads have no effect.
The PAC exposes a WO acknowledgement service view and makes no read assertion.

The common-register C structure and its comments conflict:

| Common field | Compiled C offset | Header comment / previous RDL offset |
|---|---:|---:|
| abnor_intr_raw | 0x70 | 0x60 |
| abnor_imsk | 0x74 | 0x64 |
| abnor_intr_stat | 0x78 | 0x68 |
| eoi_ren | 0x88 | 0x78 |
| raw_intr_stat | 0x8c | 0x7c |
| intr_stat | 0x90 | 0x80 |

Neither interpretation has hardware proof. WS53 now omits all six common
registers instead of choosing either silently, and exposes each channel's
confirmed masked status at +0x11c/+0x21c. Legacy WS63/BS2X outputs are unchanged;
this is not retrospective validation of their imported common registers.
The optional layout checker asserts the C offsets but labels them explicitly
**unverified on hardware**. Exported addressBlock sizes exclude absent channels.

TCXO v150 uses a 32-bit control/status register at 0x57000200 and four 32-bit
slots at +4/+8/+0xc/+0x10, each carrying **16 bits**. Selected config does not
use the optional two-data-register variant. The SDK refreshes, polls valid,
and combines words3..0 into a 64-bit value. Init enables and clears a shared
counter; deinit only drops the driver pointer. A future HAL must coordinate
ownership before init, and cannot infer a private timebase or measured rate.

No TIMER/TCXO reset value was established from these sources. The WS53
integration now suppresses inherited generic-IP reset annotations via
hisi_reset_unverified, so PAC consumers cannot call a misleading reset() or
write() that depends on an assumed zero reset value. Complete command/load
writes and preserving control-field updates remain possible. Deterministic
model initialization is a simulator contract, not silicon reset evidence.

The optional SDK checker covers 17 timing offsets in addition to the existing
ADC/diagnostic/TSENSOR checks. No board access, RAM takeover or Flash write was
performed. Updated map: **30 peripherals, 919 register views, 1871 fields,
20 IRQs**. TCXO behavior modeling and hardware timing validation remain open.

## UART v151 application profile correction (2026-10-23)

Pinned SDK `163d9150ab94a02d09a05f623351f9facee3025e`,
`drivers/drivers/hal/uart/v151/hal_uart_v151_regs_def.h`,
`hal_uart_v151.c`, `hal_uart_v151_regs_op.c` and WS53 `uart_porting.c`.
The selected basic (non-PRO) structure uses **32-bit volatile slots**, not the
16-bit accesses imported from older PACs. This describes the evidenced SDK
access width, not a proof that the bus rejects every other width. UART0 is H1
at 0x52081000/IRQ39, UART1 is H0 at 0x52080000/IRQ41, UART2 is L0 at
0x57014000/IRQ42. Instance register layouts remain equivalent and derivedFrom.

DATA +4 is split into RX (RO consuming FIFO read) and TX (WO push), forbidding
misleading read-modify-write. RX_FIFO_WRITE +0x40 is a WO test-injection command,
not an evidenced receive-count read alias on WS53. RX_FIFO_CNT +0x4c exposes
its low7 count, not a reserved field. The SDK's misleading FIFO_STATUS field
names are corrected semantically: bit2 is RX **not empty**, bit3 is RX **full**.
LINE_STATUS data_available is bit5, TX shift-register-empty is bit7; framing,
parity and overrun bits1/2/3 clear on reading this status. RX_FIFO_ERR bit0 has
conditional read side effects, so it is not represented as unconditional clear.
MODEM_STATUS delta bits clear on read. TX_FIFO_READ consumes test-mode TX data.
No hardware reset values or parameter-register FIFO-depth value are inferred.

The SDK uses FIFO_CTL reads despite labeling it WO, and writes UART_PARAMETER
DMA_MODE despite labeling the whole parameter register RO. We retain WO FIFO
commands and RO parameters pending hardware evidence; a future driver needs an
explicit FIFO configuration value, not RMW on the command view. The basic
profile has no PRO UART_BUSY bit. INTR_ID includes busy-detect=7 in operation
code, but its clearing sequence is only present under PRO; no basic-profile
clear is invented. Reading INTR_ID is not claimed to acknowledge TX interrupts.

UART clock software defaults to 32 MHz and its setter ignores the bus argument;
that is not proof of a shared physical clock source. Port reset pulses use
**16-bit** accesses: H1 bit1/H0 bit0 at 0x52000168 and L0 bit7 at 0x570000b0.
The debug UART bypasses port reset. Pin routes are H1 MGPIO12/AGPIO4 mode2,
H0 MGPIO8/9 mode1 or MGPIO17/18 mode2 depending on configuration, L0 AGPIO1/2
mode1 (RX can be disabled). No pin changes or reset operations were performed.
The optional cross-core-resume branch routes L0 interrupts to acore; its presence
does not establish ownership in the installed custom firmware.

Only H0/H1 have DMA request mappings in this port; L0 returns the invalid request
sentinel. Do not expose UART2 DMA simply because the family API has three UARTs.
The SDK baud-read helper combines divisor halves with AND and loses fractional
precision; it is not a valid golden oracle. Future baud configuration must use
wide arithmetic, explicit 8x/16x oversampling and representability checks.

`scripts/check_ws53_uart_layout.py` compiles the original register header with
only platform type stubs: **25 register-view offsets/widths and 76 field
encodings** are checked without MMIO. WS63/BS2X generated SVD bytes are unchanged;
their existing silicon-specific behavior is not generalized to WS53. Updated
map: **30 peripherals, 922 register views, 1901 fields, 20 IRQs**. UART HAL
configuration, full IRQ/error paths, DMA, timing and physical-board validation
remain incomplete; console output alone proves none of those capabilities.

## UART reset/routing controls and clock conflicts (2026-10-23)

The same pinned SDK `163d9150` establishes two shared halfword reset registers
in `uart_porting.c`: H0/application UART1 bit0 and H1/application UART0 bit1 at
`0x52000168`, L0/application UART2 bit7 at `0x570000b0`. The helper performs a
16-bit RMW clear followed by a 16-bit RMW set. It does not specify a pulse delay,
completion poll or reset contents. The configured software-debug UART bypasses
this reset. The RDL therefore exposes active-low fields with unknown resets;
it does not imply that the control core has released the UART or that resetting
a live console is safe.

`acore/platform_core.h` names `0x57000000` as `GLB_CTL_M_RB_BASE`. The new
`GLB_CTL` instance contains only the above reset and the evidenced full-word
L0 IRQ-routing command at `+0x48`. `uart_port_config_pinmux()` writes exactly
`1` under `CONFIG_UART_MULTI_CORE_RESUME`, described as acore-only routing.
Physical read semantics and other values are not established. This is a WO
command view constrained to 1, not an invented one-bit RW selector. The SVD
constraint documents the command; unsafe raw writes still require caller checks.
The adjacent TCXO window is a separate existing peripheral, not absorbed here.

Clock initialization is **not closed** by these controls:

- `ws53_liteos_app.config` disables `CONFIG_UART_SUPPORT_LPC`; the only call to
  `uart_port_clock_enable()` in `driver/uart/uart.c` is under that condition,
  and no WS53 implementation was found in the SDK source tree.
- `hal_clocks_application_private.h` names UARTL0 as M_CLKEN0 bit9 and H1 as
  M_CLKEN1 bit5. WS53 `clock_recover.h` unions instead mark both positions
  reserved. The generic header also assigns SPI/ADC bits inconsistent with the
  WS53 concrete ports. H0 bit4 agrees, but agreement for one UART cannot justify
  writes to the other two alleged gates. The RDL deliberately omits them.
- `dev_work_clock_init()` sets a software UART clock value to 32 MHz. Its port
  setter ignores the bus argument and writes no clock source/divider register.
  That software value cannot establish all three physical UART clock paths.
- `clock_recover.c` writes zero to `0x52001104` to enable UART auto-gating, but
  provides no per-instance field/source/divider semantics. It is not modeled as
  a manual UART gate or a frequency selector.

The optional SDK checker now compares three reset field mappings, the 16-bit
clear/set path and the constrained routing command in addition to 25 UART
register views/76 fields. Export tests reject writable gates at the conflicting
bit positions. Legacy WS63/BS2X SVD output is unchanged. Current WS53 map:
**31 peripherals, 925 register views, 1905 fields, 20 IRQs**. No hardware register
was accessed, and no full clock-aware UART constructor is claimed.

## GPIO v150 basic-bank audit (2026-10-23)

Pinned SDK `163d9150`, `hal_gpio_v150_regs_def.h`, `hal_gpio_v150_regs_op.h`,
`hal_gpio_v150.c`, `hal_gpio_v150_comm.c` and WS53 `gpio_porting.{c,h}` establish
three banks at `0x5700c000`, `0x57028000`, `0x57010000`, with one group each and
bit spans 23/12/3. Their local IRQs are 34/33/29. The generic menuconfig
`GPIO0_WIDTH=32`/`GPIO_BANK_NUM=1` is not the three-instance port geometry.
MULTISYSTEM is disabled. The original C basic group is 0x40 bytes; fourteen
32-bit slots through +0x34 are now represented, including SW_CTL at +8.
SW_CTL has bit read/write helpers but no established source-selection encoding.
It is an opaque bitmap, not a claimed software/hardware selector enum.
LOCK +0x200 and LP_STATE +0x204 have declarations but no established field
semantics and remain absent, as do the inactive MULTISYSTEM command windows.

Direction/type/polarity/dual-edge are per-pin bitmaps. A whole-field enum value
`Input=1` would mean only bit0 is input, not that the entire bank is input.
The WS53 `RAW_BITMAP` IP parameter exports `PINS` fields without those misleading
whole-field enums; legacy chips retain their existing output unchanged. No WS53
GPIO reset values are proven. The integration suppresses inherited resets and
does not offer Resettable on these registers.

EOI/SET/CLR are command views: write-one clear pending edge, set output latch,
clear output latch. They remain WO and have explicit modified-write semantics.
SDK bit helpers read-modify-write these commands, and its suspend/resume copies
the register block (including commands/status). Neither is a safe template for
the Rust HAL: do not read command registers or replay snapshots over them.
The SDK rebase routine instead writes enable=0, mask=all ones, EOI=all ones.
GPIO input and output reads both use SW_OUT; output direction is 0, input is 1.
IRQ registration supports low/high/falling/rising/dual edge and enables debounce
after reconfiguration. Debounce filter timing is not established here.

Bank width is not a physical pad count. GPIO0 positions 1/6/7/11/16 are routed
through other SDK pad identities, and 14/15/21 belong to SGPIO0/1/2. GPIO2's first
two bits share SWD physical pins. The SDK range-based pin lookup does not reject
all these GPIO0 holes. A future typed HAL must canonicalize aliases and reserve
debug pins; it must not accept arbitrary bank indices just because they fit the
register width. `gpio_select_core()` and `gpio_ulp_int_en()` are no-op ports,
not evidence of resource handoff or clock control.

`check_ws53_gpio_layout.py` compiles the original C header, checks **42 register
view offsets/widths**, basic-group stride and the unmodeled lock/LP offsets,
and compares bank bases/bit spans to the concrete port. It does not access MMIO.
The WS53 map is now **31 peripherals, 928 register views, 1908 fields, 20 IRQs**.
WS63/BS2X SVD bytes remain unchanged. GPIO HAL, pin ownership, pad configuration,
debounce, clock management and hardware validation remain open.

## Application watchdog v151 access audit (2026-10-23)

SDK commit `163d9150ab94a02d09a05f623351f9facee3025e`, application profile
`ws53_liteos_app`. Sources are `drivers/drivers/hal/watchdog/v151/`
`hal_watchdog_v151{,_regs_def,_regs_op}.{c,h}`, the WS53 watchdog port,
`include/acore/platform_core.h`, `ws53_application/main.c` and the non-OS NMI
sources (paths below the SDK's `src/`). No new silicon evidence is claimed.

The block is eleven 32-bit slots at `0x5702b000`. The SDK-view parameter on the
shared IP adds separate read/write aliases for LOCK and CCVR, giving thirteen
SVD views. LOCK reads 0 unlocked / 1 locked; writing `0x5a5a5a5a` unlocks and
other keys lock. A key is not a readable state value, so neither view permits
RMW. CCVR_REQUEST writes 1 while CCVR_STATUS reads valid at bit1; requests cannot
accidentally write a sampled acknowledgement back. LOAD is a 24-bit value at
bits31:8; this does not prove the unknown low-byte counter initialization.

The SDK kick path writes **0x5a5a5a5a**. The register comment contradicts itself
about other keys; only the actual driver key is constrained/exported. The SDK
status definition is **0 free, 1 busy**, opposite the inherited import. Mode 0
resets on the first expiry interrupt, mode 1 on the second (subject to RST_EN),
not an asserted additional full timeout after that interrupt. EOI reads zero
and clears the interrupt per the register comment. The actual SDK clear helper
performs a read then writes 1, so it does not independently prove write-only
clear behavior. Only read-to-clear is exposed, with `modifyExternal` to prevent
passive generated Debug reads. LP/status setter helpers also do not establish
writable status hardware. All inherited reset annotations are withheld for WS53.

`check_ws53_watchdog_layout.py` compiles the unchanged original C register types
and operation helpers into a **host RAM** test, checking offsets, widths, field
encodings and actual kick/lock/latch/clear accesses. This is source-layout and
helper evidence, not a hardware or QEMU behavior model. `check.py` enforces the
asymmetric API, no reset assumptions and no invented WDT local IRQ in normal CI.
WS63/BS2X generated output is unchanged by the opt-in SDK view; legacy watchdog
semantics on those targets remain a separately reviewed migration.

The port defaults to **32,000 Hz**, not WS63's 24 MHz; an FPGA branch uses
31,902 Hz. Neither is a measurement of the current board. Application startup
sets bits2/3 at `0x57030900` using halfword RMW, then the port releases bit0 using
word RMW and waits 60 us. Full reset/clock ownership and mixed-width effects
remain to be audited before a safe constructor.

WDT uses an NMI aggregation path: the port's local-IRQ registration is empty,
and `watchdog_func_adapt` enables `NMI_CWDT`. The current non-OS source header
`kernel/non_os/non_os/non_os.h` calls that bit **1**, whereas the bundled ROM
header `drivers/chips/ws53/rom/app/kernel/non_os/non_os/non_os.h` calls it **2**.
The selected include path must be proven before binding the route. The NMI block
base is `0x52000700`, but source/ROM status offset conventions also differ.
`B_WDT_IRQN = LOCAL_INTERRUPT0 + 60` is not assigned to the application watchdog.
NMI delivery, stage progression after EOI, lock protection range, clock loss,
reset timing and physical watchdog recovery are still unverified; no complete
watchdog HAL/model claim follows from the PAC.

## Resolved application NMI source profile (2026-10-23)

The source/ROM discrepancy above is resolved **for the pinned
`ws53_liteos_app` source configuration**, not for the installed custom firmware.
`check_ws53_nmi_profile.py` runs the SDK's unchanged `TargetEnvironment` resolver
in an isolated temporary copy (its import removes bytecode caches). The expanded
RAM set contains `non_os` and `watchdog_port`; neither active RAM nor ROM set
contains `non_os_rom` or `non_os_ram`. The checker then executes the original
CMake `component_on_rom()` macro: only `non_os` is selected. The pinned
`build_component.cmake` exports public header directories only for selected
components and links their interfaces into the target. Both candidate component
files export their own directory, and these are the only two `non_os.h` files
in the SDK. Thus the selected application path is the source header, with
**NMI_CWDT = 1**, not the bundled ROM header's bit2.

The original source `kernel/non_os/non_os/non_os_nmi.c` is compiled unchanged
with the selected original header. Only the MMIO and IRQ-save primitives are
replaced by host mocks; dependency output checks the selected header. Tests
verify halfword enable RMW at `0x52000700`, raw-status read at `+4`, masked-status
read at `+8`, neighboring enable-bit preservation, IRQ save/restore, five source
classifications and the no-source case. The checker compares the corresponding
three 16-bit M_CRG registers and fifteen fields to the generated SVD. Read-only
status has no speculative clear-on-read or writable-clear API. The status source
names are software route evidence, not proof that every named engine is usable
or application-owned. Bit0/GWDT is omitted: the selected source classification
does not handle it. Unknown bits and reset values remain unmodeled.

This is not a complete vendor build and does not check its linked ELF, nor the
currently installed firmware. It also does not prove CPU NMI entry/return,
maskability, repeated/nested NMI behavior or watchdog reset sequencing. No local
interrupt number or RISC-V PLIC route is invented. The previous WDT register
checker continues to report both header values as source context; the profile
checker supplies the missing component-selection evidence.

## TCXO source-helper and clock-epoch audit (2026-10-23)

The pinned SDK `163d9150ab94a02d09a05f623351f9facee3025e` defines the application
window at `0x57000200` and the control-core window at `0x57000214`. Different
windows do not prove independent physical counters or exclusive ownership.
The application profile selects four 16-bit data fields in 32-bit slots, not
the optional two-word layout. No hardware reset values or TCXO IRQ are inferred.

`scripts/check_ws53_tcxo_contract.py --sdk <checkout>` compiles the unchanged
`drivers/drivers/hal/tcxo/hal_tcxo_v150.c` and `hal_tcxo_v150_regs_op.c` into a
host-RAM test. It verifies all five SVD offsets/widths, enable-then-clear,
high-to-low counter assembly excluding the reserved upper halves, the ready
snapshot path and deinit leaving hardware enabled. It checks every source file
against the pinned Git object and records hashes. This does not emulate latch
handshakes, run on silicon, or execute the SDK's `0xffffffff`-iteration timeout.

The WS53 port defaults to 32 ticks/us and application startup selects that same
reference rate. The value is configuration evidence, not a measured board clock.
Raw counter ticks require an explicit frequency for conversion. SDK LPM support
records a software compensation value, then reinitializes/clears the counter on
resume; raw samples cannot supply monotonic uptime across this transition.
IRQ locking in the public driver does not establish control-core/NMI exclusion.
The helpers set command bits with RMW but do not prove their hardware readback
or self-clear latency; drivers must not replay an unrelated CLEAR on REFRESH.

Crucially, `build/config/target_config/ws53/target_config.py` lists
`tcxo_rom`, `hal_tcxo_rom`, `tcxo_port_rom` for the application profile. The C
helper oracle is **source evidence, not execution of these ROM routines**.
The installed custom firmware and its counter ownership remain unverified.
Clock/reset provisioning, control-core coordination, command completion timing
and sleep compensation require separate implementation and hardware acceptance.

```sh
uv run --script scripts/check_ws53_tcxo_contract.py --sdk /path/to/fbb_ws53
```

This optional SDK-dependent check is not part of ordinary Cargo builds or the
SDK-free register-generation CI. It changes no RDL or generated PAC interface.

## RTC v150 application profile and access corrections (2026-10-23)

Source pin `163d9150ab94a02d09a05f623351f9facee3025e`, files under SDK `src/`:
`drivers/drivers/hal/rtc_unified/{hal_rtc.c,hal_rtc.h,v150/*}`, WS53
`porting/rtc_unified/rtc_porting.{c,h}`, acore platform/IRQ headers, application
menuconfig and target configuration. `check_ws53_rtc_contract.py` records exact
input hashes and compiles the unchanged C HAL with host-RAM backing and a delay
recorder. It is source evidence, not execution of the configured ROM components
`rtc_unified_rom`, `hal_rtc_unified_rom`, `rtc_unified_port_rom` or current firmware.

There are **two configured 32-bit channels**, at `0x57029100` / `0x57029200`, with
absolute local causes 49 / 50. `RTC_CLOCK_MAX_NUM=4` and `RTC_MAX_NUM=4` are port
capacity values: the address table initializes only two entries, and
`CONFIG_RTC_MAX_NUM=2` bounds the register-pointer array. They are not proof of
four usable instances. Both `CONFIG_RTC_0_WIDTH_64` and `_1_WIDTH_64` are zero.
The high-word slots exist in the register type but are not used by this profile.
64-bit loading would additionally require a shared common-bank lock transaction;
no common-bank or cross-core ownership is established here.

Each channel has eight 32-bit slots. CONTROL bits are enable0, mode2:1,
interrupt-mask3, reset-FSM4, snapshot-request5 and snapshot-ready6. Ready and
raw/masked interrupt status now expose read-only fields for WS53. EOI exposes
only the actual SDK write-one acknowledgement: the header's read-to-clear
comment is not treated as verified read behavior. No W1C storage semantics or
automatic clearing timing is inferred. All WS53 RTC reset values are withheld.
Mode encodings 0/2=one-shot, 1=periodic, 3=free-run come from the SDK enum;
the actual source configuration routine always selects mode0. WS63/BS2X output
is unchanged by these instance-specific corrections.

The selected source profile calls a **65 us delay after start** and **32 us after
stop**, to synchronize to the low-speed domain. The default calculation rate is
32768 Hz; changing the port's rate variable does not change the physical clock.
These constants do not establish board crystal population or clock/reset setup.
LPM support is off in this profile. The new HAL must require a working explicit
delay provider and verified clocks rather than using an instruction-count delay.

Original helper tests verify both channels, 16 register offsets, control/mode
encodings, low-word-only load/read, no common unlock/high-word write, delays,
EOI before callback and deinit leaving hardware unchanged. They also preserve
two source API pitfalls as evidence: a disabled counter, a 65535-poll timeout,
and a successfully sampled zero all return zero; the active read path never
sets `*is_enable=true`. A Rust implementation must return distinct errors and
must not read stale data after the polling budget expires. These tests do not
prove expiry edges, counter reload, mask behavior, clear completion, IRQ
delivery, low-power wakeup or recovery on a physical board.

```sh
uv run --script scripts/check_ws53_rtc_contract.py --sdk /path/to/fbb_ws53
```

## PWM v151 application source contract (2026-10-23)

Source pin `163d9150ab94a02d09a05f623351f9facee3025e`: application menuconfig,
acore platform/IRQ and clock-recovery headers, PWM public/port/HAL headers, and
the original PWM driver, HAL and port C files. The optional
`scripts/check_ws53_pwm_contract.py --sdk /path/to/fbb_ws53` hashes every input
against that commit and compiles the unchanged implementation with host RAM,
clock/IRQ stubs and a delay observer. It does not access MMIO, ROM or a board.

The selected profile uses 4 groups, 8 channels, LPM support and no PRELOAD.
PWM is `0x52082000`, local causes 71/72; SPWM IRQ definitions alone do not prove
a second usable application instance. All 115 word slots and field encodings
are checked against the C types and generated SVD. High count halves remain
16-bit fields in the PAC, but the application helper writes only low halves.
The SDK port limits low+high to 255 and offset to low; the public driver limits
cycle count to 32767. Neither limit proves the physical counter width. WS63's
measured high-half behavior must not be transferred to WS53.

The clock port sets M_CLKEN0 bit7, then accesses PWM_M_DIV3 (`0x5200007c`) as a
halfword: enable bit0, clear load bit1, program divider[6:2]=16, set load bit1.
Enable preserves unrelated bits; disable clears the gate and writes zero to
the whole divider halfword. The new shared-control register describes this
source evidence, not application-exclusive ownership. The port reports 32MHz;
the input/divider relationship, frequency and waveforms remain unmeasured.

INT_MASK is an **event-enable** field: bit0 abnormal, bit1 step-end, 1 permits
delivery. The original init writes3 with a remove-mask comment. WS53 adds named
encodings and withholds all PWM reset values. START is a write-one commit
command; clear words are write-only acknowledgement masks. SDK helpers RMW
these command words, but that does not establish meaningful readback or safe
RMW. Self-clear claims are removed for WS53; no W1C storage semantics, read
side effects or clear latency are invented. WS63/BS2X outputs are unchanged.

For non-repeating pulse sequences, source group start first enables selected
channels and commits with STARTCLRCNT=1, then stages EN=0 for channels with
nonzero cycles, waits1us, and commits again with STARTCLRCNT=0. Host tests
observe both sides of the delay for all four groups, including a continuous
cycles=0 channel; they cannot prove the output waveform or stop edge.

The unchanged source also exposes hazards the Rust implementation must avoid:

- Its u32 low+high check accepts both zero and overflowing sums.
- The group helper accepts channel8 even though this profile configures0..7.
- Repeat is one global variable: opening channel1 as repeating changes a
  previously configured non-repeating channel0's subsequent group-start path.
- Registering any channel enables both shared IRQs; unregistering any other
  channel disables both. Ownership/lifetime must therefore cover the shared
  block, groups and IRQs, rather than independently tearing down each channel.
- The direct HAL callback executes before step-end acknowledgement. Register
  deinit only forgets the base pointer; it does not stop or mask the device.

These behaviors are reproduced by executable source checks. They are not
claims about the installed custom image, ROM, silicon reset or low-power
correctness. RDL/PAC is prepared; PWM HAL/model/physical validation remain open.

## WS53 I2C source/PAC contract (2026-10-23)

Source pin remains `fbb_ws53 163d9150ab94a02d09a05f623351f9facee3025e`.
`ws53_liteos_app.config` selects non-PRO v151, master, LPC/LPM and Fast-plus;
slave/interrupt/DMA are disabled in that reference build, high speed is disabled.
The port owns two configured instances at `0x52083000/0x52084000`, IRQ62/63,
8-entry TX/RX FIFOs, and reports 32MHz. It toggles M_CLKEN1 bits6/7 with halfword
RMW. These are SDK integration facts, not installed-image or silicon proof.
Shared clocks, pads and bus ownership still require a separate handoff.

`check_ws53_i2c_contract.py` compiles the original regs/master/port C sources
against host RAM and stubs after validating every input against that pin. It
checks all 36 register views per instance and named fields against generated
SVD, both clock gates preserving unrelated bits, every restart/stop flag
combination, interrupt mask polarity, and the actual clear-all write of 1 to
`IC_CLR_INT +0xc4`. No bus or probe is opened. Normal source count results at
32MHz are high/low 153/159 (100kHz), 32/40 (400kHz), and 6/21 (1MHz); the last
uses the source's mode3 and HS count slots. These are encodings, not measured
bus frequencies or proof that generic DesignWare high-speed semantics apply.

Do not copy these source defects into a HAL:

- Header comments call clear registers read-to-clear, but source helpers write.
  `ic_v151_clr_intr_data_t` exceeds 32 bits: compiled TX_ABRT/STOP/START helpers
  write `0x10000000`/`0x80000000`/`0`, unlike the status bitmap. A separate
  `set_clr_intr` helper writes a right-shifted value to ENABLE_STATUS, not CLR.
  Selective clear positions/readback are unresolved. Only the source clear-all
  command is modeled; no selective W1C or read-clear semantics are invented.
- ENABLE setters read-modify-write the command-containing register, replaying
  ABORT if read back set. FIFO force-stop/restart helpers also read a receive
  FIFO register before writing a command; Rust must keep separate read/write views.
- With Fast-plus enabled, the HS enum becomes3; `speed_mode+1` truncates to zero
  in the two-bit field. The master init configures counts/mode before returning
  the high-speed rejection. Counts above16 bits silently truncate; zero baud
  reaches a division by zero if it reaches the calculator. Validate before MMIO.
- The legacy BS2X HAL clears by reading CLR_INTR and emits independent STOPs
  for each embedded-hal Operation. This is not a suitable WS53 transaction
  implementation. Legacy outputs/APIs are preserved pending a separate review.

The shared IP now has an opt-in `SDK_VIEW`, used only by WS53: RX-pop/TX-command
aliases at0x1c, RO status/abort/level fields, WO clear commands, named abort causes,
and no claimed reset values. Both WS53 instances use it. WS63/BS2X generated
outputs remain byte-identical. No I2C HAL, model, bus timing or HIL support is
claimed by this source/PAC milestone.

## SPI/QSPI compiled source contract (2026-10-23)

`check_ws53_spi_contract.py` pins every consumed SDK input to `163d9150` and
compiles the original register/adaptation helpers and SPI port against host RAM.
The normal `ws53_liteos_app.config` has `CONFIG_DRIVER_SUPPORT_SPI` unset. With
no explicit version selector, `hal/spi/CMakeLists.txt` falls back to v151_100;
the oracle explicitly enables the optional SPI port for the test. This is not
proof the installed firmware has initialized or handed over either bus.

The test checks all modeled fields and 98/99 expanded register views for
SPI0/QSPI1, 36 shared FIFO windows per direction, IRQ65/43, the mode selector,
application DMA requests6/7 and4/5, and eight M_CRG field encodings. C structs
are the offset oracle; compiler bitfield writes are checked against SVD masks.
SPI0 and QSPI1 cannot share one register struct or status/DMA mask table.
Field-specific adaptation helpers map each layout; **whole-word setters simply
write the supplied value**, so they do not translate QSPI-format constants.

SPI0 clock enable writes full halfword1 to0x5200055c and modifies bit11 of
M_CLKEN1 (the source reads32 then writes16). QSPI1 modifies bits1/3/5 at
0x52001240, writes1 to the complete nine-bit field at0x52000574, and sets
GLOBAL_CLKEN bit7 at0x52000548. Disable reverses these. Other bits are preserved
except SPI0's full SPI_CLOCK halfword assignment. New RDL includes the missing
QSPI gate chain without inventing a divider formula or claiming XIP ownership.
Gate enable alone does not establish a32MHz source or released resets.

The original frequency helper produces divider32/3/2 for requested1/10/12MHz
at32MHz input. The source does not round up to an even divider. Both headers
say divider bit0 is fixed zero and valid enabled values are2..65534; thus output
can exceed the request if these hardware semantics hold. The host oracle only
proves written values, not real clock frequency. Rust must validate wide
arithmetic, use a legal even divider and report the achieved source-profile rate.

Three source unions have malformed reserved declarations (`spi_wsr_data_t`,
`spi_enhctl_data_t`, `sr_data_t`) and occupy more than4bytes, while the actual
MMIO structs use32-bit slots. All modeled nonreserved field positions still
match the SVD. Do not use these union sizes as register strides. Clear-all SDK
helpers do a read/modify/write; the headers specify read-clear and ignored
writes, so the PAC retains read-only clear registers and never emits RMW.
The RAM-backed test cannot establish the physical read side effect.

FIFO depth, clock/reset/pad handoff, QSPI XIP conflicts and control-core use
remain unverified. No device scan, Flash transfer or hardware operation occurs
in this contract. SPI/QSPI HAL/model implementations are still outstanding.

## I2S/SIO compiled source contract (2026-10-23)

`check_ws53_i2s_contract.py` checks the pinned SDK `163d9150` inputs before
compiling its original `hal_sio_v151_regs_def.h` and full `sio_porting.c` on the
host with OS/MMIO stubs. The default application enables I2S v151, one bus,
with DMA and loopback options disabled. The instance is I2S0 at physical block
base `0x52030000`, local IRQ67; the SDK's pointer is `0x5203003c` (VERSION).
All 25 offsets/32-bit slots and 70 fields are compared against SVD, including
compiler-produced bitfield masks. This does not prove physical behavior.

The legacy imported family RDL is substantially incorrect for this SDK:

- MODE bits0/1 select I2S/PCM and PCM timing; channels are bits5:4 (2/4/8/16),
  edge bit6 (0 falling, 1 rising), master bit7. SDK configure selects rising.
- CT_SET/CT_CLR use bits8..15; TX/RX enable bits12/13 and reset_n bit15.
- DATA_WIDTH_SET uses TX bits2:0 and RX bits5:3, with 0..4 =16/18/20/24/32 bits.
- Interrupt status, clear and mask contain six sources: RX/TX thresholds,
  independent left/right RX overflow and independent left/right TX underflow.
  SDK masks0x3d/0x3e enable TX/RX respectively (one masks).
- CRG uses bits0/1/2/4, not the imported divider bits7:0 and clock bit8.
- Added START_POS, POS_FLAG, POS_MERGE_EN and merge RX/TX data registers.
  Split and merge RX reads consume data; TX and clear commands have no read API.
  POS_FLAG is writable because configure explicitly writes zero; dynamic
  flag behavior is not established. No register reset value is assumed.

The WS53-only RDL correction preserves byte-identical WS63/BS2X SVDs. It does
not certify their old imported model. CT_SET/CLR are exposed as write-only
command views: the SDK enables/disables TX by writing one to SET/CLR and resets
with CLR then SET. Generic helpers read both command registers and write zero
to SET to disable RX, but no evidence establishes that zero works or readback
is safe to replay. Formal W1S/W1C semantics for these two registers are withheld
until verified; a future HAL must use explicit commands, not helper RMW.

The compiled port confirms clock order: set `0x57008600` bit0
(`CMU_DIV_AD_RSTN_SYNC_BIT`), then `0x52000040` bit2 (I2S gate); disabling clears
in the same order. `common_def.h` implements these as 32-bit RMW. The existing
M_CRG.M_CLKEN0 view is 16-bit for other ports. The additional M_CLKEN0_WORD
view now provides the same six fields at the same address with 32-bit access;
the existing 16-bit interface is unchanged. AUDIO_CMU.NEW_CFG0 at0x57008600
exposes only bit0, with no reset assumption or invented frequency. The source
oracle executes the original set/clear macros over host RAM and checks that
upper-half and neighboring bits survive all four operations. Both clock views
are one shared resource, not separate claimable devices. Audio clock/reset
ownership and control-core synchronization still require an explicit handoff.
Enabling must not briefly assert the shared reset; normal release should leave
shared gates enabled until shutdown ownership is proven.

Twenty original divider calls (five widths times four channel counts) confirm
integer division occurs before conversion to float: the result is exactly
`floor(32000/(25*width*channels))`. The apparent rounding branch cannot recover
the discarded fractional part. This is a source fact, not proof of clock rate
or a suitable arbitrary-rate Rust algorithm. The port hardcodes pinmux calls
for SDK S_MGPIO17/20/19/18, mode3, while application config lists pin values
13/14/15/16 and modes35..38; those config numbers must not be used as board pads.

The SDK assumes FIFO depth8, TX refill4 and RX threshold7. TX completion follows
the enqueue semaphore with a fixed295us delay; there is no evidenced BUSY/drain
status. Source deinit only drops the register pointer/semaphore. Future HAL and
models must distinguish queued data from wire completion, bound waits, handle
short RX bursts and retain resource ownership if stopping cannot be proven.
I2S HAL, model, audio waveforms and physical loopback remain outstanding.
