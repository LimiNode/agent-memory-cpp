#include <agent_memory/embedding/embedding_types.hpp>
#include <agent_memory/index/VectorSimilarityComputer.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#if defined(_WIN32)
#include <cstdlib>
#elif defined(__APPLE__)
#include <sys/types.h>
#include <sys/sysctl.h>
#endif

namespace {

#ifndef AGENT_MEMORY_COSINE_BENCH_BUILD_FLAGS
#define AGENT_MEMORY_COSINE_BENCH_BUILD_FLAGS "unspecified"
#endif

    using Clock = std::chrono::steady_clock;
    using Json = nlohmann::json;

    constexpr std::size_t kDocumentCount = 512;
    constexpr std::size_t kQueryCount = 24;
    constexpr std::size_t kTopK = 10;
    constexpr std::size_t kWarmupRepeats = 2;
    constexpr std::size_t kMeasuredRepeats = 8;
    constexpr std::uint64_t kSeed = 0x9e3779b97f4a7c15ULL;

    struct DenseCase final {
        std::vector<agent_memory::Embedding> documents;
        std::vector<agent_memory::Embedding> queries;
        std::uint64_t corpus_hash = 1469598103934665603ULL;
    };

    struct RankedItem final {
        std::size_t index = 0;
        float score = 0.0F;
    };

    struct ReferenceItem final {
        std::size_t index = 0;
        double score = 0.0;
    };

    struct TimingSummary final {
        std::vector<double> samples_ms;
        double mean_ms = 0.0;
        double min_ms = 0.0;
        double p50_ms = 0.0;
        double p95_ms = 0.0;
        double p99_ms = 0.0;
        double max_ms = 0.0;
    };

    struct ScanResult final {
        std::vector<RankedItem> items;
        double checksum = 0.0;
    };

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

    void hash_bytes(
        std::uint64_t& hash,
        const void* data,
        const std::size_t size
    ) noexcept {
        const auto* bytes = static_cast<const std::uint8_t*>(data);
        for(std::size_t index = 0; index < size; ++index) {
            hash ^= bytes[index];
            hash *= 1099511628211ULL;
        }
    }

    [[nodiscard]] DenseCase make_case(const std::size_t dimension) {
        DenseCase result;
        result.documents.reserve(kDocumentCount);
        result.queries.reserve(kQueryCount);
        std::uint64_t state = kSeed ^ (static_cast<std::uint64_t>(dimension) << 32U);
        for(std::size_t document = 0; document < kDocumentCount; ++document) {
            agent_memory::Embedding embedding;
            embedding.values.resize(dimension);
            for(auto& value : embedding.values) {
                value = random_value(state);
            }
            hash_bytes(result.corpus_hash, embedding.values.data(), embedding.values.size() * sizeof(float));
            result.documents.push_back(std::move(embedding));
        }
        for(std::size_t query = 0; query < kQueryCount; ++query) {
            agent_memory::Embedding embedding;
            embedding.values.resize(dimension);
            for(auto& value : embedding.values) {
                value = random_value(state);
            }
            hash_bytes(result.corpus_hash, embedding.values.data(), embedding.values.size() * sizeof(float));
            result.queries.push_back(std::move(embedding));
        }
        return result;
    }

    [[nodiscard]] double stable_inverse_norm(
        const agent_memory::Embedding& embedding
    ) noexcept {
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
        return 1.0 / (scale * std::sqrt(scaled_sum));
    }

    [[nodiscard]] double double_reference_cosine(
        const agent_memory::Embedding& query,
        const agent_memory::Embedding& document
    ) noexcept {
        double dot = 0.0;
        double query_norm = 0.0;
        double document_norm = 0.0;
        for(std::size_t index = 0; index < query.values.size(); ++index) {
            const auto query_value = static_cast<double>(query.values[index]);
            const auto document_value = static_cast<double>(document.values[index]);
            dot += query_value * document_value;
            query_norm += query_value * query_value;
            document_norm += document_value * document_value;
        }
        if(query_norm == 0.0 || document_norm == 0.0) {
            return 0.0;
        }
        return dot / (std::sqrt(query_norm) * std::sqrt(document_norm));
    }

    [[nodiscard]] bool better(
        const RankedItem& lhs,
        const RankedItem& rhs
    ) noexcept {
        if(lhs.score == rhs.score) {
            return lhs.index < rhs.index;
        }
        return lhs.score > rhs.score;
    }

    [[nodiscard]] bool better_reference(
        const ReferenceItem& lhs,
        const ReferenceItem& rhs
    ) noexcept {
        if(lhs.score == rhs.score) {
            return lhs.index < rhs.index;
        }
        return lhs.score > rhs.score;
    }

    [[nodiscard]] ScanResult finish_scan(
        std::vector<RankedItem> scored
    ) {
        if(scored.size() > kTopK) {
            std::partial_sort(scored.begin(), scored.begin() + kTopK, scored.end(), better);
            scored.resize(kTopK);
        } else {
            std::sort(scored.begin(), scored.end(), better);
        }
        ScanResult result;
        result.items = std::move(scored);
        for(const auto& item : result.items) {
            result.checksum += static_cast<double>(item.score) * static_cast<double>(item.index + 1U);
        }
        return result;
    }

    [[nodiscard]] ScanResult legacy_scan(
        const agent_memory::Embedding& query,
        const DenseCase& data,
        const agent_memory::VectorSimilarityComputer& similarity,
        const std::vector<float>& inverse_document_norms
    ) {
        const auto query_squared_norm = similarity.squared_norm(query);
        const auto query_inverse_norm = query_squared_norm > 0.0F
            ? 1.0F / std::sqrt(query_squared_norm)
            : 0.0F;
        std::vector<RankedItem> scored;
        scored.reserve(data.documents.size());
        for(std::size_t index = 0; index < data.documents.size(); ++index) {
            const auto dot = similarity.dot_product(query, data.documents[index]);
            const auto score = query_inverse_norm != 0.0F && inverse_document_norms[index] != 0.0F
                ? dot * query_inverse_norm * inverse_document_norms[index]
                : 0.0F;
            scored.push_back(RankedItem{index, score});
        }
        return finish_scan(std::move(scored));
    }

    [[nodiscard]] ScanResult current_scan(
        const agent_memory::Embedding& query,
        const DenseCase& data,
        const std::vector<double>& inverse_document_norms
    ) {
        const auto query_inverse_norm = stable_inverse_norm(query);
        std::vector<RankedItem> scored;
        scored.reserve(data.documents.size());
        for(std::size_t index = 0; index < data.documents.size(); ++index) {
            double dot = 0.0;
            const auto& document = data.documents[index];
            for(std::size_t value = 0; value < query.values.size(); ++value) {
                dot += static_cast<double>(query.values[value])
                    * static_cast<double>(document.values[value]);
            }
            const auto score = query_inverse_norm != 0.0 && inverse_document_norms[index] != 0.0
                ? static_cast<float>(dot * query_inverse_norm * inverse_document_norms[index])
                : 0.0F;
            scored.push_back(RankedItem{index, score});
        }
        return finish_scan(std::move(scored));
    }

    [[nodiscard]] std::vector<ReferenceItem> reference_top_k(
        const agent_memory::Embedding& query,
        const DenseCase& data
    ) {
        std::vector<ReferenceItem> scored;
        scored.reserve(data.documents.size());
        for(std::size_t index = 0; index < data.documents.size(); ++index) {
            scored.push_back(ReferenceItem{
                index,
                double_reference_cosine(query, data.documents[index])
            });
        }
        if(scored.size() > kTopK) {
            std::partial_sort(scored.begin(), scored.begin() + kTopK, scored.end(), better_reference);
            scored.resize(kTopK);
        } else {
            std::sort(scored.begin(), scored.end(), better_reference);
        }
        return scored;
    }

    [[nodiscard]] double percentile(
        const std::vector<double>& values,
        const double fraction
    ) {
        if(values.empty()) {
            return 0.0;
        }
        const auto position = fraction * static_cast<double>(values.size() - 1U);
        const auto lower = static_cast<std::size_t>(position);
        const auto upper = std::min(lower + 1U, values.size() - 1U);
        const auto weight = position - static_cast<double>(lower);
        return values[lower] * (1.0 - weight) + values[upper] * weight;
    }

    [[nodiscard]] TimingSummary summarize(std::vector<double> samples) {
        TimingSummary result;
        result.samples_ms = std::move(samples);
        std::sort(result.samples_ms.begin(), result.samples_ms.end());
        if(result.samples_ms.empty()) {
            return result;
        }
        double total = 0.0;
        for(const auto sample : result.samples_ms) {
            total += sample;
        }
        result.mean_ms = total / static_cast<double>(result.samples_ms.size());
        result.min_ms = result.samples_ms.front();
        result.p50_ms = percentile(result.samples_ms, 0.50);
        result.p95_ms = percentile(result.samples_ms, 0.95);
        result.p99_ms = percentile(result.samples_ms, 0.99);
        result.max_ms = result.samples_ms.back();
        return result;
    }

    [[nodiscard]] std::string cpu_model() {
#if defined(_WIN32)
        const auto* value = std::getenv("PROCESSOR_IDENTIFIER");
        return value == nullptr ? "unavailable" : std::string(value);
#elif defined(__APPLE__)
        char value[256]{};
        std::size_t size = sizeof(value);
        if(sysctlbyname("machdep.cpu.brand_string", value, &size, nullptr, 0) == 0) {
            return std::string(value);
        }
        return "unavailable";
#elif defined(__linux__)
        std::ifstream input("/proc/cpuinfo");
        std::string line;
        while(std::getline(input, line)) {
            constexpr std::string_view prefix = "model name\t: ";
            if(line.rfind(prefix, 0) == 0) {
                return line.substr(prefix.size());
            }
        }
        return "unavailable";
#else
        return "unavailable";
#endif
    }

    [[nodiscard]] std::string compiler_name() {
#if defined(_MSC_VER)
        return "MSVC " + std::to_string(_MSC_VER);
#elif defined(__clang__)
        return std::string("Clang ") + __clang_version__;
#elif defined(__GNUC__)
        return std::string("GCC ") + __VERSION__;
#else
        return "unknown";
#endif
    }

    [[nodiscard]] std::string architecture() {
#if defined(__x86_64__) || defined(_M_X64)
        return "x86_64";
#elif defined(__aarch64__) || defined(_M_ARM64)
        return "arm64";
#elif defined(__i386__) || defined(_M_IX86)
        return "x86";
#else
        return "unknown";
#endif
    }

    [[nodiscard]] Json timing_json(const TimingSummary& summary) {
        return Json{
            {"sample_count", summary.samples_ms.size()},
            {"mean_ms", summary.mean_ms},
            {"min_ms", summary.min_ms},
            {"p50_ms", summary.p50_ms},
            {"p95_ms", summary.p95_ms},
            {"p99_ms", summary.p99_ms},
            {"max_ms", summary.max_ms}
        };
    }

    [[nodiscard]] bool same_top_k(
        const std::vector<RankedItem>& actual,
        const std::vector<ReferenceItem>& expected
    ) noexcept {
        if(actual.size() != expected.size()) {
            return false;
        }
        for(std::size_t index = 0; index < actual.size(); ++index) {
            if(actual[index].index != expected[index].index) {
                return false;
            }
        }
        return true;
    }

    [[nodiscard]] Json run_dimension(const std::size_t dimension) {
        const auto data = make_case(dimension);
        const agent_memory::VectorSimilarityComputer similarity(true);
        std::vector<float> legacy_inverse_norms;
        std::vector<double> current_inverse_norms;
        legacy_inverse_norms.reserve(data.documents.size());
        current_inverse_norms.reserve(data.documents.size());
        for(const auto& document : data.documents) {
            const auto squared_norm = similarity.squared_norm(document);
            legacy_inverse_norms.push_back(
                squared_norm > 0.0F ? 1.0F / std::sqrt(squared_norm) : 0.0F
            );
            current_inverse_norms.push_back(stable_inverse_norm(document));
        }

        volatile double checksum = 0.0;
        for(std::size_t repeat = 0; repeat < kWarmupRepeats; ++repeat) {
            for(const auto& query : data.queries) {
                checksum += legacy_scan(query, data, similarity, legacy_inverse_norms).checksum;
                checksum += current_scan(query, data, current_inverse_norms).checksum;
            }
        }

        std::vector<double> legacy_samples;
        std::vector<double> current_samples;
        legacy_samples.reserve(kMeasuredRepeats * data.queries.size());
        current_samples.reserve(kMeasuredRepeats * data.queries.size());
        bool correct = true;
        double max_current_abs_error = 0.0;
        double max_legacy_abs_error = 0.0;
        for(std::size_t repeat = 0; repeat < kMeasuredRepeats; ++repeat) {
            for(std::size_t query_index = 0; query_index < data.queries.size(); ++query_index) {
                const auto& query = data.queries[query_index];
                const auto reference = reference_top_k(query, data);
                if(repeat == 0) {
                    const auto current = current_scan(query, data, current_inverse_norms);
                    const auto legacy = legacy_scan(query, data, similarity, legacy_inverse_norms);
                    correct = correct && same_top_k(current.items, reference);
                    for(std::size_t index = 0; index < reference.size(); ++index) {
                        max_current_abs_error = std::max(
                            max_current_abs_error,
                            std::abs(static_cast<double>(current.items[index].score) - reference[index].score)
                        );
                        max_legacy_abs_error = std::max(
                            max_legacy_abs_error,
                            std::abs(static_cast<double>(legacy.items[index].score) - reference[index].score)
                        );
                    }
                }

                const bool current_first = (repeat + query_index) % 2U == 0U;
                const auto measure = [&checksum, &query, &data, &similarity,
                                      &legacy_inverse_norms, &current_inverse_norms,
                                      current_first, &legacy_samples, &current_samples]() {
                    const auto started = Clock::now();
                    const auto result = current_first
                        ? current_scan(query, data, current_inverse_norms)
                        : legacy_scan(query, data, similarity, legacy_inverse_norms);
                    const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
                    checksum += result.checksum;
                    (current_first ? current_samples : legacy_samples).push_back(elapsed);
                    const auto other_started = Clock::now();
                    const auto other = current_first
                        ? legacy_scan(query, data, similarity, legacy_inverse_norms)
                        : current_scan(query, data, current_inverse_norms);
                    const auto other_elapsed = std::chrono::duration<double, std::milli>(Clock::now() - other_started).count();
                    checksum += other.checksum;
                    (current_first ? legacy_samples : current_samples).push_back(other_elapsed);
                };
                measure();
            }
        }
        if(!correct) {
            throw std::runtime_error("current double cosine scan disagrees with the double reference top-k");
        }

        const auto legacy = summarize(std::move(legacy_samples));
        const auto current = summarize(std::move(current_samples));
        const auto speedup = current.p50_ms == 0.0 ? 0.0 : legacy.p50_ms / current.p50_ms;
        const auto current_delta_percent = legacy.p50_ms == 0.0
            ? 0.0
            : (current.p50_ms / legacy.p50_ms - 1.0) * 100.0;
        return Json{
            {"dimension", dimension},
            {"document_count", data.documents.size()},
            {"query_count", data.queries.size()},
            {"top_k", kTopK},
            {"corpus_seed", kSeed ^ (static_cast<std::uint64_t>(dimension) << 32U)},
            {"corpus_hash_fnv1a64", [&data] {
                std::ostringstream stream;
                stream << std::hex << std::setfill('0') << std::setw(16) << data.corpus_hash;
                return stream.str();
            }()},
            {"legacy_float_simd", timing_json(legacy)},
            {"current_double_accumulation", timing_json(current)},
            {"legacy_over_current_p50_speedup", speedup},
            {"current_vs_legacy_p50_delta_percent", current_delta_percent},
            {"correctness", Json{
                {"current_matches_double_reference_top_k", correct},
                {"max_current_abs_score_error", max_current_abs_error},
                {"max_legacy_abs_score_error", max_legacy_abs_error}
            }}
        };
    }

} // namespace

int main(int argc, char** argv) {
    const std::string output_path = argc > 1 ? argv[1] : "exact-vector-cosine-benchmark.json";
    try {
        const agent_memory::VectorSimilarityComputer similarity(true);
        Json report{
            {"schema_version", 1},
            {"benchmark_name", "exact_vector_cosine_before_after_622"},
            {"legacy_source_commit", "7ba6979bde75ffe3a483a11d657f850642c633c0"},
            {"current_source_commit", "ee974627819130004be62efc79c9b401bbe29ac2"},
            {"scope", "bounded cosine scan arithmetic; not a production throughput claim"},
            {"timing_semantics", "query-level measured samples; sample_count is not independent-request count and p99 is directional"},
            {"compiler", compiler_name()},
            {"optimization_flags", AGENT_MEMORY_COSINE_BENCH_BUILD_FLAGS},
            {"architecture", architecture()},
            {"optimization_profile",
#if defined(NDEBUG)
                "NDEBUG/release-like"
#else
                "debug-like"
#endif
            },
            {"cpu_model", cpu_model()},
            {"legacy_simd_backend", std::string(agent_memory::vector_similarity_backend_name(similarity.backend()))},
            {"current_cosine_accumulation", "scalar double"},
            {"dimensions", Json::array({384, 768, 1536})},
            {"cases", Json::array({
                run_dimension(384),
                run_dimension(768),
                run_dimension(1536)
            })}
        };
        std::ofstream output(output_path, std::ios::binary);
        if(!output) {
            std::cerr << "cannot open output: " << output_path << '\n';
            return 2;
        }
        output << report.dump(2) << '\n';
        std::cout << report.dump(2) << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "exact-vector-cosine-bench: " << error.what() << '\n';
        return 1;
    }
}
