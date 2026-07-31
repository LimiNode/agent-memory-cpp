#pragma once
#ifndef AGENT_MEMORY_HEADER_EVAL_AUTOENCODER_BINARY_EVALUATION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_EVAL_AUTOENCODER_BINARY_EVALUATION_HPP_INCLUDED

/// \file AutoencoderBinaryEvaluation.hpp
/// \brief Comparable float, binary-rerank, and decoder retrieval evaluation.

#include <agent_memory/index/AutoencoderBinaryEncoder.hpp>

#include <cstddef>
#include <vector>

namespace agent_memory {

    /// \brief Candidate and oracle limits for autoencoder retrieval comparison.
    struct AutoencoderBinaryEvaluationOptions final {
        /// \brief Number of original-float neighbours treated as the oracle top-K.
        std::size_t oracle_k = 10;
        /// \brief Number of Hamming-ranked documents passed to exact reranking.
        std::size_t returned_candidate_limit = 100;
    };

    /// \brief Mean agreement against original-float cosine rankings.
    struct AutoencoderBinaryEvaluationMetrics final {
        std::size_t document_count = 0;
        std::size_t query_count = 0;
        std::size_t oracle_k = 0;
        std::size_t returned_candidate_limit = 0;
        /// \brief Mean fraction of original top-K documents present in binary candidates.
        double exact_top_k_candidate_coverage = 0.0;
        /// \brief Mean exact-top-K recall after reranking only binary candidates.
        double reranked_recall_at_k_vs_exact = 0.0;
        /// \brief Mean exact-top-K recall from decoder-reconstructed document vectors.
        double decoder_recall_at_k_vs_exact = 0.0;
    };

    /// \brief Evaluates the three retrieval modes over common dense inputs.
    ///
    /// The original-float cosine ranking is the oracle. Binary candidates are
    /// reranked with that exact same cosine function. Decoder approximation keeps
    /// the query float but ranks decoder-reconstructed document vectors; it is an
    /// explicitly separate compact-storage experiment, not a safe reranker.
    [[nodiscard]] AutoencoderBinaryEvaluationMetrics evaluate_autoencoder_binary_retrieval(
        const std::vector<Embedding>& document_vectors,
        const std::vector<Embedding>& query_vectors,
        const AutoencoderBinaryEncoder& encoder,
        const AutoencoderBinaryDecoder& decoder,
        AutoencoderBinaryEvaluationOptions options = {}
    );

} // namespace agent_memory

#endif
