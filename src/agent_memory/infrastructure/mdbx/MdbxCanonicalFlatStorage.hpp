#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_FLAT_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_FLAT_STORAGE_HPP_INCLUDED

/// \file MdbxCanonicalFlatStorage.hpp
/// \brief Opt-in numeric flat physical layout for canonical content.

#include <agent_memory/storage/CanonicalContentStore.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageContext.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>

namespace agent_memory {

/// \brief Options for the experimental numeric flat canonical layout.
struct MdbxCanonicalFlatStorageOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::size_t max_records = 1U * 1024U * 1024U;
    std::shared_ptr<MdbxStorageContext> context;
};

/// \brief Transactional, versioned flat canonical-content pilot.
///
/// Document and block storage addresses are allocated by the persistent
/// numeric identity mapping. Structure rows, immutable block-version rows and
/// the current head are published in one caller-owned MDBX write transaction.
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

  private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
