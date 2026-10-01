# Native flat and matched R4 finalist wave

Status: executed for LSQ32/LSQ48/PLSQ8x6 full-flat and the matched R4
final-reranker control wave. TQ1, TQ1+PQ8 and RSLM1 full-flat remain pending
because no 1M packed payload is available for those arms.

## Scope

All completed rows use the frozen 1M document corpus, the canonical 152-query
legacy projection, numeric document IDs, deterministic cosine top-10 ordering,
and deterministic timing contracts. The refreshed PLSQ controls use one warmup
plus five measured repeats; older LSQ rows remain directional one-repeat
evidence. The flat LSQ runner scans the ordered
packed payload directly with a bounded top-10 heap. It does not reconstruct
FP32 document rows. The matched R4 runners scan the frozen candidate stream,
select THQ top-128, and score those IDs with the native packed final scorer.

## Receipts

| Arm | Scope | p50 ms | p95 ms | p99 ms | parity |
| --- | --- | ---: | ---: | ---: | --- |
| LSQ32 | full 1M packed flat | 223.032 | 238.494 | 244.840 | native top-10 emitted |
| LSQ48 | full 1M packed flat | 252.526 | 264.469 | 269.872 | native top-10 emitted |
| PLSQ8x6x8 | full 1M packed flat | 177.9757 | 191.1420 | 202.3426 | native top-10 emitted; independent reference 152/152 |
| PLSQ8x4x8 | full 1M packed flat | 164.3054 | 172.1001 | 175.9914 | native top-10 emitted |
| PLSQ8x6x8 | frozen R4 candidate stream → THQ top-128 → packed scorer | 0.6996 | 0.7810 | 0.8465 | 152/152 |
| PLSQ8x4x8 | frozen R4 candidate stream → THQ top-128 → packed scorer | 3.3612 | 3.7177 | 3.8076 | 152/152 |
| RSLM1 | canonical R4 candidate stream → THQ top-128 → packed scorer | 0.8891 | 1.0032 | 1.0696 | structural audit passed; packed reference parity remains separate |

The flat timings are host-specific research evidence, not acceptance
thresholds. Their payload materialization and scorer source hashes are bound
in the external receipt directory used for the run. The PLSQ/RSLM matched
rows are end-to-end over the candidate stream, unlike the earlier candidate-
local codec-only gates. PLSQ full-flat top-10 was independently replayed from
the AMPLSQF1 payload and matched the native output for all 152 queries.

## Remaining gates

The table is intentionally not filled with decoded-vector measurements. A
full-flat row requires a native packed 1M representation and a scorer for the
same representation. TQ1/TQ1+PQ8 and RSLM1 therefore remain `pending` until
their packed corpus payloads exist; MDBX persistence, cold/restart/recovery,
and fresh untouched qrels remain separate gates.

## Matched final-reranker control refresh

Using the same frozen R4 shell and five measured repeats, the refreshed totals
are PLSQ8x6x8 0.6996/0.7810/0.8465 ms (p50/p95/p99), RSLM1
0.8891/1.0032/1.0696 ms, and the matched INT8 control
0.1627/0.2030/0.2592 ms under the canonical audited percentile contract.
INT8's final rerank p50 is 0.0609 ms. Raw coverage,
ID cardinality, duplicate detection, and percentile replay passed the
independent audit. See
[`2026-10-01-native-matched-r4-final-reranker-control.result.json`](2026-10-01-native-matched-r4-final-reranker-control.result.json).
