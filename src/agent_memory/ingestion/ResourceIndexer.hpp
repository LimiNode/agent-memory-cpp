#pragma once
#ifndef AGENT_MEMORY_HEADER_INGESTION_RESOURCE_INDEXER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INGESTION_RESOURCE_INDEXER_HPP_INCLUDED

/// \file ResourceIndexer.hpp
/// \brief Resource indexing orchestration over storage, embedding, and index contracts.

#include <agent_memory/domain/Resource.hpp>
#include <agent_memory/storage/IDocumentStorage.hpp>
#include <agent_memory/storage/IResourceManifestStorage.hpp>

#include <exception>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <vector>

namespace agent_memory {

    class IEmbedder;
    class IVectorIndex;

    /// \brief Pre-chunked resource state ready to be indexed.
    struct ResourceIndexSnapshot final {
        ResourceRevision revision;
        DocumentSnapshot document_snapshot;
    };

    enum class ResourceIndexRecoveryStage : std::uint8_t {
        VectorRestore,
        DocumentRestore
    };

    enum class ResourceIndexRecoveryOperation : std::uint8_t {
        UpsertPrevious,
        EraseAttempted
    };

    /// \brief One failed best-effort recovery operation.
    struct ResourceIndexRecoveryFailure final {
        ResourceIndexRecoveryStage stage = ResourceIndexRecoveryStage::VectorRestore;
        ResourceIndexRecoveryOperation operation = ResourceIndexRecoveryOperation::EraseAttempted;
        std::optional<DocumentId> document_id;
        std::optional<ChunkId> chunk_id;
        std::exception_ptr failure;
    };

    /// \brief Reports that an in-process reindex rollback could not complete.
    ///
    /// The caller must repair or rebuild the affected derived records before
    /// treating the prototype stores as synchronized again.
    class ResourceIndexRollbackError final : public std::runtime_error {
    public:
        ResourceIndexRollbackError(
            std::exception_ptr original_failure,
            std::vector<ResourceIndexRecoveryFailure> recovery_failures
        );

        [[nodiscard]] const std::exception_ptr& original_failure() const noexcept;
        [[nodiscard]] const std::exception_ptr& rollback_failure() const noexcept;
        [[nodiscard]] const std::vector<ResourceIndexRecoveryFailure>& recovery_failures()
            const noexcept;

    private:
        std::exception_ptr m_original_failure;
        std::vector<ResourceIndexRecoveryFailure> m_recovery_failures;
    };

    /// \brief Reports cleanup failure after a replacement manifest was published.
    class ResourceIndexReclaimError final : public std::runtime_error {
    public:
        ResourceIndexReclaimError(
            ResourceManifest published_manifest,
            ResourceManifest unreclaimed_manifest,
            std::exception_ptr reclaim_failure
        );

        [[nodiscard]] const ResourceManifest& published_manifest() const noexcept;
        [[nodiscard]] const ResourceManifest& unreclaimed_manifest() const noexcept;
        [[nodiscard]] const std::exception_ptr& reclaim_failure() const noexcept;

    private:
        ResourceManifest m_published_manifest;
        ResourceManifest m_unreclaimed_manifest;
        std::exception_ptr m_reclaim_failure;
    };

    /// \brief Pre-chunked dense/vector prototype for targeted resource indexing.
    ///
    /// This is not the lexical-first public M0 resource importer contract.
    /// The prototype serializes calls made through one instance, rejects stale
    /// generations, and compensates records written by a throwing in-process
    /// attempt. Failed compensation and post-publication reclamation are
    /// reported with typed errors so the caller can repair derived stores.
    /// Deletion first persists an erase-pending manifest, which is not eligible
    /// for retrieval, and retries its cleanup on the next erase call. Dependency
    /// interfaces do not provide a cross-store transaction, so callers still
    /// need the M0 importer for crash-atomic publication across processes or
    /// independently constructed indexers.
    class IResourceIndexer {
    public:
        virtual ~IResourceIndexer();

        /// \brief Inserts or replaces all derived records for one resource.
        /// \pre `snapshot.revision.resource_id` must not be empty.
        /// \pre `snapshot.document_snapshot.document.id` is globally unique and is
        /// never reused by a different resource revision.
        /// \pre Each chunk in `snapshot.document_snapshot` must belong to its document.
        virtual void reindex_resource(ResourceIndexSnapshot snapshot) = 0;

        /// \brief Removes all known derived records for one resource.
        /// \return True when a manifest was found and removal was completed.
        /// \note A cleanup failure leaves an erase-pending manifest that is not
        /// eligible for retrieval and can be retried by calling this method.
        [[nodiscard]] virtual bool erase_resource(const ResourceId& resource_id) = 0;
    };

    /// \brief Basic pre-chunked dense/vector indexer prototype.
    class ResourceIndexer final : public IResourceIndexer {
    public:
        ResourceIndexer(
            IDocumentStorage& document_storage,
            IResourceManifestStorage& manifest_storage,
            IEmbedder& embedder,
            IVectorIndex& vector_index
        );

        ResourceIndexer(const ResourceIndexer&) = delete;
        ResourceIndexer& operator=(const ResourceIndexer&) = delete;
        ResourceIndexer(ResourceIndexer&&) = delete;
        ResourceIndexer& operator=(ResourceIndexer&&) = delete;

        void reindex_resource(ResourceIndexSnapshot snapshot) override;

        [[nodiscard]] bool erase_resource(const ResourceId& resource_id) override;

    private:
        void erase_derived_record(const DerivedRecordRef& record);
        void erase_derived_records(const ResourceManifest& manifest);
        void drain_pending_reclaim_records(ResourceManifest& manifest);

        IDocumentStorage* m_document_storage = nullptr;
        IResourceManifestStorage* m_manifest_storage = nullptr;
        IEmbedder* m_embedder = nullptr;
        IVectorIndex* m_vector_index = nullptr;
        std::mutex m_mutex;
    };

} // namespace agent_memory

#endif
