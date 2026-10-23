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
