#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_FLAT_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_FLAT_STORAGE_HPP_INCLUDED

/// \file MdbxCanonicalFlatStorage.hpp
/// \brief Opt-in numeric flat physical layout for canonical content.

#include <agent_memory/storage/CanonicalContentStore.hpp>
#include <agent_memory/domain/CanonicalNavigation.hpp>
#include "MdbxCanonicalStorageMetrics.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageContext.hpp"

#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>

namespace agent_memory {

/// Two independent uint64 values, each big-endian, compared bytewise (flags 0).
/// Unlike native MDBX_INTEGERKEY, this 16-byte composite key is endian-independent.
[[nodiscard]] std::string canonical_flat_composite_key(std::uint64_t identity,
                                                       std::uint64_t revision);

/// \brief Options for the experimental numeric flat canonical layout.
struct MdbxCanonicalFlatStorageOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::size_t max_records = 1U * 1024U * 1024U;
    std::shared_ptr<MdbxStorageContext> context;
    MdbxCanonicalStorageMetrics* metrics = nullptr;
    /// Test/fault-injection seam, called inside the write transaction before head publication.
    /// Throwing aborts every write in that transaction. Must not reenter this store.
    std::function<void()> before_head_publication;
};

/// \brief Transactional, versioned flat canonical-content pilot.
///
/// Document and block storage addresses are allocated by the persistent
/// numeric identity mapping. Structure rows, immutable block-version rows and
/// the current head are published in one MDBX write transaction.
/// The class is opt-in and does not replace MdbxCanonicalContentStore.
class MdbxCanonicalFlatStorage final : public ICanonicalContentStore,
                                       public ICanonicalContentEditor {
  public:
    explicit MdbxCanonicalFlatStorage(MdbxCanonicalFlatStorageOptions options);
    ~MdbxCanonicalFlatStorage() override;
    MdbxCanonicalFlatStorage(const MdbxCanonicalFlatStorage&) = delete;
    MdbxCanonicalFlatStorage& operator=(const MdbxCanonicalFlatStorage&) = delete;

    [[nodiscard]] bool create_document(CanonicalDocumentRevision initial) override;
    [[nodiscard]] std::optional<CanonicalDocumentRevision> read_current(
        const DocumentId&) const override;
    [[nodiscard]] std::optional<CanonicalDocumentRevision> read_revision(
        const DocumentId&, std::uint64_t) const override;
    [[nodiscard]] std::optional<ContentBlock> read_block(
        const DocumentId&, const ContentBlockId&,
        std::optional<std::uint64_t> = std::nullopt) const override;
    [[nodiscard]] std::optional<std::string> materialize_markdown(
        const DocumentId&, std::optional<std::uint64_t> = std::nullopt) const override;
    [[nodiscard]] CanonicalEditResult commit(
        const CanonicalEditRequest&) override;

    /// Reads O(V+H) structure/ledger entries and validates them in O((V+H) log(V+H)),
    /// where V is the active block count and H is the historical identity count,
    /// without reading block payload values. Corruption throws; absence/budget returns no value.
    [[nodiscard]] std::optional<CanonicalOutline> read_outline(
        const DocumentId&, std::uint64_t, std::size_t max_entries = 4096) const;
    [[nodiscard]] CanonicalChildrenResult read_children(
        const DocumentId&, std::uint64_t, std::optional<ContentBlockId>,
        std::size_t max_children = 4096) const;
    /// Reads the structure once and only the selected payload values in one snapshot.
    [[nodiscard]] CanonicalSubtreeResult read_subtree(
        const DocumentId&, std::uint64_t, const CanonicalSubtreeRequest&) const;

  private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
