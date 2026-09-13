# Deep R4 ranker and topology follow-ups (2026-09-13)

## Scope

This wave tests whether the residual R4 gap can be removed by learning a
proper score over the complete 8,192-address frontier, by selecting postings
with a non-leaking utility scheduler, or by replicating/partitioning postings.
The frozen DE-1M fixture is unchanged.  Teacher ids are used only as training
labels and evaluation targets; they are never available to the runtime
ranker.  All budgets count unique document ids and posting-entry work is
reported separately.

## Deep full-frontier ranker

Address representatives are the mean FP32 vector of each immutable R4
posting.  A four-feature pointwise logistic baseline is fit on the first 114
queries and evaluated on 38 held-out queries.  The frozen 1,024-address
shortlist is restored to model-ranked order before the baseline scores the
complete 8,192-address frontier.  Features are query/address cosine, inverse
deep rank, log posting size, and normalized rank.

| unique budget | mean recall | p05 | minimum |
| ---: | ---: | ---: | ---: |
| 20k | .9105 | .685 | .60 |
| 50k | .9368 | .685 | .60 |
| 100k | .9684 | .885 | .60 |

The held-out ranker is below the earlier prefix oracle and does not approach
the .99 @ 50k gate.  This is a measured ordering result, not a production
claim; physical pages, latency, and MDBX I/O were not measured.

## Adaptive utility, multi-anchor and spilling controls

On the same three route streams (seed2702, seed2703, deep-8192), a scheduler
that maximizes marginal fresh candidates per posting entry reaches mean .9717
 at 50k and .9862 at 100k.  Deterministic posting round-robin fusion reaches
.9796 at 50k and .9921 at 100k.  This is a posting-order control, not an
equal-candidate quota and not a true multi-anchor assignment. Exact
teacher-leaking prefix and
arbitrary-support oracles remain reported only in the #388 receipt.  These
controls do not recover the missing .99 @ 50k frontier.

No balanced-subposting quality claim is made: the former chunk counter was only
an accounting stub and has been removed. No FP32 payload was duplicated.

## Decision

The evidence is not sufficient to close the topology question: this is a
four-feature baseline, and the three-anchor control is not a true multi-anchor
assignment.  All measured controls remain below the target at 50k.  The next
arm is a cost-aware/listwise full-frontier ranker followed by selective
secondary assignment or a genuinely new multi-anchor topology.  THQ-ADC/MDBX
cascade activation remains gated.

Receipts:

* `2026-09-13-r4-deep-ranker-result.json`
* `2026-09-13-r4-adaptive-spill-multianchor-result.json`

Raw bundles are stored outside Git under `E:\_repoz\agent-memory-workspaces`.
