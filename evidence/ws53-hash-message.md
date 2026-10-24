# WS53 unkeyed HASH message encoding

`scripts/check_ws53_hash_message.py` executes four unchanged functions from SDK
`163d9150ab94a02d09a05f623351f9facee3025e`: `priv_hal_hash_get_cfg`,
`drv_hash_get_state`, `hal_cipher_hash_config` and `drv_process_tail`. The original
IV/state table, algorithm types, node-type enum and product/register headers are
compiled with explicit host MMIO, DMA, cache and wait substitutes. All pinned
input files and extracted functions are hashed in `ws53-hash-message.json`.

The six unkeyed profiles are SHA-1/224/256/384/512 and SM3. Twelve state-window
cases cover both source-profile channels. The original byte-copy and register
configuration functions write little-endian words assembled from the IV bytes;
digest formatting must copy state words back in little-endian order. These are
host executions on a checked little-endian host, supplemented by the earlier
RV32 descriptor ABI audit, not actual on-chip HASH state reads.

The 512 padding cases cover every tail length of each profile, including the
one-/two-block transition. Message lengths are two complete blocks plus the
tail; cache/start/wait calls and submitted bytes are checked. No digest is
calculated. The SDK uses a 256-byte software bounce buffer; this is not evidence
of a silicon maximum transfer length. The new one-shot HAL stays within that
profile until larger/streaming submissions are separately qualified.

The SDK API enum has FIRST=1 and LAST=4; the packed descriptor has different
first/last bit positions (bits0/1). The oracle compiles the original enum and
records its values. A preliminary uncommitted draft substituted LAST=2; it was
corrected before accepting this audit. Padding bytes did not depend on that
draft substitution, but its flags assertion did not qualify the real API.

Reproduce with a local pinned SDK (no device access):

```sh
uv run --script scripts/check_ws53_hash_message.py --sdk /path/to/fbb_ws53 \
  --output /tmp/hash-message.json --vectors /tmp/hash-message.txt
```

The generator refuses to overwrite evidence. Its output fixture SHA-256 is
`a3466547b5eb76dbea359cca07b9b17a7821ee832b837d104e5c94ed0c8b5a32`.
The Rust HAL carries those generated observations, not vendor firmware. UBSan
trap instrumentation checks the original host paths. Keyed modes, long-stream
length carry, cryptographic results, cross-core ownership, cache coherence,
physical DMA drain and device/Flash/cold-boot behavior remain unqualified.
