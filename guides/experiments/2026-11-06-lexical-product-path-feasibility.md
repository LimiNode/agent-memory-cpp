# Lexical product-path feasibility and scalability gate (2026-11-06)

**Status:** `FEASIBILITY_GATE` — this study defines a reproducible comparison
and acceptance contract. It does not activate a persistent or segmented index,
change production defaults, or claim retrieval quality.

## Question and baseline

The current product path is `ProjectionAwareLexicalRetriever::retrieve()`:
it admits a scope and revision, reads current projections, tokenizes the
eligible documents, rebuilds an `ExactLexicalIndex`, scores the query, and
materializes results. The proposed path is persistent candidate generation
followed by the same admission, freshness, scope and result checks. The two
paths must use identical `ScopeId`, `KnowledgeUnitId`, projection kind,
resource generation, unit revision, manifest revision, and stale-derived
rejection rules.

PR #578 is retained as the bounded baseline: its 48-document-per-scope smoke
fixture found a descriptive public-query distribution and measured persistent
publication separately. Its persistent build samples are not query latency and
are not a quality result. This follow-up adds scale/lifecycle coverage rather
than another isolated microbenchmark.

The typed revision-bound retrieval contract from #579 is now present on
`main`. This protocol records the required snapshot/freshness checks, but does
not mark them executed for a persistent candidate path; the executable smoke
qualification is tracked separately in #587 and must provide the receipts.

## Frozen fixture and scale plan

The machine-readable fixture is
`tools/lexical-research/fixtures/lexical_product_path_feasibility.json`.
It names the pinned R3A corpus (`1,889` documents,
`473398540b16e43b2fdb2a502b8b2e459e9cc5641f2393e080a825e39f9c4dfe`), while
keeping quality status `JUDGMENT_READINESS_ONLY` because qrels are absent. The
scale ladder is synthetic expansion of real R3A document-size statistics, not
a claim that all levels are already measured. Each scale row is explicitly
`DESIGNED_NOT_MEASURED` (or `IDENTITY_ONLY_NOT_MEASURED` for the R3A identity
row): these are workload definitions, not execution receipts.

| Level | Documents | Scopes | Units/scope | Revisions | Projections |
| --- | ---: | ---: | ---: | ---: | --- |
| smoke | 96 | 2 | 48 | 1 | original |
| small | 512 | 4 | 128 | 2 | original, title |
| medium | 2,048 | 8 | 256 | 3 | original, title, heading, body, code, tags, summary, question |
| R3A replay | 1,889 | manifest-derived | manifest-derived | source revisions | fields assigned by frozen extractor; no qrels |

Each generated document records byte size, field byte sizes, scope, resource
generation, unit revision, projection revision, and manifest revision. Updates
replace a subset of resources and projections; deletes retire units; rebuild
recreates the derived representation; reopen closes and reopens MDBX. A stale
projection, wrong generation, wrong digest, or cross-scope row must be rejected
and must not enter current results.

## Lifecycle measurements

Measure these as separate samples, never as one blended number:

1. import (source/body and canonical admission);
2. initial index publication/build;
3. product query (warm and cold reopen variants);
4. targeted update and stale-row rejection;
5. delete and tombstone/retirement handling;
6. full rebuild;
7. close/reopen and current-read validation.

For every operation record median and p95 over repeated invocations, sample
count, wall-clock method, peak RSS or allocator bytes, MDBX map size/growth,
rows and bytes read/written, and update fan-out. A repeated invocation in one
process is a measured sample distribution, not independent requests and not a
stable p99/capacity claim. Build/publication time must never be compared
directly with query latency.

The receipt must include both implementations' stage names and admission
inputs, so a speedup cannot be obtained by skipping freshness, scope or
revision checks. If the persistent candidate generator cannot expose an
equivalent product query contract, its result is `INCOMPARABLE`, not a speedup.

## Candidate-generation proposal

1. Keep `ProjectionAwareLexicalRetriever` as the correctness oracle and
   production default.
2. Add a small adapter only if it consumes the existing admission and
   revision-bound contracts; it must not duplicate first-agent snapshot or
   canonical/CAS admission logic.
3. Persist postings keyed by scope, projection, token, unit and generation;
   persist a manifest binding projection revision, resource generation and body
   digest. Candidate reads must be bounded by the requested scope and current
   manifest, then use the existing result materializer.
4. Compare candidate IDs and final results against the rebuild oracle for every
   lifecycle step. Missing, stale, mixed-scope or mismatched-digest evidence
   fails closed.
5. Keep title/heading/body/code/tags/summary/question as independent fields;
   report per-field bytes, postings and candidate contribution rather than
   collapsing them into a single body token stream.

No adapter is implemented in this PR: the current public storage contracts do
not yet provide a non-duplicating, product-level candidate-generation seam.

## Quality and stop rules

Scaling measurements alone do not prove retrieval quality. The R3A corpus has
no qrels, so quality claims remain `NOT VERIFIED`. A future quality run must
use independently authored qrels/oracle, frozen query/pool manifests, explicit
`unjudged`/`incomplete_qrels`/`judged_zero_relevant` states, and the same query
and admission rules for both paths. Stop the study if corpus digest,
revision/freshness binding, scope isolation, or lifecycle parity fails.

## Feasibility decision

| Gate | Status | Required evidence |
| --- | --- | --- |
| End-to-end stage model | SPECIFIED | Separate import, query, update, delete, rebuild and reopen rows in fixture/schema; no lifecycle run is attached yet. |
| Representative scale fixture | DESIGNED / NOT MEASURED | Smoke/small/medium ladder plus pinned R3A identity; generated workloads and receipts are future deliverables. |
| Identical admission/query semantics | SPECIFIED / NOT VERIFIED | The required contract is frozen, but two executable implementations have not been compared. |
| Read/write cost accounting | NOT MEASURED | Requires an executable adapter with MDBX row/byte counters and peak-memory hooks. |
| Persistent-vs-rebuild equivalence | NOT VERIFIED | No production-level candidate-generation seam exists without duplicating admission. |
| Retrieval quality | NOT VERIFIED | R3A qrels are absent; no quality aggregate is allowed. |
| Production activation | BLOCKED | Keep rebuild path/defaults; do not enable persistent or segmented indexing. |
