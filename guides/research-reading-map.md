# research-reading-map.md

Curated bibliography с маппингом paper → roadmap decision для `agent-memory-cpp`. Документ фиксирует research-direction markers и обосновывает архитектурные решения через научную литературу.

> Не comprehensive bibliography. Только papers, которые непосредственно информировали решения в roadmap'ах проекта. Reading order — по разделам (от foundational к experimental).

## Related resources

Этот документ покрывает academic papers (curated bibliography с paper → roadmap decision mapping).
Для open-source projects (FAISS, hnswlib, USearch, sqlite-vec, mem0, Graphiti, Cognee, Letta и др.) см. `guides/related-projects.md` с cross-project benchmark plan.

## 1. Lexical Retrieval

### BM25 / BM25F

- **Paper**: arXiv:0911.5046 — "Integrating the Probabilistic Models BM25/BM25F into Lucene".
- **Roadmap decision**: Fielded BM25F в `lexical-search-roadmap.md` (10 field_ids, default weights, BM25F по SearchProjection DBI).
- **Зачем**: Основа лексического поиска; BM25F для структурированных документов (title/heading/body/code/symbol/qa).

### SPLADE / SPLADE v2 (M2+)

- **Paper**: arXiv:2107.05720 — "SPLADE: Sparse Lexical and Expansion Model for First Stage Ranking".
- **Roadmap decision**: Learned sparse adapter slot в `lexical-search-roadmap.md` (M2+ optional, external backend).
- **Зачем**: Семантическое расширение поверх inverted index. Совместимо с BM25F pipeline.

### ColBERT / ColBERTv2 (M2+)

- **Paper**: arXiv:2004.12832 — "ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT".
- **Roadmap decision**: Late interaction adapter slot в `lexical-search-roadmap.md` (M2+ optional, multi-vector per chunk).
- **Зачем**: Token-level vectors для fine-grained matching. ColBERTv2 уменьшает footprint на 6-10x.

### Sparse vs Dense fusion

- **Paper**: arXiv:2205.00235 — "To Interpolate or not to Interpolate: PRF, Dense and Sparse Retrievers".
- **Roadmap decision**: HybridRetrievalConfig + RRF fusion в `policies-roadmap.md` и `lexical-search-roadmap.md`.
- **Зачем**: Эмпирическое обоснование RRF vs weighted vs interpolation.

## 2. Dense Retrieval

### DPR (Dense Passage Retrieval)

- **Paper**: arXiv:2004.04906 — "Dense Passage Retrieval for Open-Domain Question Answering".
- **Roadmap decision**: IEmbedder + IVectorIndex interfaces в `memory-stacks-roadmap.md` и `optimization-roadmap.md`.
- **Зачем**: Dual-encoder baseline; независимое encoding query и document.

### REALM

- **Paper**: arXiv:2002.08909 — "REALM: Retrieval-Augmented Language Model Pre-Training".
- **Roadmap decision**: Memory как внешняя parametric memory; informed `KnowledgeUnitEnvelope` spec.
- **Зачем**: Концептуальное обоснование external memory + LM.

### BEIR Benchmark

- **Paper**: arXiv:2104.08663 — "BEIR: A Heterogenous Benchmark for Zero-shot Evaluation of Information Retrieval Models".
- **Roadmap decision**: Golden dataset methodology в `knowledge-base-roadmap.md` (heterogeneous query types).
- **Зачем**: Standard zero-shot eval; показывает что BM25 остаётся сильным baseline.

### RAG (Retrieval-Augmented Generation)

- **Paper**: arXiv:2005.11401 — "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks".
- **Roadmap decision**: ContextBuilder contract в `knowledge-base-roadmap.md`.
- **Зачем**: Classical parametric + non-parametric memory схема; обоснование retrieval-augmented LLM patterns.

## 3. ANN Indexes

### HNSW

- **Paper**: arXiv:1603.09320 — "Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs".
- **Roadmap decision**: HnswVectorIndex как 5-й IDenseIndex mode (M2+ experimental) в `optimization-roadmap.md`.
- **Зачем**: Mainstream in-memory ANN. M-level graph, efConstruction/efSearch параметры.

### FAISS (Billion-scale)

- **Paper**: arXiv:1702.08734 — "Billion-scale similarity search with GPUs".
- **Roadmap decision**: Architectural inspiration для IEmbeddingCodec + index factory pattern. Не прямое использование (не хотим GPU dependency).
- **Зачем**: Codec separation, training stage, search params interface.

### FreshDiskANN (M3+)

- **Paper**: arXiv:2105.09613 — "FreshDiskANN: A Fast and Accurate Graph-Based ANN Index for Streaming Similarity Search".
- **Roadmap decision**: Out of M0/M1/M2 scope. Mention в `optimization-roadmap.md` как future research.
- **Зачем**: Disk-based ANN с real-time insert/delete для billion-scale. Не сейчас, но архитектурно полезно.

### ANN-Benchmarks

- **Paper**: arXiv:1807.05614 — "ANN-Benchmarks: A Benchmarking Tool for Approximate Nearest Neighbor Algorithms".
- **Roadmap decision**: Benchmark methodology в `optimization-roadmap.md` (microbenchmark suite).
- **Зачем**: Recall/latency/params methodology для сравнения ANN implementations.

### Big ANN Challenge (NeurIPS 2023)

- **Paper**: arXiv:2409.17424 — "Results of the Big ANN: NeurIPS'23 competition".
- **Roadmap decision**: Research direction marker — filtered search, sparse search, streaming, OOD datasets.
- **Зачем**: Modern challenges для ANN; не только "cosine top-k".

## 4. Vector Compression

### Product Quantization (PQ)

- **Paper**: arXiv:1702.08734 (FAISS line) — same as §3.
- **Roadmap decision**: ProductQuantizationCodec как M2+ IEmbeddingCodec implementation в `optimization-roadmap.md`.
- **Зачем**: 8-32x compression через sub-vector K-means + LUT distance.

### Matryoshka Representation Learning

- **Paper**: arXiv:2205.13147 — "Matryoshka Representation Learning".
- **Roadmap decision**: MatryoshkaTruncationCodec как M2+ IEmbeddingCodec implementation в `optimization-roadmap.md`.
- **Зачем**: First N coordinates уже осмысленный embedding. 14x size reduction.

### FastText.zip (PQ example)

- **Paper**: arXiv:1612.03651 — "FastText.zip: Compressing text classification models".
- **Roadmap decision**: Reference для PQ engineering practice.
- **Зачем**: Industrial PQ в текстовых моделях; показывает engineering value.

### Deep Text Hashing Survey

- **Paper**: arXiv:2510.27232 — "A Survey on Deep Text Hashing: Efficient Semantic Text Retrieval with Binary Representation".
- **Roadmap decision**: Reference для binary encoder research в `optimization-roadmap.md`.
- **Зачем**: Comprehensive overview binary/hamming retrieval.

### BGE-M3 (multimodal embedder)

- **Paper**: arXiv:2402.03216 — "BGE M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation".
- **Roadmap decision**: Multi-modal embedding не в core scope. Mention как reference для единого embedder API.
- **Зачем**: Dense + sparse + multi-vector в одной модели; полезно для API design.

## 5. Autoencoder Binarization

### Near-lossless Binarization of Word Embeddings

- **Paper**: arXiv:1803.09065 — "Near-lossless Binarization of Word Embeddings".
- **Roadmap decision**: AutoencoderBinarySignatureEncoder в `optimization-roadmap.md` (M2 experimental). IBinarySignatureEncoder interface.
- **Зачем**: ~97% size reduction, ~2% accuracy loss, ~30x speedup. Training offline/Python, C++ inference only.

## 6. Graph Retrieval

### GraphRAG (Microsoft)

- **Paper**: arXiv:2404.16130 — "From Local to Global: A Graph RAG Approach to Query-Focused Summarization".
- **Roadmap decision**: CommunitySummaryJob как M2+ compaction job в `compaction-roadmap.md`.
- **Зачем**: Entity graph + community summaries для global sensemaking questions.

### LightRAG

- **Paper**: arXiv:2410.05779 — "LightRAG: Simple and Fast Retrieval-Augmented Generation".
- **Roadmap decision**: Dual-level retrieval (low-level + high-level) reference.
- **Зачем**: Incremental update pattern; efficient graph-based RAG.

### HippoRAG

- **Paper**: arXiv:2405.14831 — "HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models".
- **Roadmap decision**: Personalized PageRank on KG как research direction (не в core scope).
- **Зачем**: 10-30x cheaper than iterative retrieval. Neuro-inspired.

### STaRK / HybGRAG (Benchmarks)

- **Paper**: arXiv:2404.13207 (STaRK), arXiv:2412.16311 (HybGRAG).
- **Roadmap decision**: Benchmarks для hybrid text + relational KB evaluation.
- **Зачем**: Standardized benchmarks для graph-augmented RAG.

## 7. Context Compression

### RECOMP

- **Paper**: arXiv:2310.04408 — "RECOMP: Improving Retrieval-Augmented LMs with Compression and Selective Augmentation".
- **Roadmap decision**: IContextCompressor hook (M2+) в `knowledge-base-roadmap.md`. Extractive / abstractive / selective modes.
- **Зачем**: Post-retrieval compression снижает tokens/cost/latency. Может вернуть empty context.

### LLMLingua / LongLLMLingua

- **Paper**: arXiv:2310.05736 — "LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models".
- **Roadmap decision**: Prompt compression как M2+ extension к PromptCache.
- **Зачем**: До 20x compression. Полезно для long-context LLMs.

## 8. Memory Structures

### RAPTOR

- **Paper**: arXiv:2401.18059 — "RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval".
- **Roadmap decision**: SummaryTreeJob как M2+ compaction job в `compaction-roadmap.md`. Leaf chunks + summary nodes tree.
- **Зачем**: Recursive summarization tree. Multi-level retrieval на разных уровнях абстракции.

## 9. Adaptive Retrieval

### HyDE

- **Paper**: arXiv:2212.10496 — "Precise Zero-Shot Dense Retrieval without Relevance Labels".
- **Roadmap decision**: IQueryTransformer hook (M2+) в `memory-stacks-roadmap.md`. HyDE — конкретная реализация через adapter.
- **Зачем**: Hypothetical document embeddings для zero-shot improvement.

### Rewrite-Retrieve-Read

- **Paper**: arXiv:2305.14283 — "Query Rewriting for Retrieval-Augmented Large Language Models".
- **Roadmap decision**: IQueryTransformer hook. QueryRewrite — реализация.
- **Зачем**: User query часто не похож на нужные документы; rephrase.

### Self-RAG

- **Paper**: arXiv:2310.11511 — "Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection".
- **Roadmap decision**: IRetrievalEvaluator hook (M2+). Self-critique retrieved passages.
- **Зачем**: LLM решает когда retrieve нужен; критика retrieved chunks.

### CRAG (Corrective RAG)

- **Paper**: arXiv:2401.15884 — "Corrective Retrieval Augmented Generation".
- **Roadmap decision**: IRetrievalEvaluator hook. Lightweight evaluator decides correctness.
- **Зачем**: Corrective action если retrieval нерелевантен. Decoupled от LLM.

### LLM Page Relevance Classification

- **Source**: `ai-agent-playbook/concepts/rag-knowledge/RAG без эмбеддингов — LLM-классификация страниц вместо similarity search.md`.
- **Roadmap decision**: M2+ external retriever / page-scanning adapter lane.
  Primary mode is candidate generation (`query + page corpus -> relevant
  pages`), not `IRetrievalEvaluator`. A separate evaluator mode may re-check
  a bounded candidate pool after BM25F/dense retrieval. C++ core owns schemas,
  metrics, and fallback policy, not the LLM classifier runtime.
- **Зачем**: Exact-number/date/condition workloads where embedding similarity
  misses answer-bearing pages. Must be benchmarked against BM25F+dense+rerank.

### Document Expansion / doc2query

- **Source**: doc2query / docT5query document expansion family; pin the exact
  primary paper(s) before implementation. Internal discovery note:
  `ai-agent-playbook/concepts/rag-knowledge/Learned sparse retrieval — SPLADE семейство и гибридный sparse+dense.md`.
- **Roadmap decision**: M2+ pre-index enrichment job producing derived
  `SearchProjection`s. Generated projections must be materialized and
  versioned with generator identity, prompt/config hash, source revision,
  projection kind, artifact hash, and regeneration policy; evaluate separately
  from Contextual Retrieval prefixes.
- **Зачем**: Bridge query/document vocabulary mismatch before heavier learned
  sparse backends are enabled.

## 9A. Agent Memory Governance

### Memory for Autonomous LLM Agents

- **Paper**: arXiv:2603.07670 — "Memory for Autonomous LLM Agents:
  Mechanisms, Evaluation, and Emerging Frontiers".
- **Roadmap decision**: M2+ lifecycle-governance roadmap:
  `memory-lifecycle-governance-roadmap.md` AM-13..AM-18. Use the paper as a
  framing reference for write/manage/read policy separation, contradiction and
  staleness handling, privacy/deletion correctness, and evaluation beyond
  static retrieval metrics.
- **Зачем**: It connects memory mechanics with autonomous-agent workloads, where
  retrieval quality, temporal correctness, management actions and governance
  must be evaluated together.

### ToM / Mind Modeling Sources

- **Papers**: arXiv:2510.21903 (ToM-SWE) and arXiv:2605.10306 (Mind Modeling).
- **Roadmap decision**: Out-of-core for `agent-memory-cpp`; useful for ADELIA or
  runtime sidecars that consume evidence-backed memory records. The core stores
  observations, claims, provenance, policies and traces; it does not own hidden
  mental-state modeling.
- **Зачем**: Keeps the storage/retrieval core ethically and architecturally
  narrower while preserving a path for personalization layers.

## 10. Implementation Phases

Phase 1 — retrieval baseline:
  ExactVectorIndex, BM25, RRF(BM25, ExactVector), eval pipeline.

Phase 2 — speed:
  HNSW, scalar quantized vectors (int8/fp16), benchmark vs exact.

Phase 3 — compression:
  BinaryVectorCodec (autoencoder), Matryoshka truncation, optional PQ prototype.

Phase 4 — memory structures:
  SummaryProjection tree (RAPTOR), entity graph (entity ↔ unit), Graph traversal retriever.

Phase 5 — adaptive retrieval:
  query rewrite hook, HyDE hook, retrieval evaluator hook, context compressor hook.

Phase 5a — playbook follow-ups:
  history-aware rewrite fixtures, LLM page relevance retriever adapter lane,
  doc2query-style expansion projections, citation-first regulated-doc gates,
  CRAG contract clarification through the existing M2+ evaluator lane.

## 11. References Mapping

Прямой маппинг paper → file:

| Paper | Roadmap file | Section |
|---|---|---|
| arXiv:1803.09065 (Binarization) | optimization-roadmap.md | AutoencoderBinarySignatureEncoder |
| arXiv:1603.09320 (HNSW) | optimization-roadmap.md | IDenseIndex modes (M2+) |
| arXiv:2005.11401 (RAG) | knowledge-base-roadmap.md | ContextBuilder |
| arXiv:2004.04906 (DPR) | memory-stacks-roadmap.md | IEmbedder interface |
| arXiv:2104.08663 (BEIR) | knowledge-base-roadmap.md | Eval pipeline |
| arXiv:0911.5046 (BM25F) | lexical-search-roadmap.md | Fielded BM25F |
| arXiv:2107.05720 (SPLADE) | lexical-search-roadmap.md | M2+ sparse adapter |
| arXiv:2004.12832 (ColBERT) | lexical-search-roadmap.md | M2+ late interaction |
| arXiv:2205.00235 (Interpolation) | policies-roadmap.md | HybridRetrievalConfig |
| arXiv:1702.08734 (FAISS) | optimization-roadmap.md | Codec architecture |
| arXiv:2205.13147 (Matryoshka) | optimization-roadmap.md | MatryoshkaTruncationCodec |
| arXiv:1612.03651 (FastText.zip) | optimization-roadmap.md | PQ engineering reference |
| arXiv:2404.16130 (GraphRAG) | compaction-roadmap.md | CommunitySummaryJob |
| arXiv:2410.05779 (LightRAG) | knowledge-base-roadmap.md | Dual-level retrieval reference |
| arXiv:2310.04408 (RECOMP) | knowledge-base-roadmap.md | IContextCompressor hook |
| arXiv:2310.05736 (LLMLingua) | knowledge-base-roadmap.md | Prompt compression |
| arXiv:2401.18059 (RAPTOR) | compaction-roadmap.md | SummaryTreeJob |
| arXiv:2212.10496 (HyDE) | memory-stacks-roadmap.md | IQueryTransformer hook |
| arXiv:2305.14283 (Rewrite-Retrieve-Read) | memory-stacks-roadmap.md | IQueryTransformer hook |
| LLM page relevance classification | retrieval-techniques-roadmap.md | M2+ retriever/page-scanning adapter lane |
| doc2query / document expansion | chunkers-roadmap.md | M2+ materialized derived SearchProjection |
| arXiv:2603.07670 (Agent memory survey) | memory-lifecycle-governance-roadmap.md | AM-13..AM-18 |
| arXiv:2510.21903 / arXiv:2605.10306 (ToM / Mind Modeling) | memory-lifecycle-governance-roadmap.md | Out-of-core ADELIA/runtime boundary |
| arXiv:2310.11511 (Self-RAG) | memory-stacks-roadmap.md | IRetrievalEvaluator hook |
| arXiv:2401.15884 (CRAG) | memory-stacks-roadmap.md | IRetrievalEvaluator hook |

## 12. Open Research Questions

- FreshDiskANN-style disk-based ANN для on-disk storage mode (M3+).
- Adaptive RAG: model-driven выбор retrieval strategy per query.
- Multi-modal retrieval (text + code + image) — отдельный scope.
- Hierarchical memory structures beyond RAPTOR.
- Online learning / continuous indexing под live data streams.
