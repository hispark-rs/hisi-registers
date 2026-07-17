# SSI v151 evidence ledger

Status: shared register IP confirmed from both SDKs.

| Fact | WS63 evidence | BS2X evidence | Confidence |
|---|---|---|---|
| Full register block `0x00..0xF8` | `hal_spi_v151_regs_def.h` | Byte-identical `hal_spi_v151_regs_def.h` | licensed-sdk |
| Register operation helpers | `hal_spi_v151_regs_op.h/.c` | Byte-identical `hal_spi_v151_regs_op.h/.c` | licensed-sdk |
| WS63 SPI0 base `0x44020000` | Current PAC input and SDK-derived material | n/a | vendor-document |
| BS2X SPI0 base `0x52087000` | n/a | Current PAC input | vendor-document |
| Controller family is v151 | Build config selects `CONFIG_SPI_USING_V151` | Build config selects `CONFIG_SPI_USING_V151` | licensed-sdk |

Conclusion:

- WS63 and BS2X instantiate one shared `spi_v151_regs` register block.
- Base addresses, instance counts, IRQs, clocks, resets, DMA routing and pinmux
  remain chip integration facts.
- Different `hal_spi_v151.c` and porting behavior does not fork the register map.

Before either block becomes authoritative:

- Reconcile field access/reset/side-effect details against the common SDK header.
- Add source revision identifiers and permitted quotations.
- Compare generated SVD structure and generated PAC API against current releases.
- Validate destructive/read-clear/write-only semantics on silicon where possible.
