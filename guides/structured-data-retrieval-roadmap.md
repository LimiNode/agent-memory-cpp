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

## Tabular source profile (TSV/CSV)

Delimited text is a structured source, not merely a long text file to split
into ordinary chunks. TSV and CSV ingestion is therefore a planned tabular
profile of this roadmap. It is not implemented by the embedded core and does
not change the M0 text-only importer contract.

The profile keeps one canonical, revisioned table representation and derives
search-oriented views from it:

```text
canonical TSV/CSV artifact + source revision
        -> TabularRepresentation (dialect, schema, raw rows, row identity)
        -> TableSemanticProjection (name, purpose, columns, units, examples)
        -> structured index (keys, typed values, filters, ranges)
        -> lexical projection (headers, field names, rare values, descriptions)
        -> optional row/column projections when a row has substantive text
```

The table-level semantic projection is the default discovery route. A row is
not converted into an independent embedding by default: for large numerical
or measurement tables that creates unnecessary cost and weakens exact
structured execution. Row-level or column-level semantic projections are an
opt-in derived index for tables whose records contain meaningful natural
language or when a table-level recall evaluation demonstrates a need.

The minimum source contract is:

- preserve the original UTF-8 bytes and source revision/digest;
- record the delimiter/dialect, header policy, quoting and escaping rules,
  newline convention, encoding decision and empty-value policy;
- keep schema declarations (when supplied) alongside inferred types and the
  original string value; inference is a proposal and must never silently turn
  identifiers such as `001234` into `1234`;
- assign a stable row key when the source provides a primary key, otherwise
  use a revision-bound row identity rather than treating a physical line
  number as a durable identity;
- retain a locator for every row and cell: source revision, row key or ordinal,
  column identity and, where available, byte span; and
- expose bounded streaming reads, typed filters/ranges and exact row/cell
  hydration without loading the complete file into memory.

`TableSemanticProjection` is derived evidence. It may contain the table name,
purpose, schema summary, units, representative values and a provenance link to
the canonical table revision, but it is never an authority for numeric values.
Answers requiring a value, filter, join or aggregate execute against the
structured representation and return the exact row/cell citations. Embeddings
remain rebuildable indexes and must not replace canonical values or
provenance.

Updates are revisioned. A schema or row-key change invalidates the affected
structured and semantic projections; a content-only change invalidates the
canonical row, the dependent structured indexes and only those semantic views
whose input frontier intersects the changed content. Rebuilding an entire
table index for every changed row is not a requirement of this profile.

The first public structured-data release profile is a bounded TSV/CSV source
profile under M1b Structured Knowledge. It must establish deterministic
parsing, schema and locator receipts, table discovery, exact row/cell
retrieval and targeted revision invalidation before it is advertised as
supported. SQL pushdown, AI operators, row embeddings and backend-specific
optimisations remain later milestones. A small bake-off on representative
text-heavy, mixed-type and large numerical tables should compare table-only,
table-plus-schema and optional row projections using table Recall@K, row/cell
citation accuracy, memory, update cost and retrieval p95; that bake-off does
not gate the baseline parser/retrieval implementation.

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
| S0 structured source contract | canonical storage/read-frontier contracts | fake catalog, typed rows, TSV/CSV dialect/schema/locator and provenance fixtures | no untyped or cross-frontier row reaches retrieval; exact row/cell citations survive revision changes | schema drift |
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
