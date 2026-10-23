# hisi-registers

Canonical, reusable register descriptions for HiSilicon embedded IP blocks.

This repository explores a SystemRDL-first flow:

```text
manuals / SDK headers / silicon observations / imported SVDs
                         |
                         v
          reviewed per-chip SystemRDL blocks
                         |
                         v
                  per-chip address maps
                         |
                         v
               generated CMSIS-SVD files
                         |
                         v
                     svd2rust PACs
```

## Status

Experimental. Complete split baselines have been bootstrapped from the current
WS63 and BS2X PAC SVD inputs. They cover every peripheral, expanded register,
field and interrupt represented by those snapshots. Generated files are not yet
authoritative inputs for released PACs.

The WS63 baseline is being reconciled against `fbb_ws63` and the semi-official
SVD. The BS2X baseline is explicitly provisional: `fbb_bs2x` is its behavioral
source of truth because the existing BS2X SVD has substantially lower maturity.
An SDK-first WS53 map starts with confirmed shared IP and expands only after each
variant and core owner is established from `fbb_ws53`.
Names such as "v151" are not sufficient proof by themselves. SPI is shared only
because the two SDKs contain byte-identical v151 register definitions and
register-operation sources. Chip-specific SPI integration remains in each chip map.
Watchdog v151 and SFC v150 are confirmed across all three SDKs. PWM v151 is also
shared after a semantic comparison that ignores comment-only formatting changes.
Timer v150, TCXO v150 data16, GPIO v150 basic and UART v151 basic are
also shared by all three chips. I2C/RTC retain narrower family variants.
WS53 SIO v151 uses a corrected chip-specific layout: the legacy imported SIO
model has incorrect MODE/control/width/clock fields and missing registers.
WS63/BS2X output is preserved pending its own source audit.
Configuration-selected alternatives are tracked in
`evidence/configured-ip-variants.md`.

PeakRDL currently provides a CMSIS-SVD importer, not a maintained SVD exporter.
This repository therefore owns a deliberately small fail-closed exporter in
`scripts/export_svd.py`. Expanding that exporter is part of the experiment and
must be backed by golden SVD and `svd2rust` compatibility tests.

Shared IP declarations do not establish chip-specific reset values. Mark an
unverified integration, register file, register or field with
`hisi_reset_unverified = true`: the exporter omits its reset value/mask even if
an imported IP has legacy reset annotations. A child cannot override an
ancestor's veto. Unknown bits outside a verified reset mask stay unknown.
WS53 TIMER/TCXO use this guard; TIMER also omits the disputed common window
documented in `evidence/ws53-source-matrix.md`.

## Layout

`uv run --script scripts/check_ws53_i2s_contract.py --sdk /path/to/fbb_ws53`
compiles the pinned SDK C definitions and port, checks all 25 I2S registers and
70 fields plus 13 clock-view fields against the generated SVD, and exercises
the original divider over 20 width/channel combinations. Original clock RMW
macros execute against host RAM and preserve neighboring/upper bits. This is
source evidence, not hardware or HAL support. See `evidence/ws53-source-matrix.md`
for unresolved ownership and command semantics.

`uv run --script scripts/check_ws53_i2s_dma_contract.py --sdk /path/to/fbb_ws53`
executes pinned I2S DMA helpers and public transfer paths with explicit host
mocks: 2304 parameter combinations, 196 DMA attribute combinations and 28
normal/error sequences. It checks merge DATA addresses and requests 12/13,
records narrowing and missing cleanup, and demonstrates that the SDK DMA flags
do not provide a request-disable operation. Neither the mocks nor the public
width fields establish physical DMA lane or stereo packing behavior.

`uv run --script scripts/check_ws53_trng_contract.py --sdk /path/to/fbb_ws53`
executes the pinned unified-security TRNG HAL and byte driver against explicit
FIFO/ring/delay stubs: 24 control/read paths and 21 byte-fill cases. It checks
readiness races, discarded comparison words, rejected output on timeout,
partial byte fills and ring-control preservation. The bounded SDK loop counts
polls, not microseconds; this oracle establishes neither entropy quality nor
cross-core ownership. GCC and Clang produce identical evidence.

`uv run --script scripts/check_ws53_efuse_contract.py --sdk /path/to/fbb_ws53`
executes original v151 read paths against protected anonymous host RAM on
Linux/x86-64. It checks 8192 state/byte combinations, all 32896 nonempty valid
buffer ranges, 16 partial failures, upper control-bit preservation and IRQ-lock
cleanup. The simulated OTP page is read-only, programming helpers are never
called, and no physical eFuse or device mapping is accessed. This complements
the existing pure-address layout checker; it does not establish read timing,
identity schemas or exclusive controller ownership.

`hisi_svd_word_view` explicitly adds a 32-bit little-endian access view of a
16-bit RW register, retaining one RDL field source. It requires source evidence,
unknown resets, four-byte alignment and a vacant upper halfword in the existing
block extent. Arrays, side effects, whole-register write constraints, collisions
and other sizes fail closed. WS53 M_CLKEN0 uses this for PWM's halfword and I2S's
word accesses; it does not create an independent resource or new upper fields.

- `rdl/ip/`: reusable IP definitions only after cross-chip equivalence is proven.
- `rdl/chips/<chip>/peripherals/`: split chip-specific peripheral definitions.
- `rdl/chips/<chip>.rdl`: chip integration maps, instances and interrupts.
- `evidence/`: source and confidence records; no restricted vendor material.
- `generated/`: reproducible generated artifacts.
- `scripts/`: generation and drift checks.

## Generate

Install `uv`, then run:

```bash
./scripts/generate.sh
./scripts/check.sh
```

`check.sh` validates all three generated files against the vendored CMSIS-SVD
1.3.9 schema, checks chip counts and semantic features, then asks an installed
`svd2rust` to parse them. New WS53 FIFO arrays retain their dimensions and access
side effects; imported WS63/BS2X arrays remain expanded.

Known bootstrap limitation: the SVD importer expands register arrays. Addresses
and field behavior are retained, but the original PAC array API shape is not.
The checker reports the 39 WS63 and 11 BS2X source arrays that still require a
controlled compaction pass. No PAC may migrate until that API-shape gate closes.

With a local pinned WS53 SDK checkout, ADC/diagnostic C structure offsets can
also be compared against the generated map without copying vendor headers:

```bash
uv run --locked scripts/check_ws53_sdk_layout.py --sdk /path/to/fbb_ws53
uv run --script scripts/check_ws53_watchdog_layout.py --sdk /path/to/fbb_ws53
uv run --script scripts/check_ws53_nmi_profile.py --sdk /path/to/fbb_ws53
```

This optional source-layout check is not required by ordinary generation or
Cargo builds and does not access hardware.

## Truth and provenance

Every register fact must record its evidence class. A generated SVD is an export,
not a separately maintained truth source. See `evidence/README.md`.

## License

Apache-2.0. Imported source material remains subject to its original license and
must not be copied into this repository unless redistribution is explicitly
permitted.

The optional WS53 I2C source oracle checks the pinned vendor C against host RAM
(no hardware): `uv run --script scripts/check_ws53_i2c_contract.py --sdk /path/to/fbb_ws53`.
It verifies the non-PRO instance layout, FIFO command aliases and clock gates,
and records contradictory clear helpers instead of guessing selective-clear bits.
See the WS53 source matrix for remaining timing/ownership limitations.

The companion DMA source oracle executes original configuration, public master-read,
ISR and failure paths with explicit DMA/semaphore/HAL mocks:
`uv run --script scripts/check_ws53_i2c_dma_contract.py --sdk /path/to/fbb_ws53`.
It records byte data versus halfword read commands, distinct TX/RX protection,
unbounded waits and incomplete error cleanup. The layout oracle also executes
actual DMA DATA lookup, threshold/enable helpers and master prepare/restore.
Neither oracle proves peripheral handshake timing, burst tails or silicon support;
the pinned application configuration disables I2C DMA.

WS53 temperature polling source oracle (host stubs, no hardware):
`uv run --script scripts/check_ws53_tsensor_contract.py --sdk /path/to/fbb_ws53`.
It checks the selected application port, six SVD register/access contracts, the
five initialization writes, lazy startup and all 1024 raw-code conversions by
compiling the original pinned C source. `--curve-output /path/to/sdk-curve.txt`
exports the original C results for independent HAL comparison. The port's loop
has no timeout and trusts any nonzero CONTROL; neither behavior is a recommended
HAL contract. Stop, READY clear/freshness, clocks and IRQ semantics remain unknown.

WS53 PWM IRQ71 RAM evidence is recorded in
[`evidence/ws53-pwm-irq71-hil.json`](evidence/ws53-pwm-irq71-hil.json).
Invalid count/group configuration continues to assert errors after W1C. Mask
abnormal-event delivery, correct and commit configuration, then clear and verify
status before re-enabling. Three identical-ELF runs recover valid8/16 pulses.
The count-error case also sets the configuration-load flag. Exact reassertion
latency, shadow-write-only recovery and invalid output waveforms are unqualified.
This adds behavioral evidence; register layout/access definitions are unchanged.

WS53 RTC0/1 RAM evidence is in
[`evidence/ws53-rtc-hil.json`](evidence/ws53-rtc-hil.json). Both instances retain
raw status under active-high masking. EOI write completion is asynchronous;
immediate ISR return can repeatedly enter the same source. Suspend local delivery,
post EOI, observe raw deassertion in bounded main-context work, then clear local
pending and restore delivery. Three same-ELF runs qualify masked one-shot,
periodic and rearmed one-shot notifications. Counter observations support an
approximately32kHz A-board profile; the source32768Hz remains a distinct profile.
Exact synchronization delay, physical clock source and low-power are unqualified.
The header's read-clear comment conflicts with the helper's EOI1 write; this test
qualifies the write path only, without changing register layout/access metadata.

Initial WS53 MDMA HIL **failed the original HAL contract**. Evidence:
[`evidence/ws53-mdma-initial-hil.json`](evidence/ws53-mdma-initial-hil.json).
The SDK/ROM eight-slot layout is not proof of eight usable physical channels:
only slots0..3 retained configuration in this board test. Their count field
reads0 before enable. A reserved channel0 PAC diagnostic successfully copies17
u32 elements with intact guards, but final addresses point to the last element,
not one-past. These failures are retained as pre-fix evidence.
No buffers were returned to Rust, and no bus-drain proof is claimed.

The corrected four-channel memory profile is recorded in
[`evidence/ws53-mdma-four-channel-hil.json`](evidence/ws53-mdma-four-channel-hil.json).
RDL/PAC descriptions distinguish programmed length from count status and record
the observed completed addresses. HAL channels0..3 each copy17 elements using
u8/u16/u32, with three physical repetitions per width. All nine runs pass full
source/destination/guard checks and exact same-ELF QEMU UART comparison. The SDK
eight-slot profile remains separate; upper-slot availability is unexplained.
PMP/MEMATTRL are checked before takeover, buffers remain retained, and original
firmware is recovered by whole-board reset. Bus drain, peripheral handshakes,
DMA async and production shared-resource handoff remain unqualified.

[`evidence/ws53-mdma-irq-hil.json`](evidence/ws53-mdma-irq-hil.json) qualifies
completion notification on IRQ58 for channels0..3 (17u32, three repeated RAM
runs). Peripheral masking retains raw TC; selective W1C preserves other channels
and error-clear does not discard TC. A source held high across LOCIPCLR58 still
delivers after local unmask, exposing a QEMU pending-clear defect now corrected
for this source only. The same ELF passes strict UART and buffer comparisons.
Physical bus-error IRQs, precise reassertion timing, post-deassertion retention,
buffer handback and peripheral-paced IRQs are not qualified by these tests.

WS53 temperature initialization/read/reattach hardware observations are recorded
in [`evidence/ws53-tsensor-initial-hil.json`](evidence/ws53-tsensor-initial-hil.json).
The source five-write sequence starts from CONTROL/status0; twelve sample
observations,32 immediate READY reads and reattachment succeed across six bounded
RAM runs, each followed by board reset and AT recovery. QEMU and hardware pass
shared transcript/formula/state checks, not raw temperature or exact-time equality.
Instrumented first READY is4.633875ms on the board versus4.106125ms in the model;
initialization itself takes1.25us versus0.28125us. This unresolved discrepancy
must not be presented as timing parity. No calibrated accuracy, freshness per
read, stop/IRQ/clearREADY, low-power or production thermal ownership is proven.

WS53 START observations are recorded in
[`evidence/ws53-tsensor-start-hil.json`](evidence/ws53-tsensor-start-hil.json).
Three strengthened RAM runs each execute two START1 commands: READY clears but
CODE retains the previous value until the next result. HAL rejects the stale
field while READY is zero. This corrected a model that also cleared CODE.
Repeat conversion latencies differ from cold initialization and from the model;
exact sampling phase, conversion clock, stop and self-clear remain unqualified.

WS53 inherited TRNG read observations are recorded in
[`evidence/ws53-trng-inherited-hil.json`](evidence/ws53-trng-inherited-hil.json).
Three bounded RAM runs inherit ring1/READY3 and stop vendor CPU clients. Each
observes16 double-ready/two-word pairs,32 HAL words,0/1/3/4/5/17-byte fills and
two reads after release/reattach, preserving ring configuration and recovering
the original firmware. The same ELF passes an exact seeded QEMU-data oracle;
physical random bytes need not match. No silicon ring/FRO/clock writes were
performed. FIFO depth, IPC ownership, physical failure detection, entropy quality
and cryptographic RNG suitability remain unqualified. No register layout changed.

WS53 watchdog command/snapshot observations are recorded in
[`evidence/ws53-wdt-command-hil.json`](evidence/ws53-wdt-command-hil.json).
Immediate snapshots after control commands can time out or report the previous
configuration's count. Waiting200us after start/feed/stop in a diagnostic fixture
passes three bounded RAM runs, including count deltas, feed, stopped snapshots
and restart. This does not establish a minimum delay or general completion
protocol; the HAL and immediate QEMU profile remain mismatched. No NMI/expiry
or reset was deliberately triggered. No register layout/access direction changed.

WS53 HAL snapshot guard follow-up is recorded in
[`evidence/ws53-wdt-guard-hil.json`](evidence/ws53-wdt-guard-hil.json).
A 63us guard avoided lost requests but still returned stale start/restart counts;
a 200us floor passed three same-ELF hardware/QEMU runs at the inherited 32000Hz
profile. This is an empirical software policy, not a new register timing fact or
qualification of expiry, NMI, reset, low-power, or other clocks. The model's
configurable rearm window does not reproduce stale control-command counts.

WS53 UART non-PRO line configuration is checked by executing the pinned C baud
and frame helpers: `uv run --script scripts/check_ws53_uart_config_contract.py
--sdk <SDK> --vectors <output.txt>`. It covers81 baud/port/clock cases and1944
frame encodings, with independent register banks. The SDK truncates fractional
division and can truncate/overflow integer inputs; HAL configurations reject
zero/out-of-range divisors. STP=1 is1.5 bits for five data bits, two otherwise.
See `evidence/ws53-uart-config-source-contract.json`.

`evidence/ws53-uart-line-hil.json` records three A-board UART2 RAM runs of72 line
configurations and144 internally looped bytes each, with console restoration.
DIV_L readback requires opening DIV_EN in the measured UART2 profile; the first
closed-window diagnostic failed while QEMU originally passed. Source/PAC text
records this condition without claiming DIV_H/write gating or other instances.
Clock/reset/pad provisioning, physical baud/frame timing, UART0/1 and production
resource handoff remain unqualified.

`evidence/ws53-uart-flush-hil.json` records the transmit-completion distinction:
three UART2 RAM runs observe FIFO empty before the shift register completes,
48 first nonblocking flushes still busy, and144 good loopback bytes retained.
HAL uses existing LSR.TX_EMPTY_S semantics; no register definition changes.
Reading LSR can consume RX errors, so software retains flags and discards only
a freshly identified corrupt head. Three-port Rust/QEMU tests cover coalesced
errors and overrun without discarding good bytes. Physical error injection,
UART0/1 HIL, exact wire timing and asynchronous wakeup integration remain open.

WS53 UART pad/reset integration is checked with
`uv run --script scripts/check_ws53_uart_port_contract.py --sdk <SDK>`.
The pinned original C helpers execute against host event sinks in five compile
profiles (195 cases): three debug-port choices, all UARTs, reset bit preservation,
pad selection/release, and the shared software frequency setter/getter.
`evidence/ws53-uart-port-source-contract.json` records that the selected product
disables LPC clock provisioning; its generic H1/L0 gate enums conflict with
reserved fields in the clock-recovery header. No gate identity is inferred.
The multicore pin helper writes L0's shared interrupt route even for UART0/1,
release leaves pulls enabled, and this port ignores the product's L0 pin numbers
19/20 in favor of AGPIO1/2. These are source-profile facts, not evidence of the
currently flashed firmware's wiring, full reset behavior, or clock ownership.

`evidence/ws53-uart-all-lines-hil.json` extends the UART line/flush experiment
to all three application instances: the same ELF passes three A-board RAM runs,
each with216 configurations,432 retained loopback bytes and144 initial flush
waits, followed by original-firmware recovery. All ports observe FIFO empty
before transmit completion. QEMU passes the same markers using its explicit
100us serializer hypothesis. UART0/1 stay in internal loopback until board reset;
physical pads, actual baud timing, clock provisioning, DMA, real receive faults
and production ownership remain unqualified. No RDL/PAC facts changed.

`evidence/ws53-uart-reset-hil.json` records three identical H1/H0 soft-reset
observations under the inherited A-board clock profile. A queued loopback byte,
nondefault frame/sampling configuration and scratch value are compared before
and after the SDK halfword clear/set sequence; both instances reconfigure and
receive successfully afterward. BAUD_CTL changes0x37→0x7f, frame/fraction/modem/
scratch clear, and the RX queue empties with FIFO enable cleared. This is not a
universal power-on reset declaration: DIV_L reads0 even before reset with RX
queued, DIV_H was already0, and L0/held-reset/neighbor isolation/IRQ/DMA remain
unqualified. No PAC reset defaults are inferred. The new observation fixture
supports a separate opt-in QEMU observation run after implementation of the
shared H1/H0 reset model. The combined HIL parity mode still refuses this case:
before reset, DIV_L and FIFO_STATUS differ on both ports. The offline comparison
preserves all differences and exits unsuccessfully until full parity is achieved.
UART_PARAMETER is now modeled as the observed0xd04 for all three instances,
independently of the DMA behavior profile. No additional physical measurements
or RDL/PAC reset defaults are implied by these model changes.

`evidence/ws53-uart-access-hil.json` supersedes the SDK FIFO-status comments with
three same-ELF, three-port RAM runs: FIFO bit2 is full at64 queued bytes, bit3
is empty with FIFO enabled, and bit4 is set for queued RX. All64 bytes survive.
DIV_EN set attempts with1/2/64 queued RX bytes do not latch; after draining,
DIV_EN sets and DIV_L reads the programmed17. The earlier reset-probe zero was
therefore an access-control failure, not proven divisor loss. SDK bit positions
remain checked separately from contradicted descriptions; the non-PRO C type
omits bit4. TX-only busy, nonzero DIV_H and busy divisor writes remain open.

`evidence/ws53-uart-l0-reset-hil.json` qualifies the separate L0/UART2 halfword
reset bit7 at0x570000b0 under inherited A-board resources. Three raw observation
runs match H1/H0's queued-clean-RX reset profile; three HAL runs also prove
IRQ-enabled/TX-busy refusal, reconfiguration, UART0/1 scratch/frame/modem
isolation and return to the inherited console pin path, with exact same-ELF
QEMU logs and vendor-firmware recovery. Existing RDL/PAC bit placement remains
unchanged. No universal reset defaults, clock provisioning, live DMA/IRQ abort
or minimum reset timing is inferred.
