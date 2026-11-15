#include "ExactVectorIndex.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <stdexcept>
#include <utility>

namespace agent_memory {

    namespace {

        struct ScoredRecord final {
            const VectorRecord* record = nullptr;
            float score = 0.0F;
        };

        [[nodiscard]] bool better_scored_record(
            const ScoredRecord& lhs,
            const ScoredRecord& rhs
        ) noexcept {
            if(lhs.score == rhs.score) {
                return lhs.record->chunk_id < rhs.record->chunk_id;
            }
            return lhs.score > rhs.score;
        }

        [[nodiscard]] double stable_inverse_norm(const Embedding& embedding) {
            double scale = 0.0;
            double scaled_sum = 1.0;
            for(const auto value : embedding.values) {
                const auto magnitude = std::abs(static_cast<double>(value));
                if(magnitude == 0.0) {
                    continue;
                }
                if(scale < magnitude) {
                    const auto ratio = scale == 0.0 ? 0.0 : scale / magnitude;
                    scaled_sum = 1.0 + scaled_sum * ratio * ratio;
                    scale = magnitude;
                } else {
                    const auto ratio = magnitude / scale;
                    scaled_sum += ratio * ratio;
                }
            }
            if(scale == 0.0) {
                return 0.0;
            }
            const auto norm = scale * std::sqrt(scaled_sum);
            if(!std::isfinite(norm) || norm == 0.0) {
                throw std::overflow_error("ExactVectorIndex vector norm is not finite");
            }
            return 1.0 / norm;
        }

        [[nodiscard]] double wide_dot_product(
            const Embedding& lhs,
            const Embedding& rhs
        ) noexcept {
            double result = 0.0;
            for(std::size_t index = 0; index < lhs.values.size(); ++index) {
                result += static_cast<double>(lhs.values[index])
                    * static_cast<double>(rhs.values[index]);
            }
            return result;
        }

        [[nodiscard]] double wide_negative_squared_distance(
            const Embedding& lhs,
            const Embedding& rhs
        ) noexcept {
            double distance = 0.0;
            for(std::size_t index = 0; index < lhs.values.size(); ++index) {
                const auto delta = static_cast<double>(lhs.values[index])
                    - static_cast<double>(rhs.values[index]);
                distance += delta * delta;
            }
            return -distance;
        }

        [[nodiscard]] float checked_score(const double score) {
            if(!std::isfinite(score)
               || std::abs(score) > static_cast<double>(std::numeric_limits<float>::max())) {
                throw std::overflow_error("ExactVectorIndex produced a non-finite score");
            }
            return static_cast<float>(score);
        }

    } // namespace

    ExactVectorIndex::ExactVectorIndex()
        : ExactVectorIndex(ExactVectorIndexOptions{}) {}

    ExactVectorIndex::ExactVectorIndex(ExactVectorIndexOptions options)
        : m_options(options),
          m_similarity(options.enable_simd) {}

    SimilarityMetric ExactVectorIndex::similarity_metric() const noexcept {
        return m_options.similarity_metric;
    }

    std::size_t ExactVectorIndex::dimension() const noexcept {
        return m_options.dimension;
    }

    std::size_t ExactVectorIndex::size() const noexcept {
        return m_records.size();
    }

    VectorSimilarityBackend ExactVectorIndex::similarity_backend() const noexcept {
        return m_similarity.backend();
    }

    void ExactVectorIndex::upsert(VectorRecord record) {
        validate_record_embedding(record.embedding);

        const ChunkId chunk_id = record.chunk_id;
        double inverse_norm = 0.0;
        if(m_options.similarity_metric == SimilarityMetric::Cosine) {
            inverse_norm = stable_inverse_norm(record.embedding);
        }
        m_records.insert_or_assign(
            chunk_id,
            StoredRecord{std::move(record), inverse_norm}
        );
        if(m_options.dimension == 0) {
            m_options.dimension = m_records.at(chunk_id).record.embedding.dimension();
        }
    }

    std::optional<VectorRecord> ExactVectorIndex::find(const ChunkId& chunk_id) const {
        const auto it = m_records.find(chunk_id);
        if(it == m_records.end()) {
            return std::nullopt;
        }
        return it->second.record;
    }

    std::vector<VectorSearchResult> ExactVectorIndex::search(
        const VectorSearchQuery& query
    ) const {
        std::vector<VectorSearchResult> results;
        if(query.limit == 0) {
            return results;
        }

        validate_query_embedding(query.embedding);

        double query_inverse_norm = 0.0;
        if(m_options.similarity_metric == SimilarityMetric::Cosine) {
            query_inverse_norm = stable_inverse_norm(query.embedding);
        }

        std::vector<ScoredRecord> scored_records;
        scored_records.reserve(m_records.size());
        for(const auto& item : m_records) {
            const auto& stored = item.second;
            const auto& record = stored.record;
            if(!matches_metadata_filters(record.metadata, query.metadata_filters)) {
                continue;
            }

            float score = 0.0F;
            switch(m_options.similarity_metric) {
                case SimilarityMetric::Cosine:
                    if(query_inverse_norm != 0.0F && stored.inverse_norm != 0.0F) {
                        // The SIMD/scalar float dot product is not a safe
                        // cosine numerator: finite products can underflow to
                        // zero before the double inverse norms are applied.
                        // Accumulate the cosine numerator in double for every
                        // cosine query so tiny, mixed-scale and cancellation-
                        // sensitive inputs retain their mathematical ordering.
                        const auto dot = wide_dot_product(
                            query.embedding,
                            record.embedding
                        );
                        score = checked_score(
                            dot * query_inverse_norm * stored.inverse_norm
                        );
                    }
                    break;
                case SimilarityMetric::DotProduct: {
                    auto dot = static_cast<double>(
                        m_similarity.dot_product(query.embedding, record.embedding)
                    );
                    if(!std::isfinite(dot)) {
                        dot = wide_dot_product(query.embedding, record.embedding);
                    }
                    score = checked_score(dot);
                    break;
                }
                case SimilarityMetric::Euclidean: {
                    auto distance = static_cast<double>(m_similarity.negative_squared_distance(
                        query.embedding,
                        record.embedding
                    ));
                    if(!std::isfinite(distance)) {
                        distance = wide_negative_squared_distance(
                            query.embedding,
                            record.embedding
                        );
                    }
                    score = checked_score(distance);
                    break;
                }
            }
            if(!std::isfinite(score)) {
                throw std::overflow_error("ExactVectorIndex produced a non-finite score");
            }
            scored_records.push_back(ScoredRecord{&record, score});
        }

        if(scored_records.size() > query.limit) {
            std::partial_sort(
                scored_records.begin(),
                scored_records.begin() + static_cast<std::ptrdiff_t>(query.limit),
                scored_records.end(),
                better_scored_record
            );
            scored_records.resize(query.limit);
        } else {
            std::sort(scored_records.begin(), scored_records.end(), better_scored_record);
        }

        results.reserve(scored_records.size());
        for(const auto& scored : scored_records) {
            results.push_back(VectorSearchResult{
                scored.record->chunk_id,
                scored.score,
                scored.record->metadata
            });
        }
        return results;
    }

    bool ExactVectorIndex::erase(const ChunkId& chunk_id) {
        return m_records.erase(chunk_id) > 0;
    }

    void ExactVectorIndex::clear() {
        m_records.clear();
    }

    void ExactVectorIndex::validate_record_embedding(const Embedding& embedding) const {
        if(embedding.empty()) {
            throw std::invalid_argument("ExactVectorIndex records must not use empty embeddings");
        }

        for(const auto value : embedding.values) {
            if(!std::isfinite(value)) {
                throw std::invalid_argument(
                    "ExactVectorIndex records must use finite embeddings"
                );
            }
        }

        if(m_options.dimension != 0 && embedding.dimension() != m_options.dimension) {
            throw std::invalid_argument("ExactVectorIndex record dimension mismatch");
        }
    }

    void ExactVectorIndex::validate_query_embedding(const Embedding& embedding) const {
        if(embedding.empty()) {
            throw std::invalid_argument("ExactVectorIndex queries must not use empty embeddings");
        }

        for(const auto value : embedding.values) {
            if(!std::isfinite(value)) {
                throw std::invalid_argument(
                    "ExactVectorIndex queries must use finite embeddings"
                );
            }
        }

        if(m_options.dimension != 0 && embedding.dimension() != m_options.dimension) {
            throw std::invalid_argument("ExactVectorIndex query dimension mismatch");
        }
    }

} // namespace agent_memory
