# WS63 source matrix

Status: bootstrap complete; reconciliation in progress.

## Inputs

| Input | Role | Current observation |
|---|---|---|
| Current `ws63-pac/ws63-svd/WS63.svd` | Migration baseline | 38 peripherals, 579 declared registers, 1067 fields, 46 interrupts |
| Semi-official `ws63(1).svd` | Additional evidence, not canonical | 52 peripherals, 855 registers, 1187 fields, 46 interrupts |
| `fbb_ws63` | Licensed SDK behavioral oracle | Driver operations and active port code outrank stale or generic headers |
| Silicon/HIL | Highest-confidence behavior | Required for destructive, read-clear, write-only and undocumented behavior |

The two SVDs share 17 peripheral names. GPIO, I2C, IO configuration, PWM,
TSENSOR and UART have strong offset-level agreement. The semi-official input
adds direct-MMIO evidence blocks and decomposes several logical peripherals,
so peripheral counts cannot be compared as a quality score.

Notable reconciliation work:

- semi-official SPI0 contains 35 more explicit data registers than the current
  declared-register view and differs in one shared register name;
- semi-official DMA and SFC_CFG add offsets absent from the current baseline;
- EFUSE is decomposed differently and cannot be merged by matching peripheral
  name alone;
- TCXO offsets agree but register names do not, requiring source-level review;
- current SVD uses `derivedFrom` for repeated instances while the semi-official
  input expands many of them.

The semi-official SVD is evidence, not an overwrite source. Facts are merged at
register/field granularity only after SDK and, where necessary, silicon review.

