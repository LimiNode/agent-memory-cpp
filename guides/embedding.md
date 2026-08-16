# Embeddings

## Direction

Embedding generation is a first-class project boundary. Keep the public API
backend-independent and implement concrete providers as optional adapters.

Do not fork `cpp-llamalib` to retrofit embedding support. It can be useful as a
reference for small RAII wrappers around `llama.cpp`, but chat/generation
wrappers are not the right public abstraction for embeddings. Embedding models
need explicit handling of batch inputs, pooling, normalization, vector
dimensions, model metadata, and query-vs-document usage.

## Contract First

Add dependency-free embedding contracts under `src/agent_memory/embedding/`
before adding any concrete backend. The contracts should be usable by storage,
indexing, and retrieval code without including ONNX Runtime, `llama.cpp`, HTTP
clients, or Python headers.

The contract layer should model:

- embedding vector values and dimensions;
- model identifier and limits;
- similarity metric expected by the model;
- whether returned vectors are normalized;
- embedding purpose, such as query, document, or symmetric text embedding.

The current dependency-free contract types are:

- `Embedding`;
- `EmbeddingRequest`;
- `EmbeddingModelInfo`;
- `EmbeddingPurpose`;
- `SimilarityMetric`;
- `PoolingMode`;
- `IEmbedder`.

## Role-Aware Model Input

`EmbeddingPurpose` is part of the retrieval contract, not an annotation that a
backend may ignore. A retrieval model can use distinct query and document input
templates (for example, E5 query/document prefixes); a symmetric model may
deliberately use the same template for both roles.

The caller supplies the original text and its intended role. The selected
embedder owns model-specific rendering, tokenization, and any required prefix
or task instruction. Application code must not silently hard-code a string
such as `query:` because that mixes a model descriptor into generic retrieval
logic and makes later model replacement unsafe.

When the contract grows, model metadata should declare the supported roles and
the versioned input-template policy. Retrieval must embed a user query with
the `Query` role and indexed source text with the `Document` role; a testable
adapter trace should make the effective rendered input and model descriptor
auditable without treating provider-specific templates as public API.

This is an architectural requirement, not a claim that one prefix scheme is
universally better. It keeps asymmetric retrieval encoders compatible with
future symmetric encoders and task-instruction models.

## Backends

Concrete backends should live behind adapter boundaries, for example:

- `LlamaCppEmbedder` for local `llama.cpp` embedding models;
- `OnnxRuntimeEmbedder` for local ONNX Runtime models;
- `OpenAICompatibleEmbedder` for HTTP-compatible embedding APIs;
- user-provided implementations of the embedding interface.

Each backend must keep its dependency wiring optional and must not leak backend
types into dependency-free contracts.

## Deferred Tokenizer Acceleration Research

[`gigatoken`](https://github.com/marcelroed/gigatoken) is a potential future
tool for optional offline BPE fixture generation or bulk pretokenization. It is
not a core dependency and must not replace the project's lexical tokenizer:
lexical retrieval needs normalized terms and source-aware token boundaries,
not language-model token ids.

Before considering an adapter, verify exact token compatibility, supported
tokenizer family, Windows behavior, and end-to-end fixture-generation benefit
for the target embedding model. Do not use it for a model whose tokenizer
family it does not support. This is deferred until a measured offline
preparation bottleneck exists.

## Vector Storage And Math

Keep `Embedding::values` as `std::vector<float>`. It is the portable storage
format used by serialization, MDBX adapters, tests, APIs, and exact reranking.

Do not replace the public embedding vector with `Eigen::VectorXf`. If Eigen is
added later, use it behind optional math or retrieval adapters through
temporary `Eigen::Map` views over `Embedding::values`.

Generic lossless compression such as Zstd is useful for text and cold storage,
but it should not be the hot-path representation for vector search. Future
vector storage reductions should be modeled as separate encodings such as
float16, int8, binary signatures, or product quantization, while full float
embeddings remain available for final ranking.
