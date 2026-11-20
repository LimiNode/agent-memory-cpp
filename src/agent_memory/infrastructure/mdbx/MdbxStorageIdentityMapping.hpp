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

namespace mdbxc {
class Transaction;
}

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

    /// Allocates or resolves a document mapping in a caller-owned writable
    /// transaction.  The caller owns commit/rollback and may combine this
    /// operation with canonical-content and index writes.
    /// The transaction must be active, writable and from this context's
    /// connection; otherwise std::invalid_argument is thrown before access.
    /// MDBX serializes these writes; no instance mutex is acquired while the
    /// caller holds its writer, avoiding lock-order inversion with owned writes.
    [[nodiscard]] DocumentStorageId ensure_document_in_transaction(
        const DocumentId& document_id,
        mdbxc::Transaction& transaction
    );

    /// Allocates or resolves a block mapping in a caller-owned writable
    /// transaction.  The parent document must already be admitted in the same
    /// transaction or in an earlier committed state.
    /// The same transaction provenance requirements apply as for documents.
    [[nodiscard]] BlockStorageId ensure_block_in_transaction(
        const DocumentId& document_id,
        const ContentBlockId& block_id,
        mdbxc::Transaction& transaction
    );
    [[nodiscard]] std::optional<DocumentStorageId> find_document(
        const DocumentId& document_id
    ) const override;
    [[nodiscard]] std::optional<BlockStorageId> find_block(
        const DocumentId& document_id,
        const ContentBlockId& block_id
    ) const override;

    /// Reads a document mapping in the caller's active transaction from this
    /// context's connection. Retired mappings remain addressable for history.
    /// Foreign, inactive or blocked transactions throw std::invalid_argument,
    /// including when the supplied identity is empty. Never commits the caller's transaction.
    [[nodiscard]] std::optional<DocumentStorageId> find_document_in_transaction(
        const DocumentId& document_id, const mdbxc::Transaction& transaction
    ) const;

    /// Reads a block mapping with the same transaction requirements and
    /// historical tombstone behavior as find_document_in_transaction().
    [[nodiscard]] std::optional<BlockStorageId> find_block_in_transaction(
        const DocumentId& document_id, const ContentBlockId& block_id,
        const mdbxc::Transaction& transaction
    ) const;
    [[nodiscard]] bool retire_document(const DocumentId& document_id) override;
    [[nodiscard]] bool retire_block(
        const DocumentId& document_id,
        const ContentBlockId& block_id
    ) override;
    /// Returns at most max_records rows; oversized state is rejected from
    /// MDBX statistics before traversal or allocation. Traversal is bounded.
    [[nodiscard]] std::vector<StorageIdentityRecord> snapshot_records() const override;
    /// Imports a validated snapshot only into a fresh, empty namespace with
    /// no sequence history. Live, retired, partial, deletion and even identical
    /// replacement snapshots are rejected with std::invalid_argument. Import
    /// into another empty namespace is the explicit remapping boundary.
    /// This is stricter than the backend-neutral in-memory loader.
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
