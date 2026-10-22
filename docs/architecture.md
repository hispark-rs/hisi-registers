# Architecture

## Ownership

`hisi-registers` is intended to own reviewed register and chip-integration facts. PAC repositories
consume generated SVD snapshots but do not become alternate register truth sources.
HAL repositories own operational policy and safe abstractions, not register layout.

## Versioning model

IP definitions carry explicit hardware-family revisions only after equivalence is
proven. A matching marketing/revision label is insufficient: offsets, widths,
access, reset, side effects, interrupts and observed behavior must agree. Until
then, definitions remain under `rdl/chips/<chip>/peripherals/` rather than being
prematurely promoted to `rdl/ip/`.

SPI v151 is the first promoted shared block. Both SDK checkouts use byte-identical
register definitions and register-operation sources. Their porting and high-level
HAL files differ, so instance integration and behavioral policy are not part of
the shared register block.

The same evidence rule promotes watchdog v151, SFC v150, PWM v151, SIO v151,
Timer v150, TCXO v150 data16, GPIO v150 basic and UART v151 basic across
WS53/WS63/BS2X. Timer is modeled as write-one-to-clear because all three SDK
operation layers write EOI and WS63 silicon independently confirms the behavior;
the imported read-to-clear baseline was rejected.

## Export boundary

SystemRDL becomes canonical after source reconciliation. CMSIS-SVD is a generated
compatibility interface consumed by `svd2rust` and other tools. The local exporter
currently supports:

- flat peripheral instances and interrupts;
- registers and fields;
- software access type;
- reset values;
- base addresses and offsets.
- enumerated values;
- read-clear/read-set and standard modified-write behavior;
- inclusive write ranges carried by repository UDPs.
- one-dimensional register arrays with explicit stride;
- complementary read-only/write-only alternate register views;
- explicit FIFO read side effects and checked peripheral derivation.

New WS53 FIFO windows use native SVD arrays and separate RX/TX views so generated
PAC code cannot perform read-modify-write on a FIFO. Alternate views must match
offset, width, dimension and stride and have complementary access permissions.
Multidimensional arrays, split-width accesses and general RDL hardware aliases
remain unsupported and fail generation.

The bootstrap importer still expands legacy WS63/BS2X arrays and merges the one
overlapping reset-register alias, recording that normalization in each import
manifest. Those legacy API shapes are not changed by WS53 array support.
Every export is checked against the vendored ARM CMSIS-SVD 1.3.9 schema as well
as semantic tests. Schema-compliant element reordering was verified to produce
byte-identical WS63/BS2X `svd2rust 0.37.1` output against commit `61ec855`.

## Adoption gates

1. Reconcile WS63 blocks against `fbb_ws63`, the semi-official SVD and silicon.
2. Reconcile BS2X independently against `fbb_bs2x`; do not inherit WS63 facts by default.
3. Restore array and alias API shape and compare generated PAC APIs with releases.
4. Add golden tests for access/reset/side-effect semantics and evidence provenance.
5. Migrate one PAC on a branch and run HAL builds plus relevant HIL.
6. Only then make generated SVD snapshots authoritative for released PACs.
