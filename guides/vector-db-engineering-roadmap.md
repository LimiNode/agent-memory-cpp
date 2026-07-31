# Vector DB Engineering Roadmap

> C++17 compliance: кодовые сниппеты используют `const std::vector<T>&` вместо `std::span` и явные конструкторы вместо designated initializers. Этот гайд **не описывает ANN-алгоритмы** (HNSW, IVF, ANNOY — это в [`optimization-roadmap.md`](optimization-roadmap.md) и в `ai-agent-playbook/concepts/llm-research/ANN-алгоритмы для векторного поиска — HNSW, FAISS, DiskANN, ScaNN, фильтрация.md`); здесь — **operational decision matrix** для выбора конкретного Vector Store под конкретный workload.

## §1. Purpose

Этот гайд существует как **operational decision matrix** для выбора Vector Store под конкретный workload. Не обзор алгоритмов, не benchmark suite, а engineering-решения вида «у меня 1M эмбеддингов с metadata filtering и частыми апдейтами — какую БД ставить».

Cross-link на смежные гайды:

- [`optimization-roadmap.md`](optimization-roadmap.md) — vector / binary / ANN optimisation, encoder registry, dense index modes (Exact / BinaryCandidateFilter / BinaryOnly / ApproximateVector).
- [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) — typology of retrieval techniques (Naive / Advanced / Hybrid / Contextual / Graph / Fusion / Adaptive / Agentic / RLM); какой retriever поверх какого vector store.
- [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) — binarisation как compression layer поверх Vector Store; hybrid binary + dense indexes; composite compression.
- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §6 — capability matrix; `DenseVectors` capability, `BinaryCandidateFilter` / `Hnsw` mode selection.
- [`related-projects.md`](related-projects.md) — внешние сравнения и benchmark suites (если есть).

Non-goals:

- Не описывать HNSW / IVF / ANNOY internals (это в `optimization-roadmap.md` и в `ai-agent-playbook/concepts/llm-research/ANN-алгоритмы для векторного поиска — HNSW, FAISS, DiskANN, ScaNN, фильтрация.md`).
- Не каталогизировать embedding models (это в `optimization-roadmap.md` и `binary-embeddings-roadmap.md`).
- Не выбирать единственный «правильный» vector store — workload-driven.

## §2. Source attribution policy

Этот гайд синтезирует материал из нескольких источников. Цитаты следуют двухуровневому паттерну:

- `[Source: <public citation>]` — основная цитата на опубликованную работу, документацию проекта, ревизию репозитория или статью. При цитировании внешнего источника публичная ссылка авторитетна.
- `[Source: internal note — no public source available. Path: <path>]` — утверждение взято только из внутренних исследовательских заметок без эквивалентного публичного источника на момент написания. Требует дополнительной проверки.

Внутренние заметки — это discovery aids, а не авторитетные цитаты. Публичные roadmap-заявления цитируют публичный источник первым; внутренние заметки служат лишь маркером частного происхождения.

Основной источник этого гайда — публичный доклад Феоктистова Станислава (AIRnD) на канале «Клуб разработчиков СПб» (см. §9). Дополнительный контекст по ANN-алгоритмам — internal note в `ai-agent-playbook` (см. §9).

## §3. Decision attributes — 8 axes driving the choice

Восемь атрибутов, определяющих выбор Vector Store. Каждый workload имеет свой профиль по этим осям; выбор БД — это пересечение профилей.

1. **Algorithm.** Поддерживаемые ANN-алгоритмы (HNSW, IVF, ANNOY, FAISS-based, собственный). Влияет на recall vs latency tradeoff и на metadata-filtering model.
2. **License.** Open-source (Apache 2.0 / MIT / BSL) vs source-available vs proprietary. Влияет на self-host cost, vendor lock-in, возможность форка.
3. **Filtering.** Pre-filter (до ANN), post-filter (после ANN), hybrid (pre-filter на shard, post-filter внутри shard). Полнотекстовая интеграция (BM25 hybrid). Влияет на recall при structured queries.
4. **Scaling.** Sharding (горизонтальное масштабирование), replication (отказоустойчивость), read-write split, single-node ceiling. Влияет на max corpus size и QPS.
5. **Quantisation.** Scalar (int8) vs Product (PQ) vs none. On-disk vs in-RAM tradeoff. Влияет на memory footprint и quality loss.
6. **Deployment.** Single-binary / microservice architecture / managed cloud. Влияет на DevOps cost.
7. **Target use case.** Объём corpus (100K / 1M / 100M+), latency budget, cost ceiling, mutation rate.
8. **Maturity.** Language bindings (C++17 в нашем случае — насколько гладко интегрируется), production track record, активность комьюнити.

[Source: https://www.youtube.com/watch?v=v-EX_AYdolE — Феоктистов Станислав (AIRnD), *Инженерный взгляд на RAG: сравнение векторных баз и алгоритмов*, доклад на канале «Клуб разработчиков СПб» (devclubspb)]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/resources/rag-knowledge/Инженерный взгляд на RAG - сравнение векторных баз и алгоритмов - расшифровка.md]

## §4. Decision matrix

Сводная таблица по 5 популярным Vector Store. Все ячейки — **краткие ярлыки**; детали в §5, §6, §7 и в §9 References.

| Vector Store | Algorithm | License | Filtering | Scaling | Quantisation | Deployment | Use case |
|---|---|---|---|---|---|---|---|
| **Chroma** | HNSW | open-source + managed cloud | Yes (where syntax, inverted metadata indexes) | Limited self-hosted sharding; managed Chroma Cloud has full distribution + object storage | Quantization: verify for selected deployment/version; not confirmed in cited official documentation. | Embedded (Python/JS) + server mode | Local + cloud (evaluate separately); see [Chroma docs](https://docs.trychroma.com/) — capabilities diverge between local/open-source and managed Chroma Cloud (as of 2026-07) |
| **Qdrant** | Собственная HNSW-реализация с диском | open-source + managed cloud | Yes (payload indexes, custom sharding, replication) | Manual sharding + replication | Scalar (int8); PQ; binary quantization (recent); TurboQuant — available since Qdrant 1.18; benchmark on target embeddings before adoption. | gRPC + REST | Local + cloud; see [Qdrant docs](https://qdrant.tech/documentation/) — payload indexes, custom sharding, replication per latest docs (as of 2026-07) |
| **Milvus** | HNSW + IVF + others (multi-algo) | open-source + managed cloud (Zilliz) | Расширенная (больше операторов by default) | Microservices (read/write split, отдельные узлы) | Scalar + Product | Microservices, high DevOps | HighLoad production, read-write split needed; see [Milvus docs](https://milvus.io/docs) — evaluate against reproducible benchmark + hardware profile (as of 2026-07) |
| **Pinecone** | Не раскрыт (предположительно HNSW-based) | Proprietary (managed) | Гибкая managed | Managed horizontal scaling | Managed | API-only (zero DevOps) | Когда не хочется париться; готовы платить managed premium; see [Pinecone docs](https://docs.pinecone.io/) (as of 2026-07) |
| **Weaviate** | HNSW + others | OSS (BSD-3-Clause) | Object references + cross-reference queries via GraphQL (NOT a property-graph system like Neo4j/Graphiti; data model is object-oriented) | Sharding + replication | Через плагины | Self-host Go binary, moderate DevOps | Object store + hybrid search; cross-reference queries via GraphQL API, not a property graph; see [Weaviate docs](https://weaviate.io/developers/weaviate) (as of 2026-07) |

[Source: https://www.youtube.com/watch?v=v-EX_AYdolE — Феоктистов Станислав (AIRnD)]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/resources/rag-knowledge/Инженерный взгляд на RAG - сравнение векторных баз и алгоритмов - расшифровка.md]

> **Algorithm vs implementation note.** Ячейки в колонках "Algorithm" и "Filtering" описывают базовый ANN-алгоритм + capability конкретной реализации Vector Store. Pure-ANN (HNSW / IVF / ANNOY) не имеет встроенного attribute index; production-системы добавляют payload/attribute filters отдельным слоем (см. §4.1). Reindex/re-cluster cadence — workload-dependent, не фиксированная константа.

> **3-tier API reminder.** Символьный статус в этом гайде:
> - **Real (no tag)** — публичные продукты с открытыми сайтами / GitHub, существуют независимо от нашего проекта (Chroma, Qdrant, Milvus, Pinecone, Weaviate).
> - **Planned** — нет в этом гайде (этот гайд не предлагает своего API).
> - **Illustrative** — нет в этом гайде.
>
> Все пять Vector Store выше — **real** external products. Гайд описывает их, а не предлагает собственные классы.

### §4.1. HNSW vs IVF vs ANNOY — quick recall

Не дублируя подробного описания алгоритмов (это в [`optimization-roadmap.md`](optimization-roadmap.md) и в playbook). **Важно:** ANN-алгоритм (HNSW / IVF / ANNOY) и стратегия metadata filtering — это *раздельные слои*. Pure-алгоритм не имеет встроенного attribute index; конкретные реализации могут комбинировать graph/tree traversal с payload/attribute filters (hnswlib с attribute filters, Qdrant HNSW с payload filters, Milvus HNSW/IVF с scalar field filters, Weaviate HNSW с property filters). Re-index / re-cluster / re-build cadence — workload-dependent (drift rate, corpus size, SLA), не фиксированная константа.

- **HNSW.** Multi-layer navigable small world graph. Log-scale search time, fast insert. Pure HNSW не имеет встроенного attribute index; production-системы (Qdrant, Milvus, Weaviate, hnswlib с фильтрами) обычно добавляют pre-filter, post-filter или hybrid traversal. Reindex cadence зависит от workload.
- **IVF.** K-means clusters + per-cluster scanning. Pure IVF performs coarse vector-space candidate selection by selecting the n_probe nearest centroids to the query. Vector-space clusters are partitioned by proximity, NOT by scalar metadata. Metadata filtering is a separate scalar-index layer (Milvus combines both via a scalar-filter bitset passed to the vector search). Re-clustering cadence — workload-dependent: на static corpora может быть «никогда»; на streaming-данных — чаще (зависит от drift rate и целевого recall SLA).
- **ANNOY.** Tree structure, low memory, можно не держать в RAM. Static-data-friendly; filtering и quantisation — отдельные layers, не встроены в core ANN. Часто вытесняется HNSW/IVF.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/ANN-алгоритмы для векторного поиска — HNSW, FAISS, DiskANN, ScaNN, фильтрация.md]

## §5. Three case studies

Из источника `Инженерный взгляд на RAG` — три типовых workload'а и рекомендованный Vector Store.

### §5.1. NoteBase startup (~100K notes, single process)

Профиль: несколько миллионов эмбеддингов, низкий QPS, один сервер, нет DevOps-команды, MVP-сценарий.

Рекомендация:

- **Старт: Chroma** — single instance, HNSW, ограниченная фильтрация, простейшее развёртывание.
- **Когда нужна сложная фильтрация + horizontal scaling → мигрировать на Qdrant**.

Decision attributes: Algorithm (HNSW подходит для <100K-1M), License (OSS бесплатно), Filtering (базовой хватает на старте), Scaling (single instance ок), Deployment (single-binary, zero DevOps), Maturity (production-ready, активное комьюнити).

[Source: https://www.youtube.com/watch?v=v-EX_AYdolE — Феоктистов Станислав (AIRnD)]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/resources/rag-knowledge/Инженерный взгляд на RAG - сравнение векторных баз и алгоритмов - расшифровка.md]

### §5.2. Old Russian metric books corpus (~400M static embeddings)

Профиль: ~400 млн эмбеддингов, статичные данные, батч-import, есть разработчик для настройки.

Рекомендация:

- **Milvus + IVF + Product Quantisation** — IVF с PQ даёт disk-resident storage + не нужна рекластеризация (данные статичны). Milvus покрывает 400M embeddings и предоставляет batch API.
- **Альтернатива без DevOps → Pinecone** (managed).

Decision attributes: Algorithm (IVF + PQ для static), License (OSS или managed), Filtering (любая, corpus однородный), Scaling (горизонтальное для 400M), Quantisation (PQ обязателен при таком объёме), Deployment (microservices для Milvus, zero DevOps для Pinecone).

### §5.3. Product catalog with high QPS (50M+ embeddings, frequent updates)

Профиль: много эмбеддингов, большой QPS, сложная фильтрация, стабильная нагрузка, частые апдейты.

Рекомендация:

- **Старт: Qdrant** — собственная HNSW-реализация с диском, полная фильтрация, шардирование + репликация, scalar + product quantisation, production-ready.
- **При росте до сотен миллионов embeddings и QPS в десятки тысяч → мигрировать на Milvus** (read-write split, отдельные узлы).

Decision attributes: Algorithm (HNSW-based с фильтрацией — Qdrant), License (OSS), Filtering (full, Qdrant native), Scaling (sharding + replication), Quantisation (scalar + product для memory), Deployment (single Rust binary, moderate DevOps).

### §5.4. Hybrid Search considerations

Ни один из перечисленных Vector Store не покрывает BM25 + dense fusion «из коробки» одинаково. Hybrid Search (sparse + dense) обычно реализуется одним из способов:

- **Sparse на стороне агент-memory-cpp** через `lexical-search-roadmap.md` (BM25 / BM25F по контенту chunk'а), fusion через RRF в `HybridRetrievalEngine`. Vector Store используется только для dense candidates. Это основной подход в нашем стеке.
- **Sparse внутри Vector Store** (Qdrant BM25 sparse vectors, Weaviate hybrid search). Удобно для end-to-end latency, но плотно связывает нас с конкретным Vector Store.
- **BM25 в отдельном search engine** (Elasticsearch / OpenSearch) + dense в Vector Store + fusion в `IRetrievalEvaluator`. Дороже по DevOps, но покрывает corpus-level full-text с аналитикой.

Decision attributes: Scaling (BM25+Elasticsearch хорошо горизонтально), Filtering (Elasticsearch даёт любые structured queries), Deployment (multi-service), Maturity (mature, но больше moving parts).

[Source: https://www.youtube.com/watch?v=v-EX_AYdolE — Феоктистов Станислав (AIRnD)]

Open question (см. §10): конкретные замеры Qdrant vs Milvus vs Weaviate hybrid search на одинаковых условиях в нашем стеке — нет benchmark'а.

## §6. Scalar vs Product quantisation tradeoff

Численные оценки из источника. Scalar int8 vs float32 — **4× памяти** при потере ~2% качества.

Product Quantisation: делит вектор на `m` сегментов, K-means каждого сегмента (типично `K=256`, чтобы индекс влезал в 1 байт = `log2(256)`). Каждый вектор кодируется `m` байтами.

```text
Scalar (int8) vs float32:  ~4× memory, ~2% quality loss
PQ (m=8, k=256):            ~96× memory for 768-dim, larger quality loss depends on data
PQ (m=16, k=256):           ~48× memory for 768-dim, smaller quality loss
```

Tradeoff:

- **Scalar int8** — простой type cast, без обучения, предвычисленных таблиц не нужно. Memory savings 4×, quality loss минимальный. Use case: pgvector / Weaviate default.
- **Product (m=8)** — K-means обучение на calibration set, предвычисленные distance tables для ADC (asymmetric distance computation). Memory savings ~96× для 768-dim. Quality loss выше, но допустимо для billion-scale ANN. Use case: FAISS, Milvus billion-scale.
- **PQ residuals** — несколько уровней PQ на residual errors. Дороже обучение, выше качество. Use case: hybrid binary + FP в [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §3.4.

[Source: https://www.youtube.com/watch?v=v-EX_AYdolE — Феоктистов Станислав (AIRnD)]
<br>[Source: arXiv:1702.08734 — Jégou et al., FAISS]

> **Composite compression.** Scalar, PQ и binary можно комбинировать: см. [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §10 (MRL + INT8 / PQ / binary multipliers). Memory savings are configuration-dependent; the examples in this section range from 12× to 384× depending on embedding dimension, code width, and composition.

### §6.1. Worked example — quantisation choice для 10M × 768-dim

Типичный production-corpora: 10 миллионов эмбеддингов, 768-dim (BGE-base / E5-base / similar).

```text
Float32 baseline:
  10M × 768 × 4 bytes  = 30,720,000,000 bytes ≈ 28.6 GB

Scalar INT8:
  10M × 768 × 1 byte   =  7,680,000,000 bytes ≈  7.2 GB    (4× compression)
  Quality: ~98% (typical literature claim; см. caveats в binary-embeddings-roadmap §6)

Product Quantisation (m=8, K=256):
  10M × 8 bytes        =     80,000,000 bytes ≈ 76 MB      (~384× compression)
  + k-means centroids  = 8 × 256 × 768 × 4 bytes ≈ 6 MB (codebook)
  Total ≈ 82 MB
  Quality: ~95% (sensitive to calibration set; requires retraining)

PQ + scalar residual quantisation:
  ~similar memory, ~99% quality — но двойной цикл обучения, существенно дороже

Binary (256-bit, autoencoder):
  10M × 32 bytes       =    320,000,000 bytes ≈ 305 MB    (~96× compression)
  + decoder matrix     = 256 × 768 × 4 bytes  ≈ 768 KB
  Quality: ~95% (hypothesis, требует calibration)
```

Из этой таблицы видно, что **PQ при m=8 даёт самый агрессивный compression** (~384×) при минимальной in-memory структуре, но требует K-means calibration set и ADC distance tables. Scalar INT8 даёт 4× compression при нулевой calibration cost — хороший default для moderate workloads.

> **Note:** 384× is **raw code compression** — the size of the quantized code alone. Full end-to-end storage footprint includes codebooks, vector IDs, index structure (HNSW graph, IVF list, etc.), and optional storage of the original vectors. Real-world ratios for total storage are typically 2–4× lower than the raw code ratio.

Если memory budget позволяет 5-10 GB, scalar INT8 — самый безопасный выбор; если 1-3 GB — PQ обязателен; если <500 MB — composite (MRL truncate + PQ, см. [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §10).

[Source: https://www.youtube.com/watch?v=v-EX_AYdolE — Феоктистов Станислав (AIRnD)]
<br>[Source: arXiv:1702.08734 — Jégou et al., FAISS]

## §7. HNSW vs IVF vs ANNOY для нашего стека

Когда какой алгоритм правильный выбор для workload'ов `agent-memory-cpp`:

| Workload | Рекомендованный алгоритм | Почему |
|---|---|---|
| Medium RAG (~1M-10M vectors, mixed queries) | **HNSW** (~80% дефолт) | Log-scale latency, fast insert, no reclustering overhead. Post-filter на metadata допустим, если selectivity высокая. |
| Full metadata filtering на больших коллекциях | **IVF + PQ** | IVF позволяет отбросить clusters целиком до сканирования; PQ экономит память. Re-clustering cadence — workload-dependent: на static corpora может быть «никогда»; на streaming-данных — чаще. |
| Billion-scale с жёстким memory budget | **IVF + PQ (m=8-16)** | Только PQ с ADC даёт нужную память; HNSW на 1B+ vectors экономически нецелесообразен без PQ. |
| Edge / mobile coarse filter | **Binary (LSH или autoencoder)** | Binary codes — single-instruction XOR+POPCNT distance, минимальная RAM footprint. См. [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md). |
| Historical / read-only | **ANNOY** | Tree можно не держать в RAM, подгружать с диска по требованию. Но часто вытесняется HNSW/IVF. |

[Source: https://www.youtube.com/watch?v=v-EX_AYdolE — Феоктистов Станислав (AIRnD)]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/ANN-алгоритмы для векторного поиска — HNSW, FAISS, DiskANN, ScaNN, фильтрация.md]

## §8. Integration with our MDBX layer

Важное архитектурное разграничение: **Vector Stores — внешние компоненты**, не часть нашего MDBX-слоя.

```text
agent-memory-cpp MDBX layer (envelope + components + projections)
  → owns: scope keys, envelopes, lexical DBI, components, Graph DBIs
  → does NOT own: ANN index, vector embeddings, HNSW/IVF state

External Vector Store (Qdrant / Milvus / Pinecone / Weaviate / Chroma)
  → owns: dense embeddings, ANN index, quantisation state
  → does NOT own: scope keys, lexical scoring, decay policies, anti-loop cooldown
```

Граница проводится через `DenseVectors` capability и `DenseIndexMode` enum (см. [`optimization-roadmap.md`](optimization-roadmap.md) §"Dense Index Modes"):

- `DenseIndexMode::Exact` — brute-force over float rerank (small corpora).
- `DenseIndexMode::BinaryCandidateFilter` — binary prefilter (signatures / LSH / autoencoder), float rerank сверху.
- `DenseIndexMode::BinaryOnly` — standalone binary (no float rerank).
- `DenseIndexMode::ApproximateVector` — binary + decoder → approximate float → cosine rerank.
- `DenseIndexMode::Hnsw` — mainline M2+ backend (композиция с BinaryCandidateFilter).

На стороне Vector Store мы только **публикуем embeddings** через C++17 ABI / IPC и **принимаем top-K candidates**. Scope isolation, decay, anti-loop cooldown, lexical scoring остаются в MDBX-слое.

### §8.1. Связь с conceptual framing

Это разделение — пример compression framing из [`compression-is-intelligence-roadmap.md`](compression-is-intelligence-roadmap.md) §4: Vector Store отвечает за candidate prefilter и ANN-ускорение (см. §6 там — check-questions Q1, Q2), но **не** отвечает за Q3 (source vs interpretation), Q4 (sequence), Q5 (provenance), Q6 (reproducibility). Эти responsibility остаются в MDBX-слое через `envelope.revision`, `SourceRef`, sequence reconstruction.

### §8.2. Когда поднимать свой Vector Store, а когда managed

Decision sub-matrix внутри §8:

| Сценарий | Self-host (Qdrant / Milvus / Weaviate) | Managed (Pinecone) |
|---|---|---|
| Данные регулируются (GDPR / ФЗ-152 / health data) | ✅ данные on-prem | ❌ egress / DPA overhead |
| Есть DevOps-команда ≥ 2 FTE | ✅ типично | ❌ overkill |
| Нет DevOps / MVP | ⚠️ Qdrant умеренно, Milvus дорого | ✅ Pinecone zero DevOps |
| Cost ceiling ≤ $500/mo на 100M vectors | ✅ OSS self-host | ❌ managed premium |
| Cost ceiling $1K-$10K/mo на 100M vectors | ⚠️ Milvus требует cluster | ✅ Pinecone |
| Latency SLA <50 ms p95 | ✅ single-node Qdrant | ✅ managed Pinecone |
| Custom ann-параметры (m, efConstruction, k-means K) | ✅ полный контроль | ❌ managed скрывает параметры |
| Embedding drift / re-indexing каждую неделю | ✅ scripted pipeline | ✅ managed handles |

Практический default для `agent-memory-cpp`: **Qdrant self-host** для production workload'ов, **Chroma** для прототипов и learning, **Pinecone** только когда нет DevOps-команды.

## §10. Open questions

1. **Hybrid Search бенчмарки** — конкретные замеры Qdrant vs Milvus vs Weaviate hybrid search на одинаковых условиях в нашем стеке. Playbook не покрывает.
2. **Pinecone стоимость на разных объёмах** — актуальные цены зависят от region, pod type, throughput. Решение «managed vs self-host» по cost ceiling требует свежего quote.
3. **GPU acceleration** — поддержка GPU acceleration в каждой БД (Qdrant GPU support, Milvus GPU index, FAISS-GPU). Не покрыто в этом гайде; выбор CPU vs GPU зависит от конкретного workload'а.
4. **Sharding consistency guarantees** — eventual consistency vs strong consistency для replicated Qdrant / Milvus shards. Влияет на auditability (см. capability `Auditability` в [`usage-memory-models.md`](usage-memory-models.md) §3).
5. **Migration между Vector Store** — реальная сложность миграции Qdrant → Milvus (или обратно) при изменении scale profile. Схема миграции embedding'ов и индексов не стандартизирована.
6. **Composite compression (MRL + PQ + INT8) на production** — эффективность composite compression зависит от embedding model и corpus. Конкретные замеры для нашего workload'а отсутствуют. См. [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §10.

## §9. References

### 9.1. Public sources

- **Феоктистов Станислав (AIRnD, 2025). "Инженерный взгляд на RAG: сравнение векторных баз и алгоритмов."** Доклад на канале «Клуб разработчиков СПб» (devclubspb). URL: <https://www.youtube.com/watch?v=v-EX_AYdolE>. Покрывает: scalar vs product quantisation (4× vs ~96×), HNSW vs IVF vs ANNOY tradeoff, Chroma / Qdrant / Milvus / Pinecone / Weaviate comparison, три практических кейса (NoteBase startup, Old Russian metric books, product catalog с high QPS).
- **Jégou, Hervé; Douze, Matthijs; Schmid, Cordelia (2011); Johnson, Jeff; Douze, Matthijs; Jégou, Hervé (2017). "Billion-scale similarity search with GPUs."** arXiv:1702.08734 (FAISS). URL: <https://arxiv.org/abs/1702.08734>. Product Quantisation + asymmetric ADC distance.
- **Официальные сайты Vector Store:** <https://www.trychroma.com/>, <https://qdrant.tech/>, <https://milvus.io/>, <https://www.pinecone.io/>, <https://weaviate.io/>.

### 9.2. Internal notes (ai-agent-playbook)

- `ai-agent-playbook/resources/rag-knowledge/Инженерный взгляд на RAG - сравнение векторных баз и алгоритмов - расшифровка.md` (internal note) — расшифровка доклада Феоктистова с главами видео, извлечёнными параметрами (квантование, HNSW/IVF/ANNOY, Chroma/Qdrant/Milvus/Pinecone/Weaviate, три практических кейса, открытые вопросы).
- `ai-agent-playbook/concepts/llm-research/ANN-алгоритмы для векторного поиска — HNSW, FAISS, DiskANN, ScaNN, фильтрация.md` (internal note) — concept-summary по ANN-алгоритмам (HNSW, FAISS, DiskANN, ScaNN), фильтрация, trade-offs. Гайд по Vector Store ссылается на этот source для подробностей по алгоритмам.

### 9.3. In-house guides

- [`optimization-roadmap.md`](optimization-roadmap.md) — vector math baseline, optional Eigen adapter, SIMD dispatch (SSE4.2 / AVX2 / AVX-512), `HammingTopK` kernel, encoder registry, `DenseIndexMode` (Exact / BinaryCandidateFilter / BinaryOnly / ApproximateVector / Hnsw).
- [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) — typology of retrieval techniques (Naive / Advanced / Hybrid / Contextual / Graph / Fusion / Adaptive / Agentic / RLM); какой retriever поверх какого vector store.
- [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) — binarisation landscape (sign / autoencoder / LSH / PQ), SIMD-accelerated distance, hybrid binary + dense, composite compression (MRL + INT8 / PQ / binary).
- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) §6 — capability matrix; `DenseVectors` capability, `BinaryCandidateFilter` / `Hnsw` mode selection.
- [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) §5.5/§5.5.1 — capability-aware physical MDBX manifest and DBI budget.
- [`related-projects.md`](related-projects.md) — внешние сравнения Vector Store и benchmark suites (если есть).
- [`compression-is-intelligence-roadmap.md`](compression-is-intelligence-roadmap.md) — conceptual backbone (prediction ↔ compression equivalence, "7 check-questions for compression quality", "operational > general"); см. §4 «For RAG» и §6 application matrix для понимания, что Vector Store отвечает и за что не отвечает.
