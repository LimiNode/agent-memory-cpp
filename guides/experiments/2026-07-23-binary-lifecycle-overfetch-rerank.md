# Binary lifecycle overfetch and exact rerank

## 2026-07-23 / PR #84

Question: can the binary lifecycle benchmark measure binary overfetch followed
by exact float rerank while keeping the exact oracle target fixed?

Why this matters:

- PR #83 split `oracle_k` from the returned binary candidate count.
- The next practical retrieval question is not whether binary top-10 equals
  exact top-10. It is whether binary search can return a wider candidate set
  cheaply enough for exact float rerank to recover useful exact neighbours.
- Multi-probe Hamming search also has an internal bucket-search regime, so the
  report must expose per-limit diagnostics instead of only final quality.

Setup:

- command: `agent-memory-binary-lifecycle-bench`
- configs:
  - `tools/agent-memory-bench/binary-lifecycle-10k.manual.json`
  - `tools/agent-memory-bench/binary-lifecycle-100k.manual.json`
- synthetic normalized vectors;
- vector dimension: 64;
- binary encoder: `random_hyperplane_rademacher` v2;
- bit count: 128;
- exact oracle target: `oracle_k = 10`;
- baseline returned candidate limit: 10;
- rerank candidate limits: `10`, `32`, `64`, `128`, `256`, `512`;
- independent query noise seed inherited from PR #83.

Queries are noisy copies of corpus documents that remain in the corpus. This
makes top-1 agreement a self-retrieval-like signal and easier than arbitrary
nearest-neighbour queries.

Contract change:

- The report now contains `rerank_candidate_limits`.
- `flat_binary.rerank` and `multiprobe_binary.rerank` contain one row per
  candidate limit.
- Each rerank row reports:
  - binary search timing;
  - returned binary result count;
  - exact top-k candidate coverage;
  - exact rerank timing;
  - reranked recall vs exact top-k;
  - reranked top-1 agreement;
  - binary-search + exact-rerank end-to-end timing.
- Multi-probe rerank rows additionally report per-limit:
  - mean unique candidate count;
  - mean probed bucket count;
  - mean visited posting count.

The CTest smoke gate keeps a smaller grid `16`, `32`, `64` and validates the
rerank report structure without pinning directional timing or quality values.

Commands:

```powershell
cmake --build tmp\build-pr81-mingw --target agent-memory-binary-lifecycle-bench --parallel 1
tmp\build-pr81-mingw\tools\agent-memory-bench\agent-memory-binary-lifecycle-bench.exe `
  tools\agent-memory-bench\binary-lifecycle-10k.manual.json `
  tmp\build-pr81-mingw\binary-lifecycle-10k-pr84-fix.json
tmp\build-pr81-mingw\tools\agent-memory-bench\agent-memory-binary-lifecycle-bench.exe `
  tools\agent-memory-bench\binary-lifecycle-100k.manual.json `
  tmp\build-pr81-mingw\binary-lifecycle-100k-pr84-fix.json
```

Baseline snapshot:

| Scale | Exact query mean ms | Flat binary mean ms | Multi-probe mean ms | Flat returned top-10 overlap | Multi-probe returned top-10 overlap |
|---:|---:|---:|---:|---:|---:|
| 10k | 3.162 | 0.792 | 0.419 | 0.219 | 0.218 |
| 100k | 39.641 | 7.847 | 0.599 | 0.166 | 0.139 |

Build/lifecycle snapshot:

| Scale | Exact build ms | Flat build ms | Multi-probe build ms | Process peak RSS bytes |
|---:|---:|---:|---:|---:|
| 10k | 31.964 | 59.844 | 92.261 | 42,946,560 |
| 100k | 366.192 | 648.689 | 995.115 | 245,682,176 |

Flat binary overfetch + exact rerank:

| Scale | Candidates | Coverage@10 | Reranked top-1 agreement | Binary search mean ms | Exact rerank mean ms | End-to-end mean ms |
|---:|---:|---:|---:|---:|---:|---:|
| 10k | 10 | 0.219 | 1.000 | 0.783 | 0.015 | 0.798 |
| 10k | 32 | 0.341 | 1.000 | 0.809 | 0.047 | 0.856 |
| 10k | 64 | 0.444 | 1.000 | 0.862 | 0.082 | 0.943 |
| 10k | 128 | 0.564 | 1.000 | 0.938 | 0.132 | 1.070 |
| 10k | 256 | 0.697 | 1.000 | 1.032 | 0.233 | 1.265 |
| 10k | 512 | 0.820 | 1.000 | 1.293 | 0.434 | 1.727 |
| 100k | 10 | 0.166 | 1.000 | 7.871 | 0.017 | 7.888 |
| 100k | 32 | 0.244 | 1.000 | 7.846 | 0.053 | 7.899 |
| 100k | 64 | 0.303 | 1.000 | 7.863 | 0.091 | 7.954 |
| 100k | 128 | 0.386 | 1.000 | 7.914 | 0.156 | 8.070 |
| 100k | 256 | 0.488 | 1.000 | 8.226 | 0.280 | 8.506 |
| 100k | 512 | 0.600 | 1.000 | 8.454 | 0.525 | 8.979 |

Multi-probe overfetch + exact rerank:

| Scale | Candidates | Coverage@10 | Reranked top-1 agreement | Binary search mean ms | Exact rerank mean ms | End-to-end mean ms | Mean bucket candidates | Mean probed buckets |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10k | 10 | 0.218 | 1.000 | 0.410 | 0.015 | 0.425 | 2,604.4 | 72.0 |
| 10k | 32 | 0.336 | 1.000 | 0.456 | 0.049 | 0.505 | 2,604.4 | 72.0 |
| 10k | 64 | 0.425 | 1.000 | 0.523 | 0.080 | 0.603 | 2,604.4 | 72.0 |
| 10k | 128 | 0.524 | 1.000 | 0.676 | 0.130 | 0.807 | 2,604.4 | 72.0 |
| 10k | 256 | 0.623 | 1.000 | 0.957 | 0.236 | 1.193 | 2,604.4 | 72.0 |
| 10k | 512 | 0.695 | 1.000 | 1.402 | 0.439 | 1.841 | 2,604.4 | 72.0 |
| 100k | 10 | 0.139 | 0.844 | 0.586 | 0.016 | 0.602 | 3,578.6 | 8.0 |
| 100k | 32 | 0.185 | 0.844 | 0.750 | 0.056 | 0.806 | 3,645.8 | 8.2 |
| 100k | 64 | 0.290 | 0.984 | 4.063 | 0.096 | 4.158 | 22,525.0 | 62.8 |
| 100k | 128 | 0.378 | 1.000 | 4.367 | 0.154 | 4.521 | 26,038.3 | 72.0 |
| 100k | 256 | 0.479 | 1.000 | 5.046 | 0.281 | 5.327 | 26,038.3 | 72.0 |
| 100k | 512 | 0.568 | 1.000 | 5.956 | 0.526 | 6.483 | 26,038.3 | 72.0 |

Interpretation:

- Exact rerank does what it should: `reranked_recall_at_k_vs_exact` equals
  candidate coverage because the reranker reproduces the oracle cosine scorer
  and `ChunkId` tie-break over the returned binary candidates.
- Top-1 is much easier than top-10 on this fixture. Flat keeps top-1 agreement
  at `1.000` even when top-10 coverage is low; multi-probe reaches `1.000` at
  100k once overfetch reaches 128 candidates. Because the queries are noisy
  copies of corpus documents, this should not be generalized to arbitrary
  nearest-neighbour queries.
- At 10k, multi-probe already probes the full radius-1 budget for all tested
  candidate limits, so overfetch mostly changes final Hamming truncation and
  rerank cost.
- At 100k, multi-probe has a visible regime switch:
  - 10/32 candidates stop after roughly eight buckets;
  - 64 candidates expand to about 63 buckets;
  - 128+ candidates hit the full 72-bucket radius-1 budget.
- Rerank cost is small compared with binary search at these candidate limits.
  The bottleneck is candidate generation/search, not exact reranking.
- 128-bit random-hyperplane signatures are still not enough for high top-10
  recall on this synthetic fixture. Overfetch helps, but even 512 candidates
  reaches only `0.600` flat and `0.568` multi-probe coverage at 100k.
- Multi-probe shows a promising latency-oriented candidate-filter path at 100k.
  For example, 128 candidates gives `0.378` coverage with about
  `4.521 ms/query`, versus exact full scan at about `39.641 ms/query`. That is
  a candidate-filter result, not a final retrieval-quality win.

Caveats:

- Single local run, one seed, synthetic normalized vectors.
- Process peak RSS is whole-process high-water memory, not per-backend memory.
- Multi-probe search depth is still indirectly tied to returned candidate limit
  through `max(minimum_candidate_count, limit * candidate_multiplier)`.
- The exact reranker currently reranks only returned Hamming results; this note
  does not yet measure raw bucket-union recall before Hamming truncation.

Next checks:

- Add an explicit multi-probe probe/candidate budget independent from returned
  output limit.
- Report raw bucket-union recall separately from returned Hamming candidate
  coverage.
- Run bit-count grids such as 128/256/512/1024 at fixed overfetch limits.
- Repeat with real/precomputed embedding fixtures once the synthetic contract is
  stable.

## 2026-07-23 / PR #85 rerank preparation timing

PR #85 adds `timing_ms.rerank_prepare_ms` to separate steady-state query timing
from the one-time preparation needed by the exact candidate reranker. In the
current benchmark this preparation computes document inverse norms used by the
cosine reranker. It is excluded from per-query `exact_rerank_mean_ms` and
end-to-end query latency. The timer starts after `VectorSimilarityComputer`
construction, so it measures document inverse norm preparation rather than full
reranker object initialization.

Provenance:

- git head: `1d10c39993564058c9eb55fbceb3d1417262bee6`;
- environment: local Windows run; CPU and compiler details were not pinned for
  this directional snapshot;
- exact similarity backend in the selected local snapshots: `avx2`;
- flat Hamming backend in the selected local snapshots: `hardware_popcount`;
- multi-probe Hamming backend: not reported as a separate JSON field in this
  benchmark version;
- selected 100k rerun output:
  `tmp\build-pr81-mingw\binary-lifecycle-100k-pr85-rerun.json`.

Additional commands:

```powershell
tmp\build-pr81-mingw\tools\agent-memory-bench\agent-memory-binary-lifecycle-bench.exe `
  tools\agent-memory-bench\binary-lifecycle-10k.manual.json `
  tmp\build-pr81-mingw\binary-lifecycle-10k-pr85.json
tmp\build-pr81-mingw\tools\agent-memory-bench\agent-memory-binary-lifecycle-bench.exe `
  tools\agent-memory-bench\binary-lifecycle-100k.manual.json `
  tmp\build-pr81-mingw\binary-lifecycle-100k-pr85-rerun.json
```

Preparation snapshot:

| Scale | Rerank prepare ms | Exact query mean ms | Flat binary mean ms | Multi-probe mean ms |
|---:|---:|---:|---:|---:|
| 10k | 2.810 | 3.768 | 0.828 | 0.456 |
| 100k | 28.439 | 41.462 | 7.937 | 0.606 |

The first 100k PR #85 local run produced a flat binary query timing outlier
around `79.9 ms/query`, while an immediate rerun returned to the expected
`~7.9 ms/query` range. The table above therefore uses the rerun for 100k. The
new field should be interpreted as a directional preparation-cost sample, not a
statistically stable measurement. The discarded first 100k run reported
`rerank_prepare_ms = 28.774`.

Interpretation update:

- Preparing cosine rerank norms costs roughly `2.8 ms` at 10k and `28.4 ms` at
  100k in this local run.
- At 100k, norm preparation costs about 69% of one exact full-scan query and
  dominates the latency of a first binary or multi-probe query. It is a
  material cold-start cost, although it is amortized across subsequent queries.
- Steady-state query rows remain the right place to compare binary search plus
  exact rerank latency once document norms are already available.
