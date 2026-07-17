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
Timer v150, TCXO v150 data16, GPIO v150 basic, UART v151 basic and SIO v151 are
also shared by all three chips. I2C/RTC retain narrower family variants.
Configuration-selected alternatives are tracked in
`evidence/configured-ip-variants.md`.

PeakRDL currently provides a CMSIS-SVD importer, not a maintained SVD exporter.
This repository therefore owns a deliberately small fail-closed exporter in
`scripts/export_svd.py`. Expanding that exporter is part of the experiment and
must be backed by golden SVD and `svd2rust` compatibility tests.

## Layout

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

`check.sh` validates full-chip counts and semantic features, then asks an
installed `svd2rust` to parse both generated files.

Known bootstrap limitation: the SVD importer expands register arrays. Addresses
and field behavior are retained, but the original PAC array API shape is not.
The checker reports the 39 WS63 and 10 BS2X source arrays that still require a
controlled compaction pass. No PAC may migrate until that API-shape gate closes.

## Truth and provenance

Every register fact must record its evidence class. A generated SVD is an export,
not a separately maintained truth source. See `evidence/README.md`.

## License

Apache-2.0. Imported source material remains subject to its original license and
must not be copied into this repository unless redistribution is explicitly
permitted.
