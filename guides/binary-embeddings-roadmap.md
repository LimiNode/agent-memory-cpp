# Binary Embeddings Roadmap

> C++17 compliance: кодовые сниппеты используют `const std::vector<T>&` вместо `std::span` и явные конструкторы вместо designated initializers. Binary signatures (`BinarySignature`, `IBinarySignatureEncoder`, `BinarySignatureEncoderRegistry`, `RandomHyperplaneLSH`, `binary_bucket_index`, `DenseIndexMode::BinaryCandidateFilter`) — это cache-friendly fingerprints для coarse filter (bucket index); binary embeddings — это semantic-preserving quantizers общего назначения. Этот гайд расширяет roadmap binary signatures до общего compression layer; cross-link с [`optimization-roadmap.md`](optimization-roadmap.md) §"Binary Signature Index Tasks" обязателен.

> **Implementation status.** `BinarySignature`, width-aware batch Hamming kernels, binary-code health diagnostics, `IBinarySignatureEncoder`, the materialized/SIMD `RandomHyperplaneBinaryEncoder` v2, `CoordinateSignBinaryEncoder`, dependency-free `RandomizedHadamardBinaryEncoder`, sparse and batch encoding, `BinarySignatureInfo`, `BinarySignatureEncoderRegistry`, optimized `FlatBinarySignatureIndex`, and experimental in-memory `MultiProbeHammingIndex` are implemented in `src/agent_memory/index/`. A synthetic 128-dimensional run found that the high-quality random-hyperplane `512-bit x 500-candidate` filter was `2.25x` faster than the current `ExactVectorIndex`, but only `1.12x` faster than a comparable contiguous exact scan; a later zero-training encoder grid found that randomized Hadamard projection improved candidate coverage at the same bit budget on the 128D power-of-two fixture. Both results are directional. Persisted `.bse` loading, production `binary_bucket_index`, dense-index integration, high-recall Hamming ANN, `AutoencoderBinarySignatureEncoder`, `IAutoencoderEncoder`, and `IAutoencoderDecoder` remain roadmap-only and require separate PRs.

## Source attribution policy

Этот гайд синтезирует материал из нескольких источников. Цитаты следуют двухуровневому паттерну:

- `[Source: <public citation>]` — основная цитата на опубликованную работу, документацию проекта, ревизию репозитория или статью. При цитировании внешнего источника публичная ссылка авторитетна.
- `[Source: internal note — no public source available. Path: <path>]` — утверждение взято только из внутренних исследовательских заметок без эквивалентного публичного источника на момент написания. Требует дополнительной проверки.

Внутренние заметки — это discovery aids, а не авторитетные цитаты. Публичные roadmap-заявления цитируют публичный источник первым; внутренние заметки служат лишь маркером частного происхождения.

Этот гайд покрывает **binary embeddings** как общий compression layer поверх continuous эмбеддингов. Цель — дать набор решений «когда какой метод», включая relationship к binary signatures, SIMD-accelerated distance (XOR + POPCNT), и quality vs storage tradeoff на 64/128/256/512 bit.

Related roadmaps:

- [`optimization-roadmap.md`](optimization-roadmap.md) — основной документ: binary signature index, encoder registry, dense index modes, SIMD path.
- [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) — MinHash + LSH (Pattern 1) и RotSQ codec (Pattern 2), заимствованные из `codebase-memory-mcp`.
- [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) — typology of retrieval techniques; binary embeddings фильтруют candidate set, retrieval остаётся.
- [`memory-routing-roadmap.md`](memory-routing-roadmap.md) — pre-retrieval routing layer, sits "до" binary embedding filter.
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) — `HybridRetrievalEngine` контракт, на котором binary embeddings используются как candidate filter.

## §1. Purpose

Этот гайд существует для трёх целей:

1. **Extend binary-signatures roadmap to general compression layer.** Binary signatures (cache-friendly fingerprints для coarse bucket filter) are the low-level primitive; binary embeddings are semantic-preserving quantizers общего назначения, расширяющие signature-only подход до storage tier (binary + optional decoder) и hybrid modes (binary + dense rerank).
2. **Comparison framework.** Свести в одну карту методы binarization (sign-based, autoencoder, LSH, PQ), по единому набору осей: compression ratio, accuracy retention, training cost, inference cost, hardware-friendliness.
3. **Adoption guidance.** Для каждого метода пометить, что из него `agent-memory-cpp` может воспроизвести через наши planned primitives (`BinarySignature`, `BinarySignatureEncoderRegistry`, `IBinarySignatureEncoder`, `DenseIndexMode`) и что требует нового контракта.

Non-goals:

- Не повторять детальную спецификацию `BinarySignatureEncoderRegistry` (это в `optimization-roadmap.md` §"Encoder Registry и Versioning"). In-memory registry identity is implemented; `.bse` loading remains roadmap-only.
- Не описывать float32 / float16 / int8 codecs (это в `optimization-roadmap.md` §"Future Encodings").
- Не каталогизировать ANN-методы (HNSW, IVF, DiskANN — это в `optimization-roadmap.md` §"Dense Index Modes").

Binary embeddings — **не замена** full vectors. Это tradeoff между accuracy и storage/query cost. Quality-sensitive rerank остаётся обязательным; binary embeddings могут быть **only candidate filter** (planned `DenseIndexMode::BinaryCandidateFilter`) или **primary** storage tier (experimental modes `BinaryOnly`, `ApproximateVector`).

See [`advanced-binary-techniques-roadmap.md`](advanced-binary-techniques-roadmap.md) for research-level techniques extending beyond PR #29 binary signatures: Semantic Anchor Hamming Index, Accumulative Semantic Membership Sketch, and multi-slot binary semantic document signature.

## §2. Binarization Landscape

| Метод | Размер на вектор | Compression vs float32 | Качество (illustrative hypothesis) | Когда применять |
|---|---|---|---|---|
| **Float32 (baseline)** | d × 4 bytes | 1× | 100% | Ground truth, quality-sensitive rerank |
| **Float16** | d × 2 bytes | 2× | ~99.5% | GPU inference, mixed precision |
| **INT8 scalar quantization** | d × 1 byte | 4× | ~98% | pgvector, Weaviate default |
| **Matryoshka truncation (MRL)** | N × 4 bytes (N < d) | d/N× | varies | Adaptive serving, two-stage retrieval |
| **Product Quantization (PQ)** | m bytes (typ. 8) | 96× (768-dim) | ~95% | FAISS, Milvus, billion-scale ANN |
| **Binary (1 bit/dim)** | d / 8 bytes | 32× (768-dim) | ~85% | Coarse filter, edge, in-memory cache |
| **Binary learned (Tissier 2018)** | 8-64 B per vector (see table below) | 300d: 18.75-150×; 768d: 48-384× (see table below) | ~98% (256 bit) | Coarse-to-fine retrieval, dense storage tier |
| **LSH (random hyperplanes)** | 8-64 B per vector (see table below) | 300d: 18.75-150×; 768d: 48-384× (see table below) | ~85% (256 bit) | Cache-friendly candidate filter; `RandomHyperplaneBinaryEncoder` v2 is implemented, production bucket/ANN integration remains planned |
| **RotSQ** | ~6 bytes/dim + 12 B metadata | ~6× | ~95% | Sibling codec alongside Matryoshka, PQ |

> All values above are **illustrative starting hypotheses, not validated cross-codec quality targets**. Recall@10 numbers depend on:
> - embedding model (BERT vs E5 vs BGE behave differently under quantization);
> - corpus (technical text, conversational, structured vs unstructured);
> - retrieval metric (Recall@10 vs nDCG@10 vs MRR);
> - quantization training data (or lack thereof).
>
> Tissier et al. 2018 reported near-float results for some 256–512-bit word-embedding tasks; **transfer to sentence-transformer retrieval is unvalidated**. Do NOT quote these numbers as production acceptance contracts.

Binary embeddings занимают **наименьший размер на вектор** при сохранении семантики. Compression ratio strongly depends on dimensionality; см. per-dimension table ниже. Quality retention при увеличении bit budget — illustrative hypothesis, см. caveats.

Compression ratios for `Binary learned` and `LSH` не универсальны — они зависят от embedding dimensionality:

| Dimensionality | float32 size | Compression at 64 bits | Compression at 512 bits |
|---------------|--------------|------------------------|--------------------------|
| 300-dim (e.g., GloVe) | 1,200 B  | ~150× (≈8 B vs 1,200 B) | ~18.75× (≈64 B vs 1,200 B) |
| 768-dim (e.g., BERT/E5/BGE) | 3,072 B | ~384× (≈8 B vs 3,072 B) | ~48×  (≈64 B vs 3,072 B) |

Note: actual ratios depend on dimension. Numbers above are upper and lower bounds; intermediate bit counts (128, 256 bits) yield intermediate ratios.

> Note: compression ratio for a `bit_count`-bit code on a `dim`-dimensional float32 embedding:
> 
> ```text
> compression_ratio = (dim × 4) / (bit_count / 8)
> 
> Example for 768d float32 with 256-bit code:
>   compression_ratio = (768 × 4) / (256 / 8) = 3072 / 32 = 96×
> ```
> 
> The code is a single `bit_count`-bit value stored once per vector, NOT `bit_count / dim` bits per dimension.

### §2.1. Post-PR57 encoder and index taxonomy

PR #57 validated only the first serious baseline: a shared random-hyperplane
projection (`sign(Rx)`) used as a flat or bounded multi-probe Hamming candidate
filter, followed by exact dense rerank. It does **not** close the binarisation
track. Future work must keep two axes separate:

- **Encoder family:** how a float vector becomes bits.
- **Index family:** how those bits are searched.

Faiss makes the same distinction in its index taxonomy: `IndexLSH` stores
binary codes and compares them by Hamming distance, while scale-oriented
families such as IVF, HNSW, PQ, and IVFPQ are separate index/codec layers.
`IndexLSH` is an exhaustive flat scan over binary codes, not a bucket-based LSH
table candidate generator. Faiss also notes that its LSH implementation is not
vanilla independent random projection: it uses orthogonal projectors when
`nbits <= d` and a tight frame when `nbits > d`. Treat that as a useful next
baseline, not as proof that random-hyperplane LSH is enough for our workload.

### §2.2. External precedent and novelty boundary

Binary candidate filtering followed by oversampling and exact/full-precision
reranking is not a novel idea by itself. Treat it as the baseline industry
pattern we must compare against, not as the research claim.

Known precedents:

- **Qdrant binary quantization** keeps quantized vectors as the fast candidate
  layer and recommends rescoring with original vectors for quality recovery.
  <https://qdrant.tech/documentation/manage-data/quantization/>
- **Faiss binary indexes** separate float-to-binary transforms from binary
  Hamming indexes such as `IndexBinaryFlat`, `IndexBinaryIVF`,
  `IndexBinaryHNSW`, and `IndexBinaryHash` / `IndexBinaryMultiHash`.
  <https://github.com/facebookresearch/faiss/wiki/Binary-indexes>
- **Elasticsearch / Lucene Better Binary Quantization (BBQ)** combines bit-level
  search with oversampling, corrective factors, and reranking.
  <https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/bbq>

Project-specific value must therefore come from at least one of these axes:

- encoder construction: random, structured, PCA/ITQ, supervised, autoencoder,
  or model-specific compression;
- learned/adaptive projection `R`, with explicit artifact identity and
  retraining policy;
- supervised hashing from qrels, hard negatives, teacher scores, or synthetic
  query-document pairs, with leakage-safe train/eval splits;
- multi-chunk or memory-level aggregation rather than independent chunk-only
  codes;
- binary feature fusion: multiple code families, sparse+dense binary signals,
  or candidate voting;
- symmetric vs asymmetric quantization/scoring: binary document codes can be
  compared with a richer float/scalar query representation plus correction
  terms, rather than forcing query and document into identical Hamming-only
  codes;
- index design: MDBX-friendly buckets, multi-index hashing, HNSW-Hamming,
  IVF-like partitioning, deletion semantics, and incremental updates;
- measured binary candidate recall plus exact rerank behavior under production
  lifecycle constraints.

The multi-chunk axis is deliberately broader than "average chunk vectors".
Keep these experiments separate:

- **Document routing code:** a document-level code should fire when at least
  one child chunk is relevant.
- **Accumulated semantic code:** weak signals across several chunks can combine
  into a document/memory-level match.
- **Chunk-set sketch:** the code approximates presence/frequency of semantic
  features across a set of chunks.

Candidate aggregation objectives include OR/max pooling, sum + threshold,
counting or saturating counters, Bloom-like semantic sketches, learned set
encoders, and attention pooling. Add/delete semantics are part of the
acceptance criteria because memory objects evolve incrementally.

PR #77 adds the first dependency-free primitive for this axis:
`AggregateBinarySignatureBuilder`. It remains in the global shared binary space
and aggregates already-compatible chunk signatures with deterministic OR,
majority, all-bits, or fraction-threshold policies. It is not ASMS/MSBSE,
multi-slot document hashing, a learned set encoder, or a local projection
matrix; those remain separate research lanes in
[`advanced-binary-techniques-roadmap.md`](advanced-binary-techniques-roadmap.md).

| Family | Shared code space? | Training | Intended role | Notes |
|---|---:|---:|---|---|
| Coordinate sign `sign(x)` | Yes | No | Cheapest diagnostic baseline | One bit per source coordinate; no projection cost, but naturally supports only `bit_count == input_dimension`. |
| Random hyperplane `sign(Rx)` | Yes | No | Implemented baseline candidate filter | Good zero-training reference. PR #57 showed useful system speedups vs current `ExactVectorIndex`, but no decisive compute-only win vs contiguous exact scan at 128D. |
| Randomized Hadamard projection | Yes | No | Structured zero-training baseline | Faiss-inspired dependency-free baseline. For power-of-two input dimensions complete blocks use orthogonal Hadamard rows; for padded non-power-of-two dimensions partial row sets are not generally pairwise orthogonal. |
| PCA projection | Yes | Yes | First objective-based learned baseline | Dependency-free principal-axis projection with median thresholds. Supports `bit_count <= input_dimension`. Not ITQ and not an autoencoder. |
| Global learned projection | Yes | Yes | Main learned-hashing candidate | One corpus/calibration-trained `R`; preserves comparability across all records. PR #60 starts with a simple pair-difference baseline; PR #65 adds an unsupervised ITQ-style PCA rotation as a separately named candidate. |
| Supervised hashing | Yes | Yes | Label-aware learned hashing | Uses qrels, hard negatives, teacher scores, or synthetic pairs to pull relevant query-document pairs together in Hamming space and push hard negatives apart. Requires strict split/provenance controls. |
| Autoencoder binary encoder | Yes | Yes | Strong learned compression tier | More expressive than linear ITQ-like projection; requires training toolchain, persisted weights, and careful acceptance criteria. |
| Cluster-local projection `R_c` | Within a routed cluster | Yes | Hierarchical candidate filter | Query must first route to a small set of clusters; codes across unrelated clusters are not directly comparable. |
| Document-local projection `R_j` | Within one selected document | Yes | Local page/book/codebase scan | Valid only after document selection or inside a known scope; otherwise the query would need to be encoded once per document. |

The local variants are not drop-in replacements for a global binary index.
If document A uses `R_A` and document B uses `R_B`, then bit position 17 no
longer asks the same question in both codes. Hamming distance across those
codes is therefore not meaningful. Local projections belong after a global
router, not before it.

Post-PR57 PR ladder:

| PR | Scope | Success signal | Non-goal |
|---|---|---|---|
| PR #58 | Roadmap/taxonomy only: record the encoder families, Faiss-derived index lessons, and follow-up order. | Future agents can see that random hyperplane is only the baseline. | No code changes. |
| PR #59 | Add coordinate-sign and randomized-Hadamard encoder baselines; run the existing binary-rerank grid against all zero-training encoders. | Randomized Hadamard projection improves coverage at equal bit budgets in the synthetic 128D power-of-two grid; coordinate sign is a cheap one-bit-per-dimension diagnostic baseline. | No learned training pipeline and no Faiss-compatible projector claim. |
| PR #60 | Add a dependency-free global learned pair-difference projection baseline with persisted encoder identity. | Better candidate coverage at the same bit/candidate budget without evaluation-query leakage. | No ITQ claim, no autoencoder, and no per-document local matrices. |
| PR #61 | Run high-dimensional synthetic benchmarks (384/768/1536D) and record the real-embedding gap, still with exact dense rerank as oracle. | Determine whether the 128D bit/candidate band transfers to higher dimensions. | No production default switch and no synthetic result presented as real-embedding evidence. |
| PR #62 | Add a dependency-free PCA-style global projection baseline and run a focused 128D low-bit grid. | Test whether an objective-based learned projection beats random/pair-difference baselines. | No full ITQ, no autoencoder, and no high-dimensional PCA benchmark claim. |
| PR #63 | Close benchmark methodology gaps: learned training cost fields, raw report artifact policy, and stable performance-harness plan. | Future benchmark PRs can report cold-start/training cost and preserve raw outputs without bloating the repo. | No new encoder family. |
| PR #64 | Roadmap clarification: external binary-search precedent, project novelty boundary, and decoder-based embedding notes. | Future work does not overclaim binary prefiltering itself as novel. | No code changes. |
| PR #65 | Add ITQ-style rotation on top of the PCA projection artifact as a separately named encoder family. | Test candidate coverage, quantization loss vs PCA, bit occupancy/entropy, useful-bit count, training-seed stability, training time, artifact size, and serialized artifact identity. | No supervised labels, no autoencoder, and no local projections. |
| PR #66 | Real-embedding + locked-qrels evaluation gate. | Compare Hadamard/PCA/ITQ/float on real documents, real queries, locked qrels, bit/candidate budgets, training cost, artifact size, and leakage-safe data/encoder seeds. | No production backend choice from synthetic-only evidence. |
| PR #68 | Continue the precomputed embedding gate with fixture provenance and a medium frozen semantic-axis artifact. | Benchmark reports carry artifact identity, and CI validates a less trivial locked-qrels fixture without network/model dependencies. | No claim that the hand-authored fixture is external-model output. |
| PR #69 | Harden the precomputed embedding artifact provenance contract before adding external-model fixtures. | Revisions, prompt identities, normalization/dtype, hash algorithm, and hash scopes are explicit and validated end-to-end in reports. | No plausible-looking external fixture without reproducible artifact/config identity. |
| PR #70 | Add a verifier for precomputed embedding artifact hashes. | CI recomputes canonical config/payload SHA-256 values and catches stale or declarative hashes before external fixtures arrive. | No network/model dependency and no new quality claim. |
| PR #71 | Canonicalize precomputed artifact hash encoding. | A C++ verifier hashes binary float32 payload bytes, not incidental JSON number spelling, and negative tests reject config, vector, and record-order drift. | No new quality claim and no external-model dependency. |
| PR #72 | External-generator precomputed embedding fixture/benchmark continuation. | Add dataset/qrels content hashes, a dependency-free external text-embedding generator fixture, and a locked precomputed rerank gate. | No claim that the dependency-free hash fixture represents MiniLM/OpenAI/BGE/E5 quality. |
| PR #73 | Add the first actual third-party embedding-model fixture with `sentence-transformers/all-MiniLM-L6-v2`. | CI verifies a frozen 384D MiniLM artifact with pinned model/tokenizer revision, generator source hash, command/config, explicit float32 narrowing, and a compact 128-bit binary-rerank gate. | Network/model generation in CI; broad 256/512-bit or multi-seed quality claims. |
| PR #74 | Precomputed fixture provenance hygiene. | Split the shared fixture contract out of the surrogate generator, add a requirements-file hash identity, document the official regeneration environment, and make generator-source hash update steps explicit. | No new quality claim and no larger benchmark. |
| PR #75 | Larger real/precomputed embedding benchmark fixture. | Add a CI-friendly 36-document/12-query MiniLM fixture, verify artifact provenance, and run a compact 128/256-bit zero-training binary-rerank gate. | No production backend choice, no heavy learned-encoder CI grid, and no statistical benchmark claim. |
| PR #76 | MiniLM fixture provenance hygiene. | Share MiniLM generator runtime across small/large fixtures, include that runtime in generator source identity, and verify fixture content against its source contract without loading the model. | No regenerated vectors and no new benchmark claim. |
| PR #77 | Document or memory-level aggregate binary signatures. | Add a dependency-free aggregate signature builder with OR/max, majority/sum-threshold, all-bits/intersection, fraction-threshold policies, and explicit add/delete counter semantics. | No incompatible local projection matrices, no multi-slot ASMS/MSBSE claim, and no learned set encoder. |
| PR #78 | Precomputed fixture negative content-contract hardening. | CI proves corpus, query, and judgment drift are rejected with expected content-contract failures. | No new benchmark claim and no fixture regeneration. |
| PR #79 | Synthetic aggregate-signature benchmark gate. | Measure whether one aggregate document/memory code can recover exact object-level top-k candidates when each object has multiple chunks. | No real multi-chunk fixture claim, no ASMS/MSBSE claim, and no final-ranking quality claim. |
| PR #80 | Aggregate smoke identity hardening. | Pin the aggregate-signature smoke report identity fields in CI: seed, noise parameters, encoder family/version, and threshold fraction. | No new benchmark claim. |
| PR #81 | Scale and lifecycle benchmark gate before production backend selection. | Add a small CI-validated lifecycle report for exact vector, flat binary, and multi-probe binary indexes: build/search timing, payload bytes, candidate coverage, update/delete, rebuild, backend identity, lifecycle sizes, and bucket diagnostics. | No production backend decision, no persisted reload claim, and no stable large-scale benchmark result from a tiny smoke fixture. |
| Planned | Evaluate cluster-local/document-local projections only as hierarchical second-stage filters. | Show value after global routing, with query encoding cost counted per selected cluster/document. | No global comparison of incompatible local codes. |
| Planned | Choose index/backend direction: production binary bucket, MIH/HNSW-Hamming, IVF/PQ hybrid, HNSW dense baseline, or asymmetric quantization path. | Sub-linear candidate generation or clearly lower memory-bandwidth cost at target recall and lifecycle cost. | More flat-scan polishing unless a benchmark justifies it. |

Benchmark instrumentation contract:

- Binary grid reports include explicit learned-encoder training diagnostics:
  `encoder_training_ms`, `training_vector_count`, and projection artifact byte
  size. Query/build timing continues to exclude training cost, but reports make
  cold-start/index-build context visible for learned encoders.
- Experiment notes follow [`experiments/README.md`](experiments/README.md):
  commit compact interpretation tables, not large raw JSON dumps by default,
  and record enough command/config/head metadata to reproduce important runs.
- Remaining methodology work before treating numbers as stable benchmark
  evidence: repeated process-level runs, warm-up rules, environment notes, and
  preserved raw-output policy for selected benchmark suites.

Real-embedding evaluation gate:

- Use real embedding models and real documents/queries with locked qrels before
  choosing a production backend.
- Compare at least float exact/ANN, Hadamard, PCA, ITQ, and the strongest
  current learned projection baseline.
- Report binary candidate coverage, final exact-reranked quality, top-1
  agreement, bit/candidate budgets, training time, artifact size, and runtime
  latency.
- For trained encoders, train only on allowed training documents/pairs. Do not
  train on evaluation queries, evaluation qrels, or hard negatives mined from
  the locked test split.
- Split by corpus/query provenance strongly enough to prevent chunks from the
  same document, user, conversation, or time period from leaking across
  train/validation/test when that matters for the target workload.

Learned-artifact lifecycle contract:

- Every global learned encoder artifact (`R`, PCA/ITQ rotation, thresholds,
  normalization and source-model provenance) has a versioned identity and a
  compatibility fingerprint.
- Search across old and new artifacts is allowed only as an explicit
  multi-encoder migration mode: encode the query once per active artifact,
  search each compatible code space separately, then merge/rerank candidates.
- A partial corpus re-encoding must record which artifact produced each stored
  code. Mixing codes from different artifacts in one Hamming space is invalid.
- Production rollout needs atomic activation, rollback, rebuild policy,
  delete/update behavior during migration, and cold-start/reload validation.

Supervised-hashing data contract:

- Build supervised encoders only after a split policy exists for corpus,
  queries, qrels, hard negatives, teacher scores, and synthetic pairs.
- Mine hard negatives only from the training split or from explicitly allowed
  validation workflows; never from locked test qrels.
- Select encoder families/hyperparameters on validation, then run one locked
  test evaluation for reporting.

Coordinate-sign benchmark contract for PR #59:

- Pure coordinate sign emits exactly `input_dimension` bits.
- It participates in the bit-width grid only when `bit_count == input_dimension`.
- `bit_count > input_dimension` is not supported for pure coordinate sign:
  padding or duplicated coordinates do not add information.
- `bit_count < input_dimension` requires a separately named coordinate-subset
  policy and should not be presented as the default `sign(x)` baseline.
- Randomized Hadamard projection is the zero-training family that can cover the
  full bit-width grid in this PR. It is a structured Hadamard projection, not a
  byte-for-byte Faiss `IndexLSH` projector. For power-of-two input dimensions,
  a complete block has orthogonal rows; for non-power-of-two dimensions,
  zero-padding gives a complete-block tight frame over the original coordinates,
  while partial row sets are not generally pairwise orthogonal.

[Source: Faiss wiki, "Faiss indexes": <https://github.com/facebookresearch/faiss/wiki/Faiss-indexes>]
<br>[Source: Charikar 2002, "Similarity Estimation Techniques from Rounding Algorithms": <https://www.cs.princeton.edu/courses/archive/spr04/cos598B/bib/CharikarEstim.pdf>]
<br>[Source: Gong et al., "Iterative Quantization: A Procrustean Approach to Learning Binary Codes": <https://slazebni.cs.illinois.edu/publications/ITQ.pdf>]
<br>[Source: internal note — no public source available. Path: `ai-agent-playbook/concepts/llm-research/Бинаризация эмбеддингов для экономии памяти и ускорения retrieval.md`]

## §3. Binarization Methods

### 3.1. Threshold-Based Sign of Embedding

```text
b_i = H(x_i)
```

`H` — Heaviside step function. Naive approach: один бит на компоненту embedding'а.

- **Pros:** zero training, dependency-free, deterministic.
- **Cons:** keeps same dimension; no CPU-register alignment; loses magnitude info (only sign); NOT invertible without a learned decoder or external magnitude metadata.
- **Use case:** experimental baseline; production-grade needs learned quantization.

> Zero-training and deterministic: `b_i = sign(x_i)` is one bit per component, requiring no model. Preserves only the sign of each component; the magnitude and ordering among positive values are NOT recoverable. Not invertible without a learned decoder or external magnitude metadata.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Бинаризация эмбеддингов для экономии памяти и ускорения retrieval.md]

### 3.2. Autoencoder-Binarization (Tissier 2018)

The Tissier et al. 2018 approach uses a learned autoencoder with weight tying:

```text
Encoder:  b = H(W · x),   W ∈ R^{m × d}
Decoder:  x_hat = tanh(W^T · b + c)
Loss:     L = MSE(x, x_hat) + lambda · Σ_{i<j} corr(b_i, b_j)^2
```

Weight tying is forced: `H` is non-differentiable, so the encoder does not receive a direct gradient. The gradient flows only through the decoder; `W` is updated by the decoder signal.

> **Gradient flow (tied weights):** With weight tying `W = W_decoder^T`, encoder weights `W` are updated indirectly through the decoder path during training. The binary encoder `H(Wx)` is non-differentiable; gradient does NOT pass through `H`. This is NOT a straight-through estimator (STE) — STE would require an artificial non-zero derivative at `H` during backward pass, which is not used here. The autoencoder instead learns useful `W` representations through decoder-side updates.

**Decorrelation regulariser** penalises pairwise correlations between code bits. Without it, the autoencoder learns to reconstruct numbers well (decoder MSE), but poorly preserves semantics — the encoder gradient is absent, and `W` discards semantic information in favour of numerical accuracy. The regulariser forces code bits to carry independent semantic information.

[Source: arXiv:1803.09065 — Tissier, Gravier, Habrard 2018] <br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Бинаризация эмбеддингов для экономии памяти и ускорения retrieval.md]

> **Reference implementation note.** Code (MIT-licensed) is available at <https://github.com/tca19/near-lossless-binarization>. The reference is a small CLI/training pipeline, not a reusable SDK — port the encoder/decoder classes to our dependency-free contract; do not copy verbatim.

Results on word-level embeddings (2018, GloVe 300-dim):

| Метрика | 64-bit | 128-bit | 256-bit | 512-bit | Float baseline |
|---|---|---|---|---|---|
| Spearman на WS353 (GloVe) | 30.1 | 44.9 | 56.6 | 60.3 | 60.9 |
| Accuracy на AG-News (dict2vec) | 85.3 | 85.9 | 87.7 | 87.8 | 89.0 |
| Top-10 query latency (ms, GloVe) | 2.72 | 2.89 | 3.25 | 4.29 | 98.08 |
| Load + Top-10 latency (ms) | 160 | 213 | 310 | 500 | 23 500 |

Key empirical findings:

- 256-bit: ~2% loss on semantic similarity and classification, 30× retrieval speedup.
- 64-bit: stronger compression (150×), but up to 16% loss on large datasets.
- On GloVe (400k words), 512-bit code approaches float baseline on average semantic correlation.

**Regularising effect.** Paradoxically, binary code sometimes **exceeds** float embeddings on downstream tasks. 512-bit fasttext on WS353 yields 70.3 vs 69.7 for float; 256-bit GloVe on SimVerb yields 22.9 vs 22.7. Authors interpret this as regularisation: noisy, weakly-informative dimensions of float vectors are discarded during 1-bit quantisation, leaving a "cleaner" representation.

### 3.3. Locality-Sensitive Hashing (LSH)

LSH for binarization uses random hyperplanes (planned: `RandomHyperplaneLSH` encoder, **not yet implemented**):

```text
bit_i = sign(dot(embedding, random_hyperplane_i))
```

- **Pros:** zero training; deterministic by `(dim, bit_count, seed)`; planned as the baseline encoder in the roadmap.
- **Cons:** no semantic preservation beyond the random hyperplane approximation; ~85-90% Recall@10 of float baseline at 128 bits (hypothesis, not contract).
- **Use case:** cache-friendly candidate filter; bucket prefiltering.

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Бинаризация эмбеддингов для экономии памяти и ускорения retrieval.md]

This is the **same approach** as the planned baseline encoder in the binary-signatures roadmap; cross-link to `optimization-roadmap.md` §"Baseline Encoder" and §"Binary Signature Index Tasks". This guide treats LSH as the first member of the binarization family (zero-training baseline); autoencoder-binarization is the learned upgrade.

### 3.4. Product Quantization (PQ) for Hybrid Binary + FP

PQ decomposes the embedding into sub-vectors and quantises each independently via k-means:

```text
1. Split x ∈ R^d into m sub-vectors of length d/m each.
2. K-means cluster each sub-vector on training data → dictionary of k centroids (typ. k=256, log2(k)=8 bits per sub-vector).
3. Replace each sub-vector with the index of its nearest centroid.
4. Storage: m bytes per vector (k=256).
```

Compression ratios (Joulin et al. 2016):

| Размерность | float32 | PQ (m=8) | Коэффициент |
|---|---|---|---|
| d=300 | 1200 B | 8 B | 150× |
| d=100 | 400 B | 8 B | 50× |
| d=300, m=4 | 1200 B | 4 B | 300× |

Asymmetric ADC (approximate distance computation) via lookup tables.

[Source: arXiv:1612.03651 — Joulin, Grave, Bojanowski, Mikolov 2016, FastText.zip] <br>[Source: arXiv:1702.08734 — Jégou et al., FAISS] <br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Бинаризация эмбеддингов для экономии памяти и ускорения retrieval.md]

PQ is **not strictly binary** — it produces byte codes, not bit codes. But it composes naturally with binary embeddings: the same dense index can mix a binary candidate filter (XOR + POPCNT) with a PQ byte code (ADC distance) for finer rerank. This is the **hybrid binary + FP** mode referenced in §"Implementation Ladder" below.

## §4. SIMD-Accelerated Distance

The killer feature of binary embeddings is **SIMD-accelerated distance via XOR + POPCNT**. Hamming distance between two binarized vectors reduces to:

```text
hamming_distance(b1, b2) = popcount(b1 XOR b2)
```

This is **cheap per operation** on modern x86 (POPCNT instructions on `uint64_t` words), but the wall-clock cost is NOT "one instruction per vector". Hamming distance between two 256-bit signatures decomposes into `4 × XOR64` + `4 × POPCNT64` + integer adds, plus loads, loop overhead, and top-K maintenance. AVX2 does NOT have native vector popcount (`_mm256_popcnt_u64` does not exist); a vectorized implementation typically uses a 16-bit nibble LUT + PSHUFB lookup (Bradford-Larson or similar). Wall-clock latency for 1M vectors depends on CPU, memory bandwidth, cache hit rate, and the top-K algorithm; do not quote absolute numbers without a benchmark setup. Compare with cosine similarity, which requires multiplications and additions per dimension.

### 4.1. CPU Register Alignment

Bit sizes, register widths, and cache-line occupancy are **independent properties**. A 256-bit signature fits comfortably in one AVX2 register (32 bytes) but spans only **half** a 64-byte cache line. The table below separates the two:

| Bit size | Bytes | AVX2 register (32 B) fit | Cache line (64 B) occupancy | Use case |
|---|---|---|---|---|
| 32 bit | 4 B | one eighth | one sixteenth | experimental, super-fast coarse filter (NOT default) |
| 64 bit | 8 B | one quarter | one eighth | BasicRag default, minimum practical |
| 128 bit | 16 B | one half | one quarter | AgentLTM default, normal candidate filter |
| 256 bit | 32 B | full register | one half | CompiledWiki, quality-oriented |
| 512 bit | 64 B | spans 2 registers | full cache line | M2+ experimental, near-float quality |

Rationale: 64/128/256 bits align with CPU register sizes (`uint64_t`, SSE, AVX2) on the register side; on the cache-line side, the same bit counts occupy 1/8, 1/4, 1/2 of a 64-byte cache line respectively. 32-bit is not alignment-friendly for popcount/Hamming through `__builtin_popcount`. A 512-bit signature occupies a full cache line, which is desirable for sequential scans and SIMD loads but increases cache pressure when many signatures are processed together.

### 4.2. SIMD Dispatch

| ISA | Path | Speedup vs scalar | Notes |
|---|---|---|---|
| Scalar | `popcount64` intrinsic | 1× (baseline) | Cross-platform wrapper |
| SSE4.2 | `_mm_popcnt_u64` (64-bit only) | 2× per register | Available on most x86_64 |
| AVX2 | `_mm256_xor_si256` + nibble LUT (`_mm256_shuffle_epi8` + `_mm256_sad_epu8`) | 2-4× | `_mm256_popcnt_u64` does NOT exist (AVX-512 only) |
| AVX-512 + VPOPCNTDQ | `_mm512_xor_si512` + `_mm512_popcnt_epi64` | 8-16× | Skylake-X+ required |
| NEON | ARM popcount | varies | cross-platform via runtime detection |

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Бинаризация эмбеддингов для экономии памяти и ускорения retrieval.md]

The `popcount64` wrapper:

```cpp
namespace simd {

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

This is the same SIMD layer defined in [`optimization-roadmap.md`](optimization-roadmap.md) §"Eigen и SIMD стратегия". Binary embeddings reuse `BinarySignature::hamming_distance(lhs, rhs)` and later the planned `HammingTopK` kernel (see `optimization-roadmap.md` §"HammingTopK kernel" for the full design).

## §5. Relationship to the Binary-Signatures Roadmap

Planned binary signatures vs planned binary embeddings — two sides of the same coin (all symbols roadmap-only):

| Aspect | Planned Binary Signatures | Planned Binary Embeddings |
|---|---|---|
| **Purpose** | Cache-friendly fingerprints (bucket prefilter) | Semantic-preserving quantizers (general compression) |
| **Storage** | Descriptor-scoped bucket index: `(scope, model, model version, projection, descriptor, key projection, short key)` | Optional embedding storage tier (binary only / binary + decoder) |
| **Encoder** | `RandomHyperplaneLSH` baseline + `AutoencoderBinarizer` M2+ | Multiple encoders (sign, LSH, autoencoder, PQ, RotSQ) |
| **Reconstruction** | None (fingerprint only) | Optional decoder for `ApproximateVector` mode |
| **Quality hypothesis** | Coarse filter: Recall@10 ≈ 0.95 (binary + float rerank, hypothesis) | Standalone: Recall@10 ≈ 0.95 (256+ bits, hypothesis); binary-only: ≈ 0.85 (hypothesis) |
| **Use in `DenseIndexMode`** | `BinaryCandidateFilter` (benchmark-gated candidate) | `BinaryCandidateFilter` / `BinaryOnly` / `ApproximateVector` |

The binary-signatures roadmap is **the first slice** of the binary embeddings roadmap. It plans to establish the encoder registry, signature value types, and bucket index. Binary embeddings roadmap **extends** that into:

- Standalone binary embeddings as a storage tier (drop float, keep binary).
- Hybrid binary + dense indexes (binary for candidate filter + dense rerank).
- Multiple encoder backends (sign-based, LSH, autoencoder, future RotSQ).
- Optional decoder for approximate vector reconstruction.
- Per-bit-size quality vs storage tradeoff tables.

Cross-link to [`optimization-roadmap.md`](optimization-roadmap.md) §"Dense Index Modes (Backend Selection)" and §"Binary Signature Index Tasks" is mandatory.

## §6. Quality vs Storage Tradeoff

Per-bit-size quality targets for binary embeddings. **The numbers below are starting hypotheses for benchmarking, not acceptance contracts.** Full quality targets across `DenseIndexMode` × encoder × bit_size live in [`optimization-roadmap.md`](optimization-roadmap.md) §"Quality Targets Per Mode" and §"Per-Bit-Size Recall Targets"; that file carries the same hypothesis caveat.

> **Hypothesis, not contract.** The numbers above are starting hypotheses for benchmarking, derived from Tissier et al. 2018 results on word-level GloVe embeddings. They MUST be re-validated separately for every target embedding model (BERT, E5, BGE) and dataset. Failure to reach these numbers does NOT indicate an implementation bug — it indicates the hypothesis needs recalibration. Recall@10 of modern sentence-transformer embeddings CANNOT be derived from Spearman correlation on WS353 or AG-News classification accuracy from 2018.

```text
autoencoder_binarizer_128bit (hypothesis):
  Recall@10(binary_only) ≈ 0.95 x Recall@10(float_baseline)    # Tissier-style extrapolation
  candidate_count: ~1.5x float top-K при Recall@10 = 0.95      # rough heuristic

autoencoder_binarizer_64bit (hypothesis):
  Recall@10(binary_only) ≈ 0.90 x Recall@10(float_baseline)

random_hyperplane_lsh_64bit (baseline, hypothesis):
  Recall@10(binary_only) ≈ 0.85 x Recall@10(float_baseline)

random_hyperplane_lsh_128bit (baseline, hypothesis):
  Recall@10(binary_only) ≈ 0.90 x Recall@10(float_baseline)

Tissier 2018 results (word-level GloVe 300-dim, 2018) — observation only:
  64-bit:  ~50% of float quality on WS353 Spearman
  128-bit: ~74% of float quality
  256-bit: ~93% of float quality, 30x retrieval speedup
  512-bit: ~99% of float quality, 24x retrieval speedup
```

[Source: arXiv:1803.09065 — Tissier, Gravier, Habrard 2018] <br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Бинаризация эмбеддингов для экономии памяти и ускорения retrieval.md]

Storage estimates per 1M units × 768-dim (hypotheses; expected compression ratios, not validated quality targets):

| Encoding | Storage | Compression vs float | Quality hypothesis |
|---|---|---|---|
| Float32 (baseline) | ~3.0 GB | 1× | 1.00 (ground truth) |
| Float16 | ~1.5 GB | 2× | ~0.99 |
| INT8 | ~768 MB | 4× | ~0.98 |
| Binary 64-bit (RH-LSH) | ~8 MB | ~384× | ~0.85 |
| Binary 128-bit (RH-LSH) | ~16 MB | ~192× | ~0.90 |
| Binary 256-bit (autoencoder) | ~32 MB | ~96× | ~0.95 |
| Binary 512-bit (autoencoder) | ~64 MB | ~48× | ~0.97 |
| Binary + decoder (AE-128, Compact) | ~16 MB + ~384 KiB decoder | ~192× | ~0.93 |
| Binary + decoder (AE-128, Safe) | ~3 GB + ~384 KiB decoder | ~1× | ~0.97 |
| Binary + decoder (AE-256, Compact) | ~32 MB + ~768 KiB decoder | ~96× | ~0.95 |
| PQ (m=8, k=256) | ~8 MB | ~384× | ~0.95 |

Decoder matrix shared across stack (one per encoder registry, roadmap): ~384 KiB (`W_decoder` float32, 128-bit × 768-dim) + ~3 KiB `b_decoder` ≈ ~387 KiB per encoder registry.

## §7. Compatibility with `optimization-roadmap.md`

Binary embeddings slot into the planned `optimization-roadmap.md` binary-signatures bucket and the broader quantizer section.

### 7.1. Planned Bucket

Planned binary-signatures bucket (roadmap, **not yet implemented**):
`binary_bucket_index` keyed by `(scope_id, model_id, model_version,
projection_kind, descriptor_fingerprint, key_projection_id, short_key)`, with
posting lists of `BinaryBucketPosting { unit_id, full_signature, unit_revision,
resource_generation }`. The descriptor carries the encoder/config identity and
the first implementation has one key projection table. Planned encoder registry:
`BinarySignatureEncoderRegistry` with `.bse` file format.

### 7.2. Quantizer Section

`optimization-roadmap.md` §"Future Encodings" already defines the `VectorEncoding` enum (`Float32`, `Float16`, `Int8`, `BinarySignature`, `ProductQuantized`). Binary embeddings **extend** the `BinarySignature` entry with:

- `DenseIndexMode::BinaryOnly` — standalone binary (no float rerank).
- `DenseIndexMode::ApproximateVector` — binary + decoder → approximate float → rerank.

### 7.3. DenseIndexConfig Extensions

> **Proposed API sketch — not implemented.** The `BinaryOnlyConfig` and `ApproximateVectorConfig` additions below are illustrative — no ADR has defined them. Existing `DenseIndexConfig` lives in `memory-stacks-roadmap.md`; the binary embedding extensions below would slot alongside it.

> **Proposed API sketch — not implemented.**

```cpp
struct DenseIndexConfig {
    DenseIndexMode mode = DenseIndexMode::Exact;

    // Binary modes (BinaryCandidateFilter / BinaryOnly / ApproximateVector):
    uint32_t bit_count = 128;
    std::string encoder_id = "random_hyperplane_lsh_v1";

    // ApproximateVector mode only:
    bool store_decoder = true;
    bool store_float_fallback = false;

    // BinaryOnly mode only:
    bool drop_float_after_migration = false;

    // Hnsw mode only:
    std::optional<HnswConfig> hnsw_config;
};
```

`store_float_fallback = true` corresponds to `ApproximateVector (Safe)` mode (binary + float + decoder); `store_float_fallback = false` corresponds to `ApproximateVector (Compact)` (binary + decoder only).

## §8. Implementation Ladder

### M0: Binary Signatures Only (partially implemented)

> **Status.** Core packed signatures, width-aware Hamming dispatch, the optimized flat oracle, materialized/SIMD random-hyperplane encoder v2, coordinate-sign and randomized-Hadamard baselines, dependency-free global learned pair-difference, PCA-style projection, and ITQ-style rotation baselines, sparse/batch encoding, identity registry, and a bounded multi-probe prototype are implemented. `.bse` loading, persisted bucket layout, dense-index integration, and a production high-recall Hamming ANN remain planned.

M0 scope:

- `BinarySignature` value type with `std::vector<std::uint64_t> words` (implemented).
- `BinarySignatureInfo` with encoder id, model id, source projection kind (implemented).
- `IBinarySignatureEncoder` interface (implemented).
- `BinarySignatureEncoderRegistry` in-memory identity registry (implemented).
- `.bse` file format and registry loading (Planned API).
- `RandomHyperplaneBinaryEncoder` v2 with lazy materialization and SIMD dense scoring (implemented).
- `LearnedProjectionBinaryEncoder` v1 with deterministic document-only global pair-difference training (implemented experimental baseline; not ITQ or an autoencoder).
- `PcaProjectionBinaryEncoder` v1 with deterministic document-only PCA-style training and median thresholds (implemented experimental baseline; not ITQ or an autoencoder).
- `ItqRotationBinaryEncoder` v1 with document-only PCA followed by an unsupervised ITQ-style orthogonal rotation and zero-centered signs (implemented experimental baseline; not supervised hashing and not an autoencoder).
- `MultiProbeHammingIndex` bounded in-memory bucket prototype (implemented, experimental; no worst-case sub-linear guarantee).
- Persisted `RandomHyperplaneLSH` / production Hamming ANN wiring (Planned API).
- `binary_bucket_index` MDBX layout (Planned API).
- `DenseIndexMode::BinaryCandidateFilter` benchmark-gated candidate-filter mode (Planned API).

The in-memory multi-probe prototype is intentionally not the persistence
contract. A durable candidate-filter implementation first adopts the
application-owned `BinaryBucketIndexDescriptor` and
`BinaryBucketSearchBudget` from
[`optimization-roadmap.md`](optimization-roadmap.md) §"Binary Bucket Index
Tasks": descriptor compatibility, explicit probe/posting/decode/rerank limits,
active-generation publication and canonical hydration are required before a
bucket hit can contribute to retrieval.

This guide treats M0 as the starting point of the roadmap.

### M1: Binary Embeddings Standalone

> **Planned API — not yet implemented.** M1 features below are roadmap-level intentions, not in any ADR. The `BinaryOnlyIndex`, `ApproximateVectorIndex`, `AutoencoderBinarySignatureEncoder` are referenced in `optimization-roadmap.md` §"Dense Index Modes (Backend Selection)" but not yet implemented.

> **Planned API — not yet implemented.**

- `DenseIndexMode::BinaryOnly` (`BinaryOnlyIndex`) — Hamming bucket → Hamming rank, no float rerank. Float embeddings can be dropped after confirmation; storage savings ~190× on 1M units.
- `DenseIndexMode::ApproximateVector` (`ApproximateVectorIndex`) — Hamming bucket → decoder → approximate float → cosine rerank. Two variants: Safe (binary + float + decoder) vs Compact (binary + decoder only).
- `IAutoencoderEncoder` interface (dependency-free contract).
- `AutoencoderBinarySignatureEncoder` implementation (matmul + sign). Eigen adapter behind `AGENT_MEMORY_ENABLE_EIGEN` (default OFF).
- `IAutoencoderDecoder` interface (optional, for `ApproximateVector` mode).
- `.bse` file format extension: optional `weights_WT_decoder` matrix.
- Migration flow (write-new → read-both → bg-reindex → delete-old), as in `optimization-roadmap.md` §"Multi-encoder migration flow".

### M2: Hybrid Binary + Dense Indexes

- `DenseIndexMode::Hnsw` (`HnswVectorIndex`) — mainline M2+ backend, optionally combined with `BinaryCandidateFilter` for hybrid.
- Hybrid retrieval: binary filter produces candidate set (Recall@10 ≈ 0.95 — hypothesis, see §6), HNSW ranks within candidate set, dense rerank on top.
- Adaptive per-query mode selection via `RetrievalPlan::dense_index_mode_override`.
- Multi-encoder hybrid: binary embeddings + PQ codes for finer rerank (asymmetric ADC distance).
- Multi-index hashing is M2+ experimental: begin with a single short-key table,
  then add independently versioned key projections only when a locked
  benchmark improves the recall/latency/storage frontier under the same global
  `BinaryBucketSearchBudget`. It must report duplicate-posting bytes,
  deduplication work, update amplification and candidate recall separately
  from final reranked quality.
- Columnar bucket segments are M2+ experimental: delta/packed posting columns,
  raw fixed-width signatures, and either fixed-width quantized vectors, PQ, or
  a benchmarked frame-of-reference residual codec. See
  [`optimization-roadmap.md`](optimization-roadmap.md) §"Columnar Segment Codec
  Candidates"; generic Zstd is not assumed to improve already packed hot
  vector columns.

## §9. Open Questions

1. **Calibration of binary quality targets.** The Tissier 2018 results are on word-level GloVe; transfer to sentence-transformer embeddings (BERT, E5, BGE, M3-Embedding) is not validated. Same open question as `memory-routing-roadmap.md` §11: does the autoencoder objective generalise from 300-dim GloVe to 768-1024 dim modern embeddings?
2. **Comparison with ANN methods (HNSW, IVF, ScaNN).** On corpora >10M vectors, does binary embedding remain competitive, or does HNSW dominate? Benchmark-driven choice is required.
3. **Multi-bit quantization (2-4 bits/dim).** Active research direction since 2020. Promises intermediate trade-off between binary (1 bit) and float8 (8 bits). Integration with the planned binary-signatures encoder registry: new `BinarySignatureEncoderId` value, new `.bse` encoder format, new bit-count handling in `BinarySignature`.
4. **Multilingual and code-model embeddings.** Structure of semantic space differs from GloVe/fasttext. Does Tissier 2018's `MSE + decorrelation penalty` objective still work for multilingual and code embeddings?
5. **LLM-based hashing.** Frozen LLM + hash head vs joint fine-tuning. Trade-off: training cost vs code quality. No public benchmark comparing both approaches on retrieval metrics (mAP, NDCG).
6. **Adversarial robustness.** Small text edit (paraphrase, synonym swap) can radically change binary code. How robust is binary coarse-filter for production RAG with paraphrased queries?
7. **PQ on LLM hidden states.** Joulin 2016 PQ for text classification is on Word2Vec; does it transfer to 4096-dim LLM hidden states with residual compensation?
8. **Adaptive dimension selection per query.** Can we dynamically select embedding dimension by query complexity, so simple queries don't pay for full D dimensions?
9. **MRL + binary composition.** 2048-dim embedding → MRL-truncate to 256 → binarize to 256 bits → 32× additional compression (1024 B → 32 B), 256× cumulative (8192 B → 32 B). Does this nested compression retain usable semantic quality?
10. **Decoder training cost.** Autoencoder training requires labelled corpus (10k-1M embeddings). What is the minimum viable training set size for our specific AgentLTM workload? Workload-specific, requires empirical measurement.

## §10. Composite compression (MRL + PQ / INT8 / binary)

See [`compression-is-intelligence-roadmap.md`](compression-is-intelligence-roadmap.md) for the conceptual framing (cross-entropy, prefix-free codes, "what good compression preserves") and the §10 Composite compression table for MRL+INT8 / MRL+PQ / MRL+binary multipliers.

Одна техника сжатия (MRL truncate, INT8, PQ, binary) обычно **не** достигает жёстких storage / latency targets в одиночку. Memory savings are configuration-dependent; the examples in this section range from 12× to 384× depending on embedding dimension, code width, and composition. Этот раздел систематизирует совместимость и реалистичные multipliers.

[Source: arXiv:2205.13147 — Kusupati et al. 2022, Matryoshka Representation Learning]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Адаптивные embeddings - variable-length representations и Matryoshka.md]

### §10.1. Why composite compression

- **MRL truncate** уменьшает размерность, но каждое измерение остаётся float32 → по умолчанию 3× compression для 768→256.
- **INT8 scalar quantization** уменьшает bytes-per-dim (4 → 1) без зависимости от размерности → 4× compression.
- **PQ (m=8, K=256)** заменяет sub-vector на 1 байт индекс кластера → ~96× на 768-dim, ~128× на 256-dim (per-segment basis).
- **Binary binarisation** (Tissier 2018 autoencoder или RH-LSH) — 1 bit per dim или фиксированный bit_count → 32× для 768-dim, 8× для 256-dim.

Если storage budget жёстче, чем даёт одна техника, композиция stack'ов даёт multiplicative effect.

### §10.2. Composite multiplier table

Baseline — 768-dim float32 embedding (3,072 bytes per vector).

| Stage | Output size per vector | Stage multiplier | Cumulative |
|---|---|---|---|
| float32 baseline (768-dim) | 3,072 B | 1× | 1× |
| MRL truncate 768→256 | 1,024 B | 3× | 3× |
| MRL 256 + INT8 | 256 B | 4× vs float32, 12× cumulative | 12× |
| MRL 256 + PQ m=8 | 8 B | 128× vs 256-float, 384× vs 768-float baseline | 384× |
| MRL 256 + binary (256-bit) | 32 B | 32× vs float32 | 96× |

Примеры комбинаций (rough — для оценки storage budget):

- **MRL 256 + INT8** ≈ 12× compression (3× truncation × 4× INT8). Quality loss minimal; default для moderate-budget workloads.
- **MRL 256 + PQ m=8** ≈ 384× compression (3× truncation × 128× PQ per 256-dim segment basis, 1024 B → 8 B). Quality loss noticeable, требует calibration set.
- **MRL 256 + binary** ≈ 96× compression (3× truncation × 32× binarisation). Самый агрессивный; biggest quality risk; используется как candidate filter, не standalone retrieval.

> **All multipliers above are illustrative starting hypotheses, not validated cross-codec quality targets.** Actual ratios depend on:
> - embedding model (BERT vs E5 vs BGE behave differently under quantisation);
> - corpus (technical text, conversational, structured vs unstructured);
> - retrieval metric (Recall@10 vs nDCG@10 vs MRR);
> - quantisation training data (or lack thereof).
>
> Эти числа полезны для sizing storage budget, не как acceptance contracts.

### §10.3. Compatibility matrix

Что stack'уется с чем, и где нужна осторожность:

| Combination | Status | Notes |
|---|---|---|
| MRL + INT8 | clearly composable | MRL truncation is bit-independent of INT8; INT8 quantises each retained dimension. |
| MRL + PQ | clearly composable | Standard pipeline: MRL truncate to 256, then PQ m=8 on 256-dim sub-vectors; K-means clusters per segment. Requires K-means calibration set on MRL-truncated embeddings. |
| MRL + separately-trained binary encoder | clearly composable | Tissier 2018-style: MRL truncate, then learn a binary encoder on the MRL-truncated embedding. См. §10.4 — open question о transfer to sentence-transformers. |
| PQ + quantized centroids / residuals | requires defined codec | PQ code + INT8/quantised-centroid residuals is a defined codec schema (FAISS PQ+rz), not a simple per-stage multiplier; specify residual scheme explicitly. |
| INT8 → binary | requires defined codec | binary is typically derived from float / from a learned encoder, not from INT8→binarize pipeline. INT8-then-binarize loses the per-dim scaling that INT8 preserved. |
| MRL + SPLADE | NOT a codec — orthogonal retrieval channels | MRL dense + SPLADE sparse are orthogonal retrieval channels. They do not compose into one codec, but can coexist in hybrid fusion (different heads, different representations). |

[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Адаптивные embeddings - variable-length representations и Matryoshka.md]

### §10.4. Open question: MRL + binary composition

Из §9 question 9: «2048-dim embedding → MRL-truncate to 256 → binarize to 256 bits → 32× additional compression (1024 B → 32 B), 256× cumulative (8192 B → 32 B). Does this nested compression retain usable semantic quality?»

Промоутируем в dedicated section, потому что эта комбинация наиболее перспективна для million-vector corpora с жёсткими memory budget'ами:

- Tissier 2018 binarisation тестировалась на word-embedding scale (GloVe 300-dim); sentence-transformer transfer невалидирован.
- MRL truncate 2048→256 — coarse-to-fine semantic; первые 256 dims обычно несут основную семантику (per Kusupati et al. 2022 ImageNet results, 14× speedup с 14× reduction при comparable accuracy).
- **Composite arithmetic (MRL 256 + 256-bit binary), per code:**
  - 768-dim float32 + MRL 256 + 256-bit code: 3072 B → 1024 B → 32 B → ~96× compression (1M vectors: ~3 GB → ~32 MB).
  - 2048-dim float32 + MRL 256 + 256-bit code: 8192 B → 1024 B → 32 B → ~256× compression (1M vectors: ~8 GB → ~32 MB).
  Оба варианта влезают в single-process memory budget на million-vector corpora.
- **Открытый вопрос:** сохраняет ли nested MRL+binary качество на современных sentence-transformer embeddings (BGE, E5, M3-Embedding), или теряет tail of semantic distribution?

Рекомендация: **treat as experimental until benchmarked on target corpus.** Использовать как `DenseIndexMode::BinaryCandidateFilter` (planned) — coarse filter перед float rerank, не standalone storage.

[Source: arXiv:2205.13147 — Kusupati et al. 2022]
<br>[Source: arXiv:1803.09065 — Tissier, Gravier, Habrard 2018]
<br>[Source: internal note — no public source available. Path: ai-agent-playbook/concepts/llm-research/Адаптивные embeddings - variable-length representations и Matryoshka.md]

## §11. References

### 11.1. Public sources

- **Tissier, Julien; Gravier, Christophe; Habrard, Amaury (2018). "Near-lossless Binarization of Word Embeddings."** AAAI 2019 (arXiv:1803.09065). URL: <https://arxiv.org/abs/1803.09065>. Code: <https://github.com/tca19/near-lossless-binarization>.
- **Chen, Jianlv; Xiao, Shitao; Zhang, Peitian; Luo, Kun; Lian, Defu; Liu, Zheng (2024). "M3-Embedding: Multi-Linguality, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation."** arXiv:2402.03216, BAAI (BGE-M3 in open-source release). URL: <https://arxiv.org/abs/2402.03216>.
- **Joulin, Armand; Grave, Edouard; Bojanowski, Piotr; Mikolov, Tomas (2016). "FastText.zip: Compressing text classification models."** arXiv:1612.03651. URL: <https://arxiv.org/abs/1612.03651>.
- **Jégou, Hervé; Douze, Matthijs; Schmid, Cordelia (2011); subsequent FAISS line including Johnson, Jeff; Douze, Matthijs; Jégou, Hervé (2017). "Billion-scale similarity search with GPUs."** arXiv:1702.08734 (FAISS). URL: <https://arxiv.org/abs/1702.08734>.
- **Kusupati, Aditya; et al. (2022). "Matryoshka Representation Learning."** arXiv:2205.13147. URL: <https://arxiv.org/abs/2205.13147>.
- **He, et al. (2025). "A Survey on Deep Text Hashing."** arXiv:2510.27232. URL: <https://arxiv.org/abs/2510.27232>.

### 11.2. Internal notes (ai-agent-playbook)

- `ai-agent-playbook/concepts/llm-research/Бинаризация эмбеддингов для экономии памяти и ускорения retrieval.md` (internal note) — comprehensive concept summary: Tissier 2018 autoencoder approach, deep text hashing survey, PQ, MRL, regularising effect of binarisation.
- `ai-agent-playbook/resources/llm-research/M3-Embedding BGE-M3 - Multi-Linguality Multi-Functionality Multi-Granularity - arXiv 2402.03216 - разбор статьи.md` (internal note) — BGE-M3 paper analysis: 3M properties (multi-linguality, multi-functionality, multi-granularity), self-knowledge distillation.

### 11.3. In-house (agent-memory-cpp/guides/)

- [`optimization-roadmap.md`](optimization-roadmap.md) — primary technical anchor:
  - §"Eigen и SIMD стратегия" — SIMD abstraction layer, `popcount64`, `HammingTopK` kernel design.
  - §"Binary Signature Index Tasks" — `BinarySignature` value type, `IBinarySignatureEncoder`, baseline encoder, learned autoencoder encoder (M2 experimental), short-key generation, neighbour bucket masks.
  - §"Encoder Registry и Versioning" — `BinarySignatureEncoderRegistry`, `.bse` file format, `BinarySignatureEncoderId` taxonomy.
  - §"Binary Bucket Index Tasks" — `binary_bucket_index` MDBX layout, mutable bucket updates, query pipeline, encoder registry versioning.
  - §"Dense Index Modes (Backend Selection)" — 4-mode `IDenseIndex` interface (Exact / BinaryCandidateFilter / BinaryOnly / ApproximateVector), `DenseIndexConfig` shape, storage estimates, quality targets per mode, per-stack defaults.
  - §"Future Encodings" — `VectorEncoding` enum, `BinarySignatureEncoderId` taxonomy.
  - §"Per-Bit-Size Recall Targets" and §"Per-Mode Recall Targets" — quality targets for binary encoders across modes.
  - §"Recommended Implementation Order" — Steps 7-9 (binary signature, bucket), 22 (encoder registry), 23-25 (multi-mode IDenseIndex), 26-28 (SIMD abstraction and HammingTopK kernel).
- [`code-intelligence-roadmap.md`](code-intelligence-roadmap.md) — MinHash + LSH (Pattern 1) augments binary bucket index; RotSQ codec (Pattern 2) slots alongside Matryoshka, PQ codecs.
- [`retrieval-techniques-roadmap.md`](retrieval-techniques-roadmap.md) — typology of retrieval techniques; binary embeddings filter candidates, retrieval still ranks.
- [`memory-routing-roadmap.md`](memory-routing-roadmap.md) — pre-retrieval routing sits "до" binary embedding filter; both layers are cheap pre-filters in series.
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) — `HybridRetrievalEngine` (existing), `IQueryTransformer` (planned), `IRetrievalEvaluator` (planned) — binary embeddings integrate as candidate filter inside retrieval.
- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) — `MemoryProfileSpec`, `DenseIndexConfig`, `DenseIndexMode`, capability flags, embedding migration workflow (`EmbeddingRecomputeJob`).
- [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) — canonical physical MDBX manifest and DBI budget.
