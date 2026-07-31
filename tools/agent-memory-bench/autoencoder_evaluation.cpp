#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <agent_memory/eval/AutoencoderBinaryEvaluation.hpp>

#include <nlohmann/json.hpp>

#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

    [[nodiscard]] std::size_t parse_positive_size(const char* text, const char* name) {
        try {
            std::size_t parsed = 0;
            const auto value = std::stoull(text, &parsed);
            if(parsed != std::string{text}.size() || value == 0U) {
                throw std::invalid_argument("invalid value");
            }
            return static_cast<std::size_t>(value);
        } catch(const std::exception&) {
            throw std::invalid_argument(std::string{name} + " must be a positive integer");
        }
    }

    [[nodiscard]] nlohmann::json metric_values(
        const std::vector<agent_memory::MetricAtK>& values
    ) {
        nlohmann::json output = nlohmann::json::object();
        for(const auto& value : values) {
            output[std::to_string(value.k)] = value.value;
        }
        return output;
    }

    [[nodiscard]] nlohmann::json metrics_json(
        const agent_memory::RetrievalMetrics& metrics
    ) {
        return {
            {"evaluated_query_count", metrics.evaluated_query_count},
            {"recall_at", metric_values(metrics.recall_at)},
            {"ndcg_at", metric_values(metrics.ndcg_at)},
            {"mrr", metrics.mrr},
            {"empty_result_fraction", metrics.empty_result_fraction},
        };
    }

    [[nodiscard]] nlohmann::json descriptive_statistics_json(
        const agent_memory::AutoencoderBinaryDescriptiveStatistics& statistics
    ) {
        return {
            {"sample_count", statistics.sample_count},
            {"mean", statistics.mean},
            {"population_stddev", statistics.population_stddev},
            {"minimum", statistics.minimum},
            {"maximum", statistics.maximum},
        };
    }

    [[nodiscard]] nlohmann::json code_health_json(
        const agent_memory::BinaryCodeHealthMetrics& health
    ) {
        return {
            {"signature_count", health.signature_count},
            {"bit_count", health.bit_count},
            {"fraction_ones_per_bit", health.fraction_ones_per_bit},
            {"constant_bit_fraction", health.constant_bit_fraction},
            {"mean_bit_entropy", health.mean_bit_entropy},
            {"min_bit_entropy", health.min_bit_entropy},
            {"max_bit_entropy", health.max_bit_entropy},
            {"duplicate_signature_rate", health.duplicate_signature_rate},
            {"sampled_mean_pairwise_hamming_distance",
             health.sampled_mean_pairwise_hamming_distance},
            {"sampled_pairwise_hamming_distance_stddev",
             health.sampled_pairwise_hamming_distance_stddev},
            {"sampled_min_pairwise_hamming_distance",
             health.sampled_min_pairwise_hamming_distance},
            {"sampled_max_pairwise_hamming_distance",
             health.sampled_max_pairwise_hamming_distance},
            {"sampled_pair_count", health.sampled_pair_count},
            {"exact_signature_bucket_sizes", health.exact_signature_bucket_sizes},
        };
    }

    [[nodiscard]] nlohmann::json code_diagnostics_json(
        const agent_memory::AutoencoderBinaryCodeDiagnostics& diagnostics
    ) {
        return {
            {"document_code_health", code_health_json(diagnostics.document_code_health)},
            {"query_code_health", code_health_json(diagnostics.query_code_health)},
            {"unique_document_code_count", diagnostics.unique_document_code_count},
            {"unique_document_code_fraction", diagnostics.unique_document_code_fraction},
            {"unique_query_code_count", diagnostics.unique_query_code_count},
            {"unique_query_code_fraction", diagnostics.unique_query_code_fraction},
            {"query_document_hamming_distance",
             descriptive_statistics_json(diagnostics.query_document_hamming_distance)},
            {"cosine_negative_hamming_pearson_correlation",
             diagnostics.cosine_negative_hamming_pearson_correlation},
            {"cosine_negative_hamming_correlation_defined",
             diagnostics.cosine_negative_hamming_correlation_defined},
            {"decoder_reconstruction_cosine",
             descriptive_statistics_json(diagnostics.decoder_reconstruction_cosine)},
            {"decoded_document_norm",
             descriptive_statistics_json(diagnostics.decoded_document_norm)},
            {"shuffled_decoder_cosine",
             descriptive_statistics_json(diagnostics.shuffled_decoder_cosine)},
        };
    }

} // namespace

int main(int argc, char* argv[]) {
    if(argc < 4 || argc > 6) {
        std::cerr << "usage: agent-memory-autoencoder-eval <materialization-root> <artifact.json> <report.json> [oracle-k] [candidate-limit]\n";
        return 2;
    }
    try {
        const auto materialization =
            agent_memory::load_materialized_autoencoder_evaluation_dataset(argv[1]);
        const auto artifact = agent_memory::load_autoencoder_binary_artifact(argv[2]);
        if(artifact.input_materialization_manifest_sha256 !=
           materialization.materialization_manifest_sha256) {
            throw std::runtime_error(
                "artifact input materialization manifest hash does not match evaluation root"
            );
        }
        if(artifact.prepared_study_manifest_sha256 !=
           materialization.prepared_study_manifest_sha256) {
            throw std::runtime_error(
                "artifact prepared study manifest hash does not match evaluation root"
            );
        }
        std::vector<std::string> document_ids;
        std::vector<agent_memory::Embedding> document_vectors;
        for(const auto& record : materialization.document_embeddings) {
            document_ids.push_back(record.id);
            document_vectors.push_back(record.embedding);
        }
        std::vector<std::string> query_ids;
        std::vector<agent_memory::Embedding> query_vectors;
        for(const auto& record : materialization.query_embeddings) {
            query_ids.push_back(record.id);
            query_vectors.push_back(record.embedding);
        }
        const agent_memory::AutoencoderBinaryEvaluationOptions binary_options{
            argc >= 5 ? parse_positive_size(argv[4], "oracle-k") : 10U,
            argc >= 6 ? parse_positive_size(argv[5], "candidate-limit") : 100U,
        };
        const auto evaluation = agent_memory::evaluate_autoencoder_binary_retrieval_with_qrels(
            document_ids,
            document_vectors,
            query_ids,
            query_vectors,
            materialization.judgments,
            artifact.encoder,
            artifact.decoder,
            binary_options,
            {{1U, 5U, 10U, 100U}, {10U}}
        );
        const nlohmann::json report{
            {"schema_version", 1},
            {"artifact_sha256", artifact.artifact_sha256},
            {"materialization_manifest_sha256", materialization.materialization_manifest_sha256},
            {"prepared_study_manifest_sha256", materialization.prepared_study_manifest_sha256},
            {"document_count", materialization.document_embeddings.size()},
            {"query_count", materialization.query_embeddings.size()},
            {"oracle_k", evaluation.exact_agreement.oracle_k},
            {"returned_candidate_limit", evaluation.exact_agreement.returned_candidate_limit},
            {"exact_top_k_candidate_coverage", evaluation.exact_agreement.exact_top_k_candidate_coverage},
            {"reranked_recall_at_k_vs_exact", evaluation.exact_agreement.reranked_recall_at_k_vs_exact},
            {"decoder_recall_at_k_vs_exact", evaluation.exact_agreement.decoder_recall_at_k_vs_exact},
            {"random_candidate_coverage_expectation",
             evaluation.exact_agreement.random_candidate_coverage_expectation},
            {"candidate_coverage_lift_vs_random",
             evaluation.exact_agreement.candidate_coverage_lift_vs_random},
            {"code_diagnostics", code_diagnostics_json(evaluation.exact_agreement.code_diagnostics)},
            {"original_float", metrics_json(evaluation.original_float_metrics)},
            {"binary_candidates_exact_rerank", metrics_json(evaluation.binary_rerank_metrics)},
            {"decoder_approximation", metrics_json(evaluation.decoder_approximation_metrics)},
        };
        std::ofstream output(argv[3], std::ios::binary);
        if(!output) {
            throw std::runtime_error("cannot create autoencoder evaluation report");
        }
        output << report.dump(2) << '\n';
        if(!output) {
            throw std::runtime_error("cannot write autoencoder evaluation report");
        }
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "agent-memory-autoencoder-eval: " << error.what() << '\n';
        return 1;
    }
}
