# NeuRoute R4 fused INT8 representative kernel

## Context

- Date: 2026-08-31
- PR: stacked on the R4 physical-layout benchmark
- Status: full DE-1M matched measurement complete

## Question

Can the frozen address-major INT8 representative stage avoid a full
`18.5k x 384` FP32 materialization and a second memory pass without changing
the learned address ordering materially?

## Frozen protocol

The #245 DE-1M address-major INT8 store, FF32 IDs, K8 top-1024 traces, queries,
and learned scorer remain byte-identical. The matched treatments are the current
scalar INT8-to-FP32 decode plus scalar dot, a fused scalar control, and an AVX2
fused INT8-to-FP32-lane dot. All compute per-address maxima before invoking the
same scalar learned scorer. An ordered-reduction AVX2 treatment was added after
the tree-reduction treatment exceeded the strict address-score arithmetic gate;
it was registered before any ordered-treatment samples were measured and does
not relax the original gate.

Three deterministic shuffled measured passes follow one untimed pass. The AVX2
treatment must keep maximum and final address-score absolute error at or below
`1e-5`, representative-winner and address-top128 agreement at or above `.999`,
and must be measured on all three frozen seeds.

## Expected result

Fusing should remove the approximately `16.8 ms p95` decode pass and its large
temporary FP32 buffer. AVX2 should also reduce the remaining dot work. The
lowest compute p95 among equivalent treatments is selected directionally;
production selection remains forbidden.

## Actual result

All 5,472 warm samples completed. The fused scalar path is bit-identical and
cuts representative compute p95 by `2.85x`. Tree-reduction AVX2 is much faster,
but fails the preregistered absolute address-score arithmetic gate. Its maximum
cosine error is only `1.01e-6`, address top-128 overlap is `1.0` for every
request, and minimum representative-winner agreement is `.999023`; it remains
an end-to-end sensitivity treatment rather than the strict winner.

| Kernel | Decode + dot p95 ms | Total p95 ms | Max address-score error | Min top-128 overlap | Strict pass |
|---|---:|---:|---:|---:|:---:|
| Decode FP32 + scalar dot | 26.789 | 69.791 | 0 | 1.000 | yes |
| Fused INT8 scalar | **9.393** | 52.532 | 0 | 1.000 | yes |
| Fused INT8 AVX2 tree | 1.943 | **44.976** | 0.0000544 | 1.000 | no |
| Fused INT8 AVX2 ordered | 9.612 | 52.789 | 0 | 1.000 | yes |

Ordered AVX2 proves that strict arithmetic equivalence is possible, but the
required scalar-order reduction removes its throughput advantage on this host.
The strict selection is therefore `fused_int8_scalar`. The tree-reduction AVX2
path must be judged by downstream cascade equivalence before activation.

## Limitations and next checks

This isolates representative arithmetic under the existing `seek/read` fetch
path and scalar learned scorer. Address access, scorer batching, compression,
concurrency, and the full native cascade remain separate follow-ups.

## Evidence

```text
warm report SHA-256: 531d6422ff0328d6a82e3619dc35b53b94eefee71e95f40ddb8ee3b304c9605f
result SHA-256:      68b62691f9b94eba547e60ca2e443147cfef6f3cac014669a2b8e036a0605423
evidence SHA-256:    eddaf8d3873e97b4c79b4cdc59fcdd9ac5ff8750765d1967f70e9c4c56e176f3
```

The evidence writer independently recomputes all saved summaries, validates
the frozen parent materialization identity, and reruns the native arithmetic
self-test without requiring timings to reproduce byte for byte.
