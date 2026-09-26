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

Quality fields are selected by workload (`recall@k`, `nDCG@k`, `MRR`, source
coverage, citation preservation). Cost fields include build/ingest time,
query p50/p95/p99, peak RSS/map size, logical and physical index bytes, page
reads, and update/reindex cost when applicable.

## External comparison protocol

External backends are comparison targets, not the source of truth for the
library. A comparison is valid only when quality and workload are matched:

- report `Recall@K` against the same exact-oracle top-K set;
- report `nDCG@10` (and other qrels metrics) separately against the same qrels;
- publish quality/latency curves or a frontier, rather than one speedup at an
  unknown quality point;
- keep kernel, embedded retrieval, and client/server costs in separate rows.

The three cost layers are:

| Layer | Included | Excluded |
|---|---|---|
| Search kernel | scorer/index traversal, candidate selection, top-K maintenance | storage open, serialization, RPC, payload hydration |
| Full embedded retrieval | storage reads, filtering, index traversal, rerank, canonical hydration | network and remote service scheduling |
| Client/server request | client serialization, transport, server queueing and execution, response serialization | caller-side work outside the request |

Every external run records the actual index parameters (for example `M`,
`efConstruction`, `efSearch`, probes or quantizer settings), build/runtime
version, thread count, client concurrency, batch size, returned fields and
payload bytes, filter selectivity, and cold/warm procedure. A generic
"same settings" label is not sufficient.

## Ingestion, update, and visibility protocol

Write measurements use explicit boundaries and report these timestamps
separately:

1. admission/acceptance by the write API;
2. durable commit under the declared durability mode;
3. derived-index build or publication readiness;
4. first search visibility under the declared query contract.

`ACK` is not equivalent to search readiness unless the backend contract proves
that equivalence. Bulk build is a separate scenario from incremental
insert/update/delete. Each report states the durability mode, batching,
fsync/group-commit policy, indexing workers, and whether timings include
embedding, serialization, and payload hydration.

## Mixed read/write/rebuild gate

Lifecycle evaluation includes a bounded workload with searches running while
inserts, updates, deletes, and an index rebuild execute. The operation mix,
arrival model, corpus size, update/delete fraction, rebuild trigger, duration,
and acceptance thresholds are locked before the run. At minimum report:

- query and write p50/p95/p99;
- acceptance-to-visible latency distribution;
- pending-write/index backlog and rebuild progress;
- deletion correctness, stale-generation filtering, and post-restart results;
- quality against the exact oracle before, during, and after rebuild.

The minimum acceptance is zero resurrection of deleted/stale records, bounded
visibility lag under the declared workload, and no unreported loss of the
quality gate. A failed or incomplete lifecycle run is recorded as
`PENDING_SOURCE_REPLAY` or `INCONCLUSIVE`, not silently omitted.

### Planned operational gates

| Item | Milestone | Dependencies | Minimum benchmark | Acceptance criterion | Risk | Status |
|---|---|---|---|---|---|---|
| Matched external retrieval comparison (USearch/FAISS/other) | M1+ | exact oracle, frozen corpus/qrels, `ComparisonParityManifest` | exact vs candidate index at 3+ quality points; kernel and embedded rows | quality curves, complete parameter manifest, and no quality mismatch at the selected operating point | unfair tuning or hidden server overhead | Planned |
| Ingestion and update visibility | M1+ | lifecycle contract, durable storage mode, index publication signal | bulk build plus incremental insert/update/delete with 4 timestamp boundaries | durable-commit and search-visible latencies reported separately; ACK semantics explicit | conflating acknowledgement with readiness | Planned |
| Mixed search/update/rebuild | M2 | index lifecycle, tombstone/stale-generation checks, restart harness | pre-registered concurrent workload with rebuild and post-restart replay | query/write p95/p99, backlog and visibility lag within declared limits; zero stale/deleted resurrection | scheduler or cache effects hide correctness failures | Roadmap only |

The current compressed-native gate remains the priority; these operational
comparisons do not promote an external vector store or change the codec
selection by themselves.

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

## Minimal benchmark matrix

```text
exact scan -> F16/int8/binary/PQ or other codec -> HNSW/ANN -> hybrid -> rerank
```

The matrix is run first on a deterministic synthetic fixture and then on a
versioned local corpus. Held-out or multilingual slices are separate gates,
not silently pooled into the primary score.
