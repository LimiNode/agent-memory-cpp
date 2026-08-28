# NeuRoute width by scale by budget frontier

Date: 2026-08-28. Frozen protocol and completed measurement.

## Question

Can independently trained 14-, 16-, or 18-bit semantic-address heads reduce
the 1M routing candidate mass and native Hamming work relative to 12 bits while
preserving the frozen cascade quality?

This study does not append random or post-hoc bits to the frozen 12-bit model.
Each width trains a complete output head under the same raw-Euclidean mined-pair
recipe, seeds, German 25k corpus, and frozen training-query partition. The
configuration/evaluation queries are forbidden during model and probe-budget
selection.

## Matrix

Widths 12, 14, 16, and 18 are crossed with three frozen seeds. Calibration on
the training-query partition evaluates 64, 128, 256, 512, 1024, 2048, and 4096
probes and selects one shared budget per width. The selection is the smallest
budget satisfying the frozen survival, quality, and 10% candidate gates for all
seeds; a deterministic best-survival fallback is recorded if no budget passes.

Evaluation uses the separate 76-query configuration partition on the nested
German 25k, 100k, and 1M collections. Every route reports the fixed 256-probe
mechanism row and the calibration-selected row; when the budgets coincide one
physical row carries both roles.

## Native measurement

The native MDBX evaluator replays the exact Python candidate, Hamming, ADC, and
final E5 sequences. Eighteen-bit addresses require the declared v2 key layout
with a big-endian 32-bit address field; postings remain packed little-endian
document positions. Timing retains the corrected hardware-popcount Hamming
decomposition from the frozen scale-transfer study.

Only calibration-selected rows may select a width. A width must satisfy the
candidate, ADC-survival, and exact-quality gates at every scale and seed. The
winner is then the passing width with the lowest maximum DE 1M native total
p95. Fixed-256 rows explain the width mechanism and cannot win the production
decision directly.

## Results

All 12 width-specific model artifacts were trained and frozen. Calibration
selected the following shared budgets:

| Width | Probes | Calibration max candidate fraction | Mean/min raw E5 survival | Min exact64 nDCG retention | Selection status |
| ---: | ---: | ---: | ---: | ---: | --- |
| 12 | 256 | .06308 | .8662 / .8412 | .9206 | passed gates |
| 14 | 512 | .03233 | .8290 / .8176 | .8897 | passed gates |
| 16 | 2048 | .03211 | .8501 / .8412 | .9260 | passed gates |
| 18 | 4096 | .01652 | .7671 / .7654 | .8653 | deterministic fallback |

The independent 76-query evaluation did not preserve the calibration quality
for the wider heads. Across all nested scales and seeds:

| Width | Max candidate fraction | Min ADC64 E5 survival | Min exact64 nDCG retention | Frozen quality gate |
| ---: | ---: | ---: | ---: | --- |
| 12 | .06358 | .7316 | .8557 | pass |
| 14 | .03272 | .6684 | .7630 | fail |
| 16 | .03295 | .6526 | .8401 | fail |
| 18 | .01695 | .6145 | .7680 | fail |

Thus 16 bits is close on the aggregate 1M result, but it is not a valid winner:
the protocol requires every scale and seed, and its worst 25k/100k rows miss
the .85 retention gate. Fourteen and eighteen bits fail by larger margins.

The fixed-256 mechanism rows confirm the expected mass reduction at 1M but
also show why equal probe counts are not equal semantic coverage:

| Width | Candidate fraction | Raw E5 survival | ADC64 survival | Exact64 nDCG retention |
| ---: | ---: | ---: | ---: | ---: |
| 12 | .06163 | .8123 | .7425 | .8941 |
| 14 | .01603 | .5965 | .5671 | .7524 |
| 16 | .00428 | .3877 | .3833 | .5796 |
| 18 | .00114 | .2759 | .2750 | .4493 |

## Native 1M frontier

The pinned MinGW build reported hardware popcount and authoritative repository
commits for libmdbx and mdbx-containers. Maximum seed p95 values for the
calibration-selected budgets were:

| Width / probes | Candidates/query | Address | MDBX | Dedup/ceiling | Hamming top-768 | ADC | Exact E5 | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 12 / 256 | 61,626 | .0358 | .8904 | 4.2343 | 2.8371 | .4967 | .0488 | 8.4632 |
| 14 / 512 | 31,505 | .0627 | 1.1208 | 2.3664 | 1.5015 | .4773 | .0466 | 5.4176 |
| 16 / 2048 | 31,456 | .2548 | 4.1139 | 2.7018 | 1.5448 | .4687 | .0467 | 8.9333 |
| 18 / 4096 | 15,829 | .5337 | 7.6148 | 1.8301 | .9683 | .4724 | .0499 | 11.0566 |

Wider addressing does reduce candidate and Hamming work. It does not reduce
total work monotonically because thousands of sparse MDBX probes dominate at
16 and 18 bits. Fourteen bits is the fastest measured row, but it fails quality
and therefore cannot be selected. Under the frozen decision rule, 12 bits at
256 probes remains the only valid choice.

## Evidence

```text
quality result SHA-256:       3f948b7bd0313d926270fe5d3097e914609e85004a7bd404d98d299cb3bd54fb
materialization SHA-256:      1e1e7f83072a8114f48e4018ab5744ccfa8cfe0fa445e19972a936c93c0d25b9
native report SHA-256:        48fd4871ecc9c45b5ffef8735522fb2a68cd1ece4ab61281a7a90b371592e7d3
fail-closed evidence SHA-256: 71d22efe7a7669f4d89f4e2ee9763359654b27cb9b9c0f53f031c9e1a05f24bb
```

The evidence replay reused the frozen model bytes, regenerated the complete
quality result and materialization byte-for-byte, and independently replayed
all 63 native candidate/Hamming/ADC/exact sequences. The final receipt selects
12 bits and 256 probes; fixed-256 mechanism rows were not allowed to override
the calibration-selected decision.
