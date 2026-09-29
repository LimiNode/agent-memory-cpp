# Retrieval Execution Roadmap

This guide specifies the physical execution contract for retrieval. It is a
roadmap, not a new public C++ API. The core remains deterministic and usable
without an LLM or a remote vector service.

## Architectural boundary

The first-party embedded profile uses MDBX as the canonical owner of memory
records, revisions, visibility, tombstones and provenance. The storage
contracts remain backend-independent, so a host-managed SQLite, PostgreSQL or
other adapter may replace MDBX only after satisfying the full canonical-storage
conformance contract defined in `architecture.md`, including publication,
frontier, recovery, migration, backup/restore, authorization and provenance
gates. Vector, lexical and routing indexes are
revision-bound rebuildable projections, even when their bytes are stored in
MDBX. An external vector service is a derived-index adapter by default.

The executor is an orchestration component, not a second storage engine. It
must hydrate and validate every candidate against the canonical frontier before
returning it.

## Logical plan, physical lowering and runtime context

`RetrievalPlan` is the existing logical policy and budget value contract (owned
by the retrieval/memory-stack roadmap). It describes requested scopes,
retrievers, limits, filters and budget-exhaustion actions. This guide does not
introduce a second independent `RetrievalExecutionPlan` value.

The planned lowering is:

```text
RetrievalPlan
  -> validation and lowering
  -> ResolvedRetrievalExecutionPlan
       + pinned ReadFrontier
       + pinned ProjectionVersionRef/index generations
       + physical batches, queues and covering profile
  -> RetrievalExecutionContext
       + execution_id/trace
       + deadline and cancellation state
```

`ResolvedRetrievalExecutionPlan` is an internal, reproducible snapshot of the
logical plan. Cancellation and deadline state belong to the runtime context,
not to the value plan. The resolved plan is not a public class until an ADR
fixes ownership and ABI.

## Snapshot and execution pipeline

```text
validate logical plan and access policy
  -> pin one ReadFrontier and all projection/index generations
  -> lower routes to bounded source/cluster batches
  -> batch-read candidate IDs and compact score data
  -> retain candidate provenance and canonical unit identity
  -> bounded top-N selection with deterministic ties
  -> batch-read payloads for survivors
  -> require row.index_generation == pinned generation
  -> hydrate at the pinned frontier; drop tombstones/superseded revisions
  -> exact rerank only in one compatible representation
  -> stable top-K result with provenance
```

Publication of a newer generation affects subsequent requests only. It does
not invalidate the generation already pinned by an in-flight execution, and a
row from another generation must never be merged opportunistically.

The pipeline may overlap candidate scanning, payload reads and scoring, but
each queue is bounded. Backpressure pauses producers; it must not silently
reduce route fan-out or adaptive recall. If a budget or deadline prevents more
work, the existing `BudgetExhaustionAction` is applied and the exact existing
enum values are recorded. Query-level `RetrievalCompletion` has
`Complete`, `Partial`, `BudgetExhausted`, `RouteDropped` and
`RequiredRouteFailed`; per-route `RetrievalRouteCompletion` has
`Complete`, `Partial`, `Unavailable`, `BudgetExhausted`, `Dropped` and
`RequiredRouteFailed`.

## Reader and covering contracts

Future readers should expose batch operations equivalent to:

- `read_candidates(keys, limit)` in a resolved generation context;
- `read_payloads(const std::vector<KnowledgeUnitRef>& refs, ProjectionSpec)`;
- `read_covering_rows(keys, covering_level)` in that same context.

Generation and frontier are properties of the resolved reader/snapshot
context, not loose repeated arguments on every payload call. A minimum
covering row contains the canonical unit reference, exact projection/revision
identity, pinned index generation, codec/space identity and approximate score
inputs. Optional levels may add compressed vector codes or a short text
fragment. Full embeddings and full text are opt-in because copying them into an
index can dominate storage.

## Identity, deduplication, ties and deletion

Every candidate retains its exact source revision and projection version for
provenance. Hydration validates that binding against the pinned canonical
frontier. Superseded or stale revisions are dropped. Final deduplication uses
the canonical logical result identity (`KnowledgeUnitId`, `KnowledgeUnitRef`,
or the profile's declared equivalent), not a generic `(record_id, revision)`
pair; repeated cluster hits for one logical unit therefore produce one result.
If equal scores remain, order by the canonical identity and declared revision
tie rule so sequential and pipelined executors produce identical results.
Tombstones are checked during candidate admission and again during hydration.

## Cache contract

Centroids, routing metadata and immutable covering blocks may be cached under:

```text
index_id + index_generation + configuration_digest
  + model_or_projection_revision + preprocessing_or_input_template_digest
```

Never reuse a cache entry solely by path or model name. Cache misses and
invalidations are expected on generation changes; correctness must not depend
on cache warmth.

## Required tests and benchmark

The first implementation gate is a fake-reader suite covering empty results,
duplicate logical identities, an incomplete final batch, tombstones, stale
generations, equal-score ties, cancellation, queue backpressure and budget
exhaustion actions. Then compare sequential and overlapped execution on the
same immutable index, pinned frontier and query set. Record candidate and
hydrated counts, stale/tombstone rejections, queue high-water marks, bytes
read, p50/p95/p99 and exact-oracle quality. A throughput improvement is
admissible only at matched quality and matched result semantics.

## Milestones, dependencies and risks

| Milestone | Dependency | Minimum evidence | Acceptance | Risk |
|---|---|---|---|---|
| E1 plan/reader contract | `RetrievalPlan`, canonical frontier and generation rules | fake reader + malformed-row tests | no stale, superseded or duplicate logical result survives | API churn |
| E2 bounded executor | E1, batch-capable canonical reader | sequential vs overlapped replay | equal top-K, pinned snapshot and bounded memory | queue deadlock |
| E3 covering profiles | E2, storage-size accounting | id-only/code/partial profiles | lower payload I/O without quality loss | index bloat |
| E4 production-shaped benchmark | E2/E3, lifecycle fixtures | query/write/rebuild overlap | published quality, visibility and tail metrics | machine-specific wins |

### Reference note

The execution pattern is informed by the public article
<https://habr.com/ru/articles/1072032/>: specialised executors, batch reads,
covering data and bounded top-K state are useful hypotheses. Its reported
speedups are not project evidence or acceptance thresholds. Any comparison here
must pin the article's benchmark revision, quality protocol, hardware and
result shape, then rerun under this project's exact-oracle contract.
