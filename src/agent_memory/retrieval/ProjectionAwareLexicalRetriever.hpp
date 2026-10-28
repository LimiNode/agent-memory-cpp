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
#include <cstdint>
#include <stdexcept>
#include <string>

namespace agent_memory {

    struct ProjectionAwareLexicalRetrieverOptions final {
        ProjectionKind projection_kind = ProjectionKind::Original;
        std::size_t max_candidates = 256;
        /// Maximum number of units admitted into one in-memory query index.
        std::size_t max_scanned_units = 4096;
        /// Maximum UTF-8 bytes admitted into one in-memory query index.
        std::size_t max_indexed_projection_bytes = 8U * 1024U * 1024U;
        /// Exact M0 importer schema expected for active Original publication.
        std::string expected_manifest_schema_id =
            "agent_memory.m0_resource_importer.raw_chunks_original@agent_memory";
        std::uint32_t expected_manifest_schema_version = 1;
    };

    /// Thrown when a bounded retrieval request exceeds its configured work
    /// budget. A caller must retry with a larger budget or a persistent index;
    /// a partial result is never returned as a complete top-K answer.
    class ProjectionAwareLexicalRetrievalBudgetExceeded final
        : public std::runtime_error {
    public:
        explicit ProjectionAwareLexicalRetrievalBudgetExceeded(
            const std::string& message
        ) : std::runtime_error(message) {}
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
