# Architecture

## Ownership

`hisi-registers` owns reusable register and chip-integration facts. PAC repositories
consume generated SVD snapshots but do not become alternate register truth sources.
HAL repositories own operational policy and safe abstractions, not register layout.

## Versioning model

IP definitions carry explicit hardware-family revisions such as `ssi-v151`. A chip
map instantiates one revision and may apply a narrowly documented chip integration
variant. Similar register names are not sufficient evidence that two instances are
the same IP revision.

## Export boundary

SystemRDL is canonical. CMSIS-SVD is a generated compatibility interface consumed
by `svd2rust` and other tools. The local exporter initially supports only:

- flat peripheral instances;
- registers and fields;
- software access type;
- reset values;
- base addresses and offsets.

Interrupts, arrays, aliases, enumerations, write constraints, modified-write
semantics, read side effects and chip variants must be implemented and tested before
the corresponding IP blocks migrate. Unsupported constructs fail generation.

## Adoption gates

1. Complete and reconcile SSI v151 against WS63 and BS2X evidence.
2. Compare generated SVD/PAC APIs against the current released PACs.
3. Add golden tests for access/reset/side-effect semantics.
4. Migrate one PAC on a branch and run HAL build plus SPI HIL.
5. Only then make generated SVD snapshots authoritative for released PACs.
