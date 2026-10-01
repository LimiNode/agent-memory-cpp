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

## Canonical serving modes for compressed retrieval

Codec comparisons use three distinct serving modes:

1. **Full-flat packed 1M:** scan every packed document and produce ordered
   top-10. This isolates codec/kernel cost and cache behaviour.
2. **Prototype-IVF / balanced cascade:** deterministic prototype cells produce
   a declared candidate budget, followed by THQ top-128 and the packed final
   scorer. This measures a cheaper coarse route.
3. **Modern R4 cascade:** the frozen R4 route produces its candidate stream,
   followed by THQ4 top-128 and the packed final scorer. This is the primary
   quality-oriented production path.

The fixed-top128 scorer fixture is component evidence only. It is not a fourth
serving mode. Each mode has its own candidate contract, routing parameters and
latency scope; values from one mode must not fill another mode's table.

For a codec bake-off, freeze the same corpus, query order, numeric-ID tie rule,
payload revisions, one warmup plus five measured repeats, percentile rule and
independent parity audit. Prototype-IVF additionally records the prototype
manifest, training prefix, seed, `nlist`, assignment and candidate budget.

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

For activation and runtime evidence, report declarative and procedural quality
separately. `DeclarativeRecall` measures retrieval of required facts and
preconditions; `ProceduralExecutionSuccess` measures host-side completion and
verification; `ProcedureTransfer`, `CorrectionReuse` and
`ProcedureGeneralization` measure transfer, reuse of corrected failures and
performance outside memorized concrete cases. These metrics never authorize
execution and do not replace retrieval `Recall@K` or qrels-based `nDCG@10`.

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

## Minimal benchmark matrix

```text
exact scan -> F16/int8/binary/PQ or other codec -> HNSW/ANN -> hybrid -> rerank
```

The matrix is run first on a deterministic synthetic fixture and then on a
versioned local corpus. Held-out or multilingual slices are separate gates,
not silently pooled into the primary score.

Structured-memory and activation profiles use the same comparison discipline:
compare `hybrid`, `hybrid + typed graph expansion`, and optional activation on
the identical corpus, query set, qrels and exact oracle. Report Recall@K,
nDCG@10, MRR, context precision, latency, candidate-expansion cost, and
explanation/provenance coverage separately. Graph or activation output is not
evidence of a quality lift until this downstream benchmark passes; external
reported gains are not acceptance thresholds. The fixtures also require
numeric-first decoding of IDs/revisions/limits, a distinct candidate-to-
canonical-admission boundary, separate evidence/instruction/authority result
types, monotonic narrowing, permutation-stable replay, typed `UNCLEAR` for
insufficient evidence or exhausted shared proof budgets, and provenance for
typed conclusions, omitted evidence and policy/model revisions. These are
planned research contracts, not completed quality claims or thresholds.
