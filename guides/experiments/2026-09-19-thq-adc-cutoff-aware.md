# THQ4 cutoff-aware ADC training control

Date: 2026-09-19
Branch: `research/thq-score-codecs`

## Hypothesis

The previous ADC fit minimized a query-weighted residual distortion objective,
which improved neither fixed-split convergence nor four-fold OOF qrels. A
bounded alternative is to bias codebook fitting toward the actual retrieval
boundary: for each fitting query, include its exact FP32 top-32 documents from
the THQ4 candidate shell in the training sample. This uses teacher scores only
from the 114 in-fold queries; the 38 evaluation queries are untouched.

Each fold fits 128×3D×2-bit codebooks on 4,096 detached residual rows plus
3,648 teacher-ranked rows (114×32), then evaluates only its held-out fold.
The scorer, THQ4 shell, tie ordering, and four-fold split are unchanged from
the preceding cross-fit gate.

## Result

| fit | OOF nDCG@10 | candidate-FP32 overlap | boundary pairwise |
| --- | ---: | ---: | ---: |
| ordinary 32B/2-bit cross-fit | .653856 | .869079 | .657895 |
| cutoff-aware top-32 fit | .648924 | .870395 | .650219 |

Per-fold cutoff-aware nDCG values were `.643496`, `.649879`, `.658490`, and
`.643831`. Thus the teacher-ranked sample reduced OOF qrels by `.004932` and
did not improve the boundary metric. The small overlap increase is not a
retrieval win.

## Interpretation

This is a bounded negative for this particular cutoff-aware sampling recipe.
It does not show that pairwise/listwise learning is impossible; it shows that
simply oversampling teacher top-32 documents while retaining the same block
codebook parameterization and distortion objective is insufficient. A genuine
pairwise/listwise gate would need an explicit score-order loss (and careful
normalization/tie handling), not just a changed sample distribution.

Given the negative OOF result and the fixed-capacity controls, no further
32/48/64 B sweep is justified before changing the objective family. Native
materialization remains deferred.

Raw output (kept locally): `tmp/thq-adc-cutoff-aware.json`, SHA-256
`8850fb3c7031625720d22eec8a18f74541a8a29a3d37e666aa4d83eab01fb92d`.
Runner SHA-256:
`b61101828f3729afdba521d9bbd5e87bbe1c09746439a6d9169f51cdb25b7ae7`.
