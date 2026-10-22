#pragma once
#ifndef AGENT_MEMORY_HEADER_RETRIEVAL_PROJECTION_AWARE_LEXICAL_RETRIEVER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_RETRIEVAL_PROJECTION_AWARE_LEXICAL_RETRIEVER_HPP_INCLUDED

/// \file ProjectionAwareLexicalRetriever.hpp
/// \brief Bounded lexical retrieval over manifest-published projections.

#include <agent_memory/domain/SearchProjection.hpp>
#include <agent_memory/lexical/ITokenizer.hpp>
#include <agent_memory/retrieval/IRetriever.hpp>
#include <agent_memory/storage/IKnowledgeUnitStore.hpp>
#include <agent_memory/storage/IProjectionStore.hpp>
#include <agent_memory/storage/IResourceManifestStorage.hpp>

#include <cstddef>

namespace agent_memory {

    struct ProjectionAwareLexicalRetrieverOptions final {
        ProjectionKind projection_kind = ProjectionKind::Original;
        std::size_t max_candidates = 256;
    };

    /// \brief Retrieves only units whose projection is published and fresh.
    ///
    /// The projection store is a history view, not an active view. A candidate
    /// is admitted only when its projection revision matches the current unit
    /// revision and an active resource manifest contains the exact
    /// `original_projection:<unit_id>` binding. Canonical envelope data is
    /// re-read before a result is returned, so stale or erased units cannot
    /// escape through a derived projection.
    class ProjectionAwareLexicalRetriever final : public IRetriever {
    public:
        ProjectionAwareLexicalRetriever(
            IKnowledgeUnitStore& unit_store,
            IProjectionStore& projection_store,
            IResourceManifestStorage& manifest_store,
            ScopeId scope_id,
            ITokenizer& tokenizer,
            ProjectionAwareLexicalRetrieverOptions options = {}
        );

        [[nodiscard]] RetrievalResult retrieve(
            const RetrievalQuery& query
        ) const override;

    private:
        IKnowledgeUnitStore* m_unit_store;
        IProjectionStore* m_projection_store;
        IResourceManifestStorage* m_manifest_store;
        ScopeId m_scope_id;
        ITokenizer* m_tokenizer;
        ProjectionAwareLexicalRetrieverOptions m_options;
    };

} // namespace agent_memory

#endif
