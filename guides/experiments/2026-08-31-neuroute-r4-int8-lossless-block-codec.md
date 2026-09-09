# NeuRoute R4 INT8 lossless address-block codec frontier

## Context

- Date: 2026-08-31
- PR: stacked on the R4 native end-to-end study
- Status: full DE-1M materialization, warm/fresh-process measurement, and
  evidence recomputation complete

## Question

Can raw symmetric per-document INT8 representatives be compressed losslessly
with Zstd or VByte while preserving the random address-block access contract
and improving the physical/latency frontier left open by SIMDComp?

## Frozen protocol

The 16-bit partition, address-major document order with FF32 representatives
first, K8 top-1024 shortlist, learned R0 plus normalized max-cosine scorer, and
all downstream cascade inputs remain frozen. Each occupied address is an
independently decodable block. The comparison includes raw INT8, Zstd level 3,
Zstd level 3 with a per-seed 64 KiB dictionary trained from 4,096
query-independent deterministically selected address blocks, and centered
zigzag unsigned VByte. Codec-specific offsets and dictionary bytes are included
in the footprint.

The materialization covers all one million INT8 document records per seed, not
only the FF32 prefixes. A query decompresses the complete selected address
block when required, then scores only its frozen representative prefix. Warm
results use sequential prefault, one complete warm-up matrix, three shuffled
measured passes, and 152 queries for each of three seeds. The fresh-process
matrix contains 15 deterministic paired requests per seed and treatment; the
OS page cache is uncontrolled.

## Results

Across the three physical stores:

| Treatment | Total bytes | Saving vs raw | Warm p95 | Ratio vs raw | Fresh-process stage p95 |
|---|---:|---:|---:|---:|---:|
| Raw INT8 | 1,164,000,000 | 0.00% | 16.046 ms | 1.00x | 23.025 ms |
| Zstd/block | 1,132,904,436 | 2.67% | 33.371 ms | 2.08x | 40.397 ms |
| Zstd dictionary/block | 1,125,340,393 | 3.32% | 34.922 ms | 2.18x | 42.164 ms |
| VByte zigzag | 1,388,803,927 | -19.31% | 95.254 ms | 5.94x | 103.142 ms |

The dictionary saves only another 0.65 percentage points relative to ordinary
per-block Zstd. Its warm decode p95 is 19.524 ms, compared with 17.858 ms for
dictionary-free Zstd. VByte expands the payload because many centered zigzag
codes require two bytes and its scalar decode dominates latency.

All 5,472 warm and 180 fresh-process paired samples have bit-identical address
score hashes across all four treatments. The downstream cascade is frozen and
deterministic, so identical address scores imply identical selected addresses,
candidates, Hamming768, ADC64, and exact top-10 outputs. Raw INT8 remains the
preregistered selection: neither Zstd variant stays below the 1.25x warm-p95
gate, and VByte also expands storage.

## Interpretation

The earlier SIMDComp result was not a codec-specific accident. Raw E5 INT8
codes have little lossless redundancy at independently decodable address-block
granularity. Zstd can recover about 3%, but more than doubles matched R4 stage
p95 because every chosen block must be decompressed in full. A trained
dictionary slightly improves size and slightly worsens serving latency. VByte
is structurally mismatched to the near-full-range zigzag code distribution.

This closes lossless compression as a useful production direction for the
current store. Meaningful footprint reduction must come from changing the
quantized representation, which motivates the separate nonlinear INT5/6/8
frontier.

## Limitations

- Only Zstd level 3 and one 64 KiB dictionary contract were tested. Compression
  level mainly affects materialization rather than decode, while the small size
  delta makes a broad level search low priority.
- Dictionaries are trained independently per seed from the same corpus store;
  no evaluation queries or qrels enter training.
- The matched native path uses scalar INT8 dot after lossless decode. Ratios are
  valid within this experiment and must not be substituted for the separate
  fast-AVX2 end-to-end absolute latency.
- Fresh-process measurements include process launch but do not control the OS
  page cache and are not cold-disk measurements.
- The experiment preserves random access at address-block granularity; a global
  Zstd stream could compress better but is not deployable for this router.

## Evidence

```text
materialization SHA-256: c3e22837eb85eb581fccf8b01869b67ff6eef12792541d78d311179dc1a428e4
result SHA-256:          5457c73b69b50c71e1a6980964a324fdd7f8ee82a859535565b777426a7c8140
evidence SHA-256:        1bba50d3f1296e0d877e5c1cd9cc47dad6f3bbf439afdab82786f9de10f31904
```

The evidence writer rehashes 24 physical files totaling 3,647,050,907 bytes,
recomputes every timing summary, and revalidates score identity independently.
