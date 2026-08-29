# NeuRoute address-centroid teacher learnability

Date: 2026-08-29. Frozen implementation `9eddea4`; measurement complete.

## Question

#225 showed that privileged static actionable gain divided by posting count is
almost identical to the more expensive sequential oracle. The remaining
problem is therefore static: predict useful occupied postings for a query.
This diagnostic asks where that prediction fails when each frozen 16-bit
address is represented by its normalized mean E5 document centroid.

The study separates three difficulties instead of training another generic
scheduler:

1. ranking relevant addresses among themselves;
2. separating relevant addresses from hard semantic negatives;
3. retrieving the sparse relevant addresses from the complete occupied space.

The earlier #176 control scanned mean centroids for an 8-bit replicated Spanish
25k substrate. This experiment is different: it binds the frozen 16-bit,
single-assignment German partitions from #225, covers 25k/100k/1M, includes
posting-cost exponents, and measures the actionable Hamming768 -> ADC64
candidate-mass frontier.

## Frozen protocol

The 76 configuration-selection queries are the only query partition opened.
The separate German internal-evaluation partition remains forbidden. For every
scale and each of the three frozen routing seeds, the runner constructs one
normalized mean E5 centroid per occupied address and evaluates

```text
cosine(query, centroid) / posting_count^alpha
alpha in {0, .25, .5, .75, 1}
```

The complete matrix contains 45 rows and 76 queries per row. It reports global
average precision, pairwise AUC against the top 1,024 hard negatives,
relevant-only gain-density pairwise accuracy, discounted target-gain coverage
at 256/512/1,024 addresses, and candidate mass required for 50/75/90/95%
actionable gain after the frozen cascade.

## Results

All nine configuration selections chose `alpha=0`; cost normalization did not
repair a weak relevance score. Means below are across the three frozen seeds.

| Scale | Global AP | Hard-negative AUC | Relevant-only pairwise | Gain at 256 | Gain at 1,024 | Candidate mass at 75% | `occupied_logit` mass |
|---|---:|---:|---:|---:|---:|---:|---:|
| DE-25k | .8133 | .9935 | .7620 | .9992 | 1.0000 | .00136 | .03928 |
| DE-100k | .5607 | .9485 | .6255 | .9554 | .9908 | .00426 | .03652 |
| DE-1M | .1426 | .6497 | .5479 | .6172 | .8076 | .03207 | .03899 |

At DE-1M the 75% target reach rate is only .8026--.8421 under the frozen 10%
candidate-mass censoring limit, so the predeclared `<=1%` useful-single-centroid
gate fails. The mean candidate reduction against `occupied_logit` is only about
17%, despite a much stronger result at the smaller scales.

The decomposition localizes the failure. Relevant-only ordering at 1M is only
slightly above random, global AP is .133--.150, and the hard-negative AUC falls
to .634--.669. Nevertheless, exact centroid ranking still concentrates about
80.8% of discounted target gain into the top 1,024 addresses. The prototype is
therefore informative as a coarse shortlist generator, but is not a sufficient
final sparse router.

## Decision and interpretation

`single_centroid_useful=false`. The result supports the scale-dependent
multimodality hypothesis: averaging a larger posting list into one vector loses
the semantic islands that remain separable at 25k and partly separable at 100k.
It does not show that address semantics are unpredictable.

The independently predeclared multi-prototype follow-up is licensed. It will
compare 1/2/4/8 deterministic prototypes per occupied address and report both
quality saturation and storage/score work. A learned gain-density reranker and
native ANN activation remain downstream questions; neither is licensed for a
production claim by this configuration-only diagnostic.

## Evidence

```text
result SHA-256:   707088287b6bb2e84bd956ed15b42a5fdcb147e96e381c6042e9415fc703a3e7
evidence SHA-256: 2773ba8f86576ec9e947fed2c19e9f53bb69c8a159b2c54a0016afeb93ed74a0
```

The evidence writer rebuilt every centroid table, recomputed all exact-E5
targets and cascades from authoritative roots, and reproduced the complete
result byte for byte. Authoritative qrels replay passed. The internal partition
was not opened, and production selection remains forbidden.

## Limitations

- These are exact NumPy centroid scans, not native latency measurements.
- The cost exponent is a configuration frontier, not a held-out selection.
- One mean prototype deliberately compresses all within-address modes.
- The 1M result licenses a representation diagnostic, not immediate ANN or
  learned-scheduler deployment.
