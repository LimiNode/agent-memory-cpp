# Native flat and matched R4 finalist wave

Status: executed for the canonical three-mode serving matrix. The INT8
corrective pass below replaces the earlier scaled-dot label with exact
reconstructed cosine and binds all three INT8 rows to independent packed
replay receipts. Fresh untouched qrels and finalist-specific MDBX remain
separate gates.

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

The earlier matched-reranker control receipt reported 0.1627/0.2030/0.2592 ms
for INT8 under a different shell and is retained only as historical control
evidence. The canonical three-mode INT8 reruns are listed below with the same
1+5 contract. Raw coverage, ID cardinality, duplicate detection, and
percentile replay passed the independent audit. See
[`2026-10-01-native-matched-r4-final-reranker-control.result.json`](2026-10-01-native-matched-r4-final-reranker-control.result.json).

## INT8 semantic corrective pass

The previous INT8 receipts labelled a scaled reconstructed dot-product as
`reconstructed_cosine`. The canonical scorer now uses the persisted inverse
code norm and computes exact reconstructed cosine:

`dot(code, query) / (||code|| * ||query||)`.

The sidecar is `artifacts/int8-full-code-norms.f32` (SHA-256
`69e7e6c092643b4b78508481af7bd367eee8940a37b1cad5f9ef0d9d093dd284`), and
the resulting reconstructed-norm distribution is min 0.998447, max 1.001533,
mean 1.000018, p95 absolute deviation 0.000601. Exact dense and fused paths
agree for all 760 measured rows; the old scaled-dot control agrees with exact
cosine for only 435/760 rows and is retained only as an optimization control.

Canonical INT8 evidence is now:

| Mode | p50 / p95 / p99 ms | ordered parity | candidate SHA |
| --- | ---: | ---: | --- |
| Full-flat 1M | 407.166 / 417.831 / 428.745 | 152/152 | packed payload SHA `75d76475…` |
| Prototype-IVF balanced | 0.7485 / 0.868 / 1.214 | 152/152 | `a983347f…` calibrated route |
| Modern R4 | 0.7927 / 0.9357 / 1.151 | 152/152 | `d76cabd5…` semantic identity |

The full-flat raw/audit pair is
`artifacts/int8-full-cosine-direct.{raw.jsonl,audit.json}`; routed raw and
audit receipts are under `artifacts/prototype-ivf/` and
`artifacts/modern-r4-packed/`. The strict completion validator reports 21/21
serving rows; this is evidence completeness, not a fresh-quality or product
winner claim.
