# Canonical three-mode serving bake-off

Status: `CHECKPOINT`; the serving receipts are source-bound and the strict
validator reports 21/21 downstream rows after identity/audit checks. The
historical Prototype-IVF route sweep is complete and the packed matrix has
been regenerated against the selected `256/32/5000` route. This is not yet a
product freeze: fresh untouched qrels and persistent MDBX gates remain
separate.

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
| INT8 | 392 | **407.166 / 417.831 / 428.745 ms** | **0.7485 / 0.868 / 1.214 ms** | **0.7927 / 0.9357 / 1.151 ms** | exact reconstructed-cosine control; fresh qrels pending |
| LSQ32 | 36 | **178.670 / 188.975 / 209.586 ms** | **3.386 / 3.782 / 4.046 ms** | **3.479 / 3.855 / 4.384 ms** | sparse-LUT scorer; historical qrels; fresh pending |
| LSQ48 | 52 | **228.908 / 241.341 / 254.029 ms** | **4.482 / 4.868 / 5.128 ms** | **4.596 / 4.976 / 5.430 ms** | sparse-LUT scorer; historical qrels; fresh pending |
| TQ1 | 52/68 | **238.600 / 246.370 / 258.653 ms** | **2.564 / 2.838 / 2.989 ms** | **2.511 / 2.853 / 2.963 ms** | exact reconstructed TQ norm; fresh pending |
| TQ1+PQ8 | 64/68 | **289.122 / 302.801 / 323.720 ms** | **2.773 / 3.108 / 3.554 ms** | **2.613 / 3.007 / 3.414 ms** | independent PQ8 replay; fresh pending |
| PLSQ8x6x8 | 52 | **177.522 / 199.755 / 218.113 ms** | **3.392 / 3.715 / 3.947 ms** | **3.106 / 3.565 / 3.802 ms** | packed + independent replay; fresh pending |
| RSLM1 | 52/56 | **245.586 / 261.116 / 272.168 ms** | **3.099 / 3.460 / 3.680 ms** | **3.058 / 3.481 / 3.827 ms** | faithful packed; fresh pending |

All 21 downstream cells now have source-bound packed receipts. These numbers
exclude route generation and MDBX I/O; they are not fresh-quality or product
selection evidence.

### Prototype route checkpoint (2026-10-02)

The selected deterministic route artifact is materialized at
`artifacts/prototype-ivf/route.manifest.json` with the candidate stream in
`artifacts/prototype-ivf/calibrated-256-32-5000.i4`; its structural receipt is
`artifacts/prototype-ivf/route.audit.json`. It uses the canonical 1M vectors,
25,000 training rows, spherical 256-cell k-means, 32 selected cells per
query, a 5,000-ID downstream candidate budget and score-descending/ID-ascending
posting order. The route is source-bound and reproducible. Packed THQ/final-
codec scoring and independent parity are now recorded for all seven
Prototype-IVF cells; quality decomposition remains a separate gate.

The historical calibration audit is recorded at
`artifacts/prototype-ivf/historical-calibration.json`. The selected point
reports mean exact FP32 Recall@candidate 0.9263, p05 0.7 and worst 0.4 on the
historical fixture. This is calibration evidence, not fresh qrels; fresh
quality and route-loss decomposition remain required.

### Modern R4 refresh checkpoint (2026-10-02)

The fused source-bound ~5k candidate stream was rerun with one warmup and five
measured repeats for all seven packed finalists. Each arm records 760 raw rows,
packed payload provenance and ordered top-10 parity 152/152. The receipts are
under `artifacts/modern-r4-packed/`; the older `candidate148` predecoded rows
remain diagnostic only. Route generation, codec decode and MDBX I/O remain
outside the timed downstream scope.

The machine-readable checkpoint for this boundary is
[`2026-10-02-three-mode-bakeoff.inventory.json`](2026-10-02-three-mode-bakeoff.inventory.json).
It hashes the receipts and reports `21/21` only for the downstream evidence
inventory. It does not turn the matrix into a fresh-quality or persistence
result, and it is not a frozen product acceptance gate.

### LSQ scorer-equity correction (2026-10-03)

The earlier LSQ routed rows timed gather, full-LUT and sparse-LUT scorers in
one interval and therefore were not a fair production comparison. A separate
frozen-shell bake-off selected `sparse_lut` for both LSQ32 and LSQ48 by the
complete prepare+score+top-k total. Gather and full-LUT remain correctness
checks outside the timed interval. The four LSQ routed cells were regenerated
with one selected scorer; the selection receipt is
[`artifacts/lsq-scorer-bakeoff.json`](../../artifacts/lsq-scorer-bakeoff.json)
and the implementation-path manifest is
[`artifacts/three-mode-performance-paths.json`](../../artifacts/three-mode-performance-paths.json).

### Corrective evidence pass (2026-10-02)

TQ1 and TQ1+PQ8 are timed in separate native invocations. TQ1 uses the exact
analytical reconstructed-norm sidecar; it no longer reuses the PQ8 final-norm
denominator. Independent packed replays pass ordered top-10 parity 152/152 for
both modes and both routed shells. RSLM1 has an independent transform/UE7M9
replay with the same 152/152 parity. The Modern-R4 physical fused stream is
bound to the canonical candidate identity by
`artifacts/modern-r4-packed/candidate-identity.audit.json`.

The historical route gate is complete. Fresh qrels remain closed until the
route/configuration manifest is frozen and all fresh candidate streams are
regenerated from untouched inputs.

The completed historical sweep covers all 48 declared points. The strongest
historical candidate-recall point was `nlist=256, nprobe=32, budget=5,000`
(mean Recall@candidate 0.9263, p05 0.7, worst 0.4); the current 256/4 route
was only 0.6447 mean with p05 0.1 and worst 0.0. These are exact-FP32
candidate-recall diagnostics on the historical query fixture, not fresh qrels.
The packed Prototype-IVF receipts are now bound to the selected
`a983347f...` stream; the prior `f281...` receipts are retained only as
historical evidence.

Completion is checked separately by
`tools/agent-memory-bench/validate-three-mode-completion.py`. That validator
is intentionally strict: it requires all seven mandatory finalists in all
three modes, raw/audit bindings and independent ordered `152/152` parity. It
fails if any packed row loses those bindings.

The remaining external gate is fresh untouched qrels. The inventory records
the concrete search scope (`repository artifacts/guides`, this workspace and
`fidelity-heavy-batch-v1`) as `EXTERNAL_NOT_FOUND`; the available
`qrel-ids.i8`/`qrel-scores.f32` files are the historical fixture and are not
reused as fresh labels. No fresh quality or Pareto/product winner is claimed
until that external label bundle is supplied.

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
