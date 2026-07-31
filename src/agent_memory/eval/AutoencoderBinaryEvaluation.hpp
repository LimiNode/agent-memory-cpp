#pragma once
#ifndef AGENT_MEMORY_HEADER_EVAL_AUTOENCODER_BINARY_EVALUATION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_EVAL_AUTOENCODER_BINARY_EVALUATION_HPP_INCLUDED

/// \file AutoencoderBinaryEvaluation.hpp
/// \brief Comparable float, binary-rerank, and decoder retrieval evaluation.

#include <agent_memory/index/AutoencoderBinaryEncoder.hpp>
#include <agent_memory/index/BinarySignature.hpp>
#include <agent_memory/eval/Evaluation.hpp>

#include <cstddef>
#include <string>
#include <vector>

namespace agent_memory {

    /// \brief Candidate and oracle limits for autoencoder retrieval comparison.
    struct AutoencoderBinaryEvaluationOptions final {
        /// \brief Number of original-float neighbours treated as the oracle top-K.
        std::size_t oracle_k = 10;
        /// \brief Number of Hamming-ranked documents passed to exact reranking.
        std::size_t returned_candidate_limit = 100;
    };

    /// \brief Descriptive statistics emitted by a binary-code diagnostic pass.
    struct AutoencoderBinaryDescriptiveStatistics final {
        std::size_t sample_count = 0;
        double mean = 0.0;
        double population_stddev = 0.0;
        double minimum = 0.0;
        double maximum = 0.0;
    };

    /// \brief Evidence that an autoencoder code retains useful dense-space structure.
    ///
    /// The document and query code-health fields reveal collapsed bits and exact
    /// collisions. Pair and reconstruction fields are diagnostic only; they do
    /// not use qrels and must not be used as a substitute for held-out retrieval
    /// metrics.
    struct AutoencoderBinaryCodeDiagnostics final {
        BinaryCodeHealthMetrics document_code_health;
        BinaryCodeHealthMetrics query_code_health;
        std::size_t unique_document_code_count = 0;
        double unique_document_code_fraction = 0.0;
        std::size_t unique_query_code_count = 0;
        double unique_query_code_fraction = 0.0;
        AutoencoderBinaryDescriptiveStatistics query_document_hamming_distance;
        /// \brief Pearson correlation of float cosine with negative Hamming distance.
        double cosine_negative_hamming_pearson_correlation = 0.0;
        /// \brief Whether the Pearson correlation has nonzero variance in both variables.
        bool cosine_negative_hamming_correlation_defined = false;
        AutoencoderBinaryDescriptiveStatistics decoder_reconstruction_cosine;
        AutoencoderBinaryDescriptiveStatistics decoded_document_norm;
        /// \brief Deterministic cyclic mismatch control for decoder reconstruction cosine.
        AutoencoderBinaryDescriptiveStatistics shuffled_decoder_cosine;
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
        /// \brief Expected oracle coverage for uniformly random candidates of this size.
        double random_candidate_coverage_expectation = 0.0;
        /// \brief Observed candidate coverage divided by the random expectation.
        double candidate_coverage_lift_vs_random = 0.0;
        /// \brief Code and reconstruction diagnostics collected from the same inputs.
        AutoencoderBinaryCodeDiagnostics code_diagnostics;
    };

    /// \brief Qrels-based quality of all three autoencoder retrieval modes.
    struct AutoencoderBinaryRetrievalEvaluation final {
        AutoencoderBinaryEvaluationMetrics exact_agreement;
        RetrievalMetrics original_float_metrics;
        RetrievalMetrics binary_rerank_metrics;
        RetrievalMetrics decoder_approximation_metrics;
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

    /// \brief Evaluates original float, binary-reranked, and decoder runs against qrels.
    ///
    /// `document_ids` and `query_ids` must have the same order as their vector
    /// arrays. The binary candidate stage itself is also reported through
    /// `exact_agreement`, while the three RetrievalMetrics values measure real
    /// graded relevance against the supplied held-out judgments.
    [[nodiscard]] AutoencoderBinaryRetrievalEvaluation
    evaluate_autoencoder_binary_retrieval_with_qrels(
        const std::vector<std::string>& document_ids,
        const std::vector<Embedding>& document_vectors,
        const std::vector<std::string>& query_ids,
        const std::vector<Embedding>& query_vectors,
        const std::vector<RelevanceJudgment>& judgments,
        const AutoencoderBinaryEncoder& encoder,
        const AutoencoderBinaryDecoder& decoder,
        AutoencoderBinaryEvaluationOptions binary_options = {},
        RetrievalEvaluationOptions retrieval_options = {}
    );

} // namespace agent_memory

#endif
