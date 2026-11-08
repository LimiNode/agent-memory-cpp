#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_VERIFIED_ARTIFACT_HYDRATION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_VERIFIED_ARTIFACT_HYDRATION_HPP_INCLUDED

/// \file MdbxVerifiedArtifactHydration.hpp
/// \brief Integrity-verified byte materialization over ArtifactCatalog + Raw CAS.

#include "MdbxArtifactCatalog.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <cstdint>
#include <limits>
#include <optional>
#include <string>

namespace agent_memory {

enum class VerifiedArtifactMaterializationStatus : std::uint8_t {
    Materialized,
    Missing,
    Corrupt,
    BindingRevisionMismatch,
    VerificationBudgetExceeded,
    Unavailable,
    Unsupported,
    InvalidArgument
};

struct VerifiedArtifactMaterializationRequest final {
    SourceRevisionId source_revision_id;
    ArtifactBindingId binding_id;
    std::optional<ArtifactId> expected_artifact_id;
    std::optional<BlobDigest> expected_digest;
    std::uint64_t offset = 0;
    std::uint64_t length = 0;
    std::uint64_t verification_budget_bytes =
        (std::numeric_limits<std::uint64_t>::max)();
};

struct VerifiedArtifactMaterializationResult final {
    VerifiedArtifactMaterializationStatus status =
        VerifiedArtifactMaterializationStatus::InvalidArgument;
    SourceRevisionId source_revision_id;
    ArtifactBindingId binding_id;
    ArtifactId artifact_id;
    std::uint64_t offset = 0;
    std::string bytes;
    std::string diagnostic;
};

/// \brief Materializes bytes after exact binding identity and Raw CAS checks.
///
/// This adapter is deliberately not an authorization or current-revision
/// reader. Callers must obtain trusted scope/freshness admission separately;
/// the future authoritative path must consume a real snapshot admission
/// context rather than caller-provided matching strings. In particular,
/// `Materialized` says nothing about authorization or currentness. The
/// verification budget bounds the physical CAS bytes scanned, not merely the
/// returned range.
class MdbxArtifactMaterializer final {
public:
    explicit MdbxArtifactMaterializer(const MdbxArtifactCatalog& catalog)
        : m_catalog(catalog) {}

    [[nodiscard]] VerifiedArtifactMaterializationResult materialize(
        const VerifiedArtifactMaterializationRequest& request) const;

private:
    const MdbxArtifactCatalog& m_catalog;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
