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
posting.  A logistic ranker is fit on the first 114 queries and evaluated on
38 held-out queries.  Features are query/address cosine, inverse deep rank,
log posting size, and normalized rank.  The model scores all 8,192 addresses
before candidate enumeration.

| unique budget | mean recall | p05 | minimum |
| ---: | ---: | ---: | ---: |
| 20k | .9105 | .685 | .60 |
| 50k | .9474 | .80 | .60 |
| 100k | .9737 | .90 | .60 |

The held-out ranker is below the earlier prefix oracle and does not approach
the .99 @ 50k gate.  This is a measured ordering result, not a production
claim; physical pages, latency, and MDBX I/O were not measured.

## Adaptive utility, multi-anchor and spilling controls

On the same three route streams (seed2702, seed2703, deep-8192), a scheduler
that maximizes marginal fresh candidates per posting entry reaches mean .9717
at 50k and .9862 at 100k.  Equal-quota three-anchor fusion reaches .9796 and
.9921.  A teacher-leaking gain-per-entry upper bound reaches .9789 and .9895;
it is included only to bound topology, not as a deployable policy.  These
results show that scheduler choice and simple replication do not recover the
missing .99 @ 50k frontier.

Balanced subposting accounting (256/512/1024-entry chunks) changes logical
posting granularity only; it cannot improve membership recall without a new
assignment topology.  No FP32 payload was duplicated.

## Decision

The evidence now points to a within-route representation/topology bottleneck,
not merely a prefix scheduler.  Proper deep ranker, adaptive utility, and
three-anchor controls all remain below the target at 50k.  The next justified
research arm is selective spilling/secondary assignment or a genuinely new
multi-anchor topology.  THQ-ADC/MDBX cascade activation remains gated.

Receipts:

* `2026-09-13-r4-deep-ranker-result.json`
* `2026-09-13-r4-adaptive-spill-multianchor-result.json`

Raw bundles are stored outside Git under `E:\_repoz\agent-memory-workspaces`.
