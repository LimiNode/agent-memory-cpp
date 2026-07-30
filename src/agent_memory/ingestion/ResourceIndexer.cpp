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
                    ++expected_ordinal;
                    if(position + 1 < records.size()) {
                        expected_chunk_id = records[position + 1].chunk_id;
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

        void restore_vector_records(
            IVectorIndex& index,
            const std::vector<std::optional<VectorRecord>>& previous,
            const std::vector<VectorRecord>& attempted,
            std::vector<ResourceIndexRecoveryFailure>& recovery_failures
        ) {
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
                }
            }
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
        IEmbedder& embedder,
        IVectorIndex& vector_index
    )
        : m_document_storage(&document_storage)
        , m_manifest_storage(&manifest_storage)
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
        }

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

            restore_vector_records(
                *m_vector_index,
                previous_vectors,
                vector_records,
                recovery_failures
            );

            try {
                restore_document_snapshot(*m_document_storage, document_id, previous_document);
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

        auto erase_pending = *manifest;
        if(is_active_resource_manifest(erase_pending)) {
            erase_pending.state = ResourceManifestState::ErasePending;
            m_manifest_storage->upsert_manifest(erase_pending);
        }

        erase_derived_records(erase_pending);
        m_manifest_storage->erase_manifest(resource_id);
        return true;
    }

    void ResourceIndexer::erase_derived_record(const DerivedRecordRef& record) {
        switch(record.kind) {
        case DerivedRecordKind::Document:
            m_document_storage->erase_document(DocumentId{record.key});
            return;
        case DerivedRecordKind::Chunk:
        case DerivedRecordKind::Embedding:
            // This prototype has no separate physical chunk or embedding store.
            return;
        case DerivedRecordKind::VectorRecord:
            m_vector_index->erase(record.chunk_id);
            return;
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
            erase_derived_record(manifest.pending_reclaim_records.front());

            auto next_manifest = manifest;
            next_manifest.pending_reclaim_records.erase(
                next_manifest.pending_reclaim_records.begin()
            );
            m_manifest_storage->upsert_manifest(next_manifest);
            manifest = std::move(next_manifest);
        }
    }

} // namespace agent_memory
