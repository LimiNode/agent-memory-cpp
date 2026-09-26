# External vector benchmark and semantic fallback reference (2026-09-27)

## Sources reviewed

- Habr: [Сравнение Tarantool, USearch, Qdrant и pgvector](https://habr.com/ru/companies/vktech/articles/1080978/).
- Public benchmark snapshot reviewed at commit `4466dfc9e870c6d4fd99977ddc8f7f428bcbf13`.
- Habr: [Sber operational agent pattern](https://habr.com/ru/companies/sberbank/articles/1086740/).

## Engineering conclusions

The articles contribute protocol and boundary lessons, not transferable
quality or latency numbers. The vector benchmark reports throughput and load
times without numeric recall. Its pinned public branch has no committed
experiment configuration or result bundle that reconstructs the published
run. A shared notion of `batch` also masks different physical operations:
per-record inserts, one client batch upsert, and bulk `COPY` followed by index
creation are not the same write path. The public Tarantool adapter does not
visibly reproduce the article's explicit `iterator='neighbor', limit=10` query
form, so that search path cannot be reconstructed from the checked source
alone. These facts are reproducibility limits, not accusations of incorrectness.

Our external benchmark protocol therefore requires exact-oracle quality,
quality/latency curves, machine-readable hardware/thread/config provenance,
and separate search-kernel, embedded-retrieval and client/server layers.
Ingestion reports `accepted`, `durable_commit`, `index_ready` and
`search_visible` timestamps separately. Concurrent search/update/delete/rebuild
is a correctness scenario with stale-generation and deletion-resurrection
checks, not merely a throughput test.

The Sber operational pattern reinforces a second boundary: deterministic rules
run first; a bounded semantic provider is a fallback. The host owns model
calls, cache, retry and human review. Provider output is typed as
`completed`, `unknown`, `failed` or `needs_review`; a model never directly
executes SSH, SQL, service-control or other host actions. Every attempt binds
an idempotency key, execution epoch, deadline, retry count, model/prompt
revision and source revisions. This is a planned host integration, not a core
library dependency.

## Roadmap impact

| Milestone | Dependency | Minimal check | Acceptance | Risk |
| --- | --- | --- | --- | --- |
| M1/M2 evaluation | Exact oracle and manifest writer | One embedded-vs-external fixture | Separate Recall@K/nDCG and all four visibility timestamps are present | False parity from hidden I/O semantics |
| M2 host fallback | External provider adapter and host job store | deterministic miss followed by one bounded fallback attempt | budget exhaustion, typed outcome, revision-safe cache and no call inside core transaction | unbounded cost or stale semantic result |
| M2 lifecycle | durable queue and generation-aware indexes | search concurrent with update/delete/rebuild | p95/p99, backlog, visibility lag and no stale resurrection | measuring only ACK or throughput |

No production backend or provider is selected by these references. Existing
compressed-native codec work remains the current implementation priority.

The storage roadmap keeps three explicit deployment profiles: first-party
embedded MDBX/native storage, a hybrid profile with MDBX canonical data plus a
derived external ANN index, and a future host-managed profile with an explicit
canonical-storage adapter. Core lifecycle, revision, provenance and generation
contracts must remain backend-independent; no distributed profile may assume a
cross-store transaction.
