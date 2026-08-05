# memory-lifecycle-governance-roadmap.md

Roadmap extension for memory lifecycle governance in `agent-memory-cpp`.
This document turns recent project-expansion proposals into scoped, reviewable
contracts for the core library.

Primary references:

- `Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers`
  (arXiv:2603.07670, https://arxiv.org/html/2603.07670v1) -
  write/manage/read lifecycle framing and evaluation beyond static retrieval.
- Graphiti / Zep documentation
  (https://help.getzep.com/graphiti/getting-started/overview and
  https://help.getzep.com/graphiti/working-with-data/searching) -
  bi-temporal graph pattern, provenance-aware incremental updates and
  hybrid search.
- getzep/graphiti repository (https://github.com/getzep/graphiti) and Zep
  engineering notes:
  https://blog.getzep.com/scaling-agent-memory-zep-30x/ and
  https://blog.getzep.com/graphiti-hits-20k-stars-mcp-server-1-0/ -
  episodes as source evidence, deterministic-first entity deduplication,
  search-backend separation, MCP/query-safety lessons and the OSS-vs-managed
  infrastructure boundary.
- Mem0 public repository and benchmark notes (https://github.com/mem0ai/mem0)
  - entity linking, multi-signal retrieval, temporal retrieval and
  memory-specific benchmark axes.
- Letta / MemGPT (https://github.com/letta-ai/letta) - context virtualization
  as an application/runtime pattern, not a core storage dependency.
- Attached project-expansion review:
  `C:\Users\User\.codex\attachments\adbc32dc-7626-475b-8392-57b5b0e7d653\pasted-text.txt`.
- Attached Graphiti/Zep review:
  `C:\Users\User\.codex\attachments\dfe7f516-e425-40a3-9b6b-6348958a4f6c\pasted-text.txt`.

- Attached persistent-memory review:
  `C:\Users\User\.codex\attachments\82d2bb41-ece1-4122-ab45-37fc0fb4b548\pasted-text.txt`.
- Attached workflow-layer review:
  `C:\Users\User\.codex\attachments\37025a23-b8e1-401b-b4e4-931bf539deee\pasted-text.txt`.

## 1. Boundary

`agent-memory-cpp` is the embedded memory/retrieval core. It owns durable
memory records, evidence, provenance, lifecycle state, mutation policies,
retrieval plans and evaluation traces.

Sibling agent/runtime projects own:

- user mental models and theory-of-mind hypotheses;
- online consultation flows and post-session coaching logic;
- event-driven workflow orchestration;
- prompt/reasoning/process reward models;
- UI, ASR/TTS, browser automation and agent loops.

The core may store evidence that those layers use. It should not directly
declare hidden mental states as facts unless an application-specific adapter
persists them as ordinary, provenance-backed knowledge units.

`external/mdbx-containers` should stay domain-neutral. It may provide typed
tables, range indexes, reverse indexes, relation indexes, multi-table atomic
writes and large-value/blob helpers. It should not know about
`KnowledgeUnitKind`, `MemoryRelationKind`, user models or agent policies.

## 2. Write / Manage / Read Policy SPI

The existing `WritePolicy` remains the low-level mechanical gate for flush,
importance, dedupe, supersede and merge thresholds. M2+ should add a higher
level policy SPI around the autonomous memory lifecycle:

```cpp
class IMemoryWritePolicy;
class IMemoryManagementPolicy;
class IMemoryReadPolicy;

struct MemoryWriteDecision {
    bool store_raw = false;
    bool create_atomic_units = false;
    bool schedule_consolidation = false;
    double importance = 0.0;
    std::optional<double> confidence;
    RetentionClass retention;
};

enum class MemoryManagementAction : uint8_t {
    Keep,
    Supersede,
    Merge,
    Deprecate,
    Erase,
    Revalidate,
    RequestEvidence
};

struct MemoryReadPlan {
    std::vector<RetrieverSpec> retrievers;
    bool use_graph_expansion = false;
    bool require_temporal_reasoning = false;
    bool require_causal_evidence = false;
    std::size_t candidate_limit = 100;
    std::size_t final_limit = 20;
    std::size_t token_budget = 3000;
    Duration latency_budget;
};
```

Policy decisions must be traceable. A write or management action should record
the policy id/version, evidence refs, confidence and resulting primary/index
deltas. This keeps autonomous consolidation auditable without hard-coding one
agent behavior into the storage layer.

## 3. AM-13: Bi-Temporal Knowledge

Current `TemporalComponent` is a single-axis validity component. M2+ should add
an explicit bi-temporal component for workloads that must distinguish source
validity time from the time when the agent recorded or invalidated the claim.

```cpp
struct BiTemporalComponent {
    std::optional<std::int64_t> valid_from_ms;
    std::optional<std::int64_t> valid_until_ms;
    std::int64_t recorded_at_ms = 0;
    std::optional<std::int64_t> invalidated_at_ms;
    std::optional<RuntimeSequence> recorded_sequence;
    std::optional<RuntimeSequence> invalidated_sequence;
};

enum class TemporalQueryKind : std::uint8_t {
    ActiveAt,
    KnownAt,
    ActiveAtKnownAt,
    InvalidatedAfter,
    KnownAtSequence,
};

struct TemporalQuery {
    TemporalQueryKind kind = TemporalQueryKind::ActiveAt;
    std::optional<std::int64_t> valid_time_ms;
    std::optional<std::int64_t> recorded_cutoff_ms;
    std::optional<std::int64_t> invalidated_after_ms;
    std::optional<RuntimeSequence> sequence_cutoff;
};
```

Required query forms:

- `active_at(valid_time_ms)`: facts valid at a source-world time.
- `known_at(recorded_cutoff_ms)`: facts the agent had recorded by a time.
- `active_at(valid_time_ms, recorded_cutoff_ms)`: what the agent would have
  believed at a past point, excluding later corrections.
- `invalidated_after(recorded_time_ms)`: audit and contradiction review.
- `known_at_sequence(sequence)`: what a runtime component could have known when
  an external event sequence was processed.

`recorded_sequence` and `invalidated_sequence` are optional producer-origin
runtime log positions. `RuntimeSequence` is specified in
[`agent-runtime-integration-roadmap.md`](agent-runtime-integration-roadmap.md).
Sequence values are numerically ordered only inside one runtime/replica origin.
They are useful for agent runtimes that have a durable event log and need
deterministic replay or "what did component X know at decision Y?" audits.
For imported or reconciled occurrences, `KnownAtSequence` uses append-only
`KnowledgeVisibilityReceipt` records for the querying origin; a producer's
sequence never implies visibility at another replica. One receipt exists for
each `(GlobalKnowledgeUnitId, RuntimeOriginKey)` first-visible fact; a
conflicting sequence or evidence is preserved as an import conflict for
reconciliation, not accepted as a second visibility time.

For ordinary context construction, the same origin-scoped replay frontier also
excludes a unit when an invalidation, reconciliation, or superseding transition
was visible at or before the requested sequence. A transition after the cutoff
does not rewrite the historical answer. Audit mode may return the historical
unit with the transition evidence; it must not present that historical result as
currently active knowledge.

`TemporalQuery` is the retrieval-plan representation of these forms. Validation
requires exactly the fields named by its tag: `ActiveAt` needs `valid_time_ms`,
`KnownAt` needs `recorded_cutoff_ms`, `ActiveAtKnownAt` needs both,
`InvalidatedAfter` needs `invalidated_after_ms`, and `KnownAtSequence` needs an
origin-qualified `sequence_cutoff`. `InvalidatedAfter(t)` selects records whose
recorded invalidation time is strictly greater than `t`; records without an
invalidation time are excluded. Candidate construction and final hydration
evaluate the same query frontier; trace records the normalized effective
frontier. The legacy single-axis `TemporalWindow` remains M1 compatibility input
and cannot claim bi-temporal replay semantics.

Storage implications:

- add scope-aware range indexes over `valid_from_ms`, `valid_until_ms`,
  `recorded_at_ms` and `invalidated_at_ms`;
- use the A1 `runtime_sequence_index` discriminator rows for both producer
  sequences and `KnowledgeVisibilityReceipt` lookups;
- keep temporal semantics in `agent-memory-cpp`;
- use generic MDBX range-index primitives rather than a Graphiti-specific
  graph schema.

### Physical Query Profiles And Benchmarks (M2+)

Temporal optimisation keeps valid time and recorded/observed time as separate
access paths. The acceptance matrix covers `active_at`, interval overlap,
latest valid value, subject/predicate history, `known_at`, and recorded-time
cutoffs; one range index must not silently answer the other axis.

Time-partitioned immutable segments, conservative min/max time synopses,
start-time plus end-time indexes, delta-of-delta timestamp columns, and RLE or
dictionary coding for repeated source/session/speaker values are M2+ physical
experiments. They may skip only segments whose immutable bounds prove no match.
Every profile is measured against the range-index baseline for candidate count,
segment reads, decoded bytes, p50/p95/p99, update/compaction cost, and
time-travel correctness.

## 4. AM-14: Abstraction And Derivation Graph

Raw documents, chunks, facts, episodes, summaries and higher-level models are
different abstraction levels. The core needs explicit derivation metadata so
retrieval can move from compact summaries back to evidence without conflating
observation and inference.

```cpp
enum class AbstractionLevel : uint8_t {
    Raw,
    Atomic,
    Episodic,
    Aggregated,
    Model
};

struct AbstractionComponent {
    AbstractionLevel level;
    std::vector<KnowledgeUnitRef> derived_from;
    std::vector<KnowledgeUnitRef> supports;
    std::vector<KnowledgeUnitRef> contradicts;
};

struct DerivationComponent {
    std::vector<KnowledgeUnitRef> source_episodes;
    std::vector<RuntimeObjectRef> runtime_sources;
    std::string producer_id;
    std::string producer_version;
    std::optional<double> confidence;
};
```

Rules:

- `Raw` and `Episodic` units preserve source evidence.
- `Atomic` units extract individual facts, preferences, claims or decisions.
- `Aggregated` units summarize many units and must retain `derived_from`.
- `Model` units are application-defined derived records; they are not hidden
  truth unless backed by evidence and a policy.
- derived `Atomic`, `Aggregated` and `Model` units must retain a path back to
  raw episodes or external runtime sources through `DerivationComponent`.

The relation storage should be generic: relation indexes over ids and relation
kinds, not a domain-specific graph database inside `mdbx-containers`.

Graphiti's useful split is:

```text
Episode
  -> Entity / Fact / Relation / Decision / Preference
  -> Scenario / Profile / Skill / Model
```

For `agent-memory-cpp`, episodes are canonical evidence records of observations
or reports, not privileged world truth; facts and relations are derived
interpretations. Sensitive or psychological model records
must not exist without evidence and producer metadata.

## 5. AM-15: Causal Memory Relations

Some retrieval tasks require "why" and "what changed" reasoning, not just
similarity. M2+ should introduce an explicit relation vocabulary for causal and
consistency links:

```cpp
enum class RelationClass : uint8_t {
    Semantic,
    TechnicalLineage,
    Evidence,
    Supersession
};

enum class MemoryRelationKind : uint8_t {
    CausedBy,
    EnabledBy,
    PreventedBy,
    Predicted,
    Contradicts,
    Resolves,
    Supersedes
};
```

These are application-level relation semantics stored on top of generic
relation indexes. Every relation record carries one `RelationClass`; physical
`graph_edges_by_src` / `graph_edges_by_dst` storage may be shared, but a normal
semantic graph expansion includes only `Semantic` relations. Technical lineage,
evidence and supersession traversal require an explicit retrieval-plan channel
and separate fan-out budget. `ArtifactCatalog::artifact_relations` remains
technical lineage outside the semantic graph unless an application records an
explicit cross-reference. Retrieval may use bounded semantic expansion when
`MemoryReadPlan::require_causal_evidence` or a similar query intent is set.

## 6. AM-16: Progressive Retrieval

Progressive retrieval should move across abstraction levels under explicit
depth, token and latency budgets:

```text
high-level model / summary
  -> episode or aggregate evidence
  -> atomic facts / decisions / preferences
  -> raw message, document chunk or tool output
```

Expected contract:

- the first stage retrieves compact candidate units;
- expansion fetches supporting or contradicting units only when needed;
- raw bodies are loaded lazily from `ResourceBodyStore`;
- every expansion step is recorded in `IRetrievalTrace`;
- context assembly may stop at any level when the token or latency budget is
  exhausted.

This complements, but does not replace, lexical/dense/hybrid retrieval. The
retriever selection belongs to `MemoryReadPlan`.

## 7. AM-17: Evaluation Harness Expansion

Retrieval metrics alone are insufficient for agent memory. M1 keeps the
existing golden retrieval gate; M2+ should add memory-specific datasets and
metrics:

```cpp
struct MemoryEvaluationResult {
    double retrieval_recall = 0.0;
    double context_relevance = 0.0;
    double answer_groundedness = 0.0;
    double answer_relevance = 0.0;
    double temporal_accuracy = 0.0;
    double provenance_accuracy = 0.0;
    double latency_ms = 0.0;
    std::size_t context_tokens = 0;
};
```

Dataset axes:

- document RAG over raw `.md`, `.txt` and extracted PDF text;
- long-term conversational memory;
- temporal memory and supersedence;
- entity memory and relationship memory;
- causal recall;
- contradiction handling;
- forgetting, deletion and privacy-correctness.

Metrics:

- `Recall@K`, `NDCG@K`, `MRR`;
- context relevance and answer groundedness;
- temporal accuracy and stale-fact rate;
- contradiction rate and contradiction-resolution accuracy;
- task success for agentic workloads;
- citation/provenance accuracy;
- latency, token cost, write amplification and memory growth;
- deletion/privacy correctness.

Benchmark comparisons should include exact vector, binary candidate filter,
BM25/BM25F, hybrid fusion, graph expansion, hierarchical/progressive retrieval
and temporal reranking.

## 8. AM-18: Policy-Selectable Mutation Model

Append-only memory is useful for audit logs and raw episodes, but it should not
be universal. Facts, preferences, summaries and application model records need
policy-selected mutation behavior:

```cpp
enum class MemoryMutationPolicy : uint8_t {
    AppendOnly,
    Supersede,
    Merge,
    Mutable,
    Immutable,
    OperatorConfirmed
};
```

Guidance:

- raw episodes and source documents default to `AppendOnly`;
- factual claims default to `Supersede`;
- duplicate or near-duplicate derived notes may use `Merge`;
- curated cards may use `OperatorConfirmed`;
- legal/audit records may use `Immutable`;
- mutable in-place updates must still increment `KnowledgeUnitEnvelope::revision`
  when retrieval-visible content changes.

## 9. Application Edit Intents

Agent/runtime layers may propose edits, but the core should validate revision,
evidence and policy before applying them:

```cpp
using MemoryRecordRef = KnowledgeUnitRef;
using StateRevision = std::uint64_t;

enum class MemoryEditOperation : uint8_t {
    Supersede,
    Merge,
    PatchMutableFields,
    Deprecate,
    Erase
};

struct TypedPayload {
    KnowledgeUnitKind kind;
    std::vector<std::uint8_t> encoded_value;
    std::uint32_t schema_version = 1;
};

struct EvidenceRef {
    KnowledgeUnitRef unit;
    std::optional<EvidenceAnchorId> anchor_id;
};

struct MemoryEditIntent {
    MemoryRecordRef target;
    StateRevision expected_revision;
    MemoryEditOperation operation;
    TypedPayload proposed_value;
    std::vector<EvidenceRef> evidence;
    std::optional<double> confidence;
};
```

`MemoryRecordRef` and durable evidence always use `KnowledgeUnitRef`, never a
bare local `KnowledgeUnitId`. Applying an intent is a compare-and-swap: in one
write transaction the store resolves `target`, checks that its current envelope
revision exactly equals `expected_revision`, validates all evidence bindings,
then applies the policy-allowed operation and increments the retrieval-visible
revision. A missing target, stale expected revision, mismatched local/global
binding, invalid payload kind, or forbidden operation returns a non-applied
conflict result; it must not silently merge or overwrite durable memory.

## 10. AM-19: Deterministic-First Entity Resolution

Entity resolution must not default to "ask the LLM for every new name." The
Graphiti/Zep lesson is to use cheap deterministic gates first, then let an LLM
produce an auditable proposal only for ambiguous cases.

Suggested pipeline:

```text
canonical normalization
  -> exact id/name/alias lookup
  -> scope/type compatibility check
  -> lexical candidates
  -> MinHash/LSH or binary signature candidates
  -> dense candidates
  -> deterministic score fusion
  -> ambiguous result
  -> optional LLM resolution proposal
```

```cpp
enum class EntityResolutionStatus : uint8_t {
    ExactMatch,
    HeuristicMatch,
    ProbableMatch,
    Ambiguous,
    NewEntity,
    Rejected
};

struct EntityResolutionResult {
    EntityResolutionStatus status;
    std::optional<KnowledgeUnitRef> resolved_entity;
    double confidence = 0.0;
    std::vector<KnowledgeUnitRef> candidates;
    std::vector<ScoreContribution> scores;
    bool requires_llm = false;
};
```

Rules:

- LLM output may propose a merge, alias or new entity; it must not silently
  mutate durable identity.
- Merge decisions require evidence and a policy id/version.
- Low-entropy or short names should be marked ambiguous earlier because fuzzy
  similarity is unstable there.
- Deterministic thresholds are project-owned constants and must carry
  provenance in docs/tests when copied from an external reference.

The ambiguous path has a durable proposal boundary, rather than a special
Graphiti-only write path:

```cpp
enum class EntityResolutionRecommendation : uint8_t {
    CreateNew,
    LinkAlias,
    MergeIntoExisting,
    KeepDistinct,
    FlagContradiction,
    Reject,
};

struct EntityResolutionProposal {
    EntityResolutionRecommendation recommendation;
    KnowledgeUnitRef subject_entity;
    std::optional<KnowledgeUnitRef> proposed_target;
    std::vector<KnowledgeUnitRef> considered_candidates;
    std::optional<KnowledgeUnitRef> source_mention;
    std::vector<KnowledgeUnitRef> evidence;
    std::string resolver_policy_id;
    std::uint32_t resolver_policy_version = 0;
    std::string resolver_fingerprint;
    double confidence = 0.0;
};
```

The proposal is immutable evidence. Applying it creates a regular
`MemoryEditIntent` or a new Relation/Fact lineage with an expected revision;
it never rewrites an existing entity, fact or graph edge in place. Policy may
auto-apply only deterministic exact matches that meet its explicit threshold
and whose `KnowledgeUnitRef` bindings have passed the same local-to-global
validation as a proposal.
Every heuristic or LLM-assisted recommendation is auditable and may be
accepted, rejected, or superseded without destroying the original episode.
Application validates every local-to-global `KnowledgeUnitRef` binding for the
deterministic result's resolved entity/candidates and for a proposal's subject,
target, candidates, mention and evidence before applying an intent; an
unresolved or mismatched binding fails closed.

Required M2+ fixtures cover exact alias resolution, same-name distinct
entities, ambiguous candidates, an evidence-free rejection, a stale
compare-and-swap application, and a later contradiction that keeps both claims
retrieval-visible in audit mode.

## 11. AM-20: Typed Query And MCP Safety

LLM-facing tools must never turn model output into storage syntax.

Rules:

- free strings never become table names, graph labels, DBI names, Cypher labels
  or index identifiers;
- external filters are parsed into internal ids through a registry allowlist;
- backend-specific query text is built only after typed validation;
- all values are passed as parameters when the backend supports parameters;
- permissions distinguish read, episode write, fact proposal, fact
  confirmation, invalidation and deletion.

```cpp
struct EntityTypeFilter {
    std::vector<EntityTypeId> allowed_types;
};

enum class MemoryPermission : uint32_t {
    ReadMemory,
    WriteEpisode,
    ProposeFact,
    ConfirmFact,
    InvalidateFact,
    DeleteEvidence
};
```

For backends where labels or index names cannot be parameterized, adapters must
use static registry mapping:

```cpp
const DatabaseLabel& resolve_label(EntityTypeId id);
```

The LLM never supplies `DatabaseLabel` text.

## 12. AM-21: Logical Index Separation And LLM-Free Read Path

Zep's managed service separated graph operations from vector and BM25 search
when production load made one backend do too much. `agent-memory-cpp` should
borrow the contract boundary, not the immediate microservice architecture.

Logical interfaces remain separate even if the M1/M2 implementation stores them
inside one MDBX environment:

```cpp
class IEntityStore;
class IRelationStore;
class ITemporalIndex;
class IGraphTraversalIndex;
class ILexicalIndex;
class IVectorIndex;
```

The default read path should be deterministic and replayable:

```text
query
  -> lexical / vector / entity candidates
  -> temporal filtering
  -> bounded graph expansion
  -> fusion
  -> deterministic reranking
  -> result
```

LLMs may be optional adapters for query transformation, rare ambiguous entity
resolution, offline extraction, or final answer generation. Baseline retrieval
must remain usable without an LLM call and must record all expansion/filtering
steps in `IRetrievalTrace`.

## 13. AM-22: Temporal Context Graph Profile And Graphiti Evaluation

`TemporalContextGraphMemory` is an M2+ profile assembled from existing
canonical concepts, not a second graph database or a parallel source of truth:

```text
raw Episode / ConversationEpisode / Note
  -> Entity, Fact and Relation units
  -> BiTemporalComponent + DerivationComponent + ordinary lifecycle lineage
  -> graph_edges_by_src / graph_edges_by_dst and temporal candidate indexes
  -> bounded hybrid RetrievalPlan route + canonical hydration
```

Episodes remain immutable source evidence. Facts and Relation units are derived
claims with validity, recorded/invalidated times, derivation evidence and
policy-controlled supersedence. The profile may use prescribed entity/relation
kinds or an approved learned ontology registry, but model-generated labels
must first resolve through that typed registry. They never become backend graph
labels or DBI names.

Retrieval uses the existing CandidateSet, `RetrievalAccessContext`, temporal
frontier and final hydration rules. It may combine lexical, dense, entity,
temporal and bounded graph-expansion routes, but must use one shared lifecycle
and authority check. A traversal cannot revive an inaccessible, invalidated or
stale unit merely because a neighbouring edge still exists for audit.

The profile creates no new upstream `mdbx-containers` abstraction. Its first
implementation uses the existing downstream Relation-owned two-orientation
edge recipe, scope-aware temporal indexes, transactions and bounded pages. Any
additional physical DBI delta, graph traversal cache or service split needs an
explicit profile manifest row, owner, benchmark and restore/rebuild contract.

Graphiti is an optional external baseline and adapter experiment, not a linked
runtime dependency. A fair comparison uses the same episode corpus, source
revision policy, identity/alias fixtures, query set, access filter, embedding
model and cold/warm procedure. Report entity-link precision/recall, fact and
temporal-answer accuracy, citation/provenance fidelity, contradiction handling,
update/delete latency, p50/p99 retrieval latency, ingest throughput, disk/RSS
and any LLM token/cost. Feature coverage and semantics must be published beside
performance numbers; no result may imply that the two systems share an
identical storage model.

Acceptance requires a revisioned fixture set with alias, same-name ambiguity,
fact invalidation, preference change, cross-episode integration, conflicting
perspectives, historical `KnownAt` query and evidence-anchor recovery. Native
and external-baseline runs use the same golden expected answers and retain a
`ComparisonParityManifest`.

`ComparisonParityManifest.workload_contract_digest` is versioned and binds the
alias/identity fixtures, resolver policy and thresholds, ontology registry,
source-revision policy, temporal and contradiction policy, traversal limits,
access policy, query/answer set and evaluation procedure. AM-22 acceptance
rejects a comparison whose digest differs, even when the corpus name matches.

## 14. AM-23: Fail-Closed Memory Admission And External Materialization

Extraction, import and model output must not write a retrieval-visible
`KnowledgeUnit` directly. They first produce a `MemoryCandidate`; a versioned
admission policy then produces a durable decision. This is a higher-level
boundary than `WritePolicy`: the latter decides how an accepted unit is
managed, while admission decides whether a candidate may become a unit at all.

```cpp
enum class MemoryAdmissionAction : std::uint8_t {
    Accept,
    Reject,
    Quarantine,
    RequestConfirmation
};

enum class ExternalMaterializationPolicy : std::uint8_t {
    ReferenceOnly,
    EphemeralSnapshot,
    DurableSnapshot
};

struct MemoryCandidate {
    CandidateId id;
    CandidatePayload payload;
    ContentDigest content_digest;
    CandidateProvenance provenance;
    std::vector<EvidenceRef> evidence;
    SourceTrustClass source_trust;
    std::vector<SensitivityLabel> sensitivity_labels;
    ExternalMaterializationPolicy requested_materialization =
        ExternalMaterializationPolicy::ReferenceOnly;
};

struct MemoryAdmissionDecision {
    MemoryAdmissionAction action = MemoryAdmissionAction::Reject;
    std::string policy_id;
    std::string policy_version;
    std::vector<AdmissionReasonCode> reasons;
    std::vector<SensitivityLabel> effective_sensitivity_labels;
    ExternalMaterializationPolicy materialization =
        ExternalMaterializationPolicy::ReferenceOnly;
};

class IMemoryAdmissionPolicy;
class IMemoryAdmissionAuditSink;
```

The names above define the intended contract shape, not an M1 public header.
Their concrete value types must be canonical, serializable and scoped before
implementation. `CandidateProvenance` records the producing adapter/model and
version, source identity/revision where available, and a content or evidence
digest; it does not make a model-produced statement authoritative by itself.
`CandidatePayload` is transient policy input: its raw text or structured fields
are not durable unless an `Accept` decision authorizes the resulting write.

### Admission Invariants

- `Reject`, `Quarantine` and `RequestConfirmation` candidates are not
  retrieval-visible. They must not create lexical postings, embeddings, binary
  signatures, graph edges, summaries or ordinary `KnowledgeUnit` rows.
- `Quarantine` is a separately access-controlled review queue, never a hidden
  low-confidence retrieval index. `RequestConfirmation` is likewise pending
  application or operator confirmation, not an implicit acceptance.
- Secret detection, content sanitization and sensitivity classification run
  before text is embedded, sent to an external model adapter, stored in an
  LLM-derived record, indexed, logged or copied into a retrieval trace. A
  policy that cannot complete a required check fails closed.
- An `Accept` decision binds the candidate digest, policy id/version, evidence
  references, effective labels and requested materialization in the same
  transaction or durable outbox as the resulting memory write. A stale,
  substituted or differently classified candidate cannot reuse the decision.
- A policy may require explicit confirmation for model-derived facts,
  untrusted sources, sensitive labels, missing revision evidence or a material
  change from the source episode. It must emit a reason code rather than
  silently degrading that case to acceptance.
- Admission changes neither lifecycle truth nor access rights. Accepted units
  still pass the normal authority, lifecycle, temporal and hydration checks on
  every read.

### External Source Materialization

The application declares one materialization policy for each source connector
or candidate class; importers must not quietly retain a body merely because it
was available during extraction.

| Policy | Durable contract | Retrieval consequence |
|---|---|---|
| `ReferenceOnly` | Retain only a validated locator, source identity/revision and permitted citation metadata. Do not retain a source body. | A result can cite the source but cannot promise offline body hydration. |
| `EphemeralSnapshot` | Hold a bounded, encrypted working copy only for the declared processing lifetime. It has an expiry/cleanup receipt and is not a durable `ResourceBodyStore` revision. | It is unavailable after expiry and cannot be treated as durable evidence. Any derived accepted unit retains the source reference and admission provenance. |
| `DurableSnapshot` | Retain an immutable, revision-bound body in `ResourceBodyStore` with its digest, retention class and deletion policy. | Revision-bound citations and later evidence hydration are permitted subject to normal access checks. |

`ReferenceOnly` is the safe default. `EphemeralSnapshot` and `DurableSnapshot`
require policy authorization after the sanitization and sensitivity gate. A
later materialization upgrade is a new policy-governed write with an evidence
and revision check; it must not rewrite a reference-only citation as though the
body had always been retained. Deletion, expiry and source-revocation flows
must remove or invalidate derived retrieval projections according to the
existing lifecycle/deletion contract. Once `DurableSnapshot` is authorized,
the ArtifactProvenance profile owns the resulting artifact, source-revision,
blob-retention and evidence-anchor contract; admission does not create a
parallel catalog or BlobStore.

### Minimal Admission Audit (M2)

Admission audit is distinct from `IRetrievalTrace`: a retrieval trace explains
how an accepted record was read, while the admission audit explains why a
candidate was accepted, rejected or held. An optional `IMemoryAdmissionAuditSink`
records the candidate id/digest, action, reason codes, policy id/version,
timestamps, source-trust category and materialization choice.

Raw candidate content, raw query text, secrets, unredacted source bodies and
model chain-of-thought are excluded from the default audit schema. A deployment
that needs any of them must define an explicit, access-controlled retention
policy outside the ordinary retrieval store. When audit delivery is required by
policy, the decision receipt and accepted write use one atomic transaction or a
durable transactional outbox; an unrecorded required decision fails closed.

Required M2 fixtures cover an accepted revision-bound candidate, an untrusted
candidate rejected before embedding, a secret-labelled candidate routed to
quarantine, a pending confirmation that produces no retrieval candidate, each
materialization mode, snapshot expiry, decision/candidate digest mismatch, and
an audit event that proves no raw body or query text was persisted by default.

## 15. Deferred To ADELIA / Runtime

The following proposals are valuable, but they are not core
`agent-memory-cpp` roadmap items:

- `FocusView`, `ContextWorkspace`, `ContextPage` and
  `IContextMaterializer` as application context virtualization.
- `UserModelNode`, mental hypotheses, theory-of-mind sidecars and
  personalization models.
- MetaMind, Mind Modeling and predictive-coding reasoning loops.
- Event-driven workflow engines, Archon-style DAG policies and orchestration.
- Declarative `ScenarioPackage`s, `StageDecisionDraft`s, host-evaluated
  transition predicates, workflow blackboards, scenario renderers and trace
  viewers. A model may propose a stage decision, but the host validates schema,
  transition admissibility, completion, revision/CAS, budget and policy before
  one atomic workflow commit.
- UI/runtime integrations, ASR/TTS, browser automation, Candle/langchain-rust
  inference adapters and process reward models.

They can consume the memory core through regular read/write/policy interfaces.

## 16. Roadmap Placement

Suggested maturity placement:

- M1: keep current `TemporalComponent`, `WritePolicy`, retrieval metrics and
  raw resource support.
- M2: add bi-temporal component/indexes, policy-selectable mutation model,
  fail-closed memory admission/external-materialization policy, optional
  admission audit, and expanded evaluation metrics.
- M2+: add abstraction/derivation graph, causal relation vocabulary and
  progressive retrieval; add deterministic-first entity resolution, typed
  query/MCP safety, logical index separation and the optional
  `TemporalContextGraphMemory` profile/evaluation lane.
- M3/research: application-level mind models and workflow orchestration in
  sibling projects, validated against the same memory trace/eval harness.
