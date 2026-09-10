# Nearest-E5 anchor inside IVF pool

Date: 2026-09-07. This experiment separates anchor availability from anchor
selection. For each IVF cell budget, it selects the highest-cosine prototype
inside the returned pool, then evaluates the continuous shared-alpha segment
oracle from that selected anchor.

## Protocol

The frozen source has 454,322 K8 prototypes, 4,096 IVF centroids and 152 DE
queries. For each `M in {1,2,4,8,16,32,64}`, the top-M centroid cells are
opened, all assigned prototypes are scored by query cosine, and the nearest
prototype in that pool becomes the anchor. All prototypes are then ranked by
Euclidean distance to the resulting linear segment; survival is teacher top-10
prototype membership. This is still an exhaustive oracle around the anchor
selector, not a serving benchmark.

## Result

Mean teacher top-10 survival over 152 queries:

| IVF cells M | mean pool | K=10,000 |
|---:|---:|---:|
| 1 | 478 | .9197 |
| 2 | 985 | .9461 |
| 4 | 2,156 | .9553 |
| 8 | 4,666 | .9625 |
| 16 | 9,687 | .9717 |
| 32 | 20,328 | .9763 |
| 64 | 40,836 | .9796 |

## Interpretation

The nearest-E5 selector inside the IVF pool steadily approaches the privileged
segment ceiling (`.9816`), showing that useful anchors are often present even
when the IVF top-1 anchor is not. However, the final gains become expensive:
M=64 requires about 40.8k prototype vectors, and M=16 still needs 9.7k for
only .9717 survival at K=10k. This makes anchor routing and pool-size control,
not spherical-vs-linear geometry, the practical bottleneck.

The result does not yet measure the oracle-best anchor among every returned
prototype. That is a separate upper-bound calculation and must not be
confused with the nearest-E5 runtime selector. Rank-aware quotas and a
confidence-weighted selector are the next meaningful tests.

## Limitations

The IVF generator is the simplified frozen centroid assignment, and segment
scoring scans all prototypes. Metrics are routing-ceiling teacher survival,
not final document qrels, latency, bytes or MDBX behavior.
