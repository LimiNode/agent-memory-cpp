#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <agent_memory/eval/AutoencoderBinaryEvaluation.hpp>

#include <nlohmann/json.hpp>

#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

    [[nodiscard]] std::size_t positive_size(const char* text, const char* name) {
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

    [[nodiscard]] nlohmann::json statistics_json(
        const agent_memory::AutoencoderBinaryDescriptiveStatistics& statistics
    ) {
        return {{"sample_count", statistics.sample_count}, {"mean", statistics.mean},
                {"population_stddev", statistics.population_stddev},
                {"minimum", statistics.minimum}, {"maximum", statistics.maximum}};
    }

    [[nodiscard]] nlohmann::json metrics_json(const agent_memory::RetrievalMetrics& metrics) {
        nlohmann::json ndcg = nlohmann::json::object();
        for(const auto& value : metrics.ndcg_at) {
            ndcg[std::to_string(value.k)] = value.value;
        }
        return {{"ndcg_at", ndcg}, {"mrr", metrics.mrr},
                {"evaluated_query_count", metrics.evaluated_query_count},
                {"empty_result_fraction", metrics.empty_result_fraction}};
    }

    [[nodiscard]] nlohmann::json build_environment_json() {
        return {
            {"configured_environment_sha256", AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256},
            {"compiler_id", AGENT_MEMORY_EVALUATOR_COMPILER_ID},
            {"compiler_version", AGENT_MEMORY_EVALUATOR_COMPILER_VERSION},
            {"cxx_standard", AGENT_MEMORY_EVALUATOR_CXX_STANDARD},
            {"cxx_extensions", AGENT_MEMORY_EVALUATOR_CXX_EXTENSIONS != 0},
            {"generator", AGENT_MEMORY_EVALUATOR_GENERATOR},
            {"build_configuration", AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION},
            {"system_name", AGENT_MEMORY_EVALUATOR_SYSTEM_NAME},
            {"system_processor", AGENT_MEMORY_EVALUATOR_SYSTEM_PROCESSOR},
            {"pointer_bits", AGENT_MEMORY_EVALUATOR_POINTER_BITS},
            {"base_cxx_flags_sha256", AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256},
            {"active_configuration_flags_sha256", AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256},
        };
    }

} // namespace

int main(int argc, char* argv[]) {
    if(argc < 6 || argc > 8) {
        std::cerr << "usage: agent-memory-autoencoder-cascade-eval <materialization-root> <artifact.json> <report.json> <hamming-candidates> <asymmetric-candidates> [warmup-repeats] [timing-repeats]\n";
        return 2;
    }
    try {
        const auto materialization = agent_memory::load_materialized_autoencoder_evaluation_dataset(argv[1]);
        const auto artifact = agent_memory::load_autoencoder_binary_artifact(argv[2]);
        if(artifact.input_materialization_manifest_sha256 != materialization.materialization_manifest_sha256 ||
           artifact.prepared_study_manifest_sha256 != materialization.prepared_study_manifest_sha256) {
            throw std::runtime_error("artifact provenance does not match evaluation materialization");
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
        const agent_memory::AutoencoderBinaryCascadeOptions options{
            10U,
            positive_size(argv[4], "hamming-candidates"),
            positive_size(argv[5], "asymmetric-candidates"),
            argc >= 7 ? positive_size(argv[6], "warmup-repeats") : 1U,
            argc >= 8 ? positive_size(argv[7], "timing-repeats") : 3U,
        };
        const auto evaluation = agent_memory::evaluate_autoencoder_binary_cascade_with_qrels(
            document_ids, document_vectors, query_ids, query_vectors, materialization.judgments,
            artifact.encoder, options, {{1U, 5U, 10U, 100U}, {10U}}
        );
        const nlohmann::json report{
            {"schema_version", 1}, {"artifact_sha256", artifact.artifact_sha256},
            {"artifact_family", artifact.artifact_family}, {"bit_count", artifact.encoder.info().bit_count},
            {"materialization_manifest_sha256", materialization.materialization_manifest_sha256},
            {"prepared_study_manifest_sha256", materialization.prepared_study_manifest_sha256},
            {"evaluation_document_ids_sha256", materialization.evaluation_document_ids_sha256},
            {"evaluation_query_ids_sha256", materialization.evaluation_query_ids_sha256},
            {"evaluation_qrels_sha256", materialization.evaluation_qrels_sha256},
            {"evaluator_id", "agent-memory-autoencoder-cascade-eval"}, {"evaluator_version", "v1"},
            {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
            {"ranking_similarity_backend", "scalar"},
            {"evaluator_build_environment", build_environment_json()},
            {"document_count", evaluation.document_count}, {"query_count", evaluation.query_count},
            {"qrels_positive_query_count", evaluation.qrels_positive_query_count},
            {"oracle_k", evaluation.oracle_k}, {"hamming_candidate_limit", evaluation.hamming_candidate_limit},
            {"asymmetric_candidate_limit", evaluation.asymmetric_candidate_limit},
            {"binary_payload_bytes", evaluation.binary_payload_bytes}, {"float_payload_bytes", evaluation.float_payload_bytes},
            {"hamming_distance_backend", agent_memory::hamming_distance_backend_name(evaluation.hamming_backend)},
            {"hamming_exact_top_k_candidate_coverage", evaluation.hamming_exact_top_k_candidate_coverage},
            {"asymmetric_exact_top_k_candidate_coverage", evaluation.asymmetric_exact_top_k_candidate_coverage},
            {"hamming_qrels_positive_candidate_coverage", evaluation.hamming_qrels_positive_candidate_coverage},
            {"asymmetric_qrels_positive_candidate_coverage", evaluation.asymmetric_qrels_positive_candidate_coverage},
            {"reranked_recall_at_k_vs_exact", evaluation.reranked_recall_at_k_vs_exact},
            {"original_float", metrics_json(evaluation.original_float_metrics)},
            {"cascade_exact_rerank", metrics_json(evaluation.cascade_rerank_metrics)},
            {"timing", {{"document_signature_build_ms", evaluation.timing.document_signature_build_ms},
                {"query_projection_ms", statistics_json(evaluation.timing.query_projection_ms)},
                {"hamming_candidate_search_ms", statistics_json(evaluation.timing.hamming_candidate_search_ms)},
                {"asymmetric_lut_build_ms", statistics_json(evaluation.timing.asymmetric_lut_build_ms)},
                {"asymmetric_candidate_search_ms", statistics_json(evaluation.timing.asymmetric_candidate_search_ms)},
                {"exact_rerank_ms", statistics_json(evaluation.timing.exact_rerank_ms)},
                {"total_query_pipeline_ms", statistics_json(evaluation.timing.total_query_pipeline_ms)}}},
        };
        std::ofstream output(argv[3], std::ios::binary);
        if(!output) {
            throw std::runtime_error("cannot create cascade evaluation report");
        }
        output << report.dump(2) << '\n';
        if(!output) {
            throw std::runtime_error("cannot write cascade evaluation report");
        }
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "agent-memory-autoencoder-cascade-eval: " << error.what() << '\n';
        return 1;
    }
}
