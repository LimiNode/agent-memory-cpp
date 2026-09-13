# R4 ordering and selective-spill follow-up (2026-09-13)

This follow-up implements the corrective controls requested by the review of
#390. The former `equal_quota` label is replaced by the more precise
`posting_round_robin`; it is not a quota in candidate or posting-entry units.
The deep-ranker audit now recomputes row counts, split identity/disjointness,
budgets, and all aggregate metrics from the raw bundle.

The first new research arm is a matched apples-to-apples comparison on the
same 38-query evaluation split used by the four-feature baseline. It includes
the deep model order, a sampled-pairwise rich posting ranker, and the exact
prefix/arbitrary-support controls imported from the canonical #388 raw bundle.
The rich ranker adds cross-route rank/presence and agreement signals to the
centroid/rank/posting-size features. It remains a research control: no
production activation, physical page, MDBX, or latency claim is made.

The second arm is an offline selective-secondary-assignment control. Documents
from primary seed 2701 may be replicated into independent seed-2702 postings
using deterministic random, boundary-closure, or disagreement policies at
1.0x/1.25x/1.5x/2.0x footprint. This is deliberately labelled a control, not
a full SPANN/SOAR implementation; canonical payloads are not duplicated.

Results and raw bundles are recorded in the corresponding JSON receipt and
outside Git under `E:\_repoz\agent-memory-workspaces`.

## Executed controls

On the 38-query research evaluation split, the rich pairwise control reached
`.9474` mean recall at 50k unique candidates (deep model order: `.9500`; the
matched prefix and arbitrary-support controls were `.9842` and `.9895`).
The result is a modest improvement over the earlier simple ranker, not a
complete LTR solution.

The selective-spill control used the frozen 1,024-address shortlist order and
replicated document IDs from seed 2701 into seed 2702. At 50k, random,
boundary, and disagreement assignment reached approximately `.9368`, `.9395`,
and `.9395` mean recall at 1.5x replication, and `.9605` at 2.0x. Because
this arm uses a bounded shortlist and only 38 queries, it is a control rather
than an authoritative full-frontier SPANN/SOAR result. Physical page bytes,
latency, and MDBX I/O remain unmeasured.
