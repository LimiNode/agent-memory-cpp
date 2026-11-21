#include "Lexical.hpp"

#include <cmath>
#include <stdexcept>

namespace agent_memory {

    TokenId::TokenId(const std::uint64_t value)
        : m_value(value) {}

    std::uint64_t TokenId::value() const noexcept {
        return m_value;
    }

    bool TokenId::empty() const noexcept {
        return m_value == 0;
    }

    bool LexicalDocumentRecord::empty() const noexcept {
        return tokens.empty();
    }

    bool LexicalSearchQuery::empty() const noexcept {
        return terms.empty();
    }

    bool operator==(const TokenId lhs, const TokenId rhs) noexcept {
        return lhs.value() == rhs.value();
    }

    bool operator!=(const TokenId lhs, const TokenId rhs) noexcept {
        return !(lhs == rhs);
    }

    bool operator<(const TokenId lhs, const TokenId rhs) noexcept {
        return lhs.value() < rhs.value();
    }

    bool is_valid(const Bm25Options& options) noexcept {
        return std::isfinite(options.k1)
            && options.k1 > 0.0F
            && std::isfinite(options.b)
            && options.b >= 0.0F
            && options.b <= 1.0F;
    }

    float bm25_term_score(
        const std::uint64_t term_frequency,
        const std::uint64_t document_frequency,
        const std::uint64_t document_length,
        const std::uint64_t document_count,
        const std::uint64_t total_token_count,
        const Bm25Options& options
    ) {
        if(!is_valid(options)) {
            throw std::invalid_argument("invalid BM25 options");
        }
        if(document_count == 0) {
            if(term_frequency != 0 || document_frequency != 0 || document_length != 0 || total_token_count != 0) {
                throw std::invalid_argument("nonzero BM25 statistics for an empty collection");
            }
            return 0.0F;
        }
        if(document_frequency > document_count || term_frequency > document_length ||
            document_length > total_token_count || total_token_count < document_count ||
            (term_frequency != 0 && document_frequency == 0)) {
            throw std::invalid_argument("inconsistent BM25 statistics");
        }
        if(term_frequency == 0) {
            return 0.0F;
        }

        const auto count = static_cast<float>(document_count);
        const auto frequency = static_cast<float>(document_frequency);
        const auto idf = std::log(
            1.0F + ((count - frequency + 0.5F) / (frequency + 0.5F))
        );
        const auto tf = static_cast<float>(term_frequency);
        const auto average_length = static_cast<float>(total_token_count) / count;
        const auto length_ratio = static_cast<float>(document_length) / average_length;
        const auto numerator = tf * (options.k1 + 1.0F);
        const auto denominator = tf +
            options.k1 * (1.0F - options.b + options.b * length_ratio);
        if(!std::isfinite(numerator) || !std::isfinite(denominator)) {
            throw std::overflow_error("BM25 term arithmetic overflow");
        }
        const auto score = idf * (numerator / denominator);
        if(!std::isfinite(score)) {
            throw std::overflow_error("BM25 term score overflow");
        }
        return score;
    }

    bool is_valid(const LexicalPosting& posting) noexcept {
        return !posting.token_id.empty()
            && posting.term_frequency == posting.positions.size();
    }

    bool is_valid(const LexicalDocumentRecord& record) noexcept {
        if(record.revision.resource_id.empty() || record.chunk_id.empty() || record.tokens.empty()) {
            return false;
        }

        for(const auto& token : record.tokens) {
            if(token.empty()) {
                return false;
            }
        }

        return true;
    }

    bool is_valid(const LexicalSearchQuery& query) noexcept {
        if(query.terms.empty()) {
            return false;
        }

        if(query.bm25.has_value() && !is_valid(*query.bm25)) {
            return false;
        }

        for(const auto& term : query.terms) {
            if(term.empty()) {
                return false;
            }
        }

        return true;
    }

} // namespace agent_memory
