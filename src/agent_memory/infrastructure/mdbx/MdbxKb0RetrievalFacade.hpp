#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_KB0_RETRIEVAL_FACADE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_KB0_RETRIEVAL_FACADE_HPP_INCLUDED

/// \file MdbxKb0RetrievalFacade.hpp
/// \brief Small host-facing composition of KB-0 retrieval and trace.

#include "MdbxSnapshotRevisionBoundRetriever.hpp"

#include <agent_memory/retrieval/RevisionBoundRetrievalTrace.hpp>

#include <functional>
#include <memory>
#include <string>

#if AGENT_MEMORY_HAS_MDBX

namespace agent_memory {

/// \brief Composes snapshot admission with a deterministic observational trace.
///
/// Candidate production remains the existing bounded lexical path. This class
/// adds no DBI, graph capability, runtime orchestration, hydration fallback or
/// production activation. The current M0 result is `SummaryOnly`; callers must
/// use a source-backed reader before advertising `Hydrated`.
class MdbxKb0RetrievalFacade final {
public:
    MdbxKb0RetrievalFacade(
        std::shared_ptr<MdbxStorageContext> context,
        ScopeId scope_id,
        ITokenizer& tokenizer,
        ProjectionAwareLexicalRetrieverOptions options = {},
        std::string table_prefix = "agent_memory");

    [[nodiscard]] RevisionBoundRetrievalTraceResponse retrieve_with_trace(
        const RetrievalQuery& query) const;

    [[nodiscard]] RevisionBoundRetrievalResponse retrieve_detailed(
        const RetrievalQuery& query) const;

    void set_read_hook_for_testing(std::function<void()> hook);
    void set_candidate_hook_for_testing(std::function<void()> hook);

private:
    MdbxSnapshotRevisionBoundRetriever m_retriever;
    ProjectionAwareLexicalRetrieverOptions m_options;
    ScopeId m_scope_id;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
