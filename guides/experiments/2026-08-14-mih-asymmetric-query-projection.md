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
