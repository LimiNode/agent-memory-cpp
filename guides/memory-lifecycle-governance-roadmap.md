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
```

Required query forms:

- `active_at(valid_time_ms)`: facts valid at a source-world time.
- `known_at(recorded_cutoff_ms)`: facts the agent had recorded by a time.
- `active_at(valid_time_ms, recorded_cutoff_ms)`: what the agent would have
  believed at a past point, excluding later corrections.
- `invalidated_after(recorded_time_ms)`: audit and contradiction review.
- `known_at_sequence(sequence)`: what a runtime component could have known when
  an external event sequence was processed.

`recorded_sequence` and `invalidated_sequence` are optional origin-scoped
runtime log positions. `RuntimeSequence` is specified in
[`agent-runtime-integration-roadmap.md`](agent-runtime-integration-roadmap.md).
Sequence values are numerically ordered only inside one runtime/replica origin.
They are useful for agent runtimes that have a durable event log and need
deterministic replay or "what did component X know at decision Y?" audits.

Storage implications:

- add scope-aware range indexes over `valid_from_ms`, `valid_until_ms`,
  `recorded_at_ms` and `invalidated_at_ms`;
- keep temporal semantics in `agent-memory-cpp`;
- use generic MDBX range-index primitives rather than a Graphiti-specific
  graph schema.

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
    std::optional<EntityId> resolved_id;
    double confidence = 0.0;
    std::vector<EntityId> candidates;
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

## 13. Deferred To ADELIA / Runtime

The following proposals are valuable, but they are not core
`agent-memory-cpp` roadmap items:

- `FocusView`, `ContextWorkspace`, `ContextPage` and
  `IContextMaterializer` as application context virtualization.
- `UserModelNode`, mental hypotheses, theory-of-mind sidecars and
  personalization models.
- MetaMind, Mind Modeling and predictive-coding reasoning loops.
- Event-driven workflow engines, Archon-style DAG policies and orchestration.
- UI/runtime integrations, ASR/TTS, browser automation, Candle/langchain-rust
  inference adapters and process reward models.

They can consume the memory core through regular read/write/policy interfaces.

## 14. Roadmap Placement

Suggested maturity placement:

- M1: keep current `TemporalComponent`, `WritePolicy`, retrieval metrics and
  raw resource support.
- M2: add bi-temporal component/indexes, policy-selectable mutation model and
  expanded evaluation metrics.
- M2+: add abstraction/derivation graph, causal relation vocabulary and
  progressive retrieval; add deterministic-first entity resolution, typed
  query/MCP safety and logical index separation.
- M3/research: application-level mind models and workflow orchestration in
  sibling projects, validated against the same memory trace/eval harness.
