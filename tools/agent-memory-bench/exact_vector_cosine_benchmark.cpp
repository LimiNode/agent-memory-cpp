#include <agent_memory/embedding/embedding_types.hpp>
#include <agent_memory/index/ExactVectorIndex.hpp>
#include <agent_memory/index/VectorSimilarityComputer.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
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

#if defined(__APPLE__)
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

    constexpr std::array<std::array<unsigned, 3>, 6> kMeasurementOrders{{
        {{0U, 1U, 2U}},
        {{0U, 2U, 1U}},
        {{1U, 0U, 2U}},
        {{1U, 2U, 0U}},
        {{2U, 0U, 1U}},
        {{2U, 1U, 0U}}
    }};

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
        long double score = 0.0L;
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

    [[nodiscard]] long double independent_reference_cosine(
        const agent_memory::Embedding& query,
        const agent_memory::Embedding& document
    ) noexcept {
        if(query.values.size() != document.values.size()) {
            return std::numeric_limits<long double>::quiet_NaN();
        }
        // Keep this oracle independent from both production paths: it uses
        // long-double accumulation and does not call VectorSimilarityComputer
        // or the compensated production helper.
        long double dot = 0.0L;
        long double dot_compensation = 0.0L;
        long double query_norm = 0.0L;
        long double query_norm_compensation = 0.0L;
        long double document_norm = 0.0L;
        long double document_norm_compensation = 0.0L;
        for(std::size_t index = 0; index < query.values.size(); ++index) {
            const auto query_value = static_cast<long double>(query.values[index]);
            const auto document_value = static_cast<long double>(document.values[index]);
            const auto product = query_value * document_value;
            const auto next_dot = dot + product;
            if(std::abs(dot) >= std::abs(product)) {
                dot_compensation += (dot - next_dot) + product;
            } else {
                dot_compensation += (product - next_dot) + dot;
            }
            dot = next_dot;
            const auto query_square = query_value * query_value;
            const auto next_query_norm = query_norm + query_square;
            if(std::abs(query_norm) >= std::abs(query_square)) {
                query_norm_compensation += (query_norm - next_query_norm) + query_square;
            } else {
                query_norm_compensation += (query_square - next_query_norm) + query_norm;
            }
            query_norm = next_query_norm;
            const auto document_square = document_value * document_value;
            const auto next_document_norm = document_norm + document_square;
            if(std::abs(document_norm) >= std::abs(document_square)) {
                document_norm_compensation += (document_norm - next_document_norm) + document_square;
            } else {
                document_norm_compensation += (document_square - next_document_norm) + document_norm;
            }
            document_norm = next_document_norm;
        }
        dot += dot_compensation;
        query_norm += query_norm_compensation;
        document_norm += document_norm_compensation;
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
        const std::vector<double>& inverse_document_norms,
        const bool compensated = false
    ) {
        const auto query_inverse_norm = stable_inverse_norm(query);
        std::vector<RankedItem> scored;
        scored.reserve(data.documents.size());
        for(std::size_t index = 0; index < data.documents.size(); ++index) {
            double dot = 0.0;
            double compensation = 0.0;
            const auto& document = data.documents[index];
            for(std::size_t value = 0; value < query.values.size(); ++value) {
                const auto product = static_cast<double>(query.values[value])
                    * static_cast<double>(document.values[value]);
                if(!compensated) {
                    dot += product;
                } else {
                    const auto next = dot + product;
                    if(std::abs(dot) >= std::abs(product)) {
                        compensation += (dot - next) + product;
                    } else {
                        compensation += (product - next) + dot;
                    }
                    dot = next;
                }
            }
            if(compensated) {
                dot += compensation;
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
                independent_reference_cosine(query, data.documents[index])
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

    [[nodiscard]] std::string benchmark_source_commit() {
        if(const auto* value = std::getenv("GITHUB_SHA"); value != nullptr && *value != '\0') {
            return value;
        }
        if(const auto* value = std::getenv("AGENT_MEMORY_SOURCE_COMMIT"); value != nullptr && *value != '\0') {
            return value;
        }
        return "UNPINNED";
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
            {"raw_samples_ms", summary.samples_ms},
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

    [[nodiscard]] long double max_score_error_by_id(
        const std::vector<RankedItem>& actual,
        const agent_memory::Embedding& query,
        const DenseCase& data
    ) noexcept {
        long double maximum = 0.0L;
        for(const auto& item : actual) {
            if(item.index < data.documents.size()) {
                maximum = std::max(
                    maximum,
                    std::abs(
                        static_cast<long double>(item.score)
                        - independent_reference_cosine(query, data.documents[item.index])
                    )
                );
            }
        }
        return maximum;
    }

    void accumulate_max_score_error(
        long double& maximum,
        const long double observed
    ) noexcept {
        maximum = std::max(maximum, observed);
    }

    [[nodiscard]] Json production_benchmark_json(
        const DenseCase& data,
        const bool enable_simd
    ) {
        agent_memory::ExactVectorIndex index(agent_memory::ExactVectorIndexOptions{
            data.documents.front().dimension(),
            agent_memory::SimilarityMetric::Cosine,
            enable_simd
        });

        agent_memory::Metadata document_metadata;
        document_metadata.set("scope", "documents");
        const auto build_started = Clock::now();
        for(std::size_t document = 0; document < data.documents.size(); ++document) {
            index.upsert(agent_memory::VectorRecord{
                agent_memory::ChunkId{"bench:" + std::to_string(document)},
                data.documents[document],
                document_metadata
            });
        }
        agent_memory::Metadata excluded_metadata;
        excluded_metadata.set("scope", "chat");
        index.upsert(agent_memory::VectorRecord{
            agent_memory::ChunkId{"bench:excluded"},
            data.documents.front(),
            excluded_metadata
        });
        const auto build_ms = std::chrono::duration<double, std::milli>(
            Clock::now() - build_started
        ).count();

        const agent_memory::MetadataFilter scope_filter{"scope", "documents"};
        for(const auto& query : data.queries) {
            (void)index.search(agent_memory::VectorSearchQuery{
                query,
                kTopK,
                {scope_filter}
            });
        }

        std::vector<double> samples;
        samples.reserve(kMeasuredRepeats * data.queries.size());
        bool top_k_correct = true;
        bool tie_order_correct = true;
        long double maximum_error = 0.0L;
        std::size_t excluded_hits = 0;
        volatile double checksum = 0.0;
        for(std::size_t repeat = 0; repeat < kMeasuredRepeats; ++repeat) {
            for(const auto& query : data.queries) {
                const auto started = Clock::now();
                const auto actual = index.search(agent_memory::VectorSearchQuery{
                    query,
                    kTopK,
                    {scope_filter}
                });
                const auto elapsed = std::chrono::duration<double, std::milli>(
                    Clock::now() - started
                ).count();
                samples.push_back(elapsed);
                for(const auto& hit : actual) {
                    checksum += static_cast<double>(hit.score);
                    if(hit.chunk_id.value() == "bench:excluded") {
                        ++excluded_hits;
                    }
                }

                if(repeat == 0U) {
                    const auto expected = reference_top_k(query, data);
                    top_k_correct = top_k_correct && actual.size() == expected.size();
                    for(std::size_t rank = 0; rank < actual.size() && rank < expected.size(); ++rank) {
                        const auto expected_id = "bench:" + std::to_string(expected[rank].index);
                        top_k_correct = top_k_correct
                            && actual[rank].chunk_id.value() == expected_id;
                        if(rank > 0U && actual[rank - 1U].score == actual[rank].score) {
                            tie_order_correct = tie_order_correct
                                && actual[rank - 1U].chunk_id < actual[rank].chunk_id;
                        }
                        if(actual[rank].chunk_id.value().rfind("bench:", 0U) == 0U) {
                            const auto index_text = actual[rank].chunk_id.value().substr(6U);
                            if(index_text != "excluded") {
                                const auto candidate = static_cast<std::size_t>(std::stoull(index_text));
                                maximum_error = std::max(
                                    maximum_error,
                                    std::abs(
                                        static_cast<long double>(actual[rank].score)
                                        - independent_reference_cosine(query, data.documents[candidate])
                                    )
                                );
                            }
                        }
                    }
                }
            }
        }

        const auto timing = summarize(std::move(samples));
        return Json{
            {"simd_enabled", enable_simd},
            {"selected_backend", std::string(agent_memory::vector_similarity_backend_name(index.similarity_backend()))},
            {"record_count", index.size()},
            {"excluded_record_count", 1},
            {"build_ms", build_ms},
            {"search", timing_json(timing)},
            {"correctness", Json{
                {"top_k_identity_parity", top_k_correct},
                {"deterministic_tie_order", tie_order_correct},
                {"max_returned_top_k_abs_score_error", maximum_error},
                {"excluded_record_hits", excluded_hits}
            }},
            {"query_filter", "scope=documents"}
        };
    }

    [[nodiscard]] bool run_self_tests() {
        DenseCase data;
        data.documents = {
            agent_memory::Embedding{{1.0F, 0.0F, 0.0F}},
            agent_memory::Embedding{{1.0F, 1.0e-7F, 0.0F}},
            agent_memory::Embedding{{0.6F, 0.8F, 0.0F}},
            agent_memory::Embedding{{0.8F, -0.6F, 0.0F}}
        };
        data.queries = {
            agent_memory::Embedding{{1.0F, 0.0F, 0.0F}},
            agent_memory::Embedding{{0.6F, 0.8F, 0.0F}}
        };
        const agent_memory::VectorSimilarityComputer similarity(true);
        std::vector<double> norms;
        norms.reserve(data.documents.size());
        for(const auto& document : data.documents) {
            norms.push_back(stable_inverse_norm(document));
        }
        for(const auto& query : data.queries) {
            const auto expected = reference_top_k(query, data);
            const auto actual = current_scan(query, data, norms);
            if(!same_top_k(actual.items, expected)) {
                std::cerr << "self-test: generic top-k mismatch\n";
                return false;
            }
            if(max_score_error_by_id(actual.items, query, data) > 1.0e-6L) {
                std::cerr << "self-test: generic score error\n";
                return false;
            }
        }

        DenseCase adversarial;
        adversarial.documents = {
            agent_memory::Embedding{{0.0F, 0.0F, 0.0F}},
            agent_memory::Embedding{{1.0F, 1.0e-7F, 0.0F}},
            agent_memory::Embedding{{1.0F, -1.0e-7F, 0.0F}},
            agent_memory::Embedding{{std::numeric_limits<float>::denorm_min(),
                                     -std::numeric_limits<float>::denorm_min(),
                                     std::numeric_limits<float>::denorm_min()}},
            agent_memory::Embedding{{1.0e20F, -1.0e20F, 1.0F}}
        };
        adversarial.queries = {
            agent_memory::Embedding{{0.0F, 0.0F, 0.0F}},
            agent_memory::Embedding{{1.0e20F, 1.0e-20F, -1.0F}},
            agent_memory::Embedding{{1.0F, 0.0F, 0.0F}}
        };
        std::vector<double> adversarial_norms;
        adversarial_norms.reserve(adversarial.documents.size());
        for(const auto& document : adversarial.documents) {
            adversarial_norms.push_back(stable_inverse_norm(document));
        }
        for(const auto& query : adversarial.queries) {
            const auto expected = reference_top_k(query, adversarial);
            const auto actual = current_scan(query, adversarial, adversarial_norms);
            const auto corrected = current_scan(query, adversarial, adversarial_norms, true);
            if(!same_top_k(actual.items, expected)
               || !same_top_k(corrected.items, expected)
               || max_score_error_by_id(actual.items, query, adversarial) > 2.0e-6L
               || max_score_error_by_id(corrected.items, query, adversarial) > 2.0e-6L) {
                std::cerr << "self-test: adversarial cosine matrix mismatch\n";
                return false;
            }
        }
        for(const auto dimension : {1U, 2U, 17U}) {
            DenseCase dimensional;
            dimensional.documents = {
                agent_memory::Embedding{std::vector<float>(dimension, 1.0F)},
                agent_memory::Embedding{std::vector<float>(dimension, -1.0F)}
            };
            dimensional.queries = {
                agent_memory::Embedding{std::vector<float>(dimension, 1.0F)}
            };
            std::vector<double> dimensional_norms;
            for(const auto& document : dimensional.documents) {
                dimensional_norms.push_back(stable_inverse_norm(document));
            }
            const auto expected = reference_top_k(dimensional.queries.front(), dimensional);
            if(!same_top_k(
                   current_scan(dimensional.queries.front(), dimensional, dimensional_norms).items,
                   expected
               )) {
                std::cerr << "self-test: dimensional cosine matrix mismatch\n";
                return false;
            }
        }

        DenseCase cancellation_data;
        cancellation_data.documents = {
            agent_memory::Embedding{{-1.0e-10F, 1.0F, -1.0F}},
            agent_memory::Embedding{{1.0e-10F, 1.0F, -1.0F}}
        };
        cancellation_data.queries = {
            agent_memory::Embedding{{1.0e-10F, 1.0F, 1.0F}}
        };
        const auto cancellation_norms = std::vector<double>{
            stable_inverse_norm(cancellation_data.documents[0]),
            stable_inverse_norm(cancellation_data.documents[1])
        };
        const auto cancellation_reference = reference_top_k(
            cancellation_data.queries.front(), cancellation_data
        );
        const auto cancellation_production = current_scan(
            cancellation_data.queries.front(), cancellation_data, cancellation_norms
        );
        const auto cancellation_corrected = current_scan(
            cancellation_data.queries.front(), cancellation_data, cancellation_norms, true
        );
        if(cancellation_reference.front().index != 1U
           || !(cancellation_reference.front().score > 0.0L)
           || !(cancellation_reference.back().score < 0.0L)
           || same_top_k(cancellation_production.items, cancellation_reference)
           || !same_top_k(cancellation_corrected.items, cancellation_reference)
           || !(cancellation_corrected.items.front().score > 0.0F)
           || !(cancellation_corrected.items.back().score < 0.0F)) {
            std::cerr << "self-test: cancellation negative control mismatch ref="
                      << cancellation_reference.front().index << "/"
                      << static_cast<double>(cancellation_reference.front().score)
                      << "," << cancellation_reference.back().index << "/"
                      << static_cast<double>(cancellation_reference.back().score)
                      << " prod=" << cancellation_production.items.front().index
                      << "/" << cancellation_production.items.back().index << "\n";
            return false;
        }

        const auto normalized = independent_reference_cosine(
            data.queries.back(), data.documents[2]
        );
        if(!(std::abs(normalized - 1.0L) < 1.0e-6L)) {
            std::cerr << "self-test: normalized vector mismatch\n";
            return false;
        }
        long double aggregate_error = 0.0L;
        for(const auto observed : {2.0e-6L, 7.0e-6L, 1.0e-7L}) {
            accumulate_max_score_error(aggregate_error, observed);
        }
        if(aggregate_error != 7.0e-6L) {
            std::cerr << "self-test: score error aggregation mismatch\n";
            return false;
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
            for(std::size_t query_index = 0; query_index < data.queries.size(); ++query_index) {
                const auto& query = data.queries[query_index];
                const auto& order = kMeasurementOrders[
                    (repeat * data.queries.size() + query_index) % kMeasurementOrders.size()
                ];
                for(const auto variant : order) {
                    if(variant == 0U) {
                        checksum += legacy_scan(query, data, similarity, legacy_inverse_norms).checksum;
                    } else if(variant == 1U) {
                        checksum += current_scan(query, data, current_inverse_norms).checksum;
                    } else {
                        checksum += current_scan(query, data, current_inverse_norms, true).checksum;
                    }
                }
            }
        }

        std::vector<double> legacy_samples;
        std::vector<double> current_samples;
        std::vector<double> compensated_samples;
        legacy_samples.reserve(kMeasuredRepeats * data.queries.size());
        current_samples.reserve(kMeasuredRepeats * data.queries.size());
        compensated_samples.reserve(kMeasuredRepeats * data.queries.size());
        bool correct = true;
        bool compensated_correct = true;
        long double max_current_abs_error = 0.0L;
        long double max_compensated_abs_error = 0.0L;
        long double max_legacy_abs_error = 0.0L;
        std::array<std::size_t, kMeasurementOrders.size()> order_counts{};
        for(std::size_t repeat = 0; repeat < kMeasuredRepeats; ++repeat) {
            for(std::size_t query_index = 0; query_index < data.queries.size(); ++query_index) {
                const auto& query = data.queries[query_index];
                const auto reference = reference_top_k(query, data);
                if(repeat == 0) {
                    const auto current = current_scan(query, data, current_inverse_norms);
                    const auto compensated = current_scan(query, data, current_inverse_norms, true);
                    const auto legacy = legacy_scan(query, data, similarity, legacy_inverse_norms);
                    correct = correct && same_top_k(current.items, reference);
                    compensated_correct = compensated_correct && same_top_k(compensated.items, reference);
                    accumulate_max_score_error(
                        max_current_abs_error,
                        max_score_error_by_id(current.items, query, data)
                    );
                    accumulate_max_score_error(
                        max_legacy_abs_error,
                        max_score_error_by_id(legacy.items, query, data)
                    );
                    accumulate_max_score_error(
                        max_compensated_abs_error,
                        max_score_error_by_id(compensated.items, query, data)
                    );
                }

                const auto order_index =
                    (repeat * data.queries.size() + query_index) % kMeasurementOrders.size();
                ++order_counts[order_index];
                for(const auto variant : kMeasurementOrders[order_index]) {
                    const auto started = Clock::now();
                    ScanResult result;
                    if(variant == 0U) {
                        result = legacy_scan(query, data, similarity, legacy_inverse_norms);
                    } else if(variant == 1U) {
                        result = current_scan(query, data, current_inverse_norms);
                    } else {
                        result = current_scan(query, data, current_inverse_norms, true);
                    }
                    const auto elapsed = std::chrono::duration<double, std::milli>(
                        Clock::now() - started
                    ).count();
                    checksum += result.checksum;
                    if(variant == 0U) {
                        legacy_samples.push_back(elapsed);
                    } else if(variant == 1U) {
                        current_samples.push_back(elapsed);
                    } else {
                        compensated_samples.push_back(elapsed);
                    }
                }
            }
        }
        if(!correct || !compensated_correct) {
            throw std::runtime_error("cosine scan disagrees with the independent reference top-k");
        }

        const auto legacy = summarize(std::move(legacy_samples));
        const auto current = summarize(std::move(current_samples));
        const auto compensated = summarize(std::move(compensated_samples));
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
            {"current_production_double_accumulation", timing_json(current)},
            {"corrected_compensated_double_accumulation", timing_json(compensated)},
            {"measurement_order_policy", "all six permutations, deterministic query/repeat rotation"},
            {"measurement_order_counts", order_counts},
            {"production_exact_vector_search", Json::array({
                production_benchmark_json(data, false),
                production_benchmark_json(data, true)
            })},
            {"legacy_over_current_p50_speedup", speedup},
            {"current_vs_legacy_p50_delta_percent", current_delta_percent},
            {"correctness", Json{
                {"current_matches_independent_reference_top_k", correct},
                {"corrected_matches_independent_reference_top_k", compensated_correct},
                {"score_error_domain", "top-k candidates matched by candidate ID"},
                {"full_scoring_matrix_measured", false},
                {"adversarial_fixture_matrix", "cancellation, near-ties, mixed scales, subnormals, zero vectors, permutations, dimensional boundaries"},
                {"max_current_abs_score_error", max_current_abs_error},
                {"max_corrected_abs_score_error", max_compensated_abs_error},
                {"max_legacy_abs_score_error", max_legacy_abs_error}
            }}
        };
    }

} // namespace

int main(int argc, char** argv) {
    if(argc > 1 && std::string_view(argv[1]) == "--self-test") {
        if(!run_self_tests()) {
            std::cerr << "exact-vector-cosine-bench self-test: FAIL\n";
            return 1;
        }
        std::cout << "exact-vector-cosine-bench self-test: PASS\n";
        return 0;
    }
    const std::string output_path = argc > 1 ? argv[1] : "exact-vector-cosine-benchmark.json";
    try {
        const agent_memory::VectorSimilarityComputer similarity(true);
        Json report{
            {"schema_version", 3},
            {"benchmark_protocol_version", "exact-vector-cosine-v3-balanced-production-path"},
            {"benchmark_name", "exact_vector_cosine_before_after_622"},
            {"benchmark_source_commit", benchmark_source_commit()},
            {"legacy_source_commit", "7ba6979bde75ffe3a483a11d657f850642c633c0"},
            {"current_source_commit", "ee974627819130004be62efc79c9b401bbe29ac2"},
            {"corrected_algorithm_source_commit", "e2d94c09f9787b53d4454941785217acc6dedbd0"},
            {"corrected_source_policy", "manual reproduction in this benchmark; executable source is benchmark_source_commit"},
            {"scope", "bounded cosine scan arithmetic; not a production throughput claim"},
            {"timing_semantics", "measured query-level sample distribution; sample_count is not independent-request count and p99 is directional, not a stability guarantee; kernel-only and production search timings are separate"},
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
            {"current_cosine_accumulation", "current production scalar double (sequential accumulation)"},
            {"corrected_cosine_accumulation", "Neumaier-compensated scalar double from the cancellation corrective"},
            {"independent_reference", "compensated long-double-style accumulation with independent norm calculation"},
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
