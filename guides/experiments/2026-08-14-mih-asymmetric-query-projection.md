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

The five-seed fixed matrix rejects this v1 asymmetric objective. Relative to
matched ordinary ITQ, frozen-document asymmetric query routing changes ADC-K2
E5-oracle survival by `-0.013403`, reranked nDCG@10 by `-0.006110`, candidates
by `+582.97/query`, and posting visits by `+718.87/query`. All five seed-level
ADC deltas are negative (`-0.013259`, `-0.020687`, `-0.013099`, `-0.012700`,
`-0.007268`). It is also worse than the matched shared-W treatment.

This is a useful no-go rather than a refutation of query-aware learning:
query-side freedom alone can route into many more frozen document buckets
without moving relevant documents into the desired ones. Train MIH
false-positive mining is therefore not enough under the simple Hamming-radius
loss. A future asymmetric branch needs an explicit candidate/posting work term
or a routing-aware objective, rather than more epochs of this loss.
