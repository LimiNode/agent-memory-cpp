#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_VERIFIED_ARTIFACT_HYDRATION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_VERIFIED_ARTIFACT_HYDRATION_HPP_INCLUDED

/// \file MdbxVerifiedArtifactHydration.hpp
/// \brief Exact-revision, scope-checked hydration over ArtifactCatalog + Raw CAS.

#include "MdbxArtifactCatalog.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <cstdint>
#include <optional>
#include <string>

namespace agent_memory {

enum class VerifiedHydrationStatus : std::uint8_t {
    Hydrated,
    SummaryOnly,
    Missing,
    Corrupt,
    Stale,
    WrongScope,
    Unavailable,
    Unsupported,
    InvalidArgument
};

struct VerifiedHydrationRequest final {
    SourceRevisionId source_revision_id;
    ArtifactBindingId binding_id;
    /// Scope is supplied by the caller because Raw ArtifactCatalog bindings
    /// are intentionally scope-neutral. Both values must match before bytes
    /// can be exposed.
    std::string expected_scope;
    std::string binding_scope;
    std::optional<SourceRevisionId> current_source_revision_id;
    std::optional<ArtifactId> expected_artifact_id;
    std::optional<BlobDigest> expected_digest;
    std::uint64_t offset = 0;
    std::uint64_t length = 0;
    bool allow_summary_only = true;
};

struct VerifiedHydrationResult final {
    VerifiedHydrationStatus status = VerifiedHydrationStatus::InvalidArgument;
    SourceRevisionId source_revision_id;
    ArtifactBindingId binding_id;
    ArtifactId artifact_id;
    std::uint64_t offset = 0;
    std::string bytes;
    std::string diagnostic;
};

/// \brief Hydrates bytes only after exact identity, freshness, scope, receipt
/// and Raw CAS integrity checks have passed.
class MdbxVerifiedArtifactHydration final {
public:
    explicit MdbxVerifiedArtifactHydration(const MdbxArtifactCatalog& catalog)
        : m_catalog(catalog) {}

    [[nodiscard]] VerifiedHydrationResult hydrate(
        const VerifiedHydrationRequest& request) const;

private:
    const MdbxArtifactCatalog& m_catalog;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
