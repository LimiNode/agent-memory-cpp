# related-projects.md

Landscape map для agent-memory-cpp: позиционирование, sister library references, cross-project benchmark plan. Companion к `guides/research-reading-map.md` (academic papers) и `guides/memory-stacks-roadmap.md` (canonical spec).

## 1. Позиционирование

agent-memory-cpp — embedded C++17 memory/retrieval engine для AI agents с:
  - MDBX storage.
  - Typed KnowledgeUnits (Chunk / QAPair / Fact / Entity / Relation / Event / Summary / ConversationEpisode / CompiledArticle / Task / Decision).
  - Capability-aware MemoryProfileSpec (BasicRag / AgentLTM / CompiledWiki и др.) с per-stack DBIs.
  - MDBX-based projection-version-safe indexes (`LexicalPosting.projection_version`).
  - BM25/vector/graph/temporal retrieval с multi-mode dense index (Exact / BinaryCandidateFilter / BinaryOnly / ApproximateVector / Hnsw).
  - Optional encoders (RandomHyperplaneLSH / AutoencoderBinarizer).
  - CompactionWorker с 9 job types.
  - Cross-cutting runtime services.

Niche:
  - НЕ direct конкурент mem0/Cognee/Letta/Zep как end-user platform.
  - LOW-LEVEL embeddable core, может быть storage/retrieval layer для таких систем.
  - Bench-target для embedded/local-first сценариев, где Python-платформа не подходит.

Direct differentiators:
  - C++17 static library, zero Python deps.
  - Capability-aware MemoryProfileSpec с 7 готовыми спецификациями.
  - Multi-mode IDenseIndex (5 modes) с per-mode DBI mapping.
  - Per-unit revision-stale-filter (не generation-based).
  - MemoryStack открытие одной операцией (path + spec) с capability-aware DBI creation.

Disclaimer:
Документ описывает целевую архитектуру и benchmark-позиционирование. Часть capabilities
относится к M1/M2/M2+ roadmap, не к текущей реализации. Реальное наличие и качество
каждой capability проверяется через benchmark results (когда будет готов M1 eval pipeline).

## 2. Direct competitors / sister projects (closest in API surface)

| Project | Layer | Primary focus | Differentiator vs agent-memory-cpp |
|---|---|---|---|
| mem0 | Python SDK / server | Universal memory layer для AI agents. Multi-level memory, user/session/agent state, hybrid search, BM25, entity extraction, temporal reasoning. | agent-memory-cpp НЕ SDK; C++17 core. mem0 = продукт/обёртка, мы = primitive для embedding. |
| Letta / MemGPT | Python платформа | Stateful agents с advanced memory + runtime + self-improve. | Letta = agent runtime platform. Мы = memory/retrieval core без agent loop. Потенциальный customer: наш core как storage backend для Letta. |
| Graphiti / Zep | Python library | Temporal context graphs для AI agents. Bi-temporal facts, provenance/source data, episodes, incremental updates, hybrid retrieval, deterministic-first dedupe. | **Самый близкий конкурент по temporal+provenance части**. Python-only, не embedded. Наша ниша: embedded C++17; current M1 uses `TemporalComponent` + `GraphEdge`, while Graphiti-style bi-temporal semantics are tracked as M2+ AM-13, the optional `TemporalContextGraphMemory` profile is AM-22, and entity-resolution/query-safety lessons are tracked as AM-19..AM-21. |
| Cognee | Python platform | Self-hosted AI memory platform с persistent long-term memory, knowledge graph, vector embeddings, graph reasoning, ontology generation. | Cognee = Python платформа с UI/ingestion. Мы = storage/retrieval core для embedding в C++ apps. |
| A-MEM / AgenticMemory | Research prototype | Zettelkasten-style agentic memory: dynamic organization, notes, structured attributes, links, memory evolution. (arXiv:2502.12110) | Research-grade, не production. Inspirational для memory evolution (CompactionWorker + SummaryTreeJob). |
| LightRAG | Python framework | Dual-level graph + vector RAG (EMNLP 2025). | LightRAG = document RAG framework, не typed agent memory. Совпадает по hybrid retrieval pattern (graph + vector + RRF). |
| Microsoft GraphRAG | Python pipeline | Modular graph-based RAG для corpora. Community detection, global questions. | Подобен для community detection + global questions (CommunitySummaryJob reference). Мы ближе к agent memory, GraphRAG — к document corpora. |
| LlamaIndex | Python framework | Document agent + OCR + indices/graphs/retrievers/query engines/reranking. | LlamaIndex = огромный application framework. Совпадает по retriever interface pattern (IRetriever, HybridRetriever). Не конкуренты, разные abstraction levels. |
| codebase-memory-mcp (research source / pattern donor, не direct competitor) | C static-binary MCP server | Code intelligence через tree-sitter + Hybrid LSP + SQLite-graph (14 MCP tools, ~162 KB pure C). | НЕ direct competitor (different scope: code intelligence, не agent memory). Изучен как pattern donor — 9 конкретных engineering patterns (MinHash + LSH, RotSQ quantization, coverage shadow graph, atomic shared ID, bounded BFS, team-shared artifact, Cypher subset, AC-over-LZ4, adaptive-poll watcher). См. детали и приоритеты в [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md). License: MIT. |

## 3. Sister library / adapter references

| Project | Что у нас похожего | Что можно позаимствовать |
|---|---|---|
| FAISS | IDenseIndex 5 modes. Codec registry. GPU k-selection. | Index factory pattern, codec registry, multi-threading per index. Не копировать API — брать архитектурные идеи. |
| hnswlib | HnswVectorIndex. | Header-only C++11 HNSW с incremental insert/update/delete. Adapter оборачивает hnswlib::HierarchicalNSW за нашим IDenseIndex interface. |
| USearch | IDenseIndex + binary signatures + SIMD HammingTopK strategy. | C++ HNSW + SIMD opt + binary Tanimoto/Sørensen similarities. Reference для Eigen/SIMD стратегии (AVX2/AVX-512 POPCNT). Disk-backed index experiments. |
| sqlite-vec | Embedded-C++ static library + vector search. Pure C, no deps, float/int8/binary. | Reference для embedded-архитектуры: vector search без external DB server. |
| Tantivy (Rust) | BM25 search, embedded-first. | Tokenization design, BM25 scoring. Cross-implementation reference для BM25 correctness. |
| Argos Translate | Optional cross-lingual ingestion/retrieval adapter. | Offline package identity, model digest provenance and pivot path tracking for `TranslatedCanonical` projections. No Python dependency in core. |
| Gigatoken | High-throughput language-model tokenization and raw file tokenization pipeline. | Pattern donor for tokenizer-aware ingestion: file/bytes sources, compression, document boundaries, safe chunk splitting, persistent worker caches, and ingestion throughput benchmarks. No Rust/Python dependency in core. |

## 4. C++ embeddable memory stores (architectural reference)

| Project | Что у нас похожего | Что можно позаимствовать |
|---|---|---|
| DuckDB | Embedded analytical DB в одном файле. | "Embedded-first DB as library" deployment model. Single-file DB, no external server. Совпадает с нашим подходом. |
| SQLite ecosystem (FTS5, RTREE) | BM25 + spatial indexes в embedded DB. | FTS5 — BM25 implementation, posting structure. RTREE — spatial index design idea. |
| libmdbx / LMDB | MDBX под капотом нашего проекта. | Direct mdbx API. Single-process design. Уже используется через mdbx-containers обёртку. |
| RocksDB / LevelDB | LSM-tree embedded DB. | Write-optimized storage вариант для M3+ (если MDBX B-tree становится узким местом). Сейчас НЕ в scope. |

## 5. Cross-project benchmark plan (для M1+)

Когда MemoryStack готов к M1 eval:

**Retrieval primitives (datasets: BEIR / MS MARCO / bespoke):**
  - vs FAISS: ExactVectorIndex vs FAISS IndexFlatL2 (memory footprint, query latency, throughput).
  - vs FAISS: HnswVectorIndex vs FAISS IndexHNSWFlat (Recall@10 vs latency tradeoff, build time).
  - vs FAISS: BinaryOnlyIndex vs FAISS IndexBinaryFlat + IVF.
  - vs hnswlib: HnswVectorIndex (своя impl или adapter) vs hnswlib as reference HNSW implementation. Ground truth для ANN evaluation = ExactVectorIndex или FAISS IndexFlatL2 (per dataset).
  - vs sqlite-vec: ExactVectorIndex vs sqlite-vec на SQLite (как reference embedded vector search).
  - vs USearch: HnswVectorIndex vs USearch на тех же datasets (Recall@K curves).

**BM25 search:**
  - vs Tantivy (Rust) или Lucene (Java, JVM overhead отдельно): BM25 scoring throughput per document.
  - Использовать BEIR datasets для retrieval benchmark.

**Layer 2 integrational (не прямые benchmarks, но positioning):**
  - vs mem0 (Python): cross-language integration test (наш C++ + их Python SDK через gRPC/IPC). Демонстрирует что наш core доступен из Python как низкоуровневый backend.
  - vs Graphiti: temporal graph benchmark на их test dataset. Compare by feature coverage, update latency, query latency, provenance correctness, и temporal graph semantics против Graphiti-style workloads. Цель — калибровать наш implementation, не заранее доказать превосходство.

**Benchmark hypotheses / targets (to validate, not guaranteed):**
  - Retrieval@K: target 0.95–1.0× Recall@10 vs reference ANN (FAISS / hnswlib) при same latency budget.
    Реальная позиция определяется benchmark results.
  - BM25: измерить против Tantivy / Lucene. Конкретный target установить после
    validation tokenizer/scoring parity (например, идентичная tokenization, BM25 constants).
  - Storage: ожидаемое преимущество ТОЛЬКО для capability-aware profiles, которые не создают
    unused DBIs. Реальный выигрыш зависит от конкретного profile config.
  - Latency: гипотеза для validation. Zero-copy mmap + per-stack tuning могут помочь,
    но benchmark decides.

## 6. Architecture inspiration notes (что позаимствовать из каждого)

- **Graphiti / Zep**: bi-temporal context graph pattern с edge-level temporal metadata (valid_from / valid_until), episodes as source evidence, deterministic-first entity/edge dedupe and hybrid semantic+keyword+graph retrieval. Current M1 design has single-axis `TemporalComponent` + `GraphEdge`; Graphiti-style valid-time/recorded-time semantics are planned in [`memory-lifecycle-governance-roadmap.md`](memory-lifecycle-governance-roadmap.md) AM-13, with AM-19..AM-22 covering entity resolution, typed query/MCP safety, logical index separation and the optional temporal-context-graph profile/evaluation lane.
- **Cognee**: knowledge graph + community detection для "global questions". У нас CommunitySummaryJob (M2+, GraphRAG-style).
- **mem0**: explicit fact extraction с slot-based QA retrieval. У нас QALookup slot в QAKB profile.
- **Letta / MemGPT**: agent context block architecture (Persona + Memory + Recent). У нас ContextBlock / Context через ContextBuilder.
- **LlamaIndex**: retriever interface (IUnitRetriever), query engine (= HybridRetriever orchestration), exports для observability.
- **A-MEM**: memory evolution через compaction (Compaction + Merge + SummaryPromotion).
- **FAISS**: index factory pattern + codec registry для разных представлений векторов (Float / Float16 / Int8 / Binary / Matryoshka / PQ).
- **hnswlib**: header-only C++11 с incremental insert/delete (без build-then-load pattern). Adapter option для нашего HnswVectorIndex.
- **USearch**: SIMD-first design для binary embeddings (HammingTopK kernel).
- **DuckDB / SQLite-vec**: "single-file DB as library" deployment model — мы тоже.
- **Tantivy**: BM25 tokenization edge-cases (Cyrillic, CJK, identifiers in code).
- **Argos Translate**: offline translation package/pivot semantics for optional
  `ITranslationAdapter` and `TranslationProjectionMeta`; see
  [`translation-adapters-roadmap.md`](translation-adapters-roadmap.md).
- **Gigatoken**: tokenizer-aware raw ingestion pipeline and benchmark hygiene;
  see [`chunkers-roadmap.md`](chunkers-roadmap.md) §10 and
  [`experiments/2026-07-27-gigatoken-tokenization-reference.md`](experiments/2026-07-27-gigatoken-tokenization-reference.md).
- **Memory for Autonomous LLM Agents survey**: write/manage/read lifecycle,
  contradiction handling, privacy/deletion correctness and evaluation beyond
  static Recall@K; see
  [`memory-lifecycle-governance-roadmap.md`](memory-lifecycle-governance-roadmap.md).
- **Mem0**: entity linking, multi-signal retrieval, temporal retrieval and
  memory benchmark axes; useful as a comparison target, but its ADD-only public
  direction becomes a selectable `MemoryMutationPolicy`, not our universal
  storage rule.
- **ToM-SWE / Mind Modeling / MetaMind / Archon-style workflow sources**:
  useful for ADELIA and runtime layers; core `agent-memory-cpp` stores
  evidence and derived units, not application-owned mental models or workflow
  engines.

## 7. Open questions / roadmap items (из конкурентного анализа)

- M1: как наши MemoryStacks сравниваются с mem0/Cognee/Zep real-world performance на их test datasets?
- M2: Bi-temporal knowledge graph (Graphiti-style edge-level TTL) — нужен ли отдельный edge-level TTL job? (deferred M2+)
- M2+: Lifecycle governance AM-13..AM-18 — map bi-temporal validity,
  derivation/causal relations, progressive retrieval and mutation policy to
  concrete profile deltas and DBI budget before implementation.
- M2+: Graphiti/Zep follow-ups AM-19..AM-22 — entity-resolution fixtures,
  typed MCP/tool filter tests, logical-index separation and temporal-context-
  graph comparison parity before claiming Graphiti-style production readiness.
- M2: Community detection (Leiden/Louvain) для CommunitySummaryJob — брать готовую C++ библиотеку или своя реализация? (deferred M2+)
- M2: SPLADE / ColBERT adapters — пробовать интегрировать готовые реализации или LLM-distilled sparse vectors?
- M3: Distributed scope routing — у mem0 и Zep это multi-tenant. У нас пока single-process. (out of scope, research)
- M3: Embedded LLM distillation (compact embedding-LLM без external API) — uSearch-style или MiniLLM.
- См. также [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) для 9 engineering patterns, позаимствованных из `codebase-memory-mcp` (MIT): MinHash + LSH near-clone detection, RotSQ scalar quantization, coverage shadow graph, atomic shared ID generator, bounded BFS + schema introspection, team-shared graph artifact, Cypher read subset, Aho-Corasick over LZ4, adaptive-poll background watcher.

## 8. References (cross-link)

- guides/memory-stacks-roadmap.md — canonical spec.
- guides/research-reading-map.md — academic papers (24 curated, paper → roadmap decision).
- guides/optimization-roadmap.md — retrieval/index engineering.
- guides/knowledge-base-roadmap.md — retrieval + eval pipeline.
- guides/lexical-search-roadmap.md — BM25F + projections + Cyrillic morphology.
- guides/translation-adapters-roadmap.md — optional cross-lingual projections.
- guides/memory-lifecycle-governance-roadmap.md — Graphiti-inspired temporal
  context graph, entity resolution and typed query safety roadmap.
- ai-agent-playbook/concepts/ — общий агентский knowledge base (СВИНОПАС, Anna_AI, GraphRAG и др.).

## 9. External references

### Direct competitors:
- mem0: https://github.com/mem0ai/mem0
- Letta: https://github.com/letta-ai/letta
- Graphiti: https://github.com/getzep/graphiti
- Zep: https://github.com/getzep/zep
- Cognee: https://github.com/topoteretes/cognee
- A-MEM: https://arxiv.org/abs/2502.12110 (paper), GitHub: WujiangXu/AgenticMemory и agiresearch/A-mem
- LightRAG: https://github.com/HKUDS/LightRAG
- Microsoft GraphRAG: https://github.com/microsoft/graphrag
- LlamaIndex: https://github.com/run-llama/llama_index

### C++ sister libraries:
- FAISS: https://github.com/facebookresearch/faiss
- hnswlib: https://github.com/nmslib/hnswlib
- USearch: https://github.com/unum-cloud/usearch
- sqlite-vec: https://github.com/asg017/sqlite-vec
- Tantivy: https://github.com/quickwit-oss/tantivy

### Embedded storage reference:
- DuckDB: https://duckdb.org/
- libmdbx: https://github.com/erthink/libmdbx
- LMDB: https://github.com/LMDB/lmdb
- RocksDB: https://github.com/facebook/rocksdb

### Research source / pattern donor (not a direct competitor):
- codebase-memory-mcp: https://github.com/DeusData/codebase-memory-mcp (MIT; 9 patterns extracted in [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md))
- Argos Translate: https://github.com/argosopentech/argos-translate
- Gigatoken: https://github.com/marcelroed/gigatoken
