#include "ResourceIndexer.hpp"

#include <agent_memory/embedding/IEmbedder.hpp>
#include <agent_memory/index/IVectorIndex.hpp>

#include <algorithm>
#include <map>
#include <optional>
#include <stdexcept>
#include <utility>
#include <vector>

namespace agent_memory {

    namespace {

        constexpr char RESOURCE_INDEXER_MANIFEST_SCHEMA_ID[] =
            "agent_memory.resource_indexer";
        constexpr std::uint32_t RESOURCE_INDEXER_MANIFEST_SCHEMA_VERSION = 1;

        bool is_resource_indexer_schema(const ResourceManifest& manifest) noexcept {
            return manifest.payload_version == ResourceManifestPayloadVersion::V5 &&
                manifest.schema.schema_id == RESOURCE_INDEXER_MANIFEST_SCHEMA_ID &&
                manifest.schema.schema_version == RESOURCE_INDEXER_MANIFEST_SCHEMA_VERSION;
        }

        bool is_resource_indexer_active_record_sequence(
            const std::vector<DerivedRecordRef>& records
        ) noexcept {
            if(
                records.empty() ||
                records.front().kind != DerivedRecordKind::Document ||
                records.front().ordinal != 0
            ) {
                return false;
            }

            if((records.size() - 1) % 3 != 0) {
                return false;
            }

            for(std::size_t record_index = 1, ordinal = 0;
                record_index < records.size();
                record_index += 3, ++ordinal) {
                const auto& chunk = records[record_index];
                const auto& embedding = records[record_index + 1];
                const auto& vector = records[record_index + 2];
                if(
                    chunk.kind != DerivedRecordKind::Chunk ||
                    embedding.kind != DerivedRecordKind::Embedding ||
                    vector.kind != DerivedRecordKind::VectorRecord ||
                    chunk.ordinal != ordinal ||
                    embedding.ordinal != ordinal ||
                    vector.ordinal != ordinal ||
                    chunk.chunk_id.empty() ||
                    chunk.chunk_id != embedding.chunk_id ||
                    chunk.chunk_id != vector.chunk_id
                ) {
                    return false;
                }
            }

            return true;
        }

        int resource_indexer_chunk_record_rank(DerivedRecordKind kind) noexcept {
            switch(kind) {
            case DerivedRecordKind::Chunk:
                return 0;
            case DerivedRecordKind::Embedding:
                return 1;
            case DerivedRecordKind::VectorRecord:
                return 2;
            case DerivedRecordKind::Document:
            case DerivedRecordKind::BinaryBucketPosting:
            case DerivedRecordKind::LexicalPosting:
            case DerivedRecordKind::GraphRecord:
            case DerivedRecordKind::Custom:
                return -1;
            }
            return -1;
        }

        bool is_resource_indexer_pending_reclaim_sequence(
            const std::vector<DerivedRecordRef>& records
        ) noexcept {
            if(records.empty()) {
                return true;
            }

            std::size_t position = 0;
            if(records.front().kind == DerivedRecordKind::Document) {
                if(records.front().ordinal != 0) {
                    return false;
                }
                ++position;
                if(position == records.size()) {
                    return true;
                }
                if(resource_indexer_chunk_record_rank(records[position].kind) != 0) {
                    return false;
                }
            }

            int expected_rank = resource_indexer_chunk_record_rank(records[position].kind);
            if(expected_rank < 0) {
                return false;
            }

            std::uint32_t expected_ordinal = records[position].ordinal;
            ChunkId expected_chunk_id = records[position].chunk_id;
            for(; position < records.size(); ++position) {
                const auto& record = records[position];
                if(
                    resource_indexer_chunk_record_rank(record.kind) != expected_rank ||
                    record.ordinal != expected_ordinal ||
                    record.chunk_id != expected_chunk_id
                ) {
                    return false;
                }

                ++expected_rank;
                if(expected_rank == 3) {
                    expected_rank = 0;
                    if(position + 1 < records.size()) {
                        const auto& next_record = records[position + 1];
                        if(
                            resource_indexer_chunk_record_rank(next_record.kind) != 0 ||
                            next_record.ordinal <= expected_ordinal
                        ) {
                            return false;
                        }
                        expected_ordinal = next_record.ordinal;
                        expected_chunk_id = next_record.chunk_id;
                    }
                }
            }

            return expected_rank == 0;
        }

        void validate_resource_indexer_manifest(
            const ResourceManifest& manifest,
            const ResourceId& expected_resource_id
        ) {
            if(
                !is_valid_resource_manifest(manifest) ||
                manifest.revision.resource_id != expected_resource_id
            ) {
                throw ResourceIndexManifestCompatibilityError(
                    ResourceIndexManifestCompatibilityReason::InvalidTopology,
                    "Stored resource manifest is invalid"
                );
            }

            if(manifest.payload_version != ResourceManifestPayloadVersion::V5) {
                throw ResourceIndexManifestCompatibilityError(
                    ResourceIndexManifestCompatibilityReason::LegacyPayload,
                    "Stored resource manifest requires explicit migration"
                );
            }

            if(!is_resource_indexer_schema(manifest)) {
                throw ResourceIndexManifestCompatibilityError(
                    ResourceIndexManifestCompatibilityReason::ForeignSchema,
                    "Stored resource manifest is not owned by ResourceIndexer"
                );
            }

            if(
                !manifest.revision.body_digest ||
                !is_valid_resource_body_digest(*manifest.revision.body_digest) ||
                manifest.revision.pipeline_config_hash == 0 ||
                !is_resource_indexer_active_record_sequence(manifest.records) ||
                !is_resource_indexer_pending_reclaim_sequence(
                    manifest.pending_reclaim_records
                )
            ) {
                throw ResourceIndexManifestCompatibilityError(
                    ResourceIndexManifestCompatibilityReason::InvalidTopology,
                    "Stored resource manifest is not compatible with ResourceIndexer"
                );
            }
        }

        void validate_resource_index_snapshot(const ResourceIndexSnapshot& snapshot) {
            if(snapshot.revision.resource_id.empty()) {
                throw std::invalid_argument("ResourceIndexSnapshot resource id must not be empty");
            }

            if(
                !snapshot.revision.body_digest ||
                !is_valid_resource_body_digest(*snapshot.revision.body_digest)
            ) {
                throw std::invalid_argument(
                    "ResourceIndexSnapshot body digest must use a supported algorithm"
                );
            }

            if(snapshot.revision.pipeline_config_hash == 0) {
                throw std::invalid_argument(
                    "ResourceIndexSnapshot pipeline configuration hash must not be zero"
                );
            }

            if(snapshot.document_snapshot.document.id.empty()) {
                throw std::invalid_argument("ResourceIndexSnapshot document id must not be empty");
            }

            for(const auto& chunk : snapshot.document_snapshot.chunks) {
                if(chunk.document_id != snapshot.document_snapshot.document.id) {
                    throw std::invalid_argument(
                        "ResourceIndexSnapshot chunks must belong to snapshot document"
                    );
                }

                for(const auto& other_chunk : snapshot.document_snapshot.chunks) {
                    if(&chunk != &other_chunk && chunk.id == other_chunk.id) {
                        throw std::invalid_argument(
                            "ResourceIndexSnapshot chunk ids must be unique"
                        );
                    }
                }
            }
        }

        ResourceManifest make_manifest(const ResourceIndexSnapshot& snapshot) {
            ResourceManifest manifest;
            manifest.revision = snapshot.revision;
            manifest.schema = ResourceManifestSchema{
                RESOURCE_INDEXER_MANIFEST_SCHEMA_ID,
                RESOURCE_INDEXER_MANIFEST_SCHEMA_VERSION
            };
            manifest.payload_version = ResourceManifestPayloadVersion::V5;
            manifest.records.push_back(DerivedRecordRef{
                DerivedRecordKind::Document,
                {},
                snapshot.document_snapshot.document.id.value(),
                0
            });

            std::uint32_t ordinal = 0;
            for(const auto& chunk : snapshot.document_snapshot.chunks) {
                manifest.records.push_back(DerivedRecordRef{
                    DerivedRecordKind::Chunk,
                    chunk.id,
                    {},
                    ordinal
                });
                manifest.records.push_back(DerivedRecordRef{
                    DerivedRecordKind::Embedding,
                    chunk.id,
                    {},
                    ordinal
                });
                manifest.records.push_back(DerivedRecordRef{
                    DerivedRecordKind::VectorRecord,
                    chunk.id,
                    {},
                    ordinal
                });
                ++ordinal;
            }
            return manifest;
        }

        std::vector<VectorRecord> make_vector_records(
            const DocumentSnapshot& snapshot,
            IEmbedder& embedder
        ) {
            std::vector<VectorRecord> records;
            records.reserve(snapshot.chunks.size());
            for(const auto& chunk : snapshot.chunks) {
                auto embedding = embedder.embed(EmbeddingRequest{
                    chunk.text,
                    EmbeddingPurpose::Document
                });
                records.push_back(VectorRecord{
                    chunk.id,
                    std::move(embedding),
                    chunk.metadata
                });
            }
            return records;
        }

        bool vector_records_match_persisted_snapshot(
            const std::vector<VectorRecord>& expected_records,
            const IVectorIndex& index
        ) {
            for(const auto& expected : expected_records) {
                const auto actual = index.find(expected.chunk_id);
                if(
                    !actual ||
                    actual->chunk_id != expected.chunk_id ||
                    actual->embedding.values != expected.embedding.values ||
                    actual->metadata.values() != expected.metadata.values()
                ) {
                    return false;
                }
            }

            return true;
        }

        bool is_retained_by(
            const DerivedRecordRef& old_record,
            const ResourceManifest& new_manifest
        ) {
            for(const auto& new_record : new_manifest.records) {
                if(
                    has_same_derived_record_identity(old_record, new_record)
                ) {
                    return true;
                }
            }
            return false;
        }

        bool manifest_has_document_record(
            const ResourceManifest& manifest,
            const DocumentId& document_id
        ) {
            return std::any_of(
                manifest.records.begin(),
                manifest.records.end(),
                [&document_id](const DerivedRecordRef& record) {
                    return record.kind == DerivedRecordKind::Document &&
                        record.key == document_id.value();
                }
            );
        }

        bool uses_document_owner(const DerivedRecordRef& record) noexcept {
            return record.kind == DerivedRecordKind::Document;
        }

        bool uses_chunk_owner(const DerivedRecordRef& record) noexcept {
            return record.kind == DerivedRecordKind::Chunk ||
                record.kind == DerivedRecordKind::Embedding ||
                record.kind == DerivedRecordKind::VectorRecord;
        }

        bool record_is_physically_absent(
            const IDocumentStorage& document_storage,
            const IVectorIndex& vector_index,
            const DerivedRecordRef& record
        ) {
            if(uses_document_owner(record)) {
                const auto document_id = DocumentId{record.key};
                return !document_storage.find_document(document_id) &&
                    document_storage.list_chunks(document_id).empty();
            }
            if(uses_chunk_owner(record)) {
                return !document_storage.find_chunk(record.chunk_id) &&
                    !vector_index.find(record.chunk_id);
            }
            return true;
        }

        ResourceIndexRecordOwner make_record_owner(const ResourceManifest& manifest) {
            return ResourceIndexRecordOwner{
                manifest.revision.resource_id,
                manifest.revision.generation,
                manifest.schema
            };
        }

        std::optional<ResourceIndexRecordOwner> find_record_owner(
            const IResourceIndexRecordOwnerStorage& storage,
            const DerivedRecordRef& record
        ) {
            if(uses_document_owner(record)) {
                return storage.find_document_owner(DocumentId{record.key});
            }
            if(uses_chunk_owner(record)) {
                return storage.find_chunk_owner(record.chunk_id);
            }
            return std::nullopt;
        }

        void upsert_record_owner(
            IResourceIndexRecordOwnerStorage& storage,
            const DerivedRecordRef& record,
            ResourceIndexRecordOwner owner
        ) {
            if(uses_document_owner(record)) {
                storage.upsert_document_owner(DocumentId{record.key}, std::move(owner));
                return;
            }
            if(uses_chunk_owner(record)) {
                storage.upsert_chunk_owner(record.chunk_id, std::move(owner));
            }
        }

        void erase_record_owner(
            IResourceIndexRecordOwnerStorage& storage,
            const DerivedRecordRef& record
        ) {
            if(uses_document_owner(record)) {
                const bool erased = storage.erase_document_owner(DocumentId{record.key});
                (void)erased;
                return;
            }
            if(record.kind == DerivedRecordKind::VectorRecord) {
                const bool erased = storage.erase_chunk_owner(record.chunk_id);
                (void)erased;
            }
        }

        struct ResourceIndexRecordOwnerSnapshot final {
            DerivedRecordRef record;
            std::optional<ResourceIndexRecordOwner> owner;
        };

        std::vector<ResourceIndexRecordOwnerSnapshot> capture_active_record_owners(
            const ResourceManifest& manifest,
            const IResourceIndexRecordOwnerStorage& storage
        ) {
            std::vector<ResourceIndexRecordOwnerSnapshot> snapshots;
            for(const auto& record : manifest.records) {
                if(record.kind == DerivedRecordKind::Document || record.kind == DerivedRecordKind::Chunk) {
                    snapshots.push_back(ResourceIndexRecordOwnerSnapshot{
                        record,
                        find_record_owner(storage, record)
                    });
                }
            }
            return snapshots;
        }

        void restore_active_record_owners(
            IResourceIndexRecordOwnerStorage& storage,
            const IDocumentStorage& document_storage,
            const IVectorIndex& vector_index,
            const std::vector<ResourceIndexRecordOwnerSnapshot>& snapshots,
            const ResourceIndexRecordOwner& attempted_owner,
            bool attempted_owners_prebound,
            bool document_restore_succeeded,
            const std::vector<ChunkId>& vector_restore_failures,
            std::vector<ResourceIndexRecoveryFailure>& recovery_failures
        ) {
            for(const auto& snapshot : snapshots) {
                const bool physical_rollback_succeeded =
                    document_restore_succeeded &&
                    (
                        !uses_chunk_owner(snapshot.record) ||
                        std::find(
                            vector_restore_failures.begin(),
                            vector_restore_failures.end(),
                            snapshot.record.chunk_id
                        ) == vector_restore_failures.end()
                    );
                auto recovery_operation = ResourceIndexRecoveryOperation::UpsertAttempted;
                try {
                    if(!physical_rollback_succeeded) {
                        if(!attempted_owners_prebound) {
                            upsert_record_owner(storage, snapshot.record, attempted_owner);
                        }
                        continue;
                    }
                    if(snapshot.owner) {
                        recovery_operation = ResourceIndexRecoveryOperation::UpsertPrevious;
                        upsert_record_owner(storage, snapshot.record, *snapshot.owner);
                        continue;
                    }

                    recovery_operation = ResourceIndexRecoveryOperation::VerifyPhysicalAbsence;
                    if(!record_is_physically_absent(
                        document_storage,
                        vector_index,
                        snapshot.record
                    )) {
                        throw ResourceIndexRecordOwnershipError(
                            "Physical record remains while restoring an absent owner binding"
                        );
                    }
                    recovery_operation = ResourceIndexRecoveryOperation::EraseAttempted;
                    if(uses_document_owner(snapshot.record)) {
                        const bool erased = storage.erase_document_owner(
                            DocumentId{snapshot.record.key}
                        );
                        (void)erased;
                    } else if(uses_chunk_owner(snapshot.record)) {
                        const bool erased = storage.erase_chunk_owner(snapshot.record.chunk_id);
                        (void)erased;
                    }
                } catch(...) {
                    recovery_failures.push_back(ResourceIndexRecoveryFailure{
                        ResourceIndexRecoveryStage::OwnerRestore,
                        recovery_operation,
                        uses_document_owner(snapshot.record)
                            ? std::optional<DocumentId>{DocumentId{snapshot.record.key}}
                            : std::nullopt,
                        uses_chunk_owner(snapshot.record)
                            ? std::optional<ChunkId>{snapshot.record.chunk_id}
                            : std::nullopt,
                        std::current_exception()
                    });
                }
            }
        }

        std::string record_owner_label(const DerivedRecordRef& record) {
            if(uses_document_owner(record)) {
                return "document '" + record.key + "'";
            }
            return "chunk '" + record.chunk_id.value() + "'";
        }

        ResourceManifest make_unreclaimed_manifest(const ResourceManifest& manifest) {
            ResourceManifest unreclaimed;
            unreclaimed.revision = manifest.revision;
            unreclaimed.records = manifest.pending_reclaim_records;
            unreclaimed.schema = manifest.schema;
            unreclaimed.payload_version = manifest.payload_version;
            return unreclaimed;
        }

        bool revisions_have_matching_idempotency_evidence(
            const ResourceRevision& left,
            const ResourceRevision& right
        ) noexcept {
            if(
                !left.body_digest ||
                !right.body_digest ||
                !is_valid_resource_body_digest(*left.body_digest) ||
                !is_valid_resource_body_digest(*right.body_digest) ||
                left.pipeline_config_hash == 0 ||
                right.pipeline_config_hash == 0
            ) {
                return false;
            }

            if(
                left.body_digest->algorithm != right.body_digest->algorithm ||
                left.body_digest->bytes != right.body_digest->bytes
            ) {
                return false;
            }

            return left.pipeline_config_hash == right.pipeline_config_hash;
        }

        std::optional<DocumentSnapshot> load_document_snapshot(
            IDocumentStorage& storage,
            const DocumentId& document_id
        ) {
            const auto document = storage.find_document(document_id);
            if(!document) {
                return std::nullopt;
            }

            return DocumentSnapshot{
                *document,
                storage.list_chunks(document_id)
            };
        }

        bool snapshots_are_equal(
            const DocumentSnapshot& left,
            const DocumentSnapshot& right
        ) {
            if(
                left.document.id != right.document.id ||
                left.document.kind != right.document.kind ||
                left.document.source_uri != right.document.source_uri ||
                left.document.text != right.document.text ||
                left.document.metadata.values() != right.document.metadata.values() ||
                left.chunks.size() != right.chunks.size()
            ) {
                return false;
            }

            std::map<ChunkId, const DocumentChunk*> right_chunks_by_id;
            for(const auto& right_chunk : right.chunks) {
                if(!right_chunks_by_id.emplace(right_chunk.id, &right_chunk).second) {
                    return false;
                }
            }

            for(const auto& left_chunk : left.chunks) {
                const auto right_it = right_chunks_by_id.find(left_chunk.id);
                if(right_it == right_chunks_by_id.end()) {
                    return false;
                }

                const auto& right_chunk = *right_it->second;
                if(
                    left_chunk.document_id != right_chunk.document_id ||
                    left_chunk.source_range.offset != right_chunk.source_range.offset ||
                    left_chunk.source_range.length != right_chunk.source_range.length ||
                    left_chunk.text != right_chunk.text ||
                    left_chunk.metadata.values() != right_chunk.metadata.values()
                ) {
                    return false;
                }
            }

            return true;
        }

        bool manifests_have_matching_active_records(
            const ResourceManifest& left,
            const ResourceManifest& right
        ) {
            if(left.records.size() != right.records.size()) {
                return false;
            }

            for(const auto& left_record : left.records) {
                const auto matching_record = std::find_if(
                    right.records.begin(),
                    right.records.end(),
                    [&left_record](const DerivedRecordRef& right_record) {
                        return has_same_derived_record_identity(left_record, right_record)
                            && left_record.ordinal == right_record.ordinal;
                    }
                );
                if(matching_record == right.records.end()) {
                    return false;
                }
            }

            return true;
        }

        void restore_document_snapshot(
            IDocumentStorage& storage,
            const DocumentId& document_id,
            const std::optional<DocumentSnapshot>& previous
        ) {
            if(previous) {
                storage.upsert_document(*previous);
            } else {
                const bool removed = storage.erase_document(document_id);
                (void)removed;
            }
        }

        std::vector<ChunkId> restore_vector_records(
            IVectorIndex& index,
            const std::vector<std::optional<VectorRecord>>& previous,
            const std::vector<VectorRecord>& attempted,
            std::vector<ResourceIndexRecoveryFailure>& recovery_failures
        ) {
            std::vector<ChunkId> failed_chunk_ids;
            for(std::size_t index_position = 0; index_position < attempted.size(); ++index_position) {
                try {
                    if(previous[index_position]) {
                        index.upsert(*previous[index_position]);
                    } else {
                        const bool removed = index.erase(attempted[index_position].chunk_id);
                        (void)removed;
                    }
                } catch(...) {
                    recovery_failures.push_back(ResourceIndexRecoveryFailure{
                        ResourceIndexRecoveryStage::VectorRestore,
                        previous[index_position]
                            ? ResourceIndexRecoveryOperation::UpsertPrevious
                            : ResourceIndexRecoveryOperation::EraseAttempted,
                        std::nullopt,
                        attempted[index_position].chunk_id,
                        std::current_exception()
                    });
                    failed_chunk_ids.push_back(attempted[index_position].chunk_id);
                }
            }
            return failed_chunk_ids;
        }

        bool record_sequence_has_chunk_triple(
            const std::vector<DerivedRecordRef>& records,
            const ChunkId& chunk_id
        ) {
            for(std::size_t index = 0; index + 2 < records.size(); ++index) {
                const auto& chunk = records[index];
                const auto& embedding = records[index + 1];
                const auto& vector = records[index + 2];
                if(
                    chunk.kind == DerivedRecordKind::Chunk &&
                    embedding.kind == DerivedRecordKind::Embedding &&
                    vector.kind == DerivedRecordKind::VectorRecord &&
                    chunk.chunk_id == chunk_id &&
                    embedding.chunk_id == chunk_id &&
                    vector.chunk_id == chunk_id &&
                    chunk.ordinal == embedding.ordinal &&
                    chunk.ordinal == vector.ordinal
                ) {
                    return true;
                }
            }
            return false;
        }

    } // namespace

    ResourceIndexManifestCompatibilityError::ResourceIndexManifestCompatibilityError(
        ResourceIndexManifestCompatibilityReason reason,
        const char* message
    )
        : std::logic_error(message)
        , m_reason(reason) {}

    ResourceIndexManifestCompatibilityReason
    ResourceIndexManifestCompatibilityError::reason() const noexcept {
        return m_reason;
    }

    ResourceIndexRecordOwnershipError::ResourceIndexRecordOwnershipError(
        std::string message
    )
        : std::logic_error(std::move(message)) {}

    ResourceIndexRollbackError::ResourceIndexRollbackError(
        std::exception_ptr original_failure,
        std::vector<ResourceIndexRecoveryFailure> recovery_failures
    )
        : std::runtime_error("ResourceIndexer rollback failed; repair or rebuild derived records")
        , m_original_failure(std::move(original_failure))
        , m_recovery_failures(std::move(recovery_failures)) {}

    const std::exception_ptr& ResourceIndexRollbackError::original_failure() const noexcept {
        return m_original_failure;
    }

    const std::exception_ptr& ResourceIndexRollbackError::rollback_failure() const noexcept {
        static const std::exception_ptr EMPTY_FAILURE;
        if(m_recovery_failures.empty()) {
            return EMPTY_FAILURE;
        }
        return m_recovery_failures.front().failure;
    }

    const std::vector<ResourceIndexRecoveryFailure>&
    ResourceIndexRollbackError::recovery_failures() const noexcept {
        return m_recovery_failures;
    }

    ResourceIndexReclaimBlockedError::ResourceIndexReclaimBlockedError(
        ResourceManifest active_manifest,
        ResourceManifest unreclaimed_manifest,
        std::exception_ptr reclaim_failure
    )
        : std::runtime_error(
            "ResourceIndexer could not reclaim superseded records before publishing a replacement"
        )
        , m_active_manifest(std::move(active_manifest))
        , m_unreclaimed_manifest(std::move(unreclaimed_manifest))
        , m_reclaim_failure(std::move(reclaim_failure)) {}

    const ResourceManifest& ResourceIndexReclaimBlockedError::active_manifest() const noexcept {
        return m_active_manifest;
    }

    const ResourceManifest& ResourceIndexReclaimBlockedError::unreclaimed_manifest() const noexcept {
        return m_unreclaimed_manifest;
    }

    const std::exception_ptr& ResourceIndexReclaimBlockedError::reclaim_failure() const noexcept {
        return m_reclaim_failure;
    }

    ResourceIndexReclaimError::ResourceIndexReclaimError(
        ResourceManifest published_manifest,
        ResourceManifest unreclaimed_manifest,
        std::exception_ptr reclaim_failure
    )
        : std::runtime_error(
            "ResourceIndexer published a replacement manifest but could not reclaim superseded records"
        )
        , m_published_manifest(std::move(published_manifest))
        , m_unreclaimed_manifest(std::move(unreclaimed_manifest))
        , m_reclaim_failure(std::move(reclaim_failure)) {}

    const ResourceManifest& ResourceIndexReclaimError::published_manifest() const noexcept {
        return m_published_manifest;
    }

    const ResourceManifest& ResourceIndexReclaimError::unreclaimed_manifest() const noexcept {
        return m_unreclaimed_manifest;
    }

    const std::exception_ptr& ResourceIndexReclaimError::reclaim_failure() const noexcept {
        return m_reclaim_failure;
    }

    IResourceIndexer::~IResourceIndexer() = default;

    ResourceIndexer::ResourceIndexer(
        IDocumentStorage& document_storage,
        IResourceManifestStorage& manifest_storage,
        IResourceIndexRecordOwnerStorage& owner_storage,
        IEmbedder& embedder,
        IVectorIndex& vector_index
    )
        : m_document_storage(&document_storage)
        , m_manifest_storage(&manifest_storage)
        , m_owner_storage(&owner_storage)
        , m_embedder(&embedder)
        , m_vector_index(&vector_index) {}

    void ResourceIndexer::reindex_resource(ResourceIndexSnapshot snapshot) {
        std::lock_guard<std::mutex> lock(m_mutex);
        validate_resource_index_snapshot(snapshot);

        auto manifest = make_manifest(snapshot);
        if(!is_valid_resource_manifest(manifest)) {
            throw std::invalid_argument("ResourceIndexSnapshot produced invalid manifest");
        }

        auto old_manifest = m_manifest_storage->find_manifest(
            snapshot.revision.resource_id
        );
        if(old_manifest) {
            validate_resource_indexer_manifest(
                *old_manifest,
                snapshot.revision.resource_id
            );
            validate_manifest_record_ownership(*old_manifest);
            validate_document_closure_ownership(*old_manifest);
        }

        if(
            !old_manifest ||
            !manifest_has_document_record(
                *old_manifest,
                snapshot.document_snapshot.document.id
            )
        ) {
            validate_document_closure_ownership(manifest);
        }

        validate_requested_record_ownership(
            manifest,
            old_manifest,
            snapshot.document_snapshot.document.id
        );

        if(old_manifest) {
            if(!is_active_resource_manifest(*old_manifest)) {
                throw std::logic_error(
                    "ResourceIndexSnapshot cannot replace a resource with erase-pending cleanup"
                );
            }

            if(snapshot.revision.generation < old_manifest->revision.generation) {
                throw std::logic_error("ResourceIndexSnapshot generation is stale");
            }

            const bool is_same_generation =
                snapshot.revision.generation == old_manifest->revision.generation;
            if(is_same_generation) {
                const auto active_snapshot = load_document_snapshot(
                    *m_document_storage,
                    snapshot.document_snapshot.document.id
                );
                if(
                    !revisions_have_matching_idempotency_evidence(
                        snapshot.revision,
                        old_manifest->revision
                    ) ||
                    !manifests_have_matching_active_records(manifest, *old_manifest) ||
                    !active_snapshot ||
                    !snapshots_are_equal(snapshot.document_snapshot, *active_snapshot)
                ) {
                    throw std::logic_error(
                        "ResourceIndexSnapshot generation conflicts with active manifest"
                    );
                }

                const auto expected_vector_records = make_vector_records(
                    snapshot.document_snapshot,
                    *m_embedder
                );
                if(!vector_records_match_persisted_snapshot(expected_vector_records, *m_vector_index)) {
                    throw std::logic_error(
                        "ResourceIndexSnapshot generation conflicts with persisted vector records"
                    );
                }
            }

            if(!old_manifest->pending_reclaim_records.empty()) {
                try {
                    drain_pending_reclaim_records(*old_manifest);
                } catch(...) {
                    const auto failure = std::current_exception();
                    if(is_same_generation) {
                        throw ResourceIndexReclaimError(
                            *old_manifest,
                            make_unreclaimed_manifest(*old_manifest),
                            failure
                        );
                    }

                    throw ResourceIndexReclaimBlockedError(
                        *old_manifest,
                        make_unreclaimed_manifest(*old_manifest),
                        failure
                    );
                }
            }

            if(is_same_generation) {
                return;
            }
        }

        auto vector_records = make_vector_records(snapshot.document_snapshot, *m_embedder);

        const auto previous_document = load_document_snapshot(
            *m_document_storage,
            snapshot.document_snapshot.document.id
        );

        std::vector<std::optional<VectorRecord>> previous_vectors;
        previous_vectors.reserve(vector_records.size());
        for(const auto& record : vector_records) {
            previous_vectors.push_back(m_vector_index->find(record.chunk_id));
        }

        const auto previous_owners = capture_active_record_owners(manifest, *m_owner_storage);
        const auto attempted_owner = make_record_owner(manifest);
        try {
            upsert_active_record_owners(manifest);
        } catch(...) {
            const auto original_failure = std::current_exception();
            std::vector<ResourceIndexRecoveryFailure> recovery_failures;
            restore_active_record_owners(
                *m_owner_storage,
                *m_document_storage,
                *m_vector_index,
                previous_owners,
                attempted_owner,
                false,
                true,
                {},
                recovery_failures
            );
            if(!recovery_failures.empty()) {
                throw ResourceIndexRollbackError(
                    original_failure,
                    std::move(recovery_failures)
                );
            }
            std::rethrow_exception(original_failure);
        }

        const auto document_id = snapshot.document_snapshot.document.id;
        try {
            m_document_storage->upsert_document(std::move(snapshot.document_snapshot));
            for(const auto& record : vector_records) {
                m_vector_index->upsert(record);
            }
            if(old_manifest) {
                for(const auto& record : old_manifest->records) {
                    if(!is_retained_by(record, manifest)) {
                        manifest.pending_reclaim_records.push_back(record);
                    }
                }
            }
            m_manifest_storage->upsert_manifest(manifest);
        } catch(...) {
            const auto original_failure = std::current_exception();
            std::vector<ResourceIndexRecoveryFailure> recovery_failures;

            const auto vector_restore_failures = restore_vector_records(
                *m_vector_index,
                previous_vectors,
                vector_records,
                recovery_failures
            );

            bool document_restore_succeeded = false;
            try {
                restore_document_snapshot(*m_document_storage, document_id, previous_document);
                document_restore_succeeded = true;
            } catch(...) {
                recovery_failures.push_back(ResourceIndexRecoveryFailure{
                    ResourceIndexRecoveryStage::DocumentRestore,
                    previous_document
                        ? ResourceIndexRecoveryOperation::UpsertPrevious
                        : ResourceIndexRecoveryOperation::EraseAttempted,
                    document_id,
                    std::nullopt,
                    std::current_exception()
                });
            }

            restore_active_record_owners(
                *m_owner_storage,
                *m_document_storage,
                *m_vector_index,
                previous_owners,
                attempted_owner,
                true,
                document_restore_succeeded,
                vector_restore_failures,
                recovery_failures
            );

            if(!recovery_failures.empty()) {
                throw ResourceIndexRollbackError(
                    original_failure,
                    std::move(recovery_failures)
                );
            }

            std::rethrow_exception(original_failure);
        }

        if(old_manifest && !manifest.pending_reclaim_records.empty()) {
            try {
                drain_pending_reclaim_records(manifest);
            } catch(...) {
                throw ResourceIndexReclaimError(
                    manifest,
                    make_unreclaimed_manifest(manifest),
                    std::current_exception()
                );
            }
        }
    }

    bool ResourceIndexer::erase_resource(const ResourceId& resource_id) {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto manifest = m_manifest_storage->find_manifest(resource_id);
        if(!manifest) {
            return false;
        }

        validate_resource_indexer_manifest(*manifest, resource_id);
        validate_manifest_cleanup_preflight(*manifest);

        auto erase_pending = *manifest;
        if(is_active_resource_manifest(erase_pending)) {
            erase_pending.state = ResourceManifestState::ErasePending;
            m_manifest_storage->upsert_manifest(erase_pending);
        }

        validate_manifest_cleanup_preflight(erase_pending);
        erase_derived_records(erase_pending);
        m_manifest_storage->erase_manifest(resource_id);
        return true;
    }

    void ResourceIndexer::erase_derived_record(const DerivedRecordRef& record) {
        switch(record.kind) {
        case DerivedRecordKind::Document: {
            const bool erased = m_document_storage->erase_document(DocumentId{record.key});
            (void)erased;
            if(!is_record_physically_absent(record)) {
                throw ResourceIndexRecordOwnershipError(
                    "Document record remains after erase attempt: " + record_owner_label(record)
                );
            }
            erase_record_owner(*m_owner_storage, record);
            return;
        }
        case DerivedRecordKind::Chunk:
        case DerivedRecordKind::Embedding:
            // This prototype has no separate physical chunk or embedding store.
            return;
        case DerivedRecordKind::VectorRecord: {
            const bool erased = m_vector_index->erase(record.chunk_id);
            (void)erased;
            if(!is_record_physically_absent(record)) {
                throw ResourceIndexRecordOwnershipError(
                    "Chunk record remains after erase attempt: " + record_owner_label(record)
                );
            }
            erase_record_owner(*m_owner_storage, record);
            return;
        }
        case DerivedRecordKind::BinaryBucketPosting:
        case DerivedRecordKind::LexicalPosting:
        case DerivedRecordKind::GraphRecord:
        case DerivedRecordKind::Custom:
            throw std::logic_error(
                "ResourceIndexer cannot reclaim a record it does not own"
            );
        }

        throw std::logic_error("ResourceIndexer encountered an unknown record kind");
    }

    void ResourceIndexer::erase_derived_records(const ResourceManifest& manifest) {
        for(const auto& record : manifest.records) {
            erase_derived_record(record);
        }

        for(const auto& record : manifest.pending_reclaim_records) {
            erase_derived_record(record);
        }
    }

    void ResourceIndexer::drain_pending_reclaim_records(ResourceManifest& manifest) {
        while(!manifest.pending_reclaim_records.empty()) {
            validate_manifest_cleanup_preflight(manifest);
            erase_derived_record(manifest.pending_reclaim_records.front());

            auto next_manifest = manifest;
            next_manifest.pending_reclaim_records.erase(
                next_manifest.pending_reclaim_records.begin()
            );
            m_manifest_storage->upsert_manifest(next_manifest);
            manifest = std::move(next_manifest);
        }
    }

    void ResourceIndexer::validate_manifest_cleanup_preflight(
        const ResourceManifest& manifest
    ) const {
        validate_manifest_record_ownership(manifest);
        validate_document_closure_ownership(manifest);
    }

    void ResourceIndexer::validate_manifest_record_ownership(
        const ResourceManifest& manifest
    ) const {
        for(const auto& record : manifest.records) {
            if(!uses_document_owner(record) && !uses_chunk_owner(record)) {
                continue;
            }

            const auto owner = find_record_owner(*m_owner_storage, record);
            if(!owner) {
                if(
                    manifest.state == ResourceManifestState::ErasePending &&
                    is_record_physically_absent(record)
                ) {
                    continue;
                }
                throw ResourceIndexRecordOwnershipError(
                    "Active resource record has no ownership binding: " +
                    record_owner_label(record)
                );
            }
            if(!is_valid_resource_index_record_owner(*owner)) {
                throw ResourceIndexRecordOwnershipError(
                    "Active resource record has an invalid ownership binding: " +
                    record_owner_label(record)
                );
            }
            if(owner->resource_id != manifest.revision.resource_id) {
                throw ResourceIndexRecordOwnershipError(
                    "Active resource record belongs to another resource: " +
                    record_owner_label(record)
                );
            }
            if(owner->generation != manifest.revision.generation) {
                throw ResourceIndexRecordOwnershipError(
                    "Active resource record has a mismatched generation: " +
                    record_owner_label(record)
                );
            }
            if(
                owner->manifest_schema.schema_id != manifest.schema.schema_id ||
                owner->manifest_schema.schema_version != manifest.schema.schema_version
            ) {
                throw ResourceIndexRecordOwnershipError(
                    "Active resource record has a mismatched owner schema: " +
                    record_owner_label(record)
                );
            }
        }

        std::optional<std::uint64_t> pending_generation;
        for(const auto& record : manifest.pending_reclaim_records) {
            if(!uses_document_owner(record) && !uses_chunk_owner(record)) {
                continue;
            }

            const auto owner = find_record_owner(*m_owner_storage, record);
            if(!owner) {
                if(is_record_physically_absent(record)) {
                    continue;
                }
                throw ResourceIndexRecordOwnershipError(
                    "Pending resource record has no ownership binding but still exists: " +
                    record_owner_label(record)
                );
            }

            if(
                !is_valid_resource_index_record_owner(*owner) ||
                owner->resource_id != manifest.revision.resource_id ||
                owner->generation >= manifest.revision.generation ||
                owner->manifest_schema.schema_id != manifest.schema.schema_id ||
                owner->manifest_schema.schema_version != manifest.schema.schema_version
            ) {
                throw ResourceIndexRecordOwnershipError(
                    "Pending resource record is not owned by an older manifest revision: " +
                    record_owner_label(record)
                );
            }

            if(!is_record_physically_absent(record)) {
                if(
                    pending_generation &&
                    *pending_generation != owner->generation
                ) {
                    throw ResourceIndexRecordOwnershipError(
                        "Pending resource records span multiple manifest generations: " +
                        record_owner_label(record)
                    );
                }
                pending_generation = owner->generation;
            }
        }

        for(const auto& record : manifest.pending_reclaim_records) {
            if(!uses_document_owner(record) && !uses_chunk_owner(record)) {
                continue;
            }

            const auto owner = find_record_owner(*m_owner_storage, record);
            if(!owner) {
                continue;
            }
            if(!pending_generation) {
                throw ResourceIndexRecordOwnershipError(
                    "Pending owner generation cannot be proven from physical records: " +
                    record_owner_label(record)
                );
            }
            if(owner->generation != *pending_generation) {
                throw ResourceIndexRecordOwnershipError(
                    "Pending owner has a generation different from physical reclaim evidence: " +
                    record_owner_label(record)
                );
            }
        }
    }

    void ResourceIndexer::validate_requested_record_ownership(
        const ResourceManifest& manifest,
        const std::optional<ResourceManifest>& active_manifest,
        const DocumentId& requested_document_id
    ) const {
        for(const auto& record : manifest.records) {
            if(record.kind != DerivedRecordKind::Document && record.kind != DerivedRecordKind::Chunk) {
                continue;
            }

            const auto owner = find_record_owner(*m_owner_storage, record);
            if(!owner) {
                if(!is_record_physically_absent(record)) {
                    throw ResourceIndexRecordOwnershipError(
                        "Requested physical record has no ownership binding but already exists: " +
                        record_owner_label(record)
                    );
                }
                continue;
            }

            if(
                !is_valid_resource_index_record_owner(*owner) ||
                owner->resource_id != manifest.revision.resource_id ||
                owner->manifest_schema.schema_id != manifest.schema.schema_id ||
                owner->manifest_schema.schema_version != manifest.schema.schema_version
            ) {
                throw ResourceIndexRecordOwnershipError(
                    "Requested physical record belongs to another resource owner: " +
                    record_owner_label(record)
                );
            }

            if(owner->generation == manifest.revision.generation) {
                continue;
            }

            if(
                active_manifest &&
                owner->generation == active_manifest->revision.generation &&
                is_retained_by(record, *active_manifest)
            ) {
                if(record.kind == DerivedRecordKind::Chunk) {
                    const auto stored_chunk = m_document_storage->find_chunk(record.chunk_id);
                    if(!stored_chunk || stored_chunk->document_id != requested_document_id) {
                        throw ResourceIndexRecordOwnershipError(
                            "Retained chunk does not belong to the requested document: " +
                            record_owner_label(record)
                        );
                    }
                }
                continue;
            }

            throw ResourceIndexRecordOwnershipError(
                "Requested physical record is not owned by the active resource revision: " +
                record_owner_label(record)
            );
        }
    }

    void ResourceIndexer::validate_document_closure_ownership(
        const ResourceManifest& manifest
    ) const {
        const auto validate_record_sequence = [this, &manifest](
            const std::vector<DerivedRecordRef>& records,
            bool is_pending_reclaim
        ) {
            for(const auto& record : records) {
                if(record.kind != DerivedRecordKind::Document) {
                    continue;
                }

                const auto document_id = DocumentId{record.key};
                std::uint64_t expected_generation = manifest.revision.generation;
                if(
                    is_pending_reclaim &&
                    !is_record_physically_absent(record)
                ) {
                    const auto document_owner = m_owner_storage->find_document_owner(
                        document_id
                    );
                    if(
                        !document_owner ||
                        !is_valid_resource_index_record_owner(*document_owner) ||
                        document_owner->resource_id != manifest.revision.resource_id ||
                        document_owner->generation >= manifest.revision.generation ||
                        document_owner->manifest_schema.schema_id != manifest.schema.schema_id ||
                        document_owner->manifest_schema.schema_version !=
                            manifest.schema.schema_version
                    ) {
                        throw ResourceIndexRecordOwnershipError(
                            "Pending document has no compatible ownership binding: '" +
                            document_id.value() + "'"
                        );
                    }
                    expected_generation = document_owner->generation;
                }
                for(const auto& chunk : m_document_storage->list_chunks(document_id)) {
                    if(chunk.document_id != document_id) {
                        throw ResourceIndexRecordOwnershipError(
                            "Document closure contains a chunk with a mismatched parent: '" +
                            chunk.id.value() + "'"
                        );
                    }

                    const auto owner = m_owner_storage->find_chunk_owner(chunk.id);
                    const bool matching_generation = owner &&
                        owner->generation == expected_generation;
                    if(
                        !owner ||
                        !is_valid_resource_index_record_owner(*owner) ||
                        owner->resource_id != manifest.revision.resource_id ||
                        !matching_generation ||
                        owner->manifest_schema.schema_id != manifest.schema.schema_id ||
                        owner->manifest_schema.schema_version != manifest.schema.schema_version ||
                        !record_sequence_has_chunk_triple(records, chunk.id)
                    ) {
                        throw ResourceIndexRecordOwnershipError(
                            "Document closure contains an unowned or undeclared chunk: '" +
                            chunk.id.value() + "'"
                        );
                    }
                }
            }
        };

        validate_record_sequence(manifest.records, false);
        validate_record_sequence(manifest.pending_reclaim_records, true);
    }

    bool ResourceIndexer::is_record_physically_absent(const DerivedRecordRef& record) const {
        return record_is_physically_absent(*m_document_storage, *m_vector_index, record);
    }

    void ResourceIndexer::upsert_active_record_owners(const ResourceManifest& manifest) {
        const auto owner = make_record_owner(manifest);
        for(const auto& record : manifest.records) {
            if(record.kind == DerivedRecordKind::Document || record.kind == DerivedRecordKind::Chunk) {
                upsert_record_owner(*m_owner_storage, record, owner);
            }
        }
    }

} // namespace agent_memory
