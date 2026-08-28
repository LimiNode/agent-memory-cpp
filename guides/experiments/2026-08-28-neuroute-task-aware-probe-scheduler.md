# NeuRoute task-aware probe scheduler

Date: 2026-08-28. Frozen protocol and completed measurement.

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

## Result

The 378-row calibration and complete held-out cascade replayed byte-for-byte.
Both the current full-space scheduler and the occupied-logit baseline pass the
inherited held-out gate on every width, scale, and seed. Neither learned query
head passes that same all-row gate. The posting-mass penalty is actively harmful
under scale transfer.

The most informative DE-1M comparison is the 16-bit calibration-selected role,
averaged over three frozen seeds:

| Treatment | Probes | Candidate fraction | ADC64 survival | Exact64 retention |
| --- | ---: | ---: | ---: | ---: |
| current full space | 4096 | .0620 | .7798 | .9333 |
| occupied logit | 2048 | .0316 | .6882 | .8897 |
| occupied mass-aware | 4096 | .0103 | .1053 | .1716 |
| anchored reachability | 2048 | .0304 | .6206 | .7530 |
| anchored mass-aware | 2048/4096 | .0370 | .6232 | .7717 |

At the fixed 256-probe mechanism point, occupied-logit and current-full-space
are identical: .0043 candidate fraction, .3833 ADC64 survival, and .5796 exact64
retention at 16 bits. Occupied-only traversal therefore does not repair query
semantics. Its useful effect is removing empty-address work at the much larger
calibrated frontier: it halves requested probes and candidate mass relative to
the 4096-probe full-space fallback while retaining the inherited quality gate.

The task-aware ridge targets do not realize the oracle headroom from #216.
Training a last-layer query head against exact top-100 address signs loses too
much seed/scale robustness. Dividing teacher or scheduler utility by posting
mass is worse: it learns small lists that are cheap but mostly irrelevant.

Result SHA-256:
`fb5e9926c1ec44b3820d3c27f49e2ca77096dfbee4378a15c13070bacf5ff39a`.
Evidence SHA-256:
`146c3059c92c78527be92aeebf8f118e861ba8f6482b9ed927ece6edc10d10de`.

## Interpretation

The document partition remains viable, but the tested closed-form query-only
teacher is not a replacement for the original scheduler. The deployable
research candidate from this PR is only the non-learned occupied-address
enumeration, and even that requires native confirmation before any runtime
choice. A future learned scheduler needs a listwise or cascade-aware objective;
another sign-mean ridge variation is not justified by these results.

## Evidence contract

The runner verifies the passed #216 mechanism gate, the #203 result/evidence,
materialization, all frozen route payloads, all input manifests, and every model
hash. Learned heads are regenerated from frozen training queries and compared
array-for-array with their saved artifacts. The evidence writer reruns the full
training, calibration, and held-out cascade and requires byte-identical output.

The evidence writer completed both result and deterministic head-artifact byte
replay. Generated JSON and NPZ payloads remain local under
`tmp/neuroute-task-aware-probe-scheduler/` per the raw artifact policy.
