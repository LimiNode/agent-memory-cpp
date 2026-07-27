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
- Mem0 public repository and benchmark notes (https://github.com/mem0ai/mem0)
  - entity linking, multi-signal retrieval, temporal retrieval and
  memory-specific benchmark axes.
- Letta / MemGPT (https://github.com/letta-ai/letta) - context virtualization
  as an application/runtime pattern, not a core storage dependency.
- Attached project-expansion review:
  `C:\Users\User\.codex\attachments\adbc32dc-7626-475b-8392-57b5b0e7d653\pasted-text.txt`.

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
    double confidence = 1.0;
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
    std::optional<std::int64_t> valid_to_ms;
    std::int64_t recorded_at_ms = 0;
    std::optional<std::int64_t> invalidated_at_ms;
};
```

Required query forms:

- `active_at(valid_time_ms)`: facts valid at a source-world time.
- `known_at(recorded_cutoff_ms)`: facts the agent had recorded by a time.
- `active_at(valid_time_ms, recorded_cutoff_ms)`: what the agent would have
  believed at a past point, excluding later corrections.
- `invalidated_after(recorded_time_ms)`: audit and contradiction review.

Storage implications:

- add scope-aware range indexes over `valid_from_ms`, `valid_to_ms`,
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
    std::vector<KnowledgeUnitId> derived_from;
    std::vector<KnowledgeUnitId> supports;
    std::vector<KnowledgeUnitId> contradicts;
};
```

Rules:

- `Raw` and `Episodic` units preserve source evidence.
- `Atomic` units extract individual facts, preferences, claims or decisions.
- `Aggregated` units summarize many units and must retain `derived_from`.
- `Model` units are application-defined derived records; they are not hidden
  truth unless backed by evidence and a policy.

The relation storage should be generic: relation indexes over ids and relation
kinds, not a domain-specific graph database inside `mdbx-containers`.

## 5. AM-15: Causal Memory Relations

Some retrieval tasks require "why" and "what changed" reasoning, not just
similarity. M2+ should introduce an explicit relation vocabulary for causal and
consistency links:

```cpp
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
relation indexes. Retrieval may use bounded expansion over these links when
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
struct MemoryEditIntent {
    MemoryRecordRef target;
    StateRevision expected_revision;
    MemoryEditOperation operation;
    TypedPayload proposed_value;
    std::vector<EvidenceRef> evidence;
    double confidence = 1.0;
};
```

This prevents an agent loop from overwriting durable memory without CAS-style
revision checks and explicit provenance.

## 10. Deferred To ADELIA / Runtime

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

## 11. Roadmap Placement

Suggested maturity placement:

- M1: keep current `TemporalComponent`, `WritePolicy`, retrieval metrics and
  raw resource support.
- M2: add bi-temporal component/indexes, policy-selectable mutation model and
  expanded evaluation metrics.
- M2+: add abstraction/derivation graph, causal relation vocabulary and
  progressive retrieval.
- M3/research: application-level mind models and workflow orchestration in
  sibling projects, validated against the same memory trace/eval harness.
