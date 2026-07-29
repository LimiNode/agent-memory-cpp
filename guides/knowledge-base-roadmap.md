# knowledge-base-roadmap.md

Спецификация knowledge base подсистемы `agent-memory-cpp`: компонентная модель данных (envelope + components + projections), retrieval contract, evaluation pipeline, ContextBuilder и cross-stack контракты. Документ опирается на архитектурные решения из `guides/memory-stacks-roadmap.md` и дополняет их retrieval-flow деталями.

Этот файл supersede'ит предыдущую версию, описывавшую монолитный `KnowledgeUnit` struct и `IKnowledgeUnitStore`-as-primary-table. Новая модель разделяет данные на три независимых слоя (см. ADR-001) и вводит capability-aware создание DBI.

> C++17 compliance: illustrative code (no std::span, no designated initializers). Decay formula — canonical (см. policies-roadmap.md §2.3). SourceRef — split на inline summary (envelope) + DBI (M1).

## 1. Purpose

Этот документ конкретизирует retrieval и evaluation слой поверх архитектурного манифеста `memory-stacks-roadmap.md`. Описывает:

- `KnowledgeUnitEnvelope` — lean lookup-critical contract with persisted identity hash.
- Components — operational + per-kind payload компоненты.
- `SearchProjection` — retrieval-specific text views.
- Domain stores — capability-aware I/O интерфейсы.
- Retrieval composition — план, retrievers, hybrid fusion, decay-aware scoring.
- `ContextAssembly` — budget-aware trim с citations.
- Evaluation & tracing — golden dataset, метрики, traces.

Cross-references: `guides/memory-stacks-roadmap.md` (ADR'ы, MemoryProfileSpec, MemoryStack, capability validation), `guides/knowledge-units-roadmap.md` (per-kind payload-компоненты), `guides/lexical-search-roadmap.md` (BM25F), `guides/optimization-roadmap.md` (vector/binary indexes), `guides/mdbx-containers-extension-tz.md` (storage primitives, canonical physical manifest).

Non-goals: BM25F scoring details, embedding model адаптеры, per-kind payload схемы, CompactionWorker, runtime services.

## 2. Cross-cutting Architecture References

Этот документ наследует ADR'ы из `memory-stacks-roadmap.md` (раздел 2, ADR Index). Ключевые для retrieval-flow решения:

- **ADR-001**: Envelope + Components + SearchProjections (NOT монолитный `KnowledgeUnit`). Минимальный I/O на hot path, lazy load компонентов.
- **ADR-002**: `MemoryProfileSpec` декларативный, `MemoryStack` — runtime-объект.
- **ADR-003**: Profile и Scope ортогональны. Все secondary indexes начинаются с `scope_id` в ключе.
- **ADR-005**: Search text в envelope ограничен `primary_text` (256-1024 байт), остальное через `SearchProjection`s.
- **ADR-007**: `embedding_meta` + `embedding_vectors` поддерживают multi-projection/multi-model (M2).
- **ADR-008**: Decay/anti-loop меняет retrieval score, не удаляет записи. Defaults для `AgentLongTermMemory`: half_life=7d, use_boost=0.35, cooldown=60s, self_echo=0.3.
- **ADR-011**: Lifecycle FSM — `Active` / `Superseded` / `Deprecated` / `Erased`. Anti-loop cooldown реализован через `UsageStatsComponent.cooldown_until_ms` (runtime state, не lifecycle).
- **ADR-013**: Runtime services (PromptCache, CompactionWorker, WriteGate, AsyncIndexer) — ортогональный слой, не встроены в retrieval.

## 3. KnowledgeUnitEnvelope (lean contract)

`KnowledgeUnitEnvelope` — минимальный lookup-critical набор полей (Layer A в ADR-001). Полная спецификация serialization version — в `memory-stacks-roadmap.md` секция 6.3.

```cpp
struct KnowledgeUnitEnvelope {
    KnowledgeUnitId id;             // стабильная, монотонная, не reuse (allocate(), НЕ content-hash)
    KnowledgeUnitKind kind;         // дискриминатор per-kind semantics
    ScopeId scope_id;               // multi-tenancy namespace
    std::string primary_text;       // 256-1024 байт, retrieval seed
    std::string display_text;       // LLM-friendly formatted text
    LifecycleState lifecycle_state; // Active / Superseded / Deprecated / Erased
    std::vector<SourceRefSummary> sources; // inline provenance summary, max 3 per unit, ≤256 байт каждое
    int64_t created_at_ms;
    int64_t updated_at_ms;
    int64_t observed_at_ms;         // когда source наблюдался
    uint64_t revision;              // монотонный per UnitId, increments on mutable retrieval-view changes (§3.5)
    ContentHash content_hash;        // persisted identity hash used by KnowledgeUnitKey
    uint16_t content_hash_recipe_version;
    double priority_weight;         // [0.0, 1.0], ranking boost
    std::vector<KnowledgeUnitId> supersedes;     // lineage вперёд (vector: может быть несколько predecessors)
    std::optional<KnowledgeUnitId> superseded_by; // lineage назад (single, immediate successor)
    std::optional<KnowledgeUnitId> derived_from; // compilation/aggregation origin
};
```

> **Замечание:** `KnowledgeUnitEnvelope` НЕ содержит поля `generation`. `generation` — per-resource / per-derived-record version, живёт в `ResourceManifest.generation` (per-resource) и в per-record metadata (`LexicalPosting.resource_generation`, `EmbeddingProjectionMeta` freshness token). Envelope-level versioning — это `revision` (uint64_t), не `generation`.

### 3.1. Lookup-critical поля

Hot path retrieval использует только `id`, `kind`, `scope_id`, `lifecycle_state`, `priority_weight` — без I/O компонентов.

### 3.2. Размер primary_text

`primary_text` — короткий текст для fallback BM25 (M0). Лимит 256-1024 байт enforced на write (`std::invalid_argument` при превышении). Retrieval предпочитает `SearchProjection` для длинного текста.

### 3.3. Per-kind правила генерации primary_text

При создании `CreateUnitRequest` с пустым `primary_text` generation function заполняет поле:

```cpp
std::string generate_primary_text(KnowledgeUnitKind kind, const ComponentView& components) {
    switch (kind) {
        case KnowledgeUnitKind::Chunk:             // 500 символов body + heading
            return components.chunk.body.substr(0, 500) + " | " + components.chunk.heading_path;
        case KnowledgeUnitKind::QAPair:            // canonical + variants
            return components.qa.canonical_question + " | " + join(components.qa.question_variants, " | ");
        case KnowledgeUnitKind::Fact:              // subject predicate object
            return components.fact.subject + " " + components.fact.predicate + " " + components.fact.object;
        case KnowledgeUnitKind::Summary:           return components.summary.full_text;
        case KnowledgeUnitKind::CompiledArticle:   return components.article.title + "\n" + components.article.body_first_paragraph;
        case KnowledgeUnitKind::ConversationEpisode: return components.episode.first_utterances(2);
        case KnowledgeUnitKind::Event:             return components.event.short_description;
        case KnowledgeUnitKind::Entity:            return components.entity.name + " (" + components.entity.type + ")";
        case KnowledgeUnitKind::Relation:          return components.relation.from_kind + " -[" + components.relation.edge_kind + "]-> " + components.relation.to_kind;
        default:                                   return "";  // Custom: caller provides
    }
}
```

### 3.4. Display text vs primary_text

`display_text` — отделён от `primary_text`: используется в `ContextBuilder` для LLM-friendly форматирования (markdown, code blocks inline). `primary_text` — для индексов, retrieval, trace logging. Разделение предотвращает "мусорный" текст в retrieval hits при красивом отображении в context.

### 3.5. Revision semantics (canonical)

`KnowledgeUnitKey = (kind, scope_id, content_hash)` is immutable for a given
`KnowledgeUnitId`. `content_hash` and `content_hash_recipe_version` are stored
in the envelope so storage can verify identity with one primary read. Changes
to hash material create a new `UnitId` plus supersede/merge lineage; they do not
mutate the old unit in place.

`content_hash` is a derived storage value, not caller authority. Upsert code
MUST compute it through the single versioned `compute_content_hash(kind,
CanonicalIdentityInput)` pipeline from `knowledge-units-roadmap.md` §4, then
persist both the hash and recipe version in the envelope. Caller-provided hashes
are accepted only as optional assertions and must be rejected on mismatch.

`revision++` on mutable content-view changes that do not alter
`KnowledgeUnitKey`:

- `primary_text` changed
- `display_text` changed (если retrieval-relevant)
- non-identity source summary changes
- `lifecycle_state` changed (только durable transitions):
  - `Active -> Superseded`, `Active -> Deprecated`, `Active -> Erased`
  - `Superseded -> Deprecated`, `Superseded -> Erased`
- `projections` regeneration

Hash material changes require a new `UnitId`:

- `kind` changed
- `scope_id` changed
- canonical payload/body identity changed (QAPayload/FactPayload/Chunk body
  digest/etc.)
- `content_hash_recipe_version` changed without a migration preserving the old
  stored hash

`revision` НЕ инкрементится на:

- `UsageStatsComponent` changes (`use_count`, `last_used_at_ms`, `cooldown_until_ms`, `soft_suppression_until_ms`) — runtime state, не content-bearing
- `Decay` scoring metadata changes
- `priority_weight` изменения (scoring metadata)
- `EmbeddingProjectionMeta` changes (производные данные; используется freshness token для stale-check)
- Anti-loop cooldown state (`UsageStatsComponent.cooldown_until_ms`) — runtime, не content-bearing

`DecayAwareRetriever` / `HybridRetriever` stale-filter:

- Skip a derived hit unless its complete `ProjectionVersionRef` matches the active canonical projection value.
- Подробности stale-filter pattern — `memory-stacks-roadmap.md` §17.11.

## 4. Components (operational + per-kind)

Layer B в ADR-001. Два семейства: operational (общие для всех kinds) и per-kind payloads (kind-specific данные).

### 4.1. Operational components

```cpp
struct UsageStatsComponent {
    uint64_t use_count;
    int64_t last_used_at_ms;
    int64_t last_injected_at_ms;
    uint64_t injection_count;
    int64_t cooldown_until_ms;        // anti-loop guard (runtime state)
    int64_t soft_suppression_until_ms; // runtime state
};
// UsageStatsComponent — runtime state. НЕ content-bearing, НЕ инкрементирует envelope.revision.
// Lifecycle FSM transitions не затрагивают UsageStatsComponent.

struct SpeakerComponent {
    std::string speaker_id;           // agent / user / cohost id
    SpeakerScope speaker_scope;       // Self / Owner / Cohost / Audience
    std::optional<UtteranceId> utterance_id;
    std::optional<SessionId> session_id;
    std::optional<KnowledgeUnitId> reply_to_unit_id;
};

struct TemporalComponent {
    int64_t valid_from_ms;
    int64_t valid_until_ms;           // 0 = still valid
    int64_t observed_at_ms;
    int64_t recorded_in_session_ms;
};

struct VectorRef {
    ScopeId scope_id;
    KnowledgeUnitId unit_id;
    ProjectionKind projection_kind;
    std::string model_id;
    std::string model_version;
};

// Canonical freshness identity for every derived search projection. It is
// shared by lexical postings, dense rows, binary buckets and index jobs.
struct ProjectionVersionRef {
    uint64_t unit_revision_at_build = 0;
    uint64_t projection_revision = 0;
    uint64_t derivation_generation = 0;
    std::string derivation_fingerprint;
};

// This is the durable application projection identity. It is not a
// VectorStore local id, an ANN node id, or an ownership claim over vector bytes.
// It lives in the dedicated `embedding_meta` store, not in `unit_components`.
struct EmbeddingProjectionMeta {
    ScopeId scope_id;
    KnowledgeUnitId unit_id;
    ProjectionKind projection_kind;
    std::string model_id;             // e.g. "bge-small-en-v1.5"
    std::string model_version;
    ProjectionVersionRef projection_version;
    std::string model_descriptor_fingerprint;
    std::string vector_codec_descriptor_fingerprint;
    int64_t computed_at_ms;
};

// A dense row is active only when its full ProjectionVersionRef equals the
// active canonical projection. A unit revision alone is insufficient for a
// translated or regenerated projection whose derivation changes independently.

struct CompactionMetaComponent {
    int64_t last_decay_at_ms;
    int64_t last_dedupe_check_at_ms;
    std::optional<KnowledgeUnitId> merged_into;
    double last_decay_score;
};
```

`TemporalComponent` is the M1 single-axis temporal component. M2+ bi-temporal
semantics (`valid_from_ms` / `valid_until_ms` vs `recorded_at_ms` /
`invalidated_at_ms`) are specified separately in
[`memory-lifecycle-governance-roadmap.md`](memory-lifecycle-governance-roadmap.md)
AM-13 and should not be claimed by this component alone.

### 4.2. Per-kind payload components

Per-kind данные живут в выделенных payload-компонентах. Подробные спецификации — в `guides/knowledge-units-roadmap.md`.

```cpp
struct QAPayload {
    std::string canonical_question;
    std::vector<std::string> question_variants;
    std::string answer;
    std::string category;
    int64_t last_verified_at_ms;
};

struct FactPayload {
    std::string subject;
    std::string predicate;
    std::string object;
    std::string value_type;           // "string" | "int" | "float" | "datetime"
    std::optional<std::string> unit;
};

struct ChunkPayload {
    uint64_t byte_offset;
    uint64_t byte_length;
    std::vector<std::string> heading_path;
    std::vector<std::string> code_blocks;
    std::vector<std::string> symbols;
};

struct ConversationEpisodePayload {
    std::vector<UtteranceId> utterance_ids;
    int64_t started_at_ms;
    int64_t ended_at_ms;
    uint32_t turn_count;
    std::vector<std::string> participants;
};

struct CompiledArticlePayload {
    std::string title;
    std::string owner;
    std::vector<std::string> readers;
    std::vector<std::string> keywords;
    ArticleStatus status;             // Draft | Review | Published | Archived
    int64_t last_compiled_at_ms;
    std::vector<KnowledgeUnitRef> derived_from;
};
```

### 4.3. Storage layout для components

- **Operational components** — DBI `unit_components` через
  `TypeDiscriminatedTable` with physical key
  `CompositeKey<ComponentKind, UnitId> -> TypedComponentValue`. The tag is part
  of the physical key, not only a prefix inside the value, so multiple
  components for the same unit cannot overwrite one another. Stable
  application-owned type ids and fail-closed validation are mandatory.
- **Per-kind payloads** — отдельные DBI: `qa_payloads`, `fact_payloads`, `conversation_episode_payloads`, `compiled_article_payloads`, `chunk_payloads`. Key = UnitId.
- `MultiTableWriter` обеспечивает atomic coordinated writes (envelope + components + projections + secondary indexes в одной транзакции).

## 5. SearchProjections (retrieval-specific views)

Layer C в ADR-001. `SearchProjection` — отдельное текстовое представление unit, оптимизированное под конкретный retrieval method.

```cpp
enum class ProjectionKind : uint16_t {
    Original,           // исходный текст unit (BM25F input)
    QAQuestion,         // canonical + variants (QAPair)
    QAAnswer,           // answer (QAPair)
    QACombined,         // M2+: versioned "Question + Answer" dense experiment
    Summary,            // short summary
    CodeSymbols,        // extracted symbols (Chunk)
    DenseContextual,    // M2: contextual header для dense
    // M2+: DenseQuery, DensePassage
};

struct SearchProjection {
    UnitId unit_id;
    ScopeId scope_id;
    ProjectionKind kind;
    ProjectionVersionRef version;
    int64_t valid_from_ms;
    int64_t valid_until_ms;             // 0 = still valid
    std::string text;
    std::string index_id;
    std::optional<VectorRef> vector_ref;
};
```

### 5.1. Storage layout

```
unit_projections
    key   = (scope_id, UnitId, ProjectionKind, version.projection_revision)
    value = SearchProjection
```

Sparse storage retains only generated projections. `version.projection_revision`
increments on regenerated text views; a changed derivation package increments
`derivation_generation` and changes `derivation_fingerprint`. Old versions
remain until compaction purge. A row is active only when its complete
`ProjectionVersionRef` equals the profile's active projection version.

### 5.2. Per-kind generation rules

| Kind | Original | QAQuestion | QAAnswer | QACombined | Summary | CodeSymbols |
|---|---|---|---|---|---|---|
| Chunk | full body | — | — | — | — | extracted symbols |
| QAPair | question + answer | canonical + variants | answer | M2+ versioned question + answer | — | — |
| Fact | subject predicate object | — | — | — | — | — |
| Summary | full text | — | — | — | redundant | — |
| CompiledArticle | title + body | — | — | — | short | — |
| ConversationEpisode | flattened | — | — | — | — | — |
| Event | description | — | — | — | — | — |
| Entity | name + type + aliases | — | — | — | — | — |
| Relation | from → edge → to | — | — | — | — | — |

Generation rules детерминированы: given the same unit + components, the same projections are emitted.

## 6. Domain Stores (capability-aware)

`MemoryStack::open(path, spec)` создаёт только нужные DBI. Validation в `memory-stacks-roadmap.md` секция 10 гарантирует, что capabilities согласованы. DBI budget follows `dbi-manifest.yaml`: logical expanded peak 61, configured `max_dbs` 96, reserved headroom 35, and minimum required headroom 16.

### 6.1. IKnowledgeUnitStore (всегда открыт)

Primary table для envelope CRUD. Backend — MDBX DBI `knowledge_units` (key = UnitId). Secondary index `knowledge_units_by_kind`.

```cpp
class IKnowledgeUnitStore {
public:
    virtual ~IKnowledgeUnitStore();
    // put/get/scan работают с KnowledgeUnitId (monotonic, allocate(), НЕ content-hash)
    virtual std::optional<KnowledgeUnitEnvelope> find(const KnowledgeUnitId& id) const = 0;
    virtual void upsert(KnowledgeUnitEnvelope envelope) = 0;
    virtual bool erase(const KnowledgeUnitId& id) = 0;
    virtual std::vector<KnowledgeUnitId> scan_by_kind(KnowledgeUnitKind kind) const = 0;
    virtual std::vector<KnowledgeUnitId> scan_by_scope(const ScopeId& scope_id) const = 0;
    // content-addressing lookup: KnowledgeUnitKey — отдельная struct (content-hash), служит
    // для dedup/upsert-by-content; возвращает текущий Id, под которым живёт этот content
    virtual std::optional<KnowledgeUnitId> find_by_content_key(const KnowledgeUnitKey& key) const = 0;
};
```

### 6.2. IComponentStore (если любой компонент включён)

Backend — `TypeDiscriminatedTable`, DBI `unit_components`, physical key
`(ComponentKind, UnitId)`. Открывается если
`enable_usage_stats`/`enable_temporal_validity`/`enable_speaker`/
`enable_compaction`/`enable_knowledge_activation`. `enable_dense_vectors`
opens the separate `embedding_meta` store; it is not a component selector.

```cpp
class IComponentStore {
public:
    virtual ~IComponentStore();
    virtual std::optional<ComponentVariant> get(ComponentKind kind, const KnowledgeUnitId& unit_id) const = 0;
    virtual void set(ComponentKind kind, const KnowledgeUnitId& unit_id, const ComponentVariant& value) = 0;
    virtual void erase(ComponentKind kind, const KnowledgeUnitId& unit_id) = 0;
};
```

### 6.3. IProjectionStore (всегда для indexed retrieval)

Backend — DBI `unit_projections`. Методы: `put`, `list(unit_id, kind_filter?)`, `delete_revision(unit_id, kind, revision)`.

### 6.4. IEmbeddingStore (если DenseVectors=true)

Backend — `embedding_meta` + `embedding_vectors` DBI. Методы: `put_vector(model_id, kind, unit_id, vector)`, `get_vector(...)`, `get_meta(kind, unit_id)`. Multi-model — M2.

`IEmbeddingStore` owns rebuildable projection bytes, not canonical knowledge
payloads. Its durable lookup identity is `(scope_id, model_id, model_version,
projection_kind, unit_id)`; a physical vector backend may map
that identity to a local slot or block offset, but that mapping is private and
rebuildable. `DenseRetriever` treats every backend result as a candidate and
must hydrate the active unit/projection, verify the complete
`EmbeddingProjectionMeta` freshness token, and then apply lifecycle, scope,
authority and provenance rules before returning a `RetrievalHit`.

### 6.5. Per-payload stores (по capability)

Каждый store открывается если соответствующий payload-компонент включён. Подробные интерфейсы — в `guides/knowledge-units-roadmap.md`:

- `IQAKnowledgeBase` — если `enable_qa_payload = true`.
- `IFactStore` — если `enable_fact_payload = true`.
- `IEpisodeStore` — если `enable_conversation_episode = true`.
- `IArticleStore` — если `enable_compiled_article = true`.
- `IChunkStore` — backed by canonical always-open `chunk_payloads`; required
  when writing `KnowledgeUnitKind::Chunk`.

### 6.6. Типичный DBI usage

BasicRag ~10, QAKnowledgeBase ~14, AgentLTM ~22, FullResearch ~30.

## 7. Retrieval Composition

Retrieval — directed graph typed retrievers. `HybridRetriever` orchestrator применяет fusion strategy.

### 7.1. RetrievalPlan (cross-stack)

`RetrievalPlan` — value type, передаваемый между retrievers. Полная спецификация — в `memory-stacks-roadmap.md` секция 7.3. Retrieval-ориентированные поля: `raw_query`, `query_type`, `scope_ids`, `tiers`, `mode`, `retrievers[]`, `kinds[]`, `temporal_window?`, `speaker_filter?`, `metadata_filter?`, `candidate_pool_size=200`, `limit=32`, `context_budget?`, `decay_policy_override?`.

For M2 decomposition, a `RetrievalPlan` may carry one immutable
`BoundedQueryPlan`. A host or optional query transformer may construct it, but
the core only validates and executes its bounded retrieval branches; it does
not run an agent loop or require an LLM.

```cpp
struct QueryBranchBudget {
    std::size_t candidate_limit = 0;
    std::size_t token_limit = 0;
    std::uint64_t latency_budget_ms = 0;
    std::optional<RetrievalIoBudget> io_budget;
};

struct QueryBranch {
    std::string branch_id;
    std::string text;
    std::string derivation;  // original, rewrite, decomposition, translation, HyDE
    std::optional<std::string> parent_branch_id;
    QueryBranchBudget budget;
};

struct BoundedQueryPlan {
    std::vector<QueryBranch> branches;
    std::size_t max_branches = 0;
    std::size_t total_candidate_limit = 0;
    std::size_t total_token_limit = 0;
    std::uint64_t total_latency_budget_ms = 0;
};
```

Validation rejects an empty plan, duplicate branch ids, cycles, branch counts
above `max_branches`, or aggregate budgets above their declared totals. Fusion
deduplicates candidates by canonical unit/revision identity, and the retrieval
trace records branch-to-hit-to-context-block lineage. M0/M1 use one original
branch; decomposition, multi-hop routing and multilingual pivots are M2
opt-in behavior.

### 7.1.1. Exact Projection Routes And Missing Data (M2)

An embedding lookup always names one exact `(projection_kind, model_id,
model_version)` route. The canonical `DenseProjectionRoute` and
`MissingProjectionPolicy` live in `memory-stacks-roadmap.md` Section 7.3.
Storage returns that active projection or `NotFound`; it
must never silently substitute `Original`, `QAQuestion`, `QAAnswer`,
`DenseContextual`, or another sibling merely because dimension and model happen
to match.

`UseExplicitFallbackRoute` is valid only when `fallback_route_id` names another
route in the same plan, has its own candidate budget, and does not create a
cycle. The planner owns this semantic decision; storage owns only exact lookup.
The trace records a missing projection, recompute request, or fallback route so
evaluation can distinguish a true primary-route hit from a recovery path.

### 7.1.2. CandidateSet And Physical I/O Budget (M2)

`CandidateSet` is an execution-local set of eligible canonical unit ids after
scope, strict authority, lifecycle, temporal, speaker, source, and other
pushdown-safe filters. It is not a durable index and never replaces final
revision/provenance validation during hydration.

```cpp
enum class CandidateSetRepresentation : uint8_t {
    All,
    Empty,
    SortedIds,
    Bitmap,
};
```

The planner chooses `SortedIds` for sparse sets and an implementation-selected
bitmap representation for dense sets; a Roaring-compatible implementation is
an optional optimisation, not a core dependency. Retrievers intersect their
candidates with the set before expensive vector decode, lexical scoring or
graph expansion only under one of two contracts:

1. CandidateSet construction and canonical hydration use the same consistent
   read snapshot/frontier, and every pushed-down secondary row is confirmed for
   that frontier; or
2. a derived index returns a conservative superset and final hydration performs
   the mutable canonical decision.

An implementation must not silently exclude a currently eligible unit from a
stale lifecycle, source, temporal or authority row. Strict deny-by-default
access still applies before expensive candidate work, but its deny decision
must be evaluated against authoritative `RetrievalAccessContext`/policy data at
the same frontier, never a stale allow/deny cache. The trace records the
CandidateSet representation, source generation and read frontier.

`RetrievalIoBudget` (defined in `memory-stacks-roadmap.md` Section 7.3) is a
runtime limit shared by lexical, dense, graph and temporal routes. All enabled
fields are nonzero hard caps; the effective budget is the field-wise minimum of
profile, branch and route limits. Admission occurs before a known-cost
read/decode. `RetrievalResult.completion` and per-route trace outcomes expose
budget exhaustion to callers; page-fault and cache metrics remain telemetry,
never portability-sensitive correctness gates.

Runtime-integration filters are optional and only active when
`CognitiveTrace` is enabled:

```cpp
struct RuntimeRetrievalFilters {
    std::vector<RuntimeObjectRef> runtime_instance_filter;
    std::vector<RuntimeOriginKey> origin_filter;
    std::vector<RuntimeObjectRef> observer_filter;
    std::vector<RuntimeObjectRef> character_filter;
    std::vector<RuntimeObjectRef> producer_node_filter;
    std::vector<RuntimeTraceRef> trace_filter;
    std::vector<RuntimeSequenceRange> sequence_ranges;
    std::vector<EpistemicLayer> epistemic_layers;
    bool require_evidence = false;
    bool include_conflicts = true;
    bool include_superseded = false;
};
```

`ScopeId` remains a namespace/access boundary. Observer, character, producer,
authority, partition and replica are not encoded as scope.

Runtime-integration filter types and physical secondary-index mappings are
defined in
[`agent-runtime-integration-roadmap.md`](agent-runtime-integration-roadmap.md).
They use generic `metadata_filters` and range-index substrates rather than
per-component DBIs.

### 7.2. IUnitRetriever

```cpp
class IUnitRetriever {
public:
    virtual ~IUnitRetriever();
    [[nodiscard]] virtual std::string_view name() const noexcept = 0;
    [[nodiscard]] virtual std::vector<RetrievalHit> retrieve(const RetrievalPlan& plan) const = 0;
};
```

Реализации:

- `LexicalRetriever` — BM25F по `unit_projections` (key: scope_id, projection_kind).
- `DenseRetriever` — vector search по `embedding_vectors`.
- `QARetriever` — targeted lookup по `IQAKnowledgeBase` (QALookup intent).
- `GraphRetriever` — bounded expansion через `graph_edges_by_src`/`graph_edges_by_dst`.
- `TemporalRetriever` — query `temporal_unit_index`; M1 temporal lookup has one
  authoritative validity axis.
- `DecayAwareRetriever` — обёртка, применяет DecayPolicy поверх других retrievers.
- `AntiLoopCooldown` — фильтр перед scoring, пропускает units с `cooldown_until_ms > now_ms`.
- `IntentRouter` — pre-router, классифицирует query и выбирает retrievers.

See [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) for advanced retrieval beyond Naive RAG: GraphRAG, Event-based RAG, multimodal VLM, deterministic RAG (LangGraph state machines), RLM as alternative to RAG.

### 7.3. RetrievalHit и Hybrid Fusion (RRF)

```cpp
struct RetrievalHit {
    KnowledgeUnitId unit_id;
    double score = 0.0;
    uint32_t rank = 0;
    std::string source;                   // retriever name
    std::vector<CitationHandle> citations;
    std::string snippet;
    std::optional<ProjectionKind> projection_kind;
    std::optional<DetailLevel> detail_level;
    std::vector<DrillDownRef> drill_down;
    std::optional<PerspectiveComponent> perspective_summary;
    std::optional<EpistemicLayer> epistemic_layer;
};
```

RRF formula:

```text
score(unit) = sum over retrievers r:
    weight_r / (k + rank_r(unit))

default k = 60
default per-stack weights (см. memory-stacks-roadmap.md секция 8):
    BasicRag:    lexical=1.0, vector=1.0
    AgentLTM:    lexical=1.0, vector=1.0, qa=1.5, graph=0.5, temporal=1.0
    SpeakerChat: lexical=1.0, speaker=1.5
```

Per-stack default weights в `HybridRetrievalConfig::retriever_weights`. Validation: `weights.size() == number of retrievers`. `WeightedMax`/`Learned` fusion — M2+.

### 7.4. Decay-Aware Scoring

`DecayAwareRetriever` оборачивает другие retrievers и применяет DecayPolicy через две стадии per canonical spec (см. `policies-roadmap.md` §2.3):

```cpp
double decay_factor(uint64_t elapsed_ms, double half_life_ms) {
    if (half_life_ms <= 0) return 1.0;
    return std::exp(-double(elapsed_ms) / half_life_ms);
}

double apply_decay_and_boost(
    double base_score,
    const UsageStatsComponent& usage,
    const DecayPolicy& policy,
    uint64_t now_ms) {
    uint64_t elapsed = (now_ms >= usage.last_used_at_ms) ? (now_ms - usage.last_used_at_ms) : 0;
    double factor = decay_factor(elapsed, policy.half_life_ms);
    return base_score * factor + policy.use_boost * std::log1p(double(usage.use_count));
}

double apply_post_filters(
    double score,
    const UsageStatsComponent& usage,
    const SpeakerComponent* speaker,
    SpeakerId agent_self_id,
    uint64_t now_ms,
    const DecayPolicy& policy) {
    if (usage.cooldown_until_ms > now_ms) score *= policy.cooldown_factor;
    if (speaker && speaker->speaker_id == agent_self_id) score *= policy.self_echo_suppression;
    return score;
}
```

Алгоритм:

1. AntiLoopCooldown filter (перед scoring).
2. Получить `UsageStatsComponent` для каждого unit.
3. Применить `apply_decay_and_boost` (exponential decay на base_score + log-бонус за использование).
4. Применить `apply_post_filters` (cooldown-фактор и self_echo_suppression поверх результата).

Defaults для `AgentLongTermMemory` (см. `memory-stacks-roadmap.md` секция 8.3): half_life=7d, use_boost=0.35, cooldown=60s, self_echo=0.3, cold_threshold=0.01.

### 7.5. Bounded Graph Expansion

`GraphRetriever` использует `GraphExpansionOptions` (см. `memory-stacks-roadmap.md` секция 8 + `knowledge-units-roadmap.md` section 5.2):

```cpp
struct GraphExpansionOptions {
    uint32_t max_depth = 2;
    uint32_t max_edges = 64;
    uint32_t budget_tokens = 1024;
    std::vector<EdgeKind> allowed_edge_kinds;
    double min_weight = 0.0;
};
```

BFS от seed units, max_depth BFS, max_edges — глобальный cap, allowed_edge_kinds — фильтр (empty = all), min_weight — prune low-confidence. Determinism: ordering `(edge_weight desc, edge_kind, from_id, to_id)`. Floating subgraph как retrieval view (не stored as separate copy).

M2+ may add immutable adjacency segments as an optimisation over canonical
`graph_edges_by_src`/`graph_edges_by_dst` rows: sorted neighbour ids, packed
weights, dictionary-coded edge kinds, and optional metadata locators. The
segment is never the authority for an edge. `CandidateSet` may represent BFS
frontier and visited nodes, while the existing depth/edge/token limits remain
hard traversal budgets. Benchmark against row-wise expansion on breadth,
locality, update/compaction cost, decoded bytes and deterministic result order
before promoting a packed adjacency layout.

См. также [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) для Bounded BFS + schema introspection (Pattern 5) borrowed from `codebase-memory-mcp` — это уточняет API shape `GraphStore` для будущих расширений (callbacks + early-stop visitor, schema introspection для diagnostics).

### 7.6. Adaptive Routing

This section covers query-type routing. Domain/concept/playbook activation is
specified in [`knowledge-activation-roadmap.md`](knowledge-activation-roadmap.md)
and is a separate capability from vector/lexical retrieval.

`ILightweightIntentRouter` — non-LLM классификатор (decision tree / trained classifier):

```cpp
enum class QueryType : uint8_t {
    Unknown,
    QALookup,            // "How do I ...?"
    FactLookup,          // "What is the capital of ...?"
    ProcedureLookup,     // "Steps to ..."
    TemporalLookup,      // "What happened on ..."
    GraphLookup,         // "What is connected to ...?"
    NoAnswerCheck,       // impossible-to-answer queries
    CausalWhy,
    DecisionRecall,
    TaskRecall,
    PerspectiveLookup,
    KnowledgeAtSequence,
    UnresolvedProblemLookup,
    EvidenceDrillDown,
};
```

Pre-router (перед retrieval) — per stack configurable. `QALookup` → приоритет `QARetriever`. `TemporalLookup` → `TemporalRetriever` + `LexicalRetriever`. Default `Unknown` — применяются все retrievers по profile. Domain, role, stage, topic, platform, and audience are soft routing signals by default; they should boost or prioritize candidates rather than exclude neighboring domains unless a profile explicitly marks the field as a strict safety filter.

## 8. ContextAssembly with Budgets

`ContextBuilder` превращает ranked hits в budgeted context для downstream consumer.

### 8.1. ContextBudget per-block

`MemoryProfileSpec::context_budget` (см. `memory-stacks-roadmap.md` секция 6.2). Constraint: `sum(per_block) <= total_tokens`.

```cpp
struct ContextBudget {
    size_t total_tokens = 4096;
    size_t qa_tokens = 512;
    size_t chunk_tokens = 2048;
    size_t graph_tokens = 512;
    size_t summary_tokens = 512;
    size_t evidence_tokens = 256;
};
```

Per-stack defaults: `BasicRag` (chunks=3072), `AgentLTM` (qa=512, chunks=2048, graph=512, summary=512), `QAKB` (qa=1024, chunks=512), `SpeakerChat` (chunks=1500, summary=800).

### 8.2. IContextBuilder и TrimmedContextBuilder

```cpp
class IContextBuilder {
public:
    virtual ~IContextBuilder();
    [[nodiscard]] virtual Context build(const RetrievalPlan& plan, const std::vector<RetrievalHit>& hits) const = 0;
};
```

`TrimmedContextBuilder` — дефолтная реализация. Алгоритм trim order:

1. QA block (highest precision, lowest cost).
2. Top 30% high-score chunks от remaining budget.
3. Low-score chunks (rest of chunk budget).
4. Summaries.
5. Graph expansion (entities/relations only, no raw text).
6. Evidence blocks (quotes + ranges) inline с parent block, count against parent's budget.

Citations обязательны: каждый `ContextBlock` имеет `citations`. Без citations — block rejected, logged as warning. Final context logging через `IRetrievalTrace` (см. секцию 9.1) — обязательно, не side channel.

### 8.3. ContextBlock и Context

```cpp
struct ContextBlock {
    std::string block_id;
    BlockType block_type;                 // QA | Chunk | Summary | Graph | Evidence | Task | Decision | Procedure | Episode | Perspective | Conflict | CausalPath
    std::string content;
    std::vector<CitationHandle> citations; // M0 summary; M1 full ref/anchor handles
    double score;
    size_t token_count;
    std::vector<KnowledgeUnitRef> unit_refs;
    std::string perspective_label;        // "User stated", "Node inferred", etc.
};

struct Context {
    std::vector<ContextBlock> blocks;
    std::vector<MaterializationInstruction> materialization_instructions;
    size_t total_tokens;
    std::string trace_id;
    std::string retrieval_plan_id;
    bool truncated;
};
```

`materialization_instructions` is empty for text-only contexts. Every entry
must name an `EvidenceAnchorId` reachable from a `CitationHandle` in `blocks`; it is a
typed, authorization-gated request handle and never an implicit binary payload.

For artifact-aware sources, `CitationHandle.summary` remains the compact
text/citation path. The associated full reference may carry an `EvidenceAnchor` with a typed page,
region, time or other media locator as specified in
[`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md). Context does
not implicitly include binary bytes: a host may request an explicit
materialization of a cited page, frame, crop or clip when its downstream model
can consume it.

Determinism: given the same plan/hits/budget → same `Context`. Это делает retrieval traces reproducible.

Retrievers hydrate and validate `CitationHandle` values against the canonical
unit/source-revision state before a hit becomes visible. `ContextBuilder` may
drop a handle whose full reference or anchor no longer resolves, but it must
then label the block `provenance-incomplete`; it must not retain a detached
`MaterializationInstruction` or silently substitute a same-path newer revision.

Perspective-safe context assembly must not collapse local interpretations into
omniscient facts. Blocks should use labels such as `User stated`,
`Planner believed`, `RiskNode inferred` and `System reconstruction estimates`.

### 8.4. IContextCompressor Hook (M2+)

Reference: RECOMP (arXiv:2310.04408), LLMLingua (arXiv:2310.05736).

Post-retrieval compression context'а перед передачей в LLM. Снижает tokens/cost/latency. Может вернуть empty context если retrieval бесполезен.

```cpp
class IContextCompressor {
public:
    virtual ~IContextCompressor() = default;

    struct CompressionOptions {
        enum class Mode {
            None,           // no compression
            Extractive,     // select important sentences
            Abstractive,    // generate summary via LLM
            Selective,      // drop low-relevance blocks entirely
        };
        Mode mode = Mode::None;
        size_t target_tokens = 1024;
        double quality_threshold = 0.5;  // minimum acceptable relevance
    };

    virtual CompressedContext compress(
        const Context& ctx,
        const CompressionOptions& options) = 0;
};

// RECOMP extractive implementation:
class RecompExtractiveCompressor final : public IContextCompressor {
    // Dual encoder scores each sentence, keep top-K by relevance.
};

// LLMLingua implementation:
class LlamaLinguaCompressor final : public IContextCompressor {
    // Prompt compression via small LM, preserves semantic structure.
};
```

Integration:

- `ContextBuilder.build(plan, hits)` -> `Context`.
- `IContextCompressor.compress(ctx, opts)` -> `CompressedContext`.
- `CompressedContext` подаётся в LLM вместо raw `Context`.

Когда включать:

- M0/M1: `None` (raw context).
- M2+: optional per `RetrievalPlan`.
- Default для long-context LLMs: `Extractive` или `Abstractive`.

## 9. Evaluation & Tracing

Evaluation — first-class citizen. Retrieval traces, datasets, metrics — часть contract, не bolt-on.

### 9.1. RetrievalTrace

```cpp
struct RetrievalIoCounters {
    std::uint32_t segment_reads = 0;
    std::uint32_t mdbx_cursor_seeks = 0;
    std::uint64_t encoded_bytes_read = 0;
    std::uint64_t decoded_bytes = 0;
    std::uint64_t cache_hits = 0;
    std::uint64_t cache_misses = 0;
    std::optional<std::string> first_exhausted_limit;
};

struct ProjectionRouteTrace {
    std::string branch_id;
    std::string route_id;
    std::optional<std::string> parent_route_id;
    std::string input_candidate_set_id;
    std::string execution_id;
    std::uint32_t input_candidate_count = 0;
    std::uint32_t output_candidate_count = 0;
    std::string outcome;  // used, missing, recompute_scheduled, fallback, budget_exhausted, dropped
    std::optional<std::string> fallback_route_id;
    RetrievalIoCounters io_counters;
    RetrievalCompletion completion = RetrievalCompletion::Complete;
};

struct PolicyDecisionTrace {
    std::string policy_fingerprint;
    std::string claims_issuer_id;
    std::string claims_version;
    std::uint32_t pre_candidate_allow_count = 0;
    std::uint32_t pre_candidate_deny_count = 0;
    std::uint32_t post_fusion_allow_count = 0;
    std::uint32_t post_fusion_deny_count = 0;
};

struct QueryBranchTrace {
    std::string branch_id;
    std::vector<KnowledgeUnitRef> candidate_units;
    std::vector<KnowledgeUnitRef> fused_units;
    std::vector<std::string> context_block_ids;
};

struct ContextBlockInputTrace {
    KnowledgeUnitRef source_unit;
    std::uint64_t envelope_revision = 0;
    ProjectionVersionRef projection_version;
    std::vector<CitationHandle> citations;
};

struct ContextBlockTrace {
    std::string block_id;
    std::string branch_id;
    std::vector<ContextBlockInputTrace> inputs;
    std::optional<TemporalQuery> temporal_query;
    std::string normalized_temporal_frontier_digest;
    std::string selection_reason;
};

struct RetrievalTrace {
    std::string trace_id;
    std::optional<RuntimeTraceRef> runtime_trace;
    RetrievalPlan plan;
    std::vector<QueryBranchTrace> query_branches;
    std::vector<ContextBlockTrace> context_block_lineage;
    std::vector<std::vector<RetrievalHit>> per_retriever_hits;  // associative
    std::vector<RetrievalHit> targeted_hits;                     // targeted (QALookup)
    std::vector<RetrievalHit> fused_hits;
    Context final_context;
    LatencyStats latency_ms;                                    // per-stage
    RetrievalIoBudget effective_io_budget;
    RetrievalIoCounters io_counters;
    PolicyDecisionTrace policy_decision;
    std::vector<ProjectionRouteTrace> projection_route_events;
    std::vector<KnowledgeUnitRef> causal_path;
};
```

Latency per stage: tokenize, lexical, vector, qa, graph, temporal, fusion, build_context. Метрики: `cache_hit_rate`, `anti_loop_skip_rate`, `decay_score_distribution` (histogram), `retrieval_channel_latency` (p50/p95/p99 per channel). Per-retriever breakdown: `associative` (lexical/vector/graph) vs `targeted` (QA, exact match). Associative timeout 50ms, targeted 4000ms.

`RetrievalIoCounters` records segment reads, MDBX cursor seeks, encoded and
decoded bytes, cache hits/misses when available, and the first exhausted I/O
limit. `ProjectionRouteTrace` records exact route use, missing projections,
scheduled recompute, and explicit fallback. These supplement the existing
per-stage latency and channel metrics without turning optional page-fault data
into a portability-sensitive correctness gate.

`PolicyDecisionTrace` is redacted observability rather than an authorization
cache: it identifies the applied policy and host claims version, and records
only aggregate pre-candidate and post-fusion allow/deny counts. It never stores
the identity, text, citation, or metadata of a denied knowledge unit.

Every `ProjectionRouteTrace` names its branch, parent input where applicable,
and deterministic route execution identity. This makes fallback activation and
candidate fan-out replayable without allowing the same fallback route to run
twice for identical branch input.

`ContextBlock.block_id` is deterministic for the validated block inputs. Every
`QueryBranchTrace.context_block_ids` entry resolves to exactly one
`ContextBlockTrace`. Each `ContextBlockInputTrace` binds one source occurrence,
active envelope revision, complete projection version and its citations; no
parallel arrays need implicit positional correspondence. The trace retains the
normalized `TemporalQuery` when present and a canonical digest of its effective
frontier. This creates a replayable branch -> hit -> context-block chain without
requiring the trace itself to be a mandatory durable audit record; retention and
redaction are application policy.

### 9.2. RetrievalDataset / TestCase / Judgment

```cpp
struct RetrievalJudgment {
    KnowledgeUnitId unit_id;
    uint32_t grade = 0;                  // 0..3 (0=irrelevant, 1=related, 2=useful, 3=exact)
    std::string note;
};

struct RetrievalTestCase {
    std::string id;
    std::string query;
    QueryType expected_query_type;
    std::vector<RetrievalJudgment> judgments;
    std::vector<KnowledgeUnitId> must_include;
    std::vector<KnowledgeUnitId> must_exclude;
    std::optional<std::string> expected_answer;
    std::optional<RetrievalPlan> plan_override;
};

struct RetrievalDataset {
    std::string name;
    std::vector<RetrievalTestCase> cases;
};
```

### 9.3. Golden dataset requirements

M1 minimum: ≥50 test cases; ≥3 distinct intent types (QALookup, FactLookup, GraphLookup minimum; TemporalLookup/ProcedureLookup encouraged); ≥1 no-answer case per intent; ≥1 case per `KnowledgeUnitKind`. Dataset checked in под `tests/data/golden/` в JSON form.

### 9.4. RetrievalMetrics

- `Recall@K` — fraction of judged units (grade > 0) в top-K hits.
- `MRR` — mean reciprocal rank of first grade > 0 unit.
- `NDCG@K` — graded 0..3 NDCG with logarithmic discount.
- `ContextPrecision` — fraction of context tokens from grade > 0 units.
- `NoAnswerAccuracy` — fraction of no-answer cases where builder emits no answer.
- `CitationFidelity` — fraction of context blocks whose source_refs resolve in storage.
- `Latency` — p50, p95, p99 per stage.
- `IndexSize` — bytes per category (units, postings, graph, temporal).
- `ReindexTime` — seconds per resource, per backend.

M2+ memory-governance metrics are tracked in
[`memory-lifecycle-governance-roadmap.md`](memory-lifecycle-governance-roadmap.md)
AM-17: answer groundedness/relevance, temporal accuracy, stale-fact rate,
contradiction handling, task success, token cost, write amplification, memory
growth, privacy/deletion correctness and entity-resolution quality
(merge precision/recall, ambiguous-rate, false-merge rate). They extend the M1
retrieval gate; they do not replace it.

### 9.5. Hybrid Release Gate

For each locked profile and query class, CI requires hybrid retrieval to meet the
configured non-regression tolerance against `max(BM25-only, exact-dense)` on the
same qrels, filters, candidate depth and I/O mode. It must also preserve
`NoAnswerAccuracy` and meet that profile's latency budget. A numerical lift
target, including 20 percent, is a profile-specific hypothesis rather than a
global release blocker: it may be promoted only after a representative benchmark
and must name its corpus, query slice, hardware and confidence interval.

### 9.6. Intent-class-specific test cases (M1)

- **TemporalValidityLookup** — single-axis temporal query: "What is valid at T?" + "What is valid now?". True bi-temporal queries are M2+ AM-13.
- **SupersedenceChain** — новый fact supersed'ит старый: retrieval возвращает новый, не старый.
- **CooldownRespect** — после retrieval unit не возвращается в течение cooldown_ms.
- **SpeakerFilter** — фильтрация по `speaker_scope` (Self/Owner/Cohost/Audience).
- **CompactionHandoff** — compaction worker восстанавливается после crash через `compaction_handoffs` DBI.
- **CrossDomainCoverage** — activation plan includes required neighboring
  domains and concepts for cross-domain tasks.
- **ProcedureActivation** — playbook header is selected before loading the full
  playbook body or raw evidence chunks.
- **SameEventDifferentPerspectives** — several node interpretations of the
  same event remain distinct and labeled.
- **KnowledgeAtSequence** — retrieval excludes knowledge recorded after the
  requested runtime sequence.
- **CausalWhy** — action recall returns decision, evidence and direct causal
  path.
- **DecisionAlternatives** — selected and rejected alternatives survive
  retrieval/compaction.
- **PerspectiveLeakage** — local/private perspective does not leak without an
  allowed projection.
- **EvidenceDrillDown** — summary opens to structured record, then raw evidence.
- **ReplayDeterminism** — same append-only corpus rebuilds the same derived
  projection.

### 9.7. BEIR-style heterogeneous benchmark methodology

Reference: BEIR (arXiv:2104.08663).

Golden dataset должен покрывать heterogeneous query types:

- Factual ("What is the capital of France?").
- Argument ("Why does X cause Y?").
- Comparison ("Compare A vs B").
- Multi-hop ("Find entity X, then related entity Y").
- No-answer ("intentionally unanswerable query").

Metrics:

- `Recall@1/5/10/50` across query types.
- `nDCG@10` for graded relevance.
- `NoAnswerAccuracy` (separate metric для unanswerable queries).
- `CitationFidelity` (precision of source attribution).
- `ContextPrecision` (relevance of retrieved context to query).
- `Latency` p50/p95/p99.

Reporting:

- Per query-type breakdown (table).
- Per stack (`BasicRag` vs `AgentLTM` vs etc.).
- Per mode (`Exact` vs `HNSW` vs `BinaryCF` vs `BinaryOnly`).
- Hybrid result versus `max(BM25, exact-dense)`, with the profile-specific
  non-regression tolerance and any separately approved lift hypothesis.

## 10. Cross-Module Risks

Топ-5 архитектурных рисков и митигации:

1. **Component explosion (DBI budget).** Митигация: `TypeDiscriminatedTable` для operational components, per-kind таблицы только для крупных payloads (qa/fact/episode/article/chunk).

2. **Profile drift.** `profile_signature` mismatch при `open_existing()`. Митигация: detected на open, additive auto-migrate для новых capabilities, breaking → error + migration tool (`agent-memory-cli profile-migrate`).

3. **Multi-projection retrieval overhead.** N projections × N retrievers. Митигация: `candidate_pool_size` limited (200 default), `projection_kind` в posting keys для targeted scan, sparse projection storage.

4. **Decay + Cooldown + self-echo complexity.** Много параметров политик. Митигация: defaults в `MemoryProfiles::` namespace, per-stack validation в `open()`, eval pipeline проверяет effectiveness (`anti_loop_skip_rate > 0` для активно используемых stacks).

5. **Cross-stack isolation (interference между scope_id).** Митигация: scope-aware keys (все secondary indexes начинаются с `scope_id`), per-scope transactions, `metadata_filters` через `ReverseIndexTable` (scope-prefixed).

6. **Envelope bloat (vector<SourceRefSummary> в hot-path lookup).** Митигация: max 3 sources per unit, ≤256 байт preview на каждый (SourceRefSummary хранит только quote_hash_high + excerpt_preview + confidence + scope_ref), полный SourceRef с excerpt_text вынесен в `source_refs` DBI (M1) с lookup через `source_refs_by_unit` index; на hot-path подтягиваются только summary'ы, detail-fetch — lazy по требованию.

## 11. Cross-references и Implementation Order

Этот документ расширяет `memory-stacks-roadmap.md` секция 16 (Recommended Implementation Order) более детальной спецификацией:

| Шаг в memory-stacks-roadmap | Детализация здесь |
|---|---|
| Шаг 1-2: envelope + scope + metadata filters | Секция 3 (envelope fields, per-kind rules) |
| Шаг 5: component infrastructure | Секция 4 (operational + per-kind components, storage) |
| Шаг 7: SearchProjections + BM25F indexing | Секция 5 (projection kinds, generation rules, storage) |
| Шаг 13: retrieval composition | Секция 7 (RetrievalPlan, retrievers, RRF, decay-aware) |
| Шаг 15: eval pipeline | Секция 9 (golden dataset, metrics, hybrid lift) |

Дополнительные секции: domain stores (секция 6) — capability-aware DBI creation; ContextAssembly (секция 8) — budget trim order, citations; Cross-module risks (секция 10) — top-5 retrieval-specific риски.

## 12. References

- `guides/memory-stacks-roadmap.md` — центральный манифест архитектуры, ADR'ы, MemoryProfileSpec, MemoryStack, capability validation, maturity levels.
- `guides/mdbx-containers-extension-tz.md` — canonical physical MDBX manifest and DBI budget.
- `guides/knowledge-units-roadmap.md` — per-kind payload-компоненты (QAPayload, FactPayload, ChunkPayload, ConversationEpisodePayload, CompiledArticlePayload, Entity, Relation).
- `guides/lexical-search-roadmap.md` — BM25F поверх projections, postings, tokenization.
- `guides/optimization-roadmap.md` — vector/binary secondary indexes, multi-projection embeddings.
- `guides/mdbx-containers-extension-tz.md` — `TypeDiscriminatedTable`, `MultiTableWriter`, `ReverseIndexTable` storage primitives.
- `guides/architecture.md` — 4-слойная модель.
- `guides/policies-roadmap.md` (future) — детальная спецификация DecayPolicy/WritePolicy/SpeakerScopePolicy.
- `guides/compaction-roadmap.md` (future) — CompactionWorker, job types, handoff structure.
- `guides/runtime-services-roadmap.md` (future) — PromptCache, AsyncIndexer, WriteGate.
- `guides/memory-lifecycle-governance-roadmap.md` — M2+ bi-temporal validity,
  progressive retrieval, mutation policy and expanded memory-eval contracts.

External references (ai-agent-playbook):

- `concepts/ai-agents/AI-агенты и AI-VTuber — архитектурные паттерны из видео 2026.md` — hot/cold path separation, layered memory.
- `concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md` — anti-loop / decay / cooldown patterns.
- `resources/llm-research/Memory for Autonomous LLM Agents survey — конспект.md` — write-read-manage loop, lifecycle.

External research references (arXiv):

- arXiv:2310.04408 — "RECOMP: Improving Retrieval-Augmented LMs with Compression and Selective Augmentation".
- arXiv:2310.05736 — "LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models".
- arXiv:2104.08663 — "BEIR: A Heterogenous Benchmark for Zero-shot Evaluation of Information Retrieval Models".
- arXiv:2005.11401 — "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks" (RAG).
- See also: `guides/research-reading-map.md`.
