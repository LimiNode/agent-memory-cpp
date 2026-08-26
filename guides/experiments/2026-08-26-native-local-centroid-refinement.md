# Native local centroid-refinement diagnostic

Date: 2026-08-26. This focused timing diagnostic tests the practical cost of
retaining centroids only after a learned-address pool has been formed. It does
not measure E5 query encoding, MDBX access, posting union, ITQ/ADC, exact
rerank, index construction, or end-to-end latency. Per-query temporary score
vector allocation is included in the timed kernel.

## Setup

The runner pins the exact recorded #176 result and selected model SHA-256, not
merely a model with a compatible shape. Its frozen es-25k document-only
`8-bit / replication-4` substrate yields 256 normalized float bucket centroids.
The native C++17 benchmark performs
FP32 dot products and deterministic top-16 selection for all 648 frozen
queries, with five warmups and fifteen retained warm repetitions.

Two exactly defined treatments are timed:

- the model's existing confidence-generated 64-address pool;
- an exhaustive scan over all 256 occupied centroids.

The standalone native kernel is intentionally scalar reference code compiled
with MinGW g++ 15.2 and `-O3 -march=native`; it validates a stable top-K
checksum across every retained repeat. It is not a cross-platform production
claim or a SIMD-kernel comparison.

## Result

| Native FP32 centroid scoring, per-query score-vector allocation, and top-16 only | Pool size | Repeat-mean p50 ms/query | Repeat-mean p95 ms/query |
| --- | ---: | ---: | ---: |
| Learned confidence pool | 64 | 0.0278 | 0.0281 |
| Full occupied-centroid scan | 256 | 0.1058 | 0.1071 |

On this machine, local float centroid refinement adds only about `27.8 us` to
the narrow benchmarked part of the read path; even the complete 256-centroid
scan is about `106 us`. These are percentiles across 15 whole-run per-query
means, not p50/p95 latencies of individual queries. This supports retaining
the practical hybrid control:

```text
query router -> small address pool -> local float centroid ordering -> MDBX postings
```

It does not show that centroids are free end-to-end, nor does it invalidate the
centroid-free research line. It does show that their removal should no longer
be justified by the old warm Python `0.2 ms` differential alone.
