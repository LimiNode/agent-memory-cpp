#pragma once
#ifndef AGENT_MEMORY_HEADER_INGESTION_RESOURCE_INDEXER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INGESTION_RESOURCE_INDEXER_HPP_INCLUDED

/// \file ResourceIndexer.hpp
/// \brief Resource indexing orchestration over storage, embedding, and index contracts.

#include <agent_memory/domain/Resource.hpp>
#include <agent_memory/storage/IDocumentStorage.hpp>
#include <agent_memory/storage/IResourceManifestStorage.hpp>

#include <mutex>

namespace agent_memory {

    class IEmbedder;
    class IVectorIndex;

    /// \brief Pre-chunked resource state ready to be indexed.
    struct ResourceIndexSnapshot final {
        ResourceRevision revision;
        DocumentSnapshot document_snapshot;
    };

    /// \brief Pre-chunked dense/vector prototype for targeted resource indexing.
    ///
    /// This is not the lexical-first public M0 resource importer contract.
    /// The prototype serializes calls made through one instance, rejects stale
    /// generations, and compensates records written by a throwing in-process
    /// attempt. Dependency interfaces do not provide a cross-store transaction,
    /// so callers still need the M0 importer for crash-atomic publication across
    /// processes or independently constructed indexers.
    class IResourceIndexer {
    public:
        virtual ~IResourceIndexer();

        /// \brief Inserts or replaces all derived records for one resource.
        /// \pre `snapshot.revision.resource_id` must not be empty.
        /// \pre Each chunk in `snapshot.document_snapshot` must belong to its document.
        virtual void reindex_resource(ResourceIndexSnapshot snapshot) = 0;

        /// \brief Removes all known derived records for one resource.
        /// \return True when a manifest was found and removed.
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

        void reindex_resource(ResourceIndexSnapshot snapshot) override;

        [[nodiscard]] bool erase_resource(const ResourceId& resource_id) override;

    private:
        void erase_derived_records(const ResourceManifest& manifest);
        void reclaim_superseded_derived_records(
            const ResourceManifest& old_manifest,
            const ResourceManifest& new_manifest
        );

        IDocumentStorage* m_document_storage = nullptr;
        IResourceManifestStorage* m_manifest_storage = nullptr;
        IEmbedder* m_embedder = nullptr;
        IVectorIndex* m_vector_index = nullptr;
        std::mutex m_mutex;
    };

} // namespace agent_memory

#endif
