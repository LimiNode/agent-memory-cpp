# Native SIMD and page-shaped K1 INT8 coarse layout (2026-09-14)

## Question

Can the compact per-dimension INT8 K1 coarse sidecar be scored with an
AoSoA layout and AVX2 lane batching without changing the selected-address
frontier, and which lane shape is a useful physical-layout control before an
MDBX benchmark?

## Setup

- frozen DE-1M coarse sidecars from the corrected #404 materialization;
- three R4 seeds with 65,108/65,039/65,191 occupied K1 addresses;
- 152 frozen query vectors per seed and 3 measured passes after one untimed
  warm-up pass;
- row-major INT8 scalar control and AoSoA layouts with 8, 10, 16, and 32
  address lanes; each tile stores all 384 coordinates with the lane as the
  innermost dimension;
- 4,096-byte logical-page accounting is reported from payload sizes only; OS
  page faults, physical MDBX pages, and disk-cold behavior are not measured;
- selected-address parity is checked against the row-major scalar scores by
  top-128 overlap and exact prefix-ID overlap for top-8,192/top-16,384 on the
  first measured pass; teacher IDs are not used.

The 10-lane shape is included because `10 * 384 = 3,840` bytes fits inside a
4 KiB page. The 16- and 32-lane shapes intentionally span two and three
logical pages and test whether the wider SIMD batch outweighs that shape.

## Results

All 6,840 samples passed the independent audit. Every AoSoA mode had minimum
top-128 overlap `1.0`; minimum set overlap was `.999878` for top-8,192 and
`.999939` for top-16,384. The raw floating-point checksum is not bit-identical
for AVX2 because lane-wise accumulation changes the final rounding order, and
the resulting boundary can exchange a handful of addresses. The audit keeps
the exact prefix IDs for one measured pass and binds the remaining passes by
deterministic checksums; this is a measured near-parity control, not a claim of
bitwise score equality.

| layout | tile bytes | logical pages | physical pages | p50 ms/query | p95 ms/query |
| --- | ---: | ---: | ---: | ---: | ---: |
| row-major scalar | — | 6,104 (seed 2701) | 6,104 | 28.73 | 32.77 |
| AoSoA-8 AVX2 | 3,072 | 6,104 | 6,105 | 13.25 | 19.63 |
| AoSoA-10 AVX2 | 3,840 | 6,104 | 6,105 | 18.01 | 19.64 |
| AoSoA-16 AVX2 | 6,144 | 6,104 | 6,105 | 8.10 | 9.57 |
| AoSoA-32 AVX2 | 12,288 | 6,104 | 6,105 | 7.88 | 8.38 |

The best local arithmetic control is AoSoA-16/32 at roughly 3.5x lower p50
and p95 than the scalar row-major pass. The page-fitting AoSoA-10 shape is
slower, so 4 KiB fit alone is not a sufficient selection criterion. AoSoA-32
has the lowest p50 in this run, while AoSoA-16 is close; the difference is
directional and must be rechecked in a storage benchmark.

## Interpretation

The K1 sidecar is no longer limited to the scalar arithmetic demonstrated in
#404. A lane-major physical representation gives a reproducible native SIMD
control while preserving virtually all of the route's selected-address
prefix. This supports the architectural direction of keeping a compact
immutable coarse sidecar and optimizing its sequential scan independently from
R4 postings. The tiny boundary exchanges are a numerical consequence of
accumulation order; a production implementation must freeze a precision and
tie policy.

This is still a component result. It does not establish end-to-end K1 → K16
latency, posting reads, THQ/exact cost, MDBX page behavior, or production
selection. The next storage experiment must use the same winning layout(s) in
a page/read harness and report logical payload bytes separately from actual
MDBX/OS page reads.

## Provenance

Raw evidence is retained outside Git under
`E:\_repoz\agent-memory-workspaces\r4-k1-simd-layout-raw`.

| artifact | SHA-256 |
| --- | --- |
| materialized manifest | `c6dde0fc6c885ec4c2e4769511e79fdf0ce9055aba98c2551b4fbb95f1d9e940` |
| raw samples | `1f1b45d815baaecafee4a13fdde3bcd9189402089c3f6a0a0dcbc1a523f241cc` |
| receipt | `8d14a0c35606ab1303319572ccb1cde11785865c584222f97ad3a0bd2db0a473` |
| runner | `f484c48300696db5a97145395f1f04b99e5f2c61539f26b3daf00fa45dcf43d9` |
| audit | `7eaed028c8c80ae7f311f23e56794052f39a47d46b9c937ca9ed5435a4f8b5cd` |
| materializer | `764fd9f44df6bb70dc765066f97111cfd089e174483baf03866ca56e85a4cf65` |
| native harness source | `24982e8f0c797094cdb446d3e50baa2d759a5771cdc159a2dfeed07e6caf1f29` |
| native executable | `84f13f30312a26f490aaf4c6e814b1cba6fc43b973d85cd89aab66603245f44e` |

The independent audit command is:

```powershell
python tools/agent-memory-bench/audit-r4-k1-simd-layout.py `
  --manifest E:\_repoz\agent-memory-workspaces\r4-k1-simd-layout-raw\materialized\manifest.json `
  --receipt E:\_repoz\agent-memory-workspaces\r4-k1-simd-layout-raw\simd.receipt.json `
  --raw E:\_repoz\agent-memory-workspaces\r4-k1-simd-layout-raw\simd.raw.json `
  --native-executable E:\_repoz\agent-memory-workspaces\r4-k1-simd-layout\build-r4-k1-int8\tools\agent-memory-bench\Release\agent-memory-neuroute-r4-k1-simd-layout.exe
```
