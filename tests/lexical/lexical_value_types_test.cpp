#include <agent_memory.hpp>

#include <array>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string_view>

namespace {

    int fail(const std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    int test_default_token_id_is_empty() {
        const agent_memory::TokenId empty_id;
        if(!empty_id.empty()) {
            return fail("default token id must be empty");
        }
        return 0;
    }

    int test_token_id_value() {
        const agent_memory::TokenId alpha_id{1};
        if(alpha_id.empty() || alpha_id.value() != 1) {
            return fail("token id must expose its numeric value");
        }
        return 0;
    }

    int test_token_id_equality_and_ordering() {
        const agent_memory::TokenId alpha_id{1};
        const agent_memory::TokenId same_alpha_id{1};
        const agent_memory::TokenId beta_id{2};
        if(alpha_id != same_alpha_id || !(alpha_id < beta_id)) {
            return fail("token id must support equality and deterministic ordering");
        }
        return 0;
    }

    int test_default_bm25_options_valid() {
        const agent_memory::Bm25Options default_bm25;
        if(!agent_memory::is_valid(default_bm25)) {
            return fail("default BM25 options must be valid");
        }
        return 0;
    }

    int test_bm25_k1_must_be_positive() {
        if(agent_memory::is_valid(agent_memory::Bm25Options{0.0F, 0.75F})) {
            return fail("BM25 k1 must be positive");
        }
        return 0;
    }

    int test_bm25_b_in_unit_interval() {
        if(agent_memory::is_valid(agent_memory::Bm25Options{1.5F, 1.5F})) {
            return fail("BM25 b must stay inside [0, 1]");
        }
        return 0;
    }

    int test_bm25_term_score_independent_goldens() {
        struct Golden final {
            std::uint64_t tf;
            std::uint64_t df;
            std::uint64_t length;
            std::uint64_t count;
            std::uint64_t total;
            agent_memory::Bm25Options options;
            float expected;
        };
        // Independent real-valued reductions, not values produced by the helper:
        // log(8/3), (10/7)*log(10/3), (20/29)*log(2), (5/3)*log(12/5),
        // log(2), (5/3)*log(4/3), and (5/3)*log(8/3), respectively.
        const std::array<Golden, 7> goldens{{
            {1, 1, 4, 3, 12, {1.5F, 0.75F}, 0.980829253F},
            {2, 1, 3, 4, 12, {1.5F, 0.75F}, 1.719961149F},
            {1, 2, 4, 4, 8, {1.5F, 0.75F}, 0.478032538F},
            {3, 2, 20, 5, 40, {1.5F, 0.0F}, 1.459114562F},
            {2, 2, 8, 4, 16, {1.5F, 1.0F}, 0.693147181F},
            {3, 1, 8, 1, 8, {1.5F, 0.75F}, 0.479470121F},
            {3, 1, 3, 3, 9, {1.5F, 0.75F}, 1.634715422F}
        }};
        for(const auto& golden : goldens) {
            const auto score = agent_memory::bm25_term_score(
                golden.tf, golden.df, golden.length, golden.count, golden.total, golden.options
            );
            if(!std::isfinite(score) || std::abs(score - golden.expected) > 1e-6F) {
                return fail("BM25 term score must match independent numeric goldens");
            }
        }
        return 0;
    }

    int test_bm25_term_score_zero_and_integer_boundaries() {
        const agent_memory::Bm25Options options;
        if(agent_memory::bm25_term_score(0, 0, 0, 0, 0, options) != 0.0F ||
            agent_memory::bm25_term_score(0, 0, 3, 2, 6, options) != 0.0F ||
            agent_memory::bm25_term_score(0, 1, 3, 2, 6, options) != 0.0F) {
            return fail("consistent absent terms and an empty collection must score zero");
        }
        // At the uint64 boundary the tf factor rounds to k1+1; IDF is log(4/3).
        constexpr auto maximum_count = std::numeric_limits<std::uint64_t>::max();
        const auto maximum_score = agent_memory::bm25_term_score(
            maximum_count, 1, maximum_count, 1, maximum_count, options
        );
        if(!std::isfinite(maximum_score) || std::abs(maximum_score - 0.719205181F) > 1e-6F) {
            return fail("BM25 integer counts must not wrap at the uint64 boundary");
        }
        return 0;
    }

    int test_bm25_term_score_rejects_inconsistent_statistics() {
        // Columns are tf, df, document length, N, total token count.
        const std::array<std::array<std::uint64_t, 5>, 12> invalid{{
            {{1, 1, 1, 0, 0}},
            {{0, 1, 0, 0, 0}},
            {{0, 0, 1, 0, 0}},
            {{0, 0, 0, 0, 1}},
            {{0, 0, 0, 1, 0}},
            {{1, 3, 1, 2, 2}},
            {{3, 1, 2, 2, 4}},
            {{1, 1, 4, 2, 3}},
            {{0, 0, 1, 3, 2}},
            {{1, 0, 1, 1, 1}},
            {{0, 2, 1, 1, 1}},
            {{0, 0, 5, 2, 4}}
        }};
        for(const auto& counts : invalid) {
            bool rejected = false;
            try {
                (void)agent_memory::bm25_term_score(
                    counts[0], counts[1], counts[2], counts[3], counts[4], {}
                );
            } catch(const std::invalid_argument&) {
                rejected = true;
            }
            if(!rejected) {
                return fail("inconsistent BM25 counts must be rejected before zero-score shortcuts");
            }
        }
        return 0;
    }

    int test_bm25_term_score_rejects_invalid_options_even_for_empty_collection() {
        constexpr auto infinity = std::numeric_limits<float>::infinity();
        constexpr auto nan = std::numeric_limits<float>::quiet_NaN();
        const std::array<agent_memory::Bm25Options, 8> invalid{{
            {0.0F, 0.75F}, {-1.0F, 0.75F}, {infinity, 0.75F}, {nan, 0.75F},
            {1.5F, -0.1F}, {1.5F, 1.1F}, {1.5F, infinity}, {1.5F, nan}
        }};
        for(const auto& options : invalid) {
            bool rejected = false;
            try {
                (void)agent_memory::bm25_term_score(0, 0, 0, 0, 0, options);
            } catch(const std::invalid_argument&) {
                rejected = true;
            }
            if(!rejected) {
                return fail("invalid BM25 options must not bypass validation on an empty collection");
            }
        }
        return 0;
    }

    int test_bm25_term_score_rejects_float_overflow() {
        const agent_memory::Bm25Options options{std::numeric_limits<float>::max(), 0.75F};
        // Overflow in numerator only, denominator only, and both. A denominator-only
        // overflow would otherwise manufacture a finite zero score.
        const std::array<std::array<std::uint64_t, 2>, 3> cases{{
            {{2, 2}}, {{1, 4}}, {{2, 4}}
        }};
        for(const auto& counts : cases) {
            bool rejected = false;
            try {
                (void)agent_memory::bm25_term_score(counts[0], 1, counts[1], 2, 4, options);
            } catch(const std::overflow_error&) {
                rejected = true;
            }
            if(!rejected) {
                return fail("non-finite BM25 arithmetic must fail instead of producing a sortable score");
            }
        }
        return 0;
    }

    int test_exact_bm25_reuses_checked_term_scoring() {
        agent_memory::ExactLexicalIndex index;
        index.upsert({
            agent_memory::ChunkId{"chunk:bm25:0"},
            {agent_memory::ResourceId{"resource:bm25"}, 1, 1, 1},
            {
                {"alpha", {0, 5}, 0, agent_memory::TokenKind::Word},
                {"alpha", {6, 5}, 1, agent_memory::TokenKind::Word}
            },
            {}
        });
        agent_memory::LexicalSearchQuery query;
        query.terms = {"alpha", "alpha"};
        const auto hits = index.search(query);
        // Two query occurrences, each (10/7)*log(4/3).
        if(hits.size() != 1 || std::abs(hits[0].score - 0.821948778F) > 1e-6F) {
            return fail("exact BM25 must retain independent scores and repeated-query-term semantics");
        }
        query.bm25 = agent_memory::Bm25Options{std::numeric_limits<float>::max(), 0.75F};
        try {
            (void)index.search(query);
        } catch(const std::overflow_error&) {
            return 0;
        }
        return fail("exact BM25 must propagate checked term arithmetic overflow");
    }

    int test_record_with_tokens_not_empty() {
        const agent_memory::ResourceRevision revision{
            agent_memory::ResourceId{"resource:alpha"},
            3,
            11,
            17
        };
        agent_memory::LexicalDocumentRecord record{
            agent_memory::ChunkId{"chunk:alpha:0"},
            revision,
            {
                agent_memory::Token{
                    "agent",
                    agent_memory::TextRange{0, 5},
                    0,
                    agent_memory::TokenKind::Word
                },
                agent_memory::Token{
                    "memory",
                    agent_memory::TextRange{6, 6},
                    1,
                    agent_memory::TokenKind::Word
                }
            },
            {}
        };
        if(record.empty()) {
            return fail("record with tokens must not be empty");
        }
        return 0;
    }

    int test_lexical_stats_counts() {
        const agent_memory::ResourceRevision revision{
            agent_memory::ResourceId{"resource:alpha"},
            3,
            11,
            17
        };
        agent_memory::LexicalDocumentRecord record{
            agent_memory::ChunkId{"chunk:alpha:0"},
            revision,
            {
                agent_memory::Token{
                    "agent",
                    agent_memory::TextRange{0, 5},
                    0,
                    agent_memory::TokenKind::Word
                },
                agent_memory::Token{
                    "memory",
                    agent_memory::TextRange{6, 6},
                    1,
                    agent_memory::TokenKind::Word
                }
            },
            {}
        };
        record.metadata.set("language", "en");

        const agent_memory::LexicalDocumentStats stats{
            record.chunk_id,
            record.revision,
            record.tokens.size(),
            2,
            record.metadata
        };
        if(stats.token_count != 2 || stats.unique_token_count != 2) {
            return fail("lexical stats must store token and unique-token counts");
        }
        return 0;
    }

    int test_posting_alignment() {
        const agent_memory::ResourceRevision revision{
            agent_memory::ResourceId{"resource:alpha"},
            3,
            11,
            17
        };
        const agent_memory::LexicalPosting posting{
            agent_memory::TokenId{1},
            agent_memory::ChunkId{"chunk:alpha:0"},
            revision,
            2,
            {0, 4}
        };
        if(posting.term_frequency != posting.positions.size()) {
            return fail("posting positions should line up with term frequency in tests");
        }
        return 0;
    }

    int test_default_lexical_query() {
        agent_memory::LexicalSearchQuery query;
        if(!query.empty() || query.limit != 10) {
            return fail("default lexical query must be empty with default limit");
        }
        return 0;
    }

    int test_lexical_query_terms_and_filters() {
        agent_memory::LexicalSearchQuery query;
        query.terms = {"agent", "memory"};
        query.metadata_filters.push_back(agent_memory::MetadataFilter{"language", "en"});
        if(query.empty() || query.metadata_filters.size() != 1) {
            return fail("lexical query must store normalized terms and metadata filters");
        }
        return 0;
    }

    int test_lexical_search_result() {
        const agent_memory::LexicalSearchResult result{
            agent_memory::ChunkId{"chunk:alpha:0"},
            4.25F,
            agent_memory::Metadata{}
        };
        if(result.score <= 0.0F || result.chunk_id != agent_memory::ChunkId{"chunk:alpha:0"}) {
            return fail("lexical search result must expose chunk id and higher-is-better score");
        }
        return 0;
    }

    int test_matches_metadata_filters_accepts_matching() {
        agent_memory::Metadata record;
        record.set("language", "en");
        record.set("category", "docs");

        if(!agent_memory::matches_metadata_filters(record, {{"language", "en"}})) {
            return fail("matches_metadata_filters must accept matching metadata");
        }
        if(agent_memory::matches_metadata_filters(record, {{"category", "src"}})) {
            return fail("matches_metadata_filters must reject mismatched metadata");
        }
        return 0;
    }

} // namespace

int main() {
    int failures = 0;
    failures += test_default_token_id_is_empty();
    failures += test_token_id_value();
    failures += test_token_id_equality_and_ordering();
    failures += test_default_bm25_options_valid();
    failures += test_bm25_k1_must_be_positive();
    failures += test_bm25_b_in_unit_interval();
    failures += test_bm25_term_score_independent_goldens();
    failures += test_bm25_term_score_zero_and_integer_boundaries();
    failures += test_bm25_term_score_rejects_inconsistent_statistics();
    failures += test_bm25_term_score_rejects_invalid_options_even_for_empty_collection();
    failures += test_bm25_term_score_rejects_float_overflow();
    failures += test_exact_bm25_reuses_checked_term_scoring();
    failures += test_record_with_tokens_not_empty();
    failures += test_lexical_stats_counts();
    failures += test_posting_alignment();
    failures += test_default_lexical_query();
    failures += test_lexical_query_terms_and_filters();
    failures += test_lexical_search_result();
    failures += test_matches_metadata_filters_accepts_matching();
    return failures > 0 ? 1 : 0;
}
