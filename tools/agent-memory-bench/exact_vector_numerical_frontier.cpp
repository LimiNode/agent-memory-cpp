#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

    using Json = nlohmann::json;
    using Clock = std::chrono::steady_clock;

#ifndef AGENT_MEMORY_NUMERICAL_FRONTIER_SOURCE_COMMIT
#define AGENT_MEMORY_NUMERICAL_FRONTIER_SOURCE_COMMIT "unknown"
#endif

    constexpr std::size_t kDefaultDocumentCount = 256;
    constexpr std::size_t kDefaultQueryCount = 16;
    constexpr std::size_t kDefaultDimension = 384;
    constexpr std::size_t kDefaultWarmups = 2;
    constexpr std::size_t kDefaultRepeats = 6;
    constexpr std::size_t kTopK = 10;

    enum class Method {
        FloatLanes,
        Fp64Sequential,
        Fp64Neumaier,
        Fp64Pairwise,
        Fp64Blockwise,
        Fp64Guarded
    };

    struct VectorPair final {
        std::vector<float> query;
        std::vector<float> document;
    };

    struct Corpus final {
        std::vector<std::vector<float>> documents;
        std::vector<std::vector<float>> queries;
        std::uint64_t seed = 0;
    };

    struct Ranked final {
        std::size_t id = 0;
        double score = 0.0;
    };

    struct Summary final {
        std::vector<double> samples_ms;
        double mean_ms = 0.0;
        double min_ms = 0.0;
        double p50_ms = 0.0;
        double p95_ms = 0.0;
        double max_ms = 0.0;
        std::size_t nonfinite_scores = 0;
        double max_abs_error = 0.0;
        double max_relative_error = 0.0;
        std::size_t top1_matches = 0;
        std::size_t topk_matches = 0;
    };

    [[nodiscard]] std::string_view method_name(const Method method) noexcept {
        switch(method) {
            case Method::FloatLanes: return "float_lanes";
            case Method::Fp64Sequential: return "fp64_sequential";
            case Method::Fp64Neumaier: return "fp64_neumaier";
            case Method::Fp64Pairwise: return "fp64_pairwise";
            case Method::Fp64Blockwise: return "fp64_blockwise";
            case Method::Fp64Guarded: return "fp64_guarded_blockwise";
        }
        return "unknown";
    }

    [[nodiscard]] std::uint64_t next_random(std::uint64_t& state) noexcept {
        state ^= state << 7U;
        state ^= state >> 9U;
        state ^= state << 8U;
        return state;
    }

    [[nodiscard]] float random_value(std::uint64_t& state) noexcept {
        const auto bits = next_random(state) >> 40U;
        const auto unit = static_cast<double>(bits) / static_cast<double>(1ULL << 24U);
        return static_cast<float>(unit * 2.0 - 1.0);
    }

    [[nodiscard]] Corpus make_corpus(
        const std::size_t document_count,
        const std::size_t query_count,
        const std::size_t dimension,
        const std::uint64_t seed
    ) {
        Corpus corpus;
        corpus.seed = seed;
        std::uint64_t state = seed;
        corpus.documents.resize(document_count, std::vector<float>(dimension));
        corpus.queries.resize(query_count, std::vector<float>(dimension));
        for(auto& row : corpus.documents) {
            for(auto& value : row) {
                value = random_value(state);
            }
        }
        for(auto& row : corpus.queries) {
            for(auto& value : row) {
                value = random_value(state);
            }
        }
        return corpus;
    }

    [[nodiscard]] double neumaier_sum(const std::vector<double>& values) noexcept {
        double sum = 0.0;
        double compensation = 0.0;
        for(const auto value : values) {
            const auto next = sum + value;
            if(std::abs(sum) >= std::abs(value)) {
                compensation += (sum - next) + value;
            } else {
                compensation += (value - next) + sum;
            }
            sum = next;
        }
        return sum + compensation;
    }

    [[nodiscard]] double pairwise_sum(
        const std::vector<double>& values,
        const std::size_t begin,
        const std::size_t end
    ) noexcept {
        const auto count = end - begin;
        if(count == 0) {
            return 0.0;
        }
        if(count <= 32) {
            double sum = 0.0;
            for(std::size_t index = begin; index < end; ++index) {
                sum += values[index];
            }
            return sum;
        }
        const auto middle = begin + count / 2;
        return pairwise_sum(values, begin, middle)
            + pairwise_sum(values, middle, end);
    }

    [[nodiscard]] double blockwise_sum(
        const std::vector<double>& values,
        const std::size_t block_size,
        bool* finite
    ) noexcept {
        std::vector<double> blocks;
        blocks.reserve((values.size() + block_size - 1) / block_size);
        for(std::size_t begin = 0; begin < values.size(); begin += block_size) {
            const auto end = std::min(values.size(), begin + block_size);
            double block = 0.0;
            for(std::size_t index = begin; index < end; ++index) {
                block += values[index];
            }
            blocks.push_back(block);
            if(!std::isfinite(block)) {
                *finite = false;
            }
        }
        const auto result = neumaier_sum(blocks);
        if(!std::isfinite(result)) {
            *finite = false;
        }
        return result;
    }

    [[nodiscard]] double guarded_blockwise_sum(
        const std::vector<double>& values,
        const std::size_t block_size,
        bool* used_fallback
    ) noexcept {
        bool finite = true;
        const auto result = blockwise_sum(values, block_size, &finite);
        if(finite && std::isfinite(result)) {
            *used_fallback = false;
            return result;
        }
        *used_fallback = true;
        return neumaier_sum(values);
    }

    [[nodiscard]] double sum_products(
        const std::vector<float>& lhs,
        const std::vector<float>& rhs,
        const Method method,
        bool* used_fallback = nullptr
    ) noexcept {
        std::vector<double> products;
        products.reserve(lhs.size());
        if(used_fallback != nullptr) {
            *used_fallback = false;
        }
        if(method == Method::FloatLanes) {
            std::array<float, 8> lanes{};
            std::size_t index = 0;
            for(; index + lanes.size() <= lhs.size(); index += lanes.size()) {
                for(std::size_t lane = 0; lane < lanes.size(); ++lane) {
                    lanes[lane] += lhs[index + lane] * rhs[index + lane];
                }
            }
            float result = 0.0F;
            for(const auto lane : lanes) {
                result += lane;
            }
            for(; index < lhs.size(); ++index) {
                result += lhs[index] * rhs[index];
            }
            return static_cast<double>(result);
        }
        for(std::size_t index = 0; index < lhs.size(); ++index) {
            products.push_back(
                static_cast<double>(lhs[index]) * static_cast<double>(rhs[index])
            );
        }
        switch(method) {
            case Method::Fp64Sequential: {
                double result = 0.0;
                for(const auto product : products) {
                    result += product;
                }
                return result;
            }
            case Method::Fp64Neumaier:
                return neumaier_sum(products);
            case Method::Fp64Pairwise:
                return pairwise_sum(products, 0, products.size());
            case Method::Fp64Blockwise: {
                bool finite = true;
                return blockwise_sum(products, 32, &finite);
            }
            case Method::Fp64Guarded: {
                bool fallback = false;
                const auto result = guarded_blockwise_sum(products, 32, &fallback);
                if(used_fallback != nullptr) {
                    *used_fallback = fallback;
                }
                return result;
            }
            case Method::FloatLanes:
                break;
        }
        return 0.0;
    }

    [[nodiscard]] double cosine_score(
        const std::vector<float>& query,
        const std::vector<float>& document,
        const Method method,
        bool* used_fallback = nullptr
    ) noexcept {
        const auto dot = sum_products(query, document, method, used_fallback);
        const auto query_norm = std::sqrt(std::max(0.0, sum_products(query, query, method)));
        const auto document_norm = std::sqrt(
            std::max(0.0, sum_products(document, document, method))
        );
        if(query_norm == 0.0 || document_norm == 0.0) {
            return 0.0;
        }
        return dot / (query_norm * document_norm);
    }

    [[nodiscard]] double reference_cosine(
        const std::vector<float>& query,
        const std::vector<float>& document
    ) noexcept {
        std::vector<long double> dot(query.size());
        std::vector<long double> query_norm(query.size());
        std::vector<long double> document_norm(query.size());
        for(std::size_t index = 0; index < query.size(); ++index) {
            dot[index] = static_cast<long double>(query[index])
                * static_cast<long double>(document[index]);
            query_norm[index] = static_cast<long double>(query[index])
                * static_cast<long double>(query[index]);
            document_norm[index] = static_cast<long double>(document[index])
                * static_cast<long double>(document[index]);
        }
        long double dot_sum = 0.0L;
        long double dot_compensation = 0.0L;
        long double query_sum = 0.0L;
        long double query_compensation = 0.0L;
        long double document_sum = 0.0L;
        long double document_compensation = 0.0L;
        for(std::size_t index = 0; index < query.size(); ++index) {
            const auto add_neumaier = [](long double& sum, long double& compensation,
                                         const long double value) {
                const auto next = sum + value;
                if(std::abs(sum) >= std::abs(value)) {
                    compensation += (sum - next) + value;
                } else {
                    compensation += (value - next) + sum;
                }
                sum = next;
            };
            add_neumaier(dot_sum, dot_compensation, dot[index]);
            add_neumaier(query_sum, query_compensation, query_norm[index]);
            add_neumaier(document_sum, document_compensation, document_norm[index]);
        }
        dot_sum += dot_compensation;
        query_sum += query_compensation;
        document_sum += document_compensation;
        if(query_sum == 0.0 || document_sum == 0.0) {
            return 0.0;
        }
        return static_cast<double>(dot_sum
            / (std::sqrt(query_sum) * std::sqrt(document_sum)));
    }

    [[nodiscard]] bool better(const Ranked& lhs, const Ranked& rhs) noexcept {
        if(lhs.score == rhs.score) {
            return lhs.id < rhs.id;
        }
        return lhs.score > rhs.score;
    }

    [[nodiscard]] std::vector<Ranked> rank_query(
        const Corpus& corpus,
        const std::size_t query_index,
        const Method method,
        std::size_t* nonfinite,
        bool* fallback
    ) {
        std::vector<Ranked> ranked;
        ranked.reserve(corpus.documents.size());
        for(std::size_t document = 0; document < corpus.documents.size(); ++document) {
            bool used_fallback = false;
            const auto score = cosine_score(
                corpus.queries[query_index], corpus.documents[document], method, &used_fallback
            );
            if(used_fallback && fallback != nullptr) {
                *fallback = true;
            }
            if(!std::isfinite(score)) {
                ++*nonfinite;
            }
            ranked.push_back(Ranked{document, score});
        }
        std::partial_sort(
            ranked.begin(),
            ranked.begin() + std::min(kTopK, ranked.size()),
            ranked.end(),
            better
        );
        ranked.resize(std::min(kTopK, ranked.size()));
        return ranked;
    }

    [[nodiscard]] std::vector<std::vector<Ranked>> reference_ranking(const Corpus& corpus) {
        std::vector<std::vector<Ranked>> result(corpus.queries.size());
        for(std::size_t query = 0; query < corpus.queries.size(); ++query) {
            for(std::size_t document = 0; document < corpus.documents.size(); ++document) {
                result[query].push_back(Ranked{
                    document,
                    reference_cosine(corpus.queries[query], corpus.documents[document])
                });
            }
            std::partial_sort(
                result[query].begin(),
                result[query].begin() + std::min(kTopK, result[query].size()),
                result[query].end(),
                better
            );
            result[query].resize(std::min(kTopK, result[query].size()));
        }
        return result;
    }

    [[nodiscard]] double percentile(std::vector<double> values, const double fraction) {
        if(values.empty()) {
            return 0.0;
        }
        std::sort(values.begin(), values.end());
        const auto position = fraction * static_cast<double>(values.size() - 1);
        const auto lower = static_cast<std::size_t>(position);
        const auto upper = std::min(values.size() - 1, lower + 1);
        const auto weight = position - static_cast<double>(lower);
        return values[lower] * (1.0 - weight) + values[upper] * weight;
    }

    [[nodiscard]] Summary summarize(
        const std::vector<double>& samples,
        const std::vector<std::vector<Ranked>>& expected,
        const std::vector<std::vector<Ranked>>& actual,
        const std::size_t nonfinite,
        const double max_abs_error,
        const double max_relative_error
    ) {
        Summary summary;
        summary.samples_ms = samples;
        summary.nonfinite_scores = nonfinite;
        summary.max_abs_error = max_abs_error;
        summary.max_relative_error = max_relative_error;
        if(!samples.empty()) {
            summary.mean_ms = std::accumulate(samples.begin(), samples.end(), 0.0)
                / static_cast<double>(samples.size());
            summary.min_ms = *std::min_element(samples.begin(), samples.end());
            summary.max_ms = *std::max_element(samples.begin(), samples.end());
            summary.p50_ms = percentile(samples, 0.50);
            summary.p95_ms = percentile(samples, 0.95);
        }
        for(std::size_t query = 0; query < actual.size(); ++query) {
            if(actual[query].empty() || expected.empty()) {
                continue;
            }
            if(actual[query][0].id == expected[query][0].id) {
                ++summary.top1_matches;
            }
            bool topk_match = actual[query].size() == expected[query].size();
            for(std::size_t index = 0; topk_match && index < actual[query].size(); ++index) {
                if(actual[query][index].id != expected[query][index].id) {
                    topk_match = false;
                }
            }
            if(topk_match) {
                ++summary.topk_matches;
            }
        }
        return summary;
    }

    [[nodiscard]] Json summary_json(const Summary& summary) {
        return Json{
            {"samples_ms", summary.samples_ms},
            {"sample_count", summary.samples_ms.size()},
            {"mean_ms", summary.mean_ms},
            {"min_ms", summary.min_ms},
            {"p50_ms", summary.p50_ms},
            {"p95_ms", summary.p95_ms},
            {"max_ms", summary.max_ms},
            {"nonfinite_scores", summary.nonfinite_scores},
            {"max_abs_error_vs_long_double_oracle", summary.max_abs_error},
            {"max_relative_error_vs_long_double_oracle", summary.max_relative_error},
            {"top1_exact_queries", summary.top1_matches},
            {"topk_exact_queries", summary.topk_matches}
        };
    }

    [[nodiscard]] Json run_benchmark(
        const std::size_t document_count,
        const std::size_t query_count,
        const std::size_t dimension,
        const std::size_t warmups,
        const std::size_t repeats
    ) {
        const auto corpus = make_corpus(document_count, query_count, dimension, 0x5eed1234ULL);
        const auto expected = reference_ranking(corpus);
        constexpr std::array<Method, 6> methods{
            Method::FloatLanes,
            Method::Fp64Sequential,
            Method::Fp64Neumaier,
            Method::Fp64Pairwise,
            Method::Fp64Blockwise,
            Method::Fp64Guarded
        };
        constexpr auto method_count = methods.size();
        std::array<std::vector<double>, method_count> samples;
        std::array<std::vector<std::vector<Ranked>>, method_count> actual;
        std::array<std::size_t, method_count> nonfinite{};
        std::array<bool, method_count> fallback{};
        const auto run_scan = [&](const std::size_t method_index) {
            actual[method_index].clear();
            double checksum = 0.0;
            for(std::size_t query = 0; query < query_count; ++query) {
                actual[method_index].push_back(rank_query(
                    corpus,
                    query,
                    methods[method_index],
                    &nonfinite[method_index],
                    &fallback[method_index]
                ));
                for(const auto item : actual[method_index].back()) {
                    checksum += item.score;
                }
            }
            volatile double keep_checksum = checksum;
            static_cast<void>(keep_checksum);
        };

        // Rotate the method order on every repeat so a single fixed order does
        // not systematically assign warm caches or CPU frequency to one arm.
        for(std::size_t repeat = 0; repeat < warmups; ++repeat) {
            for(std::size_t slot = 0; slot < method_count; ++slot) {
                run_scan((slot + repeat) % method_count);
            }
        }
        for(std::size_t repeat = 0; repeat < repeats; ++repeat) {
            for(std::size_t slot = 0; slot < method_count; ++slot) {
                const auto method_index = (slot + repeat) % method_count;
                const auto start = Clock::now();
                run_scan(method_index);
                const auto elapsed = std::chrono::duration<double, std::milli>(
                    Clock::now() - start
                ).count();
                samples[method_index].push_back(elapsed);
            }
        }
        Json methods_json = Json::object();
        for(std::size_t method_index = 0; method_index < method_count; ++method_index) {
            const auto method = methods[method_index];
            double max_abs_error = 0.0;
            double max_relative_error = 0.0;
            for(std::size_t query = 0; query < query_count; ++query) {
                for(std::size_t document = 0; document < document_count; ++document) {
                    bool ignored_fallback = false;
                    const auto actual_score = cosine_score(
                        corpus.queries[query], corpus.documents[document], method,
                        &ignored_fallback
                    );
                    const auto reference_score = reference_cosine(
                        corpus.queries[query], corpus.documents[document]
                    );
                    const auto absolute = std::abs(actual_score - reference_score);
                    max_abs_error = std::max(max_abs_error, absolute);
                    if(reference_score != 0.0) {
                        max_relative_error = std::max(
                            max_relative_error, absolute / std::abs(reference_score)
                        );
                    }
                }
            }
            methods_json[std::string(method_name(method))] = summary_json(summarize(
                samples[method_index], expected, actual[method_index], nonfinite[method_index],
                max_abs_error, max_relative_error
            ));
            methods_json[std::string(method_name(method))]["guarded_fallback_observed"] =
                fallback[method_index];
        }
        return Json{
            {"schema_version", 1},
            {"family", "exact_vector_numerical_optimization_frontier_v1"},
            {"status", "EXECUTED"},
            {"source_commit", AGENT_MEMORY_NUMERICAL_FRONTIER_SOURCE_COMMIT},
            {"production_api_changed", false},
            {"portable_fp64_control", true},
            {"simd_claim", "none"},
            {"documents", document_count},
            {"queries", query_count},
            {"dimension", dimension},
            {"warmups", warmups},
            {"measured_repeats", repeats},
            {"sample_count_semantics", "one sample is one complete corpus/query sweep; not an independent request"},
            {"timing_claim", "directional measured distribution; p95 is not a stability guarantee"},
            {"oracle", "long_double_neumaier_over_float_products"},
            {"methods", methods_json},
            {"limitations", Json::array({
                "The harness is a direct arithmetic scan, not end-to-end ExactVectorIndex throughput.",
                "The portable FP64 methods are research controls and are not production API candidates.",
                "The long-double oracle can have platform-dependent precision when long double equals double."
            })}
        };
    }

    void require(const bool condition, const char* message) {
        if(!condition) {
            throw std::runtime_error(message);
        }
    }

    void self_test() {
        const std::vector<float> lhs{1.0e20F, 1.0F, -1.0e20F, 1.0F};
        const std::vector<float> rhs{1.0F, 1.0F, 1.0F, 1.0F};
        const auto sequential = sum_products(lhs, rhs, Method::Fp64Sequential);
        const auto neumaier = sum_products(lhs, rhs, Method::Fp64Neumaier);
        const auto pairwise = sum_products(lhs, rhs, Method::Fp64Pairwise);
        const auto blockwise = sum_products(lhs, rhs, Method::Fp64Blockwise);
        require(std::abs(sequential - 1.0) < 1.0e-9, "fp64 sequential cancellation control failed");
        require(std::abs(neumaier - 2.0) < 1.0e-9, "Neumaier cancellation control failed");
        require(std::abs(pairwise - 1.0) < 1.0e-9, "pairwise cancellation control failed");
        require(std::abs(blockwise - 1.0) < 1.0e-9, "blockwise cancellation control failed");

        std::vector<double> injected{
            std::numeric_limits<double>::max(), std::numeric_limits<double>::max()
        };
        bool used_fallback = false;
        const auto guarded = guarded_blockwise_sum(injected, 1, &used_fallback);
        require(used_fallback && !std::isfinite(guarded), "guard trigger control failed");

        const auto corpus = make_corpus(32, 4, 64, 12345);
        const auto expected = reference_ranking(corpus);
        for(const auto method : std::array<Method, 6>{
                Method::FloatLanes, Method::Fp64Sequential, Method::Fp64Neumaier,
                Method::Fp64Pairwise, Method::Fp64Blockwise, Method::Fp64Guarded}) {
            std::size_t nonfinite = 0;
            bool fallback = false;
            for(std::size_t query = 0; query < corpus.queries.size(); ++query) {
                const auto actual = rank_query(corpus, query, method, &nonfinite, &fallback);
                require(!actual.empty(), "ranking self-test returned no candidates");
                require(actual[0].id < corpus.documents.size(), "ranking returned invalid id");
            }
            static_cast<void>(expected);
            require(nonfinite == 0, "random finite fixture produced non-finite score");
        }

        Corpus tie_corpus;
        tie_corpus.documents = {{1.0F, 0.0F}, {1.0F, 0.0F}, {0.99999994F, 0.001F}};
        tie_corpus.queries = {{1.0F, 0.0F}};
        for(const auto method : std::array<Method, 6>{
                Method::FloatLanes, Method::Fp64Sequential, Method::Fp64Neumaier,
                Method::Fp64Pairwise, Method::Fp64Blockwise, Method::Fp64Guarded}) {
            std::size_t nonfinite = 0;
            bool fallback = false;
            const auto ranked = rank_query(tie_corpus, 0, method, &nonfinite, &fallback);
            require(ranked.size() == 3 && ranked[0].id == 0 && ranked[1].id == 1,
                    "near-tie deterministic ordering control failed");
        }
        std::cout << "exact vector numerical frontier self-test: PASS\n";
    }

    struct Arguments final {
        bool self_test = false;
        std::string output;
        std::size_t documents = kDefaultDocumentCount;
        std::size_t queries = kDefaultQueryCount;
        std::size_t dimension = kDefaultDimension;
        std::size_t warmups = kDefaultWarmups;
        std::size_t repeats = kDefaultRepeats;
    };

    [[nodiscard]] std::size_t parse_size(const char* value, const char* option) {
        try {
            const auto parsed = std::stoull(value);
            if(parsed == 0) {
                throw std::invalid_argument("zero");
            }
            return static_cast<std::size_t>(parsed);
        } catch(...) {
            throw std::invalid_argument(std::string("invalid value for ") + option);
        }
    }

    [[nodiscard]] Arguments parse_arguments(const int argc, char** argv) {
        Arguments arguments;
        for(int index = 1; index < argc; ++index) {
            const std::string_view option(argv[index]);
            if(option == "--self-test") {
                arguments.self_test = true;
            } else if(option == "--output" && index + 1 < argc) {
                arguments.output = argv[++index];
            } else if(option == "--documents" && index + 1 < argc) {
                arguments.documents = parse_size(argv[++index], "--documents");
            } else if(option == "--queries" && index + 1 < argc) {
                arguments.queries = parse_size(argv[++index], "--queries");
            } else if(option == "--dimension" && index + 1 < argc) {
                arguments.dimension = parse_size(argv[++index], "--dimension");
            } else if(option == "--warmups" && index + 1 < argc) {
                arguments.warmups = parse_size(argv[++index], "--warmups");
            } else if(option == "--repeats" && index + 1 < argc) {
                arguments.repeats = parse_size(argv[++index], "--repeats");
            } else {
                throw std::invalid_argument("unknown or incomplete option: " + std::string(option));
            }
        }
        return arguments;
    }

} // namespace

int main(const int argc, char** argv) {
    try {
        const auto arguments = parse_arguments(argc, argv);
        if(arguments.self_test) {
            self_test();
            return 0;
        }
        const auto receipt = run_benchmark(
            arguments.documents,
            arguments.queries,
            arguments.dimension,
            arguments.warmups,
            arguments.repeats
        );
        if(arguments.output.empty()) {
            std::cout << std::setw(2) << receipt << '\n';
        } else {
            std::ofstream output(arguments.output, std::ios::binary | std::ios::trunc);
            if(!output) {
                throw std::runtime_error("cannot open output receipt");
            }
            output << std::setw(2) << receipt << '\n';
            std::cout << "wrote " << arguments.output << '\n';
        }
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "exact vector numerical frontier: " << error.what() << '\n';
        return 2;
    }
}
