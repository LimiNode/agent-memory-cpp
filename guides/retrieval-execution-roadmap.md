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
`Complete`, `Partial`, `BudgetExhausted`, `Dropped` and
`RequiredRouteFailed`; per-route `RetrievalRouteCompletion` has
`Complete`, `Partial`, `Unavailable`, `BudgetExhausted`, `Dropped` and
`RequiredRouteFailed`.

### Filter-aware sufficiency and bounded refill

`candidate_limit` is an input budget, not a promise that a filtered query can
return `target_k` results. The executor must expose the following bounded
loop:

```text
target_k
  -> candidate generation
  -> access/metadata filtering and stale/tombstone removal
  -> deduplicate by canonical identity
  -> sufficiency check
       -> Complete
       -> bounded refill from the source
       -> BudgetExhausted / Partial
```

The trace records `generated`, `rejected_by_access`,
`rejected_by_metadata`, `survived`, `refill_rounds` and `visited_candidates`.
Refill consumes the same posting, I/O, deadline and rerank budgets; it must not
silently become an unbounded scan. Acceptance requires a locked selective
fixture with correct `Complete`/`BudgetExhausted` semantics and deletion and
stale-generation filtering.

## Hybrid route eligibility, pools and fusion

This section tightens the execution contract for the existing logical
`RetrievalPlan`, `RetrievalTrace` and `RetrievalHit` records. It does not add a
second retrieval plan, hit type or tracing system. The logical ownership and
current RRF contract remain in
[`knowledge-base-roadmap.md`](knowledge-base-roadmap.md); this guide specifies
how an executor lowers that contract into eligible route-local work.

### Hard constraints and the filter frontier

The executor normalizes strict parts of `RetrievalPlan` into one execution-time
**filter frontier**. `FilterFrontier` is a contract term for that resolved
eligibility boundary, not a new public C++ value type. Every candidate route in
one execution receives the same frontier identity and canonical read frontier
when the operation requires a consistent view.

Typical hard constraints are:

| Constraint | Default | Contract |
|---|---|---|
| Scope, tenant, owner namespace | Hard | A candidate outside the requested scope is ineligible. |
| Authorization and visibility | Hard, fail closed | A missing, stale or unavailable allow decision cannot become an allow. |
| Lifecycle, tombstone and active revision | Hard | A stale or superseded record is not a current hit. |
| Explicit temporal bound | Hard when present | The candidate's authoritative validity interval must satisfy the bound. |
| Source type, language, workspace/project | Profile-declared | Hard when the caller declares it as an eligibility requirement; otherwise it may remain a ranking signal. |
| Arbitrary metadata predicate | Profile-declared | The planner must preserve exact predicate semantics even when physical pushdown is unavailable. |

The semantic requirement is independent of physical implementation:

```text
RetrievalPlan strict predicates
        -> FilterFrontier
        -> eligible retrieval universe
        -> lexical / dense / structured / graph routes
```

A backend may implement the frontier with a scope or index prefix, partition,
secondary index, bitmap/set intersection, precomputed filter column or another
exact mechanism. High-cardinality partitioning is a planner and benchmark
decision, not a requirement of the contract. A post-filter is acceptable only
when it is exact and complete for the requested universe, or when a conservative
superset is deliberately followed by canonical validation without pretending
that the candidate budget preserved recall.

Authorization and visibility have an additional fail-closed rule:

> An unauthorized candidate must not become visible merely because one route
> retrieved it.

The executor may repeat a final visibility check as defense in depth, but a
post-filter after a small top-N candidate pool does not satisfy eligibility. The
authoritative decision is evaluated at the same canonical frontier as the
candidate set; denied unit identities and claims are not written to the normal
trace. This is a retrieval boundary, not a general authorization framework.

### Route-local candidate pools

Each enabled route gets an explicit bounded pool in the resolved execution
plan. A compatible plan may contain, for example:

```text
LexicalRoute     -> N_lex
DenseRoute       -> N_dense
StructuredRoute  -> N_struct
GraphRoute       -> N_graph
```

The existing `RetrievalPlan.candidate_pool_size` remains a profile/default
compatibility value. It is not a claim that every route should use one common
number. Per-route budgets are independently configurable, consume the declared
I/O/deadline budget, and are part of experiment identity. A route records its
branch id, route/index/projection generation, frontier digest, budget and
deterministic tie rule together with each admitted candidate.

Lexical, dense and structured routes produce their own pools before fusion. A
dense pool must not become a prefilter for BM25, and a lexical pool must not
become a prefilter for dense retrieval, because either cascade can remove exact
identifiers or semantically relevant items before the complementary route has a
chance to see them. A shared strict `CandidateSet` is allowed before both
routes only when it represents the exact same eligibility frontier described
above.

The central diagnostic invariant is:

> Fusion cannot recover a relevant item that no candidate route admitted to its
> pool.

Candidate budgets therefore remain visible in traces and evaluation rather than
being hidden behind the final `limit`.

### Fusion contract

The first deterministic reference is the existing `FusionStrategy::RRF`.
Reciprocal Rank Fusion combines ranks without pretending that BM25, cosine or
structured scores share a calibrated numeric scale:

```text
score(unit) = sum over routes r:
    weight_r / (rrf_k + rank_r(unit))
```

The canonical fusion rank is **one-based**: the first candidate admitted by a
route has `rank_r = 1`. The existing `RetrievalHit::rank` default of `0` is an
unassigned sentinel before route lowering; a zero rank must never enter RRF or
be serialized as a valid branch rank. Rank assignment and tie handling are part
of the route trace so an evaluation replay cannot silently switch to zero-based
positions.

The default `rrf_k` and profile weights stay owned by
`knowledge-base-roadmap.md`; this guide requires the resolved execution identity
to include the algorithm, `rrf_k`, route weights, route identities, candidate
budgets and tie-breaking policy. RRF is a baseline, not a universal optimum.
Weighted normalized scores, query-dependent routing and learned fusion remain
future alternatives and require calibration evidence under the same qrels and
candidate budgets before becoming a default.

Only candidates that pass the route's frontier and generation checks may enter
fusion. Fusion deduplicates by the existing canonical logical identity, keeps
per-route ranks and scores for explainability, and applies a deterministic tie
rule before the final limit and any optional reranker. An optional reranker is a
later stage; it does not erase the branch-pool or fusion diagnostics.

### Loss tracing and diagnostics

The existing `RetrievalTrace`, `ProjectionRouteTrace` and
`retrieval-explainability-roadmap.md` remain the owners. They should expose
stage evidence sufficient to distinguish at least:

```text
ExcludedByHardFilter
MissedByLexicalBranch
MissedByDenseBranch
MissedByStructuredBranch
CandidateBudgetTruncation
BudgetAttributionUnknown
LostDuringFusion
LostDuringRerank
FinalLimitTruncation
```

These are diagnostic categories, not a second required enum until an API
decision records their exact spelling. For each route, the trace or its
profile-approved aggregate must identify candidate count, branch rank and
score/distance, candidate budget, frontier identity, projection/index
generation and completion outcome. The fusion portion records contributing
branch ranks, the fused score and the final rank. A profile may redact candidate
identities, but it must retain enough aggregate information to reproduce the
loss category without exposing denied units.

The trace must distinguish a legitimate hard-filter exclusion from an
unfiltered branch miss. It must also distinguish a miss caused by a pool that
was too small from a candidate that reached fusion and lost there. This is the
execution counterpart of the stable explain shape in
[`retrieval-explainability-roadmap.md`](retrieval-explainability-roadmap.md).

### Evaluation decomposition

Existing aggregate Recall, MRR and nDCG metrics remain valid. The hybrid gate
adds stage metrics over the same query set, qrels, frontier and canonical
hydration oracle:

| Metric | Meaning |
|---|---|
| `branch_recall@N` | Relevant eligible items present in one route's pool of size `N`. |
| `candidate_union_recall` | Recall after the union of all route pools, before fusion. |
| `filtered_eligible_recall` | Recall relative to items that satisfy the hard frontier, not all judged source items. |
| `fusion_recall@K` | Recall after fusion and before reranking/final hydration limit. |
| `reranked_recall@K` | Recall after an optional reranker at a declared depth. |
| `final_recall@K` | Recall of the returned, hydrated top-K. |
| `candidate_budget_loss` | Relevant eligible items absent at the normal route budget but present in the declared counterfactual control route. |
| `budget_attribution_unknown` | Relevant eligible items absent from the normal route when the control route cannot establish whether the miss was budget-related. |
| `filter_exclusion_count` | Items rejected by each hard constraint, kept as aggregate counts. |
| `fusion_loss` | Items present in the candidate union but absent after fusion at the declared cutoff. |

The evaluation report must say whether a relevant item was never found, was
excluded by the frontier, was truncated by a route budget, or was lost after
fusion. A normal route alone cannot prove that an absent item would have ranked
below its pool limit, so `CandidateBudgetTruncation` is not inferred from
absence alone. It must use the counterfactual control contract below; otherwise
the result is `BudgetAttributionUnknown`.

### Counterfactual route control for budget attribution

Gate H0 must define an independently justified deeper route for each branch:

```text
normal route:  candidate budget N
control route: N_control >> N, or an exhaustive/exact eligible-set oracle
```

Both routes use the same query, `FilterFrontier`, projection/model generation,
read frontier and deterministic tie policy. The control route is evaluation
work only; it does not change the production result or silently grant an
unbounded runtime budget.

For a judged eligible item `u`:

```text
u absent at N, present at N_control -> CandidateBudgetTruncation
u absent at N_control               -> BranchMiss (within control coverage)
control incomplete or incomparable   -> BudgetAttributionUnknown
```

If a branch cannot provide a justified control, the report may still publish
ordinary branch recall, but it must not label every normal-route miss as
`candidate_budget_loss`. The control receipt records its depth or exact-oracle
method, eligible-set coverage, additional candidate work and any remaining
unknown attribution.

### Compact retrieval keys and MDBX publication

Posting lists and route-local candidate tables may use a compact numeric key for
space and cursor efficiency. The existing `KnowledgeUnitId`/`KnowledgeUnitRef`
and source/provenance identities remain the domain truth; a compact posting key
is an internal mapping with deterministic reverse resolution and no independent
citation meaning. This does not create a new durable identifier family.

For the first-party MDBX profile, scope-aware metadata, lexical postings and
domain/filter rows that must agree with a canonical publication should be
updated in one backend-owned transaction where the storage contract permits it.
Approximate vector/ANN structures may instead be generation-built and published
as a whole. Their rebuild or publication boundary must be recorded in the
projection generation and pinned by the executor; it does not require an
in-place mutable ANN update in the canonical transaction. Reads use the same
`ReadFrontier`/generation pairing already required by the execution pipeline.
See [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) for
the backend boundary and [`canonical-content-storage-roadmap.md`](canonical-content-storage-roadmap.md)
for canonical publication and reindexing semantics.

### Ownership, maturity and future gate

| Capability | Current status | Owner or next step |
|---|---|---|
| `RetrievalPlan`, `RetrievalHit`, `RetrievalTrace`, RRF vocabulary | Contract only | `knowledge-base-roadmap.md`; no complete hybrid backend is implied. |
| Pinned frontier, bounded refill and generation-aware reader | Roadmap only | This guide; implement with the E1–E4 execution milestones. |
| Hard-filter semantics, route-local pools and loss categories | Contract | This guide plus the existing trace/explainability owners. |
| MDBX-native filter/lexical publication versus generation-built ANN | Roadmap only | Storage integration and projection lifecycle implementation. |
| Gate H0 — hybrid retrieval/fusion | Research candidate | Freeze corpus, qrels, filter selectivity and route budgets before implementation claims. |

Gate H0 is deliberately not run by this docs slice. It should compare a
lexical-only route, dense-only route, the candidate union, RRF and one calibrated
alternative under identical hard filters and budgets. Its receipt must include
branch recall, a declared deeper/exhaustive control for budget attribution,
candidate-budget loss versus unknown attribution, fusion loss, authorization
fail-closed fixtures, p50/p95/p99 latency and candidate-work accounting. A small
blog or vendor benchmark may motivate the protocol, but cannot supply
acceptance numbers for this project.

The design is informed by the official YDB
[hybrid-search concept](https://ydb.tech/docs/en/concepts/query_execution/hybrid_search?version=main),
the YDB [hybrid query contract](https://ydb.tech/docs/en/dev/hybrid-search?version=main)
and its [filtered vector-index guidance](https://ydb.tech/docs/en/dev/vector-indexes-kmeans-tree-type?version=main).
Those sources support the hypotheses that branch pools, rank fusion and
filter-aware candidate generation should be explicit. The related
[YDB/Habr article](https://habr.com/ru/companies/ydb/articles/1087208/) is a
secondary architectural precedent only; no YDB API, physical index scheme or
reported benchmark number is adopted here.

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
