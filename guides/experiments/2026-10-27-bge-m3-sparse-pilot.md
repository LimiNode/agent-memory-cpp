# BGE-M3 sparse pilot execution (2026-10-27)

**Status:** `INSUFFICIENT_EVIDENCE`

This Draft PR adds an executable, optional pilot runner. It does not add a
production writer, C++ model runtime, MDBX layout, or required ML dependency.

## Current execution

The exact BGE-M3 companion files were downloaded at revision
`5617a9f61b028005a4858fdac845db406aefb181` into an ignored local cache. The
existing checkpoint is retained outside Git. The runner verifies file sizes and
SHA-256 values before any model load.

The run intentionally stops before inference because no permission-compatible
English/Russian/code corpus, frozen query set, or independently judged qrels
has been supplied. No ranking, quality, or latency number is reported.

## Required data contract

`corpus.json` contains a revision and `documents[]` with unique `id`, `text`,
and `language` (`en`, `ru`, or `code`). `queries.json` contains a revision and
`queries[]` with unique `id`, `text`, `language`, and `answer_mode` (`judged`
or `no_answer`). `qrels.json` contains a revision and unique
`query_id`/`doc_id`/non-negative integer `grade` judgments. No-answer queries
must have no qrels; judged queries must have at least one qrel. The files must
be frozen and their receipt hashes retained outside Git.

## Scoring and measurement contract

The runner calls the official BGE-M3 sparse interface for both documents and
queries. It builds a weighted posting index from document token IDs, computes
query-weight × document-weight scores, and checks a bounded sample against
FlagEmbedding's `compute_lexical_matching_score`. BM25 is computed on the same
documents, queries, qrels, cutoff, and deterministic tie rule.

Artifact loading, document encoding, query encoding, index construction,
posting traversal/scoring, ranking/materialization, and end-to-end query work
are separate phases. Small samples are labelled `directional`; query samples
are observations over the frozen query set, not independent repeated queries.

The only permitted decision values are `GO_TO_LARGER_EVALUATION`, `NO_GO`, and
`INSUFFICIENT_EVIDENCE`. This current run is the last value.
