# Centroid-free semantic-address ranking

Date: 2026-08-26. This follow-up isolates the output-objective question raised
by the direct learned semantic-address v1 study. It does not change document
placement: documents retain the frozen document-only PCA/median `8-bit`,
replication-4 address substrate from PR #176.

## Question

Can a query-side router choose useful final MDBX addresses without a runtime
centroid scan when it ranks complete addresses rather than independent address
bits?

## Fixed setup

- frozen Spanish 25k E5 materialization, ITQ/ADC/E5 cascade and v1 partitions;
- maximum 16 address lookups and a hard 10% candidate-union ceiling;
- 324 train queries, 162 configuration-selection queries and the already seen
  162-query v1 internal partition;
- five predeclared fixed seeds per newly trained treatment, fixed final epoch;
- no model, seed or configuration selection from the internal partition.

The internal partition is a locked comparative replay, not an untouched
confirmation set after the v1 result motivated this follow-up. A later
successful treatment requires new external confirmation.

## Treatments

| Treatment | Training target and runtime routing |
| --- | --- |
| Symmetric document-head control | Existing document PCA logits with confidence subset flips |
| Bitwise BCE v1 control | Published `384 -> 128 -> 16` learned bit logits and confidence flips |
| Address multi-label BCE | `384 -> 128 -> 256` logits; one output per physical address, weighted E5-top-10 address membership |
| Address listwise marginal gain | Same 256 logits; a normalized greedy sequence of marginal oracle coverage divided by the square root of fresh posting count |
| Semantic-tree beam | A document-only balanced semantic tree over the 256 physical buckets; `384 -> 128 -> 255` branch logits and best-first beam to 16 leaves |

All learned treatments score only addresses and then use identical MDBX-like
postings followed by ITQ-256 Hamming, ADC and exact E5 rerank. There is no
runtime centroid scoring in any headline treatment.

## Required evidence

For every treatment and seed, retain the model hash, train loss, requested and
accepted addresses, candidate count, raw/Hamming/ADC oracle survival and nDCG.
Report seed distributions and deterministic paired bootstrap intervals against
the symmetric control. The result is successful only if a centroid-free
treatment improves the control under the same hard candidate ceiling; matching
the centroid-refined v1 runtime control remains an aspirational, not selected,
threshold.

## es-25k result

All treatments completed on the predeclared five seeds (where applicable). The
following internal values are seed means; candidate fractions remained within
`9.75--9.78%` for every row.

| Treatment | E5 top-10 survival after ADC | nDCG@10 | Paired ADC delta vs symmetric, 95% bootstrap |
| --- | ---: | ---: | ---: |
| Symmetric document-head control | 66.36% | 0.6523 | reference |
| Bitwise BCE v1 control | 64.81% | 0.6068 | -1.54 pp (-5.37, +2.28) |
| Address multi-label BCE | 63.78% | 0.6008 | -2.58 pp (-5.99, +0.68) |
| Address listwise marginal gain | 51.54% | 0.5144 | -14.81 pp (-18.65, -10.85) |
| Semantic-tree beam | 55.57% | 0.5475 | -10.79 pp (-14.41, -6.96) |

The multi-label 256-address model removed bitwise factorization but still did
not improve the symmetric control; its seed distribution was `62.10--65.80%`
survival. The listwise greedy target and the document-only semantic tree were
unambiguously worse. This closes the narrower hypothesis that a different
query-side ranking objective alone can recover the centroid-refined frontier on
the fixed PCA/median partition with the available 324 query labels.

The next isolated test must therefore change the representation-learning
regime, not retune these output heads: a shared document/query continuous
encoder with learned document placement, trained primarily from the document
distribution, is the appropriate NeuRoute-like follow-up. This result does not
yet establish that local centroid refinement is architecturally necessary.
