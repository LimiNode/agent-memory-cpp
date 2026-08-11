# 2026-08-11 MIH local-approximation study

## Question

Can inexpensive local changes to the selected 256-bit ITQ MIH cascade improve
E5-oracle survival without increasing its candidate work?

The fixed cascade is:

```text
budgeted-confidence 32x8 MIH, soft target 12,288
-> Hamming K1=768
-> binary ADC K2=256
-> exact E5 rerank
```

## Scope and interpretation

This study evaluates three deliberately narrow approximations, not the full
algorithms discussed in weighted-MIH and MIH-aware-learning literature:

1. static, calibration-only centroid-separation weighted Hamming;
2. ADC-cost rather than margin ordering of the same one-bit probe universe;
3. calibration-balanced equal-width bit grouping.

Consequently, a negative result here does not reject query-adaptive weighted
best-first enumeration, multi-bit probe combinations, true variable-width
bands, or MIH-aware code learning.

For the weighted-Hamming arm, `hamming_top_k_recall` is a scorer-self recall:
the full-corpus reference order uses the active scorer. It is not a directly
comparable cross-policy quality metric. E5-oracle survival after each cascade
stage is the primary comparison metric.

## Results

Static weighted Hamming and one-bit ADC-guided probing were near-no-op in their
separate fixed calibration-only matrices. They should be recorded as narrow
no-go results, respectively for global bit weights and for replacing margin
priority with ADC cost within exact-plus-one-bit probing.

The first equal-width correlation-balanced grouping run on seeds 42--46 was
exploratory. Its positive mean was not treated as predeclared confirmation.
The confirmatory contract was then frozen before execution: layouts
`contiguous`, fixed-random (seed `20260812`), and
`calibration-correlation-balanced`; untouched ITQ seeds 52--56; and the fixed
cascade above.

| Confirmatory comparison | Mean delta, ADC survival | Mean delta candidates | Mean delta posting visits |
| --- | ---: | ---: | ---: |
| balanced - contiguous | +.000208 | +.07 | +1.68 |
| balanced - fixed random | +.000240 | -.31 | +.71 |

The five seed deltas are not pooled as independent `5 x query_count`
observations. Per-seed paired query bootstraps use 10,000 replicates and seed
`20260811`. Every 95% interval crosses zero; balanced-minus-contiguous also
has one negative seed. The tiny point estimates are not explained by material
candidate or posting expansion, but they do not establish a practically
meaningful retrieval advantage.

## Evidence staging

The confirmatory evidence bundle contains the frozen matrix, 15 reports, 15
per-query contribution NPZ files, ten paired bootstrap reports, compact and
bundle manifests, and source snapshots. It was validated before upload as
`mih-calibration-balanced-bands-evidence-v1.zip`:

```text
archive SHA-256: 396bccee00f44a40e2dcab30151b0d3496f9dc1783545d26ea1f416cd19fd1b2
bundle root:     2cf3b4593b8dd18aa51cbf407ce40ea1c2f08e7dbae10f4f396336a2d69b9413
```

The ZIP is published in the namespaced GitHub evidence release and is not
committed to Git.

## Interpretation

The three inexpensive local approximations did not deliver the expected final
cascade improvement. The evidence is compatible with a strong downstream ADC
stage absorbing small ordering changes and with margin and per-bit ADC flip
cost being nearly monotonic for the current ITQ geometry.

The equal-width grouping hypothesis remains plausible but is not confirmed by
this matrix. It must not be described as a demonstrated MIH improvement.

## Next checks

The next experiments must make a stronger, predeclared intervention:

1. query-adaptive weighted best-first probing with two- and three-bit bucket
   combinations under candidate and posting budgets;
2. true variable-width MIH bands, where key lengths rather than only bit
   membership are calibrated;
3. MIH-aware ITQ/code selection using a calibration-only candidate-efficiency
   objective.

The first follow-up is in progress as a separate experiment: query-adaptive
ADC-cost best-first enumeration of bounded two- and three-bit bucket probes,
with predeclared soft candidate and posting-visit targets. It is intentionally
separate from this note's three narrow approximations.

At larger corpus scales, the same variants should also be judged by posting
bytes, bucket-tail behaviour, and latency, not only final E5 survival.
