# NeuRoute listwise 16-bit probe scheduler

Date: 2026-08-28. Frozen protocol and completed measurement.

## Question

Can a query-side scheduler realize the strong 16-bit document-partition oracle
without changing document addresses or repeating documents across postings?

#216 showed that the mean 90%-coverage oracle candidate mass at DE-1M is only
about `.000155`, while the current scheduler consumes roughly `.0456`. #217
then removed empty-address traversal but did not improve semantic ordering at a
fixed probe budget. This protocol isolates the remaining ordering error.

## Frozen surface

All three 16-bit raw-Euclidean routes, their document encoders, thresholds,
addresses, posting lists, Hamming codes, and ADC payloads come byte-for-byte
from #203. Only a query-side address-scoring head may be learned. The new #220
authoritative receipt for #217 is an activation prerequisite.

The 153 German training queries are the only queries used for fitting and
budget/penalty selection. The 76 configuration queries remain held out until
the complete scheduler is frozen.

## Treatments

```text
A occupied_logit
B listwise_gain
C listwise_gain_cost
D cascade_aware
```

`occupied_logit` is the successful non-learned #217 baseline. The learned
treatments score complete occupied addresses in a joint feature basis containing
16 singleton signs and all 120 pairwise sign products. This is deliberately not
another independent-bit regression.

For each training query, a deterministic address-utility ridge fit projects the
whole exact-E5 top-100 discounted gain vector into that joint basis. A second
anchored ridge maps the frozen 64-dimensional query hidden state to those joint
utility coefficients. Positive teacher addresses receive a frozen 256x weight
so the sparse top-100 signal is not erased by the many occupied negative
addresses. The cost treatment reuses the listwise semantic head; it does not
train a second copy.

The cost treatment subtracts a calibration-only multiple of standardized
`log1p(posting_count)` after semantic scoring. It never divides semantic gain by
posting mass. The cascade-aware treatment gives utility only to exact-E5
teacher documents that survive a global Hamming-768 then ADC-64 cascade, making
its target an actionable final-pool ceiling rather than raw address similarity.

## Evaluation

Calibration freezes one probe budget per seed/treatment and one cost penalty for
the cost treatment. Held-out evaluation runs the complete candidate,
Hamming-768, ADC-64, and exact-10 cascade at DE-25k, DE-100k, and DE-1M.

Every row records the exact minimum posting mass needed to cover 90% of the
query's exact-E5 top-10 and reports:

```text
measured candidate fraction
- oracle minimum candidate fraction
= oracle mass regret
```

A learned scheduler licenses native confirmation only if every scale and seed
passes the inherited quality gates and DE-1M either reduces candidate work by
at least 25% or halves oracle mass regret relative to `occupied_logit`. No
production selection is permitted in this PR.

## Result

The frozen implementation is commit `2b51a80`. The complete matrix contains
six learned heads, 108 calibration rows, and 36 held-out rows (three scales x
three seeds x four treatments). The local run completed in about 107 seconds;
this duration is operational context, not a stable timing claim.

The byte-replayable artifacts are:

```text
tmp/neuroute-listwise-probe-scheduler/result.json
SHA-256 79c9e261e5a2037a41a4f5e3ff846839f3622b36053d0f26a9fb2717230cb9ea

tmp/neuroute-listwise-probe-scheduler/evidence.json
SHA-256 db677e801cb4240a26d4070afa525b6a370a346371f83be5de18d5e40df8aaab
```

The evidence writer reproduced the result and all six head files byte for byte
and revalidated the authoritative qrels roots before and after the full quality
replay.

DE-1M held-out means across the three frozen seeds are:

| Treatment | Candidate fraction | Raw top-10 survival | ADC-64 survival | Exact nDCG@10 | Retention vs full E5 | Oracle-mass regret |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `occupied_logit` | .031613 | .7390 | .6882 | .5757 | .8897 | .031441 |
| `listwise_gain` | .033063 | .7478 | .6952 | .5537 | .8558 | .032891 |
| `listwise_gain_cost` | .042468 | .7930 | .7342 | .5682 | .8781 | .042295 |
| `cascade_aware` | .032483 | .7509 | .7000 | .5573 | .8614 | .032310 |

The apparent survival gains do not produce a better final frontier. Relative
DE-1M candidate-work changes for the three seeds are:

| Treatment | Seed 2026082701 | Seed 2026082702 | Seed 2026082703 |
| --- | ---: | ---: | ---: |
| `listwise_gain` | -4.19% | -5.77% | -3.81% |
| `listwise_gain_cost` | -61.35% | -61.58% | +19.88% |
| `cascade_aware` | -2.74% | -3.91% | -1.61% |

Positive values mean less candidate work than `occupied_logit`; negative values
mean more. The corresponding oracle-regret changes have the same direction and
nearly the same magnitude. None reaches the predeclared 25% candidate reduction
or 50% regret reduction on all seeds.

Only `occupied_logit` passes every inherited quality gate. The learned
treatments retain adequate ADC survival, but their worst held-out DE-1M exact
retentions are `.8384`, `.8341`, and `.8457` for `listwise_gain`,
`listwise_gain_cost`, and `cascade_aware`, below the frozen `.85` floor.
Therefore:

```text
native_confirmation_licensed = false
production_selection_licensed = false
```

## Interpretation

The experiment rejects this particular query-side recipe. Projecting sparse
address gain into the 136-dimensional singleton-plus-pairwise basis and then
fitting an anchored linear query head does not generalize the strong 16-bit
partition oracle to held-out queries. The nondestructive log-mass penalty is
also seed-unstable: it saves work for one seed but spends substantially more for
the other two. Cascade-aware labels improve mean ADC survival slightly, but do
not recover final exact ranking quality or candidate efficiency.

This does not invalidate the 16-bit document partition or the oracle result from
#216. It closes the frozen quadratic-feature/two-stage-ridge scheduler, not all
listwise or sequential schedulers. A future continuation would need a genuinely
nonlinear or sequential query model, more independent training queries, and an
objective that preserves final ranking quality while learning marginal address
gain. No native benchmark or production activation should follow from this PR.
