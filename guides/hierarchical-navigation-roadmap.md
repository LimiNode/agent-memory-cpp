# Hierarchical Navigation and DomainMap Retrieval Roadmap

This guide defines navigation projections that help an agent decide what to
search and where to search. It covers curated maps, derived topic maps and
bounded map-of-maps traversal.

Current status: **Docs/tests only**. No production hierarchical navigator is
claimed.

## 1. Navigation is a routing projection

A navigator is a compact map of corpus structure:

- domains, topics, aliases and task intents;
- child maps and leaf routes;
- likely source scopes and projection kinds;
- playbooks, concepts and evidence families;
- constraints, warnings and known coverage gaps.

A navigator is not a fact, an independent observation, an authority decision or
an access-control boundary. It changes retrieval planning, not epistemic status.

The existing `KnowledgeUnitKind::DomainMap`, `ActivationPlan` and
`ActivationTrace` are the intended substrate. This guide does not
introduce a `NavigationNote` kind or a second taxonomy.

## 2. Map hierarchy

A map may reference another map, but traversal is bounded:

~~~text
root map
  -> domain map
    -> topic or task map
      -> leaf route
        -> canonical sources and ordinary projections
~~~

A root map should stay compact enough to load as bootstrap context. Child maps
are loaded on demand. A leaf route may point to source identities, scope IDs,
retrieval profiles, query variants and temporal/frontier constraints.

Supported origins are explicit:

- `manual` — curated by a human or trusted editor;
- `derived` — generated from canonical units and relations;
- `imported` — supplied by another knowledge base.

Origin does not determine truth. It determines how the map is maintained and
what validation it requires.

Review and admission are a separate axis. The `review_state` field below uses
the existing `CurationState` values (`Raw`, `Candidate`, `Reviewed`,
`Canonical`) from the knowledge-activation contract; it is not a new review
enum. A `review_receipt` refers to the applicable existing curation, admission
or publication receipt and policy revision. It does not replace source
provenance or lifecycle state. The origin remains unchanged when a map moves
from `Candidate` to `Reviewed` or `Canonical`.

## 3. Map entry contract

A materialized map records, at minimum:

~~~yaml
map_id: domain.ai
scope_id: workspace-main
map_generation: 12
source_frontier: ...
origin: manual | derived | imported
review_state: Raw | Candidate | Reviewed | Canonical
review_receipt: ...
map_availability: available | stale | unavailable
title: AI
aliases: [artificial intelligence]
intents: [understand_ai, review_agent_design]
child_maps: [...]
target_routes:
  - scope_id: workspace-main
    retrieval_profile: hybrid-default
    projection_kinds: [lexical, dense, graph]
coverage:
  included_domains: [ai, software_engineering]
  known_gaps: [fresh external news]
constraints:
  max_depth: 3
  max_children: 16
provenance: [...]
updated_at_ms: ...
review_after_ms: ...
~~~

The exact serialized shape remains an implementation choice until a public
storage contract is needed. The semantics above are normative.

Map entries must carry enough provenance to answer:

- which canonical revisions produced the entry;
- which map generation was active;
- what was intentionally omitted;
- whether the route was manually curated or automatically derived;
- when the map should be refreshed.

A map digest verifies the materialized map. It does not replace its source
frontier or replay material.

### Routing eligibility

Every map that contributes routing input, including an initial root or domain
map, must pass the same eligibility gate before its hints are consumed:

- `review_state` is `Reviewed` or `Canonical` under the existing
  `CurationState` contract;
- the applicable admission/publication receipt is valid for the map generation;
- `map_availability` is `available`, and its source frontier is usable for
  the query;
- scope, access, lifecycle, language and temporal checks pass.

A `Raw` or `Candidate` root/domain map may be retained as a non-authoritative
candidate, but it must not supply routing hints. If no eligible map is available,
the independent flat retrieval route remains active.

## 4. Activation and retrieval flow

A host may map eligible navigation output onto the existing `ActivationPlan`
fields: domain and concept selections use `domains` and `concepts`;
playbook references use `playbooks`; aliases and route query text use
`query_variants`; and limits use `budget_hint`. Map generation, path,
frontier, provenance and fallback reasons are reported through the existing
`ActivationTrace` and route diagnostics. No `route_hints` field is introduced.

The reference flow is:

1. retrieve a bounded set of root or domain maps;
2. use a map only after the routing-eligibility gate above has passed;
3. expand only child maps whose `review_state` is `Reviewed` or `Canonical`,
   whose `map_availability` is `available`, and which pass the normal scope,
   access and lifecycle checks; bound expansion by depth, fanout and byte budgets;
3. validate scope, access, lifecycle, language and temporal filters independently;
4. run ordinary lexical/dense/graph retrieval using the resulting hints;
5. retain an independent original-query route;
6. fuse route-local candidates with existing completion and provenance rules;
7. hydrate canonical records before factual context assembly;
8. expose the map path as routing diagnostics, not as evidence.

A stale, unavailable, low-confidence or ambiguous map must not suppress the
independent route. The implementation falls back to broader retrieval or
returns an explicit unknown/partial diagnostic.

Map routing is not an authorization shortcut. A route hint may narrow a search,
but it may never grant access to a scope or expose a forbidden candidate.

## 5. Curated navigators and LLM wiki patterns

A manually curated map of content can encode practical routes that embeddings
often miss:

- "start here" material;
- task-oriented reading paths;
- high-value warnings and exceptions;
- stable aliases and local terminology;
- pointers to child maps and source locations.

The `usage-llm-wiki.md` guide already documents raw corpus, synthesized
articles, an index and an operation log. The playbook examples
[ai-agent-playbook](https://github.com/LimiNode/ai-agent-playbook) and
[digital-growth-playbook](https://github.com/LimiNode/digital-growth-playbook)
are reference examples of curated root maps, task routes and thematic child
maps. They are useful design evidence, not a quality guarantee.

The map must remain a navigation layer. A short map summary can suggest what to
retrieve, but the final factual answer must be hydrated from canonical source
material.

## 5.1 Research basis and limits

The navigation hypothesis is informed by adjacent retrieval research, not
proved by it:

- [RAPTOR](https://proceedings.iclr.cc/paper_files/paper/2024/hash/8a2acd174940dbca361a6398a4f9df91-Abstract-Conference.html)
  is a recursive summary-tree and multi-abstraction retrieval baseline; it
  motivates comparing multiple summary levels, but it does not establish this
  project's `DomainMap` schema, admission state, access rules or lifecycle
  semantics.
- [HippoRAG](https://arxiv.org/abs/2405.14831) is a knowledge-graph and
  Personalized-PageRank associative-retrieval baseline; it motivates
  graph-conditioned routing comparisons, but it does not establish the
  proposed root/domain/topic taxonomy or production quality.

These sources are research inputs for HN-0 fixtures. They do not prove that
hierarchical maps improve retrieval, grant authority, or replace independent
canonical-source hydration.

## 6. Map maintenance and offline consolidation

Navigation maps are derived or curated projections with their own generation,
frontier and review lifecycle.

Offline consolidation may:

- add a new child map for a repeatedly observed topic;
- update aliases and task routes;
- mark a route stale after source revision or deletion;
- rebuild a derived map from canonical relations;
- record unknown coverage instead of inventing a route.

It must not silently rewrite a manual map into an automatic map, and it must not
turn a map entry into independent evidence. See
`offline-consolidation-roadmap.md`.

## 7. Research gate HN-0

The **Hierarchical Navigation Retrieval Gate** compares:

1. flat lexical+dense baseline;
2. flat retrieval plus curated maps;
3. flat retrieval plus derived maps;
4. map-conditioned retrieval with route or scope hints;
5. map plus bounded graph expansion;
6. map-only retrieval as a negative control.

All arms use the same canonical corpus, embedding split, read frontiers,
candidate budget, decoded-byte budget and final ContextPack/token budget. Each
arm receives the same component-wise query-time resource envelope:

- map bytes read and decoded;
- map nodes and edges traversed, fanout and query variants;
- retrieval calls and decoded bytes;
- model calls and tokens when a map policy uses a model;
- measured query latency.

The ledger is a vector of independently bounded resources. An arm may not trade
unbounded work in one component for savings in another. If a matched-total-cost
scalar is reported, it must be derived from a versioned, predeclared
normalization and weight table; the raw resource vector remains part of the
result. The independent flat route and any fallback consume the same envelope,
with no hidden fallback allowance.

Map construction, update and rebuild cost are recorded in a separate offline
ledger. Each experiment declares the workload used to amortize that cost
(number of queries, updates and rebuilds); the matched-total-cost slice adds
the applicable amortized offline cost to the query-time cost for every arm.
The query-only slice reports online work separately. HN-0 reports both
matched-total-cost and matched-latency slices, with the same final ContextPack
budget for every arm. Calibration and policy tuning must be separated from
held-out evaluation.

The benchmark fixes one evaluation granularity per fixture: document, block or
segment. It must not vary granularity between comparison arms.

Metrics include:

- Recall@k, nDCG and final hydrated recall;
- map route-selection accuracy;
- rare-facet, correction and conflict recall;
- cross-domain recall and unknown-domain abstention;
- candidate count, decoded bytes and p50/p95/p99 latency;
- false-routing and stale-map rates;
- scope/access leakage;
- update, delete, rebuild and relocation consistency;
- provenance and lifecycle preservation.

A map-only answer is not accepted as factual evidence. Navigation quality is
measured by the downstream retrieval and decision probes it enables.

## 8. Acceptance fixtures

The first fixture set includes:

- a broad query that requires several child maps;
- an underspecified query with useful aliases;
- a rare fact absent from the map but present in canonical evidence;
- a stale child map after source revision;
- a wrong route that must fall back to flat retrieval;
- a `Raw` or `Candidate` root map that is ignored and leaves flat retrieval
  unchanged;
- the same local ID in two scopes;
- a forbidden scope referenced by a map;
- a map-only candidate with no source hydration;
- a cross-context query with independent frontiers;
- a hub map whose fanout exceeds its bounded policy.

The fixture must distinguish no map, stale map, filtered route,
map-budget exhaustion and ordinary retrieval miss.

## 9. Non-goals

This contract does not add:

- a generic knowledge hierarchy;
- a new memory subsystem;
- a scheduler or autonomous planner;
- a universal `TrustScore`;
- a map-based authority or access-control system;
- a new DBI, public C++ API or mandatory LLM dependency.

The next implementation slice, if approved, is a small read-only DomainMap
router over existing activation and retrieval contracts.
