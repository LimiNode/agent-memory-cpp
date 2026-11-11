#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RAW_MARKDOWN_PRODUCT_INGESTION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RAW_MARKDOWN_PRODUCT_INGESTION_HPP_INCLUDED

/// \file MdbxRawMarkdownProductIngestion.hpp
/// \brief A bounded host-facing Raw Markdown import and hydration slice.

#include "MdbxAuthoritativeArticleHydration.hpp"
#include "MdbxArtifactCatalog.hpp"
#include "MdbxResourceImporter.hpp"
#include "MdbxSnapshotRevisionBoundRetriever.hpp"
#include "MdbxStorageContext.hpp"

#include <agent_memory/ingestion/ResourceImporter.hpp>
#include <agent_memory/storage/ResourceArtifactBinding.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

/// \brief Outcome of the multi-authority import publication sequence.
enum class RawMarkdownProductImportStatus : std::uint8_t {
    /// Current in the final coherent MDBX metadata snapshot, not forever.
    Published,
    Incomplete,
    Conflict,
    InvalidArgument
};

struct RawMarkdownProductIngestionOptions final {
    std::shared_ptr<MdbxStorageContext> context;
    std::string table_prefix = "agent_memory";
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::filesystem::path raw_cas_root;
    std::uint64_t max_full_body_bytes = 64U * 1024U * 1024U;
    /// Forwarded unchanged to the existing importer fencing/recovery policy.
    std::uint64_t importer_ownership_lease_ms = 30'000;
    std::function<std::uint64_t()> importer_clock_ms;
    std::uint64_t max_verified_cas_bytes = 64U * 1024U * 1024U;
    std::uint64_t max_range_bytes = 64U * 1024U;
    std::size_t max_candidates = 64;
    std::size_t max_scanned_units = 4096;
    std::size_t max_indexed_projection_bytes = 8U * 1024U * 1024U;
    std::function<void(ResourceImporterFaultPoint)> importer_fault_injector;
    ArtifactCatalogFaultInjector catalog_fault_injector;
    /// Test-only seam immediately after catalog completion and before the
    /// typed join is published.
    std::function<void()> before_join_publication_hook_for_testing;
    /// Test-only seam immediately after the typed join and before the final
    /// currentness observation.
    std::function<void()> before_final_observation_hook_for_testing;
};

struct RawMarkdownProductImportResult final {
    RawMarkdownProductImportStatus status =
        RawMarkdownProductImportStatus::InvalidArgument;
    std::string diagnostic;
    std::optional<ResourceImportResult> resource;
    std::optional<SourceRevisionId> source_revision_id;
    std::optional<ArtifactBindingId> artifact_binding_id;
    std::optional<ArtifactCatalogRecord> artifact;
    std::optional<ResourceArtifactBinding> resource_artifact_binding;
};

/// \brief One lexical candidate and its fail-closed authoritative hydration.
///
/// The candidate is retained for diagnostics. `hydration.bytes` is non-empty
/// only when all importer manifest, Original projection, typed join, catalog
/// metadata and File CAS checks passed at one bounded linearization point.
struct RawMarkdownProductHit final {
    RevisionBoundRetrievalResult candidate;
    MdbxAuthoritativeArticleHydrationResult hydration;
};

/// \brief Composes the existing Raw Markdown authorities without adding a
/// coordinator or claiming physical atomicity across MDBX and File CAS.
///
/// The sequence is importer -> CAS/catalog -> typed join. Intermediate rows
/// are permitted in the two durability domains, but this adapter reports
/// `Incomplete` on interruption or `Conflict` if superseded at the final
/// coherent metadata snapshot. `Published` establishes currentness only in
/// that snapshot, not future freshness, query-budget sufficiency or hydration.
/// Hydration remains fail-closed until every authority agrees.
/// Only `.md` and `.markdown` source URIs and original UTF-8 text are
/// admitted. Production retrieval activation, multimodal codecs and graph or
/// coordinator integrations are intentionally outside this slice.
class MdbxRawMarkdownProductIngestion final {
public:
    explicit MdbxRawMarkdownProductIngestion(
        RawMarkdownProductIngestionOptions options
    );
    ~MdbxRawMarkdownProductIngestion();

    MdbxRawMarkdownProductIngestion(
        const MdbxRawMarkdownProductIngestion&) = delete;
    MdbxRawMarkdownProductIngestion& operator=(
        const MdbxRawMarkdownProductIngestion&) = delete;

    /// Identical replay requires the same bound_at_ms, including pending
    /// catalog intents. Changing it conflicts with the original binding.
    [[nodiscard]] RawMarkdownProductImportResult import(
        ObservedResource resource,
        std::uint64_t bound_at_ms = 0
    );

    /// Throws ProjectionAwareLexicalRetrievalBudgetExceeded rather than
    /// returning a partial ranking when the configured index budget is exceeded.
    [[nodiscard]] std::vector<RawMarkdownProductHit> retrieve_and_hydrate(
        const ScopeId& scope_id,
        const RetrievalQuery& query,
        std::uint64_t max_bytes,
        std::size_t limit = 0
    ) const;

private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif // AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RAW_MARKDOWN_PRODUCT_INGESTION_HPP_INCLUDED
