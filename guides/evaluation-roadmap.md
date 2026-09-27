# Evaluation and Benchmark Roadmap

Status: `Normative` for retrieval and index claims. This guide defines what a
future implementation PR must measure; it does not require a benchmark
dependency in the core library.

## Evaluation ladder

1. **Contract tests** — deterministic ordering, tie breaks, scope/filter
   semantics, reopen and delete behaviour.
2. **Exact oracle** — a small in-memory or brute-force implementation used to
   measure recall and ranking loss for every approximate or compressed path.
3. **Component benchmark** — isolated tokenizer, scorer, encoder, index,
   decoder, or reranker cost.
4. **End-to-end benchmark** — ingestion, index publication, query execution,
   candidate generation, reranking, and context materialization.
5. **Operational replay** — warm/cold cache, restart, update/delete, rebuild,
   and crash-recovery cases.

A passing lower rung never implies that a higher rung is passed.

## Required report

Every comparative run publishes a compact JSON report and a manifest binding:

```text
commit, compiler/build flags, hardware, corpus/chunking, query set, qrels,
embedding/model IDs, index parameters, candidate depth, final limit,
filter policy, concurrency, cold/warm procedure, and inclusion of encoding time
```

For external or lifecycle comparisons, the machine-readable manifest must also
carry these fields (not merely prose in a report):

```text
corpus_sha256, query_sha256, qrels_sha256, oracle_sha256, benchmark_config_sha256
row_counts, cpu_model, physical_cores, logical_cores, numa_topology, ram_bytes
storage_medium, process_affinity, thread_affinity, effective_thread_counts
repeat_count, warmup_count, raw_distribution_sha256, startup_open_load_ms
index_parameters, batch_size, concurrency, result_shape
```

`result_shape` states whether the measurement returns IDs only, IDs plus
scores, or full records. A backend's advertised batch size is not assumed to
be one physical write operation; the manifest records the actual operation
path.

Quality fields are selected by workload (`recall@k`, `nDCG@k`, `MRR`, source
coverage, citation preservation). Cost fields include build/ingest time,
query p50/p95/p99, peak RSS/map size, logical and physical index bytes, page
reads, and update/reindex cost when applicable.

## Acceptance rules

- Approximate search must report quality against the exact oracle on the same
  candidate contract.
- A latency claim requires repeated runs, warmup policy, fixed environment,
  and a preserved raw result; one local timing is directional only.
- Codec claims separate storage codec bytes, search-code bytes, model/global
  tables, and temporary decode buffers.
- Hybrid claims report each component, fusion, and end-to-end result; a fused
  score must not hide a missing candidate source.
- External comparisons require a `ComparisonParityManifest` and an explicit
  compatibility matrix. Missing inputs produce `PENDING_SOURCE_REPLAY`, never
  an inferred result.
- Quality/latency frontiers are compared only at measured comparable quality.
  `Recall@K` is reported against the exact oracle; `nDCG@K` is reported against
  qrels. They are separate metrics and must not be substituted for one another.
- Report search-kernel, full embedded retrieval, and client/server request
  costs as separate timing layers. Include query encoding, transport,
  serialization and result materialization only in the layers where they occur.

## Ingestion and lifecycle timing

Write measurements separate these observable boundaries:

```text
accepted -> durable_commit -> index_ready -> search_visible
```

`ACK` is not automatically `search_visible`. Bulk build and incremental
insert/update/delete are separate scenarios, and each records durability mode,
batch size, physical write operation, index publication/rebuild time and the
first query that observes the new revision.

Operational replay includes a bounded concurrent scenario with search running
during updates, deletes and rebuild. Its pre-registered acceptance fields are
query/write p95/p99, visibility lag, queue/backlog depth,
revision-generation filtering, and no deleted-record resurrection. A passing
write benchmark without these correctness checks is not a lifecycle result.

## Physical retrieval execution gate (planned)

The retrieval protocol also evaluates how a candidate stream is executed. This
is a library-internal optimization boundary, not a distributed-actor contract.
An implementation may use a stateful `RetrievalExecutor` (or an equivalent
host-owned executor) that owns the execution plan, bounded queues and batch
lifetimes for one request. It must remain dependency-free in the core and
delegate persistence-specific reads to adapters.

Every executor profile declares a `RetrievalExecutionPlan` containing at least:

- candidate source and routing depth (`level_top`/cluster count);
- `max_candidates`, `top_k`, byte and payload budgets;
- batch sizes for candidate reads and canonical payload hydration;
- deduplication key and deterministic equal-score/tie policy;
- index id/generation and embedding-model revision required for cache hits;
- whether a covering or partial-covering projection may satisfy scoring fields;
- cancellation/deadline behavior and the returned result shape.

The physical pipeline is measured as explicit stages:

```text
prepare query -> choose partitions -> batch candidate read -> bounded top-N
  -> deduplicate -> batch payload/covering read -> exact score/rerank -> top-K
```

Implementations may overlap candidate reads, hydration and scoring only through
bounded queues. They must not retain an unbounded candidate list, and a covering
projection is always a rebuildable derived index rather than a second canonical
store. Full embeddings in a covering projection are opt-in; the default profile
stores only identifiers, revisions, code/score data and the minimum fields
needed by the declared scorer.

The minimum benchmark compares sequential and pipelined executors over the same
index, candidate stream, recall target and result shape. It records stage-wise
and end-to-end p50/p95/p99, peak queue depth, candidate/read counts, duplicate
rate, payload bytes, cache hit rate, stale-generation drops and cancellation
latency. Acceptance requires equal exact-oracle quality within the pre-registered
tolerance, no stale-generation or deleted-record leakage, bounded memory, and
no hidden extra storage reads. A failed covering or cache lookup falls back to
canonical hydration and is recorded as such.

This gate is planned for M2+ and depends on stable retrieval contracts, an
index-generation manifest and the lifecycle replay in this document. The main
risk is optimizing adapter or I/O scheduling while accidentally changing the
candidate set or quality contract; the benchmark therefore freezes quality and
storage inputs before timing.

## Federated retrieval across heterogeneous embedding spaces (M2+ planned)

Federation may combine several derived vector sources, but it must not pretend
that scores from different embedding spaces are directly comparable. Every
source publishes a `SemanticSpaceManifest` and an `IndexManifest` before it can
participate in a federated query. At minimum these manifests bind the source
and projection identities, embedding model and revision, preprocessing hash,
dimension, metric, normalization policy, codec identity, corpus revision and
active index generation. A candidate also carries its source, local rank,
local score, score kind (`exact`, `reconstructed`, `approximate` or
`rank_only`) and the space-manifest hash.

The coordinator uses an explicitly selected fusion policy:

```text
query per compatible space -> local candidate batches -> stable-ID dedup
  -> common-space exact rerank, payload/cross-encoder rerank, calibrated fusion,
     or rank-only RRF -> deterministic top-K
```

The policy is capability-driven. Raw vectors in one compatible space may use a
common exact rerank; reconstructed codes are marked approximate. Heterogeneous
spaces require a declared common query/payload reranker or a separately
validated mapping trained on anchor pairs. If only local scores or ranks are
available, rank-only RRF is the safe default. Raw cosine, inner-product and L2
values from unrelated models must never be added or weighted without a
held-out calibration manifest. A mapping is not inferred from coincidentally
similar dimensions, and a model migration adapter is not a universal
translation guarantee.

The minimum federated gate uses the same corpus, query set and exact oracle as
the single-source controls and reports, per source and for the union:

- local candidate recall, union candidate recall and final `Recall@K`/`nDCG@K`;
- candidate, deduplication, rerank and re-encoding work plus p50/p95/p99;
- bytes read, payload/vector hydration, cache hits and missing/stale-source
  drops;
- fusion policy, calibration/anchor provenance and the result shape.

Fixtures must cover one compatible model with different ANN indexes, different
codecs, genuinely heterogeneous models with shared payload, and heterogeneous
models with no shared payload (RRF only). They also cover duplicate stable IDs,
missing sources, incompatible manifests, stale generations and source failure.
No federated result is accepted if it silently mixes generations or presents a
local approximate score as a global exact score. This gate is a derived-index
and coordinator concern; canonical records and provenance remain in the
library-owned store, and an external or SQLite source is an optional adapter,
not a second canonical database.

## Minimal benchmark matrix

```text
exact scan -> F16/int8/binary/PQ or other codec -> HNSW/ANN -> hybrid -> rerank
```

The matrix is run first on a deterministic synthetic fixture and then on a
versioned local corpus. Held-out or multilingual slices are separate gates,
not silently pooled into the primary score.
