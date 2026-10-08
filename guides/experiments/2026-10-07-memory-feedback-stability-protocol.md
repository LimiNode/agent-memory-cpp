# Memory feedback stability gate (F0)

Status: `PROPOSED`; this note defines a research protocol only. It does not
change the production scoring formula, usage-state semantics, public C++ API,
MDBX schema, or default memory-stack policy.

Date: 2026-10-07

Base: `origin/main` at `c28f826f5205066f74eff98c265ddff1a3ef9af6`

Evidence status: protocol only; no F0 experiment has been run and no evaluation
rung in [`evaluation-roadmap.md`](../evaluation-roadmap.md) is claimed. F0
does not block the MDBX C1 canonical-content storage slice or depend on the
optional expectation/runtime work.

## Research question

Under what conditions does stateful usage feedback preserve retrieval
**diversity**, **correctness**, and **reproducibility**? The gate must measure
the current policy before deciding whether `use_boost` should be retained,
bounded, decayed, replaced, or removed. No variant is preferred in advance.

The gate is about feedback applied after or around retrieval. It does not turn
retrieval usage into source authority, lifecycle authority, or action authority.

## Current baseline

The normative baseline is described in
[`policies-roadmap.md`](../policies-roadmap.md) (decay and post-filter sections),
[`knowledge-base-roadmap.md`](../knowledge-base-roadmap.md) (the
`UsageStatsComponent` and decay-aware retrieval sections), and
[`memory-stacks-roadmap.md`](../memory-stacks-roadmap.md) (default stack
profiles).

The current intended score is:

```text
score = base_score * decay_factor + use_boost * log1p(use_count)
```

`use_count` is intentionally non-decaying in the current baseline. Cooldown,
self-echo suppression, and their factors are post-filters/score modifiers,
not lifecycle transitions. The current post-retrieval hook records a cooldown,
increments `use_count`, and records `last_used_at_ms`; it does not increment
the content revision or change the lifecycle FSM. `injection_count` and
`last_injected_at_ms` are separate runtime fields and must not be silently
interpreted as `use_count`.

The exact event that should increment `use_count` is an open research question
for this gate. Existing wording such as “successful retrieval” is a baseline
reference, not permission to conflate retrieval, selection, prompt injection,
and useful outcome.

There is a second feedback path: a use event also advances `last_used_at_ms`,
which can refresh the decaying base score even when `use_boost = 0`. The screen
must isolate counter boost, recency refresh and cooldown instead of attributing
all divergence to `log1p(use_count)`. The owners contain both a cooldown score
modifier and a filter/skip description. Every arm must declare which behavior
it executes; F0 does not silently settle that ambiguity or change defaults.

## Event contract for the experiment

Every run must distinguish these events in its trace. A later event may not be
inferred merely because an earlier event occurred.

| Event | Meaning | Evidence it supplies |
| --- | --- | --- |
| `candidate_generated` | A route produced a candidate under the resolved `FilterFrontier`, before fusion/final return. Internal conservative-superset probes are not disclosed candidates. | Route trace at the bound read frontier. |
| `returned` | The candidate was present in the ordered retrieval result. | Retrieval-result trace. |
| `selected` | The host or context planner selected the result for further assembly. | Selection decision and plan revision. |
| `context_pack_included` | The result was included in a `ContextPack`. | Context-pack identity and omission trace. |
| `provider_egress_allowed` | The host policy allowed a specific outbound request. | Policy/authorization decision; it does not prove dispatch. |
| `provider_request_sent` | The host sent the item or its declared representation. | Host request manifest and dispatch receipt; it does not prove provider use. |
| `effective_context_reported` | The host or provider reported what remained after transformations. | Separate host-owned effective-context artifact with reporting origin and coverage. |
| `host_confirmed_useful` | The host recorded a task outcome with explicit usefulness attribution. | Outcome provenance, query/context/frontier and attribution rule; this is a host assessment, not automatic proof of causal benefit. |

`use_count`, `injection_count`, and any future outcome-backed aggregate must be
reported against one of these event definitions. The gate must not introduce a
`validated_use_count` field or public type. Outcome-backed observations may be
compared as a research variant without committing their storage shape.

Before baseline execution, freeze the exact event-to-counter mapping and label
it as the operational interpretation of the current roadmap. If multiple
interpretations are plausible, keep them as named sensitivity arms. Do not
rename all of them "successful retrieval". `ContextFingerprint` binds the
provider-neutral context only; it cannot stand in for an effective-context
report or prove injection. Unreported host stages remain unknown.

Each query reads a pinned usage snapshot. Its post-hook contributes to a later
snapshot, never to its own ranking input. Record event order, update scope and
retry/deduplication treatment so retries do not accidentally count as useful
exposure. This is an experiment contract, not a new production counter API.

## Hard invariants

Usage feedback is a ranking/runtime signal. It must never bypass a stronger
contract:

- lifecycle exclusion, validity exclusion, access policy, and fail-closed
  authorization run before usage can affect ranking;
- lifecycle-excluded, invalid, erased or inaccessible records do not reappear
  because their usage counters are large; `Superseded` history remains
  available only under the existing explicit temporal/audit rules, never as a
  current hit merely because of usage;
- a stale projection or generation mismatch is not repaired by a usage boost;
- contradicted evidence remains retrievable where allowed by the existing
  plan/policy, including conflict or historical retrieval; usage must not
  promote it to current, verified, or source-trusted truth;
- retrieval frequency does not establish source trust, epistemic authority,
  corroboration, or current action permission;
- a model-generated summary copied or repeatedly injected into context does not
  gain evidence status merely from that repetition;
- cooldown and usage updates do not mutate canonical content revisions or
  silently create an evidence/provenance transition;
- no hidden LLM call, provider feedback, or host outcome may be synthesized
  inside the experiment to make usage appear useful.

These invariants are evaluated before aggregate quality metrics. A violation is
an invalid baseline/variant result, not a trade-off to be hidden in an average
score.

## Reproducibility contract

A replay input is complete only when it binds all state that can affect order or
eligibility:

- canonical corpus/resource revisions and the applicable read frontier;
- the resolved `RetrievalPlan`, policy revision, hard-filter and lifecycle
  rules;
- active projection/index generations and embedding/model revisions;
- logical clock/time values used by decay, validity, and cooldown;
- the complete usage-state snapshot or an equivalent usage-state frontier and
  fingerprint, including cooldown fields and injection state when used;
- deterministic candidate ordering and tie-break rules;
- the exploration seed/schedule when a cold-start or diversity arm samples a
  choice;
- implementation/build identity and experiment configuration digest.

A digest identifies the usage state; it does not reconstruct it. Retain a
replayable snapshot or an initial snapshot plus ordered usage events. Each arm
starts from the same initial state and evolves its own state without cross-arm
counter contamination. Publish intermediate frontiers as well as the initial
one so a divergent trajectory can be replayed.

With the same bound inputs, a deterministic run must produce the same ordered
candidate/result IDs and the same ranking digest. Floating-point scores may be
compared under a declared numerical tolerance, but an ordering difference is a
replay difference. A concurrent or asynchronous usage update whose order is not
captured in the usage frontier makes the run non-reproducible and must be
reported as such.

A usage snapshot is part of the retrieval input, not an incidental mutable
counter. A query, corpus, and model revision alone do not define a reproducible
ranking when stateful feedback is enabled.

## Required fixtures

Each fixture must use stable identities and explicit source/provenance labels.
The fixture names are normative; the data size and exact values are selected in
the executable research plan.

| Fixture | Required observation |
| --- | --- |
| Equal relevance / long horizon | Two or more equally relevant units receive paired, controlled early choices. Swap identities/tie positions and measure persistent divergence, coverage and recovery over a declared finite horizon. Finite runs cannot establish permanent lock-in. |
| Lifecycle exclusion | Give a lifecycle-excluded, invalid, stale, or inaccessible unit an artificially huge `use_count`; it must never be returned through a path that excludes it. |
| Contradicted evidence | Give conflicting units different usage histories. Conflict retrieval may return the contradicted unit when requested, but usage must not relabel it current or verified. |
| Repeated summary injection | Repeatedly inject a model-generated summary derived from one source. Its evidence status and source-trust/independence status must remain unchanged unless an explicit provenance-bearing observation is added. |
| Usage snapshot replay | Replay the same query, plan, generations, clock, and usage snapshot. Ordered IDs and ranking digest must match exactly. Change only the usage snapshot and show the resulting state change explicitly. |
| Cold start / diversity | Start with zero or near-zero usage and compare deterministic tie-breaking with an explicitly declared exploration/diversity arm. Exploration must be observable and replayable, not an accidental random tie. |
| Recency and cooldown control | With identical event schedules, isolate nonzero counter boost, zero boost with recency refresh, and cooldown semantics. Separate feedback-amplified concentration from concentration already caused by deterministic top-K ties. |

The fixtures should include both one-resource repetition and multiple
independent/provenance-related resources. Two records with different IDs are
not automatically independent evidence.

## Metrics and reporting

Report metrics per fixture and per policy variant. Do not choose arbitrary
pass/fail thresholds before the protocol is extended with a declared dataset,
query set, and decision rule. Freeze the horizon, parameter/seed grid, exposure
denominators and recovery criterion before comparing results. A bounded pilot
may choose these values for a later held-out run but cannot retrospectively
declare its own confirmatory pass.

- **Retrieval quality:** Recall@K, precision@K, MRR, nDCG@K, or task-specific
  judgments against a declared qrels/oracle. Missing source judgments are
  `PENDING_SOURCE_REPLAY`, not an inferred pass.
- **Rank stability:** exact ordered-ID replay identity, top-K overlap, rank
  correlation/divergence under controlled usage perturbations, and time-series
  rank churn.
- **Coverage and diversity:** unique units, source/lineage-root coverage,
  selection concentration (for example entropy or HHI), top-item share, and
  cold-start recovery. Report the aggregation window and denominator.
- **Lifecycle and epistemic safety:** excluded-item resurrection rate, stale
  projection return rate, conflict-evidence visibility, and any change in
  current/verified/source-trust labels after usage feedback.
- **Reproducibility:** usage-state/frontier digest match, ordered-result digest,
  score replay status, and explicit nondeterminism causes.
- **Cost:** candidate/read/write counts, usage-update cost, storage/IO impact,
  and p50/p95/p99 latency when a serving comparison is meaningful.

A single average score must not hide lifecycle violations, missing branches,
or a diversity collapse. Quality, safety, reproducibility, and cost remain
separate dimensions.

## Variant comparison

Run the current baseline first. Only then compare alternatives under the same
corpus, qrels, plan, generations, clock, tie-break, and initial state:

1. current raw `use_count` with `log1p` boost;
2. decayed usage state;
3. bounded or saturating usage boost;
4. injection-based feedback;
5. outcome-backed observations with explicit provenance/frontier;
6. zero counter boost with declared recency/cooldown behavior, plus a frozen
   usage-state control to isolate all stateful feedback.

These are experiment arms, not production recommendations. Keep cooldown,
self-echo, lifecycle filters, and model/projection generations fixed while
isolating a usage arm; otherwise an apparent feedback effect is confounded with
another policy change. Any arm using exploration must declare its seed and
selection rule.

No arm may be promoted because it improves a scalar ranking metric while
violating the hard invariants. If an outcome-backed arm is tested, its outcome
attribution must be inspectable; a host assertion without provenance is not
independent evidence.

## Execution and evidence rules

1. Freeze the fixture manifest, canonical revisions, qrels/oracle, resolved
   plan, policy/generation manifests, clock, tie-break, and initial usage
   snapshot before a run.
2. Record event traces for the event classes the arm exercises. Mark unobserved
   host stages unavailable; do not fabricate them. Per-item traces include only
   authorized candidates. Authorization denials follow aggregate-only
   `PolicyDecisionTrace`; a privileged fixture oracle may assert exclusions
   without disclosing denied IDs, text or metadata through retrieval traces.
3. Execute the baseline on cold-start, controlled-perturbation, long-horizon,
   and replay fixtures before running variants.
4. Preserve compact source/config hashes, usage-state/frontier digests, result
   digests, and run metadata. Do not commit large benchmark dumps or generated
   databases; follow the raw-artifact rules in
   [`experiments/README.md`](README.md).
5. If an input, qrels set, replay, or source artifact is unavailable, mark the
   corresponding result `PENDING_SOURCE_REPLAY` or `INSUFFICIENT_EVIDENCE`.
   Do not substitute synthetic data while claiming a source-bound result.
6. Keep research receipts separate from implementation changes. A passing
   research arm does not alter production defaults, public APIs, DBIs, or
   lifecycle semantics.

The smallest next execution is a deterministic, network-free synthetic screen
of the registered fixtures, followed by a separately pinned source/qrels replay
before any product-quality claim. Synthetic results establish behavior of the
declared model only. A missing real-source replay remains pending and is never
replaced by a synthetic success. The executable plan must register commands,
implementation mapping and numerical decision rules before gate closure.

The experiment must not call an LLM inside a storage transaction or use hidden
provider calls to generate labels, summaries, or outcomes. If an external
provider is part of a declared arm, pin its model/provider revision and record
its response artifact and provenance separately.

## Gate outcomes

At completion, record exactly one primary outcome:

- **`ACCEPT_CURRENT_POLICY`** — the current usage policy preserves the declared
  quality, diversity, safety, and replay properties for the tested scope;
- **`REVISE_POLICY`** — the current policy violates a declared invariant or
  shows a material, reproducible stability/correctness defect; propose a
  separate implementation/docs PR with tests and migration semantics;
- **`INSUFFICIENT_OR_UNKNOWN`** — evidence is missing, non-replayable,
  underpowered, or contradictory; do not infer acceptance or revision.

A gate outcome is a research conclusion, not a production change. Any policy
revision requires its own owner-doc update, implementation contract, focused
conformance tests, and exact-head CI. The F0 result must cite its fixture,
configuration, source revisions, usage frontier, and compact evidence receipt.

## Open questions and follow-up

- Which event, if any, should be the production meaning of `use_count`?
- Should cooldown remain a score modifier, a hard exclusion, or a separate
  diversity policy for each memory stack?
- How should concurrent usage updates be ordered or merged while preserving a
  replayable usage frontier?
- Which outcome attribution rules are strong enough to support future
  outcome-backed observations without creating a universal trust score?
- Which diversity and correctness thresholds are appropriate for each stack and
  query class? They remain undeclared until a concrete evaluation dataset and
  decision rule are registered.

This note intentionally leaves those questions open. It is a bounded research
gate for the existing retrieval policy, not a new memory kind, trust model,
prospective-memory subsystem, or storage schema.
