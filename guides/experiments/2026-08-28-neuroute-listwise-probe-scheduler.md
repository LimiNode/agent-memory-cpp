# NeuRoute listwise 16-bit probe scheduler

Date: 2026-08-28. Frozen protocol; measurement pending.

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
