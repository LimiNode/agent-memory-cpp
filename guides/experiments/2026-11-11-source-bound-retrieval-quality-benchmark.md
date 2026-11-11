# Source-Bound Retrieval Quality Benchmark

Status: planning proposal only. This document does not add a retrieval backend,
change production storage, or claim benchmark results.

Integration baseline: `main@f8b00005f4e94e46048786624b08584b62357815`
after #598 and #592. This is an M0 Raw Markdown research extension, not a
milestone promotion. The normative owner is
[evaluation-roadmap.md](../evaluation-roadmap.md); this note adds no public API
or persistence contract. The first executable slice targets evidence rungs 1
(contract tests) and 2 (exact logical controls). End-to-end and operational
replay claims require separate rung 4/5 evidence.

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
Even then, release-gate adoption requires a separate explicit decision.

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

Concrete reuse points are `tools/lexical-research/evaluate_lexical_quality.py`,
`MdbxPersistentLexicalCandidateAdapter::generate()`,
`admit_persistent_lexical_candidates()`, the
`mdbx_article_to_citation_vertical_test` fixture, and
`MdbxAuthoritativeArticleHydrator::hydrate()`. The persistent adapter rebuilds
an in-memory FlatLexicalIndex for a read snapshot: bounded posting and candidate
windows do not bound that reconstruction cost. Record it separately.
Admission and hydration re-read authority; lexical frontier fields alone are
not freshness proof. Hydration's final metadata observation is its
linearization point, not a promise that source state remains current forever.
Record raw candidate order and the final admitted order independently, including
`Complete`, `Truncated`, `UnsupportedQuery`, and `InconsistentSnapshot` adapter
diagnostics. Unsupported metadata filters are not an empty-result quality
success; scope tests use separately scope-bound indexes and explicit admission.

#607 is still a dependency, not an assumed capability. Its current corrective
branch must not be edited or imported into this proposal. The end-to-end lane
waits for its independently reviewed merge and records that merged revision.

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

Keep Git source commit, `SourceRevisionId`, local resource generation,
unit/projection revision, and `BlobDigest` as distinct identities. Equality of
one does not establish equality of the others. A real A@1 to A@2 mutation uses
two frozen manifests and a declared transition log, not a changed baseline.

### Acquisition stages

First retain #598's nine-document fixture as a regression control, unchanged.
For a larger pilot, propose 100 independently selected technical Markdown files
and 40 source-authored queries, with all scope-eligible query/document pairs
judged. These are acquisition targets, not acquired data or observed results.
Pin upstream commits, licenses, selection rules and exact `git show`/archive
bytes; never trust working-tree bytes merely because a commit was named.

Query families should include identifiers, paraphrases, procedures, version
exceptions, revision changes, wrong-scope and expected-empty cases. Two
assessors judge without seeing ranks; disagreements are adjudicated before
freezing. Partition by source-document family into calibration and held-out
queries before any tuning. Record inter-assessor disagreement and exclusions.
Further scaling to thousands of files is a separately costed acquisition
decision. Pooled, partially judged queries remain `incomplete_qrels`, not
full-corpus quality evidence.

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

Only complete, conflict-free, nonzero-relevance qrels participate in aggregate
candidate-relevance Recall@K or nDCG@K. Keep the #598 grade >= 2 relevance
threshold and exponential DCG gain; freeze any later change as a new profile.
Complete expected-empty queries have separate contract outcomes, not undefined
recall denominators.
The evaluator reports counts for excluded queries instead of treating them as
zero-quality observations.

Freeze the eligibility universe independently of retrieval output. Retain
document and unit mappings for multi-chunk documents; deduplicate document
rankings by first occurrence and score span/citation judgments separately.
Do not count several chunks of one document as several relevant documents.

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

Freeze K in {5, 10} and the candidate window independently. Candidate-relevance
recall is the fraction of independently judged relevant documents in top-K;
if oracle-overlap recall is measured, name it separately. Ranking consistency
means exact repeated-run ordered-ID equality within each arm under its frozen
tie rule, plus top-K overlap between arms; it is not an expectation of equal
scores from different scorers. Preserve producer scores without recomputation.

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

Report verified relevant recall using the same complete-qrels relevant-document
denominator as candidate recall, and verification success over attempted opens
separately. Failed or skipped opens must not disappear from the denominator.
Use an independent source-byte slice as the locator oracle (UTF-8 byte offsets,
including multibyte and out-of-bounds negatives), not the hydrator's own output
as its expected value. Returned-range bytes and full-object verification bytes
have separate budgets and counters.

### D. Publication visibility

Run the same query while a product publication is complete, partially staged,
recoverable after reopen, and incomplete. The evaluator must distinguish
visible, staged-but-not-visible, unavailable, and rejected states. A partial
publication must not become `Exact` merely because one row or metadata object
is present.

Assert the ingestion contract's actual visibility boundary: accepted/staged,
durable commit, index-ready, and search-visible are separate observations.
Include reopen with an absent or corrupt required row. Rejecting a partial
publication is success for that negative fixture; accepting it is failure.
Reopen is not process-kill or crash-recovery qualification. If the merged
producer exposes no suitable partial-state seam, report that scenario
`NOT_EVALUATED` and block the publication-visibility claim, not fabricate a pass.

### E. Runtime cost

Record indexed document count, candidate-window size, memory/map budget, full
CAS verification cost, and write/read counters when available. Latency claims
require an explicit independent-request protocol, warm/cold policy, query mix,
and sample definition. Repeated timing samples from one request are not
independent requests and do not justify a stable p99 claim.

The proposed cost sweep uses candidate windows {32, 128, 256}, a recorded
per-term posting budget, and two independently declared returned-range/full-CAS
byte budgets. A full-object scan budget failure is a typed unavailable result,
not a fast successful citation. Include snapshot reconstruction, admission and
hydration time in end-to-end cost, not only the posting-loop time.

## Failure taxonomy and gates

Keep independent candidate, freshness, evidence and publication outcomes per
query/item, plus run-level cost status. The following labels are evaluation
classifications, not new C++ result types or a single enum that hides failures:

| Outcome | Meaning | Aggregate quality effect |
|---|---|---|
| `VERIFIED_RELEVANT` | Candidate and source evidence agree | eligible |
| `CANDIDATE_ONLY` | Candidate exists but source evidence is unavailable | excluded from verified-evidence success; counted |
| `STALE_REJECTED` | Source revision is older than requested and was rejected | freshness diagnostic |
| `SCOPE_REJECTED` | Access scope was denied correctly | not a scope leak |
| `SCOPE_LEAK` | Result crossed the requested scope | hard failure, non-zero exit |
| `EXPECTED_EMPTY_PASS` | Frozen zero-relevant, expected-empty contract returned no results | separate contract pass |
| `FAIL_NO_ANSWER` | An expected-empty query returned a result, matching #598 semantics | hard failure, non-zero exit |
| `RELEVANT_MISS` | A relevant document was not retrieved | quality loss, not automatically a hard failure |
| `INCOMPLETE_PUBLICATION` | Publication is not complete enough for visibility | not `Exact`; counted |
| `SOURCE_CORRUPT` | Corrupt source was correctly rejected | expected negative; accepting it is a hard failure |
| `UNJUDGED` | qrels do not support a quality claim | excluded from aggregates |

The executable comparator must reject mutated receipts, source digests,
revision IDs, scope IDs, qrels, and publication completeness. Positive results
alone are insufficient evidence for any gate.

Require exit code 2 and the expected diagnostic for a policy mutation; an
unrelated exception is not a passing negative control. Integrity failures have
their own nonzero error classification. A complete expected-empty query is not
the same as an allowed but irrelevant hit in a wrong-scope test; preserve
#598's separate `PERMITTED_IRRELEVANT_HIT` diagnostic.

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

The executable lane will publish exact CLI commands with input/output paths,
seed, query order, thread count, range/full-CAS budgets and index-window
settings. Preserve raw per-query/per-stage observations and their hashes in
the experiment workspace; retain reviewable artifacts under the repository's
[research workspace policy](research-workspace-layout.md). Unavailable replay
inputs yield `PENDING_SOURCE_REPLAY`, never a substitute synthetic result.

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

Start with the existing tiny fixture, explicit source bindings, and deterministic
fault controls. The independently judged 100-file pilot and runtime-distribution
lane are follow-up batches, after that conformance slice is reviewed. No
corpus acquisition, relevance annotation, timing run or result is delivered by
this planning note.

Expected result is a reproducible separation of candidate hits from verified
current evidence, not an assumed retrieval lift. Main risks are annotation
cost, incomplete judgments, multi-chunk denominator drift, full-CAS read
amplification, and visibility seams not yet available from #607. Stop or mark
the affected dimension unavailable when those inputs are missing.

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
