# THQ FastScan v3 matched kernel bakeoff

Date: 2026-09-21
Status: `EXECUTED`

## Question

The #437 corrective replay established parity, but its SIMD prototype rebuilt
query LUTs inside every 32-document block, used a plane-major layout, and
converted every uint8 lookup back to FP32 with a per-pair scale. This gate asks
whether a Faiss-style implementation changes the winner, and whether its dense
scan result transfers to the production-shaped R4 candidate workload.

## Protocol

All arms score the same canonical THQ4 96-byte document codes. The benchmark
contains:

1. scalar exact byte LUT;
2. exact byte LUT with four independent accumulators;
3. scalar exact pair LUT;
4. exact AVX2 pair LUT over a block32 layout;
5. scalar per-pair-scale uint8 control;
6. query-prepacked per-pair-scale AVX2 LUT over a plane layout;
7. query-global-scale uint8 LUT with uint16 accumulation over a plane layout;
8. the same integer kernel over a true block32 layout.

The global scale reserves enough range for rounding and verifies that the sum
of per-pair maxima fits uint16. LUTs are packed once per query. Arms are
executed in a deterministic randomized order. Reported `total` timing includes
the arm-specific query-table setup.

Two workloads are intentionally separate:

- dense: persistent layouts are prebuilt, then 1M documents are scored and
  top-128 is selected;
- production-shaped: the frozen R4 stream supplies about 5k arbitrary document
  IDs; each arm includes code gathering, any required runtime packing, scoring,
  and top-128 selection.

The dense result is a bounded eight-query replay with five timed repetitions.
The production result covers all 152 canonical queries with one warmup and 20
timed repetitions per arm. The dense and production audits bind their result,
runner, and source inputs by SHA-256. The production audit additionally binds
the candidate stream, FP32 documents, and qrels. These are source-binding and
invariant audits, not independent computational replays.

## Dense 1M result

| arm | total p50 | total p95 | mean top-128 overlap |
| --- | ---: | ---: | ---: |
| byte LUT scalar | 119.49 ms | 123.81 ms | 1.000000 |
| byte LUT unrolled4 | 87.61 ms | 91.78 ms | 1.000000 |
| pair FP32 scalar | 220.67 ms | 225.12 ms | 1.000000 |
| pair FP32 AVX2 block32 | 75.08 ms | 79.29 ms | 1.000000 |
| pair uint8 scalar | 232.90 ms | 238.46 ms | 0.998047 |
| pair uint8 prepacked plane | 116.69 ms | 122.47 ms | 0.998047 |
| global uint8/u16 plane | 59.84 ms | 62.31 ms | 0.822266 |
| global uint8/u16 block32 | **34.13 ms** | **36.24 ms** | 0.822266 |

The exact pair-SIMD arm disproves the broad interpretation that pair
factorization is inherently slower than byte LUT. With a suitable persistent
layout it is about 14% faster than the optimized exact byte arm on this host.
The Faiss-style integer block32 arm is much faster again, but its global-scale
quantization changes too much of the exact THQ top-128 to be treated as an
exact substitute.

## Production-shaped R4 result

| arm | total p50 | total p95 | total p99 | THQ top-128 overlap | final FP32 top-10 overlap | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| byte LUT scalar | 0.805 ms | 0.896 ms | 0.999 ms | 1.000000 | 1.000000 | .654201 |
| byte LUT unrolled4 | **0.641 ms** | **0.726 ms** | **0.820 ms** | 1.000000 | 1.000000 | .654201 |
| pair FP32 scalar | 1.263 ms | 1.377 ms | 1.471 ms | 1.000000 | 1.000000 | .654201 |
| pair FP32 AVX2 block32 | 1.374 ms | 1.680 ms | 2.031 ms | 1.000000 | 1.000000 | .654201 |
| pair uint8 scalar | 1.356 ms | 1.484 ms | 1.604 ms | 0.998664 | 1.000000 | .654201 |
| pair uint8 prepacked plane | 1.389 ms | 1.675 ms | 2.023 ms | 0.998664 | 1.000000 | .654201 |
| global uint8/u16 plane | 1.267 ms | 1.558 ms | 1.793 ms | 0.919870 | 1.000000 | .654201 |
| global uint8/u16 block32 | 1.233 ms | 1.532 ms | 1.845 ms | 0.919870 | 1.000000 | .654201 |

All exact arms agree with the byte reference at top-128; the maximum recorded
FP32 score error is below `4.5e-7`. Both quantized families preserve the
candidate-FP32 top-10 on all 152 queries after the common FP32 rerank, despite
their different THQ top-128 membership.

The workload changes the decision. Runtime gather and block packing cost more
than the block kernels save at about 5k arbitrary IDs. The optimized doc-major
byte LUT is about 1.9x faster at p50 than the best block32 integer arm. Thus:

> block32 integer FastScan wins a persistent dense scan, while direct
> doc-major byte LUT wins the measured R4 candidate cascade.

This is not a contradiction: the two layouts optimize different access
topologies.

## Decision and limitations

- Keep `byte_lut_unrolled4` as the current production-shaped THQ filter
  candidate.
- Keep exact pair-SIMD block32 as the dense/full-scan candidate.
- Do not promote global-scale uint8 solely from final top-10 survival: its
  top-128 membership loss is large and held-out confirmation is absent.
- Do not materialize the whole 1M corpus only in block32 form for R4 random-ID
  serving; scoring neighbouring documents would create read amplification.
- Timings are from one Windows host. Core/NUMA pinning and hardware counters
  were not available in this run.
- The common final stage in this gate is FP32. Final-codec selection remains a
  separate research question.

Artifacts:

- `2026-09-21-thq-fastscan-v3-dense-result.json`;
- `2026-09-21-thq-fastscan-v3-dense.audit.json`;
- `2026-09-21-thq-fastscan-v3-production-result.json`;
- `2026-09-21-thq-fastscan-v3-production.audit.json`.

Next: run the already prepared Faiss ResidualQuantizer 32/48-byte capacity
gate, then compare the resulting finalist against faithful RSLM and INT8 in a
native end-to-end cascade.
