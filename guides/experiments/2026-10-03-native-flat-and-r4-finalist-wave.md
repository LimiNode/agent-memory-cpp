# Native flat and matched R4 finalist wave

Status: executed for LSQ32/LSQ48/PLSQ8x6 full-flat and PLSQ8x6/RSLM1 matched R4. TQ1,
TQ1+PQ8 and RSLM1 full-flat remain pending because no 1M packed payload is
available for those arms.

## Scope

All completed rows use the frozen 1M document corpus, the canonical 152-query
legacy projection, numeric document IDs, deterministic cosine top-10 ordering,
and one warmup plus one measured repeat. The flat LSQ runner scans the ordered
packed payload directly with a bounded top-10 heap. It does not reconstruct
FP32 document rows. The matched R4 runners scan the frozen candidate stream,
select THQ top-128, and score those IDs with the native packed final scorer.

## Receipts

| Arm | Scope | p50 ms | p95 ms | p99 ms | parity |
| --- | --- | ---: | ---: | ---: | --- |
| LSQ32 | full 1M packed flat | 223.032 | 238.494 | 244.840 | native top-10 emitted |
| LSQ48 | full 1M packed flat | 252.526 | 264.469 | 269.872 | native top-10 emitted |
| PLSQ8x6x8 | full 1M packed flat | 188.6500 | 199.5890 | 207.6412 | native top-10 emitted |
| PLSQ8x6x8 | frozen R4 candidate stream → THQ top-128 → packed scorer | 3.5738 | 3.9454 | 5.0577 | 152/152 |
| RSLM1 | canonical R4 candidate stream → THQ top-128 → packed scorer | 3.6574 | 3.8830 | 3.9269 | packed output matches full-candidate reference 152/152 |

The flat timings are host-specific research evidence, not acceptance
thresholds. Their payload materialization and scorer source hashes are bound
in the external receipt directory used for the run. The PLSQ/RSLM matched
rows are end-to-end over the candidate stream, unlike the earlier candidate-
local codec-only gates.

## Remaining gates

The table is intentionally not filled with decoded-vector measurements. A
full-flat row requires a native packed 1M representation and a scorer for the
same representation. TQ1/TQ1+PQ8 and RSLM1 therefore remain `pending` until
their packed corpus payloads exist; MDBX persistence, cold/restart/recovery,
and fresh untouched qrels remain separate gates.
