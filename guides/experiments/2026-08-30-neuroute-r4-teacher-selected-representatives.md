# NeuRoute R4 teacher-selected actual-document representatives

## Context

- Date: 2026-08-30
- PR: stacked on the R4 fine-grained actual-document interaction study
- Status: full DE-1M measurement and independent artifact/model/result replay complete

## Question

Can a query-independent K32 set selected from training-only exact-E5 teacher
support improve the strong deterministic actual-document frontier while keeping
the partition, shortlist, scorer, candidate mass, and cascade fixed?

## Frozen protocol

The parent-selected `actual_k32_learned_top8` scorer architecture, current-K8
exact top-1024 shortlist, 16-bit document partition, 8,141 training queries,
three route seeds, optimizer/model seeds, and Hamming768 -> ADC64 -> exact-E5
cascade are unchanged. Evaluation uses strict `.003`, `.004`, and `.005`
unique candidate fractions.

Representative selection reads only the frozen training shortlists and static
exact-E5 gain-density targets. For each training query, positive-address target
weights are normalized to unit mass. Within each positive posting, the four
actual documents with highest exact query cosine receive normalized discounted
rank support. Each address then selects up to 32 documents by descending
accumulated support, breaking ties by lowest global document position. Unfilled
slots retain the deterministic R4 K32 prefix order.

The selected positions are materialized before configuration evaluation and
remain query-independent at runtime. Configuration and internal query labels
are forbidden from materialization. All three models are serialized before the
configuration split opens; internal evaluation opens only after configuration
replay.

## Materialization audit

| Seed | Positive query/address pairs | Supported docs | Supported addresses | Selected supported slots | Slots outside deterministic K32 |
|---:|---:|---:|---:|---:|---:|
| 2026082701 | 57,333 | 128,269 | 20,319 | 127,894 | 11,694 |
| 2026082702 | 57,523 | 128,989 | 20,638 | 128,665 | 12,368 |
| 2026082703 | 56,631 | 129,191 | 20,532 | 128,818 | 11,729 |

Every representative is unique within its address and belongs to its frozen
posting list. Normalized support sums to the number of training queries with a
nonzero target: 8,133, 8,133, and 8,131. Zero configuration/internal queries
participate in selection.

## Results

At the strict `.005` frontier, three-seed means are:

| Partition | Treatment | Candidate fraction | Actionable gain | Exact nDCG@10 |
|---|---|---:|---:|---:|
| Configuration | Deterministic K32 | .004976 | .8722 | .6173 |
| Configuration | Teacher-selected K32 | .004984 | .8523 | .6125 |
| Internal | Deterministic K32 | .004979 | .9003 | .6501 |
| Internal | Teacher-selected K32 | .004985 | .8845 | .6471 |

Teacher selection lowers internal actionable gain on every seed by `.0185`,
`.0097`, and `.0192`. Exact nDCG changes by `-.0015`, `+.0003`, and `-.0076`.
Configuration already shows the same actionable-gain sign on all seeds. Both
the direct and selection-progress gates therefore fail.

This is a real intervention rather than a no-op: roughly 11.7--12.4 thousand
selected slots per seed are documents not present in deterministic K32, while
candidate mass remains effectively matched. The narrow result is:

> Training-only accumulated per-document top-four wins do not improve the
> query-independent K32 set and consistently damage its actionable coverage.

Result SHA-256 is
`025a0c4ced56232a2189964a103f43742170bdde040a473cf8ca788e7bf83688`.
An independent replay regenerated all 12 selection artifacts, all three model
archives, and the full canonical result byte for byte. Evidence SHA-256 is
`41998c9613591e8cd7389228398fc47847c17d17a5fd96ba2fcbd35262a16b2c`.

## Interpretation

The positive parent result and this negative result are compatible. Exact
actual-document interactions are highly informative, but choosing a universal
set by summing independent local wins overfits frequent training modes and can
remove the query-independent geometric diversity supplied by farthest-first
K32. A useful teacher-trained selector likely needs an explicit set-coverage or
redundancy objective rather than independent document support.

## Limitations

- This rejects one normalized top-four weighted-win selector, not every
  teacher-trained or full-resolution representative model.
- The selector does not optimize marginal coverage conditional on documents
  already selected for the address.
- It does not reserve a fixed diversity quota before teacher-supported slots,
  learn a projection, or distill a query-conditioned set encoder.
- Exact materialization and interaction construction are offline diagnostics;
  no native latency or storage-layout claim is made.
- The result is frozen to DE-1M and licenses neither native confirmation nor
  production selection.

## Follow-up

Do not continue with another independent support heuristic. If this branch is
reopened, the next informative selector is diversity-aware teacher coverage:
greedily maximize held-out training gain conditional on the documents already
selected, with an explicit deterministic/farthest-first quota and a separately
sealed validation split. Otherwise retain deterministic K32 as the current
R4 representative recipe and move engineering work to exact coarse-retrieval
latency and native storage only after review of the full research stack.
