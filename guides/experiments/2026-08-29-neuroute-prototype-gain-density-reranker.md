# NeuRoute prototype gain-density reranker

Date: 2026-08-29. Final frozen implementation `c4c6f6c`; measurement complete.

## Question

#228 showed that eight deterministic prototypes per occupied 16-bit address
raise DE-1M internal gain@256 from `.6364` to `.8346`, but prototype order still
needs about `.894%` of the corpus to reach 75% actionable gain. This follow-up
asks whether a small learned gain-density reranker can convert the strong
prototype shortlist into a sparse final address schedule.

## Frozen protocol

Every scale and route seed rebuilds the #228 nested `K=8` prototype table.
Training, configuration, and internal evaluation use disjoint German query
partitions of 153, 76, and 76 queries. Internal evaluation remains closed until
training and configuration selection finish.

The model is a deterministic weighted pairwise ridge fit over query-address
features within the top-1024 prototype shortlist. Its features contain the
sorted eight prototype cosines, cosine moments and margins, normalized posting
cost, coarse rank, effective prototype capacity, and fixed interactions. Each
positive address is paired with 32 hard negatives and weighted by its
within-query discounted exact-E5 top-10 gain per posting entry.

Configuration chooses ridge alpha from `.001/.01/.1/1` and shortlist size from
`512/1024` by global gain-density coverage at address budget 256, then candidate
mass. One common global exact-E5 gain-density denominator is used for both
shortlist sizes.

Internal evaluation compares:

```text
prototype_score
posting_cost_heuristic
learned_pairwise_gain_density
privileged_gain_density_teacher on the selected shortlist
privileged_gain_density_teacher on the maximum 1024 shortlist
```

The privileged treatments are diagnostic ceilings and never participate in
selection. All treatments run the same Hamming-768 -> ADC-64 -> exact-E5
cascade at address budgets 128/256/512/1024.

## Result

All DE-1M configuration selections chose a 512-address shortlist. Ridge alphas
were `1`, `.01`, and `1` for the three seeds. The internal budget-256 means are:

| Treatment | Shortlist | Target-address recall | Address AP | Candidate fraction | Static gain | Actionable gain | Exact nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Prototype score | 512 | .8570 | .4489 | .00542 | .8346 | .8213 | .6219 |
| Posting-cost heuristic | 512 | .8570 | .0777 | .00295 | .5072 | .5039 | .4236 |
| Learned pairwise density | 512 | .8570 | .1913 | .00477 | .6010 | .5974 | .4884 |
| Privileged density teacher | 512 | .8570 | 1.0000 | .00301 | .8831 | .8744 | .6355 |
| Privileged density teacher | 1024 | .9054 | 1.0000 | .00226 | .9247 | .9197 | .6494 |

The learned reranker saves some posting mass but loses much more actionable
gain. Relative to prototype order, its per-seed DE-1M actionable gain@256
changes are `-.2477`, `-.1978`, and `-.2263`. Candidate-fraction ratios are
`.868`, `.848`, and `.929`. The simple posting-cost heuristic is even more
destructive, confirming again that cost is useful only after relevance has
been identified reliably.

The maximum-shortlist privileged control is the decisive diagnostic. On the
three DE-1M seeds it reaches actionable gain `.9187/.9193/.9210` at candidate
fractions `.00231/.00220/.00228`. Thus the sparse frontier exists inside the
prototype top-1024 shortlist; the failure is in the learned ordering, not in
the document partition, prototype capacity, or address budget.

## Decision

`learned_reranker_materially_better = false` and
`learned_direct_router_sufficient = false`. Native confirmation and production
selection are not licensed.

`privileged_teacher_direct_router_sufficient = true`, so
`richer_model_or_training_followup_licensed = true`. This licenses a future
nonlinear or listwise model inside the fixed top-1024 prototype shortlist. It
does not license another global occupied-address model or post-hoc tuning of the
current ridge recipe.

```text
result SHA-256:   665aad19510e6da74de1c5b840c2868f9978b85c2b46703475e0d29ee5878988
evidence SHA-256: cd29f1505c62379b54d748b65ab636ca96ac5830133e1bb50fb433fdc6a1f5d3
```

The evidence writer retrained every per-scale/per-seed model, rebuilt every
prototype table, reran all 72 calibration rows and 45 internal rows, reproduced
the complete result byte for byte, and retained the authoritative qrels binding
inherited through #228.

## Limitations and next check

The model is linear in a fixed nonlinear feature basis and uses only 153 German
training queries. Its target is static discounted gain density, while the final
metric is cascade-actionable survival. The exhaustive prototype scorer remains
a diagnostic rather than a native latency implementation.

A justified continuation should keep the 1024-address prototype shortlist and
document partition frozen, increase independent training-query density, and
compare a small nonlinear listwise scorer against a cascade-aware or
marginal-gain teacher. The maximum-shortlist privileged row is the fixed ceiling
that such a model must approach.
