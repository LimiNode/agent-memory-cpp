# MIH-aware ITQ geometry diagnosis

## 2026-08-13 — pre-execution contract

### Question

Which part of the first MIH-aware ITQ result changes the calibration geometry:
split-specific ITQ initialization, or the subsequent document-only refinement
path? This diagnostic is calibration-only: it never accepts an evaluation root,
query vectors, qrels, or any held-out metric.

### Frozen controls

For each seed 52–56, four code paths are measured over the frozen 25,000
calibration documents: full-25k production ITQ; trainer-compatible ITQ fit on
the stable 80% split without SGD; the #132 split-initialized zero-work artifact;
and the corresponding 0.10-work artifact. The artifacts are provenance-linked
to #132’s fixed matrix and are not retrained or tuned here.

For every code, the diagnostic records per-bit occupancy and entropy; per-band
bucket entropy, occupied buckets, posting-size mean/p95/max, exact and
radius-one collision; intraband correlation; random-document and document-only
E5-neighbour Hamming distributions; and radius-zero/radius-one union plus
posting work for every calibration document as a pseudoquery. E5 neighbours
use 1,024 deterministic calibration anchors and exact top-10 calibration
neighbours.

The archive retains per-row raw NPZ contributions: radius-zero and radius-one
candidate/posting vectors for all 25,000 pseudoqueries, random-pair and
neighbour Hamming distances, pseudoquery IDs, pair indices, anchor indices,
and neighbour indices. This makes the three same-seed transitions replayable
without rerunning the expensive union traversal.

### Limitations

Each pseudoquery is itself in the calibration index, so its exact and
radius-one work includes one self-hit. That is not an estimate of an external
query's absolute work. It is common to all four same-document variants and is
negligible relative to the observed multi-thousand-document unions, making it
acceptable for this paired geometry decomposition.

### Decision use

This is a causal decomposition diagnostic, not another held-out winner
selection. It determines whether the next algorithm must first preserve
full-25k ITQ initialization, or whether an anchored Hamming-target objective
has a distinct geometry problem to solve. Any later held-out frontier remains a
new predeclared experiment.
