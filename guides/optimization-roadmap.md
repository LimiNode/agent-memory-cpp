# Optimization Roadmap

> C++17 compliance: кодовые сниппеты используют const std::vector& вместо std::span и явные конструкторы вместо designated initializers. Dedupe distance — cosine, lower = stricter.

## Purpose

This document describes the optimization layers (vector math, vector encoding,
binary signatures, binary bucket index, secondary indexes) for the retrieval
primitives in `agent-memory-cpp`. It concretizes **Layer 2** of
[`guides/memory-stacks-roadmap.md`](memory-stacks-roadmap.md), defining how the
storage-shape primitives used by retrieval (dense vectors, binary signatures,
reverse indexes) are organized under the component architecture
(Envelope + Components + SearchProjections), the capability-aware physical MDBX
manifest from [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md),
the multi-projection / multi-model embedding model (ADR-007), and the
scope-aware multi-tenancy rule (ADR-012).

Non-goals of this document:

- The component model itself and the Envelope schema (see
  [`memory-stacks-roadmap.md` Section 3](memory-stacks-roadmap.md#3-adr-001-memory-data-model)).
- BM25F postings, tokenization, projection_kind-aware lexical indexing (see
  [`lexical-search-roadmap.md`](lexical-search-roadmap.md)).
- CompactionWorker and EmbeddingRecomputeJob semantics (see
  [`compaction-roadmap.md`](compaction-roadmap.md), future).
- CLI and tooling (see [`cli-roadmap.md`](cli-roadmap.md), future).

See [`compression-is-intelligence-roadmap.md`](compression-is-intelligence-roadmap.md) for the conceptual backbone (prediction ↔ compression, "7 check-questions for compression quality", why operational details > general patterns) and [`vector-db-engineering-roadmap.md`](vector-db-engineering-roadmap.md) for the operational decision matrix (Chroma / Qdrant / Milvus / Pinecone / Weaviate across 8 attributes).

## Optimization Decision

Optimization is justified by a measured deployment constraint, not by a
preference for compact formats. The relevant constraints are corpus size versus
available RAM, cold-start time, page-fault/read amplification, SSD and backup
budget, p99 retrieval latency, ingestion/update churn, and the operational cost
of requiring an external vector service.

For a small corpus whose float or HNSW working set fits comfortably in RAM, the
baseline wins unless a benchmark proves otherwise. Compressed bucket segments
are for the local-first, disk-conscious case: a large mostly-read corpus,
targeted resource updates, bounded candidate/rerank work, and a need to retain
canonical provenance in the same portable workspace. They are deliberately a
poor fit for unbounded random graph traversal or a highly write-churned
collection.

Every proposed codec/index mode must compare against the simplest viable
baseline on the same corpus and hardware. Promotion requires a documented
improvement in at least one deployment constraint without unacceptable loss of
final reranked quality or update/recovery behavior. `float32 -> Zstd` alone is
not a storage strategy: vector compression begins with a versioned numeric or
binary codec and applies block compression only when representative data shows
that it helps.

## Core Rules

- Keep `Embedding` as `std::vector<float>` in the public contract.
- Treat `Embedding` as a portable storage/data format, not as a math engine.
- Use Eigen only behind optional adapter or algorithm boundaries.
- Use `Eigen::Map` for temporary zero-copy views; do not store long-lived maps
  over vectors that may be destroyed or reallocated.
- Compress text and document payloads before compressing hot vector-search data.
- Keep full float embeddings available for final reranking.
- Treat binary signatures as candidate filters, not as final ranking truth.
- Measure approximate search quality by recall and latency against an exact
  float baseline.
- Dense vector storage is keyed by `(scope_id, model_id, model_version,
  projection_kind, unit_id)`; a binary bucket key is descriptor-scoped by
  `(scope_id, model_id, model_version, projection_kind,
  descriptor_fingerprint, key_projection_id, short_key)`. A BM25F-only profile
  does not open a binary bucket index.
- All secondary indexes are scope-aware: every key begins with `scope_id`.
- Multi-projection and multi-model embeddings live side by side in the same
  `embedding_vectors` DBI, addressed by `projection_kind`, `model_id`, and
  immutable `model_version` respectively.

## Near-Term Tasks

### Vector Math Baseline

- Add a small dependency-free `math` or `index` helper for dot product, cosine,
  negative squared Euclidean distance, normalization, and score ordering.
- Reuse the helper from exact search and future rerank code.
- Keep the first version std-only.
- Add tests for dimension mismatch, zero vectors, normalized vectors, and score
  ordering.

### Optional Eigen Adapter

- Add an optional CMake option only when real Eigen-backed code is introduced:

```cmake
AGENT_MEMORY_ENABLE_EIGEN
```

- Expose a public feature macro with a `0` or `1` value when the option is
  introduced:

```cpp
AGENT_MEMORY_HAS_EIGEN
```

- Use `Eigen::Map<const Eigen::VectorXf>` over `Embedding::values` for dot,
  norm, cosine, batch scoring, centroid, averaging, and reranking experiments.
- Keep Eigen out of `embedding_types.hpp`, `IEmbedder.hpp`, `IEmbeddingStore`, and
  other dependency-free contracts.
- Benchmark Eigen paths against the std-only baseline before making them the
  default for any algorithm.
- Подробнее см. "Eigen и SIMD стратегия" секция ниже для maturity breakdown,
  hot path analysis, SIMD dispatch и HammingTopK kernel.

### Eigen и SIMD стратегия

Разделение ответственности:

- Eigen: matrix-vector (encoder inference), batch cosine/dot, dense math.
- Custom SIMD: popcount, Hamming scan, бинарные hot-path kernels.

Hot path analysis (ВАЖНО):

- Encoder inference: 1 matrix-vector per query (~98,304 FLOPs для 768×128).
- Hamming scan: thousands of XOR+popcount per query (128-bit × N candidates).
- Реальный bottleneck — Hamming, НЕ encoder.

> **Current implementation note (PR #57).** Runtime AVX2/SSE2/scalar float
> similarity, width-aware POPCNT/AVX2/lookup Hamming dispatch, contiguous batch
> Hamming scans, distance-bucket top-k, and the materialized/SIMD
> `RandomHyperplaneBinaryEncoder` v2 are implemented. Measurements show that
> encoder and selection costs can dominate short-code Hamming arithmetic, so
> bottlenecks must be reported per stage rather than assumed in advance.

Maturity breakdown:

- M0/M1: std-only baseline (float math через `std::vector` + scalar loops);
  `popcount64` intrinsics для Hamming (cross-platform wrapper); Eigen
  НЕ обязателен. Optional `AGENT_MEMORY_ENABLE_EIGEN` только для
  экспериментов.
- M2: optional Eigen adapter через `AGENT_MEMORY_ENABLE_EIGEN`;
  `AutoencoderBinarySignatureEncoder` inference on Eigen (matmul + sign);
  decoder support (если `ApproximateVector` mode); training pipeline — Python,
  C++ только inference.
- M2/M3: specialized SIMD `HammingTopK` kernel (после benchmark).
  - AVX2: `_mm256_xor_si256` (XOR, 4× uint64_t parallel) +
    scalar `__builtin_popcountll` per lane ИЛИ nibble LUT через
    `_mm256_shuffle_epi8 + _mm256_sad_epu8` (Hacker's Delight).
  - AVX-512 + VPOPCNTDQ: `_mm512_xor_si512` + `_mm512_popcnt_epi64`
    (8× uint64_t per instruction, требует Skylake-X+).
  - Runtime CPU detection (не compile-time).
  - Замечание: `_mm256_popcnt_u64` НЕ существует в AVX2 (это AVX-512 VPOPCNTDQ).
- NEVER first: hand-written AVX matrix-vector encoder (complexity vs benefit
  плохой).

CMake flags:

```cmake
# Optional dependencies.
option(AGENT_MEMORY_ENABLE_EIGEN "Enable Eigen for dense vector math" OFF)
option(AGENT_MEMORY_ENABLE_ZSTD  "Enable Zstd compression"           OFF)

# CPU feature detection (prefer runtime over compile-time).
option(AGENT_MEMORY_HAS_POPCNT   "Has popcnt instruction" ON)
option(AGENT_MEMORY_HAS_AVX2     "Has AVX2"               OFF)
option(AGENT_MEMORY_HAS_AVX512   "Has AVX-512"            OFF)
option(AGENT_MEMORY_HAS_NEON     "Has ARM NEON"           OFF)
```

Рекомендация: runtime detection через `cpu_features` library (Google) или
platform intrinsics. Compile-time опции — для special builds.

SIMD abstraction layer:

```cpp
namespace simd {

// Cross-platform popcount.
inline uint32_t popcount64(uint64_t x) {
#if defined(_MSC_VER)
    return static_cast<uint32_t>(__popcnt64(x));
#elif defined(__GNUC__) || defined(__clang__)
    return static_cast<uint32_t>(__builtin_popcountll(x));
#else
    return fallback_popcount64_sw_ar(x);
#endif
}

uint32_t hamming_distance_u64(
    const uint64_t* a,
    const uint64_t* b,
    size_t word_count);

}  // namespace simd
```

HammingTopK kernel (для после M2 benchmark):

- Алгоритм: bucket-based prefiltering через `binary_bucket_index`
  (O(log N)); per-bucket — linear scan с Hamming distance; top-K maintained
  в binary heap size K.
- AVX2 путь для Hamming (2-4x speedup vs scalar):
  1. XOR: `_mm256_xor_si256` (vector, AVX2) — 256 бит = 4× uint64_t parallel.
  2. Popcount: scalar `__builtin_popcountll` (GCC/Clang) / `__popcnt64` (MSVC)
     per lane, ИЛИ nibble LUT через `_mm256_shuffle_epi8 + _mm256_sad_epu8`
     (Hacker's Delight technique, ~2x vs scalar),
     ИЛИ SSE4.2 `_mm_popcnt_u64` (только 64-bit) — 2× per AVX2 register.
  3. Sum: горизонтальная сумма 4× uint64_t → scalar.
  Замечание: `_mm256_popcnt_u64` НЕ существует (это AVX-512 VPOPCNTDQ).

- AVX-512 + VPOPCNTDQ путь (8-16x speedup):
  1. XOR: `_mm512_xor_si512` (vector, AVX-512) — 512 бит = 8× uint64_t parallel.
  2. Popcount: `_mm512_popcnt_epi64` (vector, VPOPCNTDQ) — 8× uint64_t per instruction.
  3. Sum: горизонтальная сумма 8× uint64_t → scalar.
  Требование: CPU с AVX-512 + VPOPCNTDQ (Skylake-X и позже).

- Implementation:
  - Runtime CPU detection (`cpu_features` library или platform intrinsics).
  - Dispatch table: scalar / SSE4.2 / AVX2 / AVX-512.
  - Benchmark-driven выбор (НЕ преждевременная оптимизация).

Eigen encoder memory sizes (per encoder registry, НЕ per unit):

```text
weights_W: float[bit_count × input_dim]
  Size estimates:
    128 × 768:   393,216 bytes  ≈ 384 KiB
    256 × 768:   786,432 bytes  ≈ 768 KiB
    128 × 1024:  524,288 bytes  ≈ 512 KiB
    256 × 1024:  1,048,576 bytes ≈ 1.0 MiB

weights_WT (optional): float[input_dim × bit_count] — same sizes.

b_encoder: float[bit_count]
  128-bit: 512 B
  256-bit: 1 KB

b_decoder (optional): float[input_dim]
  768-dim: 3 KB
  1024-dim: 4 KB

Итого (128-bit × 768-dim, encoder + decoder): ~770 KB per encoder registry.
Итого (256-bit × 1024-dim, encoder + decoder): ~2 MB per encoder registry.
```

Per encoder registry (НЕ per unit): один экземпляр на `MemoryStack`.

Eigen interface pattern:

```cpp
// В core — interface только.
class IAutoencoderEncoder {
public:
    virtual ~IAutoencoderEncoder() = default;
    virtual BinarySignature encode(const Embedding& x) const = 0;
};

// Optional Eigen adapter (только если AGENT_MEMORY_ENABLE_EIGEN).
#ifdef AGENT_MEMORY_ENABLE_EIGEN
class EigenAutoencoderEncoder final : public IAutoencoderEncoder {
    Eigen::MatrixXf m_W_encoder;  // [bit_count × input_dim]
    Eigen::VectorXf m_b_encoder;
public:
    BinarySignature encode(const Embedding& x) const override {
        Eigen::VectorXf z = m_W_encoder * x + m_b_encoder;
        BinarySignature sig(m_bit_count);
        for (uint32_t i = 0; i < m_bit_count; ++i) {
            sig.set_bit(i, z[i] > 0.0f);
        }
        return sig;
    }
};
#endif
```

Training pipeline (НЕ в core):

- Core library (C++): inference encoder (encode only); decoder (optional,
  для `ApproximateVector` mode).
- Tools/experiments (Python recommended): trainer (autoencoder); dataset
  preparation (sample embeddings из corpus); weight export в `.bse` file.
- НЕ core: backward pass, optimizer, batching, shuffling — это отдельный
  tool, не часть библиотеки.

### Compression Contracts

- Add dependency-free compression value types before adding a codec backend.
- Prefer a lightweight `ByteView` for C++17 instead of `std::span`.
- Model at least:

```cpp
enum class CompressionCodec {
    None,
    Zstd
};

struct CompressedBlobHeader final {
    CompressionCodec codec = CompressionCodec::None;
    std::uint32_t codec_level = 0;
    std::uint64_t uncompressed_size = 0;
    std::uint64_t uncompressed_hash = 0;
    std::string dictionary_id;
};
```

- Store hashes of uncompressed content so change detection does not depend on a
  codec, level, or dictionary choice.
- Keep dictionaries identified by stable `dictionary_id` values.
- Note: per ADR-007, embedding vectors share the same `CompressionCodec`
  pipeline as text payloads; dictionaries are still trained per domain
  (text chunks, binary bucket lists, embedding blobs).

### Optional Zstd Adapter

- Add Zstd as an optional dependency after the compression contracts exist:

```cmake
AGENT_MEMORY_ENABLE_ZSTD
```

```cpp
AGENT_MEMORY_HAS_ZSTD
```

- Use Zstd as the preferred text/chunk compression backend.
- Keep `None` as the baseline codec for tests and minimal builds.
- Treat miniz/zlib or LZ4 as later alternatives, not as the first required
  implementation.
- Train separate dictionaries for different payload domains, for example text
  chunks, binary bucket lists, and embedding blobs.
- Do not use a text-trained dictionary for binary vector blobs without a
  benchmark.

### Compressed Text Storage

- Compress document and chunk text independently enough to preserve random
  access by chunk id.
- Avoid requiring decompression of a full document just to return one retrieved
  chunk.
- Keep metadata small and usually uncompressed unless a benchmark shows value.
- Add tests that verify round-trip content, uncompressed hash, codec metadata,
  and missing-dictionary failures.

### Raw Resource Body Compression

Raw resources (`.md`, `.txt`, extracted `.pdf`, transcripts, logs) are a
separate compression domain from embeddings, postings and generated summaries.
The roadmap target is to support both:

- MDBX-backed `ResourceBodyStore`, where body chunks live in primary blob/body
  tables and are referenced by `ResourceId` / `SourceRef`;
- file-pack storage, where the original folder layout can be preserved and
  files are replaced or mirrored by compressed container artifacts that require
  an application viewer/exporter.

Resource-body compression contract:

- compression unit is a bounded body chunk, not the entire corpus;
- each body or chunk records `codec`, `codec_level`, `dictionary_id`,
  `uncompressed_size`, `uncompressed_hash`, content type and source revision;
- chunk boundaries should align with ingestion/chunking offsets when feasible
  so citation drill-down does not decompress unrelated text;
- reverse indexes, posting lists and relation indexes store ids/ranges only,
  never inline body bytes;
- raw body storage is optional profile delta and must be accounted for in
  `mdbx-containers-extension-tz.md` §5.5.1 when MDBX-backed.

Benchmarks before enabling compression by default:

- compression ratio by source type (`md`, `txt`, extracted `pdf`, transcript,
  logs);
- random chunk read latency;
- full resource restore/export latency;
- write amplification during replace/revision updates;
- dictionary training benefit vs operational complexity.

## Vector Encoding Tasks

### Canonical Float Storage

- Keep float32 embeddings as the source of truth for quality-sensitive rerank.
- Store compressed float vectors only for cold storage, archives, or benchmarked
  bucket layouts.
- Do not run hot vector search by decompressing every candidate from a generic
  compressed blob unless a benchmark proves it is faster for the target
  workload.

### Future Encodings

Plan a separate vector encoding layer instead of changing `Embedding`:

```cpp
enum class VectorEncoding {
    Float32,
    Float16,
    Int8,
    BinarySignature,
    ProductQuantized
};
```

Binary signature encoder ID taxonomy (parallel enum для binary encoders,
используется в `BinarySignatureInfo` и `EncoderRegistry`):

```cpp
enum class BinarySignatureEncoderId {
    RandomHyperplaneLSH = 0,
    AutoencoderBinarizer = 1,
    HaarLikeExperimental = 2,  // reserved
};
```

Possible follow-up tasks:

- Add `EncodedVector` value types for persisted or index-specific encodings.
- Add float16 and int8 experiments after exact float baselines are available.
- Add product quantization only with benchmark and quality reporting.
- Keep encoding metadata tied to model id, dimension, similarity metric, and
  normalization.
- Encoding metadata also carries `projection_kind`, so two encodings of the same
  unit by different projections are not silently mixed.

### Matryoshka Truncation Codec (M2+)

Reference: arXiv:2205.13147 — "Matryoshka Representation Learning".

Принцип: embedding обучен так, что первые N координат уже осмысленны. Хранить только первые N.

```cpp
class MatryoshkaTruncationCodec final : public IEmbeddingCodec {
    uint32_t m_truncated_dim;  // 64 / 128 / 256 / 512
public:
    Embedding decode(const EncodedVector& v) const override {
        Embedding full(m_original_dim);
        for (uint32_t i = 0; i < m_truncated_dim; ++i) full[i] = v[i];
        for (uint32_t i = m_truncated_dim; i < m_original_dim; ++i) full[i] = 0.0f;
        return full;
    }
};
```

Storage: 768 → 128 = ~6x compression.
При search: zero-pad до полного dim, cosine.

### Product Quantization Codec (M2+)

Reference: arXiv:1702.08734 (FAISS line).

Принцип: K-means на sub-vectors. Compact codes (uint8 per sub-vector). Approximate distance через LUT.

```cpp
class ProductQuantizationCodec final : public IEmbeddingCodec {
    uint32_t m_num_subvectors = 8;
    uint32_t m_subvector_dim = 96;  // для 768-dim
    uint32_t m_k = 256;             // K-means clusters per sub-vector
    std::vector<std::vector<float>> m_codebooks;  // [num_subvectors × k × subvector_dim]
};
```

Storage: 768 × 4 bytes → 8 × 1 byte = ~96x compression.
Distance: asymmetric ADC (approximate distance computation).

Применение: cold storage tier (архивные embeddings), не hot retrieval.

## Projection-Aware Vector Storage

Per ADR-007 in `memory-stacks-roadmap.md`, dense vector storage is multi-model
and multi-projection. A single unit may carry independent embeddings for:

- different models (e.g. `bge-m3` and `openai-3-small`), and
- different projection kinds (e.g. `Original` and `DenseContextual`).

The `embedding_vectors` DBI is keyed accordingly:

```text
embedding_vectors
    key   = (scope_id, model_id, model_version, ProjectionKind, UnitId)
    value = vector_blob  (float32, optionally compressed)
```

The companion metadata table records provenance, encoder configuration, and
compaction state:

```text
embedding_meta
    key   = (scope_id, UnitId, ProjectionKind, model_id, version)
    value = EmbeddingMetaComponent
            { model_id, version, dim, encoder_id, seed,
              written_at_ms, is_active, superseded_by_revision }
```

### Multi-Projection Example

```cpp
auto& store = stack.embeddings();

EmbeddingWriteRequest req_original;
req_original.unit_id = my_chunk_id;
req_original.scope_id = my_scope_id;
req_original.projection_kind = ProjectionKind::Original;
req_original.model_id = "bge-m3";
req_original.model_version = "1.0";
req_original.vector = dense_vector_original_projection;
store.upsert(req_original);

EmbeddingWriteRequest req_contextual;
req_contextual.unit_id = my_chunk_id;
req_contextual.scope_id = my_scope_id;
req_contextual.projection_kind = ProjectionKind::DenseContextual;
req_contextual.model_id = "bge-m3";
req_contextual.model_version = "1.0";
req_contextual.vector = dense_vector_dense_contextual_projection;
store.upsert(req_contextual);
```

Both writes coexist in the same DBI; they are independent rows with distinct
keys.

### Multi-Model Example

```cpp
EmbeddingWriteRequest req_openai;
req_openai.unit_id = my_chunk_id;
req_openai.scope_id = my_scope_id;
req_openai.projection_kind = ProjectionKind::Original;
req_openai.model_id = "openai-3-small";
req_openai.model_version = "2024-01";
req_openai.vector = openai_vector_original_projection;
store.upsert(req_openai);
```

After both models are present, retrieval can fuse their results with RRF:

```cpp
SearchOptions search_opts;
search_opts.scope_ids = {my_scope_id};
search_opts.models = {"bge-m3", "openai-3-small"};
search_opts.projection_kinds = {ProjectionKind::Original,
                                ProjectionKind::DenseContextual};
search_opts.fusion = FusionStrategy::RRF;
search_opts.limit = 32;
auto hits = store.search_multi_model(query_vector, search_opts);
```

The RRF fusion (or weighted alternative) is implemented in the retrieval layer
(`retrieval/`) over per-(model, projection) candidate lists produced by
`IEmbeddingStore`.

### EmbeddingMeta Component

`EmbeddingMetaComponent` (per `memory-stacks-roadmap.md` Section 12.2) carries
metadata that lets the retrieval layer pick the right embedding at query time:

```text
EmbeddingMetaComponent {
    ProjectionKind projection_kind;
    std::string    model_id;
    std::string    version;
    std::uint32_t  dim;
    std::string    encoder_id;
    std::uint64_t  seed;
    std::int64_t   written_at_ms;
    bool           is_active;             // false during migration
    std::uint32_t  superseded_by_revision; // 0 if active
}
```

Two embeddings with the same `(scope_id, unit_id)` but different
`projection_kind` or `model_id` are siblings, not duplicates.

### Invariants

- For a given `(scope_id, unit_id, projection_kind)`, at most one row per
  `model_id` has `is_active == true`. Older revisions may persist until
  compaction purge.
- A read that requests a `projection_kind` with no `is_active` row falls back
  to the most recent `is_active` sibling of the same unit, or returns an empty
  vector if none exists.
- Cross-model fusion requires at least two `is_active` rows from different
  `model_id` values within the same `(scope_id, projection_kind)`.

## Embedding Migration Workflow

Per ADR-007 and open issue 17.8 in `memory-stacks-roadmap.md`, embedding models
are not pinned forever. The migration path is the `EmbeddingRecomputeJob`,
executed by `CompactionWorker`.

### Migration Job Shape

```cpp
struct EmbeddingRecomputeJob final {
    JobId                 job_id;
    ScopeId               scope_id;
    std::string           source_model_id;
    std::string           target_model_id;
    std::vector<ProjectionKind> projection_kinds; // subset, e.g. {Original}
    std::optional<KnowledgeUnitId> unit_filter;   // nullopt = all units in scope
    std::uint32_t         new_version;
    CompactionHandoff     handoff;
};
```

Progress is reported through `compaction_handoffs` (see
`mdbx-containers-extension-tz.md` §5.5.1 and `compaction-roadmap.md`). The
handoff payload includes the recomputed `(unit_id, projection_kind)` count, the
target `version`, and a recovery token for crash-safe resume.

### Per-(unit_id, projection_kind) Granularity

The job recomputes one embedding per `(unit_id, projection_kind)` it owns:

```text
for each (unit_id, projection_kind) in scope:
    fetch SearchProjection[projection_kind] for unit_id
    embed it with the target model
    write new embedding_meta row with version = new_version, is_active = true
    write new embedding_vectors row keyed by (scope_id, target_model_id,
                                              target_model_version,
                                              projection_kind, unit_id)
```

This means a unit migrated under one projection does not need to redo the
other projections. Migration cost scales with the chosen subset.

### Old Embeddings During Migration

Old rows remain in `embedding_meta` and `embedding_vectors` until compaction
purge. Their state is:

- `EmbeddingMetaComponent::is_active = false`.
- `EmbeddingMetaComponent::superseded_by_revision = new_version`.

The retrieval layer still sees them, but with `is_active = false` they are not
fused into the primary candidate list unless explicitly requested (for A/B
comparison or migration telemetry).

### Multi-Model Search During Migration

A query arriving while migration is in flight may encounter:

- new embedding for unit A,
- old embedding for unit B,
- no embedding yet for unit C.

The retrieval layer merges candidates from both active models. Per-unit score
selection prefers the active revision, but `is_active = false` rows may still
participate in RRF for short windows if the worker has not yet flushed them.
This is intentional: the migration is eventually consistent, but visibility is
never empty.

### Migration Telemetry

Track during migration:

```text
units_total
units_recomputed
units_failed
avg_recompute_latency_ms
p95_recompute_latency_ms
old_active_count (pre)
new_active_count (post)
rrf_skip_rate_old (window)
```

These counters land in `compaction_handoffs` and feed the embedding-migration
metrics used by M2 ship-it criteria (`memory-stacks-roadmap.md` Section 13.3).

## Binary Signature Index Tasks

### Dependency-Free Signature Types

- Add `BinarySignature` with `std::vector<std::uint64_t> words` and
  `std::size_t bit_count`.
- Ensure unused bits in the last word are zeroed.
- Add `hamming_distance(lhs, rhs)` implemented through XOR and popcount.
- While the project remains C++17, use compiler intrinsics or a local fallback
  instead of requiring C++20 `std::popcount`.
- Add `BinarySignatureInfo` with encoder id, source model id, source
  projection kind, source dimension, signature bits, approximated metric, and
  seed.
- Add `IBinarySignatureEncoder` as a dependency-free contract.

A binary signature is always tied to a specific projection kind and model id.
A unit with two projections has two signatures; a unit migrated to a new
model has new signatures alongside (or superseding) the old ones.

### Baseline Encoder

- Implement a dependency-free scalar random-hyperplane encoder first:

```text
bit_i = sign(dot(embedding, random_hyperplane_i))
```

- Make generation deterministic by seed, dimension, signature bit count, and
  projection kind.
- Store encoder config with the index.
- Reject or rebuild indexes when model id, projection kind, dimension,
  encoder id, seed, or signature bit count does not match.
- Keep the public encoder contract shared by BoW-derived vectors and dense
  embeddings. Later SIMD/AVX and Eigen-backed implementations must preserve the
  same bit-level contract or advertise a new `encoder_version` and config
  fingerprint.

### Learned Autoencoder Binary Encoder (M2 experimental)

Reference: arXiv 1803.09065 — "Near-lossless Binarization of Word Embeddings".

Принцип: pretrained float embedding → обученный encoder W → binary code →
optional decoder W^T → reconstruction.

Properties (per arXiv 1803.09065):

- ~97% size reduction vs float.
- ~2% accuracy loss.
- ~30x speedup на top-k retrieval benchmark.

Training pipeline (offline, Python):

1. Sample N (10k-1M) embeddings из целевого корпуса.
2. Train autoencoder с objective:
   `minimize ||x - decoder(encoder(x))||^2 + decorrelation_penalty`.
3. Export matrix W (encoder) и W^T (decoder, optional) в `.bse` файл.

C++ inference (hot path):

```text
bit_i = sign(dot(W_i, embedding))
```

Один matmul `[bit_count × input_dim] × [input_dim]` + `sign()`.

```cpp
BinarySignature encode(const Embedding& x) const {
    BinarySignature sig(m_bit_count);
    for (uint32_t i = 0; i < m_bit_count; ++i) {
        float dot = 0.0f;
        for (uint32_t j = 0; j < m_input_dim; ++j) {
            dot += m_W[i * m_input_dim + j] * x[j];
        }
        sig.set_bit(i, dot > 0.0f);  // sign() function
    }
    return sig;
}
```

Storage tradeoff:

- M0/M1 default: float + binary (binary только candidate filter, float для
  exact rerank).
- M2 experimental: optional binary-only mode (без float, decoder
  реконструирует approximate float).

Bit sizes (CPU register alignment):

- 32 bit  — эксперимент / super-fast coarse filter (НЕ default).
- 64 bit  — default для BasicRag, минимальный практический.
- 128 bit — default для AgentLTM, нормальный candidate filter.
- 256 bit — quality-oriented для CompiledWiki.

Аргументация: 64/128/256 бит выровнены по CPU register sizes (`uint64_t`,
SSE, AVX); 32 бит не alignment-friendly для popcount/Hamming через
`__builtin_popcount`.

Training data dependency: autoencoder обучен на sample embeddings из
конкретного корпуса. Если корпус сильно меняется (новые домены) — encoder
нужно ретренировать. Operational consideration, не bug.

### Experimental Encoder

- Add a Haar-like encoder only after the random-hyperplane baseline:

```text
bit_i = sum(group_a) > sum(group_b)
```

- Mark it experimental.
- Compare recall and latency against random hyperplanes.
- Do not treat Haar-like signatures as the default without benchmark evidence.

### Short Key Generation

- Add short-key strategies:

```text
prefix bits
mixed bits
```

- Use `uint32_t` for 24, 28, and 32 bit keys.
- Use `uint64_t` for 64 bit keys.
- Make short-key strategy part of index config and require rebuild when it
  changes.
- Prefer mixed bits if prefix bits create hot buckets.
- Short-key layout for a bucket index covering dense vectors includes the
  projection kind: a query over `Original` projections must never pick up
  candidates from `DenseContextual` projections.

### Neighbor Bucket Masks

- Add `make_hamming_masks(bit_count, radius)` for short-key lookup.
- Cache masks by `(bit_count, radius)`.
- Add a guard against excessive lookup counts.
- Expected lookup counts:

```text
24 bits, radius 0 -> 1 bucket
24 bits, radius 1 -> 25 buckets
24 bits, radius 2 -> 301 buckets
24 bits, radius 3 -> 2325 buckets

28 bits, radius 0 -> 1 bucket
28 bits, radius 1 -> 29 buckets
28 bits, radius 2 -> 407 buckets
28 bits, radius 3 -> 3683 buckets
```

## Binary Bucket Index Tasks

See [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) for MinHash near-clone detection (Pattern 1) and RaBitQ-style RotSQ (Pattern 2) borrowed from codebase-memory-mcp — these augment, not replace, the Hamming-based binary bucket index defined below.

### Ownership and Stable Contract

`agent-memory-cpp` owns the binary bucket index as a derived retrieval
projection. Its canonical record is a `KnowledgeUnit` projection and its
canonical vector identity remains `(scope_id, unit_id, projection_kind,
model_id, model_version)`. A bucket posting, vector-backend record id, block
slot, short key, or full binary signature is never a durable application
identity and never replaces provenance, lifecycle, or active-revision checks.

The persistent application-level index must have one versioned descriptor per
logical dense collection. It is written before a generation is published and
is validated before a writer or reader uses its buckets:

```cpp
// Proposed application contract -- not implemented.
struct BinaryBucketKeyProjectionDescriptor {
    std::string projection_id;
    std::string projection_config_fingerprint;
    uint32_t short_key_bit_count = 0;
};

struct BinaryBucketIndexDescriptor {
    uint32_t schema_version = 1;
    std::string descriptor_fingerprint;

    std::string model_id;
    std::string model_version;
    ProjectionKind projection_kind = ProjectionKind::Original;
    std::string similarity_metric;
    std::string normalization_rule;
    std::string vector_codec_id;
    uint32_t vector_codec_version = 1;
    std::string block_compression_codec_id;
    uint32_t block_compression_codec_version = 1;
    std::string block_compression_dictionary_digest;

    std::string encoder_id;
    std::string encoder_config_fingerprint;
    uint32_t full_signature_bit_count = 0;
    std::vector<BinaryBucketKeyProjectionDescriptor> key_tables;
    uint32_t posting_layout_version = 1;
    uint32_t block_layout_version = 1;
};

struct BinaryBucketSearchBudget {
    uint32_t max_bucket_probes = 0;
    uint32_t max_posting_visits = 0;
    uint32_t max_unique_candidates = 0;
    uint64_t max_decoded_bytes = 0;
    uint32_t max_exact_rerank_candidates = 0;
};
```

Every search-budget field is required and must be nonzero for an enabled
backend; `max_exact_rerank_candidates <= max_unique_candidates <=
max_posting_visits` is also required. They are hard validation limits, not
tuning hints.
The query trace records the actual probe, posting, decode, deduplication and
rerank counts plus the first exhausted limit. A planner may select a smaller
budget, but it must not silently exceed the profile maximum. This deliberately
separates bucket-search depth from `returned_candidate_limit`: the latter is
the public candidate/output contract, while the former limits the work needed
to obtain it.

The first durable layout uses exactly one `key_tables` entry. Multiple entries
are a later multi-index-hashing mode, not an implicit consequence of
multi-probe: each table has a distinct projection id and config fingerprint;
candidates are unioned, deduplicated by canonical projection identity, then
subject to the same global budget. It may be enabled only after a benchmark
shows its recall gain justifies duplicated postings, write amplification and
candidate deduplication.

### In-Memory Prototype

- Build the first binary bucket index in memory, without MDBX and without Zstd.
- Use short signature keys to collect candidate postings.
- Filter by full-signature Hamming distance.
- Fetch or keep float embeddings for exact rerank.
- Compare against `ExactVectorIndex` for recall and latency.

### MDBX Prototype

- Add MDBX-backed bucket storage after the in-memory prototype.
- Integrate with resource manifests before treating bucket storage as mutable
  production storage; bucket postings need `unit_id` and `envelope.revision`
  (aka `unit_revision`) for stale-entry filtering.
- Prefer the two-stage layout first:

```text
binary_bucket_index:
    key   = (scope_id, model_id, model_version, projection_kind,
             descriptor_fingerprint, key_projection_id, short signature key)
    value = compressed or uncompressed posting list
            vector<BinaryBucketPosting> per descriptor-scoped table/key:
              struct BinaryBucketPosting {
                  KnowledgeUnitId unit_id;          // monotonic uint64_t
                  BinarySignature full_signature;   // 64/128/256 bits
                  uint64_t unit_revision;           // envelope.revision at encoding time
                  std::optional<uint64_t> resource_generation;  // optional, для derived records
              };

embedding_store:
    key   = (scope_id, model_id, model_version, projection_kind, unit_id)
    value = float32 vector blob or encoded vector blob

unit_store:
    key   = (scope_id, unit_id)
    value = compressed chunk text and metadata
```

- Store bucket values as compact posting lists of `BinaryBucketPosting`
  (`{unit_id, full_signature, unit_revision, optional resource_generation}`).
  Filter на stale: `skip if posting.unit_revision < envelope.revision`.
- Keep one-stage bucket values with embedded float vectors as an experimental
  benchmark variant.
- Prefer sparse MDBX key-value lookup for 64-bit short keys.
- Treat dense direct-address directories as possible only for small key sizes
  and only after measuring memory cost.
- A binary bucket collection is always model-, projection-, descriptor- and
  key-projection-specific. A BM25F-only profile does not open this derived
  dense index rather than weakening its key and risking incompatible encoder
  or model postings in one bucket.

### Immutable Segment Publication and Compaction

The first durable implementation must not mutate an arbitrary record inside a
compressed bucket value. It publishes a new immutable delta segment or block,
then atomically swaps an application-owned active-generation manifest. A
manifest names the descriptor fingerprint, active generation, immutable
segment/block ids, and the source/resource generations it covers. The old
manifest stays readable until the new one is fully published.

```text
write/reindex projection
    -> write new derived vector record and bucket delta segments
    -> validate descriptor and bounded encoded sizes
    -> atomically publish active-generation manifest
    -> readers validate unit/resource revision during hydration
    -> later compaction merges reachable segments and reclaims tombstones
```

Deletion and reindexing append a revision-aware tombstone or publish a newer
generation; they do not require an in-place deletion from a compressed block.
Interrupted writes may leave unreachable derived segments, but must never make
an unpublished generation visible. Compaction may reclaim only segments that
are unreachable from the active manifest and no longer protected by a retained
revision/export snapshot. This is application lifecycle logic, not a generic
`mdbx-containers` API requirement.

### Columnar Segment Codec Candidates (M2+ Experimental)

An immutable bucket segment may have different codecs for different columns.
It is not a single opaque `Zstd(bucket)` blob. The application-owned segment
header records the layout version, record count, per-column codec ids/versions,
encoded and decoded sizes, descriptor fingerprint, optional dictionary digest,
and a digest of the decoded logical contents. All size arithmetic is checked;
unknown codecs, missing dictionaries, excessive decoded sizes and corrupt
column offsets fail closed.

```text
[segment header]
[canonical projection ids / local-id map]
[unit/resource revision and locator metadata]
[fixed-width full binary signatures]
[optional quantized or PQ vector payload]
```

The planned column rules are deliberately asymmetric:

- Sorted local record ids may use `uint64` base + bounded `uint32` deltas,
  then delta/bit packing. A block that cannot safely use the narrow delta
  representation uses a versioned 64-bit or VByte fallback; compression must
  never truncate a durable id.
- Repeated resource ids, revisions, flags, token counts and text offsets are
  candidates for a local dictionary, RLE, delta coding and bounded integer
  packing. A record order is chosen per segment; if sorting by id would destroy
  useful vector locality, the local-id map is a separate compressed column
  rather than a reason to silently reorder payload semantics.
- Full binary signatures remain raw fixed-width words, aligned for XOR/popcount
  scans. General integer/Zstd codecs are not applied to the Hamming hot path.
- Quantized payloads use their native representation first: raw `int8`,
  fixed-width packed `int4`/`int6`, or PQ codes. Applying a generic integer
  compressor after an already full-range `int8` payload is not presumed useful.

`FrameOfReferenceResidual` is a separate experimental payload codec: for a
bounded, coherent segment it stores a centroid or base, scale groups and
columnar quantized residuals. A SIMD-BP128/FastPFOR-style kernel may then pack
fixed-size residual groups. LSH/binary-bucket co-membership does **not** prove
small per-coordinate residuals, so this codec is selected only after measuring
residual-width histograms and final retrieval quality on representative data.
It competes with fixed-width int4/int6 and PQ; it is not an automatic second
compression pass after int8 quantization.

`transpose -> byte/bit shuffle -> Zstd` is allowed only as a cold-block variant
for typed numeric columns when its descriptor records the transform and the
benchmark proves an end-to-end benefit. It may improve compression of similar
floating-point/residual bytes, but it introduces decode work and is not the
default hot rerank format. A trained Zstd dictionary is likewise optional: its
content digest is part of the descriptor, it is suitable only for stable,
repetitive serialized data, and it must be compared with no-dictionary Zstd.

The first implementation can use a small dependency-free codec or an optional
adapter around a proven integer-packing library. `simdcomp` is a useful
reference for 128-value integer blocks and tail handling, but it is not a core
dependency or a promise that one external layout defines our durable format.

Acceptance requires a corpus with random, coherent and deliberately
heterogeneous buckets. Report per-column and total encoded bytes, compression
and decode throughput, p50/p95/p99 end-to-end query latency, decoded bytes,
candidate/reranked recall, residual-width distribution, write amplification,
compaction cost, cold reopen and corrupt-header/dictionary-negative tests.
The codec is promoted only if it improves the measured deployment frontier over
plain int8, fixed-width packed quantization and PQ on the same hardware.

### Mutable Bucket Updates

- Do not rely on stable entry offsets inside compressed bucket blobs.
- For deletion or reindexing, filter bucket entries by `unit_id`,
  `unit_revision`, and `projection_kind` after decompression.
- For frequently updated memory, allow stale bucket entries and skip them at
  query time until compaction rewrites affected buckets.
- Keep compaction observable through benchmark counters such as stale entry
  ratio, rewritten bucket count, and reclaimed bytes.
- Embedding migration reuses the same stale-entry pattern: old signatures
  remain in their buckets until compaction rewrite.

### Query Pipeline

The target approximate pipeline is:

```text
query text
    -> query embedding
    -> projection_kind filter (Original, DenseContextual, ...)
    -> full binary signature
    -> descriptor compatibility validation
    -> one short key per enabled table
    -> bounded neighbor-key probes by BinaryBucketSearchBudget
    -> descriptor/table-scoped bucket and segment lookups
    -> bounded decode/decompression of posting lists
    -> full-signature Hamming filter
    -> unique canonical projection ids
    -> batch fetch float embeddings for the requested
       (model_id, model_version, projection_kind)
    -> optional cross-model RRF
    -> exact rerank
    -> fetch unit text
```

Float rerank remains mandatory for quality-sensitive retrieval.

The returned `VectorHit` remains only a candidate. The retrieval engine
hydrates the active envelope/payload, validates scope, lifecycle, authority,
`unit_revision`, optional resource generation, and provenance before context
construction. A stale or inaccessible canonical record is dropped rather than
being surfaced because its derived bucket posting was fast to read.

### QAPair Projection Blocks (M2+ Experimental)

QAPair acceleration is projection-first, not a special vector-record schema.
The primary collection/block slice is `QAQuestion`; it holds question
signatures and quantized/full vectors keyed by the existing canonical
projection identity. `QAAnswer` may have a separate collection for an explicit
candidate route or remain a cold second-stage payload read only for the bounded
question candidates. `QACombined` is a third, separately benchmarked slice;
it is not a replacement for the question slice.

The physical layout follows the existing columnar segment contract:

```text
[projection-id or local-id map]
[unit revision and optional resource-generation metadata]
[question/answer/combined signature column]
[corresponding quantized or exact vector payload]
[optional compact locator/answer-body reference]
```

Blocks are compatible only within one `(scope, model, model version,
projection kind, descriptor fingerprint)` slice. A write-time locality key such
as language, source, topic cluster or embedding model may improve packing,
dictionary/RLE effectiveness and cold reads, but is never an implicit query
filter or a durable semantic identity. An explicit user/profile filter remains
part of the retrieval plan and is applied independently of physical placement.

Question, answer and combined binary routes may each have one or more
descriptor-scoped bucket indexes, but their postings refer to the same
application-owned projection identities. They do not create extra canonical
QAPair records or three mandatory copies of an embedding. Exact answer-vector
sharing is an optional storage optimisation only when the derived projection
bytes and complete model/codec descriptor are identical; provenance and
lifecycle checks still happen per QAPair during hydration.

The QA benchmark matrix must compare question-only, question plus bounded
answer rerank, and any enabled combined route against exact per-projection
baselines. It reports answer-vector reads, decoded bytes, p50/p95/p99 latency,
recall, final QA answer quality, citation fidelity, and disk/write amplification
alongside the general vector metrics. A more compact block format is not a
promotion criterion when it harms the calibrated QA retrieval frontier.

### Encoder Registry и Versioning

Implemented slice: `BinarySignatureInfo` and the in-memory
`BinarySignatureEncoderRegistry` provide dependency-free identity validation by
`config_fingerprint`. They intentionally do not load `.bse` files yet. File
loading, trained autoencoder weights, and per-stack default encoder selection
remain separate follow-up PRs.

ID taxonomy:

```text
random_hyperplane_lsh      // baseline, обучение не нужно
autoencoder_binarizer      // обученный autoencoder поверх float embeddings
haar_like_experimental     // reserved для future экспериментов
```

`IBinarySignatureEncoder` interface:

```cpp
class IBinarySignatureEncoder {
public:
    virtual ~IBinarySignatureEncoder() = default;

    virtual BinarySignature encode(const Embedding& x) const = 0;
    virtual std::optional<Embedding> decode(const BinarySignature& s) const {
        return std::nullopt;
    }
    virtual std::string encoder_id() const = 0;  // "random_hyperplane_lsh_v1",
                                                // "autoencoder_binarizer_v1"
    virtual uint32_t bit_count() const = 0;
};
```

`BinarySignatureEncoderRegistry`:

```cpp
class BinarySignatureEncoderRegistry {
public:
    void register_encoder(
        const std::string& encoder_id,                  // "autoencoder_binarizer_v1"
        uint32_t bit_count,                             // 128
        uint32_t input_dim,                             // 768 (для bge-m3 и т.п.)
        const std::vector<float>& weights_W,            // matrix [bit_count x input_dim]
        std::optional<std::vector<float>> weights_WT_decoder,  // optional decoder
        const std::string& source_description);          // "trained on 100k embeddings 2026-07-10"

    std::shared_ptr<IBinarySignatureEncoder> create(
        const std::string& encoder_id) const;
};
```

File format (offline training exports this):

```text
BinarySignatureEncoderFile (.bse):
  magic:         "BSE1" (4 bytes)
  version:       uint32 (= 1)
  bit_count:     uint32
  input_dim:     uint32
  has_decoder:   uint8 (0 or 1)
  weights_W:     float[bit_count x input_dim]    # ~384 KiB для 128-bit × 768-dim
  weights_WT:    float[input_dim x bit_count]    # optional decoder
```

Loaded через `MemoryStack::open(...)` — registry читает `.bse` файлы из
stack config dir.

Per-stack default encoder (см. §11 Memory Stacks Integration, рекомендация;
расширен до per-stack default mode + encoder — полная таблица и quality
targets живут в §"Dense Index Modes (Backend Selection)" ниже):

```text
BasicRag:          Exact (encoder n/a)
QAKnowledgeBase:   Exact или BinaryCandidateFilter (random_hyperplane_lsh_128bit)
AgentLTM:          BinaryCandidateFilter (autoencoder_binarizer_128bit,
                   ежели обучен; иначе random_hyperplane_lsh_128bit)
SpeakerAwareChat:  Exact (encoder n/a; chat keyword-heavy, не semantic-heavy)
CompiledWiki:      BinaryCandidateFilter (autoencoder_binarizer_256bit,
                   quality-oriented)
TemporalFactStore: Exact (encoder n/a)
FullResearch:      BinaryCandidateFilter (autoencoder_binarizer_128bit)
```

Encoder ID в скобках — это `DenseIndexConfig::encoder_id`; mode —
`DenseIndexConfig::mode`. Для mode = Exact binary encoder не активен.

Per (unit, encoder) signatures (для migration):

```text
binary_signature_by_unit
    key = (scope_id, UnitId, encoder_id, model_version) → BinarySignature
    [Sparse — только если encoder был запущен на этом unit]
```

Multi-encoder migration flow:

1. Phase 1: write-new (новый encoder пишется рядом со старым).
2. Phase 2: read-both (retrieval берёт max candidates из обоих).
3. Phase 3: bg-reindex (background job переписывает signatures под новый
   encoder).
4. Phase 4: compaction-delete-old (compaction удаляет старые signatures).

## Dense Index Modes (Backend Selection)

See [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) for dense retrieval details: bi-encoder vs decoder-based embeddings, hard-negative mining, EOS pooling, decoder-LLM-based retrievers (BGE/Qwen-Embed/NV-Embed).

`IDenseIndex` interface расширен с одной реализации до 5 modes
(4 base modes + HNSW M2+), выбираемых через `DenseIndexMode`:

```cpp
enum class DenseIndexMode : uint8_t {
    Exact = 0,                      // brute-force float cosine, ground truth
    BinaryCandidateFilter = 1,      // optional M1 experiment; binary filter + float rerank
    BinaryOnly = 2,                 // binary only, Hamming ranking (M2 experimental/compact)
    ApproximateVector = 3,          // binary + decoder → approx vector → rerank (M2 experimental)
    Hnsw = 4,                       // M2+ experimental ANN backend
};

class IDenseIndex {
public:
    virtual ~IDenseIndex() = default;
    virtual std::vector<VectorHit> search(
        const Embedding& query,
        const DenseSearchOptions& options) = 0;
    virtual DenseIndexMode mode() const = 0;
};

// 1. Brute-force float baseline. Ground truth для всех quality benchmarks.
class ExactVectorIndex final : public IDenseIndex {
    // Linear scan, batch cosine по (scope_id, projection_kind, model_id).
};

// 2. Binary filter + float rerank. Default production.
class BinaryCandidateFilterIndex final : public IDenseIndex {
    // Hamming bucket lookup → candidate set → float cosine rerank.
};

// 3. Binary only, Hamming ranking. Experimental/compact storage.
class BinaryOnlyIndex final : public IDenseIndex {
    // Hamming bucket lookup → Hamming rank (no rerank).
};

// 4. Binary + decoder → approx vector → rerank. Experimental.
class ApproximateVectorIndex final : public IDenseIndex {
    // Hamming bucket lookup → decoder → approx vector → cosine rerank.
    // Два варианта: Safe (binary + float + decoder) vs Compact (binary + decoder only).
};

// 5. HNSW graph ANN. Mainline M2+ backend.
class HnswVectorIndex final : public IDenseIndex {
    // M-level proximity graph, greedy traversal на верхних уровнях,
    // beam search на нижних. O(log N) average search complexity.
    // Storage: graph edges adjacency + nodes array.
};
```

Mode + encoder + bit count spec хранятся в `DenseIndexConfig` внутри
`MemoryProfileSpec`:

```cpp
struct HnswConfig {
    uint32_t m = 16;                 // avg bidirectional links per node
    uint32_t ef_construction = 100;  // beam size at build time
    uint32_t ef_search = 50;         // beam size at query time
};

struct DenseIndexConfig {
    DenseIndexMode mode = DenseIndexMode::Exact;

    // Binary modes (BinaryCandidateFilter / BinaryOnly / ApproximateVector):
    uint32_t bit_count = 128;
    std::string encoder_id = "random_hyperplane_lsh_v1";

    // ApproximateVector mode only:
    bool store_decoder = true;
    bool store_float_fallback = false;

    // Hnsw mode only:
    std::optional<HnswConfig> hnsw_config;
};
```

См. canonical `DenseIndexConfig` и `HnswConfig` в guides/memory-stacks-roadmap.md
для полной спецификации + per-stack defaults + M2+ alternative comments.

Cross-link: encoder_id в DenseIndexConfig резолвится через
`BinarySignatureEncoderRegistry` (см. §"Encoder Registry и Versioning" выше).
Encoder ID taxonomy (parallel enum) живёт в §"Future Encodings" выше:
`RandomHyperplaneLSH = 0`, `AutoencoderBinarizer = 1`,
`HaarLikeExperimental = 2`.

### Storage Estimates Per Mode (1M units × 768-dim)

```text
| Mode                          | Storage                  | Notes                                    |
|-------------------------------|--------------------------|------------------------------------------|
| Exact                         | ~3 GB                    | float32 baseline, ground truth           |
| BinaryCandidateFilter         | ~3.02 GB                 | float + binary для rerank                |
| BinaryOnly                    | ~16 MB                   | binary only, ~190x compression vs float |
| ApproximateVector (Safe)      | ~3.02 GB + ~384 KiB decoder | float + binary + decoder (128-bit × 768) |
| ApproximateVector (Compact)   | ~16 MB + ~384 KiB decoder  | binary + decoder only, no float (128-bit) |
```

Decoder matrix shared across stack (one per encoder registry): ~384 KiB
(`W_decoder` float32, 128-bit × 768-dim) + ~3 KiB `b_decoder` ≈ ~387 KiB per
encoder registry. Полная таблица sizes — см. §"Eigen и SIMD стратегия"
выше. Format — см. `.bse` file format в §"Encoder Registry и Versioning".

### Quality Targets Per Mode (Recall@10 vs Exact baseline)

Значения нормированы против `Recall@10(ExactVectorIndex) = 1.00`:

```text
ExactVectorIndex:                            = 1.00 (M1a required baseline and ground truth).

BinaryCandidateFilter + RH-64:               >= 0.85 of Exact.
BinaryCandidateFilter + RH-128:              >= 0.90 of Exact.
BinaryCandidateFilter + AE-64:               >= 0.90 of Exact.
BinaryCandidateFilter + AE-128:              >= 0.95 of Exact.  // production target
BinaryCandidateFilter + AE-256:              >= 0.97 of Exact.

BinaryOnly + RH-128:                         >= 0.80 of Exact.  // experimental
BinaryOnly + AE-128:                         >= 0.85 of Exact.  // experimental
BinaryOnly + AE-256:                         >= 0.90 of Exact.  // experimental, quality

ApproximateVector + AE-128 (Compact):        >= 0.90 of Exact.
ApproximateVector + AE-128 (Safe):           >= 0.95 of Exact.
ApproximateVector + AE-256 (Compact):        >= 0.93 of Exact.
ApproximateVector + AE-256 (Safe):           >= 0.97 of Exact.
```

Эти targets — самостоятельные per-mode targets, не заменяют existing
Per-Bit-Size targets в §"Per-Bit-Size Recall Targets" (которые нормированы
на `Recall@10(binary_only)`). Cross-link обязателен.

### Per-Stack Default Mode + Encoder

Полная таблица (mode + encoder); legacy encoder-only таблица в §"Encoder
Registry и Versioning" остаётся для обратной совместимости:

```text
BasicRag:           Exact (encoder n/a)
QAKnowledgeBase:    Exact или BinaryCandidateFilter (RH-128)
AgentLTM:           BinaryCandidateFilter (AE-128)   // production default
SpeakerAwareChat:   Exact (n/a)                       // keyword-heavy
CompiledWiki:       BinaryCandidateFilter (AE-256)    // quality
TemporalFactStore:  Exact (n/a)
FullResearch:       BinaryCandidateFilter (AE-128)
```

### Per-Stack Default Mode: M1 vs M2 Production Candidate

Декомпозиция M1 production defaults и M2 candidate migration:

| Stack | M1 default | M2 production candidate | Selection criteria |
|---|---|---|---|
| BasicRag | Exact | Exact | Small corpus, keyword-heavy, M2 не меняется |
| QAKnowledgeBase | Exact или BinaryCandidateFilter (RH-128) | HNSW (если corpus > 50k) | Corpus size + filter usage |
| AgentLTM | BinaryCandidateFilter (AE-128) | HNSW + BinaryCF (hybrid) | Latency vs storage tradeoff |
| SpeakerAwareChat | Exact | Exact | Keyword-heavy, не semantic-heavy |
| CompiledWiki | BinaryCandidateFilter (AE-256) | HNSW или BinaryCF (AE-256) | Quality priority |
| TemporalFactStore | Exact | Exact | Smaller corpus, recency-based |
| FullResearch | BinaryCandidateFilter (AE-128) | HNSW + BinaryCF (hybrid) | Latency vs storage tradeoff |

Decision logic (M2):
  - Filter-heavy query (>50% queries use metadata filter): BinaryCF preferred.
  - Latency-critical (< 50ms p95): HNSW preferred.
  - Storage-critical (memory-constrained): BinaryOnly preferred.
  - Default: HNSW если corpus > 100k units, иначе BinaryCF.

Benchmark-driven choice: profile different stacks per use case через golden dataset.

Решение принимается на основании: corpus size, recall target, hardware
budget, latency budget. См. §"Quality Targets Per Mode" выше для production
target values.

Production dense index modes (после M2):
  Default для AgentLTM/FullResearch: HNSW или BinaryCandidateFilter + AE-128.
  Default для CompiledWiki: HNSW или BinaryCandidateFilter + AE-256.
  Default для BasicRag: Exact (small corpus).

Production trade-off:
  - HNSW: best quality (>0.97 Recall@10), но graph storage ~20% от vector size.
  - BinaryCF: ~95% от HNSW quality, меньше storage, лучше для filtered query.
  - Exact: ground truth, но O(N) latency.

### Multi-Mode Migration

Mode change = major migration (новая БД + transfer), не silent in-place
rewrite:

```text
Old: ExactVectorIndex
New: BinaryCandidateFilter
Migration:
  1. Сканировать все unit'ы в scope.
  2. Для каждого: compute binary signature через указанный encoder.
  3. Write to binary_bucket_index.
  4. Float embeddings НЕ удаляются (используются для rerank).
  5. Validate Recall@10 через golden dataset против mode target.

Old: BinaryCandidateFilter
New: BinaryOnly
Migration:
  1. Drop float embeddings (storage savings: ~3 GB → ~16 MB на 1M units).
  2. Keep binary + bucket.
  3. Quality risk accepted by user (mode = experimental).
  4. Validate Recall@10 через golden dataset против mode target.
```

Migration тесты обязательны: golden dataset assertion, Recall@10 against
mode-specific target, storage delta validation.

### RetrievalPlan Integration

```cpp
struct RetrievalPlan {
    // ... existing fields ...
    std::optional<DenseIndexMode> dense_index_mode_override;
};
// Если задан, retrieval использует указанный mode вместо профильного default.
// None / nullopt = использовать per-stack default mode.
```

Override полезен для A/B evaluation, миграций, debug queries, per-query
fallback paths.

### HNSW Vector Index (M2+ experimental)

Reference: arXiv:1603.09320 — "Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs".

Принцип:
  - M-level proximity graph (обычно M=16, M_max=32 для in-memory).
  - Greedy traversal на верхних уровнях, beam search на нижних.
  - efConstruction (build-time) и efSearch (query-time) параметры.
  - O(log N) average search complexity.

Реализация:
  - Custom HnswVectorIndex : IDenseIndex (если есть время и ресурсы).
  - ИЛИ интеграция hnswlib (C++ standalone, MIT license) как adapter.
  - Storage: edges в adjacency list, nodes в flat array.

Параметры per stack:
  BasicRag:       usually Exact; HNSW только если corpus > 100k units AND dense retrieval enabled.
                  Default params (когда используется): HNSW (M=16, efConstruction=100, efSearch=50).
  AgentLTM:       HNSW (M=32, efConstruction=200, efSearch=100) — quality
  CompiledWiki:   HNSW (M=32, efConstruction=200, efSearch=100)
  FullResearch:   HNSW (M=32, efConstruction=200, efSearch=100)

Storage estimate:
  - 1M units × 768-dim float32: 3 GB (vector) + ~600 MB (graph edges)
  - С quantized vectors: меньше.

Tradeoff vs BinaryCandidateFilter:
  - HNSW: лучше quality для high-recall (>0.97), random access slow.
  - BinaryCF: лучше для batch rerank + structured filtering.

See [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) for binary embeddings (extending PR #29 binary signatures to general semantic-preserving quantizers; XOR + POPCNT SIMD distance; quality vs storage tradeoff at 64/128/256/512 bits per dim).

## RFF KDE Semantic Router (experimental, pre-retrieval)

### Source

- Zenodo preprint: <https://zenodo.org/records/20737147> (2026-06-17, CC BY 4.0).
- Reference implementation: <https://gitlab.com/eidheim/kde-rf-classification> (MIT).

### What

Approximation of Kernel Density Estimation via Random Fourier Features. For each class, store only the mean of transformed embeddings (`z(x)`); per-class density is a single inner product at query time.

```text
m_c = (1/N_c) * Σ z(x_i)   over class-c examples
f_c(x) ≈ z(x)^T · m_c
```

- Label-informed weighting: modifies random-feature contributions using class labels (e.g. class-frequency reweighting, weighting by classification difficulty, or weighting by user-prior class importance). Exact weighting semantics should follow the reference paper; treat as configurable until experimentally validated.
- Feature pruning: drop weak RFF components whose absolute mean falls below a noise floor.
- Online update: per-class accumulator (`m_c`, `n_c`) updated incrementally when new labeled examples arrive.

### Where in architecture

Pre-retrieval semantic router. NOT a replacement for BM25, dense ANN, graph, or hybrid fusion. Routes a query to top-K candidate partitions, where K (or a score threshold, or adaptive K via confidence margin) is itself a hyperparameter selected to optimise downstream recall vs false-positive cost. Top-1, top-2, top-3, and adaptive-K configurations must all be evaluated. Acts before the hybrid candidate retrieval/fusion stage (today: before `IRetrievalEngine::retrieve()` in `HybridRetrievalEngine`; future versions may use a dedicated hybrid class).

### Use cases

- Semantic routing of memory partitions (project vs project, episodic vs fact).
- Streaming / incremental learning via per-class accumulator updates.
- Persona modeling: classify across axes like `technical_interest`, `current_project`.
- Out-of-distribution detector: low top-K margin (or low top-1 confidence when K=1) signals a novel memory type that should fall back to broad retrieval.

### Priority

AFTER the core retrieval pipeline: BM25 → dense ANN → hybrid fusion → reranker → metadata filters → graph / entity retrieval. Router is M2+ experimental and only adds value once the core pipeline is stable.

### Evaluation plan (when implemented)

- **Baselines:** cosine nearest centroid, logistic regression, linear SVM, kNN on raw embeddings, small MLP.
- **Hyperparameter sweep:** RFF count ∈ {256, 512, 1024, 4096}; kernels ∈ {Gaussian, Laplacian}.
- **Metrics:** macro-F1, top-K routing recall (sweep over K), **downstream** Recall@k / nDCG (headline: does routing improve retrieval?), latency per route, model size, incremental update cost.
- **Embeddings:** E5 / BGE, L2-normalized. Compare with and without normalization to disentangle kernel compatibility with cosine-trained embeddings.

### Open questions

- Whether RFF with 768-dim embeddings stays compact while preserving linear-classifier quality.
- Whether semantic embeddings (cosine-optimized) play well with Euclidean RBF / Laplacian kernels.
- Label acquisition: the method needs labels; current repo has none. Possible routes: manual seeding, weak supervision from `WritePolicy` decisions, or skip until labels exist.

### Status

Experimental, no PR planned yet. MIT-licensed reference code, so re-implementation in C++ is unconstrained.

### Cross-reference

Persistent class-mean storage (`m_c`, `n_c` per class) requires MDBX-backed state. See [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) §5.5 (memory-stack layer) for the candidate DBI shape.

## Secondary & Reverse Indexes

The knowledge base layers (lexical, graph, temporal, QA, metadata filters)
all share a common storage pattern: a primary table plus a set of secondary
indexes that turn a secondary key into a primary record reference. The full
design lives in [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) Section
12.3 and in [`lexical-search-roadmap.md`](lexical-search-roadmap.md). The
section here is the storage-shape view of that pattern.

All secondary indexes are scope-aware (ADR-012): every key begins with
`scope_id`. The reverse indexes that opt into DenseVectors also include
`projection_kind` so that one bucket never serves two projections.

### Pattern

```text
primary table:
    key   = primary_record_id (KnowledgeUnitId, ChunkId, etc.)
    value = payload blob

secondary index:
    key   = (scope_id, inverted_key [+, projection_kind])
            e.g. (scope_id, token, projection_kind, field)
                 (scope_id, metadata_key, encoded_value)
                 (scope_id, src_node_id, edge_kind, dst_node_id)
    value = primary_record_id or compact posting list (DUPSORT)
```

Secondary indexes are derived data. They are updated transactionally with
the primary table when the backend supports it; otherwise they are rebuilt by a
`compact_index()` pass. Backends are allowed to omit secondary indexes and
answer queries by scanning the primary table, but the public contract says
"should have them" so a future MDBX adapter can be benchmarked fairly against
the in-memory baseline.

### Concrete Index Catalog

```text
inverted_token_to_unit:
    key   = (scope_id, token_id, projection_kind, field_id)
    value = DUPSORT UnitId
    used by:  lexical search (pre-filter for phrase/proximity),
              lexical BM25F fallback when dense is unavailable

metadata_to_resource:
    key   = (scope_id, metadata_key, encoded_value, unit_id)
    value = empty
    [ReverseIndexTable]
    used by:  MetadataFilter, MetadataInFilter, MetadataTagFilter

field_to_postings:
    key   = (scope_id, projection_kind, field_id, token_id, unit_id)
    value = PostingStats
    used by:  BM25F and fielded sparse retrieval

graph_edges_by_src:
    key   = (scope_id, from_unit_id, edge_kind, to_unit_id)
    value = GraphEdgePayload (weight, reason, generation)
    used by:  IGraphStore::expand, outgoing lookup

graph_edges_by_dst:
    key   = (scope_id, to_unit_id, edge_kind, from_unit_id)
    value = GraphEdgePayload (edge_kind, weight, reason, generation)
    used by:  IRelationStore::incoming, reverse expansion

temporal_unit_index:
    key   = (scope_id, valid_from_ms, unit_id)
    value = valid_until_ms / compact validity payload
    used by:  ITemporalIndex::range, ITemporalIndex::at

Recorded/observed-time indexing is M2+ bi-temporal scope, not part of the M1
single-axis temporal contract.

speaker_to_units:
    key   = (scope_id, speaker_id, unit_id)
    value = empty
    used by:  SpeakerScopePolicy::include_*

session_to_units:
    key   = (scope_id, session_id, unit_id)
    value = empty
    used by:  session-scoped retrieval

usage_by_last_access:
    key   = (scope_id, last_used_at_ms, unit_id)
    value = empty
    used by: decay/archive scans

usage_by_cooldown:
    key   = (scope_id, cooldown_until_ms, unit_id)
    value = empty
    used by: cooldown expiry scans

unit_revision_index: optional, M2+
    key   = (scope_id, unit_id, revision)
    value = empty (presence-only)
    used by: stale-filter validation / debug / compaction events

resource_generation_index: optional, M3+
    key   = (scope_id, resource_id, resource_generation, unit_id)
    value = empty (presence-only)
    used by: resource reindex / derived record invalidation

M0/M1: индексы не создаются; per-posting `unit_revision` check inline
       (см. §"Stale Filter Pattern" ниже) — достаточно.
M2+: опциональные индексы для batch reindex / debugging.

binary_bucket_index:                     // if DenseVectors
    key   = (scope_id, model_id, model_version, projection_kind,
             descriptor_fingerprint, key_projection_id, short_key)
    value = posting list
            vector<BinaryBucketPosting>:
              { unit_id, full_signature, unit_revision,
                optional resource_generation }
    used by:  binary signature candidate filter

embedding_meta:                          // if EmbeddingMeta or EmbeddingMigration
    key   = (scope_id, unit_id, projection_kind, model_id, version)
    value = EmbeddingMetaComponent
    used by:  projection/model selection at retrieval time

embedding_vectors:                       // if DenseVectors
    key   = (scope_id, model_id, model_version, projection_kind, unit_id)
    value = vector_blob
    used by:  IDenseIndex::search, IDenseIndex::search_multi_model
```

The `inverted_token_to_unit` index is a thin convenience over the field
postings: the lexical index already knows the token -> unit mapping, but a
reverse index lets the QA and fact layers skip a full lexical scan when they
only need unit ids by token.

### Cross-Table Transaction Pattern

When a backend supports transactions, the writer follows this pattern:

```text
begin writable transaction
    upsert primary row(s)
    upsert secondary index entries
    update resource manifest if affected
    upsert embedding_meta + embedding_vectors if DenseVectors
    upsert binary_bucket_index entry if DenseVectors
commit
```

A partial failure (e.g. MDBX map full) rolls back the whole transaction.
Without transactions, secondary indexes are eventually-consistent: a
`compact_index()` pass reconciles them with the primary table.

### Bulk Index Build Versus Incremental Updates

```text
bulk build (startup, full reindex):
    scan primary table in (scope_id, key) order
    for each row, derive and upsert its secondary index entries
    one pass; O(N) work, single transaction when supported

incremental (per unit update):
    load old manifest
    delete old secondary entries for affected rows
    upsert new primary rows
    upsert new secondary entries
    upsert new embedding rows (per active projection_kind + model_id)
    commit
```

The bulk build path is the deterministic baseline; CI runs it on every test
corpus before benchmarking.

### Compression Of Secondary Index Values

Secondary index values are usually small (a `KnowledgeUnitId` is 36 chars or a
UUID; an `EdgePayload` is a few hundred bytes). Generic compression rarely
helps and breaks the key layout assumptions of DUPSORT tables. The default for
secondary indexes is no compression. Compression is only considered when:

- the value carries a long list (e.g. an embedded posting list per token), and
- a benchmark shows a measured gain on the target corpus.

### Stale Filter Pattern

Revision filtering (cheap stale-removal) — primary path через
`envelope.revision`:

```text
unit_id matches secondary index lookup (binary_bucket_index, edge indexes, ...)
    -> batch load current envelopes (по KnowledgeUnitId)
    -> для каждого candidate:
        skip if posting.unit_revision < envelope.revision  // stale signature
        (rare) skip if posting.resource_generation
                && ResourceManifest.generation differs      // derived record
        else: accept (compute Hamming / score / rank)
```

Стоимость: O(N_bucket) Hamming + O(K) batch envelope fetch.
Для 100k candidates × 128-bit: ~100k XOR + 200k popcount = <1 ms (scalar),
<0.3 ms (AVX-512 + VPOPCNTDQ).

The pattern must not turn secondary index lookups into many random reads.
Implementations can amortize revision checks by:

- keeping the current `envelope.revision` in a small in-memory cache per
  resource (один ко многим unit'ам);
- batching the envelope fetch with the secondary index read in the same
  transaction;
- storing a per-unit `last_seen_revision` / `unit_revision` inside the
  secondary index value itself (e.g. inside `BinaryBucketPosting`,
  `EdgePayload::unit_revision`).

Resource-level derived records (`ResourceManifest.generation`) обрабатываются
отдельно: secondary lookup по `resource_id` → manifest → filter derived
postings. Это path используется редко (только для reindex / invalidation).

### Schema Versioning

Every secondary index carries a `schema_version` byte in its value so old and
new indexes can be detected and rebuilt side by side during a rolling upgrade.
The first implementation pins `schema_version = 1`; bumping it is a breaking
change that requires a `compact_index()` rebuild.

When `DenseVectors` is enabled, the bucket index schema_version also reflects
its `projection_kind` layout. Adding a new projection kind bumps the version
once per index rebuild, never per write.

## Benchmark Tasks

### Lexical Baselines

- Keep a dependency-free BM25 baseline for hybrid retrieval experiments.
- Compare hybrid methods against BM25-only and vector-only baselines.
- Use [`guides/lexical-search-roadmap.md`](lexical-search-roadmap.md) for
  tokenization, postings, Unicode, phrase/proximity, fuzzy search, BM25F,
  and raw resource store planning.

### Exact Baseline

- Keep an exact brute-force float baseline for every approximate experiment.
- Use the same similarity metric as the approximate pipeline.
- If vectors are normalized, benchmark cosine optimized as dot product.
- Run the exact baseline per `(projection_kind, model_id)` pair so the
  approximate pipeline can be compared within the same slice.

### Projection-Aware Metrics

In addition to the standard metrics, record per projection kind:

```text
recall@1_by_projection
recall@5_by_projection
recall@10_by_projection
recall@50_by_projection
candidate_count_before_full_filter_by_projection
candidate_count_after_full_filter_by_projection
cross_model_rrf_lift   = recall@10(multi-model RRF) / max recall@10(single model)
```

Per-model slices are reported similarly when more than one embedding model is
active in the profile.

### Metrics

Record at least:

```text
recall@1
recall@5
recall@10
recall@50
candidate_count_before_full_filter
candidate_count_after_full_filter
latency
storage_size
read_amplification
decompression_time
rerank_time
migration_window_recall_drift     // migration telemetry
```

Initial target:

```text
recall@10 >= 0.95
candidate_count_after_full_filter << total_vectors
latency below brute force for large enough datasets
storage overhead acceptable for embedded use
```

### Per-Bit-Size Recall Targets

Targets для binary encoder variants (значения — `Recall@10(binary_only)`,
нормализованное против `Recall@10(float_baseline)`):

```text
autoencoder_binarizer_128bit:
  Recall@10(binary_only) >= 0.95 x Recall@10(float_baseline)
  candidate_count: ~1.5x float top-K при Recall@10 = 0.95

autoencoder_binarizer_64bit:
  Recall@10(binary_only) >= 0.90 x Recall@10(float_baseline)

random_hyperplane_lsh_64bit (baseline):
  Recall@10(binary_only) >= 0.85 x Recall@10(float_baseline)

random_hyperplane_lsh_128bit (baseline):
  Recall@10(binary_only) >= 0.90 x Recall@10(float_baseline)
```

Метрики для сравнения encoder'ов:

```text
- Recall@1/5/10/50 binary vs float ground-truth
- candidate count (size of bucket)
- latency p50/p95 (encoder + bucket lookup + rerank)
- index size (binary codes только vs binary + float)
```

### Per-Mode Recall Targets

Targets для `IDenseIndex` mode x encoder x bit_size (Recall@10 против
`Recall@10(ExactVectorIndex)` baseline = 1.00). Полная таблица и
mode-specific storage estimates живут в §"Dense Index Modes (Backend
Selection)" выше; здесь — benchmark matrix scope.

```text
ExactVectorIndex:                                1.00 (baseline).

BinaryCandidateFilter:
  RH-64:                                         >= 0.85
  RH-128:                                        >= 0.90
  AE-64:                                         >= 0.90
  AE-128:                                        >= 0.95   // production target
  AE-256:                                        >= 0.97

BinaryOnly (experimental):
  RH-128:                                        >= 0.80
  AE-128:                                        >= 0.85
  AE-256:                                        >= 0.90

ApproximateVector:
  AE-128 Compact:                                >= 0.90
  AE-128 Safe:                                   >= 0.95
  AE-256 Compact:                                >= 0.93
  AE-256 Safe:                                   >= 0.97
```

Bench harness расширен matrix ниже дополнительной осью `dense_index_mode`
с 4 значениями (Exact / BinaryCandidateFilter / BinaryOnly /
ApproximateVector). Recall@10 assertion проверяется per
`(mode, encoder, bit_size)` tuple. Все experimental modes (BinaryOnly,
ApproximateVector) тегнуты отдельно от production matrix и не блокируют
ship-it criteria для BinaryCandidateFilter — см. `memory-stacks-roadmap.md`
Section 13.

### Matrix

Benchmark:

```text
short signature bits: 24, 28, 32, 64
full signature bits: 128, 256, 512, 1024
short Hamming radius: 0, 1, 2, 3
projection kinds: Original, DenseContextual, [Bm25Body/Title/Heading/Symbols]
model ids: { bge-m3 }, { openai-3-small }, { bge-m3 + openai-3-small }
fusion: none, RRF k=60, WeightedMax
compression: none, zstd level 1, zstd level 3, zstd level 6, zstd dictionary
layout: two-stage bucket vs one-stage bucket
datasets: random, clustered, real text chunks with real embeddings
```

The cross-model matrix is gated on at least one profile having multi-model
embeddings enabled (FullResearchMemoryStack, for instance).

### Bucket Diagnostics

Track:

```text
total_vectors
total_buckets_used
empty_bucket_ratio
avg_bucket_size
max_bucket_size
p50_bucket_size
p95_bucket_size
p99_bucket_size
hot_bucket_count
bucket_fragmentation     // stale entries per bucket
rewrite_dwell_ms         // compaction rewrite latency p95
```

When buckets are too hot, test mixed short keys, another seed, another
encoder, a larger short-key bit count, or splitting the bucket by
`projection_kind`.

## Planned CMake Options

Add these only when the corresponding implementation exists:

```cmake
AGENT_MEMORY_ENABLE_BINARY_INDEX
AGENT_MEMORY_ENABLE_ZSTD
AGENT_MEMORY_ENABLE_EIGEN
AGENT_MEMORY_BUILD_BENCHMARKS
```

Planned public feature macros:

```cpp
AGENT_MEMORY_HAS_BINARY_INDEX
AGENT_MEMORY_HAS_ZSTD
AGENT_MEMORY_HAS_EIGEN
```

Once introduced, these macros should be defined consistently as `0` or `1`.

## Recommended Implementation Order

This order is aligned with the 16-step sequence in
[`memory-stacks-roadmap.md` Section 16](memory-stacks-roadmap.md#16-recommended-implementation-order).
Steps 1-3 mirror Layer 1 math and compression baseline; steps 4-6 introduce
projection-aware vector encoding; steps 7-9 add the binary signature and
bucket layers; steps 10-13 add scope-aware secondary indexes; steps 14-15
add projection-aware benchmarks.

### Steps 1-3: Math, compression, and Zstd baseline (unchanged)

1. Dependency-free vector math helpers.
2. Compression contracts with `CompressionCodec::None`.
3. Optional Zstd adapter.

### Steps 4-6: Vector encoding with projection awareness

4. Compressed document/chunk text storage (projection-agnostic).
5. Dependency-free binary signature value types and Hamming distance, with
   `projection_kind` recorded in `BinarySignatureInfo`.
6. Random-hyperplane binary encoder keyed by
   `(model_id, model_version, projection_kind, dim, seed)`. Compressed float storage
   benchmark for embedding blobs.

### Steps 7-9: Binary signature, bucket, and projection layout

7. Short-key generation and Hamming neighbor masks. Bucket keys are scoped by
   `(scope_id, model_id, model_version, projection_kind,
   descriptor_fingerprint, key_projection_id, short_key)`.
8. In-memory binary bucket index prototype with scope-aware and
   projection-aware buckets.
9. Recall/latency benchmark against exact float search, run per
   `(model_id, model_version, projection_kind)` slice.
   - Keep early binary-code health diagnostics deterministic so regressions are
     reproducible.
   - Add statistical pairwise-distance estimates later, after large dense
     workloads exist: seed, sample count, repeat count, mean/stddev, CI95, and
     the sampling policy.

### Steps 10-13: Scope-aware secondary indexes and resource manifest

10. Resource manifest contracts for targeted reindexing, scope-aware.
11. MDBX-backed two-stage bucket storage with
     `embedding_vectors: (scope_id, model_id, model_version, projection_kind, unit_id)` and
     descriptor-scoped `binary_bucket_index` keys from §"Binary Bucket Index
     Tasks", one key projection table initially.
12. Bucket compression benchmarks and bucket diagnostics.
    - Separate deterministic diagnostics from statistical estimates; do not
      treat a fixed sampled health metric as a confidence-bounded benchmark
      result.
13. Generation-aware stale filtering and compaction hooks.

### Steps 14-15: Projection-aware benchmarks and one-stage variants

14. Optional Eigen rerank/scoring adapter, applied per
    `(model_id, model_version, projection_kind)` candidate list.
15. One-stage bucket benchmark variant, projection-aware matrix, and
    cross-model RRF benchmark when at least two models are present.

### Steps 16-19: Knowledge base integration (unchanged shape, scope-aware)

16. Cross-table transaction pattern: a single MDBX writable transaction
    updates a primary row plus its scope-aware secondary index entries plus
    its resource manifest entries plus its embedding rows. Includes a failure
    test that asserts atomicity (no half-committed secondary index).
17. MDBX-backed `KnowledgeUnitStore` with `(scope_id, unit_id)` and
    `knowledge_units_by_kind` reverse index. Each kind has a kind-specific
    layout. The store ships with a `compact_index()` pass that rebuilds the
    reverse index from the primary table.
18. MDBX-backed `FactStore`, `QAKnowledgeBase`, `GraphStore`, and
    `TemporalIndex`. Each one is a thin shim over its in-memory baseline that
    uses the cross-table transaction pattern from step 16. Each store ships
    with a revision-aware reindex path that is wired into the
    `ResourceIndexer` (uses `envelope.revision` per `unit_id` для stale-filter;
    см. §"Stale Filter Pattern" выше).
19. Schema versioning: every secondary index value gains a `schema_version`
    byte (default `1`). Bumping the version requires a `compact_index()`
    rebuild. Tests assert that a downgrade does not silently corrupt lookups.

### Step 20: Embedding migration workflow

20. EmbeddingRecomputeJob end-to-end. Validates the migration path described
    in the "Embedding Migration Workflow" section above: per-(unit_id,
    projection_kind) recompute, dual-write of new embeddings with
    `is_active = true`, old embeddings retained with
    `is_active = false`, progress reported through `compaction_handoffs`,
    and cross-model RRF searches during migration without empty windows.

### Step 21: Final storage layer integration

21. Final storage layer integration: the `ResourceIndexer` calls the unit
    store, the fact store, the QA store, the graph store, the temporal
    index, the lexical field postings, the embedding store, and the binary
    bucket index in a single transaction. The golden dataset
    (`memory-stacks-roadmap.md` Section 13 ship-it criteria) is run
    end-to-end and the hybrid lift target (Recall@10 hybrid >= 1.20x
    BM25-only) is asserted, including the projection-aware and
    cross-model slices.

### Step 22: Encoder Registry + Autoencoder Binary Encoder (M2 experimental)

22. `BinarySignatureEncoderRegistry` с `.bse` file loading.
    - `AutoencoderBinarySignatureEncoder` implementation (matmul + sign).
    - Multi-encoder per `(unit, encoder_id)` storage с sparse
      `binary_signature_by_unit` table.
    - Migration flow (write-new → read-both → bg-reindex → delete-old).
    - Per-stack default encoder selection (см. §"Encoder Registry и Versioning").
    - Evaluation harness: autoencoder vs random-hyperplane, per bit size
      (32/64/128/256), с recall@1/5/10/50 targets из §"Per-Bit-Size Recall
      Targets".
    - Stack config dir loading через `MemoryStack::open(...)`.

### Steps 23-25: Multi-Mode IDenseIndex (M2 → M3)

Шаги 23-25 вводят `IDenseIndex` с 4 backend стратегиями
(см. §"Dense Index Modes (Backend Selection)" выше для интерфейсов,
storage estimates, quality targets и per-stack defaults).

23. **Step 23: BinaryCandidateFilter mode (default production).**
    - `IDenseIndex` interface + `DenseIndexMode` enum + `DenseIndexConfig` в
      `MemoryProfileSpec`.
    - `ExactVectorIndex` (brute-force float cosine) реализация + benchmarks
      против in-memory baseline.
    - `BinaryCandidateFilterIndex` (Hamming bucket → candidate set → float
      rerank) реализация, переиспользует `binary_bucket_index` из Step 12.
    - Integration с `BinarySignatureEncoderRegistry` (Step 22) — encoder_id
      резолвится через registry.
    - Evaluation harness: Recall@10 per bit size (64/128/256) per encoder
      (RH/AE), targets из §"Quality Targets Per Mode".
    - Per-stack default mode selection для AgentLTM, CompiledWiki, FullResearch.

24. **Step 24: ApproximateVector mode (experimental).**
    - `ApproximateVectorIndex` с двумя вариантами: Safe (binary + float +
      decoder) и Compact (binary + decoder only).
    - `IBinarySignatureEncoder::decode()` extension — optional decoder
      matrix в `.bse` file format (см. §"Encoder Registry и Versioning").
    - Storage toggle через `DenseIndexConfig::store_decoder` +
      `store_float_fallback`.
    - Evaluation harness: Compact vs Safe per bit size, quality gap
      documented vs production BinaryCandidateFilter targets.

25. **Step 25: BinaryOnly mode (experimental, compact).**
    - `BinaryOnlyIndex` (Hamming bucket → Hamming rank, no rerank).
    - Float embeddings могут быть dropped после confirmation; storage
      savings ~190x на 1M units.
    - Migration tooling: Exact/BinaryCandidateFilter → BinaryOnly drop-flow
      (см. §"Multi-Mode Migration").
    - Quality risk accepted by user (mode = experimental). Recall@10 targets
      из §"Quality Targets Per Mode" — experimental thresholds, не
      production.

### Steps 26-28: SIMD abstraction и HammingTopK kernel (M2 → M3)

> **Status update.** Step 26 and the flat-scan portion of Step 27 are
> implemented dependency-free with runtime fallbacks. The experimental
> `MultiProbeHammingIndex` is not yet the planned production high-recall bucket
> backend: at useful recall it did not beat the optimized flat scan in the first
> directional experiment. A later comparable-baseline run showed that the
> high-quality `512-bit x 500-candidate` flat filter was only `1.12x` faster
> than a contiguous exact cosine scan at 128 source dimensions in one local
> run, despite remaining `2.25x` faster than the current `ExactVectorIndex`.
> Treat this as directional evidence, not a production default. AVX-512 and
> persisted bucket integration remain planned.
>
> **Post-PR57 follow-up order.** Do not treat random-hyperplane LSH as the
> final binary encoder. The next binary PRs should follow
> [`binary-embeddings-roadmap.md`](binary-embeddings-roadmap.md) §"Post-PR57
> encoder and index taxonomy": first compare zero-training encoder families
> (`sign(x)`, random hyperplanes, randomized Hadamard projection as a
> Faiss-inspired but not Faiss-equivalent structured baseline), then evaluate
> a global learned projection, then pass the precomputed/frozen-embedding qrels
> gates, then evaluate aggregate signatures and cluster/document-local
> projections only behind a global routing stage.

26. **Step 26 (M2): SIMD abstraction layer.**
    - `simd::popcount64()` — cross-platform wrapper (MSVC / GCC / Clang /
      software fallback).
    - `simd::hamming_distance_u64()` — scalar baseline + AVX2 / AVX-512
      dispatch (через runtime detection).
    - CPU feature detection через `cpu_features` library (Google) или
      platform intrinsics (`__cpuid` / `getauxval`).
    - Покрытие тестами: cross-platform popcount identity, Hamming distance
      reference vector, dispatch path selection per detected CPU.

27. **Step 27 (M2/M3): HammingTopK kernel.**
    - Bucket prefilter → linear Hamming scan → top-K binary heap.
    - AVX2 dispatch: 4-way XOR + popcount (scalar `__builtin_popcountll`
      per lane ИЛИ nibble LUT через `_mm256_shuffle_epi8 + _mm256_sad_epu8`),
      2-4x speedup vs scalar (НЕ 4-8x — `_mm256_popcnt_u64` НЕ существует).
    - AVX-512 + VPOPCNTDQ dispatch: 8-way XOR + popcount
      (`_mm512_xor_si512` + `_mm512_popcnt_epi64`), 8-16x speedup.
    - Runtime CPU detection в `MemoryStack::open` (НЕ compile-time
      dispatch).
    - Benchmark-driven выбор kernel (НЕ преждевременная оптимизация);
      см. §"Benchmark Tasks" выше.

    Additional measured follow-ups before promotion:
    - Compare cross-record SIMD or Harley-Seal carry-save accumulation with
      the current per-signature kernel for wide codes and large batches.
    - Reuse query-scoped distance/top-k workspaces and consider compact
      `uint16_t` distances when `bit_count <= 65535`.
    - Provide a lightweight candidate-position API for no-metadata hot paths;
      materialize public records only after selection.
    - Benchmark a compact row-major or blocked dense rerank store, ordered for
      candidate locality, against both `ExactVectorIndex` and a contiguous
      compute-oriented exact oracle.
    - Evaluate int8 intermediate reranking and adaptive per-query candidate
      budgets only after exact float rerank remains the quality oracle.
    - Extend code-health diagnostics with sampled inter-bit correlation,
      effective rank, and mutual information with relevance outcomes before
      promoting learned or hand-tuned projections.

28. **Step 28 (M3): Eigen encoder optional adapter.**
    - `IAutoencoderEncoder` interface в core (dependency-free contract).
    - `EigenAutoencoderEncoder` implementation guarded by
      `AGENT_MEMORY_ENABLE_EIGEN` (default OFF).
    - `IAutoencoderDecoder` interface (optional, для `ApproximateVector`
      mode).
    - Decoder inference на Eigen (если `ApproximateVector` mode).
    - Сравнительный benchmark Eigen vs std-only baseline; targets из
      §"Per-Bit-Size Recall Targets" остаются обязательными.

### Steps 29-31: Additional dense index and codecs (M2+)

29. **Step 29 (M2): HNSW Vector Index (HnswVectorIndex or hnswlib adapter).**
    - 5-й `IDenseIndex` mode (см. §"HNSW Vector Index" выше).
    - Per-stack параметры (M, efConstruction, efSearch) — см. таблицу.
    - Benchmark vs Exact и BinaryCandidateFilter: Recall@10 ≥ 0.97, latency reduction.
    - Tradeoff: graph storage overhead ~20% vs random access latency.

30. **Step 30 (M2): MatryoshkaTruncationCodec.**
    - `IEmbeddingCodec` implementation — store первые N координат (64/128/256/512).
    - ~6x compression для 768 → 128.
    - Decode zero-pad до полного dim, cosine.
    - Reference: arXiv:2205.13147.

31. **Step 31 (M2): ProductQuantizationCodec (cold storage tier).**
    - `IEmbeddingCodec` implementation — K-means на sub-vectors, uint8 codes.
    - ~96x compression для 768-dim с 8 sub-vectors.
    - Asymmetric ADC distance computation.
    - Cold storage tier (архивные embeddings), не hot retrieval.
    - Reference: arXiv:1702.08734 (FAISS line).

## Non-Goals For The First Optimization Pass

- Do not replace `std::vector<float>` in `Embedding`.
- Do not make Eigen a required public dependency.
- Do not compress hot vector-search data with a generic compressor without a
  benchmark.
- Do not treat binary Hamming results as final retrieval ranking.
- Do not add product quantization before simpler float16/int8/binary
  experiments and exact baselines exist.
- Do not collapse multi-projection embeddings into a single `(scope_id,
  unit_id)` key: ADR-007 requires `(scope_id, model_id, model_version,
  projection_kind, unit_id)`.
- Do not skip `scope_id` in any secondary index: ADR-012 mandates
  scope-awareness for multi-tenancy.
- Do not silently mix embeddings across `projection_kind` values during
  fusion or rerank.

## References

- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) — Layer 2 context,
  ADR-007 (multi-projection embeddings), ADR-012 (scope-aware secondary
  indexes), capability matrix, implementation
  order (Section 16), open issues (Section 17, especially 17.8 for
  embedding migration).
- [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) —
  canonical physical MDBX manifest and DBI budget (§5.5/§5.5.1).
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) — Envelope +
  retrieval flow that this optimization layer serves.
- [`lexical-search-roadmap.md`](lexical-search-roadmap.md) — BM25F over
  projections, scope-aware inverted indexes, postings.
- [`compaction-roadmap.md`](compaction-roadmap.md) — CompactionWorker job
  types, handoff structure used by EmbeddingRecomputeJob.
- [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) —
  TypeDiscriminatedTable for components, MultiTableWriter for atomic
  cross-table writes.
- [`architecture.md`](architecture.md) — 4-layer model and Maturity Levels.
- [`cli-roadmap.md`](cli-roadmap.md) (future) — `agent-memory-cli` target,
  embedding-migration telemetry inspection.
- arXiv 1803.09065: "Near-lossless Binarization of Word Embeddings".
  <https://arxiv.org/abs/1803.09065>
- arXiv:1603.09320: "HNSW — Efficient and robust approximate nearest neighbor search".
  <https://arxiv.org/abs/1603.09320>
- arXiv:1702.08734: "Billion-scale similarity search with GPUs" (FAISS line).
  <https://arxiv.org/abs/1702.08734>
- arXiv:2205.13147: "Matryoshka Representation Learning".
  <https://arxiv.org/abs/2205.13147>
- arXiv:1612.03651: "FastText.zip: Compressing text classification models".
  <https://arxiv.org/abs/1612.03651>
- arXiv:2510.27232: "A Survey on Deep Text Hashing".
  <https://arxiv.org/abs/2510.27232>
- arXiv:1807.05614: "ANN-Benchmarks".
  <https://arxiv.org/abs/1807.05614>
- arXiv:2402.03216: "BGE M3-Embedding" (multi-modal reference).
  <https://arxiv.org/abs/2402.03216>
- arXiv:2409.17424: "Results of the Big ANN: NeurIPS'23 competition".
  <https://arxiv.org/abs/2409.17424>
- arXiv:2105.09613: "FreshDiskANN" (M3+ reference).
  <https://arxiv.org/abs/2105.09613>
- See also: guides/research-reading-map.md для curated bibliography.
- ai-agent-playbook: related materials on embedding binarization patterns.
- §"Future Encodings" (this document) — `BinarySignatureEncoderId` enum
  taxonomy (`RandomHyperplaneLSH`, `AutoencoderBinarizer`,
  `HaarLikeExperimental`) — ID source-of-truth для `DenseIndexConfig::encoder_id`
  и `BinarySignatureEncoderRegistry`.
- §"Dense Index Modes (Backend Selection)" (this document) — 4-mode
  `IDenseIndex` interface (Exact / BinaryCandidateFilter / BinaryOnly /
  ApproximateVector), `DenseIndexConfig` shape, storage estimates, quality
  targets, per-stack defaults, multi-mode migration flow и
  `RetrievalPlan::dense_index_mode_override`.
- Eigen library: optional dependency для dense vector math
  (`AGENT_MEMORY_ENABLE_EIGEN`, default OFF). Используется за
  `IAutoencoderEncoder` / `IAutoencoderDecoder` interface boundary;
  см. §"Eigen и SIMD стратегия" выше для maturity breakdown и hot path
  analysis.
- `cpu_features` library (Google): cross-platform CPU feature detection
  (POPCNT, SSE4.2, AVX2, AVX-512, NEON). Рекомендован для runtime SIMD
  dispatch в `HammingTopK` kernel (Step 27).
- Intel Intrinsics Guide: AVX2/AVX-512 POPCNT intrinsics — reference для
  Hamming scan kernel implementation:
  - AVX-512 + VPOPCNTDQ: `_mm512_xor_si512`, `_mm512_popcnt_epi64`
    (8× uint64_t per instruction).
  - AVX2: `_mm256_xor_si256` (vector XOR, 4× uint64_t parallel) +
    scalar `__builtin_popcountll` / `__popcnt64` per lane ИЛИ nibble LUT
    (`_mm256_shuffle_epi8` + `_mm256_sad_epu8`).
  - Замечание: `_mm256_popcnt_u64` НЕ существует (это AVX-512 VPOPCNTDQ).
- §"Eigen и SIMD стратегия" (this document) — maturity breakdown, hot
  path analysis, CMake flags, `simd::` abstraction layer, `HammingTopK`
  kernel design, Eigen encoder memory sizes, `IAutoencoderEncoder`
  interface pattern, training pipeline boundaries.
