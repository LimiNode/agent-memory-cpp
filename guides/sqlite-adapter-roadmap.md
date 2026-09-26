# SQLite Adapter Roadmap

Status: **Roadmap only**. SQLite is an optional portable storage adapter; this
guide does not replace the canonical storage contracts or the MDBX profile.

## Position in the architecture

SQLite is useful where a single portable file, FTS5/BM25, temporary candidate
tables, or broad tooling compatibility matters. MDBX remains an optional
backend for workloads that need its transaction and layout characteristics.
Neither backend owns retrieval policy, embeddings, or semantic model calls.

```text
storage contracts
  +-- MDBX adapter (existing/optional)
  `-- SQLite adapter (planned/optional)
       +-- documents, chunks, metadata
       +-- resource manifests
       +-- FTS5/BM25 projections
       +-- semantic-result derived records
       `-- temporary candidate tables
```

The vector index remains a separate concern. SQLite may store embedding BLOBs
or metadata, but it is not automatically a vector database and must not replace
the exact/ANN index contracts.

## Planned adapter surface

The first implementation may provide adapters such as:

```text
src/agent_memory/infrastructure/sqlite/
  SqliteDocumentStorage.hpp/.cpp
  SqliteResourceManifestStorage.hpp/.cpp
  SqliteResourceIndexRecordOwnerStorage.hpp/.cpp
```

These names are planning targets only. They must implement existing
dependency-free contracts and preserve scope, revision, provenance, and
targeted-reindex semantics. No SQLite type should appear in core headers.

## Storage responsibilities

Candidate rows should contain stable ids, scope, current revision, source
references, and the projection generation used to produce the row. FTS5/BM25
is a derived projection and can be rebuilt from canonical documents/chunks.
Semantic decisions are also derived records and must carry the provenance
fields defined in [`semantic-execution-roadmap.md`](semantic-execution-roadmap.md).

Do not invoke a network-backed LLM from a SQLite user-defined function or from
inside a write transaction. The semantic executor reads a bounded candidate
batch, calls the backend, and writes results in a separate controlled operation.

## Build and maturity gates

SQLite must be behind a future option such as `AGENT_MEMORY_ENABLE_SQLITE`,
default OFF until the adapter has contract, reopen, crash, migration, and
resource-reindex tests. The adapter is not an M0 requirement. A future M1/M2
profile may select it explicitly and must publish the same benchmark manifest
as MDBX comparisons (dataset hash, cache state, p50/p95/p99, memory/map size,
and write amplification where relevant).
