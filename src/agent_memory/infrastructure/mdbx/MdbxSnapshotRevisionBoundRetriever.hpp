#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_SNAPSHOT_REVISION_BOUND_RETRIEVER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_SNAPSHOT_REVISION_BOUND_RETRIEVER_HPP_INCLUDED

/// \file MdbxSnapshotRevisionBoundRetriever.hpp
/// \brief MDBX snapshot-consistent final admission for revision-bound hits.

#include <agent_memory/retrieval/RevisionBoundRetrieval.hpp>

#include <functional>
#include <memory>
#include <string>

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageContext.hpp"

namespace agent_memory {

/// \brief Revalidates lexical candidates from one MDBX read snapshot.
///
/// Candidate production and ranking remain the existing bounded in-memory
/// path. Only final admission is snapshot-consistent: the KnowledgeUnit,
/// Original projection and root ResourceManifest are read through one
/// caller-owned transaction. This adapter deliberately does not claim a
/// snapshot-consistent ranking or source-byte hydration.
class MdbxSnapshotRevisionBoundRetriever final {
public:
    MdbxSnapshotRevisionBoundRetriever(
        std::shared_ptr<MdbxStorageContext> context,
        ScopeId scope_id,
        ITokenizer& tokenizer,
        ProjectionAwareLexicalRetrieverOptions options = {},
        std::string table_prefix = "agent_memory"
    );

    [[nodiscard]] std::vector<RevisionBoundRetrievalResult> retrieve(
        const RetrievalQuery& query
    ) const;

    [[nodiscard]] RevisionBoundRetrievalResponse retrieve_detailed(
        const RetrievalQuery& query
    ) const;

    /// \brief Admits one producer-owned candidate from one fresh read snapshot.
    ///
    /// This direct seam is useful for callers that already own a bounded
    /// candidate window and for conformance tests. The score and typed
    /// revision identity are taken from the candidate; no ChunkId inference is
    /// performed.
    [[nodiscard]] RevisionBoundRetrievalResult admit_candidate(
        const ProjectionAwareLexicalCandidate& candidate
    ) const;

    /// \brief Installs a deterministic test seam after the read transaction
    /// is opened and before final admission begins.
    void set_read_hook_for_testing(std::function<void()> hook);

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    ScopeId m_scope_id;
    ITokenizer* m_tokenizer;
    ProjectionAwareLexicalRetrieverOptions m_options;
    std::string m_table_prefix;
    mutable std::function<void()> m_read_hook_for_testing;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
