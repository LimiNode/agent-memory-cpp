#pragma once
#ifndef AGENT_MEMORY_HEADER_INDEX_EXACT_VECTOR_INDEX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INDEX_EXACT_VECTOR_INDEX_HPP_INCLUDED

/// \file ExactVectorIndex.hpp
/// \brief In-memory exact vector index implementation.

#include "IVectorIndex.hpp"
#include "VectorSimilarityComputer.hpp"

#include <map>

namespace agent_memory {

    /// \brief Options used to create an exact in-memory vector index.
    struct ExactVectorIndexOptions final {
        std::size_t dimension = 0;
        SimilarityMetric similarity_metric = SimilarityMetric::Cosine;
        /// \brief Enables the best SIMD backend supported by the running CPU.
        bool enable_simd = true;
    };

    /// \brief Deterministic in-memory exact vector index.
    class ExactVectorIndex final : public IVectorIndex {
    public:
        ExactVectorIndex();
        explicit ExactVectorIndex(ExactVectorIndexOptions options);

        [[nodiscard]] SimilarityMetric similarity_metric() const noexcept override;
        [[nodiscard]] std::size_t dimension() const noexcept override;
        [[nodiscard]] std::size_t size() const noexcept override;

        /// \brief Returns the vector arithmetic backend selected by this index.
        [[nodiscard]] VectorSimilarityBackend similarity_backend() const noexcept;

        void upsert(VectorRecord record) override;

        [[nodiscard]] std::optional<VectorRecord> find(
            const ChunkId& chunk_id
        ) const override;

        /// \brief Searches all stored vectors exactly.
        /// \note Scores are ordered with larger values first. Euclidean mode uses
        ///       negative squared distance as its score.
        [[nodiscard]] std::vector<VectorSearchResult> search(
            const VectorSearchQuery& query
        ) const override;

        [[nodiscard]] bool erase(const ChunkId& chunk_id) override;

        /// \brief Removes all records without changing the configured or adopted dimension.
        void clear() override;

    private:
        struct StoredRecord final {
            VectorRecord record;
            double inverse_norm = 0.0;
        };

        void validate_record_embedding(const Embedding& embedding) const;
        void validate_query_embedding(const Embedding& embedding) const;

        ExactVectorIndexOptions m_options;
        VectorSimilarityComputer m_similarity;
        std::map<ChunkId, StoredRecord> m_records;
    };

} // namespace agent_memory

#endif
