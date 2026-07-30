# memory-stacks-roadmap.md

> Все C++ сниппеты в этом документе — illustrative C++17 (не designated initializers, не std::span, не std::strong_ordering). Компилируемые примеры — отдельная задача `examples/` при старте реализации.

Центральная спецификация архитектуры памяти `agent-memory-cpp`. Определяет модель данных (envelope + components + projections), декларативную спецификацию профилей, runtime-стек, capability matrix, валидационные правила и maturity levels. Единственный canonical physical MDBX manifest и DBI budget живут в `guides/mdbx-containers-extension-tz.md` §5.5/§5.5.1. Документ supersede'ит компонент-агностичные части `knowledge-base-roadmap.md` и `knowledge-units-roadmap.md` для разделов, относящихся к profiles/stacks.

> Research background for the M2+ retrieval-hook contracts
> (`IQueryTransformer`, `IRetrievalEvaluator`) and for dense-retrieval
> tradeoffs lives in `guides/research-reading-map.md`.

## 1. Purpose

Этот документ фиксирует архитектурные решения для подсистемы памяти `agent-memory-cpp`:

- Какие структуры данных используются (Envelope, Components, SearchProjections).
- Как пользователь описывает нужный тип памяти (MemoryProfileSpec).
- Как пользователь открывает и использует память (MemoryStack).
- Какие готовые конфигурации предоставляет библиотека.
- Как MDBX environment раскладывается по DBI в зависимости от профиля.
- Какие уровни зрелости (M0/M1/M2) определяют ship-it критерии.
- Какие cross-cutting runtime-сервисы ортогональны профилям.
- Как raw resources (`.md`, `.txt`, extracted `.pdf`, transcripts, logs) живут
  рядом с normalized/card-like knowledge units.
- Как optional translation adapters создают canonical-language projections без
  добавления Python/ML зависимости в C++ core.

Non-goals документа:

- Детальная спецификация payload-компонентов (см. `knowledge-units-roadmap.md`).
- Детальная спецификация BM25F / postings / tokenization (см. `lexical-search-roadmap.md`).
- Детальная спецификация dense vector / binary signature / secondary indexes (см. `optimization-roadmap.md`).
- Детальная спецификация compaction-воркера (см. `compaction-roadmap.md`).
- Детальная спецификация runtime-сервисов prompt-cache/write-gate (см. `runtime-services-roadmap.md`).

## 2. Architectural Decision Records (ADR Index)

Сводка архитектурных решений. Полный текст каждого ADR — в секции 3 и далее.

| ID | Название | Решение |
|---|---|---|
| ADR-001 | Memory Data Model | Envelope + Components + SearchProjections (не монолитный struct) |
| ADR-002 | MemoryStack vs MemoryProfileSpec | Spec декларативный, Stack — runtime-объект |
| ADR-003 | Profile/Scope ортогональность | Profile = capabilities, Scope = namespace, ortho |
| ADR-004 | KnowledgeUnit миграция | v0.x breaking refactor, v1.x additive migrations |
| ADR-005 | Search text в envelope | primary_text (256-1024 байт) + SearchProjections отдельно |
| ADR-006 | GraphStorage | DBI внутри одного MDBX env, не внешний graph DB |
| ADR-007 | Embedding storage | embedding_meta + embedding_vectors, multi-projection/multi-model |
| ADR-008 | Decay/anti-loop | Меняет retrieval score, не удаляет записи; defaults для AgentLTM |
| ADR-009 | Compaction strategy | Hybrid: on-write cheap ops + on-schedule heavy jobs |
| ADR-010 | MDBX environment | Один env, много DBI. Multi-env — только при обоснованной потребности |
| ADR-011 | Lifecycle FSM | 4 durable states: Active / Superseded / Deprecated / Erased (SoftSuppressed — runtime state в UsageStatsComponent.cooldown_until_ms) |
| ADR-012 | Multi-tenancy | Все secondary indexes — scope-aware |
| ADR-013 | Runtime services | CompactionWorker, WriteGate and AsyncIndexer are core-adjacent runtime services; LLM caches are host integrations |
| ADR-014 | CLI | Отдельный target `agent-memory-cli`, не core library |
| ADR-015 | Maturity Levels | M0 (MVP) / M1 (Production) / M2 (Advanced) с ship-it критериями |
| ADR-016 | Raw resources vs normalized units | Raw ResourceBody first-class; card-like KnowledgeUnits are curated derivatives, not mandatory input format |
| ADR-017 | Translation projections | Original text stays authoritative; translated canonical-language text is an optional derived SearchProjection |
| ADR-018 | Knowledge activation | One logical corpus with domain maps, playbooks, activation metadata, soft routing, and budgeted context planning |
| ADR-019 | Agent runtime integration boundary | Memory stores durable cognitive records; runtime owns live cognition and execution |
| ADR-020 | Perspective orthogonal to Scope | Scope is namespace/ownership; observer/character/authority/partition are components |
| ADR-021 | Introspection as observation | Node self-report is persisted evidence, not privileged truth |
| ADR-022 | Causal history first-class | Decisions/actions/outcomes carry direct causes, trace ids and evidence |
| ADR-023 | Bi-temporal agent knowledge | World validity and runtime knowledge time are distinct M2+/A-lane axes |
| ADR-024 | Monotonic evidence | Raw observations/events are append-only except explicit erase policy |
| ADR-025 | Semantic replication | Memory exports/imports records and conflicts; it does not replicate MDBX pages |
| ADR-026 | Artifact provenance | Source/Revision/Artifact/Representation/Segment, typed evidence anchors, own catalog/blob truth and derived external-vector adapters |

См. также [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) для Bounded BFS + schema introspection (Pattern 5) borrowed from `codebase-memory-mcp` — это extension candidate для `GraphStore` (расширяет capability flag `GraphRelations` поверх substrate из ADR-006 GraphStorage; storage substrate и capability flag — отдельные сущности, не синонимы).

See [`memory-architectures-roadmap.md`](memory-architectures-roadmap.md) for a comparison of 13+ external memory architectures (Karpathy / A-MEM / lifemodel / СВИНОПАС / NOUZ / Self-Evolving / Vault Audit AI / Mem0 / Letta / Zep / Memoria) and their applicability to the in-house stack patterns.

See [`memory-lifecycle-governance-roadmap.md`](memory-lifecycle-governance-roadmap.md)
for M2+ lifecycle governance items: bi-temporal knowledge, abstraction and
derivation graph, causal relations, progressive retrieval, expanded memory
evaluation, policy-selectable mutation, deterministic-first entity resolution,
typed query/MCP safety and logical index separation.

See [`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md) for the
artifact-aware extension of ADR-016: source revisions, immutable original
bytes, versioned derived representations, typed evidence locations and the
separate artifact-processing lineage graph.

See [`milestones.md`](milestones.md) for the normative M0/M1/M2 scope lock.
This roadmap keeps architectural details, but milestone ownership and ship-it
requirements are resolved by the milestone manifest.

See [`knowledge-activation-roadmap.md`](knowledge-activation-roadmap.md) for
the knowledge-planning layer: DomainMap, Playbook, CapabilityRegistry, strict
filters versus soft routing, and activation-specific eval classes.

See [`agent-runtime-integration-roadmap.md`](agent-runtime-integration-roadmap.md)
for the optional A0-A4 cognitive-runtime integration lane. That lane stores
durable runtime origin, perspective, causal, task/decision/procedure and
reconciliation records without adding live scheduling, action execution or
ADELIA headers to core.

## 3. ADR-001: Memory Data Model

### 3.1. Решение

Данные memory unit разделяются на три независимых слоя:

```
Layer A: KnowledgeUnitEnvelope (lookup-critical, hot path)
  id, kind, scope_id, primary_text, display_text,
  lifecycle_state, sources, timestamps, priority_weight

Layer B: Components (operational + per-kind payloads)
  UsageStatsComponent, SpeakerComponent, TemporalComponent,
  CompactionMetaComponent,
  QAPayload, FactPayload, ConversationEpisodePayload,
  CompiledArticlePayload, ChunkPayload

Layer C: SearchProjections (retrieval-specific text views)
  Original, DenseContextual, Bm25Body/Title/Heading/Symbols,
  QAQuestion, QAAnswer, QACombined, Summary, CodeSymbols, TranslatedCanonical
```

Каждый слой имеет собственные logical stores и обновляется независимо (но
атомарно через MultiTableWriter при coordinated writes). Physical MDBX DBI
names and key shapes закреплены в `mdbx-containers-extension-tz.md` §5.5.

### 3.2. Обоснование

Монолитный `KnowledgeUnit` struct с 30+ полями приводит к распуханию value envelope при сериализации, неясным инвариантам, раздуванию индексов, необходимости additive migration каждого нового поля, дублированию данных при нескольких представлениях.

Компонентная модель даёт hot path с минимум I/O, lazy load компонентов по запросу retrieval layer, добавление нового компонента без модификации envelope, естественное место для kind-специфичных данных в payload-компонентах.

### 3.3. Альтернативы, отклонённые

- Монолитный KnowledgeUnit с 50 optional полями. Отклонён: нарушает инварианты, раздувает I/O.
- Полностью отдельные storage типы для каждого use-case. Отклонён: дублирование кода, невозможность RRF fusion, нет единого API.
- JSON-value envelope с произвольными полями. Отклонён: теряем строгие типы C++, schema validation, индексы по typed fields.

## 3A. ADR-016: Raw Resources vs Normalized Units

### 3A.1. Решение

Raw resources являются first-class input/storage layer, а card-like
`KnowledgeUnit` — normalized/curated layer поверх них. Импорт `.md`, `.txt`,
extracted `.pdf`, transcript, log или playbook не требует предварительной
ручной конвертации в карточку. Если вход не содержит curated schema, importer
создаёт минимальный generic document unit и связывает его с raw body через
`ResourceId`/`SourceRef`.

```text
RawResource / ResourceBody
  original bytes or extracted text, revisions, codec, content hash

KnowledgeUnitEnvelope + Components
  normalized semantic unit: Fact, QAPair, Note, Chunk, Summary, ...

SearchProjection
  retrieval-facing text derived from raw body or normalized payload
```

Card-like units полезны для curated knowledge: trust, tags, outdated flags,
lifecycle, facts, QA, summaries, decisions and graph relations. Они не являются
единственным допустимым ingestion format.

### 3A.2. Обоснование

Агенты часто начинают с сырой базы знаний: Obsidian vault, playbooks, markdown
notes, `.txt` logs, PDF extracts. Требование сначала привести всё к curated
card schema делает early RAG слишком хрупким. При этом нормализованные units
нужны для фильтров, trust policy, lifecycle, compaction, graph relations and
evaluation. Поэтому система поддерживает оба уровня:

- raw resources обеспечивают faithful citation, export, re-chunking and
  drill-down;
- normalized units дают controlled semantics and policy hooks;
- compaction/summarization может позже вывести curated facts/cards/summaries
  из raw document units.

### 3A.3. Storage boundary

Raw body bytes не хранятся в envelope, `unit_components`,
`unit_projections` или reverse indexes. Они живут в `ResourceBodyStore`
(MDBX-backed или file-pack backend), а search indexes хранят ids, compact
postings, projections and source ranges. См. `mdbx-containers-extension-tz.md`
§12.9 and `optimization-roadmap.md` §"Raw Resource Body Compression".

## 3B. ADR-017: Translation Projections

### 3B.1. Решение

Перевод является optional derived layer поверх ingestion/retrieval, а не
обязательным форматом хранения. Оригинальный resource body и
`SearchProjection::Original` остаются authoritative для цитирования,
re-chunking, export и forensic review. Если profile включает
`TranslationProjection`, adapter может создать дополнительную
`SearchProjection::TranslatedCanonical` в canonical language профиля.

```text
RawResource / ResourceBody
  original bytes or extracted text, original language, content hash

SearchProjection::Original
  original-language retrieval view

SearchProjection::TranslatedCanonical
  optional canonical-language retrieval view, generated by ITranslationAdapter

TranslationProjectionMeta
  provider/model/package fingerprint, detected source language, pivot path,
  source revision, projection generation and quality hint
```

Детальный контракт находится в
[`translation-adapters-roadmap.md`](translation-adapters-roadmap.md). Argos
Translate используется как pattern donor для offline package/pivot semantics,
но не становится dependency `agent-memory-cpp`.

### 3B.2. Обоснование

Raw notes, playbooks, transcripts and PDFs may arrive in Russian, English or a
mixed corpus. Cross-lingual retrieval needs a stable way to search by canonical
language without losing the original text. A translated projection gives:

- BM25/vector indexing over a normalized language view;
- deterministic invalidation when the source revision or translation package
  changes;
- audit metadata for pivot translation and model drift;
- lower coupling than embedding a translation runtime into the static library.

The projection is lossy by definition. Retrieval may use it for recall, but
final context should prefer source-language excerpts when exact citation
matters.

## 4. ADR-002: MemoryStack vs MemoryProfileSpec

### 4.1. Решение

Два разных уровня API: `MemoryProfileSpec` (декларативный, сериализуемый) и `MemoryStack` (runtime-объект с handles). Конструкция `MemoryStack::open(path, spec)` валидирует spec, открывает env, создаёт DBI по capability, возвращает stack.

### 4.2. Обоснование

- `MemoryProfileSpec` — это данные: сериализуются, версионируются, читаются из YAML.
- `MemoryStack` — это объект с открытыми handles, prepared statements, транзакциями.
- Разделение позволяет сохранять spec в metadata файла и проверять при открытии (drift detection через profile_signature hash).
- Stack закрывается через `stack.close()` — handles освобождаются, env остаётся на диске.

### 4.3. Naming rationale

`MemoryStack` выбран вместо `MemoryProfile` (конфликт с `std::execution::MemoryProfile`), `MemoryConfiguration` (слишком общее), `MemoryKit` (маркетинговый звук). `MemoryStack` передаёт композицию runtime-компонентов и интуитивен.

## 5. ADR-003: Profile/Scope ортогональность

### 5.1. Решение

Profile (через `MemoryStack`) и Scope — независимые оси: Profile задаёт capabilities/schema/policies, Scope задаёт data ownership/namespace. Каждый KnowledgeUnit имеет `ScopeId scope_id`. Все secondary indexes начинаются с `scope_id` в ключе.

### 5.2. Пример

```cpp
// Один стек, много scopes
auto stack = MemoryStack::open("agent.mdbx", MemoryProfiles::AgentLongTermMemory());

CreateUnitRequest req_a;
req_a.envelope = make_envelope(...).with_scope("user:yaroslav");
req_a.payload = ...;
stack.create_or_get_unit(req_a);

CreateUnitRequest req_b;
req_b.envelope = make_envelope(...).with_scope("agent:nika");
req_b.payload = ...;
stack.create_or_get_unit(req_b);

// Retrieval с фильтром
RetrievalPlan plan;
plan.raw_query = "...";
plan.scope_ids = {"user:yaroslav"};
auto hits = stack.retrieve(plan);
```

### 5.3. Обоснование

Profile ортогональна scope: один стек может обслуживать данные разных пользователей/проектов. Изменение profile (capabilities) — major migration. Изменение scope фильтра — обычный query filter.

## 6. MemoryProfileSpec — декларативная спецификация

### 6.1. Capability flags

```cpp
enum class MemoryCapability : uint64_t {
    None               = 0,
    LexicalBm25F       = 1ull << 0,
    DenseVectors       = 1ull << 1,
    QAPairs            = 1ull << 2,
    TemporalValidity   = 1ull << 3,
    UsageStats         = 1ull << 4,
    Decay              = 1ull << 5,
    SpeakerAttribution = 1ull << 6,
    Compaction         = 1ull << 7,
    GraphRelations     = 1ull << 9,
    EmbeddingMigration = 1ull << 10,
    CompiledArticles   = 1ull << 11,
    ConversationMemory = 1ull << 12,
    FullSourceRefs     = 1ull << 13,
    TranslationProjection = 1ull << 15,
    KnowledgeActivation = 1ull << 16,
    CognitiveTraceComponents = 1ull << 17,
    ProceduralActivation = 1ull << 18,
    ReplicaReconciliation = 1ull << 19,
    ArtifactProvenance = 1ull << 20,
    DurableGlobalIdentity = 1ull << 21,
    SequenceReplay = 1ull << 22,
};

using CapabilitySet = std::underlying_type_t<MemoryCapability>;
```

Optional affective-agent capabilities are intentionally not part of the default
baseline above. They are tracked separately in
[`affective-memory-roadmap.md`](affective-memory-roadmap.md) as an overlay on
`AgentLongTermMemory`: `AffectiveEpisodes`, `GoalAttribution`,
`OutcomeTracking`, `RelationshipState`, and `SensitiveInferencePolicy`.

Future lifecycle-governance capabilities are intentionally not added to the M1
capability enum yet. `BiTemporalValidity`, `AbstractionGraph`,
`CausalRelations`, `ProgressiveRetrieval`, `MemoryGovernance` and
`MutationPolicy` are tracked in
[`memory-lifecycle-governance-roadmap.md`](memory-lifecycle-governance-roadmap.md)
until their profile matrix and DBI budget deltas are specified.

`ArtifactProvenance` is an optional M2 capability. It enables the catalog and
blob/retention contracts from
[`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md); concrete
MDBX DBI deltas stay profile-owned and are not part of the baseline manifest.

Additional M2+ lifecycle candidates such as `EntityResolution`,
`TypedToolFilters` and `LogicalIndexSeparation` are also tracked there until
their profile deltas and test gates are defined.

General cross-cutting opt-ins such as encrypted local storage and context
planning are not affective capabilities. They are represented by
`enable_encrypted_storage`/`EncryptionPolicy` and
`enable_context_planner`/`ContextTierPolicy` in `MemoryProfileSpec` because
ordinary RAG and agent-memory profiles may use them too.

`KnowledgeActivation` is a domain-planning capability, not a new physical
database by itself. It enables deterministic activation metadata, DomainMap and
Playbook handling, soft domain routing, and activation eval fixtures. Storage
uses the existing envelope/projection/metadata/graph substrate until a future
profile explicitly adds `playbook_payloads`, `domain_map_payloads`, or
`activation_rules` DBI deltas to `mdbx-containers-extension-tz.md`.

`CognitiveTraceComponents`, `SequenceReplay`, `ProceduralActivation` and
`ReplicaReconciliation` are
coarse-grained runtime-integration groups. They deliberately do not create a
capability bit for each scalar component. `CognitiveTraceComponents` uses
existing `unit_components`, graph edges, resource bodies and projections, and
requires `FullSourceRefs` for durable evidence. `SequenceReplay` is separate:
it adds bounded `KnownAtSequence`, requires `CognitiveTraceComponents`,
`DurableGlobalIdentity`, identity-scheme validation and the
`runtime_sequence_index` profile delta. Components-only adapters must reject
that indexed query rather than advertise replay without its identity and
sequence stores. Partition-specific DBIs beyond those remain future manifest
deltas.

### 6.2. Политики

```cpp
enum class DecayMode { None, Linear, Exponential, Logistic };

struct DecayPolicy {
    DecayMode mode = DecayMode::None;
    double half_life_ms = 0;
    double use_boost = 0;
    double cooldown_ms = 0;
    double self_echo_suppression = 0;
    double cold_threshold = 0;
};

enum class WriteFlushTrigger {
    OnEveryEvent, OnTimer, OnSizeThreshold, OnImportance
};

struct WritePolicy {
    WriteFlushTrigger trigger = WriteFlushTrigger::OnEveryEvent;
    uint64_t flush_interval_ms = 0;
    uint64_t size_threshold_bytes = 0;
    double importance_threshold = 0;
    double dedupe_distance_threshold = 0;
    bool allow_supersede = true;
    bool allow_merge = true;
    bool allow_episode_compaction = true;
};

struct SpeakerScopePolicy {
    bool include_self = true;
    bool include_owner = true;
    bool include_cohost = true;
    bool include_audience = false;
    bool attribute_quote = true;
};

enum class RetrievalMode { Associative, Targeted, Hybrid, Disabled };
enum class FusionStrategy { RRF, WeightedMax, Learned, Planner };
struct HybridRetrievalConfig {
    FusionStrategy fusion = FusionStrategy::RRF;
    double rrf_k_constant = 60.0;
    std::vector<double> retriever_weights;
    size_t candidate_pool_size = 200;
};

struct ContextBudget {
    size_t total_tokens = 4096;
    size_t qa_tokens = 512;
    size_t chunk_tokens = 2048;
    size_t graph_tokens = 512;
    size_t summary_tokens = 512;
    size_t evidence_tokens = 256;
};

enum class ContextTier : uint8_t {
    Short,   // current session, recent turns, active unresolved events
    Medium,  // recent summaries, commitments, short-term relationship evidence
    Long,    // durable facts, older episodes, graph relations, semantic KB
    Base     // addressable persona, policy, compiled project knowledge
};

struct ContextTierPolicy {
    bool enable_short = true;
    bool enable_medium = true;
    bool enable_long = true;
    bool enable_base = true;

    std::optional<uint64_t> short_window_ms;
    std::optional<uint64_t> medium_window_ms;
    size_t short_k = 8;
    size_t medium_k = 12;
    size_t long_k = 20;

    bool allow_graph_expansion = false;
    bool allow_compiled_wiki = false;
};

enum class DenseIndexMode : uint8_t {
    Exact = 0,                      // brute-force float cosine, ground truth
    BinaryCandidateFilter = 1,      // optional M1 experiment; binary filter + float rerank
    BinaryOnly = 2,                 // binary only, Hamming ranking (M2 experimental/compact)
    ApproximateVector = 3,          // binary + decoder → approx vector → rerank (M2 experimental)
    Hnsw = 4,                       // M2+ experimental ANN backend
};

struct HnswConfig {
    uint32_t m = 16;                     // среднее число bidirectional links per node (8-64)
    uint32_t ef_construction = 100;      // beam size при build (64-200, trade-off build time vs quality)
    uint32_t ef_search = 50;             // beam size при query (10-200, trade-off recall vs latency)
};

struct DenseIndexConfig {
    DenseIndexMode mode = DenseIndexMode::Exact;

    // Binary modes (mode == BinaryCandidateFilter / BinaryOnly / ApproximateVector):
    uint32_t bit_count = 128;
    std::string encoder_id = "random_hyperplane_lsh_v1";

    // ApproximateVector mode only:
    bool store_decoder = true;
    bool store_float_fallback = false;

    // Hnsw mode only:
    // Если mode == Hnsw и hnsw_config не задан, используются defaults (M=16, efConstruction=100, efSearch=50).
    // Если mode != Hnsw, поле игнорируется.
    std::optional<HnswConfig> hnsw_config;
};

// HnswConfig per-stack defaults (когда mode == Hnsw):
//   m: average number of bidirectional links per node. Higher = better recall, more memory.
//      Typical: 16 (default), 32 (high quality), 8 (compact).
//   ef_construction: beam size during index build. Higher = better quality, slower build.
//      Typical: 100 (default), 200 (high quality), 64 (fast build).
//   ef_search: beam size during query. Higher = better recall, slower query.
//      Typical: 50 (default), 100 (high recall), 10 (fast query).
//
// Per-stack defaults (при переключении на Hnsw mode):
//   BasicRag:      M=16, efConstruction=100, efSearch=50   (только если corpus > 100k units)
//   AgentLTM:      M=32, efConstruction=200, efSearch=100
//   CompiledWiki:  M=32, efConstruction=200, efSearch=100
//   FullResearch:  M=32, efConstruction=200, efSearch=100
```

### 6.3. Полная спецификация

```cpp
struct RetrievalIoBudget {
    std::uint32_t max_segment_reads = 0;
    std::uint32_t max_mdbx_cursor_seeks = 0;
    std::uint64_t max_encoded_bytes_read = 0;
    std::uint64_t max_decoded_bytes = 0;
    std::uint64_t max_io_time_us = 0;
};

struct MemoryProfileSpec {
    std::string name;

    CapabilitySet capabilities = 0;

    bool enable_lexical_bm25f = true;
    bool enable_dense_vectors = false;
    bool enable_graph = false;

    bool enable_usage_stats = false;
    bool enable_temporal_validity = false;
    bool enable_speaker = false;
    bool enable_qa_payload = false;
    bool enable_fact_payload = false;
    bool enable_conversation_episode = false;
    bool enable_compiled_article = false;
    bool enable_full_source_refs = false;
    bool enable_embedding_meta = false; // opens dedicated embedding_meta, not unit_components
    bool enable_compaction = false;
    bool enable_async_indexer = false;
    bool enable_context_planner = false;
    bool enable_knowledge_activation = false;
    bool enable_durable_global_identity = false;
    bool enable_cognitive_trace_components = false;
    bool enable_sequence_replay = false;
    bool enable_procedural_activation = false;
    bool enable_replica_reconciliation = false;
    bool enable_encrypted_storage = false;
    std::optional<DecayPolicy> decay_policy;
    std::optional<WritePolicy> write_policy;
    std::optional<SpeakerScopePolicy> speaker_policy;
    std::optional<HybridRetrievalConfig> hybrid_config;
    std::optional<RetrievalIoBudget> retrieval_io_budget;
    std::optional<ContextBudget> context_budget;
    std::optional<ContextTierPolicy> context_tier_policy;
    std::optional<RetrievalMode> default_retrieval_mode;
    std::optional<DenseIndexConfig> dense_index_config;
    std::optional<EncryptionPolicy> encryption_policy;

    uint32_t envelope_schema_version = 1;
    uint32_t component_schema_versions[kNumComponentKinds] = {};

    std::array<uint8_t, 32> reserved = {};
};
```

`capabilities` bitmask вычисляется из high-level флагов при `MemoryStack::open()` для единообразия проверок.

`ContextTierPolicy` is a planning overlay, not a storage layout. The same
`MemoryStack` may satisfy `short`, `medium`, `long`, and `base` tiers through
recency filters, compiled summaries, graph expansion, and read-only knowledge
packs. `MemoryAwareContextPlanner` from
[`runtime-services-roadmap.md`](runtime-services-roadmap.md) chooses which tiers
are queried for a turn based on urgency, trigger features, latency budget, and
token budget.

`Base` is always addressable, not always fully injected. `allow_compiled_wiki`
controls whether large compiled knowledge packs may be queried or cached for a
turn; a small immutable system prefix can remain enabled even when compiled
wiki expansion is disabled.

`EncryptionPolicy` is defined in
[`policies-roadmap.md`](policies-roadmap.md). `enable_encrypted_storage` is an
opt-in security/storage capability for local deployments and sensitive memory
profiles; it is not required by default profiles.

## 7. MemoryStack — runtime API

### 7.1. Открытие стека

```cpp
class MemoryStack {
public:
    static MemoryStack open(
        const std::string& path,
        const MemoryProfileSpec& spec);

    static MemoryStack open_existing(
        const std::string& path,
        const MemoryProfileSpec* expected_spec = nullptr);

    void close() noexcept;

    bool is_open() const noexcept;
    MemoryProfileSpec spec() const;
    CapabilitySet capabilities() const;

    IKnowledgeUnitStore& units();
    IComponentStore& components();
    IProjectionStore& projections();
    IEmbeddingStore& embeddings();

    RetrievalResult retrieve(const RetrievalPlan& plan);
    CreateOrGetResult create_or_get_unit(const CreateUnitRequest& request);
    UpdateUnitResult update_unit(
        KnowledgeUnitId unit_id,
        std::uint64_t expected_revision,
        const MutableUnitPatch& patch);
    BulkCreateOrGetResult create_or_get_units(
        const std::vector<CreateUnitRequest>& requests);

    CompactionWorker& compaction();
    bool has_compaction() const;

    StackStats stats() const;
    SchemaInfo schema_info() const;
};
```

### 7.2. Запись unit

```cpp
struct GraphEdgeId final {
    std::array<std::uint8_t, 16> bytes{};
};

// One authoritative semantic relation; storage writes two physical orientations.
struct GraphEdge final {
    GraphEdgeId edge_id;
    ScopeId scope_id;
    KnowledgeUnitId from_unit_id;
    KnowledgeUnitId to_unit_id;
    EdgeKind edge_kind;
    RelationClass relation_class = RelationClass::Semantic;
    GraphEdgePayload payload;
    std::vector<KnowledgeUnitRef> evidence;
};

struct CreateUnitRequest {
    KnowledgeUnitEnvelope envelope;   // envelope.sources: vector<SourceRefSummary>, max ~3 inline, <=256 байт excerpt preview каждый
    std::optional<QAPayload> qa_payload;
    std::optional<FactPayload> fact_payload;
    std::optional<ChunkPayload> chunk_payload;
    std::optional<ConversationEpisodePayload> episode_payload;
    std::optional<CompiledArticlePayload> article_payload;
    std::optional<std::vector<SourceRef>> full_source_refs; // requires enable_full_source_refs
    std::vector<ComponentVariant> components;
    std::vector<SearchProjection> projections;
    std::vector<GraphEdge> graph_edges;
    std::optional<double> importance_score;
};

struct MutableUnitPatch {
    std::optional<std::string> display_text;
    std::optional<std::vector<ComponentVariant>> components;
    std::optional<std::vector<SearchProjection>> projections;
    std::optional<std::vector<GraphEdge>> graph_edges;
    std::optional<std::vector<SourceRef>> full_source_refs;
    std::optional<double> importance_score;
};

struct CreatedUnit {
    KnowledgeUnitId unit_id;
    std::vector<JobId> enqueued_jobs;
};

struct ExistingUnit {
    KnowledgeUnitId unit_id;
};

using CreateOrGetResult = std::variant<CreatedUnit, ExistingUnit>;

struct UpdatedUnit {
    KnowledgeUnitId unit_id;
    std::uint64_t new_revision = 0;
    std::vector<JobId> enqueued_jobs;
};

struct UnitNotFound {};

struct StaleUnitRevision {
    std::uint64_t expected_revision = 0;
    std::uint64_t actual_revision = 0;
};

struct ImmutableIdentityChanged {};

using UpdateUnitResult = std::variant<
    UpdatedUnit,
    UnitNotFound,
    StaleUnitRevision,
    ImmutableIdentityChanged>;
```

`create_or_get_unit` is create/dedupe only. If the content key already exists it
returns `ExistingUnit` and does not mutate the existing unit. Mutable updates
use `update_unit` with `expected_revision`; stale revisions return
`StaleUnitRevision` without writes. `MutableUnitPatch` collection fields use
`nullopt` for "leave unchanged", an empty vector for "clear this collection" and
a non-empty vector for "replace the whole collection". Fine-grained collection
deltas are deferred until a future explicit `CollectionPatch<T>` contract.
`update_unit` may change only fields listed as mutable in
`knowledge-units-roadmap.md` §4, increments envelope revision, regenerates
affected projections and updates secondary indexes in one transaction.

### 7.3. Retrieval

```cpp
enum class MissingProjectionPolicy : uint8_t {
    ReturnEmpty,
    ScheduleRecompute,
    UseExplicitFallbackRoute,
};

enum class ProjectionRouteInputKind : uint8_t {
    Corpus,
    PriorRouteCandidates,
};

enum class ProjectionRouteActivation : uint8_t {
    Root,
    FallbackOnly,
};

enum class BudgetExhaustionAction : uint8_t {
    ReturnPartial,
    DropRoute,
    FailIfRequired,
};

struct DenseProjectionRoute {
    std::string route_id;
    ProjectionRouteActivation activation = ProjectionRouteActivation::Root;
    ProjectionKind projection_kind = ProjectionKind::Original;
    std::string model_id;
    std::string model_version;
    std::size_t candidate_limit = 0;
    ProjectionRouteInputKind input_kind = ProjectionRouteInputKind::Corpus;
    std::optional<std::string> parent_route_id;
    std::size_t input_candidate_limit = 0;
    FusionStrategy fusion_strategy = FusionStrategy::RRF;
    MissingProjectionPolicy on_missing = MissingProjectionPolicy::ReturnEmpty;
    std::optional<std::string> fallback_route_id;
    std::optional<RetrievalIoBudget> io_budget;
    std::optional<TieredRerankBudget> rerank_budget;
    BudgetExhaustionAction on_budget_exhaustion = BudgetExhaustionAction::ReturnPartial;
};

struct RetrievalAccessContext {
    // Immutable normalized grants issued by the host, never caller-selected plan input.
    std::string principal_id;
    std::vector<std::string> role_ids;
    std::vector<std::string> jurisdictions;
    std::vector<ScopeId> authorized_scope_ids;
    std::optional<double> trust_score;
    std::vector<std::string> policy_claims;
    std::string issuer_id;
    std::string claims_version;
};

enum class RetrievalCompletion : uint8_t {
    Complete,
    BudgetExhausted,
    RouteDropped,
    RequiredRouteFailed,
};

struct RetrievalPlan {
    std::string raw_query;
    std::vector<ProjectionQueryVariant> query_variants;
    QueryType query_type = QueryType::Unknown;

    std::vector<ScopeId> scope_ids;
    std::optional<ScopeId> default_scope;
    RetrievalAccessContext access_context;

    std::vector<MemoryTier> tiers = {MemoryTier::Hot, MemoryTier::Warm};
    RetrievalMode mode = RetrievalMode::Hybrid;

    std::vector<RetrieverSpec> retrievers;
    std::vector<DenseProjectionRoute> dense_routes;  // M2 exact projection routes
    std::optional<BoundedQueryPlan> bounded_query_plan; // M2 decomposition

    std::vector<KnowledgeUnitKind> kinds;
    std::optional<TemporalWindow> temporal_window;  // M1 single-axis compatibility
    std::optional<TemporalQuery> temporal_query;    // M2+ tagged bi-temporal frontier
    std::optional<SpeakerScopePolicy> speaker_filter;
    std::optional<TypedMetadataFilter> metadata_filter;

    double associative_timeout_ms = 50.0;
    double targeted_timeout_ms = 4000.0;

    size_t candidate_pool_size = 200;
    size_t limit = 32;
    std::optional<RetrievalIoBudget> io_budget;  // M2 physical read/decode cap
    std::optional<ContextBudget> context_budget;

    std::optional<DecayPolicy> decay_policy_override;
    std::optional<DenseIndexMode> dense_index_mode_override;
    // Если задан — retrieval использует указанный mode вместо профильного default.
};

struct RetrievalResult {
    std::vector<RetrievalHit> hits;
    std::optional<Context> assembled_context;
    RetrievalTrace trace;
    RetrievalCompletion completion = RetrievalCompletion::Complete;
};
```

`RetrievalAccessContext` is issued by the embedding host after authentication
and authorization. `authorized_scope_ids` is normalized, contains no empty or
duplicate scope, and is the complete scope grant for this request. A validated
plan first normalizes its requested scope set as follows:

1. A non-empty `scope_ids` list is the exact requested set; it must contain no
   empty or duplicate entries, every entry must be granted, and an optional
   `default_scope` must be both granted and a member of that exact set.
2. An empty `scope_ids` list with a present `default_scope` requests exactly
   that one scope; the default must be granted.
3. An empty `scope_ids` list without `default_scope` is a validation error.
4. Normalization never expands an empty request to all `authorized_scope_ids`.

A caller therefore cannot grant itself access by placing an arbitrary `ScopeId`
in the plan. `Visibility::Scope` requires both a normalized requested scope and
the matching host grant. Missing `trust_score` fails a policy that specifies
`minimum_trust`. Required fixtures cover a cross-scope request, an ungranted
default, an empty request without default, and the guarantee that an empty list
does not fan out to every host grant.

Dense route validation requires globally unique route IDs and one acyclic graph
containing both `parent_route_id` and `fallback_route_id` edges. A root route
may read the corpus. A `PriorRouteCandidates` route must name a parent and has
an explicit bounded `input_candidate_limit`. A `FallbackOnly` route is never
scheduled as a root: it runs only when its parent selected
`UseExplicitFallbackRoute` because the requested projection is absent or
incompatible. The executor runs a route at most once for one
`(branch_id, route_id, input-candidate-set identity)` tuple; repeated fallback
edges reuse the existing outcome rather than duplicate I/O or candidates.

`RetrievalAccessContext` is immutable input from the embedding application. The
host authenticates its principal and issues role, jurisdiction and other
policy-relevant claims; the memory library neither authenticates identities nor
consults hidden host-global state. `AccessPolicy` evaluates this context before
candidate creation and again before materialization.

`MissingProjectionPolicy` is a route-availability policy: `missing` means the
requested collection or descriptor is absent or incompatible. Sparse per-unit
coverage, an empty search result, a stale row and budget exhaustion are separate
outcomes and must not trigger an implicit whole-query fallback.

`PriorRouteCandidates` requires a non-empty `parent_route_id` and a nonzero
`input_candidate_limit`; `Corpus` requires neither. The planner validates the
route DAG, parent order and bounds before execution. The default QAPair profile
uses `QAQuestion` from `Corpus`; `QAAnswer` is a bounded
`PriorRouteCandidates` rerank. Corpus-wide answer search is explicit,
benchmark-gated opt-in.

The profile, branch and route budgets are hard maxima. A route's effective cap
is the field-wise minimum of all enabled levels. The plan order is the
deterministic admission order: every known-cost read/decode is admitted only
when it fits both the route allowance and shared query counter, and unused
allowance remains available to later routes. `0` is invalid for an engaged cap;
an absent optional budget means that level supplies no additional cap. A future
parallel executor must reproduce this observable accounting until a separate
fair-sharing contract is accepted.

On budget exhaustion, a route follows its declared `on_budget_exhaustion`.
`ReturnPartial` may participate in fusion only with an explicit partial outcome;
`DropRoute` contributes no hits; `FailIfRequired` makes the query result
`RequiredRouteFailed`. No `BudgetExhausted` result may claim exhaustive top-K
semantics.

## M2+ Retrieval Hooks

### IQueryTransformer Hook (M2+)

Reference: HyDE (arXiv:2212.10496), Rewrite-Retrieve-Read (arXiv:2305.14283).

LLM-driven query transformation перед retrieval. C++ ядро не зависит от LLM; adapters реализуют конкретные стратегии.
Keep history-aware rewrite separate from generic rewrite: the former receives a
bounded dialogue summary / previous-turn query context and is evaluated on
multi-turn follow-up fixtures, including over-expansion cases where stale
conversation context must NOT be injected into the new query.

```cpp
class IQueryTransformer {
public:
    virtual ~IQueryTransformer() = default;

    struct QueryTransformationContext {
        Query current_query;
        std::optional<std::string> bounded_dialogue_summary;
        std::vector<Query> selected_previous_turns;
        std::vector<std::string> correction_markers;
        std::size_t max_generated_variants = 5;
        std::size_t max_history_tokens = 0;
    };

    struct QueryVariant {
        std::string text;
        std::string transformer_id;  // "hyde", "rewrite", "self_rag"
        double weight = 1.0;
    };

    virtual std::vector<QueryVariant> transform(
        const QueryTransformationContext& context) = 0;
};

// HyDE implementation:
class HydeQueryTransformer final : public IQueryTransformer {
public:
    std::vector<QueryVariant> transform(
        const QueryTransformationContext& context) override {
        // 1. LLM генерирует hypothetical answer for context.current_query (external).
        // 2. Embed hypothesis через IEmbedder.
        // 3. Return как QueryVariant с transformer_id="hyde".
    }
};

// Rewrite implementation:
class RewriteQueryTransformer final : public IQueryTransformer {
public:
    std::vector<QueryVariant> transform(
        const QueryTransformationContext& context) override {
        // LLM rephrases context.current_query в 3-5 вариантов.
    }
};

// History-aware rewrite implementation:
class HistoryAwareRewriteQueryTransformer final : public IQueryTransformer {
public:
    std::vector<QueryVariant> transform(
        const QueryTransformationContext& context) override {
        // Uses bounded_dialogue_summary, selected_previous_turns, and
        // correction_markers. The adapter must be stateless by default:
        // hidden session state makes tests, concurrency, and replay
        // reproducibility brittle.
    }
};
```

Integration:
  `MemoryStack::retrieve(plan)` builds `QueryTransformationContext` from the
  current query plus explicit, bounded session context and calls the transformer
  before retriever'ами.
  Каждый `QueryVariant` даёт свой набор кандидатов.
  RRF fusion combines across all variants.

### IRetrievalEvaluator Hook (M2+)

Reference: Self-RAG (arXiv:2310.11511), CRAG (arXiv:2401.15884).

Post-retrieval оценка качества candidates. Может trigger corrective action (re-search с другим transformer).

```cpp
class IRetrievalEvaluator {
public:
    virtual ~IRetrievalEvaluator() = default;

    struct RetrievalDecision {
        enum class Action { Accept, Reject, CorrectiveSearch, FallbackToLLM };
        Action action;
        std::optional<std::string> corrective_query;
        double confidence;
    };

    virtual RetrievalDecision evaluate(
        const Query& q,
        const std::vector<RetrievalHit>& hits) = 0;
};

// CRAG implementation:
class CragRetrievalEvaluator final : public IRetrievalEvaluator {
public:
    RetrievalDecision evaluate(
        const Query& q,
        const std::vector<RetrievalHit>& hits
    ) override {
        // Lightweight evaluator (fine-tuned T5 или rule-based).
        // Confidence < threshold → CorrectiveSearch с web fallback.
    }
};
```

Integration:
  `MemoryStack::retrieve(plan)` вызывает `evaluator.evaluate()` после retriever'ов.
  `Action::CorrectiveSearch` → повторный retrieval с расширенным query.
  `Action::FallbackToLLM` → return empty context (LLM отвечает без retrieval).

## 8. Default Memory Stacks (готовые профили)

Готовые спецификации в namespace `MemoryProfiles::`.

### 8.1. BasicRagStack

```cpp
inline MemoryProfileSpec BasicRag() {
    MemoryProfileSpec s;
    s.name = "basic_rag";
    s.envelope_schema_version = 1;
    s.enable_lexical_bm25f = true;
    s.enable_dense_vectors = false;
    s.enable_graph = false;
    s.enable_usage_stats = false;
    s.enable_qa_payload = false;
    s.enable_fact_payload = false;
    s.enable_compaction = false;
    s.context_budget = ContextBudget(
        /*total_tokens=*/4096,
        /*qa_tokens=*/0,
        /*chunk_tokens=*/3072,
        /*graph_tokens=*/0,
        /*summary_tokens=*/512,
        /*evidence_tokens=*/256);
    s.hybrid_config = HybridRetrievalConfig(
        FusionStrategy::RRF,
        /*rrf_k_constant=*/60.0,
        /*retriever_weights=*/std::vector<double>{1.0},
        /*candidate_pool_size=*/200);
    // BasicHybridRag(IEmbedder&) is the explicit dense opt-in variant.
    return s;
}
```

### 8.2. QAKnowledgeBaseStack

```cpp
inline MemoryProfileSpec QAKnowledgeBase() {
    MemoryProfileSpec s;
    s.name = "qa_kb";
    s.enable_lexical_bm25f = true;
    s.enable_dense_vectors = true;
    s.enable_qa_payload = true;
    s.enable_usage_stats = true;
    s.enable_temporal_validity = true;
    s.enable_compaction = false;
    s.context_budget = ContextBudget(
        /*total_tokens=*/2048,
        /*qa_tokens=*/1024,
        /*chunk_tokens=*/512,
        /*graph_tokens=*/0,
        /*summary_tokens=*/0,
        /*evidence_tokens=*/256);
    s.dense_index_config = DenseIndexConfig(
        DenseIndexMode::Exact);
    return s;
}
```

The default dense projection for this profile is `QAQuestion`; `QAAnswer` is a
bounded M2+ rerank/candidate opt-in and `QACombined` is an experiment enabled
only after calibration. The profile never treats a vector block as the source
of question or answer bytes. Its detailed per-route candidate budgets and
fusion policy live in the retrieval plan, so an answer-heavy route cannot
silently turn a short FAQ lookup into a full double-index scan.

### 8.3. AgentLongTermMemoryStack

```cpp
inline MemoryProfileSpec AgentLongTermMemory() {
    MemoryProfileSpec s;
    s.name = "agent_ltm";
    s.enable_lexical_bm25f = true;
    s.enable_dense_vectors = true;
    s.enable_graph = true;
    s.enable_usage_stats = true;
    s.enable_temporal_validity = true;
    s.enable_fact_payload = true;
    s.enable_embedding_meta = true;
    s.enable_compaction = true;
    s.enable_context_planner = true;
    s.enable_knowledge_activation = true;
    s.context_budget = ContextBudget(
        /*total_tokens=*/4096,
        /*qa_tokens=*/512,
        /*chunk_tokens=*/2048,
        /*graph_tokens=*/512,
        /*summary_tokens=*/512,
        /*evidence_tokens=*/256);
    // DecayPolicy(DecayMode::Exponential, half_life_ms, use_boost,
    //             cooldown_ms, self_echo_suppression, cold_threshold)
    s.decay_policy = DecayPolicy(
        DecayMode::Exponential,
        /*half_life_ms=*/7.0 * 24.0 * 3600.0 * 1000.0,
        /*use_boost=*/0.35,
        /*cooldown_ms=*/60.0 * 1000.0,
        /*self_echo_suppression=*/0.3,
        /*cold_threshold=*/0.01);
    s.write_policy = WritePolicy(
        WriteFlushTrigger::OnTimer,
        /*flush_interval_ms=*/30.0 * 1000.0,
        /*size_threshold_bytes=*/0,
        /*importance_threshold=*/0.4,
        /*dedupe_distance_threshold=*/0.15,
        /*allow_supersede=*/true,
        /*allow_merge=*/true,
        /*allow_episode_compaction=*/true);
    // dedupe_distance_threshold: skip/merge if cosine_distance <= threshold (canonical, см. WritePolicy).
    s.hybrid_config = HybridRetrievalConfig(
        FusionStrategy::RRF,
        /*rrf_k_constant=*/60.0,
        /*retriever_weights=*/std::vector<double>{1.0, 1.0, 1.5, 0.5, 1.0},
        /*candidate_pool_size=*/200);
    s.default_retrieval_mode = RetrievalMode::Hybrid;
    s.dense_index_config = DenseIndexConfig(
        DenseIndexMode::Exact,
        /*bit_count=*/0,
        /*encoder_id=*/"");
    // M2+: BinaryCandidateFilter or Hnsw is an explicit benchmark-gated override.
    return s;
}
```

### 8.4. SpeakerAwareChatStack

```cpp
inline MemoryProfileSpec SpeakerAwareChat() {
    MemoryProfileSpec s;
    s.name = "speaker_chat";
    s.enable_lexical_bm25f = true;
    s.enable_dense_vectors = false;
    s.enable_speaker = true;
    s.enable_temporal_validity = true;
    s.enable_conversation_episode = true;
    s.enable_usage_stats = true;
    s.context_budget = ContextBudget(
        /*total_tokens=*/3000,
        /*qa_tokens=*/0,
        /*chunk_tokens=*/1500,
        /*graph_tokens=*/0,
        /*summary_tokens=*/800,
        /*evidence_tokens=*/256);
    s.speaker_policy = SpeakerScopePolicy(
        /*include_self=*/true,
        /*include_owner=*/true,
        /*include_cohost=*/true,
        /*include_audience=*/false,
        /*attribute_quote=*/true);
    s.write_policy = WritePolicy(
        WriteFlushTrigger::OnTimer,
        /*flush_interval_ms=*/5.0 * 1000.0,
        /*size_threshold_bytes=*/0,
        /*importance_threshold=*/0.2,
        /*dedupe_distance_threshold=*/0.0,
        /*allow_supersede=*/true,
        /*allow_merge=*/true,
        /*allow_episode_compaction=*/true);
    // dense_index_config: not set (default DenseIndexMode::Exact) — keyword-heavy профиль.
    return s;
}
```

### 8.5. CompiledWikiStack

```cpp
inline MemoryProfileSpec CompiledWiki() {
    MemoryProfileSpec s;
    s.name = "compiled_wiki";
    s.enable_lexical_bm25f = true;
    s.enable_dense_vectors = true;
    s.enable_compiled_article = true;
    s.enable_fact_payload = true;
    s.enable_embedding_meta = true;
    s.enable_compaction = true;
    s.enable_context_planner = true;
    s.enable_knowledge_activation = true;
    s.context_budget = ContextBudget(
        /*total_tokens=*/6000,
        /*qa_tokens=*/0,
        /*chunk_tokens=*/2000,
        /*graph_tokens=*/0,
        /*summary_tokens=*/2000,
        /*evidence_tokens=*/512);
    s.dense_index_config = DenseIndexConfig(
        DenseIndexMode::Exact,
        /*bit_count=*/0,
        /*encoder_id=*/"");
    // M2+: BinaryCandidateFilter or Hnsw is an explicit benchmark-gated override.
    return s;
}
```

### 8.6. TemporalFactStoreStack

```cpp
inline MemoryProfileSpec TemporalFactStore() {
    MemoryProfileSpec s;
    s.name = "temporal_facts";
    s.enable_lexical_bm25f = true;
    s.enable_dense_vectors = false;
    s.enable_temporal_validity = true;
    s.enable_fact_payload = true;
    s.enable_graph = true;
    s.context_budget = ContextBudget(
        /*total_tokens=*/3000,
        /*qa_tokens=*/0,
        /*chunk_tokens=*/1500,
        /*graph_tokens=*/800,
        /*summary_tokens=*/0,
        /*evidence_tokens=*/256);
    // dense_index_config: not set (default DenseIndexMode::Exact) — smaller corpus, recency-based.
    return s;
}
```

### 8.7. FullResearchMemoryStack

```cpp
inline MemoryProfileSpec FullResearchMemory() {
    MemoryProfileSpec s = AgentLongTermMemory();
    s.name = "full_research";
    s.enable_speaker = true;
    s.enable_qa_payload = true;
    s.enable_conversation_episode = true;
    s.enable_compiled_article = true;
    s.dense_index_config = DenseIndexConfig(
        DenseIndexMode::Exact,
        /*bit_count=*/0,
        /*encoder_id=*/"");
    // M2+: BinaryCandidateFilter or Hnsw is an explicit benchmark-gated override.
    return s;
}
```

### 8.8. CognitiveRuntimeMemory

Optional overlay for ADELIA-like cognitive runtimes. It stores durable runtime
origin, perspective, causal context, task/decision/procedure records and
progressive-disclosure hints. It does not execute actions, schedule nodes or
own runtime authority.

```cpp
inline MemoryProfileSpec CognitiveRuntimeMemory() {
    MemoryProfileSpec s = AgentLongTermMemory();
    s.name = "cognitive_runtime";
    s.enable_context_planner = true;
    s.enable_knowledge_activation = true;
    s.enable_durable_global_identity = true;
    s.enable_cognitive_trace_components = true;
    s.enable_sequence_replay = true;
    s.enable_procedural_activation = true;
    s.enable_graph = true;
    s.enable_full_source_refs = true;
    // Replica reconciliation is A3 and remains opt-in.
    s.enable_replica_reconciliation = false;
    return s;
}
```

This profile selects `FullSourceRefs` for component evidence plus two explicit
opt-in DBI deltas: `global_unit_id_to_local_id` for
`DurableGlobalIdentity` and `runtime_sequence_index` for `SequenceReplay` and
bounded `KnownAtSequence`. A0/A2 runtime components still use
`unit_components`; causal and procedure relations use the existing graph DBIs;
raw event/tool payloads use `ResourceBodyStore` when enabled. Replica
reconciliation remains A3 and is not selected by this profile.

See [`usage-memory-models.md`](usage-memory-models.md) for an operator decision guide on choosing between Karpathy 3-layer, A-MEM, lifemodel, NOUZ, Mem0/Letta/Zep, Блок фактов, and dual-layer patterns for your workload.

## 9. Capability Matrix

| Capability | BasicRag | QAKB | AgentLTM | SpeakerChat | CompiledWiki | TemporalFact | FullResearch |
|---|---|---|---|---|---|---|---|
| LexicalBm25F | yes | yes | yes | yes | yes | yes | yes |
| DenseVectors | no | yes | yes | no | yes | no | yes |
| QAPairs | no | yes | no | no | no | no | yes |
| TemporalValidity | no | yes | yes | yes | no | yes | yes |
| UsageStats | no | yes | yes | yes | no | no | yes |
| Decay | no | no | yes | no | no | no | yes |
| SpeakerAttribution | no | no | no | yes | no | no | yes |
| Compaction | no | no | yes | no | yes | no | yes |
| GraphRelations | no | no | yes | no | no | yes | yes |
| EmbeddingMigration | no | no | yes | no | yes | no | yes |
| CompiledArticles | no | no | no | no | yes | no | yes |
| ConversationMemory | no | no | no | yes | no | no | yes |
| FullSourceRefs | no | opt | opt | opt | yes | opt | yes |
| TranslationProjection | opt | opt | opt | opt | opt | opt | opt |
| KnowledgeActivation | no | opt | yes | opt | yes | opt | yes |
| ArtifactProvenance | no | no | no | no | opt | no | yes |

`opt` — capability не включена по умолчанию, но может быть добавлена через minor in-place migration.
`TranslationProjection` is present only when the translation adapter contract
creates a persisted `TranslatedCanonical` projection with complete provenance.
Its value types, policy normalization and profile-signature contribution are
owned exclusively by `translation-adapters-roadmap.md`. Query-time language
routing/pivoting is a separate M2 planner capability, not a signal to persist
translated text.
`KnowledgeActivation` capability is present iff
`enable_knowledge_activation == true`; it uses existing canonical storage until
a profile-specific DBI delta is added to the physical manifest.
`ArtifactProvenance` capability is present iff the profile declares an
artifact catalog and BlobStore backend; it requires `FullSourceRefs` and the
artifact-aware SourceRef/EvidenceAnchor schema.
`DurableGlobalIdentity` capability is present iff
`enable_durable_global_identity == true`; it selects the
`global_unit_id_to_local_id` profile delta and supplies the portable occurrence
identity required by bounded runtime sequence replay and import/export.
`CognitiveTraceComponents` capability is present iff
`enable_cognitive_trace_components == true`; it requires `FullSourceRefs` and
does not by itself select a replay DBI. `SequenceReplay` capability is present
iff `enable_sequence_replay == true`; it selects `runtime_sequence_index` and
requires `CognitiveTraceComponents` plus `DurableGlobalIdentity`.
`ProceduralActivation` capability is present iff
`enable_procedural_activation == true`. `ReplicaReconciliation` capability is
present iff `enable_replica_reconciliation == true`; it remains A3 and opens no
DBI until `dbi-manifest.yaml` adds a concrete partition delta.

## 10. Validation Rules

При `MemoryStack::open()` валидируются инварианты. Ошибки валидации выбрасывают `std::invalid_argument` с диагностикой.

| Правило | Если нарушено |
|---|---|
| Включён LexicalBm25F или DenseVectors (хотя бы один retrieval path) | "Stack must enable at least one retrieval path" |
| Decay=true требует UsageStats=true | "DecayPolicy requires UsageStatsComponent" |
| SpeakerAttribution=true требует UsageStats=true | "SpeakerAttribution requires UsageStats" |
| Compaction=true требует UsageStats=true | "Compaction requires UsageStats" |
| CompiledArticles=true требует Compaction=true | "CompiledArticles requires Compaction" |
| ConversationMemory=true требует SpeakerAttribution=true | "ConversationMemory requires SpeakerAttribution" |
| KnowledgeActivation=true требует ContextPlanner=true | "KnowledgeActivation requires context planner" |
| CognitiveTraceComponents=true требует Components + FullSourceRefs | "Cognitive trace components require components and source refs" |
| SequenceReplay=true требует CognitiveTraceComponents + DurableGlobalIdentity + `runtime_sequence_index` profile delta | "SequenceReplay requires durable identity and runtime sequence index" |
| ProceduralActivation=true требует KnowledgeActivation=true и GraphRelations=true | "ProceduralActivation requires knowledge activation and graph relations" |
| ReplicaReconciliation=true требует SequenceReplay=true | "ReplicaReconciliation requires sequence replay" |
| EmbeddingMigration=true требует DenseVectors=true | "EmbeddingMigration requires DenseVectors" |
| ArtifactProvenance=true requires FullSourceRefs=true | "ArtifactProvenance requires full source refs" |
| DecayPolicy: cooldown_ms >= 0, half_life_ms > 0 (если mode != None) | "Invalid DecayPolicy" |
| WritePolicy: flush_interval_ms > 0 (если trigger == OnTimer) | "Invalid WritePolicy" |
| ContextBudget: сумма per-block <= total_tokens | "ContextBudget overflow" |
| HybridRetrievalConfig: retriever_weights.size() == number of retrievers | "Weights size mismatch" |
| Persisted translation requires a valid adapter-owned policy, canonical BCP-47 language and projection provenance | "Persisted translation requires active TranslationPolicy" |
| envelope_schema_version в spec соответствует сохранённому | "Schema version mismatch (migration required)" |

Дополнительно: `profile_signature` (hash от spec) сохраняется в `schema_info` DBI. При `open_existing()` без `expected_spec` сравнивается с текущим и диагностируется drift.

## 11. Layer Architecture

```
Layer 4: Applications (examples/, apps/)
  ChatBotApp, WikiMaintainerApp, StreamMemoryApp, ...

Layer 3: Memory Stacks (см. секцию 7)
  MemoryStack — runtime объект с handles к storage/retrieval
  MemoryProfileSpec — декларативная спецификация

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
  Используют Layer 1 + Layer 2 через интерфейсы
```

Ключевые свойства:

- Layer 4 не зависит от Layer 1 (только через Layer 2 + Layer 3).
- Layer 3 не зависит от Layer 4.
- Layer 2 не зависит от Layer 3 (можно использовать напрямую).
- Runtime services могут вызываться из любого layer, но сами не зависят от конкретного MemoryStack.

## 12. MDBX Storage Layout Ownership

Этот документ является canonical source для data model, profiles,
capabilities and `MemoryStack::open(spec)` validation. Единственный canonical
physical MDBX manifest живёт в
[`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) §5.5, а
authoritative DBI budget checkpoint — в §5.5.1 того же TZ.

Следствия:

- новые physical DBI names добавляются сначала в TZ §5.5/§5.5.1;
- этот roadmap может ссылаться на capabilities and logical stores, но не
  дублирует key/value layout таблиц;
- `content_key_to_unit_id` является частью canonical manifest TZ §5.5 и
  используется для `KnowledgeUnitKey -> KnowledgeUnitId` dedupe/migration
  lookup;
- runtime queues, compaction handoffs, resource bodies, persistent caches and
  mode-specific dense indexes учитываются как explicit profile deltas в TZ
  §5.5.1 or in the owning roadmap before implementation;
- если разделы расходятся, physical manifest из TZ побеждает, а этот документ
  должен быть обновлён как stale reference.

### 12.1. Capability-to-Storage Mapping

`MemoryProfileSpec` выбирает logical capabilities: QAPairs, TemporalFact,
ConversationMemory, CompiledArticles, FullSourceRefs,
DenseVectors, LexicalBm25F, GraphRelations, TemporalValidity,
SpeakerAttribution, UsageStats, and Compaction. `chunk_payloads` is a canonical always-open DBI, and
`ChunkPayload` is required when writing `KnowledgeUnitKind::Chunk`; it is not a
separate capability selector and does not require an additional kind selector.
Concrete DBI names and table types for these capabilities are enumerated in TZ
§5.5.

### 12.2. Runtime and Mode-Specific Deltas

Compaction uses downstream `TaskQueue`/`JobStore` from
`runtime-services-roadmap.md` §4.6. Its MDBX recipe is
`mdbx-containers-extension-tz.md` §12.5 and currently costs +5 queue DBI per
persistent queue plus +1 `compaction_handoffs` DBI when compaction is enabled.

Host-owned LLM request/response caches, raw `ResourceBodyStore`, HNSW, binary
bucket indexes and other mode-specific dense indexes are not part of
the baseline physical manifest unless their owning roadmap adds an explicit
profile-delta row to TZ §5.5.1.

## 13. Maturity Levels

Normative scope lives in [`milestones.md`](milestones.md). The subsections
below remain as a detailed architectural orientation, but the staged split
`M0`, `M1a`, `M1b`, `M1c`, `M2`, and `M2+` in `milestones.md` wins on any
scope conflict.

### 13.1. M0 — MVP

Цель: запустить первый end-to-end pipeline на минимальном стеке.

Включено:

- KnowledgeUnitEnvelope (lean hot-path envelope, ~18 полей: id, kind, scope_id, primary_text, display_text, lifecycle_state, sources, timestamps, revision, content_hash, content_hash_recipe_version, lineage fields, priority_weight).
- primary_text + display_text в envelope; sources — inline `vector<SourceRefSummary>` (max ~3 на unit, <=256 байт excerpt preview каждый).
- KnowledgeUnitId монотонный, opaque (`uint64_t`). `KnowledgeUnitKey = (kind, ScopeId, ContentHash)` и content-key secondary index (`content_key_to_unit_id`) — для dedupe/supersedence готов с M0.
- SearchProjection::Original создаётся с самого начала (минимальный: unit_id → primary_text). BM25F работает через projection model с M0 (не flat fallback).
- Lifecycle FSM с состояниями Active / Superseded / Deprecated / Erased (4 durable states). SoftSuppressed/cooldown — runtime state в UsageStatsComponent, не durable lifecycle.
- Минимальный QAPayload (только canonical_question + answer + optional category + last_verified_at_ms) для QAKnowledgeBaseStack с M1b.
- Расширенный QAPayload (question_variants, frequency ranking) — M2+.
  Bi-temporal history is M2+ and belongs to AM-13.
- ChunkPayload (minimal) для BasicRagStack с M0. Остальные per-kind payloads (FactPayload, ConversationEpisodePayload, CompiledArticlePayload) — M1.
- BasicRagStack; QAKnowledgeBaseStack enters at M1b with `QAPairs`.
- Чтение через ILexicalIndex, запись через простой write API.
- `scope_id` обязателен во всех secondary/range keys, которые обслуживают
  tenant/project/agent-scoped lookup или range query. Primary lookup by globally
  unique `UnitId` является явным исключением.

Не включено (M1+): Components, QAPayload/QAKnowledgeBaseStack, остальные per-kind payloads (FactPayload, ConversationEpisodePayload, CompiledArticlePayload), дополнительные SearchProjections (QAQuestion/QAAnswer/Summary), DecayPolicy/WritePolicy, Compaction, full SourceRef DBI (в M0 — только inline summary), bi-temporal history. Provider-specific LLM caching is outside this library at every maturity level.

Ship-it критерии:

- BasicRag retrieve+write на 10k unit работает с p95 latency ≤ 50 ms under
  the reproducible benchmark contract in `milestones.md`.
- Все unit-тесты на envelope serialization проходят.
- Lifecycle FSM покрыт тестами для всех 4 durable states.

### 13.2. M1 — Production

M1 is staged by `milestones.md`: M1a hybrid retrieval, M1b structured
knowledge, M1c runtime maintenance. A profile may only claim the part it
implements and tests.

Цель: полноценные профили для реальных use-cases.

Включено (поверх M0):

- Все компоненты (UsageStats, Speaker, Temporal, EmbeddingMeta,
  CompactionMeta, ActivationMetadata).
- Все payload-компоненты (QAPayload, FactPayload, ConversationEpisodePayload, CompiledArticlePayload, ChunkPayload).
- SearchProjections DBI с 4 стандартными kind: Original, QAQuestion, QAAnswer, Summary.
- BM25F по projections (projection_kind в posting keys).
- DecayPolicy + WritePolicy + SpeakerScopePolicy.
- MemoryStacks: AgentLongTermMemoryStack, SpeakerAwareChatStack, CompiledWikiStack, TemporalFactStoreStack, FullResearchMemoryStack.
- CompactionWorker с базовыми job types: DecayJob, DedupeJob, ArchiveColdJob.
- Provider-neutral `ContextFingerprint` / `PromptPrefixDescriptor` output for
  host-side LLM cache adapters; no provider cache implementation in core.
- IRetrievalTrace с метриками cache_hit_rate, anti_loop_skip_rate, decay_score_distribution.
- Knowledge activation metadata for deterministic DomainMap/Playbook routing
  when `KnowledgeActivation` is enabled.

Не включено (M2+):

- Полный набор projection kinds (DenseContextual, Bm25Fields, CodeSymbols).
- Multi-vector embeddings с cross-model RRF.
- Compaction jobs: MergeJob, SummaryPromotionJob, EmbeddingRecomputeJob.
- CLI tool.
- Distributed scope routing.
- Eval pipeline с intent-класс-специфичными golden datasets.
- Bi-temporal storage and true bi-temporal eval.

Ship-it критерии:

- Все 7 MemoryStacks открываются и проходят round-trip write/read тесты.
- AgentLongTermMemory retrieve с decay показывает Recall@10(hybrid) ≥ 1.20 * Recall@10(BM25-only) на golden dataset.
- Cooldown-фильтр работает корректно (fact не возвращается в течение cooldown_ms после retrieval).
- Compaction worker не теряет данные при crash (write-ahead job state).
- Anti-loop подсвинок (self_echo_suppression) применяется в DecayAwareRetriever.

### 13.3. M2 — Advanced

Цель: полный набор возможностей, edge cases, production hardening.

Включено (поверх M1):

- Полный набор projection kinds.
- Multi-vector embeddings (multi-model, multi-projection) с cross-model RRF.
- Compaction: MergeJob, SummaryPromotionJob, EmbeddingRecomputeJob.
- CLI tool (`agent-memory-cli`).
- Eval pipeline с intent-классами: TemporalValidityLookup, SupersedenceChain, CooldownRespect, SpeakerFilter, CompactionHandoff, CrossDomainCoverage, ProcedureActivation.
- Distributed scope routing (multi-process / multi-host scope namespaces).
- Compaction metrics и self-tuning.
- Embedding recompute migration с progress tracking.

Ship-it критерии:

- 50+ golden test cases покрывают все intent-классы.
- p95 latency ≤ 2x baseline BM25 для всех профилей under the reproducible
  benchmark contract in `milestones.md`.
- Migration script `basic_rag → agent_ltm` работает без потери данных.
- CLI tool покрывает inspect/stats/check/vacuum/reindex/profile-info/profile-migrate.

## 14. Profile Migration Strategy

### 14.1. In-place minor upgrade (допустимо)

Добавление одной capability к существующему профилю:

```cpp
// Пример: BasicRag → BasicRagWithUsageStats
MemoryProfileSpec old_spec = MemoryProfiles::BasicRag();
MemoryProfileSpec new_spec = old_spec;
new_spec.enable_usage_stats = true;
new_spec.decay_policy = MemoryProfiles::DefaultAgentDecay();

// migration in-place:
// 1. Создать unit_components, usage_by_last_access and usage_by_cooldown DBIs
//    (если не существуют)
// 2. Заполнить нулевыми UsageStatsComponent для всех существующих units
//    and write bounded range-index entries for last access / cooldown
// 3. Обновить profile_signature в schema_info
// 4. Vacuum (опционально)
```

Это additive: новая DBI создаётся, существующие данные не трогаются. `profile_signature` обновляется.

Content-key index (`content_key_to_unit_id`) входит в canonical physical
manifest; legacy DBs without it require additive profile-signature migration
before enabling content-addressed dedupe.

### 14.2. Major migration (новая БД)

Смена профиля с существенными изменениями:

```cpp
// Пример: BasicRag → AgentLongTermMemory
auto source = MemoryStack::open("old.mdbx", MemoryProfiles::BasicRag());
auto target = MemoryStack::open("new.mdbx", MemoryProfiles::AgentLongTermMemory());
for (auto& unit : source.units().scan_all()) {
    CreateUnitRequest req;
    req.envelope = unit.envelope();
    req.components = derive_components_from_envelope(unit.envelope());
    req.projections = generate_default_projections(unit);
    target.create_or_get_unit(req);
}
```

Migration tool встроен в CLI как `agent-memory-cli profile-migrate`.

### 14.3. Schema versioning

`envelope_schema_version` и `component_schema_versions[]` хранятся в `schema_info`. При `open_existing()`:

- Версия совпадает → нормальный запуск.
- Версия выше (target старее source) → ошибка "downgrade not supported".
- Версия ниже (target новее source) → автоматическая additive migration (если поддержана) или ошибка "migration script required".

## 15. Cross-References

Этот документ связан с:

- `knowledge-base-roadmap.md` — детальная спецификация envelope + retrieval flow (будет переписан после ADR).
- `knowledge-units-roadmap.md` — per-kind payload-компоненты (будет переименован в `knowledge-payloads-roadmap.md`).
- `lexical-search-roadmap.md` — BM25F по projections.
- `optimization-roadmap.md` — vector/binary storage, scope-aware secondary indexes.
- `mdbx-containers-extension-tz.md` — TypeDiscriminatedTable для components, MultiTableWriter.
- `architecture.md` — 4-слойная модель + Maturity Levels.
- `guides/mdbx-stack-boundaries.md` — границы ответственности между agent-memory-cpp и external/mdbx-containers/.
- `policies-roadmap.md` — детальная спецификация DecayPolicy/WritePolicy/SpeakerScopePolicy с диапазонами и defaults.
- `compaction-roadmap.md` — CompactionWorker, job types, handoff structure.
- `runtime-services-roadmap.md` — AsyncIndexer, WriteGate and host-boundary context planning.
- `cli-roadmap.md` — agent-memory-cli target.
- `guides/related-projects.md` — ландшафтная карта (mem0 / Cognee / Zep / Graphiti для direct competitors, FAISS / hnswlib / USearch / sqlite-vec для sister libraries) и cross-project benchmark plan.
- `memory-lifecycle-governance-roadmap.md` — M2+ lifecycle governance:
  bi-temporal knowledge, derivation graph, causal relations, progressive
  retrieval, expanded memory evaluation, mutation policy, entity resolution
  and typed query/MCP safety.

## 16. Recommended Implementation Order

Шаги упорядочены по зависимостям. Каждый шаг — отдельный PR или под-PR.

### Шаг 1: Envelope + базовые DBI

- Создать `KnowledgeUnitEnvelope` struct (lean hot-path envelope, ~18 полей: id, kind, scope_id, primary_text, display_text, lifecycle_state, sources, timestamps, revision, content_hash, content_hash_recipe_version, lineage fields, priority_weight).
- MDBX DBI: default-open physical set from
  `mdbx-containers-extension-tz.md` §5.5, including identity DBI
  `knowledge_units`, `content_key_to_unit_id`, `knowledge_units_by_kind` and
  `schema_info`.
- Сериализация envelope через flat binary (msgpack или custom).
- `MemoryStack::open()` для пустой spec (только envelope + lexical).
- Lifecycle FSM с 4 durable states (Active, Superseded, Deprecated, Erased). SoftSuppressed/cooldown — runtime state в UsageStatsComponent, не durable lifecycle.

### Шаг 1.5: Revision semantics + stale-filter

- Определить revision++ правила: инкремент только на mutable retrieval-view
  changes, которые сохраняют stored `content_hash` (`primary_text`,
  `display_text` если retrieval-relevant, non-identity source summaries,
  projections regeneration, lifecycle_state только durable transitions).
  Identity hash material changes (`kind`, `scope_id`, canonical payload/body
  identity, hash recipe without preserving migration) create a new `UnitId` plus
  supersede/merge lineage. НЕ инкрементить на UsageStats / Decay /
  priority_weight / embedding metadata / soft suppression / cooldown. Explicit list
  durable lifecycle transitions, инкрементящих revision: Active→Superseded,
  Active→Deprecated, Active→Erased, Superseded→Erased, Superseded→Deprecated.
- `LexicalPosting` and `EmbeddingProjectionMeta` carry the same `ProjectionVersionRef`.
- Retrieval-time: skip a posting or dense row unless its complete projection version equals the active canonical projection version.
- Подробности — §17.11 Stale-filter pattern.

### Шаг 2: Scope-aware keys + metadata filters

- Все DBI keys начинаются с `scope_id`.
- `ScopeId` type (variant<"global", UserScope, AgentScope, ProjectScope, AppScope>).
- `metadata_filters` DBI через `ReverseIndexTable`.
- Profile validation: scope_id обязателен для всех write.

### Шаг 3: LexicalBm25F capability

- BM25F baseline (k1=1.5, b=0.75, Okapi IDF).
- DBI: `inverted_token_to_unit`, `field_to_postings`, `lexical_*_stats`.
- Tokenization (UTF-8 canonical, code-aware).
- `ILexicalIndex` интерфейс.
- BasicRagStack готов.

### Шаг 4: DenseVectors capability + binary signature

- `IDenseIndex` интерфейс.
- DBI: `embedding_vectors`, `embedding_meta` (минимум — embedding_meta опционально).
- ExactVectorIndex baseline.
- Binary signature index (optimization-roadmap).
- BasicRagStack расширен.

### Шаг 4.5: Multi-mode IDenseIndex backends

- `DenseIndexConfig` (см. §6.2) и `DenseIndexMode` enum (5 значений: Exact, BinaryCandidateFilter, BinaryOnly, ApproximateVector, Hnsw) как часть MemoryProfileSpec.
- 4 base backends + HNSW M2+:
  - `ExactVectorIndex` (M0+, baseline, brute-force float).
  - `BinaryCandidateFilterIndex` (optional M1 experiment: binary filter + float rerank).
  - `BinaryOnlyIndex` (M2+, experimental/compact, Hamming ranking).
  - `ApproximateVectorIndex` (M2+, experimental, decoder support для binary → approx vector → rerank).
  - `HnswVectorIndex` (M2+ experimental, 5th mode — mainline ANN backend; см. optimization-roadmap.md §"HNSW Vector Index").
- Mode-specific DBI creation в `MemoryStack::open(spec, mode)` с capability-aware логикой (см. §12.7).
- `BinaryCandidateFilter` may become a profile default only after its
  application-owned `BinaryBucketIndexDescriptor`, `BinaryBucketSearchBudget`,
  active-generation publication and stale-hydration checks are present **and**
  that profile passes the documented exact-baseline benchmark gate. The
  baseline starts with one short-key table; multi-index hashing is a separately
  benchmarked M2+ option, not an automatic profile expansion.
- `RetrievalPlan.dense_index_mode_override` для runtime override (см. §7.3).
- Storage tradeoffs и capacity estimates документированы (см. §17.12).
- Quality benchmarks per mode на golden dataset: exact-oracle/candidate/reranked
  recall, p50/p95/p99 latency, posting probes/visits, decoded bytes, RSS,
  on-disk bytes, and targeted update/delete/compaction cost.
- Per-stack default в `MemoryProfiles::*` фабриках (см. §8).

### Шаг 5: Component infrastructure

- `TypeDiscriminatedTable` в mdbx-containers (если ещё не реализован).
- `ComponentStore` (CRUD с tag-prefix).
- Компоненты: UsageStatsComponent, SpeakerComponent, TemporalComponent,
  CompactionMetaComponent,
  ActivationMetadataComponent.
- DBI: `unit_components` (TypeDiscriminatedTable).
- Валидация инвариантов (Decay требует UsageStats и т.д.).

### Шаг 6: Payload components per kind

- QAPayload + QALookup slot.
- FactPayload.
- ConversationEpisodePayload.
- CompiledArticlePayload.
- ChunkPayload (по умолчанию для Chunk kind).
- per-kind DBI: `qa_payloads`, `fact_payloads`, и т.д.

### Шаг 7: SearchProjections

- `SearchProjection` struct.
- DBI: `unit_projections` (scope, unit, kind, revision).
- Generation rules per kind: Original, QAQuestion, QAAnswer, Summary,
  optional TranslatedCanonical.
- BM25F индексирует projections (projection_kind в posting keys).

### Шаг 8: Graph + Temporal indexes

- `GraphEdgeId`, `GraphEdgePayload` struct + EdgeKind enum. `GraphEdge` is the
  authoritative logical relation, including endpoints, relation class, payload
  and evidence.
- DBI: `graph_edges_by_src`, `graph_edges_by_dst` are two atomic physical
  orientations of one logical edge; packed adjacency is rebuildable only.
- Bounded graph expansion (max_depth, max_edges, budget_tokens).
- Single-axis TemporalComponent + DBI: `temporal_unit_index` for M1b; the
  M2+ bi-temporal range indexes remain owned by AM-13. A separate
  event range index requires a future authoritative `EventRecord` entity.
- `floating subgraph` как retrieval view.

### Шаг 9: Speaker + Session indexes

- SpeakerComponent обязательный для SpeakerChatStack.
- DBI: `speaker_to_units`, `session_to_units`.
- SpeakerScopePolicy в RetrievalPlan.

### Шаг 10: Decay + Anti-loop

- DecayPolicy в MemoryProfileSpec.
- DecayAwareRetriever: применяет `apply_decay_and_boost(base_score, usage, policy, now_ms, last_used_at_ms)` — экспоненциальный decay multiplier на `base_score` плюс аддитивный `use_boost * log1p(use_count)`.
- AntiLoopCooldown и self-echo suppression — отдельный шаг `apply_filters(score, usage, speaker, agent_self_id, now_ms, policy)`, мультипликативные фильтры, применяются ПОСЛЕ `apply_decay_and_boost`.

```cpp
// Decay multiplier (applied to base_score, not use_count)
double decay_factor(uint64_t elapsed_ms, double half_life_ms) {
    return std::exp(-double(elapsed_ms) / half_life_ms);
}

// Final scoring (additive boost on top of decayed base):
double apply_decay_and_boost(
    double base_score,
    const UsageStatsComponent& usage,
    const DecayPolicy& policy,
    uint64_t now_ms,
    uint64_t last_used_at_ms) {
    double elapsed = double(now_ms - last_used_at_ms);
    double factor = decay_factor(elapsed, policy.half_life_ms);
    return base_score * factor + policy.use_boost * std::log1p(double(usage.use_count));
}

// Cooldown и self-echo — отдельные мультипликативные фильтры, применяются ПОСЛЕ:
double apply_filters(
    double score,
    const UsageStatsComponent& usage,
    const SpeakerComponent* speaker,
    SpeakerId agent_self_id,
    uint64_t now_ms,
    const DecayPolicy& policy) {
    if (usage.cooldown_until_ms > now_ms) {
        score *= policy.cooldown_factor;        // default 0.1
    }
    if (speaker && speaker->speaker_id == agent_self_id) {
        score *= policy.self_echo_suppression;  // default 0.3
    }
    return score;
}
```

### Шаг 11: WritePolicy + WriteGate

- WriteGate применяет WritePolicy: trigger, importance_threshold, dedupe_distance_threshold.
- Flush policies (OnTimer, OnSizeThreshold, OnImportance).
- Bulk write с atomic enqueue compaction jobs.

### Шаг 12: All default MemoryStacks

- Готовые спецификации: BasicRag, QAKnowledgeBase, AgentLongTermMemory, SpeakerAwareChat, CompiledWiki, TemporalFactStore, FullResearchMemory.
- Round-trip тесты для каждой (open → write 100 units → close → reopen → verify).

### Шаг 13: Retrieval Plan + Hybrid + RRF

- HybridRetriever orchestrator.
- RRF fusion с дефолтными весами per stack.
- ContextBudget per-block + trim order.
- IntentRouter (lightweight non-LLM).
- IRetrievalTrace с метриками.

### Шаг 14: CompactionWorker (M1 минимум)

- ICompactionJob interface.
- Job types: DecayJob, DedupeJob, ArchiveColdJob.
- Downstream `TaskQueue`/`JobStore` из `runtime-services-roadmap.md` §4.6;
  MDBX storage recipe — `mdbx-containers-extension-tz.md` §12.5.
- Job lifecycle transitions use the typed transition pattern from
  `runtime-services-roadmap.md` §4.6.1: state-specific transition functions
  consume persisted `JobRecord` + `ClaimToken` when required and return both
  primary-record and queue-index deltas.
- CompactionHandoff structure.
- Async worker thread.

### Шаг 15: Eval pipeline (M1 baseline)

- Golden dataset: ≥50 test cases, ≥3 интента.
- RetrievalMetrics: Recall@K, MRR, NDCG@K, ContextPrecision, NoAnswerAccuracy, CitationFidelity, Latency p50/p95/p99.
- HybridLiftTarget: Recall@10(hybrid) ≥ 1.20 * Recall@10(BM25-only).

### Шаг 15.5: Memory lifecycle governance (M2+)

- BiTemporalComponent and range indexes for valid-time vs recorded-time
  queries.
- MemoryMutationPolicy for append-only, supersede, merge, mutable, immutable
  and operator-confirmed profiles.
- AbstractionComponent and relation indexes for derivation/support/contradiction
  links.
- MemoryRelationKind for causal and consistency-aware retrieval.
- Progressive retrieval traces from high-level models/summaries down to raw
  evidence.
- Expanded memory-eval metrics from
  `memory-lifecycle-governance-roadmap.md` AM-17.
- Deterministic-first entity resolution (AM-19) with LLM fallback only as an
  auditable proposal.
- Typed query/tool filters (AM-20) so LLM output never becomes schema syntax.
- Logical index separation and an LLM-free baseline read path (AM-21).

### Шаг 16: CLI tool (M2, отложен)

- `agent-memory-cli` target.
- Команды: inspect, stats, check, vacuum, reindex, profile-info, profile-migrate, dump-unit, dump-components, run-eval.

### Шаги R31-R34 (M2): Retrieval hook contracts

- R31 (M2): IQueryTransformer interface + HydeQueryTransformer + RewriteQueryTransformer adapters.
- R31a (M2): HistoryAwareRewriteQueryTransformer adapter + multi-turn eval fixtures.
- R32 (M2): IRetrievalEvaluator interface + CragRetrievalEvaluator + SelfRagEvaluator adapters.
- R33 (M2): HyDE integration в HybridRetriever.
- R34 (M2): CRAG corrective search loop.

## 17. Open Issues

Вопросы, требующие решения до или во время реализации.

### 17.1. Migration test fixtures

- Где хранить миграционные тестовые базы? В `tests/fixtures/` или отдельный `tests/migration/`?
- Как покрывать round-trip миграцию `basic_rag → agent_ltm` без ручного труда?

### 17.2. Profile drift detection

- Что делать, если `schema_info.profile_signature` не совпадает с текущим `spec`?
- Стратегии: error / auto-migrate / warn-and-continue?
- Решение: по умолчанию error, для additive capabilities — auto-migrate, для breaking — error + migration tool.

### 17.3. Embedding metadata size

- EmbeddingMeta хранит model_id + version. Сколько версий держать онлайн?
- Per unit может быть 5+ embedding versions (при активной migration).
- Решение: CompactionJob удаляет versions старше N дней при отсутствии ссылок.

### 17.4. Search projection regeneration

- Когда SearchProjection регенерируется? На write? На compaction tick? По требованию?
- Решение: на create/update (для active revision), если projection kind включён в `CreateUnitRequest` / `MutableUnitPatch`. Старые revisions остаются до compaction purge.
- Projections создаются для каждого unit'а в момент write (Original — всегда; QAQuestion/QAAnswer — только для QAPair; Summary — только для Summary/CompiledArticle).

### 17.5. Cyrillic morphology

- Tokenization backend для русского языка: optional или required?
- Внешняя зависимость (например, ICU) vs встроенный лёгкий стеммер?
- Решение: required в M1, optional backend через `AGENT_MEMORY_ENABLE_MORPHOLOGY` CMake flag.

### 17.6. CompactionWorker thread safety

- Один worker на MemoryStack или пул?
- Синхронизация с on-write operations (MultiTableWriter)?
- Решение: один worker thread, write/compaction используют одну транзакцию через MultiTableWriter (compaction jobs ждут в очереди).

### 17.7. Host LLM cache scope

- Provider request/response caches are host-owned. Core emits only a stable
  `ContextFingerprint` / `PromptPrefixDescriptor`; the host decides whether a
  provider cache key is scope-aware and may never treat a cached response as
  canonical memory.
- Решение: да, scope_id входит в cache_key, чтобы разные пользователи не видели чужие responses.

### 17.8. Embedding model migration

- EmbeddingRecomputeJob требует source embeddings + target model. Где взять target model?
- Решение: target model регистрируется в EmbeddingModelRegistry (config-level), worker читает оттуда.

### 17.9. Multi-process / multi-host

- Multi-process: НЕ в scope для M0/M1/M2. mdbx-containers extension явно исключает multi-process.
- Возможно рассмотреть в виде future research-task после M2, не блокирует текущую работу.
- MDBX multi-process read-only режим теоретически доступен штатно без изменений в mdbx-containers, но не тестируется и не специфицируется.

### 17.10. AsyncIndexer batching

- AsyncIndexer батчит inserts в lexical/vector индексы. Где граница batch size?
- Решение: configurable, default 1000 units или 50 MB, whichever first.

### 17.11. Stale-filter pattern — revision-based per-record check

- `LexicalPosting.projection_version != active ProjectionVersionRef` → skip posting (defer to async reindex).
- `EmbeddingProjectionMeta.projection_version` mismatch → recompute or skip.
- Постинг может быть stale даже если unit не удалён (например, payload изменился, и в BM25F-индексе застрял старый term frequency).
- Реализация M0: per-posting check inline в retrieval pipeline.
- Реализация M2 (при >1M units): `unit_revision_index` DBI для batch reindex.
- `generation_index` (старая схема) УДАЛЁН; заменён на per-posting `unit_revision` + retrieval-time check (см. §18 Glossary).

### 17.12. Mode-aware storage estimates

Storage footprint существенно зависит от `DenseIndexConfig.mode` (см. §6.2, §12.7). Для corpus в 1M units с 768-мерными float32 embeddings:

```
Baseline (без dense index):
  N/A — pure lexical/keyword профиль.

Exact mode (DenseIndexMode::Exact):
  embedding_vectors: 1M × 768 × 4 bytes ≈ 3 GB.

BinaryCandidateFilter (optional M1 experiment; production choice only after evaluation):
  embedding_vectors: ~3 GB.
  binary_bucket_index (128-bit): 1M × 16 bytes ≈ 16 MB.

BinaryOnly (compact):
  binary_bucket_index (128-bit): ~16 MB.
  embedding_vectors: НЕ открывается.

ApproximateVector Safe (store_float_fallback == true):
  embedding_vectors: ~3 GB.
  binary_bucket_index: ~16 MB.
  decoder (~128-bit): ~400 KB (shared, in registry).

ApproximateVector Compact (store_float_fallback == false):
  embedding_vectors: НЕ открывается.
  binary_bucket_index: ~16 MB.
  decoder: ~400 KB.
```

Per-mode подробные таблицы см. в `optimization-roadmap.md` секция "Dense Index Modes". При планировании capacity mode selection — primary driver.

## References

- arXiv:2212.10496: "Precise Zero-Shot Dense Retrieval without Relevance Labels" (HyDE).
- arXiv:2305.14283: "Query Rewriting for Retrieval-Augmented Large Language Models".
- arXiv:2310.11511: "Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection".
- arXiv:2401.15884: "Corrective Retrieval Augmented Generation".
- arXiv:2004.04906: "Dense Passage Retrieval for Open-Domain Question Answering" (DPR).
- arXiv:2005.11401: "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks" (RAG).
- arXiv:2104.08663: "BEIR: A Heterogenous Benchmark for Zero-shot Evaluation of Information Retrieval Models".
- See also: guides/research-reading-map.md.

## 18. Glossary

- **Envelope** — минимальный lookup-critical набор полей KnowledgeUnit (id, kind, scope, primary_text, display_text, lifecycle, sources, timestamps, priority_weight).
- **LifecycleState** — durable state of a KnowledgeUnit. Values: Active, Superseded, Deprecated, Erased. Changes increment envelope.revision. SoftSuppressed/cooldown НЕ является LifecycleState value — хранится в UsageStatsComponent.cooldown_until_ms.
- **Component** — optional typed payload, прикреплённый к unit (operational или per-kind).
- **SearchProjection** — отдельное текстовое представление unit для конкретного retrieval method.
- **DenseIndexMode** — backend selection для dense vector retrieval (см. §6.2). Values: `Exact` (brute-force float, required M1a baseline and ground truth), `BinaryCandidateFilter` (optional M1 experiment: binary filter + float rerank), `BinaryOnly` (binary only, Hamming ranking, experimental/compact), `ApproximateVector` (binary + decoder → approx vector → rerank, experimental). Default per stack and mode-aware DBI mapping follow measured evaluation, not a M1a ship gate.
- **MemoryProfileSpec** — декларативная спецификация capabilities + policies.
- **MemoryStack** — runtime-объект, открытый через `MemoryStack::open(path, spec)`.
- **ScopeId** — namespace identifier для multi-tenancy.
- **Profile** — см. MemoryProfileSpec.
- **Stack** — см. MemoryStack.
- **Maturity Level** — M0/M1/M2, определяет ship-it критерии и scope функциональности.
- **Revision** — per-unit version, monotonically increasing per UnitId. Поле `KnowledgeUnitEnvelope.revision` (`uint64_t`). Инкрементится на mutable retrieval-view changes that preserve stored `content_hash`: `primary_text`, `display_text` если retrieval-relevant, non-identity source summaries, projections regeneration, lifecycle_state changed (только durable transitions: Active→Superseded, Active→Deprecated, Active→Erased, Superseded→Erased, Superseded→Deprecated). Смена identity hash material создаёт новый `UnitId` plus supersede/merge lineage. НЕ инкрементится на UsageStats / Decay / priority_weight / EmbeddingMeta / soft suppression / cooldown.
- **Generation** — per-resource / per-derived-record version, НЕ часть `KnowledgeUnitEnvelope`. Живёт в `ResourceManifest.generation` и projection metadata. Envelope-level versioning — это `revision`, не `generation`.
- **Stale filter** — per-record check: a lexical posting and dense row require a complete matching `ProjectionVersionRef`. M0: inline в retrieval pipeline; M2: projection-version indexes for batch reindex (см. §17.11).
- **Profile signature** — hash от full `MemoryProfileSpec`, including
  core capabilities and selectors; host LLM cache settings are deliberately
  excluded;
  используется для drift detection.
- **ADR** — Architecture Decision Record, раздел в этом документе с фиксированным решением.
