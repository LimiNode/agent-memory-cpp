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
The final control uses ten Lloyd iterations and four randomized restarts. A
preliminary single-initialization replay was also run and is not used as the
conclusion because its result changed when the input-row order changed.
The scorer, THQ4 shell, tie ordering, and four-fold split are unchanged from
the preceding cross-fit gate.

## Result

| fit | OOF nDCG@10 | candidate-FP32 overlap | boundary pairwise |
| --- | ---: | ---: | ---: |
| ordinary 32B/2-bit cross-fit | .653856 | .869079 | .657895 |
| cutoff-aware top-32 fit (10 iterations, 4 restarts) | .654191 | .863816 | .650219 |

The final cutoff-aware per-fold nDCG values are recorded in the raw receipt;
the paired delta to candidate-FP32 is `-0.000009`, with bootstrap CI95
`[-.013711,+.014596]` and worst-query loss `-.369070`. Thus the teacher-ranked
sample plus more serious fitting is statistically indistinguishable from the
candidate-FP32 reference and does not improve the boundary metric. The
preliminary single-initialization `.648924` result is retained only as an
initialization-sensitivity diagnostic, not pooled with the final control.

## Interpretation

This is a bounded neutral/negative for this particular cutoff-aware sampling
recipe. It does not show that pairwise/listwise learning is impossible; it
shows that simply oversampling teacher top-32 documents while retaining the
same block codebook parameterization and distortion objective is insufficient.
The initialization sensitivity is itself a methodological warning: a future
gate must use order-independent initialization or multiple restarts. A genuine
pairwise/listwise gate would need an explicit score-order loss (and careful
normalization/tie handling), not just a changed sample distribution.

Given the negative OOF result and the fixed-capacity controls, no further
32/48/64 B sweep is justified before changing the objective family. Native
materialization remains deferred.

Raw output (kept locally): `tmp/thq-adc-cutoff-aware.json`, SHA-256
`dbbf3d0d784a672bff9c3171d3f581a9db3ce211f013684f77f54b4d9167bb50`.
Runner SHA-256:
`db1f2b4047ac69f6f5ff037fa7161d13171692962e6df800065313ea4bb62234`.
