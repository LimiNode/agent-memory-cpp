# Index Lifecycle Roadmap

Status: `Proposal`, owned by the storage/index boundary. Derived indexes are
rebuildable views; canonical resources, knowledge units, payloads, and source
provenance remain the source of truth.

## Required lifecycle states

```text
unpublished -> building -> published -> stale -> rebuilding -> retired
```

Each index generation records the corpus/profile generation, configuration
digest, builder version, and publication timestamp. Readers pin one generation
for a request and never mix postings or vectors from different generations.

## Mutation rules

- insert/update publishes new derived entries only after the canonical write is
  durable;
- update creates a new projection generation when identity or model inputs
  change;
- delete/tombstone filtering is applied at candidate materialization and during
  rebuild;
- targeted reindexing is preferred for one resource revision; full rebuild is
  an explicit maintenance operation;
- compaction and rebuild use bounded batches and resumable checkpoints, never
  one unbounded write transaction.

## Required checks before implementation

1. reopen after publish and after an interrupted build;
2. old generation remains readable until the new one is published;
3. stale postings cannot hydrate a deleted or superseded unit;
4. restart resumes or safely discards a checkpoint;
5. rebuild is deterministic from canonical storage and its manifest;
6. map growth, page limits, backpressure, and read-transaction lifetime are
   observable.

HNSW, compressed vectors, graph adjacency, lexical postings, and learned sparse
   views must each document their tombstone, update, serialization, and rebuild
   policy before entering a shipping milestone.

## Durable exact generation compatibility profile

The optional `MdbxExactIndexGenerationStore` owns derived lifecycle only. It
does not publish canonical units, resources, projections, CAS or access policy.
The existing `IVectorIndex` and its `ChunkId` contract remain unchanged.

One additional opt-in DBI, `<prefix>_exact_index_generations`, contains immutable
generation descriptors, numbered candidate rows, progress, completion manifests
and one active pointer per index name. Schema v1 supports bounded FP32 exact
reference vectors, dot product or negative squared L2, and no normalization.
The descriptor includes model/space and recipe identity, dimension, scalar bit
width, metric, codec, scope and Original projection family. It is stored once
per generation. Unsupported encodings fail closed; this is not an ANN backend.

A build declares its expected row count (1..1024), appends at most 32 rows per
transaction, and completes only after every row is present and validated.
Dimensions are bounded at 4096. Duplicate units are rejected. Completion binds
the descriptor and ordered row payloads with SHA-256 and records an independently
computed source frontier over immutable canonical/projection bindings. This
frontier identifies exactly the selected inputs, not a claim of corpus coverage.
An interrupted build remains unselectable; a new build uses a new generation ID.
Completed generations cannot be appended or overwritten. A repeated begin with
the identical descriptor/count is a no-op; conflicting reuse is rejected.

Candidate identity is an explicit `KnowledgeUnitId` and unit revision, never a
vector position or `ChunkId`. During append, existing unit/projection/manifest
authorities are read in the same transaction. Each row captures the exact source
resource revision and digest and a fingerprint of provenance and Original
projection evidence. Completion and activation revalidate all bindings. Only
the active-pointer update is claimed atomic in MDBX; external artifacts are not
part of that transaction. Failed activation leaves the previous pointer intact.
Rollback explicitly selects a retained complete compatible generation, provided
its canonical inputs are still current.

Pinning validates completion and returns an immutable in-memory copy of that
generation with a weak lifetime-aware identity of its MDBX connection, table
prefix and logical index. Later activation cannot change it. An expired or
unrelated connection is rejected before reading candidates. Each query independently checks
current canonical admission in one MDBX snapshot, so a generation pin does not
freeze canonical truth. Stale revisions, erased units, inactive manifests and
changed provenance are excluded. Scope and a required host-owned authorization/
hard-filter predicate run before scoring and top-K. No stored field can grant
access. A missing predicate is rejected. This profile returns source-bound
candidate envelopes, not CAS hydration or a claim of verified full source bytes.

Rebuild paths remain distinct: topology rebuild consumes retained compatible
embeddings; lossless re-encoding consumes exact FP32; re-embedding consumes exact
retained text plus an immutable compatible model/recipe. This profile accepts
explicit exact FP32 inputs and persists them for reopen. It does not implement
an encoder or lossy decoding. A pinned snapshot's `retained_inputs()` copies
the exact persisted vectors with canonical IDs/revisions and retains the
original descriptor for compatible rebuild. The values are host-asserted
inputs: `model_id` and `recipe_id` are identity assertions, not cryptographic
model attestation. Callers must not mix spaces or relabel values as another
model's embeddings; the store can enforce dimensions and finite FP32 values,
but cannot identify an arbitrary external model mix-up. Missing exact vector
inputs produce typed `RebuildUnavailable`; a changed model is a different
descriptor/space, never a reinterpretation of old values. Identical rebuild
means a new immutable generation from the same inputs, not rewriting the old
generation.

The pre-release compatibility gate requires separate installed OLD and NEW
C++17 processes and canonical logical snapshot equality, not MDBX file-byte
equality. Missing upgrade proof is an open project-specific gate, not evidence
of a canonical schema incompatibility. Production R4 activation, performance,
compaction/resume and external model/codebook publication remain deferred.

### Consumer and conformance entry points

Include `<agent_memory/infrastructure/mdbx/MdbxExactIndexGenerationStore.hpp>`
with MDBX enabled. The focused `agent_memory_mdbx_exact_index_generation_test`
demonstrates begin/append/complete/activate/pin/query and typed negative results.
The `tests/installed-exact-generation` external C++17 consumer builds with
`find_package(agent_memory CONFIG REQUIRED)`; it does not add library sources.
The focused test removes its uniquely owned temporary MDBX/CAS directory after
success. Set `AGENT_MEMORY_KEEP_EXACT_GENERATION_TEST_DATA=1` only when retaining
that directory for a diagnostic run; failure paths retain it automatically.

`tools/run-exact-generation-upgrade.py` requires clean exact-revision OLD/NEW
checkouts, installed dependency packages and a fresh artifact destination. It
builds those packages itself, then runs OLD create, NEW upgrade, NEW interrupted
build, NEW reopen, NEW uncommitted-pointer crash, NEW reopen, OLD verify as
separate processes. OLD is pinned to the post-Raw-Markdown baseline
`52004324bc0d2b07c3657245195db349dd391774`. The receipt records both commits,
source/dependency trees, options/toolchains, executable and fixture digests,
canonical logical snapshot and process exit codes. Synthetic SHA-derived
reference vectors qualify lifecycle, not embedding quality. The packaging
workflow retains the receipt, log and canonical snapshot as an artifact.

Authorization callbacks run inside the query's read transaction and must not
reenter this context or perform canonical writes. The store and test hook are
not concurrently mutable. Independent queries use their own thread-owned read
transactions. Generation snapshots own no live MDBX transaction.
