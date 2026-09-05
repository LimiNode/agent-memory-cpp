# Learned ordinal lattice router

Date: 2026-09-05. Follow-up to PR #298; corrected historical learned-router
replay added after review of PRs #176 and #199.

## Hypothesis

Replacing twelve independent binary address bits with a small number of
learned ordinal axes may preserve the same nominal bucket capacity while
giving multi-probe and replication a meaningful lattice geometry. The first
matrix is Binary12, 8×3, 6×4, 4×8, and 10×3 ordinal axes/levels.

## Method

The runner is `tools/agent-memory-bench/run-neuroute-ordinal-lattice.py` and
its contract is `neuroute-ordinal-lattice.example.json`. PCA projections and
ordered thresholds are fitted on train vectors only. Documents are assigned to
mixed-radix cells. Query probing uses a priority queue over neighbouring cells,
with edge cost derived from the distance to the crossed threshold. Replication
places a document in the closest boundary-adjacent cells.

Both fixed-width thermometer storage and packed base-L cell IDs are reported.
For example, 6×4 has 4096 states, 18 fixed-width bits, but only 12 packed
base-4 bits. These are distinct storage contracts and must not be conflated.

## Evaluation

The evaluator reports routing-ceiling exact top-10 overlap and nDCG@10,
p05/worst query, raw posting visits, unique candidates, p95 route time,
payload bytes, and model bytes, separately for config and internal queries.
The first pass is `R=1`; `R=2/3/4` is then evaluated using the same learned
coordinates. Global exhaustive scans are diagnostic only and are not a
product claim.

```text
python tools/agent-memory-bench/plan-neuroute-ordinal-lattice.py
python tools/agent-memory-bench/run-neuroute-ordinal-lattice.py --self-test
python tools/agent-memory-bench/run-neuroute-ordinal-lattice.py --input <teacher-cache.npz> --output <ordinal-lattice.json>
```

This is a PCA/threshold baseline, not yet a retrieval-supervised projection
optimizer. That distinction is intentional: it isolates the value of ordinal
lattice geometry before adding a second learning objective.

## DE-1M replay

The teacher cache was materialized from the frozen DE-1M E5 vectors and the
existing 76-query configuration plus 76-query internal protocols. 153 other
queries were reserved as train provenance. The exact teacher is global E5
inner-product top-10; qrels nDCG is additionally calculated from the frozen
DE qrels. The cache manifest and result are kept outside Git because they
contain the 1M-vector source reference and raw reports:

```text
E:/_repoz/agent-memory-cpp/tmp/ordinal-lattice-de1m/manifest.json
E:/_repoz/agent-memory-cpp/tmp/ordinal-lattice-de1m/result.json
```

The full PCA/ordinal matrix completed with 60,800 query rows and 800 summaries.
At `P=256`, `K=256`, `R=1`, the combined 152-query routing ceiling was:

| Router | Top-10 overlap | qrels nDCG@10 | Unique candidates | Route p95 ms |
|---|---:|---:|---:|---:|
| PCA Binary12 | 0.586 | 0.497 | 62,436 | 103.1 |
| PCA Ordinal 8×3 | 0.372 | 0.347 | 38,177 | 65.2 |
| PCA Ordinal 6×4 | 0.391 | 0.368 | 61,211 | 91.4 |
| PCA Ordinal 4×8 | 0.330 | 0.321 | 62,958 | 100.9 |
| PCA Ordinal 10×3 | 0.151 | 0.171 | 4,693 | 31.7 |

Replication improved the PCA Binary12 control at the cost of a large posting
expansion (for example, `R=4`, `P=256` reached about 0.774 overlap and 143k
unique candidates), but no ordinal layout overtook that PCA control. The 10×3
layout is the clearest negative result: its 59,049 nominal states are too
sparse for the current PCA geometry and lose most teacher neighbours.

## Corrected historical learned Binary12 replay

The review identified that the `Binary12` row above was not the learned router
from PR #176: it was a PCA projection with median/quantile splits and plain
confidence probing. The corrected runner is
`tools/agent-memory-bench/run-neuroute-historical-learned-router.py`. It uses
the historical `384 → 128 → 16` MLP, BCE-with-logits targets formed from exact
E5 top-10 document-address bits, fixed-final-epoch training, and confidence-
ranked subset-flip probing. The document head follows #176's deterministic
`documents[::4]` SVD sample and median thresholds. PR #199 remains the scale-
transfer reference for the frozen `12-bit/256-probe` operating point; the
original #176 checkpoint is not available locally, so this is a deterministic
retraining, not a byte-identical checkpoint replay.

Command and artifact (raw JSON is intentionally kept outside Git):

```text
python tools/agent-memory-bench/run-neuroute-historical-learned-router.py \
  --cache E:/_repoz/agent-memory-cpp/tmp/ordinal-lattice-de1m/manifest.json \
  --output E:/_repoz/agent-memory-cpp/tmp/ordinal-lattice-de1m/historical-learned-result.json \
  --probes 32,64,128,256 --replication 1,2,4 --document-budget 1024
```

Manifest SHA-256: `25f10151c60461edbfd0ac52e66caf5777834c80ba4637a8986e03de14f352ae`.
Corrected result SHA-256: `106437a08abdb1aaa3d9531714ae6f00245f185ec08dc64fb04093c359d36502`.

At `P=256` and `K=1024`, the combined config+internal routing ceiling was:

| Replication | Top-10 overlap | qrels nDCG@10 | Unique candidates | Route p95 (ms) |
|---:|---:|---:|---:|---:|
| 1 | 0.581 | 0.451 | 66,872 | 82.4 |
| 2 | 0.711 | 0.542 | 101,593 | 121.6 |
| 4 | 0.793 | 0.585 | 147,045 | 181.4 |

For direct comparison with the original #299 `K=256` table, the same replay
was also run with `--document-budget 256` (artifact
`historical-learned-result-k256.json`, SHA-256
`6b8b613ec87302682abb59e8aa92189ff502abd5db2fcac9b1c57944c5eab55a`). The
reported overlap and qrels nDCG values at `P=256` are unchanged: the extra
candidate budget does not alter the selected top-10 on this split.

The per-partition values are retained in the raw report; p05 overlap remains
0.1–0.4 and worst-case overlap is zero for several rows. These numbers are
consistent with the historical direction (replication buys recall by paying
posting expansion), but they do not reproduce PR #199's serving claim on a
different corpus/protocol. They are routing-ceiling measurements only: no
ITQ/ADC, INT, local K8, or full R4 cascade is applied after candidate union.
The reported route p95 includes address probing, posting union, and the exact
inner-product selection of the bounded document budget; it is not an isolated
MLP inference benchmark.

## Interpretation

The lattice idea is geometrically coherent, but the original PCA/quantile
replay does not support replacing a supervised binary router. Its negative
result is still valid for that unsupervised construction: fixed-width and
packed storage have identical routing quality, and the bottleneck is the PCA
projection/threshold geometry rather than packing. The corrected supervised
replay is a separate baseline and should be the comparison point for future
ordinal/THQ routers. On this DE-1M retraining it improves with replication but
does not reach a near-lossless routing ceiling at the 60–100k candidate range;
therefore no downstream cascade or product claim should be based on the old
PCA `Binary12` label. A true byte-identical #176/#199 reproduction would still
require the unavailable frozen checkpoint and its original serving fixture.
