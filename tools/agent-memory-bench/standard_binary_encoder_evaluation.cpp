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
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

    struct ScoredPosition final {
        std::size_t position = 0;
        float score = 0.0F;
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
        return lhs.score == rhs.score ? lhs.position < rhs.position : lhs.score > rhs.score;
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
        const agent_memory::VectorSimilarityComputer similarity;
        std::vector<float> document_inverse_norms;
        document_inverse_norms.reserve(document_vectors.size());
        for(const auto& document : document_vectors) {
            document_inverse_norms.push_back(inverse_norm(document, similarity));
        }
        const auto document_signatures = encoder.encode_batch(document_vectors);
        if(document_signatures.size() != document_vectors.size()) {
            throw std::logic_error("encoder batch result count mismatch");
        }
        agent_memory::RetrievalRun original_run{"original_float", {}};
        agent_memory::RetrievalRun binary_run{"binary_candidates_exact_rerank", {}};
        double coverage_sum = 0.0;
        double dense_nearest_hamming_sum = 0.0;
        double dense_rank_100_hamming_sum = 0.0;
        double dense_neighbour_hamming_margin_sum = 0.0;
        std::size_t nonpositive_dense_neighbour_hamming_margin_count = 0;
        for(std::size_t query_index = 0; query_index < query_vectors.size(); ++query_index) {
            const auto& query = query_vectors[query_index];
            const auto query_inverse_norm = inverse_norm(query, similarity);
            const auto oracle = cosine_rank(
                query, query_inverse_norm, document_vectors, document_inverse_norms, similarity
            );
            original_run.queries.push_back(make_query_run(
                query_ids[query_index], oracle, document_ids, "original_float"
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
            }
            std::sort(candidates.begin(), candidates.end(), better_score);
            binary_run.queries.push_back(make_query_run(
                query_ids[query_index], candidates, document_ids, "binary_exact_rerank"
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
        output.original_float_metrics = agent_memory::evaluate_retrieval(
            dataset, original_run, {{1U, 5U, 10U, 100U}, {10U}}
        );
        output.binary_rerank_metrics = agent_memory::evaluate_retrieval(
            dataset, binary_run, {{1U, 5U, 10U, 100U}, {10U}}
        );
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
        const std::vector<ScoredPosition> oracle{{0U, 1.0F}, {1U, 0.5F}};
        const std::vector<ScoredPosition> candidates{
            {0U, 1.0F}, {2U, 0.9F}, {1U, 0.8F}
        };
        if(overlap_fraction(oracle, candidates, 2U, 1U) != 0.5 ||
           overlap_fraction(oracle, candidates, 2U, 3U) != 1.0) {
            std::cerr << "standard binary encoder evaluator candidate coverage self-test failed\n";
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
        } else {
            training_vectors.reserve(materialization.training_embeddings.size());
            for(const auto& record : materialization.training_embeddings) {
                training_vectors.push_back(record.embedding);
            }
        }
        const auto dimension = document_vectors.front().dimension();
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
            {"schema_version", 1},
            {"materialization_manifest_sha256", materialization.materialization_manifest_sha256},
            {"prepared_study_manifest_sha256", materialization.prepared_study_manifest_sha256},
            {"document_count", document_vectors.size()},
            {"query_count", query_vectors.size()},
            {"training_document_count", training_vectors.size()},
            {"training_id_list_path", training_id_list_path},
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
