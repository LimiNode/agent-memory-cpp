# knowledge-activation-roadmap.md

Normative roadmap for knowledge representation and activation above raw
retrieval.

Hybrid retrieval answers "which fragments are similar to the query?" Knowledge
activation answers "which domains, concepts, procedures, constraints, and
evidence must be considered for this turn?" Both layers are required for agent
systems that use `agent-memory-cpp` as their knowledge substrate.

## 1. Boundary

`agent-memory-cpp` owns storage, retrieval, planning contracts, context
assembly inputs, and deterministic activation primitives. It does not own
agent orchestration or LLM reasoning.

Activation is a planning/retrieval-hint layer. It may choose domains, query
variants, budgets, playbook headers and fallback strategy; it must not execute
tools, grant permissions, run playbooks, plan the agent task, or own LLM/tool
orchestration. Domain routing is never an access-control boundary: scope,
access, status, language, jurisdiction and trust filters apply independently
before and after activation.

```cpp
struct ActivationPlan {
    std::vector<WeightedDomain> domains;
    std::vector<ConceptRef> concepts;
    std::vector<PlaybookRef> playbooks;
    std::vector<CapabilityRef> suggested_capabilities;
    std::vector<QueryVariant> query_variants;
    ContextBudgetHint budget_hint;
    bool corpus_wide_fallback = true;
    ActivationTrace trace;
};
```

`CapabilityRegistry` describes available capability families and missing
capabilities; it does not own executors or permission policy.

The activation layer must keep one logical knowledge corpus with multiple
domain views. Physical storage may split data for technical reasons, but it
must not create separate databases for every topic or topic intersection.

```text
Raw sources
  -> evidence cards
  -> canonical concepts / frameworks / cases
  -> playbooks
  -> domain maps and capability maps
  -> budgeted agent context
```

## 2. Independent Axes

Do not overload one `kind` enum with topic, object type, agent role, and
application stage. The canonical axes are independent:

| Axis | Meaning | Examples |
|---|---|---|
| `semantic_class` | Domain taxonomy used for activation, distinct from persisted `KnowledgeUnitKind` | `source`, `evidence`, `concept`, `framework`, `checklist`, `case`, `tool`, `policy`, `risk`, `metric` |
| `domains` | What areas it belongs to | `ai`, `software_engineering`, `traffic_acquisition`, `business`, `creator_economy` |
| `facets` | When or where it applies | lifecycle, activity, audience, platform, artifact |
| `intents` | Which user/task intents activate it | `launch_virtual_influencer`, `review_code`, `diagnose_campaign` |
| `agent_roles` | Which agent role benefits from it | `product`, `market`, `growth`, `tech`, `mentor` |
| `relations` | How it connects to other nodes | `uses`, `requires`, `applies_to`, `measured_by`, `supports`, `contradicts`, `supersedes`, `derived_from`, `governed_by` |

These axes are domain-level metadata in `agent-memory-cpp`; concrete
application taxonomies own the actual string/id vocabularies.

`KnowledgeUnitKind` remains the storage/payload discriminator (`Chunk`,
`Playbook`, `DomainMap`, `CapabilityMap`, and so on). `semantic_class` must not
repeat or override it: a `KnowledgeUnitKind::Playbook` may be semantically a
`framework` or `checklist`, but validation records both axes explicitly. This
keeps activation taxonomy extensible without creating a second competing kind
enum.

## 3. Canonical Objects

### Raw Source

Immutable or revisioned source material: articles, markdown files, transcripts,
PDF extracts, logs, code, videos after text extraction. Raw sources are stored
through `ResourceBodyStore` and cited through `SourceRef`.

### Evidence Card

A concrete claim, observation, method, example, or quote derived from a source.
Evidence cards must keep provenance. They are not automatically canonical
truth.

### Concept

A canonical, versioned explanation of a stable idea. A concept can belong to
many domains and can cite many evidence cards.

### Playbook

A procedure with a goal, inputs, prerequisites, steps, decision points, failure
modes, outputs, and verification criteria. A playbook is activated by intent
and trigger metadata before its full body is loaded into context.

### DomainMap

A materialized view of a domain: active concepts, common intents, relevant
playbooks, adjacent domains, constraints, and high-priority warnings. A
DomainMap is rebuildable from canonical nodes and graph relations, but
activation of a new version is explicit.

### CapabilityRegistry

A compact registry of what the agent/application can do: tools, playbook
families, retrievers, adapters, and unavailable capabilities. This is bootstrap
context, not a large RAG corpus.

Storage ownership:

| Object | Ownership | Storage mapping |
|---|---|---|
| Knowledge card | Declarative curated knowledge object | `KnowledgeUnitKind` appropriate to fact/concept/note plus source refs |
| `Playbook` | Authoritative canonical knowledge object with provenance | `KnowledgeUnitKind::Playbook`, `knowledge_units`, projections, source refs, activation metadata |
| `ProcedureCandidate` | Learned/imported proposal from traces | `KnowledgeUnitKind::Procedure` with `ProcedureStateComponent.current_status = Candidate` |
| `Procedure` | Validated versioned procedure | `KnowledgeUnitKind::Procedure`, activation metadata, `ProcedureStatsComponent` |
| `DomainMap` | Versioned materialized configuration/derived state | `KnowledgeUnitKind::DomainMap`, compiled body/projection, graph edges |
| `CapabilityRegistry` / `CapabilityMap` | Runtime/application registry projected into bootstrap context | `KnowledgeUnitKind::CapabilityMap` only when persisted as knowledge; executors stay outside memory core |
| Runtime capability implementation | Live executable capability | external runtime only; memory stores ids, schemas and requirements |
| domain/facet/intent assignments | Derived or reviewed metadata | `metadata_filters` and/or `ActivationMetadataComponent` |
| `ActivationTrace` | Query-time diagnostic result | returned with retrieval/context trace; not canonical corpus state |

If any row receives a dedicated physical DBI later, `dbi-manifest.yaml` and the
DBI budget in `mdbx-containers-extension-tz.md` must be updated first.

## 4. Activation Metadata

Minimal metadata for a canonical node:

```yaml
schema: agent-memory/knowledge-node-v1
id: concept.ai_influencer
semantic_class: concept
title: "AI influencer"
summary: "..."

aliases:
  - virtual influencer
  - synthetic influencer

domains:
  - ai
  - content_marketing
  - traffic_acquisition

facets:
  lifecycle:
    - validation
    - launch
  audience:
    - creator
    - solo_founder

intents:
  - understand_ai_influencer
  - launch_virtual_influencer

agent_roles:
  - market
  - growth
  - tech

relations:
  - type: uses
    target: concept.generative_content_pipeline
  - type: measured_by
    target: metric.audience_engagement

status: active
trust_level: B
version: 3
updated_at_ms: 1785100000000
review_after_ms: 1792876000000
```

Activation assignments should be reproducible:

```yaml
taxonomy_version: knowledge-taxonomy-v1
assignment_generation: 42
source_unit_revision: 7
policy_fingerprint: deterministic-router-v1
origin: manual | automatic | imported
confidence: 0.83
updated_at_ms: 1785100000000
```

`ActivationTrace` records the activated domains, DomainMap versions, trigger
rules, fallback decisions, cross-domain additions and whether activation
changed final rank or only context budgeting.

The filesystem path must not define semantic identity. For file-backed
catalogs, group by object kind (`concepts/`, `playbooks/`, `domain_maps/`) and
store domains/facets as metadata.

## 5. Retrieval And Activation Pipeline

```text
User query
  -> lightweight intent/entity/task classification
  -> KnowledgePlanner
       domains to activate
       concepts to inspect
       playbooks to consider
       evidence freshness requirements
  -> DomainMap lookup
  -> hybrid search: lexical + dense + exact aliases/ids
  -> bounded graph expansion
  -> rerank/fusion
  -> ContextBuilder
  -> downstream agent/LLM
```

M1b uses deterministic activation first: aliases, trigger phrases, intent
dictionary, domain keywords, current role, and explicit graph edges. Learned or
LLM-based planners are M2+ adapters.

## 6. Strict Filters Versus Soft Routing

Strict filters may exclude candidates:

- scope / tenant;
- access level;
- lifecycle status;
- language when the profile requires it;
- jurisdiction or compliance boundary;
- trust threshold;
- explicit outdated/deprecated exclusion.

Soft routing signals should boost or prioritize, not exclude by default:

- domain;
- agent role;
- stage;
- topic;
- platform;
- audience;
- neighboring concept.

This prevents cross-domain failures where a query classified as `ai` also needs
ordinary software engineering, traffic acquisition, security, or testing
knowledge.

Security invariant: activation never grants access. A record that fails scope
or access checks remains unavailable even if a domain/playbook strongly
activates it. A record in an unactivated domain may still appear through
corpus-wide fallback if strict filters allow it.

## 7. Storage Mapping

The activation layer should reuse the canonical memory substrate:

| Need | Default storage |
|---|---|
| canonical node envelope | `knowledge_units` |
| activation text | `unit_projections` |
| domains/facets/intents/roles | `metadata_filters` or typed activation component |
| aliases/exact ids | lexical dictionary plus metadata filters |
| relations | `graph_edges_by_src` / `graph_edges_by_dst` |
| source evidence | `source_refs`, evidence card units |
| compiled DomainMap body | `CompiledArticlePayload` or future `DomainMapPayload` |
| playbook body | `ChunkPayload`/`CompiledArticlePayload` initially; future `PlaybookPayload` when two consumers require it |

New physical DBIs for `playbook_payloads`, `domain_map_payloads`, or
`activation_rules` are not part of the baseline manifest until
`mdbx-containers-extension-tz.md` gets explicit profile-delta rows.

Playbook retrieval does not authorize execution. A playbook returned by memory
is a knowledge artifact with revision, provenance, trust, applicable domains,
required capabilities and optional safety/approval metadata. The transition
from retrieved playbook to tool execution belongs to the downstream runtime.

`ProcedureActivationCandidate` is a retrieval/planning artifact, not an
execution request. This roadmap owns its canonical value-type contract:

```cpp
struct ProcedureActivationCandidate {
    KnowledgeUnitRef procedure;

    double precondition_match = 0.0;
    double capability_match = 0.0;
    double historical_success = 0.0;
    double context_relevance = 0.0;

    std::vector<KnowledgeUnitRef> supporting_units;
    std::vector<CapabilityRef> missing_capabilities;

    bool requires_validation = false;
};
```

An optional runtime adapter may enrich a missing `CapabilityRef` with the
runtime object that could provide it, but that adapter-only detail is not part
of this canonical M1b candidate and never turns it into execution.

`CapabilityRegistry` stores capability id, version, declarative input/output
schema, safety metadata and procedure requirements. Runtime adapters own
callable implementation, live availability, authority, resource budget and the
actual node providing the capability.

Procedure lifecycle:

```text
trace episodes
  -> ProcedureCandidate
  -> sandbox/runtime validation
  -> active Procedure
  -> runtime executions
  -> outcome statistics
  -> degraded/retired/superseded Procedure
```

Memory records proposal, validation evidence and outcome statistics. Runtime
policy or an operator decides promotion to active procedure.

## 8. Lifecycle

Canonical knowledge objects use a curation workflow that is independent from
the durable record lifecycle:

```text
raw -> candidate -> reviewed -> canonical -> deprecated
```

```cpp
enum class CurationState : uint8_t {
    Raw,
    Candidate,
    Reviewed,
    Canonical
};
```

`CurationState` is owned by `ActivationMetadataComponent`. It answers whether
the content is ready for activation/curation. `KnowledgeUnitEnvelope` lifecycle
remains `Active`, `Superseded`, `Deprecated`, or `Erased`; a unit may be
`Canonical + Active`, `Reviewed + Deprecated`, or any other valid combination.
The arrow above is therefore a curation promotion path, not a replacement for
the lifecycle FSM and not an implicit erase/deprecation operation.

Invariants:

- every canonical node has a source or is explicitly marked as a hypothesis;
- generated summaries cite evidence nodes;
- source updates create candidate changes, not silent canonical rewrites;
- contradictory sources do not merge into false consensus;
- time-sensitive knowledge has `review_after_ms`;
- derived search/vector/DomainMap indexes are rebuildable from canonical
  storage.

## 9. Eval Classes

Activation quality is evaluated separately from chunk recall:

| Eval class | Checks |
|---|---|
| `CrossDomainCoverage` | Query activates all required neighboring domains |
| `ProcedureActivation` | Correct playbook header is selected before evidence chunks |
| `ProcedureActivationPrecision` | Activated procedure matches preconditions and capabilities |
| `ProcedureActivationRecall` | Required procedure is not missed when evidence exists |
| `ProcedureDegradation` | Repeated failures degrade a procedure without deleting history |
| `DomainMapActivation` | Domain map appears in the context plan when needed |
| `MissingConceptDetection` | Planner identifies absent concepts or sources |
| `EvidenceGrounding` | Canonical concepts/playbooks cite supporting evidence |
| `SoftRoutingRecall` | Useful cross-domain results are not removed by domain filters |
| `FallbackSafety` | Corpus-wide fallback recovers missed domains without bypassing strict filters |

Example: "How do I launch an AI influencer and get the first audience?" must
cover AI content generation, positioning, traffic acquisition, monetization,
and platform constraints.

Activation eval reports compare against corpus-wide retrieval without routing:
domain-routing accuracy, cross-domain recall, under-routing, over-routing,
fallback rate, activation latency, playbook selection accuracy and retrieval
quality delta. Required fixtures include multi-domain queries, wrong
high-confidence domains, missing metadata, stale DomainMap, conflicting
playbooks, conflicting procedures, no-domain queries and cases where routing
hurts the baseline.

## 10. Milestone Placement

- M0: no activation layer beyond scope/lifecycle/source filters.
- M1b: deterministic activation metadata, DomainMap/Playbook as canonical
  knowledge objects, and activation eval fixtures.
- M2: richer `KnowledgePlanner`, graph expansion policies, compiled domain map
  refresh jobs, and role-aware context budgets.
- M2+: learned planners, LLM query planners, contradiction-aware synthesis, and
  cross-application taxonomy adapters.
