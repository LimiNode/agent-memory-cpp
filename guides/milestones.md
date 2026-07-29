# milestones.md

Normative milestone and capability manifest for `agent-memory-cpp`.

This document is the conflict resolver for roadmap scope. If another guide
claims a capability belongs to a different milestone, this file wins until the
other guide is updated. Other roadmap documents may be `Normative`,
`Informational`, `Proposal`, `Experiment`, or `Superseded`; this file is
`Normative`.

## Milestone Ladder

Every row in a "Required capabilities" table is a release gate for that
milestone. A milestone is complete only when each required capability has a
normative owner, required public API, required DBI/profile delta, correctness
tests, reopen/crash tests where storage is involved, explicit exclusions and
performance gates when latency/throughput is claimed. The default status for
rows in this file is `Planned`; implementation PRs must update status to
`Implemented` only with passing tests and diagnostics.

Dependency DAG:

```text
M0 -> M1a
M0 -> M1b
M0 -> M1c
M1a, M1b and M1c do not depend on one another unless an implementation profile
explicitly opts into a combined stack.
M1a/M1b/M1c -> M2
M2 -> M2+
```

### M0 - Lexical Document Memory

Goal: ship a small, deterministic, local document memory that can ingest raw
files and answer retrieval requests without dense embeddings, graph expansion,
background compaction, sync, or agent orchestration.

Required capabilities:

| Capability | Required API | Required DBI | Required tests | Deferred dependencies | Owner |
|---|---|---|---|---|---|
| Raw resource ingest | `IResourceStore`, `ResourceManifest`, `ResourceBodyStore` | `resource_bodies` profile delta if MDBX-backed | import UTF-8 `.md`/`.txt`/logs, stable logical ResourceId across an update, re-open, reject derived text without extractor provenance | chunked body store optional | `resource-reindexing.md`, `mdbx-containers-extension-tz.md` |
| Chunk/Note units | `IKnowledgeUnitStore::create_or_get_unit` | `knowledge_units`, `content_key_to_unit_id`, `knowledge_units_by_kind`, `chunk_payloads` | create/get/reopen, duplicate content dedupe | Fact/QA rich payloads | `knowledge-base-roadmap.md` |
| Immutable identity | `KnowledgeUnitKey`, `supersede_unit`, `update_mutable_fields` | `content_key_to_unit_id` | identity-field update rejected, mutable patch accepted | merge policy | `knowledge-units-roadmap.md` |
| Source summaries | `SourceRefSummary` inline in envelope | `knowledge_units` | imported raw unit carries a revision-bound citation preview that survives reopen and resource update | full `source_refs` DBI | `knowledge-base-roadmap.md` |
| Original projection | `IProjectionStore` | `unit_projections` | stale revision skipped, projection regenerated | QA/Summary projections | `knowledge-base-roadmap.md` |
| Lexical retrieval | `ILexicalIndex`, `LexicalRetriever` | lexical dictionary/stats/postings from TZ | BM25F over `Original`, p95 target fixture | dense, learned sparse | `lexical-search-roadmap.md` |
| Scope isolation | `ScopeId` in every secondary/range key | all secondary DBIs | cross-scope leakage tests | distributed scope routing | `memory-stacks-roadmap.md` |
| Retrieval trace | `IRetrievalTrace` minimal events | none | trace contains query, candidate counts, filters | full metrics | `knowledge-base-roadmap.md` |

M0 must not require `IEmbedder`, graph expansion, persistent runtime queue,
compaction jobs, sync, translation, bi-temporal storage, speaker memory, or
compiled wiki profiles.

M0 is text-only: it does not natively ingest PDF/DOCX/image/audio/video, run
OCR/ASR, or claim original page/frame/media citations. An externally extracted
UTF-8 text may be imported only as explicitly derived text. Non-text connectors
must use the M2 artifact provenance profile.

### M1a - Hybrid Retrieval

Goal: add optional dense retrieval and hybrid fusion on top of the M0 document
memory.

Required capabilities:

| Capability | Required API | Required DBI | Required tests | Deferred dependencies | Owner |
|---|---|---|---|---|---|
| Embedder registration | `IEmbedder` / profile validation | none | fail-fast when dense requested without embedder | bundled model runtime | `embedding.md` |
| Exact vector baseline | `IEmbeddingStore`, exact vector index | `embedding_meta`, `embedding_vectors` | recall parity with in-memory exact baseline | ANN/HNSW | `optimization-roadmap.md` |
| Hybrid fusion | `HybridRetriever`, RRF | none | hybrid Recall@10 gate vs lexical baseline | learned fusion | `knowledge-base-roadmap.md` |
| Optional text-only external vector adapter | derived projection adapter with canonical-hit hydration | none in canonical MDBX manifest | stale revision, delete propagation and quality/latency comparison against library-owned baseline | non-text/media sources | `artifact-provenance-roadmap.md` |
| Benchmark harness | eval runner fixture schema | none | reproducible warm/cold run JSON | external comparisons | `build-and-test.md` |

`BasicLexicalRag()` is the default factory. `BasicHybridRag(IEmbedder&)` or an
equivalent explicit opt-in is required for dense vectors.

### M1b - Structured Knowledge

Goal: add curated knowledge objects and single-axis temporal validity without
requiring the runtime maintenance system.

Required capabilities:

| Capability | Required API | Required DBI | Required tests | Deferred dependencies | Owner |
|---|---|---|---|---|---|
| QA units | `QAPayload`, QA retriever | `qa_payloads` | lookup, no-answer, citation fidelity | frequency ranking | `knowledge-units-roadmap.md` |
| Fact units | `FactPayload`, fact retriever | `fact_payloads` | fact lookup, supersedence | bi-temporal history | `knowledge-units-roadmap.md` |
| Operational components | `IComponentStore` | `unit_components` with physical key `(ComponentKind, UnitId)` | multiple components per unit, tag scan, stable type ids | separate typed DBIs if needed | `knowledge-base-roadmap.md`, `mdbx-containers-extension-tz.md` |
| Full source refs | `ISourceRefStore` | `source_refs` | detail citation lookup | source reverse lookup | `knowledge-base-roadmap.md` |
| Single-axis temporal validity | `TemporalComponent` | `temporal_unit_index` | valid-at lookup, stale fact exclusion | AM-13 bi-temporal | `memory-lifecycle-governance-roadmap.md` |
| Knowledge activation metadata | `ActivationMetadataComponent` or equivalent payload fields | existing `metadata_filters`, `graph_edges_by_*`, `unit_projections` | domain/intent/playbook activation fixtures | learned planner | `knowledge-activation-roadmap.md` |
| Persisted translation projection (optional) | adapter-owned `TranslationPolicy`, `TranslatedCanonical` projection and provenance | existing `unit_projections` | original citation, adapter fingerprint drift, deterministic fake translator | query routing/pivoting | `translation-adapters-roadmap.md` |

M1b may add domain maps and playbooks as `KnowledgeUnitKind` values, but they
must be canonical, versioned objects that cite evidence; they are not just
chunks from source documents.

### M1c - Runtime Maintenance

Goal: add bounded background work after identity, projections, and core indexes
are stable.

Required capabilities:

| Capability | Required API | Required DBI | Required tests | Deferred dependencies | Owner |
|---|---|---|---|---|---|
| Persistent task queue | `TaskQueue`, `JobRecord`, `TableSequence` | runtime queue DBIs from TZ | lease, cancel, priority, restart, abort | multi-node queue | `runtime-services-roadmap.md` |
| Async indexer | `IAsyncIndexer` | queue + affected indexes | bounded batch size, crash recovery | unbounded rebuild | `runtime-services-roadmap.md` |
| Basic compaction | `CompactionWorker` | queue + `compaction_handoffs` | Decay/Dedupe/ArchiveCold crash safety | merge/summary promotion | `compaction-roadmap.md` |
| Usage range indexes | decay/cooldown query contracts | `usage_by_last_access`, `usage_by_cooldown` | bounded range scans | learned decay | `policies-roadmap.md` |
| Operational limits | write transaction policy | none | max batch, max txn time, backpressure | multi-env sharding | `mdbx-containers-extension-tz.md` |

Operational limits are part of the M1c contract, not tuning notes. The runtime
must define maximum records per write transaction, maximum encoded batch bytes,
maximum foreground write transaction duration, maximum read transaction
lifetime, incremental reindex page size, compaction priority/backpressure
rules, and map growth policy. No M1c job may perform an unbounded full-index
rebuild inside one transaction.

### M2 - Advanced Profiles

Goal: broaden from the stable M1 substrate to advanced memory systems.

M2 may include graph expansion, speaker-aware chat, compiled wiki, query-time
multilingual routing/pivoting and evaluation, richer context planning, CLI, migration tools, ANN backends,
advanced mutation policies, artifact provenance profiles, and profile-specific
golden datasets. An artifact provenance profile adds stable Source/Revision
identity, immutable original bytes, versioned representations, typed evidence
locators and segment-to-Chunk materialization. It is optional for M2, but a
public non-text source connector must not bypass this contract.

### M2+ - Research / Optional

Bi-temporal storage, abstraction graphs, causal relations, entity resolution,
logical sync adoption, late interaction, learned sparse backends, and advanced
binary document encoders live here until they receive DBI budget rows,
acceptance tests, and explicit profile owners.

## Orthogonal A-Lane: Agent Runtime Integration

The A-lane is optional readiness for ADELIA-like cognitive runtimes. It does
not change M0/M1/M2 scope and is not required by ordinary RAG or document
memory profiles.

| Lane | Name | Scope | Prerequisites |
|---|---|---|---|
| A0 | Adapter prototype | `Custom` units, typed metadata, neutral runtime refs, example adapter | M0 raw/resource/projection substrate |
| A1 | Cognitive trace contracts | runtime origin, causal, perspective, epistemic components, sequence filters | M1b components and graph substrate; M2+ for bi-temporal |
| A2 | Task, Decision and Procedure | formal payloads, procedure activation, capability refs, outcome stats | M1b + activation metadata; M1c for background validation jobs |
| A3 | Partition and reconciliation | replica stamps, import/export, semantic conflicts, merge tests | M2/M2+ lifecycle governance |
| A4 | Plasticity support | procedure mining, topology mutation evidence, introspection snapshots, rollback records | external runtime policy; memory stores evidence only |

ADELIA integration is a reference adapter, not a core dependency. Core stores
durable cognitive records; the external runtime owns live cognition,
scheduling, authority and execution.

## Cross-Cutting Invariants

- One logical corpus may expose many domain views. Do not split physical
  storage by topic unless a technical isolation requirement exists.
- `KnowledgeUnitKey` is immutable. Updates that touch identity fields create a
  new unit and supersede the old one.
- Dense retrieval is optional. Profiles requesting dense retrieval fail fast if
  no embedder/vector backend is registered.
- Bi-temporal semantics are not M1. M1 temporal validity has one authoritative
  validity axis.
- Background jobs are bounded. No M1 path may rebuild an unbounded index in one
  write transaction.
- Derived search/vector/graph indexes are rebuildable from canonical storage.
- Strict filters may exclude results only for safety or tenancy: scope, access,
  status, language, jurisdiction, trust threshold. They use the deny-by-default
  `AccessPolicy` enforcement contract in `policies-roadmap.md` before candidate
  creation and again before context materialization. Domain, role, stage, topic,
  platform, and audience are soft routing signals by default.

## Reproducible Benchmark Contract

Any ship-it latency/quality claim must publish a JSON report plus a dataset
manifest/hash, hardware snapshot, compiler and build flags, cold/warm cache
procedure, query mix, index state, top-k, concurrency, lexical/dense/hybrid
mode, embedding-generation inclusion policy, filter mix, median/p95/p99,
peak RSS/map size, and write-amplification counters where writes are measured.
Cross-project comparisons, including TencentDB-Agent-Memory, Graphiti/Zep or
Mem0-style systems, require a compatibility matrix that states which workloads
and guarantees are actually comparable.

## Document Status Registry

| Guide | Status | Notes |
|---|---|---|
| `milestones.md` | Normative | Scope and ship-it conflict resolver |
| `architecture.md` | Normative, except duplicated milestone summaries | High-level boundaries |
| `memory-stacks-roadmap.md` | Normative for profiles and ADRs; milestone split delegated here | Capability model |
| `knowledge-base-roadmap.md` | Normative for retrieval/store contracts | Must follow this file for M0/M1 scope |
| `artifact-provenance-roadmap.md` | Normative for M2 artifact profiles | Required before public non-text connectors; defines a narrow M1a text-only derived-index exception |
| `knowledge-activation-roadmap.md` | Normative for activation/planning concepts; implementation staged by this file | Domain maps, playbooks, soft routing |
| `agent-runtime-integration-roadmap.md` | Proposal / A-lane | Cognitive runtime integration without core execution |
| `mdbx-containers-extension-tz.md` | Normative for physical DBI manifest and upstream primitive contracts | Must track exact upstream compatibility snapshots |
| `memory-lifecycle-governance-roadmap.md` | Proposal / M2+ | AM-13..AM-21 are not M1 scope |
| `memory-architectures-roadmap.md` | Informational | External architecture mapping |
| `usage-memory-models.md` | Informational | Usage guidance and examples |
| `advanced-binary-techniques-roadmap.md` | Experiment / M3 research | Not M0/M1/M2 ship-it scope |
