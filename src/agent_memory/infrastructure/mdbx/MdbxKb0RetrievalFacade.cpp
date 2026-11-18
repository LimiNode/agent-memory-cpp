#include "MdbxKb0RetrievalFacade.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <utility>

namespace agent_memory {

MdbxKb0RetrievalFacade::MdbxKb0RetrievalFacade(
    std::shared_ptr<MdbxStorageContext> context,
    ScopeId scope_id,
    ITokenizer& tokenizer,
    ProjectionAwareLexicalRetrieverOptions options,
    std::string table_prefix)
    : m_retriever(
        std::move(context), scope_id, tokenizer, options, std::move(table_prefix)),
      m_options(std::move(options)),
      m_scope_id(std::move(scope_id)) {}

RevisionBoundRetrievalTraceResponse MdbxKb0RetrievalFacade::retrieve_with_trace(
    const RetrievalQuery& query) const {
    auto response = m_retriever.retrieve_detailed(query);
    RevisionBoundRetrievalTraceResponse result;
    result.trace = make_revision_bound_retrieval_trace(
        query, m_scope_id, m_options, response,
        response.completeness == RevisionBoundCompleteness::Complete);
    result.response = std::move(response);
    return result;
}

RevisionBoundRetrievalResponse MdbxKb0RetrievalFacade::retrieve_detailed(
    const RetrievalQuery& query) const {
    return m_retriever.retrieve_detailed(query);
}

void MdbxKb0RetrievalFacade::set_read_hook_for_testing(std::function<void()> hook) {
    m_retriever.set_read_hook_for_testing(std::move(hook));
}

void MdbxKb0RetrievalFacade::set_candidate_hook_for_testing(
    std::function<void()> hook) {
    m_retriever.set_candidate_hook_for_testing(std::move(hook));
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
