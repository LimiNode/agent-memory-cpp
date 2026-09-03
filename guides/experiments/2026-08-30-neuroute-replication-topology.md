# NeuRoute document-level replication-topology diagnostic

## Context

- Date: 2026-08-30
- PR: stacked on the decoupled relevance/cost study
- Status: full DE-1M measurement and independent topology/result replay complete

## Question

Can one query-independent secondary address per document improve frozen K8
prototype routing under the same unique-candidate work, and how much headroom
would ideal query-specific placement expose?

## Protocol

The DE-1M 16-bit primary partition, three seeds, exact K8 prototype scoring,
top-1024 address order, `.005` hard unique-candidate budget, and Hamming768 ->
ADC64 -> exact-E5 cascade are frozen. No learned query reranker is used.

Four physical/diagnostic replication treatments are compared with the original
single assignment:

- nearest semantic secondary: for every document, choose the best occupied
  one-bit-neighbor address centroid;
- SOAR-style complementary secondary: semantic similarity minus `.25` times
  the absolute primary/secondary residual correlation;
- training-fitted complementary: consider up to four secondary addresses from
  discounted exact-top10 address co-occurrence on all 8,141 training queries,
  with the SOAR choice as fallback;
- privileged per-query ceiling: expose each query's exact top10 documents in
  already selected control topology work. This is diagnostic and cannot be a
  physical/global assignment.

Each deployable replication is query-independent, maps all 1M documents to a
secondary address different from the primary, and creates exactly 2M raw
postings. K8 is rebuilt from the resulting posting lists. Candidate counts are
the deduplicated union of document IDs, never the sum of posting lengths. All
nine deployable mappings are frozen before configuration/internal evaluation.
Training-fitted assignments never read either held-out partition.

## Decision rule

A deployable topology passes only if every seed improves actionable gain by at
least `.02` over single assignment while remaining at or below `.005` unique
candidates. The privileged treatment is excluded from this gate. Teacher-aware
K8, learned reranking, native activation, and production selection are outside
this experiment.

## Results

Three-seed internal means are:

| Treatment | Storage | Candidate fraction | Opened addresses | Static gain | Actionable gain | Exact nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|
| Single assignment | 1.0x | .004999 | 243.5 | .8243 | .8126 | .6154 |
| Nearest semantic secondary | 2.0x | .005000 | 151.2 | .7981 | .7859 | .6076 |
| SOAR complementary secondary | 2.0x | .004999 | 155.2 | .8035 | .7902 | .6067 |
| Training-fitted complementary | 2.0x | .005000 | 120.3 | .7694 | .7596 | .5874 |
| Privileged per-query ceiling | diagnostic 2.0x | .004991 | 243.1 | 1.0000 | .9733 | .6610 |

No global topology passes. Nearest semantic replication loses `.0267`
actionable gain on average; SOAR loses `.0224`; training-fitted co-occurrence
loses `.0531`. Every actionable delta is negative in every seed. SOAR is
slightly better than nearest on actionable gain but not exact nDCG. The
training-fitted treatment changes roughly 292k-298k documents away from the
SOAR fallback and performs worst, so the tested top10 co-occurrence prior does
not generalize into useful physical placement.

The mechanism is visible in work, not just quality. With the same 5k unique
candidate budget, replicated postings make each opened address heavier. The
prototype order can open only about 151-155 addresses for nearest/SOAR and 120
for training-fitted, versus 244 in the control. The extra semantic paths do not
recover enough new relevant documents to offset that lost address breadth.

The privileged ceiling is nevertheless very high: `.9733` actionable and
`.6610` nDCG. Therefore the negative result is not evidence that replication is
intrinsically useless. It establishes a sharper boundary:

> One document-level copy can nearly eliminate routing loss if placed with
> query-specific knowledge, but nearest-centroid, residual-decorrelated, and
> training top10-co-occurrence global placements all spend the 2x storage and
> posting mass in the wrong locations under a fixed candidate budget.

Result SHA-256 is
`8464c36c69579e79ba409c9d2a48633d0e0f7b6c84e331d9ce9ac5afe5b4be07`.
Independent replay regenerated all nine 1M-entry mapping artifacts with
identical SHA-256 values and reproduced the complete result byte for byte.
Evidence SHA-256 is
`3d1dcc7835c8587c0fe4772e44436aa046d9d9e5ac709656627838414adbe630`.

## Limitations

- Nearest/SOAR candidates are occupied one-bit neighbors, not an exhaustive
  search over all 65,536 addresses.
- The SOAR-style residual objective is a deterministic diagnostic adaptation,
  not a claim of bit-identical parity with a specific external implementation.
- Training-fitted assignment uses top10 address co-occurrence and at most four
  candidates per primary address; it is not a high-capacity learned document
  placement model.
- K8 remains teacher-blind. Jointly changing replication and representative
  geometry was deliberately forbidden so the topology effect stayed isolated.
- The privileged ceiling is per-query and cannot be materialized globally; its
  2x label means one possible secondary copy per document, not a deployable
  assignment recipe.
- Materialization cost is offline evidence and carries no runtime latency claim.

## Interpretation and next boundary

The approved replication branch is now closed for these three global recipes.
It should not be activated or used to justify 2x posting storage. A future
replication study would need a genuinely learned per-document placement
objective that approximates the privileged query distribution while explicitly
penalizing posting concentration. It must remain separate from teacher-aware
K8 so placement and representative geometry are not changed simultaneously.

Together with the negative decoupled-policy result, this shifts the next
research choice back to a teacher-trained/full-resolution address
representation or to a new document-placement objective, not another scalar
cost heuristic, ridge/listwise target variant, or nearest-centroid spill.
