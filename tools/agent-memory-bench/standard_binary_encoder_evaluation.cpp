#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <agent_memory/eval/Evaluation.hpp>
#include <agent_memory/index/CoordinateSignBinaryEncoder.hpp>
#include <agent_memory/index/IBinarySignatureEncoder.hpp>
#include <agent_memory/index/ItqRotationBinaryEncoder.hpp>
#include <agent_memory/index/LearnedProjectionBinaryEncoder.hpp>
#include <agent_memory/index/PcaProjectionBinaryEncoder.hpp>
#include <agent_memory/index/RandomizedHadamardBinaryEncoder.hpp>
#include <agent_memory/index/RandomHyperplaneBinaryEncoder.hpp>
#include <agent_memory/index/VectorSimilarityComputer.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <set>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

    struct ScoredPosition final {
        std::size_t position = 0;
        float score = 0.0F;
        std::string_view document_id;
    };

    struct EncoderReport final {
        std::string family;
        std::size_t bit_count = 0;
        std::size_t training_vector_count = 0;
        double exact_top_k_candidate_coverage = 0.0;
        double candidate_coverage_lift_vs_random = 0.0;
        double mean_dense_nearest_hamming_distance = 0.0;
        double mean_dense_rank_100_hamming_distance = 0.0;
        double mean_dense_neighbour_hamming_margin = 0.0;
        double nonpositive_dense_neighbour_hamming_margin_fraction = 0.0;
        agent_memory::RetrievalMetrics original_float_metrics;
        agent_memory::RetrievalMetrics binary_rerank_metrics;
        agent_memory::BinaryCodeHealthMetrics document_code_health;
    };

    [[nodiscard]] bool better_score(const ScoredPosition& lhs, const ScoredPosition& rhs) noexcept {
        return lhs.score == rhs.score ? lhs.document_id < rhs.document_id : lhs.score > rhs.score;
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

    [[nodiscard]] std::vector<std::string> load_training_ids(
        const std::filesystem::path& path
    ) {
        std::ifstream input(path, std::ios::binary);
        if(!input) {
            throw std::runtime_error("cannot open canonical training ID list");
        }
        std::vector<std::string> output;
        std::unordered_set<std::string> seen;
        std::string line;
        while(std::getline(input, line)) {
            const auto row = nlohmann::json::parse(line);
            if(!row.is_object() || !row.contains("id") || !row.at("id").is_string()) {
                throw std::runtime_error("canonical training ID list row must contain string id");
            }
            const auto id = row.at("id").get<std::string>();
            if(id.empty() || !seen.insert(id).second) {
                throw std::runtime_error("canonical training ID list contains empty or duplicate id");
            }
            output.push_back(id);
        }
        if(output.empty()) {
            throw std::runtime_error("canonical training ID list is empty");
        }
        return output;
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
        if(document_languages.size() != 1U || document_languages != query_languages) {
            throw std::runtime_error("mixed-language MIRACL evaluation requires per-language corpus filtering");
        }
        return {document_languages.begin(), document_languages.end()};
    }

    [[nodiscard]] float inverse_norm(
        const agent_memory::Embedding& embedding,
        const agent_memory::VectorSimilarityComputer& similarity
    ) noexcept {
        const auto squared_norm = similarity.squared_norm(embedding);
        return squared_norm > 0.0F ? 1.0F / std::sqrt(squared_norm) : 0.0F;
    }

    [[nodiscard]] std::vector<ScoredPosition> cosine_rank(
        const agent_memory::Embedding& query,
        float query_inverse_norm,
        const std::vector<agent_memory::Embedding>& documents,
        const std::vector<std::string>& document_ids,
        const std::vector<float>& document_inverse_norms,
        const agent_memory::VectorSimilarityComputer& similarity
    ) {
        std::vector<ScoredPosition> output;
        output.reserve(documents.size());
        for(std::size_t position = 0; position < documents.size(); ++position) {
            output.push_back({
                position,
                similarity.dot_product_values(
                    query.values.data(), documents[position].values.data(), query.dimension()
                ) * query_inverse_norm * document_inverse_norms[position],
                document_ids[position],
            });
        }
        std::sort(output.begin(), output.end(), better_score);
        return output;
    }

    [[nodiscard]] double overlap_fraction(
        const std::vector<ScoredPosition>& oracle,
        const std::vector<ScoredPosition>& candidates,
        std::size_t oracle_k,
        std::size_t candidate_limit
    ) {
        std::vector<bool> selected(oracle.size(), false);
        for(std::size_t index = 0;
            index < std::min(candidate_limit, candidates.size());
            ++index) {
            selected[candidates[index].position] = true;
        }
        std::size_t overlap = 0;
        for(std::size_t index = 0; index < oracle_k; ++index) {
            overlap += selected[oracle[index].position] ? 1U : 0U;
        }
        return static_cast<double>(overlap) / static_cast<double>(oracle_k);
    }

    [[nodiscard]] agent_memory::RetrievalEvalDataset make_dataset(
        const std::vector<std::string>& document_ids,
        const std::vector<std::string>& query_ids,
        const std::vector<agent_memory::RelevanceJudgment>& judgments
    ) {
        agent_memory::RetrievalEvalDataset output;
        output.name = "materialized-standard-binary-encoder-evaluation";
        output.judgments = judgments;
        for(const auto& id : document_ids) {
            output.corpus.push_back({id, {}, {}, {}});
        }
        for(const auto& id : query_ids) {
            output.queries.push_back({
                id, "materialized embedding", {}, 10, {},
                agent_memory::EvalQueryAnswerMode::JudgedRetrieval
            });
        }
        agent_memory::validate_retrieval_eval_dataset(output);
        return output;
    }

    [[nodiscard]] agent_memory::RetrievalQueryRun make_query_run(
        const std::string& query_id,
        const std::vector<ScoredPosition>& ranking,
        const std::vector<std::string>& document_ids,
        const char* retriever_name
    ) {
        agent_memory::RetrievalQueryRun output;
        output.query_id = query_id;
        output.hits.reserve(ranking.size());
        for(const auto& entry : ranking) {
            output.hits.push_back({
                document_ids[entry.position], entry.score, 0, retriever_name
            });
        }
        return output;
    }

    [[nodiscard]] agent_memory::RetrievalEvalDataset single_query_dataset(
        const agent_memory::RetrievalEvalDataset& dataset,
        std::size_t query_index
    ) {
        agent_memory::RetrievalEvalDataset output;
        output.queries.push_back(dataset.queries.at(query_index));
        for(const auto& judgment : dataset.judgments) {
            if(judgment.query_id == output.queries.front().id) {
                output.judgments.push_back(judgment);
            }
        }
        return output;
    }

    void accumulate_metrics(
        agent_memory::RetrievalMetrics& total,
        const agent_memory::RetrievalMetrics& query_metrics
    ) {
        if(total.recall_at.empty() && total.ndcg_at.empty() && total.query_count == 0U) {
            total.recall_at = query_metrics.recall_at;
            total.ndcg_at = query_metrics.ndcg_at;
            for(auto& metric : total.recall_at) { metric.value = 0.0; }
            for(auto& metric : total.ndcg_at) { metric.value = 0.0; }
        }
        if(total.recall_at.size() != query_metrics.recall_at.size() ||
           total.ndcg_at.size() != query_metrics.ndcg_at.size()) {
            throw std::logic_error("inconsistent per-query retrieval metric cutoffs");
        }
        const auto judged = static_cast<double>(query_metrics.judged_query_count);
        const auto no_answer = static_cast<double>(query_metrics.no_answer_query_count);
        for(std::size_t index = 0; index < total.recall_at.size(); ++index) {
            total.recall_at[index].value += query_metrics.recall_at[index].value * judged;
        }
        for(std::size_t index = 0; index < total.ndcg_at.size(); ++index) {
            total.ndcg_at[index].value += query_metrics.ndcg_at[index].value * judged;
        }
        total.mrr += query_metrics.mrr * judged;
        total.no_answer_accuracy += query_metrics.no_answer_accuracy * no_answer;
        total.query_count += query_metrics.query_count;
        total.judged_query_count += query_metrics.judged_query_count;
        total.no_answer_query_count += query_metrics.no_answer_query_count;
        total.ignored_query_count += query_metrics.ignored_query_count;
        total.evaluated_query_count += query_metrics.evaluated_query_count;
        total.evaluated_query_run_count += query_metrics.evaluated_query_run_count;
        total.evaluated_query_latency_count += query_metrics.evaluated_query_latency_count;
        total.ignored_query_run_count += query_metrics.ignored_query_run_count;
        total.empty_result_count += query_metrics.empty_result_count;
    }

    void finalize_metrics(agent_memory::RetrievalMetrics& metrics) noexcept {
        if(metrics.judged_query_count != 0U) {
            const auto judged = static_cast<double>(metrics.judged_query_count);
            for(auto& metric : metrics.recall_at) { metric.value /= judged; }
            for(auto& metric : metrics.ndcg_at) { metric.value /= judged; }
            metrics.mrr /= judged;
        }
        if(metrics.no_answer_query_count != 0U) {
            metrics.no_answer_accuracy /= static_cast<double>(metrics.no_answer_query_count);
        }
        if(metrics.evaluated_query_count != 0U) {
            metrics.empty_result_fraction = static_cast<double>(metrics.empty_result_count) /
                static_cast<double>(metrics.evaluated_query_count);
        }
    }

    [[nodiscard]] EncoderReport evaluate_encoder(
        const std::string& family,
        std::size_t training_vector_count,
        const agent_memory::IBinarySignatureEncoder& encoder,
        const std::vector<std::string>& document_ids,
        const std::vector<agent_memory::Embedding>& document_vectors,
        const std::vector<std::string>& query_ids,
        const std::vector<agent_memory::Embedding>& query_vectors,
        const agent_memory::RetrievalEvalDataset& dataset,
        std::size_t oracle_k,
        std::size_t candidate_limit
    ) {
        const auto& info = encoder.info();
        if(info.input_dimension != document_vectors.front().dimension()) {
            throw std::invalid_argument("encoder input dimension does not match materialized vectors");
        }
        const auto effective_oracle_k = std::min(oracle_k, document_vectors.size());
        const auto effective_candidate_limit = std::min(candidate_limit, document_vectors.size());
        const agent_memory::VectorSimilarityComputer similarity(
            agent_memory::VectorSimilarityBackend::Scalar
        );
        std::vector<float> document_inverse_norms;
        document_inverse_norms.reserve(document_vectors.size());
        for(const auto& document : document_vectors) {
            document_inverse_norms.push_back(inverse_norm(document, similarity));
        }
        const auto document_signatures = encoder.encode_batch(document_vectors);
        if(document_signatures.size() != document_vectors.size()) {
            throw std::logic_error("encoder batch result count mismatch");
        }
        agent_memory::RetrievalMetrics original_metrics;
        agent_memory::RetrievalMetrics binary_metrics;
        double coverage_sum = 0.0;
        double dense_nearest_hamming_sum = 0.0;
        double dense_rank_100_hamming_sum = 0.0;
        double dense_neighbour_hamming_margin_sum = 0.0;
        std::size_t nonpositive_dense_neighbour_hamming_margin_count = 0;
        for(std::size_t query_index = 0; query_index < query_vectors.size(); ++query_index) {
            const auto& query = query_vectors[query_index];
            const auto query_inverse_norm = inverse_norm(query, similarity);
            const auto oracle = cosine_rank(
                query, query_inverse_norm, document_vectors, document_ids,
                document_inverse_norms, similarity
            );
            const auto query_dataset = single_query_dataset(dataset, query_index);
            accumulate_metrics(original_metrics, agent_memory::evaluate_retrieval(
                query_dataset, {"original_float", {
                    make_query_run(query_ids[query_index], oracle, document_ids, "original_float")
                }}, {{1U, 5U, 10U, 100U}, {10U}}
            ));
            const auto query_signature = encoder.encode(query);
            const auto dense_nearest_hamming = agent_memory::hamming_distance(
                query_signature, document_signatures[oracle.front().position]
            );
            const auto dense_rank_100_hamming = agent_memory::hamming_distance(
                query_signature,
                document_signatures[oracle[std::min<std::size_t>(99U, oracle.size() - 1U)].position]
            );
            const auto dense_neighbour_hamming_margin =
                static_cast<double>(dense_rank_100_hamming) -
                static_cast<double>(dense_nearest_hamming);
            dense_nearest_hamming_sum += static_cast<double>(dense_nearest_hamming);
            dense_rank_100_hamming_sum += static_cast<double>(dense_rank_100_hamming);
            dense_neighbour_hamming_margin_sum += dense_neighbour_hamming_margin;
            nonpositive_dense_neighbour_hamming_margin_count +=
                dense_neighbour_hamming_margin <= 0.0 ? 1U : 0U;
            std::vector<ScoredPosition> candidates;
            candidates.reserve(document_signatures.size());
            for(std::size_t position = 0; position < document_signatures.size(); ++position) {
                candidates.push_back({
                    position,
                    -static_cast<float>(agent_memory::hamming_distance(
                        query_signature, document_signatures[position]
                    )),
                    document_ids[position],
                });
            }
            std::sort(candidates.begin(), candidates.end(), better_score);
            candidates.resize(effective_candidate_limit);
            coverage_sum += overlap_fraction(
                oracle,
                candidates,
                effective_oracle_k,
                effective_candidate_limit
            );
            for(auto& candidate : candidates) {
                candidate.score = similarity.dot_product_values(
                    query.values.data(), document_vectors[candidate.position].values.data(), query.dimension()
                ) * query_inverse_norm * document_inverse_norms[candidate.position];
                candidate.document_id = document_ids[candidate.position];
            }
            std::sort(candidates.begin(), candidates.end(), better_score);
            accumulate_metrics(binary_metrics, agent_memory::evaluate_retrieval(
                query_dataset, {"binary_candidates_exact_rerank", {
                    make_query_run(query_ids[query_index], candidates, document_ids, "binary_exact_rerank")
                }}, {{1U, 5U, 10U, 100U}, {10U}}
            ));
        }
        EncoderReport output;
        output.family = family;
        output.bit_count = info.bit_count;
        output.training_vector_count = training_vector_count;
        output.exact_top_k_candidate_coverage = coverage_sum /
            static_cast<double>(query_vectors.size());
        output.mean_dense_nearest_hamming_distance = dense_nearest_hamming_sum /
            static_cast<double>(query_vectors.size());
        output.mean_dense_rank_100_hamming_distance = dense_rank_100_hamming_sum /
            static_cast<double>(query_vectors.size());
        output.mean_dense_neighbour_hamming_margin = dense_neighbour_hamming_margin_sum /
            static_cast<double>(query_vectors.size());
        output.nonpositive_dense_neighbour_hamming_margin_fraction =
            static_cast<double>(nonpositive_dense_neighbour_hamming_margin_count) /
            static_cast<double>(query_vectors.size());
        output.candidate_coverage_lift_vs_random =
            output.exact_top_k_candidate_coverage /
            (static_cast<double>(effective_candidate_limit) /
             static_cast<double>(document_vectors.size()));
        finalize_metrics(original_metrics);
        finalize_metrics(binary_metrics);
        output.original_float_metrics = std::move(original_metrics);
        output.binary_rerank_metrics = std::move(binary_metrics);
        output.document_code_health = agent_memory::analyze_binary_code_health(document_signatures);
        return output;
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

    [[nodiscard]] nlohmann::json metrics_json(const agent_memory::RetrievalMetrics& metrics) {
        return {
            {"evaluated_query_count", metrics.evaluated_query_count},
            {"recall_at", metric_values(metrics.recall_at)},
            {"ndcg_at", metric_values(metrics.ndcg_at)},
            {"mrr", metrics.mrr},
            {"empty_result_fraction", metrics.empty_result_fraction},
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

    [[nodiscard]] nlohmann::json report_json(const EncoderReport& report) {
        return {
            {"encoder_family", report.family},
            {"bit_count", report.bit_count},
            {"training_vector_count", report.training_vector_count},
            {"exact_top_k_candidate_coverage", report.exact_top_k_candidate_coverage},
            {"candidate_coverage_lift_vs_random", report.candidate_coverage_lift_vs_random},
            {"mean_dense_nearest_hamming_distance",
             report.mean_dense_nearest_hamming_distance},
            {"mean_dense_rank_100_hamming_distance",
             report.mean_dense_rank_100_hamming_distance},
            {"mean_dense_neighbour_hamming_margin",
             report.mean_dense_neighbour_hamming_margin},
            {"nonpositive_dense_neighbour_hamming_margin_fraction",
             report.nonpositive_dense_neighbour_hamming_margin_fraction},
            {"original_float", metrics_json(report.original_float_metrics)},
            {"binary_candidates_exact_rerank", metrics_json(report.binary_rerank_metrics)},
            {"document_code_health", {
                {"constant_bit_fraction", report.document_code_health.constant_bit_fraction},
                {"mean_bit_entropy", report.document_code_health.mean_bit_entropy},
                {"total_bit_entropy", report.document_code_health.total_bit_entropy},
                {"p05_bit_entropy", report.document_code_health.p05_bit_entropy},
                {"median_bit_entropy", report.document_code_health.median_bit_entropy},
                {"p95_bit_entropy", report.document_code_health.p95_bit_entropy},
                {"correlation_sample_count",
                 report.document_code_health.correlation_sample_count},
                {"mean_absolute_bit_correlation",
                 report.document_code_health.mean_absolute_bit_correlation},
                {"p95_absolute_bit_correlation",
                 report.document_code_health.p95_absolute_bit_correlation},
                {"p99_absolute_bit_correlation",
                 report.document_code_health.p99_absolute_bit_correlation},
                {"max_absolute_bit_correlation",
                 report.document_code_health.max_absolute_bit_correlation},
                {"bit_correlation_participation_ratio",
                 report.document_code_health.bit_correlation_participation_ratio},
                {"duplicate_signature_rate", report.document_code_health.duplicate_signature_rate},
            }},
        };
    }

} // namespace

int main(int argc, char* argv[]) {
    if(argc == 2 && std::string{argv[1]} == "--self-test") {
        const auto build_environment = evaluator_build_environment_json();
        const auto manifest = build_environment.value("configured_environment_sha256", std::string{});
        if(manifest.size() != 64U ||
           build_environment.value("cxx_standard", 0) != 17 ||
           !build_environment.contains("cxx_extensions") ||
           !build_environment.at("cxx_extensions").is_boolean() ||
           build_environment.value("build_configuration", std::string{}).empty() ||
           build_environment.value("base_cxx_flags_sha256", std::string{}).size() != 64U ||
           build_environment.value("active_configuration_flags_sha256", std::string{}).size() != 64U ||
           build_environment.value("pointer_bits", 0) != static_cast<int>(sizeof(void*) * 8U) ||
           build_environment.value("compiler_id", std::string{}).empty() ||
           build_environment.value("system_name", std::string{}).empty()) {
            std::cerr << "standard binary encoder evaluator build-environment self-test failed\n";
            return 1;
        }
        const std::vector<ScoredPosition> oracle{{0U, 1.0F, "a"}, {1U, 0.5F, "b"}};
        const std::vector<ScoredPosition> candidates{
            {0U, 1.0F, "a"}, {2U, 0.9F, "c"}, {1U, 0.8F, "b"}
        };
        if(overlap_fraction(oracle, candidates, 2U, 1U) != 0.5 ||
           overlap_fraction(oracle, candidates, 2U, 3U) != 1.0) {
            std::cerr << "standard binary encoder evaluator candidate coverage self-test failed\n";
            return 1;
        }
        std::vector<ScoredPosition> tie_first{{0U, -1.0F, "document-b"},
                                              {1U, -1.0F, "document-a"}};
        std::vector<ScoredPosition> tie_permuted{{0U, -1.0F, "document-a"},
                                                 {1U, -1.0F, "document-b"}};
        std::sort(tie_first.begin(), tie_first.end(), better_score);
        std::sort(tie_permuted.begin(), tie_permuted.end(), better_score);
        if(tie_first.front().document_id != "document-a" ||
           tie_permuted.front().document_id != "document-a") {
            std::cerr << "standard binary encoder evaluator tie-break self-test failed\n";
            return 1;
        }
        const std::vector<std::string> test_document_ids{"document-a", "document-b"};
        const std::vector<std::string> test_query_ids{"query-a", "query-b"};
        const auto test_dataset = make_dataset(
            test_document_ids,
            test_query_ids,
            {
                {"query-a", "document-a", 1},
                {"query-b", "document-b", 1},
            }
        );
        const std::vector<ScoredPosition> first_query_ranking{
            {0U, 1.0F, "document-a"}, {1U, 0.5F, "document-b"}
        };
        const std::vector<ScoredPosition> second_query_ranking{
            {0U, 1.0F, "document-a"}, {1U, 0.5F, "document-b"}
        };
        const agent_memory::RetrievalEvaluationOptions options{{1U, 5U, 10U}, {10U}};
        const auto retained_metrics = agent_memory::evaluate_retrieval(
            test_dataset,
            {"retained", {
                make_query_run("query-a", first_query_ranking, test_document_ids, "retained"),
                make_query_run("query-b", second_query_ranking, test_document_ids, "retained"),
            }},
            options
        );
        agent_memory::RetrievalMetrics streaming_metrics;
        for(std::size_t query_index = 0; query_index < test_query_ids.size(); ++query_index) {
            const auto& ranking = query_index == 0U ? first_query_ranking : second_query_ranking;
            accumulate_metrics(streaming_metrics, agent_memory::evaluate_retrieval(
                single_query_dataset(test_dataset, query_index),
                {"streaming", {
                    make_query_run(test_query_ids[query_index], ranking, test_document_ids, "streaming")
                }},
                options
            ));
        }
        finalize_metrics(streaming_metrics);
        const auto metrics_match = [](const agent_memory::RetrievalMetrics& lhs,
                                      const agent_memory::RetrievalMetrics& rhs) {
            if(lhs.query_count != rhs.query_count ||
               lhs.judged_query_count != rhs.judged_query_count ||
               lhs.evaluated_query_count != rhs.evaluated_query_count ||
               lhs.empty_result_count != rhs.empty_result_count ||
               lhs.recall_at.size() != rhs.recall_at.size() ||
               lhs.ndcg_at.size() != rhs.ndcg_at.size() ||
               std::fabs(lhs.mrr - rhs.mrr) > 1.0e-12 ||
               std::fabs(lhs.empty_result_fraction - rhs.empty_result_fraction) > 1.0e-12) {
                return false;
            }
            for(std::size_t index = 0; index < lhs.recall_at.size(); ++index) {
                if(lhs.recall_at[index].k != rhs.recall_at[index].k ||
                   std::fabs(lhs.recall_at[index].value - rhs.recall_at[index].value) > 1.0e-12) {
                    return false;
                }
            }
            for(std::size_t index = 0; index < lhs.ndcg_at.size(); ++index) {
                if(lhs.ndcg_at[index].k != rhs.ndcg_at[index].k ||
                   std::fabs(lhs.ndcg_at[index].value - rhs.ndcg_at[index].value) > 1.0e-12) {
                    return false;
                }
            }
            return true;
        };
        if(!metrics_match(retained_metrics, streaming_metrics)) {
            std::cerr << "standard binary encoder evaluator streaming parity self-test failed\n";
            return 1;
        }
        return 0;
    }
    if(argc != 6 && argc != 7) {
        std::cerr << "usage: agent-memory-standard-binary-eval <materialization-root> <report.json> <bit-count> <candidate-limit> <seed> [train-ids.jsonl]\n";
        return 2;
    }
    try {
        const auto bit_count = parse_positive_size(argv[3], "bit-count");
        const auto candidate_limit = parse_positive_size(argv[4], "candidate-limit");
        const auto seed = static_cast<std::uint64_t>(parse_positive_size(argv[5], "seed"));
        const auto materialization =
            agent_memory::load_materialized_autoencoder_evaluation_dataset(argv[1]);
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
        std::vector<agent_memory::Embedding> training_vectors;
        std::string training_id_list_path;
        std::string training_id_list_sha256 = materialization.training_document_ids_sha256;
        if(argc == 7) {
            const auto selected_ids = load_training_ids(argv[6]);
            std::unordered_map<std::string, const agent_memory::Embedding*> available;
            available.reserve(materialization.training_embeddings.size());
            for(const auto& record : materialization.training_embeddings) {
                available.emplace(record.id, &record.embedding);
            }
            training_vectors.reserve(selected_ids.size());
            for(const auto& id : selected_ids) {
                const auto existing = available.find(id);
                if(existing == available.end()) {
                    throw std::runtime_error("canonical training ID is absent from materialization");
                }
                training_vectors.push_back(*existing->second);
            }
            training_id_list_path = argv[6];
            training_id_list_sha256 = agent_memory::sha256_file_hex(argv[6]);
        } else {
            training_vectors.reserve(materialization.training_embeddings.size());
            for(const auto& record : materialization.training_embeddings) {
                training_vectors.push_back(record.embedding);
            }
        }
        const auto dimension = document_vectors.front().dimension();
        const auto language_ids = evaluation_language_ids(document_ids, query_ids);
        const auto dataset = make_dataset(document_ids, query_ids, materialization.judgments);
        std::vector<EncoderReport> reports;
        const auto append = [&](const char* family, std::size_t training_count,
                                const agent_memory::IBinarySignatureEncoder& encoder) {
            reports.push_back(evaluate_encoder(
                family, training_count, encoder, document_ids, document_vectors, query_ids,
                query_vectors, dataset, 10U, candidate_limit
            ));
        };

        agent_memory::RandomHyperplaneBinaryEncoder random_hyperplane({dimension, bit_count, seed});
        append("random_hyperplane_rademacher", 0U, random_hyperplane);
        agent_memory::RandomizedHadamardBinaryEncoder randomized_hadamard({dimension, bit_count, seed});
        append("randomized_hadamard", 0U, randomized_hadamard);
        agent_memory::LearnedProjectionBinaryEncoder learned(
            agent_memory::train_learned_projection_encoder(
                training_vectors, {dimension, bit_count, seed, training_vectors.size()}
            )
        );
        append("pair_difference_projection", training_vectors.size(), learned);
        if(bit_count <= dimension) {
            agent_memory::PcaProjectionBinaryEncoder pca(
                agent_memory::train_pca_projection_encoder(
                    training_vectors, {dimension, bit_count, seed, 24U, training_vectors.size()}
                )
            );
            append("pca_sign", training_vectors.size(), pca);
            agent_memory::ItqRotationBinaryEncoder itq(
                agent_memory::train_itq_rotation_encoder(
                    training_vectors, {dimension, bit_count, seed, 24U, 16U, training_vectors.size()}
                )
            );
            append("itq_rotation", training_vectors.size(), itq);
        }
        if(bit_count == dimension) {
            agent_memory::CoordinateSignBinaryEncoder coordinate_sign({dimension});
            append("coordinate_sign", 0U, coordinate_sign);
        }

        nlohmann::json output{
            {"schema_version", 2},
            {"materialization_manifest_sha256", materialization.materialization_manifest_sha256},
            {"prepared_study_manifest_sha256", materialization.prepared_study_manifest_sha256},
            {"materialized_training_document_ids_sha256", materialization.training_document_ids_sha256},
            {"evaluation_document_ids_sha256", materialization.evaluation_document_ids_sha256},
            {"evaluation_query_ids_sha256", materialization.evaluation_query_ids_sha256},
            {"evaluation_qrels_sha256", materialization.evaluation_qrels_sha256},
            {"evaluation_protocol", "miracl_monolingual_per_language_v1"},
            {"language_ids", language_ids},
            {"tie_break_policy", "score_desc_document_id_asc_v1"},
            {"evaluator_id", "agent-memory-standard-binary-eval"},
            {"evaluator_version", "v1"},
            {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
            {"ranking_similarity_backend", agent_memory::vector_similarity_backend_name(
                agent_memory::VectorSimilarityComputer(agent_memory::VectorSimilarityBackend::Scalar).backend()
            )},
            {"evaluator_build_environment", evaluator_build_environment_json()},
            {"document_count", document_vectors.size()},
            {"query_count", query_vectors.size()},
            {"training_document_count", training_vectors.size()},
            {"training_id_list_path", training_id_list_path},
            {"training_document_ids_sha256", training_id_list_sha256},
            {"oracle_k", 10},
            {"returned_candidate_limit", candidate_limit},
            {"seed", seed},
            {"reports", nlohmann::json::array()},
        };
        for(const auto& report : reports) {
            output["reports"].push_back(report_json(report));
        }
        std::ofstream stream(argv[2], std::ios::binary);
        if(!stream) {
            throw std::runtime_error("cannot create standard binary encoder report");
        }
        stream << output.dump(2) << '\n';
        if(!stream) {
            throw std::runtime_error("cannot write standard binary encoder report");
        }
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "agent-memory-standard-binary-eval: " << error.what() << '\n';
        return 1;
    }
}
