# NeuRoute frozen feasible candidate-work frontier

## Context

- Date: 2026-08-30
- PR: stacked on the matched R0/R3 document-summary ladder
- Status: full DE-1M measurement and independent result replay complete

## Question

How much of R3c's fixed-256 improvement over R0 remains when every treatment
is evaluated at the same physically feasible candidate work, rather than the
same number of opened addresses?

## Frozen protocol

The DE-1M 16-bit partition, K8 exact prototype shortlist, top-1024 addresses,
three route seeds, internal 76-query split, twelve serialized R0/R3 models,
authoritative qrels, and Hamming768 -> ADC64 -> exact-E5 cascade are frozen from
the parent result. No model is fitted in this PR.

Candidate-fraction budgets are `.20%`, `.25%`, `.30%`, `.35%`, `.40%`,
`.50%`, and `.625%`. For each query and budget the runner reports the last
strict order prefix whose unique candidate union does not exceed the budget and
the first prefix that crosses it. Single assignment makes posting-count sums
equal unique candidates here. Interpolation between the two physical points is
reported only as a non-deployable descriptive statistic.

The frozen treatments are prototype order, R0, R3a, R3b, R3c, and privileged
static gain density. A separate privileged budget-aware marginal control uses
authoritative target information and is diagnostic only.

## Decision rule

At the headline `<=.005` candidate fraction, R3c must preserve at least half of
the parent fixed-256 actionable improvement (`.0319`) over R0 in every seed to
license the decoupled relevance/cost branch on its preregistered gate. The
replication topology diagnostic remains required regardless of this outcome.
Nothing in this experiment licenses native activation or production selection.

## Results

Three-seed internal means at each query's last feasible `<=.005` prefix are:

| Treatment | Candidate fraction | Opened addresses | Actionable gain | Exact nDCG@10 |
|---|---:|---:|---:|---:|
| Prototype order | .004986 | 242.1 | .8114 | .6156 |
| R0 scalar | .004983 | 255.0 | .8329 | .6281 |
| R3a occupancy | .004986 | 240.5 | .8335 | .6280 |
| R3b residual mean | .004984 | 218.1 | .8422 | .6334 |
| R3c residual shape | .004984 | 212.4 | .8595 | .6401 |
| Privileged gain density | .004992 | 464.8 | .9133 | .6492 |
| Privileged marginal diagnostic | .004985 | 241.8 | .9056 | .6506 |

R3c remains materially better than R0 at matched candidate work: the mean
actionable improvement is `.0266`, and exact nDCG improves by `.0120`. The
per-seed actionable improvements are `.0217`, `.0448`, and `.0132`, which
retain `.68x`, `1.40x`, and `.41x` of the parent `.0319` fixed-256 improvement.
The every-seed half-improvement gate therefore fails narrowly on the third
seed even though the mean ordering improvement is real.

The result narrows the parent interpretation:

> R3c did not obtain all of its quality merely by opening expensive postings,
> but the representation alone does not deliver a stable enough cost-matched
> gain to close the policy question.

The privileged controls still expose substantial headroom at the same work.
Static density reaches `.9133` actionable gain; the true cascade-marginal
control reaches `.9056` and slightly higher nDCG. Their different address
counts and ranking trade-offs are further evidence that relevance prediction
and cost policy must be measured separately.

Result SHA-256 is
`f5fe1995ad82d365e33a360e2010a7e44e24bcd7521d4e03393753f53039ea36`.
Independent replay reproduced the complete 12.5 MB result byte for byte.
Evidence SHA-256 is
`f21fa7ae644f3e2ad61c58be4bc8909dbfe859f4821b633a224fc75f8f5e8683`.

## Limitations

- The matched-work frontier is frozen to the current models; it does not train
  a model to predict relevance separately from posting cost.
- Exact top-1024 prototype retrieval remains an offline research operation.
- The marginal control sees authoritative internal targets and is not a
  deployable scheduler.
- Strict prefixes intentionally do not skip a crossing address. A deployable
  hard-budget policy that may skip work is tested separately.
- Interpolated values are not physically realizable and carry no activation
  claim.

## Next checks

Run the already approved decoupled relevance x cost diagnostic conditionally:
freeze R3c, fit relevance targets only on training queries, choose calibration
and policy lambda on configuration queries, and keep internal qrels sealed.
Then run the independent replication-topology diagnostic even if the learned
policy improves, because replication changes the physical address assignment
rather than only the query-side order.
