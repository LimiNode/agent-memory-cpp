# Independent retrieval-quality fixture (2026-11-06)

**Status:** `FIXTURE_READY_NOT_EVALUATED`.

This Draft PR supplies a small, self-contained synthetic knowledge-base fixture
for product-usefulness evaluation. Documents cover Habr-style technical
articles, Telegram tool/service and business-niche posts, a timed transcript,
tables, code, headings, summaries, source revisions and a stale derived view.
The fixture is intentionally synthetic and is not evidence about the external
world.

Queries are manually authored and independent of any retriever. They cover
exact terminology, synonym/paraphrase, document and section topics, business
niches, tools/services, procedures, cross-document comparison, stale derived
knowledge, no-answer and wrong-scope controls. The business-niche query has an
explicit exhaustive enumeration contract; a ranked top-K result alone is not
treated as exhaustive.

Each query declares an explicit `eligible_document_ids` pool. The validator
requires an explicit grade for every eligible ID in `complete_qrels` and
`judged_zero_relevant` states; absent grades remain `unjudged` and are never
silently converted to zero. `incomplete_qrels` exposes its unjudged count and
is excluded from aggregates. Wrong-scope documents are kept out of qrels and
are represented by a separate hard-filter contract. Stale-derived rejection
and legitimate no-answer outcomes are also separate contracts.

The business-niche query declares complete source coverage and validates the
exact expected item enumeration. A ranked hit for the source document alone
does not satisfy that contract. No qrels were generated from model scores or
from the implementation under test.

The fixture is a correctness/evaluation input, not a quality result. Recall@K,
nDCG@K and no-answer behavior remain unmeasured until an independent run is
performed against an actual retriever. The validator only checks identity,
manual protocol, closed-world pool coverage, duplicate rejection, hard-filter
and stale-hit contracts, exhaustive-query metadata and grade range.
