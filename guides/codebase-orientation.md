# Codebase Orientation

## Current Tree

```text
cmake/
    AgentMemoryDependencies.cmake
    AgentMemoryOptions.cmake
examples/
    basic_usage.cpp
external/
    libmdbx/
    mdbx-containers/
src/
    agent_memory.hpp
src/agent_memory/
    chat.hpp
    core.hpp
    core/
        LibraryInfo.hpp
        LibraryInfo.cpp
    domain.hpp
    domain/
        Document.hpp
        Document.cpp
        Identifiers.hpp
        Identifiers.cpp
        Metadata.hpp
        Metadata.cpp
        Resource.hpp
        Resource.cpp
        SourceKind.hpp
        SourceKind.cpp
    embedding.hpp
    embedding/
        embedding_types.hpp
        embedding_types.cpp
        enums.hpp
        enums.cpp
        IEmbedder.hpp
        IEmbedder.cpp
    eval.hpp
    eval/
        Evaluation.hpp
        Evaluation.cpp
    ingestion.hpp
    ingestion/
        ResourceIndexer.hpp
        ResourceIndexer.cpp
    index.hpp
    index/
        ExactVectorIndex.hpp
        ExactVectorIndex.cpp
        VectorIndex.hpp
        VectorIndex.cpp
        IVectorIndex.hpp
        IVectorIndex.cpp
    lexical.hpp
    lexical/
        Tokenizer.hpp
        Tokenizer.cpp
        Lexical.hpp
        Lexical.cpp
        ExactLexicalIndex.hpp
        ExactLexicalIndex.cpp
        ILexicalIndex.hpp
        ILexicalIndex.cpp
        TokenDictionary.hpp
        TokenDictionary.cpp
        ITokenDictionary.hpp
        ITokenDictionary.cpp
        ITokenizer.hpp
        ITokenizer.cpp
        StandardTokenizer.hpp
        StandardTokenizer.cpp
    memory.hpp
    memory/
        MemoryObject.hpp
        MemoryObject.cpp
    retrieval.hpp
    retrieval/
        Retrieval.hpp
        Retrieval.cpp
        IRetriever.hpp
        IRetriever.cpp
    infrastructure.hpp
    infrastructure/
        mdbx.hpp
        mdbx/
            MdbxDocumentStorage.hpp
            MdbxDocumentStorage.cpp
            MdbxResourceManifestStorage.hpp
            MdbxResourceManifestStorage.cpp
    storage.hpp
    storage/
        IDocumentStorage.hpp
        IDocumentStorage.cpp
        IResourceManifestStorage.hpp
        IResourceManifestStorage.cpp
tests/
    CMakeLists.txt
    domain/
        domain_primitives_test.cpp
        resource_manifest_test.cpp
    embedding/
        embedding_contracts_test.cpp
    eval/
        evaluation_contracts_test.cpp
    index/
        vector_index_contracts_test.cpp
        exact_vector_index_test.cpp
    retrieval/
        retrieval_contracts_test.cpp
    ingestion/
        resource_indexer_test.cpp
    lexical/
        tokenizer_contracts_test.cpp
        standard_tokenizer_test.cpp
        lexical_value_types_test.cpp
        token_dictionary_contract_test.cpp
        lexical_index_contract_test.cpp
        exact_lexical_index_test.cpp
    infrastructure/
        mdbx/
            mdbx_document_storage_test.cpp
            mdbx_resource_manifest_storage_test.cpp
    storage/
        feature_flags_test.cpp
        document_storage_contract_test.cpp
        resource_manifest_storage_contract_test.cpp
    smoke/
        agent_memory_smoke.cpp
```

## Public Includes

Consumers include project headers through the `agent_memory/` prefix:

```cpp
#include <agent_memory.hpp>
```

The build interface currently exposes `src/` as the public include root.

## Existing Smoke API

`agent_memory::core::LibraryInfo` provides minimal project metadata for the
initial smoke test and example:

- `library_name()`;
- `library_description()`;
- `library_version()`.

Do not treat this class as the future application facade.

## Domain Primitives

The domain layer currently provides dependency-free value objects for the first
storage and indexing steps:

- `DocumentId` and `ChunkId`;
- `ResourceId`;
- `Metadata`;
- `SourceKind`;
- `Document`, `DocumentChunk`, and `TextRange`.
- `ResourceRevision`, `DerivedRecordKind`, `DerivedRecordRef`, and
  `ResourceManifest`.

## Storage Contracts

The storage layer currently provides dependency-free contracts:

- `IDocumentStorage` for persisting a `DocumentSnapshot` and loading/removing
  documents and their chunks.
- `IResourceManifestStorage` for persisting the resource-owned derived-record
  manifest used by targeted reindexing.

Concrete backends, including MDBX, must implement these contracts outside the
domain layer.

`ChunkId` values are globally unique within a storage backend, not scoped only
to one document.

MDBX dependency wiring lives in `cmake/AgentMemoryDependencies.cmake` and is
disabled by default. It exposes `AGENT_MEMORY_HAS_MDBX` as a public compile
definition with value `0` or `1`.

When source dependencies are used, `external/libmdbx` and
`external/mdbx-containers` are flat sibling submodules. The build adds libmdbx
first so `mdbx-containers` can reuse the parent-provided MDBX target.

`MdbxDocumentStorage` lives in `src/agent_memory/infrastructure/mdbx/` and is
compiled only when `AGENT_MEMORY_ENABLE_MDBX=ON`. It implements
`IDocumentStorage` through `mdbx-containers` tables and stores adapter details
behind a Pimpl so public storage contracts stay dependency-free.

`MdbxResourceManifestStorage` uses the same optional infrastructure boundary for
`IResourceManifestStorage` and stores resource manifests in an MDBX table while
keeping public storage contracts dependency-free.

## Embedding Direction

Embedding code starts with dependency-free contracts in
`src/agent_memory/embedding/`:

- `Embedding`, `EmbeddingRequest`, and `EmbeddingModelInfo`;
- `EmbeddingPurpose`, `SimilarityMetric`, and `PoolingMode`;
- `IEmbedder` with single-item and batch embedding methods.

Concrete providers such as `llama.cpp`, ONNX Runtime, or HTTP APIs belong
behind optional adapter boundaries. Do not fork `cpp-llamalib` to turn a
chat/generation wrapper into the project embedding API.

## Index Contracts

Vector index contracts live in `src/agent_memory/index/`:

- `VectorRecord`, `VectorSearchQuery`, `VectorSearchResult`, and
  `MetadataFilter`;
- `matches_metadata_filters()`;
- `IVectorIndex` for upsert/find/search/erase/clear operations.

Concrete exact or approximate vector indexes should implement this contract
without defining retrieval policy.

`ExactVectorIndex` is the dependency-free in-memory baseline implementation. It
supports cosine, dot product, and Euclidean scoring, applies exact metadata
filters, and keeps result ordering deterministic by chunk id when scores tie.

## Ingestion Direction

Resource indexing lives in `src/agent_memory/ingestion/` and composes
dependency-free contracts. `ResourceIndexer` currently accepts pre-chunked
resource snapshots, writes document state, embeds chunks, upserts vector records,
and stores resource manifests. It is a dense/vector prototype, not the M0
lexical-first public importer: its current replacement flow is not declared
failure-atomic. The planned `IResourceImporter` contract in
[`resource-reindexing.md`](resource-reindexing.md) owns connector observations,
revision publication and mandatory raw provenance. `ResourceIndexer` does not
own parsing, chunking policy, MDBX details, or retrieval ranking.

## Lexical Contracts

Lexical contracts live in `src/agent_memory/lexical/`:

- `Token`, `TokenKind`, `TokenizeOptions`, and `TokenizationResult`;
- `TokenId`, lexical postings, document stats, search queries, and search
  results;
- `TokenDictionaryEntry` and `ITokenDictionary` for normalized token text to id
  allocation and document-frequency stats;
- `ILexicalIndex` for upsert/search/find-stats/erase/clear operations;
- `ITokenizer` for tokenizer backends.

Tokenizer output uses normalized lookup text, byte ranges into the original
UTF-8 source text, emitted token positions, and coarse token kinds. Keep this
layer dependency-free; optional Unicode backends belong behind later adapters.

`StandardTokenizer` is the std-only baseline. It lowercases ASCII when requested,
keeps non-ASCII UTF-8 bytes intact, recognizes words, numbers, identifiers,
paths, optional symbols, and can emit searchable parts for code-style
identifiers.

`ExactLexicalIndex` is the dependency-free in-memory BM25 baseline. It stores
tokenized chunks, tracks per-token document frequency, applies exact metadata
filters, and keeps result ordering deterministic by chunk id when scores tie.

## Retrieval Contracts

Retrieval contracts live in `src/agent_memory/retrieval/`:

- `RetrievalQuery`, `RetrievedChunk`, and `RetrievalResult`;
- `IRetriever` for text, embedding, or mixed retrieval queries.

Concrete retrievers may compose embedding, index, and storage contracts, but
backend-specific details must stay behind adapter boundaries.

## Where To Add Code

- Core metadata and stable library primitives: `src/agent_memory/core/`.
- Domain value objects and contracts: `src/agent_memory/domain/` or the
  relevant domain slice.
- Storage contracts and neutral types: `src/agent_memory/storage/`.
- MDBX-specific implementation details: future infrastructure/storage area.
- Embedding contracts: `src/agent_memory/embedding/`.
- Lexical search contracts and implementations: `src/agent_memory/lexical/`.
- Retrieval/ranking behavior: `src/agent_memory/retrieval/`.
- Retrieval evaluation contracts: `src/agent_memory/eval/`.
- Resource ownership and targeted reindexing: start with domain/storage
  contracts, then add infrastructure adapters.
- Memory policies and composition: `src/agent_memory/memory/`.
- Context builders and formatting: `src/agent_memory/context/`.

Create a new area only when the PR introduces real code for that area.

## Evaluation Contracts

Retrieval evaluation value types and metric helpers live in
`src/agent_memory/eval/`:

- `RetrievalEvalDataset` models a BEIR-style corpus/query/qrels dataset shape;
- `RetrievalRun` models one retriever configuration's ordered hits and optional
  per-query latency;
- `evaluate_retrieval()` computes Recall@K, MRR, nDCG@K, no-answer accuracy,
  and latency summaries.

Current MRR is intentionally unbounded. Add MRR@K in a follow-up benchmark
runner PR once real run configuration and reporting needs are clearer.

`RetrievalRunHit::score` is diagnostic payload for metric reporting. Implicit
rank uses the vector order. If a benchmark importer receives an unordered score
dump, add a separate normalizer/import helper instead of changing
evaluate_retrieval() ordering.

evaluate_retrieval() is metric-only and does not validate that qrel item ids
exist in `RetrievalEvalDataset::corpus`. Add corpus id uniqueness and qrels to
corpus integrity checks in a follow-up dataset loader/runner validation PR.

This layer intentionally does not load external datasets, invoke embedders, or
own benchmark executables. Those belong in follow-up benchmark runner/tooling
PRs.

## Tests And Examples

- Put unit and smoke tests under `tests/`.
- Put small consumer-style examples under `examples/`.
- Keep examples non-interactive unless the user explicitly requests an
  interactive example.
