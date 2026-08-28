# NeuRoute nonlinear direct-address scheduler

Date: 2026-08-28. Frozen protocol; measurement pending.

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
