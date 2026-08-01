#include "AutoencoderBinaryEvaluation.hpp"

#include <agent_memory/index/VectorSimilarityComputer.hpp>

#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>
#include <string>
#include <utility>

namespace agent_memory {
    namespace {

        struct ScoredPosition final {
            std::size_t position = 0;
            float score = 0.0F;
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
                return lhs.position < rhs.position;
            }
            return lhs.score > rhs.score;
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
                });
            }
            std::sort(ranked.begin(), ranked.end(), better_score);
            return ranked;
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

    } // namespace

    AutoencoderBinaryEvaluationMetrics evaluate_autoencoder_binary_retrieval(
        const std::vector<Embedding>& document_vectors,
        const std::vector<Embedding>& query_vectors,
        const AutoencoderBinaryEncoder& encoder,
        const AutoencoderBinaryDecoder& decoder,
        AutoencoderBinaryEvaluationOptions options
    ) {
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
        const VectorSimilarityComputer similarity;
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
        RunningPearsonCorrelation cosine_negative_hamming;
        for(std::size_t query_index = 0; query_index < query_vectors.size(); ++query_index) {
            const auto& query = query_vectors[query_index];
            const auto query_inverse_norm = inverse_norm(query, similarity);
            const auto oracle = cosine_rank(
                query,
                query_inverse_norm,
                document_vectors,
                document_inverse_norms,
                similarity
            );
            const auto& query_signature = query_signatures[query_index];
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
                hamming_rank.push_back({
                    position,
                    -static_cast<float>(hamming),
                });
            }
            std::sort(hamming_rank.begin(), hamming_rank.end(), better_score);
            hamming_rank.resize(candidate_limit);
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
        const VectorSimilarityComputer similarity;
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

        RetrievalRun original_run{"original_float", {}};
        RetrievalRun rerank_run{"binary_candidates_exact_rerank", {}};
        RetrievalRun decoder_run{"decoder_approximation", {}};
        for(std::size_t query_index = 0; query_index < query_vectors.size(); ++query_index) {
            const auto& query = query_vectors[query_index];
            const auto query_inverse_norm = inverse_norm(query, similarity);
            const auto original = cosine_rank(
                query,
                query_inverse_norm,
                document_vectors,
                document_inverse_norms,
                similarity
            );
            const auto query_signature = encoder.encode(query);
            std::vector<ScoredPosition> candidates;
            candidates.reserve(document_signatures.size());
            for(std::size_t position = 0; position < document_signatures.size(); ++position) {
                candidates.push_back({
                    position,
                    -static_cast<float>(hamming_distance(query_signature, document_signatures[position])),
                });
            }
            std::sort(candidates.begin(), candidates.end(), better_score);
            candidates.resize(candidate_limit);
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
                decoded_inverse_norms,
                similarity
            );
            original_run.queries.push_back(
                make_query_run(query_ids[query_index], original, document_ids, "original_float")
            );
            rerank_run.queries.push_back(
                make_query_run(query_ids[query_index], candidates, document_ids, "binary_exact_rerank")
            );
            decoder_run.queries.push_back(
                make_query_run(query_ids[query_index], decoded, document_ids, "decoder_approximation")
            );
        }
        output.original_float_metrics = evaluate_retrieval(dataset, original_run, retrieval_options);
        output.binary_rerank_metrics = evaluate_retrieval(dataset, rerank_run, retrieval_options);
        output.decoder_approximation_metrics = evaluate_retrieval(dataset, decoder_run, retrieval_options);
        return output;
    }

} // namespace agent_memory
