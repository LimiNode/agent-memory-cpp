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
control; wrong-scope and stale-derived hits are counted separately. The
business-niche query is marked `NOT_AVAILABLE_EXHAUSTIVE_ENUMERATION` because a
ranked top-K result cannot prove exhaustive coverage.

The receipt records the exact fixture SHA-256, retrieval path, query IDs,
returned document IDs and producer scores. It does not claim persistent-index
parity, derived-summary retrieval, stable p99 latency, or production quality.
