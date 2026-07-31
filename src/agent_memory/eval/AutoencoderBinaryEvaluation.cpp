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
            std::size_t oracle_k
        ) {
            std::vector<bool> actual_positions(oracle.size(), false);
            for(const auto entry : actual) {
                actual_positions[entry.position] = true;
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
        for(const auto& query : query_vectors) {
            const auto query_inverse_norm = inverse_norm(query, similarity);
            const auto oracle = cosine_rank(
                query,
                query_inverse_norm,
                document_vectors,
                document_inverse_norms,
                similarity
            );
            const auto query_signature = encoder.encode(query);
            std::vector<ScoredPosition> hamming_rank;
            hamming_rank.reserve(document_signatures.size());
            for(std::size_t position = 0; position < document_signatures.size(); ++position) {
                hamming_rank.push_back({
                    position,
                    -static_cast<float>(hamming_distance(query_signature, document_signatures[position])),
                });
            }
            std::sort(hamming_rank.begin(), hamming_rank.end(), better_score);
            hamming_rank.resize(candidate_limit);
            output.exact_top_k_candidate_coverage += overlap_fraction(
                oracle,
                hamming_rank,
                oracle_k
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
            output.reranked_recall_at_k_vs_exact += overlap_fraction(oracle, reranked, oracle_k);

            const auto decoded = cosine_rank(
                query,
                query_inverse_norm,
                decoded_documents,
                decoded_inverse_norms,
                similarity
            );
            output.decoder_recall_at_k_vs_exact += overlap_fraction(oracle, decoded, oracle_k);
        }
        const auto divisor = static_cast<double>(query_vectors.size());
        output.exact_top_k_candidate_coverage /= divisor;
        output.reranked_recall_at_k_vs_exact /= divisor;
        output.decoder_recall_at_k_vs_exact /= divisor;
        return output;
    }

} // namespace agent_memory
