#include "ResourceIndexer.hpp"

#include <agent_memory/embedding/IEmbedder.hpp>
#include <agent_memory/index/IVectorIndex.hpp>

#include <optional>
#include <stdexcept>
#include <utility>
#include <vector>

namespace agent_memory {

    namespace {

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
            }
        }

        ResourceManifest make_manifest(const ResourceIndexSnapshot& snapshot) {
            ResourceManifest manifest;
            manifest.revision = snapshot.revision;
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

        bool is_retained_by(
            const DerivedRecordRef& old_record,
            const ResourceManifest& new_manifest
        ) {
            for(const auto& new_record : new_manifest.records) {
                if(
                    old_record.kind == new_record.kind
                    && old_record.chunk_id == new_record.chunk_id
                    && old_record.key == new_record.key
                ) {
                    return true;
                }
            }
            return false;
        }

        bool revisions_have_matching_hashes(
            const ResourceRevision& left,
            const ResourceRevision& right
        ) noexcept {
            return matches_revision_hashes(
                left,
                right.content_hash,
                right.pipeline_config_hash
            );
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

        void restore_document_snapshot(
            IDocumentStorage& storage,
            const DocumentId& document_id,
            const std::optional<DocumentSnapshot>& previous
        ) noexcept {
            try {
                if(previous) {
                    storage.upsert_document(*previous);
                } else {
                    const bool removed = storage.erase_document(document_id);
                    (void)removed;
                }
            } catch(...) {
            }
        }

        void restore_vector_records(
            IVectorIndex& index,
            const std::vector<std::optional<VectorRecord>>& previous,
            const std::vector<VectorRecord>& attempted
        ) noexcept {
            for(std::size_t index_position = 0; index_position < attempted.size(); ++index_position) {
                try {
                    if(previous[index_position]) {
                        index.upsert(*previous[index_position]);
                    } else {
                        const bool removed = index.erase(attempted[index_position].chunk_id);
                        (void)removed;
                    }
                } catch(...) {
                }
            }
        }

    } // namespace

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

        auto vector_records = make_vector_records(snapshot.document_snapshot, *m_embedder);

        const auto old_manifest = m_manifest_storage->find_manifest(
            snapshot.revision.resource_id
        );
        if(old_manifest) {
            if(snapshot.revision.generation < old_manifest->revision.generation) {
                throw std::logic_error("ResourceIndexSnapshot generation is stale");
            }

            if(snapshot.revision.generation == old_manifest->revision.generation) {
                if(revisions_have_matching_hashes(snapshot.revision, old_manifest->revision)) {
                    return;
                }
                throw std::logic_error("ResourceIndexSnapshot generation conflicts with active manifest");
            }
        }

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
            m_manifest_storage->upsert_manifest(manifest);
        } catch(...) {
            restore_vector_records(*m_vector_index, previous_vectors, vector_records);
            restore_document_snapshot(*m_document_storage, document_id, previous_document);
            throw;
        }

        if(old_manifest) {
            try {
                reclaim_superseded_derived_records(*old_manifest, manifest);
            } catch(...) {
            }
        }
    }

    bool ResourceIndexer::erase_resource(const ResourceId& resource_id) {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto manifest = m_manifest_storage->find_manifest(resource_id);
        if(!manifest) {
            return false;
        }

        m_manifest_storage->erase_manifest(resource_id);
        try {
            erase_derived_records(*manifest);
        } catch(...) {
        }
        return true;
    }

    void ResourceIndexer::erase_derived_records(const ResourceManifest& manifest) {
        for(const auto& record : manifest.records) {
            if(record.kind == DerivedRecordKind::Document && !record.key.empty()) {
                m_document_storage->erase_document(DocumentId{record.key});
            }

            if(record.kind == DerivedRecordKind::VectorRecord && !record.chunk_id.empty()) {
                m_vector_index->erase(record.chunk_id);
            }
        }
    }

    void ResourceIndexer::reclaim_superseded_derived_records(
        const ResourceManifest& old_manifest,
        const ResourceManifest& new_manifest
    ) {
        ResourceManifest superseded;
        superseded.revision = old_manifest.revision;
        for(const auto& record : old_manifest.records) {
            if(!is_retained_by(record, new_manifest)) {
                superseded.records.push_back(record);
            }
        }
        erase_derived_records(superseded);
    }

} // namespace agent_memory
