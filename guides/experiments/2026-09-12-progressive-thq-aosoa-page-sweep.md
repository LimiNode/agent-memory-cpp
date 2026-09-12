# Page-matched progressive THQ-ADC layout sweep

Date: 2026-09-12  
Fixture: frozen `thq-full-scan-v2` (1,000,000 documents, 152 queries,
THQ4-384).  This note records the follow-up requested after the first AoSoA
replay.  It is deliberately scoped to logical payload accounting until a
native/MDBX backend supplies physical page counters.

## Hypothesis

Progressive ADC can eliminate a substantial amount of document-level work, but
global tile/page savings depend on whether all documents in a tile become
inactive.  Therefore the relevant control is a matched ~4-KB payload sweep:

| tile documents | coordinates/block | payload/block |
| ---: | ---: | ---: |
| 512 | 32 | 4096 B |
| 256 | 64 | 4096 B |
| 128 | 128 | 4096 B |

The layouts are materialized from the same frozen manifest and document-code
SHA.  Their manifests are cryptographically bound to that source; no payload
is copied into Git.

## Conformance smoke

The fixed-order parity gate passed for two queries on both new layouts.  The
runner compares the progressive result with a canonical fixed-order exhaustive
scan using `(score, document_id)` tie ordering.

| layout | query 0 logical bytes | query 1 logical bytes | query 0 coordinate fraction | query 1 coordinate fraction |
| --- | ---: | ---: | ---: | ---: |
| 256×64 | 95,699,968 | 90,421,248 | 0.746344 | 0.666325 |
| 128×128 | 96,000,000 | 95,791,104 | 0.813652 | 0.703247 |

These are smoke measurements, not a 152-query result.  In particular, they do
not establish OS/MDBX page savings or production latency.

Full fixed-order logical timing subsequently completed for all 152 queries:

| layout | p50 ms | p95 ms | mean logical bytes | mean blocks | mean coordinate fraction | mean survival@256 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 256×64 | 20051.86 | 20412.95 | 94,102,104 | 22,978.1 | 0.741228 | 1.000000 |
| 128×128 | 25496.02 | 25665.32 | 95,843,059 | 23,400.7 | 0.811934 | 1.000000 |

As an apples-to-apples Python control, the packed flat ordinal scan completed
all 152 queries over the same frozen payload: p50 `3613.34 ms`, p95
`3668.53 ms`, mean teacher survival@256 `0.999342`, and exact packed/thermometer
equality for the first two queries.  This control is useful for relative
orchestration overhead only; it is not a native SIMD or page-latency result.

The native packed harness then completed the same 152-query corpus on one
machine.  It measured thermometer Hamming p50/p95 `77.156/85.387 ms`, packed
ordinal-L1 `808.990/838.941 ms`, and interval-squared ADC `662.330/682.573 ms`.
Teacher survival@256 was `0.999342` for Hamming and ordinal-L1 and `1.0` for
ADC.  This is a native CPU component benchmark; it does not measure MDBX page
faults, cold-cache behavior, or an AoSoA scan.

## Interpretation and next gate

The first replay already showed that global AoSoA pruning saved only a small
fraction of logical payload while reducing coordinate work.  The page-matched
sweep tests the granularity trade-off rather than assuming that finer tiles are
better.  A native flat packed scan versus progressive AoSoA showdown remains
the decisive compute/bandwidth test.  Only after that comparison should an
R4/K8/K32 candidate-page cascade or MDBX page benchmark be interpreted.

The separate dynamic-cutoff oracle is stronger on compute accounting: across
760 all-parity rows it reduced equivalent coordinate work to a mean `0.297589`
of flat scan while preserving exact top-256 and cutoff parity.  The gap between
that oracle and the roughly 90--96 MB logical payload reads is the reason page
selection (R4/K8/K32 or clustered storage order) remains the next architectural
experiment.

`adc_lut_variance` is an unweighted diagnostic.  `adc_expected_cost` uses the
corpus level histogram to rank blocks by expected interval-squared ADC cost;
`adc_expected_variance` uses the same histogram to rank uncertainty.  They are
separate experimental factors and must not be conflated.

Execution receipts remain `PENDING` until the full replay artifacts, runner and
layout hashes, and (for physical claims) native page measurements are present.

During the all-query parity gate, the initial float32 implementation failed on
some reordered queries because accumulation order perturbed close top-k scores.
The runner now accumulates block contributions in float64 and reports the
failing query index.  A 20-query probe passes with this correction; the full
152-query parity run is in progress and remains fail-closed.
