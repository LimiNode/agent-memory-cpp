# SemCS memory-substrate compatibility audit

Status: **research/audit only**. This document does not implement SemCS/SCS,
add a graph backend, create a new `KnowledgeUnitKind`, or change an MDBX
schema. It records whether the current `agent-memory-cpp` contracts can host a
future Semantic Construct Search adapter owned by ADELIA.

Audit baseline: `main@dde38ae89fd8bef4be4e7306f2250a7fe3a094d4` (post-#611).
The bounded Markdown Technical Preview remains the release candidate. PR #612
belongs to the second agent and is intentionally not edited, rebased, or used
as evidence here. The ADELIA material, including Draft PR #232, is external
design input rather than an approved `agent-memory-cpp` API.

The supplied material uses both **SemCS** and **SCS**. This report uses SemCS
for the cross-project capability and reserves `SCS-0` for a future ADELIA
profile. The names are not interchangeable implementation contracts.

## Audit method and evidence boundary

The audit compared the current source and owner guides, then classified each
claim as `Implemented`, `Partial`, `Contract only`, `Docs/tests only`, or
`Roadmap only`. A green test is evidence only for the behavior exercised by
that test; a roadmap record is not a product capability.

Relevant owners inspected:

- [Knowledge units](../knowledge-units-roadmap.md), [knowledge base](../knowledge-base-roadmap.md), and [linked derived knowledge](../linked-derived-knowledge.md);
- [artifact and provenance](../artifact-provenance-roadmap.md), [source trust](../source-trust-roadmap.md), and [memory lifecycle governance](../memory-lifecycle-governance-roadmap.md);
- [root-resource frontier conformance](../root-resource-frontier-conformance.md) and [retrieval coverage](../retrieval-roadmap-coverage.md);
- public `KnowledgeUnitEnvelope`, `ResourceManifest`, `DerivedPublicationRequest`,
  `MdbxResourceFrontierSnapshot`, `MdbxDerivedPublicationStore` and
  `MdbxCurrentDerivedPublicationReader` contracts;
- conformance tests for derived publication, frontier fencing/snapshots and
  current-read revalidation.

The repository does not contain the ADELIA checkout or the files named in the
integration reference. Their claims below are therefore a read-only comparison
against the supplied proposal, not an executable cross-repository test.

## 1. Architecture coverage matrix

| SemCS requirement | Existing owner / evidence | Status | Gap and minimal action |
|---|---|---|---|
| Durable occurrence identity | `KnowledgeUnitId`, `ScopeId`, monotonic non-reused unit allocation; `ResourceId` and typed artifact IDs | Implemented for M0 local units; Partial for portable occurrences | A SemCS occurrence needs an explicit profile identity and cross-environment mapping. Reuse existing opaque IDs and add an additive profile contract only after SCS-0 fixes its identity rules. |
| Exact unit/source revision binding | `ResourceRevisionRef`, `SourceRefSummary`, `ResourceManifest`; M0 source-backed hydration | Implemented for current Markdown/Chunk path | Multi-parent semantic records need all exact parent revisions, not the one optional `KnowledgeUnitEnvelope::derived_from` field. |
| Opaque typed semantic artifact | `KnowledgeUnitEnvelope.primary_text`, `ResourceManifest` and `Artifact`/`BlobDigest` can carry bounded payloads | Partial | No neutral envelope stores profile ID, schema version, canonical payload digest and opaque bytes as one durable semantic artifact. Define a storage-neutral value contract only after ADELIA supplies SCS-0 grammar. |
| Structural byte-for-byte round-trip | Length-framed MDBX serializers and File CAS SHA-256; reopen tests | Partial | Existing serializers round-trip their own records, but no SemCS artifact/profile envelope fixture exists. Add a profile-owned opaque payload fixture, not a semantic parser in memory. |
| Multi-parent derivation | `DerivedPublicationRequest.inputs` is a vector of unit revision/source generation/digest bindings; B1/B2 fence validates inputs | Partial / narrow implementation | The current publication is storage-oriented and single-scope; it lacks typed source anchors, recipe/producer fingerprints and an independent derivation manifest. Add an additive derivation record over existing bindings. |
| Immutable derivation DAG | `linked-derived-knowledge.md` defines acyclic technical lineage and immutable historical bindings | Contract only | No Relation/derivation graph store is implemented. Publication records must reject cycles and preserve ordered direct inputs before any graph view is materialized. |
| Typed semantic relations | `KnowledgeUnitKind::Relation` and roadmap `RelationClass`/`EdgeKind` vocabulary | Contract only / roadmap only | No production Relation unit authoring or adjacency store is present. Do not add enum values or infer semantics from graph edges. |
| Separate semantic and lineage channels | `RelationClass` and linked-derived owner guide distinguish TechnicalLineage, Semantic, Evidence and Supersession | Contract only | Keep one canonical publication/provenance authority; materialized relation rows remain optional views with separate traversal flags and budgets. |
| Reverse dependency lookup | Roadmap specifies reverse affected-unit lookup; publication inputs are forward-only in current slice | Contract only | Add bounded reverse lookup as a derived index over an immutable publication manifest. It must be rebuildable and must not become provenance authority. |
| External support assessments | Source-trust and knowledge-base guides distinguish support/evidence from truth | Roadmap only | No support-assessment persistence contract is implemented. A future record must retain assessor, policy, frontier and revision without computing a verdict in memory. |
| Historical visibility at frontier F | `MdbxResourceFrontierSnapshot` is one consistent current read view; current reader has `Historical` and `Current` dispositions | Partial | There is no general historical `read_at_frontier` API or durable publication visibility epoch. Add a frontier/visibility receipt and exact historical reader before claiming reconstruction. |
| Current stale rejection | B1 snapshot, B2 fenced publication and B3 `MdbxCurrentDerivedPublicationReader` validate active units/root manifests in one read transaction | Implemented for fenced derived-publication slice | The status is bounded to this MDBX profile. It is not a general semantic graph reader or authorization system. |
| Scope/access isolation | `ScopeId`, access-scope checks and explicit unavailable/stale results in M0/B3 | Partial | Access policy is external; relation traversal and historical access decisions are not implemented. Never leak derived body/locators before policy admission. |
| Bounded graph retrieval | Roadmap budgets depth/fanout/bytes/time and current lexical/retrieval budgets exist | Contract only | No graph traversal implementation. Preserve independent flat retrieval fallback and explicit `Partial`/budget outcomes. |
| Replayable publication | MDBX body/manifest/marker transaction, create-or-validate replay, reopen tests | Implemented for storage/fenced publication | Cross-store semantic artifact plus external CAS atomicity is not proven. Keep replay identity separate from truth/admission policy. |
| Release compatibility with Markdown | #611 changes only consumer/tests/docs; existing Markdown schema and CAS remain intact | Implemented as compatibility constraint | SemCS must be additive: optional tables/projections and versioned opaque records; no rewrite of existing Raw Markdown rows. |

The matrix deliberately reports `Relation` as a declared kind, not as an
implemented graph facility. A type enum, roadmap pseudocode and a test-owned
fixture do not establish durable authoring or reopen semantics.

## 2. Ownership boundary: memory versus ADELIA

| Concern | `agent-memory-cpp` owns | ADELIA / external reasoner owns |
|---|---|---|
| Identity and bytes | Opaque artifact/occurrence IDs, exact revisions, canonical digest, deterministic serialization and retention outcomes | Meaning of a profile and semantic equivalence of two payloads |
| Derivation | Immutable direct parent revisions, operation/profile fingerprints, source frontier witness, publication generation and replay | Compose/Substitute/Relax/Generalize semantics and operator validity |
| Relations | Typed relation occurrences as optional persisted views, scope checks, bounded traversal and provenance links | Which relations to propose, interpret or use in reasoning |
| Epistemic state | Store externally supplied support/objection assessments with assessor/policy/frontier provenance | Truth, belief revision, hypothesis selection, admission and research policy |
| Freshness | Validate stored bindings against an authoritative frontier and return Current/Stale/Historical/Unavailable | Decide whether a current result is acceptable for an inquiry or action |
| Access | Enforce scope/lifecycle/access preconditions before body or locator materialization | Supply policy, principal and task-specific authorization context |
| Retrieval | Candidate bounds, snapshot consistency, relation-channel budgets and fail-closed dispositions | Query planning, semantic routing, model/tool calls and final context policy |

Memory must not infer truth from a relation edge, retrieval score, model
confidence or support count. `Stored`, `Historical`, `Current`, `Stale`,
`Unavailable`, `Supported`, `Refuted` and `Admitted` are separate states.

## 3. Minimal neutral logical contract

The following is a design sketch for a future profile adapter, not a C++ API or
MDBX DBI mandate. It intentionally composes existing IDs and revisions.

```text
SemanticArtifactEnvelope {
  artifact_id: opaque ArtifactId/OccurrenceId
  occurrence_revision: uint64
  scope: ScopeId
  profile_id: string                 # e.g. adelia.scs-0
  profile_version: uint32
  encoding_schema_id: string
  encoding_schema_version: uint32
  canonical_payload_digest: BlobDigest
  payload_bytes: opaque length-delimited bytes
  source_frontier: FrontierWitness
  evidence: [EvidenceBinding]
  visibility: PublicationVisibility
}

DerivationPublication {
  publication_id: opaque
  output: (artifact_id, occurrence_revision)
  ordered_unit_inputs: [(KnowledgeUnitId, unit_revision)]
  ordered_source_inputs: [SourceEvidenceBinding]
  operation_id: string
  operation_version: string
  parameters_digest: BlobDigest
  producer_fingerprint: BlobDigest
  source_frontier: FrontierWitness
  publication_generation: uint64
  marker: committed
}

SourceEvidenceBinding {
  source_revision: ResourceRevisionRef # resource identity, generation, digest
  evidence_anchor_id: opaque
}
```

The immutable `marker` records only committed storage publication. It is not a
stored lifecycle state machine. `Current`, `Historical`, `Stale` and
`Unavailable` are read dispositions derived from authoritative frontier,
revision and access evidence; external admission remains a separate policy
decision. This sketch therefore does not add an implementation requirement for
persisting or transitioning those read dispositions.

Required invariants:

1. `profile_id`, schema version and digest are part of the durable envelope;
   memory does not parse or reinterpret `payload_bytes`.
2. Ordered input bindings are canonicalized before hashing. Reordering inputs
   changes the publication digest unless the profile explicitly declares an
   unordered operation.
3. Every unit parent is an exact retained revision. Every source evidence
   binding resolves to an immutable source revision with the equivalent of the
   full `ResourceRevisionRef` (resource identity, generation and digest). A
   current revision is never substituted for a missing historical parent.
4. A derivation publication is acyclic and immutable. Re-materialization creates
   a new output revision/generation.
5. The committed publication marker is the sole atomic storage-visibility point
   for the complete record. It establishes `Committed` storage state, but does
   not establish authoritative freshness, current claim support, host
   authorization, semantic validity or `Admitted` use. `Current` requires an
   authoritative frontier reader, and `Admitted` requires the external access
   and policy decision. Optional Relation rows are views over the committed
   manifest and can be rebuilt or absent.
6. A payload digest proves bytes, not semantic equivalence. Equivalence is an
   external SCS/ADELIA assessment.
7. Historical and current reads have separate authorization and freshness
   checks; missing evidence yields `Unavailable` or an explicitly `Partial`
   result without derived body leakage.

This shape is compatible with the existing `DerivedPublicationInput`,
`ResourceRevisionRef`, `BlobDigest`, `MdbxResourceFrontierSnapshot` and fenced
publication concepts, but it is not fully represented by those current types.
In particular, an opaque `SourceRevisionId` or `EvidenceAnchorId` is only a
reference: it is not an authoritative revision binding until it resolves to
the immutable source identity, generation and digest carried by
`ResourceRevisionRef`.

## 4. Three graph semantics

### Semantic relations

Answer: “what is related, supports, contradicts or is useful to inspect?”
Cycles are allowed. Edges do not establish evidence independence, truth,
access or derivation ancestry. Traversal must opt into semantic classes and
carry scope, access, depth, fanout, byte and time budgets.

### Derivation lineage

Answer: “which exact revisions and evidence produced this output?” It is a DAG
of immutable publication manifests. A semantic `Summarizes` edge may be a view
of this lineage, but the edge is not its authority. Source updates make active
descendants stale; they do not rewrite historical manifests.

### Epistemic dependencies

Answer: “which externally supplied assessments support or object to a
hypothesis?” Competing hypotheses and independent alternatives remain
separate. The memory layer stores assessment provenance and historical state;
it does not run ATMS, truth maintenance or automatic belief revision.

The same physical backend may eventually host these rows, but the channels have
different cycle, freshness and authorization rules. A single generic “graph
edge” API would erase those distinctions and is rejected.

## 5. Historical visibility and frontier gap

| State to distinguish | Current evidence | Audit result |
|---|---|---|
| Physically present row | MDBX store `read`/`read_in_transaction` and reopen tests | Implemented for owned slices |
| Published and visible at F0 | B1 snapshot identity and B2 commit marker | Partial; no general durable visibility epoch |
| Added after F0 | Snapshot transaction lifetime and publication generation | Partial for one MDBX snapshot; no cross-store historical reader |
| Retained but not current | `read_historical`, unit lifecycle and `Stale` current reader outcome | Implemented for fenced derived publication, not all semantic records |
| Visible before but inaccessible now | Scope/lifecycle checks and `Unavailable` dispositions | Partial; policy/principal authorization is external and not persisted end-to-end |

`read_historical()` proves that a retained fenced publication can be inspected;
it does not reconstruct an arbitrary knowledge-base state at a named frontier.
Likewise, `MdbxResourceFrontierSnapshot` is a consistent current admission view,
not a historical time-travel database. A future `read_at_frontier` contract
must define frontier identity, visibility receipts, retention/authorization,
completeness and missing-body behavior before implementation.

## 6. Conceptual adapter operations

These names are capabilities, not committed API names. Each can be composed
from existing stores only after its missing authority is supplied.

| Capability | Owner / inputs | Success and failure dispositions | Current status |
|---|---|---|---|
| `publish_construct` | Profile adapter; envelope, scope, exact source frontier, publication generation | `Committed` only after marker; `Conflict`, `FenceRejected`, `Unavailable`; no implicit semantic validation | Partial via fenced derived publication, no opaque SemCS profile |
| `read_construct` | Artifact store + profile ID + exact revision + access policy | `Historical`, `Current`, `Stale`, `Unavailable`; never substitute newer bytes | Partial; current derived reader is narrower |
| `publish_derivation` | Ordered exact parents, operation/producer fingerprints, frontier | Acyclic committed marker or rejected replay; graph view optional | Partial via `DerivedPublicationInput`, missing typed manifest fields |
| `read_dependencies` | Publication manifest, scope and bounded route | Complete/Partial/Stale/Unavailable with exact revisions | Contract only; no reverse multi-parent API |
| `read_dependents` | Rebuildable reverse projection over committed manifests | Bounded page and explicit coverage; unavailable if index not ready | Roadmap only |
| `publish_relation` | Relation occurrence, typed class, endpoints, evidence and scope | Idempotent committed view; semantic cycles allowed, derivation cycles rejected | Contract only; no Relation store |
| `read_related_subgraph` | Semantic relation channel, access scope and budgets | Complete/Partial/Stale/Unavailable; no lineage traversal by default | Contract only |
| `read_at_frontier` | Durable frontier/visibility receipt, exact revision and access policy | Exact historical state or explicit missing/inaccessible result | Missing authority; do not implement with sequential `find()` |
| `publish_revision_evidence` | External support assessment, assessor/policy and source frontier | Immutable historical assessment, conflict or unavailable | Roadmap only |

Transaction boundary for a first implementation should be one MDBX context and
one read/write transaction covering output, manifest and marker. Cross-context,
CAS, model and ADELIA policy coordination remains outside the library.

## 7. Acceptance fixtures

The first four are the recommended implementation slice. “Executable” means
the current main already exercises the declared behavior; “contract-only” means
the fixture is specified but must not be represented by a fake test-owned row.

| Fixture | Expected invariant | Current status |
|---|---|---|
| 1. Structural round-trip | Opaque SCS-0 payload, profile/schema identity, digest and occurrence revision survive close/reopen byte-for-byte | Contract-only; no neutral envelope/profile adapter |
| 2. Multi-parent derivation | A@1 + B@2 through T produces C@1; direct and reverse dependencies retain exact revisions | Partial: fenced publication accepts multiple unit inputs, but no typed derivation manifest/reverse store |
| 3. Premise revision | A@1 becomes stale while independent B@1 assessment remains historical; no memory verdict is computed | Partial for current derived stale checks; epistemic assessment persistence is roadmap-only |
| 4. Historical reconstruction | F0 and F1 expose different declared visibility while immutable lineage remains addressable | Partial: current snapshot/historical publication exists; arbitrary `read_at_frontier` is absent |
| 5. Competing hypotheses | H1/H2 remain separate with independent evidence and assessments | Contract-only |
| 6. Relation/derivation separation | Semantic cycle is accepted; derivation cycle is rejected; no edge grants access | Contract-only; no Relation storage |
| 7. Scope and identity isolation | Same local identity in two scopes resolves independently; wrong scope is unavailable | Partial: ScopeId checks exist in M0/B1/B3, not graph traversal |
| 8. Bounded subgraph | Depth/fanout/records/bytes/time budgets yield explicit incomplete result and flat fallback | Contract-only; lexical budgets exist but no graph traversal |

The implementation order should be: (a) profile-owned opaque envelope and
round-trip fixture; (b) graph-free typed derivation manifest/reverse lookup;
(c) historical visibility receipt and `read_at_frontier`; (d) optional Relation
projection and bounded traversal. ADELIA must first freeze SCS-0 grammar,
normalization, operator determinism and equivalence policy.

## 8. Compatibility and release impact

No incompatibility with the frozen Markdown preview is visible in this audit.
The safe compatibility strategy is additive:

- retain existing Raw Markdown `ResourceId`, `ResourceRevisionRef`, Chunk,
  manifest, projection and CAS rows unchanged;
- store SemCS artifacts and derivation manifests under a versioned optional
  profile, with independent rebuildable relation indexes;
- keep payload/profile codecs out of canonical KnowledgeUnit identity unless the
  profile explicitly supplies a versioned identity recipe;
- reopen old M0 databases with SemCS capabilities disabled;
- reject unknown profile, identity scheme, digest algorithm or schema version
  before writing, rather than silently migrating;
- never make a graph backend a prerequisite for publication or provenance.

The audit found no reason to delay the bounded Markdown preview. SemCS becomes a
future M1b/M2 capability gate only when its optional profile requires a change to
existing canonical rows or weakens exact revision/freshness semantics. The
missing SemCS features are therefore not current Markdown release blockers.

## 9. Recommended minimal vertical slice

After SCS-0 profile decisions, create one independently reviewed implementation
PR with no graph backend:

1. a profile-neutral opaque artifact envelope (profile/schema IDs, digest,
   payload bytes, scope and occurrence revision);
2. a graph-free multi-parent derivation manifest composed with existing
   `DerivedPublicationInput` and `ResourceRevisionRef` values;
3. deterministic serialization and structural round-trip/reopen fixtures;
4. bounded direct and reverse dependency reads from the manifest;
5. current/stale/historical/unavailable outcomes using one authenticated
   frontier snapshot; and
6. an optional Relation projection only after the manifest conformance passes.

The PR must prove no duplicate publication on replay, reject cycles and stale
frontiers, preserve old Markdown rows, and fail closed when a parent or policy
permission is missing. It must not add a universal memory facade, coordinator,
ATMS, inference engine, graph router or compact scorer.

## 10. Rejected alternatives and open questions

Rejected:

- replacing `KnowledgeUnitEnvelope::derived_from` with an unbounded vector in
  the M0 hot path;
- treating Relation rows or retrieval scores as provenance/truth;
- implementing `read_at_frontier` as sequential current `find()` calls;
- making graph storage mandatory for graph-free publication;
- adding a new enum kind solely for ADELIA taxonomy;
- implementing semantic equivalence, belief revision or ATMS in memory;
- importing ADELIA Draft PR #232 as a normative cross-project API.

Open questions for ADELIA before an implementation PR:

1. What exact SCS-0 grammar, canonicalization and deterministic operator set are
   frozen for profile version 1?
2. Which payload fields affect artifact identity, and which are annotations?
3. Is parent ordering semantic for every operator, or can a profile declare a
   canonical unordered set?
4. What constitutes an independent support assessment and who supplies it?
5. Which historical frontiers are named, retained and authorized for replay?
6. What is the minimum acceptable partial result and redaction behavior when one
   parent body is unavailable?
7. Which relation classes are navigation hints versus evidence-bearing claims?

Until these answers are recorded by ADELIA, `agent-memory-cpp` should keep the
neutral storage contract small and profile-agnostic.

## Decision

**Audit result: compatible in principle, not implemented as SemCS.** Existing
identity, provenance, fenced publication and bounded frontier slices are useful
substrate. The missing pieces are additive semantic-profile persistence,
historical visibility receipts, reverse derivation lookup and optional Relation
views. None is a blocker for the frozen Markdown Technical Preview. This PR is
documentation/research only and must remain Draft pending independent review.
