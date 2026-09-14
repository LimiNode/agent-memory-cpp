# HNSW representative-layer scientific control (2026-09-14)

## Question

Can an off-the-shelf graph index approximately enumerate the K=16
representatives that the exact global top-R oracle needs, and is that a
credible route accelerator?  This is a scientific control only.  HNSW is not
being proposed as the mutable MDBX product index.

## Setup

- frozen DE-1M fixture: 1,000,000 documents, 152 queries, 384 dimensions;
- three frozen semantic R4 seeds, clipped to the first 16 representatives per
  occupied address;
- 2,104,812 FP32 representative vectors in one materialized matrix;
- Faiss `IndexHNSWFlat`, inner-product metric, `M=32`,
  `efConstruction=200`, eight requested threads;
- `efSearch` in `{1024, 2048, 4096, 8192, 16384}`;
- returned representative prefixes `R` in `{128, 256, 512, 1024, 2048,
  4096, 8192}`;
- whole postings are read after first-hit parent-address emission under global
  unique-document budgets of 5k, 10k, 20k, and 50k;
- teacher IDs are used only for evaluation, never for graph construction or
  search.

The exact FP32 global representative top-R order from the preceding oracle is
the comparison control.  The run uses one build and one measured search pass;
the search timings are therefore directional rather than a production
latency claim.

## Results

The graph build took **820.33 s**.  The strongest candidate frontiers were
obtained at `efSearch=16384` and `R=8192`:

| requested unique candidates | mean candidate recall | p05 | min | mean candidates |
| ---: | ---: | ---: | ---: | ---: |
| 5,000 | .990789 | .900000 | .900000 | 5,013 |
| 10,000 | .995395 | 1.000000 | .900000 | 10,013 |
| 20,000 | .995395 | 1.000000 | .900000 | 20,015 |
| 50,000 | .997368 | 1.000000 | .900000 | 50,013 |

Representative-prefix recall against the exact top-R oracle at
`efSearch=16384` was .99865 for `R=512`, .99867 for `R=1024`, and .99296
for `R=8192` (the latter is lower because the approximate order accumulates
more tail inversions).  Search p50/p95 timings were:

| `efSearch` | p50 ms | p95 ms |
| ---: | ---: | ---: |
| 1,024 | 29.33 | 33.05 |
| 2,048 | 57.31 | 63.42 |
| 4,096 | 120.29 | 130.50 |
| 8,192 | 226.22 | 244.24 |
| 16,384 | 836.09 | 892.26 |

The resident process high-water mark was approximately 7.2 GB during the
run.  The HNSW index build and the representative matrix are not included in
the per-query search timings.

## Interpretation

HNSW can approach the exact representative oracle, but only with a very high
search effort.  The best observed candidate frontier reaches `.9908 @ 5k`
and `.9974 @ 50k`, matching the quality scale of the exact top-R control, but
the corresponding p50 search cost is about 836 ms/query and the build is
roughly 14 minutes with multi-gigabyte memory use.  Lower `efSearch` values
are faster but lose representative ordering and do not improve the candidate
frontier beyond the exact route's already-known ceiling.

This supports the narrow conclusion that the representative topology is
searchable by a graph control; it does **not** make HNSW a viable MDBX route.
Its mutable adjacency graph, random access pattern, build cost, and memory
footprint conflict with the page-oriented storage requirement.  The product
direction remains a compact representative accelerator (or hierarchical
coarse scan) feeding deterministic R4 postings, then THQ and exact rerank.

## Limitations and follow-up

- The experiment is an external Faiss scientific control, not a native SIMD,
  MDBX, or physical-page benchmark.
- Search was measured once per query/`efSearch`; repeated cold/warm runs and
  process-level memory instrumentation are still absent.
- Raw rows retain candidate and timing evidence but not the complete HNSW
  neighbor graph or every returned-ID prefix; the audit therefore validates
  identities, teacher-ID accounting, hashes, and all compact aggregates, but
  does not reconstruct Faiss search independently.
- The next product-oriented gate is a compact representative accelerator
  benchmark (quantized/SIMD or hierarchical K1 coarse selection) against this
  exact top-R oracle.  Only a positive accelerator result justifies physical
  posting/MDBX measurements.

## Provenance

Authoritative receipt and raw output are retained outside Git under
`E:\\_repoz\\agent-memory-workspaces\\r4-representative-hnsw-control-raw`.
The independent audit reports `PASS` for 21,280 quality rows, 760 timing rows,
and 145 compact summaries.

| artifact | SHA-256 |
| --- | --- |
| runner | `554ee5f88ead7b6b197a7e1eefb7319c5955fd777652c1a1d94a2bd5c6f1c115` |
| audit | `f0f062f038e031b892da49d3edca90c4736f713374689804e70d68fca00a86ba` |
| raw output | `9ea094b089a7979e60bd86212526537bec61cb6752ff70b29ee46b8c272a5b59` |
| receipt | `0decc7e820117ef4f72d58da9addc12b2264806ade21ea59f35890d545d52905` |
| representative matrix | `6adf653eb18491fae73e5564a2ccc4aee89f182bd6818796e746870bd4315e74` |
