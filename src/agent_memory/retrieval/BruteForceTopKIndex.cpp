#include "BruteForceTopKIndex.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace agent_memory {

    namespace {

        bool all_finite(const std::vector<float>& values) {
            for(const float v : values) {
                if(!std::isfinite(v)) {
                    return false;
                }
            }
            return true;
        }

    } // namespace

    BruteForceTopKIndex::BruteForceTopKIndex() = default;

    std::size_t BruteForceTopKIndex::size() const noexcept {
        return m_records.size();
    }

    std::size_t BruteForceTopKIndex::dimension() const noexcept {
        return m_dimension;
    }

    void BruteForceTopKIndex::add(std::string doc_id, std::vector<float> vector) {
        if(doc_id.empty()) {
            throw std::invalid_argument(
                "BruteForceTopKIndex::add: doc_id must not be empty"
            );
        }
        if(vector.empty()) {
            throw std::invalid_argument(
                "BruteForceTopKIndex::add: vector must not be empty"
            );
        }
        if(!all_finite(vector)) {
            throw std::invalid_argument(
                "BruteForceTopKIndex::add: vector contains NaN or Inf"
            );
        }
        const std::size_t candidate_dimension = vector.size();
        if(m_dimension != 0 && candidate_dimension != m_dimension) {
            throw std::invalid_argument(
                "BruteForceTopKIndex::add: vector dimension mismatch "
                "(expected " + std::to_string(m_dimension) +
                ", got " + std::to_string(candidate_dimension) + ")"
            );
        }

        // Replace-on-update by doc_id so callers can re-ingest a document
        // without first erasing it (matches most embedding-index backends).
        for(auto& record : m_records) {
            if(record.first == doc_id) {
                record.second = std::move(vector);
                return;
            }
        }
        // Publish the dimension only after the potentially-throwing insertion
        // succeeds. A failed first insertion must leave an empty index with
        // dimension() == 0, as promised by the public contract.
        m_records.emplace_back(std::move(doc_id), std::move(vector));
        if(m_dimension == 0) {
            m_dimension = candidate_dimension;
        }
    }

    void BruteForceTopKIndex::build() {
        // No-op for brute force. Reserved for future ANN backends.
    }

    std::vector<std::pair<std::string, double>> BruteForceTopKIndex::top_k(
        const std::vector<float>& query,
        std::size_t k
    ) const {
        std::vector<std::pair<std::string, double>> result;
        if(k == 0 || query.empty()) {
            return result;
        }
        if(!all_finite(query)) {
            throw std::invalid_argument(
                "BruteForceTopKIndex: query contains NaN or Inf"
            );
        }
        if(m_records.empty()) {
            return result;
        }
        if(m_dimension != 0 && query.size() != m_dimension) {
            throw std::invalid_argument(
                "BruteForceTopKIndex: query dimension mismatch"
            );
        }

        // Compute the query L2 norm once. A zero-norm query (e.g. a fully
        // out-of-vocabulary BoW vector) cannot meaningfully match any
        // document, so we return an empty result rather than emitting a
        // deterministic-but-spurious tie-broken ranking.
        double q_norm_sq = 0.0;
        for(const float v : query) {
            q_norm_sq += static_cast<double>(v) * static_cast<double>(v);
        }
        if(q_norm_sq == 0.0) {
            return result;
        }

        struct ScoredRecord {
            std::size_t index = 0;
            double score = 0.0;
        };

        // Score each record once. The comparator below only compares scalar
        // scores and document ids; it never rescans an embedding.
        std::vector<ScoredRecord> scored;
        scored.reserve(m_records.size());
        for(std::size_t index = 0; index < m_records.size(); ++index) {
            const auto& vec = m_records[index].second;
            double dot = 0.0;
            double v_norm_sq = 0.0;
            for(std::size_t j = 0; j < query.size(); ++j) {
                dot += static_cast<double>(query[j]) * static_cast<double>(vec[j]);
                v_norm_sq += static_cast<double>(vec[j]) * static_cast<double>(vec[j]);
            }
            const double score = v_norm_sq > 0.0
                ? (dot / std::sqrt(v_norm_sq * q_norm_sq))
                : 0.0;
            if(!std::isfinite(score)) {
                throw std::runtime_error(
                    "BruteForceTopKIndex: cosine score is non-finite"
                );
            }
            scored.push_back(ScoredRecord{index, score});
        }

        const std::size_t limit = std::min(k, m_records.size());
        std::partial_sort(
            scored.begin(),
            scored.begin() + static_cast<std::ptrdiff_t>(limit),
            scored.end(),
            [&](const ScoredRecord& lhs, const ScoredRecord& rhs) {
                if(lhs.score == rhs.score) {
                    return m_records[lhs.index].first < m_records[rhs.index].first;
                }
                return lhs.score > rhs.score;
            }
        );

        result.reserve(limit);
        for(std::size_t i = 0; i < limit; ++i) {
            const auto& scored_record = scored[i];
            result.emplace_back(
                m_records[scored_record.index].first,
                scored_record.score
            );
        }
        return result;
    }

} // namespace agent_memory
