# KB-0 Architecture and Storage Audit

**Audit snapshot:** `origin/main` at `b149a5c0923c01eff662d7294796803a2766482a`

**Status:** audit-only document. It does not change production DTOs, DBIs,
schema versions, serialization, or runtime defaults.

This audit checks the boundary between canonical source data, knowledge-unit
records, derived projections, lexical candidates, and source materialization.
The normative milestone owner remains [`guides/milestones.md`](../milestones.md).
This document records evidence and release blockers; it is not a second
capability registry or roadmap.

## Executive result

The repository has a coherent text-first foundation and several strong
storage slices: UTF-8-safe raw import, durable `KnowledgeUnit` envelopes,
revision-aware Original projections, fenced derived publication, canonical C1
content, and rebuildable lexical indexes. The current implementation is not
yet a complete KB-0 Technical Preview because the product-facing retrieval path
does not compose all of those stores under one read snapshot and does not
hydrate source bytes through a typed evidence anchor.

Static source inspection identified two physical/profile facts. They must be
distinguished from runtime failures, which this audit did not reproduce:

1. `MdbxProjectionStore` and `MdbxDerivedPublicationStore` both derive the
   default table name `agent_memory_unit_projections`. Their key prefixes do
   not collide today. Joint payload/schema, maintenance and migration ownership
   needs qualification; data corruption or an incompatible operation has not
   been demonstrated. This is a potential pre-release architecture risk, not
   proof that the DBI must be split.
2. The manifest describes `lexical_posting_segments` as a reserved M2 segment
   layout, while the flat-v2 and seg-v2 adapters use that DBI for versioned
   KV payload/keyspaces. The code documents the distinction, but the selected
   profile and physical ownership are not yet machine-checked together. This
   is an experimental profile/schema-tooling risk, not a mandatory gate for
   the bounded text-first KB-0 path while those adapters remain opt-in.

These are not fixed in this audit PR.

## Evidence and method

The review used the following normative sources and implementation points:

- `guides/milestones.md` (normative M0/M1/M2 scope and required gates);
- `guides/product-path.md` (KB-0 product acceptance);
- `guides/retrieval-roadmap-coverage.md` (current implementation status);
- `guides/dbi-manifest.yaml` and section 5.5 of
  `guides/mdbx-containers-extension-tz.md` (physical DBI authority);
- `guides/storage-backend-integration-roadmap.md` (context, transaction and
  ownership rules);
- `guides/artifact-provenance-roadmap.md`, `guides/chunkers-roadmap.md`,
  `guides/structured-data-retrieval-roadmap.md`,
  `guides/multimodal-binary-retrieval-roadmap.md`, and
  `guides/linked-derived-knowledge.md`;
- domain values under `src/agent_memory/domain/`, storage contracts under
  `src/agent_memory/storage/`, ingestion code under
  `src/agent_memory/ingestion/`, and the MDBX adapters under
  `src/agent_memory/infrastructure/mdbx/`.

The implementation labels below mean:

- **Implemented:** code exists and has a corresponding focused test, but this
  does not automatically satisfy the complete M0 milestone;
- **Contract only:** a public contract or roadmap exists without the required
  production path;
- **Profile/experiment:** an intentionally bounded adapter or research slice;
- **Docs only:** no production DTO or storage implementation exists.

Evidence labels distinguish **STATIC** (source/contract inspection), **TEST
SOURCE** (a named executable fixture exists and was inspected, not rerun by
this docs-only audit), and **EXECUTED** (an actual recorded command/run).
The audit's executed checks are the DBI manifest validator and its self-test,
whitespace validation, and the documentation CI lane. These do not execute
MDBX or prove co-ownership safety. Runtime reproducers in the finding table
are acceptance work still to be performed, not reported test results.

## DTO and field ownership matrix

The table separates logical ownership from physical persistence. A derived
index may copy an identity field, but it never owns the canonical value.

| Record / field group | Logical owner | Identity-critical or mutable | Canonical / derived / physical | Required and revision binding | Serialization and validation | Lookup / implementation / tests | Migration risk |
|---|---|---|---|---|---|---|---|
| `ResourceId` | Resource importer / manifest | Identity-critical | Canonical logical identity | Required; stable across body updates in the M0 importer | `Resource.hpp`; non-empty validation; manifest key is `ResourceId` | `MdbxResourceImporter`, `MdbxResourceManifestStorage`; importer reopen/update tests | Medium: global uniqueness is profile policy, not a universal type |
| `ScopeId` | Host/application boundary | Identity-critical tenancy boundary | Canonical field and secondary-key prefix | Required on unit, projection and retrieval routes | Value-object validation; every scoped lookup must compare it | Scope tests in importer, projection, frontier and workspace suites | High if any legacy unscoped table is promoted |
| `ResourceRevision` | Resource importer / provenance profile | Identity-critical revision; pipeline fields are immutable metadata | Canonical revision descriptor | `resource_id + generation + body_digest`; derived text also binds extractor provenance | `Resource.hpp`; digest algorithm validation; manifest payload v1-v6 | Manifest storage and importer recovery tests | High: optional digest and prototype hashes need one profile decision |
| `ResourceBodyDigest` | Provenance layer | Identity-critical integrity value | Canonical physical-body fingerprint | Required for byte-verified paths; optional in generic manifest values | SHA-256 tagged value; equality checks in manifest/importer | `Resource.cpp`, manifest and importer tests | Medium: future BlobStore must preserve algorithm tag and bytes |
| `SourceRefSummary` | Knowledge-unit provenance | Identity-critical locator plus bounded mutable observation metadata | Canonical inline summary | Resource revision and quote range are immutable binding; preview is bounded evidence material | `KnowledgeUnit.cpp`; quote hash validates the preview, not source bytes | M0 unit/importer tests; no full source-ref DBI in M0 | High: source-ref expansion must not reinterpret `quote_hash(preview)` as byte proof |
| `KnowledgeUnitId` / `KnowledgeUnitKey` | Knowledge-unit store | ID is immutable; key is immutable identity | Canonical envelope identity | Unit revision changes on mutable canonical update; identity changes create a new unit | `KnowledgeUnit.hpp`; content-key and lifecycle validation | `MdbxKnowledgeUnitStore`; dedupe/reopen/guard tests | Medium: future global identity is a profile delta |
| `KnowledgeUnitEnvelope` | `IKnowledgeUnitStore` | Identity fields immutable; text, lifecycle and timestamps are mutable by contract | Canonical hot record | `scope_id`, kind, content hash and source summaries bind the unit; `revision` binds projections | `agent_memory.knowledge_unit.v1`; bounded source count/preview on MDBX load | Durable unit store and raw importer tests | High: envelope is intentionally hot; full payloads must not be added casually |
| `primary_text` / `display_text` | Knowledge-unit store | Canonical text for M0; display is a view | Canonical value copied into Original projection | Original projection must bind exact unit revision and body | Envelope validation; projection body equality check | Projection-aware and revision-bound retrieval tests | Medium: source-byte hydration must not replace this value silently |
| `SearchProjection` | Projection store / retrieval profile | Derived; projection identity includes kind and revision | Derived durable value | `(scope, unit, kind, projection_revision)` plus unit revision and derivation fingerprint | `agent_memory.projection.v1`; `is_valid_search_projection` | `MdbxProjectionStore`, Original publication tests | Medium: projection-generation changes must invalidate indexes without changing source identity |
| `ResourceManifest` / `DerivedRecordRef` | Resource importer or `ResourceIndexer` profile | Manifest topology is publication state; records are derived references | Canonical publication evidence, not source content | Resource revision, schema id/version, lifecycle and ordered bindings are checked | Payload versions v1-v6; topology/schema validation | Manifest/indexer/importer tests | High: two manifest schemas are intentionally incompatible and require explicit migration |
| `DerivedPublicationRequest` / `DerivedPublication` | Derived-publication store | Publication id and input frontier are identity-critical; body is derived | Derived durable publication | Exact ordered inputs and dependency frontier; fenced v2 marker for current-read admission | `agent_memory.derived_publication.v1/v2`; bounded payload limits | `MdbxDerivedPublicationStore`, B2/B3 tests | High: storage match is not authoritative freshness |
| `Document` / `DocumentChunk` | Legacy document adapter | IDs and parent relation are identity; text/metadata mutable per adapter | Canonical-ish legacy records plus derived ownership rows | `DocumentChunk.document_id` and `TextRange` bind local source coordinates | `agent_memory.document.v1`, `chunk.v1`, metadata list version; structural validation | `MdbxDocumentStorage`, `ResourceIndexer`; document and ownership tests | High: this adapter is not the KnowledgeUnit/KB-0 canonical path |
| `TextRange` / source locator | Domain value; future artifact profile owns typed locators | Locator is evidence identity, not text identity | Canonical evidence anchor when retained | Offset/length are revision-bound byte coordinates; line numbers are views | Only primitive range validation today | Document and importer tests; no general `EvidenceAnchor` DTO | High: transformed text and original bytes need an explicit representation binding |
| Lexical descriptor / frontier | Lexical index profile | Descriptor, tokenizer fingerprint, projection kind and statistics epoch are identity-critical | Derived physical/index state | Scope, projection, epoch, generation and layout bind every row or snapshot | `LexicalIndexDescriptor`, flat-v2/seg-v2 payload validators | Persistent and segmented lexical tests | High: index payload layout and manifest profile must be selected together |
| `Artifact`, `Representation`, `Segment`, `EvidenceAnchor` | Artifact-provenance roadmap | Planned identity-critical records | Docs only | Planned source revision → representation → segment bindings | No production DTO or serializer in this snapshot | No implementation tests; roadmap conformance only | High, but deferred from text-only M0 |
| Tabular row/cell locators | Structured-data roadmap | Planned typed locator | Docs only | Planned representation and row/cell revision binding | No parser or table DTO | No implementation tests | High; do not overload `TextRange` |
| Audio/video time/frame locators | Multimodal roadmap | Planned typed locator | Docs only | Planned media representation and interval binding | No media catalog or hydration adapter | No implementation tests | High; derived ASR/OCR is not original media |

### Ownership conclusions

- `KnowledgeUnitEnvelope` is a deliberately bounded hot record. It carries
  source summaries, not a full source body or a general payload union.
- `ResourceRevision.content_hash` and `pipeline_config_hash` are described as
  prototype-local hints, while `body_digest` is the portable evidence value.
  A release profile must not use the two 64-bit hints as a substitute for the
  digest.
- `SourceRefSummary.quote_hash` is computed over `preview`. It is useful for
  detecting preview corruption, but it cannot verify that a byte range in an
  external source still contains the same bytes.
- `KnowledgeUnitId`, `ChunkId`, `DocumentId`, `ResourceId` and future
  `SegmentId` are separate collision domains. Current raw import creates
  revision-bound Chunk units but has no durable `SegmentId` type.

## Physical MDBX DBI matrix

The following distinguishes actual adapters from manifest entries that are
reserved or documentation-only. Names are shown with the default
`agent_memory_` prefix where the adapter derives one.

| Physical DBI / keyspace | Owner and actual writer | Key/value and serialization | Scope / transaction / reverse lookup | Lifecycle, migration and tests | Status |
|---|---|---|---|---|---|
| `knowledge_units` | `MdbxKnowledgeUnitStore` | `UnitId` → `agent_memory.knowledge_unit.v1` envelope | Scope is in value and checked on scans; point reads in short transactions | Monotonic ids, content-key dedupe, reopen and rollback/guard tests | Implemented M0 |
| `content_key_to_unit_id` | `MdbxKnowledgeUnitStore` | Knowledge-unit content key → UnitId | Scope is part of content key; reverse is point lookup | Rebuildable only with explicit store logic; dedupe/reopen tests | Implemented M0 |
| `knowledge_units_by_kind` | `MdbxKnowledgeUnitStore` | Ordered scope/kind/index-sequence key → UnitId | Scope-prefixed range; no general reverse scan required | Schema marker v1/v2 and sequence rows; index recovery tests | Implemented M0 |
| `agent_memory_unit_projections` | `MdbxProjectionStore` | Composite `(scope, unit, kind, revision)` → `agent_memory.projection.v1` | Scope/unit prefix range; one local transaction per operation | Old revisions retained by design; projection reopen/corruption tests | Implemented profile |
| `agent_memory_resource_manifests` | `MdbxResourceManifestStorage` | ResourceId → resource manifest payload v1-v6 | ResourceId point lookup; optional caller-owned transaction overload | Active/erase-pending lifecycle, guarded upsert, reopen tests | Implemented profile |
| `agent_memory_documents`, `chunks`, `document_chunks` | `MdbxDocumentStorage` | DocumentId/ChunkId/DocumentId → document/chunk/list payloads | Legacy adapter-local keys; document upsert can span its own three tables | Own transaction and reopen tests; no KnowledgeUnit reverse binding | Legacy profile |
| `agent_memory_resource_index_document_owners`, `resource_index_chunk_owners` | `MdbxResourceIndexRecordOwnerStorage` | Physical record id → resource-owner payload | Point ownership checks; no general reverse resource scan | Cleanup/reclaim ownership tests | Legacy profile |
| `agent_memory_canonical_heads`, `canonical_revisions`, `canonical_bodies`, `canonical_ledgers` | `MdbxCanonicalContentStore` | Document head/revision/body/ledger keys → versioned C1 raw/plain payloads | Document/revision/body-generation keys; one writable transaction for edit | COW revisions, no-reuse ledger, corruption and reopen tests | Implemented C1 profile |
| `agent_memory_durable_lexical_catalog` | `MdbxDurableLexicalDocumentMemory` | Resource/catalog key → versioned lexical catalog payload | Scope/resource keys; catalog publication is atomic within the profile | Intent recovery and reopen tests | Implemented M0 profile |
| `agent_memory_resource_import_intents` | Importer and durable lexical profile | ResourceId → importer intent payload | ResourceId point lookup; fencing guard participates in write transaction | Lease/epoch recovery, takeover and fault tests | Implemented profile |
| `agent_memory_resource_import_bindings` | `MdbxResourceImporter` | ResourceId → durable scope binding | Global ResourceId policy; scope is value and checked | Binding collision and reopen tests | Implemented profile |
| `agent_memory_resource_import_epochs` | `MdbxResourceImporter` | ResourceId → monotonic fencing epoch | ResourceId point lookup | Recovery/takeover tests | Implemented profile |
| `agent_memory_resource_import_fencing_tokens` | Importer / canonical guarded writers | ResourceId → fencing token | Same context and transaction as guarded publication | Token migration/recovery tests | Implemented profile |
| `agent_memory_resource_import_fencing_schema` | Importer | schema marker → fencing schema version | Single marker; no scope | Explicit offline legacy migration; tests cover v3/v4 → v5 | Implemented profile |
| `agent_memory_lexical_posting_segments` | `MdbxPersistentLexicalIndex` and `MdbxSegmentedLexicalIndex` | Versioned flat-v2 value or `seg-v1/seg-v2` row keyspace → bounded lexical payload | Scope/projection encoded in state key or row key; one index transaction | Reopen, rollback, corruption, segmented fault and benchmark tests | Implemented experiment; manifest classification drift |
| Derived-publication table derived as `agent_memory_unit_projections` | `MdbxDerivedPublicationStore` | `derived_publication:<id>:b/m/a` → v1/v2 body/manifest/marker | Publication id point reads; B3 has caller-owned transaction overload | Atomic body/manifest/marker and reopen/fault tests | **STATIC: same DBI name confirmed; runtime ownership conflict POTENTIAL, not reproduced** |
| `resource_bodies`, `resource_body_manifest`, `resource_body_chunks` | Resource body profile | Resource/revision/digest and chunk-index keys → body payloads | Optional profile; range-read contract is documented, not a current default | No production BlobStore/CAS implementation | Reserved/documentation-only |
| `source_refs`, `source_refs_by_resource` | Provenance profile | UnitId or ResourceId reverse keys → full source refs | Optional full-source profile | No current general adapter | Reserved |
| `embedding_*`, graph, temporal, runtime, sync and payload DBIs | Capability/profile owners in `dbi-manifest.yaml` | Declared by manifest; many have no current writer | Profile selector controls opening; no implicit DBI creation | Migration peaks and budget are documented | Reserved/documentation-only |

### DBI budget and hot-path findings

`MdbxStorageContext` defaults to `kDefaultMdbxMaxDbs = 96`, matching
`dbi-manifest.yaml`. The manifest's expanded reference total is 70, leaving a
documented reserve. This is a capacity ceiling, not a promise that every
profile opens all 96 DBIs.

The actual code is mixed: newer adapters accept a shared
`MdbxStorageContext`, while older document/resource adapters still have
adapter-local construction paths. Passing the same filesystem path is not a
shared transaction context. The backend integration roadmap correctly treats
shared context and transaction provenance as an architectural requirement.

The confirmed shared name and experimental layout difference need separate
qualification before choosing any physical change:

- instantiate projection and derived-publication stores on one default context,
  write both payload families, inspect keys, and test reads and reopen;
  exercise each owner's available scan/delete/maintenance operations and schema
  version handling without damaging the other's rows. Inventory whether a
  public clear operation actually exists before promising a clear test.
  A safe shared namespace with a documented composite schema is acceptable.
  Only a reproduced incompatibility justifies a separate migration-safe DBI
  decision; no automatic rename or new DBI is authorized by this audit;
- select a named lexical profile and validate that its actual payload/keyspace
  (`flat-v2` versus `seg-v2`) matches the selected manifest entry, payload
  version and migration path.

Full scans appear in several rebuild and snapshot paths. They are acceptable
for bounded/rebuild operations, but must not be presented as the product query
path. Persistent flat-v2 snapshots reconstruct the dependency-free index from
one bounded KV value; seg-v2 reduces point/range work for the measured sparse
update workload but remains experimental and does not atomically include
canonical units or manifests.

## Ingestion, chunking and metadata audit

### What is implemented

- `MdbxResourceImporter` validates UTF-8, rejects truncated and invalid code
  points, and splits raw bodies only at code-point boundaries.
- Raw chunk ranges are byte offsets into the original UTF-8 body. The importer
  stores the exact chunk text and a bounded preview, and binds each raw unit to
  the resource generation and digest.
- The lexical-only profile and the raw-chunk/Original-projection profile are
  explicit publication profiles. A legacy manifest is not silently rewritten
  into a different topology.
- The older `ResourceIndexer` accepts pre-chunked `DocumentSnapshot` values,
  validates parent document identity and chunk ownership, and compensates
  derived records on in-process failure.

### Gaps and risks

- There is no durable `SegmentId`/`SegmentSet` contract. `ChunkId` and
  `KnowledgeUnitId` must not be treated as interchangeable identifiers.
- Raw chunking is byte-bounded and UTF-8-safe, but structural Markdown/code
  chunking, heading/title ownership and symbol extraction are not a single
  canonical ingestion profile yet.
- `Metadata` is a string map. `SearchProjection` has typed-ish fields such as
  title, heading, tags and symbols, but there is no product-wide declaration
  of which metadata keys are strict filters, which are soft routing signals,
  and which have an index.
- Update propagation is profile-specific. Importer recovery and manifest
  publication are tested, while general projection invalidation, persistent
  posting refresh and locator-backed source hydration remain separate work.
- `TextRange` is a byte range only. It is safe for original UTF-8 bytes after
  importer validation, but transformed/extracted representations need a
  representation identity before the same range can be called an original
  citation.

## Evidence and citation chain

The intended chain is:

```text
SourceRevision
  -> Representation
  -> Segment
  -> KnowledgeUnit
  -> SearchProjection
  -> typed Candidate
  -> EvidenceAnchor
  -> hydrated source bytes
```

The current state of each edge is:

| Edge | Current binding | Failure semantics | Status |
|---|---|---|---|
| SourceRevision → Representation | `ResourceRevision` carries generation, digest, origin and optional extractor provenance | Invalid digest/provenance is rejected; body retention is not guaranteed | Implemented narrow text value |
| Representation → Segment | Importer byte ranges and raw Chunk text; no general representation table | Missing raw body leaves unit/source unavailable; no SegmentId | Profile/partial |
| Segment → KnowledgeUnit | Raw importer binds unit source summary to resource revision and excerpt | Unit identity or source digest mismatch fails closed | Implemented M0 raw subset |
| KnowledgeUnit → SearchProjection | `SearchProjection` binds scope, unit id, unit revision, projection revision, generation, fingerprint and body | Stale/multiple/invalid projection is skipped or rejected | Implemented Original subset |
| SearchProjection → Candidate | Projection-aware lexical producer emits a producer-owned typed identity in the corrective KB-0 slice; legacy metadata remains compatibility-only | Missing/invalid identity must be unavailable, not inferred from ChunkId | Corrective slice, not yet merged in this audit snapshot |
| Candidate → EvidenceAnchor | `RevisionBoundRetrievalResult` carries `SourceRefSummary`, root generation and digest | Stale/unavailable status is explicit; no full byte anchor | Contract/experimental adapter |
| EvidenceAnchor → hydrated source | No general byte-addressable `IBlobStore`/source catalog is wired to product retrieval | Retention/access failures are not yet a complete public status matrix | Missing KB-0 gate |

The important distinction is:

- **source identity:** resource id, generation and digest;
- **preview citation:** bounded `SourceRefSummary.preview` with a hash of that
  preview;
- **byte-verified quote:** requires a reader of the exact immutable body and a
  digest/range check;
- **historical citation:** explicitly names an older generation and must not
  be replaced with the current source;
- **stale:** the requested derived or source binding no longer matches the
  trusted frontier;
- **retention-unavailable:** identity remains known but bytes are gone;
- **access denied:** policy denies materialization and must not leak sensitive
  source details.

`quote_hash(preview)` is only evidence about the stored preview. It is not
proof that the original source bytes at `TextRange` still match.

## BlobStore / CAS and compression audit

No BlobStore or Zstd implementation is added here. The future profile must
answer all of these questions before a non-text connector or compressed
source claim becomes production:

| Contract | Required decision |
|---|---|
| Immutable identity | SHA-256 (or a declared algorithm-tagged digest) is the blob identity; URI/filename is metadata, not identity |
| Ownership | Artifact owns media identity; Representation owns derived MIME/role/encoder metadata; Segment owns a typed locator |
| Publication | `prepare` writes bytes and checksum; `finalize` publishes the MDBX catalog binding only after durable bytes exist |
| Crash recovery | MDBX intent, filesystem/CAS object and final catalog state need an explicit recovery matrix; no dangling published pointer |
| GC and reachability | Reachability from retained revisions, projections and audit records wins over refcount-only deletion; erasure must be policy-aware |
| Range reads | Chunk/frame/range maps must be checked against immutable digest and declared encoding generation |
| Compression | C1 raw/plain is the current implemented canonical profile; C2 framed compression needs frame index, random-read and rewrite tests |
| Corruption | Checksum mismatch, truncated frame and unknown codec version are distinct fail-closed outcomes |
| Backup/restore | Catalog, blobs, codec descriptors, key material references and profile signature must restore as one compatible set |
| Retention/erasure | Historical citation becomes `Unavailable` after policy deletion; no silent substitution with a newer revision |
| Encryption | Optional envelope/key identity belongs to the artifact/blob profile and must not alter logical source identity |

Current `ResourceBodyDigest` and C1 canonical body records are compatible with
this direction, but do not implement the catalog, CAS, GC, range map or crash
closure by themselves.

## Multimodal extensibility matrix

The common invariants are identity, provenance, revision, representation,
locator and hydration status. The project deliberately keeps modality-specific
DTOs out of the current public API.

| Source family | Candidate representation | Original locator | Current state | Required later gate |
|---|---|---|---|---|
| Markdown / code | normalized text, headings, code/symbol projections | byte range and structural block | Raw UTF-8 and text projections exist; structural SegmentSet is absent | stable block/segment identity and reindex fixture |
| PDF | extracted text or page/region representation | page number plus region or byte range in retained representation | Roadmap only | extractor provenance, page-region anchor and retention test |
| TSV / CSV | canonical table representation, row/column/cell projections | table/row/cell locator | Roadmap only | dialect-aware parser, stable row/cell identity, typed filters |
| Image | original blob plus visual/OCR representations | image region or blob range | Roadmap/contract only | artifact catalog, region anchor, access and corruption tests |
| Audio / ASR | audio representation, transcript intervals, semantic audio projection | time range; transcript is derived | Roadmap/contract only | interval evidence and original-audio hydration |
| Video | global/scene/frame/ASR representations | time range, frame or scene region | Roadmap/contract only | segment policy, frame/clip anchor and sequence verification |

An ASR/OCR/caption result is never the original media citation. A perceptual
hash identifies a near-copy, not semantic evidence. Binary indexes may share
candidate machinery only under an exact representation/space descriptor.

## Release gates and risk table

Each finding has an explicit gate, evidence level and bounded acceptance test.
`KB0_RELEASE_GATE` identifies required product-path qualification;
`PRE_RELEASE_ARCHITECTURE_RISK` requires a scoped decision or qualification but
is not itself a demonstrated release-blocking defect; `DEFERRED_PROFILE` is
outside the selected text-first path. These labels do not override
`milestones.md` or promote a partial KB-0 preview to completed M0.

| Finding and source evidence | Evidence / reproduction status | Severity / status / gate | KB-0 impact | Minimal fix and migration risk | Acceptance test / estimated scope |
|---|---|---|---|---|---|
| Generic revision-bound retrieval uses independent unit/projection/manifest calls | STATIC: no shared transaction; TEST SOURCE: `revision_bound_retrieval_test.cpp` uses in-memory stores. Durable torn-read reproducer not run | **P1 / CONFIRMED gap / KB0_RELEASE_GATE** | `Current` cannot be advertised as snapshot-consistent | MDBX-specific caller-owned read transaction composition; no domain transaction interface or schema change | Bounded update/read race, stale rejection, close/reopen and exact-snapshot fixture; one focused PR |
| Projection and derived-publication `table_name()` both return `<prefix>_unit_projections` | STATIC: shared name and distinct key families confirmed. Mixed-store corruption/maintenance failure NOT reproduced | **P1 / POTENTIAL conflict / PRE_RELEASE_ARCHITECTURE_RISK** | Joint payload, backup and migration ownership needs qualification | Test co-ownership first; retain a documented composite schema if safe. Split only with separately reviewed migration plan; high migration risk | Mixed-store writes/reads/reopen, per-owner scan/delete/available maintenance, schema/version negatives; separate storage PR only if needed |
| `lexical_posting_segments` manifest key description differs from flat-v2/seg-v2 layouts; manifest notes already acknowledge this | STATIC: payload/keyspace difference confirmed. EXECUTED: manifest validator checks declarations, not adapter/runtime agreement | **P2 / CONFIRMED profile risk / DEFERRED_PROFILE** | Experimental schema tooling can misclassify data; does not block bounded text-first KB-0 | Named profile/schema validation before activation; no mandatory DBI rename. Medium risk if a future migration is chosen | Selected adapter/payload/keyspace conformance in a later profile PR |
| `SourceRefSummary.quote_hash` hashes preview; product retrieval has no exact source-byte reader | STATIC: preview hash implementation and summary-only adapter inspected. End-to-end body-loss fixture not run | **P1 / CONFIRMED gap / KB0_RELEASE_GATE** | Citation materialization is summary-only | Minimal retained-text reader with exact revision/digest/range validation; no general BlobStore required | Byte-verified text, missing retained body, no historical substitution, import/update/reopen; one vertical slice |
| Minimal retrieval trace required by `milestones.md` is not wired into this product route | STATIC: milestone/API inspection; no product trace conformance demonstrated | **P1 / CONFIRMED gap / KB0_RELEASE_GATE** | Candidate/rejection accounting and reproducibility incomplete | Add minimal query identity, scope, counts, limits and snapshot/frontier identity; no sensitive source content or runtime queue | Deterministic trace and no-trace compatibility fixture |
| Envelope contains bounded summaries; full `source_refs`/reverse lookup is reserved | STATIC and TEST SOURCE: bounded envelope validation in `KnowledgeUnit.cpp` and unit tests | **P2 / DEFERRED / DEFERRED_PROFILE** | Long/general provenance unavailable; bounded M0 summaries are intentional | Implement full-source profile at its normative milestone; do not turn it into an M0 prerequisite | Future reverse lookup, reopen, retention and bounds tests |
| Generic `ResourceRevision` has optional digest and prototype-local hashes | STATIC: value contract inspected; generic optional digest is intentional, not corruption | **P2 / POTENTIAL misuse / PRE_RELEASE_ARCHITECTURE_RISK** | Integrity claims must use a digest, not 64-bit hints | Require digest at the selected byte-verified admission boundary; no blanket change to legacy manifests | Selected-path missing-digest rejection and exact historical revision reads |
| No general SegmentId/structural table locators | STATIC: roadmap/type inventory; no structured profile qualification | **P2 / DEFERRED / DEFERRED_PROFILE** | Raw byte chunks remain supported scope | Later S0 profile; do not overload ChunkId/TextRange | Future UTF-8/row-cell/section fixtures |
| BlobStore/CAS, C2 compression and GC are roadmap-only | STATIC: no general implementation wired | **P2 / DEFERRED / DEFERRED_PROFILE** | Non-text/general large-source retention not claimed | Separate artifact/codec work, not required for retained raw-text composition | Future crash/GC/range/checksum/backup/erasure matrix |
| Legacy path constructors can own private connections | STATIC: constructors inspected; equal path is not shared-context proof | **P2 / POTENTIAL composition misuse / PRE_RELEASE_ARCHITECTURE_RISK** | Cross-store atomicity is not implied by sharing a filename | Select shared-context adapters for the product route; no global legacy rewrite | Context identity, foreign-transaction rejection and shutdown tests for the selected route |

Only rows explicitly labelled `KB0_RELEASE_GATE` above identify missing
product qualification. Potential risks require investigation, not an assumed
runtime defect; deferred experimental profiles do not gate text-first KB-0.
This table is not exhaustive M0 acceptance: BM25F/trace and every other required
row in `milestones.md` remain normative even if an earlier preview excludes a
capability explicitly. Green component CI alone does not close those gates.

## Minimal next implementation slice

After independent review of this audit, the smallest implementation PR should
be **MDBX snapshot-consistent revision-bound retrieval composition**:

1. one read transaction for the candidate, KnowledgeUnit, Original projection,
   manifest and source-body lookup;
2. typed candidate identity and exact source revision/digest checks;
3. explicit freshness, materialization and query-completeness outcomes;
4. scope isolation, stale rejection, close/reopen and interrupted-publication
   conformance;
5. no fallback from a requested historical revision to the current source;
6. no universal coordinator, graph backend, ANN route or new DBI unless the
   storage audit identifies a concrete profile-owned need.

This proposal does not activate production retrieval. Compact scorer adapters,
structured TSV/CSV ingestion, BlobStore/CAS and multimodal routes remain
separate PRs.

## KB-0 release boundary

### Required for the text-first Technical Preview

- UTF-8 raw resource import with stable ResourceId and canonical revisions;
- durable KnowledgeUnit identity, lifecycle and revision-bound source summary;
- active Original projection with exact manifest binding;
- bounded lexical retrieval with typed candidates and explicit diagnostics;
- one snapshot-consistent MDBX retrieval/hydration composition;
- update/reindex/reopen/recovery and scope isolation;
- external CMake consumer and reproducible retrieval trace.

### Experimental but not production-default

- flat-v2 and seg-v2 persistent lexical experiments;
- fenced derived publication and current-read revalidation slices;
- benchmark receipts and codec/index comparisons.

### Explicitly deferred

ANN/THQ production activation, graph expansion, PDF/OCR/ASR pipelines,
image/audio/video ingestion, full table query, BlobStore/CAS, Zstd/C2 physical
codec migration, automatic consolidation and universal coordinator/runtime.

The normative milestone remains `guides/milestones.md`; this document only
records what is implemented, what is experimentally profiled, and which
acceptance gates remain before calling the product path complete.
