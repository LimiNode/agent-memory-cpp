# NeuRoute address-centroid teacher learnability

Date: 2026-08-29. Frozen implementation `9eddea4`; measurement complete.

## Question

#225 showed that privileged static actionable gain divided by posting cost is
already equivalent to the more expensive sequential oracle. The remaining
problem is therefore static and query-dependent. This diagnostic asks where a
single normalized mean E5 prototype fails when it is used to expose that
teacher for every occupied frozen 16-bit address.

The study deliberately separates three levels of difficulty:

1. ordering the few relevant addresses among themselves;
2. separating them from the 1,024 hardest centroid-ranked negatives;
3. retrieving them globally from the complete occupied-address space.

For every query/address pair the score is cosine similarity divided by
`posting_count ** alpha`, with the complete frozen alpha frontier
`0, .25, .5, .75, 1`. The 76 German configuration queries are used only for
this diagnostic. The separate internal-evaluation partition remains closed.
This extends the earlier 8-bit/25k exact bucket-centroid control from #176; it
does not present centroid routing as a previously untested idea.

## Setup

The exact frozen 16-bit document placements from the width materialization are
used at DE-25k, DE-100k, and DE-1M for all three seeds. Each occupied address
receives one L2-normalized mean of its member E5 document vectors. The teacher
is the same discounted exact-E5 top-10 target and Hamming768 -> ADC64 actionable
coverage used by #225.

The complete matrix contains 45 rows:

```text
3 scales x 3 seeds x 5 cost exponents
76 configuration queries per row
```

## Results

All three configuration selections chose `alpha=0`. Mean values across the
three frozen seeds are:

| Scale | Reach at 75% actionable gain | Candidate fraction | Global AP | Hard-negative AUC | Relevant-only pairwise accuracy | Gain@256 addresses | Gain@1024 addresses |
|---|---:|---:|---:|---:|---:|---:|---:|
| DE-25k | 1.0000 | .001359 | .8133 | .9935 | .7620 | .9992 | 1.0000 |
| DE-100k | .9868 | .004263 | .5607 | .9485 | .6255 | .9554 | .9908 |
| DE-1M | .8202 | .032070 | .1426 | .6497 | .5479 | .6172 | .8076 |

At DE-1M the selected per-seed candidate fractions are `.031181`, `.031958`,
and `.033071`, compared with occupied-logit baselines `.036968`, `.043961`,
and `.036042`. A single centroid therefore reduces work only 8.2%--27.3% and
does not meet the predeclared 1% candidate-fraction usefulness gate.

Positive cost exponents are strongly negative evidence for applying posting
cost to a weak relevance score. At DE-1M, `alpha=.25` reduces global AP to
`.0002` and reaches none of the 75% coverage targets before the 10% mass cap,
even though relevant-only density pairwise accuracy rises to `.8407`. The cost
term can order already-known relevant addresses, but globally promotes cheap
irrelevant postings when relevance is not identified first.

```text
result SHA-256:
707088287b6bb2e84bd956ed15b42a5fdcb147e96e381c6042e9415fc703a3e7

evidence SHA-256:
2773ba8f86576ec9e947fed2c19e9f53bb69c8a159b2c54a0016afeb93ed74a0
```

The evidence writer reproduced the complete result byte for byte and retained
the authoritative qrels replay binding inherited from #225.

## Interpretation

The result localizes the 1M-scale failure to global sparse retrieval. The
single-centroid score is highly informative at 25k and still strong at 100k,
but at 1M it only slightly beats random relevant-address ordering and cannot
separate roughly ten useful addresses from the occupied space precisely enough.
Nevertheless, its top 1,024 addresses retain about 80.8% of discounted target
gain, so the representation is useful as a coarse semantic shortlist.

One mean can erase multimodal structure inside a posting list. The next
predeclared study therefore compares one, two, four, and eight deterministic
per-address prototypes and adds a 128-address point. It isolates prototype
capacity with raw maximum cosine before introducing a learned gain-density
reranker. Production selection remains forbidden.

## Limitations

- This is a configuration-only diagnostic, not held-out confirmation.
- Exact centroid scoring is a representational upper bound, not a native ANN
  latency measurement.
- The teacher has at most ten exact-E5 target documents per query, so AP is
  intentionally measured under extreme class imbalance.
- Positive cost exponents test the literal frozen cosine-division rule; they do
  not reject every calibrated relevance/cost model.
