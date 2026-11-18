# Algorithm Research Sweep

## Scope and source identity

This registry records the algorithm research surface at `main` commit
`00a9695762ab3bbdc9d5193d371dd8b25ca7b51c`. It is a research planning and
evidence index; it does not change production APIs, ABI, persisted formats,
retrieval defaults, or dependency policy.

The machine-readable inventory is
`2026-11-18-algorithm-research-sweep-registry.json`. Validate it with
`python tools/agent-memory-bench/validate-algorithm-research-sweep.py` and run
its mutation controls with `--self-test`.

Every executable stage must record the exact source SHA, compiler and build
identity, corpus/fixture digest, seed, raw samples, correctness oracle, and
mutation controls. Kernel-only or synthetic timings must not be presented as
production latency.

## Inventory

| Status | Surface | Evidence and disposition |
| --- | --- | --- |
| `ALREADY_IMPLEMENTED` | Exact float/vector search, SIMD similarity, binary encoders, flat and multiprobe binary indexes, lexical tokenization/BM25, and `HybridRetrievalEngine` scaffolding | Covered by source tests and existing benchmark/evaluation receipts. The hybrid engine is not evidence of BM25+dense fusion quality. |
| `ALREADY_IMPLEMENTED` | Binary and lexical evaluation harnesses, provenance/receipt conventions, and exact-result controls | Reuse these controls; do not create a second receipt format for a new algorithm family. |
| `NEEDS_CORRECTION` | Early scalar and PQ/OPQ reports, the first RTHQ ITQ orientation row, and Python ANN timing claims | Use the corrected seed manifest, full-calibration report, corrected RTHQ note, and native measurements. Superseded reports remain historical and must not be used for selection. |
| `WORTH_BENCHMARK` | Native packed-LUT scalar ADC/top-k; native MIH layouts under matched budgets; float prototype-IVF and compact THQ/scalar cascades; coordinate-aware ordinal multiprobe; matched-byte PQ/OPQ; hybrid BM25+dense fusion | Each is a separate research stage with a separate baseline, quality gate, and timing scope. No stage implies production adoption. |
| `PREVIOUSLY_REJECTED` | Binary Hamming as a final ranker, label-free learned ADC, equal-payload uncertainty-mask/two-plane ternary controls, tiny-corpus multiprobe selection, and production HNSW adoption from Python-only timing | Preserve the negative evidence and do not reopen these branches without a new falsifiable mechanism and a matched control. |
| `DEFER` | Production IVF/HNSW/PQ/OPQ classes, classical cosine-LSH, decoder-only autoencoder ranking, and morphology/SPLADE/ColBERT integrations | Require a representative corpus and a defined serving contract before implementation. |

## Staged research work

### Stage 0 — corrective closure

PR #635 (`research/exact-numerical-frontier`) and PR #633
(`research/reranker-admission-scaling`) remain independent Draft PRs. Their
acceptance evidence is exact-head CI plus local focused tests and receipt
inspection. They are prerequisites for independent review, not production
changes.

### Stage 1 — native exact-result controls

Measure only after the correctness baseline is fixed:

* full-result sorting versus bounded top-k selection in `ExactLexicalIndex`;
* `MultiProbeHammingIndex` probe-state initialization and duplicate handling;
* packed-LUT scalar ADC and top-k selection with an independent exact oracle.

For each candidate, report build/index cost separately from lookup cost,
allocation/copy cost, exact top-k parity, mutation failures, and raw timing
samples. A speedup is not actionable when it changes tie ordering, filtering,
or freshness semantics.

### Stage 2 — quantization and ANN feasibility

Compare TurboQuant/RaBitQ/THQ/ITQ/PQ/OPQ only on a matched corpus, payload
budget, training budget, and query set. Separate training time, encoded size,
held-out quality, candidate generation, exact rerank, and end-to-end lookup.
For E-means/O-means and IVF, first produce a design/feasibility note covering
centroid counts, training corpus size, empty clusters, held-out assignment
error, recall, retraining cost, and whether offline training can feed native
C++ serving. GPU/CUDA/Triton work is offline-only; unavailable hardware is
reported as `NOT_EXECUTED`.

### Stage 3 — lexical and hybrid retrieval

Before WAND/MaxScore/Block-Max WAND, identify the measured bottleneck among
scoring, candidate generation, I/O, filters, and sorting. Any pruning method
must prove upper-bound correctness and exact top-k parity. Separately evaluate
lexical-only, dense-only, lexical+reranker, RRF, score normalization, and
optional query-adaptive fusion on a sealed quality fixture. Report worst-query
regressions, source loss, freshness/revision behavior, and filter
authorization. No new production default follows from an offline receipt.

## Acceptance contract

Every stage must publish:

1. hypothesis and named baseline;
2. exact source and environment identity;
3. corpus, seed, fixture digest, and parameters;
4. independent correctness oracle and negative/mutation controls;
5. raw samples with explicit timing scope and sample-count semantics;
6. quality, memory, and failure-path results;
7. limitations, unresolved inquiries, and a bounded status.

Allowed statuses are `READY_FOR_INDEPENDENT_REVIEW`, `RESEARCH_QUALIFIED`,
`CORRECTIVE_REQUIRED`, `DEFER`, and `NOT_EXECUTED`. A research result never
activates a production default. Merge, release, package publication, and
production activation remain outside this sweep.

## Next actions

1. Independently review #635 and #633 at their rebased exact heads.
2. Open one Draft PR for the native exact-result controls, keeping benchmark
   code and receipts separate from production index changes.
3. Open separate design/feasibility PRs for quantization/ANN and hybrid
   retrieval only when the preceding evidence gates are satisfied.
