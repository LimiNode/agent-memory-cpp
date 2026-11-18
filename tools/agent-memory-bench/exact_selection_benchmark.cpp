#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <queue>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;

#ifndef AGENT_MEMORY_EXACT_SELECTION_SOURCE_COMMIT
#define AGENT_MEMORY_EXACT_SELECTION_SOURCE_COMMIT "unknown"
#endif

struct Entry final {
    double score = 0.0;
    std::uint64_t id = 0;
};

struct Better final {
    bool operator()(const Entry& left, const Entry& right) const noexcept {
        if(left.score == right.score) {
            return left.id < right.id;
        }
        return left.score > right.score;
    }
};

using Selector = std::vector<Entry>(*)(
    const std::vector<Entry>&,
    std::size_t
);

[[nodiscard]] std::size_t effective_top_k(
    const std::vector<Entry>& input,
    const std::size_t top_k
) noexcept {
    return std::min(top_k, input.size());
}

[[nodiscard]] bool same_entry(const Entry& left, const Entry& right) noexcept {
    return left.score == right.score && left.id == right.id;
}

[[nodiscard]] bool same_results(
    const std::vector<Entry>& left,
    const std::vector<Entry>& right
) noexcept {
    if(left.size() != right.size()) {
        return false;
    }
    for(std::size_t index = 0; index < left.size(); ++index) {
        if(!same_entry(left[index], right[index])) {
            return false;
        }
    }
    return true;
}

[[nodiscard]] std::vector<Entry> make_fixture(const std::size_t count) {
    std::uint64_t state = 0x5eed1234ULL;
    std::vector<Entry> result;
    result.reserve(count);
    for(std::size_t index = 0; index < count; ++index) {
        state ^= state << 13U;
        state ^= state >> 7U;
        state ^= state << 17U;
        double score = static_cast<double>(state % 1000003U) / 1000003.0;
        if(index % 31U == 0U) {
            score = 0.25;
        }
        result.push_back(Entry{score, static_cast<std::uint64_t>(index)});
    }
    return result;
}

[[nodiscard]] std::vector<Entry> full_sort(
    const std::vector<Entry>& input,
    const std::size_t top_k
) {
    const auto limit = effective_top_k(input, top_k);
    if(limit == 0U) {
        return {};
    }
    auto result = input;
    std::sort(result.begin(), result.end(), Better{});
    result.resize(limit);
    return result;
}

[[nodiscard]] std::vector<Entry> partial_sort_top_k(
    const std::vector<Entry>& input,
    const std::size_t top_k
) {
    const auto limit = effective_top_k(input, top_k);
    if(limit == 0U) {
        return {};
    }
    auto result = input;
    if(limit < result.size()) {
        std::partial_sort(result.begin(), result.begin() + limit, result.end(), Better{});
    } else {
        std::sort(result.begin(), result.end(), Better{});
    }
    result.resize(limit);
    return result;
}

[[nodiscard]] std::vector<Entry> nth_element_top_k(
    const std::vector<Entry>& input,
    const std::size_t top_k
) {
    const auto limit = effective_top_k(input, top_k);
    if(limit == 0U) {
        return {};
    }
    auto result = input;
    if(limit < result.size()) {
        std::nth_element(result.begin(), result.begin() + limit, result.end(), Better{});
        std::sort(result.begin(), result.begin() + limit, Better{});
    } else {
        std::sort(result.begin(), result.end(), Better{});
    }
    result.resize(limit);
    return result;
}

[[nodiscard]] std::vector<Entry> heap_top_k(
    const std::vector<Entry>& input,
    const std::size_t top_k
) {
    const auto limit = effective_top_k(input, top_k);
    if(limit == 0U) {
        return {};
    }
    std::priority_queue<Entry, std::vector<Entry>, Better> heap;
    for(const auto& entry : input) {
        if(heap.size() < limit) {
            heap.push(entry);
        } else if(Better{}(entry, heap.top())) {
            heap.pop();
            heap.push(entry);
        }
    }
    std::vector<Entry> result;
    result.reserve(limit);
    while(!heap.empty()) {
        result.push_back(heap.top());
        heap.pop();
    }
    std::sort(result.begin(), result.end(), Better{});
    return result;
}

template <typename Function>
[[nodiscard]] Json measure(
    const std::vector<Entry>& input,
    const std::vector<Entry>& oracle,
    const std::size_t top_k,
    Function&& function
) {
    constexpr std::size_t warmups = 2;
    constexpr std::size_t repeats = 8;
    volatile std::uint64_t checksum = 0;
    for(std::size_t index = 0; index < warmups; ++index) {
        const auto result = function(input, top_k);
        if(!result.empty()) {
            checksum ^= result.front().id;
        }
    }

    std::vector<double> samples;
    samples.reserve(repeats);
    bool exact_parity = true;
    for(std::size_t index = 0; index < repeats; ++index) {
        const auto started = Clock::now();
        const auto result = function(input, top_k);
        const auto elapsed = std::chrono::duration<double, std::milli>(
            Clock::now() - started
        ).count();
        if(!result.empty()) {
            checksum ^= result.front().id;
        }
        exact_parity = exact_parity && same_results(result, oracle);
        samples.push_back(elapsed);
    }
    if(checksum == 0xdeadbeefU) {
        std::cerr << "unreachable checksum guard\n";
    }

    return Json{
        {"exact_parity", exact_parity},
        {"sample_count", repeats},
        {"samples_ms", samples},
        {"warmups", warmups}
    };
}

[[nodiscard]] bool self_test() {
    const std::array<Selector, 4> selectors{
        full_sort,
        partial_sort_top_k,
        nth_element_top_k,
        heap_top_k
    };
    const auto assert_case = [&selectors](
        const std::vector<Entry>& input,
        const std::size_t top_k
    ) {
        const auto oracle = full_sort(input, top_k);
        const auto expected_size = effective_top_k(input, top_k);
        if(oracle.size() != expected_size) {
            return false;
        }
        for(const auto selector : selectors) {
            const auto result = selector(input, top_k);
            if(result.size() != expected_size || !same_results(oracle, result)
               || !same_results(result, selector(input, top_k))) {
                return false;
            }
        }
        return true;
    };

    // Explicit boundary controls: empty, zero, exact-N, and over-N requests.
    if(!assert_case({}, 0U)
       || !assert_case({}, 7U)) {
        return false;
    }
    const std::vector<Entry> boundary{{3.0, 9U}, {2.0, 4U}, {1.0, 7U}};
    if(!assert_case(boundary, 0U)
       || !assert_case(boundary, boundary.size())
       || !assert_case(boundary, boundary.size() + 10U)) {
        return false;
    }

    // Equal scores, negative/nonpositive scores, and repeated identities are
    // valid rows. Multiplicity is preserved; equivalent rows are identical.
    const std::vector<Entry> equal_scores{
        {1.0, 8U}, {1.0, 2U}, {1.0, 5U}, {1.0, 2U}
    };
    const std::vector<Entry> negative_scores{
        {-1.0, 3U}, {-2.0, 1U}, {0.0, 2U}, {-1.0, 0U}
    };
    if(!assert_case(equal_scores, 4U)
       || !assert_case(equal_scores, 2U)
       || !assert_case(negative_scores, 3U)) {
        return false;
    }

    const auto input = make_fixture(37);
    const auto oracle = full_sort(input, 7);
    auto score_mutation = oracle;
    score_mutation.front().score += 1.0;
    if(same_results(oracle, score_mutation)) {
        return false;
    }
    auto identity_mutation = oracle;
    identity_mutation.back().id += 1U;
    if(same_results(oracle, identity_mutation)) {
        return false;
    }
    auto boundary_mutation = full_sort(boundary, 0U);
    boundary_mutation.push_back(boundary.front());
    if(same_results(full_sort(boundary, 0U), boundary_mutation)) {
        return false;
    }
    return true;
}

[[nodiscard]] Selector selector_for(const std::string_view name) {
    if(name == "full_sort") {
        return full_sort;
    }
    if(name == "partial_sort") {
        return partial_sort_top_k;
    }
    if(name == "nth_element_then_sort") {
        return nth_element_top_k;
    }
    return heap_top_k;
}

[[nodiscard]] std::vector<Entry> make_distribution_fixture(
    const std::size_t count,
    const std::string_view distribution
) {
    if(distribution == "equal_scores") {
        std::vector<Entry> result;
        result.reserve(count);
        for(std::size_t index = 0; index < count; ++index) {
            result.push_back(Entry{0.5, static_cast<std::uint64_t>(index % 17U)});
        }
        return result;
    }
    if(distribution == "mixed_sign_ties") {
        std::vector<Entry> result;
        result.reserve(count);
        for(std::size_t index = 0; index < count; ++index) {
            const auto bucket = static_cast<int>(index % 9U) - 4;
            result.push_back(Entry{
                static_cast<double>(bucket) / 4.0,
                static_cast<std::uint64_t>(index % 23U)
            });
        }
        return result;
    }
    return make_fixture(count);
}

[[nodiscard]] Json run_case(
    const std::size_t candidate_count,
    const std::size_t top_k,
    const std::string_view distribution,
    const std::vector<std::string_view>& execution_order
) {
    const auto input = make_distribution_fixture(candidate_count, distribution);
    const auto oracle = full_sort(input, top_k);
    Json variants = Json::object();
    for(const auto name : execution_order) {
        variants[std::string(name)] = measure(
            input,
            oracle,
            top_k,
            selector_for(name)
        );
    }
    return Json{
        {"candidate_count", candidate_count},
        {"top_k", top_k},
        {"effective_top_k", effective_top_k(input, top_k)},
        {"score_distribution", distribution},
        {"execution_order", execution_order},
        {"variants", variants}
    };
}

} // namespace

int main(int argc, char** argv) {
    if(argc > 1 && std::string_view(argv[1]) == "--self-test") {
        if(!self_test()) {
            std::cerr << "exact selection self-test: FAIL\n";
            return 1;
        }
        std::cout << "exact selection self-test: PASS\n";
        return 0;
    }

    const std::string output_path = argc > 1
        ? argv[1]
        : "exact-selection-benchmark.json";
    const Json receipt{
        {"schema_version", 2},
        {"benchmark_name", "exact_selection_ranking_kernel"},
        {"source_commit", AGENT_MEMORY_EXACT_SELECTION_SOURCE_COMMIT},
        {"corpus_seed", "0x5eed1234"},
        {"correctness_oracle", "full_sort_with_score_desc_id_asc_tie_break"},
        {"candidate_identity_contract", "repeated_ids_allowed; multiplicity_preserved; score_desc_id_asc_order"},
        {"boundary_contract", "empty_and_top_k_zero_return_empty; effective_top_k=min(top_k,candidate_count)"},
        {"scope", "ranking-selection kernel only; not ExactLexicalIndex end-to-end"},
        {"timing_semantics", "one sample is one complete candidate-set selection; warmups excluded"},
        {"production_api_changed", false},
        {"status", "EXECUTED"},
        {"cases", Json::array({
            run_case(0, 0, "uniform_nonnegative_ties", {"full_sort", "partial_sort", "nth_element_then_sort", "bounded_heap"}),
            run_case(3, 0, "equal_scores", {"partial_sort", "nth_element_then_sort", "bounded_heap", "full_sort"}),
            run_case(3, 3, "equal_scores", {"bounded_heap", "full_sort", "partial_sort", "nth_element_then_sort"}),
            run_case(3, 10, "mixed_sign_ties", {"full_sort", "partial_sort", "nth_element_then_sort", "bounded_heap"}),
            run_case(128, 16, "uniform_nonnegative_ties", {"nth_element_then_sort", "bounded_heap", "full_sort", "partial_sort"}),
            run_case(1024, 64, "mixed_sign_ties", {"bounded_heap", "full_sort", "partial_sort", "nth_element_then_sort"}),
            run_case(8192, 128, "uniform_nonnegative_ties", {"full_sort", "partial_sort", "nth_element_then_sort", "bounded_heap"}),
            run_case(65536, 256, "mixed_sign_ties", {"partial_sort", "nth_element_then_sort", "bounded_heap", "full_sort"})
        })}
    };
    try {
        std::ofstream output(output_path, std::ios::binary);
        if(!output) {
            return 2;
        }
        output << receipt.dump(2) << '\n';
        std::cout << receipt.dump(2) << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "exact selection benchmark: " << error.what() << '\n';
        return 3;
    }
}
