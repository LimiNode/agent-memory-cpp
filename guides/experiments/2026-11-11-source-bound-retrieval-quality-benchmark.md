# Source-Bound Retrieval Quality Benchmark

Status: planning proposal only. This document does not add a retrieval backend,
change production storage, or claim benchmark results.

## Purpose and boundaries

The current lexical-quality baseline measures candidate ranking on a small,
source-grounded fixture. The next experiment should measure whether a retrieved
candidate can be turned into verified evidence from the source revision that
was indexed. Candidate quality and evidence quality are separate outcomes.

The benchmark must keep these paths distinct:

1. The existing in-memory `Original` retrieval path is the logical baseline.
2. The experimental persistent TF-only candidate adapter is a candidate-source
   control, not BM25 parity and not a production backend.
3. Source verification, publication visibility, and CAS reads are evaluated
   after candidate selection and must not be hidden inside a ranking metric.

The benchmark is not a release gate until the corpus, qrels, and independent
controls are frozen and an implementation PR has passed the normal review.

## Reusable components and provenance

The experiment should reuse existing artifacts rather than introduce parallel
representations:

| Component | Role in this experiment | Required provenance |
|---|---|---|
| #598 lexical-quality fixture/evaluator | Frozen query, qrels, receipt, and failure taxonomy seed | evaluator commit, fixture digest, qrels digest |
| #601 persistent lexical candidate adapter | Experimental candidate-only arm | adapter commit, index manifest, document count |
| #603 article-to-citation conformance | Locator and citation identity checks | article revision, locator format, citation digest |
| #605 authoritative hydration | Verified source-byte and artifact checks | hydration commit, CAS/object digest, verification result |
| #607 Raw Markdown Product Ingestion | Future end-to-end producer, after its own merge | source commit, manifest, publication receipt |

The benchmark runner must record the exact commit for every enabled component.
It must refuse to combine a candidate receipt with a different source or
publication manifest.

## Corpus and qrels construction

### Corpus manifest

Each corpus document is identified by a stable document ID and contains:

- source repository and pinned source commit;
- source-relative path and source byte digest;
- canonical document revision and scope ID;
- ingestion/publication generation;
- raw-unit and projection IDs;
- optional citation locators and artifact references.

The manifest is frozen before retrieval runs. A changed source byte, path,
scope, or revision creates a new corpus version; it must never silently update
an existing manifest.

### Queries and judgments

Queries are authored from the source corpus by a process that does not inspect
retrieval output. Query records include scope, intended source revision, and a
stable query ID. Judgments are made against source documents and locators, not
against candidate rankings.

The qrels file should support at least these states:

- `judged_relevant` with a graded relevance value and optional locator;
- `judged_irrelevant`;
- `unjudged`;
- `incomplete_qrels` when the candidate universe was not fully assessed;
- `judged_zero_relevant` for a complete query with no relevant source item;
- `duplicate_conflict` for contradictory judgments, which is rejected before
  aggregation.

Only complete, conflict-free qrels participate in aggregate Recall@K or nDCG@K.
The evaluator reports counts for excluded queries instead of treating them as
zero-quality observations.

### Independent controls

At minimum, every run includes controls that deliberately exercise:

- a correct source revision and a stale source revision;
- a valid CAS object and a missing or digest-mismatched object;
- complete publication and recoverable partial publication;
- public-scope access and a wrong-scope request;
- a relevant candidate and a candidate with no source evidence;
- a complete qrels query and an incomplete or conflicting qrels query.

Relevance labels must be produced before the final retrieval receipt is
accepted. Mutating a receipt, corpus digest, qrels digest, or source bytes must
make the run fail closed.

## Independent dimensions and metrics

### A. Candidate quality

For complete judged queries only, report Recall@K, nDCG@K, ranking consistency,
and query-level distributions. Relevance judgments are independent of the
retriever. The in-memory Original arm is the logical reference; the persistent
TF-only arm is reported separately and never described as BM25 parity.

### B. Source freshness

Exercise A@1 to A@2 transitions with explicit source-revision IDs. Measure:

- stale candidate rejection rate;
- admitted candidate source revision versus requested revision;
- current, stale, unavailable, and not-evaluated counts.

Synthetic freshness labels are not evidence. A freshness result requires a
real revision identity and a read or rejection decision from the source-bound
path.

### C. Evidence correctness

For every accepted evidence item, verify the exact citation locator, complete
artifact digest, and bytes read from CAS. Report separate failures for missing
source, corrupt source, invalid locator, digest mismatch, and candidate-only
results. A candidate that ranks correctly but cannot be verified is not a
verified answer.

### D. Publication visibility

Run the same query while a product publication is complete, partially staged,
recoverable after reopen, and incomplete. The evaluator must distinguish
visible, staged-but-not-visible, unavailable, and rejected states. A partial
publication must not become `Exact` merely because one row or metadata object
is present.

### E. Runtime cost

Record indexed document count, candidate-window size, memory/map budget, full
CAS verification cost, and write/read counters when available. Latency claims
require an explicit independent-request protocol, warm/cold policy, query mix,
and sample definition. Repeated timing samples from one request are not
independent requests and do not justify a stable p99 claim.

## Failure taxonomy and gates

Every query receives one primary outcome and optional diagnostic flags:

| Outcome | Meaning | Aggregate quality effect |
|---|---|---|
| `VERIFIED_RELEVANT` | Candidate and source evidence agree | eligible |
| `CANDIDATE_ONLY` | Candidate exists but source evidence is unavailable | excluded from verified-evidence success; counted |
| `STALE_REJECTED` | Source revision is older than requested and was rejected | freshness diagnostic |
| `SCOPE_REJECTED` | Access scope was denied correctly | not a scope leak |
| `SCOPE_LEAK` | Result crossed the requested scope | hard failure, non-zero exit |
| `NO_ANSWER` | Complete qrels require an answer but none was returned | hard failure, non-zero exit |
| `INCOMPLETE_PUBLICATION` | Publication is not complete enough for visibility | not `Exact`; counted |
| `SOURCE_CORRUPT` | Bytes or digest failed verification | hard evidence failure |
| `UNJUDGED` | qrels do not support a quality claim | excluded from aggregates |

The executable comparator must reject mutated receipts, source digests,
revision IDs, scope IDs, qrels, and publication completeness. Positive results
alone are insufficient evidence for any gate.

## Reproducibility contract

Every receipt includes:

- repository URL and exact source commit;
- component commits for evaluator, adapter, citation, hydration, and ingestion;
- corpus, qrels, workload, and configuration digests;
- scope, revision, publication, and CAS manifests;
- query count, complete/incomplete/excluded qrels counts;
- indexed document count and candidate window;
- build type, compiler, platform, and dependency versions;
- timing procedure, sample count semantics, and whether requests were
  independent;
- all hard-failure and diagnostic counters.

The receipt is immutable for a run. A comparator must fail when any referenced
digest or commit cannot be reproduced. It must not infer missing freshness,
citation, or latency evidence from a zero-valued counter.

## Experiment matrix

| Arm | Candidate source | Source state | Publication state | Primary question |
|---|---|---|---|---|
| A | in-memory Original | current | complete | logical candidate baseline |
| B | persistent TF-only adapter | current | complete | persistence/candidate control |
| C | A and B | stale revision | complete | stale rejection and identity |
| D | A and B | current + missing/corrupt CAS | complete | evidence verification |
| E | A and B | current | partial/reopen | publication visibility |
| F | A and B | current | complete | wrong-scope and no-answer negatives |
| G | A and B | current | complete | candidate-window and CAS cost |

Each arm uses the same frozen query and qrels files. No arm may train, tune, or
rewrite qrels using its own retrieval output.

## Minimal future implementation scope

The first implementation PR should be deliberately small:

1. Extend the existing evaluator with a source-bound manifest and digest
   verifier.
2. Add a receipt schema for revision, locator, CAS, and publication outcomes.
3. Add independent negative fixtures for stale, wrong-scope, corrupt-CAS,
   incomplete-publication, no-answer, and mutated-qrels cases.
4. Add a comparator that returns non-zero for every mandatory failure.
5. Add one C++ conformance executable for the source-bound read path and one
   reproducible Python orchestration test.

Do not add a new retrieval backend, publication coordinator, graph engine,
multimodal pipeline, or production activation in this PR. The persistent
TF-only adapter remains an explicitly experimental control until independent
quality and evidence results justify further work.

## Acceptance criteria for a future implementation PR

- Frozen corpus and qrels digests are checked at runtime.
- Query judgments are independent from retrieval receipts.
- All five dimensions produce explicit measured or `NOT_EVALUATED` statuses.
- Every mandatory gate has a passing fixture and a failing mutation control.
- Candidate quality, source freshness, evidence correctness, publication
  visibility, and runtime cost remain separately reportable.
- No BM25 parity, production-scale performance, stable p99, or release-gate
  claim is made without the corresponding measurement and receipt.
