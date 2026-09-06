# Anchor-routing synthesis and next gates

Date: 2026-09-08. This note consolidates the directional THQ findings and
freezes the corrected interpretation before the next experiment.

## Established results

* Additive per-coordinate transition cost is not a useful retrieval ordering:
  teacher/prototype/four-anchor variants retained only `.025/.038/.116` of
  THQ top-256 at a 10k budget.
* Continuous shared-alpha segment geometry is strong as an oracle: one
  teacher anchor retained `.9816` of teacher top-10 at K=10k; spherical arc
  geometry was effectively tied at `.9803`.
* The anchor is not automatically available from a cheap IVF route. Teacher
  top-1 prototype inclusion was `.349/.822/.901/.941/.980` for top
  `1/8/16/32/64` IVF cells.
* Selecting the nearest E5 prototype inside each IVF pool reached
  `.920/.972/.976/.980` at K=10k for M=`1/16/32/64`, but the corresponding
  pools contained roughly `0.5k/9.7k/20.3k/40.8k` FP32 prototypes.
* The corrected privileged linear segment budget curve is
  `.902/.929/.953/.968/.978/.982` at K=`256/512/1024/2048/5000/10000`.

These are prototype-routing ceilings, not final document qrels or serving
latencies. In particular, global nearest-prototype selection is an exhaustive
oracle control, and `teacher top-1 recall` is only a strict identity proxy.

## Frozen research order

1. Characterize IVF pools and anchor availability.
2. Measure a best-anchor-in-pool ceiling separately from practical selectors.
3. Compare nearest-E5, centroid-plus-local score and confidence/rank-aware
   selectors at M=`1/2/4/8/16/32/64`.
4. Sweep candidate budgets `5k/10k/15k/20k/40k` and report mean, median, p05,
   worst query and full-10/10 fraction.
5. Only if the runtime-anchor ceiling is near `.995`, test discrete shared-alpha
   threshold chains; only after that consider a physical selective index.

The next oracle must not use the teacher anchor to select the reported runtime
anchor. Any privileged teacher or exact global-nearest row is labeled as an
upper bound. Anchor selection and segment ranking are reported as separate
funnel stages.

An earlier implementation used an unsorted `argpartition` prefix for
intermediate K values; those values were discarded and the ray runners now
sort the selected max-K set before taking prefixes.
