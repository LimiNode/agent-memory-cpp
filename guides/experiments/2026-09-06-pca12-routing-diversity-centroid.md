# PCA12 diversity-aware centroid routing

## Question

Does adding MMR-style diversity to the top bucket-centroid seeds recover
multimodal document regions that a plain centroid ranking misses?

## Protocol

The frozen DE-1M PCA12 partition, postings, exact E5 teacher top-10 labels,
and `R=1` fill policy were kept unchanged.  The runner compared PCA and E5
centroid ranking with MMR seed sets (`seed_count` 16/32, diversity 0.5/0.8)
at 32k, 64k, and 128k candidate budgets.  Final ranking was exact E5 FP32;
reported timings are Python directional measurements.

Runner: `tools/agent-memory-bench/run-diversity-centroid-routing.py`.
Artifact: `tmp/ordinal-lattice-de1m/diversity-centroid-routing-followup.json`.
Artifact SHA-256: `40fa61a509cdad37dde8fec79aa0b921c0e6f1e3d04dcc2fafa9f356ff84e710`.

## Result

At 64k candidates, the best configuration was E5 MMR (`s=32,d=.5`), with
configuration-partition overlap `.6579`, compared with `.6566` for the plain
E5-centroid baseline.  Its nDCG was `.5055` versus `.4974`.  On the internal
partition, the best MMR (`s=16,d=.8`) reached overlap `.6118` and nDCG `.5437`,
while the baseline reached `.6105` and `.5437`.  PCA MMR was effectively
identical to PCA centroid ranking.

## Interpretation

MMR changes the initial seed precision, but the subsequent ranked centroid
stream dominates the fixed candidate budget.  Therefore diversity did not
create a meaningful downstream retrieval improvement.  It is a useful seed
diagnostic, not a promoted router.

## Limitations and follow-up

This is a routing-ceiling study on one frozen split, not a native full cascade.
A future diversity method would need to change the whole budget allocation
(for example, protected per-mode quotas), rather than only prepend seeds.
