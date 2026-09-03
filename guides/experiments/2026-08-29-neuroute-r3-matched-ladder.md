# NeuRoute matched R0/R3a/R3b/R3c document-summary ladder

## Context

- Date: 2026-08-29
- PR: stacked on the R3 document-summary materialization audit
- Status: full DE-1M measurement and independent model/result replay complete

## Question

Does exposing query-conditioned evidence about the real document distribution
inside each frozen K8/address group recover the privileged sparse ordering that
R0 and projected-prototype R1/R2 could not learn?

## Frozen protocol

The comparison keeps the DE-1M 16-bit partition, K8 prototype construction,
exact top-1024 shortlist, 8,141 pseudo-supervised training queries, static
exact-E5 gain-density teacher, four training epochs, optimizer, three seeds,
address budgets 128/256/512, and Hamming768 -> ADC64 -> exact-E5 cascade
unchanged. Every variant uses the same model seed within a route seed. All 12
models are serialized before configuration qrels are opened; internal qrels
remain closed until configuration replay completes.

The ladder is strictly additive:

- `R0`: the existing 22 scalar K8 cosine/cost/rank features;
- `R3a`: R0 plus the eight local log-counts and eight within-address count
  fractions;
- `R3b`: R3a plus eight exact query dot-products against full-384D local
  residual means;
- `R3c`: R3b plus eight full-384D query-squared diagonal-variance projections,
  eight query dot-products against the deterministic residual directions, and
  the eight local eigenvalues and energies.

No random projection is used. The R3 document summaries are frozen,
query-independent, and teacher-blind. Only the exact scalar interactions with
the query are computed for a shortlist. Hidden widths keep trainable parameter
counts matched:

| Variant | Trainable parameters |
|---|---:|
| R0 scalar | 21,805 |
| R3a occupancy | 21,831 |
| R3b residual mean | 21,763 |
| R3c residual shape | 21,815 |

The maximum/minimum ratio is below `1.004`.

## Decision rule

At budget 256, a direct pass requires every seed to reach actionable gain at
least `.90` and candidate fraction at most `.005`. The alternative progress
gate requires every seed to close at least half of the R0-to-privileged gap
without exceeding `1.05x` R0 candidate mass. An actionable gain of at least
`.87` at the same mass is separately recorded as strong document-distribution
evidence. No outcome licenses a stateful policy, native confirmation, or
production selection in this PR.

## Results

Three-seed internal means at budget 256 are:

| Treatment | Candidate fraction | Static gain | Actionable gain | Exact nDCG@10 |
|---|---:|---:|---:|---:|
| Prototype order | .005415 | .8346 | .8213 | .6219 |
| R0 scalar | .005115 | .8550 | .8368 | .6309 |
| R3a occupancy | .005448 | .8609 | .8419 | .6317 |
| R3b residual mean | .006072 | .8744 | .8546 | .6398 |
| R3c residual shape | .006262 | .8893 | .8686 | .6473 |
| Privileged teacher | .002263 | .9247 | .9197 | .6494 |

The ladder is monotonic and the richer document distribution is clearly useful:
R3c raises actionable gain by `.0319` and exact nDCG by `.0164` over R0. Its
exact nDCG is only `.0021` below the privileged ordering. This is the strongest
learned quality result in the frozen shortlist so far.

It is not yet the required **sparse** result. R3c consumes `.006262` of the
corpus at 256 addresses, about `1.22x` the R0 mean. Per seed it closes `.356`,
`.478`, and `.299` of the R0-to-teacher actionable-gain gap while consuming
`1.23x`, `1.27x`, and `1.18x` R0 candidate mass. R3a/R3b also fail the
every-seed mass and gap-closure requirements. Therefore neither the direct nor
the progress gate passes.

The narrow conclusion is:

> Actual document-distribution evidence removes a substantial quality deficit,
> but this fixed additive summary and matched ListNet scorer do not recover the
> privileged gain-density ordering at the required candidate cost.

This rejects the claim that R0 merely needed occupancy or one simple residual
moment. It does **not** reject full-resolution/teacher-trained address
representations. That branch is now licensed. Stateful scheduling remains
unlicensed because the privileged static density result still nearly exhausts
the sequential oracle.

Result SHA-256 is
`ae4a7d1f4fe30f42b21643a60e4ef2ebf1a5f5855492872c51f2c77d678ee5b9`.
Independent replay regenerated all 12 model archives and reproduced the full
result byte for byte. Evidence SHA-256 is
`7db66e05e94ea37a98ce90a26e30e3fd7ea3cc56eaefcaf0915594848429a1fb`.

## Limitations

- R3c uses diagonal variance and one residual direction, not full covariance or
  document-level cross-attention.
- Roughly 91% of nonempty K8-local groups have fewer than three documents, so
  variance/direction is sparse by construction even though count and residual
  mean remain informative.
- Address budgets are matched but posting mass is an outcome. The current result
  does not include a dense budget sweep that equalizes candidate mass between
  R0 and R3c.
- The objective is static exact-E5 gain density. It may not penalize learned
  mass errors strongly enough even when the representation predicts relevance.
- Exact prototype retrieval and interaction construction are offline research
  operations and carry no latency claim.

## Next check

Test a teacher-trained/full-resolution address representation within the same
frozen top-1024 shortlist. Preserve an explicit mass-aware control or evaluate
at matched candidate fractions so representation quality cannot be bought by
silently opening larger postings. Do not move to a stateful scheduler unless
that stronger static representation also fails.
