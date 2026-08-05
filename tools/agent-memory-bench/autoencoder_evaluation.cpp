#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <agent_memory/eval/AutoencoderBinaryEvaluation.hpp>
#include <agent_memory/index/VectorSimilarityComputer.hpp>

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

    struct EmbeddingIdentity final {
        std::string model_id;
        std::string model_revision;
        std::string query_prefix;
        std::string document_prefix;
        bool normalized = false;
    };

    [[nodiscard]] EmbeddingIdentity load_embedding_identity(
        const std::filesystem::path& manifest_path,
        const char* owner,
        bool artifact_teacher = false
    ) {
        std::ifstream input(manifest_path, std::ios::binary);
        nlohmann::json document;
        try {
            input >> document;
        } catch(const nlohmann::json::exception& error) {
            throw std::runtime_error(std::string{"cannot parse "} + owner + ": " + error.what());
        }
        const auto& embedding = artifact_teacher ?
            document.at("training").at("teacher") : document.at("embedding");
        const auto require_string = [&embedding, owner](const char* name) {
            const auto& value = embedding.at(name);
            if(!value.is_string() || value.get_ref<const std::string&>().empty()) {
                throw std::runtime_error(std::string{owner} + " embedding field is invalid: " + name);
            }
            return value.get<std::string>();
        };
        const auto& normalized = embedding.at("normalized");
        if(!normalized.is_boolean()) {
            throw std::runtime_error(std::string{owner} + " embedding normalized must be boolean");
        }
        return {
            require_string(artifact_teacher ? "id" : "model_id"),
            require_string(artifact_teacher ? "revision" : "model_revision"),
            require_string("query_prefix"), require_string("document_prefix"),
            normalized.get<bool>(),
        };
    }

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

    [[nodiscard]] agent_memory::AutoencoderBinaryCandidateScoring parse_candidate_scoring(
        const char* text
    ) {
        const auto value = std::string{text};
        if(value == "hamming") {
            return agent_memory::AutoencoderBinaryCandidateScoring::HammingDistance;
        }
        if(value == "asymmetric-affine-dot") {
            return agent_memory::AutoencoderBinaryCandidateScoring::AsymmetricAffineDot;
        }
        throw std::invalid_argument(
            "candidate-scoring must be hamming or asymmetric-affine-dot"
        );
    }

    [[nodiscard]] const char* candidate_scoring_name(
        agent_memory::AutoencoderBinaryCandidateScoring scoring
    ) noexcept {
        return scoring == agent_memory::AutoencoderBinaryCandidateScoring::AsymmetricAffineDot
            ? "asymmetric_affine_dot_v1"
            : "hamming_distance_v1";
    }

    [[nodiscard]] agent_memory::AutoencoderBinaryAsymmetricScoringBackend
    parse_asymmetric_scoring_backend(const char* text) {
        const auto value = std::string{text};
        if(value == "scalar-reference") {
            return agent_memory::AutoencoderBinaryAsymmetricScoringBackend::ScalarReference;
        }
        if(value == "byte-lut") {
            return agent_memory::AutoencoderBinaryAsymmetricScoringBackend::ByteLookupTable;
        }
        throw std::invalid_argument(
            "asymmetric-scoring-backend must be scalar-reference or byte-lut"
        );
    }

    [[nodiscard]] const char* asymmetric_scoring_backend_name(
        agent_memory::AutoencoderBinaryCandidateScoring scoring,
        agent_memory::AutoencoderBinaryAsymmetricScoringBackend backend
    ) noexcept {
        return scoring == agent_memory::AutoencoderBinaryCandidateScoring::AsymmetricAffineDot
            ? backend == agent_memory::AutoencoderBinaryAsymmetricScoringBackend::ScalarReference
                ? "scalar_reference_v1"
                : "byte_lookup_table_v1"
            : "not_applicable";
    }

    [[nodiscard]] std::string language_id_from_record_id(const std::string& record_id) {
        const auto separator = record_id.find(':');
        if(separator == std::string::npos || separator == 0U) {
            throw std::runtime_error("MIRACL record ID must begin with a language prefix");
        }
        return record_id.substr(0U, separator);
    }

    [[nodiscard]] std::vector<std::string> evaluation_language_ids(
        const std::vector<std::string>& document_ids,
        const std::vector<std::string>& query_ids
    ) {
        std::set<std::string> document_languages;
        std::set<std::string> query_languages;
        for(const auto& id : document_ids) {
            document_languages.insert(language_id_from_record_id(id));
        }
        for(const auto& id : query_ids) {
            query_languages.insert(language_id_from_record_id(id));
        }
        if(document_languages.empty() || document_languages != query_languages) {
            throw std::runtime_error("MIRACL evaluation document and query languages must match");
        }
        return {document_languages.begin(), document_languages.end()};
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
            {"total_bit_entropy", health.total_bit_entropy},
            {"min_bit_entropy", health.min_bit_entropy},
            {"p05_bit_entropy", health.p05_bit_entropy},
            {"median_bit_entropy", health.median_bit_entropy},
            {"p95_bit_entropy", health.p95_bit_entropy},
            {"max_bit_entropy", health.max_bit_entropy},
            {"correlation_sample_count", health.correlation_sample_count},
            {"mean_absolute_bit_correlation", health.mean_absolute_bit_correlation},
            {"p95_absolute_bit_correlation", health.p95_absolute_bit_correlation},
            {"p99_absolute_bit_correlation", health.p99_absolute_bit_correlation},
            {"max_absolute_bit_correlation", health.max_absolute_bit_correlation},
            {"bit_correlation_participation_ratio",
             health.bit_correlation_participation_ratio},
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
            {"dense_nearest_hamming_distance",
             descriptive_statistics_json(diagnostics.dense_nearest_hamming_distance)},
            {"dense_rank_100_hamming_distance",
             descriptive_statistics_json(diagnostics.dense_rank_100_hamming_distance)},
            {"dense_neighbour_hamming_margin",
             descriptive_statistics_json(diagnostics.dense_neighbour_hamming_margin)},
            {"nonpositive_dense_neighbour_hamming_margin_fraction",
             diagnostics.nonpositive_dense_neighbour_hamming_margin_fraction},
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

    [[nodiscard]] nlohmann::json evaluator_build_environment_json() {
        const std::string build_configuration = AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION;
        return {
            {"configured_environment_sha256", AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256},
            {"compiler_id", AGENT_MEMORY_EVALUATOR_COMPILER_ID},
            {"compiler_version", AGENT_MEMORY_EVALUATOR_COMPILER_VERSION},
            {"cxx_standard", AGENT_MEMORY_EVALUATOR_CXX_STANDARD},
            {"cxx_extensions", AGENT_MEMORY_EVALUATOR_CXX_EXTENSIONS != 0},
            {"generator", AGENT_MEMORY_EVALUATOR_GENERATOR},
            {"build_configuration",
             build_configuration.empty() ? "unspecified" : build_configuration},
            {"system_name", AGENT_MEMORY_EVALUATOR_SYSTEM_NAME},
            {"system_processor", AGENT_MEMORY_EVALUATOR_SYSTEM_PROCESSOR},
            {"pointer_bits", AGENT_MEMORY_EVALUATOR_POINTER_BITS},
            {"base_cxx_flags_sha256", AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256},
            {"active_configuration_flags_sha256",
             AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256},
        };
    }

} // namespace

int main(int argc, char* argv[]) {
    if(argc < 4 || argc > 8) {
        std::cerr << "usage: agent-memory-autoencoder-eval <materialization-root> <artifact.json> <report.json> [oracle-k] [candidate-limit] [candidate-scoring] [asymmetric-scoring-backend]\n";
        return 2;
    }
    try {
        const auto materialization =
            agent_memory::load_materialized_autoencoder_evaluation_dataset(argv[1]);
        const auto artifact = agent_memory::load_autoencoder_binary_artifact(argv[2]);
        const auto evaluation_identity = load_embedding_identity(
            std::filesystem::path{argv[1]} / "manifest.json", "evaluation materialization manifest"
        );
        if(artifact.artifact_family == "nlb_qrels_supervised_v1") {
            const auto training_identity = load_embedding_identity(
                argv[2], "qrels-supervised artifact", true
            );
            if(training_identity.model_id != evaluation_identity.model_id ||
               training_identity.model_revision != evaluation_identity.model_revision ||
               training_identity.query_prefix != evaluation_identity.query_prefix ||
               training_identity.document_prefix != evaluation_identity.document_prefix ||
               training_identity.normalized != evaluation_identity.normalized) {
                throw std::runtime_error("artifact and evaluation embedding identities differ");
            }
            nlohmann::json artifact_document;
            nlohmann::json prepared_study;
            std::ifstream artifact_input(argv[2], std::ios::binary);
            std::ifstream prepared_input(
                std::filesystem::path{argv[1]} / "prepared-study-manifest.json", std::ios::binary
            );
            artifact_input >> artifact_document;
            prepared_input >> prepared_study;
            const auto& exclusion = artifact_document.at("training").at("held_out_exclusion");
            const auto& evaluated_ids = prepared_study.at("split").at(
                "evaluation_document_ids_sha256"
            );
            if(!exclusion.at("document_ids_set_sha256").is_string() ||
               !evaluated_ids.is_string() ||
               exclusion.at("document_ids_set_sha256") != evaluated_ids) {
                throw std::runtime_error(
                    "supervised artifact exclusion set does not match evaluation documents"
                );
            }
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
            argc >= 7 ? parse_candidate_scoring(argv[6]) :
                agent_memory::AutoencoderBinaryCandidateScoring::HammingDistance,
            argc >= 8 ? parse_asymmetric_scoring_backend(argv[7]) :
                agent_memory::AutoencoderBinaryAsymmetricScoringBackend::ByteLookupTable,
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
        const auto language_ids = evaluation_language_ids(document_ids, query_ids);
        if(language_ids.size() != 1U) {
            throw std::runtime_error(
                "mixed-language MIRACL evaluation requires per-language corpus filtering"
            );
        }
        const nlohmann::json report{
            {"schema_version", 2},
            {"artifact_sha256", artifact.artifact_sha256},
            {"artifact_family", artifact.artifact_family},
            {"bit_count", artifact.encoder.info().bit_count},
            {"training_document_ids_sha256", artifact.training_document_ids_sha256},
            {"validation_document_ids_sha256", artifact.validation_document_ids_sha256},
            {"calibration_document_ids_sha256", artifact.calibration_document_ids_sha256},
            {"artifact_input_materialization_manifest_sha256",
             artifact.input_materialization_manifest_sha256},
            {"artifact_prepared_study_manifest_sha256",
             artifact.prepared_study_manifest_sha256},
            {"materialization_manifest_sha256", materialization.materialization_manifest_sha256},
            {"prepared_study_manifest_sha256", materialization.prepared_study_manifest_sha256},
            {"artifact_evaluation_materialization_match",
             artifact.input_materialization_manifest_sha256 ==
                 materialization.materialization_manifest_sha256},
            {"evaluation_embedding_identity", {
                {"model_id", evaluation_identity.model_id},
                {"model_revision", evaluation_identity.model_revision},
                {"query_prefix", evaluation_identity.query_prefix},
                {"document_prefix", evaluation_identity.document_prefix},
                {"normalized", evaluation_identity.normalized},
            }},
            {"evaluation_document_ids_sha256", materialization.evaluation_document_ids_sha256},
            {"evaluation_query_ids_sha256", materialization.evaluation_query_ids_sha256},
            {"evaluation_qrels_sha256", materialization.evaluation_qrels_sha256},
            {"tie_break_policy", "score_desc_document_id_asc_v1"},
            {"evaluator_id", "agent-memory-autoencoder-eval"},
            {"evaluator_version", "v1"},
            {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
            {"ranking_similarity_backend", agent_memory::vector_similarity_backend_name(
                agent_memory::VectorSimilarityComputer(agent_memory::VectorSimilarityBackend::Scalar).backend()
            )},
            {"evaluator_build_environment", evaluator_build_environment_json()},
            {"evaluation_protocol", "miracl_monolingual_per_language_v1"},
            {"language_ids", language_ids},
            {"document_count", materialization.document_embeddings.size()},
            {"query_count", materialization.query_embeddings.size()},
            {"oracle_k", evaluation.exact_agreement.oracle_k},
            {"returned_candidate_limit", evaluation.exact_agreement.returned_candidate_limit},
            {"candidate_scoring", candidate_scoring_name(binary_options.candidate_scoring)},
            {"asymmetric_scoring_backend", asymmetric_scoring_backend_name(
                binary_options.candidate_scoring,
                binary_options.asymmetric_scoring_backend
            )},
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
