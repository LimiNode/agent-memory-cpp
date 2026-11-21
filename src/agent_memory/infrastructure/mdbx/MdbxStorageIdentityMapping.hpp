#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_STORAGE_IDENTITY_MAPPING_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_STORAGE_IDENTITY_MAPPING_HPP_INCLUDED

/// \file MdbxStorageIdentityMapping.hpp
/// \brief Transactional MDBX pilot for persistent numeric identity mappings.

#include <agent_memory/storage/StorageIdentityMapping.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <utility>

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageContext.hpp"

namespace agent_memory {

/// \brief Configuration for the opt-in persistent identity dictionary.
struct MdbxStorageIdentityMappingOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::size_t max_records = 1U * 1024U * 1024U;
    std::shared_ptr<MdbxStorageContext> context;
};

/// \brief Transactional persistent pilot for StorageIdentityMapping.
///
/// Logical identities remain in the domain layer.  Every intern, retirement,
/// reverse mapping and next-id update is committed in one MDBX write
/// transaction.  The class is deliberately opt-in and is not wired into any
/// production storage default.
class MdbxStorageIdentityMapping final : public IStorageIdentityMapping {
public:
    explicit MdbxStorageIdentityMapping(MdbxStorageIdentityMappingOptions options);
    ~MdbxStorageIdentityMapping() override;

    MdbxStorageIdentityMapping(const MdbxStorageIdentityMapping&) = delete;
    MdbxStorageIdentityMapping& operator=(const MdbxStorageIdentityMapping&) = delete;

    [[nodiscard]] DocumentStorageId ensure_document(const DocumentId& document_id) override;
    [[nodiscard]] BlockStorageId ensure_block(
        const DocumentId& document_id,
        const ContentBlockId& block_id
    ) override;
    [[nodiscard]] std::optional<DocumentStorageId> find_document(
        const DocumentId& document_id
    ) const override;
    [[nodiscard]] std::optional<BlockStorageId> find_block(
        const DocumentId& document_id,
        const ContentBlockId& block_id
    ) const override;
    [[nodiscard]] bool retire_document(const DocumentId& document_id) override;
    [[nodiscard]] bool retire_block(
        const DocumentId& document_id,
        const ContentBlockId& block_id
    ) override;
    [[nodiscard]] std::vector<StorageIdentityRecord> snapshot_records() const override;
    void load_records(const std::vector<StorageIdentityRecord>& records) override;

    /// Resolves a physical document id from one consistent read snapshot.
    [[nodiscard]] std::optional<DocumentId> resolve_document(DocumentStorageId id) const;

    /// Resolves a physical block id from one consistent read snapshot.
    [[nodiscard]] std::optional<std::pair<DocumentId, ContentBlockId>> resolve_block(
        BlockStorageId id
    ) const;

private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
