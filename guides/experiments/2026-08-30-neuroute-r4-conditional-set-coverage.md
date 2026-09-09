# NeuRoute R4 conditional representative set coverage

## Context

- Date: 2026-08-30
- PR: stacked on the R4 representative coverage-saturation study
- Status: full DE-1M measurement and independent artifact/model/result replay complete

## Question

Can a training-only conditional facility-location objective choose a better
query-independent K32 actual-document basis than deterministic farthest-first,
once the partition, K8 top-1024 shortlist, matched exact-max scorer, candidate
budgets, and downstream cascade are frozen?

## Frozen protocol

The parent study selected `K*=32`; K32 is fixed here. Four conditional recipes
use the same normalized static exact-E5 address targets and 8,141 training
queries:

- coverage from the empty set;
- centroid-nearest actual document plus 31 coverage selections;
- deterministic FF8 plus 24 coverage selections;
- deterministic FF16 plus 16 coverage selections.

For each address, greedy selection maximizes
`sum_q target(q,address) * max_doc cosine(q,doc)`. Uncovered query cosine starts
at `-1`; the lowest global document position breaks exact marginal-gain ties.
Addresses without positive training support retain deterministic FF32. Selection
is query-independent at runtime and reads zero configuration/internal labels.

Controls are frozen FF32, the prior independent-win K32 set retrained under the
same max scorer, prototype order, and privileged gain density. Every non-frozen
recipe gets its own deterministic four-epoch model with the same model seed and
optimizer settings. Configuration selects maximum mean actionable gain at the
strict `.005` budget; internal opens once.

## Selection audit

Across the three route seeds, normalized target support covers 20,319, 20,638,
and 20,532 occupied addresses. The four conditional recipes materialize
891,610, 876,204, and 898,743 active K32 slots respectively. Every selected
document belongs to its address posting, every active set is duplicate-free,
and the normalized target-weight sums reproduce 8,133, 8,133, and 8,131
nonzero-target training queries. Configuration/internal selection-query count
is zero.

## Results

At the strict `.005` frontier, three-seed means are:

| Partition | Treatment | Candidate fraction | Actionable gain | Exact nDCG@10 |
|---|---|---:|---:|---:|
| Configuration | FF32 | .004976 | .8708 | .6184 |
| Configuration | Coverage from empty | .004982 | .8513 | .6082 |
| Configuration | Centroid1 + coverage31 | .004986 | .8491 | .6088 |
| Configuration | FF8 + coverage24 | .004986 | .8448 | .6066 |
| Configuration | FF16 + coverage16 | .004983 | .8512 | .6096 |
| Configuration | Independent wins, matched max | .004985 | .8517 | .6126 |
| Internal | FF32 | .004979 | .9007 | .6507 |
| Internal | Coverage from empty | .004984 | .8832 | .6457 |
| Internal | Centroid1 + coverage31 | .004984 | .8821 | .6458 |
| Internal | FF8 + coverage24 | .004985 | .8829 | .6430 |
| Internal | FF16 + coverage16 | .004985 | .8844 | .6443 |
| Internal | Independent wins, matched max | .004985 | .8844 | .6477 |

Configuration selects frozen FF32. All teacher-driven set recipes lose roughly
`.0191--.0260` configuration actionable gain. Internal independently preserves
the sign: the best conditional recipe, FF16 + coverage16, remains about `.0163`
below FF32 actionable gain and `.0064` below its exact nDCG. The preregistered
conditional-coverage progress gate fails.

Canonical result SHA-256 is
`7e7b9546bb4a34a047ba446407aae8166f8e2402f293594039e4098d83d95b27`.
An independent run regenerated all 12 conditional selection artifacts, all 15
matched model archives, and the canonical result byte for byte. Evidence
SHA-256 is
`8ef68cf98d3529b1f3e56d5c518618ccee45343fa71e5843abdf4a297dab1f56`.

## Interpretation

The negative independent-win result was not merely a mismatch with learned
top-eight pooling: retraining that set with the matched exact-max scorer still
loses. More importantly, explicitly conditioning each new document on coverage
already supplied by the set also does not recover the deterministic frontier.
Anchoring 1, 8, or 16 farthest-first documents fails to reverse the sign.

Within this frozen teacher and training pool, weighted facility location favors
frequent supervised query modes at the cost of query-independent geometric
coverage that transfers to held-out queries. The current production candidate
therefore remains teacher-blind FF32. This closes the preregistered conditional
set-coverage branch narrowly; it does not prove that every learned or
full-resolution selector must fail.

## Limitations

- The facility objective uses the existing normalized static exact-E5 address
  targets. A separately learned representation or different teacher is not
  tested.
- Training supervision is fixed to 8,141 queries and may omit rare held-out
  modes that farthest-first preserves.
- Selection and exact representative interactions are offline diagnostics;
  no native fetch/decode latency is measured.
- This is one DE-1M partition family with three route seeds.
- INT5 quantization and physical representative storage remain deliberately
  outside this causal comparison.

## Follow-up

Retain deterministic FF32 as the selected actual-document basis. Do not spend
another PR on independent or facility-style static teacher selection without a
new representation or supervision source. After review, the next clean
engineering study is the separately scoped physical representation ladder:
freeze FF32 and measure contiguous versus document-store fetch, INT5/SIMDComp
decode, dot products, and end-to-end warm/cold behavior without changing
selection quality in the same PR.
