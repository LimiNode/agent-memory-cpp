# Lexical Retrieval Qualification R3: Representative Evaluation Plan

Status: Draft protocol only. No corpus, qrels, quality result, or production
backend decision is established by this note.

This plan follows the matched C++ correctness smoke in R2. R2 showed that the
in-memory BM25 baseline, flat-v2, and segmented-v2 can be exercised against
the same frozen inputs; it intentionally reported `QUALITY_INCONCLUSIVE`.
R3 is the next bounded step: acquire a permitted representative corpus and
independent relevance judgments before making a quality or production claim.

## Question and scope

The question is whether the existing lexical retrieval paths preserve useful
retrieval quality on a corpus that is larger and more representative than the
eight-document R2 smoke fixture, while keeping lifecycle and measurement
boundaries explicit.

R3 compares only implementation paths that already exist:

* `bm25`: production `ExactLexicalIndex::search()`;
* `flat-v2`: `MdbxPersistentLexicalIndex`;
* `segmented-v2`: `MdbxSegmentedLexicalIndex`.

Flat-v2 and segmented-v2 remain physical storage layouts, not independent
ranking algorithms. Learned-sparse and BGE-M3 are not promoted or integrated
by this plan; they need a separate independent baseline and protocol.

The plan does not change production defaults, public retrieval APIs, MDBX
schema, tokenizer behavior, or publication semantics. It adds no benchmark
framework. The existing R1/R2 receipt and validator contracts are the intended
execution seam, extended only when the representative fixture requires it.

## Data acquisition gate

No data is committed until all of the following are recorded in the eventual
fixture manifest:

1. dataset name, release/version, source URL, and license or usage grant;
2. acquisition date, source revision, and a cryptographic digest for each
   corpus, query, and qrels file;
3. language and document-type coverage, including the expected C++/English/
   Russian and mixed-language slices;
4. a reproducible preparation command that does not use test qrels to select,
   deduplicate, or rewrite documents;
5. a retention decision for raw data and a compact checked-in manifest rather
   than a large generated dump.

The preferred source is a technically permitted corpus with real C++ and
Russian/English memory-like text. If no source satisfies the license and
provenance gate, R3 remains blocked and the repository continues to report
correctness/performance qualification only. Hand-authored text may be used as
a development smoke fixture, but it cannot be presented as representative
evaluation evidence.

## Corpus construction

The preparation step must be deterministic and produce immutable document IDs.
Document IDs are assigned before query or qrels work and are never derived
from relevance judgments. The corpus should include, with counts recorded in
the manifest:

* exact C++ symbols, API names, and code/configuration snippets;
* Russian morphology and mixed RU/EN terminology;
* rare factual details and identifiers;
* corrected, stale, and contradictory statements;
* hard negatives sharing vocabulary but not the requested fact;
* documents that should not match a query despite lexical overlap.

Near-duplicate documents, generated boilerplate, and source duplicates must be
identified by a deterministic normalization/digest rule. Any removal rule is
applied without inspecting test judgments. If temporal or source splits are
available, the split rule is frozen before qrels are collected.

The final manifest must state document count, language/type counts, token and
length distributions, duplicate policy, and all excluded records. A corpus
smaller than the declared minimum or missing one of the required scenario
classes is a smoke fixture, not R3 evidence.

## Query and judgment protocol

Queries are authored and frozen independently of the implementation under
test. The query set must cover:

* exact C++ symbol and API lookup;
* Russian morphology and mixed RU/EN lookup;
* rare facts and identifier-heavy requests;
* corrected versus stale information;
* contradiction and hard-negative discrimination;
* lexical-empty/no-answer requests;
* realistic memory-oriented questions with more than one relevant document.

Each query has a stable ID, text, language/scenario labels, and a declared
split. Query authors do not tune tokenization, BM25 parameters, candidate
depth, or backend-specific preprocessing against test judgments.

Judgments are collected by an independent judging pass, separate from the
implementation authors and from any tuning run. The qrels schema uses graded
relevance with an explicit rubric, for example:

* `0`: not relevant, misleading, or contradicted for the requested fact;
* `1`: weak/contextually related evidence;
* `2`: useful answer-bearing evidence;
* `3`: direct, authoritative answer-bearing evidence.

The final rubric and adjudication policy are frozen before aggregation. At
least two judgments are collected for every scored query-document pair, with a
third adjudication for disagreement. The manifest records judge IDs only as
opaque labels and records agreement statistics without exposing personal data.

The validator must reject duplicate or conflicting `(query_id, doc_id)` rows,
unknown IDs, malformed grades, and incomplete adjudication. Queries with no
judgments, incomplete qrels, or explicit zero-relevant judgments remain
separate states. Incomplete and unjudged queries are excluded from quality
aggregates; they are reported in coverage tables. A judged-zero-relevant query
is included only when the zero judgment is explicit and fully adjudicated.

## Leakage and tuning controls

The corpus, query, and qrels revisions are frozen before the evaluation run.
No test query or qrel may influence:

* tokenizer or normalization changes;
* BM25 parameters, candidate depth, or tie-breaking;
* storage layout selection;
* corpus filtering, duplicate removal, or document sampling;
* learned-sparse/BGE-M3 model choice.

Any exploratory tuning uses a declared development split. The held-out test
split is run once the configuration is locked, with the exact configuration
and source revisions captured in the receipt. If the source does not provide
a valid held-out split, the result is labeled exploratory and cannot select a
production default.

## Measurements and acceptance gates

The existing R2 receipt remains the minimum evidence shape. R3 adds qrels
coverage and per-query graded metrics only after the qrels completeness gate
passes. For each backend, record separately:

* Recall@1/5/10, nDCG@10, and MRR on complete judged queries;
* no-answer accuracy and judged-zero-relevant false-positive rate;
* per-query ranked IDs, scores, candidate count, and qrels state;
* build, update, reopen, query, and index-footprint measurements;
* tokenizer/scorer/backend revisions and corpus/query/qrels digests.

`bm25` latency must use the production exact-search path. Durable rows must
identify whether they use an equivalent production query API or a diagnostic
snapshot scorer. If those paths are not equivalent, performance remains
`PERFORMANCE_INCONCLUSIVE`; no cross-path latency winner is reported.

Timing claims require warm-up rules, repeated observations, environment
metadata, and preserved raw outputs. `sample_count` is the number of recorded
observations, not the number of independent queries. `measured` describes the
observed sample distribution and does not by itself establish p99 stability.

Acceptance requires all of the following:

1. fixture and qrels validators pass, including mutation/negative controls;
2. no incomplete or unjudged rows enter quality aggregates;
3. ranking and score parity remain separate gates from quality metrics;
4. all three implementation paths pass lifecycle/reopen checks;
5. receipt and manifest are reproducible from the declared commit and inputs;
6. no production default, schema, or public API changes are introduced.

Failure of any gate produces a non-zero run and `QUALITY_INCONCLUSIVE` or
`PERFORMANCE_INCONCLUSIVE` as appropriate. R3 must not silently downgrade a
data or judgment failure to a missing metric.

## Evidence and follow-up

The Draft R3 PR should contain this plan, a compact manifest schema, and only
small permitted fixtures or acquisition instructions. Large corpora, raw
judgment dumps, and generated MDBX files stay outside Git and are retained
under the repository's evidence-release policy after archive validation.

The first implementation PR should stop at acquisition and validation. A
separate measurement PR may run the locked fixture. No conclusion about BM25,
flat-v2, segmented-v2, learned sparse, or BGE-M3 is allowed until the
independent qrels and completeness report are published with the receipt.
