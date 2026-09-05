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
