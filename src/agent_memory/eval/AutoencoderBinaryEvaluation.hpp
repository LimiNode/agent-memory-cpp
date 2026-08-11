#pragma once
#ifndef AGENT_MEMORY_HEADER_EVAL_AUTOENCODER_BINARY_EVALUATION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_EVAL_AUTOENCODER_BINARY_EVALUATION_HPP_INCLUDED

/// \file AutoencoderBinaryEvaluation.hpp
/// \brief Comparable float, binary-rerank, and decoder retrieval evaluation.

#include <agent_memory/index/AutoencoderBinaryEncoder.hpp>
#include <agent_memory/index/AsymmetricBinarySignatureScorer.hpp>
#include <agent_memory/index/BinarySignature.hpp>
#include <agent_memory/eval/Evaluation.hpp>

#include <cstddef>
#include <string>
#include <vector>

namespace agent_memory {

    /// \brief Candidate ordering used before exact float reranking.
    enum class AutoencoderBinaryCandidateScoring {
        /// \brief Symmetric Hamming distance between packed query and document codes.
        HammingDistance,
        /// \brief Continuous query affine projections scored against packed document bits.
        AsymmetricAffineDot,
    };

    /// \brief Backend used when candidate_scoring is AsymmetricAffineDot.
    ///
    /// The scalar backend is retained as a numerical reference. The byte lookup
    /// table is the default candidate-scoring implementation.
    using AutoencoderBinaryAsymmetricScoringBackend =
        AsymmetricBinarySignatureScoringBackend;

    /// \brief Candidate and oracle limits for autoencoder retrieval comparison.
    struct AutoencoderBinaryEvaluationOptions final {
        /// \brief Number of original-float neighbours treated as the oracle top-K.
        std::size_t oracle_k = 10;
        /// \brief Number of Hamming-ranked documents passed to exact reranking.
        std::size_t returned_candidate_limit = 100;
        /// \brief Candidate ordering evaluated before the exact float reranker.
        AutoencoderBinaryCandidateScoring candidate_scoring =
            AutoencoderBinaryCandidateScoring::HammingDistance;
        /// \brief Asymmetric implementation selected when candidate_scoring uses logits.
        AutoencoderBinaryAsymmetricScoringBackend asymmetric_scoring_backend =
            AutoencoderBinaryAsymmetricScoringBackend::ByteLookupTable;
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
        /// \brief Hamming distance from each query to its closest dense-space document.
        AutoencoderBinaryDescriptiveStatistics dense_nearest_hamming_distance;
        /// \brief Hamming distance from each query to its dense rank-100 document.
        ///
        /// This is a teacher-geometry control, not a relevance-negative label.
        AutoencoderBinaryDescriptiveStatistics dense_rank_100_hamming_distance;
        /// \brief Dense rank-100 Hamming distance minus the dense-nearest distance.
        AutoencoderBinaryDescriptiveStatistics dense_neighbour_hamming_margin;
        /// \brief Fraction of queries whose dense-neighbour Hamming margin is nonpositive.
        double nonpositive_dense_neighbour_hamming_margin_fraction = 0.0;
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

    /// \brief Candidate limits and timing repetitions for a three-stage cascade.
    struct AutoencoderBinaryCascadeOptions final {
        std::size_t oracle_k = 10;
        /// \brief Full-corpus Hamming candidates passed to asymmetric scoring.
        std::size_t hamming_candidate_limit = 512;
        /// \brief Asymmetric candidates passed to exact float reranking.
        std::size_t asymmetric_candidate_limit = 128;
        /// \brief Untimed warm-up executions of the complete candidate pipeline.
        std::size_t warmup_repeat_count = 1;
        /// \brief Timed executions per query; quality is computed from the final run.
        std::size_t timing_repeat_count = 3;
    };

    /// \brief Per-query stage timing summaries for the binary cascade.
    struct AutoencoderBinaryCascadeTiming final {
        double document_signature_build_ms = 0.0;
        AutoencoderBinaryDescriptiveStatistics query_projection_ms;
        AutoencoderBinaryDescriptiveStatistics hamming_candidate_search_ms;
        AutoencoderBinaryDescriptiveStatistics asymmetric_lut_build_ms;
        AutoencoderBinaryDescriptiveStatistics asymmetric_candidate_search_ms;
        AutoencoderBinaryDescriptiveStatistics exact_rerank_ms;
        AutoencoderBinaryDescriptiveStatistics total_query_pipeline_ms;
    };

    /// \brief Qrels and candidate-stage evidence for Hamming -> asymmetric -> float rerank.
    struct AutoencoderBinaryCascadeEvaluation final {
        std::size_t document_count = 0;
        std::size_t query_count = 0;
        std::size_t oracle_k = 0;
        std::size_t hamming_candidate_limit = 0;
        std::size_t asymmetric_candidate_limit = 0;
        std::size_t binary_payload_bytes = 0;
        std::size_t float_payload_bytes = 0;
        /// \brief Queries that have at least one positive qrels judgment in the corpus.
        std::size_t qrels_positive_query_count = 0;
        HammingDistanceBackend hamming_backend = HammingDistanceBackend::LookupTable;
        double hamming_exact_top_k_candidate_coverage = 0.0;
        double asymmetric_exact_top_k_candidate_coverage = 0.0;
        /// \brief Macro mean over qrels-positive queries after Hamming.
        double hamming_qrels_positive_candidate_coverage = 0.0;
        /// \brief Macro mean over qrels-positive queries after asymmetric scoring.
        double asymmetric_qrels_positive_candidate_coverage = 0.0;
        double reranked_recall_at_k_vs_exact = 0.0;
        RetrievalMetrics original_float_metrics;
        RetrievalMetrics cascade_rerank_metrics;
        AutoencoderBinaryCascadeTiming timing;
    };

    /// \brief Evaluates the three retrieval modes over common dense inputs.
    ///
    /// The original-float cosine ranking is the oracle. Binary candidates are
    /// reranked with that exact same cosine function. Decoder approximation keeps
    /// the query float but ranks decoder-reconstructed document vectors; it is an
    /// explicitly separate compact-storage experiment, not a safe reranker.
    [[nodiscard]] AutoencoderBinaryEvaluationMetrics evaluate_autoencoder_binary_retrieval(
        const std::vector<std::string>& document_ids,
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

    /// \brief Evaluates a Hamming -> asymmetric -> exact-rerank candidate cascade.
    ///
    /// Document signatures are materialized once. Full-float oracle and qrels
    /// accounting are outside the candidate-pipeline timers; timing fields cover
    /// only query projection, candidate stages, and final float reranking.
    [[nodiscard]] AutoencoderBinaryCascadeEvaluation
    evaluate_autoencoder_binary_cascade_with_qrels(
        const std::vector<std::string>& document_ids,
        const std::vector<Embedding>& document_vectors,
        const std::vector<std::string>& query_ids,
        const std::vector<Embedding>& query_vectors,
        const std::vector<RelevanceJudgment>& judgments,
        const AutoencoderBinaryEncoder& encoder,
        AutoencoderBinaryCascadeOptions cascade_options = {},
        RetrievalEvaluationOptions retrieval_options = {}
    );

} // namespace agent_memory

#endif
