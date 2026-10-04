# Z0 small-block compression screen (research prototype)

Status: executed and audited as an offline screen. This is not a production
Zstd storage implementation and does not measure MDBX serving latency.

## Hypothesis and contract

The screen tests whether small physical blocks make lossless compression useful
for the existing canonical document ordering. It covers the FP32 exact
reference, conventional INT8 control, and LSQ32/TQ1/TQ1+PQ8 compact arms. Each
payload is divided into approximately 4 KiB, 16 KiB, and 64 KiB uncompressed
blocks (whole rows only), then compressed with Zstd levels 1 and 3. FP32 also
has a byte-shuffle transform before Zstd. Compression and decompression are
timed in memory; no MDBX pages or query path are involved.

The raw receipt is retained in the research workspace at
`tmp/mdbx-compression-z0/mdbx-z0-compression.result.json` (SHA-256
`fa9d5b44e761373e4e0508aff4f01f20778279d1d07d0e8bb292c7f95c9f9c5a`). The
canonical FP32 source is the audited DE-1M evaluation vector file; all source
hashes and the fixture manifest hash are persisted in that receipt. The
committed compact summary is
[`2026-10-04-mdbx-z0-compression-screen.summary.json`](2026-10-04-mdbx-z0-compression-screen.summary.json).

## Results

| arm | best transform/block/level | best saving | Z1 carry-forward? |
|---|---|---:|---|
| FP32 | byte-shuffle / 16 KiB / 3 | 10.814% | yes |
| INT8 | plain / 64 KiB / 3 | 3.991% | no |
| LSQ32 | plain / 64 KiB / 1 | -0.015% | no |
| TQ1 | plain / 64 KiB / 1 | -0.015% | no |
| TQ1+PQ8 | plain / 64 KiB / 1 | -0.015% | no |

The provisional 5% rule is only a research filter. It does not eliminate
INT8, which remains a required conventional accuracy/control representation,
and it does not eliminate FP32, which remains the exact/reference tier. The
compact learned payloads are already near incompressible in canonical order;
carrying them into an MDBX+Zstd serving gate would need a different hypothesis
(for example route-local ordering or codec-aware framing) rather than more
levels on the same blocks.

## Next gate

Only the FP32 byte-shuffle arm currently qualifies for a Z1 serving screen.
That future screen must compare uncompressed Row-KV, compressed blocks, and
plain packed blocks using MDBX page stats, returned logical bytes, bytes
decompressed, useful bytes, decode time, and total query time. It must not mix
the offline compression ratio with physical-I/O claims.
