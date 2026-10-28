# WS53 shared control-core logic/clock release

This adds a separate 32-bit write-only view of `SYSTEM_MEMORY + 0x96c`
(`0x5703096c`). `CCORE_RESET_STATUS` remains read-only with its existing
three-bit observation. The new `CCORE_LOGIC_CLOCK_RELEASE_COMMAND` has one
full-word `LOGIC_CLOCK_RELEASE` field and a CMSIS-SVD range of exactly 3.
No reset value, reset mask, RMW interface, command 0 or CPU-release command 7
is described by the new view. This is a shared domain operation, not a
TSENSOR-only reset or an application-core-exclusive resource.

The source contract pins SDK `163d9150ab94a02d09a05f623351f9facee3025e` and
checks all three files byte-for-byte against that commit:

- `src/drivers/chips/ws53/include/acore/platform_core.h`: address,
  `RESET_CRG_CCORE_VALUE=3` and distinct `RESET_CCORE_VALUE=7`.
- `src/application/ws53/ws53_application/main.c`: a full-word write of 3
  immediately followed by `uapi_tcxo_delay_us(5)` in `copy_to_ccore`.
- `src/middleware/utils/common_headers/chip_io.h`: the included non-`UT_TEST`
  `writel(addr, d)` macro is one volatile `uint32_t` store, not a bitfield RMW.

Run `uv run --script scripts/check_ws53_ccore_domain.py --sdk SDK --svd SVD`.
The [contract report](ws53-ccore-domain-contract.json) includes the source
hashes and one positive/twelve negative checks. The same SVD boundary tests
run under `scripts/check.sh` without an SDK checkout. They reject wrong
address/width, RMW, partial-word command, widened range admitting 0 or 7,
wrong alias, invented reset, extra overlapping writer and changed field name.

The motivating board evidence is retained separately in the integration
repository, under `docs/plan/evidence/ws53-flash-tsensor10-failure-2026-10-25`.
Its initial failure summary SHA-256 is
`612af59d6efd4e6a2dd313d65f89f0d4c0df588ebe65901e18bcb08cb21f04b7`.
The domain-probe appendix summary is
`31246b9e0741e8f713ee7db059ad4736d457e9e061993789f653840c27a9e8ab`.
That diagnostic observed TSENSOR CONTROL/STATUS change from `0x3131302a`
to zero after 0→3. Writing zero again and then reading the domain timed out;
restore and resume failed. A later whole-chip reset restored debug access but
reproduced the same application guard failure. These are separate outcomes;
neither proves a safe reversible domain lease or a successful Flash application.
The RDL regression itself accesses no hardware.

The PAC does not grant ownership. A future HAL must prove handoff, check exact
state 0 before the full-word command, wait at least the SDK interval, and verify
state 3 while leaving CPU-release bit 2 clear. In particular it must not reset
the domain on `Drop` or treat a failed readback as evidence that a rollback is
safe. Undefined encodings and unrelated shared-domain masters remain outside
this command's qualification. No eFuse, permanent protection or Flash content
is changed by this work.

`writeConstraint` records the intended command; svd2rust 0.37.1 still allows
other values through unsafe raw writes. A write-only accessor prevents normal
read/modify operations but cannot enforce this shared-resource protocol.

The [regression report](ws53-ccore-domain-regression.json) records tool versions,
32 semantic tests, three parser checks, and byte-identical WS63/BS2X exports
against `6cda2bb`. Removing only the new command from WS53 gives the same
normalized XML as that baseline. All 22 installed dependencies match `uv.lock`,
including lxml 6.0.2, and the final full test run was offline. Its
[complete log](ws53-ccore-domain-check.log) is retained. The initial substitute
schema run with system lxml 6.1.1 was superseded, not used as final qualification.
