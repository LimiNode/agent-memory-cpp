#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_CONTENT_ROUTER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_CONTENT_ROUTER_HPP_INCLUDED

/// \file MdbxCanonicalContentRouter.hpp
/// \brief Explicit workspace routing for canonical MDBX content.
///
/// Every operation names a logical workspace and expected placement generation.
/// The router performs one exact registry lookup and delegates to that
/// workspace-local store; it never falls back to another context.

#include "MdbxWorkspaceStorageRegistry.hpp"

#if AGENT_MEMORY_HAS_MDBX
namespace agent_memory {

/// \brief Domain-specific R0 router for canonical-content operations.
///
/// The registry must outlive this non-owning router. Physical context identity
/// is deliberately not added to document IDs or canonical domain objects.
///
/// \see `guides/storage-backend-integration-roadmap.md`
class MdbxCanonicalContentRouter final {
  public:
    explicit MdbxCanonicalContentRouter(MdbxWorkspaceStorageRegistry& registry) noexcept;

    /// Creates a document in the explicitly resolved workspace placement.
    /// \throws MdbxUnknownWorkspaceError or MdbxStalePlacementError before dispatch.
    [[nodiscard]] bool create_document(const std::string& workspace_id,
                                       std::uint64_t placement_generation,
                                       CanonicalDocumentRevision initial);
    /// Reads the current revision from the explicitly resolved workspace.
    [[nodiscard]] std::optional<CanonicalDocumentRevision>
    read_current(const std::string& workspace_id,
                 std::uint64_t placement_generation,
                 const DocumentId& document_id) const;
    /// Reads one immutable historical revision from the explicitly resolved workspace.
    [[nodiscard]] std::optional<CanonicalDocumentRevision>
    read_revision(const std::string& workspace_id,
                  std::uint64_t placement_generation,
                  const DocumentId& document_id,
                  std::uint64_t revision) const;
    /// Reads one block from the current or requested workspace revision.
    [[nodiscard]] std::optional<ContentBlock>
    read_block(const std::string& workspace_id,
               std::uint64_t placement_generation,
               const DocumentId& document_id,
               const ContentBlockId& block_id,
               std::optional<std::uint64_t> revision = std::nullopt) const;
    /// Materializes deterministic Markdown from the explicitly resolved workspace.
    [[nodiscard]] std::optional<std::string>
    materialize_markdown(const std::string& workspace_id,
                         std::uint64_t placement_generation,
                         const DocumentId& document_id,
                         std::optional<std::uint64_t> revision = std::nullopt) const;
    /// Atomically commits one optimistic edit in the explicitly resolved workspace.
    [[nodiscard]] CanonicalEditResult commit(const std::string& workspace_id,
                                             std::uint64_t placement_generation,
                                             const CanonicalEditRequest& request);

  private:
    [[nodiscard]] const MdbxCanonicalContentStore& store(
        const std::string& workspace_id, std::uint64_t placement_generation) const;
    [[nodiscard]] MdbxCanonicalContentStore& mutable_store(
        const std::string& workspace_id, std::uint64_t placement_generation);

    MdbxWorkspaceStorageRegistry* m_registry; ///< Non-owning immutable-placement registry.
};

} // namespace agent_memory
#endif

#endif
