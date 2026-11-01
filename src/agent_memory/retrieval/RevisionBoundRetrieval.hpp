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
#include <string>
#include <vector>

namespace agent_memory {

/// \brief Outcome of canonical source validation for one lexical candidate.
enum class RevisionBoundHydrationOutcome : std::uint8_t {
    Current,
    Partial,
    Stale,
    Unavailable
};

/// \brief Retrieval result that cannot silently lose revision identity.
///
/// The inline source summary is a typed, revision-bound locator.  M0 does not
/// yet own a byte-addressable source catalog, so a valid summary may be
/// returned as Partial: callers must not treat it as a fully hydrated quote.
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
    RevisionBoundHydrationOutcome hydration =
        RevisionBoundHydrationOutcome::Unavailable;
    /// Empty for Stale/Unavailable outcomes; otherwise the canonical unit text.
    std::string text;
};

/// \brief Adapts the bounded legacy lexical path to revision-bound results.
///
/// This is intentionally a small composition adapter. It does not replace
/// IRetriever or change production retrieval defaults. Backend-specific
/// implementations may provide a shared read snapshot; this generic adapter
/// uses the existing store contracts and therefore documents Partial when
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

private:
    ProjectionAwareLexicalRetriever m_legacy;
    IKnowledgeUnitStore* m_unit_store;
    IProjectionStore* m_projection_store;
    IResourceManifestStorage* m_manifest_store;
    ScopeId m_scope_id;
};

} // namespace agent_memory

#endif
