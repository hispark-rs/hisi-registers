# hisi-registers

Canonical, reusable register descriptions for HiSilicon embedded IP blocks.

This repository explores a SystemRDL-first flow:

```text
manuals / SDK headers / silicon observations / imported SVDs
                         |
                         v
               shared SystemRDL IP blocks
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

Experimental. The first tracer bullet models the common SSI v151 controller used
by WS63 and BS2X. Generated files are not yet authoritative inputs for released
PACs.

PeakRDL currently provides a CMSIS-SVD importer, not an exporter. This repository
therefore owns a deliberately small fail-closed exporter in
`scripts/export_svd.py`. Expanding that exporter is part of the experiment and
must be backed by golden SVD and `svd2rust` compatibility tests.

## Layout

- `rdl/ip/`: reusable, versioned peripheral IP definitions.
- `rdl/chips/`: chip integration maps (instances and base addresses).
- `evidence/`: source and confidence records; no restricted vendor material.
- `generated/`: reproducible generated artifacts.
- `scripts/`: generation and drift checks.

## Generate

Install `uv`, then run:

```bash
./scripts/generate.sh
./scripts/check.sh
```

`check.sh` also asks an installed `svd2rust` to parse both generated files.

## Truth and provenance

Every register fact must record its evidence class. A generated SVD is an export,
not a separately maintained truth source. See `evidence/README.md`.

## License

Apache-2.0. Imported source material remains subject to its original license and
must not be copied into this repository unless redistribution is explicitly
permitted.
