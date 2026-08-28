# NeuRoute nonlinear direct-address scheduler

Date: 2026-08-28. Frozen implementation `aa1c1ad`; measurement complete.

## Question

#222 found both a quadratic address-basis loss and a shared query-mapping loss.
Can a nonlinear query tower plus a direct learned address representation realize
more of the frozen 16-bit oracle, and does independent training-query density
change the answer?

## Independent query pool

MIRACL does not publish a German train split at the pinned revision. The
additional pool therefore uses the available Spanish, French, and Russian train
topics from the same immutable MIRACL revision. Their 7,988 query texts are
embedded with the exact frozen multilingual-E5 model and deterministically
ordered. They are appended after the original 153 German training queries.

Nested training sizes are `153, 512, 2048, 4096, 8141`. The 76 German
configuration queries are used only for model-size and probe-budget selection.
The separate 76 German internal-evaluation queries remain untouched until all
models and selections are frozen.

## Models

Both variants learn a small nonlinear query tower and a direct 64-dimensional
embedding for every 16-bit address:

```text
direct_id
centroid_initialized_id
```

`direct_id` starts from a deterministic bit projection. The centroid variant
initializes occupied-address vectors from the mean frozen E5 document centroid
passed through the frozen router hidden layers. Both then receive identical
sampled-listwise training against exact-E5 top-100 discounted address gain.

The study reports the complete calibration data-size curve. One training size
and probe budget per seed/variant are selected without evaluation queries, then
run through the complete cascade on DE-25k/100k/1M internal evaluation.

Sequential follow-up is licensed only if a nonlinear variant passes every
quality gate and reduces DE-1M candidate work by 25% or oracle regret by 50% on
all three seeds. Production selection is forbidden.

## Results

The frozen run produced all 30 model artifacts, 198 calibration rows, six
calibration-selected models, and 27 held-out rows. The evidence writer retrained
every model in a temporary directory and reproduced all model archives and the
complete result byte for byte.

```text
result SHA-256:   4a7054c126079545e95a12cb819c9c1fc166c172d4c4cf26f6349a8ab5dccec9
evidence SHA-256: 268d1f533a3bc3c0f138dc1712a8d27836b2b24c7271262e84991489cd512ebf
```

The held-out means across the three frozen seeds were:

| Scale | Treatment | Candidate fraction | Raw E5 survival | ADC64 survival | Exact64 nDCG@10 | nDCG retention |
|---|---|---:|---:|---:|---:|---:|
| DE-25k | `occupied_logit` | .1000 | .8930 | .8895 | .7179 | .9603 |
| DE-25k | `direct_id` | .1000 | .9110 | .9061 | .7161 | .9578 |
| DE-25k | `centroid_initialized_id` | .1000 | .9377 | .9325 | .7386 | .9880 |
| DE-100k | `occupied_logit` | .0466 | .7899 | .7794 | .6729 | .9280 |
| DE-100k | `direct_id` | .0529 | .8193 | .8105 | .6935 | .9566 |
| DE-100k | `centroid_initialized_id` | .0597 | .8241 | .8184 | .7242 | .9989 |
| DE-1M | `occupied_logit` | .0316 | .7364 | .7048 | .5899 | .8924 |
| DE-1M | `direct_id` | .0357 | .7263 | .6974 | .6316 | .9554 |
| DE-1M | `centroid_initialized_id` | .0437 | .6912 | .6693 | .6754 | 1.0217 |

All three `direct_id` selections used the full 8,141-query pool. That is useful
evidence that independent training density materially changes this model: at
2,048 probes its configuration-set raw-survival mean rose from .3307 at 153
queries to .8965 at 8,141 queries. The gain did not transfer into cheaper
held-out DE-1M routing. Relative to each seed's `occupied_logit` baseline,
`direct_id` increased candidate work by 11.0%, 16.6%, and 11.8%; oracle regret
increased by nearly the same amounts.

Centroid initialization improved final exact64 nDCG but was unstable under the
frozen selection rule: only one seed selected the full pool, while two selected
153 queries. On DE-1M it increased candidate work by 18.7%, 51.6%, and 45.2%
and reduced raw/ADC survival. Retention above one is possible because exact64
reranking over the routed cascade can outscore the separate full-E5 baseline on
the finite qrels metric; it is not evidence of more than complete oracle
coverage.

## Decision and interpretation

Neither nonlinear treatment passed the predeclared all-seed quality and
improvement gate. `sequential_followup_licensed` and
`production_selection_licensed` are both false.

The result rejects a narrow hypothesis: a static normalized-dot-product scorer
with a nonlinear query tower and one learned vector per address does not realize
the #216 oracle headroom under this teacher, selection rule, and data pool. It
does not reject arbitrary sequential schedulers. However, the sequential study
was explicitly conditional on this static model passing, so training or quoting
sequential measurements now would be post-hoc protocol expansion. The stacked
follow-up therefore records a fail-closed closure instead of running the
unlicensed experiment.

## Limitations

- The independent training topics are Spanish, French, and Russian because the
  pinned MIRACL revision has no German train split; domain/language transfer is
  part of the measured treatment.
- Address negatives are sampled rather than a full 65,536-way listwise loss.
- The conclusion applies to the frozen 64-dimensional static address scorer,
  not every nonlinear or autoregressive scheduler.
- Final quality gains accompanied by more candidate work are diagnostically
  interesting but do not satisfy the routing-efficiency objective.
