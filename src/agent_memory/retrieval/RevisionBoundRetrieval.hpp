#pragma once
#ifndef AGENT_MEMORY_HEADER_RETRIEVAL_REVISION_BOUND_RETRIEVAL_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_RETRIEVAL_REVISION_BOUND_RETRIEVAL_HPP_INCLUDED

/// \file RevisionBoundRetrieval.hpp
/// \brief Product-facing lexical results with explicit revision provenance.

#include <agent_memory/domain/Resource.hpp>
#include <agent_memory/domain/SearchProjection.hpp>
#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>

#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace agent_memory {

/// \brief Canonical freshness of the admitted unit and its Original projection.
enum class RevisionBoundFreshness : std::uint8_t {
    Current,
    Stale,
    Unavailable
};

/// \brief Materialization level of the returned canonical/provenance data.
enum class RevisionBoundMaterialization : std::uint8_t {
    Hydrated,
    SummaryOnly,
    Unavailable
};

/// \brief Completeness of the retrieval operation, independent of one hit.
enum class RevisionBoundCompleteness : std::uint8_t {
    Complete,
    BudgetExceeded,
    UnsupportedQuery
};

/// \brief Explicit diagnostic for a query feature outside the M0 profile.
class RevisionBoundUnsupportedQuery final : public std::runtime_error {
public:
    explicit RevisionBoundUnsupportedQuery(const std::string& message)
        : std::runtime_error(message) {}
};

/// \brief Validates the producer-owned typed identity carried by a candidate.
[[nodiscard]] bool is_valid_revision_bound_candidate_identity(
    const ProjectionAwareLexicalCandidate& identity,
    ProjectionKind expected_kind = ProjectionKind::Original
) noexcept;

/// \brief Returns whether an optional typed candidate identity is usable.
[[nodiscard]] bool has_valid_revision_bound_candidate_identity(
    const std::optional<ProjectionAwareLexicalCandidate>& candidate,
    ProjectionKind expected_kind = ProjectionKind::Original
) noexcept;

/// \brief Checks the identity after canonical unit re-read.
[[nodiscard]] bool matches_revision_bound_candidate_identity(
    const ProjectionAwareLexicalCandidate& identity,
    const KnowledgeUnitEnvelope& envelope,
    ProjectionKind expected_kind = ProjectionKind::Original
) noexcept;

/// \brief Retrieval result that cannot silently lose revision identity.
///
/// The inline source summary is a typed, revision-bound locator.  M0 does not
/// yet own a byte-addressable source catalog, so a valid summary may be
/// returned as SummaryOnly: callers must not treat it as a fully hydrated quote.
/// Returned values obey the state table in guides/kb0-revision-bound-retrieval.md:
/// non-Current results have Unavailable materialization and empty text;
/// source/root_digest are either both absent or a validated exact revision pair.
/// A Stale pair is diagnostic provenance, never a current citation.
struct RevisionBoundRetrievalResult final {
    KnowledgeUnitId knowledge_unit_id;
    ScopeId scope_id;
    ProjectionKind projection_kind = ProjectionKind::Original;
    std::uint64_t unit_revision = 0;
    ResourceId root_resource_id;
    std::uint64_t root_generation = 0;
    std::optional<ResourceBodyDigest> root_digest;
    std::optional<SourceRefSummary> source;
    float score = 0.0F;
    RevisionBoundFreshness freshness = RevisionBoundFreshness::Unavailable;
    RevisionBoundMaterialization materialization =
        RevisionBoundMaterialization::Unavailable;
    /// Inline canonical text is present only for Current/SummaryOnly results.
    /// It is a bounded summary, not proof of byte-addressable source hydration.
    std::string text;
};

struct RevisionBoundRetrievalResponse final {
    std::vector<RevisionBoundRetrievalResult> results;
    RevisionBoundCompleteness completeness = RevisionBoundCompleteness::Complete;
    std::string diagnostic;
    /// Number of producer-owned candidates that were examined.
    std::size_t candidate_count = 0;
    /// Number of candidates admitted as Current from the final read view.
    std::size_t admitted_count = 0;
    /// Number of candidates rejected because their canonical state was stale.
    std::size_t stale_count = 0;
    /// Number of candidates rejected as unavailable or malformed.
    std::size_t unavailable_count = 0;
};

/// \brief Adapts the bounded legacy lexical path to revision-bound results.
///
/// This is intentionally a small composition adapter. It does not replace
/// IRetriever or change production retrieval defaults. Backend-specific
/// implementations may provide a shared read snapshot; this generic adapter
/// uses the existing store contracts and therefore reports SummaryOnly when
/// only the M0 inline source summary is available.
class RevisionBoundLexicalRetriever final {
public:
    RevisionBoundLexicalRetriever(
        IKnowledgeUnitStore& unit_store,
        IProjectionStore& projection_store,
        IResourceManifestStorage& manifest_store,
        ScopeId scope_id,
        ITokenizer& tokenizer,
        ProjectionAwareLexicalRetrieverOptions options = {}
    );

    [[nodiscard]] std::vector<RevisionBoundRetrievalResult> retrieve(
        const RetrievalQuery& query
    ) const;

    /// \brief Returns hit statuses and query completeness without conflating
    /// unsupported features or work-budget failures with zero matches.
    /// A limit above max_candidates is rejected before reading any stores.
    /// Both entry points throw std::logic_error for an unpaired producer result.
    [[nodiscard]] RevisionBoundRetrievalResponse retrieve_detailed(
        const RetrievalQuery& query
    ) const;

    /// \brief Admits candidates produced by another bounded lexical backend.
    ///
    /// The backend owns ranking and candidate completeness; this method owns
    /// only canonical scope, lifecycle, revision, projection, and manifest
    /// admission. The two vectors must be parallel and equally sized.
    [[nodiscard]] RevisionBoundRetrievalResponse admit_candidates(
        const std::vector<ProjectionAwareLexicalCandidate>& candidates,
        const std::vector<float>& scores
    ) const;

private:
    ProjectionAwareLexicalRetriever m_legacy;
    IKnowledgeUnitStore* m_unit_store;
    IProjectionStore* m_projection_store;
    IResourceManifestStorage* m_manifest_store;
    ScopeId m_scope_id;
    ProjectionAwareLexicalRetrieverOptions m_options;
};

} // namespace agent_memory

#endif
