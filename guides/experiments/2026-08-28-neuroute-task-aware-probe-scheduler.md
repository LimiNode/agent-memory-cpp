# NeuRoute task-aware probe scheduler

Date: 2026-08-28. Frozen protocol; measurement pending.

## Question

Can query-side occupied-address ordering realize enough of the oracle headroom
found in #216 without changing frozen 14/16-bit document addresses?

The experiment freezes all six strong raw-Euclidean routes from #203. It never
updates document encoders, thresholds, addresses, or postings. Only the final
query-side 64-to-width mapping may differ for learned treatments.

## Treatments

```text
current_full_space
occupied_logit
occupied_mass_aware
anchored_reachability
anchored_mass_aware
```

The occupied-logit baseline separates empty-address traversal from query-score
quality. The mass-aware baseline subtracts a calibrated log posting-mass
penalty. The two learned heads use a deterministic closed-form ridge update of
the query-only last layer. Their targets are exact-E5 top-100 address sign
distributions, weighted either by relevant-document gain or by gain per posting
mass. A ridge anchor toward the original head protects the inherited geometry.

## Split and selection

All mass penalties, probe budgets, and query-head parameters use only the 153
frozen training queries at DE-25k. The 76 configuration queries remain held out
until the five treatments are frozen. Evaluation then runs the complete
candidate, Hamming-768, ADC-64, and exact-10 cascade on nested DE-25k, DE-100k,
and DE-1M.

A treatment licenses a later native confirmation only if every scale and seed
passes the inherited frontier:

```text
candidate fraction <= .10
ADC64 E5-oracle survival >= .65
exact64 nDCG retention vs full E5 >= .85
```

No treatment can become a production default in this PR. Query-head artifacts
and their source frozen model identities must be hash-bound in the result.

## Evidence contract

The runner verifies the passed #216 mechanism gate, the #203 result/evidence,
materialization, all frozen route payloads, all input manifests, and every model
hash. Learned heads are regenerated from frozen training queries and compared
array-for-array with their saved artifacts. The evidence writer reruns the full
training, calibration, and held-out cascade and requires byte-identical output.
