# NeuRoute LTHQ retrieval-supervised threshold study

Date: 2026-09-05. PR #297.

## Question

Can learned, ordered thermometer thresholds preserve retrieval quality at a
payload matched to ordinary THQ, without learning a global address map?  The
study compares `THQ3/4/5` reconstruction controls with `LTHQ-T3/4/5`, where
only thresholds are learned and coordinates remain the original embedding
coordinates.

## Contract

The runner is
`tools/agent-memory-bench/run-neuroute-lthq.py`; the contract is
`tools/agent-memory-bench/neuroute-lthq.example.json`.  Input must contain an
independent teacher cache with train and evaluation query partitions.  Train
positives are teacher top-32 IDs and negatives are near-cutoff teacher ranks
32--512.  Evaluation labels are never used while fitting thresholds.

Thresholds are kept ordered after every coordinate update.  The objective is a
cutoff-aware pairwise hinge on Hamming distances, optimized by deterministic
coordinate descent over bounded empirical-quantile candidates.  This is an
LTHQ-T experiment; adaptive bit allocation, rotation, and an ordinal semantic
router are deliberately deferred.

## Payloads and decision gate

For a 384-dimensional input the payloads are 96, 144, and 192 bytes for
levels 3, 4, and 5 respectively.  The first actionable comparisons are
`LTHQ3 96 B` versus `THQ4 144 B` and `LTHQ4 144 B` versus `THQ5 192 B`.
Quality is reported with top-10/top-32/top-64/top-256 overlap, nDCG@10,
p05/worst query, query count, payload bytes, model bytes, and scan timing.
A small gain at equal payload is not sufficient to activate a more complex
branch; native confirmation and production selection remain out of scope.

## Reproduction

```text
python tools/agent-memory-bench/plan-neuroute-lthq.py
python tools/agent-memory-bench/run-neuroute-lthq.py --self-test
python tools/agent-memory-bench/run-neuroute-lthq.py --input <teacher-cache.npz> --output <lthq.json>
```

No benchmark numbers are recorded until an independent teacher cache is
materialized.  A result produced from the published evaluation cases as the
training set must be labelled calibration-only and cannot close this study.
