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
- `imported` — supplied by another knowledge base;
- `reviewed` — derived or imported and accepted through review.

Origin does not determine truth. It determines how the map is maintained and
what validation it requires.

## 3. Map entry contract

A materialized map records, at minimum:

~~~yaml
map_id: domain.ai
scope_id: workspace-main
map_generation: 12
source_frontier: ...
origin: manual | derived | imported | reviewed
status: active | stale | unavailable
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

## 4. Activation and retrieval flow

A host may use a map to construct an `ActivationPlan` containing domains,
concepts, playbooks, route hints, query variants and budget hints.

The reference flow is:

1. retrieve a bounded set of root or domain maps;
2. expand only approved child maps within depth, fanout and byte budgets;
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
candidate and decoded-byte budgets. Map construction, calibration and policy
tuning must be separated from held-out evaluation.

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
