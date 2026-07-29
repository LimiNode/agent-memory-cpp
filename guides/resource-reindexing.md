# Resource Reindexing Roadmap

## Purpose

This guide captures the planned resource ownership layer for partial
reindexing. The goal is to let a knowledge base replace one source resource
without rebuilding every document, embedding, vector index, text index, or
approximate bucket.

The core idea is a reverse manifest:

```text
resource_id -> all derived records created from that resource
```

Chunks, embeddings, binary signatures, lexical postings, graph records, and
other index entries are derived data. They must be invalidated or refreshed by
resource, not only discovered by scanning the whole database.

## Core Rules

- Every stored chunk, embedding, and index entry should be traceable back to a
  stable `ResourceId`.
- `ChunkId` remains globally unique within a storage backend.
- A resource manifest records the derived records that belong to one resource.
- Reindexing one resource must not require a full index rebuild.
- Reindexing must be idempotent: repeating the same resource update should not
  duplicate derived records.
- Backends that support transactions should replace resource state and derived
  records atomically.
- Frequently updated indexes may use generations, tombstones, or stale-entry
  filtering before physical compaction.
- Full float embeddings remain the quality source of truth for reranking even
  when approximate signatures or compressed bucket lists are present.

## Terms

`ResourceId`
: Stable **local** identity of an original logical source item in one storage
environment. A markdown file, web page, code symbol, conversation note, profile
fact, or memory note can all be resources. It is stable across revisions and
reindexing in that environment.

`SourceId` / `SourceRevisionId`
: The artifact-provenance profile's durable cross-environment identities for the
same logical source and its immutable observed snapshot. One local `ResourceId`
maps to one `SourceId`; each local `ResourceRevision` maps to exactly one
`SourceRevisionId`. Import may allocate a different local `ResourceId`, but it
must retain and validate the source/revision identities and their manifest.

`ResourceRevision`
: An immutable local observed/reindex record for a `ResourceId`. The active
manifest selects its current generation; older cited revisions remain
addressable until retention can safely remove them. The first implementation
can model a revision as `resource_id`, `generation`, an uncompressed content
hash, and a pipeline configuration hash. It adapts to, rather than replaces,
immutable `SourceRevisionId` in artifact-aware profiles.

`SourceLocator`
: A portable, mutable observation of where a logical resource was seen. It is
not identity and may change while `ResourceId` remains stable. A source can
retain active and historical aliases so export may reconstruct a useful folder
tree without making a filesystem path part of identity.

`ResourceManifest`
: The list of derived keys created from the current resource revision.

`DerivedRecordRef`
: A typed reference to a derived record, for example a chunk id, embedding id,
vector record key, binary bucket key, lexical posting key, or graph node key.

## Suggested Manifest Shape

The first value types should stay dependency-free and live near the domain or
storage contracts.

```cpp
enum class SourceLocatorKind : uint8_t {
    WorkspaceRelativePath,
    CanonicalUri,
    ImportedArchivePath,
    ApplicationAlias
};

struct SourceLocatorObservation final {
    std::string workspace_root_id;
    SourceLocatorKind kind = SourceLocatorKind::WorkspaceRelativePath;
    std::string normalized_relative_path;
    std::uint64_t observed_at_ms = 0;
};

struct ResourceRevision final {
    ResourceId resource_id;
    std::optional<SourceId> source_id;
    std::optional<SourceRevisionId> source_revision_id;
    std::uint64_t generation = 0;
    std::uint64_t content_hash = 0;
    std::uint64_t pipeline_config_hash = 0;
    std::vector<SourceLocatorObservation> locator_observations;
};

enum class SourceLocatorStatus : uint8_t { Active, Alias, Retired };

struct SourceLocator final {
    std::string workspace_root_id;
    SourceLocatorKind kind = SourceLocatorKind::WorkspaceRelativePath;
    std::string normalized_relative_path;
    std::uint64_t first_seen_at_ms = 0;
    std::uint64_t last_seen_at_ms = 0;
    SourceLocatorStatus status = SourceLocatorStatus::Active;
};

enum class DerivedRecordKind {
    Document,
    Chunk,
    Embedding,
    VectorRecord,
    BinaryBucketPosting,
    LexicalPosting,
    GraphRecord,
    Custom
};

struct DerivedRecordRef final {
    DerivedRecordKind kind = DerivedRecordKind::Chunk;
    ChunkId chunk_id;
    std::string key;
    std::uint32_t ordinal = 0;
};

struct ResourceManifest final {
    ResourceRevision revision;
    std::vector<DerivedRecordRef> records;
};
```

This is not a final API. It documents the intended contract shape before code is
introduced.

`ResourceRevisionRef` is the local durable citation binding. It identifies the
exact retained body revision from which a byte range was measured, while
`ResourceId` remains the stable identity of the changing logical source. An
artifact-aware profile maps this reference to its immutable `SourceRevisionId`.
Every non-preview-only quote keeps that referenced body revision live: a later
update may make a newer generation active for retrieval, but must not overwrite
the older bytes while a `SourceRefSummary` still materializes them. An
implementation may use immutable versioned entries in `ResourceBodyStore` or an
artifact BlobStore; the current `ResourceManifest` is an active-view pointer,
not permission to discard cited generations.

`ResourceId` is never recomputed from `content_hash`, `pipeline_config_hash`,
path or URI. A connector may discover a rename through a stable provider/file
identity, an explicit application mapping, or an operator-confirmed match, then
append/update a `SourceLocator` while retaining the logical ResourceId. Equal
bytes alone must not silently merge two independently imported documents.
`workspace_root_id` is an application-defined portable root label, not an
absolute machine path; export preserves root-relative paths and aliases where
retention policy permits.

`SourceLocator` is mutable source-level history for navigation and rename
tracking. `SourceLocatorObservation` is the immutable portable location seen by
one `ResourceRevision`; every imported revision records at least one
observation. A later document may reuse a retired path without changing the
older revision's observed location. Artifact-aware profiles carry the same
observations on the corresponding `SourceRevision` and include them in export.

`pipeline_config_hash` should cover settings that change derived records even
when source text is unchanged. Examples include chunking settings, parser
version, embedding model id, normalization policy, signature encoder config,
index-specific encoding settings, raw source format, compression framing,
document boundary policy, tokenizer id, token budget, overlap, and safe-boundary
policy.

Tokenizer-aware ingestion treats source bytes, decompression, document
boundaries, parser/extractor output, token-budgeted chunking and index
projection generation as separate reproducible stages. This follows the
Gigatoken-style pipeline lesson captured in
[`chunkers-roadmap.md`](chunkers-roadmap.md) §10, without making Gigatoken or
any Rust/Python tokenizer a core dependency.

`DerivedRecordRef` is intentionally broad at the roadmap stage, but concrete
code should define field usage precisely. Chunk, embedding, and vector records
can use `chunk_id`. Bucket postings, lexical postings, graph records, or
backend-specific records can use `key` plus an optional `ordinal`. `ordinal`
is a resource-local order or discriminator, not a stable offset inside a
compressed bucket blob.

The first value-type PR should only expose a required-field helper. Strict
validation, for example rejecting unused fields for a record kind, belongs with
the storage contract PR where invalid combinations can be defined against real
persistence semantics.

Do not introduce `EmbeddingId` until the storage model supports multiple
embeddings per `ChunkId`. The baseline model treats embedding and vector records
as derived data addressed by `ChunkId` for one active pipeline. If future
backends keep multiple model, purpose, revision, modality, encoding, or vector
index variants per chunk, introduce a composite identifier or use `key` /
`Custom` until that contract is clear.

## MDBX Storage Shape

A future MDBX implementation can keep resource state in separate tables:

```text
resources:
    key   = resource_id
    value = resource metadata, current generation, content hash, optional blob

resource_manifests:
    key   = resource_id
    value = list of derived record references for the current generation

resource_bodies:
    key   = (resource_id, generation, content_hash)
    value = immutable source bytes or an addressable body/blob reference

chunks:
    key   = chunk_id
    value = chunk text, metadata, resource_id, generation, chunk_index

embeddings:
    key   = chunk_id or embedding_id
    value = float32 vector, model metadata, resource_id, generation

binary_bucket_index:
    key   = short binary signature key
    value = posting list with chunk_id, resource_id, generation, full signature
```

The manifest should store enough keys to remove or mark all derived records for
one resource without scanning unrelated resources.

## Reindex Algorithm

The normal replace flow is two-phase. It must not hold a write transaction while
parsing a document, calling an embedder, or rebuilding a large derived index:

```text
1. Read the current manifest and prepare immutable source/body bytes, parsed
   text, chunks, projections and a candidate next generation outside a write
   transaction. Heavy embedding, ANN and bulk backfill work is revision-guarded
   derived work, not part of publication.
2. In a short write transaction, compare the expected current generation,
   write the new resource revision, raw units, required lexical projections and
   manifest, then publish the next generation as active. A conflict restarts
   preparation from the newly observed generation.
3. Mark the former generation stale only after the active-generation swap.
   Readers resolve the manifest/current generation first and therefore keep
   seeing the prior complete revision until publication succeeds.
4. Enqueue optional dense, ANN, signature or graph work as idempotent jobs
   carrying `(resource_id, generation, pipeline_config_hash)`. Workers reject
   stale generations before making a derived view visible.
```

If content hash and ingestion settings did not change, the reindex operation may
skip expensive work. The skip check must compare both `content_hash` and
`pipeline_config_hash`.

The M0 lexical-first importer requires the source body, raw units, mandatory
provenance summaries, `Original` projections and lexical indexes before it may
publish a new active generation. Dense/vector projections can be synchronous
only for a deliberately small profile; otherwise they remain revision-guarded
eventual work and retrieval revalidates every derived hit against the active
manifest. `ResourceIndexer` does not yet implement this protocol.

## Tombstones And Compaction

Compressed bucket lists make physical deletion expensive because one entry
usually requires:

```text
read bucket -> decompress -> filter entries -> recompress -> write bucket
```

That is acceptable for infrequent document updates. For mutable agent memory,
use generation filtering or tombstones:

```text
bucket entry generation != current resource generation -> stale, skip at query
```

A later `compact_index()` operation can rebuild affected buckets and remove
stale entries in batches. This keeps recall-time updates fast while preserving a
path to reclaim storage.

Query-time stale filtering must not turn approximate search into many random
resource-table reads. Implementations should keep current resource generations
cheap to access, for example through a small in-memory cache, a batch lookup, or
a bucket posting format that lets stale checks be amortized.

## Notes As Resources

Short memory notes should use the same resource model instead of a separate
architecture:

```text
resource_id = note_id
chunks.size() = 1
```

The note update path is then just a small resource reindex. It removes the old
embedding and index entries for that one note, writes the new derived records,
and updates the manifest.

## M0 Import Contracts

The public M0 write path is lexical-first and does not require an embedder or a
vector index. It is owned by ingestion, not by an application CLI. Concrete
connectors remain adapters, but every adapter publishes the same observed-source
shape:

```cpp
struct ObservedResource {
    std::optional<ResourceId> known_local_id;
    std::optional<SourceId> durable_source_id;
    SourceLocator locator;
    std::string content_type;
    std::vector<std::uint8_t> utf8_body;
    SourceTextOrigin text_origin;
    std::optional<DerivedTextProvenance> derived_text_provenance;
    TypedMetadata metadata;
};

struct ResourceImportResult {
    ResourceId resource_id;
    ResourceRevision revision;
    std::vector<KnowledgeUnitId> raw_units;
};

class IResourceConnector {
public:
    virtual ~IResourceConnector() = default;
    virtual std::vector<ObservedResource> observe() = 0;
};

class IResourceImporter {
public:
    virtual ~IResourceImporter() = default;
    virtual ResourceImportResult import(ObservedResource resource) = 0;
};

class ICuratedUnitNormalizer {
public:
    virtual ~ICuratedUnitNormalizer() = default;
    virtual std::vector<KnowledgeUnitRef> normalize(
        const ResourceImportResult& imported) = 0;
};
```

`IResourceImporter` resolves/allocates the stable ResourceId, appends the
ResourceRevision and locator history, writes body/manifest, creates raw
`Note`/`Chunk` units with required inline SourceRefSummary, then publishes
`Original` projections. Publication is coordinated: a failed import leaves the
previous manifest/generation visible, or writes a new generation that becomes
active only after all required units and projections exist. `ICuratedUnitNormalizer`
is M1b: it may create Facts, QAPairs, concepts or cards with provenance, but
never replaces, mutates away or impersonates the raw source.

`text_origin` is mandatory. `OriginalText` must not carry extraction metadata.
`DerivedExtraction` must carry non-empty source media type, extractor id and
extractor version; otherwise the importer rejects the observation. It persists
that provenance with the `ResourceRevision`, sets the resulting
`SourceRefSummary::text_origin` to `DerivedExtraction`, and never emits an
original-media citation from the derived text alone.

Every newly created quote-based `SourceRefSummary` binds the committed
`ResourceRevisionRef` before publication. A failed import leaves both the prior
active manifest and its cited body revisions materializable. A retention job may
remove an old body only after every remaining reference is explicitly
`preview-only` or has been migrated to another durable evidence anchor.

These are roadmap contracts, not an assertion that the current C++ prototype
already implements them. Connector-specific filesystem walking, `--knowledge-path`
CLI UX, frontmatter parsing and PDF/OCR adapters remain outside the core.

## Existing Dense Prototype And Future Contracts

`src/agent_memory/ingestion/ResourceIndexer` is a pre-chunked dense/vector
prototype. It requires `IEmbedder` and `IVectorIndex`. It now writes replacement
document/vector records and publishes the replacement manifest before it performs
best-effort reclamation of records that belong only to the old manifest. It
serializes calls through one instance, rejects an older generation, treats an
equal generation with equal hashes as idempotent, and rejects an equal generation
with conflicting hashes. If an in-process document, vector, or manifest write
throws, it restores the document/vector records touched by that attempt and leaves
the prior manifest active. An interruption during reclamation can leave harmless
stale derived records, never remove the published replacement. The independent
prototype backends cannot make this a cross-store crash transaction or coordinate
independently constructed indexers, so applications must not treat it as the
generic M0 import API or as crash-atomic reindexing. In particular, a raw
`IVectorIndex` reader can observe a just-written record while a reindex call is
still in flight; public retrieval must use the future M0 transactional importer
and active-manifest validation rather than this prototype path.

### Legacy Public API Boundary

`SourceRef` was intentionally renamed to `FactSourceRef` before the first
stable public release. This is a source-breaking pre-1.0 migration, not a
deprecated alias: keeping an alias would reserve the canonical `SourceRef`
name for the incompatible legacy fact pointer. Consumers rename their include
and type references, then migrate durable provenance to canonical
`SourceRefSummary` / `SourceRef` through the importer. Serialized legacy fact
records retain their versioned decoder only for explicit migration tooling; new
public APIs must not write them as canonical citations.

The current `Document`, `DocumentChunk`, `RetrievedChunk`, and structured-fact
records predate the M0 provenance contract. `Document::TextRange` is only a
byte-coordinate primitive; it has no resource revision or representation
binding. `RetrievedChunk` cannot carry a revision-bound citation. The former
fact-local `SourceRef` has been renamed `FactSourceRef` so it cannot be confused
with the canonical provenance `SourceRef` described in
`knowledge-units-roadmap.md`.

New source connectors and public artifact-aware retrieval must use the canonical
`SourceRefSummary`/full `SourceRef` contract and return a hydration handle for
its `EvidenceAnchor`. A temporary adapter may project a canonical reference into
`FactSourceRef` or `Document::TextRange` for legacy consumers, but never the
other way around. The first public M0 importer/retrieval API introduces the
canonical domain header; no adapter may expose the legacy types as its durable
provenance model.

Possible dependency-free contracts:

```cpp
class IResourceManifestStorage {
public:
    virtual ~IResourceManifestStorage();

    [[nodiscard]] virtual std::optional<ResourceManifest> find_manifest(
        const ResourceId& resource_id
    ) const = 0;

    virtual void upsert_manifest(ResourceManifest manifest) = 0;

    [[nodiscard]] virtual bool erase_manifest(
        const ResourceId& resource_id
    ) = 0;
};

class IResourceIndexer {
public:
    virtual ~IResourceIndexer();

    virtual void index_resource(ResourceSnapshot resource) = 0;

    virtual void reindex_resource(
        const ResourceId& resource_id,
        ResourceSnapshot resource
    ) = 0;

    [[nodiscard]] virtual bool erase_resource(
        const ResourceId& resource_id
    ) = 0;
};
```

The exact names can change when implementation starts. The important boundary is
that M0 importing composes canonical resource/unit/projection storage before
optional embedding and index work, rather than hiding every stage behind one
large facade.

## Test Expectations

Future PRs should add focused tests for:

- replacing one resource removes old chunks and vector records for that
  resource only;
- replacing one note behaves as a one-chunk resource update;
- failed reindex leaves the old committed manifest visible;
- unchanged content hash can skip re-embedding when ingestion settings match;
- stale bucket entries are ignored by generation checks;
- compaction removes stale bucket entries without changing query results;
- repeated reindex of the same resource is idempotent;
- two resources cannot conflict through reused chunk ids.
- changed content keeps the same ResourceId and creates a newer revision;
- an old quote remains materializable from its bound body revision after a
  newer resource generation becomes active;
- a derived extraction without complete extractor provenance is rejected, and a
  valid derived extraction never renders as an original-media quotation;
- rename/move preserves locator history only after stable connector identity or
  explicit application confirmation, never merely because bytes match;
- export/import may remap ResourceId but preserves SourceId/SourceRevisionId
  and root-relative locator history.

## Recommended Implementation Order

1. Add dependency-free `ResourceId`, `ResourceRevision`, and manifest value
   types, then add the optional artifact-profile SourceId/SourceRevisionId
   bridge without changing the M0 meaning of `ResourceId`.
2. Add resource manifest storage contracts and tests.
3. Add an in-memory manifest storage or fake for contract tests.
4. Add MDBX-backed resource manifest storage.
5. Add resource-aware document/chunk metadata helpers.
6. Add lexical-first `IResourceImporter` conformance tests for update, failure
   publication, revision-bound SourceRefSummary, and derived-text origin.
7. Add a small dense `ResourceIndexer` composition test with `IDocumentStorage`,
   `IEmbedder`, and `IVectorIndex` as an optional derived-index path.
8. Add targeted reindexing for exact vector search.
9. Add generation-aware stale filtering for binary bucket indexes.
10. Add compaction tasks for compressed bucket lists.
