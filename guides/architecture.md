# Architecture

> Knowledge base architecture is component-based (Envelope + Components + SearchProjections), NOT a single `KnowledgeUnit` value type. См. [`guides/memory-stacks-roadmap.md`](memory-stacks-roadmap.md) для полной спецификации.

## Baseline

Use a DDD-like architecture with small domain slices and explicit dependency
boundaries. The project is a static C++17 library first; optional infrastructure
should not leak into core APIs.

## Dependency Direction

Dependencies should point inward (DDD layering):

```text
infrastructure/adapters -> storage/index/embedding implementations
retrieval/memory/context -> domain contracts and value objects
domain/core -> standard library and stable internal primitives only
```

The 4-layer model (Layer 4 applications to Layer 3 memory stacks to Layer 2
retrieval primitives to Layer 1 storage primitives) refines this layering; see
"Layer Architecture" below. Runtime services are orthogonal and may be
invoked from any layer without violating the directionality of dependencies.

Rules:

- Core and domain code must not depend on MDBX, ONNX Runtime, HTTP clients,
  Python, or other optional infrastructure.
- Storage adapters implement storage contracts; they do not define memory
  strategy semantics.
- Embedding adapters implement embedding contracts; they do not own retrieval
  ranking policy.
- Retrieval implementations may compose embedding, index, and storage
  contracts, but they must not expose backend-specific dependencies through
  public retrieval contracts.
- Context assembly consumes retrieval results and memory policies; it should not
  perform storage-specific queries directly.
- Runtime services (CompactionWorker, WriteGate, AsyncIndexer)
  are instance-owned by a `MemoryStack` when enabled, but depend only on narrow
  Layer 1 + Layer 2 facades such as `IRuntimeStorageFacade`,
  `IRuntimeProfileView` and `ICompactionWriteContext`; they never reach into a
  concrete `MemoryStack` to read mutable profile-specific internals.

## Static Library Bias

The main target is `agent_memory`, built as a static library. Prefer `.cpp`
implementation files for behavior that may depend on external libraries or
change ABI/compile-time cost. Header-only code is acceptable for small value
types, templates, and trivial inline helpers.

## Boundary Guidelines

- Put stable value objects and contracts near the domain they describe.
- Put dependency-owning implementations under `infrastructure/` or a
  dependency-specific subarea.
- Keep public APIs small and easy to inspect.
- Avoid one large facade that hides all behavior.
- Avoid speculative plugin systems before at least two real implementations
  require the same extension point.

## Agent Runtime Integration

`agent-memory-cpp` may store durable cognitive records for external runtimes:
runtime origin, local perspective, causal context, task/decision/procedure
payloads, outcomes, introspection observations and reconciliation metadata.
The external runtime owns live cognition, scheduling, focus, authority, tool
execution, topology mutation, transport and consensus.

The neutral contracts live in
[`agent-runtime-integration-roadmap.md`](agent-runtime-integration-roadmap.md).
Reference integrations such as ADELIA map their native ids to
`RuntimeObjectRef` in adapters; core headers must not include ADELIA types.

## Layer Architecture

The system is organized as four layers plus an orthogonal runtime-services
layer. The description mirrors the canonical layer diagram in
[`guides/memory-stacks-roadmap.md`](memory-stacks-roadmap.md) (section 11).

```text
Layer 4: Applications (examples/, apps/)
  ChatBotApp, WikiMaintainerApp, StreamMemoryApp, ...

Layer 3: Memory Stacks (memory/)
  MemoryStack — runtime object with handles to storage/retrieval
  MemoryProfileSpec — declarative specification

Layer 2: Retrieval Primitives (retrieval/)
  ILexicalIndex, IDenseIndex, IGraphIndex, ITemporalIndex,
  HybridRetriever, RRF, ContextBuilder, IntentRouter,
  RetrievalTrace, DecayAwareRetriever, AntiLoopCooldown

Layer 1: Storage Primitives (storage/)
  EnvelopeStore, ComponentStore, ProjectionStore,
  MDBX tables, MultiTableWriter, ReverseIndexTable,
  RangeIndexTable, TypeDiscriminatedTable, CompositeKey

Cross-cutting Runtime Services (runtime/) — orthogonal
  CompactionWorker, WriteGate, AsyncIndexer
  Use Layer 1 + Layer 2 through interfaces
```

Invariants:

- Layer 4 does not depend on Layer 1 (only through Layer 2 and Layer 3).
- Layer 3 does not depend on Layer 4.
- Layer 2 does not depend on Layer 3 (can be used directly).
- Runtime services may be invoked from any layer. They may be owned by a
  specific `MemoryStack` instance, but their implementation talks to
  `MemoryStackRuntimeFacade` / Layer 1 + Layer 2 contracts rather than concrete
  stack internals.

The layering reinforces the DDD dependency direction above: lower layers know
nothing about upper layers, and the application boundary never reaches past
Layer 2 for raw storage or index access.

## Planned Storage Direction

Storage contracts live under `src/agent_memory/storage/` and stay dependency
free. MDBX-backed storage is expected to implement those contracts with
`mdbx-containers` as an adapter detail. The upper project should keep source
dependencies flat under `external/`: `libmdbx` and `mdbx-containers` are sibling
submodules, not nested dependency checkouts. Parent projects should be able to
provide dependency targets when possible.

MDBX support is opt-in through `AGENT_MEMORY_ENABLE_MDBX`. The build must reuse
an existing `mdbx_containers::mdbx_containers` target before adding a local
source tree or falling back to `find_package(mdbx_containers)`.

The MDBX document storage adapter lives under
`src/agent_memory/infrastructure/mdbx/`. It implements `IDocumentStorage` and
must not define memory strategy, retrieval ranking, or embedding behavior.

## Planned Resource Reindexing Direction

Ingestion should eventually track resource ownership for all derived records.
Each original source item should have a stable local `ResourceId`, a current
local revision or generation, and a manifest of chunks, embeddings, vector
records, binary bucket postings, lexical postings, or graph records derived
from it. Artifact-aware profiles additionally map that local identity to a
durable `SourceId` and immutable `SourceRevisionId`; imports may remap local
ids but never rewrite those provenance identities.

This lets the system replace one markdown file, note, profile fact, or other
resource without rebuilding unrelated indexes. Resource reindexing should
compose storage, embedding, and index contracts; it should not become a large
facade that owns retrieval policy.

MDBX-backed implementations should update a resource, its manifest, and its
derived records in one writable transaction when possible. Approximate indexes
with compressed bucket lists may use stale-generation filtering or tombstones
before physical compaction.

Detailed tasks are tracked in `guides/resource-reindexing.md`.

## Planned Knowledge Base Direction

The knowledge base is the unified retrieval layer over heterogeneous
knowledge units: text chunks, QAPairs, facts, events, entities,
relations, sections, summaries, conversation episodes, and notes. It
is not a RAG library and not an LLM framework. Contracts stay
dependency-free, evaluation is a primary citizen, and adapters live
outside core.

The knowledge base data model is **component-based** — Envelope +
Components + SearchProjections. There is NO single `KnowledgeUnit`
value type and NO discriminated union of payload types in one struct.
The old `DocumentChunk` / `MemoryObject` / `chat::Message` /
`facts::Fact` value types are retired and replaced by these contracts:

- A lean `KnowledgeUnitEnvelope` (~16-field hot-path struct): id, kind,
  scope_id, primary_text, display_text, lifecycle_state, sources,
  timestamps, revision, priority_weight, supersedes/superseded_by.
  See [`guides/knowledge-units-roadmap.md`](knowledge-units-roadmap.md)
  for per-kind specifications and migration helpers.
- Typed components (operational + per-kind payloads): operational
  components `UsageStats`, `Speaker`, `Temporal`, `EmbeddingMeta`,
  `CompactionMeta`; per-kind payload components `QAPayload`,
  `FactPayload`, `ChunkPayload`, `ConversationEpisodePayload`,
  `CompiledArticlePayload`. Stored separately from the envelope.
- `SearchProjections` (multi-projection retrieval): `Original`,
  `QAQuestion`, `QAAnswer`, `QACombined`, `Summary`, `CodeSymbols`,
  `DenseContextual`, `Bm25Fields`. Each projection has its own
  payload and kind-specific indexer.
- Component-based stores: `IKnowledgeUnitStore` (envelope),
  `IComponentStore` (operational + per-kind payload components),
  `IProjectionStore` (retrieval projections), per-payload stores for
  high-volume kinds. They share a primary-table-plus-secondary-index
  pattern. The pattern is described in
  [`guides/optimization-roadmap.md`](optimization-roadmap.md) under
  "Secondary & Reverse Indexes" and in
  [`guides/knowledge-base-roadmap.md`](knowledge-base-roadmap.md)
  under "Domain Stores".
- Retrieval / index stores: `ILexicalIndex`, `IDenseIndex`,
  `IGraphIndex`, `ITemporalIndex` consumed by retriever composition.
- Context / eval / tracing contracts: `ContextBuilder`,
  `IRetrievalTrace`, `RetrievalMetrics`, `RetrievalDataset`.

Cross-cutting contracts:

- A `SourceRef` first-class provenance value type lifted into the
  domain layer. Every component / projection that needs citation,
  every `RetrievalHit`, and every retrieval trace entry references
  `SourceRef`. Artifact-aware ingestion extends this with stable source and
  revision identities, immutable artifacts, versioned representations,
  segment-backed Chunk units and typed evidence locators; see
  [`guides/artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md).
- A retriever graph built on `RetrievalPlan`, `IUnitRetriever`, and
  `RetrievalHit` with `KnowledgeUnitId` as the unified key. `ChunkId`
  is one of many unit kinds, not the only key.
- A budgeted `IContextBuilder` with per-block budgets, mandatory final
  context logging through `IRetrievalTrace`, and a deterministic
  trim order.
- An evaluation pipeline (`RetrievalTrace`, `RetrievalDataset`,
  `RetrievalMetrics`) with a golden dataset of at least 50 cases
  spanning at least three intents, and a hybrid lift target of
  `Recall@10 >= 1.20 * Recall@10(BM25-only)`.

> `KnowledgeUnitId` — monotonic opaque `uint64_t` (allocated, never
> reused). Separate `KnowledgeUnitKey` struct for content-addressing
> (dedupe, migration, supersedence).

Detailed contracts, data-model shape, per-kind field blocks, the QA
knowledge base, the temporal index, the typed graph, the typed
metadata, and the cross-encoder / HyDE / corrective RAG adapter
slots are all tracked in
[`guides/knowledge-base-roadmap.md`](knowledge-base-roadmap.md).

The knowledge base roadmap sits on top of the existing roadmaps and
does not replace them:

- Lexical retrieval contracts and the BM25 baseline remain in
  [`guides/lexical-search-roadmap.md`](lexical-search-roadmap.md); the
  knowledge base roadmap adds fielded BM25F storage on top.
- Optimization contracts (compression, binary signature index, Eigen
  rerank, secondary indexes) remain in
  [`guides/optimization-roadmap.md`](optimization-roadmap.md); the
  knowledge base roadmap references the secondary index pattern.
- Resource ownership and targeted reindexing remain in
  [`guides/resource-reindexing.md`](resource-reindexing.md); the
  knowledge base roadmap extends the manifest with new
  `DerivedRecordKind` variants for the new unit kinds.
- Optional affective-agent memory remains an overlay, not a core emotion
  runtime. [`guides/affective-memory-roadmap.md`](affective-memory-roadmap.md)
  defines how persisted appraisal, affect snapshots, goal impacts, outcomes,
  salience, relationship evidence, sensitive-inference policies, encrypted
  local persistence, and urgency-aware context planning can be added while
  keeping live affect dynamics in a sibling runtime controller.

LLM contextualization engines, cross-encoder rerank engines, concrete
translation engines, hybrid chunker implementations, ASR, VLM document parsing,
hosted vector databases, Python bindings, and agent framework bridges stay in
`adapters/` and `examples/`. Dependency-free SPI/value types such as
`ITranslationAdapter`, `CanonicalLanguageCode`, `DetectedLanguage`,
`TranslationPolicy` and
translation provenance metadata may live in the core/domain contract; concrete
provider SDKs, package managers and model runtimes do not.

The default retrieval/storage route is the library's own stores and indexes.
An external vector database, when selected by an adapter, is a derived segment
index only and does not own canonical artifact bytes, evidence anchors or
backup truth. The library's MDBX path remains the default; a text-only external
adapter is optional for M1a migration/benchmark comparison and must hydrate and
revalidate every candidate from canonical local storage.

The canonical, currently normative specification of the data model,
profiles, stacks, capability matrix, validation rules, and maturity lives in
[`guides/memory-stacks-roadmap.md`](memory-stacks-roadmap.md). The sole
canonical physical MDBX manifest and DBI budget live in
[`guides/mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md)
§5.5/§5.5.1. See also the short cross-reference in "Knowledge Base Direction
(cross-reference)" below.

## Planned Embedding Direction

Embedding contracts live under `src/agent_memory/embedding/` and stay
dependency free. Concrete providers such as `llama.cpp`, ONNX Runtime, or
OpenAI-compatible HTTP APIs must be implemented as optional adapters.

Do not fork chat/generation wrappers such as `cpp-llamalib` to make embeddings
fit them. The project should expose its own embedding contract with explicit
model metadata, dimensions, similarity metric, normalization, pooling, and
query/document purpose semantics.

## Planned Index Direction

Index contracts live under `src/agent_memory/index/` and stay dependency free.
Vector indexes store chunk embeddings and expose nearest-neighbour search by
query vector, result limit, score, and exact metadata filters. Exact in-memory,
MDBX-backed, or approximate indexes must implement these contracts without
owning retrieval ranking policy.

`ExactVectorIndex` is allowed in the index layer because it is dependency-free
and acts as the deterministic baseline for tests and small local workloads.

## Maturity Levels (M0/M1/M2)

Normative scope lives in [`milestones.md`](milestones.md). This section is a
short orientation summary only; if it conflicts with `milestones.md`, the
milestone manifest wins.

### M0 — Lexical Document Memory

- Raw resource ingest, `ResourceBodyStore`, Chunk/Note units, immutable
  content identity, inline source summaries, `SearchProjection::Original`,
  scope-aware BM25F, and a minimal retrieval trace.
- Default factory is lexical. Dense vectors, graph, compaction, sync,
  translation, speaker/chat/wiki profiles, and bi-temporal storage are not M0.

Ship-it:

- `BasicRag` retrieve+write на 10k units с p95 latency ≤ 50 ms.
- Все unit-тесты на envelope serialization проходят.
- Lifecycle FSM покрыт тестами для 4 durable states.

### M1 — Staged Production

- M1a: optional dense retrieval, exact vector baseline, explicit embedder
  registration, hybrid/RRF, and reproducible benchmark harness.
- M1b: QA/fact payloads, operational components, full source refs,
  single-axis temporal validity, and deterministic knowledge activation
  metadata.
- M1c: persistent runtime queue, bounded async indexer, basic compaction,
  usage range indexes, and write transaction limits.

Ship-it:

- Each M1 stage has profile-specific open/create/reopen tests.
- Hybrid profiles fail fast when dense is requested without an embedder.
- Temporal M1 tests cover single-axis validity and supersedence, not
  bi-temporal history.
- Runtime jobs are bounded and crash-safe before compaction is declared ready.

### M2 — Advanced

- Полный набор projection kinds (DenseContextual, Bm25Fields, CodeSymbols).
- Multi-vector embeddings (multi-model, multi-projection) с cross-model RRF.
- Compaction jobs: Merge, SummaryPromotion, EmbeddingRecompute.
- CLI tool `agent-memory-cli` (inspect / stats / check / vacuum / reindex /
  profile-info / profile-migrate).
- Distributed scope routing (multi-process / multi-host scope namespaces).
- Compaction metrics и self-tuning.
- Embedding recompute migration с progress tracking.

Ship-it:

- 50+ golden test cases покрывают все enabled intent/activation classes.
- p95 latency ≤ 2× baseline BM25 для всех профилей.
- Migration script `basic_rag → agent_ltm` работает без потери данных.
- CLI tool покрывает inspect / stats / check / vacuum / reindex /
  profile-info / profile-migrate.

## Cross-cutting Runtime Services

Per
[`guides/memory-stacks-roadmap.md`](memory-stacks-roadmap.md) ADR-013
и секция 11. Ортогональны profile: одна и та же реализация сервиса
обслуживает любой `MemoryStack` через интерфейсы Layer 1 и Layer 2.

- **Host LLM cache adapter** — outside this library. Core may return a
  provider-neutral `ContextFingerprint` / `PromptPrefixDescriptor`, while the
  host owns provider metadata, response cache safety and LLM calls.
- **CompactionWorker** (async) — выполняет `ICompactionJob`-ы:
  Decay, Dedupe, ArchiveCold (M1); Merge, SummaryPromotion,
  EmbeddingRecompute (M2). Хранит состояние jobs в downstream `TaskQueue`
  DBI (`jobs_by_id`, `jobs_scheduled`, `jobs_ready`, `jobs_by_lease`,
  `jobs_by_status`) with `JobRecord.kind = Compaction`; handoff state lives in
  `compaction_handoffs`.
- **AsyncIndexer** (background) — батчит inserts в lexical / vector /
  projection индексы. Default: 1000 units или 50 MB, whichever first.
- **WriteGate** — применяет `WritePolicy` к входящим записям:
  importance threshold, dedupe distance, supersede/merge/episode-compaction
  разрешения, flush trigger.

Доступны через интерфейсы (`ICompactionWorker`, `IAsyncIndexer`, `IWriteGate`). Сервисы не зависят от конкретного
`MemoryStack` и не имеют права лезть в profile-specific state напрямую —
только через публичные контракты.

## Knowledge Base Direction (cross-reference)

См. [`guides/memory-stacks-roadmap.md`](memory-stacks-roadmap.md) для
центральной спецификации envelope, components, projections, capability
matrix и validation rules.

Состав knowledge base:

- `KnowledgeUnitEnvelope` (~16 полей lean): id, kind, scope_id, primary_text,
  display_text, lifecycle_state, sources, timestamps, revision,
  priority_weight, supersedes/superseded_by.
- Components (operational + per-kind payloads): UsageStats, Speaker,
  Temporal, EmbeddingMeta, CompactionMeta + QAPayload, FactPayload,
  ChunkPayload, ConversationEpisodePayload, CompiledArticlePayload.
- SearchProjections (multi-projection retrieval): Original,
  DenseContextual, Bm25Fields (Body / Title / Heading / Symbols),
  QAQuestion, QAAnswer, Summary, CodeSymbols.
- 7 default MemoryStacks: `BasicRagStack`, `QAKnowledgeBaseStack`,
  `AgentLongTermMemoryStack`, `SpeakerAwareChatStack`, `CompiledWikiStack`,
  `TemporalFactStoreStack`, `FullResearchMemoryStack`.
- Policies: DecayPolicy, WritePolicy, SpeakerScopePolicy, HybridRetrievalConfig.
- Capability matrix (LexicalBm25F / DenseVectors / QAPairs / TemporalValidity /
  UsageStats / Decay / SpeakerAttribution / Compaction /
  GraphRelations / EmbeddingMigration / CompiledArticles / ConversationMemory).
- Validation rules при `MemoryStack::open()` (Decay → UsageStats и т.д.).

> `KnowledgeUnitId` — monotonic opaque `uint64_t` (allocated, never
> reused). Отдельный `KnowledgeUnitKey` используется для
> content-addressing (dedupe, migration, supersedence).

Per-kind payloads и lifecycle FSM: см.
[`guides/knowledge-units-roadmap.md`](knowledge-units-roadmap.md).
Retrieval flow и evaluation: см.
[`guides/knowledge-base-roadmap.md`](knowledge-base-roadmap.md).
Decay / Write / Speaker policies: см.
[`guides/policies-roadmap.md`](policies-roadmap.md).
`CompactionWorker` (job types, handoff): см.
[`guides/compaction-roadmap.md`](compaction-roadmap.md).
Runtime services (AsyncIndexer / WriteGate; host LLM cache boundary):
см. [`guides/runtime-services-roadmap.md`](runtime-services-roadmap.md).

See [`usage-memory-models.md`](usage-memory-models.md) for practitioner guidance on memory model choice; see [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) for the underlying comparison framework.

## Profile Migration Strategy

In-place minor upgrade (additive capability) vs major migration (новая БД).
Подробности в
[`guides/memory-stacks-roadmap.md`](memory-stacks-roadmap.md) секция 14.

Краткая сводка:

- **Minor (in-place)**: добавление одной capability к существующему
  профилю. Новая DBI создаётся, существующие данные не трогаются,
  `profile_signature` в `schema_info` обновляется.
- **Major (новая БД)**: смена профиля с существенными изменениями
  (например, `BasicRag` → `AgentLongTermMemory`). Требует export/import
  через CLI: `agent-memory-cli profile-migrate`.
- **Schema versioning**: `envelope_schema_version` и
  `component_schema_versions[]` хранятся в `schema_info`. При
  `open_existing()` проверяется версия и сравнивается `profile_signature`
  для drift detection.

## Planned Lexical Search Direction

Lexical search contracts should stay dependency free and use `ChunkId` as the
primary retrieval key. Postings should also carry `ResourceId` and generation so
targeted reindexing can remove, replace, or skip stale entries without a full
corpus rebuild.

BM25 is the first ranked keyword baseline. Boolean, BM25F, phrase/proximity,
fuzzy, n-gram, graph, learned sparse, and late-interaction methods should be
added as separate focused layers.

Canonical persisted source and chunk text is UTF-8. UTF-32/code point buffers
are allowed only as temporary tokenizer internals or benchmarked derived
artifacts.

Detailed tasks are tracked in `guides/lexical-search-roadmap.md`. The
fielded BM25F storage layout and the migration path from flat text to
fielded text are tracked in
[`guides/knowledge-base-roadmap.md`](knowledge-base-roadmap.md) under
"Fielded Lexical Storage (BM25F)" and in
[`guides/lexical-search-roadmap.md`](lexical-search-roadmap.md) under
the BM25F subsection. The lexical index returns
`KnowledgeUnitId`-keyed hits, not only `ChunkId`-keyed hits, so it
participates in the unified retrieval pipeline.

## Planned Retrieval Direction

Retrieval contracts live under `src/agent_memory/retrieval/` and stay
dependency free. A retriever accepts text, embedding, or mixed query signals,
metadata filters, and a result limit, then returns ordered scored chunks.

Concrete retrievers should be built as composition over `IEmbedder`,
`IVectorIndex`, and `IDocumentStorage` rather than as one large facade.

Hybrid retrieval should start with metadata filters, BM25, vector search, and
Reciprocal Rank Fusion before adding planner-guided or learned reranking layers.

## Planned Optimization Direction

Optimization work should preserve the dependency-free contracts already used by
storage, embedding, index, and retrieval layers.

Text and document payloads may be compressed through an optional compression
adapter. Hot vector-search data should keep full float embeddings available for
final reranking, while approximate encodings such as binary signatures,
float16, int8, or product quantization live in separate index-specific layers.

Eigen can be used later through temporary zero-copy views such as `Eigen::Map`,
but `Embedding` must remain a `std::vector<float>` value type in the public API.

Binary signature and bucket indexes are approximate candidate filters. They
must be benchmarked against exact float search by recall@K, latency, candidate
count, storage size, read amplification, and decompression time before being
treated as production defaults. Latency reports must distinguish the current
public exact-index implementation from a contiguous compute-oriented exact
baseline; otherwise container layout and result-materialization overhead can be
mistaken for an arithmetic advantage of binary search.

### Planned Binary Candidate-Generator Direction

Binary retrieval is a replaceable candidate-generator layer, not a single
universal index choice. The planned candidates are an exhaustive
`BinaryFlat` Hamming scan, immutable sparse Multi-Index Hashing (MIH), and an
optional binary HNSW adapter. Each receives the same binary query code and
returns document positions plus backend work diagnostics; none owns final
retrieval ranking.

```text
BinaryFlat | sparse MIH | Binary HNSW
                 -> candidate positions
                 -> full Hamming -> ADC -> exact embedding rerank
```

MIH is a self-contained candidate generator: it must not be cascaded through
HNSW before full Hamming. A future union of independently cheap MIH and HNSW
candidate sets is a separate benchmark hypothesis, not a default pipeline.
`BinaryFlat` is an exact baseline in binary Hamming space, not a replacement
for the final float-quality oracle.

A future coarse locator code is a MIH representation variant, not a fourth
general backend: a short binary code may generate candidates while a retained
full binary code supplies Hamming/ADC ranking. It remains deferred until the
native arbitrary-m MIH frontier and cost-aware schedule selection are complete.

Do not introduce a public `BinaryAnnBackend` API yet. `BinaryFlat` is not ANN,
and the candidate-generator result contract must first be informed by the
native benchmark. A future internal boundary may be named
`BinaryCandidateGenerator`; at minimum it must preserve candidate positions,
binary-code identity, query/index work diagnostics, and the metadata needed to
reject stale or inaccessible records before final reranking.

Mutable collections should remain canonical-storage-first. The expected future
physical shape is a small mutable/delta segment and rebuildable immutable
segments; individual segments may use different candidate generators. Candidate
positions from all searched segments are merged and reranked globally, so a
backend-local score is never treated as a final cross-segment ranking. Exact
scope/access filters and tombstone or generation checks apply before and after
candidate generation.

Backend selection and segment thresholds are benchmark-gated. Corpus scale,
code geometry, radius schedule, update pattern, memory budget, quality floor,
and p50/p95/p99 latency all matter; no corpus-size-only threshold is a product
contract before a locked workload demonstrates it.

The knowledge base layers (lexical field postings, graph edges, temporal
events, metadata filters) share a common primary-table-plus-secondary-index
pattern. The pattern, the index catalog, the cross-table transaction
semantics, and the schema versioning rules are tracked in
[`guides/optimization-roadmap.md`](optimization-roadmap.md) under
"Secondary & Reverse Indexes" and in
[`guides/knowledge-base-roadmap.md`](knowledge-base-roadmap.md) under
"Domain Stores", "Graph Storage", "Temporal Index", and "Metadata
Filters".

Optimization strategy — Eigen vs SIMD разделение:

M0/M1: std-only baseline, popcount64 intrinsics для Hamming. Eigen НЕ обязателен.
M2: optional Eigen adapter через `AGENT_MEMORY_ENABLE_EIGEN`, AutoencoderBinarySignatureEncoder inference.
M2/M3: SIMD HammingTopK kernel с AVX2/AVX-512 dispatch (после benchmark).
Never first: hand-written AVX matrix-vector encoder.

Hot path: Hamming scan (real bottleneck), не encoder (1 matrix-vector per query).
Детальная спецификация: см. [`guides/optimization-roadmap.md`](optimization-roadmap.md) → "Eigen и SIMD стратегия".

> **Measured update (PR #57).** Do not assume that Hamming scan is always the
> only bottleneck. Before projection materialization, encoder work dominated;
> after hot-path optimization, top-k selection, result construction, and exact
> rerank can dominate depending on bit width and candidate count. The current
> dependency-free implementation uses runtime-dispatched float SIMD,
> width-aware Hamming kernels, and a scalar fallback without requiring Eigen.

CMake flags (planned):
- `AGENT_MEMORY_ENABLE_EIGEN` (default OFF)
- `AGENT_MEMORY_ENABLE_ZSTD` (default OFF)
- `AGENT_MEMORY_HAS_POPCNT` (default ON)
- `AGENT_MEMORY_HAS_AVX2` (default OFF)
- `AGENT_MEMORY_HAS_AVX512` (default OFF)
- `AGENT_MEMORY_HAS_NEON` (default OFF)

## References

- [`guides/memory-stacks-roadmap.md`](memory-stacks-roadmap.md) —
  центральный манифест: ADR'ы, Envelope/Components/Projections,
  `MemoryProfileSpec`, `MemoryStack`, default profiles, capability matrix,
  validation rules, Maturity Levels (M0/M1/M2), Profile
  Migration.
- [`guides/knowledge-base-roadmap.md`](knowledge-base-roadmap.md) —
  retrieval flow, ContextBuilder, evaluation pipeline, cross-stack contracts.
- [`guides/knowledge-units-roadmap.md`](knowledge-units-roadmap.md) —
  per-kind payload-компоненты (QAPayload, FactPayload, ConversationEpisode,
  CompiledArticle, Chunk), lifecycle FSM.
- [`guides/lexical-search-roadmap.md`](lexical-search-roadmap.md) —
  BM25F, postings, tokenization.
- [`guides/translation-adapters-roadmap.md`](translation-adapters-roadmap.md) —
  optional cross-lingual projections and translation adapter provenance.
- [`guides/optimization-roadmap.md`](optimization-roadmap.md) —
  vector/binary storage, scope-aware secondary indexes, compression.
- [`guides/mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) —
  canonical physical MDBX manifest, DBI budget, TypeDiscriminatedTable,
  MultiTableWriter, ReverseIndexTable.
- [`guides/policies-roadmap.md`](policies-roadmap.md) (future) —
  детальная спецификация DecayPolicy / WritePolicy / SpeakerScopePolicy
  с диапазонами и defaults.
- [`guides/compaction-roadmap.md`](compaction-roadmap.md) (future) —
  `CompactionWorker`, job types, handoff structure.
- [`guides/runtime-services-roadmap.md`](runtime-services-roadmap.md) (future) —
  AsyncIndexer, WriteGate and the host LLM cache boundary.
- [`guides/cli-roadmap.md`](cli-roadmap.md) (future) —
  `agent-memory-cli` target (inspect / stats / check / vacuum / reindex /
  profile-info / profile-migrate).
