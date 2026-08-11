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

## Query-adaptive ADC best-first follow-up

The final predeclared matrix freezes three optional-probing resource-target
pairs `(candidate, postings)`: `(8192, 11000)`, `(12288, 19000)`, and
`(16384, 30000)`. For each pair and each ITQ seed 42--46, it compares the
one-bit margin-ordered confidence control with globally binary-ADC-cost-ordered
two- and three-bit per-band mutations. Exact buckets remain mandatory. Both
targets apply only to optional probing, so an exact-bucket floor may already
exceed a target and the final posting may cross it.

The original v1 staging run did not give the confidence control a posting
target. It is exploratory only and is superseded by the matched-target v2
replay below. The final 45 rows use the same candidate and posting targets for
all three policies. Values are five-seed means; `raw`, `Hamming`, and `ADC`
are E5 oracle top-10 survival after the raw MIH union, Hamming K=768, and
binary-ADC K=256 respectively.

| Optional targets | Policy | Raw | Hamming | ADC | Candidates | Posting visits | Bucket probes | Optional probes by flip depth 1 / 2 / 3 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8,192 / 11,000 | confidence | .965112 | .962412 | .961821 | 8,227.26 | 10,438.75 | 100.18 | 68.18 / 0 / 0 |
| 8,192 / 11,000 | ADC best-first, <=2 | .962843 | .960128 | .959537 | 8,228.01 | 10,408.34 | 100.61 | 51.17 / 17.43 / 0 |
| 8,192 / 11,000 | ADC best-first, <=3 | .962620 | .959936 | .959345 | 8,227.80 | 10,400.82 | 100.66 | 50.06 / 16.69 / 1.90 |
| 12,288 / 19,000 | confidence | .992939 | .987780 | .986789 | 12,311.97 | 18,279.60 | 177.53 | 145.53 / 0 / 0 |
| 12,288 / 19,000 | ADC best-first, <=2 | .992444 | .987540 | .986629 | 12,312.45 | 18,180.04 | 177.89 | 89.87 / 56.01 / 0 |
| 12,288 / 19,000 | ADC best-first, <=3 | .992204 | .987316 | .986390 | 12,313.11 | 18,130.45 | 177.85 | 85.04 / 50.10 / 10.71 |
| 16,384 / 30,000 | confidence | .998498 | .992109 | .991102 | 16,054.75 | 28,876.21 | 285.21 | 253.21 / 0 / 0 |
| 16,384 / 30,000 | ADC best-first, <=2 | .999185 | .992700 | .991693 | 16,339.50 | 29,777.20 | 294.53 | 133.07 / 129.46 / 0 |
| 16,384 / 30,000 | ADC best-first, <=3 | .999153 | .992748 | .991741 | 16,356.83 | 29,653.58 | 294.52 | 121.44 / 106.42 / 34.67 |

The depth counters demonstrate actual two- and three-bit enumeration rather
than a declarative radius setting. At the two smaller targets the candidate
target ends almost every query. At 16,384, the confidence control exhausts its
one-bit universe on 67.8% of queries, while the best-first variants reach the
candidate target on 58.9% (two-bit) and 68.8% (three-bit) of queries, and the
posting target on the remainder. This explains the larger realized candidate,
posting, and probe work for the latter variants at that target.

Thus this bounded ADC-cost policy has no uniform matched-target improvement:
it loses after binary ADC at 8,192 and 12,288, while the apparent advantage at
16,384 accompanies more realized work after the confidence control has
exhausted one-bit buckets. It is a no-go for this particular bounded
approximation, not for broader query-adaptive multiprobe research.

The validated v3 evidence bundle contains the frozen matrix, 45 reports, 45
per-query contribution NPZ files, 30 deterministic paired 10,000-replicate
bootstrap reports, compact and bundle manifests, and source snapshots. The
contributions retain per-query probe-depth, posting-depth, and stopping-reason
values; the packager independently recomputes the corresponding report
summaries from them.

```text
archive SHA-256: fbe00f2e56ac7c45aaa9eb75c24def7681e6cb6c31c1b2dfe6efc2e119925b7e
bundle root:     bfccac59f193cf87adb72ba2c4c3755431132a9aeacde16e5bf56c62c9bd7883
```

It is staged for the namespaced draft release `evidence/mih-adc-best-first-v3`
and is not committed to Git. At larger corpus scales, the same variants should
also be judged by posting bytes, bucket-tail behaviour, and latency, not only
final E5 survival.
