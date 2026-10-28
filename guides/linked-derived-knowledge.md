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
- [`derived-representations-and-codecs.md`](derived-representations-and-codecs.md)
  for codec-neutral aspects, retention and compact-vector readiness.

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

## Relation classes and traversal channels

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

The generic derivation/publication record is authoritative and does not
require a graph capability. A Relation occurrence is an optional materialized
view over that record. When present, it has `GraphEdgeId` equal to the Relation
unit's durable global identity. Derivation views use the existing
`RelationClass::TechnicalLineage`; source/evidence links use the existing
`RelationClass::Evidence`; semantic links use `RelationClass::Semantic`; and
replacement links use `RelationClass::Supersession`. No new class or edge enum
is introduced.

These classes are separate traversal channels. A semantic query reads only
the explicitly allowed semantic classes by default. Technical lineage,
evidence drill-down and supersession each require an explicit route flag and
have independent depth, fanout, byte and time budgets. A route trace records
the channel and every admitted edge. `GraphEdge.from/to` remains an identity
pair only; it is not the revision binding described below.

`graph_edges_by_src` and
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

### Authoritative revision binding

`GraphEdge.from` and `GraphEdge.to` resolve the existing scoped
`KnowledgeUnitRef` identity and deliberately do not carry a revision. The
authoritative revision binding lives in the Relation-owned derivation payload
for the occurrence (and in its canonical provenance record), not in a second
unit-reference type. For each occurrence the payload stores:

```text
output:       (scope, unit_id, revision)
input_units:  [(scope, unit_id, revision), ...]   # canonical byte order
input_sources:[(SourceRevisionId, EvidenceAnchorId), ...]
```

The Relation's GraphEdge, when materialized, points from the output unit
identity to one direct input identity. A single `DerivationPublication`
therefore owns one output record and one-to-many Relation occurrences, each
with a stable occurrence identity and the same publication generation. The
complete ordered input list is stored once in the publication manifest;
occurrences carry only their own input binding, occurrence digest and
publication identity. This avoids copying an N-input list into each of N
records. The publication digest covers the ordered input list, source anchors,
recipe/producer fingerprints and output revision. Serialization uses fixed
field order, explicit integer endianness and length-delimited byte strings;
unknown trailing fields are rejected for this proposal rather than silently
ignored.

Resolution is scope-qualified and revision-qualified. A reader first resolves
the retained unit/source revision and then its body or evidence anchor. If the
requested historical revision is missing, erased or outside retention, the
result is `Unavailable` (or `Partial` for an explicitly best-effort route); a
newer current revision is never substituted. Replacing a unit creates a new
envelope revision and leaves the old binding immutable. Updating a source
advances the source frontier and affects current freshness, but does not alter
historical Relation payloads.

For example, the logical record for `Summary S@7` derived from `A@1` and
`B@4` is serialized as:

```text
LDK1 | scope=kb | output=S:7 | inputs=[A:1,B:4]
     | source_frontier={A:1,B:4} | generation=12
     | recipe=sha256:... | producer=sha256:...
     | relations=[rel:S->A:g12, rel:S->B:g12]
```

After reopen, the store validates the digest, resolves both retained input
revisions and checks that both Relation occurrences carry generation 12 before
returning an active publication. If `A@1` was collected, the same record is
still available as historical lineage, but body hydration is `Unavailable`;
`A@2` must not be used as a substitute.

### Atomic multi-input publication

The first KB-1 profile uses a bounded transaction only when the output unit,
all Relation occurrences, provenance payload and publication marker share one
MDBX transaction domain. Its state machine is:

1. **Prepared** — freeze the canonical ordered input set and digest, record
   expected cardinality, output identity/revision, source frontier, recipe and
   producer fingerprints, and allocate a new generation.
2. **Staged** — write the output body, all Relation-owned occurrences and
   revision-qualified payloads. Staged rows are invisible to active reads.
3. **Committed** — in one transaction, verify every expected input and relation
   occurrence, then write the publication marker containing the digest,
   cardinality and generation. This marker is the sole active commit point.
4. **Visible** — readers in one snapshot admit the output only when the marker
   is committed and every referenced occurrence resolves. A crash before the
   marker leaves an unpublished generation that recovery may discard or retry.

Retries are create-or-validate by `(output revision, input digest, recipe,
producer)`; they never create duplicate Relation occurrences. A crash after
commit is idempotent and reopen reconstructs visibility from the marker. A
single transaction domain gives atomic visibility and snapshot consistency,
but not cross-database or cross-process coordination, lease arbitration,
long-running orchestration or automatic retry scheduling. Those guarantees
belong to a future publication coordinator; the coordinator stack is not
assumed to be integrated by this document.

### Generic aspects without graph materialization

An aspect projection such as `example/actor` or `example/relationships` may
publish a versioned text, structured payload or embedding-backed projection
using the generic publication manifest alone. It need not create a semantic
Relation or an adjacency row. If a caller later asks for evidence drill-down,
the implementation can materialize explicitly permitted technical-lineage or
evidence Relation views from the same manifest. The manifest remains the only
authority, so rebuilding or deleting a view cannot change provenance.

The same manifest carries a bounded `dependency_frontier_witness`: a digest of
direct input revisions and the committed witnesses of derived inputs. A reader
checks this witness and the current source frontier for the requested route,
following only the declared dependency frontier. If any epoch or witness
mismatches, the read is `Stale` even when reverse invalidation is incomplete;
there is no full-corpus scan per query. Writers fence publication against the
captured frontier epoch before committing a new generation.

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

## Freshness, history and authorization

The following states are intentionally distinct:

- **Historical validity** asks whether a retained derivation record still
  describes the exact input revisions recorded at publication time. It does
  not depend on today's source frontier.
- **Active freshness** asks whether the publication generation matches the
  current source frontier and active unit revisions for the requested scope.
- **Unit lifecycle/access** asks whether each unit and source revision is
  visible to the caller under its current policy.
- **Re-materialization** creates a new output revision and generation; it
  never edits an old derivation record.

When Source A advances from revision 1 to revision 2, a Summary L1 based on
A@1 and every active Summary L2 that depends on L1 are stale for `Current`
reads. Their historical records remain valid under an explicit historical
query. Reverse invalidation is an optimization, not the authority: if it is
interrupted, query-time validation compares the recorded source frontier and
unit revisions with the current frontier and fails closed as `Stale`.

Writers use a revision/read frontier fence: a publication prepared against a
frontier must either commit against that same frontier or be rejected and
retried. Readers capture one snapshot generation, validate all direct inputs
before materializing text, and emit diagnostics identifying the expected and
observed frontier, generation and missing dependency. No retained historical
revision is deleted merely because an active descendant is stale.

A navigation projection can select where to look, but it cannot authorize an
input or replace source hydration. Every hop independently checks scope,
access, lifecycle and generation. A derived unit whose supporting inputs cannot
be inspected under the effective policy is not admitted as derived text in a
retrieval hit, snippet or ContextPack. The authorization check therefore runs
before text materialization, route-trace expansion or source-locator exposure;
it returns `Unavailable`/`Partial` without leaking the summary or upstream
metadata. A redacted representation is a separate publication with its own
provenance, disclosure policy and explicit admission rule, not an implicit
fallback.

The same admission fence applies to every candidate-producing path: lexical
postings, dense/ANN indexes, metadata filters, semantic relations and future
hybrid fusion. An index row may be staged, stale or unauthorized; its presence
never publishes derived text. Before a hit can produce a snippet or ContextPack
block, the reader validates the committed publication generation, frontier
witness, representation/index version and effective authorization. Failed
checks are dropped with an explicit diagnostic rather than repaired by
substituting a newer unit revision.

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
- a five-input publication that crashes after partial staging and remains
  invisible until every Relation occurrence is committed;
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
- historical lookup of an exact retained revision, with a missing retained
  body returning `Unavailable` rather than a newer replacement;
- identical unit IDs in different scopes resolving independently;
- an updated source while reverse invalidation is incomplete, with query-time
  stale rejection;
- access denial before derived text, route trace or source locator
  materialization;
- semantic retrieval never traversing technical lineage unless explicitly
  enabled;
- content-changing replacement preserving unit identity while advancing its
  revision;
- idempotent re-publication and reopen without duplicate Relation occurrences;
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
