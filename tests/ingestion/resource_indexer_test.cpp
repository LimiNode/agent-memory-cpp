#include <agent_memory.hpp>

#include <algorithm>
#include <functional>
#include <iostream>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
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

        void fail_on_nth_erase(std::size_t ordinal) noexcept {
            m_fail_on_erase_ordinal = ordinal;
            m_erase_count = 0;
        }

        void set_chunk_order_by_id(bool enabled) noexcept {
            m_order_chunks_by_id = enabled;
        }

        void reset_operation_counts() noexcept {
            m_upsert_count = 0;
            m_erase_count = 0;
        }

        [[nodiscard]] std::size_t upsert_count() const noexcept {
            return m_upsert_count;
        }

        [[nodiscard]] std::size_t erase_count() const noexcept {
            return m_erase_count;
        }

        void inject_unchecked_chunk(agent_memory::DocumentChunk chunk) {
            if(!find_document(chunk.document_id)) {
                throw std::invalid_argument("injected chunk requires an existing document");
            }
            m_chunk_ids_by_document[chunk.document_id].push_back(chunk.id);
            m_chunks[chunk.id] = std::move(chunk);
        }

        void upsert_document(agent_memory::DocumentSnapshot snapshot) override {
            ++m_upsert_count;
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
            if(m_order_chunks_by_id) {
                std::sort(
                    chunks.begin(),
                    chunks.end(),
                    [](const agent_memory::DocumentChunk& left,
                       const agent_memory::DocumentChunk& right) {
                        return left.id < right.id;
                    }
                );
            }
            return chunks;
        }

        [[nodiscard]] bool erase_document(const agent_memory::DocumentId& id) override {
            ++m_erase_count;
            if(
                m_fail_on_erase_ordinal != 0
                && m_erase_count == m_fail_on_erase_ordinal
            ) {
                m_fail_on_erase_ordinal = 0;
                throw std::runtime_error("simulated document erase failure");
            }

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
        std::size_t m_upsert_count = 0;
        std::size_t m_fail_on_erase_ordinal = 0;
        std::size_t m_erase_count = 0;
        bool m_order_chunks_by_id = false;
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

        void fail_next_erase() noexcept {
            m_fail_next_erase = true;
        }

        void set_upsert_hook(std::function<void()> hook) {
            m_upsert_hook = std::move(hook);
        }

        void reset_operation_counts() noexcept {
            m_upsert_count = 0;
            m_erase_count = 0;
        }

        [[nodiscard]] std::size_t upsert_count() const noexcept {
            return m_upsert_count;
        }

        [[nodiscard]] std::size_t erase_count() const noexcept {
            return m_erase_count;
        }

        void inject_unchecked_manifest(agent_memory::ResourceManifest manifest) {
            m_manifests[manifest.revision.resource_id] = std::move(manifest);
        }

        void upsert_manifest(agent_memory::ResourceManifest manifest) override {
            ++m_upsert_count;
            if(m_fail_next_upsert) {
                m_fail_next_upsert = false;
                throw std::runtime_error("simulated manifest storage failure");
            }

            if(!agent_memory::is_valid_resource_manifest(manifest)) {
                throw std::invalid_argument("invalid resource manifest");
            }

            const auto resource_id = manifest.revision.resource_id;
            m_manifests[resource_id] = std::move(manifest);
            if(m_upsert_hook) {
                auto hook = std::move(m_upsert_hook);
                hook();
            }
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
            ++m_erase_count;
            if(m_fail_next_erase) {
                m_fail_next_erase = false;
                throw std::runtime_error("simulated manifest erase failure");
            }
            return m_manifests.erase(resource_id) > 0;
        }

    private:
        bool m_fail_next_upsert = false;
        bool m_fail_next_erase = false;
        std::function<void()> m_upsert_hook;
        std::size_t m_upsert_count = 0;
        std::size_t m_erase_count = 0;
        std::map<agent_memory::ResourceId, agent_memory::ResourceManifest> m_manifests;
    };

    class InMemoryResourceIndexRecordOwnerStorage final
        : public agent_memory::IResourceIndexRecordOwnerStorage {
    public:
        void fail_next_upsert() noexcept {
            m_fail_next_upsert = true;
        }

        void fail_on_nth_upsert(std::size_t ordinal) noexcept {
            m_fail_on_upsert_ordinal = ordinal;
            m_upsert_attempt_count = 0;
        }

        void clear_upsert_failure() noexcept {
            m_fail_next_upsert = false;
            m_fail_on_upsert_ordinal = 0;
        }

        void reset_operation_counts() noexcept {
            m_document_upsert_count = 0;
            m_chunk_upsert_count = 0;
            m_document_erase_count = 0;
            m_chunk_erase_count = 0;
        }

        [[nodiscard]] std::size_t mutation_count() const noexcept {
            return m_document_upsert_count + m_chunk_upsert_count +
                m_document_erase_count + m_chunk_erase_count;
        }

        [[nodiscard]] std::optional<agent_memory::ResourceIndexRecordOwner> find_document_owner(
            const agent_memory::DocumentId& document_id
        ) const override {
            const auto it = m_document_owners.find(document_id);
            if(it == m_document_owners.end()) {
                return std::nullopt;
            }
            return it->second;
        }

        [[nodiscard]] std::optional<agent_memory::ResourceIndexRecordOwner> find_chunk_owner(
            const agent_memory::ChunkId& chunk_id
        ) const override {
            const auto it = m_chunk_owners.find(chunk_id);
            if(it == m_chunk_owners.end()) {
                return std::nullopt;
            }
            return it->second;
        }

        void upsert_document_owner(
            agent_memory::DocumentId document_id,
            agent_memory::ResourceIndexRecordOwner owner
        ) override {
            ++m_document_upsert_count;
            if(consume_upsert_failure()) {
                throw std::runtime_error("simulated document owner storage failure");
            }
            if(!agent_memory::is_valid_resource_index_record_owner(owner)) {
                throw std::invalid_argument("invalid document owner");
            }
            m_document_owners[std::move(document_id)] = std::move(owner);
        }

        void upsert_chunk_owner(
            agent_memory::ChunkId chunk_id,
            agent_memory::ResourceIndexRecordOwner owner
        ) override {
            ++m_chunk_upsert_count;
            if(consume_upsert_failure()) {
                throw std::runtime_error("simulated chunk owner storage failure");
            }
            if(!agent_memory::is_valid_resource_index_record_owner(owner)) {
                throw std::invalid_argument("invalid chunk owner");
            }
            m_chunk_owners[std::move(chunk_id)] = std::move(owner);
        }

        [[nodiscard]] bool erase_document_owner(
            const agent_memory::DocumentId& document_id
        ) override {
            ++m_document_erase_count;
            return m_document_owners.erase(document_id) > 0;
        }

        [[nodiscard]] bool erase_chunk_owner(const agent_memory::ChunkId& chunk_id) override {
            ++m_chunk_erase_count;
            return m_chunk_owners.erase(chunk_id) > 0;
        }

    private:
        bool consume_upsert_failure() noexcept {
            ++m_upsert_attempt_count;
            if(m_fail_next_upsert) {
                m_fail_next_upsert = false;
                return true;
            }
            if(
                m_fail_on_upsert_ordinal != 0 &&
                m_upsert_attempt_count == m_fail_on_upsert_ordinal
            ) {
                m_fail_on_upsert_ordinal = 0;
                return true;
            }
            return false;
        }

        bool m_fail_next_upsert = false;
        std::size_t m_fail_on_upsert_ordinal = 0;
        std::size_t m_upsert_attempt_count = 0;
        std::size_t m_document_upsert_count = 0;
        std::size_t m_chunk_upsert_count = 0;
        std::size_t m_document_erase_count = 0;
        std::size_t m_chunk_erase_count = 0;
        std::map<agent_memory::DocumentId, agent_memory::ResourceIndexRecordOwner>
            m_document_owners;
        std::map<agent_memory::ChunkId, agent_memory::ResourceIndexRecordOwner>
            m_chunk_owners;
    };

    class FailingVectorIndex final : public agent_memory::IVectorIndex {
    public:
        explicit FailingVectorIndex(agent_memory::ExactVectorIndexOptions options)
            : m_inner(std::move(options)) {}

        void fail_on_nth_upsert(std::size_t ordinal) noexcept {
            m_fail_on_upsert_ordinal = ordinal;
            m_upsert_count = 0;
        }

        void fail_next_erase() noexcept {
            m_fail_next_erase = true;
        }

        void fail_next_upsert() noexcept {
            m_fail_next_upsert = true;
        }

        void set_upsert_failure_hook(std::function<void()> hook) {
            m_upsert_failure_hook = std::move(hook);
        }

        void reset_operation_counts() noexcept {
            m_upsert_count = 0;
            m_erase_count = 0;
        }

        [[nodiscard]] std::size_t upsert_count() const noexcept {
            return m_upsert_count;
        }

        [[nodiscard]] std::size_t erase_count() const noexcept {
            return m_erase_count;
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
            if(m_fail_next_upsert) {
                m_fail_next_upsert = false;
                throw std::runtime_error("simulated vector index failure");
            }
            if(
                m_fail_on_upsert_ordinal != 0 &&
                m_upsert_count == m_fail_on_upsert_ordinal
            ) {
                m_fail_on_upsert_ordinal = 0;
                if(m_upsert_failure_hook) {
                    auto hook = std::move(m_upsert_failure_hook);
                    hook();
                }
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
            ++m_erase_count;
            if(m_fail_next_erase) {
                m_fail_next_erase = false;
                throw std::runtime_error("simulated vector erase failure");
            }
            return m_inner.erase(chunk_id);
        }

        void clear() override {
            m_inner.clear();
        }

    private:
        agent_memory::ExactVectorIndex m_inner;
        std::size_t m_fail_on_upsert_ordinal = 0;
        std::size_t m_upsert_count = 0;
        std::size_t m_erase_count = 0;
        bool m_fail_next_upsert = false;
        bool m_fail_next_erase = false;
        std::function<void()> m_upsert_failure_hook;
    };

    class FakeEmbedder final : public agent_memory::IEmbedder {
    public:
        void reset_call_count() noexcept {
            m_call_count = 0;
        }

        [[nodiscard]] std::size_t call_count() const noexcept {
            return m_call_count;
        }

        [[nodiscard]] const agent_memory::EmbeddingModelInfo& info() const noexcept override {
            return m_info;
        }

        [[nodiscard]] agent_memory::Embedding embed(
            const agent_memory::EmbeddingRequest& request
        ) override {
            ++m_call_count;
            if(request.purpose != agent_memory::EmbeddingPurpose::Document) {
                throw std::invalid_argument("fake embedder expects document purpose");
            }

            if(request.text.find("updated") != std::string::npos) {
                return agent_memory::Embedding{{0.0F, 1.0F}};
            }
            return agent_memory::Embedding{{1.0F, 0.0F}};
        }

    private:
        std::size_t m_call_count = 0;
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

    agent_memory::ResourceBodyDigest make_body_digest(std::uint8_t value) {
        agent_memory::ResourceBodyDigest digest;
        digest.bytes.fill(value);
        return digest;
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
                0x11223344U,
                make_body_digest(static_cast<std::uint8_t>(generation))
            },
            agent_memory::DocumentSnapshot{
                make_document(document_id, "resource text generation " + std::to_string(generation)),
                std::move(chunks)
            }
        };
    }

} // namespace

int main() {
    static_assert(!std::is_copy_constructible<agent_memory::ResourceIndexer>::value);
    static_assert(!std::is_copy_assignable<agent_memory::ResourceIndexer>::value);
    static_assert(!std::is_move_constructible<agent_memory::ResourceIndexer>::value);
    static_assert(!std::is_move_assignable<agent_memory::ResourceIndexer>::value);

    InMemoryDocumentStorage document_storage;
    InMemoryResourceManifestStorage manifest_storage;
    InMemoryResourceIndexRecordOwnerStorage owner_storage;
    FakeEmbedder embedder;
    FailingVectorIndex vector_index(agent_memory::ExactVectorIndexOptions{
        2,
        agent_memory::SimilarityMetric::DotProduct
    });

    agent_memory::ResourceIndexer indexer{
        document_storage,
        manifest_storage,
        owner_storage,
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
        owner_storage.find_document_owner(manifest_failed_document_id) ||
        owner_storage.find_chunk_owner(manifest_failed_chunk_id) ||
        !document_storage.find_document(old_document_id) ||
        !vector_index.find(old_chunk_id)
    ) {
        return fail("manifest failure must restore records and ownership bindings");
    }

    const agent_memory::ResourceId prebind_failure_resource_id{
        "resource:indexer:prebind-failure"
    };
    const agent_memory::DocumentId prebind_failure_document_id{
        "doc:indexer:prebind-failure"
    };
    const agent_memory::ChunkId prebind_failure_chunk_id{
        "chunk:indexer:prebind-failure"
    };
    document_storage.reset_operation_counts();
    manifest_storage.reset_operation_counts();
    owner_storage.reset_operation_counts();
    vector_index.reset_operation_counts();
    owner_storage.fail_on_nth_upsert(2);
    try {
        indexer.reindex_resource(make_snapshot(
            prebind_failure_resource_id,
            1,
            prebind_failure_document_id,
            {make_chunk(
                prebind_failure_chunk_id,
                prebind_failure_document_id,
                0,
                "prebind failure"
            )}
        ));
        return fail("owner prebinding failure must be reported before physical writes");
    } catch(const std::runtime_error&) {
    }
    if(
        document_storage.upsert_count() != 0 ||
        document_storage.erase_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        vector_index.upsert_count() != 0 ||
        vector_index.erase_count() != 0 ||
        document_storage.find_document(prebind_failure_document_id) ||
        vector_index.find(prebind_failure_chunk_id) ||
        owner_storage.find_document_owner(prebind_failure_document_id) ||
        owner_storage.find_chunk_owner(prebind_failure_chunk_id)
    ) {
        return fail("failed owner prebinding must leave physical state untouched");
    }
    owner_storage.clear_upsert_failure();

    const agent_memory::ResourceId prebound_owner_resource_id{
        "resource:indexer:prebound-owner"
    };
    const agent_memory::DocumentId prebound_owner_document_id{
        "doc:indexer:prebound-owner"
    };
    const agent_memory::ChunkId prebound_owner_first_chunk_id{
        "chunk:indexer:prebound-owner:first"
    };
    const agent_memory::ChunkId prebound_owner_second_chunk_id{
        "chunk:indexer:prebound-owner:second"
    };
    vector_index.fail_on_nth_upsert(2);
    vector_index.fail_next_erase();
    vector_index.set_upsert_failure_hook([&owner_storage] {
        owner_storage.fail_next_upsert();
    });
    try {
        indexer.reindex_resource(make_snapshot(
            prebound_owner_resource_id,
            1,
            prebound_owner_document_id,
            {
                make_chunk(
                    prebound_owner_first_chunk_id,
                    prebound_owner_document_id,
                    0,
                    "prebound owner first"
                ),
                make_chunk(
                    prebound_owner_second_chunk_id,
                    prebound_owner_document_id,
                    20,
                    "prebound owner second"
                )
            }
        ));
        return fail("pre-owner vector rollback failure must be reported");
    } catch(const agent_memory::ResourceIndexRollbackError& error) {
        if(
            error.recovery_failures().size() != 1 ||
            error.recovery_failures().front().stage
                != agent_memory::ResourceIndexRecoveryStage::VectorRestore
        ) {
            return fail("pre-owner rollback must not consume the preserved owner binding");
        }
    }
    const auto prebound_owner = owner_storage.find_chunk_owner(
        prebound_owner_first_chunk_id
    );
    if(
        !vector_index.find(prebound_owner_first_chunk_id) ||
        !prebound_owner ||
        prebound_owner->resource_id != prebound_owner_resource_id ||
        prebound_owner->generation != 1
    ) {
        return fail("failed pre-owner rollback must retain the attempted chunk owner");
    }
    owner_storage.clear_upsert_failure();
    const bool repaired_prebound_owner_vector = vector_index.erase(
        prebound_owner_first_chunk_id
    );
    (void)repaired_prebound_owner_vector;

    const agent_memory::ResourceId retained_prebound_resource_id{
        "resource:indexer:retained-prebound-owner"
    };
    const agent_memory::DocumentId retained_prebound_document_id{
        "doc:indexer:retained-prebound-owner"
    };
    const agent_memory::ChunkId retained_prebound_first_chunk_id{
        "chunk:indexer:retained-prebound-owner:first"
    };
    const agent_memory::ChunkId retained_prebound_second_chunk_id{
        "chunk:indexer:retained-prebound-owner:second"
    };
    indexer.reindex_resource(make_snapshot(
        retained_prebound_resource_id,
        1,
        retained_prebound_document_id,
        {
            make_chunk(
                retained_prebound_first_chunk_id,
                retained_prebound_document_id,
                0,
                "retained prebound first"
            ),
            make_chunk(
                retained_prebound_second_chunk_id,
                retained_prebound_document_id,
                20,
                "retained prebound second"
            )
        }
    ));
    vector_index.fail_on_nth_upsert(2);
    vector_index.set_upsert_failure_hook([&owner_storage, &vector_index] {
        owner_storage.fail_next_upsert();
        vector_index.fail_next_upsert();
    });
    try {
        indexer.reindex_resource(make_snapshot(
            retained_prebound_resource_id,
            2,
            retained_prebound_document_id,
            {
                make_chunk(
                    retained_prebound_first_chunk_id,
                    retained_prebound_document_id,
                    0,
                    "updated retained prebound first"
                ),
                make_chunk(
                    retained_prebound_second_chunk_id,
                    retained_prebound_document_id,
                    20,
                    "updated retained prebound second"
                )
            }
        ));
        return fail("retained pre-owner vector rollback failure must be reported");
    } catch(const agent_memory::ResourceIndexRollbackError&) {
    }
    const auto retained_prebound_owner = owner_storage.find_chunk_owner(
        retained_prebound_first_chunk_id
    );
    if(
        !retained_prebound_owner ||
        retained_prebound_owner->resource_id != retained_prebound_resource_id ||
        retained_prebound_owner->generation != 2
    ) {
        return fail("retained failed rollback must not restore a previous chunk owner");
    }
    owner_storage.clear_upsert_failure();

    const agent_memory::ResourceId owner_rollback_vector_resource_id{
        "resource:indexer:owner-rollback-vector"
    };
    const agent_memory::DocumentId owner_rollback_vector_document_id{
        "doc:indexer:owner-rollback-vector"
    };
    const agent_memory::ChunkId owner_rollback_vector_chunk_id{
        "chunk:indexer:owner-rollback-vector"
    };
    manifest_storage.fail_next_upsert();
    vector_index.fail_next_erase();
    try {
        indexer.reindex_resource(make_snapshot(
            owner_rollback_vector_resource_id,
            1,
            owner_rollback_vector_document_id,
            {make_chunk(
                owner_rollback_vector_chunk_id,
                owner_rollback_vector_document_id,
                0,
                "owner rollback vector"
            )}
        ));
        return fail("failed vector rollback after owner publication must be reported");
    } catch(const agent_memory::ResourceIndexRollbackError& error) {
        if(
            error.recovery_failures().size() != 1 ||
            error.recovery_failures().front().stage
                != agent_memory::ResourceIndexRecoveryStage::VectorRestore
        ) {
            return fail("failed vector rollback must retain its recovery diagnostic");
        }
    }

    const auto owner_rollback_vector_chunk_owner = owner_storage.find_chunk_owner(
        owner_rollback_vector_chunk_id
    );
    if(
        document_storage.find_document(owner_rollback_vector_document_id) ||
        !vector_index.find(owner_rollback_vector_chunk_id) ||
        owner_storage.find_document_owner(owner_rollback_vector_document_id) ||
        !owner_rollback_vector_chunk_owner ||
        owner_rollback_vector_chunk_owner->resource_id != owner_rollback_vector_resource_id ||
        owner_rollback_vector_chunk_owner->generation != 1
    ) {
        return fail("failed vector rollback must retain an attempted chunk owner binding");
    }
    const bool repaired_owner_rollback_vector = vector_index.erase(
        owner_rollback_vector_chunk_id
    );
    (void)repaired_owner_rollback_vector;

    const agent_memory::ResourceId owner_rollback_document_resource_id{
        "resource:indexer:owner-rollback-document"
    };
    const agent_memory::DocumentId owner_rollback_document_id{
        "doc:indexer:owner-rollback-document"
    };
    const agent_memory::ChunkId owner_rollback_document_chunk_id{
        "chunk:indexer:owner-rollback-document"
    };
    document_storage.fail_on_nth_erase(2);
    manifest_storage.fail_next_upsert();
    try {
        indexer.reindex_resource(make_snapshot(
            owner_rollback_document_resource_id,
            1,
            owner_rollback_document_id,
            {make_chunk(
                owner_rollback_document_chunk_id,
                owner_rollback_document_id,
                0,
                "owner rollback document"
            )}
        ));
        return fail("failed document rollback after owner publication must be reported");
    } catch(const agent_memory::ResourceIndexRollbackError& error) {
        if(
            error.recovery_failures().size() != 1 ||
            error.recovery_failures().front().stage
                != agent_memory::ResourceIndexRecoveryStage::DocumentRestore
        ) {
            return fail("failed document rollback must retain its recovery diagnostic");
        }
    }

    const auto owner_rollback_document_owner = owner_storage.find_document_owner(
        owner_rollback_document_id
    );
    const auto owner_rollback_document_chunk_owner = owner_storage.find_chunk_owner(
        owner_rollback_document_chunk_id
    );
    if(
        !document_storage.find_document(owner_rollback_document_id) ||
        !document_storage.find_chunk(owner_rollback_document_chunk_id) ||
        vector_index.find(owner_rollback_document_chunk_id) ||
        !owner_rollback_document_owner ||
        !owner_rollback_document_chunk_owner ||
        owner_rollback_document_owner->generation != 1 ||
        owner_rollback_document_chunk_owner->generation != 1
    ) {
        return fail("failed document rollback must retain attempted owner bindings");
    }
    const bool repaired_owner_rollback_document = document_storage.erase_document(
        owner_rollback_document_id
    );
    (void)repaired_owner_rollback_document;

    const agent_memory::DocumentId rollback_failed_document_id{"doc:indexer:rollback-failed"};
    const agent_memory::ChunkId rollback_failed_first_chunk_id{"chunk:indexer:rollback-failed:first"};
    const agent_memory::ChunkId rollback_failed_second_chunk_id{"chunk:indexer:rollback-failed:second"};
    document_storage.fail_on_nth_erase(2);
    vector_index.fail_on_nth_upsert(2);
    vector_index.fail_next_erase();
    try {
        indexer.reindex_resource(make_snapshot(
            resource_id,
            2,
            rollback_failed_document_id,
            {
                make_chunk(
                    rollback_failed_first_chunk_id,
                    rollback_failed_document_id,
                    0,
                    "updated first chunk"
                ),
                make_chunk(
                    rollback_failed_second_chunk_id,
                    rollback_failed_document_id,
                    20,
                    "updated second chunk"
                )
            }
        ));
        return fail("resource indexer must surface an incomplete rollback");
    } catch(const agent_memory::ResourceIndexRollbackError& error) {
        if(
            !error.original_failure()
            || !error.rollback_failure()
            || error.recovery_failures().size() != 2
            || error.recovery_failures()[0].stage
                != agent_memory::ResourceIndexRecoveryStage::VectorRestore
            || error.recovery_failures()[0].operation
                != agent_memory::ResourceIndexRecoveryOperation::EraseAttempted
            || !error.recovery_failures()[0].chunk_id
            || *error.recovery_failures()[0].chunk_id != rollback_failed_first_chunk_id
            || error.recovery_failures()[1].stage
                != agent_memory::ResourceIndexRecoveryStage::DocumentRestore
            || error.recovery_failures()[1].operation
                != agent_memory::ResourceIndexRecoveryOperation::EraseAttempted
            || !error.recovery_failures()[1].document_id
            || *error.recovery_failures()[1].document_id != rollback_failed_document_id
        ) {
            return fail("rollback failure must retain both original and rollback diagnostics");
        }
    }

    if(
        !manifest_storage.find_manifest(resource_id)
        || manifest_storage.find_manifest(resource_id)->revision.generation != 1
    ) {
        return fail("rollback failure must preserve the active manifest under its strong exception contract");
    }

    if(!document_storage.find_document(rollback_failed_document_id)) {
        return fail("rollback failure fixture must leave repair-visible derived state");
    }
    const bool repaired_document = document_storage.erase_document(rollback_failed_document_id);
    (void)repaired_document;

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
    const auto newest_snapshot = make_snapshot(
        resource_id,
        3,
        newest_document_id,
        {
            make_chunk(newest_chunk_id, newest_document_id, 0, "newest chunk")
        }
    );
    indexer.reindex_resource(newest_snapshot);

    embedder.reset_call_count();
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

    if(embedder.call_count() != 0) {
        return fail("stale generation must be rejected before embedding");
    }

    embedder.reset_call_count();
    indexer.reindex_resource(newest_snapshot);

    if(embedder.call_count() != 1) {
        return fail("idempotent generation must verify its persisted vector");
    }

    const agent_memory::ResourceId reordered_resource_id{"resource:indexer:reordered"};
    const agent_memory::DocumentId reordered_document_id{"doc:indexer:reordered"};
    const auto reordered_snapshot = make_snapshot(
        reordered_resource_id,
        1,
        reordered_document_id,
        {
            make_chunk(
                agent_memory::ChunkId{"chunk:indexer:z"},
                reordered_document_id,
                12,
                "second chunk"
            ),
            make_chunk(
                agent_memory::ChunkId{"chunk:indexer:a"},
                reordered_document_id,
                0,
                "first chunk"
            )
        }
    );
    indexer.reindex_resource(reordered_snapshot);
    document_storage.set_chunk_order_by_id(true);
    embedder.reset_call_count();
    indexer.reindex_resource(reordered_snapshot);
    if(embedder.call_count() != 2) {
        return fail("same-generation retry must not depend on storage chunk order");
    }

    const auto reordered_manifest = manifest_storage.find_manifest(reordered_resource_id);
    if(!reordered_manifest) {
        return fail("reordered fixture must persist a manifest");
    }

    auto ordinal_mismatch_manifest = *reordered_manifest;
    for(auto& record : ordinal_mismatch_manifest.records) {
        if(
            record.kind == agent_memory::DerivedRecordKind::VectorRecord &&
            record.chunk_id == agent_memory::ChunkId{"chunk:indexer:z"}
        ) {
            record.ordinal = 99;
            break;
        }
    }
    manifest_storage.inject_unchecked_manifest(std::move(ordinal_mismatch_manifest));
    try {
        indexer.reindex_resource(reordered_snapshot);
        return fail("same-generation retry must reject an ordinal mismatch");
    } catch(const std::logic_error&) {
    }
    document_storage.set_chunk_order_by_id(false);

    const agent_memory::ResourceId vector_verify_resource_id{"resource:indexer:vector-verify"};
    const agent_memory::DocumentId vector_verify_document_id{"doc:indexer:vector-verify"};
    const agent_memory::ChunkId vector_verify_chunk_id{"chunk:indexer:vector-verify"};
    const auto vector_verify_snapshot = make_snapshot(
        vector_verify_resource_id,
        1,
        vector_verify_document_id,
        {
            make_chunk(
                vector_verify_chunk_id,
                vector_verify_document_id,
                0,
                "vector verification chunk"
            )
        }
    );
    indexer.reindex_resource(vector_verify_snapshot);
    const auto original_vector = vector_index.find(vector_verify_chunk_id);
    if(!original_vector) {
        return fail("vector verification fixture must persist its vector");
    }

    const bool removed_vector = vector_index.erase(vector_verify_chunk_id);
    (void)removed_vector;
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(vector_verify_snapshot);
        return fail("same-generation retry must reject a missing active vector");
    } catch(const std::logic_error&) {
    }
    if(
        embedder.call_count() != 1 ||
        !document_storage.find_document(vector_verify_document_id)
    ) {
        return fail("missing vector retry must fail without mutating the document");
    }

    vector_index.upsert(*original_vector);
    auto altered_vector = *original_vector;
    altered_vector.embedding.values[0] = -1.0F;
    vector_index.upsert(altered_vector);
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(vector_verify_snapshot);
        return fail("same-generation retry must reject an altered active vector");
    } catch(const std::logic_error&) {
    }
    if(embedder.call_count() != 1) {
        return fail("altered vector retry must validate with deterministic embedding");
    }
    vector_index.upsert(*original_vector);

    auto same_revision_different_snapshot = make_snapshot(
        resource_id,
        3,
        agent_memory::DocumentId{"doc:indexer:same-revision-different-layout"},
        {
            make_chunk(
                agent_memory::ChunkId{"chunk:indexer:same-revision-different-layout"},
                agent_memory::DocumentId{"doc:indexer:same-revision-different-layout"},
                0,
                "same source revision with different derived layout"
            )
        }
    );
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(std::move(same_revision_different_snapshot));
        return fail("same revision with different derived snapshot must conflict");
    } catch(const std::logic_error&) {
    }

    if(embedder.call_count() != 0) {
        return fail("different same-generation snapshot must be rejected before embedding");
    }

    const agent_memory::DocumentId ambiguous_document_id{"doc:indexer:ambiguous"};
    auto ambiguous_same_generation = make_snapshot(
        resource_id,
        3,
        ambiguous_document_id,
        {
            make_chunk(
                agent_memory::ChunkId{"chunk:indexer:ambiguous"},
                ambiguous_document_id,
                0,
                "ambiguous same-generation chunk"
            )
        }
    );
    ambiguous_same_generation.revision.content_hash = 0;
    ambiguous_same_generation.revision.pipeline_config_hash = 0;
    ambiguous_same_generation.revision.body_digest.reset();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(std::move(ambiguous_same_generation));
        return fail("same-generation snapshots without identity evidence must conflict");
    } catch(const std::logic_error&) {
    }

    if(embedder.call_count() != 0) {
        return fail("ambiguous same-generation snapshot must be rejected before embedding");
    }

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
    ++conflicting_generation.revision.pipeline_config_hash;
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(std::move(conflicting_generation));
        return fail("resource indexer must reject a conflicting resource generation");
    } catch(const std::logic_error&) {
    }

    if(embedder.call_count() != 0) {
        return fail("conflicting generation must be rejected before embedding");
    }

    auto digest_conflicting_generation = make_snapshot(
        resource_id,
        3,
        agent_memory::DocumentId{"doc:indexer:digest-conflict"},
        {
            make_chunk(
                agent_memory::ChunkId{"chunk:indexer:digest-conflict"},
                agent_memory::DocumentId{"doc:indexer:digest-conflict"},
                0,
                "digest-conflicting chunk"
            )
        }
    );
    digest_conflicting_generation.revision.body_digest = make_body_digest(0x11U);
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(std::move(digest_conflicting_generation));
        return fail("resource indexer must reject a conflicting body digest");
    } catch(const std::logic_error&) {
    }

    if(embedder.call_count() != 0) {
        return fail("conflicting body digest must be rejected before embedding");
    }

    const auto newest_manifest = manifest_storage.find_manifest(resource_id);
    if(
        !newest_manifest ||
        newest_manifest->revision.generation != 3 ||
        !document_storage.find_document(newest_document_id) ||
        !vector_index.find(newest_chunk_id) ||
        document_storage.find_document(ambiguous_document_id)
    ) {
        return fail("stale, conflicting, or idempotent work must not replace the active generation");
    }

    const agent_memory::DocumentId reclaim_document_id{"doc:indexer:reclaim"};
    const agent_memory::ChunkId reclaim_chunk_id{"chunk:indexer:reclaim"};
    document_storage.fail_on_nth_erase(2);
    try {
        indexer.reindex_resource(make_snapshot(
            resource_id,
            4,
            reclaim_document_id,
            {
                make_chunk(reclaim_chunk_id, reclaim_document_id, 0, "reclaim chunk")
            }
        ));
        return fail("reindex must surface failed post-publication reclamation");
    } catch(const agent_memory::ResourceIndexReclaimError& error) {
        if(
            error.published_manifest().revision.generation != 4
            || error.unreclaimed_manifest().records.empty()
            || !error.reclaim_failure()
        ) {
            return fail("reclaim failure must retain published and unreclaimed manifest diagnostics");
        }
    }

    const auto reclaim_manifest = manifest_storage.find_manifest(resource_id);
    const auto reclaim_document_owner = owner_storage.find_document_owner(reclaim_document_id);
    if(
        !reclaim_manifest
        || reclaim_manifest->revision.generation != 4
        || reclaim_manifest->pending_reclaim_records.empty()
        || !reclaim_document_owner
        || reclaim_document_owner->generation != 4
        || !document_storage.find_document(reclaim_document_id)
        || !document_storage.find_document(newest_document_id)
        || !vector_index.find(reclaim_chunk_id)
        || !vector_index.find(newest_chunk_id)
    ) {
        return fail("failed reclaim must preserve the newly published active generation");
    }

    const auto pending_reclaim_count = reclaim_manifest->pending_reclaim_records.size();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(make_snapshot(
            resource_id,
            3,
            agent_memory::DocumentId{"doc:indexer:stale-with-backlog"},
            {}
        ));
        return fail("stale reindex must be rejected before reclaim cleanup");
    } catch(const std::logic_error&) {
    }

    auto conflicting_with_backlog = make_snapshot(
        resource_id,
        4,
        agent_memory::DocumentId{"doc:indexer:conflict-with-backlog"},
        {}
    );
    conflicting_with_backlog.revision.body_digest = make_body_digest(0x44U);
    try {
        indexer.reindex_resource(std::move(conflicting_with_backlog));
        return fail("conflicting reindex must be rejected before reclaim cleanup");
    } catch(const std::logic_error&) {
    }

    const auto unchanged_backlog = manifest_storage.find_manifest(resource_id);
    if(
        embedder.call_count() != 0 ||
        !unchanged_backlog ||
        unchanged_backlog->pending_reclaim_records.size() != pending_reclaim_count ||
        !document_storage.find_document(newest_document_id) ||
        !vector_index.find(newest_chunk_id)
    ) {
        return fail("stale or conflicting reindex must not mutate reclaim backlog");
    }

    document_storage.fail_on_nth_erase(1);
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(make_snapshot(
            resource_id,
            5,
            agent_memory::DocumentId{"doc:indexer:blocked-reclaim"},
            {}
        ));
        return fail("pre-publication reclaim failure must block replacement publication");
    } catch(const agent_memory::ResourceIndexReclaimBlockedError& error) {
        if(
            error.active_manifest().revision.generation != 4 ||
            error.unreclaimed_manifest().records.empty() ||
            !error.reclaim_failure()
        ) {
            return fail("blocked reclaim error must report the still-active manifest");
        }
    }

    const auto blocked_manifest = manifest_storage.find_manifest(resource_id);
    if(
        embedder.call_count() != 0 ||
        !blocked_manifest ||
        blocked_manifest->revision.generation != 4 ||
        blocked_manifest->pending_reclaim_records.size() != pending_reclaim_count
    ) {
        return fail("blocked reclaim must not publish the requested replacement");
    }

    manifest_storage.fail_next_upsert();
    try {
        indexer.reindex_resource(make_snapshot(
            resource_id,
            4,
            reclaim_document_id,
            {
                make_chunk(reclaim_chunk_id, reclaim_document_id, 0, "reclaim chunk")
            }
        ));
        return fail("same-generation reclaim retry must surface post-publication failure");
    } catch(const agent_memory::ResourceIndexReclaimError& error) {
        const auto durable_manifest = manifest_storage.find_manifest(resource_id);
        if(
            error.published_manifest().revision.generation != 4 ||
            !durable_manifest ||
            error.published_manifest().pending_reclaim_records.size() !=
                durable_manifest->pending_reclaim_records.size() ||
            error.unreclaimed_manifest().records.size() !=
                durable_manifest->pending_reclaim_records.size()
        ) {
            return fail("same-generation reclaim failure must report durable published state");
        }
    }

    manifest_storage.fail_next_upsert();
    try {
        indexer.reindex_resource(make_snapshot(
            resource_id,
            5,
            agent_memory::DocumentId{"doc:indexer:blocked-manifest-save"},
            {}
        ));
        return fail("pre-publication reclaim save failure must block replacement");
    } catch(const agent_memory::ResourceIndexReclaimBlockedError& error) {
        const auto durable_manifest = manifest_storage.find_manifest(resource_id);
        if(
            error.active_manifest().revision.generation != 4 ||
            !durable_manifest ||
            error.active_manifest().pending_reclaim_records.size() !=
                durable_manifest->pending_reclaim_records.size() ||
            error.unreclaimed_manifest().records.size() !=
                durable_manifest->pending_reclaim_records.size()
        ) {
            return fail("blocked reclaim failure must report durable active state");
        }
    }

    agent_memory::ResourceIndexer restarted_indexer{
        document_storage,
        manifest_storage,
        owner_storage,
        embedder,
        vector_index
    };
    embedder.reset_call_count();
    restarted_indexer.reindex_resource(make_snapshot(
        resource_id,
        4,
        reclaim_document_id,
        {
            make_chunk(reclaim_chunk_id, reclaim_document_id, 0, "reclaim chunk")
        }
    ));

    const auto reclaimed_manifest = manifest_storage.find_manifest(resource_id);
    if(
        !reclaimed_manifest
        || !reclaimed_manifest->pending_reclaim_records.empty()
        || document_storage.find_document(newest_document_id)
        || vector_index.find(newest_chunk_id)
        || embedder.call_count() != 1
    ) {
        return fail("restart retry must verify vectors and drain reclaim backlog");
    }

    vector_index.fail_next_erase();
    try {
        const bool erased = restarted_indexer.erase_resource(resource_id);
        (void)erased;
        return fail("resource erase must propagate derived cleanup failures");
    } catch(const std::runtime_error&) {
    }

    const auto erase_pending_manifest = manifest_storage.find_manifest(resource_id);
    if(
        !erase_pending_manifest
        || agent_memory::is_active_resource_manifest(*erase_pending_manifest)
        || document_storage.find_document(reclaim_document_id)
    ) {
        return fail("failed resource cleanup must retain an inactive manifest for retry");
    }

    embedder.reset_call_count();
    try {
        restarted_indexer.reindex_resource(make_snapshot(
            resource_id,
            5,
            agent_memory::DocumentId{"doc:indexer:blocked-by-pending"},
            {
                make_chunk(
                    agent_memory::ChunkId{"chunk:indexer:blocked-by-pending"},
                    agent_memory::DocumentId{"doc:indexer:blocked-by-pending"},
                    0,
                    "blocked by pending cleanup"
                )
            }
        ));
        return fail("reindex must reject an erase-pending resource");
    } catch(const std::logic_error&) {
    }

    const auto pending_after_reindex_attempt = manifest_storage.find_manifest(resource_id);
    if(
        embedder.call_count() != 0 ||
        !pending_after_reindex_attempt ||
        pending_after_reindex_attempt->revision.generation != 4 ||
        agent_memory::is_active_resource_manifest(*pending_after_reindex_attempt)
    ) {
        return fail("reindex over erase-pending cleanup must not publish or embed");
    }

    if(!restarted_indexer.erase_resource(resource_id)) {
        return fail("resource erase must report removed resource state");
    }

    if(manifest_storage.find_manifest(resource_id)) {
        return fail("resource erase must remove manifest");
    }

    if(document_storage.find_document(reclaim_document_id)) {
        return fail("resource erase must remove current document");
    }

    if(vector_index.find(reclaim_chunk_id)) {
        return fail("resource erase must remove current vector record");
    }

    if(restarted_indexer.erase_resource(resource_id)) {
        return fail("resource erase of missing resource must report false");
    }

    const agent_memory::ResourceId manifest_erase_resource_id{"resource:indexer:manifest-erase"};
    const agent_memory::DocumentId manifest_erase_document_id{"doc:indexer:manifest-erase"};
    const agent_memory::ChunkId manifest_erase_chunk_id{"chunk:indexer:manifest-erase"};
    restarted_indexer.reindex_resource(make_snapshot(
        manifest_erase_resource_id,
        1,
        manifest_erase_document_id,
        {
            make_chunk(
                manifest_erase_chunk_id,
                manifest_erase_document_id,
                0,
                "manifest erase retry"
            )
        }
    ));

    manifest_storage.fail_next_erase();
    try {
        (void)restarted_indexer.erase_resource(manifest_erase_resource_id);
        return fail("resource erase must surface manifest erase failures");
    } catch(const std::runtime_error&) {
    }

    const auto manifest_erase_pending = manifest_storage.find_manifest(manifest_erase_resource_id);
    if(
        !manifest_erase_pending ||
        agent_memory::is_active_resource_manifest(*manifest_erase_pending) ||
        document_storage.find_document(manifest_erase_document_id) ||
        vector_index.find(manifest_erase_chunk_id)
    ) {
        return fail("manifest erase failure must retain a retryable pending manifest");
    }

    if(!restarted_indexer.erase_resource(manifest_erase_resource_id)) {
        return fail("retry must remove a manifest left pending by manifest erase failure");
    }

    const agent_memory::ResourceId sparse_reclaim_resource_id{
        "resource:indexer:sparse-reclaim"
    };
    const agent_memory::DocumentId sparse_reclaim_old_document_id{
        "doc:indexer:sparse-reclaim:old"
    };
    const agent_memory::ChunkId sparse_reclaim_chunk_0{"chunk:indexer:sparse-reclaim:0"};
    const agent_memory::ChunkId sparse_reclaim_chunk_1{"chunk:indexer:sparse-reclaim:1"};
    const agent_memory::ChunkId sparse_reclaim_chunk_2{"chunk:indexer:sparse-reclaim:2"};
    restarted_indexer.reindex_resource(make_snapshot(
        sparse_reclaim_resource_id,
        1,
        sparse_reclaim_old_document_id,
        {
            make_chunk(sparse_reclaim_chunk_0, sparse_reclaim_old_document_id, 0, "old zero"),
            make_chunk(sparse_reclaim_chunk_1, sparse_reclaim_old_document_id, 10, "old one"),
            make_chunk(sparse_reclaim_chunk_2, sparse_reclaim_old_document_id, 20, "old two")
        }
    ));

    const auto sparse_reclaim_replacement = make_snapshot(
        sparse_reclaim_resource_id,
        2,
        sparse_reclaim_old_document_id,
        {
            make_chunk(sparse_reclaim_chunk_1, sparse_reclaim_old_document_id, 0, "retained one")
        }
    );
    vector_index.fail_next_erase();
    try {
        restarted_indexer.reindex_resource(sparse_reclaim_replacement);
        return fail("sparse reclaim fixture must fail its first cleanup attempt");
    } catch(const agent_memory::ResourceIndexReclaimError&) {
    }

    const auto sparse_reclaim_pending = manifest_storage.find_manifest(
        sparse_reclaim_resource_id
    );
    if(
        !sparse_reclaim_pending ||
        sparse_reclaim_pending->pending_reclaim_records.size() != 4 ||
        sparse_reclaim_pending->pending_reclaim_records.front().kind !=
            agent_memory::DerivedRecordKind::VectorRecord ||
        sparse_reclaim_pending->pending_reclaim_records.front().ordinal != 0 ||
        sparse_reclaim_pending->pending_reclaim_records.back().ordinal != 2 ||
        !vector_index.find(sparse_reclaim_chunk_1)
    ) {
        return fail("partial sparse reclaim must persist a retryable ordered subsequence");
    }

    agent_memory::ResourceIndexer sparse_reclaim_restart{
        document_storage,
        manifest_storage,
        owner_storage,
        embedder,
        vector_index
    };
    sparse_reclaim_restart.reindex_resource(sparse_reclaim_replacement);
    const auto sparse_reclaim_recovered = manifest_storage.find_manifest(
        sparse_reclaim_resource_id
    );
    if(
        !sparse_reclaim_recovered ||
        !sparse_reclaim_recovered->pending_reclaim_records.empty() ||
        !vector_index.find(sparse_reclaim_chunk_1) ||
        vector_index.find(sparse_reclaim_chunk_0) ||
        vector_index.find(sparse_reclaim_chunk_2)
    ) {
        return fail("restart must drain a sparse partial reclaim queue without erasing retained data");
    }

    try {
        indexer.reindex_resource(agent_memory::ResourceIndexSnapshot{});
        return fail("resource indexer must reject empty resource snapshot");
    } catch(const std::invalid_argument&) {
    }

    const agent_memory::ResourceId invalid_evidence_resource_id{
        "resource:indexer:invalid-evidence"
    };
    const agent_memory::DocumentId invalid_evidence_document_id{
        "doc:indexer:invalid-evidence"
    };
    const agent_memory::ChunkId invalid_evidence_chunk_id{"chunk:indexer:invalid-evidence"};
    auto missing_digest_snapshot = make_snapshot(
        invalid_evidence_resource_id,
        1,
        invalid_evidence_document_id,
        {
            make_chunk(
                invalid_evidence_chunk_id,
                invalid_evidence_document_id,
                0,
                "missing evidence"
            )
        }
    );
    missing_digest_snapshot.revision.body_digest.reset();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(std::move(missing_digest_snapshot));
        return fail("resource indexer must reject a snapshot without a body digest");
    } catch(const std::invalid_argument&) {
    }

    auto zero_pipeline_snapshot = make_snapshot(
        invalid_evidence_resource_id,
        1,
        invalid_evidence_document_id,
        {
            make_chunk(
                invalid_evidence_chunk_id,
                invalid_evidence_document_id,
                0,
                "zero pipeline"
            )
        }
    );
    zero_pipeline_snapshot.revision.pipeline_config_hash = 0;
    try {
        indexer.reindex_resource(std::move(zero_pipeline_snapshot));
        return fail("resource indexer must reject a zero pipeline hash");
    } catch(const std::invalid_argument&) {
    }

    if(
        embedder.call_count() != 0 ||
        manifest_storage.find_manifest(invalid_evidence_resource_id) ||
        document_storage.find_document(invalid_evidence_document_id) ||
        vector_index.find(invalid_evidence_chunk_id)
    ) {
        return fail("invalid snapshot evidence must be rejected before all mutation");
    }

    const agent_memory::DocumentId duplicate_chunk_document_id{"doc:indexer:duplicate-chunk"};
    try {
        indexer.reindex_resource(make_snapshot(
            agent_memory::ResourceId{"resource:indexer:duplicate-chunk"},
            1,
            duplicate_chunk_document_id,
            {
                make_chunk(
                    agent_memory::ChunkId{"chunk:indexer:duplicate"},
                    duplicate_chunk_document_id,
                    0,
                    "first duplicate"
                ),
                make_chunk(
                    agent_memory::ChunkId{"chunk:indexer:duplicate"},
                    duplicate_chunk_document_id,
                    16,
                    "second duplicate"
                )
            }
        ));
        return fail("resource indexer must reject duplicate chunk ids");
    } catch(const std::invalid_argument&) {
    }

    const agent_memory::ResourceId legacy_resource_id{"resource:indexer:legacy-invalid"};
    const agent_memory::DocumentId legacy_document_id{"doc:indexer:legacy-invalid"};
    const agent_memory::ChunkId legacy_chunk_id{"chunk:indexer:legacy-invalid"};
    const auto legacy_snapshot = make_snapshot(
        legacy_resource_id,
        1,
        legacy_document_id,
        {
            make_chunk(legacy_chunk_id, legacy_document_id, 0, "legacy chunk")
        }
    );
    indexer.reindex_resource(legacy_snapshot);

    const auto valid_legacy_manifest = manifest_storage.find_manifest(legacy_resource_id);
    if(!valid_legacy_manifest) {
        return fail("legacy fixture must publish its initial manifest");
    }

    auto invalid_legacy_manifest = *valid_legacy_manifest;
    for(const auto& record : invalid_legacy_manifest.records) {
        if(record.kind == agent_memory::DerivedRecordKind::VectorRecord) {
            invalid_legacy_manifest.pending_reclaim_records.push_back(record);
            break;
        }
    }
    manifest_storage.inject_unchecked_manifest(std::move(invalid_legacy_manifest));

    embedder.reset_call_count();
    try {
        indexer.reindex_resource(legacy_snapshot);
        return fail("invalid stored manifest must be rejected before reclaim");
    } catch(const std::logic_error&) {
    }

    if(
        embedder.call_count() != 0 ||
        !document_storage.find_document(legacy_document_id) ||
        !vector_index.find(legacy_chunk_id)
    ) {
        return fail("invalid stored manifest must not mutate active derived state");
    }

    try {
        const bool erased = indexer.erase_resource(legacy_resource_id);
        (void)erased;
        return fail("erase must reject an invalid stored manifest before deletion");
    } catch(const std::logic_error&) {
    }

    if(
        !document_storage.find_document(legacy_document_id) ||
        !vector_index.find(legacy_chunk_id)
    ) {
        return fail("invalid stored manifest must not erase active derived state");
    }

    const agent_memory::ResourceId owner_resource_a_id{"resource:indexer:owner-a"};
    const agent_memory::DocumentId owner_document_a_id{"doc:indexer:owner-a"};
    const agent_memory::ChunkId owner_chunk_a_id{"chunk:indexer:owner-a"};
    const agent_memory::ResourceId owner_resource_b_id{"resource:indexer:owner-b"};
    const agent_memory::DocumentId owner_document_b_id{"doc:indexer:owner-b"};
    const agent_memory::ChunkId owner_chunk_b_id{"chunk:indexer:owner-b"};
    indexer.reindex_resource(make_snapshot(
        owner_resource_a_id,
        1,
        owner_document_a_id,
        {make_chunk(owner_chunk_a_id, owner_document_a_id, 0, "owner A")}
    ));
    indexer.reindex_resource(make_snapshot(
        owner_resource_b_id,
        1,
        owner_document_b_id,
        {make_chunk(owner_chunk_b_id, owner_document_b_id, 0, "owner B")}
    ));
    const auto owner_manifest_a = manifest_storage.find_manifest(owner_resource_a_id);
    if(!owner_manifest_a) {
        return fail("owner fixture must publish resource A");
    }

    auto swapped_owner_manifest = *owner_manifest_a;
    for(auto& record : swapped_owner_manifest.records) {
        if(record.kind == agent_memory::DerivedRecordKind::Document) {
            record.key = owner_document_b_id.value();
        } else {
            record.chunk_id = owner_chunk_b_id;
        }
    }
    if(!agent_memory::is_valid_resource_manifest(swapped_owner_manifest)) {
        return fail("swapped-owner fixture must remain generically valid");
    }
    manifest_storage.inject_unchecked_manifest(std::move(swapped_owner_manifest));

    manifest_storage.reset_operation_counts();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(make_snapshot(
            owner_resource_a_id,
            2,
            agent_memory::DocumentId{"doc:indexer:owner-a:replacement"},
            {}
        ));
        return fail("reindex must reject manifest records owned by another resource");
    } catch(const agent_memory::ResourceIndexRecordOwnershipError&) {
    }
    try {
        (void)indexer.erase_resource(owner_resource_a_id);
        return fail("erase must reject manifest records owned by another resource");
    } catch(const agent_memory::ResourceIndexRecordOwnershipError&) {
    }
    if(
        embedder.call_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        !document_storage.find_document(owner_document_b_id) ||
        !vector_index.find(owner_chunk_b_id)
    ) {
        return fail("foreign physical ownership must be rejected before every mutation");
    }

    const agent_memory::ResourceId ownerless_resource_id{"resource:indexer:ownerless"};
    const agent_memory::DocumentId ownerless_document_id{"doc:indexer:ownerless"};
    const agent_memory::ChunkId ownerless_chunk_id{"chunk:indexer:ownerless"};
    indexer.reindex_resource(make_snapshot(
        ownerless_resource_id,
        1,
        ownerless_document_id,
        {make_chunk(ownerless_chunk_id, ownerless_document_id, 0, "ownerless source")}
    ));
    const auto ownerless_manifest = manifest_storage.find_manifest(ownerless_resource_id);
    if(!ownerless_manifest) {
        return fail("ownerless fixture must publish its source manifest");
    }
    (void)owner_storage.erase_document_owner(ownerless_document_id);
    (void)owner_storage.erase_chunk_owner(ownerless_chunk_id);

    document_storage.reset_operation_counts();
    manifest_storage.reset_operation_counts();
    owner_storage.reset_operation_counts();
    vector_index.reset_operation_counts();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(make_snapshot(
            agent_memory::ResourceId{"resource:indexer:ownerless:collision"},
            1,
            ownerless_document_id,
            {make_chunk(ownerless_chunk_id, ownerless_document_id, 0, "ownerless collision")}
        ));
        return fail("ownerless physical identities must not be claimed by a new resource");
    } catch(const agent_memory::ResourceIndexRecordOwnershipError&) {
    }

    auto ownerless_erase_pending = *ownerless_manifest;
    ownerless_erase_pending.state = agent_memory::ResourceManifestState::ErasePending;
    manifest_storage.inject_unchecked_manifest(std::move(ownerless_erase_pending));
    try {
        (void)indexer.erase_resource(ownerless_resource_id);
        return fail("ownerless erase-pending records must prove physical absence");
    } catch(const agent_memory::ResourceIndexRecordOwnershipError&) {
    }

    if(
        embedder.call_count() != 0 ||
        document_storage.upsert_count() != 0 ||
        document_storage.erase_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        owner_storage.mutation_count() != 0 ||
        vector_index.upsert_count() != 0 ||
        vector_index.erase_count() != 0 ||
        !document_storage.find_document(ownerless_document_id) ||
        !document_storage.find_chunk(ownerless_chunk_id) ||
        !vector_index.find(ownerless_chunk_id)
    ) {
        return fail("ownerless physical records must be rejected before mutation and cleanup");
    }

    const agent_memory::ResourceId closure_resource_id{"resource:indexer:closure"};
    const agent_memory::DocumentId closure_document_id{"doc:indexer:closure"};
    const agent_memory::ChunkId closure_active_chunk_id{"chunk:indexer:closure:active"};
    const agent_memory::ChunkId closure_extra_chunk_id{"chunk:indexer:closure:extra"};
    indexer.reindex_resource(make_snapshot(
        closure_resource_id,
        1,
        closure_document_id,
        {make_chunk(closure_active_chunk_id, closure_document_id, 0, "closure active")}
    ));
    const auto closure_active_vector = vector_index.find(closure_active_chunk_id);
    const auto closure_active_owner = owner_storage.find_chunk_owner(closure_active_chunk_id);
    if(!closure_active_vector || !closure_active_owner) {
        return fail("closure fixture must publish an owned active chunk");
    }
    document_storage.inject_unchecked_chunk(
        make_chunk(closure_extra_chunk_id, closure_document_id, 20, "closure extra")
    );
    auto closure_extra_vector = *closure_active_vector;
    closure_extra_vector.chunk_id = closure_extra_chunk_id;
    vector_index.upsert(std::move(closure_extra_vector));

    document_storage.reset_operation_counts();
    manifest_storage.reset_operation_counts();
    owner_storage.reset_operation_counts();
    vector_index.reset_operation_counts();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(make_snapshot(
            closure_resource_id,
            2,
            closure_document_id,
            {make_chunk(closure_active_chunk_id, closure_document_id, 0, "updated closure")}
        ));
        return fail("retained document closure must reject an ownerless child");
    } catch(const agent_memory::ResourceIndexRecordOwnershipError&) {
    }

    const auto closure_manifest = manifest_storage.find_manifest(closure_resource_id);
    const auto closure_owner_after_rejection = owner_storage.find_chunk_owner(
        closure_active_chunk_id
    );
    if(
        embedder.call_count() != 0 ||
        document_storage.upsert_count() != 0 ||
        document_storage.erase_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        owner_storage.mutation_count() != 0 ||
        vector_index.upsert_count() != 0 ||
        vector_index.erase_count() != 0 ||
        !closure_manifest ||
        closure_manifest->revision.generation != 1 ||
        !document_storage.find_chunk(closure_extra_chunk_id) ||
        !vector_index.find(closure_extra_chunk_id) ||
        owner_storage.find_chunk_owner(closure_extra_chunk_id) ||
        !closure_owner_after_rejection ||
        closure_owner_after_rejection->generation != closure_active_owner->generation
    ) {
        return fail("closure rejection must preserve every physical and owner state");
    }

    const agent_memory::ResourceId closure_replacement_resource_id{
        "resource:indexer:closure-replacement"
    };
    const agent_memory::DocumentId closure_replacement_old_document_id{
        "doc:indexer:closure-replacement:old"
    };
    const agent_memory::DocumentId closure_replacement_new_document_id{
        "doc:indexer:closure-replacement:new"
    };
    const agent_memory::ChunkId closure_replacement_active_chunk_id{
        "chunk:indexer:closure-replacement:active"
    };
    const agent_memory::ChunkId closure_replacement_extra_chunk_id{
        "chunk:indexer:closure-replacement:extra"
    };
    indexer.reindex_resource(make_snapshot(
        closure_replacement_resource_id,
        1,
        closure_replacement_old_document_id,
        {make_chunk(
            closure_replacement_active_chunk_id,
            closure_replacement_old_document_id,
            0,
            "closure replacement active"
        )}
    ));
    const auto closure_replacement_vector = vector_index.find(
        closure_replacement_active_chunk_id
    );
    if(!closure_replacement_vector) {
        return fail("replacement closure fixture must publish its active vector");
    }
    document_storage.inject_unchecked_chunk(make_chunk(
        closure_replacement_extra_chunk_id,
        closure_replacement_old_document_id,
        20,
        "closure replacement ownerless"
    ));
    auto closure_replacement_extra_vector = *closure_replacement_vector;
    closure_replacement_extra_vector.chunk_id = closure_replacement_extra_chunk_id;
    vector_index.upsert(std::move(closure_replacement_extra_vector));

    document_storage.reset_operation_counts();
    manifest_storage.reset_operation_counts();
    owner_storage.reset_operation_counts();
    vector_index.reset_operation_counts();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(make_snapshot(
            closure_replacement_resource_id,
            2,
            closure_replacement_new_document_id,
            {make_chunk(
                agent_memory::ChunkId{"chunk:indexer:closure-replacement:new"},
                closure_replacement_new_document_id,
                0,
                "closure replacement new"
            )}
        ));
        return fail("replacement reclaim must reject an ownerless old-document child");
    } catch(const agent_memory::ResourceIndexRecordOwnershipError&) {
    }
    if(
        embedder.call_count() != 0 ||
        document_storage.upsert_count() != 0 ||
        document_storage.erase_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        owner_storage.mutation_count() != 0 ||
        vector_index.upsert_count() != 0 ||
        vector_index.erase_count() != 0 ||
        !document_storage.find_chunk(closure_replacement_extra_chunk_id) ||
        !vector_index.find(closure_replacement_extra_chunk_id)
    ) {
        return fail("replacement reclaim rejection must not mutate any storage channel");
    }

    const agent_memory::ResourceId closure_target_resource_id{
        "resource:indexer:closure-target"
    };
    const agent_memory::DocumentId closure_target_old_document_id{
        "doc:indexer:closure-target:old"
    };
    const agent_memory::DocumentId closure_target_new_document_id{
        "doc:indexer:closure-target:new"
    };
    const agent_memory::ChunkId closure_target_old_chunk_id{
        "chunk:indexer:closure-target:old"
    };
    const agent_memory::ChunkId closure_target_new_chunk_id{
        "chunk:indexer:closure-target:new"
    };
    const agent_memory::ChunkId closure_target_extra_chunk_id{
        "chunk:indexer:closure-target:extra"
    };
    indexer.reindex_resource(make_snapshot(
        closure_target_resource_id,
        1,
        closure_target_old_document_id,
        {make_chunk(
            closure_target_old_chunk_id,
            closure_target_old_document_id,
            0,
            "closure target old"
        )}
    ));
    const auto closure_target_old_vector = vector_index.find(closure_target_old_chunk_id);
    const auto closure_target_old_owner = owner_storage.find_chunk_owner(
        closure_target_old_chunk_id
    );
    if(!closure_target_old_vector || !closure_target_old_owner) {
        return fail("target closure fixture must publish an active owner");
    }

    auto target_residual_snapshot = make_snapshot(
        closure_target_resource_id,
        2,
        closure_target_new_document_id,
        {make_chunk(
            closure_target_new_chunk_id,
            closure_target_new_document_id,
            0,
            "closure target new"
        )}
    );
    document_storage.upsert_document(target_residual_snapshot.document_snapshot);
    document_storage.inject_unchecked_chunk(make_chunk(
        closure_target_extra_chunk_id,
        closure_target_new_document_id,
        20,
        "closure target undeclared child"
    ));
    auto target_residual_owner = *closure_target_old_owner;
    target_residual_owner.generation = 2;
    owner_storage.upsert_document_owner(
        closure_target_new_document_id,
        target_residual_owner
    );
    owner_storage.upsert_chunk_owner(closure_target_new_chunk_id, target_residual_owner);
    auto closure_target_new_vector = *closure_target_old_vector;
    closure_target_new_vector.chunk_id = closure_target_new_chunk_id;
    vector_index.upsert(std::move(closure_target_new_vector));
    auto closure_target_extra_vector = *closure_target_old_vector;
    closure_target_extra_vector.chunk_id = closure_target_extra_chunk_id;
    vector_index.upsert(std::move(closure_target_extra_vector));

    document_storage.reset_operation_counts();
    manifest_storage.reset_operation_counts();
    owner_storage.reset_operation_counts();
    vector_index.reset_operation_counts();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(std::move(target_residual_snapshot));
        return fail("replacement must reject an undeclared target-document child");
    } catch(const agent_memory::ResourceIndexRecordOwnershipError&) {
    }
    if(
        embedder.call_count() != 0 ||
        document_storage.upsert_count() != 0 ||
        document_storage.erase_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        owner_storage.mutation_count() != 0 ||
        vector_index.upsert_count() != 0 ||
        vector_index.erase_count() != 0 ||
        !document_storage.find_chunk(closure_target_extra_chunk_id) ||
        !vector_index.find(closure_target_extra_chunk_id)
    ) {
        return fail("target closure rejection must preserve every residual child");
    }

    const agent_memory::ResourceId closure_erase_foreign_resource_id{
        "resource:indexer:closure-erase-foreign"
    };
    const agent_memory::DocumentId closure_erase_foreign_document_id{
        "doc:indexer:closure-erase-foreign"
    };
    const agent_memory::ChunkId closure_erase_foreign_active_chunk_id{
        "chunk:indexer:closure-erase-foreign:active"
    };
    const agent_memory::ChunkId closure_erase_foreign_extra_chunk_id{
        "chunk:indexer:closure-erase-foreign:extra"
    };
    indexer.reindex_resource(make_snapshot(
        closure_erase_foreign_resource_id,
        1,
        closure_erase_foreign_document_id,
        {make_chunk(
            closure_erase_foreign_active_chunk_id,
            closure_erase_foreign_document_id,
            0,
            "closure erase foreign active"
        )}
    ));
    const auto closure_erase_foreign_vector = vector_index.find(
        closure_erase_foreign_active_chunk_id
    );
    const auto closure_erase_foreign_owner = owner_storage.find_chunk_owner(
        closure_erase_foreign_active_chunk_id
    );
    if(!closure_erase_foreign_vector || !closure_erase_foreign_owner) {
        return fail("foreign erase closure fixture must publish its active owner");
    }
    document_storage.inject_unchecked_chunk(make_chunk(
        closure_erase_foreign_extra_chunk_id,
        closure_erase_foreign_document_id,
        20,
        "closure erase foreign child"
    ));
    auto closure_erase_foreign_extra_vector = *closure_erase_foreign_vector;
    closure_erase_foreign_extra_vector.chunk_id = closure_erase_foreign_extra_chunk_id;
    vector_index.upsert(std::move(closure_erase_foreign_extra_vector));
    auto foreign_child_owner = *closure_erase_foreign_owner;
    foreign_child_owner.resource_id = agent_memory::ResourceId{
        "resource:indexer:closure-erase-foreign:other"
    };
    owner_storage.upsert_chunk_owner(closure_erase_foreign_extra_chunk_id, foreign_child_owner);

    document_storage.reset_operation_counts();
    manifest_storage.reset_operation_counts();
    owner_storage.reset_operation_counts();
    vector_index.reset_operation_counts();
    embedder.reset_call_count();
    try {
        (void)indexer.erase_resource(closure_erase_foreign_resource_id);
        return fail("direct erase must reject a foreign document child");
    } catch(const agent_memory::ResourceIndexRecordOwnershipError&) {
    }
    if(
        embedder.call_count() != 0 ||
        document_storage.upsert_count() != 0 ||
        document_storage.erase_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        owner_storage.mutation_count() != 0 ||
        vector_index.upsert_count() != 0 ||
        vector_index.erase_count() != 0 ||
        !document_storage.find_chunk(closure_erase_foreign_extra_chunk_id) ||
        !vector_index.find(closure_erase_foreign_extra_chunk_id)
    ) {
        return fail("foreign direct-erase rejection must not mutate any storage channel");
    }

    const agent_memory::ResourceId closure_pending_resource_id{
        "resource:indexer:closure-pending"
    };
    const agent_memory::DocumentId closure_pending_document_id{
        "doc:indexer:closure-pending"
    };
    const agent_memory::ChunkId closure_pending_active_chunk_id{
        "chunk:indexer:closure-pending:active"
    };
    const agent_memory::ChunkId closure_pending_extra_chunk_id{
        "chunk:indexer:closure-pending:extra"
    };
    indexer.reindex_resource(make_snapshot(
        closure_pending_resource_id,
        1,
        closure_pending_document_id,
        {make_chunk(
            closure_pending_active_chunk_id,
            closure_pending_document_id,
            0,
            "closure pending active"
        )}
    ));
    const auto closure_pending_vector = vector_index.find(closure_pending_active_chunk_id);
    const auto closure_pending_owner = owner_storage.find_chunk_owner(
        closure_pending_active_chunk_id
    );
    const auto closure_pending_manifest = manifest_storage.find_manifest(
        closure_pending_resource_id
    );
    if(!closure_pending_vector || !closure_pending_owner || !closure_pending_manifest) {
        return fail("pending closure fixture must publish its active state");
    }
    document_storage.inject_unchecked_chunk(make_chunk(
        closure_pending_extra_chunk_id,
        closure_pending_document_id,
        20,
        "closure pending undeclared child"
    ));
    auto closure_pending_extra_vector = *closure_pending_vector;
    closure_pending_extra_vector.chunk_id = closure_pending_extra_chunk_id;
    vector_index.upsert(std::move(closure_pending_extra_vector));
    owner_storage.upsert_chunk_owner(closure_pending_extra_chunk_id, *closure_pending_owner);
    auto pending_closure_manifest = *closure_pending_manifest;
    pending_closure_manifest.state = agent_memory::ResourceManifestState::ErasePending;
    manifest_storage.inject_unchecked_manifest(std::move(pending_closure_manifest));

    document_storage.reset_operation_counts();
    manifest_storage.reset_operation_counts();
    owner_storage.reset_operation_counts();
    vector_index.reset_operation_counts();
    embedder.reset_call_count();
    try {
        (void)indexer.erase_resource(closure_pending_resource_id);
        return fail("pending restart must reject an undeclared same-owner child");
    } catch(const agent_memory::ResourceIndexRecordOwnershipError&) {
    }
    if(
        embedder.call_count() != 0 ||
        document_storage.upsert_count() != 0 ||
        document_storage.erase_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        owner_storage.mutation_count() != 0 ||
        vector_index.upsert_count() != 0 ||
        vector_index.erase_count() != 0 ||
        !document_storage.find_chunk(closure_pending_extra_chunk_id) ||
        !vector_index.find(closure_pending_extra_chunk_id)
    ) {
        return fail("pending closure rejection must not mutate any storage channel");
    }

    const agent_memory::ResourceId closure_drain_resource_id{
        "resource:indexer:closure-drain"
    };
    const agent_memory::DocumentId closure_drain_old_document_id{
        "doc:indexer:closure-drain:old"
    };
    const agent_memory::DocumentId closure_drain_new_document_id{
        "doc:indexer:closure-drain:new"
    };
    const agent_memory::ChunkId closure_drain_old_chunk_id{
        "chunk:indexer:closure-drain:old"
    };
    const agent_memory::ChunkId closure_drain_extra_chunk_id{
        "chunk:indexer:closure-drain:extra"
    };
    indexer.reindex_resource(make_snapshot(
        closure_drain_resource_id,
        1,
        closure_drain_old_document_id,
        {make_chunk(
            closure_drain_old_chunk_id,
            closure_drain_old_document_id,
            0,
            "closure drain old"
        )}
    ));
    const auto closure_drain_old_vector = vector_index.find(closure_drain_old_chunk_id);
    const auto closure_drain_old_owner = owner_storage.find_chunk_owner(
        closure_drain_old_chunk_id
    );
    if(!closure_drain_old_vector || !closure_drain_old_owner) {
        return fail("drain closure fixture must publish an active owner");
    }

    manifest_storage.set_upsert_hook([&] {
        document_storage.inject_unchecked_chunk(make_chunk(
            closure_drain_extra_chunk_id,
            closure_drain_old_document_id,
            20,
            "closure drain undeclared child"
        ));
        auto extra_vector = *closure_drain_old_vector;
        extra_vector.chunk_id = closure_drain_extra_chunk_id;
        vector_index.upsert(std::move(extra_vector));
        owner_storage.upsert_chunk_owner(closure_drain_extra_chunk_id, *closure_drain_old_owner);
    });

    try {
        indexer.reindex_resource(make_snapshot(
            closure_drain_resource_id,
            2,
            closure_drain_new_document_id,
            {make_chunk(
                agent_memory::ChunkId{"chunk:indexer:closure-drain:new"},
                closure_drain_new_document_id,
                0,
                "closure drain new"
            )}
        ));
        return fail("drain must reject an undeclared pending-reclaim child");
    } catch(const agent_memory::ResourceIndexReclaimError&) {
    }
    const auto closure_drain_manifest = manifest_storage.find_manifest(
        closure_drain_resource_id
    );
    if(
        !closure_drain_manifest ||
        closure_drain_manifest->revision.generation != 2 ||
        closure_drain_manifest->pending_reclaim_records.empty() ||
        !document_storage.find_chunk(closure_drain_extra_chunk_id) ||
        !vector_index.find(closure_drain_extra_chunk_id)
    ) {
        return fail("drain closure rejection must retain the published reclaim backlog");
    }

    const agent_memory::ResourceId retained_parent_resource_id{
        "resource:indexer:retained-parent"
    };
    const agent_memory::DocumentId retained_parent_old_document_id{
        "doc:indexer:retained-parent:old"
    };
    const agent_memory::DocumentId retained_parent_new_document_id{
        "doc:indexer:retained-parent:new"
    };
    const agent_memory::ChunkId retained_parent_chunk_id{
        "chunk:indexer:retained-parent"
    };
    indexer.reindex_resource(make_snapshot(
        retained_parent_resource_id,
        1,
        retained_parent_old_document_id,
        {make_chunk(
            retained_parent_chunk_id,
            retained_parent_old_document_id,
            0,
            "retained parent source"
        )}
    ));
    const auto retained_parent_owner = owner_storage.find_chunk_owner(retained_parent_chunk_id);
    if(!retained_parent_owner) {
        return fail("retained-parent fixture must publish a chunk owner");
    }
    document_storage.reset_operation_counts();
    manifest_storage.reset_operation_counts();
    owner_storage.reset_operation_counts();
    vector_index.reset_operation_counts();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(make_snapshot(
            retained_parent_resource_id,
            2,
            retained_parent_new_document_id,
            {make_chunk(
                retained_parent_chunk_id,
                retained_parent_new_document_id,
                0,
                "retained parent replacement"
            )}
        ));
        return fail("retained chunk must not move under a different document");
    } catch(const agent_memory::ResourceIndexRecordOwnershipError&) {
    }

    const auto retained_parent_chunk = document_storage.find_chunk(retained_parent_chunk_id);
    const auto retained_parent_owner_after_rejection = owner_storage.find_chunk_owner(
        retained_parent_chunk_id
    );
    if(
        embedder.call_count() != 0 ||
        document_storage.upsert_count() != 0 ||
        document_storage.erase_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        owner_storage.mutation_count() != 0 ||
        vector_index.upsert_count() != 0 ||
        vector_index.erase_count() != 0 ||
        !document_storage.find_document(retained_parent_old_document_id) ||
        document_storage.find_document(retained_parent_new_document_id) ||
        !retained_parent_chunk ||
        retained_parent_chunk->document_id != retained_parent_old_document_id ||
        !vector_index.find(retained_parent_chunk_id) ||
        !retained_parent_owner_after_rejection ||
        retained_parent_owner_after_rejection->generation != retained_parent_owner->generation
    ) {
        return fail("retained chunk parent mismatch must be rejected before mutation");
    }

    const agent_memory::ResourceId foreign_resource_id{"resource:indexer:foreign"};
    const agent_memory::DocumentId foreign_document_id{"doc:indexer:foreign"};
    const agent_memory::ChunkId foreign_chunk_id{"chunk:indexer:foreign"};
    indexer.reindex_resource(make_snapshot(
        foreign_resource_id,
        1,
        foreign_document_id,
        {
            make_chunk(foreign_chunk_id, foreign_document_id, 0, "foreign source chunk")
        }
    ));

    const auto owned_foreign_manifest = manifest_storage.find_manifest(foreign_resource_id);
    if(!owned_foreign_manifest) {
        return fail("foreign manifest fixture must publish an owned baseline");
    }

    auto foreign_manifest = *owned_foreign_manifest;
    foreign_manifest.records.push_back(agent_memory::DerivedRecordRef{
        agent_memory::DerivedRecordKind::Document,
        {},
        "doc:indexer:unrelated",
        99
    });
    foreign_manifest.records.push_back(agent_memory::DerivedRecordRef{
        agent_memory::DerivedRecordKind::BinaryBucketPosting,
        {},
        "bucket:indexer:foreign",
        99
    });
    foreign_manifest.records.push_back(agent_memory::DerivedRecordRef{
        agent_memory::DerivedRecordKind::LexicalPosting,
        {},
        "lexical:indexer:foreign",
        99
    });
    foreign_manifest.records.push_back(agent_memory::DerivedRecordRef{
        agent_memory::DerivedRecordKind::GraphRecord,
        {},
        "graph:indexer:foreign",
        99
    });
    foreign_manifest.records.push_back(agent_memory::DerivedRecordRef{
        agent_memory::DerivedRecordKind::Custom,
        {},
        "custom:indexer:foreign",
        99
    });
    if(!agent_memory::is_valid_resource_manifest(foreign_manifest)) {
        return fail("foreign manifest fixture must remain generically valid");
    }
    manifest_storage.inject_unchecked_manifest(std::move(foreign_manifest));

    manifest_storage.reset_operation_counts();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(make_snapshot(
            foreign_resource_id,
            2,
            agent_memory::DocumentId{"doc:indexer:foreign:replacement"},
            {}
        ));
        return fail("resource indexer must reject a generically valid foreign manifest");
    } catch(const agent_memory::ResourceIndexManifestCompatibilityError& error) {
        if(error.reason() != agent_memory::ResourceIndexManifestCompatibilityReason::InvalidTopology) {
            return fail("foreign topology must report an invalid-topology compatibility error");
        }
    }

    if(
        embedder.call_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        !document_storage.find_document(foreign_document_id) ||
        !vector_index.find(foreign_chunk_id)
    ) {
        return fail("foreign manifest reindex rejection must occur before storage mutation");
    }

    manifest_storage.reset_operation_counts();
    try {
        (void)indexer.erase_resource(foreign_resource_id);
        return fail("resource indexer erase must reject a foreign manifest");
    } catch(const agent_memory::ResourceIndexManifestCompatibilityError& error) {
        if(error.reason() != agent_memory::ResourceIndexManifestCompatibilityReason::InvalidTopology) {
            return fail("foreign erase must report an invalid-topology compatibility error");
        }
    }

    if(
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        !document_storage.find_document(foreign_document_id) ||
        !vector_index.find(foreign_chunk_id)
    ) {
        return fail("foreign manifest erase rejection must occur before storage mutation");
    }

    const agent_memory::ResourceId foreign_schema_resource_id{
        "resource:indexer:foreign-schema"
    };
    const agent_memory::DocumentId foreign_schema_document_id{
        "doc:indexer:foreign-schema"
    };
    const agent_memory::ChunkId foreign_schema_chunk_id{
        "chunk:indexer:foreign-schema"
    };
    indexer.reindex_resource(make_snapshot(
        foreign_schema_resource_id,
        1,
        foreign_schema_document_id,
        {
            make_chunk(
                foreign_schema_chunk_id,
                foreign_schema_document_id,
                0,
                "foreign schema chunk"
            )
        }
    ));
    const auto owned_foreign_schema_manifest = manifest_storage.find_manifest(
        foreign_schema_resource_id
    );
    if(!owned_foreign_schema_manifest) {
        return fail("foreign-schema fixture must publish an owned baseline");
    }

    auto foreign_schema_manifest = *owned_foreign_schema_manifest;
    foreign_schema_manifest.schema = agent_memory::ResourceManifestSchema{
        "third_party.resource_indexer",
        1
    };
    if(!agent_memory::is_valid_resource_manifest(foreign_schema_manifest)) {
        return fail("foreign-schema fixture must remain generically valid");
    }
    manifest_storage.inject_unchecked_manifest(std::move(foreign_schema_manifest));

    manifest_storage.reset_operation_counts();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(make_snapshot(
            foreign_schema_resource_id,
            2,
            agent_memory::DocumentId{"doc:indexer:foreign-schema:replacement"},
            {}
        ));
        return fail("resource indexer must reject a V5 manifest owned by another indexer");
    } catch(const agent_memory::ResourceIndexManifestCompatibilityError& error) {
        if(error.reason() != agent_memory::ResourceIndexManifestCompatibilityReason::ForeignSchema) {
            return fail("foreign owner schema must report a foreign-schema compatibility error");
        }
    }

    try {
        (void)indexer.erase_resource(foreign_schema_resource_id);
        return fail("resource indexer erase must reject a V5 manifest owned by another indexer");
    } catch(const agent_memory::ResourceIndexManifestCompatibilityError& error) {
        if(error.reason() != agent_memory::ResourceIndexManifestCompatibilityReason::ForeignSchema) {
            return fail("foreign owner schema erase must report a foreign-schema compatibility error");
        }
    }

    if(
        embedder.call_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        !document_storage.find_document(foreign_schema_document_id) ||
        !vector_index.find(foreign_schema_chunk_id)
    ) {
        return fail("foreign owner schema must be rejected before storage mutation");
    }

    const agent_memory::ResourceId legacy_format_resource_id{"resource:indexer:legacy-format"};
    const agent_memory::DocumentId legacy_format_document_id{"doc:indexer:legacy-format"};
    const agent_memory::ChunkId legacy_format_chunk_id{"chunk:indexer:legacy-format"};
    indexer.reindex_resource(make_snapshot(
        legacy_format_resource_id,
        1,
        legacy_format_document_id,
        {
            make_chunk(legacy_format_chunk_id, legacy_format_document_id, 0, "legacy format chunk")
        }
    ));
    const auto current_format_manifest = manifest_storage.find_manifest(legacy_format_resource_id);
    if(!current_format_manifest) {
        return fail("legacy-format fixture must publish an owned baseline");
    }

    auto legacy_format_manifest = *current_format_manifest;
    legacy_format_manifest.payload_version = agent_memory::ResourceManifestPayloadVersion::V4;
    legacy_format_manifest.schema = {};
    manifest_storage.inject_unchecked_manifest(std::move(legacy_format_manifest));

    manifest_storage.reset_operation_counts();
    embedder.reset_call_count();
    try {
        indexer.reindex_resource(make_snapshot(
            legacy_format_resource_id,
            2,
            agent_memory::DocumentId{"doc:indexer:legacy-format:replacement"},
            {}
        ));
        return fail("resource indexer must require migration for legacy payloads");
    } catch(const agent_memory::ResourceIndexManifestCompatibilityError& error) {
        if(error.reason() != agent_memory::ResourceIndexManifestCompatibilityReason::LegacyPayload) {
            return fail("legacy payload must report a migration-required compatibility error");
        }
    }

    try {
        (void)indexer.erase_resource(legacy_format_resource_id);
        return fail("resource indexer erase must require legacy payload migration");
    } catch(const agent_memory::ResourceIndexManifestCompatibilityError& error) {
        if(error.reason() != agent_memory::ResourceIndexManifestCompatibilityReason::LegacyPayload) {
            return fail("legacy erase must report a migration-required compatibility error");
        }
    }

    if(
        embedder.call_count() != 0 ||
        manifest_storage.upsert_count() != 0 ||
        manifest_storage.erase_count() != 0 ||
        !document_storage.find_document(legacy_format_document_id) ||
        !vector_index.find(legacy_format_chunk_id)
    ) {
        return fail("legacy manifest rejection must occur before storage mutation");
    }

    return 0;
}
