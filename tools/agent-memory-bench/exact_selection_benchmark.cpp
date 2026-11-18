#include <nlohmann/json.hpp>

#include <algorithm>
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
    auto result = input;
    std::sort(result.begin(), result.end(), Better{});
    result.resize(top_k);
    return result;
}

[[nodiscard]] std::vector<Entry> partial_sort_top_k(
    const std::vector<Entry>& input,
    const std::size_t top_k
) {
    auto result = input;
    if(top_k < result.size()) {
        std::partial_sort(result.begin(), result.begin() + top_k, result.end(), Better{});
    } else {
        std::sort(result.begin(), result.end(), Better{});
    }
    result.resize(top_k);
    return result;
}

[[nodiscard]] std::vector<Entry> nth_element_top_k(
    const std::vector<Entry>& input,
    const std::size_t top_k
) {
    auto result = input;
    if(top_k < result.size()) {
        std::nth_element(result.begin(), result.begin() + top_k, result.end(), Better{});
        std::sort(result.begin(), result.begin() + top_k, Better{});
    } else {
        std::sort(result.begin(), result.end(), Better{});
    }
    result.resize(top_k);
    return result;
}

[[nodiscard]] std::vector<Entry> heap_top_k(
    const std::vector<Entry>& input,
    const std::size_t top_k
) {
    std::priority_queue<Entry, std::vector<Entry>, Better> heap;
    for(const auto& entry : input) {
        if(heap.size() < top_k) {
            heap.push(entry);
        } else if(Better{}(entry, heap.top())) {
            heap.pop();
            heap.push(entry);
        }
    }
    std::vector<Entry> result;
    result.reserve(top_k);
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
        checksum ^= result.front().id;
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
        checksum ^= result.front().id;
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
    const auto input = make_fixture(37);
    const auto oracle = full_sort(input, 7);
    if(!same_results(oracle, partial_sort_top_k(input, 7))
       || !same_results(oracle, nth_element_top_k(input, 7))
       || !same_results(oracle, heap_top_k(input, 7))) {
        return false;
    }

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
    return true;
}

[[nodiscard]] Json run_case(const std::size_t candidate_count, const std::size_t top_k) {
    const auto input = make_fixture(candidate_count);
    const auto oracle = full_sort(input, top_k);
    return Json{
        {"candidate_count", candidate_count},
        {"top_k", top_k},
        {"variants", Json{
            {"full_sort", measure(input, oracle, top_k, full_sort)},
            {"partial_sort", measure(input, oracle, top_k, partial_sort_top_k)},
            {"nth_element_then_sort", measure(input, oracle, top_k, nth_element_top_k)},
            {"bounded_heap", measure(input, oracle, top_k, heap_top_k)}
        }}
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
        {"schema_version", 1},
        {"benchmark_name", "exact_selection_ranking_kernel"},
        {"source_commit", AGENT_MEMORY_EXACT_SELECTION_SOURCE_COMMIT},
        {"corpus_seed", "0x5eed1234"},
        {"correctness_oracle", "full_sort_with_score_desc_id_asc_tie_break"},
        {"scope", "ranking-selection kernel only; not ExactLexicalIndex end-to-end"},
        {"timing_semantics", "one sample is one complete candidate-set selection; warmups excluded"},
        {"production_api_changed", false},
        {"status", "EXECUTED"},
        {"cases", Json::array({
            run_case(128, 16),
            run_case(1024, 64),
            run_case(8192, 128),
            run_case(65536, 256)
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
