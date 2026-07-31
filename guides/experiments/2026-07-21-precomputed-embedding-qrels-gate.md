# Precomputed embedding qrels gate

## 2026-07-21 — PR #66 smoke gate

### What we checked

PR #66 adds the first deterministic path for evaluating binary candidate
retrieval on frozen dense embeddings with locked qrels:

- load a JSON fixture containing corpus/query records, qrels, embedding model
  metadata, document embeddings, and query embeddings;
- build an exact vector oracle over the frozen document embeddings;
- build binary signatures with configured encoder families;
- search binary candidates, exact-rerank those candidates with the source
  float embeddings, and evaluate the reranked run against qrels.

The smoke fixture is intentionally tiny. It tests the evaluation plumbing, not
production retrieval quality.

### Why it matters

The earlier binary experiments were synthetic and mostly used exact-float
top-k preservation as the quality proxy. That is useful for measuring candidate
loss, but it does not prove that the pipeline can consume real embedding
artifacts or report qrels-based retrieval metrics.

This gate creates the missing bridge:

```text
frozen embeddings + locked qrels
    -> exact vector oracle
    -> binary candidate filter
    -> exact float rerank
    -> shared RetrievalMetrics
```

Embedding generation remains outside the C++ test run, so future fixtures can
use OpenAI, SentenceTransformers, E5/BGE, or any other provider without adding
network/API dependencies to CI.

### Expected result

For the committed smoke fixture we expect:

- exact vector search recovers all positive qrels within the configured
  evaluation depth;
- every binary encoder report includes qrels metrics after exact rerank;
- candidate limits eventually recover the exact top-k when the candidate limit
  reaches the full corpus size;
- trained encoders report document-vector training metadata, and query vectors
  are not training input.

### Setup

Local verification command:

```bash
./tmp/build-pr66-mingw/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-smoke.example.json \
    tmp/build-pr66-mingw/precomputed-embedding-binary-rerank-grid-ctest.json
```

Fixture:

- dataset: `tests/eval/fixtures/precomputed-embedding-tiny.json`;
- corpus size: `6`;
- query count: `3`;
- embedding dimension: `4`;
- metric: cosine;
- result limit: `3`;
- bit counts: `4`, `8`;
- candidate limits: `3`, `4`, `6`;
- encoder seed: `42`;
- encoder families:
  - `random_hyperplane_rademacher`;
  - `coordinate_sign`;
  - `pca_projection`;
  - `itq_rotation_projection`.

### Actual result

Exact vector oracle quality against qrels:

| Metric | Value |
| --- | ---: |
| Recall@1 | 0.6111 |
| Recall@10 | 1.0000 |
| nDCG@10 | 1.0000 |
| MRR | 1.0000 |

Representative binary rerank smoke rows:

| Encoder | Bits | Candidates | Exact top-k candidate coverage | Qrels candidate coverage | Reranked Recall@10 | Reranked nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| random hyperplane | 4 | 3 | 0.6667 | 1.0000 | 1.0000 | 1.0000 |
| random hyperplane | 8 | 3 | 0.7778 | 1.0000 | 1.0000 | 1.0000 |
| coordinate sign | 4 | 3 | 0.8889 | 1.0000 | 1.0000 | 1.0000 |
| PCA projection | 4 | 3 | 0.7778 | 1.0000 | 1.0000 | 1.0000 |
| ITQ rotation | 4 | 3 | 0.6667 | 1.0000 | 1.0000 | 1.0000 |
| all supported encoders | supported | 6 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

The exact-top-k coverage and qrels coverage intentionally differ: exact top-k
is a dense-neighbour preservation proxy, while qrels coverage answers whether
the judged relevant items survived the candidate stage. On this tiny fixture,
candidate limit `3` already preserves all positive qrels even when it misses
some exact-neighbour positions.

### Interpretation

The PR establishes a useful regression gate:

- real/precomputed embedding artifacts can be loaded and validated;
- document/query embedding coverage is identity-checked against the retrieval
  dataset;
- binary candidate retrieval can be evaluated against qrels after exact rerank;
- future real-embedding fixtures can be added without changing the benchmark
  architecture.

Do not interpret the smoke numbers as evidence that any encoder family is
better in production. The fixture is too small and deliberately axis-aligned.

### Limitations

- Tiny hand-authored fixture, not a real corpus.
- Single encoder seed.
- Single local run; timings are only a smoke signal.
- No externally generated embedding model fixture is committed yet.
- No held-out training/evaluation split beyond the rule that trained encoders
  use document vectors only and never query vectors.

### What to check next

- Add a medium or real externally generated fixture.
- Add a common-baseline report comparing synthetic conclusions with real
  embedding qrels.
- Keep qrels metrics and exact-top-k candidate coverage separate in all future
  reports.

## 2026-07-22 — PR #68 medium fixture and artifact provenance gate

### What we checked

PR #68 keeps embedding generation outside the C++ test run, but adds the
missing provenance surface for frozen embedding artifacts:

- optional `embedding_artifact` metadata in precomputed embedding datasets;
- validation that all provenance fields are non-empty when the block exists;
- propagation of artifact provenance into benchmark JSON reports;
- a medium hand-authored semantic-axis fixture with more documents, more
  queries, and enough structure for encoder/candidate settings to differ;
- a dedicated CTest smoke gate for that medium fixture.

The medium fixture is deliberately **not** described as output from a real
external embedding model. It is a frozen semantic-axis artifact used to test the
precomputed benchmark mechanics without network/model dependencies in CI.

### Setup

Local verification command:

```bash
./tmp/build-pr68/tools/agent-memory-bench/agent-memory-bench \
    tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-medium.example.json \
    tmp/build-pr68/precomputed-embedding-binary-rerank-grid-medium-ctest.json
```

Fixture:

- dataset: `tests/eval/fixtures/precomputed-embedding-medium.json`;
- corpus size: `12`;
- query count: `4`;
- embedding dimension: `8`;
- artifact projection kind: `semantic_axes_8d`;
- metric: cosine;
- result limit: `5`;
- bit counts: `4`, `8`;
- candidate limits: `5`, `8`, `12`;
- encoder seed: `42`;
- encoder families:
  - `random_hyperplane_rademacher`;
  - `coordinate_sign`;
  - `pca_projection`;
  - `itq_rotation_projection`.

### Actual result

Exact vector oracle quality against qrels:

| Metric | Value |
| --- | ---: |
| Recall@10 | 1.0000 |
| nDCG@10 | 1.0000 |
| MRR | 1.0000 |

Representative binary rerank rows from one local CTest run:

| Encoder | Bits | Candidates | Exact top-k candidate coverage | Qrels candidate coverage | Reranked Recall@10 | Reranked nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| random hyperplane | 4 | 5 | 0.6000 | 0.6458 | 0.6458 | 0.8049 |
| random hyperplane | 8 | 5 | 0.6500 | 0.7083 | 0.7083 | 0.9162 |
| coordinate sign | 8 | 5 | 0.8000 | 1.0000 | 1.0000 | 1.0000 |
| PCA projection | 4 | 5 | 0.7000 | 0.7917 | 0.7917 | 0.9523 |
| PCA projection | 8 | 5 | 0.6500 | 0.7292 | 0.7292 | 0.9191 |
| ITQ rotation | 4 | 5 | 0.7000 | 0.8542 | 0.8542 | 0.9610 |
| ITQ rotation | 8 | 5 | 0.7000 | 0.8542 | 0.8542 | 0.9622 |
| ITQ rotation | 4 | 8 | 0.9000 | 1.0000 | 1.0000 | 1.0000 |
| ITQ rotation | 8 | 8 | 0.8500 | 1.0000 | 1.0000 | 1.0000 |
| all supported encoders | supported | 12 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

Training diagnostics are present in the report. Zero-training families report
zero training cost and zero artifact payload; PCA/ITQ report document-vector
training metadata. In this tiny medium fixture the measured local training
times are sub-millisecond to a few milliseconds, so they are useful only as
schema coverage, not performance evidence.

### Interpretation

This PR strengthens the regression gate in three ways:

- benchmark output can now be traced to a frozen embedding artifact identity;
- medium fixture rows are no longer all trivially equal at the smallest
  candidate limit, so the gate can catch more ranking/candidate regressions;
- full-corpus candidate rows still prove that exact rerank and qrels evaluation
  recover the oracle when candidate generation is not lossy.

The result does **not** settle production encoder choice. Coordinate sign looks
strong on this axis-aligned fixture because the fixture was built around stable
semantic axes; that is a useful diagnostic, not a claim about real embeddings.

### What to check next

- Add an externally generated embedding fixture with the same provenance fields.
- Preserve generator/model/config revisions, dataset revision, qrels revision,
  prompt identities, config hash, and artifact hash for every real fixture.
- Compare synthetic, hand-authored semantic-axis, and external-model fixtures
  side by side before making backend decisions.

## 2026-07-22 — PR #69 provenance contract hardening

### What we checked

PR #69 tightens the provenance shape before committing any external-model
embedding artifact:

- one ambiguous `source_revision` is split into `dataset_revision`,
  `generator_revision`, `model_revision`, and `qrels_revision`;
- asymmetric embedding setups get explicit `document_prompt_id` and
  `query_prompt_id`;
- `normalization`, `dtype`, and `hash_algorithm` are part of artifact identity;
- `config_hash` and `artifact_hash` are validated as lowercase 64-character
  SHA-256 hex strings;
- CTest validates that the complete provenance object survives
  dataset-load -> benchmark-run -> report-JSON serialization.

### Canonical hash scope

`config_hash` is the SHA-256 digest of the canonical generator configuration:

```text
model id/revision
document/query prompt identities
normalization and dtype
pooling/truncation
projection transform
random seed
other behavior-affecting generator settings
```

`artifact_hash` is the SHA-256 digest of the canonical embedding payload before
any self-referential provenance wrapper is added. For JSON fixtures this means
the ordered document/query embedding records with ids and float values, not the
whole JSON file containing `embedding_artifact.artifact_hash` itself.

### Interpretation

This PR is intentionally a contract hardening PR, not a new quality benchmark.
It prevents the next external fixture from becoming a pile of plausible-looking
metadata whose hashes and revisions cannot be reproduced.

### What to check next

- Add a verifier that recomputes `config_hash` and `artifact_hash` from the
  canonicalized config and payload.
- Keep real-model conclusions separate from hand-authored semantic-axis fixture
  conclusions.

## 2026-07-22 — PR #70 artifact hash verifier gate

### What we checked

PR #70 adds the missing integrity check for committed precomputed embedding
fixtures. The loader still validates schema/coverage, but CI now also runs a
separate verifier that:

- canonicalizes the generator-config identity fields;
- canonicalizes ordered document/query embedding records;
- recomputes SHA-256 for the canonical config text and embedding payload;
- rejects fixtures whose declared `config_hash` or `artifact_hash` no longer
  matches the actual content.

### Canonical verifier command

```bash
cmake --build build --target agent-memory-precomputed-artifact-verify
build/tools/agent-memory-bench/agent-memory-precomputed-artifact-verify \
    tests/eval/fixtures/precomputed-embedding-medium.json
```

CTest runs the same verifier for the tiny and medium fixtures.

### Interpretation

This closes the main limitation left after PR #69: hashes are no longer only
well-formed metadata. For committed fixtures, they are recomputed from the
canonical config/payload and fail CI if they drift.

The verifier is intentionally dependency-free and does not generate embeddings.
It is the integrity gate that the next external-model fixture should pass after
an external generator writes the frozen JSON artifact.

### What to check next

- Add an externally generated embedding fixture using this verifier.
- Record the external generator command/config next to the fixture.
- If real model prompts become non-trivial, include prompt text or prompt hashes
  in the canonical generator config rather than relying only on prompt ids.

## 2026-07-22 — PR #71 canonical artifact encoding

### What we changed

PR #71 replaces the prototype CMake verifier with a small C++ verifier
executable. The verifier still recomputes `config_hash` and `artifact_hash`,
but `artifact_hash` is no longer based on incidental JSON number spelling.
Embedding records are encoded as a canonical binary payload before hashing:

- ASCII magic `agent-memory-precomputed-embedding-payload-v1`;
- document embedding records in fixture order, followed by query embedding
  records in fixture order;
- record type byte: `1` for documents and `2` for queries;
- little-endian `uint32` id byte length, followed by UTF-8 id bytes;
- little-endian `uint32` vector dimension;
- every numeric value parsed from JSON, rounded to IEEE-754 `float32`, encoded
  little-endian, with negative zero canonicalized to positive zero.

The verifier currently accepts only `dtype = "float32"` and
`hash_algorithm = "sha256"`. It also checks the declared normalization contract:
`embedding_model.normalized = true` requires `normalization = "l2"`, while
`false` requires `normalization = "none"`.

### Why this matters

Equivalent JSON spellings such as `1.0`, `1.00`, and `1e0` now produce the same
payload bytes. Real changes to stored float32 vectors, record ids, record order,
or behavior-affecting config fields still change the declared hashes.

CTest covers both positive fixture verification and negative mutations for:

- generator/model config drift;
- corpus/query dataset drift;
- graded-qrels drift;
- embedding vector payload drift;
- ordered record payload drift.

### Interpretation

This PR does not add a new quality benchmark. It makes the precomputed-embedding
gate safer before introducing external-model fixtures: future benchmark results
can point to frozen vectors whose config and payload hashes are reproducible
inside the C++ test suite.

## 2026-07-22 — PR #72 external-generator precomputed fixture

### What we checked

PR #72 adds the first dependency-free external-generator fixture:

- fixture: `tests/eval/fixtures/precomputed-embedding-external-hash.json`;
- generator:
  `tools/agent-memory-bench/generate-precomputed-external-hash-fixture.py`;
- benchmark config:
  `tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-external-hash.example.json`.

The generator is a standalone Python script that derives normalized dense
vectors from text using deterministic hashed token, bigram, and character
trigram features. The C++ benchmark does not run the generator and does not know
the embedding algorithm; it consumes only the frozen JSON vectors.

This is deliberately not presented as a production neural embedding model. Its
purpose is to exercise the real precomputed-artifact workflow without network,
API-key, or Python package dependencies in CI.

### Integrity contract extension

The artifact provenance now includes:

```text
dataset_hash
qrels_hash
```

in addition to `config_hash` and `artifact_hash`.

The C++ verifier recomputes all four hashes:

- `config_hash` from the generator/model/prompt/config identity text;
- `dataset_hash` from canonical corpus and query records;
- `qrels_hash` from canonical graded relevance judgments;
- `artifact_hash` from canonical binary float32 embedding payload bytes.

For schema v1 these are ordered canonical payloads: corpus, query, judgment,
and embedding record order is part of the frozen artifact identity. A future
schema may define sorted semantics, but PR #72 intentionally keeps the fixture
order explicit and reproducible.

Negative verifier tests now reject corpus-text and qrels mutations as separate
failure modes. This closes the remaining gap where a fixture could change its
evaluation target while preserving embedding payload hashes and revision
strings.

The dependency-free generator is now covered by a regeneration test as well:
CI runs the Python script into the build tree and compares the generated JSON
byte-for-byte against the committed fixture. This proves the frozen artifact is
not only self-verifying, but also reproducible from the checked-in generator.

### Directional result

On the external-hash fixture, the exact-vector oracle reports:

| Metric | Value |
| --- | ---: |
| Recall@1 | 0.4000 |
| Recall@5 | 0.8667 |
| Recall@10 | 0.8667 |
| nDCG@10 | 0.8703 |
| MRR | 1.0000 |

This is useful because the fixture is no longer an idealized hand-authored
semantic-axis cube. The exact oracle is good but imperfect against qrels, so the
binary candidate pipeline is tested under a slightly messier, text-derived
embedding geometry.

### Interpretation

This PR proves that a frozen external generator can produce a self-verifying
precomputed embedding artifact consumed by the C++ benchmark pipeline. It does
not prove anything about MiniLM, OpenAI, Qwen, E5, BGE, or another third-party
embedding family. Those require a separate fixture with pinned model/tokenizer
revision and generator environment.

### What to check next

- Add an actual third-party embedding-model fixture when a stable generator
  environment is available.
- Record prompt text or prompt hashes when the generator uses non-identity
  prompts.
- For neural fixtures, serialize model output as explicit float32 values with
  round-trip-safe JSON spelling instead of compact decimal rounding used by the
  hash-derived fixture.
- Keep generator execution outside CI unless the model dependency is vendored or
  otherwise made deterministic and cheap.

## 2026-07-22 — PR #73 MiniLM third-party frozen fixture

PR #73 adds the first committed fixture generated by an actual third-party
embedding model:

- fixture: `tests/eval/fixtures/precomputed-embedding-minilm-l6-v2.json`;
- generator: `tools/agent-memory-bench/generate-precomputed-minilm-fixture.py`;
- benchmark config:
  `tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-minilm.example.json`;
- model: `sentence-transformers/all-MiniLM-L6-v2`;
- model/tokenizer revision:
  `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`;
- dimension: `384`;
- pooling/normalization: mean pooling + L2 normalization;
- numeric contract: model outputs are explicitly narrowed to float32 before
  JSON serialization.

The generator is not run in CI. CI consumes the frozen artifact and verifies:

- `config_hash`;
- `dataset_hash`;
- `qrels_hash`;
- `artifact_hash`;
- extended generator provenance: `tokenizer_revision`,
  `generator_source_hash`, `generator_contract_source_hash`,
  `generator_command`, and `generator_requirements_lock`.

`generator_source_hash` is a SHA-256 digest of the MiniLM generator script.
`generator_contract_source_hash` is a SHA-256 digest of the shared canonical
fixture-contract script. This avoids a circular dependency on the PR commit SHA
while still binding the artifact identity to the checked-in generation logic.

### Directional result

On the MiniLM fixture, the exact-vector oracle reports:

| Metric | Value |
| --- | ---: |
| Recall@1 | 0.4000 |
| Recall@5 | 0.7667 |
| Recall@10 | 0.7667 |
| nDCG@10 | 0.9462 |
| MRR | 1.0000 |

The contrast between `Recall@10` and `nDCG@10` is useful: MiniLM places the
highest-value relevant item very well on this small fixture, but it does not
recover every judged relevant document. This makes the fixture a better
regression gate than a perfectly aligned synthetic cube, while still being far
too small for production-quality conclusions.

Representative binary-rerank rows from one local CTest run:

| Encoder | Bits | Candidates | Exact top-k candidate coverage | Qrels candidate coverage | Reranked Recall@10 | Reranked nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Random hyperplane | 128 | 5 | 0.76 | 0.93 | 0.93 | 0.9640 |
| Randomized Hadamard | 128 | 8 | 0.80 | 0.93 | 0.87 | 0.9563 |
| PCA projection | 128 | 8 | 0.60 | 0.63 | 0.57 | 0.7060 |
| ITQ rotation | 128 | 5 | 0.68 | 0.77 | 0.77 | 0.9462 |
| Full candidate set | 128 | 12 | 1.00 | 1.00 | 0.77 | 0.9462 |

As in PR #72, small-corpus timings are not performance evidence. The benchmark
does show training cost fields for learned encoders: on this local run,
128-bit PCA training took about 10.2 seconds and ITQ about 17.2 seconds over 12
document vectors. That is a useful warning to keep learned training outside
query/build hot-path timing and to avoid broad CI grids at 384D.

### Interpretation

This PR closes the gap between dependency-free surrogate fixtures and a real
embedding-model artifact. It does not establish a production default encoder or
bit budget. The next useful work is a larger real/precomputed fixture or a
separate offline benchmark with more queries, multiple seeds, and 256/512-bit
budgets.

### Follow-up work recorded after review

PR #73 intentionally keeps the MiniLM fixture small and CI-friendly. The next
steps should be split instead of expanding this smoke fixture:

1. Provenance hygiene: move shared fixture-contract helpers out of
   `generate-precomputed-external-hash-fixture.py`, document the official
   regeneration environment, and replace the inline
   `generator_requirements_lock` string with a real lockfile or an explicit
   lockfile-hash policy.
2. Larger real/precomputed benchmark: increase document/query count, topic
   diversity, bit budgets, seeds, and candidate ratios. Learned encoders such
   as PCA and ITQ should use separated training/evaluation records before their
   relative quality is interpreted.
3. Optional attestation hardening: record hashes for resolved model/tokenizer
   files, such as model config, tokenizer JSON, and model weights, when a
   fixture is meant to support stronger reproducibility claims.

## 2026-07-22 — PR #74 provenance hygiene follow-up

PR #74 keeps the PR #73 MiniLM fixture small but tightens its provenance:

- shared fixture corpus, queries, qrels, and canonical hash encodings moved to
  `tools/agent-memory-bench/precomputed_fixture_contract.py`;
- the dependency-free external-hash generator now imports that contract module
  and still regenerates the same JSON byte-for-byte;
- the MiniLM fixture now records `generator_contract_source_hash` for the
  dedicated contract module instead of the larger external-hash generator;
- `generator_requirements_lock` now records the path and SHA-256 of
  `tools/agent-memory-bench/requirements-minilm-fixture.txt`;
- the MiniLM generator parses that same requirements manifest and checks the
  declared Python/package versions before producing vectors;
- the MiniLM CTest recomputes the generator, contract, and requirements-file
  hashes from checked-in files.

The document/query vectors did not change: `artifact_hash` remains
`713b34f1fdddb4676930fbac458257a2897e4d7891ef47b519e65b1ad01a8ce1`.
Only the provenance-bound `config_hash` changed because the contract-source and
requirements-lock identities changed.

## 2026-07-22 — PR #75 larger MiniLM precomputed fixture

PR #75 adds a larger frozen MiniLM fixture without changing the PR #73 smoke
fixture:

- fixture:
  `tests/eval/fixtures/precomputed-embedding-minilm-l6-v2-large.json`;
- content contract:
  `tools/agent-memory-bench/precomputed_fixture_large_contract.py`;
- generator:
  `tools/agent-memory-bench/generate-precomputed-minilm-large-fixture.py`;
- corpus/query/qrels shape: 36 documents, 12 queries, 36 graded qrels;
- model/tokenizer: same pinned `sentence-transformers/all-MiniLM-L6-v2`
  revision as PR #73;
- CI benchmark families: `random_hyperplane_rademacher` and
  `randomized_hadamard_projection`;
- bit budgets: 128 and 256;
- candidate limits: 10, 18, 27, and 36.

The fixture is intentionally still small enough for CI. It is larger than the
first MiniLM smoke fixture, but it is not a statistically stable production
benchmark.

The exact-vector oracle reports:

| Metric | Value |
| --- | ---: |
| Recall@10 | 1.0000 |
| nDCG@10 | 0.9167 |

Representative local rows:

| Encoder | Bits | Candidates | Exact top-k candidate coverage | Qrels candidate coverage | Reranked Recall@10 | Reranked nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Random hyperplane | 128 | 10 | 0.6250 | 0.9444 | 0.9444 | 0.8851 |
| Random hyperplane | 256 | 18 | 0.9000 | 1.0000 | 1.0000 | 0.9175 |
| Randomized Hadamard | 128 | 18 | 0.8750 | 1.0000 | 1.0000 | 0.9167 |
| Randomized Hadamard | 256 | 10 | 0.7250 | 1.0000 | 1.0000 | 0.9169 |
| Full candidate set | 128/256 | 36 | 1.0000 | 1.0000 | 1.0000 | 0.9167 |

This is a useful quality-regression signal for candidate over-fetch: even at
10 candidates, the binary stage usually preserves most qrels-relevant items,
and 18 candidates is enough to recover full qrels recall in this fixture for
the stronger zero-training rows. It is not speed evidence. On such a small
corpus, exact vector scan remains the wrong denominator for production-scale
claims, and binary encode/search/rerank overhead can dominate.

PCA and ITQ are intentionally excluded from the PR #75 CI grid. Learned
encoders need a larger train/evaluation split and their training cost should be
measured in an offline or follow-up benchmark before they become part of a
larger always-on CI gate.

## 2026-07-31 — Multilingual E5 frozen fixture

### What we checked

This continuation adds a small reproducible external-model fixture for
`intfloat/multilingual-e5-small`:

- 12 bilingual Russian/English documents and 6 bilingual queries;
- 12 graded relevance judgments, including a lower-grade translation for each
  query;
- model and tokenizer revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`;
- model-default SentenceTransformers pooling and L2-normalized float32 vectors;
- CPU-only execution with one Torch thread and deterministic-inference policy
  `torch_cpu_single_thread_deterministic_inference_v1`;
- explicit asymmetric E5 inputs: `passage:` for documents and `query:` for
  queries;
- generator source, content contract, environment lock, dataset, qrels,
  configuration, and embedding-payload integrity checks.

The third-party model is executed only by the offline generator. CTest verifies
the frozen artifact, its bilingual content contract, and a compact binary
candidate/rerank matrix without downloading a model.

### Expected result

The fixture should prove the artifact path can preserve an asymmetric,
multilingual model contract. Full-corpus binary candidates must recover exact
top-k and qrels coverage for both zero-training encoder families. It is too
small to choose an encoder or to train an autoencoder.

### Setup

- fixture:
  `tests/eval/fixtures/precomputed-embedding-multilingual-e5-small.json`;
- corpus/query shape: 12 / 6;
- source dimension: 384;
- bit counts: 128 and 256;
- candidate limits: 6, 9, and 12;
- families: random hyperplane and randomized Hadamard; seed 42.

Generation uses a cached local model and pinned environment manifest:

```text
requirements-multilingual-e5-small-fixture.txt
generate-precomputed-multilingual-e5-small-fixture.py --local-files-only
```

The generator passes `device="cpu"` to SentenceTransformers and enables Torch
deterministic algorithms. The artifact records the execution device, float32
payload dtype, and deterministic-policy identifier; its generator revision is
therefore a CPU-only regeneration contract, not a claim that CUDA would produce
the same bytes.

### Actual result

The exact oracle reached Recall@10 = 1.000 and nDCG@10 = 1.000 on this
deliberately compact bilingual fixture.

| Encoder | Bits | Candidates | Exact top-k coverage | Qrels coverage | Reranked nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Random hyperplane | 128 | 6 | 0.6944 | 1.0000 | 1.0000 |
| Random hyperplane | 256 | 9 | 0.9444 | 1.0000 | 1.0000 |
| Randomized Hadamard | 128 | 6 | 0.7222 | 0.9167 | 0.8896 |
| Randomized Hadamard | 256 | 9 | 0.9444 | 1.0000 | 1.0000 |
| Any listed family | 128 / 256 | 12 | 1.0000 | 1.0000 | 1.0000 |

### Interpretation

The fixture establishes a multilingual, real-model regression boundary rather
than a quality verdict. At this scale the small candidate set already preserves
most judged relevance, while exact top-k candidate coverage still reveals that
the binary order is lossy. This reinforces the current use of binary signatures
as a candidate stage with exact float reranking.

The fixture is intentionally unsuitable as autoencoder training evidence:
12 document embeddings cannot estimate a general compression model. The first
autoencoder experiment needs a larger document-only training split and a
held-out E5 query/qrels evaluation. It should compare encoder-only candidate
filtering with ITQ/PCA and evaluate decoder-backed approximate-vector retrieval
as a separate question.

### Limitations and next checks

- Hand-authored bilingual content is a contract fixture, not a representative
  Russian corpus.
- One encoder seed and one local timing run are not stable performance evidence.
- The next E5 work is a larger split with no query/qrels leakage into encoder
  training, followed by the offline autoencoder trainer and versioned encoder
  plus optional decoder artifact.
- `gigatoken` remains deferred optional offline BPE tooling; assess it only
  after fixture generation exposes a measured tokenization bottleneck.
