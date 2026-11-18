#pragma once
#ifndef AGENT_MEMORY_HEADER_RETRIEVAL_REVISION_BOUND_RETRIEVAL_TRACE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_RETRIEVAL_REVISION_BOUND_RETRIEVAL_TRACE_HPP_INCLUDED

/// \file RevisionBoundRetrievalTrace.hpp
/// \brief Minimal, deterministic trace for the experimental KB-0 retrieval path.

#include "RevisionBoundRetrieval.hpp"

#include <cstddef>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

/// \brief Per-hit provenance and admission state emitted by the KB-0 trace.
struct RevisionBoundRetrievalTraceHit final {
    KnowledgeUnitId knowledge_unit_id;
    std::uint64_t unit_revision = 0;
    ProjectionKind projection_kind = ProjectionKind::Original;
    ResourceId root_resource_id;
    std::uint64_t root_generation = 0;
    std::optional<ResourceBodyDigest> root_digest;
    RevisionBoundFreshness freshness = RevisionBoundFreshness::Unavailable;
    RevisionBoundMaterialization materialization =
        RevisionBoundMaterialization::Unavailable;
};

/// \brief Bounded evidence for one revision-bound retrieval operation.
///
/// The trace is observational. It does not authorize a hit, refill stale
/// candidates, or imply source-byte hydration. `SummaryOnly` remains the only
/// materialization emitted by the current M0 path.
struct RevisionBoundRetrievalTrace final {
    std::string query_identity;
    std::string query_digest;
    ScopeId scope_id;
    std::size_t limit = 0;
    std::size_t max_candidates = 0;
    ProjectionKind projection_kind = ProjectionKind::Original;
    RevisionBoundCompleteness completeness = RevisionBoundCompleteness::Complete;
    std::string diagnostic;
    std::size_t candidate_count = 0;
    std::size_t admitted_count = 0;
    std::size_t stale_count = 0;
    std::size_t unavailable_count = 0;
    /// Final unit/projection/manifest admission used one MDBX read view.
    bool final_admission_snapshot_consistent = false;
    /// Candidate ranking still occurs before that final admission snapshot.
    bool ranking_snapshot_consistent = false;
    std::vector<RevisionBoundRetrievalTraceHit> hits;
};

/// \brief Response carrying both product results and their bounded trace.
struct RevisionBoundRetrievalTraceResponse final {
    RevisionBoundRetrievalResponse response;
    RevisionBoundRetrievalTrace trace;
};

/// \brief Builds a trace without reading storage or changing the response.
[[nodiscard]] RevisionBoundRetrievalTrace make_revision_bound_retrieval_trace(
    const RetrievalQuery& query,
    const ScopeId& scope_id,
    const ProjectionAwareLexicalRetrieverOptions& options,
    const RevisionBoundRetrievalResponse& response,
    bool final_admission_snapshot_consistent);

} // namespace agent_memory

#endif
