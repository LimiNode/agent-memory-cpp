# R4 residual coverage oracle (2026-09-13)

This is a membership-only diagnostic over the already executed three-seed
shallow streams and the 8,192-address deep stream.  It does not introduce a
new index or claim that all supported teachers fit a fixed candidate budget.

At exhausted single-seed support, the three seeds cover `.9086`, `.9026`, and
`.9000` of the 1,520 query/teacher pairs; their union covers `.9796`.  The deep
8,192-address route covers `.9888`, and the union of all three shallow routes
with deep support covers `.9961`.  Thus the measured topology contains more
than `.99` teacher support in aggregate, but the previous global-budget replay
still reached only `.9796 @ 50k`: the remaining problem is route fusion and
budget allocation, not simply absence of every relevant address.

Miss sets are only weakly correlated.  At 20k, pairwise Jaccard overlap is
approximately `.201/.254/.220` for seed pairs (2701/2702, 2701/2703,
2702/2703), with 31 query/teacher pairs missed by all three.  Similar values
hold at 5k, 10k, and 50k.  This supports measuring multi-route fusion before
discarding independent R4 views; it does not justify extrapolating linearly to
unmaterialized seeds.

The support result is a necessary-condition oracle, not a production gate:
candidate budgets, posting duplication, physical page traffic, payload rerank,
and latency remain unmeasured in this diagnostic.  See
`2026-09-13-r4-residual-coverage-result.json` and the external raw bundles used
by the source experiments.
