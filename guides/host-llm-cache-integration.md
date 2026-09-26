# Host LLM Cache Integration

## Purpose

Provider prompt-prefix caches, local response caches, model KV caches and
cache-augmented generation are host-application concerns. They are not
`agent-memory-cpp` APIs, capabilities, DBIs, `MemoryStack` services or CLI
commands.

The library may supply canonical retrieval results, context fingerprints and
revision/provenance metadata. A host decides whether and how an LLM request is
cached, and is solely responsible for provider SDK types, credentials, tool
results, model compatibility, invalidation and user-visible cache policy.

## Host Contract

A host cache key must cover all response-affecting input: provider and model,
normalized prompt/context, tool definitions and results, scope/authority,
generation parameters, and the canonical resource or unit revisions used to
construct the context. A cache hit is not canonical memory and must never be
written back as a KnowledgeUnit without the ordinary provenance and curation
path.

Hosts may independently implement:

- provider prompt-prefix metadata and provider-reported token-cache metrics;
- an opt-in local response cache, disabled by default for dynamic or tool-using
  turns;
- in-process or backend-specific model KV caches;
- compiled-context packs for small, stable corpora.

All persisted host-cache data is deployment-owned. It is excluded from the
library's canonical DBI manifest and workspace backup contract unless the host
separately includes it. A host must treat source/resource revision changes,
authorization changes and tool output as invalidation inputs; TTL alone is not
a correctness guarantee.

After a successful canonical commit, a host integration may receive a
revision-change notification and invalidate its own cache. Notification delivery
and cache invalidation are non-transactional: they are outside the MDBX atomic
write group, profile signature, DBI manifest and canonical backup. Their delay
or failure must not roll back or postpone canonical memory visibility.

## Integration Boundary

The host may receive a context fingerprint plus durable evidence identifiers
from its retrieval adapter. It must revalidate a cache hit against the current
canonical revisions before reuse. The embedded library remains usable without
an LLM, a provider SDK or any cache implementation.

## Deterministic-first semantic fallback

The host may add a semantic fallback after deterministic retrieval has been
exhausted, but the fallback is a bounded adapter decision, not a library-owned
execution loop. A host policy records limits before dispatching a model request:

```text
max_candidate_count, max_context_tokens, max_model_tokens,
max_wall_time_ms, max_attempts
```

The normal order is deterministic metadata/filter/lexical/vector retrieval,
then a bounded candidate set, then an optional semantic provider. A provider
must not widen scope, bypass authorization, or execute tools because fallback
was selected. If the budget is exhausted, the host returns an explicit
degraded result rather than retrying indefinitely.

Provider output is typed and provenance-bearing rather than an opaque
instruction. The minimum host-side outcome vocabulary is `completed`,
`unknown`, `failed`, and `needs_review`; none authorizes execution without a
separate host admission decision. Every attempt records an idempotency key,
execution epoch, deadline, retry count, provider/model revision,
prompt/context fingerprint and source revision identifiers.

Response-cache entries include provider/model revision, normalized prompt and
tool schema, generation parameters, authorization scope and all source
revisions. TTL is an eviction policy, never a correctness proof. Cache hits
are revalidated against current revisions and produce the same typed
outcome/provenance record as a fresh attempt.

### Planned host integration gate

Status: `planned` (M2+/host adapter). Acceptance requires a deterministic-only
baseline, an explicitly budgeted fallback, typed outcomes, idempotent retry
evidence, revision-safe cache invalidation, and a test proving that no provider
call occurs inside a core storage transaction. This does not add SQLite, HTTP,
or an LLM dependency to the core library.
