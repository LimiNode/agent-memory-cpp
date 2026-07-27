# Build And Test

## Requirements

- CMake 3.20 or newer.
- C++17 compiler.
- Windows, Linux, or macOS.

## CMake Targets

- Main target: `agent_memory`.
- Public alias: `agent_memory::agent_memory`.

## CMake Options

All project options use the `AGENT_MEMORY_` prefix.

| Option | Default | Description |
| --- | --- | --- |
| `AGENT_MEMORY_BUILD_TESTS` | top-level build only | Build tests and register them with CTest. |
| `AGENT_MEMORY_BUILD_EXAMPLES` | `OFF` | Build examples from `examples/`. |
| `AGENT_MEMORY_BUILD_BENCHMARKS` | `OFF` | Build benchmark tools from `tools/`. Requires `AGENT_MEMORY_ENABLE_JSON=ON`. |
| `AGENT_MEMORY_ENABLE_JSON` | `ON` | Enable JSON-backed eval loaders and benchmark report serialization. |
| `AGENT_MEMORY_ENABLE_WARNINGS` | `ON` | Enable project compiler warnings. |
| `AGENT_MEMORY_ENABLE_MDBX` | `OFF` | Enable MDBX-backed storage dependencies. |
| `AGENT_MEMORY_MDBX_CONTAINERS_SOURCE_DIR` | empty | Optional override for a local `mdbx-containers` source tree. Defaults to `external/mdbx-containers` when present. |
| `AGENT_MEMORY_MDBX_DEPS_MODE` | `AUTO` | MDBX dependency mode forwarded to `mdbx-containers` source builds: `AUTO`, `SYSTEM`, or `BUNDLED`. |

## Baseline Commands

Use `tmp/` or `build-*` for local verification outputs. These paths are
generated and should stay untracked.

```bash
cmake -S . -B tmp/build-cpp17 \
    -DCMAKE_BUILD_TYPE=Release \
    -DAGENT_MEMORY_BUILD_TESTS=ON \
    -DAGENT_MEMORY_BUILD_EXAMPLES=ON

cmake --build tmp/build-cpp17 --parallel
ctest --test-dir tmp/build-cpp17 --output-on-failure
```

Build and smoke-test the synthetic benchmark CLI:

```bash
cmake -S . -B tmp/build-bench \
    -DCMAKE_BUILD_TYPE=Release \
    -DAGENT_MEMORY_BUILD_TESTS=ON \
    -DAGENT_MEMORY_BUILD_BENCHMARKS=ON \
    -DAGENT_MEMORY_ENABLE_JSON=ON

cmake --build tmp/build-bench --parallel
ctest --test-dir tmp/build-bench --output-on-failure
./tmp/build-bench/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/config.example.json \
    tmp/benchmark-report.json
```

When tests and benchmarks are enabled together, CTest also registers
`agent_memory_benchmark_cli_synthetic_sweep`, a smoke test that runs
`agent-memory-bench` against the committed synthetic sweep fixture. It also
registers benchmark CLI smoke tests for the binary flat-vs-float mode and the
binary rerank grid mode.

Benchmark numbers are reviewable only when they satisfy the contract in
[`milestones.md`](milestones.md): dataset manifest/hash, hardware snapshot,
compiler/build flags, cold/warm procedure, query mix, index state, top-k,
concurrency, retrieval mode, embedding-generation inclusion policy, filter mix,
median/p95/p99, peak RSS/map size, and write-amplification counters where
writes are measured. Smoke fixtures validate report shape; they are not
production benchmark claims.

Validate the roadmap DBI manifest whenever `guides/dbi-manifest.yaml` or
`guides/mdbx-containers-extension-tz.md` changes:

```bash
py -3 tools/validate-dbi-manifest.py guides/dbi-manifest.yaml
py -3 tools/validate-dbi-manifest.py guides/dbi-manifest.yaml --self-test
```

When `AGENT_MEMORY_BUILD_TESTS=ON`, CTest also exposes
`agent_memory_dbi_manifest_validate` and
`agent_memory_dbi_manifest_validator_self_test`.

Run the BoW-vs-BM25 synthetic sweep fixture:

```bash
./tmp/build-bench/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/synthetic-sweep.example.json \
    tmp/synthetic-sweep-report.json
```

Run the binary rerank statistical grid fixture:

```bash
./tmp/build-bench/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/synthetic-binary-rerank-grid.example.json \
    tmp/synthetic-binary-rerank-grid-report.json
```

Run the PCA-focused 128D learned-projection fixture:

```bash
./tmp/build-bench/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/synthetic-binary-rerank-grid-pca.example.json \
    tmp/synthetic-binary-rerank-grid-pca-report.json
```

Run the ITQ-focused 128D learned-projection fixture:

```bash
./tmp/build-bench/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/synthetic-binary-rerank-grid-itq.example.json \
    tmp/synthetic-binary-rerank-grid-itq-report.json
```

Run the precomputed-embedding qrels smoke fixture:

```bash
./tmp/build-bench/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-smoke.example.json \
    tmp/precomputed-embedding-binary-rerank-grid-smoke-report.json
```

Run the high-dimensional synthetic variants when investigating projection
scaling beyond the default 128D fixture:

```bash
./tmp/build-bench/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/synthetic-binary-rerank-grid-highdim-384.example.json \
    tmp/synthetic-binary-rerank-grid-highdim-384-report.json

./tmp/build-bench/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/synthetic-binary-rerank-grid-highdim-768.example.json \
    tmp/synthetic-binary-rerank-grid-highdim-768-report.json

./tmp/build-bench/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/synthetic-binary-rerank-grid-highdim-1536.example.json \
    tmp/synthetic-binary-rerank-grid-highdim-1536-report.json
```

The grid separates `data_seeds` from `encoder_seeds`. `repeat_count` controls
binary timing measurements, while `exact_timing_repeat_count` independently
controls repeated timing of two common exact baselines. Quality is sampled once
per data/encoder seed pair; timing repeats are not treated as independent
quality observations.

The optional `encoder_families` list selects which binary encoders participate
in the grid. Supported values are:

- `random_hyperplane_rademacher`;
- `coordinate_sign`;
- `randomized_hadamard_projection`;
- `learned_pair_difference_projection`;
- `pca_projection`;
- `itq_rotation_projection`.

If omitted, the grid keeps the historical random-hyperplane-only behavior.
`coordinate_sign` is unseeded and emits exactly `embedding_dimensions` bits, so
the runner skips other `bit_counts` for that family. The random-hyperplane and
randomized-Hadamard families use every configured `encoder_seed`.
`learned_pair_difference_projection` trains a deterministic global artifact
from the current data seed's document vectors only; evaluation queries are not
training input. Encoder artifacts are cached per family/seed/bit inside one
data-seed run, so `repeat_count` repeats binary materialization/search timing
without retraining the same artifact. With `randomize_execution_order=true`,
the runner shuffles the full `encoder_family x encoder_seed x bit_count x
repeat` task list for each data seed; JSON reports remain grouped by family and
encoder seed for readability.

`pca_projection` also trains only on document vectors and supports
`bit_count <= embedding_dimensions`; the runner skips larger bit widths for
that family. `itq_rotation_projection` has the same bit-width constraint, starts
from the PCA artifact, then learns an unsupervised ITQ-style orthogonal rotation
before applying zero-centered signs.

Binary grid JSON reports include an `encoder.training` section for every
per-repeat report and an `encoder_training` section in per-bit and aggregate
summaries. These fields record `encoder_training_ms`, `training_vector_count`,
`artifact_payload_bytes`, and whether training was included in query/build
timing. Learned projection, PCA, and ITQ report document-vector training;
zero-training families report `training_source = "none"` and zero
training/artifact payload.
Training remains excluded from query timing and binary materialization timing,
but the cold-start cost is visible in the report.

The `precomputed_embedding_binary_rerank_grid` mode uses the same encoder
families and candidate-rerank structure, but loads frozen document/query
embeddings and qrels from JSON instead of generating synthetic vectors. It is
intended for deterministic real-embedding regression gates: exact-vector oracle
and reranked binary results are both evaluated with the shared qrels metric
pipeline, while embedding generation remains outside the C++ test run. Dataset
fixtures may carry optional `embedding_artifact` provenance; benchmark reports
preserve it so frozen vectors can be traced back to the generator/model/config
that produced them. When `embedding_artifact` is present, the block must be
complete and must use the canonical hash contract:

- `hash_algorithm` is currently `sha256`;
- `config_hash`, `dataset_hash`, `qrels_hash`, and `artifact_hash` are
  lowercase 64-character SHA-256 hex strings without a `sha256:` prefix;
- `config_hash` covers the canonical generator configuration, including model
  id/revision, document/query prompt identities, normalization, dtype,
  pooling/truncation, projection transform, random seed, and other
  behavior-affecting settings;
- `artifact_hash` covers the canonical embedding payload before any
  self-referential provenance wrapper is added. For JSON fixtures this means the
  ordered document/query embedding records with ids and float values;
- `dataset_hash` covers the canonical corpus/query payload consumed by
  evaluation, and `qrels_hash` covers the canonical graded relevance judgments;
- dataset, qrels, and embedding payload hashes are ordered canonical payloads:
  fixture record order is part of the frozen artifact identity unless a future
  schema explicitly defines sorted semantics;
- provenance separates `dataset_revision`, `generator_revision`,
  `model_revision`, and `qrels_revision`; do not collapse those identities into
  one ambiguous source revision.

Precomputed embedding fixtures with `embedding_artifact` should also pass the
artifact verifier:

```bash
cmake --build build --target agent-memory-precomputed-artifact-verify
build/tools/agent-memory-bench/agent-memory-precomputed-artifact-verify \
    tests/eval/fixtures/precomputed-embedding-medium.json
```

The verifier recomputes `config_hash` from the canonical generator-config
fields, `dataset_hash` from corpus/query records, `qrels_hash` from judgments,
and `artifact_hash` from the canonical binary document/query embedding payload.
The embedding payload encoding is:

- ASCII magic `agent-memory-precomputed-embedding-payload-v1`;
- document embedding records in fixture order, then query embedding records in
  fixture order;
- one record type byte: `1` for documents and `2` for queries;
- little-endian `uint32` id byte length, followed by the UTF-8 id bytes;
- little-endian `uint32` vector dimension;
- each vector value rounded to IEEE-754 `float32`, encoded little-endian, with
  negative zero canonicalized to positive zero.

The verifier currently requires `dtype = "float32"` and `hash_algorithm =
"sha256"`. It is intentionally separate from the dataset loader: ordinary
benchmark runs validate schema and coverage, while CI/verifier runs prove that
committed fixtures are cryptographically tied to their declared contents.

`precomputed-embedding-external-hash.json` is the first dependency-free
external-generator fixture. Its vectors are generated by
`tools/agent-memory-bench/generate-precomputed-external-hash-fixture.py`, then
frozen and consumed by the same C++ benchmark path as future third-party model
fixtures. It is not a MiniLM/OpenAI quality benchmark; it is a reproducibility
and artifact-provenance gate for externally generated embeddings.

To regenerate it:

```bash
python tools/agent-memory-bench/generate-precomputed-external-hash-fixture.py \
    --output tests/eval/fixtures/precomputed-embedding-external-hash.json
```

When Python 3 is available, CI also runs
`agent_memory_precomputed_embedding_external_hash_regeneration`, which
regenerates this fixture into the build tree and compares it byte-for-byte with
the committed JSON. The fixture is pinned to LF line endings so this
reproducibility check is stable across Windows, macOS, and Linux checkouts.

`precomputed-embedding-minilm-l6-v2.json` is the first real third-party
embedding-model fixture. It was generated with
`sentence-transformers/all-MiniLM-L6-v2` at model/tokenizer revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, mean pooling, explicit float32
narrowing, and L2 normalization. CI verifies the committed artifact and runs a
small binary-rerank benchmark against it, but intentionally does not regenerate
the vectors: regeneration downloads/runs a transformer model and requires the
pinned Python environment recorded by
`tools/agent-memory-bench/requirements-minilm-fixture.txt`.

To regenerate it locally:

```bash
python tools/agent-memory-bench/generate-precomputed-minilm-fixture.py \
    --output tests/eval/fixtures/precomputed-embedding-minilm-l6-v2.json
```

If dependencies are installed in a non-default location, set `PYTHONPATH` before
running the command. The artifact records a `generator_source_hash` for the
thin MiniLM generator plus shared MiniLM runtime helper, and a
`generator_contract_source_hash` for the shared canonical fixture-contract
script; the MiniLM benchmark CTest compares both hashes against the checked-in
scripts without rerunning the model.

Changing either generator script or
`tools/agent-memory-bench/minilm_fixture_generator_common.py` is an intentional
fixture-update operation: rerun the generator, refresh the generator source
hashes, and commit the updated artifact in the same PR. The shared dataset/qrels
and canonical byte encodings live in
`tools/agent-memory-bench/precomputed_fixture_contract.py`, so
`generator_contract_source_hash` is tied to that contract module instead of the
surrogate external-hash generator. CI also runs a cheap Python content-contract
check that compares each MiniLM fixture's `corpus`, `queries`, and `judgments`
against its source contract module without loading the model, plus negative
mutations for corpus, query, and judgment drift. The artifact's
`generator_requirements_lock` field records the path and SHA-256 of the
requirements file; the generator parses that same file and checks the declared
Python and package versions before producing vectors. Changing the requirements
file is also a fixture-update operation. Treat the official regeneration
environment as CPU execution with the pinned versions recorded in that
requirements file.

`precomputed-embedding-minilm-l6-v2-large.json` is a larger frozen MiniLM
fixture with 36 documents, 12 queries, and 36 graded qrels. It uses a dedicated
content contract in
`tools/agent-memory-bench/precomputed_fixture_large_contract.py` and the same
canonical hash helpers and requirements manifest as the small MiniLM fixture.
CI verifies the artifact and runs a compact binary-rerank gate with
zero-training encoders only; learned PCA/ITQ-style grids should stay in
separate offline or follow-up benchmark runs until their runtime is acceptable
for the main CI suite.

The two exact baselines answer different questions:

- `current_exact_index` measures the existing `ExactVectorIndex`, including its
  public index data structures and result construction;
- `contiguous_exact` stores normalized source vectors row-major, dispatches the
  SIMD dot-product kernel once per batch, reuses scoring workspaces, and returns
  lightweight positions. It is the compute-oriented denominator for deciding
  whether binary filtering beats a well-laid-out exact scan.

Both baselines must return exactly the same top-k for every query. Their timing
samples and median speedup denominators are reported separately; do not describe
a speedup against `current_exact_index` as a speedup against contiguous exact
vector arithmetic.

Binary grid reports also record `exact_vector_similarity_backend`. GNU/Clang
x86 builds select AVX2 at runtime, fall back to SSE2 when available, and
otherwise use the scalar C++17 implementation. The current MSVC x64 build uses
SSE2 for vector arithmetic; an isolated `/arch:AVX2` translation unit and
dispatch path remain future work.

They also record `binary_hamming_backend` and
`binary_encoder_similarity_backend`. Short packed signatures prefer hardware
POPCNT, sufficiently wide signatures may select the AVX2 nibble-LUT kernel on
GNU/Clang x86, and all platforms retain a lookup-table fallback. The current
MSVC x86/x64 implementation selects POPCNT when available and does not compile
the AVX2 Hamming kernel. The random-hyperplane `v2`
encoder materializes its deterministic projection lazily and uses the same
AVX2/SSE2/scalar vector backend for dense inputs. Every dense backend and the
sparse path use the same eight-lane reduction contract, so persisted signatures
do not change with runtime CPU dispatch.

Tests and diagnostic tools can query backend availability and request a
specific backend through `HammingDistanceComputer` and
`VectorSimilarityComputer`. Forced construction rejects unavailable backends;
it never executes an unsupported instruction speculatively.

Run the decomposed Hamming hot-path benchmark:

```bash
./tmp/build-bench/tools/agent-memory-bench/agent-memory-hamming-hot-path-bench \
    tools/agent-memory-bench/hamming-hot-path.example.json \
    tmp/hamming-hot-path-report.json
```

The report separates raw contiguous distance calculation, `partial_sort`,
`nth_element`, distance-bucket selection, the complete flat-index API, and the
experimental bounded multi-probe index. Use the 100k fixtures only for manual
directional runs; the tiny smoke fixture is the one registered in CTest.

Run the deterministic staircase sweep helper:

```bash
py -3 tools/benchmarks/synthetic_staircase.py \
    --bench-exe tmp/build-bench/tools/agent-memory-bench/agent-memory-bench \
    --output-dir tmp/synthetic-staircase-v1 \
    --summary tmp/synthetic-staircase-v1/synthetic-staircase-v1.md
```

The staircase helper keeps generated datasets, configs, and raw JSON reports in
`tmp/`. Commit only the generated Markdown summary when it changes a design
decision.

Regenerate the committed tiny synthetic fixture only when intentionally
changing the generator contract:

```bash
py -3 tools/benchmarks/synthetic_generator.py \
    --documents 50 \
    --queries 40 \
    --seed 12648430 \
    --limit 50 \
    --output tests/eval/fixtures/tiny_synthetic_v1.json
```

Configure with flat MDBX submodules:

```bash
git submodule update --init external/libmdbx external/mdbx-containers

cmake -S . -B tmp/build-mdbx \
    -DCMAKE_BUILD_TYPE=Release \
    -DAGENT_MEMORY_ENABLE_MDBX=ON \
    -DAGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED \
    -DAGENT_MEMORY_BUILD_TESTS=ON

cmake --build tmp/build-mdbx --parallel
ctest --test-dir tmp/build-mdbx --output-on-failure
```

Use `AGENT_MEMORY_MDBX_CONTAINERS_SOURCE_DIR=/path/to/mdbx-containers` only
when testing a custom checkout outside `external/`.

On multi-config generators, pass the configuration to build and test commands:

```bash
cmake --build tmp/build-cpp17 --config Release --parallel
ctest --test-dir tmp/build-cpp17 --build-config Release --output-on-failure
```

## CI Expectations

GitHub Actions builds the default configuration on Windows, Linux, and macOS.
CI should configure, build, and run CTest with examples and tests enabled.

CI also includes a Linux MDBX wiring job. That job initializes only the flat
`external/libmdbx` and `external/mdbx-containers` submodules, configures with
`AGENT_MEMORY_ENABLE_MDBX=ON`, and runs CTest. Do not use recursive submodule
checkout for this job; nested dependency checkouts would hide flat-layout
regressions.

MDBX-enabled test builds register `agent_memory_mdbx_document_storage_test`,
which opens a temporary MDBX database and validates the document storage
adapter.

## Verification Heuristics

- Documentation-only changes: inspect Markdown and run `git diff --check`.
- CMake changes: configure and build at least one clean build directory.
- MDBX dependency wiring changes: configure once with `AGENT_MEMORY_ENABLE_MDBX=OFF`
  and once with `AGENT_MEMORY_ENABLE_MDBX=ON` using flat `external/libmdbx` and
  `external/mdbx-containers` submodules or an installed package.
- Public header changes: build tests and examples.
- Behavior changes: add or update tests, then run the relevant CTest subset or
  the full suite when the affected area is shared.
