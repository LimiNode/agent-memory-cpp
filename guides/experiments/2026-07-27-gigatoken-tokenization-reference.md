# Gigatoken Tokenization Pipeline Reference Review

## 2026-07-27

### Context

Reviewed `marcelroed/gigatoken` as a pattern donor for raw-document ingestion,
token-budgeted chunking and ingestion benchmarks in `agent-memory-cpp`. The
question was whether to borrow anything for roadmap/TZ without adding a
Rust/Python tokenizer dependency to the C++17 core.

### Source

- Repository: `https://github.com/marcelroed/gigatoken`
- Inspected revision: `34a1599f0c0ae7d7cd0d1c530e6522320158b360`
- Public inspection date: 2026-07-27
- Inspected surfaces:
  - `README.md`
  - `design_doc.md`
  - `Cargo.toml`
  - `src/batch.rs`
  - `src/input/file_source.rs`
  - `src/input/jsonl.rs`
  - `src/bpe/pretoken_cache.rs`
  - `benchmarks/compare/measure.py`

### Question

Can Gigatoken improve our roadmap without becoming a required tokenizer or
runtime dependency?

### Observed Upstream Behavior

- Gigatoken is a Rust/PyO3 tokenizer package with a Python API and compatibility
  modes for HuggingFace Tokenizers and Tiktoken.
- The README presents throughput benchmarks on `owt_train.txt` and reports GB/s
  tokenization on modern CPUs for many BPE tokenizers.
- The fastest API lets Rust read file sources directly instead of passing large
  Python objects through the boundary.
- Source handling distinguishes plain text, JSONL, Parquet, compression
  framing, in-memory bytes and file paths.
- The batch encoder groups documents into coarse parallel chunks and splits
  oversized documents at tokenizer-safe boundaries before reassembling outputs.
- Worker/tokenizer state keeps hot caches across calls.
- Benchmark scripts isolate libraries in fresh processes and document split
  policy differences between Gigatoken, HuggingFace Tokenizers and Tiktoken.

### Local Design Inference

Do not adopt Gigatoken as a core dependency. The project is useful as a
high-throughput tokenizer reference and pipeline pattern donor, but
`agent-memory-cpp` should keep dependency-free C++ contracts in core and place
concrete tokenizers behind optional adapters/tools.

Borrow these patterns:

- explicit raw input source spec: bytes, files, plain text, JSONL, Parquet and
  compressed variants;
- compression as orthogonal framing rather than semantic document format;
- document boundary policy before semantic chunking;
- tokenizer identity as part of the reproducible pipeline config;
- tokenizer-safe oversized-document splitting;
- persistent worker/cache lifetime for ingestion workers;
- benchmark reporting split by read/decompress, parse, tokenize, chunk,
  projection/index build and memory high-water mark;
- validation that chunked tokenization preserves whole-document tokenization
  semantics for tokenizer-safe split modes.

### Rejected As Direct Dependency

- Rust/PyO3 runtime in the C++17 static library.
- Treating Gigatoken's README throughput as an end-to-end ingestion target for
  `agent-memory-cpp`.
- Copying tokenizer internals or SIMD pretokenizers into the roadmap before a
  concrete tokenizer adapter contract exists.

### Roadmap Impact

Updated:

- `guides/chunkers-roadmap.md`: added §10 tokenizer-aware raw input pipeline,
  raw source/compression/boundary/token budget sketches, chunk metadata additions
  and ingestion benchmark gates.
- `guides/resource-reindexing.md`: expanded `pipeline_config_hash` to include
  source format, compression, boundary policy, tokenizer id, token budget,
  overlap and safe-boundary policy.
- `guides/related-projects.md`: classified Gigatoken as a sister/pattern donor,
  not a direct dependency.

### Follow-Up Checks

- Decide whether tokenizer identity belongs in `ChunkPayload` proper or in a
  typed metadata extension once the first ingestion API PR starts.
- Add a deterministic fake tokenizer for chunking tests before wiring real model
  tokenizers.
- Add fixtures for whole-resource vs separator vs JSONL boundary modes.
- Add a benchmark harness that reports ingestion phases separately, not only
  end-to-end resource indexing time.
- Keep Windows support expectations conservative for any optional Gigatoken
  sidecar, because upstream notes limited Windows testing.
