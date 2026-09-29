# Structured and AI-Augmented Retrieval Roadmap

Status: `Roadmap only`. This is an optional extension of retrieval, not a
change to the embedded-memory core and not a general SQL engine or theorem
prover.

## Boundary and storage profiles

The first-party embedded memory profile keeps canonical records, revisions,
provenance and visibility in MDBX. Core storage and retrieval contracts remain
backend-independent. A host may select an adapter backed by SQLite,
PostgreSQL, or another SQL system when structured data is the primary source or
when deployment needs a host-managed database. Such an adapter must expose a
read frontier, typed row identity, provenance and read-only/authorization
policy; it does not silently create a second canonical memory store. SQL is a
structured retrieval source by default. Replacing MDBX as canonical storage
requires the complete canonical-storage conformance contract: atomic
publication, revision/read-frontier semantics, tombstones, durability and
recovery, concurrent snapshots, schema migration, backup/restore,
derived-index lifecycle, compaction/retention, authorization and provenance.

SQLite is therefore a useful SQL/lexical and AI-augmented-search backend when
vector search is unnecessary, while MDBX remains the default embedded vector
and memory profile. PostgreSQL is an optional host-managed production adapter.
External vector stores remain derived-index adapters unless a separate storage
contract explicitly makes another backend canonical.

## Logical and physical plans

Structured retrieval is a route in the existing logical `RetrievalPlan`, not a
second retrieval API:

```text
RetrievalPlan
  -> StructuredRoute
       -> LogicalStructuredPlan
       -> validation (schema, authority, read-only, budget)
       -> PhysicalStructuredPlan
            -> SQL/relational pushdown
            -> lexical/vector candidate routes
            -> optional typed AI operators
       -> bounded execution and ContextBlock/provenance projection
```

The initial relational operators are `Scan`, `Project`, `Filter`, `Join`,
`Aggregate`, `Sort` and `Limit`. Physical plans must expose explainable
operator boundaries, row/byte/time limits and the source revision/frontier
used for each result.

## Optional typed AI operators

AI operators are derived, bounded computations over rows or candidate pairs;
they are not authority and they never execute side effects:

- `AI.IF` — typed predicate with `true`, `false`, `unknown` or `needs_review`;
  `ambiguous` and `conflict` are evidence qualifiers attached to the typed
  result, not additional boolean outcomes;
- `AI.SCORE` — typed numeric score with metric/model/provenance receipt;
  calibration is present only through an explicit `CalibrationRef` and
  calibration evidence, never implied by the word "score";
- `AI.RERANK` — bounded candidate reranking;
- `AI.JOIN` — bounded pair generation after deterministic relational filters.

The provider boundary is capability-oriented (`ICompletionProvider`,
`IEmbedder`, `IRerankerProvider`, `ITokenizerProvider`). Remote APIs and
embedded runtimes are interchangeable infrastructure adapters. LlamaLib,
llama.cpp, compatible forks and other runtimes are references/optional
adapters, never core dependencies or public storage types.

AI output is parsed into a typed result, validated against schema and policy,
and recorded as derived evidence. An optional natural-language frontend may
propose a read-only typed plan, but it cannot emit arbitrary SQL, mutate data,
call tools, or bypass authorization. Execution remains outside storage
transactions; only a validated result is committed through the ordinary
admission path.

## Bounded execution rules

- Apply deterministic schema, scope, authorization, time and relational
  filters before AI work.
- Bound rows, candidate pairs, bytes, model tokens, wall time and attempts.
- Prefer batch predicate evaluation and shared-document/prefix reuse when the
  provider advertises those capabilities; keep a stateless fallback.
- Preserve `unknown` and `needs_review` outcomes and attach
  `ambiguous`/`conflict` evidence qualifiers rather than coercing either into a
  boolean answer.
- Record provider execution status separately from semantic outcome.
- Keep SQL/AI calls outside MDBX transactions and revalidate the pinned
  frontier before materializing context.

## Milestone ladder

| Milestone | Dependency | Minimum evidence | Acceptance | Risk |
|---|---|---|---|---|
| S0 structured source contract | canonical storage/read-frontier contracts | fake catalog, typed rows and provenance fixtures | no untyped or cross-frontier row reaches retrieval | schema drift |
| S1 SQL adapters | S0, parameterized read-only interface | SQLite fixture, PostgreSQL compatibility plan | pushdown preserves identity, limits and visibility | backend divergence |
| S2 relational plan | S0/S1 | logical-to-physical explain replay | deterministic bounded plan and stable results | planner complexity |
| S3 structured retrieval route | `RetrievalPlan`, context projection | mixed lexical/vector/structured fixture | provenance and `RetrievalCompletion` remain intact | result-shape drift |
| S4 typed AI operators | provider contracts and fake provider | batch, timeout, abstention and receipt tests | no provider call inside storage transaction | semantic overclaim |
| S5 bounded AI joins | S4, deterministic prefilters | pair-explosion and anchor fixtures | explicit pair/byte/token caps | combinatorial cost |
| S6 cost-aware planner | S2-S5 | explain/analyze and selectivity replay | operator ordering improves cost at matched quality | unstable estimates |
| S7 query-aware inference | provider capability manifests | shared-prefix vs stateless benchmark | optimization is optional and semantically equivalent | cache invalidation |
| S8 optional NL-to-plan | S2, schema/authority validator | adversarial read-only plan tests | invalid/mutating plans fail closed | prompt injection |

This roadmap is informed by Quail-style separation of logical semantics,
physical execution and typed AI operators. Quail itself is not a dependency or
an implementation oracle for this project.
