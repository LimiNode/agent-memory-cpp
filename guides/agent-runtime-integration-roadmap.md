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
4. Runtime traces and retrieval traces link through origin-qualified trace/span
   references.
5. Causal relations are not inferred only from timestamps.
6. `Hypothesis` is not returned as `ValidatedClaim` without explicit policy.
7. Raw evidence is append-only except explicit erase policy.
8. Derived state must cite evidence/provenance.
9. Procedure activation does not execute the procedure.
10. Memory reconciliation preserves conflicts unless runtime policy resolves
    them.
11. A0-A2 do not add per-component DBIs.
12. Core contracts do not include ADELIA headers.
13. `KnowledgeUnitId` is a local storage key, not a replicated identity.
14. Runtime sequence values are ordered only within one runtime/replica origin.

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
    Replica = 13,
    Event = 14
};

struct RuntimeObjectRef {
    std::string adapter_id;           // e.g. "adelia"
    std::string runtime_instance_id;  // concrete world/runtime instance
    std::optional<std::string> replica_id;
    RuntimeObjectKind kind = RuntimeObjectKind::Runtime;
    std::string opaque_id;
    std::optional<std::uint64_t> revision;
};

// Canonical origin for an append-only runtime sequence. It deliberately does
// not reuse RuntimeObjectRef: a sequence belongs to a runtime/replica pair,
// not to a replica object with its own opaque_id and optional revision.
struct RuntimeOriginKey {
    std::string adapter_id;
    std::string runtime_instance_id;
    std::string replica_id;
};
```

An ADELIA adapter maps runtime-native node identifiers to neutral refs:

```text
native node identifier -> RuntimeObjectRef{"adelia", runtime_id, replica_id, Node, "...", revision}
```

The mapping lives in the adapter, not in core headers.

Stable object identity is `(adapter_id, runtime_instance_id, replica_id, kind,
opaque_id)`. `revision` is an optional observed-version/freshness value and is
never part of equality, ordering, a persisted key, a filter encoding, or an
origin comparison. `replica_id` is required when `opaque_id` is only
replica-local. If an adapter omits `replica_id`, it must guarantee that
`opaque_id` is globally unique within the runtime instance for that kind.
For `RuntimeObjectKind::Replica`, `opaque_id` is the replica identifier and
`replica_id` is always absent. A containing runtime object carries that replica
identifier in its `replica_id` field. This gives every replica exactly one
stable tuple and byte encoding; adapters normalize or reject the redundant
legacy form where `replica_id == opaque_id` before equality, persistence or
import/export validation.

Adapters must reject an empty `adapter_id`, `runtime_instance_id` or
`opaque_id`. `RuntimeOriginKey` has non-empty fields and is normalized once for
equality, keys, import/export validation and filters. A conversion from a
`RuntimeObjectRef` runtime/replica pair validates `Runtime` and `Replica` kinds,
matching adapter/runtime ids, and requires
`runtime_instance.replica_id == replica.opaque_id`. The optional object revision
may be exposed to callers for stale observation detection, but changing it must
not make a different runtime object or sequence origin.

## Replicated Identity And Runtime Time

`KnowledgeUnitId` remains the compact local MDBX key. It is valid only inside
one physical database/environment. The common `GlobalKnowledgeUnitId`,
`KnowledgeUnitRef`, and `GlobalIdentityComponent` contract is owned by the
[knowledge-unit roadmap](knowledge-units-roadmap.md#common-durable-knowledge-unit-identity),
not by this optional runtime integration. ADELIA adapters reuse that common
occurrence identity for causal records, import/export, and reconciliation; they
do not define a competing runtime-specific identity type.

Runtime log positions are origin-scoped:

```cpp
struct RuntimeSequence {
    RuntimeOriginKey origin;
    std::uint64_t value = 0;
};

struct RuntimeSequenceRange {
    RuntimeOriginKey origin;
    std::optional<std::uint64_t> from;
    std::optional<std::uint64_t> until;
};

struct RuntimeTraceRef {
    RuntimeOriginKey origin;
    std::string trace_id;
    std::optional<std::string> span_id;
};
```

`RuntimeSequenceRange` is the inclusive interval `[from, until]`. An absent
`from` is unbounded below, an absent `until` is unbounded above, and both absent
means all sequence values for the validated origin. When both bounds are
present, `from <= until` is required. Sequence values may be compared
numerically only when their `RuntimeOriginKey` values match; cross-origin order
requires causal edges or a separate vector-clock/HLC contract.

`RuntimeTraceRef` is an exact stable-identity reference. `trace_id` and
`span_id` are not globally unique strings: their equality and index encoding
always include `origin`.

```cpp
struct KnowledgeVisibilityReceipt {
    KnowledgeUnitRef unit;
    RuntimeOriginKey origin;
    std::uint64_t visible_at_sequence = 0;
    std::int64_t recorded_at_ms = 0;
    std::optional<KnowledgeUnitRef> import_or_reconciliation_evidence;
};
```

`KnowledgeVisibilityReceipt` is append-only and records when a concrete origin
could first use an occurrence, including after semantic import or
reconciliation. `KnownAtSequence(origin, cutoff)` includes a unit only when an
applicable receipt has `visible_at_sequence <= cutoff`; unknown origin-local
visibility is excluded. A producer sequence is not substituted for another
replica's receipt.

The A1 `runtime_sequence_index` is also the required physical path for these
receipts. Its key carries an explicit discriminator:

```text
(scope_id, VisibilityReceipt, encoded_origin, visible_at_sequence,
 GlobalKnowledgeUnitId) -> KnowledgeVisibilityReceipt
```

Producer event rows use the same range substrate with the `ProducerEvent`
discriminator. The physical DBI is mixed: `VisibilityReceipt` rows are durable
authoritative replay evidence, while `ProducerEvent` rows are rebuildable
acceleration derived from canonical units/components. Its logical adapter
exports and imports every receipt as an append-only record identified by
`(GlobalKnowledgeUnitId, RuntimeOriginKey, visible_at_sequence)`, preserving
`recorded_at_ms` and import/reconciliation evidence. During import the local
`KnowledgeUnitRef` must rebind through `GlobalKnowledgeUnitId` and fail closed
on an incompatible identity scheme or a conflicting duplicate receipt. It must
not substitute a producer sequence for a receipt. A profile without this index
must reject `KnownAtSequence` as an indexed query; it may expose a separately
named scan-only experiment, but must not silently claim bounded replay.

For normal replay, a unit is visible at a cutoff only if a receipt for that
origin is at or before the cutoff and no invalidation, reconciliation, or
superseding transition visible to the same origin is at or before that cutoff.
Later transitions do not alter an earlier replay. Audit mode may return the
historical unit together with its visible invalidation/reconciliation evidence;
ordinary context construction excludes it after the transition.

## Components

All A0-A2 components use the existing `unit_components`
`TypeDiscriminatedTable<ComponentKind, UnitId, TypedComponentValue>` substrate.
No new DBI is allocated for each component.

### RuntimeOriginComponent

```cpp
struct RuntimeOriginComponent {
    RuntimeObjectRef runtime_instance;
    std::optional<RuntimeObjectRef> producer_node;

    std::string run_id;
    std::optional<RuntimeTraceRef> trace;

    std::optional<RuntimeSequence> event_sequence;
    std::optional<RuntimeOriginKey> sequence_origin;

    std::optional<RuntimeObjectRef> partition;
    std::optional<RuntimeObjectRef> replica;
};
```

If `sequence_origin` or `event_sequence` is present, it must describe the same
normalized origin as the runtime/replica object observations. A mismatch is a
validation error, not a merge policy choice.

`validate_runtime_origin()` is the common validation rule for every A-lane
record. It requires non-empty stable identities and validates
`runtime_instance.kind == Runtime`, `replica.kind == Replica`, and
`producer_node.kind == Node` when present. Optional producer, partition and
replica references must belong to the same stable `(adapter_id,
runtime_instance_id, replica_id)` origin tuple; optional revisions are observed
state and never participate in equality. `RuntimeSequence` and
`RuntimeSequenceRange` use the same tuple. A cross-origin reference is explicit
causal evidence, not a valid local origin field.
When present, `RuntimeOriginComponent.trace.origin` must equal this normalized
origin.

### CausalContextComponent

```cpp
struct CausalContextComponent {
    std::vector<KnowledgeUnitRef> direct_cause_units;
    std::vector<RuntimeObjectRef> direct_cause_events;  // kind = Event

    std::string correlation_id;
    std::string causation_id;

    std::optional<RuntimeSequence> local_log_position;
};
```

### Authority Evidence

Persisted authority metadata is historical evidence, not a current permission
grant. The runtime must re-check live authorization before executing any
action, especially after replay, import or reconciliation.

```cpp
enum class AuthorityEvidenceKind : std::uint8_t {
    Claimed = 1,
    Observed = 2,
    RuntimeVerified = 3,
    OperatorGranted = 4
};

struct AuthorityEvidenceRef {
    RuntimeObjectRef authority;
    AuthorityEvidenceKind evidence_kind = AuthorityEvidenceKind::Observed;
    std::string historical_receipt_id;
    std::int64_t observed_at_ms = 0;
    std::optional<std::int64_t> expires_at_ms;
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
    std::optional<AuthorityEvidenceRef> authority_evidence;

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
    std::optional<double> confidence;

    std::string producer_model_id;
    std::string producer_model_version;

    std::vector<KnowledgeUnitRef> supporting_units;
    std::vector<KnowledgeUnitRef> contradicting_units;
};
```

Retrieval must not automatically mix `Hypothesis` and `ValidatedClaim`.

### Epistemic Validation Matrix

No A-lane cognitive record may create privileged truth from an unbound local
projection. Validation is fail-closed before persistence:

| Record/layer | Required validation |
|---|---|
| Every A-lane cognitive record | `RuntimeOriginComponent` with a valid producer origin; every supplied confidence and coverage value is finite and in `[0, 1]` |
| `LocalPresentation`, `Interpretation`, `Hypothesis`, `NarrativeSummary` | `PerspectiveComponent`; non-empty producer id/version; at least one durable evidence or provenance reference; derived layers require supplied confidence |
| `SelfModel` / `OtherModel` perspective | `observer` and `represented_subject` are stored separately; they may not be inferred to be the same object |
| `ValidatedClaim` | non-empty validation policy id/version plus durable validation evidence; a hypothesis is not promoted merely by confidence |
| `SystemReconstruction` | remains a perspective-bound reconstruction and cannot auto-promote to `ValidatedClaim` |

Absent confidence or evidence never implies `confidence = 1.0`. A record that cannot satisfy
its required row is rejected or retained only as explicitly marked raw
ingestion data; it must not be persisted as a validated cognitive claim.

### BiTemporalComponent

The canonical M2+ bi-temporal component lives in
[`memory-lifecycle-governance-roadmap.md`](memory-lifecycle-governance-roadmap.md)
AM-13. This roadmap does not define a second temporal source of truth. A-lane
runtime knowledge time uses that component's `recorded_at_ms`,
`invalidated_at_ms`, `recorded_sequence` and `invalidated_sequence` fields.

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

    std::optional<RuntimeObjectRef> requested_by;
    std::optional<RuntimeObjectRef> parent_task;

    std::vector<CapabilityRef> required_capabilities;
    std::vector<KnowledgeUnitRef> constraints;

    std::optional<std::int64_t> deadline_ms;
};

struct TaskAssignmentLeaseEvidence {
    KnowledgeUnitRef task;
    RuntimeObjectRef assignee;
    RuntimeOriginComponent origin;
    std::int64_t granted_at_ms = 0;
    std::int64_t expires_at_ms = 0;
    std::vector<KnowledgeUnitRef> evidence;
    std::optional<std::string> external_receipt;
};

struct TaskCancellationOutcome {
    KnowledgeUnitRef task;
    RuntimeObjectRef actor;
    RuntimeOriginComponent origin;
    std::string outcome_kind;
    std::vector<KnowledgeUnitRef> evidence;
    std::optional<std::string> external_receipt;
};

struct TaskStateComponent {
    TaskStatus current_status = TaskStatus::Proposed;
    std::uint64_t state_revision = 0;
    std::optional<std::int32_t> priority;
    std::optional<RuntimeObjectRef> assigned_to;
    std::optional<std::int64_t> assignment_lease_expires_at_ms;
    std::optional<KnowledgeUnitRef> assignment_lease_evidence;
    std::optional<KnowledgeUnitRef> cancellation_outcome;
    std::vector<KnowledgeUnitRef> produced_units;
    std::optional<KnowledgeUnitRef> last_transition;
};
```

`TaskPayload` is the stable task definition. Status transitions are append-only
events plus `TaskStateComponent`; they do not mutate the definition just to
record runtime progress.

```cpp
enum class StateMachineSubjectKind : std::uint8_t { Task, Procedure };

struct DurableStateTransition {
    KnowledgeUnitRef transition;
    KnowledgeUnitRef subject;
    StateMachineSubjectKind subject_kind = StateMachineSubjectKind::Task;
    std::uint64_t expected_state_revision = 0;
    std::uint64_t resulting_state_revision = 0;
    std::uint16_t from_status = 0;
    std::uint16_t to_status = 0;
    RuntimeObjectRef actor;
    RuntimeOriginComponent origin;
    std::optional<std::string> external_receipt;
    std::optional<std::string> external_state_revision;
    std::vector<KnowledgeUnitRef> evidence;
    std::vector<KnowledgeUnitRef> state_change_evidence;
};
```

The application/profile layer owns this durable task/procedure state-machine
contract: it validates the declared transition graph, compares
`expected_state_revision` with CAS semantics, and persists transition
provenance. It atomically appends `DurableStateTransition`, advances the
subject state component to `resulting_state_revision`, and updates
`last_transition`; a CAS conflict appends nothing. The transition's `subject`
must resolve to the same occurrence as the mutated task/procedure record, and
`resulting_state_revision == expected_state_revision + 1`. Every accepted
transition records durable runtime origin, actor, and any available external
receipt/revision. The memory library does not schedule work, grant authority,
invoke a capability or decide whether an external runtime should perform the
transition; those remain runtime/operator responsibilities.
`mdbx-containers` supplies only the generic transactional storage primitives
used by this application-owned contract.

Priority and assignment leases are durable application-owned observations, not
a scheduler: the application decides assignment, renewal, expiry handling and
queue ordering. A cancellation transition records its structured outcome and
evidence; the library never invokes or interrupts external work.

`TaskStateComponent` is valid only when `assigned_to`,
`assignment_lease_expires_at_ms`, and `assignment_lease_evidence` are either all
present or all absent. The referenced `TaskAssignmentLeaseEvidence` must name
the same task occurrence, assignee, expiry and normalized origin. `Cancelled`
requires `cancellation_outcome`; every other status has no active cancellation
outcome. Any priority, lease, or cancellation change is represented by the
accepted `DurableStateTransition.state_change_evidence`, yielding an immutable
audit/reconciliation history rather than a mutable observation alone.

```cpp
struct DecisionAlternative {
    std::string alternative_id;
    std::string description;

    std::optional<double> predicted_utility;
    std::optional<double> predicted_risk;

    std::vector<KnowledgeUnitRef> supporting_units;
    std::vector<KnowledgeUnitRef> opposing_units;
};

struct DecisionPayload {
    std::vector<DecisionAlternative> alternatives;
};

struct DecisionSelectionComponent {
    std::string selected_alternative_id;
    std::string rationale_summary;
    std::optional<double> confidence;

    std::optional<RuntimeObjectRef> committed_by;
    std::optional<AuthorityEvidenceRef> authority_evidence;
    std::vector<RuntimeObjectRef> resulting_actions;  // kind = Action or Event
};
```

`TaskPayload` and `DecisionPayload` are immutable definitions. Assignment,
status, produced units, selected alternative, rationale, committer and resulting
actions are mutable observed state and therefore live in `TaskStateComponent` or
`DecisionSelectionComponent` with append-only transition evidence. Identity and
content-digest recipes include only the immutable definition, envelope identity
and declared immutable references; they exclude state revisions and all mutable
components. LLM-generated rationale is not a complete causal explanation.
Canonical causes come from `CausalContextComponent` and supporting units.

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

    std::uint64_t version = 1;

    std::vector<CapabilityRef> required_capabilities;
    std::vector<KnowledgeUnitRef> preconditions;

    std::string workflow_format;
    std::string workflow_reference;

    std::vector<KnowledgeUnitRef> source_episodes;
    std::vector<KnowledgeUnitRef> validation_cases;
};

struct ProcedureStateComponent {
    ProcedureStatus current_status = ProcedureStatus::Candidate;
    std::uint64_t state_revision = 0;
    std::optional<KnowledgeUnitRef> last_transition;
};

struct ProcedureStatsComponent {
    std::uint64_t activation_count = 0;
    std::uint64_t success_count = 0;
    std::uint64_t failure_count = 0;
    std::optional<double> estimated_success_probability;
    std::optional<double> mean_utility;
};
```

`ProcedureStateComponent` uses the same `DurableStateTransition` protocol with
`subject_kind = Procedure`. The application/profile layer owns durable state,
CAS and transition-history validation for the stored procedure record, while
the runtime owns execution, worker scheduling, capability invocation and
authority policy. A state transition without durable runtime provenance is a
validation error, not an invitation for the store to infer or execute work.

Do not persist function pointers, closures or ADELIA node handles in
`ProcedurePayload`.

## Typed Relations

Causal and runtime relations use application-owned `EdgeKind` values and
typed graph edge payloads. Important semantics must not live only in
`primary_text = "A caused B"`.

The common `GraphEdge` record is authoritative: it has a stable edge ID,
endpoints, kind, relation class, payload and evidence. The outgoing and incoming
graph DBIs are two physical orientations of this one record. Import/export
serializes one logical edge and reconstructs both orientations after global-ID
remapping; packed adjacency is only a rebuildable traversal optimization.

Every application graph edge also carries the common `RelationClass` declared
by `memory-lifecycle-governance-roadmap.md`. The runtime relation vocabulary
below is `Semantic` unless a caller explicitly records an evidence,
supersession or technical-lineage relation; ordinary graph expansion therefore
cannot cross those non-semantic classes by accident.

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
    std::vector<RuntimeObjectRef> runtime_instance_filter;
    std::vector<RuntimeOriginKey> origin_filter;
    std::vector<RuntimeObjectRef> observer_filter;
    std::vector<RuntimeObjectRef> character_filter;
    std::vector<RuntimeObjectRef> producer_node_filter;
    std::vector<RuntimeTraceRef> trace_filter;

    std::vector<RuntimeSequenceRange> sequence_ranges;

    std::vector<EpistemicLayer> epistemic_layers;

    bool require_evidence = false;
    bool include_conflicts = true;
    bool include_superseded = false;
};
```

Every supplied runtime filter is an exact stable-identity predicate; optional
revisions are ignored. `runtime_instance_filter`, `origin_filter` and each
`RuntimeSequenceRange` intersect, and a sequence range is valid only for the
same runtime/replica origin selected by those filters. A plan that names
incompatible origins is invalid rather than an empty best-effort query.

Physical index mapping:

| Filter field | Canonical substrate | Logical key shape |
|---|---|---|
| `trace_filter` | `metadata_filters` | `(scope_id, RuntimeTrace, encoded_origin, trace_id, span_id) -> UnitId` |
| `runtime_instance` | `metadata_filters` | `(scope_id, RuntimeInstance, encoded_runtime_ref) -> UnitId` |
| `producer_node` | `metadata_filters` | `(scope_id, RuntimeProducer, encoded_runtime_ref) -> UnitId` |
| `observer` | `metadata_filters` | `(scope_id, RuntimeObserver, encoded_runtime_ref) -> UnitId` |
| `character` | `metadata_filters` | `(scope_id, RuntimeCharacter, encoded_runtime_ref) -> UnitId` |
| `epistemic_layer` | `metadata_filters` | `(scope_id, EpistemicLayer, layer) -> UnitId` |
| `procedure_status` | `metadata_filters` | `(scope_id, ProcedureStatus, status) -> UnitId` |
| `replica` | `metadata_filters` | `(scope_id, RuntimeReplica, encoded_runtime_ref) -> UnitId` |
| `event_sequence` | `runtime_sequence_index` profile delta | `(scope_id, ProducerEvent, encoded_origin, sequence, UnitId)` |
| visibility receipt | `runtime_sequence_index` profile delta | `(scope_id, VisibilityReceipt, encoded_origin, sequence, GlobalKnowledgeUnitId)` |

The metadata rows are generic secondary keys, not per-component DBIs.
Sequence rows are different: the A1 `runtime_sequence_index` profile delta owns
their range keys, because neither metadata filtering nor temporal wall-clock
indexes can implement origin-scoped sequence ranges. Its logical adapter treats
visibility receipts as durable records and producer-event rows as rebuildable
acceleration. A profile without that delta may not advertise
`KnowledgeAtSequence` as indexed or latency-bounded; it must reject the filter
or document a deliberately scan-backed experimental path.

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

`DrillDownRef::target_unit` is an ephemeral local retrieval handle. It is valid
only inside the current database/session response and must not be serialized as
a durable cross-replica reference. Durable drill-down references use
`KnowledgeUnitRef`.

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
execution request. Its canonical value-type contract is owned by
[`knowledge-activation-roadmap.md`](knowledge-activation-roadmap.md); this
roadmap maps its `missing_capabilities` to neutral `RuntimeObjectRef` values and
keeps execution outside memory.

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

struct ReconciliationResolution {
    KnowledgeUnitRef left;
    KnowledgeUnitRef right;
    RuntimeObjectRef resolved_by;
    RuntimeOriginComponent origin;
    std::string policy_id;
    std::string policy_version;
    std::vector<KnowledgeUnitRef> evidence;
    std::optional<std::string> external_receipt;
};

struct ReconciliationConflict {
    KnowledgeUnitRef left;
    KnowledgeUnitRef right;

    ReconciliationRelation relation = ReconciliationRelation::Incomparable;
    std::vector<KnowledgeUnitRef> evidence;
    std::optional<KnowledgeUnitRef> resolution;
};
```

Duplicate content does not imply duplicate occurrence. Two records with the
same content hash may remain distinct if their `GlobalKnowledgeUnitId`,
runtime origin or causal context differs.

An unset `resolution` means the conflict remains retrieval-visible. A set value
must resolve to an immutable `ReconciliationResolution` for the same ordered
pair or explicitly documented symmetric pair, with resolver, policy version,
origin and evidence. The boolean state is derived from the presence of that
record and never overwrites the unresolved evidence.

Erase/tombstone merge is monotonic. If one partition carries an authorized
erase tombstone, merge must not resurrect the erased sensitive record. Derived
projections, embeddings, summaries and materialized states may be rebuilt
locally from canonical records; they do not have to be replicated as canonical
truth.

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
| sequence filtering and durable visibility receipts | `runtime_sequence_index` profile delta with logical adapter |
| raw event payload | `ResourceBodyStore` |
| causal relations | `graph_edges_by_src` / `graph_edges_by_dst` |
| conflicts | units + graph relations |
| global knowledge-unit identity | `unit_components` + `global_unit_id_to_local_id` profile delta |

Do not add a separate `runtime_event_index` or revive `temporal_event_index`:
the only A-lane materialized range key is the explicitly budgeted
`runtime_sequence_index` profile delta. `global_unit_id_to_local_id` is an M1b
`DurableGlobalIdentity` capability reusable by A-lane profiles, rather than an
overloading of a scope-local metadata index. If partition support later needs more materialized
DBIs, `replica_frontiers` and `reconciliation_jobs` are the first candidates,
but they are not in the canonical or expanded budget until a concrete
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
| `ImportLocalIdCollision` | Same local IDs from different workspaces bind to their distinct global occurrence IDs |
| `ImportLocalIdRemap` | Import remaps a local ID without changing the referenced global occurrence ID |
| `IdentitySchemeMismatch` | Incompatible identity scheme fails before reconciliation records are written |
| `VisibilityReceiptRoundTrip` | Export/import preserves the origin-local receipt and `KnownAtSequence` cutoff; a producer event may not replace it |
| `EqualContentDistinctOccurrences` | Equal content with distinct global occurrence IDs remains distinct after import |
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
| A1 | Cognitive trace contracts | runtime origin, causal, perspective, epistemic components, sequence filters | M1b components, FullSourceRefs, graph substrate, DurableGlobalIdentity and identity-scheme validation; M2+ for bi-temporal |
| A2 | Task, Decision and Procedure | formal payloads, procedure activation, capability refs, outcome stats | M1b + activation metadata; M1c for background validation jobs |
| A3 | Partition and reconciliation | replica stamps, import/export, semantic conflicts, merge tests, identity/remap fixtures | M1b DurableGlobalIdentity and identity-scheme validation; M2/M2+ lifecycle governance |
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
