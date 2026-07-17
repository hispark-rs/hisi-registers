# Configured IP variant audit

This audit records where a shared HiSilicon IP name is insufficient to select a
register model. Register layout may be shared; instance integration remains in
each chip map.

## Timer v150

The three SDK definitions have the same offsets and field layout. Their stale
comments say read-to-clear, but all three operation headers clear EOI by writing
one. WS63 silicon independently confirms write-one-to-clear and zero-valued
reads. The active three-chip model is therefore one write-clear variant; the
BS2X SVD read access was incorrect.

## TCXO v150

The operation layer is shared. WS53/BS2X sources contain an optional two-register
32-bit form, but audited product configurations leave it disabled. The active
three-chip model is four 16-bit counter chunks. A future `data32` component is
created only when a real product profile enables it. WS53 acore uses
`0x57000200`; its control core uses `0x57000214`.

## GPIO v150

Offsets through `0x34` form the active common layout. WS53/BS2X sources contain a
`CONFIG_GPIO_SUPPORT_MULTISYSTEM` extension at `0x38..0x78`, but every audited
product configuration disables it and WS63 does not carry that extension. The
current instances therefore share `gpio-v150-basic`; multisystem remains latent.

## UART v151

All audited WS53, WS63 and BS2X configurations disable
`CONFIG_UART_IP_VERSION_V151_PRO`. Their active basic register layout is shared
as `uart_v151_basic_regs`. Differences in FIFO triggers and extension registers
belong to latent PRO subvariants and must not leak into the current PAC model.

## I2C and RTC

WS53 and BS2X share I2C v151. WS63 target configurations select I2C v150. WS53
and BS2X also share the extracted RTC v150 family variant; WS63 RTC remains
separate because its current layout differs.

## SIO v151

All three active configurations select SIO v151 and share `sio_v151_regs`. The
shared public HAL enum defines `0=falling` and `1=rising`, agreeing with WS53 and
BS2X. The inverse WS63 register comment is stale. SDK HAL bases point at offset
`0x3c`; chip maps use the physical block base (`HAL base - 0x3c`).

## WS53 SPI

WS53 profiles select `v151_100`, plain `v151`, or configurations that compile
both v100 and v151 sources. There is no chip-wide default. Future WS53 maps must
be profile-specific. A plain-v151 instance may reuse `spi_v151_regs` only after
a field-for-field comparison with WS63/BS2X.

## Extraction rule

Promote a block only when the selected profile identifies the same concrete
version; offsets, widths, resets and access semantics agree; optional tails or
widths are explicit variants; integration stays in chip maps; and checks reject
reintroduced private duplicates.
