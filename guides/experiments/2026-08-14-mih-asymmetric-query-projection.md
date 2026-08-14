# Frozen-document MIH with learned query projection

## 2026-08-14 - pre-execution contract

This experiment tests whether the small shared-W query-aware signal can be
retained without changing document buckets, posting distribution, or the MIH
index. Documents use frozen full-ITQ `W_doc` and fixed median thresholds;
queries alone use learned `W_query`. Training uses train-only qrels and mines
hard negatives from the actual frozen-document MIH candidate union, excluding
all relevant documents. The held-out `16x16-r56 -> K1=768 -> ADC K2=256 -> E5`
matrix is fixed before execution. It compares ordinary ITQ, #137's matched
shared-W treatment, and this asymmetric treatment across five fixed seeds.

The complete numeric result, paired bootstrap, evidence archive, and follow-up
interpretation will be added only after the predeclared matrix completes.

## Result

The corrected serial five-seed replay rejects this **static v1 asymmetric
objective**. Relative to matched ordinary ITQ, frozen-document asymmetric query
routing changes ADC-K2 E5-oracle survival by `-0.013403`, reranked nDCG@10 by
`-0.006110`, candidates by `+582.97/query`, and posting visits by
`+718.87/query`. All five seed-level ADC deltas are negative (`-0.013259`,
`-0.020687`, `-0.013099`, `-0.012700`, `-0.007268`). It is also worse than the
matched shared-W treatment.

The post-hoc held-out diagnostic records mean query-code drift from `W0` to
`W_query` of `25.045/256` bits (`9.783%`), alongside the work and ADC deltas.
It is descriptive only: these co-occurring changes do not establish a causal
correlation. Exact-bucket floor work remains zero under this `r=56` schedule,
so it cannot explain the increased routed union by itself.

The scope is intentionally narrow. False positives were mined only once from
the initial `W0` candidate union, retained in materialized row order rather
than ranked by downstream danger, never re-mined after `W_query` moved, and the
final epoch was used without a train-validation work gate. The result is thus a
no-go for that static initial-MIH-negative Hamming objective, not a refutation
of query-aware learning.

Draft evidence v3 is tied to commit
`ab142a5c7171b06ac8e05bfd4119e7b08b101c59`: archive SHA-256
`7838f7544d322e77943f1998a2f37fa1c31c0f6062d10314ab983da022597f97`,
bundle-root SHA-256
`9009f497b2d66d684b21972f399865f1cd312e7bf7451321ed5ceb96854e24d5`.
It validates the exact #137 contract, canonical matrix source identity, every
reused baseline contribution and W0 anchor payload, and equality of #137 and
#138 calibration-materialization manifests. The matrix itself was executed at
`9b16fba6880eaa2cc675f0a4fba33871c24c87ea`; v3 adds portable provenance
validation and does not reinterpret or rerun that measurement.

The next predeclared asymmetric branch must instead constrain query-code drift,
re-mine current-query false positives by downstream danger, add an explicit
routing/posting-work surrogate, and select only train-validation Pareto-admissible
checkpoints before one held-out replay.
