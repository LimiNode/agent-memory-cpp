# NeuRoute scheduler decomposition

Date: 2026-08-28. Frozen protocol and completed measurement.

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

## Result

The frozen implementation is commit `9c227de`. The complete 432-row frontier
and 72-row cascade matrix replayed byte for byte.

```text
result SHA-256   8e6399d9753d9a05ce582b2842d42ff5127ac00a31508000fe503e6c2f540609
evidence SHA-256 ac554f88d0c9cb120f7b4a532fb7dafca9f154a9cdbf2e493eb8bb634d7401bb
```

At the frozen 2048-probe budget, seed-mean discounted top-100 gain coverage is:

| Scale / partition | Occupied logit | Direct teacher | Best per-query quadratic | Learned quadratic |
| --- | ---: | ---: | ---: | ---: |
| DE-25k training | .7270 | 1.0000 | .8616 | .7553 |
| DE-25k held-out | .7068 | 1.0000 | .8483 | .7197 |
| DE-100k training | .6420 | 1.0000 | .7642 | .6679 |
| DE-100k held-out | .6067 | 1.0000 | .7536 | .6160 |
| DE-1M training | .6649 | 1.0000 | .7818 | .6898 |
| DE-1M held-out | .6161 | 1.0000 | .7811 | .6250 |

On DE-1M held-out queries, the corresponding exact-retention means after the
complete cascade are `.8897`, `.9911`, `.9846`, and `.8558`.

## Interpretation

All three diagnostic gates fail, and the nonlinear follow-up is licensed.

First, the address basis is a real bottleneck: even independently optimizing
136 quadratic coefficients for every held-out query retains only `.7811` of
the direct teacher's gain at DE-1M. This loss exists before learning a shared
query head and therefore cannot be repaired by adding more training queries to
the same representation alone.

Second, query mapping is also a bottleneck. On training queries the learned head
captures only about 17-26% of the improvement available to the per-query
quadratic fit. On held-out DE-1M queries it captures only 2-9%, depending on
seed. The held-out learned score is only `.6250`, barely above the `.6161`
baseline, despite the per-query quadratic upper bound reaching `.7811`.

The direct teacher and best per-query quadratic stages both preserve nearly all
final exact ranking quality, so the remaining failure is upstream of Hamming
and ADC. A meaningful continuation must change the address representation and
increase independent query supervision together; another ridge over the same
polynomial features is not justified.
