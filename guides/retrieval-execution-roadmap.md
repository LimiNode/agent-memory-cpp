# Retrieval Execution Roadmap

This guide specifies the physical execution contract for retrieval. It is a
roadmap, not a new public C++ API. The core remains deterministic and usable
without an LLM or a remote vector service.

## Architectural boundary

MDBX (or another configured canonical backend) owns documents, revisions,
embeddings, tombstones and provenance. Vector, lexical and routing indexes are
rebuildable derived projections. A retrieval executor may read several derived
indexes, but it must hydrate and validate candidates against the canonical
revision before returning them.

The executor is an orchestration component, not a second storage engine. A
SQLite or external-vector adapter may implement the reader contracts, but it
does not become canonical merely because it is used for retrieval.

## Planned execution plan

The planned `RetrievalExecutionPlan` is a host-neutral value describing one
bounded search:

- scope and active projection/index generations;
- candidate sources and routing depth (`top_clusters`, `max_candidates`);
- final `top_k`, rerank metric and tie policy;
- candidate and payload batch sizes, maximum in-flight bytes and queue depth;
- deadline, cancellation token and maximum work units;
- covering level (`id_only`, `code`, `partial_payload`, or `full_payload`);
- whether approximate scores may be used before canonical hydration.

The name is intentionally planned. It must not be treated as an implemented
class until an ADR fixes its ownership and ABI.

## Execution pipeline

```text
validate query and active generations
  -> route to bounded source/cluster set
  -> batch-read candidate IDs and compact score data
  -> deduplicate by (record_id, revision)
  -> keep a bounded top-N heap with deterministic tie ordering
  -> batch-read payloads for survivors
  -> reject tombstones and stale generations
  -> exact score/rerank in one compatible space
  -> stable top-K result with provenance
```

The pipeline may overlap candidate scanning, payload reads and scoring, but each
queue is bounded. Backpressure must reduce fan-out or pause producers; it must
not silently spill an unbounded candidate list to memory.

## Reader and covering contracts

Future readers should expose batch operations equivalent to:

- `read_candidates(keys, limit, generation)`;
- `read_payloads(record_ids, revision, projection)`;
- `read_covering_rows(keys, generation, covering_level)`.

The minimum covering row contains `record_id`, `revision`, `index_generation`,
codec/space identity and the approximate score inputs. Optional levels may add
compressed vector codes or a short text fragment. Full embeddings and full
text are opt-in because copying them into an index can dominate storage.

Every row carries the generation it was built from. A row from an older
generation is a stale miss, not a candidate that can be merged opportunistically.

## Dedupe, ties and deletion

Deduplication happens before final reranking. The identity key is
`(record_id, revision)`; the same record in two clusters is not two results.
If equal scores remain, order by immutable `record_id` (and then revision) so
sequential and pipelined executors produce identical results. Tombstones and
deleted revisions are filtered after hydration as well as during index scans.

## Cache contract

Centroids, routing metadata and immutable covering blocks may be cached under:

```text
index_id + index_generation + embedding_model_revision + preprocessing_hash
```

Never reuse a cache entry solely by path or model name. Cache misses and
invalidations are expected on generation changes; correctness must not depend
on cache warmth.

## Required tests and benchmark

The first implementation gate is a fake-reader test suite covering empty
results, duplicate IDs, an incomplete final batch, tombstones, stale
generations, equal-score ties, cancellation and bounded-queue backpressure.
Then compare sequential and overlapped execution on the same immutable index,
hardware and query set. Record candidate count, hydrated count, stale/tombstone
rejections, queue high-water marks, bytes read, p50/p95/p99 and exact-oracle
quality. A throughput improvement is admissible only at matched quality and
matched result semantics.

## Milestones, dependencies and risks

| Milestone | Dependency | Minimum evidence | Acceptance | Risk |
|---|---|---|---|---|
| E1 plan/reader contract | canonical revision and index-generation rules | fake reader + malformed-row tests | no stale or duplicate result survives | API churn |
| E2 bounded executor | E1, batch-capable MDBX reader | sequential vs overlapped replay | equal top-K and bounded memory | queue deadlock |
| E3 covering profiles | E2, storage-size accounting | id-only/code/partial profiles | lower payload I/O without quality loss | index bloat |
| E4 production-shaped benchmark | E2/E3, lifecycle fixtures | query/write/rebuild overlap | published quality, visibility and tail metrics | machine-specific wins |

### Reference note

The discussed vector-search execution article is useful as a hypothesis about
specialised executors, batch reads, covering data and bounded top-K state. Its
reported speedups are not project thresholds: the exact public URL, benchmark
revision, recall protocol and hardware must be pinned before using it as a
citation or comparison. The article does not change the canonical MDBX and
derived-index boundary above.
