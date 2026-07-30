#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_INDEX_RECORD_OWNER_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_INDEX_RECORD_OWNER_STORAGE_HPP_INCLUDED

/// \file MdbxResourceIndexRecordOwnerStorage.hpp
/// \brief MDBX-backed ResourceIndexer physical-record ownership adapter.

#include <agent_memory/storage/IResourceIndexRecordOwnerStorage.hpp>

#include <memory>
#include <optional>
#include <string>

namespace agent_memory {

    /// \brief Configuration for MDBX-backed ResourceIndexer owner bindings.
    struct MdbxResourceIndexRecordOwnerStorageOptions final {
        std::string path;
        std::string table_prefix;
        bool relative_to_exe = true;
    };

    /// \brief MDBX-backed application-local physical-record ownership bindings.
    class MdbxResourceIndexRecordOwnerStorage final
        : public IResourceIndexRecordOwnerStorage {
    public:
        explicit MdbxResourceIndexRecordOwnerStorage(
            MdbxResourceIndexRecordOwnerStorageOptions options
        );
        ~MdbxResourceIndexRecordOwnerStorage() override;

        MdbxResourceIndexRecordOwnerStorage(
            const MdbxResourceIndexRecordOwnerStorage&
        ) = delete;
        MdbxResourceIndexRecordOwnerStorage& operator=(
            const MdbxResourceIndexRecordOwnerStorage&
        ) = delete;
        MdbxResourceIndexRecordOwnerStorage(
            MdbxResourceIndexRecordOwnerStorage&& other
        ) noexcept;
        MdbxResourceIndexRecordOwnerStorage& operator=(
            MdbxResourceIndexRecordOwnerStorage&& other
        ) noexcept;

        [[nodiscard]] std::optional<ResourceIndexRecordOwner> find_document_owner(
            const DocumentId& document_id
        ) const override;
        [[nodiscard]] std::optional<ResourceIndexRecordOwner> find_chunk_owner(
            const ChunkId& chunk_id
        ) const override;
        void upsert_document_owner(
            DocumentId document_id,
            ResourceIndexRecordOwner owner
        ) override;
        void upsert_chunk_owner(
            ChunkId chunk_id,
            ResourceIndexRecordOwner owner
        ) override;
        [[nodiscard]] bool erase_document_owner(const DocumentId& document_id) override;
        [[nodiscard]] bool erase_chunk_owner(const ChunkId& chunk_id) override;

    private:
        class Impl;
        std::unique_ptr<Impl> m_impl;
    };

} // namespace agent_memory

#endif
