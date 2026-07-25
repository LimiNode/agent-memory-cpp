# compaction-roadmap.md

Спецификация CompactionWorker, job types, scheduling и CompactionHandoff для подсистемы памяти `agent-memory-cpp`. Документ конкретизирует ADR-009 (Compaction strategy) из `guides/memory-stacks-roadmap.md` и описывает runtime-сервис, который ортогонален MemoryStack (per ADR-013). Persistent queue contract живёт в `runtime-services-roadmap.md` §4.6, physical queue DBI recipe — в `mdbx-containers-extension-tz.md` §12.5/§5.5.1.

> C++17 compliance: кодовые сниппеты используют `const std::vector&` вместо `std::span` и явные сеттеры/positional constructor calls вместо designated initializers. Decay formula — canonical (см. `policies-roadmap.md` §2.3). Reading list по RAG / summary-tree / community-summary papers: `guides/research-reading-map.md`.

## 1. Purpose

Этот документ описывает:

- `CompactionWorker` — фоновый компонент MemoryStack, обрабатывающий compaction jobs.
- `ICompactionJob` интерфейс и его контракт (validate / run / checkpoint / restore / is_idempotent).
- Job types: DecayJob, DedupeJob, MergeJob, ArchiveColdJob, SummaryPromotionJob, EmbeddingRecomputeJob, CompactionHandoffJob.
- `CompactionHandoff` — структурированная запись для crash recovery и operational handoff.
- Scheduling policy: hybrid on-write + on-schedule; threading model; crash recovery; admin operations (CLI + programmatic).

Cross-references: `guides/memory-stacks-roadmap.md` (ADR-009, ADR-013, секция 8, 16 шаг 14), `guides/knowledge-units-roadmap.md` (CompactionMetaComponent, Lifecycle FSM, episode compaction 5.5.4), `guides/knowledge-base-roadmap.md` (DecayAwareRetriever, eval pipeline CompactionHandoff test 9.6), `guides/policies-roadmap.md` future (DecayPolicy, WritePolicy), `guides/runtime-services-roadmap.md` (§4.6 persistent runtime queue), `guides/mdbx-containers-extension-tz.md` (generic storage recipe 12.5, DBI budget 5.5.1, MultiTableWriter 3.7).

Non-goals: подробная спецификация embedding model адаптеров, LLM-based summary generation (использует внешний `ITextAdapter`), distributed compaction (multi-process).

Affective-memory profiles require extra caution. `MergeJob` and
`SummaryPromotionJob` must not collapse affectively meaningful episodes solely
by text similarity: trigger, target, appraisal, impacted goals, responsibility,
controllability, outcome, prediction error, unresolvedness, and relationship
evidence are part of the semantic contract. See
[`affective-memory-roadmap.md`](affective-memory-roadmap.md) ADR-A06.

## 2. CompactionWorker

### 2.1. Architecture

`CompactionWorker` — фоновый runtime component, обрабатывающий compaction
jobs через bounded executor, registered in `JobDispatcher`. Effective
concurrency is one active compaction job per stack/scope (per ADR-013, см. также
Open Issue 17.6 в `memory-stacks-roadmap.md`). Persistent scheduling uses the shared downstream
`TaskQueue`/`JobStore` from `runtime-services-roadmap.md` §4.6 and the generic
`jobs_by_id`, `jobs_scheduled`, `jobs_ready`, `jobs_by_lease`,
`jobs_by_status` MDBX recipe from `mdbx-containers-extension-tz.md` §12.5.
`JobRecord.kind = Compaction`; compaction-specific data is a versioned opaque
payload plus the single extra `compaction_handoffs` DBI for operational handoff
state.

```cpp
class CompactionWorker {
public:
    explicit CompactionWorker(MemoryStackRuntimeFacade& runtime);
    ~CompactionWorker();

    // Lifecycle
    void start();           // spawn thread (lazy start on first enqueue, explicit ok)
    void stop();            // graceful shutdown: finish current job, then exit
    void stop_immediate();  // cancel current job (use carefully, breaks idempotency)

    // Job submission
    JobId enqueue(ICompactionJob::Ptr job);
    JobId enqueue_delayed(ICompactionJob::Ptr job, std::chrono::milliseconds delay);

    // Status
    CompactionStats stats() const;
    std::vector<JobRecordView> active_jobs() const;
    std::vector<JobRecordView> recent_jobs(size_t limit) const;
    std::optional<CompactionHandoff> current_handoff() const;

    // Manual trigger (для admin tool)
    JobId trigger_now(ICompactionJob::Kind kind, CompactionParams params);

    // Health
    bool is_running() const noexcept;
    uint64_t worker_thread_id() const noexcept;
};
```

Worker создаётся через `MemoryStack::compaction()` (см. `memory-stacks-roadmap.md` секция 7.1) и доступен только если `spec.enable_compaction = true`. Создание с capability mismatch выбрасывает `std::logic_error`.

### 2.2. Threading Model

Решение: `CompactionExecutor` with `max_concurrency = 1` per stack/scope (per
Open Issue 17.6 в `memory-stacks-roadmap.md`). It may own a thread, but durable
claiming belongs to the shared `JobDispatcher`.

- Write operations (on-write cheap jobs) и compaction jobs используют одну MDBX транзакцию через `MultiTableWriter` (`mdbx-containers-extension-tz.md` секция 3.7).
- Compaction jobs ждут в shared persistent `jobs_*` queue. `JobDispatcher` is
  the only component that claims jobs; it routes typed `CompactionJobPayload`
  records to the bounded compaction executor. No compaction-specific queue DBIs
  are opened.
- Нет параллельного compaction внутри одного stack — избежание lock contention на secondary indexes.
- Executor uses bounded write transactions; no job holds one write transaction
  for its whole runtime.
- On-write cheap operations (mark dirty, enqueue DecayJob) выполняются в вызывающем потоке через `MultiTableWriter`, не блокируют worker.

```cpp
// Conceptual flow: on-write path
auto write_result = stack.write_unit(WriteRequest{...});
// внутри write_unit:
//   1. MultiTableWriter txn открыт
//   2. envelope + components + projections записаны
//   3. CompactionMetaComponent.dirty_decay = true
//   4. Если dirty threshold reached -> enqueue DecayJob
//   5. Commit
// worker thread (если запущен) подхватывает DecayJob асинхронно
```

### 2.3. Lifecycle интеграция с MemoryStack

```cpp
auto stack = MemoryStack::open(path, MemoryProfiles::AgentLongTermMemory());
// stack.compaction() возвращает ссылку на CompactionWorker
// Worker автоматически стартует при первом enqueue (lazy start)
// Worker останавливается при stack.close() (graceful shutdown)
```

Lifecycle контракт:

- `MemoryStack::open()` — создаёт `CompactionWorker`, не запускает thread.
- Первый `enqueue()` — стартует thread (lazy).
- `MemoryStack::close()` — вызывает `worker.stop()` (graceful), дожидается завершения текущего job.
- Деструктор `MemoryStack` — гарантирует `stop()` (RAII).
- Crash recovery при следующем `open()` — runtime queue выполняет lease
  recovery, а worker использует `compaction_handoffs` DBI только как
  checkpoint payload для того же `JobId`.

Worker может быть запущен явно через `worker.start()` если приложение хочет запустить предварительный decay sweep до первого write (например, для восстановления индексов после cold start).

See [`usage-llm-wiki.md`](usage-llm-wiki.md) for using `CompactionWorker` as the daily-flush / synthesis engine in a Karpathy-style LLM Wiki on top of `agent-memory-cpp`.

### 2.4. CompactionStats

```cpp
struct CompactionStats {
    uint64_t total_enqueued = 0, total_completed = 0, total_failed = 0, total_partial = 0;
    uint64_t active_jobs_count = 0, queued_jobs_count = 0;
    std::chrono::milliseconds avg_job_duration{0}, last_completion_at{0};
    uint64_t units_processed_total = 0, units_changed_total = 0;
    uint64_t units_erased_total = 0, units_promoted_total = 0;
};
```

`stats()` читает атомарные счётчики (lock-free) без обращения к MDBX.

## 3. ICompactionJob

### 3.1. Interface

```cpp
class ICompactionJob {
public:
    using Ptr = std::shared_ptr<ICompactionJob>;

    enum class Kind : uint16_t {
        Decay = 0, Dedupe = 1, Merge = 2, ArchiveCold = 3,
        SummaryPromotion = 4, EmbeddingRecompute = 5, CompactionHandoff = 6,
    };

    virtual ~ICompactionJob() = default;

    virtual Kind kind() const = 0;
    virtual std::string name() const = 0;
    virtual CompactionParams params() const = 0;
    virtual Result<void> validate(const IRuntimeProfileView& profile) const = 0;
    virtual Result<JobOutcome> run(
        const ClaimToken& token,
        JobExecutionContext& context) = 0;
    virtual std::optional<HandoffState> checkpoint() const { return std::nullopt; }
    virtual void restore(HandoffState state) {}
    virtual bool is_idempotent() const { return true; }
    virtual int priority() const { return 0; }
};
```

`validate()` — перед постановкой в shared queue, возвращает ошибку если
preconditions не выполнены. It receives only `IRuntimeProfileView`, not a
concrete `MemoryStack`. `run()` вызывается только через `JobDispatcher` for
`JobRecord.kind = Compaction`. The job MUST NOT open independent storage
transactions. Long jobs use the bounded batch transaction protocol below;
small jobs that do not need heartbeat/checkpoint may complete in a single
bounded transaction.

```cpp
class JobExecutionContext {
public:
    ICompactionReadContext& reads();
    ICompactionWriteContext& writes();

    void verify_active_lease(const ClaimToken& token, int64_t now_ms);
    ClaimToken renew_lease(
        const ClaimToken& token,
        int64_t now_ms,
        int64_t new_deadline_ms);
    void write_handoff(const CompactionHandoff& handoff);
    void complete_with_writes(
        const ClaimToken& token,
        int64_t now_ms,
        JobOutcome outcome);
    void fail_retry_with_writes(
        const ClaimToken& token,
        int64_t now_ms,
        std::chrono::milliseconds backoff,
        std::string last_error,
        const CompactionHandoff& updated_handoff);
    void fail_dead_with_writes(
        const ClaimToken& token,
        int64_t now_ms,
        std::string last_error,
        std::optional<JobOutcome> outcome);
};
```

Bounded batch protocol for long jobs:

```text
repeat:
    compute/read next bounded batch without a write transaction
    begin write transaction
    set one caller-provided now_ms for this transaction
    verify ClaimToken and now_ms < lease_until_ms
    apply idempotent or revision-guarded application writes
    update CompactionHandoff checkpoint to exactly the committed state
    renew lease and receive updated ClaimToken
    commit
until complete

final transaction:
    verify updated ClaimToken with one now_ms
    apply final writes, if any
    persist CompactionOutcomePayload in JobRecord.result_bytes
    transition Running -> Done
    commit
```

Crash semantics:

- committed batches remain visible;
- an unfinished batch rolls back completely;
- recovery resumes from the last committed `compaction_handoffs` checkpoint;
- checkpoint state must never describe uncommitted application writes;
- whole-job atomicity is allowed only for small jobs that do not require
  heartbeat or intermediate checkpoint.

При exception dispatcher records `last_error` and applies `fail_retry` or
`fail_dead` through the same fenced transition. `checkpoint()`/`restore()` —
опциональный механизм для длинных jobs. `is_idempotent()` — критично для crash
recovery: если `false` и checkpoint отсутствует, recovery sends the job to
`Dead` for manual review rather than replaying it. DecayJob, DedupeJob,
ArchiveColdJob, EmbeddingRecomputeJob — idempotent. MergeJob — conditional (если
restore checkpoint возможен).

### 3.2. JobOutcome

```cpp
struct JobOutcome {
    uint64_t units_processed = 0;
    uint64_t units_changed = 0;
    uint64_t units_erased = 0;
    uint64_t units_promoted = 0;
    std::chrono::milliseconds duration{0};
    std::optional<std::string> error;
    bool was_partial = false;  // true если job был прерван (checkpoint resume или crash)
};
```

`was_partial` устанавливается если job завершился через exception в середине обработки, или был cancelled через `stop_immediate()`. Используется для retry-логики и reporting.

### 3.3. Compaction job payload (storage representation)

Generic lifecycle state lives in shared `JobRecord` (`jobs_by_id`) and queue
indexes from `runtime-services-roadmap.md` §4.6. Compaction-specific data is the
versioned payload stored in `JobRecord.payload_bytes`; it does not duplicate
status, lease, attempts, priority or worker ownership:

```cpp
struct CompactionJobPayload {
    uint16_t codec_version = 1;
    ICompactionJob::Kind kind;
    std::string name;
    std::vector<uint8_t> params_blob;          // сериализованные params
};

struct CompactionOutcomePayload {
    uint16_t codec_version = 1;
    JobOutcome outcome;
};
```

`JobRecord.kind` is `Compaction`; `JobRecord.priority`, `run_after_ms`,
`attempts`, `max_attempts`, `lease_owner`, `lease_epoch`, `lease_until_ms`,
`status`, `last_error` and timestamps remain the single source of truth for
lifecycle and dispatch. `CompactionJobPayload` is immutable initial input.
Mutable checkpoint/progress lives only in `compaction_handoffs`; `JobRecord`
holds lifecycle, attempts, lease and error. `CompactionOutcomePayload` is
serialized into `JobRecord.result_bytes` during a terminal transition, with
`result_codec_version = CompactionOutcomePayload.codec_version`; input
`payload_bytes` is never overwritten. Admin APIs may expose `JobRecordView` by
joining `JobRecord` with decoded input and decoded result separately.

## 4. Job Types

### 4.1. DecayJob

Пересчёт decay scores для всех units в scope. Обновляет `UsageStatsComponent` и `CompactionMetaComponent`.

```cpp
struct DecayParams {
    ScopeId scope_id;
    double min_score_threshold = 0.0;  // units ниже threshold → кандидаты на archive
    size_t batch_size = 1000;
};

class DecayJob : public ICompactionJob {
    // Алгоритм (canonical DecayJob::run, см. policies-roadmap.md §2.3):
    //   for each unit candidate в scope_id (по CompactionMetaComponent.dirty_decay = true):
    //     usage = get_usage_stats(unit.unit_id)
    //     new_score = apply_decay_and_boost(unit.base_score, usage, policy, now_ms)
    //     new_score = apply_post_filters(
    //         new_score, usage,
    //         get_speaker(unit.unit_id), agent_self_id,
    //         now_ms, policy)
    //     if new_score < policy.cold_threshold:
    //       unit.compaction_meta.cold_candidate = true
    //     clear CompactionMetaComponent.dirty_decay = false
    //
    // Применяет DecayPolicy из MemoryProfileSpec.decay_policy:
    //   - mode (None / Linear / Exponential / Logistic) — выбор формулы
    //   - half_life_ms (для Exponential)
    //   - use_boost, cooldown_ms, self_echo_suppression, cold_threshold
    //
    // Не удаляет записи — это задача ArchiveColdJob.
    // Идемпотентен: повторный запуск даёт тот же результат.
};
```

Default `min_score_threshold` для `AgentLongTermMemory` = `cold_threshold` из `DecayPolicy` (default 0.01).

### 4.2. DedupeJob

Дедупликация через vector similarity. Сливает близкие units через supersede или merge.

```cpp
struct DedupeParams {
    ScopeId scope_id;
    double distance_threshold;     // обычно = WritePolicy.dedupe_distance_threshold (0.15)
    size_t batch_size = 500;
    bool prefer_supersede = true;  // vs merge
};

class DedupeJob : public ICompactionJob {
    // Алгоритм:
    //   for each pair (unit_a, unit_b) в scope с cosine_distance <= distance_threshold:
    //     if prefer_supersede и WritePolicy.allow_supersede:
    //       newer_unit supersedes older_unit (lifecycle = Superseded)
    //     elif WritePolicy.allow_merge:
    //       merge в один unit с combined metadata
    //     else:
    //       skip (no-op)
    //
    // Distance вычисляется через embeddings:
    //   - default: cosine distance между EmbeddingMetaComponent.vector_ref
    //   - fallback: BM25F similarity на unit_projections
    //
    // Идемпотентен (после supersede / merge — повторный запуск no-op).
};
```

### 4.3. MergeJob

Слияние N units в один. Используется для episode compaction (см. `knowledge-units-roadmap.md` секция 5.5.4) и related facts.

```cpp
enum class MergeStrategy {
    Concatenate,   // raw concat primary_text
    Summarize,     // LLM-generated summary (требует ITextAdapter)
    Structured,    // per-kind merge rules (для QAPair, Fact — special handling)
};

struct MergeParams {
    std::vector<KnowledgeUnitId> source_units;
    MergeStrategy strategy = MergeStrategy::Concatenate;
    std::optional<KnowledgeUnitId> target_unit;  // если nullopt, создаётся новый unit
    ScopeId scope_id;
};

class MergeJob : public ICompactionJob {
    // Алгоритм:
    //   1. Загрузить source_units (envelope + payload + components).
    //   2. Применить strategy:
    //      - Concatenate: target.primary_text = join(source_units.primary_text, "\n---\n")
    //      - Summarize: ITextAdapter.summarize(source_texts) -> target.primary_text
    //      - Structured: per-kind merge (QAPair: union variants; Fact: keep latest)
    //   3. Создать target_unit (если nullopt) или обновить существующий.
    //   4. Для каждого source_unit: lifecycle = Superseded, superseded_by = target_unit.
    //   5. CompactionMetaComponent для source_units: merged_into = target_unit.
    //   6. update lineage: target_unit.supersedes = source_units.
    //
    // Условно идемпотентен: при restore из checkpoint повторный merge даёт тот же target.
};
```

### 4.4. ArchiveColdJob

Физическое или логическое удаление units, помеченных как cold candidates через DecayJob.

```cpp
struct ArchiveColdParams {
    ScopeId scope_id;
    double score_threshold;          // аналог DecayParams.min_score_threshold
    bool physical_erase = false;    // true = erase из MDBX; false = lifecycle = Deprecated
    uint64_t older_than_ms = 0;     // дополнительный фильтр: created_at_ms < now_ms - older_than_ms
    size_t batch_size = 500;
};

class ArchiveColdJob : public ICompactionJob {
    // Алгоритм:
    //   for each unit в scope где:
    //     CompactionMetaComponent.cold_candidate = true
    //     AND last_decay_score < score_threshold
    //     AND created_at_ms < (now_ms - older_than_ms):
    //       if physical_erase:
    //         erase из knowledge_units DBI
    //         erase из secondary indexes
    //         erase из unit_components DBI
    //         erase из unit_projections DBI
    //         erase из per-payload DBI
    //       else:
    //         envelope.lifecycle_state = Deprecated (или Erased для audit-safety)
    //
    // Идемпотентен: повторный запуск no-op для уже обработанных units.
};
```

Физическое удаление vs logical (lifecycle = Erased) — выбор зависит от требований auditability. По умолчанию используется logical (audit-safe).

### 4.5. SummaryPromotionJob

Промоция частей в `CompiledArticle` (для wiki-maintainer). Анализирует часто-retrieved facts, генерирует summary, создаёт `CompiledArticlePayload`.

```cpp
struct SummaryPromotionParams {
    ScopeId scope_id;
    double usage_threshold;         // use_count > threshold
    double relevance_threshold;      // last_decay_score > threshold
    size_t max_articles_per_run = 10;
    std::optional<std::string> target_owner;       // CompiledArticlePayload.owner
    std::optional<std::string> target_readers;     // CompiledArticlePayload.readers
    bool use_llm_summarizer = false;               // ITextAdapter.summarize vs extractive
};

class SummaryPromotionJob : public ICompactionJob {
    // Алгоритм:
    //   1. Найти candidate units:
    //      - use_count >= usage_threshold
    //      - last_decay_score >= relevance_threshold
    //      - kind in {Fact, Chunk, Summary}
    //   2. Кластеризовать candidates (по embeddings, density-based или fixed N).
    //   3. Для каждого кластера:
    //      - Сгенерировать summary (LLM или extractive top-K sentences).
    //      - Создать CompiledArticle unit с CompiledArticlePayload:
    //          title = cluster centroid keywords
    //          body = generated summary
    //          derived_from = source unit_ids
    //      - Пометить source units: lifecycle = Superseded, superseded_by = article unit_id.
    //
    // Требует: CompiledArticles capability (см. memory-stacks-roadmap.md секция 9, validation rule 10).
    // Без LLM: extractive summary через top-K sentences (cheap fallback).
};
```

### 4.6. EmbeddingRecomputeJob

Пересчёт embeddings с `source_model` на `target_model`. Используется при model upgrade.

```cpp
struct EmbeddingRecomputeParams {
    ScopeId scope_id;
    std::string source_model_id;
    std::string source_model_version;
    std::string target_model_id;
    std::string target_model_version;
    std::vector<ProjectionKind> projection_kinds;  // какие projection'ы пересчитывать
    size_t batch_size = 100;
};

class EmbeddingRecomputeJob : public ICompactionJob {
    // Алгоритм:
    //   for each unit в scope:
    //     1. Загрузить projection_kind text (через unit_projections).
    //     2. Embed через target_model (через ITextEmbedder adapter).
    //     3. Записать новый embedding с revision + 1:
    //        - embedding_meta: новый model_id, model_version, computed_at_ms
    //        - embedding_vectors: новый vector blob
    //     4. Старые embeddings остаются с revision = N до ArchiveColdJob.
    //
    // Требует: EmbeddingMigration capability (см. validation rule в memory-stacks-roadmap.md секция 10).
    //
    // Checkpointable: каждые batch_size units сохраняет HandoffState.
    // Идемпотентен: при restore повторно эмбеддит уже processed units (same result).
};
```

См. также `mdbx-containers-extension-tz.md` §5.5 (`embedding_meta`,
`embedding_vectors`) для storage layout embedding meta полей и
`policies-roadmap.md` для downstream decay policy.

### 4.7. CompactionHandoffJob (meta-job)

Сохраняет handoff state в `compaction_handoffs` DBI для crash recovery.

```cpp
struct CompactionHandoffParams {
    HandoffId handoff_id;
    SessionId session_id;
    std::string goal;                       // что делает compaction
    std::vector<std::string> plan_steps;    // ["scan candidates", "apply decay", ...]
    std::vector<std::string> constraints;   // max_duration, max_units, safety
    std::optional<bool> approval_required;
};

class CompactionHandoffJob : public ICompactionJob {
    // Автоматически enqueue'ится при:
    // - compaction worker stop (graceful): создаёт handoff с status = Aborted,
    //   current_state = serialized HandoffState / CompactionJobPayload checkpoint.
    // - compaction worker startup: queue lease recovery owns requeue;
    //   handoff payload restores the same JobId checkpoint.
    // - explicit checkpoint call: periodic snapshot каждые checkpoint_interval_ms.
    //
    // Приоритет: высокий (priority = 100).
    //
    // Не идемпотентен по умолчанию (каждая запись — новая HandoffId),
    // но повторное выполнение с тем же handoff_id безопасно (overwrite).
};
```

См. секцию 5 для детальной структуры `CompactionHandoff`.

### 4.8. SummaryTreeJob (M2+, RAPTOR-style)

Reference: arXiv:2401.18059 — "RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval".

Принцип: cluster similar chunks, generate summary nodes, build hierarchical tree. Retrieval может проходить на разных уровнях абстракции.

```cpp
class SummaryTreeJob final : public ICompactionJob {
    struct Params {
        ScopeId scope_id;
        double cluster_distance_threshold;  // для clustering
        size_t max_tree_depth = 3;
        size_t summaries_per_node = 5;
        bool use_llm_for_summaries = true;  // external LLM
    };
};
```

Algorithm:

1. Cluster chunks в scope через embedding similarity (UMAP + GMM или simpler).
2. Для каждого cluster: generate summary через LLM (external adapter).
3. Создать `SummaryUnit` (`kind=Summary`) с `derived_from = [cluster_chunks]`.
4. Рекурсивно cluster summaries -> build tree до `max_depth`.
5. Update `SearchProjection` (Summary projection для каждого summary node).

Storage:

- Summary units в существующей `knowledge_units` DBI.
- Tree edges в `graph_edges_by_src`/`graph_edges_by_dst` DBI (`parent_unit_id` -> `child_unit_ids`).
- Optional: dedicated `summary_tree` DBI для fast tree traversal.

Integration:

- Retrieval может выбирать tree level:
  - Level 0: leaf chunks (high precision).
  - Level `max_depth`: root summaries (high coverage, lower precision).
- Adaptive: start at root, descend if needed.

### 4.9. CommunitySummaryJob (M2+, GraphRAG-style)

Reference: arXiv:2404.16130 — "From Local to Global: A Graph RAG Approach to Query-Focused Summarization".

Принцип: detect communities in entity graph, generate summary per community. Global sensemaking questions ("main themes of corpus") решаются через community-level summaries.

```cpp
class CommunitySummaryJob final : public ICompactionJob {
    struct Params {
        ScopeId scope_id;
        size_t min_community_size = 5;
        size_t max_communities = 100;
        bool use_llm_for_summaries = true;
    };
};
```

Algorithm:

1. Cluster entities в graph через Leiden / Louvain algorithm.
2. Для каждой community: aggregate entity -> unit links.
3. Generate summary через LLM (external adapter): "Main themes and relationships in this community".
4. Создать `SummaryUnit` с `kind=Summary` и metadata: `community_id`.

Integration:

- Retrieval query types добавляются:
  - "global" -> match against community summaries.
  - "entity_centric" -> match against entity graph.
  - "chunk_centric" -> match against leaf chunks (existing).

## 5. CompactionHandoff Structure

### 5.1. Purpose

`CompactionHandoff` — структурированная запись состояния compaction worker для:

1. **Crash recovery** — восстановление после неожиданного завершения (worker thread died, process killed, OOM).
2. **Operational handoff** — передача состояния между процессами (например, между короткими CLI вызовами и долгим worker).

Per `ai-agent-playbook: concepts/llm-research/Управление контекстом LLM-агента — стратегии снижения стоимости.md` — auto-compaction должен быть operational handoff, не литературный пересказ. Это означает: структурированные поля (goal, plan_steps, constraints), а не free-form text summary.

### 5.2. Schema

```cpp
enum class HandoffStatus : uint8_t {
    InProgress = 0,    // job выполняется
    Completed = 1,     // успешно завершён
    Failed = 2,        // завершён с ошибкой
    Aborted = 3,       // остановлен (graceful stop_immediate или crash detection)
};

struct CompactionHandoff {
    HandoffId handoff_id;
    SessionId session_id;
    int64_t created_at_ms = 0;
    int64_t updated_at_ms = 0;
    HandoffStatus status = HandoffStatus::InProgress;

    // Operational handoff fields (структурированные, не литературные)
    std::string goal;                              // что делает compaction (1 sentence)
    std::vector<std::string> constraints;          // max_duration, max_units, safety constraints
    std::vector<std::string> plan_steps;           // ["scan candidates", "apply decay", ...]
    std::optional<bool> approval_required;         // true если sensitive operation (physical_erase)
    std::string current_state;                     // serialized HandoffState / compaction checkpoint (base64 or msgpack blob)
    std::vector<std::string> error_history;        // лог ошибок для post-mortem
    std::optional<std::string> next_step;          // checkpoint: что делать при resume

    // Progress tracking
    uint64_t units_total = 0;
    uint64_t units_processed = 0;
    uint64_t units_changed = 0;
    uint64_t checkpoint_at_unit = 0;               // последний checkpointed unit offset
    int64_t estimated_remaining_ms = 0;

    // Job metadata (для resume)
    ICompactionJob::Kind job_kind;
    JobId job_id;
};
```

### 5.3. Storage

DBI `compaction_handoffs` (см. `mdbx-containers-extension-tz.md` §5.5.1 и
§12.5):

```
compaction_handoffs
  key = SessionId → CompactionHandoff
```

Session-scoped: один handoff per session. При создании нового handoff старый помечается `Completed` или `Aborted`.

### 5.4. Checkpoint lifecycle

Worker вызывает `checkpoint_progress()` каждые `checkpoint_interval_ms`
(default 5000 ms) at batch boundaries: сериализует
`current_job_->checkpoint()` в `handoff_.current_state`, обновляет
`units_processed`, `units_changed`, `checkpoint_at_unit`, renews the queue lease
and commits the handoff in the same bounded write transaction as the batch's
application writes. Graceful stop requests a final bounded checkpoint batch when
the current batch completes. A process crash cannot flush in-memory state; after
restart recovery uses only the last committed handoff checkpoint.

## 6. Scheduling Policy

### 6.1. Hybrid on-write + on-schedule (per ADR-009)

**On-write (synchronous, cheap):** выполняется в вызывающем потоке через `MultiTableWriter` в рамках write transaction.

- Schema validation (`envelope` + `payload` + `components` schema checks).
- Lifecycle state update (initial = Active).
- `SourceRef` write в `metadata_filters` DBI.
- Lightweight secondary index update (`knowledge_units_by_kind`, `metadata_filters`).
- Mark dirty flags: `CompactionMetaComponent.dirty_decay = true`, `dirty_dedupe = true`.
- Enqueue compaction job if dirty threshold reached.

**On-schedule (background, heavy):** выполняется worker thread асинхронно.

- Decay recompute (`DecayJob`).
- Dedupe (`DedupeJob`).
- Merge (`MergeJob`).
- Archive cold (`ArchiveColdJob`).
- Summary promotion (`SummaryPromotionJob`).
- Embedding recompute (`EmbeddingRecomputeJob`).
- Graph cleanup (orphaned edges).

### 6.2. Job submission rules

| Trigger | Action |
|---|---|
| Unit write | `CompactionMetaComponent.dirty_decay = true` |
| Unit write завершён | Enqueue `DecayJob` если dirty_decay count в scope > threshold |
| Unit write с `importance_score < WritePolicy.importance_threshold` | Skip enqueue |
| Dedupe distance threshold reached (on write) | Enqueue `DedupeJob` |
| `cold_candidate` flag count в scope > N | Enqueue `ArchiveColdJob` |
| Schedule timer (per `WritePolicy.flush_interval_ms`) | Enqueue `DecayJob` (forced flush) |
| Model upgrade | Enqueue `EmbeddingRecomputeJob` для всех units в scope |
| Manual CLI / admin tool | `worker.trigger_now(kind, params)` |
| Worker stop (graceful) | Auto-enqueue `CompactionHandoffJob` (Aborted status) |
| Worker startup | Run queue lease recovery, then use in-progress `compaction_handoffs` only to restore checkpoint payload for the same `JobId` |

### 6.3. Job priority

Compaction jobs обрабатываются FIFO с учётом priority (больше = раньше):

| Kind | Priority | Обоснование |
|---|---|---|
| `CompactionHandoffJob` | 100 | высокий — для crash recovery |
| `DecayJob` | 50 | нормальный — affects retrieval score |
| `DedupeJob` | 40 | нормальный — affects storage size |
| `MergeJob` | 30 | нормальный — episode compaction |
| `SummaryPromotionJob` | 20 | низкий — admin-triggered |
| `EmbeddingRecomputeJob` | 10 | низкий — не блокирует retrieval |
| `ArchiveColdJob` | 10 | низкий — reclamation |

### 6.4. Dirty counter aggregation

Решение "когда enqueue job" использует счётчик dirty units per scope (`dirty_decay_count`, `dirty_dedupe_count`, `last_flush_ms`) — агрегат в `unit_components` DBI или отдельный ключ.

Threshold для enqueue (per stack):

- `AgentLongTermMemory`: dirty_decay >= 100 или elapsed >= 30s с последнего DecayJob.
- `CompiledWiki`: dirty_decay >= 50 (маленький stack, чаще).
- `SpeakerAwareChat`: dirty_decay >= 1000 (высокий write rate).

## 7. Crash Recovery

### 7.1. Compaction worker crash

При startup `CompactionWorker`:

1. Запускает `TaskQueue::recover_expired_leases(now)`; queue является
   единственным владельцем requeue/terminalize решения для expired jobs.
2. Открывает `compaction_handoffs` DBI, ищет in-progress handoff
   (status = InProgress) для текущего session.
3. Если найден и соответствующий `JobId` возвращён queue recovery в
   claimable `Pending` state — dispatcher later decodes `CompactionJobPayload`
   from immutable `JobRecord.payload_bytes`; executor десериализует job from that
   payload, validates `handoff->job_id`, restores state через
   `ICompactionJob::restore(handoff->current_state)`, resume с
   `checkpoint_at_unit`.
4. Если `is_idempotent()` = true — безопасно перезапустить с offset (или с начала если checkpoint отсутствует).
5. Если `is_idempotent()` = false и checkpoint отсутствует — fail safely,
   логирует error, переводит shared `JobRecord` в `Dead` (manual review
   required).

### 7.2. MemoryStack crash

MDBX гарантирует atomicity per bounded transaction (через `MultiTableWriter`).
Compaction job, прерванный mid-batch:

- незавершённая batch transaction откатывается полностью;
- already committed batches remain visible and are not rolled back;
- primary DBI, secondary indexes, handoff checkpoint and lease renewal are
  consistent for each committed batch;
- resume при next `open()` starts from the last committed handoff checkpoint;
- if the lease expires between batches, another worker may claim the same
  `JobId`; stale commits from the old worker are rejected by `ClaimToken` /
  `lease_epoch` fencing.

Acceptance cases:

- crash before batch commit leaves no checkpoint or application writes from that
  batch;
- crash after batch commit resumes from that batch checkpoint;
- lease expires between batches and recovery reclaims the same `JobId`;
- old worker attempts commit after a new claim and is rejected;
- checkpoint describes exactly committed application state.

### 7.3. Partial failure

Если job fails partway (например, embedding model недоступен, DecayPolicy невалиден для части units):

1. Build updated `CompactionHandoff` with checkpoint/progress and append
   `handoff.error_history` (timestamp, unit_id, error message).
2. If retry budget remains, call one fenced transition:

```cpp
context.fail_retry_with_writes(
    token,
    now_ms,
    backoff,
    last_error,
    updated_handoff);
```

This atomically verifies the token, writes the checkpoint, transitions the same
`JobRecord` from `Running` to `Pending`, preserves the same `JobId`, creates the
new `jobs_scheduled` entry and removes the lease entry. `enqueue_delayed()` is
only for a new independent operation, not retry/resume of the same handoff.
3. If attempts are exhausted, call `fail_dead_with_writes(token, now_ms, ...)`
   and persist the terminal outcome in `JobRecord.result_bytes`.

```cpp
struct RetryPolicy {
    uint32_t max_attempts = 3;
    std::chrono::milliseconds base_backoff{1000};
    double backoff_multiplier = 5.0;
    std::chrono::milliseconds max_backoff{5 * 60 * 1000};
};
```

## 8. Default Stack Compaction Configurations

Из `memory-stacks-roadmap.md` секция 8 (Default Memory Stacks) и 9 (Capability Matrix):

| Stack | Compaction jobs | Schedule | Trigger |
|---|---|---|---|
| `BasicRag` | None (opt-in) | — | — |
| `QAKnowledgeBase` | None (opt-in) | — | — |
| `AgentLongTermMemory` | Decay, Dedupe, ArchiveCold | Timer 30s + OnWrite | `WritePolicy.flush_interval_ms = 30s` |
| `SpeakerAwareChat` | Decay (recent cleanup) | Timer 5min | `WritePolicy.flush_interval_ms = 5s` write, DecayJob 5min |
| `CompiledWiki` | SummaryPromotion, ArchiveCold | Manual + Timer 1h | Manual `trigger_now`, periodic 1h |
| `TemporalFactStore` | ArchiveCold | Timer 1h | `TemporalComponent.valid_until_ms < now_ms` |
| `FullResearchMemory` | All jobs | Timer 30s + OnWrite | `AgentLongTermMemory` config + additional |

Конфигурации определены через `MemoryProfileSpec`:

- `BasicRag`, `QAKnowledgeBase`: `enable_compaction = false` по умолчанию. Можно включить через minor in-place migration (см. `memory-stacks-roadmap.md` секция 14.1).
- `AgentLongTermMemory`: `enable_compaction = true`, `decay_policy.mode = Exponential`, `half_life_ms = 7d`, `cold_threshold = 0.01`.
- `SpeakerAwareChat`: `enable_compaction = true`, decay для cleanup старых episodes.
- `CompiledWiki`: `enable_compaction = true`, `enable_compiled_article = true` (validation rule: CompiledArticles requires Compaction).
- `TemporalFactStore`: `enable_compaction = true`, ArchiveCold для expired facts.
- `FullResearchMemory`: union всех capabilities.

## 9. Admin Operations

### 9.1. CLI

Команды `agent-memory-cli` target (см. `memory-stacks-roadmap.md` секция 16, шаг 16):

```
agent-memory-cli compaction status
agent-memory-cli compaction enqueue <kind> [--scope <scope_id>] [--params <json>]
agent-memory-cli compaction list [--limit N] [--status <pending|running|done|failed>]
agent-memory-cli compaction cancel <job_id>
agent-memory-cli compaction handoff list
agent-memory-cli compaction handoff inspect <session_id>
agent-memory-cli compaction retry <job_id>     # re-enqueue Dead job
agent-memory-cli compaction stats              # CompactionStats snapshot
```

Подробная спецификация CLI — в `guides/cli-roadmap.md` (future).

### 9.2. Programmatic

```cpp
auto& compaction = stack.compaction();

DecayParams decay_params;
decay_params.scope_id = "user:yaroslav";
decay_params.min_score_threshold = 0.05;
decay_params.batch_size = 500;
auto job_id = compaction.enqueue(std::make_shared<DecayJob>(decay_params));

MergeParams merge_params;
merge_params.source_units = {unit_a, unit_b, unit_c};
merge_params.strategy = MergeStrategy::Summarize;
merge_params.scope_id = "agent:nika";
auto merge_id = compaction.trigger_now(ICompactionJob::Kind::Merge, merge_params);

auto stats = compaction.stats();           // CompactionStats snapshot
auto recent = compaction.recent_jobs(10);  // last 10 JobRecordView records

auto handoff = compaction.current_handoff();
if (handoff && handoff->status == HandoffStatus::InProgress) {
    log("{} / {} units, ETA {}ms", handoff->units_processed,
        handoff->units_total, handoff->estimated_remaining_ms);
}
```

## 10. Implementation Order

Из `memory-stacks-roadmap.md` секция 16 (шаг 14), конкретизация для compaction:

| Шаг | Что | Зависимости |
|---|---|---|
| 14.0 | `ICompactionJob` interface + `CompactionWorker` skeleton + `CompactionJobPayload` / `CompactionOutcomePayload` serialization | Шаги 1-2 (envelope + DBI) |
| 14.1 | shared runtime `jobs_*` queue integration (`runtime-services-roadmap.md` §4.6; storage recipe `mdbx-containers-extension-tz.md` §12.5) + `JobDispatcher` compaction executor + `DecayJob`/`DedupeJob`/`ArchiveColdJob` (M1 minimum); no compaction-specific queue DBIs | 14.0, шаги 5, 10 (components + DecayPolicy) |
| 14.2 | `CompactionHandoff` structure + crash recovery + `CompactionHandoffJob` meta-job | 14.1 |
| 14.3 | `MergeJob` для episode compaction | `ConversationEpisodePayload` (см. `knowledge-units-roadmap.md` 5.5.4) |
| 14.4 | `SummaryPromotionJob` (с `ITextAdapter` интерфейсом) + `EmbeddingRecomputeJob` | `CompiledArticlePayload`, `DenseVectors` capability |
| 14.5 | CLI commands для admin operations + Eval pipeline (CompactionHandoff test case) | 14.2-14.4 |
| C32 (M2) | `SummaryTreeJob` (RAPTOR-style summary tree) | 14.4, embedding capabilities, `ITextAdapter` |
| C33 (M2) | `CommunitySummaryJob` (GraphRAG-style community summaries) | C32, graph retrieval, `ITextAdapter` |
| C34 (M2) | Tiered storage transitions в `DecayPolicy` (hot/warm/cold/erased) | 14.1, `ProductQuantizationCodec` (см. `optimization-roadmap.md`) |

См. также `knowledge-units-roadmap.md` секция 10 для синхронизации.

## 11. Open Issues

- **Optimal batch_size per job type.** Default values (100-1000) — placeholder, требуют benchmark на realistic workloads.
- **Adaptive scheduling.** Auto-tune interval based on write rate: при высоком write rate — DecayJob чаще. Требует metric collection (write events per second).
- **Cross-stack compaction coordination.** Если несколько stack'ов (AgentLTM + CompiledWiki) разделяют scope — нужна координация compaction jobs во избежание redundant work. Решение: shared dirty counter или distributed lock.
- **Distributed compaction (multi-process).** Out of scope для v0.x. Возможное решение: external coordinator (Redis lock / ZooKeeper).
- **EmbeddingRecompute rollback.** При провале migration на target_model — как откатить? Embeddings хранятся с revision, можно вернуться к N-1, но search индексы нужно перестроить. Требует explicit rollback strategy.
- **Shutdown timeout.** `stop()` — graceful, ждёт завершения current job. Если job висит (LLM timeout, model unavailable) — нужен timeout cap. Default: 60s, configurable.
- **Physical_erase audit log.** При `ArchiveColdJob.physical_erase = true` — нужен отдельный `audit_log` DBI (какие unit_ids удалены, когда, по какой причине).
- **Compaction metrics в RetrievalTrace.** Добавить `compaction_metrics` в `RetrievalTrace` (см. `knowledge-base-roadmap.md` секция 9.1): `last_decay_at_ms`, `cold_candidate_count`, `pending_decay_jobs`.
- **Tiered storage (M2+).** Hot tier: `ExactVectorIndex` / HNSW / `BinaryCandidateFilterIndex` (fast queries). Warm tier: F16 storage, `BinaryCandidateFilter` + AE-128 (medium speed, smaller). Cold tier: `ProductQuantizationCodec` (M2+) для archived embeddings (slow queries, ~96x compression). Compaction: tier transitions per `DecayPolicy.cold_threshold` + age. Hot -> Warm: после 30 days без retrieval. Warm -> Cold: после 90 days без retrieval. Cold -> Erased: после 365 days без retrieval.

## 12. References

Internal documents:

- `guides/memory-stacks-roadmap.md` — секции 7 (MemoryStack), 8 (Default Stacks), 9 (Capability Matrix), 10 (Validation Rules), 16 (Implementation Order, шаг 14); ADR-009 (compaction strategy), ADR-013 (runtime services). Physical compaction DBI names live in `mdbx-containers-extension-tz.md` §12.5 / §5.5.1.
- `guides/knowledge-units-roadmap.md` — `CompactionMetaComponent`, Lifecycle FSM, episode compaction (секция 5.5.4).
- `guides/knowledge-base-roadmap.md` — `DecayAwareRetriever`, `UsageStatsComponent`, Eval pipeline CompactionHandoff test case (секция 9.6), `EmbeddingMetaComponent`.
- `guides/policies-roadmap.md` (future) — DecayPolicy / WritePolicy полные спецификации.
- `guides/runtime-services-roadmap.md` — секция 4.6 (`TaskQueue` / `JobStore` runtime contract).
- `guides/mdbx-containers-extension-tz.md` — секция 12.5 (generic runtime-job storage recipe), секция 3.7 (MultiTableWriter).
- `guides/cli-roadmap.md` (future) — `agent-memory-cli` compaction subcommands.
- `guides/runtime-services-roadmap.md` — PromptCache, AsyncIndexer, WriteGate, persistent runtime queue.

External references (ai-agent-playbook):

- `concepts/llm-research/Управление контекстом LLM-агента — стратегии снижения стоимости.md` — auto-compaction patterns, operational handoff vs literal summary.
- `playbooks/Какой уровень памяти агента выбрать.md` — auto-compaction как operational handoff.
- `concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md` — compaction в СВИНОПАС, anti-loop / cooldown patterns.
- `concepts/ai-agents/AI-агенты и AI-VTuber — архитектурные паттерны из видео 2026.md` — hot/cold path separation, layered memory.

External research references (arXiv):

- arXiv:2401.18059 — "RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval".
- arXiv:2404.16130 — "From Local to Global: A Graph RAG Approach to Query-Focused Summarization".
- arXiv:2410.05779 — "LightRAG: Simple and Fast Retrieval-Augmented Generation".
- arXiv:2405.14831 — "HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models".
- arXiv:2404.13207 — "STaRK: Benchmarking LLM Retrieval on Textual and Relational Knowledge Bases".
- See also: `guides/research-reading-map.md`.
