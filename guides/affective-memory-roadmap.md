# Affective Memory Roadmap

This document defines an optional roadmap lane for storing affectively
meaningful agent memories. It does **not** turn `agent-memory-cpp` into an
emotion engine or an autonomous-agent framework. The intended boundary is:

```text
agent-affect runtime
  live needs, goals, appraisal, affect dynamics, coping, behaviour choice

agent-memory-cpp
  persisted events, snapshots, transitions, outcomes, relationship evidence,
  retrieval and compaction contracts
```

The library should remain a universal embedded memory/retrieval toolkit. The
affective lane is an opt-in profile extension for projects that need durable
emotional context, relationship history, and outcome-aware episode recall.

Generic runtime origin, causal context, perspective, epistemic status,
task/decision/procedure payloads and reconciliation live in
[`agent-runtime-integration-roadmap.md`](agent-runtime-integration-roadmap.md).
This document only adds affective appraisal, coping, relationship and
sensitive-inference semantics on top of those generic contracts.

## ADR-A01 — Live affect is runtime state

The current affective state of an agent is not a `KnowledgeUnit` and must not
be reconstructed by naive retrieval of old snapshots.

Memory stores:

- which event happened;
- which appraisal was recorded;
- which goals were affected;
- what affect snapshot existed before/after the transition;
- which action or coping strategy was chosen;
- what outcome and utility prediction error followed.

The live state belongs to a sibling runtime controller:

```cpp
AffectController affect;
AffectState current = affect.state();
```

The memory stack may persist snapshots and transition records, but it does not
own the controller dynamics.

## ADR-A02 — Appraisal precedes emotion labels

Discrete labels such as `"angry"`, `"sad"`, or `"relieved"` are derived
interpretations. The durable primary data is structured appraisal: goal
relevance, goal congruence, certainty, controllability, responsibility,
urgency, social significance, and confidence.

This keeps memory useful across affect models and avoids baking one emotion
taxonomy into the storage layer.

## ADR-A03 — Self and inferred user affect are separate

Agent self-state, user self-report, and inferred user affect must never be
stored in the same field. Every appraisal or affect snapshot carries both the
subject of the state and how the evidence was obtained. These are two separate
axes:

```cpp
struct AffectSubjectRef {
    ScopeId scope_id;
    std::string subject_id;
};

enum class AffectEvidenceKind : std::uint8_t {
    AgentInternal,
    SelfReported,
    Inferred,
    SensorDerived
};
```

`subject_id` identifies the agent, user, participant, or other entity whose
state is being described. `evidence_kind` explains whether the value came from
agent runtime state, a user self-report, an inference, or an external signal.

Inferences about a user's emotional or mental state require confidence,
provenance, model identity, and retention policy. They must not be presented as
facts unless the source explicitly supports that.

## ADR-A04 — Episodes are append-only

Affective transitions are event-sourced. Old transition records are not
rewritten when the controller is improved or when a relationship state is
recomputed.

Conceptual record:

```cpp
struct AffectTransitionRecord {
    std::uint64_t sequence = 0;
    std::optional<KnowledgeUnitId> trigger_event_id;
    std::optional<KnowledgeUnitId> transition_unit_id;
    std::string controller_version;
    std::uint64_t config_hash = 0;
};
```

Changing controller parameters may produce new derived state, but it must not
silently mutate history.

Append-only applies to the raw transition log. Derived retrieval units such as
episode summaries, relationship materialized views, and coping statistics may
be superseded or recomputed as long as they retain evidence links back to the
raw records that produced them. If a retention policy physically deletes raw
transitions, full replay before that deletion point is no longer guaranteed.

## ADR-A05 — Affect may modulate retrieval, not truth

Affective context can influence retrieval and reranking:

- similar appraisal dimensions;
- same target entity or relationship;
- same impacted goal;
- unresolved episodes;
- similar utility prediction error;
- prior coping outcome.

It must not change factual confidence, provenance, or safety policy. Mood
congruence may be a small capped factor, not a dominant loop:

```text
score += w_mood * mood_congruence
where abs(w_mood) is deliberately bounded
```

This avoids artificial rumination where negative state retrieves negative
memories, which further amplifies negative state.

## ADR-A06 — Compaction preserves causal structure

Affective episodes must not be merged solely because their text is similar.
Compaction must preserve at least:

- trigger;
- actor/target;
- appraisal;
- impacted goals;
- peak affect snapshot;
- action or coping strategy;
- outcome;
- utility prediction error;
- unresolvedness;
- relationship evidence.

Two textually similar events can have different affective meaning if
responsibility, controllability, outcome, or relationship target differs.

## ADR-A07 — Sensitive inference requires provenance

Sensitive emotional, mental-health, or relationship inferences need explicit
policy metadata:

```cpp
enum class SensitivityClass : std::uint8_t {
    Normal,
    Personal,
    Sensitive,
    CrisisRelated
};

struct RetentionPolicyComponent {
    SensitivityClass sensitivity = SensitivityClass::Normal;
    bool allow_personalization = true;
    bool allow_model_training = false;
    bool allow_long_term_storage = true;
    std::optional<std::uint64_t> erase_after_ms;
};
```

Every inferred user-state component should also carry confidence, provenance,
model/version, observation time, and deletion support.

Unknown values are not encoded as `0.0`. Scalar dimensions use
`std::optional<double>` until a compact presence-mask representation is justified
by benchmarks. `NaN` and infinities are invalid. `valence` and
`goal_congruence` use `[-1.0, 1.0]`; other normalized dimensions use
`[0.0, 1.0]` unless their field contract says otherwise. Confidence for
inferred values is optional and must be supplied by the producing model or
policy; a partially populated inferred record must not silently become
max-confidence.

## ADR-A08 — Sensitive local memory needs optional encryption-at-rest

Affective memory is likely to contain unusually sensitive material: inferred
emotional state, relationship evidence, conflict episodes, mental-health
signals, and private conversation summaries. Projects that enable this lane
should be able to opt into encrypted local persistence.

The memory library should expose encryption as a storage/security capability,
not as a mandatory behaviour of every profile:

- use authenticated encryption, not unauthenticated ciphertext;
- keep key derivation and key storage policy outside the retrieval layer;
- record encryption scheme, key identity, and rotation metadata in profile
  metadata;
- support machine-local secrets for local single-user deployments;
- support user-password or external-KMS key providers for stricter deployments;
- preserve crash safety for file-backed artifacts through temp-file plus atomic
  replace where applicable;
- rely on MDBX transaction durability for database pages, while still allowing
  encrypted value payloads or encrypted artifact blobs.

AES-256-GCM with a memory-hard password KDF is a reasonable first candidate for
local encrypted artifacts, but the roadmap should name the capability as AEAD
encryption-at-rest rather than freezing one crypto construction too early.
`EncryptionPolicy` in [`policies-roadmap.md`](policies-roadmap.md) owns the
threat model, key provider, encryption scope, nonce uniqueness, AAD binding,
and rotation details.

Encryption does not replace retention, deletion, provenance, or consent
policies. It only reduces exposure if local files are copied or inspected
outside the running process.

Encrypted values are not the same as an encrypted database. DBI names, key
ordering, record counts, payload sizes, timestamps, update frequency, access
patterns, and some indexes may remain visible unless a stronger deployment
scope is selected.

## ADR-A09 — Live-agent context depth is urgency/recall/risk-gated

Affective agents often run in low-latency conversations. A direct live-chat
mention should not pay the same retrieval cost as a background reflection job.

The runtime should provide a policy layer that maps incoming-event urgency,
recall requirement, correctness risk, and latency budget to a memory access
plan:

```text
high urgency + low recall need       -> short + base context only
high urgency + required recall       -> quick acknowledgement + targeted long retrieval
safety-critical / high omission cost -> mandatory tiers cannot be skipped
reflective/background task           -> short + medium + long + graph/wiki
```

This is not a replacement for retrieval. It is a pre-retrieval planning step
that decides which tiers and retrievers are allowed for this turn. The memory
library provides the plan/result contracts; the application runtime owns the
urgency score, recall-intent detection, risk classification, and final
conversational behaviour.

## Optional capabilities

These capabilities are opt-in extensions to `AgentLongTermMemory`, not a new
mandatory baseline:

```text
AffectiveEpisodes
GoalAttribution
OutcomeTracking
RelationshipState
SensitiveInferencePolicy
```

The important decision is the capability grouping: do not add a separate
capability for every scalar appraisal field. Canonical bit positions live in
`memory-stacks-roadmap.md` once the schema is implemented.

Encryption-at-rest and context planning are cross-cutting capabilities, not
affective capabilities. They are useful for ordinary RAG and agent memory
profiles too, and are specified through `EncryptionPolicy` and
`MemoryAwareContextPlanner`.

## Candidate components

The first implementation should prototype these through `Custom` units and
typed metadata before freezing dedicated stores or DBIs.

### AppraisalComponent

```cpp
struct AppraisalComponent {
    AffectSubjectRef subject;
    AffectEvidenceKind evidence_kind = AffectEvidenceKind::Inferred;

    std::optional<double> novelty;
    std::optional<double> goal_relevance;
    std::optional<double> goal_congruence; // [-1, 1]
    std::optional<double> certainty;
    std::optional<double> controllability;
    std::optional<double> reversibility;
    std::optional<double> urgency;

    std::optional<double> responsibility_self;
    std::optional<double> responsibility_other;
    std::optional<double> norm_violation;
    std::optional<double> social_significance;

    std::optional<double> inference_confidence;
    std::string appraisal_model_id;
    std::vector<KnowledgeUnitId> evidence_units;
};
```

### AffectSnapshotComponent

```cpp
struct AffectSnapshotComponent {
    AffectSubjectRef subject;
    AffectEvidenceKind evidence_kind = AffectEvidenceKind::Inferred;

    std::optional<double> valence; // [-1, 1]
    std::optional<double> arousal;
    std::optional<double> control;
    std::optional<double> certainty;
    std::optional<double> affiliation;
    std::optional<double> cognitive_load;
    std::optional<double> allostatic_load;

    std::uint64_t captured_at_ms = 0;
    std::string affect_model_id;
    std::optional<double> inference_confidence;
    std::vector<KnowledgeUnitId> evidence_units;
};
```

`allostatic_load` is an internal resource-pressure estimate for agent runtime
use, not an objectively measured biological quantity. It requires a definition,
model identity, and confidence before it is interpreted outside the producing
runtime.

Emotion names can be stored as a derived interpretation:

```cpp
struct EmotionLabel {
    std::string label;
    double confidence = 0.0;
};

struct EmotionInterpretationComponent {
    std::vector<EmotionLabel> candidates;
};
```

### GoalImpactComponent

```cpp
struct GoalRef {
    ScopeId scope_id;
    std::string goal_id;
    std::uint64_t goal_version = 0;
    std::string goal_snapshot_text;
};

struct GoalImpact {
    GoalRef goal;
    std::optional<double> importance;
    std::optional<double> success_probability_before;
    std::optional<double> success_probability_after;
    std::optional<double> information_deficit;
    std::optional<double> resource_deficit;
};

struct GoalImpactComponent {
    std::vector<GoalImpact> impacts;
};
```

The active goal catalogue stays in the planning/affect runtime. Memory stores
which goals a concrete episode affected.

### ActionOutcomeComponent

Generic action/decision/outcome identity and causal links are specified by
`DecisionPayload`, `CausalContextComponent` and runtime graph relations in
[`agent-runtime-integration-roadmap.md`](agent-runtime-integration-roadmap.md).
The component below is the affective overlay: coping strategy, utility
prediction error and affective outcome interpretation.

```cpp
enum class CopingStrategy : std::uint8_t {
    None,
    ProblemFocused,
    InformationSeeking,
    Reappraisal,
    Acceptance,
    AttentionShift,
    ExpressionControl,
    SocialRepair,
    HelpSeeking
};

struct ActionOutcomeComponent {
    std::string action_id;
    CopingStrategy coping = CopingStrategy::None;

    std::optional<double> predicted_utility;
    std::optional<double> predicted_success_probability;
    std::optional<double> actual_utility;
    std::optional<double> utility_prediction_error;

    bool completed = false;
    bool resolved_trigger = false;
    std::string utility_model_id;
    std::string outcome_model_id;
    std::optional<double> outcome_confidence;
    std::vector<KnowledgeUnitId> evidence_units;
    std::uint64_t observed_at_ms = 0;
    std::uint64_t evaluated_at_ms = 0;
};
```

`utility_prediction_error = actual_utility - predicted_utility` under
`utility_model_id` and that model's normalization contract. If either side is
absent, `utility_prediction_error` is absent too. Use the longer field name to
avoid confusing this value with temporal-difference error, sensory prediction
error, or state-prediction error.

This makes the causal chain inspectable:

```text
event -> appraisal -> affect before/after -> action -> outcome
```

### SalienceComponent

`priority_weight`, usage statistics, and emotional salience are different
signals. Do not collapse them.

```cpp
struct SalienceComponent {
    std::optional<double> surprise;
    std::optional<double> emotional_intensity;
    std::optional<double> goal_relevance;
    std::optional<double> social_significance;
    std::optional<double> unresolvedness;
    std::optional<double> utility_prediction_error;
    std::optional<double> encoding_strength;
};
```

Retrieval boost is a derived policy output, not a durable source fact. If an
implementation persists a boost for diagnostics, it must also persist the
policy version that produced it.

### RelationshipStateComponent

Relationship state is slow-moving derived state backed by evidence units:

```cpp
struct RelationshipStateComponent {
    std::string target_entity_id;

    std::optional<double> trust;
    std::optional<double> affiliation;
    std::optional<double> familiarity;
    std::optional<double> perceived_threat;
    std::optional<double> reciprocity;
    std::optional<double> boundary_risk;

    std::optional<double> confidence;
    std::uint64_t updated_at_ms = 0;
    std::vector<KnowledgeUnitId> evidence_units;
    std::vector<KnowledgeUnitId> negative_evidence_units;
    std::vector<KnowledgeUnitId> superseded_evidence_units;
};
```

`evidence_units` is mandatory for explainability and correction. Relationship
state is an agent belief unless separately confirmed by the user. It must
support negative evidence, superseded evidence, manual correction, and decay
policy rather than treating every old signal as equally current.

## Profile direction

`AffectiveAgentMemory` should be an overlay on `AgentLongTermMemory`:

```cpp
inline MemoryProfileSpec AffectiveAgentMemory() {
    MemoryProfileSpec spec = MemoryProfiles::AgentLongTermMemory();
    spec.name = "affective_agent_ltm";

    spec.enable_speaker = true;
    spec.enable_conversation_episode = true;
    spec.enable_affective_episodes = true;
    spec.enable_goal_attribution = true;
    spec.enable_outcome_tracking = true;
    spec.enable_relationship_state = true;
    spec.enable_sensitive_inference_policy = true;

    return spec;
}
```

This profile is not a universal default. It is for agents that need durable
relationship and affective episode memory.

## Live-agent context tier overlay

The affective profile should be able to use the same durable stores as ordinary
agent memory while exposing a latency-oriented context tier overlay:

| Context tier | Intended contents | Typical use |
|---|---|---|
| `short` | current session, recent turns, active goals, unresolved immediate events | live response, direct mention, interruption |
| `medium` | recent episode summaries, active relationship evidence, short-term commitments | ordinary user questions, continuity |
| `long` | durable facts, older episodes, graph relations, semantic KB | deliberate recall, reflection, planning |
| `base` | read-only persona, policy, project facts, stable compiled knowledge | addressable grounding |

These are context-planning tiers, not necessarily separate database files. A
single `MemoryStack` may back several tiers through filters, recency windows,
compiled summaries, and retrieval budgets.

`base` means always addressable, not always fully injected. A small immutable
system prefix may be present on every turn, while larger persona, policy, and
compiled knowledge blocks should use prefix caching, references, or targeted
retrieval to avoid token bloat.

This gives a clean hybrid:

- live-agent tiers provide low latency and avoid prompt bloat;
- compiled wiki/base layers provide slow-cycle long-term synthesis and
  cross-agent reusable knowledge;
- the runtime planner chooses the depth per turn.

## Retrieval roadmap

Affect-aware retrieval should start as rerank/evaluator policy over normal
hybrid retrieval, not as a new vector database.

Useful features:

- same target entity;
- same impacted goal;
- similar controllability/certainty/responsibility;
- unresolved episode match;
- same coping strategy;
- similar utility prediction error;
- relationship evidence proximity;
- capped mood-congruence.

Candidate scoring can extend existing hybrid retrieval:

```text
score =
  w_semantic * semantic_similarity
+ w_lexical  * lexical_similarity
+ w_target   * target_match
+ w_goal     * goal_overlap
+ w_appraise * appraisal_similarity
+ w_unres    * unresolvedness
+ w_error    * utility_prediction_error_similarity
+ capped(w_mood * mood_congruence)
- w_repeat   * repetition_penalty
```

The exact weights belong to runtime policy, not the storage layer.

## Compaction roadmap

Affective compaction must preserve causal structure. `MergeJob` and
`SummaryPromotionJob` need affective guardrails before they are enabled for
`AffectiveEpisodes`:

- do not merge if actor intention, target, goal impact, responsibility,
  controllability, outcome, relationship target, or resolution state differs;
- summaries must retain trigger, appraisal, goals, peak affect, action,
  outcome, utility prediction error, and unresolvedness;
- relationship summaries must retain evidence links.

## Implementation ladder

### E0 — Prototype through Custom

No public API freeze. Use `KnowledgeUnitKind::Custom` and typed metadata for:

- event;
- appraisal;
- affect before/after;
- goal impacts;
- action;
- outcome.

Add deterministic scenario and replay tests in a sibling affect-runtime project
or examples, not in core memory first.

### E1 — Formal components

Add typed component contracts once the fields stabilize:

- `AppraisalComponent`;
- `AffectSnapshotComponent`;
- `GoalImpactComponent`;
- `ActionOutcomeComponent`;
- `SalienceComponent`;
- `RetentionPolicyComponent`.

No affect-aware retrieval yet.

### E2 — Retrieval and relationship state

Add:

- `RelationshipStateComponent`;
- secondary indexes by `target_entity_id` and `goal_id`;
- appraisal similarity;
- unresolved episode retrieval;
- capped mood-congruent rerank.

### E3 — Compaction and outcome learning

Add:

- affective episode summaries;
- coping effectiveness statistics;
- utility-prediction-error aggregation;
- relationship evidence-chain maintenance.

### E4 — Security and live-context planning

Add optional, profile-gated support for:

- encrypted local artifact/value storage;
- key identity and rotation metadata;
- retention/deletion tests for sensitive inferred affect;
- urgency-aware context planning;
- short/medium/long/base retrieval tier plans;
- benchmark traces that report skipped deep retrieval due to latency budget.

## Research anchors

These references are anchors for future implementation research. They are not
new mandatory dependencies and do not turn this roadmap into a psychology
framework.

Appraisal and affective-agent models:

- Scherer, K. R. — Component Process Model / stimulus evaluation checks.
  Start from "Appraisal considered as a process of multilevel sequential
  checking" in *Appraisal Processes in Emotion* and follow later CPM work:
  https://academic.oup.com/book/53557/chapter-abstract/422115725
- Marsella, S. C., and Gratch, J. — EMA process model of appraisal dynamics:
  https://people.ict.usc.edu/~gratch/CSCI534/Readings/COGSYS-RS-EMOTION-2008-6.pdf
- Dias, J., Mascarenhas, S., and Paiva, A. — FAtiMA Modular, a generic
  appraisal framework for emotional agents:
  https://doi.org/10.1007/978-3-319-12973-0_3
- Popescu, A., Broekens, J., and van Someren, M. — GAMYGDALA, an appraisal
  engine for games:
  https://doi.org/10.1109/T-AFFC.2013.24
- Simonov, P. V. — need-informational theory of emotions:
  https://pubmed.ncbi.nlm.nih.gov/6542912/
- Keramati, M., and Gutkin, B. — homeostatic reinforcement learning:
  https://elifesciences.org/articles/04811

Crypto references live in [`policies-roadmap.md`](policies-roadmap.md) because
encryption-at-rest is a general policy, not an affective-memory-specific
mechanism.

## Non-goals

Do not add these to `agent-memory-cpp` for the affective lane:

- neural emotion classifier in core;
- reinforcement learning runtime;
- end-to-end affect controller;
- another vector database;
- dozens of durable `EmotionKind` enum values;
- special embeddings only for `happy/sad/angry`.

Those belong to adapters, experiments, or sibling runtime projects.
