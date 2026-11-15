#pragma once
#ifndef AGENT_MEMORY_HEADER_RETRIEVAL_HYBRID_RETRIEVAL_ENGINE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_RETRIEVAL_HYBRID_RETRIEVAL_ENGINE_HPP_INCLUDED

/// \file HybridRetrievalEngine.hpp
/// \brief Default IRetrievalEngine implementation.
///
/// Composes:
///   - ILexicalIndex (mandatory)
///   - IReranker     (defaults to IdentityReranker)
///   - IQueryAnalyzer (defaults to PassthroughQueryAnalyzer)
///
/// Future retrieval improvements (semantic blending, LLM-based rerank,
/// structured-fact overlay) plug in by swapping the dependencies. The
/// initial behaviour is a pure lexical passthrough: analyse-then-rerank is
/// identity, so the engine is observably equivalent to calling the
/// underlying ILexicalIndex::search().

#include <agent_memory/lexical/ILexicalIndex.hpp>
#include <agent_memory/lexical/IQueryAnalyzer.hpp>
#include <agent_memory/lexical/IReranker.hpp>
#include <agent_memory/retrieval/IRetrievalEngine.hpp>

#include <memory>

namespace agent_memory {

    /// \brief Hybrid retrieval engine with swappable hooks.
    ///
    /// Reranker results are admitted against the upstream lexical snapshot:
    /// rerankers may reorder admitted candidates and adjust finite scores, but
    /// cannot introduce identities, replace provenance/metadata, or duplicate
    /// candidates. Invalid reranker output fails closed with an empty response.
    ///
    /// Lifetime contract:
    ///   The engine does not own the underlying index/analyzer/reranker
    ///   unless the matching `set_*` method is used. Default constructors
    ///   install identity implementations.
    class HybridRetrievalEngine final : public IRetrievalEngine {
    public:
        HybridRetrievalEngine();

        /// \brief Constructs an engine over an externally-owned lexical index.
        /// \param index Non-owning pointer to the index. Must outlive the engine.
        explicit HybridRetrievalEngine(const ILexicalIndex& index);

        /// \brief Replaces the lexical index.
        void set_lexical_index(const ILexicalIndex& index) noexcept;

        /// \brief Replaces the reranker. A null pointer resets to the
        ///        identity reranker so retrieve() never dereferences null.
        /// \note May throw `std::bad_alloc` if `nullptr` is passed and the
        ///       default implementation's `std::make_unique` allocates.
        void set_reranker(std::unique_ptr<IReranker> reranker);

        /// \brief Replaces the query analyzer. A null pointer resets to
        ///        the passthrough analyzer so retrieve() never
        ///        dereferences null.
        /// \note May throw `std::bad_alloc` if `nullptr` is passed and the
        ///       default implementation's `std::make_unique` allocates.
        void set_query_analyzer(std::unique_ptr<IQueryAnalyzer> analyzer);

        [[nodiscard]] RetrievalResponse retrieve(
            const RetrievalRequest& request
        ) const override;

    private:
        const ILexicalIndex* m_index = nullptr;
        std::unique_ptr<IReranker> m_reranker;
        std::unique_ptr<IQueryAnalyzer> m_analyzer;
    };

} // namespace agent_memory

#endif
