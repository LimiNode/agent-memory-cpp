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
: A local current-version/reindex record for a `ResourceId`. The first
implementation can model this as `resource_id`, `generation`, an uncompressed
content hash, and a pipeline configuration hash. It adapts to, rather than
replaces, immutable `SourceRevisionId` in artifact-aware profiles.

`ResourceManifest`
: The list of derived keys created from the current resource revision.

`DerivedRecordRef`
: A typed reference to a derived record, for example a chunk id, embedding id,
vector record key, binary bucket key, lexical posting key, or graph node key.

## Suggested Manifest Shape

The first value types should stay dependency-free and live near the domain or
storage contracts.

```cpp
struct ResourceRevision final {
    ResourceId resource_id;
    std::optional<SourceId> source_id;
    std::optional<SourceRevisionId> source_revision_id;
    std::uint64_t generation = 0;
    std::uint64_t content_hash = 0;
    std::uint64_t pipeline_config_hash = 0;
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

The normal replace flow should be:

```text
1. Begin writable transaction where the backend supports it.
2. Load the old manifest by resource_id.
3. Remove or invalidate old derived records listed in the manifest.
4. Store the new resource metadata, content hash, generation, and, when the
   artifact profile is enabled, the validated SourceId/SourceRevisionId mapping.
5. Chunk the new resource.
6. Generate embeddings for the new chunks.
7. Build any signatures, postings, or graph records needed by enabled indexes.
8. Store new chunks, embeddings, and index entries.
9. Store the new manifest.
10. Commit.
```

If content hash and ingestion settings did not change, the reindex operation may
skip expensive work. The skip check must compare both `content_hash` and
`pipeline_config_hash`.

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

## Future Contracts

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
that resource indexing composes storage, embedding, and index contracts rather
than hiding them behind a single large facade.

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

## Recommended Implementation Order

1. Add dependency-free `ResourceId`, `ResourceRevision`, and manifest value
   types, then add the optional artifact-profile SourceId/SourceRevisionId
   bridge without changing the M0 meaning of `ResourceId`.
2. Add resource manifest storage contracts and tests.
3. Add an in-memory manifest storage or fake for contract tests.
4. Add MDBX-backed resource manifest storage.
5. Add resource-aware document/chunk metadata helpers.
6. Add a small `ResourceIndexer` composition test with `IDocumentStorage`,
   `IEmbedder`, and `IVectorIndex`.
7. Add targeted reindexing for exact vector search.
8. Add generation-aware stale filtering for binary bucket indexes.
9. Add compaction tasks for compressed bucket lists.
