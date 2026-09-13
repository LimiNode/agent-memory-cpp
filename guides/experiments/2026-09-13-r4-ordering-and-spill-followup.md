# R4 ordering and selective-spill follow-up (2026-09-13)

This follow-up implements the corrective controls requested by the review of
#390. The former `equal_quota` label is replaced by the more precise
`posting_round_robin`; it is not a quota in candidate or posting-entry units.
The deep-ranker audit now recomputes row counts, split identity/disjointness,
budgets, and all aggregate metrics from the raw bundle.

The first new research arm is a matched apples-to-apples comparison on the
same 38-query evaluation split used by the four-feature baseline. It includes
the deep model order, a sampled pointwise logistic posting baseline, and the
exact prefix/arbitrary-support controls imported from the canonical #388 raw
bundle. Cross-route features are aligned through the representative document
identity of each deep posting; row numbers from independent partitions are not
treated as shared coordinates. This remains a research control: no production
activation, physical page, MDBX, or latency claim is made.

The second arm is an offline cross-seed replication control on held-out query
IDs 114..151. Documents from primary seed 2701 may be replicated into
independent seed-2702 postings using deterministic random or address-index
proxy policies at 1.0x/1.25x/1.5x/2.0x footprint. The two route streams are
restored with their model-ranked order. This is deliberately labelled a
cross-seed control, not a SPANN closure or SOAR residual assignment;
row-index disagreement is not a geometric boundary metric and canonical
payloads are not duplicated.

Results and raw bundles are recorded in the corresponding JSON receipt and
outside Git under `E:\_repoz\agent-memory-workspaces`.

## Executed controls

The previous `.9474` rich-pairwise receipt is superseded: it used misaligned
cross-route row IDs and was not a valid pairwise ranker. The corrected arm is
the sampled pointwise logistic baseline, with its result recorded in the
versioned `v2` receipt after replay. It must not be described as pairwise,
listwise, or as an exhaustive LTR result. The matched prefix and
arbitrary-support controls remain `.9842` and `.9895` on this 38-query split;
the latter is not the canonical 152-query topology gate.

The corrected replay currently reports `.9526 @ 50k` and `.9789 @ 100k` for
the pointwise baseline, versus `.9500` and `.9789` for the restored deep model
order. These numbers remain split-local diagnostics, not a canonical 152-query
claim.

The superseded selective-spill receipt used query IDs 0..37 and raw shortlist
order; its `.9368-.9605` values are not evidence. The corrected cross-seed
replication receipt uses held-out IDs 114..151, model-ranked streams, and
explicitly reports posting entries, unique candidates, and replication. It is
still only a control rather than an authoritative SPANN/SOAR result. Physical
page bytes, latency, and MDBX I/O remain unmeasured. The canonical topology
support gate remains `.9961` on all 152 queries; `.9895` on the matched split
is retained only as a diagnostic ceiling.
