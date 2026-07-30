#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_IRESOURCE_INDEX_RECORD_OWNER_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_IRESOURCE_INDEX_RECORD_OWNER_STORAGE_HPP_INCLUDED

/// \file IResourceIndexRecordOwnerStorage.hpp
/// \brief Ownership bindings for physical records managed by ResourceIndexer.

#include <agent_memory/domain/Resource.hpp>

#include <cstdint>
#include <optional>

namespace agent_memory {

    /// \brief ResourceIndexer ownership evidence for one physical record identity.
    struct ResourceIndexRecordOwner final {
        ResourceId resource_id;
        std::uint64_t generation = 0;
        ResourceManifestSchema manifest_schema;
    };

    /// \brief Returns true when an ownership binding has complete identity evidence.
    [[nodiscard]] bool is_valid_resource_index_record_owner(
        const ResourceIndexRecordOwner& owner
    ) noexcept;

    /// \brief Persists application-owned bindings for ResourceIndexer physical records.
    ///
    /// Document and chunk bindings are intentionally separate from user metadata
    /// and generic retrieval records. Implementations must provide a strong
    /// exception guarantee for each mutating operation.
    class IResourceIndexRecordOwnerStorage {
    public:
        virtual ~IResourceIndexRecordOwnerStorage();

        [[nodiscard]] virtual std::optional<ResourceIndexRecordOwner> find_document_owner(
            const DocumentId& document_id
        ) const = 0;

        [[nodiscard]] virtual std::optional<ResourceIndexRecordOwner> find_chunk_owner(
            const ChunkId& chunk_id
        ) const = 0;

        virtual void upsert_document_owner(
            DocumentId document_id,
            ResourceIndexRecordOwner owner
        ) = 0;

        virtual void upsert_chunk_owner(
            ChunkId chunk_id,
            ResourceIndexRecordOwner owner
        ) = 0;

        [[nodiscard]] virtual bool erase_document_owner(const DocumentId& document_id) = 0;
        [[nodiscard]] virtual bool erase_chunk_owner(const ChunkId& chunk_id) = 0;
    };

} // namespace agent_memory

#endif
