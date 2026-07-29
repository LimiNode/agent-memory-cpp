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

## Integration Boundary

The host may receive a context fingerprint plus durable evidence identifiers
from its retrieval adapter. It must revalidate a cache hit against the current
canonical revisions before reuse. The embedded library remains usable without
an LLM, a provider SDK or any cache implementation.
