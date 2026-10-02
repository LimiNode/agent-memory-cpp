# Canonical three-mode serving bake-off

Status: `EXECUTED_DOWNSTREAM_MATRIX`; the serving receipts are source-bound and
the strict validator reports 21/21 packed downstream rows. Fresh qrels and
persistent MDBX gates remain separate.

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
| INT8 | 388 | **407.349 / 412.041 / 415.342 ms** | **0.760 / 0.914 / 1.180 ms** | **0.739 / 0.873 / 0.918 ms** | historical control; fresh qrels pending |
| LSQ32 | 36 | **178.670 / 188.975 / 209.586 ms** | **8.837 / 10.203 / 11.013 ms** | **7.583 / 9.023 / 9.485 ms** | historical qrels; fresh pending |
| LSQ48 | 52 | **228.908 / 241.341 / 254.029 ms** | **13.676 / 14.923 / 15.910 ms** | **11.263 / 13.039 / 13.558 ms** | historical qrels; fresh pending |
| TQ1 | 52/68 | **238.600 / 246.370 / 258.653 ms** | **2.726 / 3.074 / 3.344 ms** | **2.811 / 3.482 / 3.789 ms** | bounded packed-norm control; fresh pending |
| TQ1+PQ8 | 64/68 | **289.122 / 302.801 / 323.720 ms** | **2.726 / 3.074 / 3.344 ms** | **2.811 / 3.482 / 3.789 ms** | packed downstream; fresh pending |
| PLSQ8x6x8 | 52 | **177.522 / 199.755 / 218.113 ms** | **3.498 / 3.871 / 4.692 ms** | **3.106 / 3.565 / 3.802 ms** | packed + independent replay; fresh pending |
| RSLM1 | 52/56 | **245.586 / 261.116 / 272.168 ms** | **2.954 / 3.442 / 3.797 ms** | **3.058 / 3.481 / 3.827 ms** | faithful packed; fresh pending |

All 21 downstream cells now have source-bound packed receipts. These numbers
exclude route generation and MDBX I/O; they are not fresh-quality or product
selection evidence.

### Prototype route checkpoint (2026-10-02)

The first deterministic route artifact is now materialized at
`artifacts/prototype-ivf/route.manifest.json` with the candidate stream in
`artifacts/prototype-ivf/candidate-ids.i4`; its structural receipt is
`artifacts/prototype-ivf/route.audit.json`. It uses the canonical 1M vectors,
25,000 training rows, spherical 256-cell k-means, four selected cells per
query, an 8,192-posting bound and a 5,000-ID downstream candidate budget. The
route is source-bound and reproducible. Packed THQ/final-codec scoring and
independent parity are now recorded for all seven Prototype-IVF cells; quality
decomposition remains a separate gate.

The pre-freeze teacher coverage audit is recorded at
`artifacts/prototype-ivf/quality.audit.json`. After correcting the posting
bound to select by coarse score rather than raw ID order, it reports mean
top-10 teacher coverage 0.6447368, p05 0.1 and a 0/10 worst query. Because the
teacher file is not an independently recomputed exact oracle, this is
calibration evidence, not Recall@5000; packed serving and exact route-quality
decomposition remain required.

### Modern R4 refresh checkpoint (2026-10-02)

The fused source-bound ~5k candidate stream was rerun with one warmup and five
measured repeats for all seven packed finalists. Each arm records 760 raw rows,
packed payload provenance and ordered top-10 parity 152/152. The receipts are
under `artifacts/modern-r4-packed/`; the older `candidate148` predecoded rows
remain diagnostic only. Route generation, codec decode and MDBX I/O remain
outside the timed downstream scope.

The machine-readable checkpoint for this boundary is
[`2026-10-02-three-mode-bakeoff.inventory.json`](2026-10-02-three-mode-bakeoff.inventory.json).
It hashes the receipts and is expected to report `21/21`; it does not turn the
downstream matrix into a fresh-quality or persistence result.

Completion is checked separately by
`tools/agent-memory-bench/validate-three-mode-completion.py`. That validator
is intentionally strict: it requires all seven mandatory finalists in all
three modes, raw/audit bindings and independent ordered `152/152` parity. It
fails if any packed row loses those bindings.

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
