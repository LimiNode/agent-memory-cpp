#include <agent_memory.hpp>

#include <iostream>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    class InMemoryDocumentStorage final : public agent_memory::IDocumentStorage {
    public:
        void fail_next_upsert() noexcept {
            m_fail_next_upsert = true;
        }

        void upsert_document(agent_memory::DocumentSnapshot snapshot) override {
            if(m_fail_next_upsert) {
                m_fail_next_upsert = false;
                throw std::runtime_error("simulated document storage failure");
            }

            const bool removed_existing = erase_document(snapshot.document.id);
            (void)removed_existing;

            const auto document_id = snapshot.document.id;
            m_documents[document_id] = std::move(snapshot.document);
            for(auto& chunk : snapshot.chunks) {
                m_chunk_ids_by_document[document_id].push_back(chunk.id);
                m_chunks[chunk.id] = std::move(chunk);
            }
        }

        [[nodiscard]] std::optional<agent_memory::Document> find_document(
            const agent_memory::DocumentId& id
        ) const override {
            const auto it = m_documents.find(id);
            if(it == m_documents.end()) {
                return std::nullopt;
            }
            return it->second;
        }

        [[nodiscard]] std::optional<agent_memory::DocumentChunk> find_chunk(
            const agent_memory::ChunkId& id
        ) const override {
            const auto it = m_chunks.find(id);
            if(it == m_chunks.end()) {
                return std::nullopt;
            }
            return it->second;
        }

        [[nodiscard]] std::vector<agent_memory::DocumentChunk> list_chunks(
            const agent_memory::DocumentId& document_id
        ) const override {
            std::vector<agent_memory::DocumentChunk> chunks;
            const auto ids_it = m_chunk_ids_by_document.find(document_id);
            if(ids_it == m_chunk_ids_by_document.end()) {
                return chunks;
            }

            chunks.reserve(ids_it->second.size());
            for(const auto& chunk_id : ids_it->second) {
                const auto chunk_it = m_chunks.find(chunk_id);
                if(chunk_it != m_chunks.end()) {
                    chunks.push_back(chunk_it->second);
                }
            }
            return chunks;
        }

        [[nodiscard]] bool erase_document(const agent_memory::DocumentId& id) override {
            bool removed = m_documents.erase(id) > 0;
            const auto ids_it = m_chunk_ids_by_document.find(id);
            if(ids_it != m_chunk_ids_by_document.end()) {
                for(const auto& chunk_id : ids_it->second) {
                    removed = m_chunks.erase(chunk_id) > 0 || removed;
                }
                m_chunk_ids_by_document.erase(ids_it);
            }
            return removed;
        }

    private:
        bool m_fail_next_upsert = false;
        std::map<agent_memory::DocumentId, agent_memory::Document> m_documents;
        std::map<agent_memory::ChunkId, agent_memory::DocumentChunk> m_chunks;
        std::map<
            agent_memory::DocumentId,
            std::vector<agent_memory::ChunkId>
        > m_chunk_ids_by_document;
    };

    class InMemoryResourceManifestStorage final
        : public agent_memory::IResourceManifestStorage {
    public:
        void fail_next_upsert() noexcept {
            m_fail_next_upsert = true;
        }

        void upsert_manifest(agent_memory::ResourceManifest manifest) override {
            if(m_fail_next_upsert) {
                m_fail_next_upsert = false;
                throw std::runtime_error("simulated manifest storage failure");
            }

            if(!agent_memory::is_valid_resource_manifest(manifest)) {
                throw std::invalid_argument("invalid resource manifest");
            }

            const auto resource_id = manifest.revision.resource_id;
            m_manifests[resource_id] = std::move(manifest);
        }

        [[nodiscard]] std::optional<agent_memory::ResourceManifest> find_manifest(
            const agent_memory::ResourceId& resource_id
        ) const override {
            const auto it = m_manifests.find(resource_id);
            if(it == m_manifests.end()) {
                return std::nullopt;
            }
            return it->second;
        }

        [[nodiscard]] bool erase_manifest(
            const agent_memory::ResourceId& resource_id
        ) override {
            return m_manifests.erase(resource_id) > 0;
        }

    private:
        bool m_fail_next_upsert = false;
        std::map<agent_memory::ResourceId, agent_memory::ResourceManifest> m_manifests;
    };

    class FailingVectorIndex final : public agent_memory::IVectorIndex {
    public:
        explicit FailingVectorIndex(agent_memory::ExactVectorIndexOptions options)
            : m_inner(std::move(options)) {}

        void fail_on_nth_upsert(std::size_t ordinal) noexcept {
            m_fail_on_upsert_ordinal = ordinal;
            m_upsert_count = 0;
        }

        [[nodiscard]] agent_memory::SimilarityMetric similarity_metric() const noexcept override {
            return m_inner.similarity_metric();
        }

        [[nodiscard]] std::size_t dimension() const noexcept override {
            return m_inner.dimension();
        }

        [[nodiscard]] std::size_t size() const noexcept override {
            return m_inner.size();
        }

        void upsert(agent_memory::VectorRecord record) override {
            ++m_upsert_count;
            if(
                m_fail_on_upsert_ordinal != 0 &&
                m_upsert_count == m_fail_on_upsert_ordinal
            ) {
                m_fail_on_upsert_ordinal = 0;
                throw std::runtime_error("simulated vector index failure");
            }
            m_inner.upsert(std::move(record));
        }

        [[nodiscard]] std::optional<agent_memory::VectorRecord> find(
            const agent_memory::ChunkId& chunk_id
        ) const override {
            return m_inner.find(chunk_id);
        }

        [[nodiscard]] std::vector<agent_memory::VectorSearchResult> search(
            const agent_memory::VectorSearchQuery& query
        ) const override {
            return m_inner.search(query);
        }

        [[nodiscard]] bool erase(const agent_memory::ChunkId& chunk_id) override {
            return m_inner.erase(chunk_id);
        }

        void clear() override {
            m_inner.clear();
        }

    private:
        agent_memory::ExactVectorIndex m_inner;
        std::size_t m_fail_on_upsert_ordinal = 0;
        std::size_t m_upsert_count = 0;
    };

    class FakeEmbedder final : public agent_memory::IEmbedder {
    public:
        [[nodiscard]] const agent_memory::EmbeddingModelInfo& info() const noexcept override {
            return m_info;
        }

        [[nodiscard]] agent_memory::Embedding embed(
            const agent_memory::EmbeddingRequest& request
        ) override {
            if(request.purpose != agent_memory::EmbeddingPurpose::Document) {
                throw std::invalid_argument("fake embedder expects document purpose");
            }

            if(request.text.find("updated") != std::string::npos) {
                return agent_memory::Embedding{{0.0F, 1.0F}};
            }
            return agent_memory::Embedding{{1.0F, 0.0F}};
        }

    private:
        agent_memory::EmbeddingModelInfo m_info{
            "fake-embedder",
            2,
            512,
            agent_memory::SimilarityMetric::DotProduct,
            agent_memory::PoolingMode::Mean,
            false
        };
    };

    agent_memory::Document make_document(
        agent_memory::DocumentId id,
        std::string text
    ) {
        return agent_memory::Document{
            std::move(id),
            agent_memory::SourceKind::Markdown,
            "notes/resource.md",
            std::move(text),
            {}
        };
    }

    agent_memory::DocumentChunk make_chunk(
        agent_memory::ChunkId id,
        const agent_memory::DocumentId& document_id,
        std::size_t offset,
        std::string text
    ) {
        agent_memory::Metadata metadata;
        metadata.set("scope", "resource-indexer");
        return agent_memory::DocumentChunk{
            std::move(id),
            document_id,
            agent_memory::TextRange{offset, text.size()},
            std::move(text),
            std::move(metadata)
        };
    }

    agent_memory::ResourceIndexSnapshot make_snapshot(
        agent_memory::ResourceId resource_id,
        std::uint64_t generation,
        agent_memory::DocumentId document_id,
        std::vector<agent_memory::DocumentChunk> chunks
    ) {
        return agent_memory::ResourceIndexSnapshot{
            agent_memory::ResourceRevision{
                std::move(resource_id),
                generation,
                0xAABBCCDDU + generation,
                0x11223344U
            },
            agent_memory::DocumentSnapshot{
                make_document(document_id, "resource text generation " + std::to_string(generation)),
                std::move(chunks)
            }
        };
    }

} // namespace

int main() {
    InMemoryDocumentStorage document_storage;
    InMemoryResourceManifestStorage manifest_storage;
    FakeEmbedder embedder;
    FailingVectorIndex vector_index(agent_memory::ExactVectorIndexOptions{
        2,
        agent_memory::SimilarityMetric::DotProduct
    });

    agent_memory::ResourceIndexer indexer{
        document_storage,
        manifest_storage,
        embedder,
        vector_index
    };

    const agent_memory::ResourceId resource_id{"resource:indexer"};
    const agent_memory::DocumentId old_document_id{"doc:indexer:old"};
    const agent_memory::ChunkId old_chunk_id{"chunk:indexer:old"};

    indexer.reindex_resource(make_snapshot(
        resource_id,
        1,
        old_document_id,
        {
            make_chunk(old_chunk_id, old_document_id, 0, "initial chunk")
        }
    ));

    const auto first_manifest = manifest_storage.find_manifest(resource_id);
    if(!first_manifest || first_manifest->records.size() != 4) {
        return fail("resource indexer must write document/chunk/embedding/vector manifest");
    }

    if(!document_storage.find_document(old_document_id)) {
        return fail("resource indexer must persist indexed document");
    }

    if(!vector_index.find(old_chunk_id)) {
        return fail("resource indexer must upsert chunk vector record");
    }

    document_storage.fail_next_upsert();
    try {
        indexer.reindex_resource(make_snapshot(
            resource_id,
            2,
            agent_memory::DocumentId{"doc:indexer:failed"},
            {
                make_chunk(
                    agent_memory::ChunkId{"chunk:indexer:failed"},
                    agent_memory::DocumentId{"doc:indexer:failed"},
                    0,
                    "updated chunk"
                )
            }
        ));
        return fail("resource indexer must propagate document storage failures");
    } catch(const std::runtime_error&) {
    }

    if(
        !manifest_storage.find_manifest(resource_id)
        || !document_storage.find_document(old_document_id)
        || !vector_index.find(old_chunk_id)
    ) {
        return fail("failed reindex must preserve the previously published resource state");
    }

    const agent_memory::DocumentId vector_failed_document_id{"doc:indexer:vector-failed"};
    const agent_memory::ChunkId vector_failed_first_chunk_id{"chunk:indexer:vector-failed:first"};
    const agent_memory::ChunkId vector_failed_second_chunk_id{"chunk:indexer:vector-failed:second"};
    vector_index.fail_on_nth_upsert(2);
    try {
        indexer.reindex_resource(make_snapshot(
            resource_id,
            2,
            vector_failed_document_id,
            {
                make_chunk(
                    vector_failed_first_chunk_id,
                    vector_failed_document_id,
                    0,
                    "updated first chunk"
                ),
                make_chunk(
                    vector_failed_second_chunk_id,
                    vector_failed_document_id,
                    20,
                    "updated second chunk"
                )
            }
        ));
        return fail("resource indexer must propagate later vector storage failures");
    } catch(const std::runtime_error&) {
    }

    const auto manifest_after_vector_failure = manifest_storage.find_manifest(resource_id);
    if(
        !manifest_after_vector_failure ||
        manifest_after_vector_failure->revision.generation != 1 ||
        document_storage.find_document(vector_failed_document_id) ||
        vector_index.find(vector_failed_first_chunk_id) ||
        vector_index.find(vector_failed_second_chunk_id) ||
        !document_storage.find_document(old_document_id) ||
        !vector_index.find(old_chunk_id)
    ) {
        return fail("vector failure must restore the previously published resource state");
    }

    const agent_memory::DocumentId manifest_failed_document_id{"doc:indexer:manifest-failed"};
    const agent_memory::ChunkId manifest_failed_chunk_id{"chunk:indexer:manifest-failed"};
    manifest_storage.fail_next_upsert();
    try {
        indexer.reindex_resource(make_snapshot(
            resource_id,
            2,
            manifest_failed_document_id,
            {
                make_chunk(
                    manifest_failed_chunk_id,
                    manifest_failed_document_id,
                    0,
                    "updated chunk"
                )
            }
        ));
        return fail("resource indexer must propagate manifest storage failures");
    } catch(const std::runtime_error&) {
    }

    const auto manifest_after_manifest_failure = manifest_storage.find_manifest(resource_id);
    if(
        !manifest_after_manifest_failure ||
        manifest_after_manifest_failure->revision.generation != 1 ||
        document_storage.find_document(manifest_failed_document_id) ||
        vector_index.find(manifest_failed_chunk_id) ||
        !document_storage.find_document(old_document_id) ||
        !vector_index.find(old_chunk_id)
    ) {
        return fail("manifest failure must restore the previously published resource state");
    }

    const agent_memory::DocumentId new_document_id{"doc:indexer:new"};
    const agent_memory::ChunkId new_chunk_id{"chunk:indexer:new"};

    indexer.reindex_resource(make_snapshot(
        resource_id,
        2,
        new_document_id,
        {
            make_chunk(new_chunk_id, new_document_id, 0, "updated chunk")
        }
    ));

    if(document_storage.find_document(old_document_id)) {
        return fail("resource reindex must remove old document derived from resource");
    }

    if(vector_index.find(old_chunk_id)) {
        return fail("resource reindex must remove old vector record");
    }

    const auto second_manifest = manifest_storage.find_manifest(resource_id);
    if(
        !second_manifest ||
        second_manifest->revision.generation != 2 ||
        second_manifest->records.size() != 4
    ) {
        return fail("resource reindex must replace manifest");
    }

    if(!document_storage.find_document(new_document_id)) {
        return fail("resource reindex must persist replacement document");
    }

    const auto new_vector = vector_index.find(new_chunk_id);
    if(!new_vector || new_vector->embedding.values != std::vector<float>{0.0F, 1.0F}) {
        return fail("resource reindex must persist replacement vector");
    }

    const agent_memory::DocumentId newest_document_id{"doc:indexer:newest"};
    const agent_memory::ChunkId newest_chunk_id{"chunk:indexer:newest"};
    indexer.reindex_resource(make_snapshot(
        resource_id,
        3,
        newest_document_id,
        {
            make_chunk(newest_chunk_id, newest_document_id, 0, "newest chunk")
        }
    ));

    try {
        indexer.reindex_resource(make_snapshot(
            resource_id,
            2,
            agent_memory::DocumentId{"doc:indexer:stale"},
            {
                make_chunk(
                    agent_memory::ChunkId{"chunk:indexer:stale"},
                    agent_memory::DocumentId{"doc:indexer:stale"},
                    0,
                    "stale chunk"
                )
            }
        ));
        return fail("resource indexer must reject a stale resource generation");
    } catch(const std::logic_error&) {
    }

    indexer.reindex_resource(make_snapshot(
        resource_id,
        3,
        agent_memory::DocumentId{"doc:indexer:idempotent"},
        {
            make_chunk(
                agent_memory::ChunkId{"chunk:indexer:idempotent"},
                agent_memory::DocumentId{"doc:indexer:idempotent"},
                0,
                "idempotent chunk"
            )
        }
    ));

    auto conflicting_generation = make_snapshot(
        resource_id,
        3,
        agent_memory::DocumentId{"doc:indexer:conflict"},
        {
            make_chunk(
                agent_memory::ChunkId{"chunk:indexer:conflict"},
                agent_memory::DocumentId{"doc:indexer:conflict"},
                0,
                "conflicting chunk"
            )
        }
    );
    ++conflicting_generation.revision.content_hash;
    try {
        indexer.reindex_resource(std::move(conflicting_generation));
        return fail("resource indexer must reject a conflicting resource generation");
    } catch(const std::logic_error&) {
    }

    const auto newest_manifest = manifest_storage.find_manifest(resource_id);
    if(
        !newest_manifest ||
        newest_manifest->revision.generation != 3 ||
        !document_storage.find_document(newest_document_id) ||
        !vector_index.find(newest_chunk_id) ||
        document_storage.find_document(agent_memory::DocumentId{"doc:indexer:idempotent"})
    ) {
        return fail("stale, conflicting, or idempotent work must not replace the active generation");
    }

    if(!indexer.erase_resource(resource_id)) {
        return fail("resource erase must report removed resource state");
    }

    if(manifest_storage.find_manifest(resource_id)) {
        return fail("resource erase must remove manifest");
    }

    if(document_storage.find_document(newest_document_id)) {
        return fail("resource erase must remove current document");
    }

    if(vector_index.find(newest_chunk_id)) {
        return fail("resource erase must remove current vector record");
    }

    if(indexer.erase_resource(resource_id)) {
        return fail("resource erase of missing resource must report false");
    }

    try {
        indexer.reindex_resource(agent_memory::ResourceIndexSnapshot{});
        return fail("resource indexer must reject empty resource snapshot");
    } catch(const std::invalid_argument&) {
    }

    return 0;
}
