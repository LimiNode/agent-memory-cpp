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

Activation is a retrieval/planning signal, not a truth, proof, authority, or
access-control decision. A future richer trace may expose an `ActivationSeed`,
ordered `ActivationStep` records, reason/source paths, scores, and working-set
admission. It should distinguish a minimal answer-support path from the wider
diagnostic expansion path. Every step remains subject to independent scope and
authorization filters, and the trace is query-time evidence rather than
canonical corpus state.

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

Provider/model extraction is likewise outside canonical admission: candidates
must pass deterministic grounding, validation, policy checks, and atomic
publication before they can affect activation metadata or graph projections.

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

Activation may return a procedure header first and, when the context budget
allows, one to three representative `ProcedureEvidenceTrace` references:
typically a successful exemplar, a corrected failure, and an edge case. These
are retrieval evidence, not execution requests. Selection is revision-aware
and preserves the procedure version, environment fingerprint and source-trace
provenance.

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
| `DeclarativeRecall` | Required facts and preconditions are retrieved independently of execution success |
| `ProceduralExecutionSuccess` | A host can apply an activated procedure and satisfy its verification contract |
| `ProcedureTransfer` | A validated procedure transfers to a new task instance with the same contract |
| `CorrectionReuse` | A prior failure-to-correction trace prevents recurrence of the same failure class |
| `ProcedureGeneralization` | A procedure works beyond memorized concrete cases |

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

### Graph retrieval backlog: G0 and G1 (M2 research candidates)

These are two bounded research questions, not implemented routes, selected
policies or new public API/DBI requirements. They depend on canonical
Relation/endpoint validation, bounded graph retrieval and the existing
retrieval-plan/frontier contracts. They do not block the MDBX C1 text slice.

The motivating [AAF article](https://habr.com/ru/articles/1010522/) describes
graph neighbours feeding a secondary vector search and suppression of
supernodes. Source inspection at AAF commit
`4e67ef62b3552067439bb520a4773551cf8a2156` makes the distinction precise:
[`_build_event_rag_context`](https://github.com/th0r3nt/AAF-Autonomous-Agent-Framework-/blob/4e67ef62b3552067439bb520a4773551cf8a2156/src/layer03_brain/llm/context/builder.py)
passes associated node names as new queries, and
[`raw_find_entries_in_vector_db`](https://github.com/th0r3nt/AAF-Autonomous-Agent-Framework-/blob/4e67ef62b3552067439bb520a4773551cf8a2156/src/layer01_datastate/vector_db/vector_db_management.py)
searches by query text without an allowed-document-ID constraint. This is
query expansion. Restricting the second search to graph-linked canonical
candidates is a separate project hypothesis, not an observed AAF guarantee.
The reference supplies motivation, not a quality result or project authority.

#### G0: Graph-conditioned second-stage retrieval

Compare these explicitly different arms over identical corpus revisions,
qrels, models, frontiers, final K and registered total query budgets:

| Arm | Search scope |
|---|---|
| No-graph control | Independent lexical/dense corpus routes and fusion. |
| Additive baseline | Lexical + dense + bounded graph candidates, then fusion. |
| Graph-derived query expansion | Initial seeds and anchors, bounded expansion, then node-derived query variants over the eligible corpus. |
| Graph-conditioned candidate search | Initial seeds and anchors, bounded expansion, an explicit canonical candidate set, then dense/lexical scoring of that set with the original query. |

Keep an independent original-query route for recovery; a conditioned-only
ablation is labelled separately. Graph conditioning is not a global hard
domain filter. Fallbacks must be declared in the plan and consume its budget;
empty, unavailable and incomplete graph results are distinct. The same
`FilterFrontier` and `ReadFrontier` apply to seeds, relations, endpoints,
secondary searches and final canonical hydration. Semantic traversal does not
implicitly follow `TechnicalLineage`, `Evidence` or `Supersession` edges.

The graph-to-content mapping must identify canonical unit occurrences and
revisions, with graph/projection generations and mapping coverage. An entity
name alone is not a document ID. The existing `PriorRouteCandidates` contract
is defined for `DenseProjectionRoute` in
[`memory-stacks-roadmap.md`](memory-stacks-roadmap.md); it does not already
provide an arbitrary graph parent or a lexical input API. The minimal screen
may materialize an execution-local graph candidate set and score it exactly.
Production lowering requires an explicit owner-contract update for that
handoff, rather than pretending a mixed graph/dense DAG is implemented.

Trace query/seed/anchor/edge/candidate lineage, bounded inputs and one-based
route ranks through canonical deduplication and fusion. Related routes are
retrieval votes, not independent epistemic corroboration. Report Recall@10 and
qrels-based nDCG@10 overall and for cross-domain/relation/causal query slices;
also report seed and graph-candidate recall, candidate counts, scanned edges,
decoded bytes, latency and labelled irrelevant expansion / total expansion.
Conditioned-stage recall cannot exceed its candidate-set coverage. Use the
existing counterfactual control for budget-loss attribution, and include every
stage's work in the comparison; extra searches are not a free quality gain.

Fixtures must include ambiguous/wrong seeds, missing mappings, disconnected
relevant evidence, stale edges and forbidden endpoints. Freeze the executable
plan, cost envelope and decision criteria before measurements. Accept only a
measured quality/cost trade-off with no frontier or provenance violation;
otherwise retain the baseline or record insufficient evidence. No winner or
new storage substrate is selected here.

#### G1: Hub-aware expansion

Compare ordinary bounded BFS with per-node fanout caps, top-weighted edges,
degree-normalized scores and relation-aware quotas. Keep depth, global budgets
and tie-breaking fixed while isolating each policy. Degree-based penalties are
research arms, not defaults; do not hardcode agent/user/entity names as hubs.
Suppressing expansion through a hub does not remove an otherwise valid direct
hit or erase its relations.

Degree and relation statistics must be qualified by the query's authorized,
temporal and graph-generation scope. Unknown degree remains unknown; hidden
neighbours must not influence a disclosed degree/score or leak through traces.
Limit scanned/decoded adjacency work as well as returned fanout: selecting a
top-K list after an unbounded adjacency scan is not bounded traversal. Record
deterministic ordering, policy revision and any approximate or truncated stats.

Distinguish `no_neighbors`, `filtered_by_policy`, `hub_budget_suppressed` and
`global_budget_exhausted` as diagnostic reasons, not new completion enums.
Only a completed inspection of the declared eligible neighbourhood establishes
`no_neighbors`. Policy-bounded completion describes that bounded plan, not an
exhaustive graph. Skipping work required by the plan follows existing
`RetrievalRouteCompletion`, `IncompleteRouteAction` and
`BudgetExhaustionAction`; suppression must not silently become an empty,
complete route. Authorization denials remain aggregate-only.

Use skewed-degree graphs with both irrelevant hubs and a relevant hub that is
the only bridge to evidence. Compare relevance/cross-domain recall, branch
starvation, visited/decoded edges, bytes and latency at matched global work.
Acceptance requires reproducible suppression/completion traces and a declared
quality/cost criterion; a fanout cap alone is not proof of improved retrieval.
G1 may be screened independently, then combined with G0 as a labelled ablation.

#### AR0: Bounded Adaptive Associative Recall

AR0 is a research lane for multi-step associative retrieval. It extends G0/G1
and the existing `RetrievalPlan`, `CandidateSet`, `RetrievalTrace` and
progressive-disclosure contracts; it is not a new graph API, a hidden write
path or a second memory hierarchy. The name deliberately avoids `A0`, which is
already used by the ADELIA/runtime integration lane.

An execution-local adaptive search may carry the equivalent of:

```text
original normalized plan, policy/frontier bindings and plan digest
current cue/context payload or retained replay reference, with digest
parent step and candidate lineage
per-step emitted cue/query/transform payload or retained replay reference
visited units/edges and suppression reasons
provider/model/policy revisions and deterministic parameters when a transformer is used
remaining edge, candidate, byte, token, latency and step budgets
termination/completion reason
```

This state is an execution-local audit trace, not durable memory. A trace
may be called replayable only when the normalized plan and frontiers, all
adaptive cues or their retained replay references, transformation inputs and
outputs, provider/model/policy revisions, deterministic parameters, candidate
ordering/results and required canonical generations are available for
rematerialization. A digest verifies such material; it does not replace it.
When any required input is absent, the trace must say that it is audit-only or
replay-unavailable, and an evaluation must not claim full replayability.
Every explored candidate remains subject to the same `FilterFrontier`,
`ReadFrontier`, lifecycle, provenance and access checks as a one-step query.
Exploration must not modify canonical relations, refresh `use_count`, promote
source trust or create an epistemic corroboration merely because several routes
reached the same unit. Graph-derived routes are retrieval votes, not
independent evidence.

The initial comparison matrix separates:

1. one-shot lexical/dense/hybrid retrieval;
2. graph-derived query expansion over the eligible corpus;
3. bounded graph expansion followed by scoring an explicit canonical candidate
   set;
4. deterministic spreading activation with a declared decay, restart and
   stopping policy;
5. iterative cue/context updates with bounded explore/prune steps;
6. an optional host/LLM query transformer, evaluated separately from the
   deterministic arms and never required for the baseline.

The original query route remains available as a recovery/control route.
Adaptive steps must detect cycles, repeated cues, stale edges, hub expansion,
query drift and frontier changes. A step may stop with any existing
`RetrievalRouteCompletion` value: `Complete`, `Partial`, `Unavailable`,
`BudgetExhausted`, `Dropped` or `RequiredRouteFailed`. Unknown or
unresolved inputs are diagnostic conditions, not a new completion value; the
trace records the reason separately. An empty result is not silently called
complete. No adaptive arm may hide secondary searches or adjacency work from
the declared cost envelope.

AR0 reports multi-hop Recall@K and nDCG@K, path/edge precision, candidate-set
recall, query drift, unique units and edges visited, decoded bytes, steps,
latency, diversity, termination reason and provenance coverage. Fixtures must
include a useful two-hop path, a wrong seed, a cycle, a high-degree hub, a
stale/inaccessible endpoint, a disconnected relevant item and an exhausted
shared budget. Acceptance is a measured relevance/cost/provenance trade-off;
there is no default adaptive policy until a matched comparison supports one.

## 10. Milestone Placement

- M0: no activation layer beyond scope/lifecycle/source filters.
- M1b: deterministic activation metadata, DomainMap/Playbook as canonical
  knowledge objects, and activation eval fixtures.
- M2: richer `KnowledgePlanner`, graph expansion policies, compiled domain map
  refresh jobs, and role-aware context budgets.
- M2+: learned planners, LLM query planners, contradiction-aware synthesis, and
  cross-application taxonomy adapters.

## Rare-facet and associative-expansion evaluation

AR0 is an activation/retrieval planning lane, not an evidence or authority
source. Its quality must be measured with the
[Rare-Facet / Multi-Granularity Retrieval Gate](evaluation-roadmap.md#rare-facet--multi-granularity-retrieval-gate).

The gate compares plain retrieval, associative expansion and
ASMS/MSBSE-to-AR0 composition on identical query, qrels, frontier, candidate,
decoded-byte and latency budgets. The original query route remains independent
of activation. Graph-derived names, relation expansion and activation hints
must be traceable as route inputs; they do not become canonical facts or
independent corroboration.

Every returned result is hydrated from the expected canonical source revision.
Wrong seeds, disconnected evidence, stale edges, forbidden endpoints, budget
exhaustion and hub suppression are recorded as diagnostic outcomes. A
candidate-set recall ceiling is reported separately from the quality of the
second-stage retrieval. Adding associative expansion must not change
provenance, lifecycle, source authority or action admission.
