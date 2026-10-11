#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_FEDERATED_CANONICAL_READER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_FEDERATED_CANONICAL_READER_HPP_INCLUDED

/// \file MdbxFederatedCanonicalReader.hpp
/// \brief Bounded canonical reads over explicitly selected MDBX workspaces.

#include "MdbxWorkspaceStorageRegistry.hpp"

#if AGENT_MEMORY_HAS_MDBX
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

/// Outcome of one explicitly selected workspace route.
enum class MdbxFederatedRouteStatus {
    Found,
    NotFound,
    Unavailable,
};

/// Aggregate outcome across the selected workspace routes.
enum class MdbxFederatedReadStatus {
    Complete,
    Partial,
    Unavailable,
};

/// Stable provenance retained for every route result.
struct MdbxFederatedReadProvenance final {
    std::string workspace_id;
    std::uint64_t placement_generation = 0;
};

/// Result for one selected workspace, including failures that must not be
/// collapsed into an empty canonical read.
struct MdbxFederatedRouteResult final {
    MdbxFederatedReadProvenance provenance;
    MdbxFederatedRouteStatus status = MdbxFederatedRouteStatus::NotFound;
    std::optional<CanonicalDocumentRevision> revision;
    /// Safe diagnostic category for a trusted caller. Backend exception text is
    /// intentionally not copied into this field or exposed to clients.
    std::string diagnostic;
};

/// Request for one document across an explicit, bounded set of workspaces.
///
/// The caller must authorize every workspace before constructing this request.
/// This first R1 slice is a trusted internal operation; the reader validates
/// placement identity and generation but does not implement client permissions.
struct MdbxFederatedCanonicalReadRequest final {
    DocumentId document_id;
    std::optional<std::uint64_t> revision;
    std::vector<MdbxFederatedReadProvenance> routes;
};

/// Deterministic canonical reads with per-route completion and provenance.
///
/// Revisions remain attached to their route result. There is deliberately no
/// second flat document vector because local document IDs may collide across
/// workspaces.
struct MdbxFederatedCanonicalReadResult final {
    MdbxFederatedReadStatus status = MdbxFederatedReadStatus::Complete;
    std::vector<MdbxFederatedRouteResult> routes;
};

/// \brief Reads canonical documents from explicitly selected placements.
///
/// The reader performs no path inference, global workspace scan or cross-MDBX
/// transaction. Unknown and stale placements are rejected by the registry
/// before any backend read; backend failures remain visible as unavailable
/// route results. Authorization is a host/runtime responsibility and is not
/// inferred from placement validity.
class MdbxFederatedCanonicalReader final {
  public:
    /// Maximum number of explicit routes accepted by one read.
    inline static constexpr std::size_t kMaxRoutes = 64;

    explicit MdbxFederatedCanonicalReader(const MdbxWorkspaceStorageRegistry& registry) noexcept;

    [[nodiscard]] MdbxFederatedCanonicalReadResult read(
        const MdbxFederatedCanonicalReadRequest& request) const;

  private:
    const MdbxWorkspaceStorageRegistry* m_registry;
};

} // namespace agent_memory
#endif

#endif
