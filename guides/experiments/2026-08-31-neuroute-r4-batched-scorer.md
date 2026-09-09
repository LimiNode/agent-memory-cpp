# NeuRoute R4 batched learned scorer

## Context

- Date: 2026-08-31
- PR: stacked on the fused INT8 representative kernel
- Status: full DE-1M matched measurement complete

## Question

Can the frozen learned R0 plus max-cosine scorer execute across 1,024 addresses
as address-lane SIMD batches while preserving every output score bit for bit?

## Frozen protocol

The strict `fused_int8_scalar` winner, raw INT8 address-major store, requests,
features, weights, and representative maxima remain frozen. The control is the
existing scalar scorer. The treatment transposes normalized inputs into a small
query-local SoA buffer and evaluates eight addresses per AVX2 lane group.
Accumulation order within each address and scalar `tanh` calls remain unchanged.

Three deterministic shuffled measured passes follow one untimed pass. Selection
requires identical score hashes, zero maximum score error, top-128 overlap of
one for every request, and lower address-score p95.

## Expected result

The scorer performs the same approximately nine million multiply-adds per
query but exposes address-level parallelism. It should materially reduce the
approximately 13 ms scorer p95 without changing routing semantics.

## Actual result

All 2,736 warm samples completed with byte-identical address-score hashes for
every scalar/batched pair. Batched AVX2 cuts scorer p95 by `1.98x` and reduces
the whole still-`seek/read` representative stage by about `6.83 ms p95`.

| Scorer | Address score p50/p95 ms | Total p95 ms | Max score error | Min top-128 overlap |
|---|---:|---:|---:|---:|
| Scalar R0 | 12.954 / 13.392 | 52.066 | 0 | 1.000 |
| Batched AVX2 R0 | **6.394 / 6.747** | **45.240** | 0 | 1.000 |

The selected implementation is `batched_avx2_r0`. The speedup is smaller than
eight lanes because scalar `tanh`, SoA construction, query projection, mean/max
context, and output assembly remain part of the measured scorer.

## Limitations and next checks

This remains a single-threaded AVX2 experiment on one host. It does not change
address-block access, physical compression, concurrency, or the full cascade.

## Evidence

```text
result SHA-256:      46e57a18f087572f50f079b5900b8413b635ba84c9530669898dc88a4cb48e79
warm report SHA-256: bdeee31ac8b23f3fd7e08d0c45d942cfe47f827fb1ab0d595326f9e035f5ae31
evidence SHA-256:    e85e304e9a4d50900ea8022a81f7f3811cfbc422494d0f5ef353b54700c81cec
```

The evidence writer recomputes every summary, revalidates all paired score
hashes, and reruns the native self-test.
