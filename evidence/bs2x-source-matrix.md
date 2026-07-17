# BS2X source matrix

Status: provisional bootstrap; SDK reconciliation required.

## Inputs

| Input | Role | Current observation |
|---|---|---|
| Current `bs2x-pac/bs2x-svd/BS2X.svd` | Low-maturity migration baseline | 30 peripherals, 591 declared registers, 1071 fields, 52 interrupts |
| `fbb_bs2x` | Authoritative behavioral source | Active chip port and register operations must be reviewed per block |
| WS63 definitions | Cross-chip comparison only | Never inherited merely because an IP/revision name matches |
| Silicon/HIL | Final behavior evidence | Required before claiming stable destructive or side-effect semantics |

The current BS2X baseline is useful for establishing coverage and generation
mechanics, but it is not yet a reviewed register truth source. SPI is a confirmed
exception: its v151 register definition and register-operation files are
byte-identical to WS63, and BS2X target configurations compile that implementation.
The current BS2X SVD SPI block is therefore incomplete and is intentionally not
used by this repository. Its parent SVD is left unchanged pending later retirement.

## Reconciliation order

1. startup, clock/reset and interrupt integration;
2. UART/GPIO/timer/watchdog used by bring-up and diagnostics;
3. SPI/I2C/PWM/DMA, comparing operations rather than IP names;
4. analog, RTC, USB and radio-facing blocks;
5. destructive/status side effects and silicon confirmation.

Each reconciled block must record its exact `fbb_bs2x` path/revision and must not
silently import a WS63 field, reset value or access mode.
