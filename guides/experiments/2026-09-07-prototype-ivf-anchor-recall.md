# Prototype-IVF anchor recall oracle

Date: 2026-09-07. This experiment isolates the routing bottleneck identified
by the ray/segment oracle: how often does a frozen IVF cell generator expose a
useful prototype anchor at all?

## Protocol

The same 454,322 K8 prototypes, 4,096 centroids and 152 DE queries are used.
For each query, centroids are ranked by cosine; the top `M` cells are opened,
all prototypes assigned to those cells are collected, and their cosine scores
are ranked. This is an anchor-recall diagnostic, not a complete retrieval
cascade. It reports inclusion of the exact teacher top-1 prototype and overlap
with teacher top-10 prototypes.

## Result

| top IVF cells M | mean prototype candidates | teacher top-1 recall | teacher top-10 recall |
|---:|---:|---:|---:|
| 1 | 478 | .3487 | .2151 |
| 2 | 985 | .5724 | .4020 |
| 4 | 2,156 | .6974 | .5743 |
| 8 | 4,666 | .8224 | .7401 |
| 16 | 9,687 | .9013 | .8684 |
| 32 | 20,328 | .9408 | .9467 |
| 64 | 40,836 | .9803 | .9809 |

## Interpretation

The simplified top-1 IVF anchor exposes the teacher top-1 prototype in only
34.9% of queries, which explains the `.9197` ray/segment survival observed for
that anchor source. The geometry itself reaches `.9816` with the privileged
anchor; the dominant open problem is obtaining a good anchor cheaply. Top-32
and top-64 cells approach the privileged ceiling, but at 20k--41k prototypes,
so they are not yet a useful selective route.

This result also explains why a naive four-anchor `min(segment distance)` is
not a valid fix: adding anchors without confidence or quotas broadens the
acceptance region and admits distractors. The next experiment should evaluate
rank-aware anchor quotas or a learned/confidence-weighted anchor schedule,
then apply shared-alpha scoring only to the selected anchors.

## Limitations

This uses the frozen IVF centroids and assignments as a simplified generator;
it does not include the full production prototype-IVF implementation, local K8
refinement, K32/R0 or MDBX behavior. Teacher target inclusion is an upper-bound
diagnostic and not final qrels quality.
