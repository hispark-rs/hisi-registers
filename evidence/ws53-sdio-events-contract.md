# WS53 SDIO function1 event mapping

The fixed SDK `163d9150ab94a02d09a05f623351f9facee3025e` omits the v151 register
header. The retained application ROM capture is identified by SHA256
`a1ac5c03c613112f973b45bdfbfd399d3f3017b315b61d1f6b0d2a07d2ce4671` and is not
redistributed. `scripts/check_ws53_sdio_events.py` executes the unmodified ROM
function1 handler after relocating its contiguous code away from its absolute
software globals. It uses ordinary RAM as a register surrogate, a separately
initialized software state, and callbacks that record invocations and return 0.
No physical hardware or SDIO device model is involved.

GCC and Clang compile the actual `hal_sdio.h` structures to obtain the counter
offsets. Thirty-seven inputs cover zero, every one of 32 bits, and combined masks.
The entire 128-byte information block, callback counts/defined arguments, channel
state, register-surrogate contents and output guards must match. For host-read
start, the length is set to512 and reserved high bits are preserved; callbacks
return0, so this test does not submit an ADMA table or establish data transfers.

| Bit | Event identified from ROM callback/counter |
|---|---|
| 0 | Host write complete |
| 1 | Host read complete |
| 2 | Host read error |
| 3 | Host read start |
| 4 | Host write start |
| 5 | ADMA end |
| 6 | ADMA interrupt |
| 7 | ADMA error |
| 8 | Function reset |
| 9 | Host message |
| 10 | Suspend |
| 11 | Resume |
| 12 | Device message acknowledgement |
| 13 | Function enable |

These names apply to the observed status and corresponding enable positions.
Unknown status bits14..31 remain readable but unclassified. Captured status is
still written through the separate full-width acknowledgement view. RAM surrogate
execution cannot prove hardware W1C, level/edge delivery, self-clearing reset,
bus drain, or ownership; no such semantics or reset values have been added.

Two more original entry points establish a critical software boundary: `deinit`
only clears the saved register pointer, while `sdio_port_soft_reset` returns
without changing surrogate registers or software data. Neither establishes that
an active bus master has stopped. SDIO ADMA must therefore retain buffers until
a separate hardware drain/ownership contract is established.

The first run exposed a QEMU xlinx compressed-halfword offset error: bit12 was
treated as byte offset1 instead of32. The fixed model (`9c53a617…31b942`) passes
the handler checks; a separate vendor-assembled8192-case CPU test has2048 failures
on the old model and none after correction. The fixed CPU encoding, not a changed
ROM image or adjusted counter expectation, resolves the discrepancy.

`test_ws53_sdio_events.py` rejects99 mutated SVD descriptions. Schema/exporter
tests pass; only these two WS53 register field layouts change. WS63/BS2X generated
SVDs remain byte-identical. This is register evidence, not a completed SDIO HAL,
device model, or real-board qualification.
