#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_WORKSPACE_STORAGE_REGISTRY_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_WORKSPACE_STORAGE_REGISTRY_HPP_INCLUDED

/// \file MdbxWorkspaceStorageRegistry.hpp
/// \brief Immutable logical-workspace to MDBX placement bindings.
///
/// R0 placement is deliberately explicit: a logical workspace is bound once to
/// one independently owned MDBX context and a routing generation. The binding
/// is never replaced in-place and lookup never infers a path or searches other
/// contexts. See `guides/storage-backend-integration-roadmap.md`.

#include "MdbxCanonicalContentStore.hpp"

#if AGENT_MEMORY_HAS_MDBX
#include <cstdint>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unordered_map>

namespace agent_memory {

class MdbxCanonicalContentRouter;

/// rief Base error for fail-closed workspace routing failures.
class MdbxWorkspaceRoutingError : public std::runtime_error {
  public:
    using std::runtime_error::runtime_error;
};

/// rief Raised when a workspace has no explicit placement binding.
class MdbxUnknownWorkspaceError final : public MdbxWorkspaceRoutingError {
  public:
    using MdbxWorkspaceRoutingError::MdbxWorkspaceRoutingError;
};

/// rief Raised when a caller presents an obsolete routing generation.
class MdbxStalePlacementError final : public MdbxWorkspaceRoutingError {
  public:
    using MdbxWorkspaceRoutingError::MdbxWorkspaceRoutingError;
};

/// rief Raised when create-only placement binding would be duplicated.
class MdbxPlacementConflictError final : public MdbxWorkspaceRoutingError {
  public:
    using MdbxWorkspaceRoutingError::MdbxWorkspaceRoutingError;
};

/// \brief One immutable R0 placement binding.
///
/// The logical workspace ID and routing generation are metadata for selecting
/// a context; neither becomes part of a canonical document's local identity.
class MdbxWorkspacePlacement final {
  public:
    /// Returns the immutable logical workspace key.
    [[nodiscard]] const std::string& workspace_id() const noexcept;
    /// Returns the immutable routing generation.
    [[nodiscard]] std::uint64_t generation() const noexcept;
    /// Returns the independently owned or attached MDBX context.
    [[nodiscard]] const std::shared_ptr<MdbxStorageContext>& context() const noexcept;
    /// Returns a read-only handle to the workspace-local canonical store.
    [[nodiscard]] std::shared_ptr<const MdbxCanonicalContentStore>
    canonical_content() const noexcept;

  private:
    friend class MdbxWorkspaceStorageRegistry;
    friend class MdbxCanonicalContentRouter;

    MdbxWorkspacePlacement(std::string workspace_id,
                           std::uint64_t generation,
                           std::shared_ptr<MdbxStorageContext> context,
                           std::shared_ptr<MdbxCanonicalContentStore> canonical_content);

    std::string m_workspace_id; ///< Stable logical workspace key.
    std::uint64_t m_generation; ///< Caller-visible routing generation.
    std::shared_ptr<MdbxStorageContext> m_context; ///< Independent MDBX lifecycle boundary.
    std::shared_ptr<MdbxCanonicalContentStore> m_canonical_content; ///< Workspace-local store.

    [[nodiscard]] const std::shared_ptr<MdbxCanonicalContentStore>&
    mutable_canonical_content() const noexcept;
};

/// \brief Registry for immutable, explicit workspace placements.
///
/// `bind` is create-only. A second binding for the same workspace is rejected,
/// including when it names the same context, so callers cannot silently mutate
/// an existing placement or create an ambiguous route. `resolve` is exact and
/// fail-closed for unknown or stale generations.
///
/// \see `guides/storage-backend-integration-roadmap.md`
class MdbxWorkspaceStorageRegistry final {
  public:
    MdbxWorkspaceStorageRegistry() = default;
    MdbxWorkspaceStorageRegistry(const MdbxWorkspaceStorageRegistry&) = delete;
    MdbxWorkspaceStorageRegistry& operator=(const MdbxWorkspaceStorageRegistry&) = delete;

    /// \brief Creates one immutable placement for `workspace_id`.
    /// \throws MdbxPlacementConflictError when the workspace is already bound.
    /// \throws MdbxWorkspaceRoutingError for invalid arguments.
    [[nodiscard]] std::shared_ptr<const MdbxWorkspacePlacement>
    bind(std::string workspace_id,
         std::shared_ptr<MdbxStorageContext> context,
         std::uint64_t generation = 1,
         std::string table_prefix = "agent_memory");

    /// \brief Resolves exactly one placement and validates its generation.
    /// \throws MdbxUnknownWorkspaceError when no binding exists.
    /// \throws MdbxStalePlacementError when `expected_generation` is not current.
    [[nodiscard]] std::shared_ptr<const MdbxWorkspacePlacement>
    resolve(const std::string& workspace_id, std::uint64_t expected_generation) const;

  private:
    mutable std::mutex m_mutex;
    std::unordered_map<std::string, std::shared_ptr<const MdbxWorkspacePlacement>> m_placements;
};

} // namespace agent_memory
#endif

#endif
