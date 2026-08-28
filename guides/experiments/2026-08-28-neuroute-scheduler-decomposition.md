# NeuRoute scheduler decomposition

Date: 2026-08-28. Frozen protocol; measurement pending.

## Question

#221 showed that a quadratic query-side scheduler does not realize the strong
16-bit document-partition oracle. This experiment separates three possible
losses without changing document addresses, query partitions, or cascade data:

```text
direct teacher ordering
-> best per-query quadratic projection
-> learned quadratic mapping on training queries
-> learned quadratic mapping on held-out queries
```

## Frozen setup

The experiment consumes #221 result and evidence bytes. It reuses all three
16-bit seeds, DE-25k/100k/1M materializations, the 153 training queries, and the
76 configuration queries. The 76 internal-evaluation queries remain untouched.

Each query receives exact-E5 top-100 inverse-log discounted address gains. The
direct teacher ranks those gains without a learned representation. The best
per-query quadratic stage fits the same 136 singleton-plus-pairwise feature
basis independently for each query with no query-generalization requirement.
The learned stage replays the #221 `listwise_gain` head.

Every stage reports discounted top-100 coverage, raw exact-E5 top-10 survival,
candidate fraction, and the complete Hamming-768 -> ADC-64 -> exact-10 cascade
at the primary 2048-probe budget. No native or production selection is allowed.

## Interpretation contract

- A large direct-teacher -> per-query-quadratic loss identifies address-basis
  capacity as the first bottleneck.
- A large per-query-quadratic -> learned-training loss identifies the query-head
  mapping or optimization as the first bottleneck.
- A train -> held-out collapse identifies data density/generalization.

The diagnostic is additive and does not alter #221 measured sources or results.
