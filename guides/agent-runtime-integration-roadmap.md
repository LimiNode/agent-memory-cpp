# Agent Runtime Integration Roadmap

This roadmap defines an optional integration lane for distributed cognitive
runtimes such as ADELIA. It extends `agent-memory-cpp` as durable memory,
retrieval, replay and reconciliation substrate without turning the library into
an agent framework.

## Purpose

The core library should be able to persist and retrieve records that answer:

- which runtime or node observed this;
- what that node could know at a given sequence/time;
- which evidence supported a decision;
- which alternatives were considered;
- which action was selected and what outcome followed;
- which procedure was learned from repeated traces;
- how partitions merged without destroying conflicting perspectives.

## Boundary

```text
external cognitive runtime
  live state, scheduling, focus, authority, actions, topology, transport

agent-memory-cpp
  durable events, observations, perspectives, decisions, outcomes,
  procedures, provenance, retrieval plans, replay metadata, reconciliation
  records
```

Non-goals for `agent-memory-cpp`:

- node scheduler or mailbox runtime;
- focus arbitration;
- authority enforcement;
- live self-model or live affect regulation;
- action executor or topology mutation executor;
- distributed transport or consensus protocol;
- LLM planner or tool-calling runtime.

Memory may store what those systems did and why. It must not execute actions or
grant permission to execute them.

## ADR Lane

The source audit used labels ADR-016..ADR-022. In
`memory-stacks-roadmap.md`, ADR-016..ADR-018 are already assigned, so the
runtime integration lane is cross-listed there as ADR-019..ADR-025.

| Runtime ADR | Memory-stacks ADR | Decision |
|---|---:|---|
| ARI-016 | ADR-019 | Agent runtime integration boundary |
| ARI-017 | ADR-020 | Perspective is orthogonal to `ScopeId` |
| ARI-018 | ADR-021 | Introspection is persisted as observation |
| ARI-019 | ADR-022 | Causal history is first-class |
| ARI-020 | ADR-023 | Bi-temporal agent knowledge is M2+/A-lane |
| ARI-021 | ADR-024 | Canonical evidence is monotonic |
| ARI-022 | ADR-025 | Replication is semantic, not page-level |

## Core Invariants

1. `ScopeId` is namespace/ownership, not perspective, character, authority,
   runtime instance, replica or partition.
2. `Character`, `Observer` and `Authority` are separate runtime object roles.
3. Introspection is an observation produced by a node, not privileged truth.
4. Runtime traces and retrieval traces link through `trace_id`/`span_id`.
5. Causal relations are not inferred only from timestamps.
6. `Hypothesis` is not returned as `ValidatedClaim` without explicit policy.
7. Raw evidence is append-only except explicit erase policy.
8. Derived state must cite evidence/provenance.
9. Procedure activation does not execute the procedure.
10. Memory reconciliation preserves conflicts unless runtime policy resolves
    them.
11. A0-A2 do not add per-component DBIs.
12. Core contracts do not include ADELIA headers.

## Neutral Runtime References

```cpp
enum class RuntimeObjectKind : std::uint16_t {
    Runtime = 1,
    Agent = 2,
    Node = 3,
    Character = 4,
    Authority = 5,
    Focus = 6,
    Task = 7,
    Goal = 8,
    Action = 9,
    Capability = 10,
    Coalition = 11,
    Partition = 12,
    Replica = 13
};

struct RuntimeObjectRef {
    std::string system_id;
    RuntimeObjectKind kind = RuntimeObjectKind::Runtime;
    std::string opaque_id;
    std::optional<std::uint64_t> revision;
};
```

An ADELIA adapter maps runtime-native node identifiers to neutral refs:

```text
native node identifier -> RuntimeObjectRef{"adelia", Node, "...", revision}
```

The mapping lives in the adapter, not in core headers.

## Components

All A0-A2 components use the existing `unit_components`
`TypeDiscriminatedTable<ComponentKind, UnitId, TypedComponentValue>` substrate.
No new DBI is allocated for each component.

### RuntimeOriginComponent

```cpp
struct RuntimeOriginComponent {
    RuntimeObjectRef runtime;
    std::optional<RuntimeObjectRef> producer_node;

    std::string run_id;
    std::string trace_id;
    std::string span_id;

    std::uint64_t event_sequence = 0;

    std::optional<RuntimeObjectRef> partition;
    std::optional<RuntimeObjectRef> replica;
};
```

### CausalContextComponent

```cpp
struct CausalContextComponent {
    std::vector<KnowledgeUnitId> direct_cause_units;
    std::vector<RuntimeObjectRef> direct_cause_events;

    std::string correlation_id;
    std::string causation_id;

    std::optional<std::uint64_t> logical_clock;
};
```

### PerspectiveComponent

```cpp
enum class PerspectiveMode : std::uint8_t {
    Observer = 1,
    Participant = 2,
    Actor = 3,
    SelfModel = 4,
    OtherModel = 5,
    SystemReconstruction = 6
};

struct PerspectiveComponent {
    RuntimeObjectRef observer;
    std::optional<RuntimeObjectRef> represented_subject;
    std::optional<RuntimeObjectRef> character;
    std::optional<RuntimeObjectRef> authority;

    PerspectiveMode mode = PerspectiveMode::Observer;

    double coverage = 0.0;
    double confidence = 0.0;
};
```

`character` and `observer` are intentionally independent. Two partitions can
both represent the same character while remaining different observers.

### EpistemicStatusComponent

```cpp
enum class EpistemicLayer : std::uint8_t {
    RawObservation = 1,
    LocalPresentation = 2,
    Interpretation = 3,
    Hypothesis = 4,
    ValidatedClaim = 5,
    NarrativeSummary = 6
};

struct EpistemicStatusComponent {
    EpistemicLayer layer = EpistemicLayer::RawObservation;
    double confidence = 1.0;

    std::string producer_model_id;
    std::string producer_model_version;

    std::vector<KnowledgeUnitId> supporting_units;
    std::vector<KnowledgeUnitId> contradicting_units;
};
```

Retrieval must not automatically mix `Hypothesis` and `ValidatedClaim`.

### BiTemporalComponent

This component is not M1 temporal validity. It belongs to M2+/A-lane workloads
that need to distinguish world validity from runtime knowledge time.

```cpp
struct BiTemporalComponent {
    std::optional<std::int64_t> valid_from_ms;
    std::optional<std::int64_t> valid_until_ms;

    std::int64_t recorded_at_ms = 0;
    std::optional<std::int64_t> superseded_at_ms;

    std::optional<std::uint64_t> recorded_sequence;
    std::optional<std::uint64_t> superseded_sequence;
};
```

Unknown timestamps are `std::optional`, not `0`.

### FocusContextComponent

```cpp
enum class FocusTransitionKind : std::uint8_t {
    Acquired = 1,
    Suspended = 2,
    Resumed = 3,
    Completed = 4,
    Abandoned = 5
};

struct FocusContextComponent {
    RuntimeObjectRef focus;
    std::optional<RuntimeObjectRef> parent_focus;

    FocusTransitionKind transition = FocusTransitionKind::Acquired;

    std::optional<double> salience;
    std::optional<double> interruption_cost;
};
```

Memory stores focus context for an episode. The live focus controller belongs
to the runtime.

## Task, Decision And Procedure Payloads

`Task` and `Decision` are no longer merely handoff placeholders in the
A-lane. Their payloads are stored through `unit_components` until a measured
access pattern justifies dedicated DBIs.

```cpp
enum class TaskStatus : std::uint8_t {
    Proposed = 1,
    Accepted = 2,
    Running = 3,
    Suspended = 4,
    Completed = 5,
    Failed = 6,
    Cancelled = 7
};

struct TaskPayload {
    std::string title;
    std::string goal_text;

    TaskStatus status = TaskStatus::Proposed;

    std::optional<RuntimeObjectRef> requested_by;
    std::optional<RuntimeObjectRef> assigned_to;
    std::optional<RuntimeObjectRef> parent_task;

    std::vector<RuntimeObjectRef> required_capabilities;
    std::vector<KnowledgeUnitId> constraints;
    std::vector<KnowledgeUnitId> produced_units;

    std::optional<std::int64_t> deadline_ms;
};
```

```cpp
struct DecisionAlternative {
    std::string alternative_id;
    std::string description;

    std::optional<double> predicted_utility;
    std::optional<double> predicted_risk;

    std::vector<KnowledgeUnitId> supporting_units;
    std::vector<KnowledgeUnitId> opposing_units;
};

struct DecisionPayload {
    std::vector<DecisionAlternative> alternatives;
    std::string selected_alternative_id;

    std::string rationale_summary;
    double confidence = 0.0;

    std::optional<RuntimeObjectRef> committed_by;
    std::optional<RuntimeObjectRef> authority;

    std::vector<KnowledgeUnitId> resulting_actions;
};
```

LLM-generated rationale is not a complete causal explanation. Canonical causes
come from `CausalContextComponent` and supporting units.

`Procedure` is distinct from `Playbook`:

- `Playbook`: curated human-authored or reviewed guidance;
- `ProcedureCandidate`: learned/imported proposal from traces;
- `Procedure`: validated, versioned operational knowledge;
- `RuntimeCapability`: executable runtime function; memory stores references
  and requirements, not implementation.

```cpp
enum class ProcedureStatus : std::uint8_t {
    Candidate = 1,
    Validating = 2,
    Active = 3,
    Degraded = 4,
    Blocked = 5,
    Superseded = 6,
    Retired = 7
};

struct ProcedurePayload {
    std::string name;
    std::string description;

    ProcedureStatus status = ProcedureStatus::Candidate;
    std::uint64_t version = 1;

    std::vector<RuntimeObjectRef> required_capabilities;
    std::vector<KnowledgeUnitId> preconditions;

    std::string workflow_format;
    std::string workflow_reference;

    std::vector<KnowledgeUnitId> source_episodes;
    std::vector<KnowledgeUnitId> validation_cases;

    std::uint64_t success_count = 0;
    std::uint64_t failure_count = 0;

    std::optional<double> estimated_success_probability;
    std::optional<double> mean_utility;
};
```

Do not persist function pointers, closures or ADELIA node handles in
`ProcedurePayload`.

## Typed Relations

Causal and runtime relations use application-owned `EdgeKind` values and
typed graph edge payloads. Important semantics must not live only in
`primary_text = "A caused B"`.

Recommended relation vocabulary:

```text
OBSERVED_BY
PRESENTED_TO
INTERPRETED_BY
HYPOTHESIZED_BY
SUPPORTED_DECISION
OPPOSED_DECISION
SELECTED_ACTION
PRODUCED_OUTCOME
CAUSED
CORRELATED_WITH
SUPERSEDES_KNOWLEDGE
CONTRADICTS
MERGED_WITH
COMPILED_INTO
VALIDATED_BY
REQUIRES_CAPABILITY
EXECUTED_BY
```

## Retrieval Extensions

Additional `RetrievalPlan` fields are optional and must not be required by
ordinary RAG profiles:

```cpp
struct RuntimeRetrievalFilters {
    std::vector<RuntimeObjectRef> observer_filter;
    std::vector<RuntimeObjectRef> character_filter;
    std::vector<RuntimeObjectRef> producer_node_filter;
    std::vector<std::string> trace_ids;

    std::optional<std::uint64_t> sequence_from;
    std::optional<std::uint64_t> sequence_until;

    std::vector<EpistemicLayer> epistemic_layers;

    bool require_evidence = false;
    bool include_conflicts = true;
    bool include_superseded = false;
};
```

New query classes:

```text
CausalWhy
DecisionRecall
TaskRecall
ProcedureLookup
PerspectiveLookup
KnowledgeAtSequence
UnresolvedProblemLookup
EvidenceDrillDown
```

## Progressive Disclosure

```cpp
enum class DetailLevel : std::uint8_t {
    Symbolic = 1,
    Summary = 2,
    Structured = 3,
    Evidence = 4,
    Raw = 5
};

struct DrillDownRef {
    KnowledgeUnitId target_unit;
    DetailLevel available_level = DetailLevel::Summary;
};
```

`RetrievalHit` may expose the current detail level, drill-down refs,
perspective summary and epistemic layer. This lets ADELIA first receive an
addressable outline and request raw evidence only when needed.

Context block labels must preserve perspective:

```text
User stated:
Planner believed:
RiskNode inferred:
System reconstruction estimates:
```

Do not collapse these into one omniscient statement.

## Procedure Activation

`ProcedureActivationCandidate` is a retrieval/planning artifact, not an
execution request.

```cpp
struct ProcedureActivationCandidate {
    KnowledgeUnitId procedure_id;

    double precondition_match = 0.0;
    double capability_match = 0.0;
    double historical_success = 0.0;
    double context_relevance = 0.0;

    std::vector<KnowledgeUnitId> supporting_units;
    std::vector<RuntimeObjectRef> missing_capabilities;

    bool requires_validation = false;
};
```

Memory stores capability id, version, declarative input/output schema, safety
metadata and procedure requirements. The runtime owns callable implementation,
live availability, authority, resource budget and the actual node providing a
capability.

Procedure learning flow:

```text
trace episodes
  -> ProcedureCandidate
  -> sandbox/runtime validation
  -> active Procedure
  -> runtime executions
  -> outcome statistics
  -> degradation or retirement
```

Memory records proposals and evaluations. Promotion to active procedure is a
runtime/operator policy decision.

## Partition And Reconciliation

Transport and consensus are out of scope, but memory needs neutral records for
semantic import/export and conflict preservation.

```cpp
struct ReplicaStamp {
    RuntimeObjectRef replica;
    std::uint64_t counter = 0;
};

enum class ConsistencyClass : std::uint8_t {
    Local = 1,
    MonotonicReplicated = 2,
    CausalReplicated = 3,
    LeasedSingleWriter = 4,
    QuorumRequired = 5,
    OperatorConfirmed = 6
};
```

```cpp
enum class ReconciliationRelation : std::uint8_t {
    Identical = 1,
    Compatible = 2,
    Complementary = 3,
    Superseding = 4,
    Contradictory = 5,
    Incomparable = 6
};

struct ReconciliationConflict {
    KnowledgeUnitId left;
    KnowledgeUnitId right;

    ReconciliationRelation relation = ReconciliationRelation::Incomparable;
    std::vector<KnowledgeUnitId> evidence;

    bool resolved = false;
};
```

Without coordination, the monotonic core may append raw events, observations,
hypotheses, provenance and procedure candidates. Erase, final conflict
resolution, active procedure changes, identity-model changes and irreversible
external commits require runtime authority.

After merge, unresolved conflict is retrieval-visible:

```text
Partition A believed X.
Partition B believed Y.
X and Y are currently unresolved.
```

## DBI Budget

A0-A2 use existing substrate:

| Data | Existing substrate |
|---|---|
| runtime origin | `unit_components` |
| causal context | `unit_components` + graph edges |
| perspective | `unit_components` |
| epistemic status | `unit_components` |
| focus context | `unit_components` |
| task/decision/procedure payloads | `unit_components` initially |
| sequence filtering | existing range/metadata substrate |
| raw event payload | `ResourceBodyStore` |
| causal relations | `graph_edges_by_src` / `graph_edges_by_dst` |
| conflicts | units + graph relations |

Do not add `runtime_event_index` or revive `temporal_event_index` for A-lane
M1/M2 readiness. If partition support later needs materialized DBIs,
`replica_frontiers` and `reconciliation_jobs` are the first candidates, but
they are not in the canonical or expanded budget until a concrete
implementation task updates `dbi-manifest.yaml`.

## Evaluation

Metrics:

```text
CausalPathFidelity
PerspectiveIsolationAccuracy
EpistemicLayerAccuracy
KnowledgeAtSequenceAccuracy
ProvenanceCompleteness
ConflictPreservationRate
ProcedureActivationPrecision
ProcedureActivationRecall
OutcomeCalibration
ReplayDeterminism
ReconciliationConvergence
DerivedDeletionClosure
ProgressiveDisclosureTokenEfficiency
```

Golden scenarios:

| Scenario | Expected protection |
|---|---|
| `SameEventDifferentPerspectives` | Several node interpretations remain distinct |
| `KnowledgeAtSequence` | A node does not retrieve knowledge learned later |
| `CausalWhy` | Decision, evidence and direct causal path are returned |
| `DecisionAlternatives` | Selected and rejected alternatives are preserved |
| `ProcedureActivation` | Procedure activates only with matching preconditions/capabilities |
| `ProcedureDegradation` | Repeated failures degrade procedure without deleting history |
| `PartitionAppendMerge` | Independent observations survive merge |
| `PartitionConflictPreserved` | Contradictory claims remain visible |
| `PerspectiveLeakage` | Local perspective does not leak without allowed projection |
| `EvidenceDrillDown` | Summary opens to structured record, then raw evidence |
| `DerivedErasePropagation` | Sensitive-source erase marks derived units stale/erased by policy |
| `ReplayDeterminism` | Same append-only corpus rebuilds same derived projection |

## A-Lane Readiness

The A-lane is orthogonal to M0/M1/M2. It does not change the normative scope
lock in `milestones.md`.

| Lane | Name | Scope | Prerequisites |
|---|---|---|---|
| A0 | Adapter prototype | `Custom` units, typed metadata, example adapter | M0 raw/resource/projection substrate |
| A1 | Cognitive trace contracts | runtime origin, causal, perspective, epistemic components, sequence filters | M1b components and graph substrate; M2+ for bi-temporal |
| A2 | Task, Decision and Procedure | formal payloads, procedure activation, capability refs, outcome stats | M1b + activation metadata; M1c for background validation jobs |
| A3 | Partition and reconciliation | replica stamps, import/export, semantic conflicts, merge tests | M2/M2+ lifecycle governance |
| A4 | Plasticity support | procedure mining, topology mutation evidence, introspection snapshots, rollback records | external runtime policy; memory stores evidence only |

## ADELIA Reference Adapter

ADELIA integration should live in an adapter package. The adapter maps:

```text
ADELIA runtime/node/character/capability ids -> RuntimeObjectRef
ADELIA traces/spans                         -> RuntimeOriginComponent
ADELIA focus/task state                     -> FocusContextComponent / TaskPayload
ADELIA decisions/actions/outcomes           -> DecisionPayload + causal edges
ADELIA procedure proposals                  -> ProcedurePayload candidates
```

The adapter may depend on ADELIA. `agent-memory-cpp` core must not.
