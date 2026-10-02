# Canonical three-mode serving bake-off

Status: `PLANNED`; no new measurements are claimed by this note.

The compressed-codec program has three serving modes. They answer different
questions and must not be collapsed into one latency ranking:

| Mode | Execution contract | Question |
| --- | --- | --- |
| **Full-flat packed 1M** | 1M packed rows → native packed scorer → ordered top-10 | What is the cost of the codec/kernel without routing? |
| **Prototype-IVF / balanced cascade** | prototype-IVF coarse cells → candidate budget → THQ top-128 → packed final scorer → ordered top-10 | What does a cheaper coarse route provide? |
| **Modern R4 cascade** | R4 route (~5k candidates) → THQ4 top-128 → packed final scorer → ordered top-10 | What does the quality-oriented production path provide? |

The already-known `candidate-local` fixture (fixed top-128 IDs → final scorer)
is a component benchmark, not a fourth production mode. It remains useful for
diagnosing scorer cost, but cannot substitute for either routed cascade.

## Matched matrix

The frozen finalist set is INT8 control, LSQ32, LSQ48, TQ1, TQ1+PQ8,
PLSQ8x6x8 and RSLM1. PLSQ8x4x8 remains an optional low-byte control. Every
executed row must use the same 1M corpus, 152-query order, numeric IDs,
ordered tie rule, one warmup plus five measured repeats, nearest-rank p50/p95/p99,
and raw JSONL plus an independent audit.

| Codec | Bytes/doc | Flat 1M | Prototype-IVF cascade | Modern R4 cascade | Quality |
| --- | ---: | --- | --- | --- | --- |
| INT8 | 388 | measured control | pending | measured control; unified refresh pending | historical control; fresh qrels pending |
| LSQ32 | 36 | measured packed | pending | measured under older contract; unified refresh pending | historical qrels; fresh pending |
| LSQ48 | 52 | measured packed | pending | measured under older contract; unified refresh pending | historical qrels; fresh pending |
| TQ1 | 52/68 | **238.600 / 246.370 / 258.653 ms** | pending | measured under canonical reconstructed-cosine full-flat contract; unified refresh pending | historical qrels; fresh pending |
| TQ1+PQ8 | 64/68 | pending packed 1M | pending | measured under older contract; unified refresh pending | partial historical; fresh pending |
| PLSQ8x6x8 | 52 | measured packed + independent replay | pending | measured packed; unified refresh pending | historical qrels; fresh pending |
| RSLM1 | 52 | pending packed 1M | pending | measured packed; unified refresh pending | historical qrels; fresh pending |

`pending` is intentional: the repository currently has no source-bound packed
prototype-IVF receipts for these finalists, and decoded/scalar historical IVF
results are not promoted into this matrix.

The machine-readable checkpoint for this boundary is
[`2026-10-02-three-mode-bakeoff.inventory.json`](2026-10-02-three-mode-bakeoff.inventory.json).
It hashes present receipts and records every missing row as
`PENDING_SOURCE_REPLAY`; it does not treat this inventory as a benchmark
result.

Completion is checked separately by
`tools/agent-memory-bench/validate-three-mode-completion.py`. That validator
is intentionally strict: it requires all seven mandatory finalists in all
three modes, raw/audit bindings and independent ordered `152/152` parity. It
must fail while the checkpoint inventory is partial.

## Execution contract

1. Freeze codec payloads, THQ4 payload/query codes, route manifests and query
   order before timing.
2. For flat mode, scan all 1M packed rows and score directly from the packed
   representation; no FP32 document reconstruction in the timed path.
3. For Prototype-IVF, bind a deterministic prototype/centroid manifest,
   `nlist`, training prefix, seed, cell assignment, candidate budget and
   posting order. Record coarse-routing, candidate materialization, THQ,
   final-score and total timings.
4. For Modern R4, bind the R4 candidate stream and execute the same THQ4
   top-128 implementation used by the production control before final scoring.
5. Preserve ordered top-10 IDs for every query and compare against an
   independent reference scorer where the representation permits it.
6. Publish bytes/doc with code payload, sidecars and amortized shared models
   separately. Do not count candidate-local timings as end-to-end latency.

## Acceptance and dependencies

| Milestone | Dependency | Minimum benchmark | Acceptance | Risk |
| --- | --- | --- | --- | --- |
| M1: unified R4 refresh | packed payloads and frozen R4 shell | all finalist arms, 152 queries, 1+5 repeats | raw coverage, ordered parity, independent audit, one percentile contract | old and new harnesses are not directly rankable |
| M2: Prototype-IVF packed route | prototype manifest and packed corpus payloads | at least one declared budget plus full finalist matrix | route/THQ/final/total timings and candidate recall against exact oracle | coarse route can hide quality loss or duplicate work |
| M3: fresh quality | M1/M2 freeze; untouched queries/qrels | Recall@K, nDCG@10, MRR, worst-query/p05 | no codec selection before qrels and route regeneration pass | historical 152 may overfit configuration |
| M4: persistence | 2–3 Pareto survivors only | finalist-specific MDBX warm/cold/reopen/rebuild | bytes, pages, p50/p95/p99, publication and recovery checks | storage layout can reorder the Pareto frontier |

The existing `native_thq_ivf_bakeoff` and older IVF notes remain historical
diagnostics because they use scalar/FP32 reranking and a different timing
contract. They are useful for route hypotheses, not evidence for this matched
codec matrix.
