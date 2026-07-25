# runtime-services-roadmap.md

Спецификация cross-cutting runtime сервисов (PromptCache, AsyncIndexer, WriteGate) для подсистемы памяти `agent-memory-cpp`. Документ конкретизирует ADR-013 (Runtime services) из `guides/memory-stacks-roadmap.md` секции 11.

> **C++17 compliance:** кодовые сниппеты используют `const std::vector<T>&` вместо `std::span<T>` и явные конструкторы вместо designated initializers. PromptCache split на `IPromptPrefixCache` (provider-side, всегда) и `IResponseCache` (local response, opt-in default OFF для безопасности).

## 1. Purpose

- Что описывает: PromptCache split (`IPromptPrefixCache` provider-side + `IResponseCache` local opt-in), AnthropicCacheControlAdapter, AsyncIndexer (batch вставки в lexical/vector индексы), WriteGate (применяет WritePolicy). Все сервисы ортогональны profile (доступны через интерфейсы, не зависят от конкретного MemoryStack).
- Cross-references: memory-stacks-roadmap.md (ADR-013, секции 7, 11, 16), knowledge-base-roadmap.md (RetrievalTrace), policies-roadmap.md (WritePolicy), compaction-roadmap.md (job submission), mdbx-containers-extension-tz.md (§12.5 storage recipe, §5.5.1 DBI budget).

## 2. Layer Architecture Review

Per memory-stacks-roadmap.md секция 11:

```
Layer 1: Storage Primitives
Layer 2: Retrieval Primitives
Layer 3: Memory Stacks
Layer 4: Applications

Cross-cutting Runtime Services (orthogonal):
  PromptCache, CompactionWorker, WriteGate, AsyncIndexer
  Используют Layer 1 + Layer 2 через интерфейсы
```

Runtime-сервисы доступны из любого layer, но сами не зависят от конкретного MemoryStack. Каждый сервис — singleton per MemoryStack (если включён в spec).

## 3. PromptCache (Split: Provider-Side Prefix + Local Response)

### 3.1. Purpose and Split Rationale

Кэширование в LLM-приложениях объединяет два РАЗНЫХ механизма с разными consistency guarantees:

1. **Provider-side prompt prefix cache** — `cache_control: ephemeral` (Anthropic), `prompt_cache_key` (OpenAI), и аналоги. Провайдер САМ кэширует prefix на своей стороне и возвращает метрики `cache_read_input_tokens` / `cache_write_input_tokens`. Семантически безопасен: провайдер контролирует consistency, нам нужно только эмитить `cache_control` metadata.

2. **Local response cache** — локальное кэширование ПОЛНОГО `response_text` на нашей стороне. Семантически рискованно: для динамических агентов (изменяемый контекст, tool calls, time-sensitive вопросы) может вернуть **stale answer** вместо актуального.

Семантический bug unified дизайна: смешиваются два механизма с разными consistency guarantees и разными failure modes. Решение — два независимых интерфейса:

- `IPromptPrefixCache` — provider-side, **всегда вызывается** при LLM call (cheap, провайдер гарантирует).
- `IResponseCache` — local, **opt-in, default OFF** (для безопасности по умолчанию).

Per `concepts/llm-research/Управление контекстом LLM-агента - стратегии снижения стоимости.md` (ai-agent-playbook): cache hit rate > 70% — основная цель (для обоих механизмов).

### 3.2. IPromptPrefixCache (Provider-Side)

```cpp
class IPromptPrefixCache {
public:
    virtual ~IPromptPrefixCache() = default;

    // Возвращает cache_key для нормализованного prompt prefix.
    // Используется для cache_control: ephemeral метаданных в API calls.
    virtual std::string compute_cache_key(
        const std::string& provider_id,        // "anthropic", "openai"
        const std::string& model_id,
        const std::string& prompt_prefix) = 0;

    // Метрики провайдер-кэша (cache_read_input_tokens, cache_write_input_tokens).
    virtual PromptPrefixCacheMetrics metrics() const = 0;
};

struct PromptPrefixCacheMetrics {
    uint64_t cache_read_input_tokens = 0;
    uint64_t cache_write_input_tokens = 0;
    uint64_t cache_creation_input_tokens = 0;
};
```

### 3.3. IResponseCache (Local, Opt-In)

```cpp
class IResponseCache {
public:
    virtual ~IResponseCache() = default;

    virtual std::optional<CachedResponse> lookup(
        const ResponseCacheKey& key) = 0;

    virtual void store(
        const ResponseCacheKey& key,
        const CachedResponse& response) = 0;

    virtual void invalidate(const ResponseCacheKey& key) = 0;
    virtual void invalidate_scope(ScopeId scope) = 0;

    virtual ResponseCacheMetrics metrics() const = 0;
};

struct ResponseCacheKey {
    uint32_t schema_version = 1;
    ScopeId scope_id;
    std::string provider_id;
    std::string model_id;
    std::string request_hash;    // hash(canonical prompt + tools + params)
    std::optional<std::string> suffix;
};

struct CachedResponse {
    std::string response_text;
    uint64_t input_tokens;
    uint64_t output_tokens;
    uint64_t created_at_ms;
    std::chrono::seconds ttl{3600};
};

struct ResponseCacheMetrics {
    uint64_t hits = 0;
    uint64_t misses = 0;

    double hit_rate() const {
        auto total = hits + misses;
        return total > 0 ? double(hits) / double(total) : 0.0;
    }
};
```

### 3.4. PromptPrefixCache (LRU Implementation)

LRU-таблица дедупликации `compute_cache_key` для нормализованных prompt prefix (не хранит response — провайдер делает caching):

```cpp
class PromptPrefixCache : public IPromptPrefixCache {
public:
    explicit PromptPrefixCache(size_t max_keys = 10000);

    std::string compute_cache_key(
        const std::string& provider_id,
        const std::string& model_id,
        const std::string& prompt_prefix) override;

    PromptPrefixCacheMetrics metrics() const override;

private:
    std::list<std::string> m_lru;  // front = most recent
    std::unordered_map<std::string, std::list<std::string>::iterator> m_index;
    size_t m_max_keys;
    mutable std::shared_mutex m_mutex;
    PromptPrefixCacheMetrics m_metrics;
};
```

LRU eviction по количеству ключей (`max_keys`). Ключи детерминированно вычисляются из `(provider_id, model_id, prompt_prefix)` — persistence не требуется.

### 3.5. Adapters

```cpp
// AnthropicCacheControlAdapter — translates IPromptPrefixCache to API metadata
class AnthropicCacheControlAdapter : public IPromptPrefixCache {
    // compute_cache_key возвращает cache_id для prompt prefix.
    // Используется в requests как cache_control: {type: ephemeral}.
    // Метрики провайдера (cache_read/cache_write_input_tokens) приходят из response.
    // Обновляет m_metrics после каждого API call.
};

// NoOpAdapter — для профилей без prompt cache
class NoOpPromptPrefixCache : public IPromptPrefixCache {
    // compute_cache_key возвращает пустую строку; provider не использует cache.
    // metrics() возвращает нули.
};
```

### 3.6. ResponseCache (LRU Implementation) and Persistence

`ResponseCache` — LRU-реализация `IResponseCache` для хранения `CachedResponse`:

```cpp
class ResponseCache : public IResponseCache {
public:
    explicit ResponseCache(
        size_t max_entries = 10000,
        size_t max_bytes = 100 * 1024 * 1024);  // 100 MB

    std::optional<CachedResponse> lookup(const ResponseCacheKey& key) override;
    void store(const ResponseCacheKey& key, const CachedResponse& response) override;
    void invalidate(const ResponseCacheKey& key) override;
    void invalidate_scope(ScopeId scope) override;
    ResponseCacheMetrics metrics() const override;

private:
    struct Entry {
        ResponseCacheKey key;
        CachedResponse response;
        uint64_t last_access_ms;
        size_t size_bytes;
    };

    std::list<Entry> m_lru;  // front = most recent
    std::unordered_map<ResponseCacheKey, std::list<Entry>::iterator> m_index;
    size_t m_max_entries;
    size_t m_max_bytes;
    size_t m_current_bytes = 0;
    mutable std::shared_mutex m_mutex;
    ResponseCacheMetrics m_metrics;
};
```

LRU eviction по `size_bytes` (когда превышен `max_bytes`) и по age (TTL на каждую запись).

**Persistence (M2+, опционально):** `IResponseCache` может персистить в MDBX DBI:

```
response_cache
  key = ResponseCacheStorageKey → CachedResponse

ResponseCacheStorageKey =
  CompositeKey<ScopeId, ProviderId, ModelId, RequestHash, SuffixBytes, SchemaVersion>
```

`ResponseCacheStorageKey` is semantically equivalent to `ResponseCacheKey`.
`SuffixBytes` uses canonical encoding where empty optional suffix and empty
string suffix are distinct. If the hash recipe or canonical request encoding
changes, `schema_version` changes and old entries are ignored or migrated.

`response_cache_storage` controls local response-cache persistence:

- `Disabled`: no lookup/store calls and no DBI.
- `MemoryOnly`: per-process cache, no DBI, lost on restart.
- `Mdbx`: load from `response_cache` on `MemoryStack::open()`; eviction deletes
  from DBI; survives restart.

`IPromptPrefixCache` **НЕ персистится**: ключи детерминированно вычисляются через хэш-функцию, persistence не нужна.

Для M0/M1 — `IResponseCache` отсутствует (только `IPromptPrefixCache`).

### 3.7. Default Behavior

- `IPromptPrefixCache`: opt-in через `enable_prompt_cache=true`. Default **ON** для профилей с hybrid retrieval (BasicRag, AgentLTM, QAKnowledgeBase) — provider-side кэш даёт прямую экономию токенов без consistency рисков.
- `IResponseCache`: opt-in через `response_cache_storage != Disabled`. Default
  **OFF везде** — для безопасности (см. §3.1 rationale).

### 3.8. Validation Rules

- `IPromptPrefixCache.compute_cache_key()` вызывается при каждом LLM call (cheap, O(1) lookup).
- `IResponseCache.lookup()` вызывается ТОЛЬКО если
  `spec.response_cache_storage != Disabled` (default не вызывается).
- scope-aware keys: разные `scope_id` имеют разные cache entries.
- TTL для `IResponseCache`: default 1 час, configurable per provider.

## 3.9. CAG (Cache-Augmented Generation) and ContextCache Layer

### Sources

- arXiv:2412.15605 — "Don't Do RAG: When Cache-Augmented Generation is All You Need for Knowledge Tasks".
- arXiv:2404.12457 — "RAGCache: Efficient Knowledge Caching for Retrieval-Augmented Generation".

### What

Two related but distinct ideas:

- **CAG (Cache-Augmented Generation):** pre-load the entire relevant corpus into the model's context cache (KV-cache or extended context window). At query time skip retrieval and answer from cached knowledge.
- **RAGCache:** cache intermediate states of an existing RAG pipeline (retrieved chunks, plans, KV-states) to accelerate RAG inference without changing the retrieval contract.

The two paths differ in whether retrieval is bypassed (CAG) or retained and accelerated (RAGCache). They are not the same architecture and must not be conflated.

### 3.9.1 CAG path (bypasses retrieval)

```text
Compiled knowledge pack (e.g. CompiledContextPack derived from CompiledWikiProfile)
  -> pre-loaded into model context (KV-cache or extended context window)
  -> query
  -> generation
```

Suitable when corpus is small/stable enough to fit in context.

### 3.9.2 RAGCache path (caches retrieval intermediates)

```text
query
  -> retrieval
  -> retrieved knowledge
  -> cached inference states
  -> generation
```

Suitable when corpus is too large for context or updates frequently.

### 3.9.3 Decision rule

Use CAG path when corpus fits in context, updates infrequently, query volume justifies pre-loading cost.
Use RAGCache path when corpus overflows context or retrieval latency dominates.

### 3.9.4 Storage tiers

- `CompiledContextPack` (text/structured knowledge, stable across model versions): stored in MDBX as part of the profile / compiled pack.
- `ProviderKVHandle` (runtime model KV-cache, model-specific and dtype-specific): NOT stored in MDBX; lives in GPU/host inference memory only.
- `SerializedKVCache` (optional, backend-specific): some inference backends permit serialisation to disk; compatibility is conditional on model version, layer count, and dtype. Not a default capability; document per-backend.

### 3.9.5 Integration candidates (tagged per path)

CAG-side (CompiledContextPack layer):

- `CompiledWikiProfile` → derived `CompiledContextPack` — prime CAG candidate (stable, compact, project-scoped). Pre-loaded into model context.

RAGCache-side (intermediate result cache):

- `SummaryTreeJob` — generated summaries cached and re-used across queries as retrieval-state intermediates.

Related but distinct (post-generation):

- `ResponseCache` (post-generation cache — complementary to both CAG and RAGCache; NOT an intermediate retrieval-pipeline state). Stores final LLM responses (memoization of completed generations); sits AFTER the generation step, not within the retrieval pipeline. См. §3.3 / §3.6.

Both paths:

- `PromptPrefixCache` (§3.2, always on for hybrid profiles) — agent-level prompt caching; both paths reuse the provider-side prefix mechanism.

### 3.9.6 Relationship to existing PromptCache

`IPromptPrefixCache` (§3.2) provides provider-side prefix caching. CAG extends it from "prompt prefix caching" to "context caching of compiled knowledge". The same provider-side prefix mechanism is reused; CAG adds agent-side context assembly and a `ContextCache` layer over compiled knowledge packs.

### 3.9.7 Status

Conceptual design for the M2 layer. No PR planned yet. Depends on stable `ContextBuilder` output (Layer 3 per `memory-stacks-roadmap.md`).

### 3.9.8 Cross-reference

See [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) §5.5 for the candidate DBI shape (compiled-context-pack storage, capability-gated).

## 4. AsyncIndexer

### 4.1. Purpose

AsyncIndexer выполняет rebuild/backfill и тяжёлые или explicitly
eventually-consistent indexing jobs. Он **не** является владельцем default
write visibility для critical retrieval indexes.

Default M0/M1 consistency mode:

- `MemoryStack::write_unit` commits envelope, components, projections,
  content-key/by-kind indexes, lexical candidate/stat indexes needed by active
  retrieval, metadata filters and selected lightweight secondary indexes in one
  `MultiTableWriter` transaction.
- AsyncIndexer may rebuild those indexes from authoritative unit revisions, but
  it must not be required for a newly committed unit to become retrievable in
  the same profile.

Async eventual mode is allowed only as explicit profile policy for indexes that
declare `eventually_consistent=true` (for example heavy embedding recompute,
HNSW graph rebuild, bulk lexical backfill). In that mode write_unit must enqueue
a durable `IndexUpdateJob(unit_id, unit_revision, projection_kind, index_kind)`
and readers must respect revision/generation guards documented by the owning
roadmap.

### 4.2. Interface

```cpp
class IAsyncIndexer {
public:
    virtual ~IAsyncIndexer() = default;

    // Atomically enqueue an async index update in the caller's write transaction.
    virtual JobId enqueue(IndexUpdateJob job, Transaction& txn) = 0;

    // Force flush (для admin/test)
    virtual void flush() = 0;

    // Status
    virtual AsyncIndexerStats stats() const = 0;
};

enum class IndexKind : uint8_t {
    LexicalBackfill,
    EmbeddingRecompute,
    HnswRebuild,
    Maintenance,
};

struct IndexUpdateJob {
    enum class Op { Upsert, Erase };
    Op op;
    KnowledgeUnitId unit_id;
    uint64_t unit_revision;
    ProjectionKind projection_kind;
    IndexKind index_kind;
};

struct AsyncIndexerStats {
    uint64_t jobs_enqueued = 0;
    uint64_t jobs_processed = 0;
    uint64_t jobs_failed = 0;
    uint64_t batches_processed = 0;
    uint64_t current_queue_size = 0;
    std::chrono::milliseconds avg_latency{0};
};
```

### 4.3. Implementation

```cpp
class BackgroundIndexer : public IAsyncIndexer {
public:
    explicit BackgroundIndexer(
        IRuntimeStorageFacade& storage,
        JobDispatcher& dispatcher,
        size_t batch_size = 1000,
        size_t max_bytes = 50 * 1024 * 1024);

    JobId enqueue(IndexUpdateJob job, Transaction& txn) override;
    void flush() override;
    AsyncIndexerStats stats() const override;

private:
    // Called by the bounded AsyncIndex executor after dispatcher claim.
    void process_batch(std::vector<ClaimedJob>& batch);

    IRuntimeStorageFacade& m_storage;
    JobDispatcher& m_dispatcher;
    size_t m_batch_size;
    size_t m_max_bytes;

    AsyncIndexerStats m_stats;
};
```

`IRuntimeStorageFacade` is a narrow Layer 1/Layer 2 surface: unit/projection
reads, derived-index writes and transaction creation. It is not a concrete
`MemoryStack` and does not expose mutable profile internals. The executor
receives typed durable `AsyncIndex` jobs from the shared `JobDispatcher` (§4.6)
and may batch accepted records up to `batch_size` or `max_bytes` before writing
derived indexes through `MultiTableWriter`. The in-memory batch is volatile only
after a durable claim and successful executor acceptance; there is no
process-only `std::queue` as the source of truth.

Jobs are idempotent and guarded by `(unit_id, unit_revision, projection_kind)`.
The payload never embeds stale `SearchProjection` or embedding vectors. Before
writing derived indexes, the worker loads the authoritative unit envelope,
selected projection and payload/body state from storage:

- if the unit is missing or erased, the worker applies the erase path or marks
  the job `Done` when no derived rows remain;
- if `envelope.revision != job.unit_revision`, the job is stale and is marked
  `Done` without writes;
- otherwise the worker regenerates the requested `IndexKind` from authoritative
  data and commits derived index updates atomically.

Crash recovery relies on queue leases: crash after enqueue leaves `Pending`,
crash after claim returns the job to ready after lease expiry, and stale
revision/delete races are handled by the checks above.

### 4.4. Batch triggers

- Size: batch_size (default 1000 jobs).
- Bytes: max_bytes (default 50 MB).
- Time: max_wait_ms (default 100 ms).

### 4.5. Failure handling

При ошибке в batch:
- Successful claimed jobs call `complete(token, now_ms, result)` in the same
  transaction that commits derived index writes.
- Executor execution errors call `fail_retry(token, now_ms, backoff, last_error)`
  while attempts remain; the queue updates status, lease and ready/scheduled
  indexes.
- Exhausted retries call `fail_dead(token, now_ms, last_error, result)` and remain inspectable
  through `jobs_by_status = Dead`.
- No async index job is silently dropped.

### 4.6. Persistent Runtime Queue

`TaskQueue` / `JobStore` является downstream runtime abstraction
`agent-memory-cpp`, а не public API `mdbx-containers`. Он владеет job lifecycle:
`Pending`, `Running`, `Done`, `Dead`, `Cancelled`, retry/backoff policy, worker
leases, attempts, stale-worker recovery, priority/FIFO ordering и cancellation.
Retryable failures return to `Pending` with backoff; exhausted or unrecoverable
failures become inspectable terminal `Dead`. There is no durable `Failed`
state in the canonical queue.

Persistent MDBX implementation uses generic storage primitives only:

```text
jobs_by_id:
  KeyValueTable<JobId, JobRecord>

jobs_scheduled:
  RangeIndexTable<ScheduleKey, JobId>

jobs_ready:
  RangeIndexTable<ReadyOrderKey, JobId>

jobs_by_lease:
  RangeIndexTable<LeaseUntilKey, JobId>

jobs_by_status:
  ReverseIndexTable<JobStatus, JobId>
```

Default topology is one shared persistent queue per `MemoryStack`. `JobRecord.kind`
distinguishes `Compaction`, `AsyncIndex`, maintenance and future workers; the DBI
budget in `mdbx-containers-extension-tz.md` §5.5.1 counts this single +5 queue
delta once. A separate physical queue for AsyncIndexer would require another +5
profile delta and an updated budget checkpoint.

`JobDispatcher` is the only component allowed to claim from the shared queue.
The dispatcher owns the claim loop and submits typed claimed jobs to bounded
per-kind executors:

```cpp
enum class SubmitResult { Accepted, Saturated, ShuttingDown, Unsupported };

struct ClaimedJob;

class IJobExecutor {
public:
    virtual ~IJobExecutor() = default;
    virtual SubmitResult submit(ClaimedJob job) = 0;
};

class JobDispatcher {
public:
    void register_async_index_executor(IJobExecutor& executor);
    void register_compaction_executor(IJobExecutor& executor);

    std::optional<DispatchResult> run_once(
        int64_t now_ms,
        std::chrono::milliseconds lease_duration);
};
```

Dispatcher policy keeps the queue physical layout global but the dispatch
contract kind-aware: dispatcher claims the first ready job, decodes
`JobRecord.kind` and codec version centrally, and routes only typed payloads to
registered bounded executors. Workers do not call `claim_next`, do not scan for
their own kind, do not leave unsupported jobs at the head of ready, and do not
claim payload codecs they cannot decode. Token ownership stays with the
dispatcher until `submit()` returns `Accepted`; after acceptance the executor is
responsible for lease renewal, terminal/retry transition and cooperative
shutdown. If `submit()` returns `Saturated` or `ShuttingDown`, dispatcher
immediately calls `release_unhandled(token, now_ms, backoff, reason)` so the
claim does not disappear into a volatile queue. If no executor supports the
kind/version, dispatcher applies the unavailable-executor path below.

Unavailable executor path:

- unknown `JobRecord.kind` or unsupported codec version is unrecoverable and
  transitions to `Dead` with `last_error`;
- temporarily unavailable executor/capability calls
  `release_unhandled(token, now_ms, backoff, reason)`, returns the job to scheduled
  `Pending` without incrementing execution `attempts`;
- executor execution failure calls `fail_retry(token, now_ms, backoff, last_error)`
  and increments `attempts`; exhausted attempts call
  `fail_dead(token, now_ms, last_error, std::nullopt)`.

`JobRecord` хранит как минимум `job_id`, `kind`, codec/versioned immutable input
`payload_bytes`, `status`, `priority`, `created_at_ms`, `run_after_ms`,
`attempts`, `max_attempts`, `lease_owner`, `lease_epoch`, `lease_until_ms`,
`cancel_requested`, optional `started_at_ms`, `completed_at_ms`, `last_error`,
optional `result_codec_version` and `result_bytes`.

`payload_bytes` is immutable after enqueue. `result_bytes` is written only by a
terminal transition (`Done`, `Cancelled` with outcome, or `Dead` with terminal
diagnostics when the job kind defines one). Retention/pruning of result bytes is
the same as the owning `JobRecord`; admin views decode input and result payloads
separately.

```cpp
struct ClaimToken {
    JobId job_id;
    WorkerId worker_id;
    uint64_t lease_epoch;
    int64_t lease_until_ms;
};

struct ClaimedJob {
    ClaimToken token;
    JobRecord record;
};

struct ResultPayload {
    uint16_t codec_version = 0;
    std::vector<uint8_t> bytes;
};
```

`ScheduleKey = (run_after_ms, job_id)` используется только для delayed
promotion. `ReadyOrderKey = (priority_rank, job_id)`, где меньший ключ
выбирается раньше; `priority_rank` нормализуется так, чтобы higher logical
priority сортировался раньше. `LeaseUntilKey = (lease_until_ms, job_id)`.
`JobId` является durable monotonic sequence внутри queue и тем самым
обеспечивает FIFO для одинаковой priority без отдельной sequence/meta DBI.
Allocation uses an MDBX sequence bound to `jobs_by_id`; it is advanced inside
the same enqueue transaction and is never derived from `max(job_id) + 1`.
Pruning terminal jobs does not reset or reuse the sequence.

`claim_next(now, worker_id, lease_duration)` is only called by
`JobDispatcher`. It performs atomic compare/claim in a write transaction and
returns `ClaimedJob`:

1. `promote_due(now)` переносит все `jobs_scheduled` entries с
   `run_after_ms <= now` в `jobs_ready`. Implementation may process bounded
   pages, but it must repeatedly read the first due page (`offset = 0`) or use
   cursor pagination after each mutation, and it must not claim from ready until
   the due prefix for `now` is drained; otherwise high-priority due jobs could
   be hidden behind older low-priority jobs.
2. bounded read первого ready key (`limit = 1`), перечитать primary job record,
   проверить application predicate (`Pending`, not cancelled, attempts <
   max), перевести job в `Running`, increment `lease_epoch`, записать lease,
   обновить primary record and ready/status/lease indexes, затем commit.

Реализация не делает unbounded materialization очереди; large due backlogs
обрабатываются page loop-ом с продолжением по cursor.

Index membership is state-dependent:

```cpp
struct JobIndexKeys {
    std::optional<ScheduleKey> scheduled;  // Pending delayed
    std::optional<ReadyOrderKey> ready;    // Pending ready
    JobStatus status;                      // every durable state
    std::optional<LeaseUntilKey> lease;    // Running only
};
```

State transitions:

- `enqueue` создает `Pending` record и scheduled либо ready/status index entries.
  It allocates `JobId` from the durable `jobs_by_id` sequence in the same write
  transaction; concurrent enqueue and restart must preserve monotonicity.
- `claim_next` переводит `Pending -> Running`, снимает ready entry и ставит
  lease/status entries.
- `renew_lease` обновляет `lease_until_ms` и `jobs_by_lease`, returning an
  updated `ClaimToken`.
- `complete` переводит `Running -> Done`, удаляет lease entry и обновляет
  status.
- `fail_retry` переводит `Running -> Pending`, увеличивает attempts,
  применяет backoff в `run_after_ms` и возвращает scheduled или ready entry.
- `fail_dead` переводит `Running -> Dead`, когда retry budget исчерпан.
- `release_unhandled` переводит `Running -> Pending` with backoff when the
  dispatcher cannot currently route a known job kind/version; it does not
  increment execution attempts.
- `request_cancel` переводит `Pending -> Cancelled`; для `Running` ставит
  `cancel_requested`, после чего worker завершает cooperative cancel либо
  lease recovery переводит record в terminal/cancellable state.
- `ack_cancel` переводит `Running -> Cancelled`, удаляет lease entry и
  выставляет `completed_at_ms`.
- `recover_expired_leases(now_ms)` bounded-scan-ит `jobs_by_lease` по
  `lease_until_ms <= now_ms`; если `cancel_requested = true`, job переходит в
  `Cancelled`, иначе idempotent jobs возвращаются в `Pending`, а
  non-idempotent jobs помечаются как `Dead` with inspectable `last_error`.

The transition names above are shorthand. Normative owner-sensitive signatures
are:

```cpp
ClaimToken renew_lease(
    const ClaimToken& token,
    int64_t now_ms,
    int64_t new_deadline_ms);

void complete(
    const ClaimToken& token,
    int64_t now_ms,
    std::optional<ResultPayload> result);

void fail_retry(
    const ClaimToken& token,
    int64_t now_ms,
    std::chrono::milliseconds backoff,
    std::string last_error);

void release_unhandled(
    const ClaimToken& token,
    int64_t now_ms,
    std::chrono::milliseconds backoff,
    std::string reason);

void fail_dead(
    const ClaimToken& token,
    int64_t now_ms,
    std::string last_error,
    std::optional<ResultPayload> result);

void ack_cancel(
    const ClaimToken& token,
    int64_t now_ms,
    std::optional<ResultPayload> result);
```

Each successful claim and each lease recovery increments `lease_epoch`; stale
workers cannot reuse an old token. Ordinary renewal does not increment
`lease_epoch`, but it returns a new token carrying the updated
`lease_until_ms`; callers must replace their old token before scheduling the
next heartbeat.

All owner-sensitive transitions MUST accept `ClaimToken` and, in the same
transaction as any derived writes or terminal transition, verify:

```text
status == Running
lease_owner == token.worker_id
lease_epoch == token.lease_epoch
now_ms < lease_until_ms
```

If any predicate fails, the worker's write/transition is rejected as stale.
Acceptance case: A claims -> lease expires -> B claims -> A resumes => A's
derived write and `complete(token, now_ms, result)` are rejected. Long-running
batches must heartbeat with `renew_lease(token, now_ms, new_deadline)` before
the previous lease expires. Recovery is eligible when
`lease_until_ms <= now_ms`; owner-sensitive transitions are valid only when
`now_ms < lease_until_ms`, using the same caller-provided `now_ms` value for the
whole transaction.

#### 4.6.1 Typed transition pattern

`TaskQueue` should avoid one large open-coded `switch(JobStatus)` for owner
sensitive transitions. Model each durable state as a typed transition surface
that consumes the current persisted `JobRecord` snapshot plus any required
`ClaimToken`, validates the allowed transition, and returns:

- the next `JobRecord`;
- index delta for `jobs_ready`, `jobs_scheduled`, `jobs_by_lease` and
  `jobs_by_status`;
- optional executor payload delta such as compaction handoff update.

This is a local C++ pattern, not a dependency on a state-machine framework. It
is inspired by the `Automaton` / `Mode` / `Family` split in `andrewtc/mode`:
state-specific code owns its allowed transitions, a family exposes the common
interface, and transition code can explicitly transfer only the state it is
allowed to carry forward. For this roadmap, the concrete family is
`JobLifecycle`, with state surfaces such as `PendingJob`, `RunningJob`,
`CancelledJob`, `DeadJob` and `DoneJob`.

Acceptance checks:

- every durable transition has one typed function and one table-driven test
  case;
- invalid transitions are rejected before index mutation;
- owner-sensitive transitions require `ClaimToken`;
- transition functions produce both primary-record and secondary-index deltas,
  so `JobRecord` and queue indexes cannot drift.

Queue storage acceptance cases:

- concurrent enqueue transactions allocate distinct increasing `JobId` values;
- after process restart, the next enqueue continues from the durable MDBX
  sequence;
- after pruning terminal jobs, including the current maximum `JobId`, the next
  enqueue still allocates a greater id and never reuses a deleted id.
- saturated executor submit calls `release_unhandled` for the same `JobId`
  before returning from `run_once`;
- process shutdown after claim but before executor acceptance requeues through
  `release_unhandled` or lease recovery; no token remains only in memory;
- `CompactionExecutor` enforces `max_concurrency = 1` per stack/scope while
  `AsyncIndexExecutor` may use bounded parallelism;
- unsupported kind/version never blocks the ready head indefinitely.

Compaction handoff recovery uses the same `JobId`: `compaction_handoffs`
stores checkpoint payload for the running job, while queue lease recovery is
the only owner allowed to requeue or terminalize expired work. `CompactionWorker`
must not enqueue a second resume job for the same handoff.

`mdbx-containers` отвечает только за generic tables and transaction
atomicity; runtime semantics остаются здесь.

## 5. WriteGate

### 5.1. Purpose

Применяет WritePolicy из spec к каждой WriteRequest. Реализует importance threshold, dedupe, supersede, flush triggers.

### 5.2. Interface

```cpp
class IWriteGate {
public:
    virtual ~IWriteGate() = default;

    virtual GateDecision evaluate(const WriteRequest& req) = 0;

    // Manual flush (для тестов)
    virtual void flush() = 0;
};

enum class GateAction {
    Accept,        // write immediately
    Buffer,        // buffer до flush trigger
    Deduplicate,   // skip (existing similar unit)
    Supersede,     // replace old unit
    Merge,         // merge with existing
    Skip,          // skip (below importance threshold)
};

struct GateDecision {
    GateAction action;
    std::optional<KnowledgeUnitId> related_unit_id;  // для Deduplicate / Supersede / Merge
    std::string reason;
};
```

### 5.3. Implementation

```cpp
class DefaultWriteGate : public IWriteGate {
public:
    explicit DefaultWriteGate(
        IRuntimeProfileView& profile,
        WritePolicy policy);

    GateDecision evaluate(const WriteRequest& req) override;
    void flush() override;

private:
    IRuntimeProfileView& m_profile;
    WritePolicy m_policy;
    std::vector<WriteRequest> m_buffer;
    std::mutex m_mutex;
    std::chrono::steady_clock::time_point m_last_flush;
};
```

Реализует per policies-roadmap.md секция 3.3 (WriteGate behavior).

### 5.4. Integration с WritePolicy

Все правила из policies-roadmap.md секция 3 применяются:
- importance_threshold check.
- dedupe_distance_threshold через vector search.
- supersede check (bi-temporal).
- Flush trigger (OnTimer / OnSizeThreshold / OnImportance).

## 5.5. MemoryAwareContextPlanner

`MemoryAwareContextPlanner` is a planned policy service that runs before
retrieval and before final `ContextBuilder` formatting. It decides how deeply
the memory stack should be queried for a single turn.

It is useful for live agents where a fast answer may be more important than
deep recall. Urgency is only one axis: high urgency sets a latency ceiling, but
recall requirement and correctness risk define the minimum safe retrieval
depth. A safety-critical or high-cost-of-omission request must not silently drop
mandatory long-memory retrieval just because it is urgent.

The service consumes application-level signals such as:

- incoming event urgency;
- direct mention / interruption flags;
- normalized recall intent from an external intent detector;
- lexical recall features such as "remember", "before", or "yesterday" as
  examples only, not as the core C++ contract;
- correctness/safety risk;
- latency budget;
- token budget;
- minimum required tiers;
- enabled context tiers.

Conceptual API:

```cpp
enum class RecallRequirement {
    None,
    Opportunistic,
    Preferred,
    Required
};

enum class CorrectnessRisk {
    Low,
    Medium,
    High,
    SafetyCritical
};

struct ContextTierSet {
    bool short_required = false;
    bool medium_required = false;
    bool long_required = false;
    bool base_required = false;
};

struct ContextPlanningInput {
    std::string raw_query;
    double response_urgency = 0.0;
    RecallRequirement recall_requirement = RecallRequirement::Opportunistic;
    CorrectnessRisk correctness_risk = CorrectnessRisk::Low;
    ContextTierSet minimum_required_tiers;
    bool direct_mention = false;
    bool background_task = false;
    bool allow_async_extension = false;
    std::optional<uint64_t> latency_budget_ms;
    ContextBudget budget;
};

struct ContextTierPlan {
    bool include_short = true;
    bool include_medium = true;
    bool include_long = true;
    bool include_base = true;
    bool allow_graph_expansion = false;
    bool allow_compiled_wiki = false;
    size_t short_k = 8;
    size_t medium_k = 12;
    size_t long_k = 20;
};

struct ContextPlanDecision {
    ContextTierPlan plan;
    std::vector<std::string> reasons;
    std::string policy_version;
    std::optional<std::string> error;
    bool recall_trigger_detected = false;
    bool latency_limited = false;
    bool token_limited = false;
    bool async_extension_required = false;
};

class IMemoryAwareContextPlanner {
public:
    virtual ~IMemoryAwareContextPlanner() = default;
    virtual ContextPlanDecision plan(const ContextPlanningInput& input) = 0;
};
```

Suggested live-agent defaults:

| Situation | Planning outcome |
|---|---|
| High urgency, low recall requirement | `short + base`, no deep retrieval |
| High urgency, required recall | quick acknowledgement plus targeted long retrieval, or explicit "context not confirmed yet" |
| Safety-critical / high cost of omission | minimum required tiers cannot be disabled by latency policy |
| Ordinary message | `short + medium + capped long` |
| Reflection / background synthesis | full long retrieval, graph expansion, compiled wiki |

The planner does not replace `HybridRetriever` or `ContextBuilder`. It produces
the retrieval/context plan that those components execute. Applications may
override the defaults when correctness requires full recall. Decision reasons
must be traceable so failures can be attributed to planning, retrieval,
reranking, context trimming, or policy denial.

Mandatory invariant:

```text
if decision.error is absent:
    decision.plan includes input.minimum_required_tiers

if decision.error is present:
    caller must not execute decision.plan
```

If the planner cannot satisfy required tiers within policy, latency, or token
constraints, it must set `ContextPlanDecision::error` instead of silently
returning a shallow executable plan.

## 6. Service Lifecycle

### 6.1. Опциональность

Каждый сервис — opt-in через MemoryProfileSpec:

| Service | Capability | Default |
|---|---|---|
| PromptPrefixCache | `enable_prompt_cache = true` | opt-in (default ON для hybrid retrieval профилей) |
| ResponseCache | `response_cache_storage != Disabled` | opt-in, default **OFF** (для безопасности) |
| AsyncIndexer | `enable_async_indexer = true` or required by an async-only index policy | optional; rebuild/backfill/heavy async jobs |
| WriteGate | (always on if WritePolicy set) | conditional |
| CompactionWorker | `enable_compaction = true` | opt-in |
| MemoryAwareContextPlanner | `enable_context_planner = true` | opt-in |

Уточнение по defaults:
- `PromptPrefixCache` default **ON** для профилей с hybrid retrieval (BasicRag, AgentLTM, QAKnowledgeBase) — provider-side кэш даёт прямую экономию токенов без consistency рисков.
- `ResponseCache` default **OFF везде** — opt-in через явное
  `response_cache_storage != Disabled` в spec (см. §3.1 rationale).

### 6.2. Инициализация

При MemoryStack::open(spec):
1. Создаются DBI по capabilities.
2. Инициализируются runtime-сервисы:
   - `IPromptPrefixCache` — если `enable_prompt_cache=true` (default ON для hybrid retrieval профилей).
   - `IResponseCache` — только если `response_cache_storage != Disabled` (default OFF).
   - `AsyncIndexer` — если `enable_async_indexer=true` или выбран profile с
     async-only indexing policy.
   - `WriteGate` — если `spec.write_policy` задан.
   - `CompactionWorker` — если `enable_compaction=true`.
   - `IMemoryAwareContextPlanner` — если `enable_context_planner=true`.
3. Lifecycle ordering: `IPromptPrefixCache` создаётся ДО первого LLM call; `IResponseCache` создаётся как singleton (даже если выключен) с no-op stub.

### 6.3. Shutdown

При MemoryStack::close():
1. Stop accepting new requests.
2. AsyncIndexer.flush() — finish pending batches.
3. CompactionWorker.stop() — finish current job, then exit.
4. `IResponseCache` — persist to DBI (`response_cache`) only when
   `response_cache_storage == Mdbx`; `MemoryOnly` keeps no DBI state and
   `Disabled` uses the no-op stub. Physical storage costs +1 opt-in profile
   delta in `mdbx-containers-extension-tz.md` §5.5.1 only for `Mdbx`.
5. `IPromptPrefixCache` — persistence не требуется (ключи детерминированно вычисляются).
6. Освобождение handles.

### 6.4. Graceful degradation

Если runtime-сервис не может стартовать (например, MDBX не хватает места):
- Log error.
- MemoryStack продолжает работать в degraded mode (без этого сервиса).
- Service выбрасывает `RuntimeServiceUnavailable` при обращении.

## 7. Interaction Patterns

### 7.1. Write path

```
Application
  ↓ stack.write_unit(request)
  ↓
WriteGate.evaluate(request)
  ↓
  ├── Accept → MultiTableWriter (primary + critical indexes) and optional durable IndexUpdateJob for async-only indexes
  ├── Buffer → wait for trigger
  ├── Deduplicate → return existing unit_id
  ├── Supersede → mark old as Superseded, write new
  ├── Merge → combine with existing
  └── Skip → return Skip decision
```

### 7.2. Read path

```
Application
  ↓ stack.retrieve(plan)
  ↓
IResponseCache.lookup(response_cache_key)  // ТОЛЬКО если opt-in (default OFF)
  ├── hit → return cached response_text
  └── miss (или выключен) → continue
  ↓
MemoryAwareContextPlanner.plan(input)  // if opt-in: sets tier/depth/risk plan
  ↓
HybridRetriever.retrieve(plan)
  ├── LexicalRetriever (per lexical-search-roadmap.md)
  ├── DenseRetriever (per optimization-roadmap.md)
  ├── ...
  ↓
RRF fusion
  ↓
ContextBuilder
  ↓
IPromptPrefixCache.compute_cache_key(provider_id, model_id, prompt_prefix)  // ВСЕГДА (cheap, O(1))
  ↓
LLM call с cache_control: ephemeral metadata (Anthropic) / prompt_cache_key (OpenAI)
  ↓
IResponseCache.store(response_cache_key, response)  // ТОЛЬКО если opt-in
  ↓
IPromptPrefixCache.metrics().cache_read_input_tokens += response.usage.cache_read  // обновление провайдер-метрик
```

**Provider-side vs local cache split:**
- `IPromptPrefixCache.compute_cache_key()` вызывается при каждом LLM call (cheap, no-op если ключ не меняется).
- `IResponseCache.lookup()` вызывается **ТОЛЬКО** если
  `spec.response_cache_storage != Disabled`. По умолчанию — не вызывается.
- Это даёт чёткое разделение provider-side (always) и local response (opt-in).

### 7.3. Background path

```
CompactionWorker
  ↓
ICompactionJob.run()
  ├── DecayJob → uses UsageStatsComponent
  ├── DedupeJob → uses EmbeddingStore + scope
  ├── ArchiveColdJob → uses Lifecycle FSM
  ├── ...
  ↓
MultiTableWriter (atomic per job)
```

## 8. Observability

### 8.1. Метрики (per service)

Каждый сервис экспортирует свои метрики (см. секции выше). Все метрики доступны через:

```cpp
auto stats = stack.stats();
// stats.prompt_prefix_cache, stats.response_cache, stats.async_indexer, stats.compaction, stats.write_gate
```

Отдельные accessors для split-кэша:
```cpp
auto pp = stack.prompt_prefix_cache()->metrics();   // cache_read_input_tokens, cache_write_input_tokens
auto rc = stack.response_cache()->metrics();         // hits, misses, hit_rate
```

### 8.2. RetrievalTrace integration

Per knowledge-base-roadmap.md: `RetrievalTrace.trace` содержит:
- `cache_hit` (true/false).
- `cache_key` (если hit).
- `async_indexer_queue_size` (snapshot при retrieval).
- `compaction_active_jobs` (snapshot).

### 8.3. CLI integration

```
agent-memory-cli prompt-cache stats                 # IPromptPrefixCache (cache_read/cache_write_input_tokens)
agent-memory-cli response-cache stats              # IResponseCache (hits, misses, hit_rate)
agent-memory-cli response-cache clear [--scope <scope_id>]
agent-memory-cli indexer status
agent-memory-cli indexer flush
```

## 9. Implementation Order

Per memory-stacks-roadmap.md секция 16, конкретизация:

| Шаг | Что |
|---|---|
| 11.1 | WriteGate (impl WritePolicy logic) |
| 11.2 | AsyncIndexer (background thread + batch processing) |
| 12.5 | `IPromptPrefixCache` (in-memory LRU key dedup) + `AnthropicCacheControlAdapter` |
| 12.6 | `IResponseCache` stub (default OFF, no-op implementation для safe by default) |
| M2.x | `IResponseCache` full implementation with `MemoryOnly` and `Mdbx` modes (`response_cache` DBI only for `Mdbx`, +1 opt-in profile delta in `mdbx-containers-extension-tz.md` §5.5.1) |
| M2.x | `IMemoryAwareContextPlanner` + urgency/recall/risk-aware context policy |

## 10. Open Issues

- PromptCache invalidation при обновлении knowledge (когда unit перезаписан, cache entries могут быть stale). Решение: scope-based invalidation при bulk update.
- **ResponseCache correctness при tool/function calls: если LLM вызывает tools, response зависит не только от prompt, но и от tool results. Решение: хэшировать полный conversation context (prompt + tool definitions + tool call history + tool results), не только prompt. Альтернатива: opt-out response cache для turns с tool calls.**
- AsyncIndexer backpressure: если worker медленнее producer (writes), durable queue растёт. Решение: bounded durable depth, producer throttle/reject for async-only indexes, metrics/alerts and `fail_dead` for exhausted retries; no silent drop.
- WriteGate flush trigger: на скольких units считать "OnSizeThreshold" — bytes или count?
- Multi-stack coordination: если несколько MemoryStack разделяют scope, runtime services не координируются. M2+.
- ResponseCache staleness при live data: cache TTL 1 час может вернуть устать данные для time-sensitive запросов. Опции: (a) короткий TTL, (b) invalidation при записи в KnowledgeUnit, (c) включение timestamp в key (но это убивает hit rate).

## 11. References

- `guides/memory-stacks-roadmap.md` — секции 7, 11, 16; ADR-013.
- `guides/mdbx-containers-extension-tz.md` — §12.5 runtime queue storage recipe and §5.5.1 DBI budget.
- `guides/knowledge-base-roadmap.md` — RetrievalTrace интеграция.
- `guides/policies-roadmap.md` — WritePolicy.
- `guides/compaction-roadmap.md` — CompactionWorker.
- `guides/lexical-search-roadmap.md` — LexicalRetriever.
- `guides/optimization-roadmap.md` — DenseRetriever.
- ai-agent-playbook: concepts/llm-research/Управление контекстом LLM-агента - стратегии снижения стоимости.md — prompt caching economics.
- ai-agent-playbook: concepts/ai-agents/AI-VTuber с нуля на TypeScript - модульная архитектура.md — модуль memory с async indexer.
