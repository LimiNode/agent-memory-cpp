#include "AutoencoderBinaryEvaluation.hpp"

#include <agent_memory/index/VectorSimilarityComputer.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <numeric>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace agent_memory {
    namespace {

        struct ScoredPosition final {
            std::size_t position = 0;
            float score = 0.0F;
            std::string_view document_id;
        };

        class RunningStatistics final {
        public:
            void add(double value) noexcept {
                if(m_count == 0) {
                    m_minimum = value;
                    m_maximum = value;
                } else {
                    m_minimum = std::min(m_minimum, value);
                    m_maximum = std::max(m_maximum, value);
                }
                ++m_count;
                const auto delta = value - m_mean;
                m_mean += delta / static_cast<double>(m_count);
                m_sum_squared_deviation += delta * (value - m_mean);
            }

            [[nodiscard]] AutoencoderBinaryDescriptiveStatistics result() const noexcept {
                AutoencoderBinaryDescriptiveStatistics output;
                output.sample_count = m_count;
                output.mean = m_mean;
                output.population_stddev = m_count == 0 ? 0.0 : std::sqrt(
                    std::max(
                        0.0,
                        m_sum_squared_deviation / static_cast<double>(m_count)
                    )
                );
                output.minimum = m_count == 0 ? 0.0 : m_minimum;
                output.maximum = m_count == 0 ? 0.0 : m_maximum;
                return output;
            }

        private:
            std::size_t m_count = 0;
            double m_mean = 0.0;
            double m_sum_squared_deviation = 0.0;
            double m_minimum = 0.0;
            double m_maximum = 0.0;
        };

        class RunningPearsonCorrelation final {
        public:
            void add(double x, double y) noexcept {
                ++m_count;
                const auto x_delta = x - m_mean_x;
                m_mean_x += x_delta / static_cast<double>(m_count);
                const auto y_delta = y - m_mean_y;
                m_mean_y += y_delta / static_cast<double>(m_count);
                m_sum_squared_x += x_delta * (x - m_mean_x);
                m_sum_squared_y += y_delta * (y - m_mean_y);
                m_sum_cross_deviation += x_delta * (y - m_mean_y);
            }

            [[nodiscard]] bool defined() const noexcept {
                return m_count > 1 && m_sum_squared_x > 0.0 && m_sum_squared_y > 0.0;
            }

            [[nodiscard]] double value() const noexcept {
                if(!defined()) {
                    return 0.0;
                }
                return m_sum_cross_deviation /
                    std::sqrt(m_sum_squared_x * m_sum_squared_y);
            }

        private:
            std::size_t m_count = 0;
            double m_mean_x = 0.0;
            double m_mean_y = 0.0;
            double m_sum_squared_x = 0.0;
            double m_sum_squared_y = 0.0;
            double m_sum_cross_deviation = 0.0;
        };

        [[nodiscard]] bool better_score(
            const ScoredPosition& lhs,
            const ScoredPosition& rhs
        ) noexcept {
            if(lhs.score == rhs.score) {
                return lhs.document_id < rhs.document_id;
            }
            return lhs.score > rhs.score;
        }

        void retain_best_prefix(
            std::vector<ScoredPosition>& positions,
            std::size_t limit
        ) {
            limit = std::min(limit, positions.size());
            if(limit < positions.size()) {
                std::partial_sort(
                    positions.begin(),
                    positions.begin() + static_cast<std::ptrdiff_t>(limit),
                    positions.end(),
                    better_score
                );
                positions.resize(limit);
                return;
            }
            std::sort(positions.begin(), positions.end(), better_score);
        }

        [[nodiscard]] float inverse_norm(
            const Embedding& embedding,
            const VectorSimilarityComputer& similarity
        ) noexcept {
            const auto squared_norm = similarity.squared_norm(embedding);
            return squared_norm > 0.0F ? 1.0F / std::sqrt(squared_norm) : 0.0F;
        }

        [[nodiscard]] float cosine_similarity(
            const Embedding& lhs,
            float lhs_inverse_norm,
            const Embedding& rhs,
            float rhs_inverse_norm,
            const VectorSimilarityComputer& similarity
        ) noexcept {
            return similarity.dot_product_values(
                lhs.values.data(),
                rhs.values.data(),
                lhs.dimension()
            ) * lhs_inverse_norm * rhs_inverse_norm;
        }

        [[nodiscard]] double norm(
            const Embedding& embedding,
            const VectorSimilarityComputer& similarity
        ) noexcept {
            return std::sqrt(static_cast<double>(similarity.squared_norm(embedding)));
        }

        [[nodiscard]] std::vector<ScoredPosition> cosine_rank(
            const Embedding& query,
            float query_inverse_norm,
            const std::vector<Embedding>& documents,
            const std::vector<std::string>& document_ids,
            const std::vector<float>& document_inverse_norms,
            const VectorSimilarityComputer& similarity
        ) {
            std::vector<ScoredPosition> ranked;
            ranked.reserve(documents.size());
            for(std::size_t position = 0; position < documents.size(); ++position) {
                ranked.push_back({
                    position,
                    similarity.dot_product_values(
                        query.values.data(),
                        documents[position].values.data(),
                        query.dimension()
                    ) * query_inverse_norm * document_inverse_norms[position],
                    document_ids[position],
                });
            }
            std::sort(ranked.begin(), ranked.end(), better_score);
            return ranked;
        }

        [[nodiscard]] std::vector<ScoredPosition> binary_candidate_rank(
            const BinarySignature& query_signature,
            const std::vector<BinarySignature>& document_signatures,
            const std::vector<std::string>& document_ids,
            std::size_t candidate_limit,
            AutoencoderBinaryCandidateScoring scoring,
            const std::vector<float>* query_affine_projections,
            AutoencoderBinaryAsymmetricScoringBackend asymmetric_scoring_backend
        ) {
            if(scoring == AutoencoderBinaryCandidateScoring::AsymmetricAffineDot &&
               query_affine_projections == nullptr) {
                throw std::invalid_argument("asymmetric candidate scoring requires query projections");
            }
            std::vector<ScoredPosition> output;
            output.reserve(document_signatures.size());
            if(scoring == AutoencoderBinaryCandidateScoring::HammingDistance) {
                for(std::size_t position = 0; position < document_signatures.size(); ++position) {
                    output.push_back({
                        position,
                        -static_cast<float>(hamming_distance(
                            query_signature,
                            document_signatures[position]
                        )),
                        document_ids[position],
                    });
                }
            } else {
                const AsymmetricBinarySignatureScorer asymmetric_scorer(
                    *query_affine_projections,
                    asymmetric_scoring_backend
                );
                for(std::size_t position = 0; position < document_signatures.size(); ++position) {
                    output.push_back({
                        position,
                        asymmetric_scorer.score(document_signatures[position]),
                        document_ids[position],
                    });
                }
            }
            retain_best_prefix(output, candidate_limit);
            return output;
        }

        [[nodiscard]] std::vector<ScoredPosition> contiguous_hamming_candidate_rank(
            const BinarySignature& query_signature,
            const std::vector<std::uint64_t>& packed_document_words,
            const std::vector<std::string>& document_ids,
            const HammingDistanceComputer& distance_computer,
            std::vector<std::size_t>& distance_workspace,
            std::size_t candidate_limit
        ) {
            const auto document_count = document_ids.size();
            distance_computer.compute_distances(
                query_signature.words().data(),
                packed_document_words.data(),
                document_count,
                distance_workspace.data()
            );
            std::vector<ScoredPosition> output;
            output.reserve(document_count);
            for(std::size_t position = 0; position < document_count; ++position) {
                output.push_back({
                    position,
                    -static_cast<float>(distance_workspace[position]),
                    document_ids[position],
                });
            }
            retain_best_prefix(output, candidate_limit);
            return output;
        }

        [[nodiscard]] BinarySignature signature_from_affine_projections(
            const std::vector<float>& projections
        ) {
            BinarySignature signature(projections.size());
            for(std::size_t bit = 0; bit < projections.size(); ++bit) {
                signature.set_bit(bit, projections[bit] >= 0.0F);
            }
            return signature;
        }

        [[nodiscard]] std::vector<ScoredPosition> asymmetric_candidate_rank(
            const AsymmetricBinarySignatureScorer& scorer,
            const std::vector<ScoredPosition>& hamming_candidates,
            const std::vector<BinarySignature>& document_signatures,
            std::size_t candidate_limit
        ) {
            std::vector<ScoredPosition> output = hamming_candidates;
            for(auto& candidate : output) {
                candidate.score = scorer.score(document_signatures[candidate.position]);
            }
            retain_best_prefix(output, candidate_limit);
            return output;
        }

        [[nodiscard]] double candidate_coverage(
            const std::vector<ScoredPosition>& candidates,
            const std::vector<std::size_t>& relevant_positions
        ) {
            if(relevant_positions.empty()) {
                return 0.0;
            }
            std::unordered_set<std::size_t> candidate_positions;
            candidate_positions.reserve(candidates.size());
            for(const auto& candidate : candidates) {
                candidate_positions.insert(candidate.position);
            }
            std::size_t retained = 0;
            for(const auto position : relevant_positions) {
                retained += candidate_positions.count(position);
            }
            return static_cast<double>(retained) /
                static_cast<double>(relevant_positions.size());
        }

        void exact_rerank(
            std::vector<ScoredPosition>& candidates,
            const Embedding& query,
            float query_inverse_norm,
            const std::vector<Embedding>& document_vectors,
            const std::vector<float>& document_inverse_norms,
            const VectorSimilarityComputer& similarity
        ) {
            for(auto& candidate : candidates) {
                candidate.score = similarity.dot_product_values(
                    query.values.data(),
                    document_vectors[candidate.position].values.data(),
                    query.dimension()
                ) * query_inverse_norm * document_inverse_norms[candidate.position];
            }
            std::sort(candidates.begin(), candidates.end(), better_score);
        }

        [[nodiscard]] double elapsed_milliseconds(
            std::chrono::steady_clock::time_point start,
            std::chrono::steady_clock::time_point finish
        ) noexcept {
            return std::chrono::duration<double, std::milli>(finish - start).count();
        }

        [[nodiscard]] double overlap_fraction(
            const std::vector<ScoredPosition>& oracle,
            const std::vector<ScoredPosition>& actual,
            std::size_t oracle_k,
            std::size_t actual_limit
        ) {
            std::vector<bool> actual_positions(oracle.size(), false);
            const auto actual_k = std::min(actual_limit, actual.size());
            for(std::size_t index = 0; index < actual_k; ++index) {
                actual_positions[actual[index].position] = true;
            }
            std::size_t overlap = 0;
            for(std::size_t index = 0; index < oracle_k; ++index) {
                overlap += actual_positions[oracle[index].position] ? 1U : 0U;
            }
            return static_cast<double>(overlap) / static_cast<double>(oracle_k);
        }

        void validate_embeddings(
            const std::vector<Embedding>& embeddings,
            std::size_t dimension,
            const char* description
        ) {
            if(embeddings.empty()) {
                throw std::invalid_argument(std::string{description} + " must not be empty");
            }
            for(const auto& embedding : embeddings) {
                if(embedding.dimension() != dimension) {
                    throw std::invalid_argument(std::string{description} + " dimension mismatch");
                }
                for(const auto value : embedding.values) {
                    if(!std::isfinite(value)) {
                        throw std::invalid_argument(std::string{description} + " values must be finite");
                    }
                }
            }
        }

        void validate_ids(
            const std::vector<std::string>& ids,
            std::size_t expected_count,
            const char* description
        ) {
            if(ids.size() != expected_count) {
                throw std::invalid_argument(std::string{description} + " count mismatch");
            }
            auto sorted = ids;
            for(const auto& id : sorted) {
                if(id.empty()) {
                    throw std::invalid_argument(std::string{description} + " must not contain empty IDs");
                }
            }
            std::sort(sorted.begin(), sorted.end());
            if(std::adjacent_find(sorted.begin(), sorted.end()) != sorted.end()) {
                throw std::invalid_argument(std::string{description} + " must not contain duplicate IDs");
            }
        }

        [[nodiscard]] RetrievalEvalDataset make_eval_dataset(
            const std::vector<std::string>& document_ids,
            const std::vector<std::string>& query_ids,
            const std::vector<RelevanceJudgment>& judgments
        ) {
            RetrievalEvalDataset dataset;
            dataset.name = "materialized-autoencoder-evaluation";
            dataset.judgments = judgments;
            for(const auto& id : document_ids) {
                dataset.corpus.push_back({id, {}, {}, {}});
            }
            for(const auto& id : query_ids) {
                dataset.queries.push_back({id, "materialized embedding", {}, 10, {}, EvalQueryAnswerMode::JudgedRetrieval});
            }
            validate_retrieval_eval_dataset(dataset);
            return dataset;
        }

        [[nodiscard]] RetrievalQueryRun make_query_run(
            const std::string& query_id,
            const std::vector<ScoredPosition>& ranking,
            const std::vector<std::string>& document_ids,
            const char* retriever_name
        ) {
            RetrievalQueryRun output;
            output.query_id = query_id;
            output.hits.reserve(ranking.size());
            for(const auto& entry : ranking) {
                output.hits.push_back({
                    document_ids[entry.position], entry.score, 0, retriever_name
                });
            }
            return output;
        }

        [[nodiscard]] RetrievalEvalDataset make_single_query_eval_dataset(
            const RetrievalEvalDataset& dataset,
            std::size_t query_index
        ) {
            RetrievalEvalDataset output;
            output.queries.push_back(dataset.queries.at(query_index));
            const auto& query_id = output.queries.front().id;
            for(const auto& judgment : dataset.judgments) {
                if(judgment.query_id == query_id) {
                    output.judgments.push_back(judgment);
                }
            }
            return output;
        }

        void accumulate_query_metrics(
            RetrievalMetrics& total,
            const RetrievalMetrics& query_metrics
        ) {
            if(total.recall_at.empty() && total.ndcg_at.empty() && total.query_count == 0U) {
                total.recall_at = query_metrics.recall_at;
                total.ndcg_at = query_metrics.ndcg_at;
                for(auto& metric : total.recall_at) {
                    metric.value = 0.0;
                }
                for(auto& metric : total.ndcg_at) {
                    metric.value = 0.0;
                }
            }
            if(total.recall_at.size() != query_metrics.recall_at.size() ||
               total.ndcg_at.size() != query_metrics.ndcg_at.size()) {
                throw std::logic_error("inconsistent per-query retrieval metric cutoffs");
            }
            const auto judged_count = static_cast<double>(query_metrics.judged_query_count);
            const auto no_answer_count = static_cast<double>(query_metrics.no_answer_query_count);
            for(std::size_t index = 0; index < total.recall_at.size(); ++index) {
                total.recall_at[index].value += query_metrics.recall_at[index].value * judged_count;
            }
            for(std::size_t index = 0; index < total.ndcg_at.size(); ++index) {
                total.ndcg_at[index].value += query_metrics.ndcg_at[index].value * judged_count;
            }
            total.mrr += query_metrics.mrr * judged_count;
            total.no_answer_accuracy += query_metrics.no_answer_accuracy * no_answer_count;
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

        void finalize_accumulated_metrics(RetrievalMetrics& metrics) noexcept {
            if(metrics.judged_query_count != 0U) {
                const auto judged_count = static_cast<double>(metrics.judged_query_count);
                for(auto& metric : metrics.recall_at) {
                    metric.value /= judged_count;
                }
                for(auto& metric : metrics.ndcg_at) {
                    metric.value /= judged_count;
                }
                metrics.mrr /= judged_count;
            }
            if(metrics.no_answer_query_count != 0U) {
                metrics.no_answer_accuracy /= static_cast<double>(metrics.no_answer_query_count);
            }
            if(metrics.evaluated_query_count != 0U) {
                metrics.empty_result_fraction = static_cast<double>(metrics.empty_result_count) /
                    static_cast<double>(metrics.evaluated_query_count);
            }
        }

    } // namespace

    AutoencoderBinaryEvaluationMetrics evaluate_autoencoder_binary_retrieval(
        const std::vector<std::string>& document_ids,
        const std::vector<Embedding>& document_vectors,
        const std::vector<Embedding>& query_vectors,
        const AutoencoderBinaryEncoder& encoder,
        const AutoencoderBinaryDecoder& decoder,
        AutoencoderBinaryEvaluationOptions options
    ) {
        validate_ids(document_ids, document_vectors.size(), "autoencoder evaluation document IDs");
        if(options.oracle_k == 0 || options.returned_candidate_limit == 0) {
            throw std::invalid_argument("autoencoder evaluation limits must be positive");
        }
        const auto dimension = encoder.info().input_dimension;
        if(decoder.output_dimension() != dimension ||
           decoder.bit_count() != encoder.info().bit_count) {
            throw std::invalid_argument("autoencoder evaluation encoder and decoder dimensions mismatch");
        }
        validate_embeddings(document_vectors, dimension, "autoencoder evaluation documents");
        validate_embeddings(query_vectors, dimension, "autoencoder evaluation queries");
        const auto oracle_k = std::min(options.oracle_k, document_vectors.size());
        const auto candidate_limit = std::min(options.returned_candidate_limit, document_vectors.size());
        const VectorSimilarityComputer similarity(VectorSimilarityBackend::Scalar);
        std::vector<float> document_inverse_norms;
        document_inverse_norms.reserve(document_vectors.size());
        for(const auto& vector : document_vectors) {
            document_inverse_norms.push_back(inverse_norm(vector, similarity));
        }
        const auto document_signatures = encoder.encode_batch(document_vectors);
        const auto query_signatures = encoder.encode_batch(query_vectors);
        std::vector<Embedding> decoded_documents;
        decoded_documents.reserve(document_signatures.size());
        for(const auto& signature : document_signatures) {
            decoded_documents.push_back(decoder.decode(signature));
        }
        std::vector<float> decoded_inverse_norms;
        decoded_inverse_norms.reserve(decoded_documents.size());
        for(const auto& vector : decoded_documents) {
            decoded_inverse_norms.push_back(inverse_norm(vector, similarity));
        }

        AutoencoderBinaryEvaluationMetrics output;
        output.document_count = document_vectors.size();
        output.query_count = query_vectors.size();
        output.oracle_k = oracle_k;
        output.returned_candidate_limit = candidate_limit;
        output.random_candidate_coverage_expectation =
            static_cast<double>(candidate_limit) / static_cast<double>(document_vectors.size());
        output.code_diagnostics.document_code_health = analyze_binary_code_health(
            document_signatures
        );
        output.code_diagnostics.query_code_health = analyze_binary_code_health(query_signatures);
        output.code_diagnostics.unique_document_code_count =
            output.code_diagnostics.document_code_health.exact_signature_bucket_sizes.size();
        output.code_diagnostics.unique_document_code_fraction =
            static_cast<double>(output.code_diagnostics.unique_document_code_count) /
            static_cast<double>(document_signatures.size());
        output.code_diagnostics.unique_query_code_count =
            output.code_diagnostics.query_code_health.exact_signature_bucket_sizes.size();
        output.code_diagnostics.unique_query_code_fraction =
            static_cast<double>(output.code_diagnostics.unique_query_code_count) /
            static_cast<double>(query_signatures.size());

        RunningStatistics reconstruction_cosines;
        RunningStatistics decoded_norms;
        RunningStatistics shuffled_decoder_cosines;
        for(std::size_t document_index = 0;
            document_index < document_vectors.size();
            ++document_index) {
            reconstruction_cosines.add(cosine_similarity(
                document_vectors[document_index],
                document_inverse_norms[document_index],
                decoded_documents[document_index],
                decoded_inverse_norms[document_index],
                similarity
            ));
            decoded_norms.add(norm(decoded_documents[document_index], similarity));
            if(document_vectors.size() > 1) {
                const auto shuffled_index = (document_index + 1) % document_vectors.size();
                shuffled_decoder_cosines.add(cosine_similarity(
                    document_vectors[document_index],
                    document_inverse_norms[document_index],
                    decoded_documents[shuffled_index],
                    decoded_inverse_norms[shuffled_index],
                    similarity
                ));
            }
        }
        output.code_diagnostics.decoder_reconstruction_cosine = reconstruction_cosines.result();
        output.code_diagnostics.decoded_document_norm = decoded_norms.result();
        output.code_diagnostics.shuffled_decoder_cosine = shuffled_decoder_cosines.result();

        RunningStatistics query_document_hamming_distances;
        RunningStatistics dense_nearest_hamming_distances;
        RunningStatistics dense_rank_100_hamming_distances;
        RunningStatistics dense_neighbour_hamming_margins;
        std::size_t nonpositive_dense_neighbour_hamming_margins = 0;
        RunningPearsonCorrelation cosine_negative_hamming;
        for(std::size_t query_index = 0; query_index < query_vectors.size(); ++query_index) {
            const auto& query = query_vectors[query_index];
            const auto query_inverse_norm = inverse_norm(query, similarity);
            const auto oracle = cosine_rank(
                query,
                query_inverse_norm,
                document_vectors,
                document_ids,
                document_inverse_norms,
                similarity
            );
            const auto& query_signature = query_signatures[query_index];
            const auto query_affine_projections =
                options.candidate_scoring == AutoencoderBinaryCandidateScoring::AsymmetricAffineDot
                ? encoder.affine_projections(query)
                : std::vector<float>{};
            const auto dense_nearest_hamming = hamming_distance(
                query_signature, document_signatures[oracle.front().position]
            );
            const auto dense_rank_100_hamming = hamming_distance(
                query_signature,
                document_signatures[oracle[std::min<std::size_t>(99U, oracle.size() - 1U)].position]
            );
            const auto dense_neighbour_hamming_margin =
                static_cast<double>(dense_rank_100_hamming) -
                static_cast<double>(dense_nearest_hamming);
            dense_nearest_hamming_distances.add(static_cast<double>(dense_nearest_hamming));
            dense_rank_100_hamming_distances.add(static_cast<double>(dense_rank_100_hamming));
            dense_neighbour_hamming_margins.add(dense_neighbour_hamming_margin);
            nonpositive_dense_neighbour_hamming_margins +=
                dense_neighbour_hamming_margin <= 0.0 ? 1U : 0U;
            std::vector<ScoredPosition> hamming_rank;
            hamming_rank.reserve(document_signatures.size());
            for(std::size_t position = 0; position < document_signatures.size(); ++position) {
                const auto hamming = hamming_distance(
                    query_signature,
                    document_signatures[position]
                );
                query_document_hamming_distances.add(static_cast<double>(hamming));
                cosine_negative_hamming.add(
                    cosine_similarity(
                        query,
                        query_inverse_norm,
                        document_vectors[position],
                        document_inverse_norms[position],
                        similarity
                    ),
                    -static_cast<double>(hamming)
                );
            }
            hamming_rank = binary_candidate_rank(
                query_signature,
                document_signatures,
                document_ids,
                candidate_limit,
                options.candidate_scoring,
                query_affine_projections.empty() ? nullptr : &query_affine_projections,
                options.asymmetric_scoring_backend
            );
            output.exact_top_k_candidate_coverage += overlap_fraction(
                oracle,
                hamming_rank,
                oracle_k,
                hamming_rank.size()
            );

            std::vector<ScoredPosition> reranked;
            reranked.reserve(hamming_rank.size());
            for(const auto candidate : hamming_rank) {
                reranked.push_back({
                    candidate.position,
                    similarity.dot_product_values(
                        query.values.data(),
                        document_vectors[candidate.position].values.data(),
                        dimension
                    ) * query_inverse_norm * document_inverse_norms[candidate.position],
                    document_ids[candidate.position],
                });
            }
            std::sort(reranked.begin(), reranked.end(), better_score);
            output.reranked_recall_at_k_vs_exact += overlap_fraction(
                oracle,
                reranked,
                oracle_k,
                oracle_k
            );

            const auto decoded = cosine_rank(
                query,
                query_inverse_norm,
                decoded_documents,
                document_ids,
                decoded_inverse_norms,
                similarity
            );
            output.decoder_recall_at_k_vs_exact += overlap_fraction(
                oracle,
                decoded,
                oracle_k,
                oracle_k
            );
        }
        const auto divisor = static_cast<double>(query_vectors.size());
        output.exact_top_k_candidate_coverage /= divisor;
        output.reranked_recall_at_k_vs_exact /= divisor;
        output.decoder_recall_at_k_vs_exact /= divisor;
        output.candidate_coverage_lift_vs_random =
            output.exact_top_k_candidate_coverage /
            output.random_candidate_coverage_expectation;
        output.code_diagnostics.query_document_hamming_distance =
            query_document_hamming_distances.result();
        output.code_diagnostics.dense_nearest_hamming_distance =
            dense_nearest_hamming_distances.result();
        output.code_diagnostics.dense_rank_100_hamming_distance =
            dense_rank_100_hamming_distances.result();
        output.code_diagnostics.dense_neighbour_hamming_margin =
            dense_neighbour_hamming_margins.result();
        output.code_diagnostics.nonpositive_dense_neighbour_hamming_margin_fraction =
            static_cast<double>(nonpositive_dense_neighbour_hamming_margins) /
            static_cast<double>(query_vectors.size());
        output.code_diagnostics.cosine_negative_hamming_pearson_correlation =
            cosine_negative_hamming.value();
        output.code_diagnostics.cosine_negative_hamming_correlation_defined =
            cosine_negative_hamming.defined();
        return output;
    }

    AutoencoderBinaryRetrievalEvaluation
    evaluate_autoencoder_binary_retrieval_with_qrels(
        const std::vector<std::string>& document_ids,
        const std::vector<Embedding>& document_vectors,
        const std::vector<std::string>& query_ids,
        const std::vector<Embedding>& query_vectors,
        const std::vector<RelevanceJudgment>& judgments,
        const AutoencoderBinaryEncoder& encoder,
        const AutoencoderBinaryDecoder& decoder,
        AutoencoderBinaryEvaluationOptions binary_options,
        RetrievalEvaluationOptions retrieval_options
    ) {
        validate_ids(document_ids, document_vectors.size(), "autoencoder evaluation document IDs");
        validate_ids(query_ids, query_vectors.size(), "autoencoder evaluation query IDs");
        AutoencoderBinaryRetrievalEvaluation output;
        output.exact_agreement = evaluate_autoencoder_binary_retrieval(
            document_ids,
            document_vectors,
            query_vectors,
            encoder,
            decoder,
            binary_options
        );
        const auto dataset = make_eval_dataset(document_ids, query_ids, judgments);
        const auto dimension = encoder.info().input_dimension;
        const auto candidate_limit = std::min(
            binary_options.returned_candidate_limit,
            document_vectors.size()
        );
        const VectorSimilarityComputer similarity(VectorSimilarityBackend::Scalar);
        std::vector<float> document_inverse_norms;
        document_inverse_norms.reserve(document_vectors.size());
        for(const auto& vector : document_vectors) {
            document_inverse_norms.push_back(inverse_norm(vector, similarity));
        }
        const auto document_signatures = encoder.encode_batch(document_vectors);
        std::vector<Embedding> decoded_documents;
        decoded_documents.reserve(document_signatures.size());
        for(const auto& signature : document_signatures) {
            decoded_documents.push_back(decoder.decode(signature));
        }
        std::vector<float> decoded_inverse_norms;
        decoded_inverse_norms.reserve(decoded_documents.size());
        for(const auto& vector : decoded_documents) {
            decoded_inverse_norms.push_back(inverse_norm(vector, similarity));
        }

        // A full ranking can contain every document. Evaluate and discard each
        // query run immediately instead of retaining three corpus-sized runs.
        RetrievalMetrics original_metrics;
        RetrievalMetrics rerank_metrics;
        RetrievalMetrics decoder_metrics;
        for(std::size_t query_index = 0; query_index < query_vectors.size(); ++query_index) {
            const auto& query = query_vectors[query_index];
            const auto query_inverse_norm = inverse_norm(query, similarity);
            const auto original = cosine_rank(
                query,
                query_inverse_norm,
                document_vectors,
                document_ids,
                document_inverse_norms,
                similarity
            );
            const auto query_signature = encoder.encode(query);
            const auto query_affine_projections =
                binary_options.candidate_scoring ==
                    AutoencoderBinaryCandidateScoring::AsymmetricAffineDot
                ? encoder.affine_projections(query)
                : std::vector<float>{};
            auto candidates = binary_candidate_rank(
                query_signature,
                document_signatures,
                document_ids,
                candidate_limit,
                binary_options.candidate_scoring,
                query_affine_projections.empty() ? nullptr : &query_affine_projections,
                binary_options.asymmetric_scoring_backend
            );
            for(auto& candidate : candidates) {
                candidate.score = similarity.dot_product_values(
                    query.values.data(),
                    document_vectors[candidate.position].values.data(),
                    dimension
                ) * query_inverse_norm * document_inverse_norms[candidate.position];
            }
            std::sort(candidates.begin(), candidates.end(), better_score);
            const auto decoded = cosine_rank(
                query,
                query_inverse_norm,
                decoded_documents,
                document_ids,
                decoded_inverse_norms,
                similarity
            );
            const auto query_dataset = make_single_query_eval_dataset(dataset, query_index);
            accumulate_query_metrics(
                original_metrics,
                evaluate_retrieval(
                    query_dataset,
                    {"original_float", {
                        make_query_run(query_ids[query_index], original, document_ids, "original_float")
                    }},
                    retrieval_options
                )
            );
            accumulate_query_metrics(
                rerank_metrics,
                evaluate_retrieval(
                    query_dataset,
                    {"binary_candidates_exact_rerank", {
                        make_query_run(
                    query_ids[query_index],
                    candidates,
                    document_ids,
                    binary_options.candidate_scoring ==
                        AutoencoderBinaryCandidateScoring::AsymmetricAffineDot
                    ? "binary_asymmetric_affine_exact_rerank"
                    : "binary_exact_rerank"
                        )
                    }},
                    retrieval_options
                )
            );
            accumulate_query_metrics(
                decoder_metrics,
                evaluate_retrieval(
                    query_dataset,
                    {"decoder_approximation", {
                        make_query_run(
                            query_ids[query_index], decoded, document_ids, "decoder_approximation"
                        )
                    }},
                    retrieval_options
                )
            );
        }
        finalize_accumulated_metrics(original_metrics);
        finalize_accumulated_metrics(rerank_metrics);
        finalize_accumulated_metrics(decoder_metrics);
        output.original_float_metrics = std::move(original_metrics);
        output.binary_rerank_metrics = std::move(rerank_metrics);
        output.decoder_approximation_metrics = std::move(decoder_metrics);
        return output;
    }

    AutoencoderBinaryCascadeEvaluation
    evaluate_autoencoder_binary_cascade_with_qrels(
        const std::vector<std::string>& document_ids,
        const std::vector<Embedding>& document_vectors,
        const std::vector<std::string>& query_ids,
        const std::vector<Embedding>& query_vectors,
        const std::vector<RelevanceJudgment>& judgments,
        const AutoencoderBinaryEncoder& encoder,
        AutoencoderBinaryCascadeOptions cascade_options,
        RetrievalEvaluationOptions retrieval_options
    ) {
        validate_ids(document_ids, document_vectors.size(), "autoencoder cascade document IDs");
        validate_ids(query_ids, query_vectors.size(), "autoencoder cascade query IDs");
        if(cascade_options.oracle_k == 0 ||
           cascade_options.hamming_candidate_limit == 0 ||
           cascade_options.asymmetric_candidate_limit == 0 ||
           cascade_options.timing_repeat_count == 0 ||
           cascade_options.asymmetric_candidate_limit >
               cascade_options.hamming_candidate_limit) {
            throw std::invalid_argument("invalid autoencoder binary cascade options");
        }
        const auto dimension = encoder.info().input_dimension;
        validate_embeddings(document_vectors, dimension, "autoencoder cascade document vectors");
        validate_embeddings(query_vectors, dimension, "autoencoder cascade query vectors");
        const auto dataset = make_eval_dataset(document_ids, query_ids, judgments);
        const auto hamming_candidate_limit = std::min(
            cascade_options.hamming_candidate_limit,
            document_vectors.size()
        );
        const auto asymmetric_candidate_limit = std::min(
            cascade_options.asymmetric_candidate_limit,
            hamming_candidate_limit
        );
        const VectorSimilarityComputer similarity(VectorSimilarityBackend::Scalar);
        std::vector<float> document_inverse_norms;
        document_inverse_norms.reserve(document_vectors.size());
        for(const auto& document : document_vectors) {
            document_inverse_norms.push_back(inverse_norm(document, similarity));
        }

        const auto build_start = std::chrono::steady_clock::now();
        const auto document_signatures = encoder.encode_batch(document_vectors);
        const auto word_count = binary_signature_word_count(encoder.info().bit_count);
        std::vector<std::uint64_t> packed_document_words;
        packed_document_words.reserve(document_signatures.size() * word_count);
        for(const auto& signature : document_signatures) {
            packed_document_words.insert(
                packed_document_words.end(),
                signature.words().begin(),
                signature.words().end()
            );
        }
        const auto build_finish = std::chrono::steady_clock::now();
        const HammingDistanceComputer hamming_distance_computer(word_count);
        std::vector<std::size_t> hamming_distance_workspace(document_vectors.size());

        std::unordered_map<std::string_view, std::size_t> document_positions;
        document_positions.reserve(document_ids.size());
        for(std::size_t position = 0; position < document_ids.size(); ++position) {
            document_positions.emplace(document_ids[position], position);
        }
        std::unordered_map<std::string_view, std::size_t> query_positions;
        query_positions.reserve(query_ids.size());
        for(std::size_t position = 0; position < query_ids.size(); ++position) {
            query_positions.emplace(query_ids[position], position);
        }
        std::vector<std::unordered_set<std::size_t>> unique_relevant_positions(query_ids.size());
        for(const auto& judgment : judgments) {
            if(!judgment.relevant()) {
                continue;
            }
            const auto query = query_positions.find(judgment.query_id);
            const auto document = document_positions.find(judgment.item_id);
            if(query != query_positions.end() && document != document_positions.end()) {
                unique_relevant_positions[query->second].insert(document->second);
            }
        }
        std::vector<std::vector<std::size_t>> relevant_positions(query_ids.size());
        for(std::size_t query = 0; query < query_ids.size(); ++query) {
            relevant_positions[query].assign(
                unique_relevant_positions[query].begin(),
                unique_relevant_positions[query].end()
            );
        }

        AutoencoderBinaryCascadeEvaluation output;
        output.document_count = document_vectors.size();
        output.query_count = query_vectors.size();
        output.oracle_k = std::min(cascade_options.oracle_k, document_vectors.size());
        output.hamming_candidate_limit = hamming_candidate_limit;
        output.asymmetric_candidate_limit = asymmetric_candidate_limit;
        output.binary_payload_bytes = document_signatures.size() *
            binary_signature_word_count(encoder.info().bit_count) * sizeof(std::uint64_t);
        output.float_payload_bytes = document_vectors.size() * dimension * sizeof(float);
        output.hamming_backend = hamming_distance_computer.backend();
        output.timing.document_signature_build_ms = elapsed_milliseconds(build_start, build_finish);

        RetrievalMetrics original_metrics;
        RetrievalMetrics cascade_metrics;
        RunningStatistics query_projection_timings;
        RunningStatistics hamming_search_timings;
        RunningStatistics lut_build_timings;
        RunningStatistics asymmetric_search_timings;
        RunningStatistics exact_rerank_timings;
        RunningStatistics total_pipeline_timings;

        for(std::size_t query_index = 0; query_index < query_vectors.size(); ++query_index) {
            const auto& query = query_vectors[query_index];
            const auto query_inverse_norm = inverse_norm(query, similarity);
            const auto oracle = cosine_rank(
                query,
                query_inverse_norm,
                document_vectors,
                document_ids,
                document_inverse_norms,
                similarity
            );

            struct CascadeQueryResult final {
                std::vector<ScoredPosition> hamming_candidates;
                std::vector<ScoredPosition> asymmetric_candidates;
                std::vector<ScoredPosition> reranked_candidates;
            };
            const auto execute_pipeline = [&](bool collect_timing) {
                const auto total_start = std::chrono::steady_clock::now();
                const auto projection_start = total_start;
                const auto projections = encoder.affine_projections(query);
                const auto query_signature = signature_from_affine_projections(projections);
                const auto projection_finish = std::chrono::steady_clock::now();

                const auto hamming_start = projection_finish;
                auto hamming_candidates = contiguous_hamming_candidate_rank(
                    query_signature,
                    packed_document_words,
                    document_ids,
                    hamming_distance_computer,
                    hamming_distance_workspace,
                    hamming_candidate_limit
                );
                const auto hamming_finish = std::chrono::steady_clock::now();

                const auto lut_start = hamming_finish;
                const AsymmetricBinarySignatureScorer asymmetric_scorer(
                    projections,
                    AsymmetricBinarySignatureScoringBackend::ByteLookupTable
                );
                const auto lut_finish = std::chrono::steady_clock::now();

                const auto asymmetric_start = lut_finish;
                auto asymmetric_candidates = asymmetric_candidate_rank(
                    asymmetric_scorer,
                    hamming_candidates,
                    document_signatures,
                    asymmetric_candidate_limit
                );
                const auto asymmetric_finish = std::chrono::steady_clock::now();

                const auto rerank_start = asymmetric_finish;
                auto reranked_candidates = asymmetric_candidates;
                exact_rerank(
                    reranked_candidates,
                    query,
                    query_inverse_norm,
                    document_vectors,
                    document_inverse_norms,
                    similarity
                );
                const auto rerank_finish = std::chrono::steady_clock::now();

                if(collect_timing) {
                    query_projection_timings.add(elapsed_milliseconds(
                        projection_start,
                        projection_finish
                    ));
                    hamming_search_timings.add(elapsed_milliseconds(hamming_start, hamming_finish));
                    lut_build_timings.add(elapsed_milliseconds(lut_start, lut_finish));
                    asymmetric_search_timings.add(elapsed_milliseconds(
                        asymmetric_start,
                        asymmetric_finish
                    ));
                    exact_rerank_timings.add(elapsed_milliseconds(rerank_start, rerank_finish));
                    total_pipeline_timings.add(elapsed_milliseconds(total_start, rerank_finish));
                }
                return CascadeQueryResult{
                    std::move(hamming_candidates),
                    std::move(asymmetric_candidates),
                    std::move(reranked_candidates),
                };
            };

            for(std::size_t repeat = 0; repeat < cascade_options.warmup_repeat_count; ++repeat) {
                (void)execute_pipeline(false);
            }
            CascadeQueryResult result;
            for(std::size_t repeat = 0; repeat < cascade_options.timing_repeat_count; ++repeat) {
                result = execute_pipeline(true);
            }

            output.hamming_exact_top_k_candidate_coverage += overlap_fraction(
                oracle,
                result.hamming_candidates,
                output.oracle_k,
                result.hamming_candidates.size()
            );
            output.asymmetric_exact_top_k_candidate_coverage += overlap_fraction(
                oracle,
                result.asymmetric_candidates,
                output.oracle_k,
                result.asymmetric_candidates.size()
            );
            if(!relevant_positions[query_index].empty()) {
                ++output.qrels_positive_query_count;
                output.hamming_qrels_positive_candidate_coverage += candidate_coverage(
                    result.hamming_candidates,
                    relevant_positions[query_index]
                );
                output.asymmetric_qrels_positive_candidate_coverage += candidate_coverage(
                    result.asymmetric_candidates,
                    relevant_positions[query_index]
                );
            }
            output.reranked_recall_at_k_vs_exact += overlap_fraction(
                oracle,
                result.reranked_candidates,
                output.oracle_k,
                output.oracle_k
            );

            const auto query_dataset = make_single_query_eval_dataset(dataset, query_index);
            accumulate_query_metrics(
                original_metrics,
                evaluate_retrieval(
                    query_dataset,
                    {"original_float", {
                        make_query_run(query_ids[query_index], oracle, document_ids, "original_float")
                    }},
                    retrieval_options
                )
            );
            accumulate_query_metrics(
                cascade_metrics,
                evaluate_retrieval(
                    query_dataset,
                    {"hamming_asymmetric_exact_rerank", {
                        make_query_run(
                            query_ids[query_index],
                            result.reranked_candidates,
                            document_ids,
                            "hamming_asymmetric_exact_rerank"
                        )
                    }},
                    retrieval_options
                )
            );
        }

        const auto divisor = static_cast<double>(query_vectors.size());
        output.hamming_exact_top_k_candidate_coverage /= divisor;
        output.asymmetric_exact_top_k_candidate_coverage /= divisor;
        if(output.qrels_positive_query_count != 0) {
            const auto qrels_divisor = static_cast<double>(output.qrels_positive_query_count);
            output.hamming_qrels_positive_candidate_coverage /= qrels_divisor;
            output.asymmetric_qrels_positive_candidate_coverage /= qrels_divisor;
        }
        output.reranked_recall_at_k_vs_exact /= divisor;
        finalize_accumulated_metrics(original_metrics);
        finalize_accumulated_metrics(cascade_metrics);
        output.original_float_metrics = std::move(original_metrics);
        output.cascade_rerank_metrics = std::move(cascade_metrics);
        output.timing.query_projection_ms = query_projection_timings.result();
        output.timing.hamming_candidate_search_ms = hamming_search_timings.result();
        output.timing.asymmetric_lut_build_ms = lut_build_timings.result();
        output.timing.asymmetric_candidate_search_ms = asymmetric_search_timings.result();
        output.timing.exact_rerank_ms = exact_rerank_timings.result();
        output.timing.total_query_pipeline_ms = total_pipeline_timings.result();
        return output;
    }

} // namespace agent_memory
