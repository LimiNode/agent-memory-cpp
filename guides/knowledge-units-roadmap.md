# knowledge-units-roadmap.md

Спецификация per-kind payload-компонентов и lifecycle FSM для подсистемы памяти `agent-memory-cpp`. Документ конкретизирует компонентную модель, описанную в `guides/memory-stacks-roadmap.md` (ADR-001), и согласована с retrieval-флоу в `guides/knowledge-base-roadmap.md`.

> В будущем планируется переименование файла в `knowledge-payloads-roadmap.md` после завершения всех изменений; текущее имя сохранено для совместимости cross-references.

> C++17 compliance: кодовые сниппеты используют явные конструкторы вместо designated initializers, `const std::vector<T>&` вместо `std::span`, builder-методы где уместно. Компилируемые примеры — в `examples/` (отдельная задача).

## 1. Purpose

Этот документ описывает:

- `KnowledgeUnitKind` (canonical enum) и kind → payload mapping (какой kind использует какой payload).
- Per-kind правила генерации `primary_text` и `SearchProjection`.
- `SourceRef` contract и migration mapping.
- Generic raw document import: как `.md`, `.txt`, extracted `.pdf`,
  transcripts and logs становятся searchable units без предварительной
  curated-card конвертации.
- `KnowledgeUnitId` monotonic-uint64 scheme (opaque, никогда не reused; content-addressing через отдельный `KnowledgeUnitKey`).
- Lifecycle FSM (4 durable states: Active / Superseded / Deprecated / Erased) и anti-loop подсвинок через `UsageStatsComponent.cooldown_until_ms`.
- Manifest integration через `DerivedRecordKind`.
- Storage pattern: per-component DBI allocation и capability-aware DBI creation.
- Migration plan от старого монолитного `KnowledgeUnit` к новой envelope + components модели.

Cross-references:

- `guides/memory-stacks-roadmap.md` — ADR-001 (envelope + components), `MemoryProfileSpec`, `MemoryStack`, physical manifest ownership, maturity levels.
- `guides/knowledge-base-roadmap.md` — envelope shape, retrieval flow, decay-aware scoring, evaluation pipeline.
- `guides/lexical-search-roadmap.md` — BM25F по `SearchProjection`s, field-weighted indexing.
- `guides/optimization-roadmap.md` — vector/binary storage, multi-projection embeddings.

Non-goals: BM25F scoring internals, embedding model адаптеры, compaction job types (см. `compaction-roadmap.md`, future).

## 2. KnowledgeUnitKind (canonical enum)

Канонический enum фиксирован в `memory-stacks-roadmap.md` (см. ADR-001, ADR-004) и используется в `KnowledgeUnitEnvelope.kind` как дискриминатор per-kind semantics. Изменение значений или имён — breaking change.

```cpp
enum class KnowledgeUnitKind : uint16_t {
    Chunk = 0,
    QAPair = 1,
    Fact = 2,
    Event = 3,
    Entity = 4,
    Relation = 5,
    Summary = 6,
    CompiledArticle = 7,
    ConversationEpisode = 8,
    Note = 9,
    Task = 10,        // reserved
    Decision = 11,    // reserved
    Custom = 12,      // escape hatch через metadata_typed["payload"]
    // NUM_KINDS = 13
};
```

Ссылка на `memory-stacks-roadmap.md` секцию 6.3 для размещения `kind` в envelope.

### 2.1. Kind → Payload mapping

| Kind | Primary payload | Optional additional | Notes |
|---|---|---|---|
| Chunk | ChunkPayload | — | default kind для indexed retrieval |
| QAPair | QAPayload | — | short-circuit через QALookup slot |
| Fact | FactPayload | — | для temporal/bi-temporal knowledge |
| Event | (no specific payload) | Embedded в primary_text | lightweight, декларативный |
| Entity | (no specific payload) | Embedded в primary_text | name + type |
| Relation | (no specific payload) | Embedded в primary_text, graph edges | |
| Summary | (no specific payload) | Embedded в primary_text | компрессированное представление |
| CompiledArticle | CompiledArticlePayload | — | Karpathy-style wiki articles |
| ConversationEpisode | ConversationEpisodePayload | — | multi-utterance bundle |
| Note | (no specific payload) | Embedded в primary_text, `ResourceBodyStore` source | generic free-form / raw document entrypoint |
| Task | (reserved) | — | для future handoff structure |
| Decision | (reserved) | — | для future handoff structure |
| Custom | metadata_typed["payload"] | — | escape hatch через JSON-like value |

Примечание: для kinds без dedicated payload (Event, Entity, Relation, Summary, Note) основная информация содержится в `envelope.primary_text`/`display_text` и/или operational components (`SpeakerComponent`, `TemporalComponent` и т.д.).

### 2.2. Reserved kinds

`Task` (handoff records) и `Decision` (decision points в agent reasoning) зарезервированы для follow-up. Контракты будут определены, когда соответствующие use-cases материализуются (M2+).

### 2.3. Generic raw document units

Raw files do not have to arrive as curated cards. For `.md`, `.txt`,
extracted `.pdf`, transcript and log imports, the importer creates a minimal
generic unit when no stronger domain mapping is available:

- `kind = KnowledgeUnitKind::Note` for one document-level unit, or
  `kind = KnowledgeUnitKind::Chunk` when the importer materializes each chunk
  as a separate retrieval unit;
- `ResourceId` is derived from stable URI/path plus content hash or from an
  application-provided identity;
- `title` comes from metadata, H1, first meaningful heading, or filename;
- `trust_level` defaults to profile policy (`C`/`D`) unless supplied by source
  metadata;
- tags come from frontmatter/sidecar metadata when available;
- original language is detected when possible and stored on the payload or
  translation metadata;
- `SourceRef` points back to the raw resource and byte/text range;
- `SearchProjection::Original` is generated from extracted text immediately;
- `SearchProjection::TranslatedCanonical` may be generated later when the
  `TranslationProjection` capability is enabled;
- curated Facts/QAPairs/Summaries may be derived later by compaction or
  application normalizers.

This is intentionally different from a curated card. A generic raw document
unit is searchable and citeable, but carries weaker semantics. It should not be
forced to pretend to be `Fact`, `QAPair`, `CompiledArticle` or any other
curated kind until an explicit normalizer/extractor produces those units.

## 3. SourceRef (canonical contract)

`SourceRef` — first-class provenance для всех retrieval-eligible units. Обязателен для Fact, QAPair, Relation, Event, и любого GraphNode, доступного через expansion. Рекомендуется для Chunk и CompiledArticle.

В M0 contract разделяется на два слоя: `SourceRefSummary` (inline, ≤256 байт preview, всегда присутствует в envelope) и полная `SourceRef` (с `excerpt_text`, хранится в отдельной `source_refs` DBI, появляется в M1).

```cpp
struct TextRange {
    uint64_t byte_offset;
    uint64_t byte_length;
};

struct SourceRefSummary {
    ResourceId resource_id;          // обязательно
    std::string uri;                 // обязательно (для citations)
    TextRange excerpt;               // обязательно для quote-based refs
    std::array<uint8_t, 16> quote_hash;  // обязательно: SHA1(prefix(excerpt))[:16]
    double confidence;               // [0.0, 1.0]
    std::optional<KnowledgeUnitId> anchor_unit_id;  // связь на parent unit
    std::optional<uint64_t> observed_at_ms;
    std::string preview;             // inline excerpt, ≤256 байт
};

struct SourceRef {
    SourceRefSummary summary;        // обязательно: ссылка на inline summary
    std::string excerpt_text;        // полный текст цитаты (verbatim UTF-8)
};
```

### 3.1. Обязательные поля и инварианты

- `resource_id` — обязателен для reverse lookup по ресурсу.
- `uri` — обязателен для citation fidelity (`preview + uri` = stable short citation; `excerpt_text + uri` = stable full citation).
- `quote_hash` — обязателен: 16-byte hex of SHA1(`prefix(preview)`) для summary, либо SHA1(`prefix(excerpt_text)`) для полной SourceRef. Обеспечивает детерминированную идентификацию цитат без хранения полного текста в каждом индексе.
- `preview` (≤256 байт) — обязателен в summary; используется в UI, short citations, projection и не требует обращения к `source_refs` DBI.

### 3.2. Storage

**M0 (без `source_refs` DBI):**
- В envelope: `vector<SourceRefSummary>`, **максимум 3 summary на unit** (budget: ≤3 × ~256 байт ≈ 768 байт на envelope).
- Полные цитаты не хранятся; `preview` используется как для UI, так и для BM25F indexing (через search projection).

**M1 (добавление `source_refs` DBI):**
- В envelope остаётся `vector<SourceRefSummary>` (≤3).
- Полные `SourceRef` с `excerpt_text` хранятся в отдельной `source_refs` DBI (key = `KnowledgeUnitId` → `vector<SourceRef>`).
- Public write path: `WriteRequest::full_source_refs` writes full refs
  atomically with the unit when `enable_full_source_refs=true`. Migration/admin
  tools may also call `SourceRefStore::replace_for_unit(unit_id, refs, txn)`;
  this API replaces the entire vector for that unit and must be used in the
  same transaction as envelope summary updates when both change.
- Reverse lookup по `resource_id` строится через `metadata_filters` (DBI) — см. canonical physical manifest `mdbx-containers-extension-tz.md` §5.5.
- При необходимости быстрого reverse lookup добавляется отдельный DBI
  `source_refs_by_resource` (опциональный +1 profile delta; см.
  `mdbx-containers-extension-tz.md` §5.5.1).

Пример создания envelope с inline summary (M0):

```cpp
SourceRefSummary summary;
summary.resource_id = ResourceId::from_uri("https://docs.example.com/spec");
summary.uri = "https://docs.example.com/spec#section-3";
summary.excerpt = TextRange{/*byte_offset=*/1024, /*byte_length=*/512};
summary.preview = "first ~256 bytes of the excerpt...";
summary.quote_hash = compute_quote_hash(summary.preview);
summary.confidence = 0.92;

KnowledgeUnitEnvelopeBuilder env_builder;
// id is assigned by storage inside the write_unit/upsert transaction
env_builder.set_kind(KnowledgeUnitKind::Fact);
env_builder.set_scope(ScopeId::global());
env_builder.add_source_summary(summary);  // inline, ≤3 на unit
auto env = env_builder.build();
```

## 4. KnowledgeUnitId (monotonic scheme) и KnowledgeUnitKey

`KnowledgeUnitId` — opaque strong-typedef wrapper над `uint64_t`. Используется исключительно как **primary key** для storage layer и для cross-reference между units (anchor, supersedes, derived_from). Аллоцируется **монотонно**, **никогда не reused** после erase.

Content-addressing (дедупликация, миграция, idempotent upsert) выполняется через **отдельный** `KnowledgeUnitKey` (kind + scope + `ContentHash`). Это разделяет два разных понятия:

- **ID** = runtime identity, opaque handle для map key, cross-reference и supersedence chains. Меняется при erase/recreate. Monotonic uint64_t.
- **Key** = content-addressing handle, детерминированно вычисляется из payload. Один и тот же content → один и тот же key. Используется для dedupe (две записи одного контента), миграции и bulk import.

`KnowledgeUnitKey` is immutable for an existing `KnowledgeUnitId`: changing
`kind`, `scope` or content hash creates a new unit id and records
supersede/merge lineage instead of mutating the old id. Mutable updates may
change envelope metadata, lifecycle, summaries, components and projections, but
must preserve the original content-addressing key.

```cpp
class KnowledgeUnitId {
    uint64_t m_value;
public:
    uint64_t value() const noexcept;

    bool operator==(const KnowledgeUnitId& rhs) const noexcept {
        return m_value == rhs.m_value;
    }
    bool operator!=(const KnowledgeUnitId& rhs) const noexcept {
        return !(*this == rhs);
    }
    bool operator<(const KnowledgeUnitId& rhs) const noexcept {
        return m_value < rhs.m_value;
    }
};

class ContentHash {
    std::array<uint8_t, 16> m_bytes;
public:
    // NB: псевдокод; реальная имплементация принимает ByteView или const std::vector<uint8_t>&
    static ContentHash compute(const std::vector<uint8_t>& bytes);

    std::array<uint8_t, 16> bytes() const noexcept;

    bool operator==(const ContentHash& rhs) const noexcept {
        return m_bytes == rhs.m_bytes;
    }
    bool operator!=(const ContentHash& rhs) const noexcept {
        return !(*this == rhs);
    }
    bool operator<(const ContentHash& rhs) const noexcept {
        return m_bytes < rhs.m_bytes;  // lexicographic compare of std::array
    }
};

class KnowledgeUnitKey {
    KnowledgeUnitKind m_kind;
    ScopeId m_scope;
    ContentHash m_content_hash;
public:
    bool operator==(const KnowledgeUnitKey& rhs) const noexcept {
        return m_kind == rhs.m_kind &&
               m_scope == rhs.m_scope &&
               m_content_hash == rhs.m_content_hash;
    }
    bool operator!=(const KnowledgeUnitKey& rhs) const noexcept {
        return !(*this == rhs);
    }
    bool operator<(const KnowledgeUnitKey& rhs) const noexcept {
        return std::tie(m_kind, m_scope, m_content_hash) <
               std::tie(rhs.m_kind, rhs.m_scope, rhs.m_content_hash);
    }
};
```

`ContentHash` stored in `KnowledgeUnitEnvelope` is computed with a versioned,
kind-specific `ContentHashRecipeVersion`. Recipe input is canonical bytes, not
the current physical file path or importer-local location:

- `Chunk`: `ResourceBody` digest, chunk span/offset identity and normalized
  chunk text digest. If raw body storage is external, the stable body digest
  participates in identity; transient source path does not.
- `QAPair`: normalized question/answer payload fields and declared identity
  metadata.
- `Fact`: canonical subject/predicate/object/time identity.
- `ConversationEpisode`, `CompiledArticle`, `Note`: canonical payload text or a
  stable `ResourceBody` digest for large bodies.
- `Event`, `Entity`, `Relation`, `Summary`, `Custom`: canonical primary text
  plus declared identity metadata/payload bytes.

Changing recipe version without a migration preserving the old stored hash
creates a new `KnowledgeUnitId` plus supersede/merge lineage.

The normative computation entrypoint is:

```cpp
struct ContentHashResult {
    ContentHash hash;
    uint16_t recipe_version;
};

struct CanonicalIdentityInput {
    KnowledgeUnitKind kind;
    ScopeId scope;
    std::optional<ResourceDigest> resource_body_digest;
    std::optional<ChunkSpan> chunk_span;
    std::optional<ContentDigest> normalized_chunk_text_digest;
    std::optional<QAPairIdentity> qa_pair;
    std::optional<FactIdentity> fact;
    std::optional<TextBodyIdentity> text_body;
    std::optional<DeclaredMetadataIdentity> declared_metadata;
};

ContentHashResult compute_content_hash(
    KnowledgeUnitKind kind,
    const CanonicalIdentityInput& input);
```

`compute_content_hash` owns the algorithm/domain prefix, field order,
normalization and truncation rule. `kind` argument and `input.kind` MUST match.
Current recipe: encode `agent-memory.content-hash.v1`, `kind`, `scope`, then
recipe-defined identity fields; compute SHA-256 over those canonical bytes;
store the first 16 bytes as `ContentHash`. Legacy
`<kind>:<scope>:<sha256(content)>` databases migrate by decoding the 32-byte
SHA-256 from the old id, keeping the leftmost 16 bytes as `ContentHash`, and
writing `content_hash_recipe_version = 0` before any v1 re-hash migration.

Canonical wire grammar v1:

```text
stream        = domain kind scope fields
domain        = u16be byte_len + UTF-8 bytes "agent-memory.content-hash.v1"
kind          = u16be stable KnowledgeUnitKind value
scope         = u32be byte_len + normalized UTF-8 bytes
fields        = every field tag from the per-kind table, exactly once,
                strictly in the table order
field         = u16be field_tag + u8 presence + u32be byte_len + payload?
presence      = 0 absent, 1 present-empty, 2 present-non-empty
integer       = fixed-width big-endian two's-complement or unsigned
timestamp     = i64be milliseconds since Unix epoch
digest        = raw bytes with fixed length declared by the field tag
string        = UTF-8 NFC bytes after recipe normalization
list/map      = u32be item_count + encoded items; maps sorted by encoded key
```

Absent optional and present-empty optional are distinct through `presence`:

- absent: `presence = 0`, `byte_len = 0`, payload absent;
- present-empty: `presence = 1`, `byte_len = 0`, payload absent;
- present-non-empty: `presence = 2`, `byte_len > 0`, payload present.

Duplicate, unknown or out-of-order field tags are encoding errors. Skipping an
absent field tag is also an encoding error; every tag in the per-kind table is
emitted exactly once. For fixed-width integers and digests, `byte_len` MUST
match the field's declared width. For maps, keys are normalized before sorting;
if two keys become equal after normalization, encoding fails instead of picking
one value. Concatenating raw strings is forbidden; all fields use tag +
presence + length framing.

Stable field-tag order:

| Kind | Field tags in order |
|---|---|
| `Chunk` | `0x0001 resource_body_digest`, `0x0002 span_start`, `0x0003 span_end`, `0x0004 normalized_chunk_text_digest` |
| `QAPair` | `0x0101 normalized_question`, `0x0102 normalized_answer`, `0x0103 identity_metadata_map` |
| `Fact` | `0x0201 subject`, `0x0202 predicate`, `0x0203 object`, `0x0204 valid_time`, `0x0205 observed_time` |
| `ConversationEpisode`, `CompiledArticle`, `Note` | `0x0301 canonical_text_digest`, `0x0302 resource_body_digest`, `0x0303 title_identity` |
| `Event`, `Entity`, `Relation`, `Summary`, `Custom` | `0x0401 primary_identity_text`, `0x0402 declared_identity_metadata_map`, `0x0403 declared_payload_digest` |

Text normalization table for recipe v1:

| Field family | Normalization |
|---|---|
| `normalized_question` | UTF-8 decode, Unicode NFC, CRLF/CR -> LF, trim outer Unicode whitespace, collapse internal Unicode whitespace runs to one ASCII space, case preserved |
| `normalized_answer` | UTF-8 decode, Unicode NFC, CRLF/CR -> LF, trim outer Unicode whitespace, preserve internal whitespace except line-ending normalization, case preserved |
| `subject`, `predicate`, `object` | UTF-8 decode, Unicode NFC, CRLF/CR -> LF, trim outer Unicode whitespace, collapse internal Unicode whitespace runs to one ASCII space, case preserved |
| `canonical_text_digest` source text | UTF-8 decode, Unicode NFC, CRLF/CR -> LF, trim trailing whitespace on each line, trim outer blank lines, case preserved, then digest the normalized bytes |
| `primary_identity_text` | UTF-8 decode, Unicode NFC, CRLF/CR -> LF, trim outer Unicode whitespace, collapse internal Unicode whitespace runs to one ASCII space, case preserved |
| metadata keys | UTF-8 decode, Unicode NFC, CRLF/CR -> LF, trim outer whitespace, ASCII lower-case for `[A-Z]`, collapse internal whitespace runs to one ASCII space |
| metadata string values | UTF-8 decode, Unicode NFC, CRLF/CR -> LF, trim outer whitespace, preserve case |
| tags used as identity metadata | same as metadata string values; tag identity is not translated |
| digests, timestamps, integer spans | no text normalization; encode fixed-width binary payloads |

Per-kind typed encoders build these fields inside the library. Callers provide
typed identity inputs or authoritative body digests, not opaque canonical byte
buffers. Golden vectors are part of the implementation gate, not optional test
coverage: for every kind family the repository must contain typed logical input,
exact canonical byte stream in hex, full SHA-256 and stored 16-byte
`ContentHash`. The minimum vector set covers every kind family in the field-tag
table, absent vs present-empty vs present-non-empty, Unicode NFC/NFD
equivalence, duplicate metadata keys after normalization, boundary
integer/timestamp values, legacy recipe v0 and C++11/C++17 cross-platform
equality. Implementations must refuse to enable v1 dedupe migration until those
vectors pass.

Identity vs mutable fields:

| Kind | Identity fields | Mutable fields (`revision++`) |
|---|---|---|
| `Chunk` | `resource_body_digest`, span/offset identity, normalized chunk text digest | display text, source summaries, retrieval projections, lifecycle |
| `QAPair` | normalized question/answer identity fields | display text, source summaries, usage/ranking metadata, projections, lifecycle |
| `Fact` | subject/predicate/object/time identity | confidence, display text, non-identity source summaries, projections, lifecycle |
| `ConversationEpisode`, `CompiledArticle`, `Note` | canonical payload text or stable `ResourceBody` digest | title/display summaries, source summaries, projections, lifecycle |
| `Event`, `Entity`, `Relation`, `Summary`, `Custom` | canonical primary identity text plus declared identity metadata/payload bytes | display summaries, non-identity metadata, projections, lifecycle |

### 4.1. Allocation и инварианты

- `KnowledgeUnitId` allocation is storage-owned. MDBX-backed storage obtains it
  from `knowledge_unit_sequence.next(txn)` inside the same write transaction
  that installs the content-key mapping. There is no public
  static allocation API in the canonical path.
- Reuse id после erase запрещён (даже если соответствующий unit удалён).
- `KnowledgeUnitId` opaque для storage layer; не парсится ни во что.
- При экспорте в логи/traces допускается hex-форма `to_string()` (32 hex chars), но не должно влиять на routing.
- Strong typedef: нет implicit conversion в `std::string`, `ChunkId` или арифметические типы.

### 4.2. DBI mapping

Storage использует **два** DBI для разделения identity и content:

| DBI | Key | Value | Назначение |
|---|---|---|---|
| `knowledge_units` | `KnowledgeUnitId` | `KnowledgeUnitEnvelope` | primary storage; O(1) lookup по id |
| `content_key_to_unit_id` | `KnowledgeUnitKey` | `KnowledgeUnitId` | dedupe/migration; O(1) "есть ли уже unit с таким content?" |

Идемпотентный upsert через `content_key_to_unit_id`:

```cpp
return connection.multi_write([&](Transaction& txn) -> UpsertResult {
    // 1. Build canonical identity input from authoritative fields/stores using
    // the same transaction snapshot as the later dedupe and write.
    CanonicalIdentityInput input =
        build_identity_input(unit, resource_body_store, txn);
    ContentHashResult identity = compute_content_hash(unit.kind, input);

    unit.envelope.content_hash = identity.hash;
    unit.envelope.content_hash_recipe_version = identity.recipe_version;

    KnowledgeUnitKey key{
        unit.kind,
        unit.scope,
        identity.hash
    };

    // 2. Dedupe check and no-overwrite insert are both inside the write txn.
    if (auto existing = content_key_to_unit_id.find(key, txn)) {
        return UpsertResult::Existed{*existing};
    }

    auto new_id = knowledge_unit_sequence.next(txn);
    unit.id = new_id;
    unit.envelope.id = new_id;

    auto cas = content_key_to_unit_id.insert_if_absent(key, new_id, txn);
    if (!cas.inserted) {
        return UpsertResult::Existed{
            *cas.existing_value
        };
    }

    knowledge_units.put(new_id, unit.envelope, txn);
    write_payload_and_indexes(unit, txn);
    return UpsertResult::Inserted{new_id};
});
```

All authoritative reads used to build `CanonicalIdentityInput` MUST belong to the
same transaction snapshot. MDBX-backed `ResourceBodyStore` reads receive `txn`.
External body stores must provide an immutable digest/version token, and
`write_unit` verifies that token before commit; otherwise the body is not valid
identity input for idempotent upsert.

### 4.3. Миграция с hash-based scheme

Переход от старого `<kind>:<scope>:<hash>` (где hash = SHA256(content)) к monotonic-uint64 — **breaking change**. При наличии существующих БД требуется migration script:

1. Прочитать все envelope из старой БД.
2. Для каждого envelope:
   - Прочитать `(kind, scope, content_hash, content_hash_recipe_version)` из
     envelope. Для legacy DB без persisted hash выполнить одноразовый recompute
     по legacy recipe и записать hash в envelope перед включением immutable-key
     enforcement.
   - Аллоцировать новый monotonic `KnowledgeUnitId`.
   - Перезаписать envelope с новым id в `knowledge_units`.
   - Записать `KnowledgeUnitKey → KnowledgeUnitId` в `content_key_to_unit_id`.
3. Обновить все cross-reference (`anchor_unit_id`, `superseded_by`, `derived_from`) — старые ID заменяются на новые через lookup-таблицу.
4. Перестроить `inverted_token_to_unit`, `field_to_postings`, secondary indexes.

Migration запускается отдельной утилитой (`agent-memory-cli migrate-ku-id-scheme`), не часть core API. Round-trip test обязателен.

`KnowledgeUnitId` обязателен для map key, `envelope.id`, `projection.unit_id`, `RetrievalHit.unit_id` и любых cross-reference (anchor_unit_id, superseded_by, derived_from).

See [`usage-llm-wiki.md`](usage-llm-wiki.md) for how KnowledgeUnit storage can back an LLM Wiki's `raw/` and `wiki/` partitions.

## 5. Per-Kind Specifications

Детальная спецификация payload-компонентов для каждого kind с dedicated payload. Per-kind `primary_text` rules и projections.

### 5.1. ChunkPayload

See [`chunkers-roadmap.md`](chunkers-roadmap.md) for format-specialized chunker patterns: OpenAPI (resolves `$ref`), Markdown (heading tree + parent chain), AsciiDoc, PlantUML, HTML→MD, Legal-strukturalnyy (по «Статьям»), Docling multimodal, Smart3D-style decompiled-code entity cards. Plus contextual chunking (Anthropic) and two-stage indexing (chunks + synthetic questions).

Payload для `KnowledgeUnitKind::Chunk`. Chunk — default kind для indexed retrieval.

```cpp
struct ChunkPayload {
    uint64_t byte_offset;
    uint64_t byte_length;
    std::vector<HeadingPathItem> heading_path;  // ["Section 1", "Subsection 2", ...]
    std::vector<std::string> code_blocks;       // detected code fences
    std::vector<std::string> symbols;           // extracted identifiers
    std::optional<std::string> detected_language;
};
```

#### 5.1.0. Explicit construction (без designated initializers)

```cpp
ChunkPayload chunk;
chunk.byte_offset = 4096;
chunk.byte_length = 1024;
chunk.heading_path = {HeadingPathItem{"Chapter 1"}, HeadingPathItem{"Section 1.2"}};
chunk.code_blocks = {std::string{"void foo() { return; }"}};
chunk.symbols = {std::string{"foo"}, std::string{"bar"}};
chunk.detected_language = std::string{"cpp"};
```

#### 5.1.1. Per-kind primary_text generation rule

`[H1] > [H2] > first 500 chars of body`. Если `heading_path` пуст — только first 500 chars body. Generation выполняется в `generate_primary_text` (см. `knowledge-base-roadmap.md` секция 3.3).

#### 5.1.2. Per-kind projections

- Original: full body text.
- CodeSymbols: extracted identifiers через code-aware tokenizer (если Chunk содержит code).

#### 5.1.3. Storage

DBI `chunk_payloads` (key = UnitId) is canonical always-open. `ChunkPayload`
всегда присутствует для Chunk kind (в отличие от optional payloads для других
kinds). Write validation requires it when `kind == Chunk`; it does not require a
capability flag and is not created dynamically on first write.

### 5.2. QAPayload

Payload для `KnowledgeUnitKind::QAPair`. Используется в QAKnowledgeBase stack и FullResearch stack.

Контракт разделён на два уровня зрелости: M0 (минимальный, достаточный для QAKnowledgeBaseStack через QALookup slot) и M1+ (расширенный, с variants, frequency ranking и форматом ответа).

#### 5.2.1. QAPayload минимальный (M0)

Минимальный QAPayload для QAKnowledgeBaseStack:

```cpp
struct QAPayload {
    std::string canonical_question;
    std::string answer;
    std::optional<std::string> category;
    std::optional<uint64_t> last_verified_at_ms;
};
```

Достаточно для прямого QA matching через QALookup slot и basic storage. Без variants, без frequency ranking, без temporal validity.

#### 5.2.2. QAPayload расширенный (M1+)

Полный QAPayload для advanced use-cases:

```cpp
struct QAPayload {
    std::string canonical_question;
    std::vector<std::string> question_variants;  // aliases для matching
    std::string answer;
    std::string category;
    uint64_t last_verified_at_ms;
    uint64_t frequency = 0;                      // для ranking (UsageStats integration)
    std::optional<std::string> expected_format;  // "text", "json", "code"
};
```

M1 добавляет поверх M0:
- `question_variants` (regex/keyword matching для short-circuit QA lookup).
- `frequency` (через `UsageStatsComponent` integration для ranking).
- `expected_format` (для structured output agents — "text"/"json"/"code").
- Temporal validity (через `TemporalComponent`).

#### 5.2.3. Explicit construction (без designated initializers)

```cpp
QAPayload qa;
qa.canonical_question = "How does the KnowledgeUnitId allocator work?";
qa.question_variants = {"KU id allocation", "monotonic unit id"};
qa.answer = "KnowledgeUnitId is allocated from the storage sequence inside the write transaction ...";
qa.category = "architecture";
qa.last_verified_at_ms = 1700000000000;
qa.frequency = 0;
qa.expected_format = std::string{"text"};
```

#### 5.2.4. Per-kind primary_text

`canonical_question` + первые 200 chars из `answer` как preview. Полный текст хранится в `qa_payloads` DBI, retrieval работает через `SearchProjection`s.

#### 5.2.5. Per-kind projections

- Original: question + answer (full).
- QAQuestion: только `canonical_question` + variants (для matching).
- QAAnswer: только `answer` (для retrieval когда question уже matched).

#### 5.2.6. Storage

DBI `qa_payloads`. Включается через `enable_qa_payload=true`.

#### 5.2.7. Retrieval shortcut

QALookup slot — прямой lookup по `canonical_question` + variants до BM25F, для high-precision коротких запросов. Используется `QARetriever` (см. `knowledge-base-roadmap.md` секция 7.2).

### 5.3. FactPayload

Payload для `KnowledgeUnitKind::Fact`. Используется в AgentLongTermMemory, TemporalFactStore, CompiledWiki и FullResearch stacks.

```cpp
struct FactPayload {
    std::string subject;
    std::string predicate;
    std::string object;
    std::string value_type;            // "string", "number", "date", "bool"
    std::optional<std::string> unit;   // "USD", "kg", "Celsius", ...
    std::vector<std::string> aliases;  // alternative formulations
};
```

#### 5.3.0. Explicit construction (без designated initializers)

```cpp
FactPayload fact;
fact.subject = "Einstein";
fact.predicate = "born_in";
fact.object = "Ulm";
fact.value_type = "string";
fact.unit = std::nullopt;
fact.aliases = {"Albert Einstein", "Альберт Эйнштейн"};
```

#### 5.3.1. Per-kind primary_text

`<subject> <predicate> <object> [<unit>]`. Пример: `Einstein born_in Ulm`. С aliases — через пробел: `Einstein born_in Ulm | Альберт Эйнштейн`.

#### 5.3.2. Per-kind projections

- Original: `"<subject> <predicate> <object> [<unit>]"`.

#### 5.3.3. Storage

DBI `fact_payloads`. Включается через `enable_fact_payload=true`.

#### 5.3.4. Temporal bi-temporal

Facts используют `TemporalComponent.valid_from_ms` / `valid_until_ms` для supersedence chains. Устаревший fact помечается `Superseded` через Lifecycle FSM; новый становится `Active`. Retrieval возвращает только `Active` (если не указан bi-temporal query).

### 5.4. EventPayload (опциональный)

Для `KnowledgeUnitKind::Event` dedicated payload не обязателен — обычно достаточно `TemporalComponent` + `primary_text`. Если use-case требует structured event data, добавляется:

```cpp
struct EventPayload {
    std::vector<std::string> actors;
    std::string action;
    std::optional<std::string> location;
    uint64_t occurred_at_ms;          // когда произошло (≠ observed_at_ms)
    uint64_t observed_at_ms;          // когда записан в memory
};
```

Примечание: в M1 Event обходится без dedicated payload, используя `TemporalComponent` + `envelope.primary_text`. Если payload нужен — включается аналогично FactPayload, через `enable_event_payload` (M2+).

### 5.5. ConversationEpisodePayload

Payload для `KnowledgeUnitKind::ConversationEpisode`. Используется в SpeakerAwareChatStack и FullResearch stack.

```cpp
struct ConversationEpisodePayload {
    std::vector<UtteranceId> utterance_ids;
    uint64_t started_at_ms;
    uint64_t ended_at_ms;
    uint64_t turn_count;
    std::vector<SpeakerId> participants;
    std::optional<std::string> topic;
    std::optional<UnitId> previous_episode_id;  // chain в stream
};
```

#### 5.5.1. Per-kind primary_text

Первые 1-2 реплики (utterance excerpts) как seed для retrieval. Полный текст доступен через `episode_payloads` DBI.

#### 5.5.2. Per-kind projections

- Original: concat всех utterances до max 4000 chars (для context).

#### 5.5.3. Storage

DBI `conversation_episode_payloads`. Включается через `enable_conversation_episode=true`. Требует `SpeakerAttribution` capability (см. validation rules в `memory-stacks-roadmap.md` секция 10).

#### 5.5.4. Compaction

`episode_compaction` job (см. `compaction-roadmap.md`, future) сливает N соседних episodes в один SuperEpisode при превышении context budget или по retention policy.

### 5.6. CompiledArticlePayload

Payload для `KnowledgeUnitKind::CompiledArticle`. Используется в CompiledWikiStack и FullResearch stack.

```cpp
struct CompiledArticlePayload {
    std::string title;
    std::string owner;
    std::vector<std::string> readers;
    std::vector<std::string> keywords;
    std::string status;                       // "draft", "published", "archived"
    uint64_t last_compiled_at_ms;
    std::vector<UnitId> derived_from;         // source units
    uint64_t injection_count = 0;
    uint64_t last_injected_at_ms = 0;
};
```

#### 5.6.1. Per-kind primary_text

`title` + first paragraph (до 500 chars).

#### 5.6.2. Per-kind projections

- Original: full markdown body.
- Summary: first 1000 chars + status.

#### 5.6.3. Storage

DBI `compiled_article_payloads`. Включается через `enable_compiled_article=true`. Требует `enable_compaction=true` (для `SummaryPromotionJob`).

#### 5.6.4. Lifecycle

`status` field управляет lifecycle transitions: `draft` → `published` → `archived`. `archived` соответствует `Deprecated` lifecycle state; retrieval фильтрует по умолчанию.

### 5.7. Reserved Kinds (Task, Decision)

```cpp
// TODO: определить структуру в M2.
// Сейчас зарезервированы для follow-up:
// - Task: handoff records (operational handoff structure)
// - Decision: ссылки на decision points в agent reasoning
```

Контракты будут определены, когда соответствующие use-cases материализуются (M2+).

`Decision` also becomes relevant for affective-agent memory once action,
coping, outcome, and prediction-error payloads stabilize. See
[`affective-memory-roadmap.md`](affective-memory-roadmap.md) for the optional
`ActionOutcomeComponent` direction; it is not part of the base KnowledgeUnit
contract yet.

### 5.8. Custom (escape hatch)

```cpp
// Custom unit: нет dedicated payload, всё через:
// - envelope.primary_text / display_text
// - metadata_typed["payload"] (typed JSON-like value)
// - components[] (любые operational components)
```

Используется для экспериментальных типов до добавления dedicated payload. Custom unit обязан нести `KnowledgeUnitId`, `KnowledgeUnitKind`, `SourceRef[]`, lifecycle fields и проходит стандартные validation rules. Custom payload хранится в `metadata_typed["payload"]` через typed value (variant).

The affective-memory roadmap uses this `Custom` escape hatch for E0
experiments with appraisal, affect snapshots, goal impacts, action outcomes,
salience, and relationship evidence before any dedicated component schema is
frozen.

## 6. Lifecycle FSM

Lifecycle FSM определён в `memory-stacks-roadmap.md` ADR-011 и расширен здесь детальной семантикой durable transitions и anti-loop подсвинка (через `UsageStatsComponent`).

### 6.1. States

Lifecycle FSM имеет **4 durable states**. `SoftSuppressed` НЕ является LifecycleState — это runtime/usage state, хранится в `UsageStatsComponent.cooldown_until_ms` / `soft_suppression_until_ms`.

```cpp
enum class LifecycleState : uint8_t {
    Active = 0,       // active, retrieval-eligible
    Superseded = 1,   // заменён другим unit (bi-temporal chain)
    Deprecated = 2,   // помечен на удаление (compaction candidate)
    Erased = 3,       // физически удалён (или logical erase)
    // SoftSuppressed УДАЛЁН — это runtime state в UsageStatsComponent, не durable lifecycle.
};
```

### 6.2. Transitions

Только **durable transitions**. Anti-loop / cooldown / soft_suppression — runtime state в `UsageStatsComponent`, не FSM transitions (см. §6.4).

| From | To | Trigger | Action |
|---|---|---|---|
| (new) | Active | write | initial state |
| Active | Superseded | supersede operation (newer unit supersedes older) | старый unit помечается Superseded, новый становится Active |
| Active | Deprecated | manual deprecation | авторский сигнал через WriteRequest |
| Active | Erased | manual erase | физическое удаление или logical remove (single-shot, минует Deprecated) |
| Superseded | Deprecated | compaction review | после N дней в Superseded |
| Superseded | Erased | compaction cleanup | cleanup Superseded chains (без Deprecated stage) |
| Deprecated | Erased | compaction job | физическое удаление или logical remove |

Durable lifecycle transitions инкрементят `envelope.revision` (см. `memory-stacks-roadmap.md` §16 Step 1.5, §18 Glossary).

### 6.3. Retrieval semantics

- **Active** + `usage.cooldown_until_ms <= now_ms` + не в `soft_suppression_until_ms`: применяется full scoring (BM25F + decay + boost). Участвует в RRF fusion с полным весом.
- **Active** + `usage.cooldown_until_ms > now_ms`: filtered out или score умножается на `cooldown_factor` (через `AntiLoopCooldown` filter) — runtime adjustment, не FSM transition.
- **Superseded**: filtered out by default, доступен только через явный filter (bi-temporal query).
- **Deprecated**: filtered out, доступен только через audit dump.
- **Erased**: не возвращается ни в одном retrieval; id не reuse'ится.

`SoftSuppressed` НЕ является retrieval-level state — это runtime attribute в `UsageStatsComponent`. Cooldown/soft_suppression проверяется через `UsageStatsComponent.cooldown_until_ms` / `soft_suppression_until_ms` в `DecayAwareRetriever` и `AntiLoopCooldown` filter.

### 6.4. Anti-loop подсвинок (runtime, не lifecycle)

Anti-loop реализуется через runtime state в `UsageStatsComponent`, НЕ через `LifecycleState`:

```cpp
struct UsageStatsComponent {
    uint64_t use_count = 0;
    uint64_t last_used_at_ms = 0;
    uint64_t last_injected_at_ms = 0;
    uint64_t injection_count = 0;

    // Anti-loop runtime state (NOT content-bearing, no revision++)
    uint64_t cooldown_until_ms = 0;
    // Optional: длинная soft-suppression при высокой частоте использования. M2+ feature.
    uint64_t soft_suppression_until_ms = 0;
};
```

Ключевые инварианты:

- `cooldown_until_ms` / `soft_suppression_until_ms` — runtime/usage state, **НЕ** `LifecycleState` value.
- Изменения этих полей **НЕ инкрементят** `envelope.revision`. Постинги/эмбеддинги остаются валидными.
- Anti-loop реализуется в `DecayAwareRetriever` + `AntiLoopCooldown` filter (см. `memory-stacks-roadmap.md` §16 Step 10).

### 6.4.1. DecayAwareRetriever + AntiLoopCooldown filter

```cpp
// В DecayAwareRetriever:
double apply_decay_and_boost(
    double base_score,
    const UsageStatsComponent& usage,
    const DecayPolicy& policy,
    uint64_t now_ms,
    uint64_t last_used_at_ms) {
    double elapsed = double(now_ms - last_used_at_ms);
    double factor = std::exp(-elapsed / policy.half_life_ms);
    return base_score * factor + policy.use_boost * std::log1p(double(usage.use_count));
}

// В AntiLoopCooldown filter (применяется ПОСЛЕ apply_decay_and_boost):
double apply_filters(
    double score,
    const UsageStatsComponent& usage,
    const SpeakerComponent* speaker,
    SpeakerId agent_self_id,
    uint64_t now_ms,
    const DecayPolicy& policy) {
    if (usage.cooldown_until_ms > now_ms) {
        return score * policy.cooldown_factor;        // default 0.1
    }
    if (usage.soft_suppression_until_ms > now_ms) {
        return score * policy.self_echo_suppression;  // default 0.3
    }
    if (speaker && speaker->speaker_id == agent_self_id) {
        return score * policy.self_echo_suppression;  // default 0.3
    }
    return score;
}
```

### 6.4.2. После успешного retrieval

```cpp
// В post-retrieval hook (НЕ FSM transition):
usage.cooldown_until_ms = now_ms + policy.cooldown_ms;
usage.use_count += 1;
usage.last_used_at_ms = now_ms;
// LifecycleState НЕ меняется.
// envelope.revision НЕ инкрементится.
// Постинги/эмбеддинги остаются валидными.
```

Подсвинок активируется при `SpeakerComponent.speaker_id == agent_self_id` (см. `memory-stacks-roadmap.md` ADR-008).

### 6.4.3. Миграция с FSM SoftSuppressed

- Раньше: `LifecycleState::SoftSuppressed` value, изменение FSM state → `revision++` → stale postings/embeddings → система не стабилизируется.
- Теперь: `UsageStatsComponent.cooldown_until_ms` / `soft_suppression_until_ms` — runtime state, изменения не инкрементят revision. Postings/embeddings остаются валидными.
- Миграция: при `open_existing()` все units с `lifecycle_state == SoftSuppressed` (legacy) → `lifecycle_state = Active` + `usage.cooldown_until_ms = max(now_ms, legacy_soft_suppression_until_ms)`. После миграции FSM имеет только 4 durable states.

## 7. Manifest Integration

### 7.1. DerivedRecordKind

Enum расширяется новыми значениями для component-уровня записей:

```cpp
enum class DerivedRecordKind : uint32_t {
    // Existing kinds ...
    KnowledgeUnitEnvelope = 100,
    UsageStatsComponent = 101,
    SpeakerComponent = 102,
    TemporalComponent = 103,
    EmbeddingMetaComponent = 104,
    CompactionMetaComponent = 105,
    TranslationMetaComponent = 106,
    QAPayload = 110,
    FactPayload = 111,
    ChunkPayload = 112,
    ConversationEpisodePayload = 113,
    CompiledArticlePayload = 114,
    SearchProjection = 120,
    KnowledgeUnitKey = 121,
    SourceRefSummary = 122,
    SourceRef = 123,
    // NB: KnowledgeUnitRevision НЕ существует как отдельный manifest record —
    // revision — это поле KnowledgeUnitEnvelope.revision, не отдельный kind.
    // Per-record stale-check живёт в LexicalPosting.unit_revision и
    // EmbeddingMetaComponent.unit_revision_at_compute.
};
```

Значения 100-123 зарезервированы для новых типов. Расширение additive; старые manifests остаются валидными.

### 7.2. Resource Manifest Records

При write unit в `ResourceManifest` добавляются:

- 1 запись для envelope (`DerivedRecordKind::KnowledgeUnitEnvelope`).
- N записей для payload-компонентов (если payload задан для kind).
- M записей для operational components (`UsageStatsComponent`, `SpeakerComponent` и т.д.).
- P записей для `SearchProjection`s (по одной на projection_kind).

Это позволяет отслеживать происхождение каждого слоя через manifest и обеспечивает auditability при supersedence/compaction.

## 8. Storage Pattern

### 8.1. Per-Component DBI Allocation

| Component / Payload | DBI имя | Open when |
|---|---|---|
| KnowledgeUnitEnvelope | `knowledge_units` | always |
| KnowledgeUnit by kind | `knowledge_units_by_kind` | always (scope-aware DUPSORT) |
| UsageStatsComponent | `unit_components` (tag=UsageStats) | UsageStats=true |
| SpeakerComponent | `unit_components` (tag=Speaker) | SpeakerAttribution=true |
| TemporalComponent | `unit_components` (tag=Temporal) | TemporalValidity=true |
| EmbeddingMetaComponent | `unit_components` (tag=EmbeddingMeta) | EmbeddingMeta=true |
| CompactionMetaComponent | `unit_components` (tag=CompactionMeta) | Compaction=true |
| TranslationMetaComponent | `unit_components` (tag=TranslationMeta) | TranslationProjection=true |
| QAPayload | `qa_payloads` | QAPairs=true (enable_qa_payload) |
| FactPayload | `fact_payloads` | enable_fact_payload=true |
| ChunkPayload | `chunk_payloads` | Chunk kind (default, всегда) |
| ConversationEpisodePayload | `conversation_episode_payloads` | ConversationMemory=true |
| CompiledArticlePayload | `compiled_article_payloads` | CompiledArticles=true |
| Full SourceRef vector | `source_refs` | `enable_full_source_refs=true` (M1) |
| SearchProjections | `unit_projections` | indexed retrieval (always для BasicRag+) |

Operational components живут в единой `unit_components` DBI через `TypeDiscriminatedTable` (из `mdbx-containers-extension-tz.md`). Per-kind payloads — отдельные таблицы для изоляции schema и быстрого scan по kind.

### 8.2. Capability-aware DBI Creation

При `MemoryStack::open(spec)`:

1. Core/default DBI берутся из canonical manifest
   `mdbx-containers-extension-tz.md` §5.5. Для knowledge-unit identity path это
   включает `knowledge_units`, `content_key_to_unit_id`,
   `knowledge_units_by_kind`, `unit_components`, `unit_projections`,
   `metadata_filters` и `schema_info`; capability DBI вроде `source_refs`
   открываются по профилю.
2. По capability флагам создаются дополнительные DBI (см. таблицу 8.1).
3. При drift detected (см. `memory-stacks-roadmap.md` секция 14): error или auto-migrate (per ADR-003, ADR-004).

DBI budget target ≤ 64 (расширение `max_dbs` 16→64 в `mdbx-containers`). Typical M1 stack: 18-22 DBI. Headroom есть.

### 8.3. Multi-Component Writes

При write unit с envelope + N components + M projections:

- Одна транзакция через `MultiTableWriter` (из `mdbx-containers-extension-tz.md`).
- Atomic commit: либо всё записано, либо ничего.
- Sequence: envelope → components → projections → secondary indexes → manifest records.

## 9. Migration Plan (от старого KnowledgeUnit к новой модели)

### 9.1. Breaking Refactor Strategy

В рамках v0.x — допустим breaking refactor (per ADR-004 в `memory-stacks-roadmap.md`):

1. Старый монолитный `KnowledgeUnit` struct удаляется.
2. Создаётся `KnowledgeUnitEnvelope` + Components.
3. Все references обновляются на envelope-centric API.

Если уже есть старые базы — отдельный migration script (`agent-memory-cli migrate-monolithic-ku`), не часть core API.

### 9.2. SourceRef Schema Migration (inline → separate DBI)

**M0 → M1 migration:** переход от inline `SourceRef[]` в envelope к разделению `SourceRefSummary` (inline, ≤3 на unit) + полная `SourceRef` в `source_refs` DBI.

```cpp
// Старый SourceRef → новый SourceRefSummary + SourceRef
struct OldSourceRef {
    std::string uri;
    std::string excerpt;            // полный excerpt (verbatim UTF-8)
    uint64_t byte_offset;
    uint64_t byte_length;
    std::optional<std::string> quote_hash;
};

// Mapping:
// - uri → SourceRefSummary.uri (kept)
// - byte_offset + byte_length → SourceRefSummary.excerpt (TextRange)
// - excerpt (first ≤256 байт) → SourceRefSummary.preview
// - excerpt (full) → SourceRef.excerpt_text (в source_refs DBI)
// - quote_hash (optional) → SourceRefSummary.quote_hash (required, вычисляется из preview если missing)
// - ResourceId добавляется через reverse lookup по uri
// - anchor_unit_id добавляется (default = nullopt)
// - observed_at_ms добавляется (default = nullopt)
```

Migration strategy:

1. Для каждого envelope: прочитать `sources: SourceRef[]`.
2. Для каждой SourceRef:
   - Создать `SourceRefSummary`: `preview = excerpt.substr(0, 256)`, остальные поля маппятся 1:1.
   - Создать полную `SourceRef` с `summary = <Summary>` и `excerpt_text = excerpt`.
3. Если unit имеет > 3 source refs: top-3 (по `confidence`) оставить как inline `SourceRefSummary`, остальные — только в `source_refs` DBI (полная версия).
4. Заменить `envelope.sources` на `vector<SourceRefSummary>` (≤3).
5. Записать все полные `SourceRef` в `source_refs` DBI под тем же `KnowledgeUnitId`.
6. Перестроить `metadata_filters` index по `resource_id` если требуется.

**M0 → M1 migration запускается отдельной утилитой** (`agent-memory-cli migrate-sourceref-inline-to-dbi`), не часть core API. Round-trip test обязателен: read all units, dump `source_refs` DBI, verify byte-for-byte equality excerpts.

### 9.3. KnowledgeUnitId Migration (hash-based → monotonic)

> **Breaking change:** KnowledgeUnitId migration от контент-хэша (`<kind>:<scope>:<hash>`) к монотонному `uint64_t` — breaking change, требует migration script для всех существующих БД.

Детальная процедура описана в секции 4.3 (DBI mapping → migration). Краткое summary:

1. Построить lookup-таблицу `old_id → new_id` через чтение всех envelope и
   persisted `KnowledgeUnitKey` (`kind`, `scope`, stored `content_hash`,
   `content_hash_recipe_version`). Для legacy DB без persisted hash сначала
   выполнить одноразовый recompute/backfill по legacy recipe.
2. Аллоцировать новый monotonic `KnowledgeUnitId` для каждого уникального key.
3. Перезаписать envelope с новым id в `knowledge_units`.
4. Записать `KnowledgeUnitKey → KnowledgeUnitId` в `content_key_to_unit_id`.
5. Обновить все cross-reference (`anchor_unit_id`, `superseded_by`, `derived_from`) через lookup-таблицу.
6. Перестроить `inverted_token_to_unit`, `field_to_postings`, secondary indexes.

Запускается через `agent-memory-cli migrate-ku-id-scheme`.

### 9.4. Migration Steps

1. Сканировать все `knowledge_units`, прочитать envelope.
2. Для каждого unit:
   - Извлечь components из envelope (если были вшиты в старый monolithic struct).
   - Извлечь payload (если был).
   - Записать в новую структуру (envelope + components).
3. Создать projections по per-kind правилам (см. секцию 5).
4. Перестроить secondary indexes: `inverted_token_to_unit`, `field_to_postings`, `metadata_filters`.
5. Обновить `schema_info.profile_signature`.

Round-trip test обязателен: `basic_rag` → `agent_ltm` → `basic_rag` без потери данных и индексов.

## 10. Recommended Implementation Order

Шаги из `memory-stacks-roadmap.md` секция 16, конкретизированные для per-kind payloads и lifecycle FSM:

| Шаг | Что добавляется | Payload/component |
|---|---|---|
| 1 | KnowledgeUnitEnvelope + базовые DBI | envelope только |
| 5 | Component infrastructure | operational components (UsageStats, Speaker, Temporal, EmbeddingMeta, CompactionMeta) |
| 5.5 | KnowledgeUnitKey DBI | `content_key_to_unit_id` DBI + `KnowledgeUnitKey`/`ContentHash` structs + storage-owned monotonic `KnowledgeUnitId` sequence (см. секцию 4) |
| 5.6 | SourceRefSummary inline + source_refs DBI (M1) | `SourceRefSummary` в envelope (≤3 на unit) + `source_refs` DBI для полных цитат (см. секцию 3) |
| 6 | Payload components per kind | QAPayload, FactPayload, ChunkPayload, ConversationEpisodePayload, CompiledArticlePayload |
| 9 | Lifecycle FSM (4 durable states) | Active / Superseded / Deprecated / Erased; lifecycle extension с anti-loop подсвинком через `UsageStatsComponent.cooldown_until_ms` (см. секцию 6) |
| 9.1 | Anti-loop подсвинок | self_echo_suppression в DecayAwareRetriever + AntiLoopCooldown фильтр; cooldown/soft_suppression хранятся в UsageStatsComponent (НЕ LifecycleState, НЕ инкрементит revision) |
| 12 | Round-trip тесты для default stacks | все payloads (write → close → reopen → verify) |

Дополнительные шаги (M2+): EventPayload (если потребуется), Task/Decision payloads.

## 11. References

Внутренние документы:

- `guides/memory-stacks-roadmap.md` — ADR-001 (envelope + components), ADR-003 (profile/scope), ADR-004 (KnowledgeUnit миграция), ADR-005 (search text), ADR-008 (decay/anti-loop), ADR-011 (lifecycle FSM). `MemoryProfileSpec`, `MemoryStack`, physical manifest ownership, maturity levels.
- `guides/knowledge-base-roadmap.md` — `KnowledgeUnitEnvelope` contract, retrieval flow, `IComponentStore`/`IProjectionStore`, `SearchProjection` generation rules, `DecayAwareRetriever`, evaluation pipeline.
- `guides/lexical-search-roadmap.md` — BM25F по projections, field-weighted indexing, postings structure.
- `guides/optimization-roadmap.md` — vector storage per `(model_id, projection_kind)`, binary signature indexes, scope-aware secondary indexes.
- `guides/mdbx-containers-extension-tz.md` — `TypeDiscriminatedTable` для `unit_components`, `MultiTableWriter` для atomic coordinated writes, `ReverseIndexTable` для `metadata_filters`.
- `guides/compaction-roadmap.md` (future) — `episode_compaction`, `SummaryPromotionJob`, handoff structure.
- `guides/runtime-services-roadmap.md` (future) — PromptCache, AsyncIndexer, WriteGate.
- `guides/policies-roadmap.md` (future) — детальная спецификация DecayPolicy/WritePolicy/SpeakerScopePolicy с диапазонами и defaults.
- `guides/architecture.md` — 4-слойная модель, maturity levels.

External references (ai-agent-playbook):

- `concepts/rag-knowledge/Внешняя память LLM-агентов — система СВИНОПАС.md` — anti-loop / cooldown паттерны, lifecycle states.
- `concepts/llm-research/Сравнение подходов организации памяти и контекста LLM.md` — taxonomy memory levels (hot/warm/cold), retrieval composition.
- `concepts/ai-agents/AI-агенты и AI-VTuber — архитектурные паттерны из видео 2026.md` — hot/cold path separation, layered memory.
- `resources/llm-research/Memory for Autonomous LLM Agents survey — конспект.md` — write-read-manage loop, lifecycle management.
