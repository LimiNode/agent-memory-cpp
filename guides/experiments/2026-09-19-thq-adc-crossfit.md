# THQ4 ADC four-fold query cross-fitting

Date: 2026-09-19
Branch: `research/thq-score-codecs`

## Question and protocol

The fixed 120/32 query split produced a promising 32 B/2-bit result, while
the convergence/capacity control showed no uniform capacity gain. This gate
tests whether that result survives query cross-fitting. Queries are split into
four contiguous folds of 38. For each fold, the Mahalanobis covariance is fit
on the other 114 queries and scoring is evaluated only on the held-out fold.
The detached 25k document-training sample and the canonical R4-fused shell
are unchanged. All arms use the original five-iteration, single-seed fit so
that this gate tests split leakage rather than another optimization change.

## Out-of-fold result

| arm | OOF nDCG@10 | candidate-FP32 overlap | boundary pairwise |
| --- | ---: | ---: | ---: |
| 32B-2bit | .653856 | .869079 | .657895 |
| 48B-3bit-128x3D | .656748 | .878947 | .676535 |
| 48B-2bit-192x2D | .653199 | .884868 | .666667 |
| 64B-4bit-128x3D | .648266 | .900658 | .690789 |

Per-fold 32B/2-bit nDCG values were `.646414`, `.653135`, `.653727`, and
`.662148`. The fixed-split `.698282` value therefore does not reproduce out
of fold. The OOF 32B/2-bit qrels score is essentially at the candidate-FP32
reference level for this shell (the independent score baseline reports
`.654201` mean candidate-FP32 nDCG), not a confirmed improvement. The 48/64 B
arms show the same pattern as the fixed split: more candidate overlap and
better boundary pairwise accuracy do not translate into better qrels nDCG.

## Interpretation

This is a confirmed negative for the current fixed-geometry, query-weighted
Mahalanobis ADC training recipe as a generalizable quality improvement. The
earlier 32B/2-bit advantage was split-sensitive and must not be used as a
codec-selection claim. Capacity increases to 48 or 64 B do not rescue it under
the same scorer. The result is still bounded: it is one 152-query domain and
one R4 candidate shell, with no held-out-domain replay and no cutoff-aware
objective.

The next algorithmic experiment, if pursued, must change the objective rather
than only increase bits: train a cutoff-aware pairwise/listwise scorer inside
the THQ4 top-128 shell, with all query folds kept out of fitting. A successful
claim requires an out-of-fold paired qrels delta against candidate-FP32 and
direct INT8; teacher fidelity and pairwise metrics alone are insufficient.

Raw output (kept locally): `tmp/thq-adc-crossfit.json`, SHA-256
`5243776bff0193fa43d2c3c95b20d9a1d7e0dd9839dc6fd759bb67e1a9250bad`.
Runner SHA-256:
`dbb20b36e450c5b9147f1a7569adfe90a8bd4ef95a7acadfdc34e4c5e5f232f5`.
