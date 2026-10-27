# WS53 application-core IPC contract

Source profile: fbb_ws53 `163d9150ab94a02d09a05f623351f9facee3025e`,
ws53_liteos_app, application core 2, control core 0. New descriptions live in
the unused IPC window of M_CRG; no reset values are asserted.

| Address | Access | Evidence |
| --- | --- | --- |
| 0x52000020 | word WO | ROM enable writes 0xffff, disable zero; only channel bits0/1 described |
| 0x52000028 | word RO | incoming channel pending |
| 0x5200002c | word WO command / RO discard view | ACK mask, then patched ISR fence and readback; value unspecified |
| 0x52000030 | word RO | outgoing pending; misleading ROM name clear_self2x_int performs no clear |
| 0x52000034 | halfword WO | patched send replaces ROM word write; source checks pending under local IRQ lock |
| 0x52000038 | word WO, only1 | SDK initialization releases IPC; no HAL reset or live-peer reset permission |

ROM ipc_port_register_irq registers 26/27. Masks are 1<<channel for control
core0; other cores return zero. The pinned port declares exactly two channels.
The patched receive ISR acknowledges before draining the queue, uses fence plus
readback, rechecks pending and finally clears the local interrupt controller.
This does not prove a generic cross-master DMA drain or cache coherence.

The 2026-10-27 A-board read-only capture of [0x1f220,0x1fa20) was read twice,
SHA256 `2c88c7b2090d64db73307fbd45005b27882308443b092fbedc85be63586a662a`.
Both halted PCs stayed unchanged; both sessions resumed. Before/after SWD
health each passed600 DPIDR plus halt/step/resume. No reset, MMIO write,
RAM image load, Flash, eFuse, security or permanent setting was changed.
Captured vendor ROM bytes stay outside this repository.

Ordinary RAM profile reads confirmed base0x52000020 and current channels:
control→application ch0 0x1001e300/0x500, ch1 0x1001e800/0x100;
application→control ch0 0x1001e900/0x500, ch1 0x1001ee00/0x100.
The control header offset24 contains0x1e300; main.c adds0x10000000. These
are observed configured addresses, NOT universal addresses or reset values.
The generic ipc_porting.h defaults0x10080000/four0x800 blocks are overwritten
by main.c. Never initialize a live peer's memory from those defaults.

ROM mailbox layout: src/dst/channel/empty bytes0..3, read/write u16 offsets4/6,
capacity u32 offset8, ring data offset12. Message: id/priority bytes0/1, payload
length u16 offset2, then payload, with modulo wrap. Header IPC message maximum
is60, IDs0..18, priorities0/1. The ROM send narrows public u32 length to u16;
receive dispatch lacks a demonstrated defensive remote-ID/length validation.
The shared empty byte is written by both peers. Cache policy, publication
ordering, full/empty races and compatibility with a running vendor peer require
an explicit protocol proof before exposing safe shared buffers or callbacks.
A copied interrupt-disabled region does not provide cross-core synchronization.

`scripts/check_ws53_ipc_contract.py` compiles the original public headers under
a small configuration-only host harness, validates13 exact ROM instruction
encodings and the actual relocatable patch member, checks configured RAM/header
identity and the SVD, and rejects21 register-layout mutations. It executes no
vendor ROM/patch code. The included JSON is source/read evidence, not doorbell
write, IRQ delivery, message exchange, resource handoff or low-power acceptance.

## Enable-mask access correction

The first description at b449799 exposed enable as RW, but its evidence only
contained whole-word writes0xffff/0. No read helper or physical read qualification
was demonstrated. The corrected model exposes a write-only source view; this is
not a claim that hardware reads are forbidden. HAL must not perform an inferred
RMW. It adopts an explicit disabled/all-SDK-enabled handoff state, owns the full
application enable word and keeps a shadow mask, preserving the other low16
SDK bits. Bit0/1 per-channel gating is an integration inference from the channel
mask convention, requiring the configured profile/physical validation; it is
not upgraded to a silicon fact by the source/host/model tests.
