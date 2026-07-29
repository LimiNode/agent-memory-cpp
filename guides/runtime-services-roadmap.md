# runtime-services-roadmap.md

Спецификация cross-cutting runtime сервисов (PromptCache, AsyncIndexer, WriteGate) для подсистемы памяти `agent-memory-cpp`. Документ конкретизирует ADR-013 (Runtime services) из `guides/memory-stacks-roadmap.md` секции 11.

> **C++17 compliance:** кодовые сниппеты используют `const std::vector<T>&` вместо `std::span<T>` и явные конструкторы вместо designated initializers. Provider-specific prompt/response caching is a host integration, not an `agent-memory-cpp` runtime service or DBI contract.

## 1. Purpose

- Что описывает: AsyncIndexer (batch вставки в lexical/vector индексы), WriteGate (применяет WritePolicy), bounded queues and the host boundary for context fingerprints. Provider-specific cache adapters remain in the host application.
- Cross-references: memory-stacks-roadmap.md (ADR-013, секции 7, 11, 16), knowledge-base-roadmap.md (RetrievalTrace), policies-roadmap.md (WritePolicy), compaction-roadmap.md (job submission), mdbx-containers-extension-tz.md (§12.5 storage recipe, §5.5.1 DBI budget).

## 2. Layer Architecture Review

Per memory-stacks-roadmap.md секция 11:

```
Layer 1: Storage Primitives
Layer 2: Retrieval Primitives
Layer 3: Memory Stacks
Layer 4: Applications

Cross-cutting Runtime Services (orthogonal):
  CompactionWorker, WriteGate, AsyncIndexer
  Используют Layer 1 + Layer 2 через интерфейсы
```

Runtime-сервисы доступны из любого layer, но сами не зависят от конкретного MemoryStack. Каждый сервис — singleton per MemoryStack (если включён в spec).

## 3. Host LLM Cache Boundary (Not Core API)

The normative host boundary is now
[`host-llm-cache-integration.md`](host-llm-cache-integration.md). The detailed
cache sketches retained in this historical section are non-normative research
notes only: they do not define core interfaces, `MemoryStack` services, DBIs,
capabilities, CLI commands, defaults or implementation milestones.

`agent-memory-cpp` does not call an LLM, construct provider requests, store
provider response caches, own `response_cache` DBIs, or expose Anthropic/OpenAI
types. A host may implement the historical sketches below in its own integration
package, but they are explicitly non-normative for this library.

The only core-side handoff is provider-neutral metadata derived from a finished
context:

```cpp
struct ContextFingerprint {
    std::array<std::uint8_t, 32> value;
    std::uint32_t schema_version = 1;
};

struct PromptPrefixDescriptor {
    ContextFingerprint context_fingerprint;
    std::vector<KnowledgeUnitRef> included_units;
    std::vector<SourceRefId> included_source_refs;
};
```

The host is responsible for permission-aware key construction, provider cache
metadata, response-cache invalidation and all stale/tool-call semantics. It may
not write generated responses into canonical memory without an explicit normal
write/curation path.

### Historical Note

Detailed provider-cache, response-cache and cache-augmented-generation designs
belong exclusively to the host integration guide. They are intentionally absent
from this runtime roadmap so no core API, DBI, capability, lifecycle step,
implementation milestone or CLI command can be inferred from them.

## 4. AsyncIndexer

### 4.1. Purpose

AsyncIndexer выполняет rebuild/backfill и тяжёлые или explicitly
eventually-consistent indexing jobs. Он **не** является владельцем default
write visibility для critical retrieval indexes.

Default M0/M1 consistency mode:

- `MemoryStack::create_or_get_unit` / `update_unit` commits envelope, components, projections,
  content-key/by-kind indexes, lexical candidate/stat indexes needed by active
  retrieval, metadata filters and selected lightweight secondary indexes in one
  `MultiTableWriter` transaction.
- AsyncIndexer may rebuild those indexes from authoritative unit revisions, but
  it must not be required for a newly committed unit to become retrievable in
  the same profile.

Async eventual mode is allowed only as explicit profile policy for indexes that
declare `eventually_consistent=true` (for example heavy embedding recompute,
HNSW graph rebuild, bulk lexical backfill). In that mode create/update must enqueue
a durable `IndexUpdateJob(unit_id, projection_kind, projection_version, index_kind)`
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
    ProjectionKind projection_kind;
    ProjectionVersionRef projection_version;
    IndexKind index_kind;
};

Before applying an `IndexUpdateJob`, a worker compares its complete
`projection_version` with the active projection. A mismatch is a successful
stale no-op, never a write that revives an older lexical, vector or translated
projection. `derivation_generation = 0` is valid only for projections whose
owner declares that no independent projection refresh exists.

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

Jobs are idempotent and guarded by `(unit_id, projection_kind, projection_version)`.
The payload never embeds stale `SearchProjection` or embedding vectors. Before
writing derived indexes, the worker loads the authoritative unit envelope,
selected projection and payload/body state from storage:

- if the unit is missing or erased, the worker applies the erase path or marks
  the job `Done` when no derived rows remain;
- if the active projection version differs from `job.projection_version`, the job is stale and is marked
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
enum class SubmitRejectionReason { Saturated, ShuttingDown, Unsupported };

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

struct MaintenanceJobPayload {
    uint16_t codec_version = 0;
    std::vector<uint8_t> bytes;
};

template<class Payload>
struct TypedClaimedJob {
    ClaimToken token;
    JobId job_id;
    Payload payload;
};

using AnyTypedClaimedJob = std::variant<
    TypedClaimedJob<CompactionJobPayload>,
    TypedClaimedJob<IndexUpdateJob>,
    TypedClaimedJob<MaintenanceJobPayload>>;

struct AcceptedSubmission {};

struct RejectedTypedJob {
    SubmitRejectionReason reason = SubmitRejectionReason::Unsupported;
    AnyTypedClaimedJob unaccepted_job;
};

using SubmitOutcome = std::variant<AcceptedSubmission, RejectedTypedJob>;

class IJobExecutor {
public:
    virtual ~IJobExecutor() = default;
    virtual SubmitOutcome try_submit(AnyTypedClaimedJob job) = 0;
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
dispatcher until `try_submit()` returns `AcceptedSubmission`; after acceptance the
executor is responsible for lease renewal, terminal/retry transition and
cooperative shutdown. `ClaimedJob`/`TypedClaimedJob` are move-only. Executors
never receive opaque `JobRecord` bytes. If `try_submit()` returns
`RejectedTypedJob`, it must include the unchanged typed job with the original
token/job id; dispatcher immediately calls
`release_unhandled(token, now_ms, backoff, reason)` so the claim does not
disappear into a volatile queue. `SubmitOutcome` is a closed variant: accepted
submissions carry no unaccepted job, and rejected submissions always carry one.
If no executor supports the kind/version, dispatcher applies the
unavailable-executor path below.

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

`ScheduleKey = (run_after_ms, job_id)` используется только для delayed
promotion. `ReadyOrderKey = (priority_rank, job_id)`, где меньший ключ
выбирается раньше; `priority_rank` нормализуется так, чтобы higher logical
priority сортировался раньше. `LeaseUntilKey = (lease_until_ms, job_id)`.
`JobId` является durable monotonic sequence внутри queue и тем самым
обеспечивает FIFO для одинаковой priority без отдельной sequence/meta DBI.
Allocation uses `TableSequence(jobs_by_id)`; it is advanced inside
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
  It allocates `JobId` from `TableSequence(jobs_by_id)` in the same write
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
- saturated executor submit returns the move-only `ClaimedJob` to dispatcher,
  and dispatcher calls `release_unhandled` for the same `job_id`, `worker_id`
  and `lease_epoch` before returning from `run_once`;
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

Применяет WritePolicy из spec к create/update requests. Реализует importance threshold, dedupe, supersede, flush triggers.

### 5.2. Interface

```cpp
class IWriteGate {
public:
    virtual ~IWriteGate() = default;

    virtual GateDecision evaluate(const CreateUnitRequest& req) = 0;

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

    GateDecision evaluate(const CreateUnitRequest& req) override;
    void flush() override;

private:
    IRuntimeProfileView& m_profile;
    WritePolicy m_policy;
    std::vector<CreateUnitRequest> m_buffer;
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

`MemoryStack` lifecycle covers only library-owned services: WriteGate,
AsyncIndexer, CompactionWorker and the provider-neutral context planner. It
opens the profile-selected storage, starts enabled local workers after storage
validation, flushes or checkpoints owned work on close, and exposes degraded
status for a failed local service. It neither initializes an LLM client nor
performs provider-cache or response-cache operations.

## 7. Interaction Patterns

The core write path is `WriteGate -> MultiTableWriter -> optional IndexUpdateJob`;
the retrieval path is `MemoryAwareContextPlanner -> retrievers -> ContextBuilder`.
`ContextBuilder` returns context, citations and optional
`MaterializationInstruction` values to the host. Any LLM request,
provider-specific cache metadata, tool call and generated response is outside
this path.

## 8. Observability

Runtime-service metrics cover local queue depth, worker health, write decisions,
index freshness, compaction state and retrieval/context traces. Host cache
metrics remain host telemetry and must not be surfaced as `MemoryStack`
statistics or CLI commands.

## 9. Implementation Order

1. WriteGate and atomic critical-index writes.
2. AsyncIndexer plus bounded durable job handling where a selected profile needs it.
3. Compaction worker/checkpoint handoff.
4. Provider-neutral context planner and trace contracts.

## 10. Open Issues

- AsyncIndexer backpressure, retry exhaustion and operator diagnostics.
- WriteGate batch thresholds and policy observability.
- Multi-stack coordination for shared scopes.
- Planner policy evolution without weakening required-tier guarantees.

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
