#include <agent_memory/index/ExactVectorIndex.hpp>
#include <agent_memory/index/VectorSimilarityComputer.hpp>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using namespace agent_memory;
using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;
using Corpus = std::vector<std::vector<float>>;

// These research arithmetic controls are not alternative index implementations.
// Correctness is recomputed independently in Python Decimal, not asserted here.
double dot(const std::vector<float>& a, const std::vector<float>& b,
           const std::string& accumulator) {
    if(accumulator == "fp32") {
        float narrow = 0.0F;
        for(std::size_t i = 0; i < a.size(); ++i) {
            narrow += a[i] * b[i];
        }
        return static_cast<double>(narrow);
    }
    if(accumulator == "fp64_sequential") {
        double sequential = 0.0;
        for(std::size_t i = 0; i < a.size(); ++i) {
            sequential += static_cast<double>(a[i]) * static_cast<double>(b[i]);
        }
        return sequential;
    }
    double sum = 0.0;
    double correction = 0.0;
    for(std::size_t i = 0; i < a.size(); ++i) {
        const double product = static_cast<double>(a[i]) * static_cast<double>(b[i]);
        const double next = sum + product;
        correction += std::abs(sum) >= std::abs(product)
            ? (sum - next) + product : (product - next) + sum;
        sum = next;
    }
    return sum + correction;
}

double inverse_norm(const std::vector<float>& a) {
    double scale = 0.0;
    double scaled_sum = 1.0;
    for(const float value : a) {
        const double magnitude = std::abs(static_cast<double>(value));
        if(magnitude == 0.0) {
            continue;
        }
        if(scale < magnitude) {
            const double ratio = scale == 0.0 ? 0.0 : scale / magnitude;
            scaled_sum = 1.0 + scaled_sum * ratio * ratio;
            scale = magnitude;
        } else {
            const double ratio = magnitude / scale;
            scaled_sum += ratio * ratio;
        }
    }
    return scale == 0.0 ? 0.0 : 1.0 / (scale * std::sqrt(scaled_sum));
}

Corpus random_vectors(const std::size_t rows, const std::size_t dim, std::mt19937& rng) {
    Corpus result(rows, std::vector<float>(dim));
    for(auto& row : result) {
        for(auto& value : row) {
            // Exact dyadic values; unlike uniform_real_distribution this mapping
            // does not depend on the standard library implementation.
            value = static_cast<float>(static_cast<int>(rng() % 8193U) - 4096) / 4096.0F;
        }
    }
    return result;
}

ExactVectorIndex build(const Corpus& corpus, const SimilarityMetric metric, const bool simd) {
    ExactVectorIndex index(ExactVectorIndexOptions{corpus.front().size(), metric, simd});
    for(std::size_t row = 0; row < corpus.size(); ++row) {
        index.upsert(VectorRecord{ChunkId{"chunk:" + std::to_string(row)}, Embedding{corpus[row]}, {}});
    }
    return index;
}

Json hits(const std::vector<VectorSearchResult>& values) {
    Json output = Json::array();
    for(const auto& value : values) {
        output.push_back({{"id", value.chunk_id.value()}, {"score", value.score}});
    }
    return output;
}

Json correctness_case(const std::string& name, const Corpus& corpus, const Corpus& queries,
                      const SimilarityMetric metric, const std::string& metric_name) {
    Json result = {{"name", name}, {"metric", metric_name}, {"corpus", corpus},
                   {"queries", queries}, {"runs", Json::array()}};
    for(const bool simd : {false, true}) {
        const auto index = build(corpus, metric, simd);
        Json run = {{"simd_requested", simd},
                    {"backend", vector_similarity_backend_name(index.similarity_backend())},
                    {"queries", Json::array()}};
        for(const auto& query : queries) {
            Json query_output = {{"limits", Json::array()}, {"arithmetic", Json::object()}};
            for(const auto limit : {std::size_t{0}, std::size_t{1}, std::size_t{10}, corpus.size()}) {
                query_output["limits"].push_back({{"k", limit},
                    {"hits", hits(index.search(VectorSearchQuery{Embedding{query}, limit, {}}))}});
            }
            if(metric == SimilarityMetric::Cosine) {
                for(const std::string method : {"fp32", "fp64_sequential", "neumaier"}) {
                    Json scores = Json::array();
                    for(const auto& record : corpus) {
                        scores.push_back(static_cast<float>(dot(query, record, method)
                            * inverse_norm(query) * inverse_norm(record)));
                    }
                    query_output["arithmetic"][method] = std::move(scores);
                }
            }
            run["queries"].push_back(std::move(query_output));
        }
        result["runs"].push_back(std::move(run));
    }
    return result;
}

Json controls() {
    Json output;
    auto index = build(Corpus{{1.0F, 0.0F}}, SimilarityMetric::Cosine, false);
    for(const auto& name : {"nan_query", "infinite_record", "dimension", "empty_query"}) {
        bool rejected = false;
        try {
            if(std::string(name) == "nan_query") {
                (void)index.search(VectorSearchQuery{Embedding{{std::numeric_limits<float>::quiet_NaN(), 0.0F}}, 1, {}});
            } else if(std::string(name) == "infinite_record") {
                index.upsert(VectorRecord{ChunkId{"invalid"}, Embedding{{std::numeric_limits<float>::infinity(), 0.0F}}, {}});
            } else if(std::string(name) == "dimension") {
                (void)index.search(VectorSearchQuery{Embedding{{1.0F}}, 1, {}});
            } else {
                (void)index.search(VectorSearchQuery{Embedding{{}}, 1, {}});
            }
        } catch(const std::invalid_argument&) {
            rejected = true;
        }
        output[name] = rejected;
    }
    for(const bool simd : {false, true}) {
        const float extreme = std::numeric_limits<float>::max();
        auto overflow = build(Corpus{{extreme, extreme}}, SimilarityMetric::DotProduct, simd);
        bool rejected = false;
        try {
            (void)overflow.search(VectorSearchQuery{Embedding{{extreme, extreme}}, 1, {}});
        } catch(const std::overflow_error&) {
            rejected = true;
        }
        output[simd ? "overflow_auto" : "overflow_scalar"] = rejected;
    }
    ExactVectorIndex empty(ExactVectorIndexOptions{2, SimilarityMetric::Cosine, false});
    output["empty_index"] = empty.search(VectorSearchQuery{Embedding{{1.0F, 0.0F}}, 10, {}}).empty();
    return output;
}

template<class Fn> double measure(Fn&& fn) {
    const auto start = Clock::now();
    fn();
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

Json performance_case(const std::size_t rows, const std::size_t dim,
                      const SimilarityMetric metric, const std::string& metric_name) {
    const auto seed = static_cast<std::uint32_t>(0x5eedU + rows + dim);
    std::mt19937 rng(seed);
    const auto corpus = random_vectors(rows, dim, rng);
    const auto queries = random_vectors(4, dim, rng);
    Json result = {{"rows", rows}, {"dimension", dim}, {"seed", seed},
                   {"metric", metric_name}, {"query_count", queries.size()},
                   {"runs", Json::array()}, {"kernel_samples_ms", Json::object()}};
    volatile double sink = 0.0;
    for(const bool simd : {false, true}) {
        const auto start = Clock::now();
        const auto index = build(corpus, metric, simd);
        const double construction_ms = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
        for(const auto k : {std::size_t{1}, std::size_t{10}, rows}) {
            Json run = {{"simd_requested", simd}, {"k", k}, {"construction_ms", construction_ms},
                        {"backend", vector_similarity_backend_name(index.similarity_backend())},
                        {"search_samples_ms", Json::array()}, {"output", Json::array()}};
            for(const auto& query : queries) {
                const auto output = index.search(VectorSearchQuery{Embedding{query}, k, {}});
                // Full output is verified for the independent correctness suite.
                // Keep timing matrix artifacts compact without claiming a new oracle.
                run["output"].push_back(hits(std::vector<VectorSearchResult>(output.begin(),
                    output.begin() + static_cast<std::ptrdiff_t>(std::min<std::size_t>(10, output.size())))));
                run["result_counts"].push_back(output.size());
            }
            for(std::size_t sample = 0; sample < 7; ++sample) {
                run["search_samples_ms"].push_back(measure([&] {
                    for(const auto& query : queries) {
                        const auto found = index.search(VectorSearchQuery{Embedding{query}, k, {}});
                        sink += static_cast<double>(found.size()) + (found.empty() ? 0.0 : found.front().score);
                    }
                }));
            }
            result["runs"].push_back(std::move(run));
        }
    }
    std::vector<double> record_norms;
    for(const auto& record : corpus) {
        record_norms.push_back(inverse_norm(record));
    }
    VectorSimilarityComputer scalar(false), automatic(true);
    std::vector<std::string> methods = metric == SimilarityMetric::Cosine
        ? std::vector<std::string>{"fp32", "fp64_sequential", "neumaier"}
        : std::vector<std::string>{"scalar_primitive", "auto_primitive"};
    for(const auto& method : methods) {
        result["kernel_samples_ms"][method] = Json::array();
    }
    // Rotate order, rather than always heating the cache for the same arm.
    for(std::size_t sample = 0; sample < 8; ++sample) {
        std::rotate(methods.begin(), methods.begin() + 1, methods.end());
        for(const auto& method : methods) {
            const double elapsed = measure([&] {
                double total = 0.0;
                for(const auto& query : queries) {
                    const double query_norm = inverse_norm(query);
                    for(std::size_t row = 0; row < rows; ++row) {
                        if(metric == SimilarityMetric::Cosine) {
                            total += static_cast<float>(dot(query, corpus[row], method) * query_norm * record_norms[row]);
                        } else {
                            const auto& computer = method == "scalar_primitive" ? scalar : automatic;
                            total += computer.dot_product_values(query.data(), corpus[row].data(), dim);
                        }
                    }
                }
                sink += total;
            });
            if(sample != 0) {
                result["kernel_samples_ms"][method].push_back(elapsed);
            }
        }
    }
    result["observable_sink"] = static_cast<double>(sink);
    return result;
}
} // namespace

int main(int argc, char** argv) {
    try {
        if(argc != 2) {
            throw std::invalid_argument("usage: agent-memory-exact-vector-index-decision output.json");
        }
        std::mt19937 rng(0x5eedU);
        const auto random_corpus = random_vectors(128, 32, rng);
        const auto random_queries = random_vectors(4, 32, rng);
        const float large = std::ldexp(1.0F, 60);
        Json output = {{"schema", "r5.exact_vector_index.decision.v2"},
            {"production_path", "ExactVectorIndex::search"},
            {"compiled_checkout_sha", AGENT_MEMORY_R5_CHECKOUT_SHA},
            {"runner_source_sha256", AGENT_MEMORY_R5_RUNNER_SHA256},
            {"index_source_sha256", AGENT_MEMORY_R5_INDEX_SHA256},
            {"compiler", std::string(AGENT_MEMORY_EVALUATOR_COMPILER_ID) + " " + AGENT_MEMORY_EVALUATOR_COMPILER_VERSION},
            {"cxx_standard", AGENT_MEMORY_EVALUATOR_CXX_STANDARD},
            {"base_flags_sha256", AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256},
            {"configuration_flags_sha256", AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256},
            {"build_configuration", AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION},
            {"source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
            {"controls", controls()}, {"correctness", Json::array()}, {"performance", Json::array()}};
        output["correctness"].push_back(correctness_case("cancellation",
            {{large, 0.0F, -large}, {large, 1.0F, -large}, {0.0F, 0.0F, 0.0F}},
            {{large, 1.0F, large}}, SimilarityMetric::Cosine, "cosine"));
        output["correctness"].push_back(correctness_case("near_tie_and_exact_tie",
            {{1.0F, 0.0078277587890625F}, {1.0F, 0.0078125F}, {1.0F, 0.0078125F}},
            {{1.0F, 0.0F}}, SimilarityMetric::Cosine, "cosine"));
        output["correctness"].push_back(correctness_case("tiny_cosine",
            {{1.0e-30F, 0.0F}, {0.0F, 1.0e-30F}, {-1.0e-30F, 0.0F}},
            {{1.0e-30F, 0.0F}}, SimilarityMetric::Cosine, "cosine"));
        for(const auto metric : {SimilarityMetric::Cosine, SimilarityMetric::DotProduct, SimilarityMetric::Euclidean}) {
            const std::string name = metric == SimilarityMetric::Cosine ? "cosine"
                : (metric == SimilarityMetric::DotProduct ? "dot" : "negative_squared_l2");
            output["correctness"].push_back(correctness_case("random_" + name,
                random_corpus, random_queries, metric, name));
        }
        for(const auto shape : {std::pair<std::size_t, std::size_t>{128, 8}, {1024, 128},
                              {8192, 32}, {65536, 32}, {128, 768}}) {
            output["performance"].push_back(performance_case(shape.first, shape.second, SimilarityMetric::Cosine, "cosine"));
            output["performance"].push_back(performance_case(shape.first, shape.second, SimilarityMetric::DotProduct, "dot"));
        }
        std::ofstream stream(argv[1]);
        if(!stream) {
            throw std::runtime_error("cannot open output");
        }
        stream << output.dump() << '\n';
        if(!stream) {
            throw std::runtime_error("cannot write output");
        }
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
