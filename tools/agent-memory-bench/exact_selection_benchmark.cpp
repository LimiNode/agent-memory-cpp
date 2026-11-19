#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
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
#ifndef AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256
#define AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256 "unknown"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_COMPILER_ID
#define AGENT_MEMORY_EVALUATOR_COMPILER_ID "unknown"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_COMPILER_VERSION
#define AGENT_MEMORY_EVALUATOR_COMPILER_VERSION "unknown"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_GENERATOR
#define AGENT_MEMORY_EVALUATOR_GENERATOR "unknown"
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

[[nodiscard]] Selector selector_for(const std::string_view name);

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

[[nodiscard]] Json measure_interleaved(
    const std::vector<Entry>& input,
    const std::vector<Entry>& oracle,
    const std::size_t top_k,
    const std::vector<std::string_view>& execution_order
) {
    constexpr std::size_t warmups = 2;
    constexpr std::size_t repeats = 8;
    struct VariantState final {
        std::string_view name;
        Selector selector;
        std::vector<double> samples;
        bool exact_parity = true;
    };
    std::vector<VariantState> states;
    states.reserve(execution_order.size());
    for(const auto name : execution_order) {
        states.push_back(VariantState{name, selector_for(name), {}, true});
        states.back().samples.reserve(repeats);
    }
    volatile std::uint64_t checksum = 0;
    const auto run_in_order = [&] (
        const std::size_t rotation,
        const bool record_measurement,
        Json* recorded_order
    ) {
        if(recorded_order != nullptr) {
            *recorded_order = Json::array();
        }
        for(std::size_t offset = 0; offset < states.size(); ++offset) {
            auto& state = states[(rotation + offset) % states.size()];
            if(recorded_order != nullptr) {
                recorded_order->push_back(std::string(state.name));
            }
            const auto started = record_measurement ? Clock::now() : Clock::time_point{};
            const auto result = state.selector(input, top_k);
            const auto elapsed = record_measurement
                ? std::chrono::duration<double, std::milli>(Clock::now() - started).count()
                : 0.0;
            if(!result.empty()) {
                checksum ^= result.front().id;
            }
            if(record_measurement) {
                state.exact_parity = state.exact_parity && same_results(result, oracle);
                state.samples.push_back(elapsed);
            }
        }
    };
    for(std::size_t index = 0; index < warmups; ++index) {
        run_in_order(index % states.size(), false, nullptr);
    }
    Json execution_orders = Json::array();
    for(std::size_t index = 0; index < repeats; ++index) {
        Json order;
        run_in_order(index % states.size(), true, &order);
        execution_orders.push_back(std::move(order));
    }
    if(checksum == 0xdeadbeefU) {
        std::cerr << "unreachable checksum guard\n";
    }

    Json variants = Json::object();
    for(const auto& state : states) {
        variants[std::string(state.name)] = Json{
            {"exact_parity", state.exact_parity},
            {"sample_count", state.samples.size()},
            {"samples_ms", state.samples},
            {"warmups", warmups},
            {"interleaved", true}
        };
    }
    return Json{
        {"variants", variants},
        {"warmups", warmups},
        {"repeats", repeats},
        {"execution_orders", execution_orders},
        {"timing_scope", "fixture_interleaved_variant_selection"}
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
    const std::vector<std::string_view> order{
        "full_sort", "partial_sort", "nth_element_then_sort", "bounded_heap"
    };
    const auto measurements = measure_interleaved(input, oracle, 7U, order);
    const auto execution_orders = measurements.at("execution_orders");
    if(execution_orders.size() != 8U) {
        return false;
    }
    for(std::size_t repeat = 0; repeat < execution_orders.size(); ++repeat) {
        if(execution_orders[repeat].size() != order.size()) {
            return false;
        }
        for(std::size_t offset = 0; offset < order.size(); ++offset) {
            const auto expected = std::string(order[(repeat + offset) % order.size()]);
            if(execution_orders[repeat][offset] != expected) {
                return false;
            }
        }
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
    const auto measurements = measure_interleaved(input, oracle, top_k, execution_order);
    return Json{
        {"candidate_count", candidate_count},
        {"top_k", top_k},
        {"effective_top_k", effective_top_k(input, top_k)},
        {"score_distribution", distribution},
        {"execution_order", execution_order},
        {"execution_orders", measurements.at("execution_orders")},
        {"variants", measurements.at("variants")},
        {"warmups", measurements.at("warmups")},
        {"repeats", measurements.at("repeats")},
        {"timing_scope", measurements.at("timing_scope")}
    };
}

} // namespace

int main(int argc, char** argv) {
    bool self_test_requested = false;
    std::string output_path = "exact-selection-benchmark.json";
    std::string expected_pr_head = "not_supplied";
    for(int index = 1; index < argc; ++index) {
        const std::string_view argument(argv[index]);
        if(argument == "--self-test") {
            self_test_requested = true;
        } else if(argument == "--expected-pr-head" && index + 1 < argc) {
            expected_pr_head = argv[++index];
        } else if(argument.rfind("--expected-pr-head=", 0) == 0U) {
            expected_pr_head = std::string(argument.substr(19));
        } else {
            output_path = argv[index];
        }
    }
    if(self_test_requested) {
        if(!self_test()) {
            std::cerr << "exact selection self-test: FAIL\n";
            return 1;
        }
        std::cout << "exact selection self-test: PASS\n";
        return 0;
    }

    const Json receipt{
        {"schema_version", 2},
        {"benchmark_name", "exact_selection_ranking_kernel"},
        {"source_commit", AGENT_MEMORY_EXACT_SELECTION_SOURCE_COMMIT},
        {"expected_pr_head_sha", expected_pr_head},
        {"actual_checkout_sha", AGENT_MEMORY_EXACT_SELECTION_SOURCE_COMMIT},
        {"provenance_scope", "expected_pr_head_and_actual_checkout_are_recorded_separately"},
        {"build_identity", Json{
            {"compiler_id", AGENT_MEMORY_EVALUATOR_COMPILER_ID},
            {"compiler_version", AGENT_MEMORY_EVALUATOR_COMPILER_VERSION},
            {"generator", AGENT_MEMORY_EVALUATOR_GENERATOR},
            {"configured_environment_sha256", AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256}
        }},
        {"corpus_seed", "0x5eed1234"},
        {"correctness_oracle", "full_sort_with_score_desc_id_asc_tie_break"},
        {"candidate_identity_contract", "repeated_ids_allowed; multiplicity_preserved; score_desc_id_asc_order"},
        {"boundary_contract", "empty_and_top_k_zero_return_empty; effective_top_k=min(top_k,candidate_count)"},
        {"scope", "ranking-selection kernel only; not ExactLexicalIndex end-to-end"},
        {"timing_semantics", "one sample is one complete candidate-set selection; variants are cyclically interleaved on one fixture; warmups excluded"},
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
