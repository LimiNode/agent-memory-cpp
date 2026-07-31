# Chunkers Roadmap

> C++17 compliance: кодовые сниппеты используют `const std::vector<T>&` вместо `std::span` и явные конструкторы вместо designated initializers. Этот гайд не задаёт API surface для чанкеров — он фиксирует паттерны, на основе которых реализуются конкретные `IResourceAdapter`-ы.

## Source attribution policy

Этот гайд синтезирует материал из нескольких источников. Цитаты следуют двухуровневому паттерну:

- `[Source: <public citation>]` — основная цитата на опубликованную работу, документацию проекта, ревизию репозитория или статью. При цитировании внешнего источника публичная ссылка авторитетна.
- `[Source: internal note — no public source available. Path: <path>]` — утверждение взято только из внутренних исследовательских заметок без эквивалентного публичного источника на момент написания. Требует дополнительной проверки.

Внутренние заметки — это discovery aids, а не авторитетные цитаты. Публичные roadmap-заявления цитируют публичный источник первым; внутренние заметки служат лишь маркером частного происхождения.

Гайд описывает **format-specialized** и **domain-specialized** chunkers для `agent-memory-cpp`. Default length-based chunking часто неоптимален для многих типов контента; этот документ фиксирует альтернативы, которые агенты могут подключать через `IResourceAdapter` (см. `memory-stacks-roadmap.md` §13 и `lexical-search-roadmap.md` §Raw Resource Stores).

Гайд синтезирует материалы из `ai-agent-playbook`, преимущественно три источника:

- `ai-agent-playbook/resources/rag-knowledge/ЮMoney Юджин корпоративный RAG на FRIDA и Milvus - разбор статьи.md` — production case с 5 format-specialized чанкерами.
- `ai-agent-playbook/concepts/rag-knowledge/Local RAG для проприетарного API - паттерн декомпиляции и карточки сущности.md` — entity-card chunking для decompiled code.
- `ai-agent-playbook/playbooks/rag-knowledge/Legal-RAG - структурный чанкинг по статьям и пунктам.md` — structural chunker для НПА с citations и UUID5.
- `ai-agent-playbook/playbooks/rag-knowledge/Docling RAG Agent — шаблон для обработки любых форматов.md` — Docling как multimodal chunker.
- `ai-agent-playbook/concepts/rag-knowledge/Contextual Retrieval — LLM-обогащение чанков контекстом документа.md` — Anthropic chunk-enrichment.
- `ai-agent-playbook/playbooks/rag/Contextual Retrieval — индексация production-RAG с Haiku + prompt caching.md` — production playbook.
- `ai-agent-playbook/resources/n8n-automation/Метод улучшения RAG от Anthropic - Контекстуализация в n8n - расшифровка.md` — реализация без Python в n8n + Qdrant.
- `ai-agent-playbook/playbooks/rag/RAG через Q-A пары — альтернатива загрузке PDF.md` — Q-A pairs as chunking alternative.

Related roadmaps:

- [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) — `KnowledgeUnitKind::Chunk` + `ChunkPayload` (heading_path, code_blocks, symbols, byte_offset, byte_length).
- [`lexical-search-roadmap.md`](lexical-search-roadmap.md) — SearchProjection и field-weighted indexing (куда ложится результат chunking).
- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §13 — Runtime services, `IResourceAdapter`, `IAsyncIndexer`.
- [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) — что эти chunks индексируют и как.

## §1. Purpose

Этот документ:

1. **Catalog chunker patterns.** Свести в один каталог паттерны chunker'ов: default length-based, format-specialized (OpenAPI/Markdown/AsciiDoc/PlantUML/HTML), domain-specialized (Legal/decompiled-code/multimodal).
2. **Map to our substrate.** Показать, как эти паттерны ложатся на наш `IResourceAdapter` + `ChunkPayload` + `SearchProjection` + `envelope.revision` contract'ы.
3. **Document enrichment strategies.** Context-enrichment на индексации (Anthropic-style contextualization, late chunking, reverse Q-A generation) — это слой **поверх** chunking, не замена.
4. **Implementation ladder.** Что в M0 (baseline), что в M1 (production), что в M2+ (advanced optional).
5. **Raw document path.** Зафиксировать, что обычные UTF-8 `.md`, `.txt` и
   logs могут индексироваться без предварительной curated-card нормализации;
   extracted PDF/transcript text is M0 only when explicitly labeled derived.

Non-goals:

- Не описывать chunker API contract — он в `memory-stacks-roadmap.md` §13 и `knowledge-units-roadmap.md` §5.1.
- Не повторять BM25F/BM25-формулы — это в `lexical-search-roadmap.md`.
- Не описывать vector storage — это в `optimization-roadmap.md`.

## §2. Default Chunker

### §2.0. Raw Resource Ingestion Path

Chunking starts from raw resources, not only from curated cards. The minimal
path for generic documents is:

```text
IResourceAdapter
  -> RawResource{ResourceId, uri/path, content_type, revision, body/extracted_text}
  -> optional ResourceBodyStore write (compressed or plain)
  -> ChunkPayload records with SourceRef byte/text ranges
  -> SearchProjection::Original for retrieval
  -> optional later normalizer: Fact / QAPair / Summary / CompiledArticle
```

For M2 document, image, audio and video ingestion, this is the text-only view
of the normative artifact pipeline in
[`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md). A raw
resource binds to a `SourceRevision` and immutable `Artifact`; extractors emit
versioned `Representation` records and addressable `Segment` records before
segment-backed `Chunk` units are indexed. A concrete parser remains an
`IResourceAdapter`; it must not make its private extraction JSON the core
provenance model.

M0 accepts only already-available UTF-8 text. It may index externally extracted
text as a derived resource, but it cannot present that result as an original
PDF/page/image/audio/video citation without the artifact profile.

If a source already provides curated metadata, the importer may create a
specific `KnowledgeUnitKind` immediately. If it does not, the importer creates
a weaker generic `Note`/`Chunk` unit that is still searchable and citeable.
This keeps early RAG usable for raw vaults/playbooks while preserving the
curated card-like layer for trust, lifecycle, graph relations and compaction.

Default chunker — length-based sliding window. ЮMoney production case использует:

```text
~1024 токенов с перекрытием 200 токенов, считается через токенизатор FRIDA.
```

[Source: internal note — no public source available. Path: ai-agent-playbook/resources/rag-knowledge/ЮMoney Юджин корпоративный RAG на FRIDA и Milvus - разбор статьи.md]

В нашем стеке:

```text
primary_text = первые 500 символов body + heading path
              (см. knowledge-base-roadmap.md §3.3)
```

Default chunker — это baseline, который работает для **generic prose**. У него есть известные проблемы:

- Теряет структуру документа (заголовки, секции, статьи, эндпоинты).
- Не учитывает тип содержимого (code vs table vs prose vs diagram).
- Не сохраняет citations / anchors.
- Может разорвать семантически связные блоки (конец главы + начало следующей).

Эти проблемы — повод для format-specialized и domain-specialized chunker'ов.

## §3. Format-Specialized Chunkers

Каждый формат имеет свою естественную структуру. Default length-based chunker её игнорирует. Format-specialized chunker использует эту структуру как границы chunks.

Источник — ЮMoney production case [Source: internal note — no public source available. Path: ai-agent-playbook/resources/rag-knowledge/ЮMoney Юджин корпоративный RAG на FRIDA и Milvus - разбор статьи.md]:

### 3.1. OpenAPI / Swagger

```text
Подход: разворачивает ссылки $ref, удаляет секцию components, разбивает по эндпоинтам.
        Каждый path + method становится отдельным чанком с метаданными API.
```

Применимо для:

- `OpenAPI 3.x` (`.yml`, `.yaml`, `.json`).
- `Swagger 2.0`.

Преимущества:

- Каждый endpoint = отдельный chunk → retrieval точно ограничен API surface.
- `$ref` resolution устраняет forward-reference ambiguity.
- API metadata (path, method, params, response codes) доступны как structured payload.

В нашем стеке chunker парсит YAML/JSON, разворачивает `$ref`, отбрасывает `components`, и для каждого `(path, method)` эмитит `ChunkPayload` со структурным идентификатором и metadata (см. §7 «OpenAPI chunk metadata contract»).

### 3.2. Markdown

```text
Подход: строит дерево заголовков и добавляет в каждый чанк цепочку родительских разделов —
        фрагмент сохраняет контекст положения в документе.
```

Применимо для:

- `.md` файлы с ATX-style headers (`#`, `##`, …).
- `.markdown` файлы.
- CommonMark / GFM extensions.

Преимущества:

- Heading path передаётся в каждый chunk → retrieval ранжирует по контексту.
- Code blocks внутри chunk сохраняются как `ChunkPayload.code_blocks`.
- Списки, таблицы, цитаты сохраняются без разрыва.

В нашем стеке `ChunkPayload.heading_path` уже хранит heading chain (`knowledge-units-roadmap.md` §5.1). Это **именно** parent-section chain — chunk наследует контекст своего места в дереве.

### 3.3. AsciiDoc

```text
Подход: аналогично Markdown, дополнительно извлекает метаданные документа (автор, версия).
```

Применимо для:

- `.adoc` файлы.
- AsciiDoc header (`:author:`, `:revnumber:`, `:revdate:`).

Преимущества:

- Document-level metadata (author, version) попадает в chunk metadata, не в chunk body.
- Section structure (==, ===) обрабатывается аналогично Markdown headings.

В нашем стеке asciidoc chunker эмитит `ChunkPayload` + дополнительные metadata (author, version) в `metadata_typed` envelope (`knowledge-base-roadmap.md` §3).

### 3.4. PlantUML

```text
Подход: учитывает вложенность блоков по отступам, сохраняет заголовок диаграммы.
```

Применимо для:

- `.puml`, `.iuml` файлы.
- `@startuml` / `@enduml` блоки в любом текстовом формате.

Преимущества:

- Indent-based nesting сохраняется → структура диаграммы не теряется.
- Diagram title (`title My Diagram`) попадает в heading_path.

В нашем стеке chunker парсит `@startuml ... @enduml`, обрабатывает вложенность через `indent_level`, эмитит `ChunkPayload` с `symbols = [actor names, class names]`.

### 3.5. HTML → Markdown

```text
Подход: предварительная конвертация в Markdown, затем общий пайплайн.
```

Применимо для:

- `.html` файлы.
- HTML внутри других форматов (например, doc comments).

Преимущества:

- Один pipeline для всех HTML-derived content.
- Markdown conversion обычно сохраняет семантику (headings, lists, links).

> **Proposed API sketch — not implemented.** `IResourceAdapter.preprocess(resource)` — illustrative interface; в текущем коде отдельный preprocess-hook может быть интегрирован через `IResourceStore`-based adapter chain, но имя `IResourceAdapter.preprocess` не зафиксировано.

В нашем стеке HTML → Markdown adapter реализуется через `IResourceAdapter.preprocess(resource)`, который эмитит MD-файл, передаваемый дальше в Markdown chunker.

### 3.6. Comparison Table

| Format | Structure signal | Chunk boundaries | Metadata |
|---|---|---|---|
| OpenAPI | endpoint (path + method) | per endpoint | API path, method, params |
| Markdown | ATX headings (#) | per section/heading | heading chain |
| AsciiDoc | document header + == headings | per section | author, version |
| PlantUML | @startuml blocks + indent | per diagram или nested block | diagram title |
| HTML → Markdown | tags (h1-h6) | per section | original URL, title |

[Source: internal note — no public source available. Path: ai-agent-playbook/resources/rag-knowledge/ЮMoney Юджин корпоративный RAG на FRIDA и Milvus - разбор статьи.md]

## §4. Domain-Specialized Chunkers

Domain-specialized chunker'ы оптимизированы под конкретную прикладную область, где semantics важнее generic structure.

### 4.1. Legal / Regulatory (ProcurementRAG)

Источник: `ai-agent-playbook/playbooks/rag-knowledge/Legal-RAG - структурный чанкинг по статьям и пунктам.md`.

```text
Подход: regex по «Статья N», «N.» → TextChunk + metadata.
        Citation пишется в заголовок чанка.
        UUID5 (source + structural_path) обеспечивает structural-identity.
```

> **Не путать с UUID5(source + chunk_index).** Базовая UUID5(source + chunk_index) FRAGILE: добавление новой статьи в начало документа сдвигает все downstream chunk_indexes → все downstream identities меняются → не годно для legal/regulatory, где требуется стабильность при insert-points. См. §7.2 «Two chunk identity strategies».

Ключевые решения (тот же источник):

```python
ARTICLE_RE = re.compile(r'Статья\s+(\d+(?:\.\d+)?)', re.IGNORECASE)
POINT_RE = re.compile(r'^(\d+)\.\s', re.MULTILINE)
```

```python
@dataclass
class LegalChunkMetadata:
    source: str        # ФЗ-44 / ФЗ-223 / Положение
    doc_type: str      # federal_law / internal_regulation
    article: str | None
    point: str | None
    citation: str
    chunk_index: int
```

Преимущества:

- **Citation в начале чанка** → LLM сразу видит, на какой пункт ссылается.
- **OCR fallback** для сканов: PyMuPDF + Tesseract (`min_text_chars_per_page=50` порог).
- **recreate_collection flag** для bulk reindex.

В нашем стеке:

> **Proposed API sketch — not implemented.** `IResourceAdapter.preprocess` — illustrative; см. §3.5 disclaimer.

- `LegalChunkMetadata.source` ↔ `envelope.sources[].uri` (SourceRef, см. `knowledge-units-roadmap.md` §3).
- `LegalChunkMetadata.citation` ↔ chunk header text.
- UUID5 ↔ `StructuralChunkKey = UUID5(document_id + structural_path)` (`knowledge-units-roadmap.md` §4.2) — stable identity across re-runs.
- `ContentHash` ↔ `hash(normalized_content)` — отдельное indexable поле для dedupe и "did this section change?" check.
- `KnowledgeUnitId` ↔ monotonic internal ID (per-document counter) — foreign-key для cross-table joins.
- `revision` ↔ monotonic, bumped on content edit — optimistic concurrency при сохранении identity.
- OCR ↔ `IResourceAdapter.preprocess` hook.

Четыре ключа, четыре цели — не interchangeable:

| Key                 | Formula                                       | When it changes                       | Use case                              |
|---------------------|-----------------------------------------------|---------------------------------------|---------------------------------------|
| `StructuralChunkKey`| `UUID5(document_id + structural_path)`        | structural path renumbering           | stable identity across re-runs        |
| `ContentHash`       | `hash(normalized_content)`                    | any content change                    | deduplication, "did this change?"     |
| `KnowledgeUnitId`   | monotonic internal ID                         | never (per-document counter)         | foreign-key for cross-table joins     |
| `revision`          | monotonic, bumped on content edit             | content edit (with same identity)     | optimistic concurrency                |

- `StructuralChunkKey` определяется pure-structural inputs (document_id + structural_path). Меняется **только** на structural rewrites (path renumbering, re-anchoring, semantic replacement), не на typo-fixes.
- `ContentHash` — BOTH входной component `Content`-identity UUID5 AND отдельный indexable field для fast lookup без recompute (см. §7.2 «Content identity»).
- `KnowledgeUnitId` — opaque monotonic ID; не используется как identity predicate (не меняется при контент-редакциях), но даёт cheap join target.
- `revision` — per-chunk counter; bumps on any content-bearing change, инкрементируется параллельно с identity-preserving операциями (structural key остаётся стабильным).

### 4.2. Decompiled Code (Smart3D pattern)

Источник: `ai-agent-playbook/concepts/rag-knowledge/Local RAG для проприетарного API - паттерн декомпиляции и карточки сущности.md` и `ai-agent-playbook/playbooks/rag-knowledge/Local RAG build pipeline - decompile enrich bge-m3 agent manifest.md`.

```text
Подход: entity-card = chunk. Карточка содержит сигнатуру метода, описание, извлечённые факты,
        поисковые подсказки. LLM-обогащение с правилом «только подтверждённые факты».
```

Pipeline (тот же источник):

```text
1. Source layer: декомпиляция бинарных сборок (ILSpy, dotPeek, RoslynDump для .NET;
   CFR, Procyon, JD-GUI для JVM; IDA Pro, Ghidra для native).
2. Парсинг без LLM: структурированная выжимка фактов (сигнатура метода,
   XML-дока, вызовы других методов, исключения, создаваемые объекты).
3. Обогащение LLM с правилом «только подтверждённые факты».
4. Embed через bge-m3 (мультиязычный).
5. History-aware агент с проверяющим слоем.
6. Manifest версионирования снимка знаний.
7. Build-time и runtime разделены.
```

Карточка сущности (тот же источник):

```text
- Сигнатура метода.
- Краткое описание.
- Извлечённые факты (вызовы других методов, исключения, создаваемые объекты).
- Поисковые подсказки (как пользователи могут сформулировать запрос про эту сущность).
```

7 ключевых решений (тот же источник):

1. Карточка сущности = чанк.
2. bge-m3 для двуязычных API (русский запрос + английский API).
3. Правило «только подтверждённые факты» при обогащении.
4. History-aware агент с проверяющим слоем.
5. Режим `need_more` / «честно не знаю».
6. Manifest версионирования снимка знаний.
7. Build-time и runtime разделены.

В нашем стеке:

- Entity card ↔ `Chunk` kind + `ChunkPayload` (symbol-heavy, code_blocks-heavy).
- `symbols` поле хранит имена методов и классов.
- `code_blocks` хранит декомпилированный snippet.
- `metadata_typed` хранит `entity_card` schema (signature, facts, search_hints).
- `SourceRef.resource_id` связывает с версией DLL через manifest.
- `envelope.revision` increment на rebuild для stale-filter.

### 4.3. Multimodal (Docling)

Источник: `ai-agent-playbook/playbooks/rag-knowledge/Docling RAG Agent — шаблон для обработки любых форматов.md`.

```text
Подход: Docling обрабатывает PDF/DOCX/PPTX/XLSX/images/audio/HTML.
        Built-in chunk.contextualize(doc) для chunk enrichment.
```

Преимущества:

- Один адаптер для многих форматов.
- Layout-aware (PDF tables, изображения embedded в текст).
- Audio transcription (speech-to-text).
- Built-in contextualization API.

> **Proposed API sketch — not implemented.** Имя `metadata_typed["image_caption"]` — illustrative JSON-like access к chunk metadata; реальный доступ — через `ChunkPayload` и `metadata_typed` (key→value typed map), но конкретный ключ `image_caption` ещё не зафиксирован в `knowledge-units-roadmap.md`. Docling `IResourceAdapter` integration — illustrative pattern.

В нашем стеке Docling реализуется как `IResourceAdapter`:

```text
PDF → Docling.parse() → chunks + extracted images + audio transcripts
                    ↓
              ChunkPayload с code_blocks (для embedded code) + symbols (для headings)
                    ↓
              metadata_typed["image_caption"] для embedded images
                    ↓
              SearchProjection(text=chunk + image_captions)
```

### Two distinct contextualization strategies

`Docling`'s `chunk.contextualize(doc)` — **structural contextualization**: сериализует headings, captions, table captions, image references, parent hierarchy в текстовое представление, присоединяемое как prefix к chunk. Это deterministic, local, **без вызова LLM**.

`Anthropic Contextual Retrieval` (Anthropic, 2024-09-19) — отдельная техника: вызывает LLM (default Haiku) для генерации 50–100-токенового free-form explaining prefix per chunk, с prompt caching для cost efficiency.

Две техники **не эквивалентны**. Композиция возможна: Docling chunk → optional Anthropic-style LLM enrichment → index.

## §5. Context-Enrichment Strategies

Chunk enrichment — слой **поверх** chunking. Chunk границы выбираются format-specialized chunker'ом, а content каждого chunk обогащается до индексации.

### 5.1. Anthropic Contextual Retrieval

Источник: `ai-agent-playbook/concepts/rag-knowledge/Contextual Retrieval — LLM-обогащение чанков контекстом документа.md`.

```text
50–100-токеновый LLM-сгенерированный префикс, описывающий место chunk в документе.
Префикс добавляется в оба индекса — dense-embedding и BM25.
Anthropic-бенчмарк: 49% reduction в retrieval failure rate.
Стоимость: ~$0.50 на 1000 chunks (Claude Haiku 4.5 + prompt caching + batch API).
```

Pipeline (тот же источник):

```text
1. Chunking (sliding window 500/100, semantic, structural).
2. Per chunk: LLM-call с cache_control: ephemeral на документе, max_tokens=200.
3. Склейка context + "\n\n" + chunk → embedding и BM25.
4. Original_chunk и context хранятся отдельно от индексируемого текста.
5. На query: hybrid retrieval → RRF → optional reranker → LLM с original_chunk без префикса.
```

Префикс описывает: тип документа, главную тему, раздел/подтему, конкретные сущности (компания, продукт, версия, период). **Не** пересказывает chunk, **не** делает выводов.

> **Proposed API sketch — not implemented.** `IAsyncIndexer.preindex` — illustrative contract для LLM-обогащения chunks перед индексацией. В `memory-stacks-roadmap.md` §13 определён контракт `IAsyncIndexer`, но имя hook'а `preindex` — placeholder; реализация потенциально через `AsyncIndexer` worker.

В нашем стеке это реализуется через `IAsyncIndexer.preindex` hook:

```text
Resource → read content
       ↓
chunked = chunker.chunk(resource)         // format-specialized
       ↓
for each chunk:
    context = llm.augment(chunk, full_document_context)
       ↓
SearchProjection(
    text = context + "\n\n" + chunk.body,
    original_chunk = chunk.body,           // для финального промпта
    contextual_summary = context,          // для diagnostics
    ...
)
```

### 5.2. Late Chunking

Из `ai-agent-playbook/concepts/rag-knowledge/11 RAG стратегий — спектр и комбинации.md`:

```text
Эмбеддинг применяется к документу целиком, затем токенные эмбеддинги делятся на chunks.
Каждый chunk сохраняет контекст всего документа.
```

Trade-off (тот же источник):

- Сложнее, требует модели с длинным контекстом.
- Когда применять: максимальная сохранность контекста при chunking.

В нашем стеке — `M2+` optional. Требует long-context embedding model (BGE-M3 с 8192 tokens поддерживает это).

### 5.3. Reverse Q-A Generation

Из `ai-agent-playbook/playbooks/rag/RAG через Q-A пары — альтернатива загрузке PDF.md`:

```text
LLM генерирует синтетические Q-A пары для каждого chunk.
Q-A пары индексируются отдельно.
Если query совпадает с synthetic question, возвращается связанный chunk.
```

Это **two-stage indexing** в ЮMoney production:

```text
Two-stage indexing: индексы чанков + синтетические вопросы, сгенерированные LLM.
```

[Source: internal note — no public source available. Path: ai-agent-playbook/resources/rag-knowledge/ЮMoney Юджин корпоративный RAG на FRIDA и Milvus - разбор статьи.md]

В нашем стеке это реализуется как дополнительный `SearchProjection(kind = QAQuestion)` для каждого chunk с синтетическим вопросом.

## §6. Two-Stage Indexing (Chunks + Synthetic Questions)

Two-stage indexing — паттерн, где для каждого chunk создаётся дополнительный индекс с синтетическими вопросами. Если найдено совпадение по синтетическому вопросу, возвращается связанный chunk.

Источник: `ai-agent-playbook/resources/rag-knowledge/ЮMoney Юджин корпоративный RAG на FRIDA и Milvus - разбор статьи.md`.

Pipeline:

```text
1. Chunking (format-specialized).
2. Per chunk: LLM-call генерирует N синтетических вопросов (обычно 3-5).
3. Индексируются chunks и синтетические вопросы (separate vector store или separate namespace).
4. На query: hybrid retrieval по обоим индексам.
5. Если совпал synthetic question → возвращается связанный chunk (cross-reference).
```

Преимущества:

- **Question-side retrieval** часто точнее, чем chunk-side (query vs question vs chunk — все три разные стили).
- **Long-tail coverage** — chunk может не содержать exact query terms, но синтетический вопрос содержит.

Trade-offs:

- Стоимость генерации (LLM-call per chunk).
- Storage overhead (×3-5 размер индекса).
- Качество synthetic questions зависит от LLM.

В нашем стеке это реализуется как `SearchProjection(kind = QAQuestion)` с `text = synthetic_question`. Retrieval fuse через RRF учитывает оба projection_kind.

## §7. Chunk Metadata Contract

Что каждый chunk **должен** нести в metadata для корректной работы retrieval.

Из `knowledge-units-roadmap.md` §5.1 + `knowledge-base-roadmap.md` §3 (per `ChunkPayload`) + расширения из этого гайда:

| Поле | Где | Назначение |
|---|---|---|
| `id` | envelope | monotonic `KnowledgeUnitId` (см. `knowledge-units-roadmap.md` §4) |
| `source` | envelope.sources[] | `SourceRef` / `SourceRefSummary` для citation |
| `version` | envelope.revision | monotonic per UnitId; content-bearing change → +1 |
| `structure_path` | ChunkPayload.heading_path | parent chain или (для OpenAPI) endpoint path |
| `content` | ChunkPayload (body, code_blocks) | chunk text + extracted code |
| `contextual_summary` | SearchProjection extension | Anthropic-style chunk enrichment (опционально, M2+) |
| `chunk_index` | metadata_typed | позиция в исходном документе (для UUID5 дедупликации) |
| `byte_offset` / `byte_length` | ChunkPayload | Exact source range **where representable**; absent for chunks produced by transformation (e.g., after `$ref`-resolution in OpenAPI) or for non-contiguous chunks. |
| `symbols` | ChunkPayload | extracted identifiers / class names |
| `detected_language` | ChunkPayload | для morphology backend selection |
| `used_ocr` | metadata_typed | для PDF/OCR provenance (Legal-RAG pattern) |

Без `source`, `version` и `structure_path` retrieval технически возможен (lexical / dense / hybrid search по content), но provenance-affecting операции (corpus update propagation, field-aware scoring, citation generation, filtered search по source) будут неполными. `source`/`version`/`structure_path` — RECOMMENDED, не строго обязательны для retrieval. Без `contextual_summary` chunk не может участвовать в Anthropic-style enrichment (если такая enrichment будет применяться).

### 7.1. OpenAPI chunk metadata contract

После `$ref`-resolution + удаления секции `components`, chunk больше **не является contiguous range** исходного файла. Внутренний `$ref` может ссылаться на контент из разных секций, разных файлов, или shared across endpoints.

Используйте **JSON Pointer** как идентификатор chunk, например:
- `/paths/~1users/get` — указывает на resolved `get` operation на `/users`
- `/components/schemas/User` — указывает на resolved schema component

Каждый chunk несёт:
- `json_pointer` — расположение в **fully dereferenced OpenAPI document** (in-memory или reconstructed YAML/JSON, не исходный файл)
- `source_path` — путь к оригинальному `.yaml`/`.json` файлу
- `source_section_anchor` — YAML/JSON pointer в **ORIGINAL** source для citation (может отличаться от chunk-pointer)
- `structural_kind` — один из `path`, `operation`, `schema`, `parameter`, `requestBody`, `response`, и т.д.

**НЕ используйте** `byte_offset` + `byte_length` для chunks, произведённых после `$ref`-resolution. Они будут ложно cite'ить другое место.

### 7.2. Three identity models (do not mix without explicit note)

Three distinct chunk-identity strategies exist. Pick one per chunk kind; mixing требует явной пометки в chunk contract.

| Model | Formula | Stable against | Change semantics | Use case |
|---|---|---|---|---|
| **Position** (baseline, fragile) | `UUID5(source + chunk_index)` | Re-indexing of identical segmentation | Insert-points shift; segmentation changes | Stable-by-construction corpora only (rare) |
| **Structural** | `UUID5(document_id + structural_path)` | Insert-points; intra-document restructuring | **Stable against:**<br>• insertions in other parts of the document;<br>• content edits of the same logical section (revision bumps, identity stays).<br><br>**Identity changes on:**<br>• structural path renumbering (article removed/renumbered);<br>• re-anchoring (section moves);<br>• logical section replacement (semantic identity change). | Legal, regulatory, codified documents |
| **Content** | `UUID5(document_id + structural_path + normalized_content_hash)` | Re-runs with identical content | Any content edit (intended) | Source code, transcripts, dynamic corpora |

**Structural identity** (рекомендуется для legal, regulatory, codified documents):

```
UUID5(document_id + structural_path)   // e.g., UUID5(doc_id, "/article/5/point/3")
```

Insert в середине документа **НЕ** сдвигает существующие identities. Content edits: identity stays, revision increments, content hash changes; treat content hash as separate field. Identity change зарезервирован для structural rewrites (section renumbering, re-anchoring), не для typo-fixes.

**Content identity** (рекомендуется для source code, transcripts, dynamic corpora):

```
UUID5(document_id + structural_path + normalized_content_hash)
```

Content edits создают новую identity. Стабилен при повторных run'ах с идентичным content. `normalized_content_hash` is BOTH part of the UUID5 input AND stored as a separate indexable field on the chunk (separate-lookup полезно для dedupe-without-recompute и "did this section change?" check).

**Content edit semantics:**

- content change → content_hash changes → UUID5 input changes → new identity;
- structural_path or document_id change → UUID5 input changes → new identity;
- insertion elsewhere in the document → none of the inputs change → identity stable.

**Position identity (baseline, fragile)**: `UUID5(source + chunk_index)` подходит для stable-by-construction corpora, где segmentation никогда не сдвигается (редко). Это **НЕ** robust general strategy: добавление новой статьи в начало документа сдвигает все downstream `chunk_index` → все downstream identities меняются → не годно для legal/regulatory, где требуется стабильность при insert-points.

Выбирайте исходя из того, должны ли insert-points быть стабильны или должен ли content hash быть стабилен. Задокументируйте выбор per chunk kind в contract'е `knowledge-units-roadmap.md`.

## §8. Implementation Ladder

Маппинг chunker-паттернов на наши maturity levels.

### 8.1. M0 (MVP smoke tests)

Готово из коробки:

- **Default length-based chunker** (500 chars + heading path) — baseline.
- **Markdown chunker** — heading tree + parent chain в каждом chunk.
- **ChunkPayload schema** — byte_offset, byte_length, heading_path, code_blocks, symbols, detected_language (`knowledge-units-roadmap.md` §5.1).
- **`SearchProjection` generation** — per-kind rules (`knowledge-base-roadmap.md` §5.2).
- **`envelope.revision` increments** на content-bearing change.

### 8.2. M1 (Production)

> **Planned API — not yet implemented.** `IResourceAdapter` контракт — фиксируется в `memory-stacks-roadmap.md` §13 как runtime-services interface. В этой секции упоминается как место подключения pre-indexing hook'ов.
> **Proposed API sketch — not implemented.** `IResourceAdapter.preprocess` — placeholder имя hook'а в этом контракте (см. §3.5 disclaimer).

Дополнительно:

- **OpenAPI chunker** — разворачивает `$ref`, разбивает по endpoints.
- **AsciiDoc chunker** — аналогично Markdown + document metadata.
- **HTML → Markdown adapter** — `IResourceAdapter.preprocess`.
- **PlantUML chunker** — indent-aware blocks + diagram title.
- **Legal structural chunker** (по «Статьям» + пунктам) с citation header + UUID5 dedupe.
- **Citation-first eval gate** for regulated chunkers: every chunk intended for
  answer grounding must expose a stable `source_section_anchor`, citation text,
  and `SourceRef`/`quote_hash` link so `CitationFidelity` can fail fast when
  provenance is missing.
- **`IResourceAdapter` контракт** — preprocessed → `ChunkPayload[]` через `IResourceStore`.

### 8.3. M2+ (Advanced, optional)

Дополнительно через extension packages:

- **Docling multimodal chunker** — PDF/DOCX/PPTX/XLSX/images/audio/HTML.
- **Anthropic Contextual Retrieval enrichment** — Haiku-class context generator через `AsyncIndexer` hook.
- **Late chunking** — long-context embedding model.
- **Two-stage indexing (chunks + synthetic Q-A / doc2query)** — reverse Q-A
  generation and query expansion terms stored as materialized, versioned
  derived `SearchProjection`s. Each generated projection records generator
  identity, model/version, prompt/config hash, source revision,
  projection kind, artifact hash, and regeneration policy. Evaluate this
  separately from free-form contextual prefixes.
- **Decompiled-code entity-card chunker** — Smart3D pattern для проприетарных API.
- **OCR fallback** (Tesseract) — для scanned PDFs в Legal-RAG.

## §9. References

### 9.1. External (ai-agent-playbook)

**Format-specialized:**

- `ai-agent-playbook/resources/rag-knowledge/ЮMoney Юджин корпоративный RAG на FRIDA и Milvus - разбор статьи.md` (internal note — no public source available) — production case с 5 format-specialized chunkers (OpenAPI / Markdown / AsciiDoc / PlantUML / HTML).

**Domain-specialized:**

- `ai-agent-playbook/concepts/rag-knowledge/Local RAG для проприетарного API - паттерн декомпиляции и карточки сущности.md` (internal note — no public source available) — entity-card pattern, «только подтверждённые факты».
- `ai-agent-playbook/playbooks/rag-knowledge/Local RAG build pipeline - decompile enrich bge-m3 agent manifest.md` (internal note — no public source available) — 7-шаговый pipeline для decompiled code.
- `ai-agent-playbook/playbooks/rag-knowledge/Legal-RAG - структурный чанкинг по статьям и пунктам.md` (internal note — no public source available) — НПА structural chunker, citations, UUID5, OCR.
- **github.com/docling-project/docling (MIT); arXiv:2408.09869 — Docling paper.** Private provenance: `ai-agent-playbook/playbooks/rag-knowledge/Docling RAG Agent — шаблон для обработки любых форматов.md` (internal note) — Docling multimodal.

**Context enrichment:**

- **Anthropic blog "Introducing Contextual Retrieval" (2024-09-19, https://www.anthropic.com/news/contextual-retrieval).** Private provenance: `ai-agent-playbook/concepts/rag-knowledge/Contextual Retrieval — LLM-обогащение чанков контекстом документа.md` (internal note) — Anthropic 49-67% reduction, $0.50/1000 chunks, prompt caching.
- `ai-agent-playbook/playbooks/rag/Contextual Retrieval — индексация production-RAG с Haiku + prompt caching.md` (internal note — no public source available) — production playbook.
- `ai-agent-playbook/resources/n8n-automation/Метод улучшения RAG от Anthropic - Контекстуализация в n8n - расшифровка.md` (internal note — no public source available) — реализация через n8n + Qdrant + Haiku 3.5.

**Two-stage / reverse Q-A:**

- `ai-agent-playbook/playbooks/rag/RAG через Q-A пары — альтернатива загрузке PDF.md` (internal note — no public source available) — Q-A pairs как альтернатива.
- `ai-agent-playbook/resources/rag-knowledge/ЮMoney Юджин корпоративный RAG на FRIDA и Milvus - разбор статьи.md` (internal note — no public source available) — two-stage indexing (chunks + synthetic questions).

**Survey / typology:**

- `ai-agent-playbook/concepts/rag-knowledge/11 RAG стратегий — спектр и комбинации.md` (internal note — no public source available) — Context-Aware Chunking vs Late Chunking vs Fine-tuned Embeddings.
- `ai-agent-playbook/concepts/rag-knowledge/Типы RAG - от Naive до Agentic.md` (internal note — no public source available) — typology по сложности реализации.

### 9.2. In-house (agent-memory-cpp/guides/)

- [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) §5.1 — `ChunkPayload` schema, per-kind `primary_text` rules, projections.
- [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) §4 — `KnowledgeUnitId` (monotonic), `KnowledgeUnitKey` (content-addressing) для UUID5-style dedupe.
- [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) §3 — `SourceRef` contract (citation, quote_hash).
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) §3 — envelope shape, `KnowledgeUnitEnvelope`.
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) §3.3 — `generate_primary_text` для Chunk kind.
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) §5 — `SearchProjection` model.
- [`lexical-search-roadmap.md`](lexical-search-roadmap.md) §Raw Resource Stores — `IResourceStore` контракт.
- [`lexical-search-roadmap.md`](lexical-search-roadmap.md) §Targeted Reindexing — projection-aware reindexing.
- [`lexical-search-roadmap.md`](lexical-search-roadmap.md) §Projection-Weighted BM25F — почему `heading_path` важен для BM25F.
- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §13 — Runtime Services, `IResourceAdapter`, `IAsyncIndexer`.
- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §16 — Recommended Implementation Order.
- [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) — что эти chunks индексируют и как.
- [`research-reading-map.md`](research-reading-map.md) — research references backing this project.

## §10. Tokenizer-Aware Raw Input Pipeline

Large raw corpora need a source pipeline before semantic chunkers run. Borrow
the pattern from `marcelroed/gigatoken`: keep input source, compression,
document boundary policy and tokenizer budget separate. Gigatoken is a Rust/PyO3
tokenizer and is not a core dependency; the useful local idea is the pipeline
shape.

[Source: marcelroed/gigatoken README and design doc, inspected at
`34a1599f0c0ae7d7cd0d1c530e6522320158b360` on 2026-07-27.]

```cpp
enum class RawInputFormat : uint8_t {
    PlainText,
    Markdown,
    JsonLines,
    Parquet,
    ExtractedPdfText,
    Transcript,
    OpaqueBytes
};

enum class RawCompressionKind : uint8_t {
    None,
    Gzip,
    Zstd
};

enum class DocumentBoundaryMode : uint8_t {
    WholeResource,
    Separator,
    JsonLine,
    ParquetRow,
    StructuralHeading,
    AdapterDefined
};

struct RawDocumentSourceSpec {
    RawInputFormat format = RawInputFormat::PlainText;
    RawCompressionKind compression = RawCompressionKind::None;
    DocumentBoundaryMode boundary_mode = DocumentBoundaryMode::WholeResource;
    std::string separator;
    std::string text_field;
    std::string parser_id;
    std::string parser_version;
};

struct TokenBudgetPolicy {
    std::string tokenizer_id;
    std::uint32_t target_tokens = 1024;
    std::uint32_t overlap_tokens = 200;
    bool require_safe_boundaries = true;
};
```

Rules:

- Compression is storage/input framing, not document semantics. `.jsonl.zst`
  is still JSONL after decompression.
- `DocumentBoundaryMode` determines where independent documents start before
  chunking. For example, JSONL line boundaries and Parquet rows are document
  boundaries; Markdown headings are structural chunk boundaries.
- Token-counted chunking requires a tokenizer adapter identity. If an exact
  model tokenizer is unavailable, the chunker may use an estimator, but must
  record that fact in metadata and benchmark it separately.
- Oversized documents may be split internally only at UTF-8-safe and
  tokenizer-safe boundaries. A tokenizer-safe split must not change the token
  sequence relative to encoding the full document, except for explicitly
  documented separator/special-token policy.
- `ResourceRevision::pipeline_config_hash` includes source format, compression,
  boundary policy, parser version, tokenizer id, token budget, overlap and
  normalization policy.

The C++ core owns the dependency-free contracts above. Concrete high-throughput
tokenizers, SIMD pretokenizers, Python/Rust bridges, Parquet readers and PDF
extractors remain optional adapters or tools.

### 10.1. Chunk metadata additions

Tokenizer-aware chunkers SHOULD record:

| Field | Where | Purpose |
|---|---|---|
| `tokenizer_id` | metadata_typed / `ChunkPayload` extension | Tokenizer or estimator identity used for chunk sizing. |
| `token_count` | metadata_typed / `ChunkPayload` extension | Count used by context budgeting and ingestion evaluation. |
| `source_format` | `ResourceRevision` / metadata_typed | Format after compression detection, before parser-specific transforms. |
| `boundary_mode` | `ResourceRevision` / metadata_typed | Reproducible document split policy. |
| `safe_boundary_policy` | metadata_typed | Whether splits are tokenizer-exact, UTF-8-only, structural-only or estimator-based. |

These fields do not replace `SourceRef` byte/text ranges. They explain how the
chunk was produced and when it must be regenerated.

### 10.2. Ingestion benchmark gates

M1 ingestion benchmarks SHOULD report:

- raw input read/decompression throughput;
- parser/extractor throughput;
- tokenization throughput, split by exact tokenizer vs estimator;
- chunk materialization throughput;
- lexical projection/index build throughput;
- peak RSS and output bytes per source byte;
- correctness checks: UTF-8 validity, source citation ranges, token-budget
  bound, and parity between whole-document tokenization and chunked tokenization
  for tokenizer-safe split modes.

Gigatoken-style numbers are a useful upper-bound reference for tokenization
throughput, not a target guarantee for the whole agent-memory ingestion path.
