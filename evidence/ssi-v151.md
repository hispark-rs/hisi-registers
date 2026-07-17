# SSI v151 evidence ledger

Status: experimental tracer bullet.

| Fact | WS63 evidence | BS2X evidence | Confidence |
|---|---|---|---|
| SPI0/IP prefix layout `0x00..0x18` | Current WS63 SVD, SDK-derived SVD, semi-official SVD | Current BS2X SVD | cross-chip-consistent |
| WS63 SPI0 base `0x44020000` | Current PAC input and SDK-derived material | n/a | vendor-document |
| BS2X SPI0 base `0x52087000` | n/a | Current PAC input | vendor-document |
| Controller family is SSI v151 | Current WS63 SVD description and SDK path naming | Register layout match only | inferred |

Before this block becomes authoritative:

- Reconcile the complete register list and access semantics.
- Resolve WS63/BS2X variant registers rather than forcing false identity.
- Add source revision identifiers and permitted quotations.
- Compare generated SVD structure and generated PAC API against current releases.
- Validate destructive/read-clear/write-only semantics on silicon where possible.
