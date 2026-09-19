# THQ4 cutoff-aware ADC training control

Date: 2026-09-19
Branch: `research/thq-score-codecs`

## Hypothesis

The previous ADC fit minimized a query-weighted residual distortion objective,
which improved neither fixed-split convergence nor four-fold OOF qrels. A
bounded alternative is to bias codebook fitting toward the actual retrieval
boundary: for each fitting query, first form the THQ4 top-128 set and then
include the exact FP32 top-32 documents inside that set. This uses teacher scores only
from the 114 in-fold queries; the 38 evaluation queries are untouched.

Each fold fits 128×3D×2-bit codebooks on 4,096 detached residual rows plus
3,648 teacher-ranked rows (114×32), then evaluates only its held-out fold.
The final control uses ten Lloyd iterations and four randomized restarts. A
preliminary single-initialization replay was also run and is not used as the
conclusion because its result changed when the input-row order changed.
The scorer, THQ4 shell and tie ordering are unchanged from the preceding
cross-fit gate. This corrected replay uses the same seeded shuffled fold
assignment as the production-shaped and pairwise gates (`seed=20260919`).

## Result

| fit | OOF nDCG@10 | candidate-FP32 overlap | boundary pairwise |
| --- | ---: | ---: | ---: |
| ordinary 32B/2-bit cross-fit | .653856 | .869079 | .657895 |
| cutoff-aware top-32-in-THQ fit, contiguous folds (10 iterations, 4 restarts) | .654264 | .866447 | .652412 |
| cutoff-aware top-32-in-THQ fit, shuffled folds (10 iterations, 4 restarts) | .645956 | .866447 | .654605 |

The shuffled replay's paired delta to the production-shaped FP32 reference is
`-.008244`, with bootstrap CI95 `[-.022850,+.006192]` and worst-query loss
`-.369070`. The earlier `.654264` result is therefore a contiguous-fold
historical control, not apples-to-apples evidence against the shuffled gate.
Both intervals include zero; this remains a bounded neutral/negative result,
not a claim of a decisive quality separation. The preliminary
single-initialization `.648924` result and earlier global-R4 top32 replay are
retained only as diagnostics.

## Interpretation

This is a bounded negative/neutral result for this particular cutoff-aware
sampling recipe. It does not show that pairwise/listwise learning is
impossible; it shows that simply oversampling teacher top-32 documents inside
the correct THQ4 shell while retaining the same block codebook parameterization
and distortion objective is insufficient on either fold assignment.
The initialization sensitivity is itself a methodological warning: a future
gate must use order-independent initialization or multiple restarts. A genuine
pairwise/listwise gate would need an explicit score-order loss (and careful
normalization/tie handling), not just a changed sample distribution.

Given the negative OOF result and the fixed-capacity controls, no further
32/48/64 B sweep is justified before changing the objective family. Native
materialization remains deferred.

Raw output (shuffled corrected replay): `tmp/thq-adc-cutoff-aware-shuffled.json`,
SHA-256 `2d0ae89b4d40b86e8e651141ea3bd600460ab0d9349f43644bd4ba039018accd`.
Runner SHA-256:
`d79f3fe262f6faca9b0215a1266eb1ccb0aa6c4e24ecd120b579a94da77b80dc`.
Independent source-replay audit SHA-256:
`25c6ba061e5742b95004dcb29308b4f1e2d4cf32829233aed046096e394ad9a2`.
The audit is fail-closed source replay with input SHA bindings
(`source_replay: true`).
