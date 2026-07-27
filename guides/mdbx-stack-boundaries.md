# Storage Stack Boundaries: agent-memory-cpp ↔ mdbx-containers

## Назначение

Этот документ фиксирует разделение ответственности между проектом `agent-memory-cpp` и upstream-библиотекой `external/mdbx-containers/`. Документ предназначен для AI-агентов, работающих над `agent-memory-cpp`. Для расширений самой `mdbx-containers` см. `guides/mdbx-containers-extension-tz.md`.

## Принцип

agent-memory-cpp реализует ДОМЕННЫЕ КОНТРАКТЫ (KnowledgeUnit, RetrievalPlan, IContextBuilder, eval, ...) и АДАПТЕРЫ к хранилищу (MdbxKnowledgeUnitStore, MdbxQAKnowledgeBase, MdbxGraphStore, ...). Адаптеры строятся ПОВЕРХ generic-примитивов и upstream-модулей mdbx-containers.

Нормативные источники: `guides/architecture.md` и
`guides/mdbx-containers-extension-tz.md`. Этот файл — краткий индекс границ
для AI-агентов; при конфликте побеждают архитектурный документ и TZ.

## Что реализуется в mdbx-containers (upstream)

### Generic-примитивы (наши требования в TZ)

- `ReverseIndexTable` — secondary index через DUPSORT
- `RangeIndexTable` — range queries + pagination
- `TypeDiscriminatedTable<EnumTag, Key, ValueTypes...>` — type-tag
  polymorphic values behind caller-owned keys
- `CompositeKey<Parts...>` + helpers — typed composite keys
- `MultiTableWriter` / `Connection::multi_write` — atomic multi-table writes
- `KeyValueTable` extensions (batch, diagnostics, paginated_range)
- `Config` extensions (enable_metrics, table_creation_callback)
- Utility functions (hamming_*, sortable_key_from_*, hex)

### Candidate generic primitives (не текущие требования)

- `BidirectionalRelationIndex<Scope, Source, Target, Tag, Payload>` — только
  после двух независимых consumers; scope/tag/payload opaque, traversal
  page-based with DUPSORT-aware cursor `(encoded_key, encoded_value)`.
  Generic helper assumes unique `(scope, source, target, tag)` and upsert
  semantics; multiple evidence records require downstream payload aggregation or
  an explicit domain relation id.
- `LargeValueStore<BlobId>` / `ChunkedBlobStore<BlobId>` — только если нужны
  partial reads, chunk-level checksums/compression, bounded chunks, orphan
  cleanup и повторное использование за пределами `agent-memory-cpp`.
- `PersistentQueue` — только отдельным proposal после двух независимых
  не-agent-memory consumers; текущий `TaskQueue` остаётся downstream runtime
  abstraction.

### Что НЕ реализуется в mdbx-containers

- RRF / retrieval fusion / cross-encoder orchestration
- Domain stores: `DocumentStore`, `ChunkStore`, `GraphStore`, `EventStore`,
  `TimelineStore`, `MemoryStore`, `EvidenceSet`, `RetrievalTraceStore`
- Runtime workers and queues: `TaskQueue`, retry/backoff/lease/cancellation
  state machines
- Graph semantics: `EdgeKind`, expiration policy, evidence/provenance policy,
  contradiction handling, bounded expansion rules
- Entity-resolution semantics: canonicalization, alias policy, scope/type
  compatibility, merge confidence, ambiguous-result handling and LLM proposal
  review
- LLM/tool query safety policy: permission model, schema allowlists,
  `EntityTypeId`/`RelationTypeId` registry mapping and validation before any
  backend-specific query text is built
- Source/artifact identity: `SourceRef`, `ResourceId`, `ArtifactDescriptor`,
  raw-document retention policy
- Usage decay, freshness ranking, profile/scenario lifecycle
- LLM-вызовы (любые модели, провайдеры, prompt engineering)
- Embedding model execution (ONNX, llama.cpp, OpenAI-compat, ...)
- Language detection / translation execution and package routing
- Парсеры (markdown, PDF, DOCX, code, ASR, VLM)
- Cross-encoder rerankers
- Chunker'ы (HybridChunker, sentence splitter, sliding window)
- RAG orchestration (planner-guided retrieval, agent loops)
- HTTP/MCP/API серверы
- UI и приложения

## Что реализуется в agent-memory-cpp

### Доменные контракты (`src/agent_memory/...`)

- `domain/KnowledgeUnit` — обобщение над Chunk/QAPair/Fact/Event/Entity/Relation/Section/Summary/Episode
- `domain/SourceRef` — first-class provenance (миграция из facts/)
- `domain/TypedMetadata` + расширенные `MetadataFilter` (Range, In, Tag)
- `retrieval/RetrievalPlan` — что включать в retrieval
- `retrieval/IUnitRetriever` — multi-retriever composition
- `retrieval/IContextBuilder` — budgeted context assembly
- `eval/RetrievalTrace` + `eval/RetrievalDataset` + `eval/RetrievalMetrics` — eval harness
- `lexical/IChunkEnricher` (расширенный до `SearchProjection`) — Contextual Retrieval
- `lexical/IReranker`, `lexical/IQueryAnalyzer` — pluggable slots для LLM-адаптеров
- `qa/IQAKnowledgeBase` — QAPair storage и retrieval
- `facts/IFactStore`, `facts/ITemporalIndex`, `facts/Event` — facts/events
- `graph/IGraphStore`, `graph/GraphTypes` — граф с bounded expansion
- `entity/IEntityResolver` — deterministic-first entity resolution with
  optional LLM proposal adapter; durable merges remain policy-checked
- `tools/MemoryPermission` and typed filter contracts — LLM-facing tools accept
  internal ids/allowlisted filters, not backend schema labels or DBI/table names
- `context/ContextBudget`, `context/ContextBlock` — budgeted structures

### Адаптеры к mdbx-containers (`src/agent_memory/infrastructure/mdbx/`)

- `MdbxKnowledgeUnitStore` (planned) — реализует `IKnowledgeUnitStore` через `knowledge_units` envelope table + `unit_components` + `unit_projections` + per-kind payload DBIs
- `MdbxQAKnowledgeBase` (planned) — реализует `IQAKnowledgeBase` через canonical `qa_payloads` + projection/inverted indexes
- `MdbxFactStore` (planned) — реализует `IFactStore` через canonical `fact_payloads` + projection/inverted indexes; decay считается downstream
- `MdbxTemporalIndex` (planned) — реализует `ITemporalIndex` через `RangeIndexTable<CompositeKey<ScopeId, Timestamp, EventId>, EventPayload>`
- `MdbxGraphStore` (planned) — реализует `IGraphStore` через `graph_edges_by_src` / `graph_edges_by_dst`; `EdgeKind` и traversal policy downstream
- `MdbxEntityResolutionIndex` (planned) — реализует candidate lookup для
  `IEntityResolver` через canonical-name/alias indexes, lexical signatures,
  dense/binary candidate indexes and scope/type filters; merge semantics stay
  downstream
- `MdbxResourceMetadataFilters` (planned) — `ReverseIndexTable<CompositeKey<ScopeId, MetadataKey, MetadataValue>, UnitId>` для canonical unit pre-filter; any `ResourceId` pre-filter remains adapter-local
- `MdbxResourceBodyStore` (planned) — optional primary raw body storage через `KeyValueTable<ResourceId, bytes>` или application-owned chunked layout; не через reverse indexes
- Существующие: `MdbxDocumentStorage`, `MdbxResourceManifestStorage`

### Retrieval composition

- `HybridRetrievalEngine` — multi-retriever + RRF + cross-encoder rerank slot + LLM intent analyzer slot
- `CompositeRetrievalEngine` (planned) — generic composition поверх IUnitRetriever-ов
- `TrimmedContextBuilder` (planned) — budgeted assembly с order: QA → chunks → summaries → graph

### LLM/embedding адаптеры (`src/agent_memory/adapters/` или `examples/`)

- `OpenAICompatEmbedder`, `OnnxEmbedder`, `LlamaCppEmbedder` — embedding backends
- `AnthropicContextualizer` — Contextual Retrieval через Claude Haiku
- `LlmQueryRewriter` — RAG Fusion
- `LlmRelevanceClassifier` — Corrective RAG validator
- `CrossEncoderReranker` — через ONNX
- `ITranslationAdapter` implementations — optional Argos Translate sidecar,
  offline model wrapper or external translation service; writes
  `TranslatedCanonical` projections through domain storage contracts
- `MarkdownHybridChunker`, `DoclingParser`, `WhisperTranscriber` — примеры парсеров

## Правила зависимости

1. **agent-memory-cpp ЗАВИСИТ от mdbx-containers** через PUBLIC interface в `include/mdbx_containers/` (через target `mdbx_containers::mdbx_containers`)
2. **agent-memory-cpp НЕ МОЖЕТ** редактировать файлы в `external/mdbx-containers/` (это upstream submodule)
3. **Новые требования к mdbx-containers** оформляются в `guides/mdbx-containers-extension-tz.md` как спецификация для апстрима
4. **Использование upstream-примитивов** в адаптерах agent-memory-cpp — норма, это идиоматический путь
5. **Fallback на in-memory реализации** (planned: `InMemoryKnowledgeUnitStore`, `InMemoryQAKnowledgeBase`, `InMemoryTemporalIndex`, `MemoryGraphStore`) для тестов и проектов без MDBX

## Когда какой путь выбирать

| Сценарий | Путь |
|---|---|
| Нужен persistent storage в production | MDBX adapter поверх upstream примитивов |
| Unit-тесты, контракт-тесты | In-memory адаптер (planned) |
| Embedded deployment без MDBX | In-memory адаптер |
| Новая таблица в agent-memory-cpp | Сначала проверить: есть ли generic-примитив в mdbx-containers? Если да — использовать. Если нет — описать adapter-local layout; в TZ добавлять только reusable storage primitive |
| Новая доменная абстракция (например, новый KnowledgeUnitKind) | Только в agent-memory-cpp, в mdbx-containers НЕ идёт |
| Новый generic storage pattern (например, новый вид индекса) | Сначала в TZ для mdbx-containers |
| Raw docs/tool logs в MDBX | `ResourceBodyStore` в agent-memory-cpp; body bytes только в primary body/blob table, не в reverse indexes |

## Связь с другими документами

- `guides/mdbx-containers-extension-tz.md` — спецификация расширений mdbx-containers
- `guides/knowledge-base-roadmap.md` — общий roadmap KB архитектуры
- `guides/knowledge-units-roadmap.md` — спецификация KnowledgeUnit типов
- `guides/lexical-search-roadmap.md` — BM25F fielded storage
- `guides/optimization-roadmap.md` — compression, binary signature index, bucket index
- `guides/architecture.md` — общая архитектура проекта
- `guides/memory-stacks-roadmap.md` — центральный манифест архитектуры (CapabilitySet, MemoryProfileSpec, MemoryStack, validation, Maturity Levels M0/M1/M2).
- `guides/mdbx-containers-extension-tz.md` — canonical physical MDBX manifest and DBI budget.
