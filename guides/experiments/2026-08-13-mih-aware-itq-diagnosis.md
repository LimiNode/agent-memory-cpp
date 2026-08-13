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

## 2026-08-13 — calibration result

### Actual result

The full five-seed matrix completed with 20 raw contribution files. The first
transition is essentially neutral: fitting ITQ on the stable 80% split rather
than all 25,000 calibration documents changes radius-one union work by only
`+4.51` candidates and `+8.61` posting visits/query in the five-seed mean.

| Transition | Delta mean radius-one candidates | Delta mean posting visits | Delta E5-neighbour Hamming distance |
| --- | ---: | ---: | ---: |
| full-25k ITQ → split-80% ITQ | +4.51 | +8.61 | +0.12 |
| split-80% ITQ → zero-work refinement | +6,535.13 | +46,224.07 | -14.56 |
| zero-work → 0.10-work refinement | -10.37 | -189.68 | +0.04 |

The resulting mean radius-one work is `16,867.31` candidates / `29,270.61`
posting visits for full ITQ, `16,871.82` / `29,279.22` for split-only ITQ,
`23,406.95` / `75,503.29` for zero-work refinement, and `23,396.59` /
`75,313.62` for 0.10-work refinement.

The geometry identifies the mechanism more clearly than the original frontier:
the mean per-bit entropy is effectively `1.0000` for full ITQ and `0.99999`
for split-only ITQ, but falls to `0.87181` after zero-work refinement. The
0.10 term changes this only to `0.87221`. Thus the refinement path makes
calibration E5-neighbours much closer in Hamming space, but does so by making
the global code distribution materially less balanced; the larger and more
correlated postings dominate local-radius-one work.

### Interpretation

The #132 confound is now resolved for this protocol: split-specific ITQ
initialization is not the source of the regression. The damaging transition is
the document-pair semantic/quantization refinement path itself. Raising its
v1 MIH-work coefficient to `0.10` remains far too weak to counter that change.

This closes the first v1 refinement formulation without ruling out MIH-aware
learning. A next algorithm must retain full-25k ITQ as an explicit anchor and
use a Hamming-equivalent, work-aware objective with a held-out frontier gate;
it should not merely retune the tested v1 coefficient.

### Evidence

The replay-validated archive is `mih-aware-itq-diagnosis-evidence-v1.zip`:
SHA-256 `ea5eae59d547bd327f4f4f56a90b4fa411b8619ef56f50fe3a14d78149cb3902`,
internal bundle-root SHA-256
`611c8d9ff1ca8753b98be739ad8dfa30b9ab24a775502ae7b2e9aef69ec30298`.
It contains the 20 raw paired contribution NPZ files, the exact consumed #132
artifacts and matrix manifest, the frozen contract, and source snapshots. It
will be staged as a draft evidence release on the final PR commit.
