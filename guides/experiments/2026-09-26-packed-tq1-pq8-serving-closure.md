# Packed TQ1/PQ8 serving closure (2026-09-26)

## Question

Can the frozen candidate-local TQ1 + PQ8 research arm be served directly from
packed bytes, without reconstructing a `float[384]` document vector, while
preserving the canonical THQ top-128 and quality-gate rankings?

## Setup

The gate uses the canonical 152-query candidate stream, canonical THQ4 codes
and thresholds, the source-bound TQ1 payload, and the frozen strong PQ8 model.
The native C++ scorer builds query LUTs for THQ, packed TQ signs, and PQ8 ADC;
document rows contain packed signs, a residual scale, PQ8 codes, and a final
norm. Two layouts are measured:

| Layout | Persisted side bytes/doc | Purpose |
| --- | ---: | --- |
| final-only | 64 | THQ top-128 goes directly to final TQ1+PQ8 scoring |
| two-stage | 68 | additionally preserves the intermediate TQ norm for a TQ-only arm |

The logical per-document side payload excludes shared model state. The native
scorer model is 399,360 B (THQ centroids plus PQ8 centroids); including the
4,608 B THQ threshold table makes the shared cascade state 403,968 B. The
candidate-local artifact also carries a 4 B/document ID/indexing field that
is not part of a future row-aligned production payload.

This is candidate-local evidence (18,362 payload rows and 152 queries), not a
1M row-aligned serving benchmark.

## Result

The native scorer and independent audit passed for both layouts:

| Check | 64 B | 68 B |
| --- | ---: | ---: |
| THQ top-128 exact parity | 152/152 | 152/152 |
| final PQ8 top-10 exact parity | 152/152 | 152/152 |
| intermediate TQ top-10 parity | not persisted | 152/152 |

The materializer uses the canonical strict zero boundary (`rotated > 0`;
exact zero maps to the negative centroid) and has a regression self-test for
`-epsilon, 0, +epsilon`. Native input validation is fail-closed for offsets,
candidate IDs, strictly increasing payload IDs, finite model/sidecar values and
positive norms. Each replay has a separate native-run receipt binding the
runner, all input files, payload receipt, exact invocation and JSONL output.

An independent Python score replay covered all `152 x 128 = 19,456`
candidate scores per layout. Maximum absolute error against the packed
payload arithmetic was `7.93e-10` for PQ8; the 68 B layout's intermediate TQ
scores had maximum error `7.94e-10`. These score checks are stronger than
top-10 parity alone.

The 68 B run reports, for query 0, 4.42 ms THQ prefilter, 0.69 ms query
preparation, 0.13 ms top-128 scoring, and 5.24 ms total. These are single
warm-process directional timings, not production latency claims.

## Interpretation and limitations

Packed direct scoring is now a reproducible native control for this frozen
research arm. The 4 B layout difference is a real storage-layout ablation:
64 B is sufficient when no intermediate TQ ranking is required; 68 B is
needed when that ranking is part of the cascade. The payload and audit bind
the materializer, source files, payload hash, and native output hash.

The candidate stream is not the full 1M row-aligned payload, query timing does
not include mmap/page-fault behavior, and the scorer is not yet a public
`IVectorIndex` codec. A follow-up must materialize all 1M rows and collect
repeated p50/p95/p99 plus encode/materialization throughput and storage-page
measurements. OPQ/direct ADC and native RSLM remain separate open gates.
