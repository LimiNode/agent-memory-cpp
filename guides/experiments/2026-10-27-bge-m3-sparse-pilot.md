# BGE-M3 sparse pilot execution (2026-10-27)

**Status:** `INSUFFICIENT_EVIDENCE`

This Draft PR adds an executable optional pilot. It does not add a production
writer, C++ model runtime, MDBX layout, migration, or required ML dependency.

## Current execution

The exact BGE-M3 companion files were downloaded at revision
`5617a9f61b028005a4858fdac845db406aefb181` into an ignored local cache. The
checkpoint remains outside Git. The runner verifies sizes and SHA-256 values
before model construction.

No permission-compatible English/Russian/code corpus, frozen query set, or
independently judged qrels has been supplied. The run therefore stops before
inference and reports no ranking, quality, or latency result. Verified files
are not marked loaded unless model construction and qualified inference both
succeed.

The corrective runner distinguishes missing evidence, artifact failure,
dependency failure, inference failure, scorer-parity failure, and completed
evaluation. Only completed evaluation may apply the preregistered decision
policy. An underpowered cohort remains `INSUFFICIENT_EVIDENCE`; it is not
silently converted to `NO_GO`.

## Required data contract

`corpus.json` contains a revision and `documents[]` with unique `id`, `text`,
and `language`. `queries.json` contains a revision and `queries[]` with unique
`id`, `text`, `language`, and `answer_mode`. `qrels.json` contains a revision,
unique query/document integer grades, and independent human-judgment
provenance. All three `en`/`ru`/`code` slices need a positive judged query, and
at least one no-answer query is required. No-answer queries must have no qrels.

The input files and policy are hashed in the receipt. The policy also carries
an explicit registration manifest binding its source repository, commit, path
and digest of the policy body. Admission resolves that commit/path through the
local immutable Git object database and compares the registered policy body
with the submitted body; `registered_before_evaluation: true` by itself is not
sufficient. The example policy is frozen in the repository before any future
evaluation; changing thresholds is a new policy revision, not a post-hoc
reinterpretation of results.

## Scoring and measurement contract

The runner rejects truncated or expanded inference output, out-of-vocabulary
token IDs, duplicate IDs, empty sparse maps and invalid weights before
indexing. One posting kernel computes query-weight × document-weight scores and
is checked against FlagEmbedding's `compute_lexical_matching_score`. BM25 uses
the same corpus, queries, qrels and cutoff, with document statistics built once.

Artifact verification, model loading, document encoding, batched query
encoding, sparse/BM25 index construction, posting traversal, ranking,
post-encoding retrieval and BM25 query time are separate phases. Actual
end-to-end per-query latency is unavailable until query encoding is measured
per query. Samples are directional observations over the frozen query set, not
independent repeated queries.

Recall@50 and nDCG@10 are emitted only when the retrieval depth supports them.
MRR is explicitly cut off at the configured limit. Non-empty results for
no-answer queries are a diagnostic, not calibrated abstention accuracy.

The only decisions are `GO_TO_LARGER_EVALUATION`, `NO_GO`, and
`INSUFFICIENT_EVIDENCE`. A completed receipt may legitimately carry
`INSUFFICIENT_EVIDENCE` when the qualified cohort or retrieval cutoff is
insufficient; execution failures remain separate non-completed statuses.
Completed receipt admission reloads the frozen corpus, queries and qrels from
the paths bound by their SHA-256 values, validates the ranked-hit replay bundle,
recomputes both arms' metrics and then checks the policy-derived decision. An
empty quality object, forged verdict, metric-only tampering, non-finite sparse
score or malformed qrel identity is rejected. Routine CI runs 21 dependency-free negative controls
covering scoring, zero overlap and ties, token/weight schema, short and long
inference output, identity/qrels/language completeness, corrupt artifact
evidence, insufficient top-K coverage, false GO/NO-GO receipts, invalid policy
registration, completed decision states, replay tampering and fail-closed
receipt publication. Full model inference remains an external research run.
