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

SQLite has two distinct roles in the wider architecture. The canonical storage
roadmap owns the first-class alternative-backend decision and the sequencing of
the `ConversationStore` conformance profile. This roadmap only defines the
second role: SQLite may be an external SQL data source queried by the optional
StructuredRoute; that role does not make the foreign database the canonical
memory store. The storage decision and backend conformance boundary are owned
by `canonical-content-storage-roadmap.md`, which is maintained as a separate
storage/content roadmap.
MDBX remains the default embedded vector and memory profile. PostgreSQL is an
optional host-managed production adapter. External vector stores remain
derived-index adapters unless a separate storage contract explicitly makes
another backend canonical.

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

- `AI.IF` — typed Boolean/Choice predicate whose declared semantic result may
  include an option such as `unknown`; `needs_review` is a host disposition,
  not a provider answer. `ambiguous` and `conflict` are evidence qualifiers
  attached to the result;
- `AI.SCORE` — typed numeric score with metric/model/provenance receipt;
  calibration is present only through an explicit `CalibrationRef` and
  calibration evidence, never implied by the word "score";
- `AI.RERANK` — bounded candidate reranking;
- `AI.JOIN` — bounded pair generation after deterministic relational filters.

The provider boundary is capability-oriented (`ICompletionProvider`,
`IEmbedder`, `IRerankerProvider`, `ITokenizerProvider`). A bounded semantic
predicate or classification must use a generic `ISemanticDecisionProvider`,
not a SQL-specific or JEV-specific interface. A JEV-like evaluator can be one
adapter alongside a local classifier, HTTP provider or recorded/fake provider.
Remote APIs and embedded runtimes are interchangeable infrastructure adapters.
LlamaLib, llama.cpp, compatible forks and other runtimes are
references/optional adapters, never core dependencies or public storage types.

Exact deterministic predicates run in the planner before semantic inference;
they are not provider calls. `ISemanticDecisionProvider` returns typed
evidence and a separate invocation outcome. The four axes are:

1. **Question kind** — Boolean, Choice or OrdinalScore.
2. **Result evidence** — selected option, declared distribution, ordinal value
   or score, plus provider-reported confidence only as provider evidence.
3. **Provider invocation outcome** — `Answered`, `Abstained`, `Unsupported`,
   `TimedOut`, `Malformed`, `Stale` or `Failed`.
4. **Host/query disposition** — accept, reject, retry, needs review,
   escalate/fallback or another policy-owned action.

`Unknown` is a semantic option only when the `QuestionProfile` declares it.
`Abstained` and `Unsupported` are invocation outcomes, not synonyms for that
option. `NeedsReview` is normally host policy, not a model answer. This
capability is useful to `AI.IF`, bounded `AI.SCORE`, joins and pairwise-rerank
profiles. Continuous/model-native scoring and ordinary reranking retain their
dedicated provider contracts such as `IRerankerProvider`; this interface is
not a generic scorer or reranker and is not a requirement for the canonical
memory profile.

AI output is parsed into a typed result, validated against schema and policy,
and recorded as derived evidence. An optional natural-language frontend may
propose a read-only typed plan, but it cannot emit arbitrary SQL, mutate data,
call tools, or bypass authorization. Execution remains outside storage
transactions; only a validated result is committed through the ordinary
admission path.

When a semantic decision is worth caching, it is a derived
`SemanticDecisionProjection`, not an update to the source row. It records the
question/profile digest, provider and model revision, input/frontier digest,
result evidence, invocation outcome, evidence qualifiers and creation/expiry
policy. It intentionally does not contain host-policy interpretation. A source
revision invalidates only projections whose input frontier intersects the
changed row or candidate set. The projection may be materialized in MDBX,
SQLite or another derived-index backend under the same provenance contract.

Host interpretation is a separate `HostDispositionRecord` or query-trace
entry. It references the semantic projection, exact host-policy identity and
revision/digest, disposition, evaluation time and the query frontier. A policy
revision can therefore reinterpret reusable provider evidence without
re-invoking the provider; disposition records are invalidated or superseded by
policy changes independently of the semantic projection.

## Bounded execution rules

- Apply deterministic schema, scope, authorization, time and relational
  filters before AI work.
- Bound rows, candidate pairs, bytes, model tokens, wall time and attempts.
- Prefer batch predicate evaluation and shared-document/prefix reuse when the
  provider advertises those capabilities; keep a stateless fallback.
- Preserve a declared semantic `unknown` option separately from provider
  abstention/unsupported/timeout outcomes; attach `ambiguous`/`conflict`
  evidence qualifiers rather than coercing any of them into a boolean answer.
- A host-policy change may recompute disposition from a reusable semantic
  projection when its source frontier is still valid; it must not silently
  present the old disposition as current under the new policy.
- Record provider execution status separately from semantic outcome.
- Snapshot candidate rows and the source frontier in a bounded storage read;
  close the transaction before calling a provider; then revalidate the pinned
  frontier before materializing context or publishing a
  `SemanticDecisionProjection`.
- Never call JEV, an LLM or a remote provider from inside an MDBX/SQL storage
  transaction. A stale frontier produces a retry or an explicitly stale
  derived result according to policy.

## Milestone ladder

| Milestone | Dependency | Minimum evidence | Acceptance | Risk |
|---|---|---|---|---|
| S0 structured source contract | canonical storage/read-frontier contracts | fake catalog, typed rows and provenance fixtures | no untyped or cross-frontier row reaches retrieval | schema drift |
| S1 SQL adapters | S0, parameterized read-only interface | SQLite fixture, PostgreSQL compatibility plan | pushdown preserves identity, limits and visibility | backend divergence |
| S2 relational plan | S0/S1 | logical-to-physical explain replay | deterministic bounded plan and stable results | planner complexity |
| S3 structured retrieval route | `RetrievalPlan`, context projection | mixed lexical/vector/structured fixture | provenance and `RetrievalCompletion` remain intact | result-shape drift |
| S4 typed AI operators | provider contracts and fake provider | batch, timeout, abstention, typed-outcome and projection-receipt tests | no provider call inside storage transaction; stale frontier is handled explicitly | semantic overclaim |
| S5 bounded AI joins | S4, deterministic prefilters | pair-explosion and anchor fixtures | explicit pair/byte/token caps | combinatorial cost |
| S6 cost-aware planner | S2-S5 | explain/analyze and selectivity replay | operator ordering improves cost at matched quality | unstable estimates |
| S7 query-aware inference | provider capability manifests | shared-prefix vs stateless benchmark | optimization is optional and semantically equivalent | cache invalidation |
| S8 optional NL-to-plan | S2, schema/authority validator | adversarial read-only plan tests | invalid/mutating plans fail closed | prompt injection |

This roadmap is informed by Quail-style separation of logical semantics,
physical execution and typed AI operators. Quail itself is not a dependency or
an implementation oracle for this project.
