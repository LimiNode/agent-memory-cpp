#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PERSISTENT_LEXICAL_CANDIDATE_ADAPTER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PERSISTENT_LEXICAL_CANDIDATE_ADAPTER_HPP_INCLUDED

/// \file MdbxPersistentLexicalCandidateAdapter.hpp
/// \brief Bounded candidate generation over the persistent lexical snapshot.

#include "MdbxPersistentLexicalIndex.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <agent_memory/lexical/ITokenizer.hpp>
#include <agent_memory/retrieval/RevisionBoundRetrieval.hpp>

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace agent_memory {

enum class PersistentLexicalCandidateCompleteness : std::uint8_t {
    Complete,
    Truncated,
    UnsupportedQuery
};

/// \brief Candidate identity emitted by the persistent index only.
///
/// The resource and projection fields are observations from the same lexical
/// snapshot. They are not authoritative freshness evidence; callers must use
/// the revision-bound admission seam before returning a product result.
struct PersistentLexicalCandidate final {
    ProjectionAwareLexicalCandidate identity;
    ScopeId scope_id;
    ResourceId resource_id;
    std::uint64_t resource_generation = 0;
    ProjectionVersionRef projection_version;
};

struct PersistentLexicalCandidateOptions final {
    std::size_t max_candidates = 256;
    std::size_t max_postings_per_term = 4096;
    std::size_t max_query_terms = 32;
};

struct PersistentLexicalCandidateResponse final {
    std::vector<PersistentLexicalCandidate> candidates;
    PersistentLexicalCandidateCompleteness completeness =
        PersistentLexicalCandidateCompleteness::Complete;
    std::size_t examined_postings = 0;
    LexicalReadFrontier frontier;
    std::string diagnostic;
};

/// \brief Generates bounded, non-authoritative candidates from one pinned view.
class MdbxPersistentLexicalCandidateAdapter final {
public:
    MdbxPersistentLexicalCandidateAdapter(
        MdbxPersistentLexicalIndex& index,
        ITokenizer& tokenizer,
        PersistentLexicalCandidateOptions options = {}
    );

    [[nodiscard]] PersistentLexicalCandidateResponse generate(
        const RetrievalQuery& query
    ) const;

private:
    MdbxPersistentLexicalIndex* m_index;
    ITokenizer* m_tokenizer;
    PersistentLexicalCandidateOptions m_options;
};

/// \brief Reuses the canonical revision-bound admission for persistent hits.
///
/// This function deliberately discards lexical candidates that are stale,
/// erased, out of scope, or missing an active projection/manifest. The
/// persistent index never supplies a second authority or a second coordinator.
[[nodiscard]] RevisionBoundRetrievalResponse admit_persistent_lexical_candidates(
    const ScopeId& scope_id,
    const PersistentLexicalCandidateResponse& candidates,
    IKnowledgeUnitStore& unit_store,
    IProjectionStore& projection_store,
    IResourceManifestStorage& manifest_store,
    ITokenizer& tokenizer,
    ProjectionAwareLexicalRetrieverOptions options = {}
);

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif // AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PERSISTENT_LEXICAL_CANDIDATE_ADAPTER_HPP_INCLUDED
