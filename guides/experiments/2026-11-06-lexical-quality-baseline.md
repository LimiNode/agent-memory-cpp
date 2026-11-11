# Executable lexical quality baseline (2026-11-06)

**Status:** `EXECUTABLE_BASELINE_NOT_A_RELEASE_GATE`.

This Draft PR runs the manually authored
`independent_retrieval_quality_v1` fixture through the public
`ProjectionAwareLexicalRetriever` production path. The C++ runner imports every
fixture document through `MdbxResourceImporter`, executes every frozen query,
and records producer IDs and scores in a receipt. The Python evaluator then
computes Recall@5 and nDCG@5 only for complete judged retrieval queries.

The fixture remains independent of the retriever. `incomplete_qrels` is
excluded from aggregates; `judged_zero_relevant` is checked as a no-answer
control; actual scope leaks, permitted irrelevant hits, and no-answer failures
are reported as separate statuses. The business-niche query is marked
`NOT_AVAILABLE_EXHAUSTIVE_ENUMERATION` because a ranked top-K result cannot
prove exhaustive coverage.

The Original projection admits only source-grounded `title`, `heading`,
`body`, `code`, and `tags` fields. `summary` and `question` are derived views
and are not concatenated into Original. Documents whose `source_kind` starts
with `derived_` are excluded until a source-bound publication producer and
generation-aware admission path is available.

The stale-derived fixture is therefore reported as
`NOT_IMPLEMENTED_FRESHNESS_CONFORMANCE`; its `derived_revision` label alone is
not evidence of an admitted stale publication. Its stale-hit count is reported
as `NOT_EVALUATED`, not as a numerical zero. The separate executable
`agent_memory_mdbx_lexical_quality_freshness_conformance_test` exercises a real
source update (A@1 to A@2) through snapshot final admission and must pass
before stale-rejection claims are made.

Access control has a separate strict conformance executable,
`agent_memory_mdbx_lexical_quality_scope_conformance_test`. It imports public
and private resources, runs a public-scope query, and exits non-zero if any
cross-scope candidate is returned. The evaluator also classifies every
scope-mismatched document as `FAIL_SCOPE_LEAK` and exits non-zero when the
receipt contains an access violation. Permitted same-scope irrelevant hits
remain informational quality warnings.

The evaluator validates receipt scope metadata and rejects duplicate or
conflicting judgments and incomplete pools declared as complete. Explicit
no-answer and scope-contract violations make the evaluator exit non-zero;
permitted same-scope irrelevant hits remain diagnostic warnings. Incomplete
qrels remain explicitly excluded from quality aggregates.

The receipt records the exact fixture SHA-256, retrieval path, query IDs,
returned document IDs and producer scores, plus the exact CI source HEAD when
run in pull-request CI. The previous receipt's metrics (Recall@5 0.9286 and
nDCG@5 0.9696) are historical evidence from the old concatenated-field
methodology and must not be compared as equivalent to the corrected run.
This baseline does not claim persistent-index parity, derived-summary
retrieval, stable p99 latency, or production quality.
