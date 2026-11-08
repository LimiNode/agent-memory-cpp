#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_AUTHORITATIVE_ARTICLE_HYDRATION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_AUTHORITATIVE_ARTICLE_HYDRATION_HPP_INCLUDED

/// \file MdbxAuthoritativeArticleHydration.hpp
/// \brief Authoritative Raw Markdown article hydration over one MDBX context.

#include "MdbxStorageContext.hpp"

#include <agent_memory/domain/Document.hpp>
#include <agent_memory/domain/KnowledgeUnit.hpp>
#include <agent_memory/domain/Resource.hpp>
#include <agent_memory/domain/Artifact.hpp>
#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>
#include <agent_memory/storage/FileRawBlobStore.hpp>

#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <string>

#if AGENT_MEMORY_HAS_MDBX

namespace agent_memory {

/// Result state for the limited Raw Markdown article profile.
enum class AuthoritativeArticleHydrationStatus : std::uint8_t {
    Hydrated,
    SummaryOnly,
    Stale,
    Missing,
    Corrupt,
    Unavailable,
    Unsupported,
    InvalidArgument
};

/// Typed diagnostic retained alongside the coarse result state.
enum class AuthoritativeArticleHydrationDiagnostic : std::uint8_t {
    None,
    InvalidRequest,
    ScopeMismatch,
    UnitMissing,
    UnitInactive,
    UnitRevisionMismatch,
    ProjectionMissing,
    ManifestMissing,
    ManifestStale,
    BindingMissing,
    BindingMismatch,
    CatalogMissing,
    CatalogCorrupt,
    CasMissing,
    CasCorrupt,
    LocatorUnsupported,
    LocatorMismatch,
    RangeOutOfBounds,
    BudgetExceeded,
    MetadataChanged,
    IoFailure
};

struct MdbxAuthoritativeArticleHydrationOptions final {
    std::shared_ptr<MdbxStorageContext> context;
    std::string table_prefix = "agent_memory";
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::filesystem::path raw_cas_root;
    FileRawBlobStoreOptions raw_cas_options{};
    /// Manifest schema admitted by this bounded article profile.
    std::string expected_manifest_schema_id =
        "agent_memory.m0_resource_importer.raw_chunks_original@agent_memory";
    std::uint32_t expected_manifest_schema_version = 1;
    std::uint64_t max_range_bytes = 64U * 1024U;
};

/// Candidate/request values are claims. Authority is re-read from MDBX.
struct MdbxAuthoritativeArticleHydrationRequest final {
    ProjectionAwareLexicalCandidate candidate;
    ScopeId scope_id;
    TextRange source_range;
    std::optional<ResourceRevisionRef> claimed_resource_revision;
    std::optional<BlobDigest> claimed_artifact_digest;
    std::uint64_t max_bytes = 0;
};

struct MdbxAuthoritativeArticleHydrationResult final {
    AuthoritativeArticleHydrationStatus status =
        AuthoritativeArticleHydrationStatus::InvalidArgument;
    AuthoritativeArticleHydrationDiagnostic diagnostic_code =
        AuthoritativeArticleHydrationDiagnostic::InvalidRequest;
    std::string diagnostic;
    KnowledgeUnitId knowledge_unit_id;
    ScopeId scope_id;
    std::uint64_t unit_revision = 0;
    std::optional<ResourceRevisionRef> resource_revision;
    std::optional<SourceRevisionId> source_revision_id;
    std::optional<ArtifactBindingId> artifact_binding_id;
    std::optional<ArtifactId> artifact_id;
    std::optional<BlobDigest> artifact_digest;
    TextRange source_range;
    /// Owned bytes are non-empty only for Hydrated.
    std::string bytes;
};

/// \brief Hydrates one exact Raw Markdown source range after two authoritative
/// metadata observations and a complete CAS digest verification.
///
/// The final MDBX observation is the operation's linearization point. A
/// returned Hydrated value does not promise that the source remains current
/// after that point. This class is a bounded product slice, not a coordinator,
/// multimodal API, or production retrieval activation.
class MdbxAuthoritativeArticleHydrator final {
public:
    explicit MdbxAuthoritativeArticleHydrator(
        MdbxAuthoritativeArticleHydrationOptions options
    );
    ~MdbxAuthoritativeArticleHydrator();

    MdbxAuthoritativeArticleHydrator(
        const MdbxAuthoritativeArticleHydrator&) = delete;
    MdbxAuthoritativeArticleHydrator& operator=(
        const MdbxAuthoritativeArticleHydrator&) = delete;

    [[nodiscard]] MdbxAuthoritativeArticleHydrationResult hydrate(
        const MdbxAuthoritativeArticleHydrationRequest& request
    ) const;

    /// Test-only hook immediately before the final metadata snapshot.
    void set_before_final_revalidation_hook_for_testing(
        std::function<void()> hook
    );

    /// Test-only hook immediately after the final snapshot commits. A mutation
    /// here must not retroactively invalidate the completed proof.
    void set_after_final_revalidation_hook_for_testing(
        std::function<void()> hook
    );

private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
