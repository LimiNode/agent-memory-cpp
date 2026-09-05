# Learned ordinal lattice router

Date: 2026-09-05. Follow-up to PR #298.

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
contain the 1M-vector source reference and a 26 MiB raw report:

```text
E:/_repoz/agent-memory-cpp/tmp/ordinal-lattice-de1m/manifest.json
E:/_repoz/agent-memory-cpp/tmp/ordinal-lattice-de1m/result.json
```

Manifest SHA-256: `b5a7b800e4ea0f3672730849c6bf53116bd1f1eb8330cee8c8bc893c966f3af5`.
Result SHA-256: `3de60c6cb3bc011007783b31169907fff897a0d476133922ad4e24ca2e1b8e38`.

The full matrix completed with 60,800 query rows and 800 summaries. At
`P=256`, `K=256`, `R=1`, the combined 152-query routing ceiling was:

| Router | Top-10 overlap | qrels nDCG@10 | Unique candidates | Route p95 ms |
|---|---:|---:|---:|---:|
| Binary12 | 0.586 | 0.497 | 62,436 | 103.1 |
| Ordinal 8×3 | 0.372 | 0.347 | 38,177 | 65.2 |
| Ordinal 6×4 | 0.391 | 0.368 | 61,211 | 91.4 |
| Ordinal 4×8 | 0.330 | 0.321 | 62,958 | 100.9 |
| Ordinal 10×3 | 0.151 | 0.171 | 4,693 | 31.7 |

Replication improved Binary12 at the cost of a large posting expansion (for
example, `R=4`, `P=256` reached about 0.774 overlap and 143k unique candidates),
but no ordinal layout overtook the Binary12 control. The 10×3 layout is the
clearest negative result: its 59,049 nominal states are too sparse for the
current PCA geometry and lose most teacher neighbours.

## Interpretation

The lattice idea is geometrically coherent, but this first complete replay does
not support replacing the existing binary router. The failure is not caused by
the base-L packing choice: fixed-width and packed storage have identical
routing quality. The likely bottleneck is the unsupervised PCA projection and
the global quantile thresholds, not ordinal probing itself. A follow-up with
retrieval-supervised projection learning is justified only as a separate,
explicit experiment; it must beat Binary12 at matched candidate count and
payload before any downstream THQ/INT4 cascade is considered.
