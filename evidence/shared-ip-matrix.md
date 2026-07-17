# Shared IP evidence matrix

SDK revisions used for this snapshot:

- `fbb_ws53`: `43eeaf80d8610ee5342f9ce1f8832b4c7dbf7e54`
- `fbb_ws63`: `546de103396ae09155cf77babc60ec1a10a0fc12`
- `fbb_bs2x`: `498543f9b8ca5d86c019bfc72e107b5a628a2e7a`

## Promoted shared blocks

| IP | Chips | Evidence | Integration kept per chip |
|---|---|---|---|
| SPI v151 | WS63, BS2X | Byte-identical register definition and register-operation sources | instances, base, IRQ, clock/reset, DMA, pinmux, supported modes |
| Watchdog v151 | WS53, WS63, BS2X | Byte-identical register definition and operation headers | base, IRQ, clock/reset, reset policy |
| SFC v150 | WS53, WS63, BS2X | Byte-identical register definition and operation sources | base, flash mapping, timing, command/XIP state |
| PWM v151 | WS53, WS63, BS2X | Register headers identical after removing comments/whitespace; WS63/BS2X RDL bodies agree | base, clock/reset, channel routing |
| UART v151 basic | WS53, WS63, BS2X | All active configs disable PRO; basic layout agrees | instances, base, IRQ, DMA/pinmux |
| I2C v151 | WS53, BS2X | Register headers semantically identical; both product configs select v151 | instances, base, IRQ, clock/reset |
| RTC v150 family variant | WS53, BS2X | Register headers semantically identical | channel instances, base, IRQ, clock domain |
| SIO v151 | WS53, WS63, BS2X | All target configs select v151; layout and public edge enum agree | base, IRQ, clock/divider, DMA |
| Timer v150 | WS53, WS63, BS2X | Layout agrees; all SDK operation layers write one to clear, confirmed on WS63 silicon | base, IRQ, clock/reset |
| TCXO v150 data16 | WS53, WS63, BS2X | Active configs use four 16-bit chunks | base, clock domain |
| GPIO v150 basic | WS53, WS63, BS2X | Active configs disable multisystem extension | instances, width, base, IRQ/core routing |

## Variant families, not yet promoted

| Family | Evidence shape | Decision |
|---|---|---|
| TCXO v150 data32 | Optional source path exists in WS53/BS2X but no audited product enables it | Add only with a profile that selects it |
| GPIO v150 multisystem | Optional WS53/BS2X source tail exists but no audited product enables it | Add only with a profile that selects it |
| UART v151 PRO | SDK sources contain different PRO extensions, but all audited products disable PRO | Model only when an enabled profile is identified |
| I2C WS63 v150 | WS53/BS2X v151 is extracted; WS63 target configs select v150 | Keep versioned separately |
| RTC v150 WS63 variant | WS53/BS2X family variant is extracted; WS63 current RTC model differs | Keep an explicit WS63 variant |
| DMA v151 | WS63/BS2X differ mostly by names/format, but operation sources and channel integration differ | Audit semantic fields and channel geometry before extraction |
| SIO v151 edge comment | One WS63 register comment conflicts with the shared HAL enum and the other two SDKs | Treat WS63 comment as stale; model 0=falling, 1=rising |
| SPI on WS53 | Product configurations select both v151 and v151_100 | Do not attach WS53 to the WS63/BS2X block without profile evidence |

Promotion requires exact source revision, register/access/reset comparison and a
chip-map-only list of integration differences. A shared driver filename alone is
not sufficient.
