# Canonical Content, Storage And Editing Roadmap

**Status:** Normative design contract; implementation is not complete.

This guide defines the durable content substrate used by three different domain
forms:

- `Document` and `DocumentRevision`;
- `Conversation` / `SessionEventStream`;
- `AgentMemory` represented by `KnowledgeUnit` records and components.

Those domains must not be collapsed into one universal binary record. They share
artifact, provenance, temporal, relation, materialization and projection
contracts, while their canonical content and edit semantics remain different.

This guide owns the logical content, block/entry identity, read/materialize/edit
and physical-body-encoding contracts. It extends, but does not replace:

- [`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md) for source,
  revision, artifact, representation and evidence-anchor provenance;
- [`memory-stacks-roadmap.md`](memory-stacks-roadmap.md) for envelopes,
  components, profiles, scope and memory lifecycle;
- [`resource-reindexing.md`](resource-reindexing.md) for resource generations
  and targeted replacement;
- [`chunkers-roadmap.md`](chunkers-roadmap.md) for chunker strategies;
- [`optimization-roadmap.md`](optimization-roadmap.md) for codec and vector
  experiments;
- [`mdbx-containers-extension-tz.md`](mdbx-containers-extension-tz.md) for the
  canonical physical MDBX manifest.

`milestones.md` remains the conflict resolver for implementation scope. This
document describes contracts and planned work; it does not claim that a
contract-only feature already exists in C++.

## 1. Problem And Decisions

An agent-memory corpus is usually consumed by software rather than opened by a
person as a directory of `.json`, `.txt` and `.md` files. The storage layer may
therefore keep normalized text and structured records in compact independently
readable bodies. Human-readable Markdown, JSONL and media folders remain export
and inspection views.

There is no assumed universal compressed-`.md` standard. Markdown is one
materialization format; the durable contract is the domain structure plus
content-addressed bodies and projections. A portable package or archive may
bundle those pieces for transfer, but it must not become the semantic source of
truth or force documents, conversations and memories into one record schema.

The following decisions are normative:

1. A raw source and the durable normalized content derived from it are separate
   objects. The raw source may be retained, externally referenced, or discarded
   after verified canonicalization according to policy.
2. A document's canonical normalized body is not a set of permanently copied
   retrieval chunks. It is a versioned body or block tree from which one or more
   `SegmentSet` versions can be derived.
3. A conversation/session is a typed event stream or tree. Messages, tool calls,
   file revisions, patches, commands, model changes and compaction entries are
   distinct entry kinds.
4. Agent memory is a semantic record with components such as temporal,
   perspective, epistemic, usage and provenance data. It is not forced into the
   document or chat body schema.
5. Retrieval projections, including BM25, dense vectors, binary codes, ANN
   layouts, OCR and captions, are derived and stored separately from canonical
   content.
6. Additional context used to compute an embedding is a versioned
   `ContextProfile`/recipe, not a mutation of the canonical chunk or message.
7. Compression, frame packing, dictionaries and physical backend generations
   are storage concerns. Re-encoding byte-identical decoded content must not
   invalidate semantic IDs or embeddings.
8. Read, materialize and edit are storage requirements alongside ingest and
   retrieve. A caller must be able to inspect or export canonical content
   without running the retrieval pipeline.

The resulting dependency direction is:

```text
Canonical content revision
        |
        +--> domain structure (document tree, event stream, memory components)
        |
        +--> SegmentSet / episode set / derived memory view
                    |
                    +--> lexical, dense, binary and other projections

Canonical decoded bytes
        |
        +--> physical encoding generation
              raw | Zstd | Zstd + dictionary | packed body
```

## 2. Three Domain Forms On One Shared Substrate

| Domain | Canonical form | Normal edit operation | Typical derived views |
|---|---|---|---|
| Document | revision, metadata, ordered content blocks/tree, asset occurrences | edit against revision, create a new revision | section chunks, tables, figures, summaries |
| Conversation/session | typed entries with `entry_id`, parent/reply links and timestamps | append entry, edit/supersede entry, branch or import event | episodes, thread windows, summaries, speaker views |
| Agent memory | `KnowledgeUnit` plus components and lineage | supersede, append observation, consolidate under policy | facts, episodes, summaries, relations, retrieval tiers |

The shared substrate is:

```text
Artifact / ArtifactOccurrence
SourceRef / EvidenceAnchor
Temporal data
Relation and derivation lineage
ContextProfile
SearchProjection
BlobStore / ResourceBodyStore
```

A small JPEG, CSV or voice message is therefore an independent content-addressed
`Artifact`, even when it is physically stored in the same MDBX environment as
the message or document. A document or message stores an occurrence/binding and
an `ArtifactId`, not an embedded copy tied to its domain schema. Reverse lookup
from `ArtifactId` to all occurrences is required.

Byte identity is exact (`BlobDigest`/`ArtifactId`). Equivalence of separately
encoded media is a separate derived fact such as a perceptual hash, Chromaprint
fingerprint or `derived_from` relation; it must never replace byte identity.

### 2.1 Document

A document revision contains a canonical normalized representation, not
necessarily the original PDF/HTML byte stream:

```text
Document
  -> DocumentRevision
       -> metadata
       -> ordered ContentBlock tree
       -> canonical body/blocks
       -> figure/table/asset occurrences
       -> source and derivation references
```

A scientific article may retain the original PDF, or retain only normalized
text, structure, tables and figures after a verified extraction. If the original
is removed, citations must identify the canonical representation and must not
pretend that an original page/byte range is still materializable.

Tables are structural content, not Markdown decoration. A `TableBlock` keeps a
canonical column/row/cell representation, including spans or typed values when
the source provides them, while Markdown, HTML, CSV or JSON are materialized
views. Captions, headers and selected cell text may feed lexical or embedding
projections, but a projection never replaces the structured table needed for
faithful export.

### 2.2 Conversation / Session

A conversation is not a concatenated document body by default:

```text
Conversation / SessionEventStream
  -> Message
  -> MessageEdit
  -> ToolCall / ToolResult
  -> Command / FilePatch
  -> ModelChange
  -> Compaction / Summary
  -> branch/reply relationships
```

A Telegram conversation, a two-party agent dialogue and an AI coding session are
profiles of this event model. An individual message is normally a canonical
small record in the domain store. Retrieval episodes or thread windows are
versioned derived segment sets.

For coding sessions, file content must carry input/output revision identities
and patch provenance. Short hash anchors may help a UI or tool protocol detect a
stale edit, but the durable record keeps the full file/revision digest.

### 2.3 Agent memory

Memory records use the existing `KnowledgeUnit` plus component model. The
following axes must remain separate:

| Axis | Meaning |
|---|---|
| `ScopeId` | namespace or ownership boundary |
| perspective/observer | whose view or memory this is |
| represented subject | who or what the memory is about |
| runtime origin/producer | which runtime or node produced the record |
| speaker/actor | who said or did the observed thing |
| `SourceRef` / evidence | where the information came from |
| temporal | when it happened, was observed and became known |
| retrieval lifecycle | importance, usage, decay, supersedence |
| physical lifecycle | hot/warm/cold/evicted/erased placement |

`ScopeId` must not be used as a synonym for perspective or provenance source.
This follows [`agent-runtime-integration-roadmap.md`](agent-runtime-integration-roadmap.md)
ADR-020 and keeps multi-agent/ADELIA records valid when several observers
represent the same character or when no character is present.

Short/medium/long/base are retrieval views or policies. They are not different
semantic storage formats. Decay normally changes recall priority; it does not
destroy the original observation. Consolidation creates a new derived memory
with lineage back to the observations.

## 3. Four Identity Layers

Every implementation must distinguish these identities:

1. **Content identity** — what decoded canonical content means; for a body this
   includes the canonical decoded content digest and revision.
2. **Structure identity** — how the content is represented as blocks, entries,
   trees, tables or other domain structure.
3. **Projection identity** — a derived retrieval representation: tokenizer and
   BM25 schema, embedding model/recipe, binary code, ANN build or visual/audio
   representation.
4. **Physical encoding identity** — codec, level, frame packing, compression
   dictionary, encryption, body backend and physical storage generation.

Changes propagate only along declared dependencies:

| Change | Must invalidate | Must remain valid |
|---|---|---|
| canonical content edit | affected structure and dependent projections | unrelated blocks and corpus records |
| chunker/segmenter change | new `SegmentSet` and its projections | canonical content and source revision |
| embedding model/recipe change | that projection generation and dependent ANN/code | canonical content, blocks and other projections |
| BM25/tokenizer change | lexical projection/index | dense projections and canonical content |
| Zstd level/dictionary/frame repack | physical body generation only | content, blocks, segments and embeddings if decoded bytes match |
| MDBX/file-pack migration | physical generation only | all logical IDs and projections |
| source-original retention change | materialization/reprocessing capability | canonical normalized body and semantic IDs |

A physical encoding descriptor must never be included in the digest or identity
of a semantic document, conversation entry, memory or segment when decoded
canonical content is byte-identical.

## 4. Canonical Content And Stable Blocks

`CanonicalContentRevision` is a durable, normalized representation selected by a
profile as the input for future materialization, segmentation and re-embedding.
It may be implemented by an artifact-provenance `Representation` with a
`canonical_normalized` role plus a domain structure record.

A conceptual shape is:

```cpp
struct CanonicalContentRevision {
    CanonicalContentRevisionId id;
    DomainKind domain;
    std::optional<SourceRevisionId> source_revision_id;
    std::string normalizer_id;
    std::string normalizer_version;
    BlobDigest parameters_digest;
    BlobDigest decoded_content_digest;
    StructureId structure_id;
    ReprocessingFrontier frontier;
    RetentionPolicy retention;
};

struct ContentBlock {
    ContentBlockId id;
    std::uint64_t revision = 0;
    ContentBlockKind kind;
    std::optional<ContentBlockId> parent_id;
    PositionId position;
    BlobDigest content_digest;
    ContentPayloadRef payload;
    std::vector<Locator> source_locators;
};
```

The exact public C++ types are implementation work; the invariant is not.
`ContentBlockId` is stable while a block is reused across revisions. Editing a
block creates a new block revision or replacement block and records the
supersede/change relation. Inserting a sibling does not renumber all unrelated
blocks.

### 4.1 Stable IDs And Markdown Round-Trip

The structured canonical representation is the authority for stable block IDs.
The default Markdown export is a presentation view and does not expose those
IDs as ordinary prose. A controlled editor may use either a sidecar manifest or
reserved hidden markers, but both are transport conventions rather than part of
the user's visible text.

Importing Markdown edited outside a controlled editor follows this order:

1. use the sidecar/marker binding when it is present and valid;
2. otherwise match unchanged blocks by canonical digest plus unique structural
   context (parent, heading path and neighboring order);
3. if matching is ambiguous, create a new revision with an explicit import
   warning and do not silently reuse IDs.

Deterministic matching is therefore a safe reuse optimization, not a proof of
identity. A caller that needs lossless block-level editing must retain the
structured manifest. A plain Markdown file remains a supported human-readable
source, but an external edit may legitimately produce a new block identity and
broader re-chunking frontier.

For a mostly textual document, blocks may be materialized into a normalized
UTF-8 body stream. Segments reference block IDs and local ranges, or body frame
ranges when the body is immutable. Absolute byte offsets may be cached for a
specific body generation but must not be the only durable identity for an
editable block tree.

For a conversation, entries are the canonical blocks and may remain separate
small records. For memory, atomic observations/facts/events are canonical
semantic units; summaries and reflections are derived units with provenance.

## 5. Segments, Context And Projections

Chunking is a versioned derivation from canonical content:

```text
CanonicalContentRevision
   -> SegmentSet(versioned chunker + parameters)
       -> KnowledgeUnit materialization
           -> BM25 / dense / binary / other projections
```

The system must retain enough canonical content to create another `SegmentSet`
without returning to a discarded original source. A chunk is therefore a
locator-backed retrieval view, not the final source of truth. Overlapping chunks
may share the same body bytes or block ranges.

Embedding augmentation is represented explicitly:

```text
DocumentContextProfile
SectionContextProfile
Chunk / Message / Episode content
EmbeddingRecipe
        -> embedding input digest
        -> embedding projection
```

A `ContextProfile` may be scoped to a document, section, episode or other stable
parent. It must record its own revision, producer and dependencies. The
projection stores the selected profile IDs, recipe identity and digest of the
assembled input. It must be possible to re-materialize the exact input without
mutating the canonical content.

Context invalidation is policy-controlled. A local paragraph edit should not
implicitly rewrite a document summary used by every segment. Section context
normally has a section-sized blast radius; document context is updated only on
explicit consolidation or a declared significant-change threshold. The
invalidation graph must expose the affected scope rather than silently marking
the whole corpus stale.

Vectors, lexical postings, binary signatures, graph indexes, OCR and captions
are not stored inside the canonical document/chat body as authoritative data.
They may be colocated physically through a backend, but remain separate
projection records with their own model/configuration/generation and rebuild
rules.

This also covers non-speech audio and music. A transcript is optional derived
evidence; audio embeddings, acoustic fingerprints and music-similarity vectors
are separate typed projections with their own model, sample-rate/channel
normalization and distance contract. A search hit can point to the audio
artifact and a timestamp range without pretending that the audio has a text
equivalent.

## 6. Physical Body Encoding

The existing `IArtifactBodyStore`/`ResourceBodyStore` boundary is the body API.
It should support immutable writes, digest verification, range reads when
available, frame metadata and materialization. Placement is a policy function of
size, access pattern, mutability, media type and retention:

```text
small/hot body       -> MDBX value or MDBX-backed body table
medium/cold bodies   -> measured inline or packed body store
large media          -> content-addressed filesystem/object store
export/snapshot      -> packed binary or portable package
```

A small JPEG, CSV or Markdown body may therefore live in MDBX, but it remains an
independent artifact/body record. Large PDF, audio and video bodies should use a
file-CAS or pack backend. The caller sees one body-store contract.

### 6.1 Independent frames

A canonical text or structured body may be stored as independently compressed
frames. A frame descriptor contains at least:

```text
body_id
frame_id
logical block/range coverage
physical offset and length
uncompressed length
decoded content digest
codec and codec version
codec level
optional dictionary reference
checksum and decoded-size limit
```

Logical blocks and physical frames are different layers. One frame may contain
several adjacent blocks, and editing one block may rewrite only its containing
frame while later frames remain reusable. A reader resolves a segment to its
frame(s), reads only those frames, verifies the decoded digest, and materializes
the requested range. Whole-document materialization scans frames in logical
order.

For vector and other projection payloads, a rebuildable route-local slot map or
permutation is also a physical layout optimization. It may group records by a
router or cell to reduce touched segments, but it must retain stable logical
IDs, the routing-model digest and the layout-generation identity. It is
independent of Zstd compression economics: locality, cache/page behavior and
compression ratio require separate measurements. A route-local layout is never
the canonical body or the only durable identity of a projection.

One giant compressed document body is forbidden for a random-readable profile.
One frame per tiny message is also not a default: frame overhead and loss of
shared locality must be measured. Initial experiments should compare bounded
frame sizes such as 16, 64 and 256 KiB, plus section-local packing.

### 6.2 Compression dictionaries

A compression dictionary is a `StorageCodecArtifact`, not a semantic Document,
Conversation or Memory record. It has its own digest, codec/dictionary format,
trainer/version, training corpus selector, sample manifest and evaluation
receipt. A body frame references the durable dictionary identity and digest.
Missing or incompatible dictionaries fail closed.

Ingestion must work with plain Zstd frames. Dictionary training is a later
compaction/storage optimization:

```text
sample existing canonical bodies
    -> train dictionary
    -> evaluate on held-out data
    -> recompress into a new physical generation if useful
    -> verify decoded digests
    -> atomically publish the generation
```

A database may contain frames using no dictionary and several dictionary
versions at once. Recompression must not invalidate content, structure,
segments, BM25 or embeddings when decoded canonical bytes are unchanged. It is
safe to reclaim the old generation only after readers and liveness rules permit.

Text-body dictionary experiments are independent from packed learned vector
codecs. A text dictionary must not be silently applied to binary vector payloads;
vector quantization and search representation remain their own projection and
benchmark contract.

## 7. Mutable Updates And Read/Materialize/Edit

The storage contract must expose both ingestion and inspection:

```cpp
read_document(id, revision = current)
read_block(block_id, revision = current)
read_segment(segment_id)
read_section(section_id)
materialize_document(id, format, options)

begin_edit(id, expected_revision)
commit_edit(edit, expected_revision)
```

The materializer must support at least a structured tree and a human-readable
projection such as Markdown or JSONL. It must not require BM25, embeddings or an
external model. Asset occurrences are emitted as stable artifact references;
callers may explicitly request local paths, embedded bytes or media previews.

An edit is optimistic and versioned:

```text
read revision N
  -> apply block/entry operations
  -> validate source/structure invariants
  -> commit revision N+1
  -> publish ContentChangeSet
  -> update only dependent projections
```

A stale `expected_revision` produces a conflict rather than a lost update.
`ContentChangeSet` records changed, inserted and removed blocks/entries, changed
metadata, structure ranges and invalidation hints. A chunker may return a local
invalidated range and reused segments. If it cannot prove local stability, it
may rechunk the current revision, but it must not rebuild unrelated resources.

Physical writes use append/COW semantics with tombstones and later compaction.
The physical-generation granularity is chosen by the backend: it may be a
single frame, body, pack or a larger batch. Mixed generations are valid while a
migration is in progress. Publication must atomically switch the active pointer
at the chosen scope; a global whole-store generation is an optimization, not a
semantic requirement.
MDBX is the first live mutable profile; a packed single-file representation is a
snapshot/export profile and must provide an equivalent manifest, frame index,
atomic generation publication and crash recovery before it is used as a primary
mutable store.

## 8. Retention And Reprocessing Frontier

Original source retention is policy, not a universal invariant. A profile may
select:

```text
KeepOriginal
KeepUntilCanonicalized
KeepExternalReference
DiscardAfterVerifiedCanonicalization
```

The canonical normalized body receives durable provenance: source revision,
normalizer/parser identity, parameters, coverage, issues and decoded digest.
When an original is discarded, the system can still support:

- re-chunking;
- re-embedding;
- rebuilding lexical and vector indexes;
- materializing the normalized document or conversation view.

It cannot promise re-parsing with a different extractor or exact original-page
visual fidelity. Evidence and UI must expose this distinction.

A `ReprocessingFrontier` makes the capability explicit:

| Frontier | Available operations |
|---|---|
| Original | full reparse, canonicalization, rechunking, re-embedding |
| Normalized | materialization, rechunking, re-embedding, index rebuild |
| Segments | re-embedding and projection rebuild only |
| Projections | retrieval only; no reliable source reconstruction |

`SourceRef` and `EvidenceAnchor` retain the strongest available source and
representation locator. A canonical-text citation must not be rendered as an
original PDF/page citation after the original body is removed.

## 9. Implementation Order

### Phase 0 — documentation and contract alignment (current)

- make this guide the owner of canonical content, stable blocks,
  read/materialize/edit, logical-vs-physical identity and body generations;
- update conflicting raw-resource, artifact-retention, compression and
  conversation guidance;
- keep all new capabilities marked roadmap/contract-only.

### Phase 1 — text vertical slice

- canonical normalized UTF-8 document body and stable block tree;
- MDBX-backed body store with plain/None and independently framed Zstd codecs;
- document read, section/segment read, structured/Markdown materialization;
- optimistic edit, `ContentChangeSet`, tombstones and targeted projection
  invalidation;
- a minimal inspect/materialize CLI or viewer-facing API for structured and
  Markdown/JSONL export;
- no PDF parser, media runtime or production dictionary trainer in core.

### Phase 2 — conversation/session vertical slice

- typed event entries, replies/branches, tool events, file revisions and
  patches;
- JSONL/structured materialization and append/edit/supersede operations;
- episode segment sets and contextual embedding recipes.

### Phase 3 — agent-memory integration

- map `KnowledgeUnit` components to perspective, epistemic, temporal,
  runtime-origin and evidence axes;
- preserve decay/retrieval tiers as policy views;
- add consolidation lineage and targeted invalidation.

### Phase 4 — artifact and multimodal profiles

- PDF/document/image/audio/video adapters remain optional;
- normalized representations, figure/table/audio/video segments and artifact
  occurrences use the existing provenance roadmap;
- original retention follows policy; typed locators and derived labels remain
  mandatory.

### Phase 5 — storage optimization

- benchmark inline MDBX vs file-CAS vs pack store;
- benchmark frame sizes and random materialization;
- train/evaluate dictionaries post-ingest and publish physical generations;
- run the declared corpus matrix (scientific papers, chat logs, technical docs,
  source/log data) across plain Zstd versus trained dictionaries and 16/64/256
  KiB frame targets, measuring ratio, random materialization, one-block edit
  rewrite cost, recompression throughput and dictionary memory;
- keep route-local vector placement experiments separate from this text-body
  compression matrix; a layout that reduces fetched payload blocks is not
  evidence that the payload compresses better;
- never generalize text dictionary results to learned vector payloads without a
  separate experiment.

### 9.1 Research Gate A — Editable Canonical Content Contract

The docs-only gate is complete only when the implementation team can answer the
following without choosing a backend-specific binary layout:

- which domain forms are supported (`DocumentRevision`, session/event stream
  and `KnowledgeUnit`-based memory) and which structure is canonical for each;
- which block/entry IDs are stable, how supersede/change relations are recorded
  and what a plain Markdown import is allowed to reuse;
- how structured export, controlled-editor round-trip and uncontrolled Markdown
  edits differ, including the ambiguity rule that creates a new revision;
- how `ContentChangeSet`, context profiles and projection dependencies describe
  an edit's invalidation frontier;
- which bytes are canonical decoded content and which records are derived
  segments, indexes, media projections or physical encodings;
- how random reads, full materialization, optimistic edits, crash-safe
  publication and retention frontiers are exposed by the domain contract.

The gate artifact is the reviewed contract and its decision table, not a
production editor, archive format or storage implementation. Any unresolved
choice that changes semantic identity must be recorded as an explicit ADR
before Phase 1 code starts.

### 9.2 Research Gate B — Incremental Edit/Reindex Screen

Before production incremental editing, a deterministic harness must compare an
incremental path with a full rebuild oracle for at least: one-block modify,
insert, delete, move, heading change, metadata-only edit, an edit near a chunk
boundary and a multi-block edit. It records touched/reused/new blocks and
segments, lexical/vector/codec records, canonical-body bytes rewritten and
projection parity. Wall time is secondary.

The oracle comparison must use canonical digests and stable derivation keys; it
must not require allocator-specific row IDs to be byte-identical. If a chunker
cannot prove bounded local invalidation, the harness must force a whole-document
rechunk/reindex rather than accept potentially stale projections. Section,
document and metadata context profiles are measured separately so their blast
radius is visible.

The research artifact consists of source-bound inputs, a machine-readable
receipt, an independent fail-closed auditor and mutation self-tests. It does
not introduce a production editor API.

### 9.3 Research Gate Z0 — Canonical Text Compression

Z0 runs outside MDBX on normalized canonical text/body only. It uses a frozen
train/held-out split and compares an uncompressed reference, plain Zstd and
trained-dictionary Zstd at 16/64/256 KiB frame targets and predeclared levels
(at least 1 and 3). Global and per-corpus dictionaries are separate treatments;
the dictionary size sweep and promotion gate are fixed before measuring.

Every corpus must be source-bound or explicitly marked as a fixture. The receipt
records source/split hashes, raw and compressed bytes, dictionary size/training
cost, encode/decode throughput, random and whole-body reads, edit-one-block
rewrite cost, recompression temporary space, decoded SHA-256 parity,
read-amplification and prepared-dictionary memory. An independent auditor must
recompute the metrics. No Z0 result authorizes production dictionary code by
itself; only arms passing the declared gate can enter an MDBX Z1 screen.

### 9.4 Shared Research Artifact Contract

Gates B, Z0 and physical-layout experiments use one portable corpus manifest and
receipt shape. At minimum it names the corpus/source or fixture status,
license/retention note, source and normalized-content digests, split, normalizer
and parameter versions, runner commit, environment summary and auditor version.
Results must be relocatable: receipts refer to logical manifest IDs and
relative artifact names, never absolute workspace paths. Auditors recompute
source binding, exact permutation/order claims, decoded-digest parity and
mutation checks before accepting derived metrics. A report that cannot be
replayed from its manifest is evidence for investigation, not a promotion
decision.

## 10. Acceptance Gates

A text canonical-content profile is not complete until it demonstrates:

- decode round-trip with canonical content digest verification;
- random segment/section reads without decoding the whole body;
- full normalized-document materialization;
- stable block identities across insert, delete and local edit cases;
- stale edit conflict and crash-safe generation publication;
- `ContentChangeSet` with targeted projection invalidation;
- unchanged semantic IDs and projections after byte-identical recompression;
- missing, corrupt and incompatible dictionary failure tests;
- export/import round-trip through the structured and human-readable views;
- no dangling asset occurrence or SourceRef after updates;
- bounded write transactions and observable compaction/reclaim behavior.

The first implementation must be reported as `Roadmap only` or `Contract only`
until these gates are backed by code and tests. A documentation decision is not
an implementation result.

## 11. Research And Reference Anchors

These references inform the design but are not runtime dependencies or
acceptance evidence by themselves:

- [Zstandard compression format](https://github.com/facebook/zstd/blob/dev/doc/zstd_compression_format.md)
  defines independent frames, checksums and dictionary identifiers; the
  [seekable format](https://github.com/facebook/zstd/blob/dev/contrib/seekable_format/zstd_seekable_compression_format.md)
  is a reference for frame tables and range reads.
- [libmdbx introduction](https://libmdbx.dqdkfa.ru/doxygen/intro.html) and
  [transaction API](https://libmdbx.dqdkfa.ru/doxygen/group__c__transactions.html)
  support the MDBX choice for transactional catalog records, while its single
  writer and long-reader behavior remain reasons to bound edits and compaction.
- [Docling serialization](https://docling-project.github.io/docling/concepts/serialization/)
  and [supported formats](https://docling-project.github.io/docling/usage/supported_formats/)
  demonstrate a rich document representation with separate serializers for
  text, tables and pictures; the project remains an optional parser adapter.
- The [Pi session format](https://github.com/fivewillow/badlogic-pi-mono/blob/main/packages/coding-agent/docs/session.md)
  is a useful reference for typed event entries, `id`/`parentId` branching and
  append-only session history, but is not our storage format.
- [CLAP](https://arxiv.org/abs/2206.04769) and [MERT](https://proceedings.iclr.cc/paper_files/paper/2024/file/33dffa2e3d2ab74a783d1a8c292f66d9-Paper-Conference.pdf)
  motivate separate audio/music projections rather than forcing every asset
  through ASR or a text embedding.
- [CARv1](https://ipld.io/specs/transport/car/carv1/) and [CARv2](https://ipld.io/specs/transport/car/carv2/)
  are export/package references for content-addressed blocks with an external
  index; they are candidates for interchange, not the mutable MDBX profile.
- [SQLite's internal-versus-external BLOB study](https://www.sqlite.org/intern-v-extern-blob.html)
  is evidence that inline-versus-file placement needs a benchmark instead of a
  universal byte threshold. The same principle applies to MDBX.
- [SQLite FTS5 external-content tables](https://www.sqlite.org/fts5.html#external_content_and_contentless_tables)
  are a useful precedent for keeping canonical row content separate from a
  derived lexical index; consistency and update/rebuild obligations remain
  explicit.
- Habr's [RAG-Anything overview](https://habr.com/ru/companies/bothub/articles/1037946/)
  and [Zstd dictionary case study](https://habr.com/ru/companies/oleg-bunin/articles/788038/)
  are practical secondary references; claims from them must still be checked
  against the repository's own corpus and crash/reopen tests.
