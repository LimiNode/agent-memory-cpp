# NeuRoute address-centroid learnability diagnostic

Date: 2026-08-29. Frozen implementation `9eddea4`; measurement complete.

## Question

#225 localized the remaining 16-bit routing problem to static prediction of
actionable gain per posting cost. This diagnostic asks where that prediction
fails and whether a normalized mean E5 centroid per occupied address is a
useful non-learned baseline.

The study keeps the 76 German configuration queries and all three frozen route
seeds at DE-25k, DE-100k, and DE-1M. The disjoint German internal-evaluation
partition remains forbidden. It separates relevant-address ordering,
hard-negative discrimination, global sparse retrieval, and the resulting
actionable candidate-mass frontier.

## Frozen treatments

For every occupied 16-bit address, documents are reduced to one normalized
mean E5 centroid. Addresses are ranked by

```text
cosine(query, address_centroid) / posting_count(address)^alpha
```

for `alpha = 0, .25, .5, .75, 1`. The target is the discounted exact-E5 top-10
survival after the frozen Hamming768 -> ADC64 cascade from #225.

## Result

The complete matrix contains 45 rows (`3 scales x 3 seeds x 5 alpha values`)
and 76 configuration queries per row. Alpha zero was selected for every
scale/seed: posting-cost normalization degrades the already imperfect semantic
signal instead of improving it.

At the primary 75% actionable-gain target:

| Scale | Mean centroid candidate fraction | Mean occupied-logit fraction | Mean AP | Reach rate range |
| --- | ---: | ---: | ---: | ---: |
| DE-25k | .001359 | .039282 | .8133 | 1.000 |
| DE-100k | .004263 | .036522 | .5607 | .974-.1000 |
| DE-1M | .032070 | .038991 | .1426 | .803-.842 |

The decomposition makes the scale failure explicit:

| Scale | Hard-negative AUC | Relevant-only density pairwise accuracy | Discounted gain in top-256 | Discounted gain in top-1024 |
| --- | ---: | ---: | ---: | ---: |
| DE-25k | .9935 | .7620 | .9992 | 1.0000 |
| DE-100k | .9485 | .6255 | .9554 | .9908 |
| DE-1M | .6497 | .5479 | .6172 | .8076 |

Thus one centroid is highly informative at 25k, remains a useful coarse signal
at 100k, and does not satisfy the frozen useful-frontier gate at 1M. On DE-1M,
relevant-only ordering is only slightly above chance and global AP is
`.1326-.1501`, but top-1024 centroid-ranked addresses still preserve
`.7961-.8282` of discounted target gain. The semantic signal is therefore
concentrated but insufficient as the final sparse scheduler.

## Decision

`single_centroid_useful = false`. The internal-evaluation partition was not
opened and production selection remains forbidden. The predeclared
multi-prototype follow-up is licensed to distinguish within-address
multimodality from a more general failure of centroid geometry.

```text
result SHA-256:   707088287b6bb2e84bd956ed15b42a5fdcb147e96e381c6042e9415fc703a3e7
evidence SHA-256: 2773ba8f86576ec9e947fed2c19e9f53bb69c8a159b2c54a0016afeb93ed74a0
```

The evidence writer rebuilt every centroid table, reran all rows, reproduced
the complete result byte for byte, and retained the authoritative qrels
binding inherited from #225.

## Limitations and next check

The result is a configuration-only diagnostic, not held-out production
evidence. A mean centroid compresses every occupied posting list to one mode.
The next frozen study must compare deterministic `1/2/4/8` prototypes per
address at fixed global address budgets and retain single-centroid,
occupied-logit, and privileged gain-density controls. A learned gain-density
reranker is justified only after that study shows a useful coarse shortlist.
