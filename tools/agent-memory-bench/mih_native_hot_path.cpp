#include <agent_memory.hpp>
#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::size_t kCodeBits = 256;
constexpr std::size_t kWordCount = 4;

#ifndef AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256
#define AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_NATIVE_HOT_PATH_SOURCE_SHA256
#define AGENT_MEMORY_NATIVE_HOT_PATH_SOURCE_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_NATIVE_HOT_PATH_MATERIALIZER_SOURCE_SHA256
#define AGENT_MEMORY_NATIVE_HOT_PATH_MATERIALIZER_SOURCE_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_NATIVE_HOT_PATH_VECTOR_SIMILARITY_SOURCE_SHA256
#define AGENT_MEMORY_NATIVE_HOT_PATH_VECTOR_SIMILARITY_SOURCE_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_NATIVE_HOT_PATH_BINARY_SIGNATURE_SOURCE_SHA256
#define AGENT_MEMORY_NATIVE_HOT_PATH_BINARY_SIGNATURE_SOURCE_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256
#define AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_COMPILER_ID
#define AGENT_MEMORY_EVALUATOR_COMPILER_ID "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_COMPILER_VERSION
#define AGENT_MEMORY_EVALUATOR_COMPILER_VERSION "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_CXX_STANDARD
#define AGENT_MEMORY_EVALUATOR_CXX_STANDARD 0
#endif
#ifndef AGENT_MEMORY_EVALUATOR_CXX_EXTENSIONS
#define AGENT_MEMORY_EVALUATOR_CXX_EXTENSIONS 0
#endif
#ifndef AGENT_MEMORY_EVALUATOR_GENERATOR
#define AGENT_MEMORY_EVALUATOR_GENERATOR "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION
#define AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_SYSTEM_NAME
#define AGENT_MEMORY_EVALUATOR_SYSTEM_NAME "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_SYSTEM_PROCESSOR
#define AGENT_MEMORY_EVALUATOR_SYSTEM_PROCESSOR "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_POINTER_BITS
#define AGENT_MEMORY_EVALUATOR_POINTER_BITS 0
#endif
#ifndef AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256
#define AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256
#define AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256 "unconfigured"
#endif

struct Config final {
    std::filesystem::path input;
    std::size_t query_count = 128;
    std::size_t repeat_count = 5;
    std::uint64_t query_seed = 20260811;
    std::size_t band_count = 16;
    int local_probe_radius = 0;
    int global_radius = 64;
    std::size_t hamming_limit = 512;
    std::vector<std::size_t> exact_rerank_limits{64, 128, 256};
    std::string sha256;
};

struct Input final {
    std::size_t document_count = 0;
    std::size_t query_count = 0;
    std::vector<std::uint64_t> documents;
    std::vector<std::uint64_t> queries;
    std::size_t embedding_dimension = 0;
    std::vector<float> document_vectors;
    std::vector<float> query_vectors;
    std::size_t itq_projection_dimension = 0;
    std::vector<float> query_projections;
    std::vector<float> adc_centroids;
    nlohmann::json manifest;
    std::string manifest_sha256;
};

struct Span final {
    std::size_t first = 0;
    std::size_t last = 0;
};

struct QueryDiagnostics final {
    std::size_t bucket_probes = 0;
    std::size_t posting_visits = 0;
    std::size_t unique_candidates = 0;
    std::size_t candidate_checksum = 0;
};

struct Timings final {
    double probe_enumeration_ms = 0.0;
    double posting_traversal_ms = 0.0;
    double deduplication_ms = 0.0;
    double hamming_ms = 0.0;
    double top_k_ms = 0.0;
    double total_ms = 0.0;
};

[[nodiscard]] double milliseconds(Clock::time_point start, Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - start).count();
}

[[nodiscard]] double median(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    const auto middle = values.size() / 2;
    return values.size() % 2 == 0 ? (values[middle - 1] + values[middle]) / 2.0 : values[middle];
}

[[nodiscard]] nlohmann::json build_environment() {
    return {
        {"configured_environment_sha256", AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256},
        {"compiler_id", AGENT_MEMORY_EVALUATOR_COMPILER_ID},
        {"compiler_version", AGENT_MEMORY_EVALUATOR_COMPILER_VERSION},
        {"cxx_standard", AGENT_MEMORY_EVALUATOR_CXX_STANDARD},
        {"cxx_extensions", AGENT_MEMORY_EVALUATOR_CXX_EXTENSIONS != 0},
        {"generator", AGENT_MEMORY_EVALUATOR_GENERATOR},
        {"build_configuration", AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION},
        {"system_name", AGENT_MEMORY_EVALUATOR_SYSTEM_NAME},
        {"system_processor", AGENT_MEMORY_EVALUATOR_SYSTEM_PROCESSOR},
        {"pointer_bits", AGENT_MEMORY_EVALUATOR_POINTER_BITS},
        {"base_cxx_flags_sha256", AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256},
        {"active_configuration_flags_sha256", AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256},
    };
}

[[nodiscard]] std::string required_sha256(const nlohmann::json& manifest, const char* field) {
    const auto value = manifest.value(field, "");
    if(value.size() != 64 || value.find_first_not_of("0123456789abcdef") != std::string::npos) {
        throw std::runtime_error(std::string("input manifest ") + field + " is invalid");
    }
    return value;
}

[[nodiscard]] std::vector<std::uint64_t> read_words(
    const std::filesystem::path& path,
    std::size_t count,
    const std::string& expected_sha256
) {
    if(agent_memory::sha256_file_hex(path) != expected_sha256) {
        throw std::runtime_error("packed code payload SHA-256 differs");
    }
    std::vector<std::uint64_t> values(count);
    std::ifstream input(path, std::ios::binary);
    input.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(count * sizeof(std::uint64_t)));
    if(input.gcount() != static_cast<std::streamsize>(count * sizeof(std::uint64_t))) {
        throw std::runtime_error("packed code payload is truncated");
    }
    return values;
}

[[nodiscard]] std::vector<float> read_vectors(const std::filesystem::path& path, std::size_t count, const std::string& expected_sha256) {
    if(agent_memory::sha256_file_hex(path) != expected_sha256) throw std::runtime_error("vector payload SHA-256 differs");
    std::vector<float> values(count);
    std::ifstream input(path, std::ios::binary);
    input.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(count * sizeof(float)));
    if(input.gcount() != static_cast<std::streamsize>(count * sizeof(float))) throw std::runtime_error("vector payload is truncated");
    return values;
}

[[nodiscard]] Input load_input(const std::filesystem::path& root) {
    std::ifstream input(root / "manifest.json");
    if(!input) throw std::runtime_error("cannot open native MIH input manifest");
    Input result;
    input >> result.manifest;
    result.manifest_sha256 = agent_memory::sha256_file_hex(root / "manifest.json");
    const auto& manifest = result.manifest;
    if(manifest.value("schema_version", 0) != 1 || manifest.value("family", "") != "mih_storage_benchmark_input_v1" ||
       manifest.value("code_bits", 0) != kCodeBits || manifest.value("word_count", 0) != kWordCount) {
        throw std::runtime_error("native MIH input manifest contract is invalid");
    }
    result.document_count = manifest.at("document_count").get<std::size_t>();
    result.query_count = manifest.at("query_count").get<std::size_t>();
    if(result.document_count == 0 || result.query_count == 0) throw std::runtime_error("native MIH input is empty");
    result.documents = read_words(root / manifest.at("document_codes_file").get<std::string>(), result.document_count * kWordCount, required_sha256(manifest, "document_codes_sha256"));
    result.queries = read_words(root / manifest.at("query_codes_file").get<std::string>(), result.query_count * kWordCount, required_sha256(manifest, "query_codes_sha256"));
    result.embedding_dimension = manifest.value("embedding_dimension", 0U);
    if(result.embedding_dimension == 0) throw std::runtime_error("native MIH input has no dense vector payload");
    result.document_vectors = read_vectors(root / manifest.at("document_vectors_file").get<std::string>(), result.document_count * result.embedding_dimension, required_sha256(manifest, "document_vectors_sha256"));
    result.query_vectors = read_vectors(root / manifest.at("query_vectors_file").get<std::string>(), result.query_count * result.embedding_dimension, required_sha256(manifest, "query_vectors_sha256"));
    result.itq_projection_dimension = manifest.value("itq_projection_dimension", 0U);
    if(result.itq_projection_dimension != kCodeBits) throw std::runtime_error("native MIH input has no compatible ITQ projection payload");
    result.query_projections = read_vectors(root / manifest.at("query_itq_projections_file").get<std::string>(), result.query_count * result.itq_projection_dimension, required_sha256(manifest, "query_itq_projections_sha256"));
    result.adc_centroids = read_vectors(root / manifest.at("binary_adc_centroids_file").get<std::string>(), result.itq_projection_dimension * 2, required_sha256(manifest, "binary_adc_centroids_sha256"));
    return result;
}

[[nodiscard]] std::size_t read_size(const nlohmann::json& value, const char* field, std::size_t fallback) {
    if(!value.contains(field)) return fallback;
    if(!value.at(field).is_number_unsigned()) throw std::invalid_argument(std::string("config ") + field + " is invalid");
    return value.at(field).get<std::size_t>();
}

[[nodiscard]] Config load_config(const std::filesystem::path& path) {
    std::ifstream input(path);
    if(!input) throw std::runtime_error("cannot open native MIH config");
    nlohmann::json value;
    input >> value;
    Config config;
    config.sha256 = agent_memory::sha256_file_hex(path);
    config.input = value.at("input_directory").get<std::string>();
    config.query_count = read_size(value, "query_count", config.query_count);
    config.repeat_count = read_size(value, "repeat_count", config.repeat_count);
    config.query_seed = value.value("query_seed", config.query_seed);
    config.band_count = read_size(value, "band_count", config.band_count);
    config.local_probe_radius = value.value("local_probe_radius", config.local_probe_radius);
    config.global_radius = value.value("global_radius", config.global_radius);
    config.hamming_limit = read_size(value, "hamming_limit", config.hamming_limit);
    if(value.contains("exact_rerank_limits")) config.exact_rerank_limits = value.at("exact_rerank_limits").get<std::vector<std::size_t>>();
    if(config.query_count == 0 || config.repeat_count == 0 || config.band_count == 0 || kCodeBits % config.band_count != 0 ||
       config.hamming_limit == 0 || config.exact_rerank_limits.empty() || config.local_probe_radius < 0 || config.global_radius < -1) {
        throw std::invalid_argument("native MIH config is invalid");
    }
    if(!std::is_sorted(config.exact_rerank_limits.begin(), config.exact_rerank_limits.end()) || config.exact_rerank_limits.back() > config.hamming_limit || config.exact_rerank_limits.front() == 0) throw std::invalid_argument("native MIH exact rerank limits are invalid");
    const auto band_bits = kCodeBits / config.band_count;
    if(band_bits > 16 || config.local_probe_radius > static_cast<int>(band_bits)) {
        throw std::invalid_argument("native MIH band parameters are invalid");
    }
    if(config.global_radius >= 0 && config.global_radius > static_cast<int>(kCodeBits)) {
        throw std::invalid_argument("native MIH global radius is invalid");
    }
    return config;
}

[[nodiscard]] std::uint16_t bucket_key(const std::uint64_t* words, std::size_t offset, std::size_t width) noexcept {
    const auto word = offset / 64;
    const auto shift = offset % 64;
    const auto mask = (std::uint64_t{1} << width) - 1U;
    return static_cast<std::uint16_t>((words[word] >> shift) & mask);
}

[[nodiscard]] std::vector<int> global_schedule(int radius, std::size_t band_count) {
    const auto quotient = radius / static_cast<int>(band_count);
    const auto remainder = static_cast<std::size_t>(radius % static_cast<int>(band_count));
    std::vector<int> result(band_count, quotient - 1);
    for(std::size_t index = 0; index <= remainder; ++index) result[index] = quotient;
    return result;
}

template<class Callback>
void probe_keys(std::uint16_t key, std::size_t width, int radius, Callback&& callback) {
    const auto enumerate = [&](const auto& self, std::size_t first, int remaining, std::uint16_t value) -> void {
        if(remaining == 0) {
            callback(value);
            return;
        }
        for(std::size_t bit = first; bit + static_cast<std::size_t>(remaining) <= width; ++bit) {
            self(self, bit + 1, remaining - 1, static_cast<std::uint16_t>(value ^ (std::uint16_t{1} << bit)));
        }
    };
    for(int distance = 0; distance <= radius; ++distance) enumerate(enumerate, 0, distance, key);
}

class CsrIndex final {
public:
    CsrIndex(const Input& input, std::size_t band_count)
        : m_band_count(band_count),
          m_band_bits(kCodeBits / band_count),
          m_bucket_count(std::size_t{1} << m_band_bits),
          m_offsets(m_band_count * (m_bucket_count + 1), 0) {
        for(std::size_t band = 0; band < m_band_count; ++band) {
            for(std::size_t document = 0; document < input.document_count; ++document) {
                const auto key = bucket_key(input.documents.data() + document * kWordCount, band * m_band_bits, m_band_bits);
                ++m_offsets[band * (m_bucket_count + 1) + key + 1];
            }
        }
        std::size_t cursor = 0;
        for(std::size_t band = 0; band < m_band_count; ++band) {
            auto* offsets = m_offsets.data() + band * (m_bucket_count + 1);
            for(std::size_t key = 0; key < m_bucket_count; ++key) {
                const auto count = offsets[key + 1];
                offsets[key] = cursor;
                cursor += count;
                offsets[key + 1] = cursor;
            }
        }
        m_postings.resize(cursor);
        auto fill = m_offsets;
        for(std::size_t band = 0; band < m_band_count; ++band) {
            for(std::size_t document = 0; document < input.document_count; ++document) {
                const auto key = bucket_key(input.documents.data() + document * kWordCount, band * m_band_bits, m_band_bits);
                m_postings[fill[band * (m_bucket_count + 1) + key]++] = static_cast<std::uint32_t>(document);
            }
        }
    }

    [[nodiscard]] std::vector<Span> spans(const std::uint64_t* query, const std::vector<int>& radii, QueryDiagnostics& diagnostics) const {
        std::vector<Span> result;
        for(std::size_t band = 0; band < m_band_count; ++band) {
            if(radii[band] < 0) continue;
            const auto base = band * (m_bucket_count + 1);
            probe_keys(bucket_key(query, band * m_band_bits, m_band_bits), m_band_bits, radii[band], [&](std::uint16_t key) {
                ++diagnostics.bucket_probes;
                result.push_back({m_offsets[base + key], m_offsets[base + key + 1]});
            });
        }
        return result;
    }

    [[nodiscard]] const std::vector<std::uint32_t>& postings() const noexcept { return m_postings; }
    [[nodiscard]] std::size_t logical_bytes() const noexcept { return m_offsets.size() * sizeof(std::size_t) + m_postings.size() * sizeof(std::uint32_t); }

private:
    std::size_t m_band_count;
    std::size_t m_band_bits;
    std::size_t m_bucket_count;
    std::vector<std::size_t> m_offsets;
    std::vector<std::uint32_t> m_postings;
};

class GenerationDeduplicator final {
public:
    explicit GenerationDeduplicator(std::size_t count) : m_generations(count, 0) {}

    void next_query() {
        if(m_generation == std::numeric_limits<std::uint32_t>::max()) {
            std::fill(m_generations.begin(), m_generations.end(), 0);
            m_generation = 1;
        } else {
            ++m_generation;
        }
    }

    void append_unique(std::uint32_t position, std::vector<std::uint32_t>& output) {
        if(m_generations[position] != m_generation) {
            m_generations[position] = m_generation;
            output.push_back(position);
        }
    }

private:
    std::vector<std::uint32_t> m_generations;
    std::uint32_t m_generation = 0;
};

struct Scored final {
    std::uint32_t position = 0;
    std::size_t distance = 0;
};

struct ExactScored final { std::uint32_t position = 0; float similarity = 0.0F; };
[[nodiscard]] bool more_similar(const ExactScored& left, const ExactScored& right) noexcept { return left.similarity == right.similarity ? left.position < right.position : left.similarity > right.similarity; }
[[nodiscard]] double exact_rerank(const Input& input, const float* query, const std::vector<std::uint32_t>& positions, const agent_memory::VectorSimilarityComputer& computer, std::size_t& checksum) {
    const auto start = Clock::now(); std::vector<ExactScored> scored; scored.reserve(positions.size());
    for(const auto position : positions) scored.push_back({position, computer.dot_product_values(query, input.document_vectors.data() + static_cast<std::size_t>(position) * input.embedding_dimension, input.embedding_dimension)});
    std::sort(scored.begin(), scored.end(), more_similar); for(const auto& value : scored) checksum += static_cast<std::size_t>(value.position) + 1U;
    return milliseconds(start, Clock::now());
}

struct AdcScored final { std::uint32_t position = 0; float distance = 0.0F; };
struct AdcSelection final { double elapsed_ms = 0.0; std::vector<std::uint32_t> positions; };
[[nodiscard]] bool lower_adc_distance(const AdcScored& left, const AdcScored& right) noexcept { return left.distance == right.distance ? left.position < right.position : left.distance < right.distance; }
[[nodiscard]] AdcSelection binary_adc_rerank(const Input& input, const float* query_projection, const std::vector<Scored>& hamming, std::size_t limit, std::size_t& checksum) {
    const auto start = Clock::now(); std::array<std::array<float, 256>, 32> tables{};
    for(std::size_t group = 0; group < tables.size(); ++group) for(std::size_t value = 0; value < tables[group].size(); ++value) for(std::size_t offset = 0; offset < 8; ++offset) { const auto bit = group * 8 + offset; const auto symbol = (value >> offset) & 1U; const auto delta = query_projection[bit] - input.adc_centroids[bit * 2 + symbol]; tables[group][value] += delta * delta; }
    std::vector<AdcScored> scored; scored.reserve(hamming.size());
    for(const auto& candidate : hamming) {
        const auto* code = input.documents.data() + static_cast<std::size_t>(candidate.position) * kWordCount; float distance = 0.0F;
        for(std::size_t group = 0; group < tables.size(); ++group) distance += tables[group][static_cast<std::uint8_t>(code[group / 8] >> ((group % 8) * 8))];
        scored.push_back({candidate.position, distance});
    }
    if(limit < scored.size()) { std::nth_element(scored.begin(), scored.begin() + static_cast<std::ptrdiff_t>(limit), scored.end(), lower_adc_distance); scored.resize(limit); }
    std::sort(scored.begin(), scored.end(), lower_adc_distance); AdcSelection result; result.elapsed_ms = milliseconds(start, Clock::now()); result.positions.reserve(scored.size());
    for(const auto& value : scored) { checksum += static_cast<std::size_t>(value.position) + 1U; result.positions.push_back(value.position); }
    return result;
}

[[nodiscard]] bool closer(const Scored& left, const Scored& right) noexcept {
    return left.distance == right.distance ? left.position < right.position : left.distance < right.distance;
}

[[nodiscard]] std::vector<Scored> top_k(std::vector<Scored> scored, std::size_t limit) {
    const auto stop = std::min(limit, scored.size());
    if(stop < scored.size()) {
        std::nth_element(scored.begin(), scored.begin() + static_cast<std::ptrdiff_t>(stop), scored.end(), closer);
        scored.resize(stop);
    }
    std::sort(scored.begin(), scored.end(), closer);
    return scored;
}

[[nodiscard]] std::size_t top_k_inplace(Scored* scored, std::size_t count, std::size_t limit) {
    const auto stop = std::min(limit, count);
    if(stop < count) {
        std::nth_element(
            scored,
            scored + static_cast<std::ptrdiff_t>(stop),
            scored + static_cast<std::ptrdiff_t>(count),
            closer
        );
    }
    std::sort(scored, scored + static_cast<std::ptrdiff_t>(stop), closer);
    return stop;
}

[[nodiscard]] double top_k_recall(const std::vector<Scored>& actual, const std::vector<Scored>& expected) {
    if(expected.empty()) return 0.0;
    std::vector<std::uint32_t> positions;
    positions.reserve(expected.size());
    for(const auto& value : expected) positions.push_back(value.position);
    std::sort(positions.begin(), positions.end());
    std::size_t matched = 0;
    for(const auto& value : actual) matched += std::binary_search(positions.begin(), positions.end(), value.position) ? 1U : 0U;
    return static_cast<double>(matched) / static_cast<double>(expected.size());
}

[[nodiscard]] std::vector<Scored> score_positions(
    const Input& input,
    const std::uint64_t* query,
    const std::vector<std::uint32_t>& positions,
    const agent_memory::HammingDistanceComputer& computer
) {
    std::vector<Scored> result;
    result.reserve(positions.size());
    for(const auto position : positions) {
        result.push_back({position, computer.distance_words(query, input.documents.data() + static_cast<std::size_t>(position) * kWordCount)});
    }
    return result;
}

void gather_candidate_codes(
    const Input& input,
    const std::vector<std::uint32_t>& positions,
    std::vector<std::uint64_t>& gathered
) {
    for(std::size_t index = 0; index < positions.size(); ++index) {
        std::copy_n(
            input.documents.data() + static_cast<std::size_t>(positions[index]) * kWordCount,
            kWordCount,
            gathered.data() + index * kWordCount
        );
    }
}

void materialize_scored_positions(
    const std::vector<std::uint32_t>& positions,
    const std::vector<std::size_t>& distances,
    std::vector<Scored>& output
) {
    for(std::size_t index = 0; index < positions.size(); ++index) {
        output[index] = {positions[index], distances[index]};
    }
}

enum class HammingCostComponent {
    DirectIndirectScoreBuffer,
    CandidateCodeGather,
    ContiguousHammingDistanceLoop,
    ScoreBufferMaterialization,
    GatherContiguousHammingScoreBuffer,
    ScoreBufferClone,
    TopKInplaceSelectionOnPreparedScores,
    ClonePlusTopKSelection,
};

[[nodiscard]] double measure_hamming_cost_component(
    const Input& input,
    const std::vector<std::size_t>& query_positions,
    const std::vector<std::vector<std::uint32_t>>& candidate_lists,
    const agent_memory::HammingDistanceComputer& computer,
    std::size_t hamming_limit,
    HammingCostComponent component
) {
    if(query_positions.size() != candidate_lists.size()) {
        throw std::runtime_error("native MIH Hamming component candidate grid is incomplete");
    }
    std::size_t maximum_candidates = 0;
    for(const auto& candidates : candidate_lists) maximum_candidates = std::max(maximum_candidates, candidates.size());
    std::vector<std::uint64_t> gathered(maximum_candidates * kWordCount);
    std::vector<std::size_t> distances(maximum_candidates);
    std::vector<Scored> prepared(maximum_candidates);
    std::vector<Scored> selection_scratch(maximum_candidates);
    std::size_t checksum = 0;
    double elapsed_ms = 0.0;
    for(std::size_t query_index = 0; query_index < query_positions.size(); ++query_index) {
        const auto position = query_positions[query_index];
        const auto& candidates = candidate_lists[query_index];
        const auto* query = input.queries.data() + position * kWordCount;
        const auto count = candidates.size();
        switch(component) {
        case HammingCostComponent::DirectIndirectScoreBuffer: {
            const auto start = Clock::now();
            const auto scored = score_positions(input, query, candidates, computer);
            elapsed_ms += milliseconds(start, Clock::now());
            for(const auto& value : scored) checksum += static_cast<std::size_t>(value.position) + value.distance + 1U;
            break;
        }
        case HammingCostComponent::CandidateCodeGather: {
            const auto start = Clock::now();
            gather_candidate_codes(input, candidates, gathered);
            elapsed_ms += milliseconds(start, Clock::now());
            for(std::size_t index = 0; index < count * kWordCount; ++index) checksum += static_cast<std::size_t>(gathered[index]) + 1U;
            break;
        }
        case HammingCostComponent::ContiguousHammingDistanceLoop: {
            gather_candidate_codes(input, candidates, gathered);
            const auto start = Clock::now();
            computer.compute_distances(query, gathered.data(), count, distances.data());
            elapsed_ms += milliseconds(start, Clock::now());
            for(std::size_t index = 0; index < count; ++index) checksum += distances[index] + 1U;
            break;
        }
        case HammingCostComponent::ScoreBufferMaterialization: {
            gather_candidate_codes(input, candidates, gathered);
            computer.compute_distances(query, gathered.data(), count, distances.data());
            const auto start = Clock::now();
            materialize_scored_positions(candidates, distances, prepared);
            elapsed_ms += milliseconds(start, Clock::now());
            for(std::size_t index = 0; index < count; ++index) checksum += static_cast<std::size_t>(prepared[index].position) + prepared[index].distance + 1U;
            break;
        }
        case HammingCostComponent::GatherContiguousHammingScoreBuffer: {
            const auto start = Clock::now();
            gather_candidate_codes(input, candidates, gathered);
            computer.compute_distances(query, gathered.data(), count, distances.data());
            materialize_scored_positions(candidates, distances, prepared);
            elapsed_ms += milliseconds(start, Clock::now());
            for(std::size_t index = 0; index < count; ++index) checksum += static_cast<std::size_t>(prepared[index].position) + prepared[index].distance + 1U;
            break;
        }
        case HammingCostComponent::ScoreBufferClone: {
            gather_candidate_codes(input, candidates, gathered);
            computer.compute_distances(query, gathered.data(), count, distances.data());
            materialize_scored_positions(candidates, distances, prepared);
            const auto start = Clock::now();
            std::copy_n(prepared.data(), count, selection_scratch.data());
            elapsed_ms += milliseconds(start, Clock::now());
            for(std::size_t index = 0; index < count; ++index) checksum += static_cast<std::size_t>(selection_scratch[index].position) + selection_scratch[index].distance + 1U;
            break;
        }
        case HammingCostComponent::TopKInplaceSelectionOnPreparedScores: {
            gather_candidate_codes(input, candidates, gathered);
            computer.compute_distances(query, gathered.data(), count, distances.data());
            materialize_scored_positions(candidates, distances, prepared);
            std::copy_n(prepared.data(), count, selection_scratch.data());
            const auto start = Clock::now();
            const auto selected_count = top_k_inplace(selection_scratch.data(), count, hamming_limit);
            elapsed_ms += milliseconds(start, Clock::now());
            for(std::size_t index = 0; index < selected_count; ++index) checksum += static_cast<std::size_t>(selection_scratch[index].position) + selection_scratch[index].distance + 1U;
            break;
        }
        case HammingCostComponent::ClonePlusTopKSelection: {
            gather_candidate_codes(input, candidates, gathered);
            computer.compute_distances(query, gathered.data(), count, distances.data());
            materialize_scored_positions(candidates, distances, prepared);
            const auto start = Clock::now();
            std::copy_n(prepared.data(), count, selection_scratch.data());
            const auto selected_count = top_k_inplace(selection_scratch.data(), count, hamming_limit);
            elapsed_ms += milliseconds(start, Clock::now());
            for(std::size_t index = 0; index < selected_count; ++index) checksum += static_cast<std::size_t>(selection_scratch[index].position) + selection_scratch[index].distance + 1U;
            break;
        }
        }
    }
    if(checksum == 0) throw std::runtime_error("native MIH Hamming component checksum is invalid");
    return elapsed_ms / static_cast<double>(query_positions.size());
}

struct HammingCostSamples final {
    std::vector<double> direct_indirect_score_buffer;
    std::vector<double> candidate_code_gather;
    std::vector<double> contiguous_hamming_distance_loop;
    std::vector<double> score_buffer_materialization;
    std::vector<double> gather_contiguous_hamming_score_buffer;
    std::vector<double> score_buffer_clone;
    std::vector<double> top_k_inplace_selection_on_prepared_scores;
    std::vector<double> clone_plus_top_k_selection;
};

void append_hamming_cost_samples(
    HammingCostSamples& samples,
    const Input& input,
    const std::vector<std::size_t>& query_positions,
    const std::vector<std::vector<std::uint32_t>>& candidate_lists,
    const agent_memory::HammingDistanceComputer& computer,
    std::size_t hamming_limit,
    std::size_t repeat_count
) {
    const auto run_component = [&](HammingCostComponent component, std::vector<double>& output) {
        static_cast<void>(measure_hamming_cost_component(input, query_positions, candidate_lists, computer, hamming_limit, component));
        for(std::size_t repeat = 0; repeat < repeat_count; ++repeat) {
            output.push_back(measure_hamming_cost_component(input, query_positions, candidate_lists, computer, hamming_limit, component));
        }
    };
    run_component(HammingCostComponent::DirectIndirectScoreBuffer, samples.direct_indirect_score_buffer);
    run_component(HammingCostComponent::CandidateCodeGather, samples.candidate_code_gather);
    run_component(HammingCostComponent::ContiguousHammingDistanceLoop, samples.contiguous_hamming_distance_loop);
    run_component(HammingCostComponent::ScoreBufferMaterialization, samples.score_buffer_materialization);
    run_component(HammingCostComponent::GatherContiguousHammingScoreBuffer, samples.gather_contiguous_hamming_score_buffer);
    run_component(HammingCostComponent::ScoreBufferClone, samples.score_buffer_clone);
    run_component(HammingCostComponent::TopKInplaceSelectionOnPreparedScores, samples.top_k_inplace_selection_on_prepared_scores);
    run_component(HammingCostComponent::ClonePlusTopKSelection, samples.clone_plus_top_k_selection);
}

[[nodiscard]] std::vector<Scored> full_score(
    const Input& input,
    const std::uint64_t* query,
    const agent_memory::HammingDistanceComputer& computer
) {
    std::vector<std::uint32_t> positions(input.document_count);
    std::iota(positions.begin(), positions.end(), 0);
    return score_positions(input, query, positions, computer);
}

[[nodiscard]] std::vector<int> radii_for(const Config& config) {
    if(config.global_radius >= 0) return global_schedule(config.global_radius, config.band_count);
    return std::vector<int>(config.band_count, config.local_probe_radius);
}

void require_guarantee(
    const std::vector<Scored>& full,
    const std::vector<std::uint32_t>& candidates,
    const Config& config
) {
    int bound = -1;
    if(config.global_radius >= 0) bound = config.global_radius;
    if(config.global_radius < 0 && config.band_count == 32 && config.local_probe_radius == 1) bound = 63;
    if(bound < 0) return;
    auto sorted = candidates;
    std::sort(sorted.begin(), sorted.end());
    for(const auto& value : full) {
        if(value.distance <= static_cast<std::size_t>(bound) &&
           !std::binary_search(sorted.begin(), sorted.end(), value.position)) {
            throw std::runtime_error("native MIH candidate union violates its declared guarantee");
        }
    }
}

[[nodiscard]] Timings run_query(
    const CsrIndex& index,
    const Input& input,
    const std::uint64_t* query,
    const std::vector<int>& radii,
    GenerationDeduplicator& deduplicator,
    const agent_memory::HammingDistanceComputer& computer,
    std::size_t hamming_limit,
    QueryDiagnostics& diagnostics,
    std::vector<Scored>* selected = nullptr,
    std::vector<std::uint32_t>* raw_candidates = nullptr
) {
    const auto total_start = Clock::now();
    const auto probes_start = Clock::now();
    const auto spans = index.spans(query, radii, diagnostics);
    const auto probes_end = Clock::now();
    std::vector<std::uint32_t> visited;
    for(const auto& span : spans) diagnostics.posting_visits += span.last - span.first;
    visited.reserve(diagnostics.posting_visits);
    const auto traversal_start = Clock::now();
    for(const auto& span : spans) visited.insert(visited.end(), index.postings().begin() + static_cast<std::ptrdiff_t>(span.first), index.postings().begin() + static_cast<std::ptrdiff_t>(span.last));
    const auto traversal_end = Clock::now();
    std::vector<std::uint32_t> candidates;
    candidates.reserve(visited.size());
    deduplicator.next_query();
    const auto dedup_start = Clock::now();
    for(const auto position : visited) deduplicator.append_unique(position, candidates);
    const auto dedup_end = Clock::now();
    diagnostics.unique_candidates = candidates.size();
    for(const auto position : candidates) diagnostics.candidate_checksum += static_cast<std::size_t>(position) + 1;
    if(raw_candidates) *raw_candidates = candidates;
    const auto hamming_start = Clock::now();
    auto scored = score_positions(input, query, candidates, computer);
    const auto hamming_end = Clock::now();
    const auto top_start = Clock::now();
    auto ordered = top_k(std::move(scored), hamming_limit);
    const auto top_end = Clock::now();
    if(selected) *selected = std::move(ordered);
    return {milliseconds(probes_start, probes_end), milliseconds(traversal_start, traversal_end), milliseconds(dedup_start, dedup_end), milliseconds(hamming_start, hamming_end), milliseconds(top_start, top_end), milliseconds(total_start, top_end)};
}

[[nodiscard]] int self_test() {
    try {
        Input input;
        input.document_count = 4;
        input.query_count = 2;
        input.documents.assign(input.document_count * kWordCount, 0);
        input.documents[4] = 1;
        input.documents[8] = 2;
        input.documents[12] = (std::uint64_t{1} << 63);
        input.queries.assign(input.query_count * kWordCount, 0);
        input.queries[4] = 1;
        input.embedding_dimension = 2;
        input.document_vectors = {1.0F, 0.0F, 0.0F, 1.0F, -1.0F, 0.0F, 0.0F, -1.0F};
        input.query_vectors = {1.0F, 0.0F, 0.0F, 1.0F};
        input.itq_projection_dimension = kCodeBits;
        input.query_projections.assign(input.query_count * kCodeBits, 0.0F);
        input.adc_centroids.resize(kCodeBits * 2);
        for(std::size_t bit = 0; bit < kCodeBits; ++bit) input.adc_centroids[bit * 2 + 1] = 1.0F;
        CsrIndex index(input, 32);
        Config config;
        config.band_count = 32;
        config.global_radius = -1;
        config.local_probe_radius = 1;
        config.hamming_limit = 3;
        const auto computer = agent_memory::HammingDistanceComputer(kWordCount);
        GenerationDeduplicator deduplicator(input.document_count);
        QueryDiagnostics first{};
        std::vector<Scored> first_selected;
        static_cast<void>(run_query(index, input, input.queries.data(), radii_for(config), deduplicator, computer, config.hamming_limit, first, &first_selected));
        if(first.bucket_probes != 32 * 9 || first.unique_candidates != 4 || first_selected.front().position != 0) {
            throw std::runtime_error("32x8 local-radius-one self-test is invalid");
        }
        QueryDiagnostics second{};
        std::vector<Scored> second_selected;
        static_cast<void>(run_query(index, input, input.queries.data() + kWordCount, radii_for(config), deduplicator, computer, config.hamming_limit, second, &second_selected));
        if(second.unique_candidates != 4 || second_selected.front().position != 1) {
            throw std::runtime_error("generation-array dedup retained stale candidates");
        }
        std::size_t rerank_checksum = 0;
        const auto vector_computer = agent_memory::VectorSimilarityComputer(false);
        const std::vector<std::uint32_t> exact_positions{first_selected[0].position, first_selected[1].position, first_selected[2].position};
        if(exact_rerank(input, input.query_vectors.data(), exact_positions, vector_computer, rerank_checksum) < 0.0 || rerank_checksum == 0) {
            throw std::runtime_error("exact rerank self-test is invalid");
        }
        std::size_t adc_checksum = 0;
        const auto adc = binary_adc_rerank(input, input.query_projections.data(), first_selected, 3, adc_checksum);
        if(adc.elapsed_ms < 0.0 || adc.positions.size() != 3 || adc_checksum == 0) {
            throw std::runtime_error("binary ADC rerank self-test is invalid");
        }
        HammingCostSamples cost_samples;
        append_hamming_cost_samples(
            cost_samples,
            input,
            std::vector<std::size_t>{0},
            std::vector<std::vector<std::uint32_t>>{{0, 1, 2, 3}},
            computer,
            3,
            1
        );
        if(cost_samples.direct_indirect_score_buffer.size() != 1 ||
           cost_samples.candidate_code_gather.size() != 1 ||
           cost_samples.contiguous_hamming_distance_loop.size() != 1 ||
           cost_samples.score_buffer_materialization.size() != 1 ||
           cost_samples.gather_contiguous_hamming_score_buffer.size() != 1 ||
           cost_samples.score_buffer_clone.size() != 1 ||
           cost_samples.top_k_inplace_selection_on_prepared_scores.size() != 1 ||
           cost_samples.clone_plus_top_k_selection.size() != 1) {
            throw std::runtime_error("Hamming cost decomposition self-test is invalid");
        }
        const auto full = full_score(input, input.queries.data(), computer);
        auto inplace = full;
        const auto inplace_count = top_k_inplace(inplace.data(), inplace.size(), 3);
        const auto expected_top_k = top_k(full, 3);
        if(inplace_count != expected_top_k.size() || !std::equal(inplace.begin(), inplace.begin() + static_cast<std::ptrdiff_t>(inplace_count), expected_top_k.begin(), [](const Scored& left, const Scored& right) { return left.position == right.position && left.distance == right.distance; })) {
            throw std::runtime_error("in-place top-K selection differs from the vector path");
        }
        std::vector<std::uint32_t> all_positions{0, 1, 2, 3};
        require_guarantee(full, all_positions, config);
        const auto radius_64 = global_schedule(64, 16);
        if(radius_64.size() != 16 || radius_64.front() != 4 ||
           !std::all_of(radius_64.begin() + 1, radius_64.end(), [](int value) { return value == 3; })) {
            throw std::runtime_error("global MIH radius schedule is invalid");
        }
        Input global_input;
        global_input.document_count = 2;
        global_input.query_count = 1;
        global_input.documents.assign(global_input.document_count * kWordCount, 0);
        for(std::size_t band = 0; band < 16; ++band) {
            const auto bit_offset = band * 16;
            global_input.documents[kWordCount + bit_offset / 64] |= std::uint64_t{0xFU} << (bit_offset % 64);
        }
        global_input.queries.assign(kWordCount, 0);
        CsrIndex global_index(global_input, 16);
        Config global_config;
        global_config.band_count = 16;
        global_config.global_radius = 64;
        global_config.hamming_limit = 2;
        QueryDiagnostics global_diagnostics{};
        const auto global_spans = global_index.spans(global_input.queries.data(), radii_for(global_config), global_diagnostics);
        std::vector<std::uint32_t> global_candidates;
        GenerationDeduplicator global_deduplicator(global_input.document_count);
        global_deduplicator.next_query();
        for(const auto& span : global_spans) {
            for(auto position = span.first; position < span.last; ++position) {
                global_deduplicator.append_unique(global_index.postings()[position], global_candidates);
            }
        }
        if(std::find(global_candidates.begin(), global_candidates.end(), 1U) == global_candidates.end()) {
            throw std::runtime_error("64-radius global MIH self-test omitted its boundary document");
        }
        const auto global_full = full_score(global_input, global_input.queries.data(), computer);
        require_guarantee(global_full, global_candidates, global_config);
        QueryDiagnostics radius_three_diagnostics{};
        const auto radius_three_spans = global_index.spans(global_input.queries.data(), std::vector<int>(16, 3), radius_three_diagnostics);
        std::vector<std::uint32_t> radius_three_candidates;
        GenerationDeduplicator radius_three_deduplicator(global_input.document_count);
        radius_three_deduplicator.next_query();
        for(const auto& span : radius_three_spans) {
            for(auto position = span.first; position < span.last; ++position) {
                radius_three_deduplicator.append_unique(global_index.postings()[position], radius_three_candidates);
            }
        }
        if(std::find(radius_three_candidates.begin(), radius_three_candidates.end(), 1U) != radius_three_candidates.end()) {
            throw std::runtime_error("64-radius global MIH boundary self-test is not adversarial");
        }
        std::cout << "native MIH hot-path self-test passed\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "native MIH hot-path self-test failed: " << error.what() << '\n';
        return 1;
    }
}

[[nodiscard]] int run(const Config& config, const std::filesystem::path& report_path) {
    const auto input = load_input(config.input);
    if(config.query_count > input.query_count || config.hamming_limit > input.document_count) throw std::invalid_argument("native MIH query or Hamming limit exceeds input");
    const CsrIndex index(input, config.band_count);
    const auto radii = radii_for(config);
    const auto computer = agent_memory::HammingDistanceComputer(kWordCount);
    std::vector<std::size_t> query_positions(input.query_count);
    std::iota(query_positions.begin(), query_positions.end(), 0);
    std::mt19937_64 random(config.query_seed);
    std::shuffle(query_positions.begin(), query_positions.end(), random);
    query_positions.resize(config.query_count);
    std::vector<double> probes_samples, traversal_samples, dedup_samples, hamming_samples, top_samples, total_samples;
    std::vector<std::vector<double>> exact_samples(config.exact_rerank_limits.size());
    std::vector<std::vector<double>> adc_samples(config.exact_rerank_limits.size());
    const auto vector_computer = agent_memory::VectorSimilarityComputer();
    QueryDiagnostics expected{};
    std::vector<std::vector<Scored>> hamming_shortlists;
    std::vector<std::vector<std::uint32_t>> raw_candidate_lists;
    for(std::size_t repeat = 0; repeat < config.repeat_count; ++repeat) {
        GenerationDeduplicator deduplicator(input.document_count);
        Timings aggregate{};
        QueryDiagnostics aggregate_diagnostics{};
        for(const auto position : query_positions) {
            QueryDiagnostics current{};
            std::vector<Scored> selected;
            std::vector<std::uint32_t> raw_candidates;
            const auto timing = run_query(
                index,
                input,
                input.queries.data() + position * kWordCount,
                radii,
                deduplicator,
                computer,
                config.hamming_limit,
                current,
                &selected,
                repeat == 0 ? &raw_candidates : nullptr
            );
            aggregate.probe_enumeration_ms += timing.probe_enumeration_ms;
            aggregate.posting_traversal_ms += timing.posting_traversal_ms;
            aggregate.deduplication_ms += timing.deduplication_ms;
            aggregate.hamming_ms += timing.hamming_ms;
            aggregate.top_k_ms += timing.top_k_ms;
            aggregate.total_ms += timing.total_ms;
            aggregate_diagnostics.bucket_probes += current.bucket_probes;
            aggregate_diagnostics.posting_visits += current.posting_visits;
            aggregate_diagnostics.unique_candidates += current.unique_candidates;
            aggregate_diagnostics.candidate_checksum += current.candidate_checksum;
            if(repeat == 0) {
                hamming_shortlists.push_back(std::move(selected));
                raw_candidate_lists.push_back(std::move(raw_candidates));
            }
        }
        if(repeat == 0) expected = aggregate_diagnostics;
        else if(expected.bucket_probes != aggregate_diagnostics.bucket_probes || expected.posting_visits != aggregate_diagnostics.posting_visits || expected.unique_candidates != aggregate_diagnostics.unique_candidates || expected.candidate_checksum != aggregate_diagnostics.candidate_checksum) throw std::runtime_error("native MIH warm repeats differ in candidate union");
        const auto divisor = static_cast<double>(config.query_count);
        probes_samples.push_back(aggregate.probe_enumeration_ms / divisor);
        traversal_samples.push_back(aggregate.posting_traversal_ms / divisor);
        dedup_samples.push_back(aggregate.deduplication_ms / divisor);
        hamming_samples.push_back(aggregate.hamming_ms / divisor);
        top_samples.push_back(aggregate.top_k_ms / divisor);
        total_samples.push_back(aggregate.total_ms / divisor);
    }
    if(hamming_shortlists.size() != query_positions.size()) {
        throw std::runtime_error("native MIH Hamming shortlist grid is incomplete");
    }
    HammingCostSamples hamming_cost_samples;
    append_hamming_cost_samples(
        hamming_cost_samples,
        input,
        query_positions,
        raw_candidate_lists,
        computer,
        config.hamming_limit,
        config.repeat_count
    );
    for(std::size_t limit_index = 0; limit_index < config.exact_rerank_limits.size(); ++limit_index) {
        const auto limit = config.exact_rerank_limits[limit_index];
        const auto run_rerank_pass = [&]() {
            double adc_total = 0.0, exact_total = 0.0;
            std::size_t checksum = 0;
            for(std::size_t query_index = 0; query_index < query_positions.size(); ++query_index) {
                const auto position = query_positions[query_index];
                const auto adc = binary_adc_rerank(input, input.query_projections.data() + position * input.itq_projection_dimension, hamming_shortlists[query_index], limit, checksum);
                adc_total += adc.elapsed_ms;
                exact_total += exact_rerank(input, input.query_vectors.data() + position * input.embedding_dimension, adc.positions, vector_computer, checksum);
            }
            if(checksum == 0) throw std::runtime_error("native rerank checksum is invalid");
            return std::pair<double, double>{
                adc_total / static_cast<double>(config.query_count),
                exact_total / static_cast<double>(config.query_count),
            };
        };

        // Give each independently measured K2 stage a full unrecorded warm pass.
        static_cast<void>(run_rerank_pass());
        for(std::size_t repeat = 0; repeat < config.repeat_count; ++repeat) {
            const auto [adc_ms, exact_ms] = run_rerank_pass();
            adc_samples[limit_index].push_back(adc_ms);
            exact_samples[limit_index].push_back(exact_ms);
        }
    }
    GenerationDeduplicator quality_deduplicator(input.document_count);
    double hamming_recall_sum = 0.0;
    for(const auto position : query_positions) {
        QueryDiagnostics diagnostics{};
        std::vector<Scored> actual;
        static_cast<void>(run_query(index, input, input.queries.data() + position * kWordCount, radii, quality_deduplicator, computer, config.hamming_limit, diagnostics, &actual));
        const auto full = full_score(input, input.queries.data() + position * kWordCount, computer);
        std::vector<std::uint32_t> candidates;
        candidates.reserve(actual.size());
        for(const auto& item : actual) candidates.push_back(item.position);
        if(config.global_radius >= 0 || (config.band_count == 32 && config.global_radius < 0 && config.local_probe_radius == 1)) {
            QueryDiagnostics union_diagnostics{};
            const auto spans = index.spans(input.queries.data() + position * kWordCount, radii, union_diagnostics);
            std::vector<std::uint32_t> visited;
            for(const auto& span : spans) visited.insert(visited.end(), index.postings().begin() + static_cast<std::ptrdiff_t>(span.first), index.postings().begin() + static_cast<std::ptrdiff_t>(span.last));
            GenerationDeduplicator union_deduplicator(input.document_count);
            union_deduplicator.next_query();
            std::vector<std::uint32_t> union_positions;
            for(const auto value : visited) union_deduplicator.append_unique(value, union_positions);
            require_guarantee(full, union_positions, config);
        }
        hamming_recall_sum += top_k_recall(actual, top_k(full, config.hamming_limit));
    }
    const auto divisor = static_cast<double>(config.query_count);
    const auto band_bits = kCodeBits / config.band_count;
    const auto posting_bytes = static_cast<double>(expected.posting_visits) * sizeof(std::uint32_t) / divisor;
    const auto hamming_bytes = static_cast<double>(expected.unique_candidates) * kWordCount * sizeof(std::uint64_t) / divisor;
    nlohmann::json report{
        {"schema_version", 2},
        {"family", "mih_native_hot_path_v1"},
        {"input_manifest", input.manifest},
        {"input_manifest_sha256", input.manifest_sha256},
        {"benchmark_config_sha256", config.sha256},
        {"benchmark_source_bundle_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
        {"benchmark_source_files_sha256", {
            {"tools/agent-memory-bench/mih_native_hot_path.cpp", AGENT_MEMORY_NATIVE_HOT_PATH_SOURCE_SHA256},
            {"tools/agent-memory-bench/materialize-mih-storage-input.py", AGENT_MEMORY_NATIVE_HOT_PATH_MATERIALIZER_SOURCE_SHA256},
            {"src/agent_memory/index/VectorSimilarityComputer.cpp", AGENT_MEMORY_NATIVE_HOT_PATH_VECTOR_SIMILARITY_SOURCE_SHA256},
            {"src/agent_memory/index/BinarySignature.cpp", AGENT_MEMORY_NATIVE_HOT_PATH_BINARY_SIGNATURE_SOURCE_SHA256},
        }},
        {"build_environment", build_environment()},
        {"query_count", config.query_count},
        {"query_seed", config.query_seed},
        {"query_selection_algorithm", "std_mt19937_64_shuffle_v1"},
        {"selected_query_positions", query_positions},
        {"repeat_count", config.repeat_count},
        {"band_count", config.band_count},
        {"band_width_bits", band_bits},
        {"local_probe_radius", config.local_probe_radius},
        {"global_radius", config.global_radius},
        {"band_probe_radii", radii},
        {"hamming_limit", config.hamming_limit},
        {"deduplication", "uint32_generation_array_v1"},
        {"hamming_backend", std::string(agent_memory::hamming_distance_backend_name(computer.backend()))},
        {"csr_logical_bytes", index.logical_bytes()},
        {"counters_per_query", {
            {"bucket_probes", static_cast<double>(expected.bucket_probes) / divisor},
            {"posting_visits", static_cast<double>(expected.posting_visits) / divisor},
            {"posting_bytes_touched", posting_bytes},
            {"unique_candidates", static_cast<double>(expected.unique_candidates) / divisor},
            {"candidate_fraction", static_cast<double>(expected.unique_candidates) / divisor / static_cast<double>(input.document_count)},
            {"hamming_distance_evaluations", static_cast<double>(expected.unique_candidates) / divisor},
            {"hamming_code_bytes_touched", hamming_bytes},
        }},
        {"exact_vector_similarity_backend", std::string(agent_memory::vector_similarity_backend_name(vector_computer.backend()))},
        {"timing_ms_per_query_median", {
            {"probe_enumeration", median(probes_samples)},
            {"posting_traversal", median(traversal_samples)},
            {"generation_array_dedup", median(dedup_samples)},
            {"full_hamming_on_candidates", median(hamming_samples)},
            {"top_k_selection", median(top_samples)},
            {"candidate_generator_to_hamming_top_k_total", median(total_samples)},
        }},
        {"timing_ms_per_query_repeat_means", {
            {"probe_enumeration", probes_samples},
            {"posting_traversal", traversal_samples},
            {"generation_array_dedup", dedup_samples},
            {"full_hamming_on_candidates", hamming_samples},
            {"top_k_selection", top_samples},
            {"candidate_generator_to_hamming_top_k_total", total_samples},
            {"binary_adc", nlohmann::json::object()},
            {"exact_e5_rerank", nlohmann::json::object()},
        }},
        {"hamming_candidate_cost_decomposition_ms_per_query_median", {
            {"direct_indirect_score_buffer", median(hamming_cost_samples.direct_indirect_score_buffer)},
            {"candidate_code_gather", median(hamming_cost_samples.candidate_code_gather)},
            {"contiguous_hamming_distance_loop", median(hamming_cost_samples.contiguous_hamming_distance_loop)},
            {"score_buffer_materialization", median(hamming_cost_samples.score_buffer_materialization)},
            {"gather_contiguous_hamming_score_buffer", median(hamming_cost_samples.gather_contiguous_hamming_score_buffer)},
            {"score_buffer_clone", median(hamming_cost_samples.score_buffer_clone)},
            {"top_k_inplace_selection_on_prepared_scores", median(hamming_cost_samples.top_k_inplace_selection_on_prepared_scores)},
            {"clone_plus_top_k_selection", median(hamming_cost_samples.clone_plus_top_k_selection)},
        }},
        {"hamming_candidate_cost_decomposition_ms_per_query_repeat_means", {
            {"direct_indirect_score_buffer", hamming_cost_samples.direct_indirect_score_buffer},
            {"candidate_code_gather", hamming_cost_samples.candidate_code_gather},
            {"contiguous_hamming_distance_loop", hamming_cost_samples.contiguous_hamming_distance_loop},
            {"score_buffer_materialization", hamming_cost_samples.score_buffer_materialization},
            {"gather_contiguous_hamming_score_buffer", hamming_cost_samples.gather_contiguous_hamming_score_buffer},
            {"score_buffer_clone", hamming_cost_samples.score_buffer_clone},
            {"top_k_inplace_selection_on_prepared_scores", hamming_cost_samples.top_k_inplace_selection_on_prepared_scores},
            {"clone_plus_top_k_selection", hamming_cost_samples.clone_plus_top_k_selection},
        }},
        {"exact_rerank_ms_per_query_median", nlohmann::json::object()},
        {"binary_adc_ms_per_query_median", nlohmann::json::object()},
        {"hamming_top_k_recall", hamming_recall_sum / divisor},
        {"timing_scope", "Warm direct-address CSR bucket spans, posting copy, generation-array deduplication, full Hamming over unique candidates, and stable K1 selection are each measured in seven full-query passes. Hamming candidate-cost components use the fixed raw MIH candidate unions from the first MIH pass; each component receives one unrecorded warm full-query pass and seven isolated measured full-query passes. For every K2, one unrecorded full-query warm pass then seven separately timed full-query ADC-to-exact passes run over the fixed Hamming K1 shortlist from the first MIH pass; exact E5 rerank receives the ADC-selected K2 positions. Excludes query encoding, full-corpus oracle, cold-cache I/O, and process-wide memory."},
    };
    for(std::size_t limit_index = 0; limit_index < config.exact_rerank_limits.size(); ++limit_index) {
        const auto key = std::to_string(config.exact_rerank_limits[limit_index]);
        report["binary_adc_ms_per_query_median"][key] = median(adc_samples[limit_index]);
        report["exact_rerank_ms_per_query_median"][key] = median(exact_samples[limit_index]);
        report["timing_ms_per_query_repeat_means"]["binary_adc"][key] = adc_samples[limit_index];
        report["timing_ms_per_query_repeat_means"]["exact_e5_rerank"][key] = exact_samples[limit_index];
    }
    const auto lower_limit = std::find(config.exact_rerank_limits.begin(), config.exact_rerank_limits.end(), 64U);
    const auto upper_limit = std::find(config.exact_rerank_limits.begin(), config.exact_rerank_limits.end(), 256U);
    if(lower_limit != config.exact_rerank_limits.end() && upper_limit != config.exact_rerank_limits.end()) {
        const auto lower_index = static_cast<std::size_t>(std::distance(config.exact_rerank_limits.begin(), lower_limit));
        const auto upper_index = static_cast<std::size_t>(std::distance(config.exact_rerank_limits.begin(), upper_limit));
        std::vector<double> deltas;
        deltas.reserve(config.repeat_count);
        for(std::size_t repeat = 0; repeat < config.repeat_count; ++repeat) deltas.push_back(exact_samples[upper_index][repeat] - exact_samples[lower_index][repeat]);
        const auto [minimum, maximum] = std::minmax_element(deltas.begin(), deltas.end());
        report["exact_rerank_256_minus_64_ms_per_query_aligned_repeat"] = {
            {"repeat_deltas", deltas},
            {"median", median(deltas)},
            {"min", *minimum},
            {"max", *maximum},
            {"spread", *maximum - *minimum},
        };
    }
    std::ofstream output(report_path);
    if(!output) throw std::runtime_error("cannot write native MIH report");
    output << report.dump(2) << '\n';
    std::cout << report.dump(2) << '\n';
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    if(argc == 2 && std::string(argv[1]) == "--self-test") return self_test();
    if(argc != 3) {
        std::cerr << "usage: agent-memory-mih-native-hot-path <config.json> <report.json>\n";
        return 2;
    }
    try {
        return run(load_config(argv[1]), argv[2]);
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
