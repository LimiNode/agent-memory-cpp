# Downstream best-anchor screen

Date: 2026-09-08.

## Question

After the authoritative document-conditioned replay, this diagnostic asks
whether changing the shared-alpha anchor can improve the actual downstream
document-address objective.  The measured objective is exact E5 top-10
survival after selecting shared-alpha top-P prototypes and expanding their
full R4 document addresses.

This is a bounded screen, not an exhaustive best-anchor oracle.

## Setup

* Seed `2026082701`, 16-query diagnostic subset from the same 152-query pool.
* Budgets `P=256` and `P=1024`.
* Eight candidate anchors per screen:
  * `global_cosine_top`: top eight prototypes by query cosine over all 454k
    prototypes (privileged global screen);
  * `ivf_m8_cosine_top`: top eight by cosine inside the top eight IVF cells
    (the closest of these to a runtime-reachable selector);
  * `target_conditioned_cosine_top`: top eight cosine prototypes among the
    exact target-address prototypes (target-leaking upper-bound control).
* `teacher_anchor`: the frozen prototype-teacher top-1 anchor.

For every candidate anchor, the segment ranking is recomputed and evaluated
against the full R4 document-to-address mapping.  The report stores both the
first candidate (runtime-like baseline) and the best candidate in the screen.

## Results

| screen | P=256 first / best | P=1024 first / best | P=256 worst best | P=1024 worst best |
|---|---:|---:|---:|---:|
| teacher anchor | .663 / .663 | .750 / .750 | .000 | .000 |
| global cosine top-8 | .663 / .738 | .750 / .844 | .200 | .400 |
| IVF top-8-cell cosine | .669 / .738 | .750 / .844 | .200 | .400 |
| target-conditioned top-8 | .700 / .763 | .813 / .875 | .400 | .500 |

The global and IVF screens had identical best-survival means on this subset,
although their candidate ids were not always identical.  The first IVF
candidate was only slightly above the teacher baseline at P=256 and tied it
at P=1024.  Most best anchors were the first cosine candidate (12/16 at P=256
and 10/16 at P=1024 for the global screen), so the gain is not explained by a
large arbitrary search over eight candidates.

Target-address rank under the teacher anchor had median rank 98, p90 4,656,
p95 9,386, p99 25,677 and maximum 31,596 across 157 target addresses.  This
shows a small number of distant target modes dominate the tail loss.

## Interpretation

The result keeps the line alive as a diagnostic: a cosine-selected anchor can
improve downstream document survival by roughly `.075/.094` at P=256/1024 on
this small subset.  It does not yet establish a product route: even the
privileged target-conditioned screen reaches only `.763/.875`, the sample is
16 queries, and the global screen is exhaustive over prototypes only for its
cosine ranking—not for the downstream best-anchor objective.

The IVF-reachable screen is encouraging because it tracks the global screen,
but its first candidate remains close to the teacher baseline.  A practical
selector must therefore be evaluated on a held-out/full query set and must
report candidate-pool cost; the best-of-eight value is an upper bound.

The next useful experiment is a budget-conditioned quota or multi-anchor
selector for the few high-rank target modes, followed by a larger held-out
replay.  Discrete THQ/MIH remains frozen until such a selector clears the
document-quality and cost gates.

Raw report: `tmp/best-anchor-screen-16q-v2.json`.
Runner: `tools/agent-memory-bench/evaluate-downstream-best-anchor-screen.py`.
