#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_CONTENT_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_CONTENT_STORE_HPP_INCLUDED

#include <agent_memory/storage/CanonicalContentStore.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include "MdbxStorageContext.hpp"
#include <memory>
#include <string>

namespace agent_memory {

struct MdbxCanonicalContentStoreOptions final {
    /// MDBX environment path used when no shared context is supplied.
    std::string path;
    /// Prefix for the four C1 canonical-content DBIs.
    std::string table_prefix = "agent_memory";
    /// Resolve a relative owned path against the executable directory.
    bool relative_to_exe = false;
    /// Optional host/shared context. Its connection lifecycle stays host-owned.
    std::shared_ptr<MdbxStorageContext> context;
};

/// Durable raw/plain canonical content store. C2 physical re-encoding is not
/// part of this adapter; all accepted edits use one MDBX write transaction.
class MdbxCanonicalContentStore final : public ICanonicalContentStore,
                                         public ICanonicalContentEditor {
public:
    explicit MdbxCanonicalContentStore(MdbxCanonicalContentStoreOptions options);
    ~MdbxCanonicalContentStore() override;
    MdbxCanonicalContentStore(const MdbxCanonicalContentStore&) = delete;
    MdbxCanonicalContentStore& operator=(const MdbxCanonicalContentStore&) = delete;

    bool create_document(CanonicalDocumentRevision initial) override;
    [[nodiscard]] std::optional<CanonicalDocumentRevision> read_current(const DocumentId&) const override;
    [[nodiscard]] std::optional<CanonicalDocumentRevision> read_revision(const DocumentId&, std::uint64_t) const override;
    [[nodiscard]] std::optional<ContentBlock> read_block(const DocumentId&, const ContentBlockId&, std::optional<std::uint64_t> = std::nullopt) const override;
    [[nodiscard]] std::optional<std::string> materialize_markdown(const DocumentId&, std::optional<std::uint64_t> = std::nullopt) const override;
    [[nodiscard]] CanonicalEditResult commit(const CanonicalEditRequest&) override;

private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory
#endif

#endif
