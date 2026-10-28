# Linked Derived Knowledge

Status: Proposal / integration contract. No implementation is claimed.
Baseline: `main@be3c80c1c635b92b21cee6a37963526d32ea88fd`, 2026-10-28.

This guide reconciles multi-input derived knowledge, typed relations and
hierarchical navigation. It is an integration map, not a new normative
hierarchy. The owning contracts remain:

- [`knowledge-units-roadmap.md`](knowledge-units-roadmap.md) for unit and
  Relation identity;
- [`source-trust-roadmap.md`](source-trust-roadmap.md) for provenance,
  ancestry and non-laundering;
- [`knowledge-base-roadmap.md`](knowledge-base-roadmap.md) for Relation-owned
  graph storage and bounded retrieval;
- [`knowledge-activation-roadmap.md`](knowledge-activation-roadmap.md) for
  concepts, playbooks and DomainMaps;
- [`hierarchical-navigation-roadmap.md`](hierarchical-navigation-roadmap.md)
  for navigation and evaluation;
- [`compaction-roadmap.md`](compaction-roadmap.md) for host-scheduled derived
  materialization jobs;
- [`milestones.md`](milestones.md) for milestone scope.

## Decision

`agent-memory-cpp` should not embed a MemWalker or RAPTOR engine. The reusable
product capability is a versioned linked-knowledge substrate that can:

- publish `Note`, `Summary`, `Playbook` and other derived units;
- preserve every immediate input at an exact revision;
- traverse derivation and semantic relations under explicit budgets;
- retain historical generations and reject stale active views;
- hydrate canonical evidence before a factual result is treated as complete.

MemWalker is a query-time navigation-policy reference. RAPTOR is an offline
multi-level construction and mixed-level retrieval reference. Their model
prompts, clustering choices and routing policies remain external adapters or
research implementations.

## Existing substrate and verified gap

The repository already defines `Summary`, `Note`, `Playbook`, `DomainMap` and
`Relation` unit kinds; versioned projections; Relation-owned graph edges;
source provenance; bounded graph expansion; and the HN-0 navigation gate.

The current M0 C++ envelope is deliberately narrow. In particular,
`KnowledgeUnitEnvelope::derived_from` is one optional `KnowledgeUnitId`, while
the documented `SummaryTreeJob` needs multiple exact input revisions. The M0
field remains a compatibility shortcut for single-parent lineage. It is not
the durable representation for a summary, concept or article derived from many
inputs. Multi-input lineage belongs in typed Relation/derivation records and
full provenance storage. Do not replace the optional field with an unbounded
vector in every hot-path envelope.

The current `MdbxKnowledgeUnitStore` is also a bounded Chunk-oriented slice.
The enum values and roadmap payload examples do not mean that durable authoring
and reopen are implemented for every kind.

## Two relation classes, one storage substrate

The logical graphs have different invariants but may share the existing
Relation-owned physical substrate.

### Derivation and evidence lineage

The derivation graph answers: "which exact inputs produced this output
revision?"

- nodes are revision-qualified units or source evidence;
- an output may have many direct inputs and an input may feed many outputs;
- edges point from an output revision to its direct input revisions;
- the graph is acyclic;
- historical edges are immutable;
- source updates make affected active derived generations stale without
  rewriting their history;
- transitive roots support evidence drill-down and independence assessment.

### Semantic and navigation relations

The semantic graph answers: "what is related, supported, contradicted,
required, summarized or useful to read next?"

- semantic edges may form cycles;
- they may have multiple parents and cross-links;
- they do not grant access or establish evidence independence;
- `References`, `RelatedTo`, `Supports`, `Contradicts`, `DependsOn` and
  `Summarizes` are logical examples, not a request for duplicate enum values
  when the existing `EdgeKind` vocabulary already represents the meaning;
- a `Summarizes` navigation edge is backed by derivation lineage when it makes
  an ancestry claim.

Both use one canonical Relation occurrence with `GraphEdgeId` equal to the
Relation unit's durable global identity. `graph_edges_by_src` and
`graph_edges_by_dst` are atomic traversal orientations, not independent truth.
Packed adjacency and hierarchy-specific indexes remain rebuildable views.

## Minimal logical record

The following is an illustration, not a ready public API or a DBI mandate:

```cpp
struct RevisionQualifiedUnitRef {
    KnowledgeUnitRef unit;
    std::uint64_t revision = 0;
};

struct DerivationPublication {
    RevisionQualifiedUnitRef output;
    std::vector<RevisionQualifiedUnitRef> direct_unit_inputs;
    std::vector<EvidenceAnchor> direct_source_inputs;
    std::string recipe_fingerprint;
    std::string producer_fingerprint;
    SourceFrontier source_frontier;
    std::uint64_t publication_generation = 0;
    CurationState curation_state = CurationState::Raw;
};
```

Required semantics:

- the output and every unit input carry exact revisions;
- source inputs identify exact source revisions and evidence anchors;
- recipe/producer configuration is fingerprinted when it affects output;
- the source frontier and publication generation are explicit;
- publication validates that no input can reach the output revision;
- deterministic input ordering is part of serialization;
- direct and reverse lookup are bounded and reopen-stable;
- publication/read status distinguishes active, stale and historical
  generations; the unit keeps the existing `Active`/`Superseded`/`Deprecated`/
  `Erased` lifecycle, and curation remains a separate axis;
- provenance and retention are preserved when an active generation changes.

No new DBI follows from this logical shape. The first implementation must try
the existing Relation unit plus `graph_edges_by_src`/`graph_edges_by_dst` and
update the DBI manifest, budget and migration rules only if a measured access
pattern proves an additional physical index necessary.

## Multi-level example

```text
Source A revision 4 ----> Summary L1-A revision 1 --+
                                                   +--> Summary L2 revision 3
Source B revision 7 ----> Summary L1-B revision 2 --+             |
                                                                  +--> Concept references
                                                                  +--> Playbook references
                                                                  +--> DomainMap route
```

The example permits arbitrary storage depth, multiple direct parents and
reverse dependency traversal. Every read is bounded by depth, fanout, decoded
bytes, records and time. Creating a summary does not supersede its inputs.
Several summaries with a shared root are not independent corroboration.
Contradictory roots remain visible instead of being collapsed into unsupported
consensus.

A source update publishes a new source revision and marks only dependent active
derived generations stale through reverse traversal. Old source and derived
revisions remain addressable under retention policy. Re-materialization creates
a new generation; it does not rewrite the previous derivation record.

## KB-1 Linked Knowledge profile

The smallest optional M1b subset for a technical preview is not hierarchical
generation. It is deterministic linked storage and reading:

1. durable creation/reopen of `Note`, `Summary` and `Playbook` units;
2. a concept represented by an appropriate existing kind plus
   `semantic_class=concept`, without changing the stable kind enum merely for
   taxonomy;
3. canonical text beyond the bounded envelope retrieval seed;
4. versioned publication of typed semantic and derivation relations, with the
   existing DurableGlobalIdentity capability enabled for Relation-owned edges;
5. bounded outgoing and incoming relation lookup;
6. bounded multi-hop evidence drill-down and reverse affected-unit lookup;
7. scope, access, lifecycle, generation and source-frontier validation;
8. explicit `Complete`, `Partial`, `Stale` and `Unavailable` read outcomes;
9. lexical retrieval over original and admitted derived units;
10. opening an old M0 profile without enabling linked-knowledge capabilities.

Summarization, clustering, Obsidian/Markdown link parsing and policy decisions
remain host/worker responsibilities. A host publishes normalized identities,
relations and provenance through the library contract.

## Retrieval and authorization

A navigation projection can select where to look, but it cannot authorize an
input or replace source hydration. Every hop independently checks scope,
access, lifecycle and generation. A derived unit whose supporting inputs cannot
be inspected under the effective policy returns an explicit incomplete outcome
unless a separately admitted redacted representation has its own valid
provenance and policy.

Flat retrieval remains available when a hierarchy is missing, stale or over
budget. Hierarchical traversal cannot silently consume the flat route's
reserved work budget. Query results distinguish:

- no relation;
- filtered relation;
- stale generation;
- missing or erased evidence;
- traversal-budget exhaustion;
- ordinary retrieval miss.

## MemWalker and RAPTOR evaluation

Extend the existing HN-0 comparison rather than creating another evaluation
framework.

| Arm | Purpose |
|---|---|
| Flat lexical/dense/hybrid | independent product-safe baseline |
| Bounded all-level retrieval | search admitted source and summary levels, then hydrate sources |
| Guided hierarchy traversal | MemWalker-like bounded descent and backtracking; model decisions stay outside core |
| Curated DomainMap navigation | compare a reviewed route against generated hierarchy |
| Map/tree only | negative control, not a production-safe default |

The RAPTOR research arm may use semantic clustering, recursive summaries,
collapsed-tree retrieval and soft membership with multiple parents. UMAP, GMM,
prompts and model calls are experiment configuration, not core dependencies.

Every arm records offline build/rebuild cost, update amplification, query
latency, decoded bytes, traversal work, model calls/tokens, final context budget,
citation fidelity, stale handling, contradiction recall and rare-fact recall.
All arms share corpus, frontiers, access policy, final budget and held-out
queries. No approach receives automatic preferred status.

## Acceptance matrix

The first conformance fixture must cover:

- one Summary derived from two independent source revisions;
- a summary-of-summary chain with at least three levels;
- one source used by several summaries;
- a semantic cycle accepted and a derivation cycle rejected;
- source update marking all affected active descendants stale without rewriting
  historical versions;
- missing, erased or inaccessible evidence producing an explicit incomplete
  result without content leakage;
- two summaries of one source remaining dependent evidence;
- contradictory evidence remaining separately visible;
- crash/reopen preserving exact revision bindings and active generation;
- identical local IDs in two scopes failing closed;
- an imported link/locator rename not changing canonical Relation identity;
- flat retrieval when the hierarchy is absent or stale;
- traversal enforcing depth, fanout, record, byte and time budgets;
- partial relation publication and orphan traversal rows being rejected or
  recovered atomically.

## Delivery slices

Implementation should be split into three independently reviewable PRs:

1. **Durable derived units and typed relations.** Add the narrow
   Note/Summary/Playbook authoring profile, exact multi-input revision bindings,
   reverse lookup, reopen and atomic publication. This is the storage release
   gate.
2. **Evidence drill-down and invalidation.** Add bounded traversal, source
   hydration, stale propagation, authorization checks and explicit completion
   outcomes.
3. **Multi-level lexical baseline.** Retrieve original and admitted derived
   units, open supporting evidence and compare against the independent flat
   route. Hierarchical/model routing remains experimental.

## Placement and deferred work

- **Documentation now:** shared logical contract and owner alignment.
- **Optional KB-1 / M1b preview subset:** durable linked derived knowledge,
  reverse lookup, bounded drill-down, provenance and ordinary lexical retrieval.
- **Later M1b/M2:** navigable hierarchy, mixed-level retrieval, incremental
  hierarchy generation and re-clustering.
- **M2 research:** RAPTOR clustering choices, learned routing and adaptive
  summary-tree maintenance.

The richer AM-14 runtime abstraction/derivation components remain M2+ and are
not pulled into this narrow profile.

M0 remains a graph- and LLM-independent lexical text profile. This proposal
does not implement storage, add a DBI, change a wire enum, migrate a profile,
activate a coordinator, add LLM inference, or claim MemWalker/RAPTOR quality.

## Research references

- [MemWalker: Walking Down the Memory Maze](https://arxiv.org/abs/2310.05029)
- [RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval](https://arxiv.org/abs/2401.18059)
