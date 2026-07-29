# Artifact And Provenance Roadmap

## 1. Purpose

This roadmap defines the media and provenance layer for source material that is
not merely a short UTF-8 text record: documents, images, audio, video, page
layouts, tables, transcripts and derived machine-readable views. It is
normative for future public ingestion, source-connector and artifact-storage
APIs.

The library remains an embedded C++17 memory and retrieval toolkit. It does
not become a media-processing service: PDF/DOCX parsers, OCR, ASR, vision,
scene detection and connector SDKs remain optional adapters. The core owns the
identity, storage, provenance, retrieval and materialization contracts that
make their output inspectable and replaceable.

The default deployment is self-contained: `agent-memory-cpp` owns canonical
catalog metadata, raw bytes and lexical/dense indexes through its own stores
and MDBX adapters. An external vector service is an optional derived-index
adapter only; it never owns canonical source bytes, provenance or citation
truth.

This guide extends, rather than replaces:

- [knowledge-units-roadmap.md](knowledge-units-roadmap.md) for KnowledgeUnit
  and SourceRef contracts;
- [chunkers-roadmap.md](chunkers-roadmap.md) for ingestion and chunking;
- [knowledge-base-roadmap.md](knowledge-base-roadmap.md) for retrieval and
  context assembly;
- [mdbx-containers-extension-tz.md](mdbx-containers-extension-tz.md) for
  MDBX-backed raw-body layouts and generic storage boundaries.

## 2. Design Rules

1. `KnowledgeUnitKind` describes knowledge semantics (`Fact`, `Policy`,
   `Chunk`, `Summary`), never a file format such as PDF, image or video.
2. A logical source and a fetched or imported revision have different,
   stable identities.
3. Original bytes are immutable artifacts. Text extraction, OCR, transcripts,
   layout JSON, captions and translations are versioned representations, not
   source truth.
4. Retrieval works over addressable segments and their projections, never over
   an entire PDF, image or video as one opaque record.
5. Evidence points to a typed, renderable location in source material. A
   generated representation may help find evidence but must not silently
   replace the original anchor.
6. Artifact-processing lineage is separate from the semantic knowledge graph.
7. Binary payloads are materialized only when the downstream runtime requests
   them; they are not automatically inserted into an LLM text context.

## 3. Identity Model

`SourceId`, `SourceRevisionId`, `ArtifactId`, `RepresentationId`,
`SegmentSetId`, `SegmentId`, `SourceRefId` and `EvidenceAnchorId` are opaque,
versioned durable identifiers. Their concrete codec is an application-owned
artifact-profile decision; it must have canonical byte encoding, equality,
import/export round-trip fixtures and no dependence on a local MDBX sequence.
`ArtifactId` is deterministically derived from `BlobDigest`; the other ids may
be random occurrence ids or deterministic identities over their declared
immutable inputs, but a codec must document which. `KnowledgeUnitId` and
`ResourceId` remain local handles and are never substituted for these ids.

Every catalog manifest and workspace backup set declares the identity scheme
that produced its durable ids. Import accepts only an identical scheme or an
explicit registered migration; it must otherwise fail closed before rewriting
or publishing any catalog record.

```cpp
struct ArtifactIdentityScheme {
    std::string scheme_id;          // e.g. "agent_memory.artifact_ids"
    std::uint32_t scheme_version = 1;
    std::string artifact_id_derivation;  // e.g. "blob_digest/v1"
    std::string digest_algorithm;  // e.g. "sha256"
};
```

```text
Source
  -> SourceRevision
       -> ArtifactBinding -> Artifact
       -> Representation
            -> SegmentSet -> Segment -> KnowledgeUnitKind::Chunk -> SearchProjection/index
```

### 3.1 Source

`SourceId` identifies a logical external subject: one web article, YouTube
video, book, repository, file path or chat export. It is stable across
re-downloads, edits and reprocessing.

```cpp
struct Source {
    SourceId id;
    std::string title;
    std::optional<std::string> canonical_uri;
    std::string connector_id;
    std::optional<std::string> external_id;
    std::vector<SourceLocator> locators;
    TypedMetadata metadata;
};
```

`ResourceId` remains the existing stable local storage-facing identity of the
logical source. A local `ResourceRevision`/generation maps to an immutable
`SourceRevisionId`; `SourceId` is the durable cross-environment counterpart of
the local `ResourceId`. Import may allocate a different local `ResourceId`, but
must retain and validate the SourceId/SourceRevisionId mapping.

`SourceLocator` is defined by `resource-reindexing.md`: an active or historical
root-relative path/URI observation, never a source identity. Artifact-aware
export retains locator history where policy allows. Content equality may suggest
a rename to a connector, but cannot silently merge two independent sources.

### 3.2 SourceRevision

`SourceRevisionId` identifies an immutable observed snapshot of a `Source`.
Fetching a changed web page or importing a changed local file creates a new
revision, rather than overwriting a source and losing old citations.

```cpp
enum class CatalogLifecycleState : std::uint8_t;

struct SourceRevision {
    SourceRevisionId id;
    SourceId source_id;
    std::uint64_t retrieved_at_ms;
    std::optional<std::uint64_t> published_at_ms;
    std::optional<std::uint64_t> externally_updated_at_ms;
    std::optional<std::string> etag;
    std::optional<std::string> last_modified;
    BlobDigest snapshot_digest;
    CatalogLifecycleState lifecycle;
    TypedMetadata metadata;
};
```

`snapshot_digest` describes the revision snapshot, not the logical source. A
connector may record an unchanged fetch without creating a new revision only
after proving that the canonical input bytes and relevant source metadata are
unchanged.

### 3.3 Artifact And ArtifactBinding

An `Artifact` is immutable bytes, content-addressed by an algorithm-tagged
digest. Identical byte sequences may therefore deduplicate across revisions.
Roles and retention do not belong to the globally deduplicated artifact:
they belong to its binding in a particular source revision.

```cpp
enum class ArtifactRetentionClass : std::uint8_t {
    SourceOriginal,
    DerivedDurable,
    RebuildableCache
};

enum class CatalogLifecycleState : std::uint8_t {
    Active,
    Retained,
    Superseded,
    Tombstoned,
    Erased
};

struct Artifact {
    ArtifactId id;
    BlobDigest digest;
    std::uint64_t size_bytes = 0;
};

struct ArtifactBinding {
    SourceRevisionId source_revision_id;
    ArtifactId artifact_id;
    std::string role;  // e.g. primary_source_original, extracted_audio, page_render
    ArtifactRetentionClass retention;
    std::optional<std::string> media_type;
    std::optional<std::string> original_name;
    std::uint64_t bound_at_ms = 0;
    TypedMetadata source_metadata;
};
```

```cpp
enum class BlobDigestAlgorithm : std::uint8_t { Sha256 = 1 };

struct BlobDigest {
    BlobDigestAlgorithm algorithm = BlobDigestAlgorithm::Sha256;
    std::array<std::uint8_t, 32> value;
};
```

`BlobDigest` is a full, algorithm-tagged byte digest. It is intentionally not
the existing 16-byte `ContentHash` used by `KnowledgeUnitKey`; the latter is a
unit-content/dedup contract and must not be reused for artifact identity.
`Artifact` stores only byte-intrinsic identity and size. MIME type, filename,
observation time, source metadata, retention and role are binding facts because
the same bytes can be imported into different sources under different policies.
`record_artifact` is idempotent only when `id`, digest and size agree; a
conflicting record is a validation error, not a metadata overwrite.

Each SourceRevision has exactly one retained binding with role
`primary_source_original`; it replaces the former ambiguous
`primary_artifact_id` field. Additional bindings are allowed and explicit.

`SourceOriginal` must be retained or reported as unavailable. `DerivedDurable`
is retained because rebuilding it would be expensive, nondeterministic or
would lose provenance. `RebuildableCache` may be evicted and regenerated.

Source revisions, representations and segment sets carry a lifecycle state and
retention policy even though their immutable identity never changes. `Active`
is eligible for current retrieval, `Retained` remains addressable for evidence
or backup, `Superseded` is no longer the active view, `Tombstoned` preserves
only the metadata/erasure receipt, and `Erased` has no materializable bytes.
An authorized erasure may leave an evidence anchor resolvable as tombstoned
metadata, but it must never leave an unbounded liveness root.

### 3.4 Representation

A representation is a versioned interpretation of one or more artifacts:
extracted text, OCR, structured document JSON, transcript, subtitle track,
table structure, visual description, scene index or translation. It is not
canonical knowledge and must retain enough processor provenance to explain or
rebuild it.

```cpp
struct Representation {
    RepresentationId id;
    SourceRevisionId source_revision_id;
    std::vector<ArtifactId> input_artifact_ids;
    std::optional<ArtifactId> output_artifact_id;
    std::string kind;
    std::optional<std::string> language;
    std::string processor_id;
    std::string processor_version;
    std::optional<std::string> model_id;
    BlobDigest parameters_digest;
    std::optional<double> confidence;
    std::uint64_t generated_at_ms = 0;
    CatalogLifecycleState lifecycle = CatalogLifecycleState::Active;
    TypedMetadata metadata;
};
```

Changing processor version, model, relevant parameters or input artifact bytes
creates a new representation. Translation projections defined in
[translation-adapters-roadmap.md](translation-adapters-roadmap.md) are one
specialized representation/projection path and retain their existing package
provenance requirements.

### 3.5 Segment Sets And Knowledge Units

A `Segment` is an addressable part of a representation: a paragraph, heading,
PDF page block, table, image caption, transcript interval, scene or OCR region.
It is the source-media coordinate from which retrieval units are created.

```cpp
struct SegmentSet {
    SegmentSetId id;
    RepresentationId representation_id;
    std::string segmenter_id;
    std::string segmenter_version;
    BlobDigest parameters_digest;
    std::uint64_t generated_at_ms = 0;
    CatalogLifecycleState lifecycle = CatalogLifecycleState::Active;
};

struct FigureContext {
    ArtifactId figure_artifact_id;
    std::vector<SegmentId> caption_segments;
    std::vector<SegmentId> adjacent_text_segments;
    std::optional<std::string> author_alt_text;
};

struct Segment {
    SegmentId id;
    SegmentSetId segment_set_id;
    std::uint64_t sequence = 0;
    std::string text;
    std::vector<Locator> locators;
    std::optional<SegmentId> parent_segment_id;
    std::optional<FigureContext> figure_context;
    std::optional<std::string> language;
    std::optional<std::uint64_t> token_count;
    TypedMetadata metadata;
};
```

`FigureContext` is structural representation metadata: it relates an embedded
image or figure to its caption, author-provided alt text, and neighboring text
segments without inventing a semantic graph edge. Caption and adjacent segment
ids must belong to the same immutable source revision; missing or stale ids are
validation errors. OCR text and a vision description remain separately labeled
derived representations or segments, not silently merged into the original
caption or alt text.

A retrieval-eligible segment materializes as exactly one
`KnowledgeUnitKind::Chunk` for one immutable `SegmentSetId` and one explicit
materialization policy. `SegmentId` is a deterministic stable provenance
coordinate derived from `(segment_set_id, sequence, structural locator)`;
`KnowledgeUnitId` remains the local primary key used by stores and indexes.
Changing a segmenter, processor, inputs, parameters or chunk policy creates a
new SegmentSet and new mapping; it never mutates or replaces cited segments.

The catalog keeps `SegmentMaterialization` separately as an active-view mapping
from `(segment_set_id, MaterializationPolicyId)` to local Chunk/KnowledgeUnit
ids. It may switch which immutable set is active for retrieval, while old sets
remain reachable for evidence, export and reproducibility. Fixtures must cover
same bytes with changed chunk policy, changed processor, and a retained old
anchor; none may silently acquire a new `SegmentId` or locator.

## 4. Typed Locators And Evidence Anchors

`TextRange` is sufficient only for UTF-8 text. The public provenance contract
must use typed locators; renderers turn them into human-facing strings without
changing their stored coordinates.

```cpp
struct NormalizedBox {
    double x0 = 0.0;
    double y0 = 0.0;
    double x1 = 1.0;
    double y1 = 1.0;
};

struct TextLocator {
    std::uint64_t byte_offset = 0;
    std::uint64_t byte_length = 0;
};

struct PageRegionLocator {
    std::uint32_t page_index = 0;  // zero-based internally
    std::optional<NormalizedBox> box;
    std::vector<std::string> block_ids;
    std::optional<std::uint64_t> character_start;
    std::optional<std::uint64_t> character_end;
};

struct TimeRangeLocator {
    std::uint64_t start_ms = 0;
    std::uint64_t end_ms = 0;
    std::optional<std::string> speaker_id;
    std::optional<std::string> track_id;
};

struct FrameRegionLocator {
    std::uint64_t timestamp_ms = 0;
    std::optional<std::uint64_t> frame_number;
    std::optional<NormalizedBox> box;
};

struct ImageRegionLocator {
    NormalizedBox box;
};

struct SlideLocator {
    std::uint32_t slide_index = 0;
    std::vector<std::string> shape_ids;
};

struct SpreadsheetLocator {
    std::string sheet;
    std::string cell_range;
};

struct WholeArtifactLocator {};

struct WebLocator {
    std::optional<std::string> fragment;
    std::optional<std::string> selector;
};

using Locator = std::variant<WholeArtifactLocator, TextLocator,
                             PageRegionLocator, TimeRangeLocator,
                             FrameRegionLocator, ImageRegionLocator, SlideLocator,
                             SpreadsheetLocator, WebLocator>;

struct AlignmentProvenance {
    std::string processor_id;
    std::string processor_version;
    std::string method;
    std::optional<double> confidence;
};

enum class MaterializationOperation : std::uint8_t {
    OriginalRange,
    RenderPage,
    RenderRegion,
    ExtractFrame,
    ExtractClip
};

struct MaterializationLimits {
    std::uint64_t max_output_bytes = 0;
    std::optional<std::uint64_t> max_duration_ms;
    std::optional<std::uint32_t> max_pixel_dimension;
};

enum class MaterializationAccessOutcome : std::uint8_t {
    Allowed,
    Denied,
    RequiresHostAuthorization
};

struct MaterializationInstruction {
    EvidenceAnchorId anchor_id;
    SourceRevisionId source_revision_id;
    ArtifactId original_artifact_id;
    Locator original_locator;
    MaterializationOperation operation = MaterializationOperation::OriginalRange;
    MaterializationLimits limits;
    MaterializationAccessOutcome access =
        MaterializationAccessOutcome::RequiresHostAuthorization;
};
```

`NormalizedBox` coordinates are in `[0, 1]`; they remain stable when a PDF page
or image is rendered at a different DPI. `TimeRangeLocator` uses integer
milliseconds. `start_ms <= end_ms`, boxes must be ordered and in range, and a
locator must be meaningful for the anchored media type.
`ImageRegionLocator` is for a still image and always carries a meaningful box;
`FrameRegionLocator` is reserved for time-addressed video frames. A whole image
uses `WholeArtifactLocator`, never a synthetic timestamp.

`SourceRefSummary` remains the compact M0 citation preview. In artifact-aware
profiles, a full `SourceRef` carries one or more `EvidenceAnchor` values:

```cpp
struct EvidenceAnchor {
    EvidenceAnchorId id;
    SourceId source_id;
    SourceRevisionId source_revision_id;
    ArtifactId original_artifact_id;
    Locator original_locator;
    std::optional<RepresentationId> representation_id;
    std::optional<SegmentId> segment_id;
    std::optional<Locator> representation_locator;
    std::optional<AlignmentProvenance> alignment;
    std::string excerpt;
};
```

The anchor's `original_artifact_id` is the durable citation target. A
transcript, OCR result or vision description can be named as a representation
that helped find the result, but it cannot be presented as an unqualified
original observation. `original_locator` is mandatory and coordinates the
original artifact. `representation_locator` coordinates a derived artifact only
when `representation_id` and an `AlignmentProvenance` (processor/version,
alignment method and optional confidence) are present. A context excerpt based
on derived text is labeled as derived. If an original artifact has been deleted
under an authorized retention policy, the anchor remains resolvable as metadata
and materialization reports `ArtifactUnavailable`.

## 5. Catalog, Blob And Lineage Boundaries

The application-level ports are intentionally separate:

```text
ArtifactCatalog
  owns Source, SourceRevision, Artifact, ArtifactBinding, Representation,
  SegmentSet, Segment, materialization and artifact-processing lineage metadata.

BlobStore
  owns immutable artifact bytes, digest verification, range reads and
  materialization into a caller-provided temporary/output location.

KnowledgeUnitStore / IProjectionStore
  own semantic units and their retrieval projections.

ILexicalIndex / IDenseIndex
  own derived search indexes over segment-backed Chunk units.
```

The port sketches deliberately use request/result value types so the core does
not leak a file path, an MDBX transaction type or an object-store SDK into
public APIs:

```cpp
enum class CatalogStatus : std::uint8_t {
    Ok, AlreadyExists, NotFound, Conflict, InvalidArgument,
    IntegrityViolation, BackendUnavailable
};

template <typename T>
struct CatalogResult {
    CatalogStatus status = CatalogStatus::Ok;
    std::optional<T> value;
    std::string diagnostic;
};

struct CatalogStatusResult {
    CatalogStatus status = CatalogStatus::Ok;
    std::string diagnostic;
};

class IArtifactCatalog {
public:
    virtual ~IArtifactCatalog() = default;

    virtual CatalogResult<SourceId> create_source(const CreateSourceRequest& request) = 0;
    virtual CatalogResult<SourceRevisionId> append_revision(
        const AppendSourceRevisionRequest& request) = 0;
    virtual CatalogResult<ArtifactId> record_artifact(const Artifact& artifact) = 0;
    virtual CatalogStatusResult bind_artifact(const ArtifactBinding& binding) = 0;
    virtual CatalogResult<RepresentationId> record_representation(
        const Representation& representation) = 0;
    virtual CatalogResult<SegmentSetId> record_segment_set(
        const SegmentSet& set, const std::vector<Segment>& segments) = 0;
    virtual CatalogStatusResult activate_materialization(
        const SegmentMaterialization& materialization) = 0;
    virtual CatalogResult<Segment> find_segment(SegmentId id) const = 0;
};

enum class BlobStatus : std::uint8_t {
    Ok, NotFound, AccessDenied, RangeUnsupported, DigestMismatch,
    Corrupt, LimitExceeded, BackendUnavailable
};

template <typename T>
struct BlobResult {
    BlobStatus status = BlobStatus::Ok;
    std::optional<T> value;
    std::string diagnostic;
};

struct BlobStatusResult {
    BlobStatus status = BlobStatus::Ok;
    std::string diagnostic;
};

struct BlobIngestLease {
    std::string lease_id;
    BlobDigest intended_digest;
    std::uint64_t expires_at_ms = 0;
    std::string owner_id;
};

class IBlobStore {
public:
    virtual ~IBlobStore() = default;

    virtual BlobResult<BlobIngestLease> begin_ingest(const BlobWriteRequest& request) = 0;
    virtual BlobResult<ArtifactId> put_immutable(
        const BlobWriteRequest& request, const BlobIngestLease& lease) = 0;
    virtual BlobResult<ArtifactId> finalize_ingest(const BlobIngestLease& lease) = 0;
    virtual BlobStatusResult abort_ingest(const BlobIngestLease& lease) = 0;
    virtual BlobResult<BlobMetadata> probe(ArtifactId id) const = 0;
    virtual BlobResult<BlobReadHandle> open(ArtifactId id, const BlobReadRange& range) = 0;
    virtual BlobResult<MaterializedArtifact> materialize(
        ArtifactId id,
        const MaterializationRequest& request) = 0;
};
```

`CatalogResult<T>` is used only when a successful or idempotent operation has a
durable value to return. Mutations without a value use `CatalogStatusResult`;
`CatalogResult<void>` is forbidden. `find_segment` returns `NotFound` rather
than a nested optional. Create and record operations are create-or-validate:
equal immutable input returns `AlreadyExists` plus the canonical id, while a
different record for the same id returns `Conflict` or `IntegrityViolation`.

`BlobIngestLease` is a durable, expiring liveness root created before bytes are
written. It contains an opaque lease id, intended digest, expiry and ingest
owner. `record_artifact` records verified metadata only after
`IBlobStore::finalize_ingest` has established byte identity; catalog publication
then consumes the lease atomically with bindings. A crashed or explicitly
aborted ingest retains no permanent catalog root. `record_segment_set` appends
an immutable set; `activate_materialization` changes only the current retrieval
view. A boolean existence API is deliberately forbidden because callers must
distinguish missing bytes from policy denial, corruption and backend failure.

Import/export serializes a versioned catalog manifest, including
`ArtifactIdentityScheme`, before units that cite it:
SourceId, SourceRevisionId, ArtifactId/BlobDigest, RepresentationId,
SegmentSetId, SegmentId, SourceRefId and EvidenceAnchorId are durable and are
never rewritten. The destination may map SourceId to a different local
ResourceId and global unit ids to different local unit ids. It validates or
creates the manifest, global-to-local identity mappings, bindings and anchors
in the same atomic import unit as the KnowledgeUnit records that reference
them; a failed transaction publishes neither dangling units nor a half-visible
catalog. A backend that cannot atomically cover its blob store records durable
ingest leases and exposes no units until the final catalog commit verifies all
declared BlobDigests and consumes those leases.

`ArtifactCatalog` is not a semantic graph. Its `artifact_relations` record
technical facts such as `derived_from`, `embedded_in`, `extracted_audio_from`,
`rendered_from` and `generated_from`. `IGraphStore` continues to hold semantic
relations such as `requires`, `contradicts`, `governs` and `uses`.

`BlobStore` must support digest-verified writes, immutable reads, existence
checks, bounded range reads when the backend supports them, and materialization.
It may be implemented by:

- an MDBX-backed `ResourceBodyStore` / chunked application-owned layout for
  agent-local data;
- a content-addressed external file-pack that can preserve imported folder
  paths as source metadata and reconstruct an export/viewer tree;
- a deployment-owned object store adapter.

All backends expose the same artifact identity. Compression, encryption and
chunking are storage codecs declared in the artifact/body descriptor; they do
not alter `ArtifactId`, which is based on the original immutable byte stream.

## 6. Indexing And Context Assembly

The library's own lexical and dense indexes are the primary retrieval path.
They index segment-backed Chunk units and their explicit search projections.
An `ExternalVectorIndexAdapter`, if enabled, receives only a derived segment
projection plus identifiers and filter metadata. It must not accept canonical
artifact bytes as a payload and must never become the source of truth for
citations, deletion, retention or backup.

### Text-Only External Index Adapter

The default and preferred path remains the library's own MDBX-backed lexical
and dense stores. Before the M2 artifact profile, an M1a profile may
optionally use an external vector service such as Qdrant only for a text-only
derived projection corpus. This supports incremental migration and fair
quality/latency benchmarks without creating a second knowledge base.

The text-only adapter receives only `SearchProjection::Original` text, local
unit id, stable local ResourceId, ResourceRevision/generation, projection
revision, scope and permitted filter metadata. A candidate returned by the
service is always hydrated and revalidated from the canonical local unit and
resource manifest before it becomes a `RetrievalHit` or context block. Deletion,
reindex and stale revision checks originate in the canonical store; the adapter
may lag and is rebuildable.

This admission does not authorize non-text ingestion. PDF/OCR/ASR/media
adapters, page/frame citations, artifact bytes and EvidenceAnchors still require
the M2 artifact profile. The external adapter is never a catalog, BlobStore,
backup authority or provenance authority.

Multimodal segments may expose independent projections, for example transcript
text, OCR text, visual description and image embedding input. Fusion happens
through the existing retrieval composition layer, not by pretending that all
modalities are one text field.

`Context` carries text, `SourceRefSummary`, and typed
`MaterializationInstruction` values by default. An instruction names its stable
anchor, source revision, original artifact and original locator, and constrains
the requested operation, output and authorization outcome. It is a reference,
not an implicit byte attachment. A text-only consumer receives excerpt and a
citation; a multimodal consumer may explicitly request the corresponding page,
frame, crop or clip. A transcript/OCR-derived excerpt must carry its derived
representation label and never masquerade as a direct original quote.

## 7. Persistence, Retention And Backup

Artifact provenance is an application-owned profile capability, not a new
generic `mdbx-containers` domain API. A future MDBX profile may use a compact
typed catalog table plus application-owned lineage and segment-to-unit indexes.
Its DBI delta and migration must be declared with the profile that enables it;
it is not silently added to the M0 canonical manifest.

The existing `ResourceBodyStore` rules remain in force:

- raw bytes appear only in primary body/blob storage, never reverse indexes;
- chunked bodies write chunks before a complete descriptor;
- body replacement creates a new revision and bounded GC removes only bytes
  absent from the catalog's complete liveness closure;
- descriptor codecs, limits, checksums and encryption policy are explicit.

Backups have two levels:

```text
Catalog backup
  catalog metadata, unit/projection records, source and artifact manifests.

Workspace backup set
  catalog backup + SourceOriginal and DerivedDurable artifact bytes +
  checksums, retention classes and processor manifests.
```

Rebuildable caches, thumbnails, temporary clips and all ANN/vector index state
are excluded from a complete workspace backup. They must be rebuildable from
the retained catalog and artifact set.

The liveness closure is evaluated by the same catalog backend that executes
deletion. Its roots include retained `ArtifactBinding` records with
`SourceOriginal` or `DerivedDurable` retention, all retained source revisions,
representations, segment sets, segments, evidence anchors, full source refs,
knowledge units, backup snapshots/leases, import/export transactions,
unexpired `BlobIngestLease` records and in-flight materializations. It follows
catalog lineage and binding edges before deleting any blob. An orphan sweep may
collect only an expired, unfinalized ingest lease after verifying that no catalog
binding, backup or transaction references it. A missing or inconsistent catalog
edge is fail-closed: it keeps the artifact and reports an integrity error rather
than collecting it.

## 8. Delivery Order And Acceptance Gates

### Artifact Contracts Before Non-Text Connectors

Before a public non-text source connector, document parser or media external
vector adapter is released, implement and test:

1. opaque IDs and the Source/SourceRevision/Artifact/Representation/SegmentSet/Segment
   catalog model;
2. typed locator serialization, validation and display rendering;
3. `EvidenceAnchor` integration with SourceRef and citation output;
4. ArtifactCatalog and BlobStore contracts with MDBX and external-file-pack
   conformance fixtures;
5. retention, reachability and backup-manifest behavior;
6. segment-to-Chunk materialization and source-revision reindexing.

The M1a text-only external-index admission in section 6 is explicitly exempt
from this gate, but must prove canonical-hit hydration, stale-revision rejection,
delete propagation and benchmark parity against the library-owned baseline.

### Vertical slices after contracts

The first format slice should cover PDF/document/image input: original bytes,
structured representation, page/block locators, extracted figures and
segment-backed chunks. Video/audio is a later slice: original video, audio,
timestamped transcript, scene/keyframe metadata, OCR/description projections
and on-demand frame/clip materialization.

Concrete Docling, FFmpeg, OCR, ASR and vision implementations remain adapters.
The core acceptance test is the same regardless of provider: a retrieval hit
can cite, validate and, when authorized, materialize its exact original source
location.

Minimum evaluation gates are:

- citation/locator correctness for text, page, image-region and timestamp
  fixtures, including figure caption/context links;
- no stale segment or projection after a source revision changes;
- original-versus-derived representation labeling in context and traces;
- retention and backup/restore reachability checks, including binding roots and
  interrupted import/export/materialization fixtures;
- bounded read amplification and explicit `BlobStatus` results for
  compressed/chunked artifacts;
- retrieval quality and latency reported separately for each modality and
  fusion policy.

## 9. Non-Goals

This roadmap does not require a concrete media parser, hosted vector database,
ASR/VLM model, object-store SDK or UI viewer. It also does not add a generic
workflow engine. Those are adapters or host-application responsibilities once
the stable artifact contract above exists.
