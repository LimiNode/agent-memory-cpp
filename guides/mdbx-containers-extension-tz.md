# Техническое задание: расширение header-only библиотеки `mdbx-containers`

## 0. Архитектурный контекст

Этот документ описывает storage primitives уровня Layer 1 (см. `guides/memory-stacks-roadmap.md`, секция 11) для новой компонентной архитектуры памяти: `Envelope + Components + SearchProjections`. Секция 5.5 этого TZ является единственным canonical physical MDBX manifest; `guides/memory-stacks-roadmap.md` владеет data model, profiles and capability validation.

Ключевые компоненты архитектуры, для которых задаются таблицы:

- `KnowledgeUnitEnvelope` — lookup-critical hot path (DBI: `knowledge_units`).
- `Components` — operational components хранятся в `unit_components` через
  `TypeDiscriminatedTable`; kind-specific payloads живут в capability-gated
  per-kind DBIs.
- `SearchProjections` — retrieval-specific text views, multi-version по `revision` (DBI: `unit_projections`).
- Embeddings — multi-projection, multi-model (DBI: `embedding_meta`, `embedding_vectors`).
- Secondary indexes — scope-aware (`scope_id` как префикс ключа), см. ADR-012 в `guides/memory-stacks-roadmap.md`.

Применение storage primitives в MDBX env (бюджет 64 DBI, см. секцию 12.6 roadmap-а) согласуется с capability matrix из `guides/memory-stacks-roadmap.md`, секция 9.

## 1. Цели и non-goals

### Цели

Расширить `external/mdbx-containers/include/mdbx_containers/` набором классов и утилит, необходимых для следующих направлений `agent-memory-cpp`:

1. **Knowledge Units** — все kinds имеют общий `KnowledgeUnitEnvelope` в
   `knowledge_units`; kind-specific и operational data хранятся в
   `unit_components` и per-kind payload DBIs. `TypeDiscriminatedTable` не
   является canonical storage для всего unit-а.
2. **Fielded BM25F** — отдельные поля per source: `title`, `heading_path`, `body`, `code_blocks`, `tags`, `symbols`, `metadata_typed`, `qa_question`, `qa_answer`, `summary`. Поля должны быть доступны как inverted index с composite key `(token_id, field_id)`.
3. **Generic relation indexing** — caller-owned opaque ids/tags/payloads,
   атомарное ведение явно названных outgoing/incoming orientations и
   bounded/paginated storage reads. `NodeKind`, `EdgeKind` и
   traversal/expansion policy остаются в `agent-memory-cpp`.
4. **QA Knowledge Base** — `QAPair` с полями `valid`, `last_verified`, `priority`, `freq`, `supersedes`. Inverted index по `question` tokens.
5. **Temporal index** — range queries по timestamp (`valid_from`, `valid_until`, `recorded_at`) с inclusive/exclusive bounds.
6. **Metadata filters** — поддержка `(metadata_key, metadata_value) -> ResourceId` reverse index для pre-filter.
7. **Atomic multi-table writes** — primary + secondary indexes обновляются в
   одной транзакции; `ContextBuilder` является downstream consumer-ом, а не
   upstream storage primitive.
8. **Composable secondary-index reads** — generic primitives для lookup,
   range-scan и typed relation traversal. RRF, cross-encoder rerank и
   retrieval orchestration остаются Layer 2 behavior-ом `agent-memory-cpp`.
9. **Application-owned traceability support** — generic opaque payload storage
   и bidirectional relation patterns, достаточные для downstream
   Addressable Compression / Progressive Disclosure без переноса доменных
   enum-ов памяти агента в `mdbx-containers`.

### Non-goals

- **Backward compatibility** — не ломать публичный API существующих классов.
- **ANN / HNSW / approximate vector search** — не входит в scope. Существующий `vector::VectorStore` остаётся exact baseline.
- **Full-text search primitives** (phrase, proximity, fuzzy, n-gram) — реализуются на уровне agent-memory-cpp поверх BM25-индекса.
- **Schema versioning framework поверх mdbx-containers** — payload versioning остаётся на стороне приложения (текущие префиксы `agent_memory.document.v1` и т.п. сохраняются).
- **Background compaction API** — не в scope первой итерации.
- **Bloom filters** — не в scope.
- **Multi-process write coordination через MDBX штатный multi-process API** не входит в scope (single-writer per env остаётся правилом M0/M1/M2). **Multi-node replication** через upstream `sync` subsystem (см. §1.5) рассматривается как опциональный opt-in profile за пределами M0/M1/M2; см. §11.7 для решения adoption.
- **Per-thread transaction model** сохраняется как primary design constraint (см. `common/Connection.hpp`); multi-table write обеспечивается одним `Transaction`-объектом, разделяемым между таблицами, а не координацией отдельных транзакций (см. `guides/memory-stacks-roadmap.md`, open issue 17.9).

### 1.5. Upstream sync subsystem (informational)

#### 1.5.1 Purpose

Этот раздел носит **чисто информационный характер** на момент написания ТЗ. Локальный submodule `external/mdbx-containers` (commit `e9e9f2f`, tag `v1.0.0-97-ge9e9f2f`) **не содержит** подкаталога `sync/`. Upstream `external/mdbx-containers` main snapshot `d4d219c` реализует sync subsystem, описанный ниже. Цель раздела — зафиксировать состояние upstream sync v0.1 в этом ТЗ, чтобы будущие ревизии не дрифтовали относительно фактической поверхности API. Никакие изменения в текущих DBIs секции 5 не подразумеваются; фиксация adoption — отдельный вопрос §11.7.

#### 1.5.2 Source attribution policy

Поскольку upstream `mdbx-containers` не публикует RFC / спецификации отдельно от кода, в этом разделе используется **двухуровневая citation pattern**:

1. **Public + supplementary internal** — если у PR есть публичный URL (например, GitHub PR `#86`), ссылаемся на него как на первичный источник; внутренние файлы (`DESIGN.md`, `SyncEngine.hpp`) приводятся как подтверждение / цитата, не как независимая authorities.
2. **Internal-only with explicit disclaimer** — для деталей, не отражённых в публичных PR description (например, точные LOC-счётчики header-файлов), приводится оценка со ссылкой на конкретный файл; такие числа считаются best-effort и должны быть перепроверены при формальной adoption.

Никакие URL, arXiv ID, или DOI не публикуются без верификации; если источника нет — пишем «нет публичного источника» и приводим internal anchor.

#### 1.5.3 Upstream `sync` v0.1 — файлы

Директория: `include/mdbx_containers/sync/` в upstream main snapshot `d4d219c`. Состав:

| Файл | Назначение | Ориентир LOC |
|---|---|---|
| `DESIGN.md` | Дизайн-контракт v0.1, locked decisions | н/д |
| `SyncEngine.hpp` | Pull/push/apply координатор | н/д |
| `SyncWorker.hpp` | Фоновый pull/apply lifecycle | н/д |
| `protocol.hpp` | `PullRequest`, `PullResponse`, `PushRequest`, `PushResponse` + `CancellationToken` | н/д |
| `ISyncPeer.hpp` | Абстрактный transport interface (`pull`, `push`, `request_cancel`) | н/д |
| `DirectSyncPeer.hpp` | Полностью in-process peer | н/д |
| `TransportMessageCodec.hpp` | Версионированная envelope-кодировка transport DTO | н/д |
| `HttpTransport.hpp` | Framework-neutral HTTP-shaped adapter seam | н/д |
| `WebSocketTransport.hpp` | Framework-neutral WebSocket-shaped adapter seam | н/д |
| `TransportMiddleware.hpp` | Transport middleware, allow-list, rate limiting, auth context policies | н/д |

Внутренний layout дополнительно содержит `stores/` подкаталог с 5 системными DBIs (см. §1.5.10) и compile-time gate `MDBXC_SYNC_ENABLED` (default `0`). Подключение sync-функционала требует явного `-DMDBXC_SYNC_ENABLED=1` при сборке `mdbx-containers`.

Public first-class упоминания sync subsystem в PR descriptions — это GitHub PR #86–#105 (chronologically; детали в §1.5.8). PR #104 и PR #105 уже merged на момент этого snapshot-а; transport DTO codec и HTTP adapter seam входят в v0.1.

#### 1.5.4 v0.1 support matrix

Sync v0.1 покрывает wire-format round-trip только для определённых table types upstream:

| Upstream table type | Sync v0.1 | Замечание |
|---|---|---|
| `KeyValueTable<K, V, Options>` | Supported | Базовый KV путь |
| `KeyTable<K, Options>` | Supported | Set-like без payload |
| `ValueTable<V>` | Supported | Одиночный value per name |
| `SequenceTable<V>` | Supported | Auto-increment ID generator |
| `VectorStore` (через `vector::VectorStore`) | Supported (indirect) | Persistent path использует `SequenceTable` + `KeyValueTable` в v0.1 |
| `AnyValueTable<K, Options>` | **NOT supported** | Heterogeneous typed payload не имеет wire-format в v0.1 |
| `KeyMultiValueTable<K, V, Options>` | **NOT supported** | DUPSORT multi-payload; блокирует inverted index sync (см. §5.6) |
| `HashedKeyValueStore` | **NOT supported** | Coverage shadow-graphs and HashedKeyValueStore excluded |

Покрытие для любого table type, не перечисленного как «Supported», считается **out of v0.1 scope**; см. §5.6 для влияния на DBI-схему настоящего проекта.

#### 1.5.5 Wire format locks (по `DESIGN.md`)

Locked decisions, цитаты согласно `include/mdbx_containers/sync/DESIGN.md`:

- **ChangeBatch magic** — 8 ASCII-байт `MDBXCSYN`. Позволяет отличать batch payload от других потенциальных payload-ов по тому же транспорту.
- **Transport envelope magic** — 8 ASCII-байт `MDBXCPRT`. Используется `TransportMessageCodec` для HTTP/WebSocket/IPC/message-queue DTO поверх отдельных `ChangeBatch` records.
- **ChangeBatch codec integers**, включая `codec_version`, `batch_version`, `batch_flags`, `seq`, `time_unix_ns`, `ops_count` и op-level fields, сериализуются **little-endian**; эти значения не сортируются как wire bytes.
- **`ChangeLogStore` key `seq` part** сериализуется **big-endian**, чтобы MDBX bytewise range scan по ключу `(origin_node_id, seq)` сохранял числовой порядок `seq`.
- **Payload integers** (record sizes, lengths, флаги) сериализуются **little-endian**, чтобы MDBX native word order не требовал byte-swap на типичной x86_64 / aarch64 платформе.
- **Application keys** — **opaque bytes** на уровне wire format. Sync subsystem не интерпретирует содержимое ключей; ordering и deduplication отдаются на стороне `KeyValueTable`/`KeyMultiValueTable` интерфейса.

Эти решения считаются **locked** до появления отдельного `DESIGN.md` update-а; пересмотр требует явного bump-а версии wire format.

#### 1.5.6 Lifecycle state machine (по `SyncWorker.hpp`)

`SyncWorker` реализует явный state machine для фонового pull/apply:

```
Stopped -> Starting -> Idle -> Pulling -> Applying -> Idle
                              -> Backoff -> Idle
                              -> Stopping -> Stopped
                              -> Failed
```

Переходы:

- `Stopped → Starting` — worker started и готовит background loop.
- `Starting → Idle` — background loop вошёл в режим ожидания между sync rounds (`idle_interval`).
- `Idle → Pulling` — начат pull batch.
- `Pulling → Applying` — batch received, начинается apply.
- `Applying → Idle` — apply finished без ошибок (Applied/Skipped).
- `Pulling` / `Applying` → `Backoff` — pull/apply round завершился ошибкой или конфликтом, который background loop будет ретраить.
- `Backoff → Idle` — backoff timer истёк, повторная попытка; задержка растёт от `initial_backoff` до `max_backoff`.
- Любое состояние → `Stopping` — user сигнал (`stop()` / shutdown).
- `Stopping → Stopped` — текущая операция отменена через `CancellationToken` (см. §1.5.3).
- `* → Failed` — unexpected exception вышел из основного worker loop.

В `SyncWorkerOptions` нет `max_consecutive_failures`: background worker продолжает retry loop с bounded backoff. Foreground `run_once()` имеет другую семантику: обычный неуспешный round возвращает result с `ok=false` и переводит worker в `Failed`, потому что одноразовый вызов не имеет фонового retry loop.

#### 1.5.7 Conflict reasons (по `SyncEngine.hpp`)

Возвращаемое значение `ApplyResult`:

| Значение | Семантика |
|---|---|
| `Applied` | Запись применена успешно |
| `Skipped` | Batch redundant: `seq <= last contiguous applied seq` в `_mdbxc_applied`, либо batch пришёл от local node |
| `Conflict` | Запись отклонена — см. `ApplyConflictReason` |

`ApplyConflictReason` enum (ориентировочный набор по `SyncEngine.hpp`):

| Reason | Когда возникает |
|---|---|
| `SequenceGap` | Полученный `seq` не равен `last_applied_seq + 1`; caller должен re-pull недостающие batches |
| `InconsistentBatchDbiFlags` | Внутри одного batch-а DBI flags противоречат друг другу (mixed read-only / read-write в одной txn) |
| `ExistingDbiFlagsMismatch` | DBI на локальном узле имеет flags, несовместимые с incoming batch (например, `MDBX_DUPSORT` mismatch) |

В background worker такие round failures приводят к `Backoff` и новой попытке. Переход в `Failed` не привязан к счётчику recoverable failures; он означает unexpected exception в основном worker loop или неуспешный foreground `run_once()`.

#### 1.5.8 Merged PRs since local checkout

Upstream PR-ы, появившиеся после submodule-pointer-а `e9e9f2f` и затрагивающие sync subsystem (или напрямую предшествующие sync-work):

| GitHub PR # | Направление | Связь с sync v0.1 |
|---|---|---|
| #86 | … | начальная sync-инфраструктура (точная сводка требует верификации при adoption) |
| #87–#103 | … | последующие sync-коммиты (детали per-PR требуют верификации при adoption) |
| #104 | Transport DTO codec | `TransportMessageCodec`, отдельный envelope magic `MDBXCPRT` |
| #105 | HTTP transport adapter seam | Framework-neutral HTTP-shaped adapter seam поверх `ISyncPeer` |

Расхождения с roadmap-label «PR #N» в `guides/memory-stacks-roadmap.md` §14 / open issues не предполагаются; при синтаксической коллизии (например, внутренний roadmap-link «PR #95») приоритет — фактический upstream PR #95, и в этом ТЗ ссылка даётся явно: «GitHub PR #95» или «roadmap-label PR #95».

Точный per-PR breakdown отложен до формальной adoption (§11.7); в этом разделе фиксируется диапазон для accounting-а.

#### 1.5.9 Transport status after PR #104–#105

- **Direct peer** — `DirectSyncPeer` остаётся единственным полностью in-process peer.
- **HTTP seam** — `HttpSyncPeer`, `IHttpSyncClient`, `HttpSyncServer` и `HttpSyncRoutes` задают route/content-type/body/status mapping поверх `TransportMessageCodec`, но не открывают sockets и не зависят от конкретного HTTP framework.
- **WebSocket seam** — `WebSocketSyncPeer`, `IWebSocketSyncChannel` и `WebSocketSyncServer` задают binary-message request/response contract поверх `TransportMessageCodec`, но не владеют sessions и не зависят от WebSocket framework.
- **Optional examples** — socket-backed HTTP/WebSocket examples существуют как examples, но production-grade concrete socket transport библиотека не навязывает.
- **Middleware** — transport middleware покрывает allow-list policies, fixed-budget rate limiting, HTTP request context, bearer token / remote address policies, WebSocket session identity и retry status classification.

Следствие для `agent-memory-cpp`: HTTP/WebSocket adapter seams больше не являются upstream blocker-ом сами по себе. Adoption всё равно откладывается из-за coverage gap по `KeyMultiValueTable` / `AnyValueTable` и отсутствия multi-host scope-routing requirement для M0/M1/M2 (§11.7).

#### 1.5.10 System DBIs и `max_dbs` budget

Upstream sync subsystem вводит 5 системных DBIs:

| System DBI | Назначение |
|---|---|
| `_mdbxc_meta` | Sync metadata: `db_uuid`, local `node_id`, `schema_version`, `local_seq`, `created_at_ms` |
| `_mdbxc_changelog` | Append-only changelog keyed by `(origin_node_id, seq)`; `seq` в ключе хранится BE для range scans |
| `_mdbxc_origins` | Accelerator index: `origin_node_id → max known changelog seq` для multi-origin pull |
| `_mdbxc_applied` | Last contiguous applied `seq` per origin; primary replay/skip state |
| `_mdbxc_identity_index` | Declared identity map для opaque app keys ↔ storage identity; write path deferred в v0.1 |

Эти 5 DBIs разделяют тот же `Config::max_dbs` budget с таблицами секции 5.5.
На момент написания ТЗ sync subsystem не активирован и не открывается
M0/M1/M2-профилями, но при любом opt-in adoption эти DBIs обязаны входить в
расчёт §5.5.1.

> **Note**: raw-code compression и storage budget — разные вещи. Размер wire-format payload-ов зависит от compression-а (LZ4 / ZSTD), и raw source code footprint (см. §1.5.3) не равен полному end-to-end storage footprint при включённом sync. Полная cost-модель откладывается до формальной adoption.

### 1.6. Текущее состояние

Снимок того, что уже существует в upstream `mdbx-containers` на момент расширения. Документ описывает, какие классы уже есть, а какие добавляются или расширяются.

### Что уже есть в upstream

Базовые таблицы (см. `external/mdbx-containers/include/mdbx_containers/`):

- `KeyValueTable<K, V, Options>` — map-like storage, single payload per key.
- `KeyMultiValueTable<K, V, Options>` — DUPSORT, multi-payload per key.
- `KeyTable<K, Options>` — set-like, ключи без payload.
- `ValueTable<V>` — одиночный value по фиксированному имени.
- `SequenceTable<V>` — auto-increment uint64 ID generator.
- `AnyValueTable<K, Options>` — heterogeneous typed values с runtime type info.

Инфраструктура (см. `external/mdbx-containers/common/`):

- `Connection` — RAII handle на MDBX env, per-thread transaction model.
- `Config` — env parameters (`max_dbs`, `max_dupsort_value_size`, path, flags).
- `Transaction` — RAII обёртка над `MDBX_txn`, базовые `get`/`put`/`commit`/`abort`.
- `detail::utils.hpp` — `to_bytes`/`from_bytes` хелперы, `popcount64` fallback.

### Что расширяется и что добавляется

- Расширения существующих классов — секция 4 (надстройки над `KeyValueTable`, `KeyMultiValueTable`, `KeyTable`, `ValueTable`, `SequenceTable`, `AnyValueTable`, `Connection`, `Config`).
- DBI inventory — секции 5.1–5.4 фиксируют historical/profile-specific
  tables from older roadmaps and existing adapters. Они не являются
  требованиями на создание таблиц и не добавляются к canonical manifest без
  явного `MemoryProfileSpec` delta.
- Новые классы — секция 3 (ReverseIndexTable, RangeIndexTable, TypeDiscriminatedTable, CompositeKey, MultiTableWriter, пагинация, Connection extensions).
- Новые таблицы — секция 5.5 (Memory-stack layer для компонентной архитектуры `Envelope + Components + Projections`).

Иными словами, секции 3 и 5.5 — новая generic storage surface и canonical
memory-stack manifest; секции 5.1–5.4 — inventory для reconciliation, а не
параллельная physical schema.

## 2. Принципы

1. **Header-only, C++11 baseline с C++17 guarded features.** Новые фичи используют те же guards, что и существующий код (см. `external/mdbx-containers/include/mdbx_containers/detail/utils.hpp:12-16`).
2. **ABI compatibility.** Существующие сигнатуры публичных методов не меняются. Новые методы добавляются без переупорядочивания vtable или изменения layout.
3. **Death-of-the-author** (`external/mdbx-containers/PHILOSOPHY.md`). Библиотека оценивается по техническим качествам; `include/` остаётся первичным source of truth.
4. **Per-thread transaction model.** Каждый поток владеет не более чем одной активной транзакцией (см. `common/Connection.hpp`). Multi-table write обеспечивается одним `Transaction` объектом, разделяемым между таблицами, а не координацией отдельных транзакций.
5. **DRY через композицию.** Не дублировать логику existing классов. Новые классы строятся поверх `KeyValueTable`, `KeyMultiValueTable`, `AnyValueTable`.
6. **Naming conventions.** PascalCase для классов и публичных методов, `m_` prefix для private полей, snake_case для free functions в `detail::` namespace.
7. **Include guards** — `MDBX_CONTAINERS_HEADER_<PATH>_<FILE>_<EXT>_INCLUDED` (см. `external/mdbx-containers/AGENTS.md`).
8. **Doxygen комментарии** на английском для всех публичных API.

## 3. Новые классы

### 3.1 Расширения `KeyValueTable` (без нового файла)

Добавить в `external/mdbx-containers/include/mdbx_containers/KeyValueTable.hpp`:

```cpp
// Batch update
template <typename Fn>
std::size_t update_many(const std::vector<std::pair<K, Fn>>& updates,
                        const Transaction& txn);

// Merge from another container (same K, V types)
void merge(const KeyValueTable& other, const Transaction& txn);

// Consistent snapshot
template <typename ContainerT = std::vector<std::pair<K, V>>>
ContainerT snapshot(const Transaction& txn) const;

// Force reindex (drop and rebuild from caller-provided source)
template <typename Source>
void reindex(Source&& source, const Transaction& txn,
             std::function<void(std::size_t, std::size_t)> progress = nullptr);
```

Используют MDBX-флаги: те же, что у `KeyValueTable`. Thread-safety: per-thread transaction. Backward compat: только добавление методов. Пример:

```cpp
tbl.update_many({{"k1", [](V& v){ v.x = 1; }}, {"k2", [](V& v){ v.y = 2; }}}, txn);
```

### 3.2 `ReverseIndexTable<InvertedKey, PrimaryKey>`

**Файл:** `external/mdbx-containers/include/mdbx_containers/ReverseIndexTable.hpp`.

**Назначение:** secondary index pattern с consistency через composable transaction.

```cpp
struct ReverseIndexCursor {
    std::vector<std::uint8_t> encoded_key;
    std::vector<std::uint8_t> encoded_value;
};

template <class... Parts>
struct CompositePrefix {
    std::tuple<Parts...> parts;
};

template <class InvertedKey, class PrimaryKey>
struct ReverseIndexPage {
    std::vector<std::pair<InvertedKey, PrimaryKey>> items;
    std::optional<ReverseIndexCursor> next;
};

class ReverseIndexTable<InvertedKey, PrimaryKey> {
public:
    explicit ReverseIndexTable(std::shared_ptr<Connection> conn,
                               const std::string& name = "reverse_index",
                               const TableOptions& opts = {});

    void add(const InvertedKey& inv, const PrimaryKey& primary, const Transaction& txn);
    void remove(const InvertedKey& inv, const PrimaryKey& primary, const Transaction& txn);
    std::vector<PrimaryKey> find(const InvertedKey& inv, const Transaction& txn) const;
    std::optional<PrimaryKey> find_one(const InvertedKey& inv, const Transaction& txn) const;
    std::size_t count(const InvertedKey& inv, const Transaction& txn) const;

    std::vector<std::pair<InvertedKey, std::vector<PrimaryKey>>>
    find_by_prefix(const InvertedKey& prefix, const Transaction& txn) const;

    ReverseIndexPage<InvertedKey, PrimaryKey>
    find_page(const InvertedKey& inv,
              const std::optional<ReverseIndexCursor>& after,
              std::size_t limit,
              const Transaction& txn) const;

    ReverseIndexPage<InvertedKey, PrimaryKey>
    prefix_page(const InvertedKey& prefix,
                const std::optional<ReverseIndexCursor>& after,
                std::size_t limit,
                const Transaction& txn) const;

    template <class... PrefixParts>
    ReverseIndexPage<InvertedKey, PrimaryKey>
    prefix_page(const CompositePrefix<PrefixParts...>& prefix,
                const std::optional<ReverseIndexCursor>& after,
                std::size_t limit,
                const Transaction& txn) const;

    std::size_t remove_all_for(const PrimaryKey& primary, const Transaction& txn);

    std::vector<std::pair<InvertedKey, std::vector<PrimaryKey>>>
    range(const InvertedKey& from, const InvertedKey& to, const Transaction& txn) const;

    ReverseIndexPage<InvertedKey, PrimaryKey>
    range_page(const InvertedKey& from, const InvertedKey& to,
               const std::optional<ReverseIndexCursor>& after,
               std::size_t limit,
               const Transaction& txn) const;
};
```

Внутри: `KeyMultiValueTable<InvertedKey, PrimaryKey>` с флагом `MDBX_DUPSORT`.
Удаление последнего reference для `InvertedKey` стирает ключ целиком.
`ReverseIndexTable` является однонаправленным (`InvertedKey -> PrimaryKey[]`);
incoming/outgoing semantics появляются только у caller-owned paired indexes
или future `BidirectionalRelationIndex`. `remove_all_for(primary)` допустим как
maintenance/cleanup helper и может быть O(total postings), если caller не
поддерживает отдельную reverse orientation.

Page API продолжает DUPSORT order через exclusive cursor, который содержит и
encoded key, и encoded duplicate value. Key-only cursor запрещён: при большом
posting-list под одним `InvertedKey` он не может продолжить чтение внутри
duplicate values без повторов или пропусков. `limit > 0` обязателен; реализация
должна применять documented maximum.

MDBX flags: `MDBX_DUPSORT`. Thread-safety: per-thread transaction. Backward compat: новый класс.

Use cases: `token -> chunk_id` and `inverted_token_to_unit` candidate lookup,
`(metadata_key, metadata_value) -> UnitId` pre-filter, graph outgoing/incoming
reverse orientations. Canonical `field_to_postings` is a `KeyValueTable` by
full posting identity (§5.5), and temporal indexes use `RangeIndexTable`.

Scope-aware варианты (ADR-012 в `guides/memory-stacks-roadmap.md`): `InvertedKey` расширяется префиксом `ScopeId`, чтобы secondary indexes были multi-tenant. Примеры из DBI-схемы roadmap-а:

- `(scope_id, speaker_id) -> UnitId` — `speaker_to_units` (capability `SpeakerAttribution`).
- `(scope_id, session_id) -> UnitId` — `session_to_units` (capability `SpeakerAttribution`).
- `(scope_id, metadata_key, metadata_value) -> UnitId` — `metadata_filters` (всегда открыт, lightweight pre-filter).
- `(scope_id, token_id, projection_kind, field_id) -> UnitId` — `inverted_token_to_unit` candidate index.

`field_to_postings` is intentionally absent from `ReverseIndexTable` examples:
it is a KV table keyed by full posting identity in §5.5.

Реализация: `InvertedKey = CompositeKey<ScopeId, ...>` либо `ScopeId`-prefixed key struct с `to_bytes()`/`from_bytes()`. Класс `ReverseIndexTable` остаётся generic; scope-awareness обеспечивается композицией ключей на уровне вызывающего кода.

```cpp
ReverseIndexTable<std::string, ChunkId> rev(conn, "token_to_chunk");
rev.add("algorithm", chunk_a, txn);
auto chunks = rev.find("algorithm", txn);
```

### 3.3 `RangeIndexTable<SortableKey, Payload>`

**Файл:** `external/mdbx-containers/include/mdbx_containers/RangeIndexTable.hpp`.

**Назначение:** efficient range queries с inclusive/exclusive bounds и cursor-based pagination.

```cpp
struct RangeCursor {
    std::vector<std::uint8_t> encoded_key;
    std::vector<std::uint8_t> encoded_payload;
};

template <class SortableKey, class Payload>
struct RangePage {
    std::vector<std::pair<SortableKey, Payload>> items;
    std::optional<RangeCursor> next;
};

class RangeIndexTable<SortableKey, Payload> {
public:
    void add(const SortableKey& key, const Payload& payload, const Transaction& txn);
    bool remove(const SortableKey& key, const Payload& payload, const Transaction& txn);
    std::size_t erase_key(const SortableKey& key, const Transaction& txn);
    std::optional<Payload> find(const SortableKey& key, const Transaction& txn) const;

    std::vector<std::pair<SortableKey, Payload>>
    range(const SortableKey& from, const SortableKey& to,
          bool exclusive_left = false, bool exclusive_right = false,
          const Transaction& txn) const;

    std::vector<std::pair<SortableKey, Payload>>
    range_reverse(const SortableKey& from, const SortableKey& to,
                  std::size_t limit,
                  const Transaction& txn) const;

    std::vector<std::pair<SortableKey, Payload>>
    range_paginated(const SortableKey& from, const SortableKey& to,
                    std::size_t offset, std::size_t limit,
                    const Transaction& txn) const;

    RangePage<SortableKey, Payload>
    range_page(const SortableKey& from, const SortableKey& to,
               const std::optional<RangeCursor>& after,
               std::size_t limit,
               const Transaction& txn) const;

    std::size_t range_count(const SortableKey& from, const SortableKey& to,
                            const Transaction& txn) const;

    std::optional<std::pair<SortableKey, Payload>>
    lower_bound(const SortableKey& key, const Transaction& txn) const;

    std::optional<std::pair<SortableKey, Payload>>
    upper_bound(const SortableKey& key, const Transaction& txn) const;

    std::optional<std::pair<SortableKey, Payload>> first(const Transaction& txn) const;
    std::optional<std::pair<SortableKey, Payload>> last(const Transaction& txn) const;
};
```

Внутри: `KeyValueTable<SortableKey, Payload>` (single payload per key) или `KeyMultiValueTable` (multi-payload). `SortableKey` ограничен `uint32_t`/`uint64_t`/trivially copyable struct с `to_bytes`/`from_bytes`.

`range_paginated(..., offset, limit, ...)` допустим только для read-only
pagination over stable snapshots. Mutating page loops must either use
`range_page` with exclusive cursor or repeatedly read the first bounded page of
the current range after each mutation.

Use cases: `temporal_event_index` (timestamp range), `binary_bucket_index` (short_key range + neighbor expansion), `lexical_postings` (token_id range).

### 3.4 `TypeDiscriminatedTable<EnumTag, Key, ValueVariant>`

**Файл:** `external/mdbx-containers/include/mdbx_containers/TypeDiscriminatedTable.hpp`.

**Назначение:** одна таблица для разных value types с runtime type tag.

```cpp
template <typename EnumTag, typename Key, typename... ValueTypes>
class TypeDiscriminatedTable {
public:
    template <typename T>
    void add(EnumTag tag, const Key& key, const T& value, const Transaction& txn);

    template <typename T>
    std::optional<T> find(EnumTag tag, const Key& key, const Transaction& txn) const;

    template <typename T>
    bool update(EnumTag tag, const Key& key,
                std::function<void(T&)> mutator,
                bool create_if_missing, const Transaction& txn);

    bool remove(EnumTag tag, const Key& key, const Transaction& txn);
    std::vector<Key> list_keys_by_tag(EnumTag tag, const Transaction& txn) const;
    std::size_t count_by_tag(EnumTag tag, const Transaction& txn) const;

    void set_type_tag_check(bool enabled);
};
```

Внутри: `AnyValueTable<Key>` (см. `external/mdbx-containers/include/mdbx_containers/AnyValueTable.hpp`) с type-tag prefix. Опциональная проверка через `set_type_tag_check(true)`.

Use cases:

- `unit_components` (Layer B, см. ADR-001 в `guides/memory-stacks-roadmap.md`): `ComponentKind` tag ∈ {`UsageStats`, `Speaker`, `Temporal`, `EmbeddingMeta`, `CompactionMeta`}, ключ — `UnitId`, value — `ValueVariant<UsageStatsComponent, SpeakerComponent, TemporalComponent, EmbeddingMetaComponent, CompactionMetaComponent>`. Operational компоненты, читаемые лениво из retrieval layer; tag-prefix позволяет держать все компоненты в одной DBI с минимальным I/O envelope-а.
- per-kind payload-компоненты (`QAPayload`, `FactPayload`, `ConversationEpisodePayload`, `CompiledArticlePayload`, `ChunkPayload`) — при необходимости выносятся в отдельные DBI; canonical names listed in §5.5.

### 3.5 `CompositeKey<Parts...>` и хелперы

**Файл:** `external/mdbx-containers/include/mdbx_containers/CompositeKey.hpp`.

**Назначение:** typed composite key без `std::pair<K1, K2>` everywhere.

```cpp
template <typename... Parts>
struct CompositeKey {
    std::tuple<Parts...> parts;
    // operators: ==, !=, <, hash (если C++17 std::hash доступен)
    // to_bytes() / from_bytes() требуются от каждого Parts
};

template <typename... Parts>
CompositeKey<Parts...> make_composite_key(const Parts&... parts);

// В detail/utils.hpp:
template <typename... Parts>
std::vector<std::uint8_t>
composite_key_to_bytes(const Parts&... parts);  // trivially copyable only

template <typename... Parts>
CompositeKey<Parts...> composite_key_from_bytes(const std::uint8_t* data,
                                                std::size_t size);
```

Требование: все `Parts` — trivially copyable с documented byte order и padding
policy. Минимальный acceptance — поддержка 2..5 parts, потому что canonical
manifest §5.5 использует 2-, 3-, 4- и 5-part keys. Если C++11 build не может
использовать fold expressions, реализация делает private C++11-compatible
helper без изменения публичной формы `CompositeKey<Parts...>`.

### 3.6 Расширения `paginated_range`

Добавить в `KeyValueTable`/`KeyMultiValueTable`:

```cpp
template <typename Callback>
void paginated_range(const K& from, const K& to, std::size_t page_size,
                     Callback&& cb, const Transaction& txn) const;

struct PaginationStats { std::size_t pages_returned; std::size_t items_per_page; };
PaginationStats paginated_range_stats(const K& from, const K& to,
                                      std::size_t page_size, const Transaction& txn) const;
```

Существующий `range_reverse(from, to, [limit,])` остаётся; добавляется forward direction.

### 3.7 `MultiTableWriter` (RAII helper)

**Файл:** `external/mdbx-containers/include/mdbx_containers/MultiTableWriter.hpp`.

**Назначение:** гарантировать атомарность multi-table write через одну `Transaction`.

```cpp
class MultiTableWriter {
public:
    explicit MultiTableWriter(Connection& conn);
    ~MultiTableWriter();  // rollback если не commit

    Transaction& txn();  // вернуть текущую транзакцию

    template <typename Op>
    void write(Op&& op);  // op получает Transaction&

    void commit();   // throws MdbxException on conflict
    void rollback();
};

// Connection extension:
template <typename Op>
void Connection::multi_write(Op&& op);  // RAII: commit/rollback
```

Документировать best practice: обновление primary + secondary indexes в одной транзакции (например, добавление chunk в `agent_memory_chunks` + соответствующих postings в `lexical_postings`).

Ключевой use case под Layer 1 архитектуру из `guides/memory-stacks-roadmap.md`
(Layer B + C, ADR-001): `MemoryStack::write_unit` обязан записать атомарно
через один `MultiTableWriter`:

1. Build `CanonicalIdentityInput` from authoritative request fields and stores,
   compute `ContentHashResult` through the single
   `compute_content_hash(kind, input)` pipeline from
   `knowledge-units-roadmap.md` §4, write `content_hash` and
   `content_hash_recipe_version` into the envelope, then compute
   `KnowledgeUnitKey = (kind, scope_id, content_hash)` and read
   `content_key_to_unit_id`. Caller-supplied hash values are diagnostics only
   unless they match the recomputed result. The identity input read,
   `content_key_to_unit_id` lookup, `KnowledgeUnitId` allocation and
   no-overwrite mapping insert MUST happen inside the same MDBX write
   transaction. MDBX-backed `ResourceBodyStore` reads receive that transaction;
   external body stores must provide an immutable digest/version token verified
   before commit.
2. `KnowledgeUnitKey` immutable for an existing `UnitId`. Если caller передаёт
   existing `UnitId`, storage сначала читает старый envelope из
   `knowledge_units` и строит `old_key` from
   `(old_envelope.kind, old_envelope.scope_id, old_envelope.content_hash)`;
   `old_key != new_key` rejected before writes. It also compares
   `content_hash_recipe_version`: recipe changes require a migration preserving
   the old stored hash, or a new `UnitId`. No old payload read is required for
   the immutability check. Смена `kind`, `scope_id` или content hash требует
   нового `UnitId` плюс explicit supersede/merge relationship downstream.
3. Если key уже указывает на тот же `UnitId`, операция становится idempotent
   update для mutable fields этого unit. Если key указывает на другой `UnitId`,
   write конфликтует: default policy возвращает duplicate/conflict result
   without writes; explicit merge/supersede policy должна быть выбрана
   downstream до входа в storage transaction.
4. Envelope в `knowledge_units` (DBI:
   `KeyValueTable<UnitId, KnowledgeUnitEnvelope>`).
5. Mapping `KnowledgeUnitKey -> UnitId` в `content_key_to_unit_id`, using
   `insert_if_absent` / CAS semantics. If another writer inserts the same key
   first, the operation returns `UpsertResult::Existed(existing_id)` and MUST
   not create an orphan `knowledge_units` envelope.
6. `knowledge_units_by_kind` update: добавить `(scope_id, kind) -> UnitId`.
   Поскольку `KnowledgeUnitKey` immutable, смена `(scope_id, kind)` внутри того
   же `UnitId` запрещена; removal старой by-kind записи выполняется только при
   erase/tombstone или migration, используя old envelope.
7. Ровно один per-kind payload, соответствующий `KnowledgeUnitKind`: write в
   `qa_payloads` / `fact_payloads` / `conversation_episode_payloads` /
   `compiled_article_payloads` / `chunk_payloads`. Stale payload прежнего kind
   не допускается в normal update path, потому что kind immutable; migration
   cleanup должен использовать old envelope.
8. Tag-prefixed operational components в `unit_components` (DBI:
   `TypeDiscriminatedTable<ComponentKind, UnitId, ValueVariant<UsageStats |
   Speaker | Temporal | EmbeddingMeta | CompactionMeta>>`).
9. Projections в `unit_projections` (DBI:
   `KeyValueTable<(scope_id, UnitId, ProjectionKind, revision),
   SearchProjection>`).
10. Embedding metadata + vectors (`embedding_meta`, `embedding_vectors`) only
    when the selected profile marks dense writes as synchronous; heavy
    recompute/HNSW/backfill jobs may be delegated to AsyncIndexer with a durable
    revision-guarded `IndexUpdateJob(unit_id, unit_revision, projection_kind,
    index_kind)`.
11. Все scope-aware secondary/range indexes, относящиеся к данной write:
    `inverted_token_to_unit` (candidate index по каждой projection),
    `field_to_postings` (KV posting stats by full posting identity),
    `temporal_event_index`/`temporal_unit_index`,
    `speaker_to_units`/`session_to_units`, `usage_stats_index`,
    `graph_edges_by_src`/`graph_edges_by_dst`, `metadata_filters`.
12. Optional/profile-gated writes, если профиль их включает:
    `source_refs.replace_for_unit(unit_id, refs)` for `enable_full_source_refs`,
    runtime queue writes, response cache invalidation или другие
    profile-specific indexes. Эти DBI обязаны быть перечислены в canonical
    §5.5 manifest или учтены как profile delta в §5.5.1.

Default M0/M1 consistency mode is synchronous for primary data and critical
retrieval indexes: readers never observe a committed unit without its
content-key/by-kind identity, selected per-kind payload, projections,
metadata filters and active lexical candidate/stat indexes for the same
revision. Async eventual mode is allowed only for indexes explicitly marked
`eventually_consistent=true` by their owning roadmap (for example heavy dense
embedding recompute, HNSW rebuild, bulk lexical backfill). In async mode
`write_unit` commits a durable `IndexUpdateJob(unit_id, unit_revision,
projection_kind, index_kind)`; workers must be idempotent, skip stale revisions,
replay after crash, and tolerate delete/update before older jobs run.

`ContentHash` recipe is versioned and kind-specific. The recipe input MUST be
canonical bytes using the tagged length-prefixed wire grammar from
`knowledge-units-roadmap.md` §4:

- `QAPair`: normalized question/answer payload fields and declared identity
  metadata.
- `Fact`: canonical subject/predicate/object/time identity.
- `ConversationEpisode` / `CompiledArticle` / `Note`: canonical payload text
  or a stable `ResourceBody` digest if the body is stored externally.
- `Chunk`: `ResourceBody` digest plus span/offset identity and normalized chunk
  text digest; transient file path or importer-local location is not identity.

Envelope previews, `SourceRefSummary` display excerpts, usage stats,
projections, embeddings and runtime counters are excluded unless a future recipe
version explicitly lists them.

Golden vectors for every `KnowledgeUnitKind`, empty vs absent optional fields,
Unicode composed vs decomposed strings, boundary integer/timestamp values,
legacy recipe v0 and C++11/C++17 equality are required before implementing this
hash as a migration gate.

For `Chunk`, `resource_body_digest`, span/offset identity and normalized
chunk-text digest are required inputs to `write_unit`, or storage must load them
from authoritative `ResourceBodyStore` / chunk-normalization records before
computing the hash. `ChunkPayload` alone is not sufficient identity material if
it only contains offsets, headings, code blocks or symbol hints.

При исключении в любом шаге `MultiTableWriter` откатывает все synchronous
canonical groups и выбранные synchronous profile-delta writes; частично
записанный unit не наблюдаем.
`commit()` бросает `MdbxException` на conflict; retry policy остаётся на
стороне `MemoryStack::write_unit` (см. `WritePolicy` в
`guides/memory-stacks-roadmap.md`, секция 6.2).

### 3.8 Расширения `Connection` и `Config`

`common/Config.hpp`:

```cpp
bool enable_metrics = false;  // optional metrics integration
std::function<void(const std::string& dbi_name)> table_creation_callback = nullptr;
```

`max_dupsort_value_size` уже есть; возможно, увеличить default с 512 до 1024 для больших payloads (обосновать benchmark-ом).

### 3.9 Утилиты в `detail/utils.hpp`

Добавить в `external/mdbx-containers/include/mdbx_containers/detail/utils.hpp`:

```cpp
inline std::uint64_t sortable_key_from_uint64(std::uint64_t v);   // identity
inline std::uint64_t sortable_key_from_int64(std::int64_t v);    // sign-bit flip + offset

inline int hamming_distance_uint64(std::uint64_t a, std::uint64_t b);  // popcount XOR
inline std::vector<std::uint64_t> hamming_neighbors_uint64(
    std::uint64_t key, int radius);  // для binary bucket expansion

inline std::string to_hex_string(const std::uint8_t* data, std::size_t size);
inline std::vector<std::uint8_t> from_hex_string(const std::string& hex);
```

### 3.10 Sharding rationale (negative result)

В рамках этого ТЗ **не добавляется** MDBX-level sharding API: ни `ShardMap`, ни hash-based разбиение DBI, ни автоматический routing записей между несколькими MDBX env. Это осознанный negative result, а не пропущенная часть дизайна.

Причины:

1. **Текущий target — single-env / single-writer.** M0/M1/M2 ориентированы на embedded deployment с одним MDBX env и атомарной записью через один `Transaction` / `MultiTableWriter`. Sharding сразу меняет transaction boundary: запись envelope + components + projections + secondary indexes перестаёт быть атомарной, если части unit-а попадают в разные env.
2. **`scope_id` уже является логическим partition key.** Scope-aware secondary indexes начинаются с `scope_id` (см. §3.2 и `guides/memory-stacks-roadmap.md` ADR-012). Для текущих профилей этого достаточно, чтобы изолировать tenant / project / agent namespace без физического дробления storage.
3. **DBI budget не должен умножаться на shards.** Секция §5.5 уже планирует десятки DBI в одном env, а §1.5.10 добавляет потенциальные 5 sync DBI при opt-in adoption. Физический shard-per-scope или shard-per-capability быстро превращает `max_dbs` budget из локального ограничения в distributed capacity model; это отдельный дизайн.
4. **CAP / consistency trade-offs не возникают бесплатно.** Как только shards живут в разных env / process / hosts, нужны явные ответы про consistency, availability under partition, resharding, tombstones, read-your-writes и cross-shard query merge. Встраивать только имена shard-ов без этих контрактов опаснее, чем не встраивать sharding вообще.

Важно не смешивать sharding с DUPSORT:

- `MDBX_DUPSORT` / `KeyMultiValueTable` — это **локальный physical layout внутри одной DBI**, где один ключ имеет несколько отсортированных duplicate values. Он нужен для inverted indexes, graph edges и metadata filters.
- Sharding — это **распределение keyspace между несколькими физическими storage boundary**. Он меняет routing, failure domains, consistency и merge semantics.
- `ReverseIndexTable` поверх DUPSORT может уменьшать размер posting-list scan-а и улучшать locality, но не является shard-ом и не решает multi-host replication.

Если позже появится требование distributed scope routing (см. `guides/memory-stacks-roadmap.md` §13.2 / §13.3), sharding должен проектироваться как отдельный слой поверх `MemoryStack` и заново сверяться с sync adoption trigger-ами из §11.7. До этого момента предпочтительный путь масштабирования: composite keys с `scope_id` prefix, bounded secondary indexes, batched writes и, при необходимости, segmented postings на уровне lexical roadmap-а.

## 4. Расширения существующих классов

Для каждого класса добавить методы (без поломки ABI):

**`KeyValueTable<K, V, Options>`:** `update_many`, `merge`, `snapshot`, `reindex`, `try_update(key, Fn, default_value, txn)`, `add_many(container, txn)`, `erase_many(keys, txn) → size_t`, `bulk_erase_if(pred, txn) → size_t`, `diagnostics() → {size, key_size_avg, value_size_avg, key_size_p95, value_size_p95, fragmentation_pct}`.

**`KeyMultiValueTable<K, V, Options>`:** те же batch методы плюс `deduplicate(txn) → size_t`, `count_unique_keys(txn)`.

**`KeyTable<K, Options>`:** `insert_many(container, txn) → vector<bool>`, `erase_many(keys, txn) → size_t`.

**`ValueTable<V>`:** `set_many(container, txn)`, `merge_from(other, txn)`.

**`SequenceTable<V>`:** `reserve(begin, end) → vector<id>`, `append_if_absent(value) → optional<id>`.

**`AnyValueTable<K, Options>`:** `bulk_set_of_type<T>(container, txn)`, `list_keys_by_type<T>(txn)`.

Все additions только аддитивные. Сигнатуры существующих методов не меняются.

## 5. DBI inventory и canonical physical manifest

Секции §5.1-§5.4 являются source inventory из старых roadmap-документов и
существующих adapters. Они не являются одновременным physical manifest-ом для
новой компонентной архитектуры. Единственный canonical layout для
`Envelope + Components + SearchProjections` находится в §5.5; каждую DBI из
§5.1-§5.4 нужно явно сохранить, заменить или перевести в profile-specific
adapter перед реализацией.

### 5.1 Legacy lexical inventory (superseded by §5.5)

Эти DBI сохранены как migration/source inventory для старого lexical backend.
Canonical lexical DBI names and key shapes live in §5.5: postings,
dictionary and stats are all represented there.

```text
legacy_lexical_token_by_text   KeyValueTable<string, uint64_t>
legacy_lexical_token_by_id     KeyValueTable<uint64_t, string>
legacy_lexical_postings        KeyMultiValueTable<uint64_t, LexicalPosting>
legacy_lexical_chunk_stats     KeyValueTable<ChunkId, ChunkLexicalStats>
legacy_lexical_token_stats     KeyValueTable<TokenId, LexicalTokenStats>
legacy_lexical_field_postings  ReverseIndexTable<CompositeKey<TokenId, uint8_t>, LexicalPosting>
```

### 5.2 Optimization inventory (profile-specific, не canonical manifest)

Эти DBI относятся к optimization roadmap. `embedding_store` и `chunk_store`
являются legacy names; canonical component layout использует
`embedding_meta`, `embedding_vectors`, `chunk_payloads` и `unit_projections`
из §5.5.

```text
binary_bucket_index     RangeIndexTable<uint64_t_short_key, BucketPostingBlob>
embedding_store         KeyValueTable<ChunkId, vector<float>>
chunk_store             KeyValueTable<ChunkId, CompressedBlob>
```

### 5.3 Legacy knowledge-base inventory (superseded by §5.5)

Эта секция сохранена как migration/source inventory. Она не должна
реализовываться как параллельная схема. `knowledge_units
TypeDiscriminatedTable<KnowledgeUnitKind, ...>` заменён canonical
`knowledge_units KeyValueTable<UnitId, KnowledgeUnitEnvelope>` +
`unit_components` + per-kind payload DBIs из §5.5.

```text
legacy_polymorphic_units    TypeDiscriminatedTable<KnowledgeUnitKind, KnowledgeUnitId, ...>  // superseded; do not implement as `knowledge_units`
legacy_qa_knowledge                KeyValueTable<QAPairId, QAPairPayload>
legacy_qa_inverted                 ReverseIndexTable<token, QAPairId>
legacy_fact_store                  KeyValueTable<FactId, FactPayload>
legacy_fact_inverted               ReverseIndexTable<token, FactId>
legacy_event_store                 KeyValueTable<EventId, EventPayload>
legacy_temporal_event_index        LegacyRangeIndex<uint64_timestamp, EventPayload>
legacy_graph_nodes                 KeyValueTable<NodeId, NodePayload>
legacy_graph_edges_by_src          ReverseIndexTable<CompositeKey<src_node, edge_kind>, EdgePayload>
legacy_graph_edges_by_dst          ReverseIndexTable<dst_node, src_node>
legacy_resource_metadata_filters   ReverseIndexTable<CompositeKey<metadata_key, metadata_value>, ResourceId>
legacy_resource_kinds              TypeDiscriminatedTable<DerivedRecordKind, ResourceId, payload>
```

### 5.4 Existing infrastructure inventory (adapter-local)

Эти DBI принадлежат существующим document/resource adapters. Они могут
сосуществовать с §5.5 только в профилях, которые явно включают legacy
document-storage adapter; canonical memory-stack budget §5.5.1 считает их
отдельно от core manifest.

```text
agent_memory_documents           KeyValueTable<DocumentId, string>
agent_memory_chunks              KeyValueTable<ChunkId, string>
agent_memory_document_chunks     KeyValueTable<DocumentId, string>
agent_memory_resource_manifests  KeyValueTable<ResourceId, string>
```

### 5.5 Memory-stack layer (canonical physical manifest)

DBI для компонентной архитектуры `Envelope + Components + Projections`. Открываются по capability (см. capability matrix, `guides/memory-stacks-roadmap.md`, секция 9).

```text
// Layer A — envelope (hot path), всегда открыт
knowledge_units                       KeyValueTable<UnitId, KnowledgeUnitEnvelope>
content_key_to_unit_id                KeyValueTable<KnowledgeUnitKey, UnitId>           // dedupe/migration
knowledge_units_by_kind               ReverseIndexTable<CompositeKey<ScopeId, KnowledgeUnitKind>, UnitId>

// Layer B — operational components + separate per-kind payload DBIs
unit_components                       TypeDiscriminatedTable<ComponentKind, UnitId,
                                                            ValueVariant<UsageStats | Speaker | Temporal
                                                                       | EmbeddingMeta | CompactionMeta>>
qa_payloads                           KeyValueTable<UnitId, QAPayload>                   // capability QAPairs
fact_payloads                         KeyValueTable<UnitId, FactPayload>                 // capability TemporalFact
conversation_episode_payloads         KeyValueTable<UnitId, ConversationEpisodePayload> // capability ConversationMemory
compiled_article_payloads             KeyValueTable<UnitId, CompiledArticlePayload>     // capability CompiledArticles
chunk_payloads                        KeyValueTable<UnitId, ChunkPayload>                // для kind == Chunk
source_refs                           KeyValueTable<UnitId, std::vector<SourceRef>>      // capability FullSourceRefs

// Layer C — search projections (multi-version)
unit_projections                      KeyValueTable<CompositeKey<ScopeId, UnitId, ProjectionKind, uint64_revision>,
                                                            SearchProjection>
                                      // key содержит revision для хранения истории; активная projection
                                      // определяется максимальным revision для (scope, unit, kind).

// Embeddings — multi-projection, multi-model (ADR-007)
embedding_meta                        KeyValueTable<CompositeKey<ScopeId, UnitId, ProjectionKind, ModelId, Version>,
                                                            EmbeddingMeta>
embedding_vectors                     KeyValueTable<CompositeKey<ScopeId, ModelId, ProjectionKind, UnitId>,
                                                            vector_blob>
                                      // порядок ключа выбран для cluster-friendly access при ANN-free exact scan.

// Secondary indexes — scope-aware (ADR-012)
inverted_token_to_unit                ReverseIndexTable<CompositeKey<ScopeId, TokenId, ProjectionKind, FieldId>, UnitId>
field_to_postings                     KeyValueTable<CompositeKey<ScopeId, ProjectionKind, FieldId, TokenId, UnitId>, PostingStats>
lexical_token_by_text                 KeyValueTable<CompositeKey<ScopeId, NormalizedTokenText>, TokenId>
lexical_token_by_id                   KeyValueTable<CompositeKey<ScopeId, TokenId>, NormalizedTokenText>
lexical_chunk_stats                   KeyValueTable<CompositeKey<ScopeId, UnitId, ProjectionKind>, ChunkLexicalStats>
lexical_token_stats                   KeyValueTable<CompositeKey<ScopeId, ProjectionKind, TokenId>, LexicalTokenStats>
lexical_collection_stats              KeyValueTable<CompositeKey<ScopeId, ProjectionKind>, LexicalCollectionStats>
metadata_filters                      ReverseIndexTable<CompositeKey<ScopeId, MetadataKey, MetadataValue>, UnitId>
graph_edges_by_src                    ReverseIndexTable<CompositeKey<ScopeId, FromUnitId, EdgeKind>, RelationValue<ToUnitId, EdgePayload>>
graph_edges_by_dst                    ReverseIndexTable<CompositeKey<ScopeId, ToUnitId, EdgeKind>, RelationValue<FromUnitId, EdgePayload>>
temporal_event_index                  RangeIndexTable<CompositeKey<ScopeId, Timestamp, EventId>, EventPayload>
temporal_unit_index                   RangeIndexTable<CompositeKey<ScopeId, Timestamp, UnitId>, UnitId>
speaker_to_units                      ReverseIndexTable<CompositeKey<ScopeId, SpeakerId>, UnitId>
session_to_units                      ReverseIndexTable<CompositeKey<ScopeId, SessionId>, UnitId>
usage_stats_index                     ReverseIndexTable<CompositeKey<ScopeId, UnitId>, UsageStatsComponent>

// Schema metadata
schema_info                           KeyValueTable<string, SchemaInfo>                  // envelope_version, component_versions[], profile_signature
```

См. также [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) для дополнительных Layer-1 primitives under consideration (Patterns 3, 4, 6): coverage shadow graph (новый §5.7 — `coverage_units`, `coverage_files`, `coverage_regions`), atomic shared ID generator (extends §4 SequenceTable P3 → P1), team-shared graph artifact (offline snapshot format — proposed job, not yet in `compaction-roadmap.md`).

Сводная таблица DBI секции 5.5 (для быстрого чтения владельца PR и capability-зависимости):

| DBI имя | Открывается по умолчанию | Версия payload | Owner PR | Назначение |
|---|---|---|---|---|
| `knowledge_units` | да (Layer A envelope hot path) | `agent_memory.knowledge_unit.v1` | TBD | Primary envelope storage по `UnitId` |
| `content_key_to_unit_id` | да (Layer A dedupe/migration) | `agent_memory.content_key.v1` | TBD | Content-addressed lookup `KnowledgeUnitKey -> UnitId` |
| `knowledge_units_by_kind` | да (Layer A, DUPSORT) | `agent_memory.unit_by_kind.v1` | TBD | Scope-aware reverse index `(ScopeId, KnowledgeUnitKind) -> UnitId[]` |
| `unit_components` | да (Layer B operational) | `agent_memory.component.v1` | TBD | Tag-prefixed components (UsageStats, Speaker, Temporal, EmbeddingMeta, CompactionMeta) |
| `qa_payloads` | по capability `QAPairs` | `agent_memory.qa.v1` | TBD | Per-kind payload для `QAPair` units |
| `fact_payloads` | по capability `TemporalFact` | `agent_memory.fact.v1` | TBD | Per-kind payload для `Fact` units |
| `conversation_episode_payloads` | по capability `ConversationMemory` | `agent_memory.episode.v1` | TBD | Per-kind payload для `ConversationEpisode` units |
| `compiled_article_payloads` | по capability `CompiledArticles` | `agent_memory.compiled_article.v1` | TBD | Per-kind payload для `CompiledArticle` units |
| `chunk_payloads` | да (canonical always-open) | `agent_memory.chunk.v1` | TBD | Per-kind payload для `Chunk` units; required when writing `KnowledgeUnitKind::Chunk` |
| `source_refs` | по capability `FullSourceRefs` (M1) | `agent_memory.source_ref.v1` | TBD | Full `SourceRef` vector by `UnitId`; inline summaries remain in envelope |
| `unit_projections` | да (Layer C projections) | `agent_memory.projection.v1` | TBD | Multi-version `(scope, unit, ProjectionKind, revision)` projections |
| `embedding_meta` | по capability `DenseVectors` | `agent_memory.embedding_meta.v1` | TBD | Версионированная мета (model_id, version, dim, encoder_id) |
| `embedding_vectors` | по capability `DenseVectors` | `agent_memory.embedding_vector.v1` | TBD | Vector blob по `(scope, model_id, ProjectionKind, unit_id)` |
| `inverted_token_to_unit` | по capability `LexicalIndex` | `agent_memory.inv_token.v1` | TBD | Scope-aware reverse index `(scope, token_id, projection, field) -> UnitId` |
| `field_to_postings` | по capability `LexicalIndex` | `agent_memory.field_posting.v1` | TBD | Scope-aware posting stats by `(scope, projection, field, token, unit)` |
| `lexical_token_by_text` | по capability `LexicalIndex` | `agent_memory.lex_token_text.v1` | TBD | Token dictionary `(scope, normalized_text) -> TokenId` |
| `lexical_token_by_id` | по capability `LexicalIndex` | `agent_memory.lex_token_id.v1` | TBD | Token dictionary reverse lookup `(scope, token_id) -> normalized_text` |
| `lexical_chunk_stats` | по capability `LexicalIndex` | `agent_memory.lex_chunk_stats.v1` | TBD | BM25F chunk/unit stats by `(scope, unit, projection)` |
| `lexical_token_stats` | по capability `LexicalIndex` | `agent_memory.lex_token_stats.v1` | TBD | Token corpus stats by `(scope, projection, token)` |
| `lexical_collection_stats` | по capability `LexicalIndex` | `agent_memory.lex_collection_stats.v1` | TBD | Collection-level BM25F stats by `(scope, projection)` |
| `metadata_filters` | да (lightweight pre-filter) | `agent_memory.metadata_filter.v1` | TBD | Reverse index `(scope, metadata_key, metadata_value) -> UnitId` |
| `graph_edges_by_src` | по capability `GraphIndex` | `agent_memory.graph_edge.v1` | TBD | Scope-aware outgoing edges with value `RelationValue<ToUnitId, EdgePayload>` |
| `graph_edges_by_dst` | по capability `GraphIndex` | `agent_memory.graph_edge.v1` | TBD | Scope-aware incoming edges with value `RelationValue<FromUnitId, EdgePayload>` |
| `temporal_event_index` | по capability `TemporalIndex` | `agent_memory.event.v1` | TBD | Scope-aware range index `(scope, timestamp, event_id) -> EventPayload` |
| `temporal_unit_index` | по capability `TemporalIndex` | `agent_memory.unit_ts.v1` | TBD | Scope-aware range index `(scope, timestamp, unit_id) -> UnitId` |
| `speaker_to_units` | по capability `SpeakerAttribution` | `agent_memory.speaker.v1` | TBD | Reverse index `(scope, speaker_id) -> UnitId` |
| `session_to_units` | по capability `SpeakerAttribution` | `agent_memory.session.v1` | TBD | Reverse index `(scope, session_id) -> UnitId` |
| `usage_stats_index` | по capability `UsageTracking` | `agent_memory.usage_stats.v1` | TBD | Reverse index `(scope, unit_id) -> UsageStatsComponent` |
| `schema_info` | да (schema metadata) | `agent_memory.schema.v1` | TBD | Envelope_version, component_versions[], profile_signature |

Владелец каждой DBI будет зафиксирован в соответствующем PR; на этапе TZ — TBD. Версия payload соответствует общему контракту `agent_memory.<concept>.v1`, см. `guides/memory-stacks-roadmap.md` ADR-001 and §12 ownership rules.

Capability labels in this manifest map to `MemoryProfileSpec` selectors as
follows:

| Manifest label | Profile selector / capability |
|---|---|
| `LexicalIndex` | `enable_lexical_bm25f` / `MemoryCapability::LexicalBm25F` |
| `DenseVectors` | `enable_dense_vectors` / `MemoryCapability::DenseVectors` |
| `GraphIndex` | `enable_graph` / `MemoryCapability::GraphRelations` |
| `TemporalIndex` | `enable_temporal_validity` / `MemoryCapability::TemporalValidity` |
| `UsageTracking` | `enable_usage_stats` / `MemoryCapability::UsageStats` |
| `SpeakerAttribution` | `enable_speaker` / `MemoryCapability::SpeakerAttribution` |
| `QAPairs` | `enable_qa_payload` / `MemoryCapability::QAPairs` |
| `TemporalFact` | `enable_fact_payload` / `MemoryCapability::TemporalValidity` plus fact payload selector |
| `ConversationMemory` | `enable_conversation_episode` / `MemoryCapability::ConversationMemory` |
| `CompiledArticles` | `enable_compiled_article` / `MemoryCapability::CompiledArticles` |
| `FullSourceRefs` | `enable_full_source_refs` / `MemoryCapability::FullSourceRefs` |
| `ResponseCache` | `response_cache_storage != Disabled` / `MemoryCapability::ResponseCache`; DBI opens only when `response_cache_storage == Mdbx` |
| `PromptPrefixCache` | `enable_prompt_cache` / `MemoryCapability::PromptCache`; provider-side cache, no canonical DBI by default |

Замечания:

- `unit_projections` использует multi-version ключ `(scope_id, UnitId, ProjectionKind, revision)`. При write активной projection инкрементируется `revision`; старые revisions остаются до compaction purge (см. `guides/memory-stacks-roadmap.md`, open issue 17.4).
- `embedding_meta` хранит версионированную мета-информацию (model_id + version), чтобы CompactionWorker мог удалять versions старше N дней при отсутствии ссылок (см. roadmap, open issue 17.3).
- `embedding_vectors` упорядочен по `(scope_id, model_id, ProjectionKind, UnitId)` для cluster-friendly чтения при exact scan; для ANN-расширений порядок может быть пересмотрен в `guides/optimization-roadmap.md`.
- Все secondary/range indexes, которые должны обслуживать range-query или
  lookup внутри tenant/project/agent boundary, начинаются с `ScopeId`
  (ADR-012). Primary lookup by globally unique `UnitId` является явным
  исключением. Profile validation в `MemoryStack::open()` проверяет
  обязательность `scope_id` для каждого write (см. roadmap, секция 10).
- Derivation/evidence/drill-down semantics не получают отдельных DBI в
  `mdbx-containers`. Downstream `agent-memory-cpp` должен кодировать такие
  отношения через уже существующую пару `graph_edges_by_src` /
  `graph_edges_by_dst` с application-owned `EdgeKind` и opaque payload. Это
  сохраняет две физические ориентации relation index без скрытых DBI. Generic
  relation helper assumes unique `(scope, source, target, tag)`; multiple
  evidence records for the same tuple must be aggregated in `EdgePayload` or
  modeled downstream with an explicit domain relation id.
- Raw source identity принадлежит canonical `SourceRef` / `ResourceId`
  контракту `agent-memory-cpp` (см.
  [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) §3). Full
  `SourceRef` storage lives in `source_refs`; optional
  `source_refs_by_resource` reverse lookup is a +1 profile delta and must not
  be opened unless the selected profile explicitly includes that delta. Если
  позже потребуется artifact descriptor сверх `SourceRef`, он вводится как
  application payload поверх `KeyValueTable`, а не как публичный тип
  `mdbx_containers`.

Все таблицы используют префикс payload versioning (`agent_memory.knowledge_unit.v1`, `agent_memory.qa.v1`, и т.п.). `Config::max_dbs = 64` является текущим ceiling для одного MDBX env.

#### 5.5.1 DBI budget checkpoint

`Config::max_dbs = 64` является capacity ceiling, а не обещанием открыть 64
named DBIs в каждом профиле. Canonical baseline берётся из physical manifest
§5.5; таблица ниже затем добавляет явные profile, sync and migration deltas.
Legacy/profile-specific inventory из §5.1-§5.4 не считается автоматически;
каждая такая DBI должна попасть в конкретный `MemoryProfileSpec` manifest,
прежде чем её можно учитывать в budget.

| Bucket | Steady DBIs | Migration peak | Sync status | Notes |
|---|---:|---:|---|---|
| Canonical memory-stack DBIs from §5.5 | up to 29 profile-selected | 29 full inventory | mixed | Count every row in the §5.5 summary table exactly once when full canonical inventory is enabled. |
| Existing document/resource adapter DBIs from §5.4 | 0 by default, +4 if legacy adapter enabled | +4 | KV supported | Adapter-local; not part of canonical memory-stack layout. |
| Runtime queue profile delta | 0 by default, +5 for the shared persistent queue | +5 | mixed | `jobs_by_id`, `jobs_scheduled`, `jobs_ready`, `jobs_by_lease`, `jobs_by_status`; owned by `runtime-services-roadmap.md`. |
| Compaction handoff profile delta | 0 by default, +1 if compaction enabled | +1 | KV supported | `compaction_handoffs`; operational handoff, not queue ordering. |
| MDBX-backed resource body delta | 0 by default, +1 simple KV or +2 chunked | +2 | KV supported | `resource_bodies` or `resource_body_manifest` + `resource_body_chunks`; see §12.9. |
| SourceRef reverse lookup delta | 0 by default, +1 if reverse lookup is enabled | +1 | DUPSORT not supported by sync v0.1 | `source_refs_by_resource`; optional acceleration for `ResourceId -> UnitId[]`, not required for M1 full source refs. |
| Opt-in response cache delta | 0 by default, +1 if `response_cache_storage == Mdbx` | +1 | KV supported | `response_cache` keyed by `ResponseCacheStorageKey = (scope, provider, model, request_hash, suffix, schema_version)`; runtime-services-roadmap.md owns policy and safety defaults. |
| Optional sync system DBIs from §1.5.10 | 0 by default, +5 opt-in | +5 | opt-in only | Not used by M0/M1/M2 while §11.7 is DEFER. |
| Migration / dual-write reserve | 0 | +8 | application-owned | Reserved for transitional tables during profile migrations. |
| Planned expanded peak under current assumptions | profile-selected | 56 with full canonical inventory + legacy adapter + one runtime queue + compaction handoff + chunked resource bodies + source reverse lookup + response cache + sync + reserve | within ceiling | Leaves at least 8 DBI slots of headroom under `max_dbs = 64`. |

Any future addition must update this table with capability, default-open
status, underlying MDBX table type, number of physical DBI, paired reverse
orientation, steady-state count, migration peak, and sync support status.
Manual acceptance check: enumerate the §5.5 summary table rows, add only
profile-selected deltas, then verify `steady + sync + migration_reserve <= 64`.

### 5.6. Sync subsystem mapping (informational)

Этот подраздел фиксирует соответствие между таблицами секций 5.1–5.5 и sync v0.1 coverage из §1.5.4. На момент написания ТЗ sync subsystem **не активирован** (см. §1.5 и §11.7), поэтому mapping носит **прогнозный / informational** характер: формальный статус каждого DBI будет пересмотрен при adoption.

#### 5.6.1 Per-DBI sync coverage

Для каждой canonical DBI из §5.5, а также для legacy/profile-specific
inventory из §5.1-§5.4, приводим предполагаемый underlying table type и
соответствующий статус sync v0.1. Legacy rows не добавляются к canonical DBI
budget автоматически; они считаются только при явном включении в профиль.

| DBI | Underlying mdbx-containers type | Sync v0.1 status | Замечание |
|---|---|---|---|
| `knowledge_units` | `KeyValueTable<UnitId, KnowledgeUnitEnvelope>` (§5.5 Layer A) | Supported | Базовый KV путь; sync v0.1 покрывает |
| `content_key_to_unit_id` | `KeyValueTable<KnowledgeUnitKey, UnitId>` (§5.5 Layer A) | Supported | Dedupe/migration lookup; KV путь |
| `knowledge_units_by_kind` | `ReverseIndexTable<CompositeKey<ScopeId, KnowledgeUnitKind>, UnitId>` DUPSORT (§5.5) | **NOT supported** | DUPSORT не покрыт в v0.1; reverse index layer не синхронизируется |
| `unit_components` | `TypeDiscriminatedTable<ComponentKind, UnitId, ValueVariant<…>>` через `AnyValueTable` (§3.4) | **NOT supported** | `AnyValueTable` не покрыт в v0.1 |
| `qa_payloads`, `fact_payloads`, `conversation_episode_payloads`, `compiled_article_payloads`, `chunk_payloads` | `KeyValueTable<UnitId, PerKindPayload>` (§5.5 Layer B) | Supported | Per-kind payloads — обычные KV |
| `source_refs` | `KeyValueTable<UnitId, std::vector<SourceRef>>` (§5.5 Layer B) | Supported | Full source refs; M1 capability |
| `unit_projections` | `KeyValueTable<CompositeKey<...>, SearchProjection>` (§5.5 Layer C) | Supported | Composite key — opaque bytes на wire (см. §1.5.5) |
| `embedding_meta`, `embedding_vectors` | `KeyValueTable<CompositeKey<...>, …>` (§5.5) | Supported | KV; vector store дополнительно через indirect path (§1.5.4) |
| `inverted_token_to_unit` | `ReverseIndexTable<CompositeKey<ScopeId, TokenId, ProjectionKind, FieldId>, UnitId>` поверх `KeyMultiValueTable` (§3.2) | **NOT supported** | См. §5.6.2 — critical gap для lexical inverted index |
| `field_to_postings` | `KeyValueTable<CompositeKey<ScopeId, ProjectionKind, FieldId, TokenId, UnitId>, PostingStats>` (§5.5) | Supported | Targeted posting update/delete by full posting identity |
| `lexical_token_by_text`, `lexical_token_by_id`, `lexical_chunk_stats`, `lexical_token_stats`, `lexical_collection_stats` | `KeyValueTable<CompositeKey<...>, ...>` (§5.5) | Supported | Canonical lexical dictionary/stats DBI |
| `metadata_filters` | `ReverseIndexTable<CompositeKey<ScopeId, MetadataKey, MetadataValue>, UnitId>` поверх `KeyMultiValueTable` | **NOT supported** | Pre-filter indexes не синхронизируются в v0.1 |
| `graph_edges_by_src`, `graph_edges_by_dst` | `ReverseIndexTable` поверх `KeyMultiValueTable` | **NOT supported** | Graph edges — DUPSORT-семантика, см. §3.2 + §5.5 |
| `temporal_event_index`, `temporal_unit_index` | `RangeIndexTable<CompositeKey<ScopeId, Timestamp, ...>, …>` поверх `KeyValueTable` или `KeyMultiValueTable` (§3.3) | Supported *или* **NOT supported** в зависимости от impl-а | Требует решения: если `RangeIndexTable` реализуется поверх `KeyMultiValueTable` (multi-payload per key), статус NOT supported; если поверх `KeyValueTable` (single payload), Supported |
| `speaker_to_units`, `session_to_units` | `ReverseIndexTable` поверх `KeyMultiValueTable` | **NOT supported** | DUPSORT |
| `usage_stats_index` | `ReverseIndexTable<CompositeKey<ScopeId, UnitId>, UsageStatsComponent>` (§5.5) | **NOT supported** | DUPSORT |
| `schema_info` | `KeyValueTable<string, SchemaInfo>` (§5.5) | Supported | KV |
| `source_refs_by_resource`, `response_cache` | `ReverseIndexTable` / `KeyValueTable` profile deltas (§5.5.1) | Mixed | `source_refs_by_resource` is DUPSORT and not sync v0.1; `response_cache` is KV |
| legacy `lexical_token_by_text`, `lexical_token_by_id`, `lexical_chunk_stats`, `lexical_token_stats` | `KeyValueTable<...>` (§5.1) | Supported | Legacy lexical aliases; canonical names live in §5.5 |
| legacy `lexical_postings`, `lexical_field_postings` | `KeyMultiValueTable<...>` / `ReverseIndexTable` (§5.1, §3.2) | **NOT supported** | Legacy DUPSORT postings — critical gap for BM25 sync |
| `binary_bucket_index`, `embedding_store`, `chunk_store` | `RangeIndexTable` / `KeyValueTable` (§5.2) | Supported (KV) *или* **NOT supported** (если `RangeIndexTable` через `KeyMultiValueTable`) | Зависит от impl-а, см. temporal_event_index выше |
| legacy `qa_knowledge`, `fact_store`, `event_store`, `graph_nodes` | `KeyValueTable<...>` (§5.3) | Supported | Legacy KV aliases; canonical names live in §5.5 |
| legacy `qa_inverted`, `fact_inverted`, `resource_metadata_filters`, `graph_edges_by_src/dst` | `ReverseIndexTable` поверх `KeyMultiValueTable` | **NOT supported** | Legacy DUPSORT aliases; canonical names live in §5.5 |
| legacy `resource_kinds` | `TypeDiscriminatedTable<DerivedRecordKind, ResourceId, payload>` через `AnyValueTable` (§5.3) | **NOT supported** | Legacy `AnyValueTable` alias |
| `agent_memory_documents`, `agent_memory_chunks`, `agent_memory_document_chunks`, `agent_memory_resource_manifests` | `KeyValueTable<...>` (§5.4) | Supported | KV infrastructure |

Эта таблица — **best-effort projection на момент написания TZ**. Статус для каждого DBI может измениться после `RangeIndexTable` upstream-уточнения (`KeyValueTable` vs `KeyMultiValueTable` основа) и после `KeyMultiValueTable` sync coverage, который формально запланирован на v0.2 (см. §11.7).

#### 5.6.2 Critical gaps (явные)

Из таблицы §5.6.1 вытекают два **критических gap-а** для agent-memory-cpp:

1. **`inverted_token_to_unit` (DBI из §3.3 / §5.5 Layer C secondary indexes).**
   Underlying `KeyMultiValueTable` через `ReverseIndexTable` (см. §3.2) — DUPSORT-семантика. Sync v0.1 не покрывает `KeyMultiValueTable`, поэтому lexical inverted index **не синхронизируется между узлами в v0.1**.

2. **`unit_components` (DBI из §3.4 / §5.5 Layer B).**
   Underlying `AnyValueTable` через `TypeDiscriminatedTable` (см. §3.4). Sync v0.1 не покрывает `AnyValueTable`, поэтому operational components из `unit_components` **не синхронизируются между узлами в v0.1**. Per-kind payload DBIs используют `KeyValueTable` и покрыты в §5.6.1.

Та же проблема распространяется на остальные DUPSORT-производные DBI (`metadata_filters`, `graph_edges_by_src/dst`, `speaker_to_units`, `session_to_units`, `usage_stats_index`, legacy BM25 `lexical_postings`). Canonical `field_to_postings` является KV table и покрывается sync v0.1 как derived posting stats.

Цитата из upstream `DESIGN.md` (по указанному в §1.5.3 файлу):

> "Do not add `record_op()` paths for unsupported table types without first updating this design document and adding round-trip replication tests for the new wire-format semantics."

Это означает, что **любая попытка «форсировать» sync для `KeyMultiValueTable` / `AnyValueTable` без обновления `DESIGN.md` + новых round-trip тестов считается нарушением upstream-контракта**. Adoption для указанных путей в v0.1 заблокирован upstream policy-ей.

#### 5.6.3 Implication для agent-memory-cpp

До тех пор, пока upstream не выпустит v0.2 с `KeyMultiValueTable` и `AnyValueTable` wire-format-ом, **lexical inverted index и scope-aware secondary indexes, а также `unit_components` storage, не могут быть синхронизированы между узлами**. Это напрямую влияет на multi-host scenario в `guides/memory-stacks-roadmap.md` §13.2/13.3 (Distributed scope routing).

**Промежуточное решение** для проекта: defer sync adoption для путей `KeyMultiValueTable` / `AnyValueTable` до v0.2. KV-derived пути (`knowledge_units`, `unit_projections`, `embedding_*`, `schema_info`, per-kind payloads) технически покрыты, но включать их в v0.1 single-host конфигурации смысла нет (single-writer per env остаётся правилом M0/M1/M2, см. §1 non-goals).

Конкретное решение о formal adoption (включая budget reallocation для 5 sync system DBIs) фиксируется в §11.7.

## 6. Порядок реализации (приоритеты)

**P0 (блокер для knowledge units):**
- `MultiTableWriter` + `Connection::multi_write`
- `ReverseIndexTable`
- `TypeDiscriminatedTable`
- `composite_key_to_bytes/from_bytes`

**P1 (блокер для BM25F + temporal):**
- `RangeIndexTable` с exclusive bounds и pagination
- `CompositeKey<Parts...>` struct + traits, with acceptance for 2..5 parts
- Расширения `KeyValueTable`: `update_many`, `add_many`, `erase_many`

**P2 (расширения):**
- `paginated_range` для существующих классов
- `diagnostics()` для всех таблиц
- Расширения `Config`: `enable_metrics`, `table_creation_callback`
- Утилиты: `hamming_neighbors_uint64`, `to_hex_string`, `from_hex_string`

**P3 (последующие итерации):**
- `KeyValueTable::merge`, `snapshot`, `reindex`
- `SequenceTable::reserve`, `append_if_absent`
- `AnyValueTable::bulk_set_of_type<T>`

**P4 (после появления двух независимых consumer-ов relation helper):**
- Никакие agent-memory-specific `ArtifactRefTable`, `DerivationIndex`,
  `EvidenceIndex` или `DrillDownIndex` не добавляются в public API
  `mdbx-containers`.
- Если два и более независимых downstream consumer-а докажут повторную
  применимость, можно вынести generic
  `BidirectionalRelationIndex<Scope, Source, Target, Tag, Payload>` helper поверх
  пары `ReverseIndexTable` orientations. `Tag` и `Payload` остаются opaque.

## 7. Backward compatibility

- **ABI:** новые методы добавляются как overloads или шаблоны. Template instantiations расширяются, но layout существующих классов не меняется. `MDBX_DUPSORT` flag применяется только в новых классах.
- **Source compat:** старый код с `KeyValueTable<K, V>` компилируется без правок.
- **C++11 baseline:** все новые C++17 фичи (`std::byte`, `std::optional`, structured bindings, `if constexpr`) guarded через `MDBXC_HAS_CPP17` или `__cplusplus >= 201703L`. `_compat` псевдонимы для C++11 fallback (`optional_compat` → `std::optional` на C++17, boost::optional на C++11).
- **Include guards:** новые файлы используют `MDBX_CONTAINERS_HEADER_<PATH>_<FILE>_HPP_INCLUDED`.
- **Doxygen:** все новые публичные API документированы на английском (см. `external/mdbx-containers/guides/coding-style.md`).
- **Application schema versioning:** версии payload-ов остаются на стороне `agent-memory-cpp` через `schema_info` (`envelope_schema_version`, `component_schema_versions[]`, `profile_signature`, `migration_phase`). `mdbx-containers` не мигрирует и не интерпретирует application payload schema.
- **Canonical `profile_signature`:** детерминированный hash от application-level profile manifest-а: `envelope_schema_version`, отсортированный список `component_schema_versions`, DBI manifest (`dbi_name`, table type, MDBX flags, key/value layout ids), index layout versions, capability set, profile selectors/policies (including `response_cache_storage`) и `migration_phase`. Термин `schema checksum` в этом ТЗ не используется как отдельное поле; если он появится позже, он должен быть явно mapped к `profile_signature` или заменён им.
- **Sync schema compatibility (opt-in):** если §11.7 когда-либо разрешит включить upstream sync subsystem из §1.5, `agent-memory-cpp` предоставляет application-level compatibility guard до запуска upstream sync / до передачи user DBI batches в `SyncEngine`. `mdbx-containers` не читает `schema_info`, не знает `profile_signature` и не выполняет application schema validation.

Минимальный контракт для будущего sync adoption:

1. `agent-memory-cpp` compatibility guard выполняет handshake и сравнивает `profile_signature` + `migration_phase` до передачи user batches в upstream `SyncEngine`. Несовместимые peers получают диагностируемый application-level отказ без записи в user DBI.
2. Payload migrations выполняются application-level процедурой: dual-read/backfill/switch/drop-old, а не автоматической трансляцией внутри `mdbx-containers`.
3. During migration sync либо выключен, либо разрешён только между peers с одинаковым migration phase marker в `schema_info`.
4. Application profile validator проверяет полный DBI manifest до включения sync и запрещает запуск, если профиль содержит table types без upstream round-trip coverage (`KeyMultiValueTable`, `AnyValueTable`, `HashedKeyValueStore` в snapshot §1.5.4).
5. Частичная репликация KV-derived DBI без DUPSORT/`AnyValueTable`-derived DBI не считается schema-compatible profile для `agent-memory-cpp`; это ровно причина defer-решения в §11.7.

## 8. Тестирование

### 8.1 Контрактные тесты в `external/mdbx-containers/tests/`

Новые файлы:
- `reverse_index_table_test.cpp` — add/find/remove, prefix scan, DUPSORT-aware
  page cursor, range, remove_all_for.
- `range_index_table_test.cpp` — inclusive/exclusive bounds, offset pagination,
  cursor pagination, lower/upper bound, first/last, empty ranges.
- `type_discriminated_table_test.cpp` — type discrimination correctness, optional validation, list_keys_by_tag.
- `composite_key_test.cpp` — round-trip, byte layout, ordering.
- `multi_table_writer_test.cpp` — atomicity (rollback на exception), commit/rollback контракты.
- `utils_extensions_test.cpp` — hamming_distance, hamming_neighbors, hex encoding, sortable_key_from_int64.

Каждый тест верифицирует:
1. Range boundaries (inclusive/exclusive оба).
2. DUPSORT semantics (identical keys + identical values).
3. Transaction isolation (read-only snapshot во время write).
4. Multi-table write atomicity (rollback отменяет все таблицы).
5. Empty ranges, single-element ranges, ranges spanning много DBI pages.

### 8.2 Integration тесты в `agent-memory-cpp/tests/`

- `MdbxKnowledgeUnitStore` пишет `KnowledgeUnitEnvelope` в `knowledge_units`,
  `KnowledgeUnitKey -> UnitId` в `content_key_to_unit_id`, scope-aware
  by-kind entries в `knowledge_units_by_kind`, operational components в
  `unit_components`, search projections в `unit_projections`, optional full
  refs в `source_refs`, и kind-specific payloads в соответствующие §5.5 DBI.
- `MdbxQAKnowledgeBase` использует canonical `qa_payloads` +
  `inverted_token_to_unit` / `field_to_postings` projections; старый
  `qa_knowledge` inventory из §5.3 не открывается в canonical profile.
- `MdbxGraphStore` использует `graph_edges_by_src` и `graph_edges_by_dst` как
  две явные physical orientations; traversal policy остаётся downstream.
- `MdbxTemporalIndex` использует `temporal_event_index` /
  `temporal_unit_index` для range queries; event payload semantics остаются
  downstream.

Все интеграционные тесты должны проходить в C++11 и C++17 режимах.

### 8.3 Sync tests (opt-in)

Эти тесты **не входят** в обязательный M0/M1/M2 gate, пока sync adoption остаётся deferred (§11.7). Они добавляются только в конфигурации, где upstream `sync/` доступен и сборка явно включает `MDBXC_SYNC_ENABLED=1`.

Contract tests для будущей adoption-ветки:

1. **Support matrix lock.** Тест фиксирует матрицу §1.5.4: `KeyValueTable`, `KeyTable`, `ValueTable`, `SequenceTable` и indirect `VectorStore` проходят round-trip. Отсутствие upstream round-trip support для `KeyMultiValueTable`, `AnyValueTable` и `HashedKeyValueStore` проверяется через application profile validator, а не через предположение о generic runtime `UnsupportedTableType` в `mdbx-containers`.
2. **Application schema identity guard.** Два peers с разными `profile_signature` / `migration_phase` не передают user DBI batches в upstream `SyncEngine`. Проверка должна доказывать, что application-level отказ возникает до записи в `knowledge_units`, `unit_projections` или `embedding_*`.
3. **Partial coverage guard.** Fixture с KV-derived таблицей и DUPSORT-derived inverted index не должен объявляться fully synced profile: успешный KV round-trip не маскирует отсутствие `KeyMultiValueTable` coverage. Этот guard принадлежит `agent-memory-cpp` profile validator-у.
4. **DirectSyncPeer / SyncEngine contract.** Для `DirectSyncPeer` и `SyncEngine` отдельно проверяются pull/push, `ApplyResult::{Applied,Skipped,Conflict}`, conflict diagnostic и idempotent replay (`Skipped`).
5. **SyncWorker over DirectSyncPeer contract.** Для `SyncWorker` поверх `DirectSyncPeer` отдельно проверяются round failure, переход в `Backoff`, retry и возврат в `Idle` после успешного round-а.
6. **System DBI budget.** Для зафиксированного upstream snapshot `d4d219c` проверяется, что 5 системных DBI из §1.5.10 учитываются в `max_dbs` budget и отражены в diagnostics. При обновлении pinned upstream snapshot ожидание пересматривается по system-DBI manifest.

Текущий TZ не требует создавать эти тесты до формальной adoption. Их назначение — зафиксировать future acceptance criteria, чтобы sync v0.1 не был случайно включён как «почти готовый» distributed mode.

## 9. Документация и примеры

### 9.1 Обновления в `external/mdbx-containers/README.md` и `README-RU.md`

Добавить секцию "New table types" с описанием каждого нового класса, мотивацией, code snippet.

### 9.2 Новые примеры в `external/mdbx-containers/examples/`

- `reverse_index_demo.cpp` — secondary index pattern для inverted token → chunk_id.
- `range_index_demo.cpp` — temporal range query + binary bucket neighbor expansion.
- `type_discriminated_demo.cpp` — heterogeneous component payloads в одной таблице, без использования как canonical `KnowledgeUnit` store.
- `multi_table_transaction_demo.cpp` — atomic write primary + secondary.
- `composite_key_demo.cpp` — BM25F field posting с `(token_id, field_id)`.

### 9.3 Обновления в `guides/`

Добавить cross-reference из `lexical-search-roadmap.md` и `optimization-roadmap.md` к новым mdbx-containers классам. Новый `knowledge-units-roadmap.md` описывает контракты `KnowledgeUnitKind` и то, как `TypeDiscriminatedTable` используется.

## 10. Метрики успеха

### 10.1 Сквозные (cross-cutting) метрики

1. **ABI compatibility:** все существующие тесты `external/mdbx-containers/tests/` (15 файлов) проходят без изменений после расширения.
2. **Adoption:** новые классы используются как storage backend для `MdbxKnowledgeUnitStore`, `MdbxQAKnowledgeBase`, `MdbxGraphStore`, `MdbxTemporalIndex`, `MdbxBinaryBucketIndex`.
3. **Documentation:** каждый новый публичный метод имеет Doxygen comment + usage example.
4. **Cross-version:** новый код компилируется и работает в C++11 и C++17 режимах (verified двумя CMake конфигурациями, см. `external/mdbx-containers/build-mingw-11-tests.bat` и `build-mingw-17-tests.bat`).

### 10.2 Per-deliverable измеримые targets

Матрица связывает каждый `Deliverable` из приоритетов секции 6 с конкретной метрикой и целевым значением. Все targets валидируются в `external/mdbx-containers/tests/` (контрактные) и `agent-memory-cpp/tests/` (integration). Бенчмарки проводятся на reference hardware (NVMe SSD, 32 GB RAM, single-thread, `fsync=on`).

| Приоритет | Deliverable | Метрика успеха | Целевое значение |
|---|---|---|---|
| P0 | `ReverseIndexTable::find()` (DUPSORT lookup) | p99 latency на 100K keys | `< 5 µs` |
| P0 | `Single KeyValueTable::put()` (no-sync / non-durable write path) | throughput на NVMe | `> 500K ops/sec` (acknowledged writes land в OS page cache; NOT durable across crash/power loss) |
| P0 | `Single KeyValueTable::put() + Transaction::commit()` (fsync=on) | throughput на NVMe | bottlenecked by fsync latency (сотни µs на SSD, единицы ms на HDD/remote storage); не primary optimization target |
| P0 | `Batched transaction: 1000 records per txn` (fsync=on) | throughput и p99 commit latency | `> 100K records/sec` AND `< 10ms` p99 commit latency |
| P0 | `TypeDiscriminatedTable::find(tag, key, txn)` | p99 latency на 100K keys × 9 type tags | `< 8 µs` |
| P0 | `MultiTableWriter` (atomic 6-table write) | round-trip при success | `< 2×` стоимости одной `Transaction::commit` |
| P1 | `MultiTableWriter` (batch, N=1000) | amortized fsync cost vs одиночная write | amortized latency per record ≤ 0.1 × single-record durable `Transaction::commit()` latency (~10× batching gain) |
| P1 | `RangeIndexTable::range()` inclusive/exclusive | correctness на synthetic keys | `100%` ordered match vs `KeyMultiValueTable` baseline |
| P1 | `paginated_range` over `CompositeKey` | ordering identical к sequential scan | `100%` |
| P1 | `CompositeKey` round-trip | byte layout identity across compilers | golden-byte test: `to_bytes(key)` сравнивается с versioned expected blob; cross-compile-and-run на GCC, Clang, MSVC в C++11 и C++17 режимах; endian и padding policy документированы (рекомендация: little-endian, packed, no padding для стабильного layout) |
| P2 | `KeyValueTable::paginated_range_forward()` | latency vs `range_reverse` | `< 1.5×` на 100K pages |
| P2 | `KeyValueTable::diagnostics()` | reporting точность | reported `avg`/`p95` совпадают с external sampler ±5% |
| P2 | `Config::enable_metrics` / `table_creation_callback` | integration overhead | `< 2%` per-writer overhead |
| P2 | `hamming_neighbors_uint64` (radius 0-3, 24-28 bits) | lookup count, latency | соответствие ожидаемым таблицам cumulative: 24 bits → `1/25/301/2325` (radius 0/≤1/≤2/≤3); 28 bits → `1/29/407/3683` (radius 0/≤1/≤2/≤3) (см. `guides/optimization-roadmap.md` §"Neighbor Bucket Masks") |
| P2 | `to_hex_string` / `from_hex_string` | round-trip identity | `100%` |
| P3 | `KeyValueTable::merge` (same K, V types) | throughput vs полного reindex | `> 2×` speedup на 100K entries |
| P3 | `KeyValueTable::snapshot` | memory overhead | `< 2×` raw storage size |
| P3 | `SequenceTable::reserve` / `append_if_absent` | throughput per ID | `> 1M IDs/sec` |
| P3 | `AnyValueTable::bulk_set_of_type<T>` | bulk write throughput | `> 0.5×` `KeyValueTable::add_many` |
| P3 | Paired relation cleanup pattern (TZ §12.2) | correctness | `0` dangling reverse entries после purge на synthetic workload |
| P3 | `RangeIndexTable::recent` / tail pagination (TZ §12.3) | ordering by sortable key | `100%` correct order на shuffled fixture |

Opt-in sync metrics применяются только в сборках с `MDBXC_SYNC_ENABLED=1` и не являются gate-ом для M0/M1/M2, пока §11.7 остаётся в состоянии **DEFER**:

| Приоритет | Deliverable | Метрика успеха | Целевое значение |
|---|---|---|---|
| Sync opt-in | KV sync round-trip через `DirectSyncPeer` | applied records, p50/p95/p99 apply latency, bytes in/out | report-only baseline на reference hardware |
| Sync opt-in | Application schema mismatch guard | incompatible `profile_signature` / `migration_phase` | `100%` application-level rejection before user DBI mutation |
| Sync opt-in | Unsupported table profile validator | DBI manifest with `KeyMultiValueTable` / `AnyValueTable` / `HashedKeyValueStore` | `100%` startup rejection before sync begins; no silent partial-success |
| Sync opt-in | System DBI budget | counted sync DBIs | 5 additional DBIs for pinned snapshot `d4d219c`; revise expectation when pinned upstream snapshot changes |
| Sync opt-in | Idempotent re-apply | duplicate batch handling | `100%` duplicate records reported as `Skipped`, no duplicate user records |

Измерительный протокол: не менее `30` независимых benchmark rounds, каждый round — `10 000` операций на freshly-constructed state; для каждого round-а вычисляются p50/p95/p99 по его `10 000` операциям; затем для каждого percentile (p50/p95/p99) вычисляется median (и dispersion: std-dev или IQR) по `30` rounds — это и есть финальная reported metric; дополнительно per-round p50/p95/p99 фиксируются; warm-up period документируется (минимум `3` предварительных round-а отбрасываются); эффекты GC/памяти измеряются и комментируются; hardware doc фиксируется в `external/mdbx-containers/bench/results/<YYYY-MM>/`. Альтернативный упрощённый вариант: не менее `1 000 000` operation-level samples; p50/p95/p99 по полной выборке; median ± stddev между независимыми rounds.

Замечание о метриках: throughput в строках выше — три отдельные метрики, не одна. Async (no-sync) метрики ограничены RAM и CPU bandwidth, не storage latency. Batched (один `Transaction::commit()` на N записей) метрики отражают amortized fsync стоимость и дают качественно иные числа, чем одиночные durable записи. Durable single-write метрики ограничены fsync latency и в общем случае не являются primary optimization target для write-heavy сценариев — для них предпочтительны batched протоколы с явным amortization target.

## 11. Открытые вопросы и риски

1. **ABI break risk для trivially copyable composite key.** Если любой
`CompositeKey<Parts...>` part содержит padding, byte layout может быть
нестабильным между компиляторами. Решение: документировать требование
`static_assert(std::is_trivially_copyable<Part>::value)` для каждого part,
фиксировать byte order / padding policy и добавить golden-byte tests для 2-, 3-,
4- и 5-part keys.

2. **DUPSORT performance на больших posting lists (>10K entries per token).** MDBX DUPSORT оптимизирован для sorted duplicates, но insertion sort O(N) при unordered insert. Решение: enforced sort order при insert, или переход на segmented postings (см. `guides/lexical-search-roadmap.md` секция "Posting Segments").

3. **Per-thread transaction модель ограничивает параллельный write.** Multi-table write через `MultiTableWriter` сериализует все writes в одном потоке. Для multi-writer сценариев потребуется connection-per-thread pool; это за рамками TZ.

4. **C++11 vs C++17 feature guards.** `std::optional`, `std::byte`, structured bindings, `if constexpr` — все guarded. Boost polyfill нежелателен (dependency-free-first). Fallback: собственные минимальные replacements в `detail/` namespace.

5. **Schema versioning остаётся на стороне приложения.** `TypeDiscriminatedTable` не навязывает payload version. Контракт: payload prefix `agent_memory.knowledge_unit.v1` etc. валидируется на уровне `agent-memory-cpp`, не в mdbx-containers.

6. **`max_dbs` ceiling.** `Config::max_dbs = 64` является capacity ceiling для
одного MDBX env. Единственный authoritative accounting — §5.5.1; каждое новое
physical DBI или profile delta обязано обновить этот checkpoint. Verify, что
MDBX env flags поддерживают выбранный profile manifest без `MDBX_NOTLS`
reconfiguration.

7. **Sync subsystem adoption** (см. §1.5 и §5.6). Принимать ли upstream `sync` v0.1, отложить adoption полностью, или форкать custom решение?

   **Контекст.** v0.1 покрывает только KV-derived table types (`KeyValueTable`, `KeyTable`, `ValueTable`, `SequenceTable`, `VectorStore` indirect). Это **исключает** наши критические paths: lexical candidate index через `inverted_token_to_unit` (`KeyMultiValueTable` underlying) и `unit_components` storage (`AnyValueTable` underlying через `TypeDiscriminatedTable`). Тот же gap покрывает DUPSORT secondary indexes (`metadata_filters`, `graph_edges_by_*`, `speaker_to_units`, `session_to_units`, `usage_stats_index`), а также legacy DUPSORT-производные пути из §5.3. Canonical `field_to_postings` and lexical dictionary/stats are KV and covered by the v0.1 table-type surface.

   **Trade-offs.**

   - **Принять v0.1 сейчас.** Получаем baseline для KV-путей (`knowledge_units`, `unit_projections`, `embedding_*`, `schema_info`, per-kind payloads, `field_to_postings`, lexical dictionary/stats) — но inverted candidate indexes и components по-прежнему остаются локальными. Mixed replication state (часть DBIs synced, часть — нет) увеличивает operational complexity без видимого выигрыша для M0/M1/M2.
   - **Игнорировать sync полностью.** Минимальный cognitive load на этом этапе; остаёмся strict single-host. Стоимость: позже придётся догонять upstream API drift-ы, если когда-нибудь понадобится multi-host.
   - **Форкнуть custom решение** (поверх существующих `KeyValueTable`/`KeyMultiValueTable`). Полный контроль над wire format и table coverage, включая `KeyMultiValueTable` и `AnyValueTable`. Стоимость: собственный DESIGN.md, свои round-trip тесты, своя on-call ответственность за конфликт-резолюцию. По сути повторяет upstream работу.

   Дополнительные соображения:

   - **Budget impact.** Активация sync v0.1 добавляет 5 системных DBIs (`_mdbxc_meta`, `_mdbxc_changelog`, `_mdbxc_origins`, `_mdbxc_applied`, `_mdbxc_identity_index`, см. §1.5.10) в `max_dbs` budget. Текущий план — 64 (см. §5.5.1). Sync-adoption subtotal для canonical manifest + legacy document adapter + sync + migration reserve равен 46 DBI; authoritative expanded peak с runtime queue, handoff, resource-body, source-ref reverse lookup and response-cache deltas равен 56 по §5.5.1. Любая новая capability обязана обновить §5.5.1 до adoption.
   - **`IdentityIndexStore` write path deferred upstream.** Не все identity-mapping writes покрыты в v0.1; конкретные edges помечены в upstream `SyncEngine.hpp` TODO-комментариями. Это влияет на dedup state, но не блокирует базовый pull/push.
   - **HTTP/WebSocket transport seams.** GitHub PR #104 и #105 уже merged в upstream main snapshot `d4d219c`: `TransportMessageCodec` и framework-neutral HTTP seam входят в v0.1; WebSocket seam также присутствует в текущем `DESIGN.md`. Это снимает прежний blocker «нет HTTP seam», но не превращает sync v0.1 в готовый distributed profile для `agent-memory-cpp`: concrete production socket transport остаётся adapter-local responsibility, а table coverage gaps ниже важнее для M0/M1/M2.
   - **Wire-format byte cost.** 5 sync DBIs суммарно хранят metadata/changelog/origins/applied/identity. Полная on-disk cost-модель (включая compression overhead) — отдельная задача, не решается в этом TZ; ссылка на общий принцип raw-code vs end-to-end storage footprint — §1.5.10 note.

   **Decision deadline** привязан к двум внешним триггерам и одному readiness-check:

   - (a) **Upstream v0.2** ship-ит `KeyMultiValueTable` wire format. Без этого блокируется lexical inverted index sync; см. §5.6.2.
   - (b) **agent-memory-cpp** достигает multi-host scope routing (см. `guides/memory-stacks-roadmap.md` §13.2 / §13.3, headings `Distributed scope routing`). До этого момента single-host остаётся правилом M0/M1/M2.
   - (c) **Transport readiness-check** для выбранного deployment-а: подтвердить, что framework-neutral HTTP/WebSocket seam из upstream достаточно закрывает нужный transport boundary, или добавить adapter-local bridge / concrete binding вне core library.

   **Recommendation.** **DEFER** formal adoption sync v0.1 как минимум до выполнения **(a) + (b)** и прохождения readiness-check **(c)**. До этих условий: sync subsystem не активируется, текущий TZ остаётся informational (§1.5, §5.6.1), а future revision этого документа будет обязана пересмотреть §11.7 при срабатывании любого из (a)/(b) или при выборе concrete multi-host deployment-а. v0.1 **не блокирует** M0/M1/M2 deliverables; это явное исключение из «everything upstream-goes» policy-и данного TZ.

## 12. Дополнительные API decisions (из обзора roadmap-документов)

Данный раздел фиксирует дополнительные API decisions и boundary decisions,
выявленные при обзоре существующих roadmap-документов
`external/mdbx-containers/` и смежных направлений `agent-memory-cpp`. Если
пункт помечен как out of upstream scope или informational, он не является
требованием к публичному API `mdbx-containers`.

### 12.1 Retrieval fusion (out of upstream scope)

RRF, cross-encoder rerank и любые retrieval fusion algorithms остаются в
`agent-memory-cpp` Layer 2. `mdbx-containers` не получает `HybridSearch.hpp`,
`ScoredId` или `rrf_fuse` как public API. Для upstream storage library
достаточно deterministic iteration order, pagination и bounded result encoding
в generic table primitives.

Downstream `agent-memory-cpp::HybridRetrievalEngine` может продолжать держать
локальную RRF-реализацию и тестировать её в retrieval/eval harness-е.

### 12.2 Expiring relation storage pattern

Soft-edge expiration является application policy. `mdbx-containers` не
определяет `GraphStore`, `EdgeKind`, `NodeId` или семантику "expired edge".
Generic requirements:

- relation payload may contain an application-owned `expires_at_ms`;
- application code decides whether `0`, missing value, or `null` means
  non-expiring;
- if fast purge is required, application may maintain a paired
  `RangeIndexTable<ExpiresAt, RelationKey>` and remove both orientations in one
  transaction;
- `ReverseIndexTable::remove_all_for` and any future generic
  bidirectional-relation helper must leave no dangling reverse entries.

### 12.3 Temporal and source-attributed records

`Event`, `occurred_at_ms`, `observed_at_ms`, `source_id` and freshness ranking
belong to `agent-memory-cpp` domain payloads. `mdbx-containers` only needs
generic support for:

- inclusive/exclusive bounds in `RangeIndexTable`;
- deterministic `recent(n)` / tail pagination when implemented over a sortable
  key;
- optional application-maintained reverse indexes such as
  `(scope_id, source_id) -> record_id`;
- atomic multi-table writes for primary record + temporal/source indexes.

### 12.4 Decay policy (out of upstream scope)

Usage-based decay and candidate scoring are memory-strategy semantics. They
must not be implemented as `mdbx-containers::MemoryStore` behavior. Upstream
requirements are limited to storing opaque usage counters/timestamps, updating
them transactionally, and allowing deterministic reads of the indexes that
`agent-memory-cpp` uses to compute decay-aware scores.

### 12.5 Runtime job storage recipe (out of upstream scope)

Runtime worker lifecycle не входит в upstream scope. `mdbx-containers` не
получает `TaskQueue`, `JobStatus`, retry/backoff/failure state machine,
worker leases или cancellation API как требование этого TZ.

Storage recipe, который `agent-memory-cpp` может реализовать поверх generic
таблиц:

```text
jobs_by_id:
  KeyValueTable<JobId, OpaqueJobRecord>

jobs_scheduled:
  RangeIndexTable<ScheduleKey, JobId>

jobs_ready:
  RangeIndexTable<ReadyOrderKey, JobId>

jobs_by_lease:
  RangeIndexTable<LeaseUntilKey, JobId>

jobs_by_status:
  ReverseIndexTable<JobStatus, JobId>
```

`ScheduleKey`, `ReadyOrderKey` and `LeaseUntilKey` are application-defined.
The downstream queue contract requires `JobId` to be durable monotonic within
the queue and to serve as `enqueue_sequence`; no separate sequence DBI is part
of the +5 queue delta. Allocation uses an MDBX sequence bound to existing
`jobs_by_id` table metadata and advanced inside the enqueue transaction; it is
never computed from `max(job_id) + 1`, so concurrent enqueue, restart and
terminal-job pruning cannot reuse ids. Required key shapes:

```text
ScheduleKey   = (run_after_ms, job_id)
ReadyOrderKey = (priority_rank, job_id)
LeaseUntilKey = (lease_until_ms, job_id)
```

Delayed jobs are promoted first and ready jobs are claimed by priority/FIFO.
The full `JobRecord`, lifecycle transitions, leases, cancellation and retry
policy are defined downstream in `guides/runtime-services-roadmap.md` §4.6.
Canonical durable statuses are `Pending`, `Running`, `Done`, `Dead` and
`Cancelled`; retryable failures return to `Pending`, and exhausted or
unrecoverable failures become inspectable `Dead`.
Default topology is one shared persistent runtime queue per `MemoryStack`.
`JobRecord.kind` distinguishes `Compaction`, `AsyncIndex`, maintenance and
future worker payloads; `IndexUpdateJob` is only one opaque job payload kind.
The §5.5.1 expanded peak therefore counts one +5 runtime-queue delta. Opening a
second physical queue is a separate profile delta (+5 DBIs) and must update the
budget table before implementation.
Kind-aware dispatch is downstream: a single `JobDispatcher` owns `claim_next`,
decodes `JobRecord.kind`/codec version and routes typed payloads to handlers.
Individual workers do not claim from the shared queue and do not scan for their
own kind.

Atomic claim pattern:

1. через bounded page loop перенести due `jobs_scheduled` entries в
   `jobs_ready`: читать первую страницу due-prefix с `offset = 0` (или cursor
   page), удалить/добавить эти entries, повторять до пустого due-prefix;
2. в write transaction прочитать first ready key через bounded
   `RangeIndexTable` read (`limit = 1`, no full scan);
3. перечитать `jobs_by_id[JobId]`;
4. application-level predicate проверяет, что job всё ещё ready and not
   cancelled;
5. обновить primary opaque record;
6. удалить старые scheduled/ready/status/lease index entries и, если нужно, добавить
   новые;
7. commit/rollback выполняет обычный `MultiTableWriter`.

The claim transition also increments `lease_epoch`, writes `lease_owner` /
`lease_until_ms`, and returns a downstream
`ClaimToken(job_id, worker_id, lease_epoch, lease_until_ms)`. Owner-sensitive
downstream transitions (`renew_lease`, `complete`, `fail_retry`,
`release_unhandled`, `fail_dead`, `ack_cancel`) must receive the token and check
`status == Running`, `lease_owner`, `lease_epoch` and `now_ms < lease_until_ms`
in the same transaction as any terminal state update or derived write. Recovery
is eligible when `lease_until_ms <= now_ms`. This prevents a worker whose lease
expired from completing a newer claim attempt.

Queue storage acceptance:

- concurrent enqueue allocates distinct increasing `JobId` values from the
  durable table-bound sequence;
- restart preserves allocator state;
- pruning terminal jobs, including the highest existing `JobId`, never permits
  id reuse.

Possible upstream `PersistentQueue` рассматривается только отдельным proposal
после двух независимых не-agent-memory consumer-ов. До этого полный контракт
`TaskQueue` принадлежит `guides/runtime-services-roadmap.md`.

### 12.6 Готовые наборы сигнатур для наших generic-примитивов

Дополнить описания `ReverseIndexTable` и `RangeIndexTable` (см. секции 3.2 и 3.3) следующими методами, если их ещё нет в upstream-спецификации. Все дополнения — аддитивные, без изменения layout существующих классов.

#### 12.6.1 `ReverseIndexTable`

```cpp
/// @brief Cardinality of postings for an inverted key.
///        Equivalent to find(inv, txn).size() but possibly cheaper.
std::size_t count_by_inverted(const InvertedKey& inv,
                              const Transaction& txn) const;

/// @brief Page duplicate values for one inverted key.
/// @details Cursor is exclusive and carries encoded key + encoded duplicate
///          value, so continuation inside a large DUPSORT posting-list is
///          deterministic.
ReverseIndexPage<InvertedKey, PrimaryKey>
find_page(const InvertedKey& inv,
          const std::optional<ReverseIndexCursor>& after,
          std::size_t limit,
          const Transaction& txn) const;

/// @brief Page a prefix/range without materializing all matching postings.
ReverseIndexPage<InvertedKey, PrimaryKey>
prefix_page(const InvertedKey& prefix,
            const std::optional<ReverseIndexCursor>& after,
            std::size_t limit,
            const Transaction& txn) const;

template <class... PrefixParts>
ReverseIndexPage<InvertedKey, PrimaryKey>
prefix_page(const CompositePrefix<PrefixParts...>& prefix,
            const std::optional<ReverseIndexCursor>& after,
            std::size_t limit,
            const Transaction& txn) const;

ReverseIndexPage<InvertedKey, PrimaryKey>
range_page(const InvertedKey& from,
           const InvertedKey& to,
           const std::optional<ReverseIndexCursor>& after,
           std::size_t limit,
           const Transaction& txn) const;
```

#### 12.6.2 `RangeIndexTable`

```cpp
/// @brief Last N entries in ascending key order. Backed by
///        @c range with @c from = first() and page size = N
///        taken from the tail.
std::vector<std::pair<SortableKey, Payload>>
recent(std::size_t n, const Transaction& txn) const;

/// @brief Inclusive lower bound. Returns the first entry whose
///        key is >= @c key, or std::nullopt if no such entry exists.
std::optional<std::pair<SortableKey, Payload>>
lower_bound_inclusive(const SortableKey& key, const Transaction& txn) const;

/// @brief Upper bound. Returns the first entry whose
///        key is > @c key, or std::nullopt if no such entry exists.
std::optional<std::pair<SortableKey, Payload>>
upper_bound(const SortableKey& key, const Transaction& txn) const;

/// @brief Cursor page over a range.
/// @details Cursor is exclusive and carries encoded key + encoded payload.
///          Use this for mutating page loops or for ranges where offset-based
///          pagination can skip entries after deletes.
RangePage<SortableKey, Payload>
range_page(const SortableKey& from, const SortableKey& to,
           const std::optional<RangeCursor>& after,
           std::size_t limit,
           const Transaction& txn) const;
```

Контрактные требования к обоим наборам:

- **Аддитивность.** Ни один существующий публичный метод не переименовывается и не меняет сигнатуру.
- **`noexcept` политика.** Методы-обёртки над уже существующих batch-методов помечаются `noexcept` только если базовая операция noexcept; иначе исключения пробрасываются (см. `guides/coding-style.md` upstream-проекта).
- **Determinism.** Все методы, возвращающие коллекции, обязаны возвращать их в стабильном порядке (по `InvertedKey` / `SortableKey`).
- **Partial prefix.** `CompositePrefix<...>` must encode an exact byte prefix
  for the same composite-key codec as `InvertedKey`; hidden raw MDBX cursor
  access is not allowed in downstream helpers.
- **Тесты.** В `external/mdbx-containers/tests/` добавляются контрактные тесты для каждого нового метода, в стиле существующих 15 тестовых файлов.

### 12.7 Пример использования в наших планах

Ниже — короткие сценарии, иллюстрирующие, как `agent-memory-cpp` будет
использовать generic API, описанный в секциях 12.2–12.6. Сценарии не описывают
полную реализацию и не добавляют domain stores в `mdbx-containers`.

#### 12.7.1 Temporal index via `RangeIndexTable`

```cpp
// MdbxTemporalIndex.cpp
std::vector<Event> MdbxTemporalIndex::events_on(ScopeId scope,
                                                std::int64_t day_ms,
                                                const Transaction& txn) const {
    const auto from = CompositeKey{scope, day_ms, EventId::min()};
    const auto to   = CompositeKey{scope, day_ms + 24LL * 3600 * 1000 - 1,
                                   EventId::max()};
    return decode_events(occurred_at_index_.range(from, to, txn));
}

std::vector<Event> MdbxTemporalIndex::fresh(ScopeId scope,
                                            std::size_t n,
                                            const Transaction& txn) const {
    const auto from = CompositeKey{scope, Timestamp::min(), UnitId::min()};
    const auto to = CompositeKey{scope, Timestamp::max(), UnitId::max()};
    auto newest = observed_at_index_.range_reverse(from, to, n, txn);
    std::reverse(newest.begin(), newest.end()); // optional chronological output
    return decode_events(newest);
}
```

`occurred_at_ms`, `observed_at_ms`, `Event` и freshness policy остаются
application payload/logic; `mdbx-containers` отвечает только за ordered range
reads.

#### 12.7.2 Atomic relation write

```cpp
// MdbxGraphIndex.cpp
void MdbxGraphIndex::add_edge(const GraphEdge& edge,
                              const Transaction& txn) {
    graph_edges_by_src_.add(edge.src_key(),
                            RelationValue<ToUnitId, EdgePayload>{
                                edge.to, edge.payload()},
                            txn);
    graph_edges_by_dst_.add(edge.dst_key(),
                            RelationValue<FromUnitId, EdgePayload>{
                                edge.from, edge.payload()},
                            txn);
}
```

`EdgeKind`, expiration, contradiction/evidence tags and graph traversal policy
belong to `agent-memory-cpp`; `MultiTableWriter` only guarantees that both
physical orientations commit or roll back together.

#### 12.7.3 Runtime job storage recipe

```cpp
// RuntimeQueueStorage.cpp (adapter-local pseudo-code)
struct JobIndexKeys {
    std::optional<ScheduleKey> scheduled;
    std::optional<ReadyOrderKey> ready;
    JobStatus status;
    std::optional<LeaseUntilKey> lease;
};

void RuntimeQueueStorage::move_job_index(const JobIndexKeys& old_keys,
                                         const JobIndexKeys& new_keys,
                                         JobId id,
                                         const OpaqueJobRecord& record) {
    conn_.multi_write([&](Transaction& txn) {
        jobs_by_id_.put(id, record, txn);
        if (old_keys.scheduled) {
            jobs_scheduled_.remove(*old_keys.scheduled, id, txn);
        }
        if (old_keys.ready) {
            jobs_ready_.remove(*old_keys.ready, id, txn);
        }
        jobs_by_status_.remove(old_keys.status, id, txn);
        if (old_keys.lease) {
            jobs_by_lease_.remove(*old_keys.lease, id, txn);
        }

        if (new_keys.scheduled) {
            jobs_scheduled_.add(*new_keys.scheduled, id, txn);
        }
        if (new_keys.ready) {
            jobs_ready_.add(*new_keys.ready, id, txn);
        }
        jobs_by_status_.add(new_keys.status, id, txn);
        if (new_keys.lease) {
            jobs_by_lease_.add(*new_keys.lease, id, txn);
        }
    });
}
```

`RuntimeQueueStorage` является adapter-local `agent-memory-cpp` abstraction.
`mdbx-containers` предоставляет только `KeyValueTable`, `RangeIndexTable`,
`ReverseIndexTable` и atomic transaction mechanics из §12.5.

### 12.8 Downstream addressable compression pattern (informational)

Этот раздел фиксирует вывод из обзора TencentDB Agent Memory, но не добавляет
новые public API-типы в `mdbx-containers`. Tencent использует compact
представления с drill-down идентификаторами; для `agent-memory-cpp` полезен
инвариант:

```text
compact projection -> evidence/source relation -> canonical SourceRef/ResourceId
```

Владельцем `AddressableCompression`, `ProgressiveDisclosure`, evidence policy,
contradiction policy, artifact descriptors и detail-level enum-ов является
`agent-memory-cpp`. `mdbx-containers` должен предоставить только generic
storage properties, не интерпретируя application payload:

- `KeyValueTable<K, OpaquePayload>` для application-owned descriptor-ов, если
  canonical `SourceRef` / `ResourceId` контракта недостаточно.
- Paired relation indexes for efficient outgoing/incoming traversal. Existing
  `graph_edges_by_src` / `graph_edges_by_dst` already cover this pattern for
  unit-to-unit relations when their values include the opposite endpoint.
- Canonical projection identity остаётся composite key-ем
  `(ScopeId, UnitId, ProjectionKind, revision)` из `unit_projections`; отдельный
  `ProjectionId = uint64_t` не вводится в upstream TZ.
- `Tag` и `Payload` opaque для `mdbx-containers`; semantic tags вроде
  `DerivedFrom`, `Supports`, `Contradicts`, `SummaryOf`, `DetailOf`,
  `DrillDownTo` определяются только downstream.

#### 12.8.1 Optional generic relation helper

Если после реализации двух независимых consumer-ов выяснится, что ручное
ведение двух `ReverseIndexTable` orientations стабильно дублируется, можно
вынести generic helper:

Physical layout uses exactly two DBI, either newly created by the helper or
caller-provided handles already counted in the profile manifest:

```text
outgoing:
  ReverseIndexTable<CompositeKey<ScopeId, Source, Tag>,
                    RelationValue<Target, Payload>>

incoming:
  ReverseIndexTable<CompositeKey<ScopeId, Target, Tag>,
                    RelationValue<Source, Payload>>
```

If the helper wraps existing `graph_edges_by_src` / `graph_edges_by_dst`, DBI
delta is `+0` because the canonical graph value shape in §5.5 is already
`RelationValue<OppositeEndpoint, EdgePayload>`. For caller-provided DBIs,
`+0` is allowed only when their encoding is documented as compatible with
`RelationValue<OppositeEndpoint, Payload>` and DUPSORT ordering. If the helper
creates a new relation pair, profile delta is `+2` and §5.5.1 must be updated.

```cpp
template <class Endpoint, class Payload>
struct RelationValue {
    Endpoint endpoint;
    Payload payload;
};

template <class Source, class Target, class Tag, class Payload>
struct OutgoingRelation {
    Target target;
    Tag tag;
    Payload payload;
};

template <class Source, class Target, class Tag, class Payload>
struct IncomingRelation {
    Source source;
    Tag tag;
    Payload payload;
};

struct RelationCursor {
    std::vector<std::uint8_t> encoded_key;
    std::vector<std::uint8_t> encoded_value;
};

template <class Item>
struct RelationPage {
    std::vector<Item> items;
    std::optional<RelationCursor> next;
};

template <class Scope, class Source, class Target, class Tag, class Payload>
class BidirectionalRelationIndex {
public:
    BidirectionalRelationIndex(ReverseIndexHandle outgoing,
                               ReverseIndexHandle incoming);

    void add_or_replace(const Scope& scope,
                        const Source& source,
                        const Target& target,
                        const Tag& tag,
                        const Payload& payload,
                        const Transaction& txn);

    bool remove(const Scope& scope,
                const Source& source,
                const Target& target,
                const Tag& tag,
                const Transaction& txn);

    RelationPage<OutgoingRelation<Source, Target, Tag, Payload>>
    outgoing(const Scope& scope,
             const Source& source,
             const std::optional<Tag>& tag,
             const std::optional<RelationCursor>& after,
             std::size_t limit,
             const Transaction& txn) const;

    RelationPage<IncomingRelation<Source, Target, Tag, Payload>>
    incoming(const Scope& scope,
             const Target& target,
             const std::optional<Tag>& tag,
             const std::optional<RelationCursor>& after,
             std::size_t limit,
             const Transaction& txn) const;

    std::size_t remove_all_for_source(const Scope& scope,
                                      const Source& source,
                                      const Transaction& txn);

    std::size_t remove_all_for_target(const Scope& scope,
                                      const Target& target,
                                      const Transaction& txn);
};
```

Acceptance для такого helper-а storage-only:

- обе orientations обновляются атомарно в одной транзакции;
- `(scope, source, target, tag)` is unique; `add_or_replace` overwrites the
  payload for that exact relation tuple;
- traversal order детерминирован by `(scope, endpoint, tag, opposite endpoint)`;
- delete не оставляет dangling reverse entries;
- key/value encoding bounded и documented;
- no-tag traversal uses `RelationPage` + cursor over paginated prefix/range
  reads; unbounded materialization is not an acceptable implementation
  strategy;
- cursor is exclusive and contains both encoded key and encoded duplicate value,
  so page continuation inside one `(scope, endpoint, tag)` posting-list is
  deterministic;
- `limit > 0` обязательно; implementation caps limit by a documented maximum;
- `remove(scope, source, target, tag, txn)` removes the exact edge from both
  orientations atomically;
- `remove_all_for_source/target` scope-aware and may internally execute a
  bounded page loop until all entries in that scope/endpoint prefix are gone;
- no-tag traversal uses `CompositePrefix<Scope, Endpoint>` over the full
  `(scope, endpoint, tag)` key codec;
- `std::optional<Tag>` заменяется на project-local optional compatibility type
  в C++11 build-ах;
- payload/tag не интерпретируются upstream.

#### 12.8.2 Informational downstream validation

`agent-memory-cpp` may evaluate traceability coverage, progressive-disclosure
latency/read amplification, layer selection accuracy, token reduction,
task-success delta, and explicit failure classes such as
`EVIDENCE_MISSING` or `ARTIFACT_UNAVAILABLE`. These are downstream benchmark
runner concerns and are not `mdbx-containers` acceptance gates.

Upstream acceptance criteria stay limited to CRUD correctness, deterministic
ordering, transactional consistency, bounded encoding, cleanup behavior for
paired indexes, and report-only generic microbenchmarks.

### 12.9 Large values and raw document bodies

MDBX может хранить raw documents внутри env. Запрет относится только к
secondary/reverse indexes и descriptor/ref tables: туда нельзя класть большие
inline bodies, потому что это раздувает B-tree, дублирует payload и ухудшает
cache locality/write amplification.

Downstream decision для `agent-memory-cpp`:

- raw documents/tool logs могут храниться в MDBX через primary
  `ResourceBodyStore`;
- raw documents/tool logs также могут храниться во внешнем file-pack backend-е,
  если приложению важно сохранить структуру папок и человеко-ориентированный
  export/viewer workflow;
- `ResourceBodyStore` владеет `ResourceId`, `SourceRef`, codec/version prefix
  вроде `agent_memory.resource_body.v1`, separate descriptor/body limits,
  maximum encoded value or chunk size, compression/checksum/encryption policy
  и cleanup;
- reverse indexes хранят только ids, compact postings или compact relation
  payloads, но не body bytes.

Document importer / card normalizer не входит в `mdbx-containers` scope.
`agent-memory-cpp` сам решает, превращать ли raw `.md` / `.txt` / extracted
`.pdf` в generic `Note`/`Chunk` unit, curated `Fact`/`QAPair`/`Summary`, или
оставить только `ResourceBody` + `SearchProjection`.

`KeyValueTable<ResourceId, bytes>` достаточно, если body immutable, читается
целиком и profile задаёт небольшой maximum encoded value size. Candidate
generic upstream primitive `LargeValueStore` / `ChunkedBlobStore` нужен только
если есть хотя бы одно из требований:

- partial reads или range reads без загрузки всего body;
- chunk-level checksum/compression и repair;
- bounded chunk size для больших documents/tool logs;
- append/replace chunk без перезаписи всего value;
- orphan chunk cleanup и manifest/body consistency checks;
- repeated use outside `agent-memory-cpp`.

В текущий canonical budget §5.5.1 raw body DBI не входит. Если профиль включает
MDBX-backed `ResourceBodyStore`, он обязан добавить явный profile delta:

```text
resource_bodies        +1  // simple KV body store
или
resource_body_manifest +1
resource_body_chunks   +1  // chunked body store
```

После появления двух независимых не-agent-memory consumer-ов можно оформить
отдельный upstream proposal для generic `LargeValueStore<BlobId>` /
`ChunkedBlobStore<BlobId>`. До этого это application-owned adapter pattern.

## 13. Перекрёстные ссылки (потребители в agent-memory-cpp)

Этот раздел фиксирует downstream-потребителей TZ: какие roadmap-документы и компоненты `agent-memory-cpp` будут использовать конкретные DBIs из секции 5.5 и storage decisions/patterns из секции 12.

- [`guides/optimization-roadmap.md`](optimization-roadmap.md) §"Dense Index Modes (Backend Selection)" (mode/encoder/quality targets), §"HNSW Vector Index" — потребители `embedding_vectors` и `embedding_meta` DBI; `HammingTopK` kernel использует `hamming_neighbors_uint64` и popcount-хелперы из `detail/utils.hpp`.
- [`guides/knowledge-base-roadmap.md`](knowledge-base-roadmap.md) §5 — потребители `KnowledgeUnitStore` (использует `knowledge_units` + `unit_components` + `unit_projections`), `ComponentStore` (`unit_components` через `TypeDiscriminatedTable`), `FactStore`/`QAKnowledgeBase`/`GraphStore` (соответствующие per-kind payload DBI).
- [`guides/lexical-search-roadmap.md`](lexical-search-roadmap.md) §"BM25 baseline" — потребитель `lexical_index_*` DBI (`inverted_token_to_unit`, `field_to_postings`); `MultiTableWriter` обеспечивает атомарность write primary + secondary indexes.
- [`guides/runtime-services-roadmap.md`](runtime-services-roadmap.md) §"PromptCache" / §"AsyncIndexer" / §"WriteGate" — владелец runtime queue abstraction; при MDBX persistence использует storage recipe §12.5.
- [`guides/compaction-roadmap.md`](compaction-roadmap.md) §"CompactionWorker" — потребитель `MultiTableWriter` (атомарный compaction), `usage_stats_index` (для `DecayJob`), `embedding_meta` (для `EmbeddingRecomputeJob`) и downstream runtime queue из `runtime-services-roadmap.md`.
- [`guides/knowledge-units-roadmap.md`](knowledge-units-roadmap.md) §3 — владелец canonical `SourceRef` / `ResourceId` provenance contract; future compact-projection traceability should reuse `graph_edges_by_src` / `graph_edges_by_dst` and application-owned payloads instead of adding domain-specific `mdbx_containers` APIs.
- [`guides/experiments/2026-07-23-tencentdb-agent-memory-reference.md`](experiments/2026-07-23-tencentdb-agent-memory-reference.md) — TencentDB Agent Memory reference review that motivated the downstream addressable-compression pattern in §12.8.
- [`guides/memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §"Layer 1 (Storage Primitives)" and §12 ownership — основной downstream-потребитель: все секции 5.5 DBIs становятся частью capability-aware MDBX-схемы компонентной архитектуры.

## 14. Reference documents

- `external/mdbx-containers/PHILOSOPHY.md` — death-of-the-author, header-only
- `external/mdbx-containers/AGENTS.md` — coding agent workflow, critical defaults
- `external/mdbx-containers/guides/table-api-guide.md` — decision guide для выбора table classes
- `external/mdbx-containers/guides/coding-style.md` — naming, file layout, Doxygen
- `external/mdbx-containers/guides/implementation-notes.md` — transactions, serialization, table naming
- `external/mdbx-containers/include/mdbx_containers/KeyValueTable.hpp` — existing map-like
- `external/mdbx-containers/include/mdbx_containers/KeyMultiValueTable.hpp` — DUPSORT
- `external/mdbx-containers/include/mdbx_containers/AnyValueTable.hpp` — heterogeneous typed
- `external/mdbx-containers/include/mdbx_containers/SequenceTable.hpp` — uint64 IDs
- `external/mdbx-containers/include/mdbx_containers/detail/utils.hpp` — utilities
- `external/mdbx-containers/common/Connection.hpp` — transaction management
- `guides/lexical-search-roadmap.md` — направление lexical search
- `guides/optimization-roadmap.md` — binary bucket, vector encoding
- `guides/architecture.md` — DDD boundaries, dependency direction
- `guides/memory-stacks-roadmap.md` — Layer 1–4 архитектура памяти; секция 11 описывает Layer 1 (storage primitives, к которому относится этот ТЗ), секция 12 — ownership rules для physical manifest, секция 9 — capability matrix (открытие DBI по capability), секция 10 — validation rules (включая обязательность `scope_id`)
- `src/agent_memory/infrastructure/mdbx/MdbxDocumentStorage.cpp` — current usage
- `src/agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.cpp` — current usage
