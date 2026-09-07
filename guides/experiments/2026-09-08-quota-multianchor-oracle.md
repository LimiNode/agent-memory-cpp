# Quota multi-anchor downstream oracle

Date: 2026-09-08.

## Question

The single-anchor screen showed a long tail of target addresses.  This replay
tests whether several shared-alpha rays can cover those modes when each ray
receives an explicit quota, instead of taking a minimum distance over all
anchors.  The objective is full R4 address expansion followed by exact E5
top-10 survival.

The oracle is exhaustive only over quota allocations (32-prototype steps,
up to four anchors) inside the declared eight-anchor screen.  It is therefore
not an exhaustive best-anchor-in-pool result.

## Results: global cosine top-8 screen, 16 queries

| total P | single best | equal quota | greedy marginal | quota oracle |
|---:|---:|---:|---:|---:|
| 256 | .738 | .756 | .681 | .763 |
| 512 | .806 | .819 | .744 | .825 |
| 1,024 | .844 | .850 | .775 | .863 |
| 2,048 | .906 | .913 | .856 | .919 |

The quota oracle improves on the best single ray by only `.019` at P=256
and `.019` at P=1024.  Its worst-query survival is `.30/.475` at P=256/1024.
The greedy method is not a substitute for the oracle: local marginal gains
can spend budget on a ray whose later prefix is less useful.

## Target-conditioned upper-bound smoke, 8 queries

To estimate how much headroom remains if candidate anchors themselves are
chosen with target information, the same quota search was run over the eight
highest-cosine prototypes belonging to the exact target addresses.

| total P | single best | equal quota | greedy marginal | quota oracle |
|---:|---:|---:|---:|---:|
| 256 | .688 | .813 | .850 | .875 |
| 512 | .763 | .850 | .900 | .913 |
| 1,024 | .813 | .913 | .900 | .925 |
| 2,048 | .863 | .938 | .925 | .963 |

This is target-leaking and only eight queries, so it is an upper-bound smoke,
not product evidence.  Even with that privilege, the mean is `.925` at
P=1024 and `.963` at P=2048.

## Interpretation

The global candidate set confirms that quota allocation is a real but modest
improvement over one anchor.  The target-conditioned smoke leaves a possible
multimodal gain, but does not approach the `.995` document gate at practical
P.  The next selector experiment should therefore be small and held out:
budget-conditioned candidate selection with explicit quotas, reporting the
first candidate and the best-of-screen separately.  If it cannot close the
gap toward `.995`, shared-alpha/MIH should be closed as a primary retrieval
line while retaining the flat THQ and modern R4 profiles.

Raw reports:

* `tmp/quota-global-16q.json`
* `tmp/quota-target-8q.json`

Runner: `tools/agent-memory-bench/evaluate-quota-multianchor-oracle.py`.
