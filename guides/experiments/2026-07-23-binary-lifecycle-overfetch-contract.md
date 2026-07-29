# Binary lifecycle overfetch contract groundwork

## 2026-07-23 / PR #83

Question: can the lifecycle benchmark distinguish the exact-neighbour target
from the number of binary candidates returned, so future overfetch/rerank grids
measure the intended metric?

Follow-up: PR #84 adds the first overfetch + exact rerank grid on top of this
contract. See
[`2026-07-23-binary-lifecycle-overfetch-rerank.md`](2026-07-23-binary-lifecycle-overfetch-rerank.md).

Setup:

- command: `agent-memory-binary-lifecycle-bench`
- configs:
  - `tools/agent-memory-bench/binary-lifecycle-10k.manual.json`
  - `tools/agent-memory-bench/binary-lifecycle-100k.manual.json`
- synthetic corpus: normalized vectors generated from `seed`;
- query noise: generated from an independent `query_noise_seed`;
- vector dimension: 64;
- binary encoder: `random_hyperplane_rademacher` v2;
- bit count: 128;
- `oracle_k`: 10;
- `returned_candidate_limit`: 10.

The CTest smoke fixture intentionally uses `oracle_k = 10` and
`returned_candidate_limit = 16`. The manual scale runs above keep `10/10` to
refresh the previous PR #82 numbers, while the smoke gate uses different values
so CI catches accidental reuse of one limit for both exact oracle construction
and binary candidate return.

Contract change:

- `oracle_k` controls the exact top-k set used as the quality target.
- `returned_candidate_limit` controls how many binary Hamming results are
  returned and compared with that fixed exact set.
- `result_limit` is accepted only as a legacy input fallback for old configs.
  New configs should use `oracle_k` and `returned_candidate_limit`.
- `result_limit` remains in the report as a legacy output alias for
  `returned_candidate_limit`; if a config passes both `result_limit` and
  `returned_candidate_limit`, they must match.
- `query_noise_seed` is recorded explicitly so 10k can remain a prefix of 100k
  while query noise stays identical across scale runs.

This PR still does **not** fully decouple multi-probe bucket depth from output
overfetch. In the current `MultiProbeHammingIndex`, the internal candidate
target is still derived from:

```text
max(minimum_candidate_count, returned_candidate_limit * candidate_multiplier)
```

The next quality-grid PR should add an explicit probe/candidate budget if we
want output overfetch and bucket-search depth to vary independently. The
roadmap contract is now named `BinaryBucketSearchBudget` in
[`optimization-roadmap.md`](../optimization-roadmap.md) and must bound bucket
probes, posting visits, decoded bytes, unique candidates and exact-rerank
candidates independently of `returned_candidate_limit`.

Commands:

```powershell
cmake --build tmp\build-pr81-mingw --target agent-memory-binary-lifecycle-bench --parallel 1
tmp\build-pr81-mingw\tools\agent-memory-bench\agent-memory-binary-lifecycle-bench.exe `
  tools\agent-memory-bench\binary-lifecycle-10k.manual.json `
  tmp\build-pr81-mingw\binary-lifecycle-10k-pr83-rerun.json
tmp\build-pr81-mingw\tools\agent-memory-bench\agent-memory-binary-lifecycle-bench.exe `
  tools\agent-memory-bench\binary-lifecycle-100k.manual.json `
  tmp\build-pr81-mingw\binary-lifecycle-100k-pr83.json
```

Actual refreshed single runs:

| Scale | Exact query mean ms | Flat binary query mean ms | Multi-probe query mean ms | Flat returned top-10 overlap | Multi-probe returned top-10 overlap |
|---:|---:|---:|---:|---:|---:|
| 10k | 3.311 | 0.801 | 0.430 | 0.219 | 0.218 |
| 100k | 40.554 | 7.853 | 0.554 | 0.166 | 0.139 |

Lifecycle/build snapshot:

| Scale | Exact build ms | Flat build ms | Multi-probe build ms | Flat rebuild ms | Multi-probe rebuild ms |
|---:|---:|---:|---:|---:|---:|
| 10k | 32.086 | 57.612 | 93.788 | 55.142 | 93.576 |
| 100k | 375.272 | 649.881 | 1,002.576 | 643.611 | 1,001.264 |

Multi-probe diagnostics:

| Scale | Mean candidates | Mean probed buckets | Mean visited postings | Process peak RSS bytes |
|---:|---:|---:|---:|---:|
| 10k | 2,604.375 | 72.000 | 3,077.117 | 29,855,744 |
| 100k | 3,578.629 | 8.000 | 3,665.332 | 231,415,808 |

Interpretation:

- Separating `oracle_k` from `returned_candidate_limit` makes the next
  overfetch experiment well-defined: exact top-10 can remain fixed while binary
  returns 32/64/128/256/512 candidates.
- Independent query noise removes the obvious same-query comparability issue
  from PR #82.
- The 128-bit returned top-10 overlap remains low. This remains a speed/lifecycle
  signal, not a retrieval-quality win.
- Multi-probe query time is already much lower than flat binary at 100k in this
  configuration, but the probe regime changes from 72 buckets at 10k to eight
  buckets at 100k. Do not extrapolate the 100k latency linearly to 1M without a
  separate run.

Next checks:

- Add explicit multi-probe probe/candidate budget independent from
  `returned_candidate_limit`, using `BinaryBucketSearchBudget`.
- Run a bit/candidate grid with fixed `oracle_k = 10` and candidate limits such
  as `32`, `64`, `128`, `256`, and `512`.
- Add exact rerank timing over returned binary candidates.
- Report `candidate_recall@oracle_k`, `reranked_recall@oracle_k`, binary search
  time, exact rerank time, and end-to-end time.
