# Agent Memory C++

Embedded C++17 toolkit for building memory and retrieval systems for AI agents.

Agent Memory C++ provides modular components for persistent agent memory,
retrieval-augmented generation, semantic search, hybrid BM25/vector retrieval,
Markdown knowledge bases, and knowledge graphs.

The library is designed for local and embedded use. It does not require a
separate vector database server.

## Current status

The repository is in the early foundation stage. The first PR established a
static C++17 library target, CMake options, a smoke test, and a small example.
Follow-up PRs are adding stable domain primitives before storage, retrieval, and
embedding integrations.

## Goals

* Native C++17 API
* Embedded persistent storage
* Modular memory strategies
* Pluggable embedding backends
* Exact and approximate vector search
* BM25 and hybrid retrieval
* Markdown knowledge-base ingestion
* Targeted source reindexing
* Knowledge graphs and named entities
* Deterministic, inspectable storage
* No mandatory Python runtime
* Optional MCP and HTTP adapters

## Planned memory types

* Conversation history
* Sliding-window memory
* Summary memory
* Semantic memory
* Episodic memory
* Entity memory
* User and character profiles
* Procedural memory
* Markdown knowledge bases
* Graph-based memory
* Temporal memory
* Multi-scope agent memory

## Planned retrieval methods

* Exact vector search
* HNSW approximate nearest-neighbour search
* BM25 full-text search
* Hybrid BM25 + vector search
* Reciprocal Rank Fusion
* Metadata filtering
* Graph traversal
* Temporal filtering
* Reranking
* Query expansion
* Multi-stage retrieval

## Architecture

```text
Sources
|-- Markdown documents
|-- Chat messages
|-- Code and symbols
|-- Structured records
`-- Agent events
        |
        v
Ingestion
|-- Parsers
|-- Chunkers
|-- Metadata extraction
|-- Entity extraction
`-- Change detection
        |
        v
Indexes
|-- BM25
|-- Dense vectors
|-- Sparse vectors
|-- Knowledge graph
`-- Temporal indexes
        |
        v
Memory strategies
|-- Recent memory
|-- Semantic memory
|-- Summary memory
|-- Episodic memory
|-- Entity memory
`-- Composite memory
        |
        v
Retrieval and context building
        |
        v
LLM or AI agent
```

## Source layout

Headers and implementation files live side by side under `src/`:

```text
external/
|-- libmdbx/
`-- mdbx-containers/
src/
|-- agent_memory.hpp
`-- agent_memory/
    |-- chat.hpp
    |-- core.hpp
    |-- domain.hpp
    |-- embedding.hpp
    |-- eval.hpp
    |-- facts.hpp
    |-- index.hpp
    |-- infrastructure.hpp
    |-- ingestion.hpp
    |-- lexical.hpp
    |-- memory.hpp
    |-- retrieval.hpp
    |-- storage.hpp
    |-- embedding/
    |   |-- embedding_types.hpp
    |   |-- enums.hpp
    |   `-- IEmbedder.hpp
    |-- infrastructure/
    |   |-- mdbx.hpp
    |   `-- mdbx/
    |       |-- MdbxDocumentStorage.hpp
    |       `-- MdbxResourceManifestStorage.hpp
    `-- storage/
        |-- IDocumentStorage.hpp
        `-- IResourceManifestStorage.hpp
```

Consumers can include the full public aggregate through the include root:

```cpp
#include <agent_memory.hpp>
```

## Storage

The storage layer starts with dependency-free document/chunk contracts. The
initial concrete backend is planned around:

* [libmdbx](https://github.com/erthink/libmdbx)
* [mdbx-containers](https://github.com/NewYaroslav/mdbx-containers)

These source dependencies are kept flat as Git submodules under `external/`.
When MDBX support is enabled, the build adds `external/libmdbx` before
`external/mdbx-containers` so MDBXC can reuse the parent-provided MDBX target.

Storage interfaces are separated from memory and retrieval algorithms so that
additional backends can be added later.

When `AGENT_MEMORY_ENABLE_MDBX=ON`, the library also builds
`agent_memory/infrastructure/mdbx/MdbxDocumentStorage.hpp`, an MDBX-backed
implementation of `IDocumentStorage`. Optional infrastructure headers are not
included by the aggregate `agent_memory.hpp`; include the adapter header
directly when MDBX support is enabled.

## Embeddings

Embedding generation will be exposed through a backend-independent interface.
The project will provide its own embedding contracts instead of forking
chat/generation wrappers such as `cpp-llamalib`. The current contract layer
models embedding requests, vectors, model metadata, embedding purpose,
similarity metric, pooling mode, and batch embedding.

Planned backends:

* ONNX Runtime
* local E5 models
* llama.cpp
* OpenAI-compatible embedding APIs
* custom user-provided implementations

Example interface:

```cpp
class IEmbedder {
public:
    virtual ~IEmbedder();

    virtual const EmbeddingModelInfo& info() const noexcept = 0;

    virtual Embedding embed(const EmbeddingRequest& request) = 0;

    virtual std::vector<Embedding> embed_batch(
        const std::vector<EmbeddingRequest>& requests
    );
};
```

## Indexes

The index layer starts with dependency-free vector contracts. `IVectorIndex`
stores chunk embeddings and exposes nearest-neighbour search by query embedding,
result limit, and exact metadata filters. `ExactVectorIndex` provides a small
in-memory baseline implementation for deterministic tests and local use.

## Lexical search

BM25 and keyword search are planned as first-version retrieval capabilities for
exact technical terms, identifiers, paths, commands, and errors. The planned
token dictionary, postings layout, Unicode policy, raw resource store, and
hybrid retrieval direction are tracked in `guides/lexical-search-roadmap.md`.

## Retrieval

Retrieval contracts stay dependency-free and describe text, embedding, or mixed
queries with result limits and metadata filters. `IRetriever` returns ordered
scored chunks; concrete retrieval pipelines can compose embedders, indexes, and
document storage without leaking backend details into the public contract.

## Evaluation

The evaluation layer provides dependency-free dataset/run value types and metric
helpers for retrieval experiments. It can represent BEIR-style corpus, queries,
and relevance judgments, then compute Recall@K, MRR, nDCG@K, no-answer accuracy,
and latency summaries for BM25, exact vector, hybrid, and future approximate
search runs.

Current MRR is unbounded; MRR@K is reserved for follow-up benchmark runner
work once reporting requirements are clearer.

The metric helper treats implicit hit order as the vector order. Unordered
score dumps should be normalized by future importer/runner tooling before
evaluation. Corpus/qrels integrity checks are also reserved for dataset loader
validation rather than the metric-only helper.

## Ingestion

Resource indexing starts with a small orchestration layer over existing
contracts. `ResourceIndexer` accepts a pre-chunked resource snapshot, embeds its
chunks, upserts vector records, stores document state, and writes a resource
manifest for targeted reindexing.

## Resource reindexing

Future ingestion work will track resource ownership for derived records so one
source can be replaced without rebuilding the whole knowledge base. The intended
manifest and partial-reindex flow are tracked in
`guides/resource-reindexing.md`.

## Optimization roadmap

Follow-up tasks for text compression, optional Eigen/SIMD scoring, vector
encodings, binary signature bucket indexes, MDBX-backed approximate search, and
benchmark gates are tracked in `guides/optimization-roadmap.md`.

## Project status

The project is in the initial design and prototyping stage.

The first milestone will provide:

* MDBX-backed document storage
* Markdown ingestion
* multilingual E5 embeddings through ONNX Runtime
* exact cosine-similarity search
* BM25 search
* Reciprocal Rank Fusion
* semantic memory
* recent conversation memory
* a minimal context builder
* unit tests and benchmarks

## Non-goals for the first milestone

* Distributed vector database
* Multi-node replication
* Hosted embedding service
* General-purpose agent orchestration
* LLM inference engine
* Autonomous-agent framework

## Building

```bash
git clone --recursive https://github.com/<owner>/agent-memory-cpp.git
cd agent-memory-cpp

cmake -S . -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DAGENT_MEMORY_BUILD_TESTS=ON \
    -DAGENT_MEMORY_BUILD_EXAMPLES=ON

cmake --build build --config Release
ctest --test-dir build --build-config Release --output-on-failure
```

Available CMake options:

* `AGENT_MEMORY_BUILD_TESTS`
* `AGENT_MEMORY_BUILD_EXAMPLES`
* `AGENT_MEMORY_BUILD_BENCHMARKS`
* `AGENT_MEMORY_NEUROUTE_ENABLE_AVX2`
* `AGENT_MEMORY_ENABLE_WARNINGS`
* `AGENT_MEMORY_ENABLE_MDBX`
* `AGENT_MEMORY_MDBX_CONTAINERS_SOURCE_DIR`
* `AGENT_MEMORY_MDBX_DEPS_MODE`

`AGENT_MEMORY_ENABLE_MDBX` is `OFF` by default. When enabled, the build reuses
an existing `mdbx_containers::mdbx_containers` target, adds flat local
`external/libmdbx` and `external/mdbx-containers` source trees, or falls back to
`find_package(mdbx_containers)`.
The public compile definition `AGENT_MEMORY_HAS_MDBX` is always defined as `0`
or `1`.

## Requirements

* C++17 compiler
* CMake 3.20 or newer
* Windows, Linux, or macOS

## License

MIT
