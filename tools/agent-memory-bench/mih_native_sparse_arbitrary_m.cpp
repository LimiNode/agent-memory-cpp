#include <agent_memory.hpp>

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
#include <memory>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifdef AGENT_MEMORY_ENABLE_HNSW_BENCHMARK
#include <hnswlib/hnswlib.h>
#endif

namespace {

constexpr std::size_t kCodeBits = 256;
constexpr std::size_t kWordCount = kCodeBits / 64;
using Clock = std::chrono::steady_clock;

#ifndef AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256
#define AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_SOURCE_SHA256
#define AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_SOURCE_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_MATERIALIZER_SOURCE_SHA256
#define AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_MATERIALIZER_SOURCE_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_VECTOR_SIMILARITY_SOURCE_SHA256
#define AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_VECTOR_SIMILARITY_SOURCE_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_BINARY_SIGNATURE_SOURCE_SHA256
#define AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_BINARY_SIGNATURE_SOURCE_SHA256 "unconfigured"
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
#ifndef AGENT_MEMORY_HNSWLIB_REVISION
#define AGENT_MEMORY_HNSWLIB_REVISION "unavailable"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256
#define AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256
#define AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256 "unconfigured"
#endif

struct Config final {
    std::filesystem::path input_directory;
    std::string backend = "mih";
    std::string mih_search_mode = "fixed_r56";
    std::vector<std::size_t> band_widths;
    std::vector<int> local_radii;
    std::vector<std::size_t> locator_bit_positions;
    std::size_t global_exact_max_cover_radius = kCodeBits;
    std::size_t query_count = 0;
    std::size_t warmup_count = 1;
    std::size_t repeat_count = 0;
    std::size_t hamming_limit = 768;
    std::size_t adc_limit = 256;
    std::size_t exact_limit = 256;
    std::uint64_t query_seed = 20260815;
    std::size_t hnsw_connectivity = 16;
    std::size_t hnsw_ef_construction = 200;
    std::size_t hnsw_ef_search = 768;
    std::uint64_t hnsw_seed = 20260815;
    std::filesystem::path shortlist_output;
    std::filesystem::path candidate_diagnostic_output;
    std::filesystem::path global_exact_certificate_output;
    std::string directory_mode = "sorted_lower_bound";
    std::string deduplication_mode = "two_pass_generation_array";
    std::string sha256;
};

struct Input final {
    std::size_t document_count = 0;
    std::size_t query_count = 0;
    std::size_t embedding_dimension = 0;
    std::size_t itq_projection_dimension = 0;
    std::vector<std::uint64_t> documents;
    std::vector<std::uint64_t> queries;
    std::vector<float> document_vectors;
    std::vector<float> query_vectors;
    std::vector<float> query_projections;
    std::vector<float> adc_centroids;
    nlohmann::json manifest;
    std::string manifest_sha256;
};

struct Band final { std::size_t offset = 0; std::size_t width = 0; };
struct Entry final { std::uint32_t key = 0; std::uint32_t position = 0; };
struct Span final { const std::uint32_t* values = nullptr; std::size_t first = 0; std::size_t last = 0; };
struct KeyProbe final { std::size_t band = 0; std::uint32_t key = 0; };
struct HashSlot final { std::uint32_t key = 0; std::uint32_t index = std::numeric_limits<std::uint32_t>::max(); };
struct Scored final { std::uint32_t position = 0; std::size_t distance = 0; };
struct AdcScored final { std::uint32_t position = 0; float distance = 0.0F; };
struct ExactScored final { std::uint32_t position = 0; float similarity = 0.0F; };

struct Diagnostics final {
    std::size_t probes = 0;
    std::size_t non_empty_probes = 0;
    std::size_t empty_probes = 0;
    std::size_t posting_visits = 0;
    std::size_t unique_candidates = 0;
    std::uint64_t candidate_checksum = 0;
    std::uint64_t shortlist_checksum = 0;
    std::size_t global_exact_cover_radius_sum = 0;
    std::size_t global_exact_stop_count = 0;
    std::vector<std::uint32_t> posting_lengths;
};

struct Timings final {
    double key_enumeration_ms = 0.0;
    double bucket_lookup_ms = 0.0;
    double posting_traversal_ms = 0.0;
    double deduplication_ms = 0.0;
    double hamming_ms = 0.0;
    double top_k_ms = 0.0;
    double candidate_generator_total_ms = 0.0;
};

struct GlobalExactCertificate final {
    std::size_t covered_radius = 0;
    std::size_t kth_distance = 0;
    bool strict_unseen_lower_bound_proved = false;
};

struct QueryResult final { Timings timings; std::vector<Scored> shortlist; GlobalExactCertificate global_exact; };

struct CandidateDiagnostic final {
    std::size_t candidate_union_size = 0;
    std::size_t global_fixed_r56_count = 0;
    std::size_t candidate_union_fixed_r56_count = 0;
    std::size_t exact_hamming_top_k_fixed_r56_count = 0;
    std::size_t candidate_union_exact_hamming_top_k_overlap = 0;
    std::size_t mih_shortlist_fixed_r56_count = 0;
    std::size_t mih_shortlist_exact_hamming_top_k_overlap = 0;
    std::size_t exact_hamming_top_k_max_distance = 0;
    std::array<std::size_t, 6> exact_hamming_distances_at_k{};
    std::string raw_candidate_sequence_sha256;
    std::string raw_candidate_set_sha256;
    std::string hamming_shortlist_sequence_sha256;
};

struct QueryWorkspace final {
    std::vector<KeyProbe> probes;
    std::vector<Span> spans;
    std::vector<std::uint32_t> visited;
    std::vector<std::uint32_t> candidates;
    std::vector<Scored> scored;

    void clear() {
        probes.clear(); spans.clear(); visited.clear(); candidates.clear(); scored.clear();
    }
};

enum class DirectoryMode { SortedLowerBound, FlatOpenAddress };
enum class DeduplicationMode { TwoPassGenerationArray, StreamingGenerationArray };

[[nodiscard]] DirectoryMode directory_mode(const std::string& value) {
    if(value == "sorted_lower_bound") return DirectoryMode::SortedLowerBound;
    if(value == "flat_open_address") return DirectoryMode::FlatOpenAddress;
    throw std::invalid_argument("native sparse MIH directory mode is invalid");
}

[[nodiscard]] DeduplicationMode deduplication_mode(const std::string& value) {
    if(value == "two_pass_generation_array") return DeduplicationMode::TwoPassGenerationArray;
    if(value == "streaming_generation_array") return DeduplicationMode::StreamingGenerationArray;
    throw std::invalid_argument("native sparse MIH deduplication mode is invalid");
}

void validate_config(const Config& value) {
    const auto locator_code_bits = value.locator_bit_positions.empty() ? kCodeBits : value.locator_bit_positions.size();
    if((value.backend != "mih" && value.backend != "flat" && value.backend != "hnsw") || value.band_widths.empty() || value.query_count == 0 || value.repeat_count == 0 || value.hamming_limit == 0 || value.adc_limit == 0 || value.exact_limit == 0 || value.adc_limit > value.hamming_limit || value.exact_limit > value.adc_limit || std::accumulate(value.band_widths.begin(), value.band_widths.end(), std::size_t{0}) != locator_code_bits) throw std::invalid_argument("native sparse MIH config is invalid");
    for(const auto width : value.band_widths) if(width == 0 || width > 32) throw std::invalid_argument("native sparse MIH band is invalid");
    if(value.mih_search_mode != "fixed_r56" && value.mih_search_mode != "global_exact" && value.mih_search_mode != "approximate_locator") throw std::invalid_argument("native sparse MIH search mode is invalid");
    if(!value.locator_bit_positions.empty()) {
        if(value.locator_bit_positions.size() > kCodeBits || !std::is_sorted(value.locator_bit_positions.begin(), value.locator_bit_positions.end()) || std::adjacent_find(value.locator_bit_positions.begin(), value.locator_bit_positions.end()) != value.locator_bit_positions.end() || value.locator_bit_positions.back() >= kCodeBits) throw std::invalid_argument("native sparse MIH locator bit positions are invalid");
    }
    if(value.mih_search_mode == "fixed_r56") {
        if(value.band_widths.size() != value.local_radii.size()) throw std::invalid_argument("native sparse MIH schedule is invalid");
        for(std::size_t index = 0; index < value.band_widths.size(); ++index) if(value.local_radii[index] < 0 || value.local_radii[index] > static_cast<int>(value.band_widths[index])) throw std::invalid_argument("native sparse MIH schedule is invalid");
        if(std::accumulate(value.local_radii.begin(), value.local_radii.end(), std::size_t{0}, [](const std::size_t total, const int radius) { return total + static_cast<std::size_t>(radius) + 1U; }) < 57U) throw std::invalid_argument("native sparse MIH schedule does not preserve fixed-r56 inclusion");
    } else if(value.mih_search_mode == "global_exact" && (value.backend != "mih" || !value.locator_bit_positions.empty() || !value.local_radii.empty() || value.global_exact_max_cover_radius != kCodeBits)) {
        throw std::invalid_argument("native global exact MIH config is invalid");
    } else if(value.mih_search_mode == "approximate_locator" && (value.backend != "mih" || value.locator_bit_positions.empty() || value.band_widths.size() != value.local_radii.size())) {
        throw std::invalid_argument("native approximate locator MIH config is invalid");
    } else if(value.mih_search_mode == "approximate_locator") {
        for(std::size_t index = 0; index < value.band_widths.size(); ++index) if(value.local_radii[index] < 0 || value.local_radii[index] > static_cast<int>(value.band_widths[index])) throw std::invalid_argument("native approximate locator MIH schedule is invalid");
    }
    if(value.backend == "hnsw" && (value.hnsw_connectivity == 0 || value.hnsw_ef_construction < value.hnsw_connectivity || value.hnsw_ef_search < value.hamming_limit)) throw std::invalid_argument("native HNSW configuration is invalid");
    if(!value.candidate_diagnostic_output.empty() && (value.backend != "mih" || value.mih_search_mode != "fixed_r56")) throw std::invalid_argument("native fixed-r56 candidate diagnostic requires fixed-r56 MIH");
    if(!value.global_exact_certificate_output.empty() && (value.backend != "mih" || value.mih_search_mode != "global_exact")) throw std::invalid_argument("native global exact certificate requires global-exact MIH");
    static_cast<void>(directory_mode(value.directory_mode));
    static_cast<void>(deduplication_mode(value.deduplication_mode));
}

[[nodiscard]] double milliseconds(const Clock::time_point start, const Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - start).count();
}

void append_u32_le(std::vector<std::uint8_t>& output, const std::uint32_t value) {
    for(std::size_t shift = 0; shift < 32U; shift += 8U) output.push_back(static_cast<std::uint8_t>((value >> shift) & 0xFFU));
}

void append_u64_le(std::vector<std::uint8_t>& output, const std::uint64_t value) {
    for(std::size_t shift = 0; shift < 64U; shift += 8U) output.push_back(static_cast<std::uint8_t>((value >> shift) & 0xFFU));
}

[[nodiscard]] std::string position_sequence_sha256(const std::uint32_t query_position, const std::vector<std::uint32_t>& positions) {
    std::vector<std::uint8_t> encoded;
    encoded.reserve(12U + positions.size() * 4U);
    append_u32_le(encoded, query_position);
    append_u64_le(encoded, positions.size());
    for(const auto position : positions) append_u32_le(encoded, position);
    return agent_memory::sha256_bytes_hex(encoded);
}

[[nodiscard]] std::uint64_t parse_hex_u64(const std::string& value) {
    if(value.size() != 16U || value.find_first_not_of("0123456789abcdef") != std::string::npos) throw std::invalid_argument("native global exact MIH fixture seed is invalid");
    return static_cast<std::uint64_t>(std::stoull(value, nullptr, 16));
}

[[nodiscard]] std::uint64_t splitmix64_next(std::uint64_t& state) noexcept {
    state += 0x9e3779b97f4a7c15ULL;
    auto value = state;
    value = (value ^ (value >> 30U)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27U)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31U);
}

[[nodiscard]] double percentile(std::vector<double> values, const double fraction) {
    if(values.empty() || fraction < 0.0 || fraction > 1.0) throw std::invalid_argument("native sparse MIH percentile is invalid");
    std::sort(values.begin(), values.end());
    const auto index = static_cast<std::size_t>(std::ceil(fraction * static_cast<double>(values.size()))) - 1U;
    return values[std::min(index, values.size() - 1U)];
}

[[nodiscard]] nlohmann::json percentiles(const std::vector<double>& values) {
    return {{"p50", percentile(values, 0.50)}, {"p95", percentile(values, 0.95)}, {"p99", percentile(values, 0.99)}};
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

[[nodiscard]] std::string required_sha256(const nlohmann::json& value, const char* field) {
    const auto result = value.value(field, "");
    if(result.size() != 64 || result.find_first_not_of("0123456789abcdef") != std::string::npos) throw std::runtime_error(std::string("input manifest ") + field + " is invalid");
    return result;
}

template<class Value>
[[nodiscard]] std::vector<Value> read_values(const std::filesystem::path& path, const std::size_t count, const std::string& expected_sha256, const char* description) {
    if(agent_memory::sha256_file_hex(path) != expected_sha256) throw std::runtime_error(std::string("native sparse MIH ") + description + " SHA-256 differs");
    std::vector<Value> values(count);
    std::ifstream input(path, std::ios::binary);
    input.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(count * sizeof(Value)));
    if(input.gcount() != static_cast<std::streamsize>(count * sizeof(Value))) throw std::runtime_error(std::string("native sparse MIH ") + description + " is truncated");
    return values;
}

[[nodiscard]] Input load_input(const std::filesystem::path& root) {
    std::ifstream stream(root / "manifest.json");
    if(!stream) throw std::runtime_error("cannot open native sparse MIH input manifest");
    Input result; stream >> result.manifest;
    result.manifest_sha256 = agent_memory::sha256_file_hex(root / "manifest.json");
    const auto& manifest = result.manifest;
    if(manifest.value("schema_version", 0) != 1 || manifest.value("family", "") != "mih_storage_benchmark_input_v1" || manifest.value("code_bits", 0) != kCodeBits || manifest.value("word_count", 0) != kWordCount) throw std::runtime_error("native sparse MIH input contract is invalid");
    result.document_count = manifest.at("document_count").get<std::size_t>();
    result.query_count = manifest.at("query_count").get<std::size_t>();
    if(result.document_count == 0 || result.query_count == 0 || result.document_count > std::numeric_limits<std::uint32_t>::max()) throw std::runtime_error("native sparse MIH input cardinality is invalid");
    result.documents = read_values<std::uint64_t>(root / manifest.at("document_codes_file").get<std::string>(), result.document_count * kWordCount, required_sha256(manifest, "document_codes_sha256"), "code payload");
    result.queries = read_values<std::uint64_t>(root / manifest.at("query_codes_file").get<std::string>(), result.query_count * kWordCount, required_sha256(manifest, "query_codes_sha256"), "code payload");
    result.embedding_dimension = manifest.value("embedding_dimension", 0U);
    result.itq_projection_dimension = manifest.value("itq_projection_dimension", 0U);
    if(result.embedding_dimension == 0 || result.itq_projection_dimension != kCodeBits) throw std::runtime_error("native sparse MIH dense input contract is invalid");
    result.document_vectors = read_values<float>(root / manifest.at("document_vectors_file").get<std::string>(), result.document_count * result.embedding_dimension, required_sha256(manifest, "document_vectors_sha256"), "document vector payload");
    result.query_vectors = read_values<float>(root / manifest.at("query_vectors_file").get<std::string>(), result.query_count * result.embedding_dimension, required_sha256(manifest, "query_vectors_sha256"), "query vector payload");
    result.query_projections = read_values<float>(root / manifest.at("query_itq_projections_file").get<std::string>(), result.query_count * kCodeBits, required_sha256(manifest, "query_itq_projections_sha256"), "query projection payload");
    result.adc_centroids = read_values<float>(root / manifest.at("binary_adc_centroids_file").get<std::string>(), kCodeBits * 2U, required_sha256(manifest, "binary_adc_centroids_sha256"), "ADC centroid payload");
    return result;
}

[[nodiscard]] Config load_config(const std::filesystem::path& path) {
    std::ifstream stream(path); if(!stream) throw std::runtime_error("cannot open native sparse MIH config");
    nlohmann::json value; stream >> value;
    Config result;
    result.sha256 = agent_memory::sha256_file_hex(path);
    result.input_directory = value.at("input_directory").get<std::string>();
    result.backend = value.value("backend", result.backend);
    result.mih_search_mode = value.value("mih_search_mode", result.mih_search_mode);
    result.band_widths = value.at("band_widths").get<std::vector<std::size_t>>();
    result.local_radii = value.value("local_radii", std::vector<int>{});
    result.locator_bit_positions = value.value("locator_bit_positions", std::vector<std::size_t>{});
    result.global_exact_max_cover_radius = value.value("global_exact_max_cover_radius", result.global_exact_max_cover_radius);
    result.query_count = value.at("query_count").get<std::size_t>();
    result.warmup_count = value.value("warmup_count", result.warmup_count);
    result.repeat_count = value.at("repeat_count").get<std::size_t>();
    result.hamming_limit = value.value("hamming_limit", result.hamming_limit);
    result.adc_limit = value.value("adc_limit", result.adc_limit);
    result.exact_limit = value.value("exact_limit", result.exact_limit);
    result.query_seed = value.value("query_seed", result.query_seed);
    result.hnsw_connectivity = value.value("hnsw_connectivity", result.hnsw_connectivity);
    result.hnsw_ef_construction = value.value("hnsw_ef_construction", result.hnsw_ef_construction);
    result.hnsw_ef_search = value.value("hnsw_ef_search", result.hnsw_ef_search);
    result.hnsw_seed = value.value("hnsw_seed", result.hnsw_seed);
    result.shortlist_output = value.value("shortlist_output", std::string{});
    result.candidate_diagnostic_output = value.value("candidate_diagnostic_output", std::string{});
    result.global_exact_certificate_output = value.value("global_exact_certificate_output", std::string{});
    result.directory_mode = value.value("directory_mode", result.directory_mode);
    result.deduplication_mode = value.value("deduplication_mode", result.deduplication_mode);
    validate_config(result);
    return result;
}

[[nodiscard]] std::uint32_t extract_key(const std::uint64_t* code, const Band& band) {
    if(band.width == 0 || band.width > 32 || band.offset + band.width > kCodeBits) throw std::invalid_argument("native sparse MIH band is invalid");
    const auto word = band.offset / 64U;
    const auto shift = band.offset % 64U;
    std::uint64_t value = code[word] >> shift;
    if(shift + band.width > 64U) value |= code[word + 1U] << (64U - shift);
    const auto mask = band.width == 32U ? std::numeric_limits<std::uint32_t>::max() : (std::uint32_t{1} << band.width) - 1U;
    return static_cast<std::uint32_t>(value) & mask;
}

[[nodiscard]] std::vector<Band> make_bands(const std::vector<std::size_t>& widths) {
    std::vector<Band> result; result.reserve(widths.size());
    std::size_t offset = 0;
    for(const auto width : widths) { result.push_back({offset, width}); offset += width; }
    return result;
}

struct Directory final {
    std::vector<std::uint32_t> keys;
    std::vector<std::uint32_t> offsets;
    std::vector<std::uint32_t> postings;
    std::vector<HashSlot> hash_slots;
};

[[nodiscard]] std::size_t flat_hash_capacity(const std::size_t key_count) {
    std::size_t capacity = 1;
    const auto minimum = key_count + std::max<std::size_t>(1U, key_count / 2U);
    while(capacity < minimum) {
        if(capacity > std::numeric_limits<std::size_t>::max() / 2U) throw std::overflow_error("native sparse MIH flat directory capacity overflows");
        capacity <<= 1U;
    }
    return capacity;
}

[[nodiscard]] std::size_t flat_hash_start(const std::uint32_t key, const std::size_t mask) noexcept {
    return (static_cast<std::size_t>(key) * 2654435761U) & mask;
}

class SparseIndex final {
public:
    SparseIndex(const std::vector<std::uint64_t>& codes, const std::size_t document_count, std::vector<Band> bands, const DirectoryMode mode, const std::size_t code_bits = kCodeBits)
        : m_bands(std::move(bands)), m_mode(mode), m_code_bits(code_bits), m_directories(m_bands.size()) {
        if(document_count == 0 || codes.size() != document_count * kWordCount || m_bands.empty()) throw std::invalid_argument("native sparse MIH input is invalid");
        std::size_t expected_offset = 0;
        for(const auto& band : m_bands) { if(band.offset != expected_offset || band.width == 0 || band.width > 32) throw std::invalid_argument("native sparse MIH band partition is invalid"); expected_offset += band.width; }
        if(expected_offset != m_code_bits || m_code_bits == 0 || m_code_bits > kCodeBits) throw std::invalid_argument("native sparse MIH band coverage differs");
        for(std::size_t band_index = 0; band_index < m_bands.size(); ++band_index) {
            std::vector<Entry> entries; entries.reserve(document_count);
            for(std::size_t position = 0; position < document_count; ++position) entries.push_back({extract_key(codes.data() + position * kWordCount, m_bands[band_index]), static_cast<std::uint32_t>(position)});
            std::sort(entries.begin(), entries.end(), [](const Entry& left, const Entry& right) { return left.key == right.key ? left.position < right.position : left.key < right.key; });
            auto& directory = m_directories[band_index]; directory.keys.reserve(document_count); directory.offsets.reserve(document_count + 1U); directory.postings.reserve(document_count);
            for(const auto& entry : entries) { if(directory.keys.empty() || directory.keys.back() != entry.key) { directory.keys.push_back(entry.key); directory.offsets.push_back(static_cast<std::uint32_t>(directory.postings.size())); } directory.postings.push_back(entry.position); }
            directory.offsets.push_back(static_cast<std::uint32_t>(directory.postings.size()));
            if(m_mode == DirectoryMode::FlatOpenAddress) {
                directory.hash_slots.assign(flat_hash_capacity(directory.keys.size()), {});
                const auto mask = directory.hash_slots.size() - 1U;
                for(std::size_t index = 0; index < directory.keys.size(); ++index) {
                    auto slot = flat_hash_start(directory.keys[index], mask);
                    bool inserted = false;
                    for(std::size_t probes = 0; probes < directory.hash_slots.size(); ++probes) {
                        if(directory.hash_slots[slot].index == std::numeric_limits<std::uint32_t>::max()) {
                            directory.hash_slots[slot] = {directory.keys[index], static_cast<std::uint32_t>(index)};
                            inserted = true;
                            break;
                        }
                        slot = (slot + 1U) & mask;
                    }
                    if(!inserted) throw std::runtime_error("native sparse MIH flat directory has no empty slot");
                }
                directory.keys.clear();
                directory.keys.shrink_to_fit();
            }
        }
    }

    [[nodiscard]] bool find(const std::size_t band_index, const std::uint32_t key, Span& result) const {
        const auto& directory = m_directories.at(band_index);
        std::size_t index = 0;
        if(m_mode == DirectoryMode::SortedLowerBound) {
            const auto found = std::lower_bound(directory.keys.begin(), directory.keys.end(), key);
            if(found == directory.keys.end() || *found != key) return false;
            index = static_cast<std::size_t>(found - directory.keys.begin());
        } else {
            const auto mask = directory.hash_slots.size() - 1U;
            auto slot = flat_hash_start(key, mask);
            for(std::size_t probes = 0; probes < directory.hash_slots.size(); ++probes) {
                if(directory.hash_slots[slot].index == std::numeric_limits<std::uint32_t>::max()) return false;
                if(directory.hash_slots[slot].key == key) { index = directory.hash_slots[slot].index; break; }
                slot = (slot + 1U) & mask;
            }
            if(directory.hash_slots[slot].index != std::numeric_limits<std::uint32_t>::max() && directory.hash_slots[slot].key != key) return false;
        }
        result = {directory.postings.data(), directory.offsets[index], directory.offsets[index + 1U]};
        return true;
    }

    [[nodiscard]] std::size_t logical_bytes() const noexcept {
        std::size_t result = 0;
        for(const auto& directory : m_directories) result += directory.keys.size() * sizeof(std::uint32_t) + directory.hash_slots.size() * sizeof(HashSlot) + directory.offsets.size() * sizeof(std::uint32_t) + directory.postings.size() * sizeof(std::uint32_t);
        return result;
    }

    [[nodiscard]] nlohmann::json logical_byte_breakdown() const {
        std::size_t keys = 0, slots = 0, offsets = 0, postings = 0;
        for(const auto& directory : m_directories) { keys += directory.keys.size() * sizeof(std::uint32_t); slots += directory.hash_slots.size() * sizeof(HashSlot); offsets += directory.offsets.size() * sizeof(std::uint32_t); postings += directory.postings.size() * sizeof(std::uint32_t); }
        return {{"sorted_unique_keys", keys}, {"flat_open_address_slots", slots}, {"offsets", offsets}, {"contiguous_uint32_postings", postings}, {"total", keys + slots + offsets + postings}};
    }

private:
    std::vector<Band> m_bands;
    DirectoryMode m_mode;
    std::size_t m_code_bits = kCodeBits;
    std::vector<Directory> m_directories;
};

[[nodiscard]] std::vector<std::uint64_t> pack_locator_codes(const std::vector<std::uint64_t>& full_codes, const std::vector<std::size_t>& bit_positions) {
    if(bit_positions.empty()) return full_codes;
    if(!std::is_sorted(bit_positions.begin(), bit_positions.end()) || bit_positions.back() >= kCodeBits) throw std::invalid_argument("native sparse MIH locator pack bits are invalid");
    if(full_codes.size() % kWordCount != 0U) throw std::invalid_argument("native sparse MIH locator source codes are invalid");
    std::vector<std::uint64_t> result(full_codes.size(), 0U);
    for(std::size_t row = 0; row < full_codes.size() / kWordCount; ++row) {
        const auto* source = full_codes.data() + row * kWordCount;
        auto* target = result.data() + row * kWordCount;
        for(std::size_t bit = 0; bit < bit_positions.size(); ++bit) {
            const auto source_bit = bit_positions[bit];
            if(((source[source_bit / 64U] >> (source_bit % 64U)) & 1U) != 0U) target[bit / 64U] |= std::uint64_t{1} << (bit % 64U);
        }
    }
    return result;
}

class GenerationDeduplicator final {
public:
    explicit GenerationDeduplicator(const std::size_t document_count) : m_generation(document_count, 0) {}
    void next_query() { if(m_current == std::numeric_limits<std::uint32_t>::max()) { std::fill(m_generation.begin(), m_generation.end(), 0); m_current = 1; } else { ++m_current; } }
    [[nodiscard]] bool visit(const std::uint32_t position) noexcept { if(m_generation[position] == m_current) return false; m_generation[position] = m_current; return true; }
private:
    std::vector<std::uint32_t> m_generation;
    std::uint32_t m_current = 0;
};

[[nodiscard]] bool closer(const Scored& left, const Scored& right) noexcept { return left.distance == right.distance ? left.position < right.position : left.distance < right.distance; }
[[nodiscard]] bool lower_adc_distance(const AdcScored& left, const AdcScored& right) noexcept { return left.distance == right.distance ? left.position < right.position : left.distance < right.distance; }
[[nodiscard]] bool more_similar(const ExactScored& left, const ExactScored& right) noexcept { return left.similarity == right.similarity ? left.position < right.position : left.similarity > right.similarity; }

template<class Callback>
void enumerate_keys(const std::uint32_t base, const std::size_t width, const int radius, Callback&& callback) {
    const auto recurse = [&](const auto& self, const std::size_t first, const int remaining, const std::uint32_t value) -> void {
        if(remaining == 0) { callback(value); return; }
        for(std::size_t bit = first; bit + static_cast<std::size_t>(remaining) <= width; ++bit) self(self, bit + 1U, remaining - 1, value ^ (std::uint32_t{1} << bit));
    };
    for(int distance = 0; distance <= radius; ++distance) recurse(recurse, 0, distance, base);
}

template<class Callback>
void enumerate_keys_at_distance(const std::uint32_t base, const std::size_t width, const int distance, Callback&& callback) {
    if(distance < 0 || distance > static_cast<int>(width)) throw std::invalid_argument("native sparse MIH probe distance is invalid");
    const auto recurse = [&](const auto& self, const std::size_t first, const int remaining, const std::uint32_t value) -> void {
        if(remaining == 0) { callback(value); return; }
        for(std::size_t bit = first; bit + static_cast<std::size_t>(remaining) <= width; ++bit) self(self, bit + 1U, remaining - 1, value ^ (std::uint32_t{1} << bit));
    };
    recurse(recurse, 0, distance, base);
}

[[nodiscard]] std::vector<int> exact_cover_radii(const std::vector<Band>& bands, const std::size_t covered_radius) {
    std::vector<int> radii(bands.size(), 0);
    std::size_t remaining = covered_radius + 1U > bands.size() ? covered_radius + 1U - bands.size() : 0U;
    while(remaining != 0U) {
        bool advanced = false;
        for(std::size_t band = 0; band < bands.size() && remaining != 0U; ++band) {
            if(radii[band] < static_cast<int>(bands[band].width)) { ++radii[band]; --remaining; advanced = true; }
        }
        if(!advanced) throw std::overflow_error("native global exact MIH coverage exceeds all bands");
    }
    return radii;
}

[[nodiscard]] std::size_t select_top_k(std::vector<Scored>& values, const std::size_t limit) {
    const auto count = std::min(limit, values.size());
    if(count < values.size()) { std::nth_element(values.begin(), values.begin() + static_cast<std::ptrdiff_t>(count), values.end(), closer); values.resize(count); }
    std::sort(values.begin(), values.end(), closer);
    return count;
}

#ifdef AGENT_MEMORY_ENABLE_HNSW_BENCHMARK
[[nodiscard]] int hnsw_hamming_distance(const void* left, const void* right, const void*) {
    const auto* left_words = static_cast<const std::uint64_t*>(left);
    const auto* right_words = static_cast<const std::uint64_t*>(right);
    int result = 0;
    for(std::size_t word = 0; word < kWordCount; ++word) {
#if defined(_MSC_VER)
        result += static_cast<int>(__popcnt64(left_words[word] ^ right_words[word]));
#else
        result += __builtin_popcountll(left_words[word] ^ right_words[word]);
#endif
    }
    return result;
}

class HammingSpace final : public hnswlib::SpaceInterface<int> {
public:
    [[nodiscard]] std::size_t get_data_size() override { return kWordCount * sizeof(std::uint64_t); }
    [[nodiscard]] hnswlib::DISTFUNC<int> get_dist_func() override { return hnsw_hamming_distance; }
    [[nodiscard]] void* get_dist_func_param() override { return nullptr; }
};

class HnswIndex final {
public:
    HnswIndex(const Input& input, const Config& config)
        : m_index(&m_space, input.document_count, config.hnsw_connectivity, config.hnsw_ef_construction, config.hnsw_seed) {
        for(std::size_t position = 0; position < input.document_count; ++position) {
            m_index.addPoint(input.documents.data() + position * kWordCount, position);
        }
        m_index.setEf(config.hnsw_ef_search);
    }

    [[nodiscard]] std::vector<std::uint32_t> search(const std::uint64_t* query, const std::size_t limit) const {
        auto matches = m_index.searchKnn(query, limit);
        std::vector<std::uint32_t> result(matches.size());
        for(std::size_t index = matches.size(); index > 0; --index) {
            result[index - 1U] = static_cast<std::uint32_t>(matches.top().second);
            matches.pop();
        }
        return result;
    }

    [[nodiscard]] std::size_t logical_bytes() const { return m_index.indexFileSize(); }

private:
    HammingSpace m_space;
    hnswlib::HierarchicalNSW<int> m_index;
};
#endif

[[nodiscard]] QueryResult run_flat_query(const Input& input, const std::uint64_t* query, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit, QueryWorkspace& workspace, Diagnostics& diagnostics, std::vector<std::uint32_t>* raw_candidates = nullptr) {
    workspace.clear();
    const auto candidate_start = Clock::now();
    workspace.candidates.resize(input.document_count);
    std::iota(workspace.candidates.begin(), workspace.candidates.end(), 0U);
    diagnostics.unique_candidates += workspace.candidates.size();
    for(const auto position : workspace.candidates) diagnostics.candidate_checksum += static_cast<std::uint64_t>(position) + 1U;
    if(raw_candidates != nullptr) *raw_candidates = workspace.candidates;
    const auto hamming_start = Clock::now();
    workspace.scored.reserve(workspace.candidates.size());
    for(const auto position : workspace.candidates) workspace.scored.push_back({position, hamming.distance_words(query, input.documents.data() + static_cast<std::size_t>(position) * kWordCount)});
    const auto hamming_end = Clock::now();
    const auto top_k_start = Clock::now();
    static_cast<void>(select_top_k(workspace.scored, hamming_limit));
    const auto top_k_end = Clock::now();
    for(const auto& item : workspace.scored) diagnostics.shortlist_checksum += static_cast<std::uint64_t>(item.position) + 1U;
    return {{0.0, 0.0, 0.0, 0.0, milliseconds(hamming_start, hamming_end), milliseconds(top_k_start, top_k_end), milliseconds(candidate_start, top_k_end)}, workspace.scored};
}

#ifdef AGENT_MEMORY_ENABLE_HNSW_BENCHMARK
[[nodiscard]] QueryResult run_hnsw_query(const HnswIndex& index, const Input& input, const std::uint64_t* query, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit, QueryWorkspace& workspace, Diagnostics& diagnostics, std::vector<std::uint32_t>* raw_candidates = nullptr) {
    workspace.clear();
    const auto candidate_start = Clock::now();
    const auto traversal_start = Clock::now();
    workspace.candidates = index.search(query, hamming_limit);
    const auto traversal_end = Clock::now();
    diagnostics.unique_candidates += workspace.candidates.size();
    for(const auto position : workspace.candidates) diagnostics.candidate_checksum += static_cast<std::uint64_t>(position) + 1U;
    if(raw_candidates != nullptr) *raw_candidates = workspace.candidates;
    const auto hamming_start = Clock::now();
    workspace.scored.reserve(workspace.candidates.size());
    for(const auto position : workspace.candidates) workspace.scored.push_back({position, hamming.distance_words(query, input.documents.data() + static_cast<std::size_t>(position) * kWordCount)});
    const auto hamming_end = Clock::now();
    const auto top_k_start = Clock::now();
    static_cast<void>(select_top_k(workspace.scored, hamming_limit));
    const auto top_k_end = Clock::now();
    for(const auto& item : workspace.scored) diagnostics.shortlist_checksum += static_cast<std::uint64_t>(item.position) + 1U;
    return {{0.0, 0.0, milliseconds(traversal_start, traversal_end), 0.0, milliseconds(hamming_start, hamming_end), milliseconds(top_k_start, top_k_end), milliseconds(candidate_start, top_k_end)}, workspace.scored};
}
#endif

[[nodiscard]] QueryResult run_query(const SparseIndex& index, const Input& input, const std::uint64_t* query, const std::uint64_t* locator_query, const std::vector<Band>& bands, const std::vector<int>& radii, const DeduplicationMode deduplication, GenerationDeduplicator& deduplicator, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit, QueryWorkspace& workspace, Diagnostics& diagnostics, std::vector<std::uint32_t>* raw_candidates = nullptr) {
    workspace.clear();
    const auto candidate_start = Clock::now();
    const auto enumeration_start = Clock::now();
    for(std::size_t band = 0; band < bands.size(); ++band) enumerate_keys(extract_key(locator_query, bands[band]), bands[band].width, radii[band], [&](const std::uint32_t key) { workspace.probes.push_back({band, key}); });
    const auto enumeration_end = Clock::now();
    const auto lookup_start = Clock::now();
    for(const auto& probe : workspace.probes) {
        ++diagnostics.probes;
        Span span;
        if(index.find(probe.band, probe.key, span)) { ++diagnostics.non_empty_probes; workspace.spans.push_back(span); diagnostics.posting_lengths.push_back(static_cast<std::uint32_t>(span.last - span.first)); }
        else ++diagnostics.empty_probes;
    }
    const auto lookup_end = Clock::now();
    deduplicator.next_query();
    double traversal_ms = 0.0;
    double deduplication_ms = 0.0;
    if(deduplication == DeduplicationMode::TwoPassGenerationArray) {
        const auto traversal_start = Clock::now();
        for(const auto& span : workspace.spans) {
            diagnostics.posting_visits += span.last - span.first;
            workspace.visited.insert(workspace.visited.end(), span.values + static_cast<std::ptrdiff_t>(span.first), span.values + static_cast<std::ptrdiff_t>(span.last));
        }
        traversal_ms = milliseconds(traversal_start, Clock::now());
        const auto deduplication_start = Clock::now();
        for(const auto position : workspace.visited) if(deduplicator.visit(position)) workspace.candidates.push_back(position);
        deduplication_ms = milliseconds(deduplication_start, Clock::now());
    } else {
        const auto combined_start = Clock::now();
        for(const auto& span : workspace.spans) {
            diagnostics.posting_visits += span.last - span.first;
            for(auto offset = span.first; offset < span.last; ++offset) {
                const auto position = span.values[offset];
                if(deduplicator.visit(position)) workspace.candidates.push_back(position);
            }
        }
        traversal_ms = milliseconds(combined_start, Clock::now());
        deduplication_ms = traversal_ms;
    }
    diagnostics.unique_candidates += workspace.candidates.size();
    for(const auto position : workspace.candidates) diagnostics.candidate_checksum += static_cast<std::uint64_t>(position) + 1U;
    if(raw_candidates != nullptr) *raw_candidates = workspace.candidates;
    const auto hamming_start = Clock::now();
    workspace.scored.reserve(workspace.candidates.size());
    for(const auto position : workspace.candidates) workspace.scored.push_back({position, hamming.distance_words(query, input.documents.data() + static_cast<std::size_t>(position) * kWordCount)});
    const auto hamming_end = Clock::now();
    const auto top_k_start = Clock::now();
    static_cast<void>(select_top_k(workspace.scored, hamming_limit));
    const auto top_k_end = Clock::now();
    for(const auto& item : workspace.scored) diagnostics.shortlist_checksum += static_cast<std::uint64_t>(item.position) + 1U;
    QueryResult result;
    result.timings = {milliseconds(enumeration_start, enumeration_end), milliseconds(lookup_start, lookup_end), traversal_ms, deduplication_ms, milliseconds(hamming_start, hamming_end), milliseconds(top_k_start, top_k_end), milliseconds(candidate_start, top_k_end)};
    result.shortlist = workspace.scored;
    return result;
}

[[nodiscard]] QueryResult run_global_exact_query(const SparseIndex& index, const Input& input, const std::uint64_t* query, const std::vector<Band>& bands, GenerationDeduplicator& deduplicator, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit, const std::size_t max_cover_radius, QueryWorkspace& workspace, Diagnostics& diagnostics, std::vector<std::uint32_t>* raw_candidates = nullptr) {
    workspace.clear();
    const auto candidate_start = Clock::now();
    double key_enumeration_ms = 0.0, bucket_lookup_ms = 0.0, posting_traversal_ms = 0.0, deduplication_ms = 0.0, hamming_ms = 0.0, top_k_ms = 0.0;
    deduplicator.next_query();
    std::vector<int> previous_radii(bands.size(), 0);
    bool first_level = true;
    for(std::size_t covered_radius = 0; covered_radius <= max_cover_radius; ++covered_radius) {
        const auto radii = exact_cover_radii(bands, covered_radius);
        bool changed = first_level;
        const auto enumeration_start = Clock::now();
        for(std::size_t band = 0; band < bands.size(); ++band) {
            const auto first_distance = first_level ? 0 : previous_radii[band] + 1;
            for(auto distance = first_distance; distance <= radii[band]; ++distance) {
                changed = true;
                enumerate_keys_at_distance(extract_key(query, bands[band]), bands[band].width, distance, [&](const std::uint32_t key) { workspace.probes.push_back({band, key}); });
            }
        }
        key_enumeration_ms += milliseconds(enumeration_start, Clock::now());
        first_level = false;
        if(!changed) continue;
        previous_radii = radii;
        const auto lookup_start = Clock::now();
        const auto first_probe = workspace.spans.size();
        for(std::size_t probe = 0; probe < workspace.probes.size(); ++probe) {
            const auto& key_probe = workspace.probes[probe];
            ++diagnostics.probes;
            Span span;
            if(index.find(key_probe.band, key_probe.key, span)) { ++diagnostics.non_empty_probes; workspace.spans.push_back(span); diagnostics.posting_lengths.push_back(static_cast<std::uint32_t>(span.last - span.first)); }
            else ++diagnostics.empty_probes;
        }
        workspace.probes.clear();
        bucket_lookup_ms += milliseconds(lookup_start, Clock::now());
        const auto candidate_first = workspace.candidates.size();
        const auto traversal_start = Clock::now();
        for(std::size_t span_index = first_probe; span_index < workspace.spans.size(); ++span_index) {
            const auto& span = workspace.spans[span_index];
            diagnostics.posting_visits += span.last - span.first;
            for(auto offset = span.first; offset < span.last; ++offset) {
                const auto position = span.values[offset];
                if(deduplicator.visit(position)) workspace.candidates.push_back(position);
            }
        }
        workspace.spans.clear();
        posting_traversal_ms += milliseconds(traversal_start, Clock::now());
        deduplication_ms = posting_traversal_ms;
        diagnostics.unique_candidates += workspace.candidates.size() - candidate_first;
        for(std::size_t candidate = candidate_first; candidate < workspace.candidates.size(); ++candidate) diagnostics.candidate_checksum += static_cast<std::uint64_t>(workspace.candidates[candidate]) + 1U;
        const auto hamming_start = Clock::now();
        workspace.scored.reserve(hamming_limit + (workspace.candidates.size() - candidate_first));
        for(std::size_t candidate = candidate_first; candidate < workspace.candidates.size(); ++candidate) {
            const auto position = workspace.candidates[candidate];
            workspace.scored.push_back({position, hamming.distance_words(query, input.documents.data() + static_cast<std::size_t>(position) * kWordCount)});
        }
        hamming_ms += milliseconds(hamming_start, Clock::now());
        const auto top_k_start = Clock::now();
        static_cast<void>(select_top_k(workspace.scored, hamming_limit));
        top_k_ms += milliseconds(top_k_start, Clock::now());
        if(workspace.scored.size() == hamming_limit && workspace.scored.back().distance < covered_radius + 1U) {
            for(const auto& item : workspace.scored) diagnostics.shortlist_checksum += static_cast<std::uint64_t>(item.position) + 1U;
            diagnostics.global_exact_cover_radius_sum += covered_radius;
            ++diagnostics.global_exact_stop_count;
            if(raw_candidates != nullptr) *raw_candidates = workspace.candidates;
            QueryResult result;
            result.timings = {key_enumeration_ms, bucket_lookup_ms, posting_traversal_ms, deduplication_ms, hamming_ms, top_k_ms, milliseconds(candidate_start, Clock::now())};
            result.shortlist = workspace.scored;
            result.global_exact = {covered_radius, workspace.scored.back().distance, true};
            return result;
        }
    }
    throw std::runtime_error("native global exact MIH exhausted its complete radius without a strict stopping certificate");
}

[[nodiscard]] double binary_adc_rerank(const Input& input, const float* query_projection, const std::vector<Scored>& hamming, const std::size_t limit, std::vector<AdcScored>& scored, std::vector<std::uint32_t>& positions, std::uint64_t& checksum) {
    const auto start = Clock::now();
    std::array<std::array<float, 256>, 32> tables{};
    for(std::size_t group = 0; group < tables.size(); ++group) for(std::size_t value = 0; value < tables[group].size(); ++value) for(std::size_t offset = 0; offset < 8; ++offset) { const auto bit = group * 8U + offset; const auto symbol = (value >> offset) & 1U; const auto delta = query_projection[bit] - input.adc_centroids[bit * 2U + symbol]; tables[group][value] += delta * delta; }
    scored.clear(); positions.clear(); scored.reserve(hamming.size()); positions.reserve(hamming.size());
    for(const auto& candidate : hamming) { const auto* code = input.documents.data() + static_cast<std::size_t>(candidate.position) * kWordCount; float distance = 0.0F; for(std::size_t group = 0; group < tables.size(); ++group) distance += tables[group][static_cast<std::uint8_t>(code[group / 8U] >> ((group % 8U) * 8U))]; scored.push_back({candidate.position, distance}); }
    const auto count = std::min(limit, scored.size());
    if(count < scored.size()) { std::nth_element(scored.begin(), scored.begin() + static_cast<std::ptrdiff_t>(count), scored.end(), lower_adc_distance); scored.resize(count); }
    std::sort(scored.begin(), scored.end(), lower_adc_distance);
    for(const auto& value : scored) { positions.push_back(value.position); checksum += static_cast<std::uint64_t>(value.position) + 1U; }
    return milliseconds(start, Clock::now());
}

[[nodiscard]] double exact_rerank(const Input& input, const float* query, const std::vector<std::uint32_t>& positions, const std::size_t count, const agent_memory::VectorSimilarityComputer& computer, std::vector<ExactScored>& scored, std::uint64_t& checksum) {
    const auto start = Clock::now(); scored.clear(); scored.reserve(count);
    for(std::size_t index = 0; index < count; ++index) { const auto position = positions[index]; scored.push_back({position, computer.dot_product_values(query, input.document_vectors.data() + static_cast<std::size_t>(position) * input.embedding_dimension, input.embedding_dimension)}); }
    std::sort(scored.begin(), scored.end(), more_similar); for(const auto& value : scored) checksum += static_cast<std::uint64_t>(value.position) + 1U;
    return milliseconds(start, Clock::now());
}

void verify_candidate_conformance(const Input& input, const std::uint64_t* query, const std::vector<std::uint32_t>& candidates, const std::vector<Scored>& shortlist, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit, const bool require_fixed_r56) {
    std::vector<bool> present(input.document_count, false);
    for(const auto position : candidates) present[position] = true;
    std::vector<Scored> expected;
    if(require_fixed_r56) {
        for(std::size_t position = 0; position < input.document_count; ++position) {
            const auto distance = hamming.distance_words(query, input.documents.data() + position * kWordCount);
            if(distance <= 56U && !present[position]) throw std::runtime_error("native sparse MIH fixed-r56 candidate inclusion differs");
            if(present[position]) expected.push_back({static_cast<std::uint32_t>(position), distance});
        }
    } else {
        expected.reserve(candidates.size());
        for(const auto position : candidates) expected.push_back({position, hamming.distance_words(query, input.documents.data() + static_cast<std::size_t>(position) * kWordCount)});
    }
    static_cast<void>(select_top_k(expected, hamming_limit));
    if(expected.size() != shortlist.size()) throw std::runtime_error("native sparse MIH Hamming shortlist size differs");
    for(std::size_t index = 0; index < expected.size(); ++index) if(expected[index].position != shortlist[index].position || expected[index].distance != shortlist[index].distance) throw std::runtime_error("native sparse MIH Hamming shortlist differs");
}

[[nodiscard]] std::vector<Scored> global_exact_flat_shortlist(const Input& input, const std::uint64_t* query, const std::vector<Scored>& shortlist, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit, const GlobalExactCertificate& certificate) {
    if(!certificate.strict_unseen_lower_bound_proved || shortlist.size() != hamming_limit || shortlist.empty() || shortlist.back().distance != certificate.kth_distance || certificate.kth_distance >= certificate.covered_radius + 1U) throw std::runtime_error("native global exact MIH stopping certificate differs");
    std::vector<Scored> expected;
    expected.reserve(input.document_count);
    for(std::size_t position = 0; position < input.document_count; ++position) expected.push_back({static_cast<std::uint32_t>(position), hamming.distance_words(query, input.documents.data() + position * kWordCount)});
    static_cast<void>(select_top_k(expected, hamming_limit));
    if(expected.size() != shortlist.size()) throw std::runtime_error("native global exact MIH Flat size differs");
    for(std::size_t position = 0; position < expected.size(); ++position) if(expected[position].position != shortlist[position].position || expected[position].distance != shortlist[position].distance) throw std::runtime_error("native global exact MIH Flat ordering differs");
    return expected;
}

void verify_global_exact_conformance(const Input& input, const std::uint64_t* query, const std::vector<Scored>& shortlist, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit, const GlobalExactCertificate& certificate) {
    static_cast<void>(global_exact_flat_shortlist(input, query, shortlist, hamming, hamming_limit, certificate));
}

[[nodiscard]] CandidateDiagnostic diagnose_fixed_r56_candidate_union(const Input& input, const std::uint32_t query_position, const std::uint64_t* query, const std::vector<std::uint32_t>& candidates, const std::vector<Scored>& shortlist, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit) {
    std::vector<bool> candidate_present(input.document_count, false);
    for(const auto position : candidates) candidate_present[position] = true;
    std::vector<Scored> exact;
    exact.reserve(input.document_count);
    CandidateDiagnostic result;
    result.candidate_union_size = candidates.size();
    result.raw_candidate_sequence_sha256 = position_sequence_sha256(query_position, candidates);
    auto sorted_candidates = candidates;
    std::sort(sorted_candidates.begin(), sorted_candidates.end());
    result.raw_candidate_set_sha256 = position_sequence_sha256(query_position, sorted_candidates);
    std::vector<std::uint32_t> shortlist_positions;
    shortlist_positions.reserve(shortlist.size());
    for(const auto& item : shortlist) shortlist_positions.push_back(item.position);
    result.hamming_shortlist_sequence_sha256 = position_sequence_sha256(query_position, shortlist_positions);
    for(std::size_t position = 0; position < input.document_count; ++position) {
        const auto distance = hamming.distance_words(query, input.documents.data() + position * kWordCount);
        if(distance <= 56U) {
            ++result.global_fixed_r56_count;
            if(!candidate_present[position]) throw std::runtime_error("native fixed-r56 diagnostic found a missing guaranteed candidate");
        }
        exact.push_back({static_cast<std::uint32_t>(position), distance});
    }
    for(const auto position : candidates) {
        const auto distance = hamming.distance_words(query, input.documents.data() + static_cast<std::size_t>(position) * kWordCount);
        if(distance <= 56U) ++result.candidate_union_fixed_r56_count;
    }
    static_cast<void>(select_top_k(exact, hamming_limit));
    if(exact.empty()) throw std::runtime_error("native fixed-r56 diagnostic exact Hamming top-K is empty");
    result.exact_hamming_top_k_max_distance = exact.back().distance;
    for(const auto [index, k] : std::array<std::pair<std::size_t, std::size_t>, 6>{{{0U, 10U}, {1U, 64U}, {2U, 128U}, {3U, 256U}, {4U, 512U}, {5U, 768U}}}) {
        result.exact_hamming_distances_at_k[index] = k <= exact.size() ? exact[k - 1U].distance : 0U;
    }
    std::vector<bool> exact_top_k_present(input.document_count, false);
    for(const auto& item : exact) {
        exact_top_k_present[item.position] = true;
        if(item.distance <= 56U) ++result.exact_hamming_top_k_fixed_r56_count;
        if(candidate_present[item.position]) ++result.candidate_union_exact_hamming_top_k_overlap;
    }
    for(const auto& item : shortlist) {
        if(item.distance <= 56U) ++result.mih_shortlist_fixed_r56_count;
        if(exact_top_k_present[item.position]) ++result.mih_shortlist_exact_hamming_top_k_overlap;
    }
    return result;
}

[[nodiscard]] nlohmann::json timing_series(const std::vector<double>& values) { return values; }

[[nodiscard]] bool same_candidate_diagnostics(const Diagnostics& left, const Diagnostics& right) {
    return left.probes == right.probes && left.non_empty_probes == right.non_empty_probes && left.empty_probes == right.empty_probes &&
        left.posting_visits == right.posting_visits && left.unique_candidates == right.unique_candidates &&
        left.candidate_checksum == right.candidate_checksum && left.shortlist_checksum == right.shortlist_checksum &&
        left.global_exact_cover_radius_sum == right.global_exact_cover_radius_sum && left.global_exact_stop_count == right.global_exact_stop_count &&
        left.posting_lengths == right.posting_lengths;
}

[[nodiscard]] int run(const Config& config, const std::filesystem::path& report_path) {
    const auto input = load_input(config.input_directory);
    if(config.query_count > input.query_count || config.hamming_limit > input.document_count) throw std::invalid_argument("native sparse MIH query or Hamming limit exceeds input");
    const auto bands = make_bands(config.band_widths);
    const auto selected_directory = directory_mode(config.directory_mode);
    const auto selected_deduplication = deduplication_mode(config.deduplication_mode);
    const bool global_exact_mih = config.backend == "mih" && config.mih_search_mode == "global_exact";
    const auto locator_code_bits = config.locator_bit_positions.empty() ? kCodeBits : config.locator_bit_positions.size();
    const auto locator_documents = pack_locator_codes(input.documents, config.locator_bit_positions);
    const auto locator_queries = pack_locator_codes(input.queries, config.locator_bit_positions);
    const SparseIndex index(locator_documents, input.document_count, bands, selected_directory, locator_code_bits);
#ifdef AGENT_MEMORY_ENABLE_HNSW_BENCHMARK
    std::unique_ptr<HnswIndex> hnsw_index;
    if(config.backend == "hnsw") hnsw_index = std::make_unique<HnswIndex>(input, config);
#else
    if(config.backend == "hnsw") throw std::runtime_error("native HNSW benchmark support was not configured");
#endif
    const auto hamming = agent_memory::HammingDistanceComputer(kWordCount);
    const auto vector_computer = agent_memory::VectorSimilarityComputer();
    std::vector<std::size_t> query_positions(input.query_count); std::iota(query_positions.begin(), query_positions.end(), 0U);
    std::mt19937_64 random(config.query_seed); std::shuffle(query_positions.begin(), query_positions.end(), random); query_positions.resize(config.query_count);

    const auto execute_pass = [&](const bool retain_diagnostics, const bool verify, std::vector<double>* key_samples, std::vector<double>* lookup_samples, std::vector<double>* traversal_samples, std::vector<double>* dedup_samples, std::vector<double>* hamming_samples, std::vector<double>* top_samples, std::vector<double>* adc_samples, std::vector<double>* exact_samples, std::vector<double>* generator_samples, std::vector<double>* cascade_samples, Diagnostics* aggregate) {
        GenerationDeduplicator deduplicator(input.document_count); QueryWorkspace workspace;
        workspace.probes.reserve(12000); workspace.spans.reserve(12000); workspace.visited.reserve(16000); workspace.candidates.reserve(16000); workspace.scored.reserve(16000);
        std::vector<AdcScored> adc_scored; adc_scored.reserve(config.hamming_limit);
        std::vector<std::uint32_t> adc_positions; adc_positions.reserve(config.hamming_limit);
        std::vector<ExactScored> exact_scored; exact_scored.reserve(config.exact_limit);
        Diagnostics local{};
        for(const auto position : query_positions) {
            const auto cascade_start = Clock::now();
            std::vector<std::uint32_t> raw_candidates;
            const auto* query = input.queries.data() + position * kWordCount;
            const auto result = config.backend == "mih"
                ? (global_exact_mih
                    ? run_global_exact_query(index, input, query, bands, deduplicator, hamming, config.hamming_limit, config.global_exact_max_cover_radius, workspace, local, verify ? &raw_candidates : nullptr)
                    : run_query(index, input, query, locator_queries.data() + position * kWordCount, bands, config.local_radii, selected_deduplication, deduplicator, hamming, config.hamming_limit, workspace, local, verify ? &raw_candidates : nullptr))
                : (config.backend == "flat"
                    ? run_flat_query(input, query, hamming, config.hamming_limit, workspace, local, verify ? &raw_candidates : nullptr)
#ifdef AGENT_MEMORY_ENABLE_HNSW_BENCHMARK
                    : run_hnsw_query(*hnsw_index, input, query, hamming, config.hamming_limit, workspace, local, verify ? &raw_candidates : nullptr))
#else
                    : throw std::runtime_error("native HNSW benchmark support was not configured"))
#endif
                ;
            std::uint64_t checksum = 0;
            const auto adc_ms = binary_adc_rerank(input, input.query_projections.data() + position * kCodeBits, result.shortlist, config.adc_limit, adc_scored, adc_positions, checksum);
            const auto exact_count = std::min(config.exact_limit, adc_positions.size());
            const auto exact_ms = exact_rerank(input, input.query_vectors.data() + position * input.embedding_dimension, adc_positions, exact_count, vector_computer, exact_scored, checksum);
            if(checksum == 0) throw std::runtime_error("native sparse MIH cascade checksum is invalid");
            const auto cascade_ms = milliseconds(cascade_start, Clock::now());
            if(verify) {
                if(global_exact_mih) verify_global_exact_conformance(input, query, result.shortlist, hamming, config.hamming_limit, result.global_exact);
                else verify_candidate_conformance(input, query, raw_candidates, result.shortlist, hamming, config.hamming_limit, config.backend == "mih" && config.mih_search_mode == "fixed_r56");
            }
            if(key_samples != nullptr) { key_samples->push_back(result.timings.key_enumeration_ms); lookup_samples->push_back(result.timings.bucket_lookup_ms); traversal_samples->push_back(result.timings.posting_traversal_ms); dedup_samples->push_back(result.timings.deduplication_ms); hamming_samples->push_back(result.timings.hamming_ms); top_samples->push_back(result.timings.top_k_ms); adc_samples->push_back(adc_ms); exact_samples->push_back(exact_ms); generator_samples->push_back(result.timings.candidate_generator_total_ms); cascade_samples->push_back(cascade_ms); }
        }
        if(retain_diagnostics && aggregate != nullptr) *aggregate = std::move(local);
    };

    for(std::size_t warmup = 0; warmup < config.warmup_count; ++warmup) execute_pass(false, false, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr, nullptr);
    std::vector<double> keys, lookups, traversal, deduplication, hamming_samples, top_k, adc, exact, candidate_generator, cascade;
    Diagnostics diagnostics;
    execute_pass(true, false, &keys, &lookups, &traversal, &deduplication, &hamming_samples, &top_k, &adc, &exact, &candidate_generator, &cascade, &diagnostics);
    for(std::size_t repeat = 1; repeat < config.repeat_count; ++repeat) {
        Diagnostics repeat_diagnostics;
        execute_pass(true, false, &keys, &lookups, &traversal, &deduplication, &hamming_samples, &top_k, &adc, &exact, &candidate_generator, &cascade, &repeat_diagnostics);
        if(!same_candidate_diagnostics(diagnostics, repeat_diagnostics)) throw std::runtime_error("native sparse MIH warm repeats differ in candidate pipeline");
    }
    nlohmann::json exported_shortlists = nlohmann::json::array();
    nlohmann::json candidate_diagnostics = nlohmann::json::array();
    nlohmann::json global_exact_certificates = nlohmann::json::array();
    {
        GenerationDeduplicator verification_deduplicator(input.document_count);
        QueryWorkspace verification_workspace;
        std::vector<AdcScored> verification_adc_scored;
        std::vector<std::uint32_t> verification_adc_positions;
        verification_adc_scored.reserve(config.hamming_limit);
        verification_adc_positions.reserve(config.hamming_limit);
        for(const auto position : query_positions) {
            Diagnostics verification_diagnostics;
            std::vector<std::uint32_t> raw_candidates;
            const auto* query = input.queries.data() + position * kWordCount;
            const auto result = config.backend == "mih"
                ? (global_exact_mih
                    ? run_global_exact_query(index, input, query, bands, verification_deduplicator, hamming, config.hamming_limit, config.global_exact_max_cover_radius, verification_workspace, verification_diagnostics, &raw_candidates)
                    : run_query(index, input, query, locator_queries.data() + position * kWordCount, bands, config.local_radii, selected_deduplication, verification_deduplicator, hamming, config.hamming_limit, verification_workspace, verification_diagnostics, &raw_candidates))
                : (config.backend == "flat"
                    ? run_flat_query(input, query, hamming, config.hamming_limit, verification_workspace, verification_diagnostics, &raw_candidates)
#ifdef AGENT_MEMORY_ENABLE_HNSW_BENCHMARK
                    : run_hnsw_query(*hnsw_index, input, query, hamming, config.hamming_limit, verification_workspace, verification_diagnostics, &raw_candidates))
#else
                    : throw std::runtime_error("native HNSW benchmark support was not configured"))
#endif
                ;
            if(global_exact_mih) {
                const auto flat_shortlist = global_exact_flat_shortlist(input, query, result.shortlist, hamming, config.hamming_limit, result.global_exact);
                if(!config.global_exact_certificate_output.empty()) {
                    nlohmann::json exact_positions = nlohmann::json::array(), exact_distances = nlohmann::json::array();
                    nlohmann::json flat_positions = nlohmann::json::array(), flat_distances = nlohmann::json::array();
                    for(const auto& item : result.shortlist) { exact_positions.push_back(item.position); exact_distances.push_back(item.distance); }
                    for(const auto& item : flat_shortlist) { flat_positions.push_back(item.position); flat_distances.push_back(item.distance); }
                    global_exact_certificates.push_back({
                        {"query_position", position},
                        {"covered_radius", result.global_exact.covered_radius},
                        {"unseen_lower_bound", result.global_exact.covered_radius + 1U},
                        {"kth_distance", result.global_exact.kth_distance},
                        {"strict_unseen_lower_bound_proved", result.global_exact.strict_unseen_lower_bound_proved},
                        {"exact_mih_positions", std::move(exact_positions)},
                        {"exact_mih_distances", std::move(exact_distances)},
                        {"flat_positions", std::move(flat_positions)},
                        {"flat_distances", std::move(flat_distances)},
                    });
                }
            } else verify_candidate_conformance(input, query, raw_candidates, result.shortlist, hamming, config.hamming_limit, config.backend == "mih" && config.mih_search_mode == "fixed_r56");
            if(!config.candidate_diagnostic_output.empty()) {
                const auto diagnostic = diagnose_fixed_r56_candidate_union(input, static_cast<std::uint32_t>(position), query, raw_candidates, result.shortlist, hamming, config.hamming_limit);
                candidate_diagnostics.push_back({
                    {"query_position", position},
                    {"candidate_union_size", diagnostic.candidate_union_size},
                    {"global_fixed_r56_count", diagnostic.global_fixed_r56_count},
                    {"candidate_union_fixed_r56_count", diagnostic.candidate_union_fixed_r56_count},
                    {"exact_hamming_top_k_fixed_r56_count", diagnostic.exact_hamming_top_k_fixed_r56_count},
                    {"candidate_union_exact_hamming_top_k_overlap", diagnostic.candidate_union_exact_hamming_top_k_overlap},
                    {"mih_shortlist_fixed_r56_count", diagnostic.mih_shortlist_fixed_r56_count},
                    {"mih_shortlist_exact_hamming_top_k_overlap", diagnostic.mih_shortlist_exact_hamming_top_k_overlap},
                    {"exact_hamming_top_k_max_distance", diagnostic.exact_hamming_top_k_max_distance},
                    {"exact_hamming_distances_at_k", {{"10", diagnostic.exact_hamming_distances_at_k[0]}, {"64", diagnostic.exact_hamming_distances_at_k[1]}, {"128", diagnostic.exact_hamming_distances_at_k[2]}, {"256", diagnostic.exact_hamming_distances_at_k[3]}, {"512", diagnostic.exact_hamming_distances_at_k[4]}, {"768", diagnostic.exact_hamming_distances_at_k[5]}}},
                    {"sequence_digest_encoding", "query_position_u32_le|count_u64_le|positions_u32_le_v1"},
                    {"raw_candidate_sequence_sha256", diagnostic.raw_candidate_sequence_sha256},
                    {"raw_candidate_set_sha256", diagnostic.raw_candidate_set_sha256},
                    {"hamming_shortlist_sequence_sha256", diagnostic.hamming_shortlist_sequence_sha256},
                });
            }
            if(!config.shortlist_output.empty()) {
                std::uint64_t verification_checksum = 0;
                static_cast<void>(binary_adc_rerank(input, input.query_projections.data() + position * kCodeBits, result.shortlist, config.adc_limit, verification_adc_scored, verification_adc_positions, verification_checksum));
                nlohmann::json positions = nlohmann::json::array();
                for(const auto& scored : result.shortlist) positions.push_back(scored.position);
                nlohmann::json adc_positions_export = nlohmann::json::array();
                for(const auto candidate : verification_adc_positions) adc_positions_export.push_back(candidate);
                exported_shortlists.push_back({{"query_position", position}, {"hamming_shortlist_positions", std::move(positions)}, {"binary_adc_positions", std::move(adc_positions_export)}});
            }
        }
    }
    std::string shortlist_export_sha256;
    if(!config.shortlist_output.empty()) {
        const nlohmann::json shortlist_export{{"schema_version", 1}, {"family", "native_ann_hamming_shortlist_export_v1"}, {"input_manifest_sha256", input.manifest_sha256}, {"backend", config.backend}, {"query_seed", config.query_seed}, {"selected_query_positions", query_positions}, {"hamming_limit", config.hamming_limit}, {"rows", exported_shortlists}};
        std::ofstream output(config.shortlist_output);
        if(!output) throw std::runtime_error("cannot write native ANN Hamming shortlist export");
        output << shortlist_export.dump(2) << '\n';
        output.close();
        shortlist_export_sha256 = agent_memory::sha256_file_hex(config.shortlist_output);
    }
    std::string candidate_diagnostic_sha256;
    if(!config.candidate_diagnostic_output.empty()) {
        const nlohmann::json diagnostic_export{{"schema_version", 1}, {"family", "native_mih_fixed_r56_candidate_union_diagnostic_v1"}, {"input_manifest_sha256", input.manifest_sha256}, {"benchmark_config_sha256", config.sha256}, {"backend", config.backend}, {"query_seed", config.query_seed}, {"selected_query_positions", query_positions}, {"fixed_radius", 56}, {"hamming_limit", config.hamming_limit}, {"rows", candidate_diagnostics}};
        std::ofstream output(config.candidate_diagnostic_output);
        if(!output) throw std::runtime_error("cannot write native fixed-r56 candidate diagnostic");
        output << diagnostic_export.dump(2) << '\n';
        output.close();
        candidate_diagnostic_sha256 = agent_memory::sha256_file_hex(config.candidate_diagnostic_output);
    }
    std::string global_exact_certificate_sha256;
    if(!config.global_exact_certificate_output.empty()) {
        const nlohmann::json certificate_export{{"schema_version", 1}, {"family", "native_mih_global_exact_certificate_v1"}, {"input_manifest_sha256", input.manifest_sha256}, {"benchmark_config_sha256", config.sha256}, {"backend", config.backend}, {"mih_search_mode", config.mih_search_mode}, {"query_seed", config.query_seed}, {"selected_query_positions", query_positions}, {"hamming_limit", config.hamming_limit}, {"rows", global_exact_certificates}};
        std::ofstream output(config.global_exact_certificate_output);
        if(!output) throw std::runtime_error("cannot write native global exact MIH certificate");
        output << certificate_export.dump(2) << '\n';
        output.close();
        global_exact_certificate_sha256 = agent_memory::sha256_file_hex(config.global_exact_certificate_output);
    }

    const auto per_query = static_cast<double>(config.query_count);
    const auto mean_posting_length = diagnostics.non_empty_probes == 0 ? 0.0 : static_cast<double>(diagnostics.posting_visits) / static_cast<double>(diagnostics.non_empty_probes);
    std::vector<double> posting_length_values; posting_length_values.reserve(diagnostics.posting_lengths.size()); for(const auto value : diagnostics.posting_lengths) posting_length_values.push_back(static_cast<double>(value));
    const bool is_mih = config.backend == "mih";
    const auto shared_code_store_bytes = input.document_count * kWordCount * sizeof(std::uint64_t);
    nlohmann::json backend_report{{"name", config.backend}, {"shared_code_store_bytes", shared_code_store_bytes}};
    if(is_mih) {
        backend_report["index_representation"] = selected_directory == DirectoryMode::SortedLowerBound
            ? "sorted_unique_uint32_keys_plus_uint32_offsets_plus_contiguous_uint32_postings_v1"
            : "flat_open_address_uint32_key_directory_plus_uint32_offsets_plus_contiguous_uint32_postings_v1";
        backend_report["directory_mode"] = config.directory_mode;
        backend_report["backend_index_logical_bytes"] = index.logical_bytes();
        backend_report["backend_index_logical_byte_breakdown"] = index.logical_byte_breakdown();
        backend_report["locator_representation"] = config.locator_bit_positions.empty() ? "full_itq_256_code_v1" : "selected_itq_256_bit_subset_packed_lsb_first_v1";
        backend_report["locator_code_bits"] = locator_code_bits;
        backend_report["locator_bit_positions"] = config.locator_bit_positions;
    } else if(config.backend == "flat") {
        backend_report["index_representation"] = "no_auxiliary_index_full_scan_v1";
        backend_report["backend_index_logical_bytes"] = 0;
    } else {
#ifdef AGENT_MEMORY_ENABLE_HNSW_BENCHMARK
        backend_report["index_representation"] = "hnswlib_hamming_graph_v0_8_0";
        backend_report["backend_index_logical_bytes"] = hnsw_index->logical_bytes();
        backend_report["connectivity"] = config.hnsw_connectivity;
        backend_report["ef_construction"] = config.hnsw_ef_construction;
        backend_report["ef_search"] = config.hnsw_ef_search;
        backend_report["seed"] = config.hnsw_seed;
        backend_report["hnswlib_revision"] = AGENT_MEMORY_HNSWLIB_REVISION;
#endif
    }
    nlohmann::json report{
        {"schema_version", 1},
        {"family", "mih_native_sparse_arbitrary_m_v1"},
        {"input_manifest", input.manifest},
        {"input_manifest_sha256", input.manifest_sha256},
        {"benchmark_config_sha256", config.sha256},
        {"benchmark_source_bundle_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
        {"benchmark_source_files_sha256", {{"tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp", AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_SOURCE_SHA256}, {"tools/agent-memory-bench/materialize-mih-storage-input.py", AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_MATERIALIZER_SOURCE_SHA256}, {"src/agent_memory/index/VectorSimilarityComputer.cpp", AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_VECTOR_SIMILARITY_SOURCE_SHA256}, {"src/agent_memory/index/BinarySignature.cpp", AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_BINARY_SIGNATURE_SOURCE_SHA256}}},
        {"build_environment", build_environment()},
        {"backend", backend_report},
        {"query_count", config.query_count}, {"query_seed", config.query_seed}, {"query_selection_algorithm", "std_mt19937_64_shuffle_v1"}, {"selected_query_positions", query_positions},
        {"warmup_count", config.warmup_count}, {"repeat_count", config.repeat_count},
        {"band_widths", config.band_widths}, {"mih_search_mode", config.mih_search_mode}, {"local_radii", config.local_radii}, {"locator_bit_positions", config.locator_bit_positions}, {"fixed_radius", is_mih && config.mih_search_mode == "fixed_r56" ? nlohmann::json(56) : nlohmann::json(nullptr)}, {"fixed_radius_exact_inclusion", is_mih && config.mih_search_mode == "fixed_r56" ? nlohmann::json("sum_local_radius_plus_one_at_least_57_v1") : nlohmann::json(nullptr)},
        {"global_exact_certificate_sha256", global_exact_certificate_sha256.empty() ? nlohmann::json(nullptr) : nlohmann::json(global_exact_certificate_sha256)},
        {"hamming_limit", config.hamming_limit}, {"adc_limit", config.adc_limit}, {"exact_limit", config.exact_limit},
        {"hamming_shortlist_export", config.shortlist_output.empty() ? nlohmann::json(nullptr) : nlohmann::json({{"path", config.shortlist_output.string()}, {"sha256", shortlist_export_sha256}, {"schema_version", 1}})},
        {"fixed_r56_candidate_diagnostic", config.candidate_diagnostic_output.empty() ? nlohmann::json(nullptr) : nlohmann::json({{"path", config.candidate_diagnostic_output.string()}, {"sha256", candidate_diagnostic_sha256}, {"schema_version", 1}})},
        {"deduplication", selected_deduplication == DeduplicationMode::TwoPassGenerationArray ? "two_pass_uint32_generation_array_v1" : "streaming_uint32_generation_array_v1"}, {"deduplication_mode", config.deduplication_mode}, {"hamming_backend", std::string(agent_memory::hamming_distance_backend_name(hamming.backend()))}, {"exact_vector_similarity_backend", std::string(agent_memory::vector_similarity_backend_name(vector_computer.backend()))},
        {"counters_per_query", {{"bucket_probes", static_cast<double>(diagnostics.probes) / per_query}, {"non_empty_probes", static_cast<double>(diagnostics.non_empty_probes) / per_query}, {"empty_probes", static_cast<double>(diagnostics.empty_probes) / per_query}, {"posting_visits", static_cast<double>(diagnostics.posting_visits) / per_query}, {"unique_candidates", static_cast<double>(diagnostics.unique_candidates) / per_query}, {"unique_candidates_per_posting_visit", diagnostics.posting_visits == 0 ? 0.0 : static_cast<double>(diagnostics.unique_candidates) / static_cast<double>(diagnostics.posting_visits)}, {"mean_posting_length_touched", mean_posting_length}, {"p95_posting_length_touched", posting_length_values.empty() ? 0.0 : percentile(posting_length_values, 0.95)}, {"candidate_checksum", diagnostics.candidate_checksum}, {"shortlist_checksum", diagnostics.shortlist_checksum}}},
        {"latency_ms_per_query", {{"key_enumeration", percentiles(keys)}, {"bucket_lookup", percentiles(lookups)}, {"posting_traversal", percentiles(traversal)}, {"generation_dedup", percentiles(deduplication)}, {"full_hamming_scoring", percentiles(hamming_samples)}, {"top_k_selection", percentiles(top_k)}, {"binary_adc", percentiles(adc)}, {"exact_rerank", percentiles(exact)}, {"candidate_generator_total", percentiles(candidate_generator)}, {"cascade_total", percentiles(cascade)}}},
        {"timing_ms_per_query_samples", {{"key_enumeration", timing_series(keys)}, {"bucket_lookup", timing_series(lookups)}, {"posting_traversal", timing_series(traversal)}, {"generation_dedup", timing_series(deduplication)}, {"full_hamming_scoring", timing_series(hamming_samples)}, {"top_k_selection", timing_series(top_k)}, {"binary_adc", timing_series(adc)}, {"exact_rerank", timing_series(exact)}, {"candidate_generator_total", timing_series(candidate_generator)}, {"cascade_total", timing_series(cascade)}}},
        {"conformance", {{"candidate_union_fixed_r56_checked", is_mih && config.mih_search_mode == "fixed_r56"}, {"global_exact_flat_ordering_checked", global_exact_mih}, {"global_exact_strict_stop_rule", global_exact_mih ? nlohmann::json("kth_distance_strictly_less_than_covered_radius_plus_one_v1") : nlohmann::json(nullptr)}, {"global_exact_cover_radius_mean", diagnostics.global_exact_stop_count == 0 ? nlohmann::json(nullptr) : nlohmann::json(static_cast<double>(diagnostics.global_exact_cover_radius_sum) / static_cast<double>(diagnostics.global_exact_stop_count))}, {"hamming_shortlist_checked", true}, {"checked_query_count", config.query_count}}},
        {"timing_scope", "Warm in-memory immutable backend. Candidate-generator total is independently timed from backend candidate generation through stable Hamming top-K; cascade total independently times that generator plus ADC and exact rerank. Stage values are separately timed components and must not be summed as a latency replacement. For streaming generation deduplication, posting traversal and generation-dedup stage values intentionally cover the same combined loop and must not be added. Global exact MIH extends a coverage certificate until the Kth discovered distance is strictly less than the integer lower bound for every unseen document. Excludes query encoding, full-corpus conformance scan, cold-cache I/O, index build, and process-wide memory."},
    };
    if(is_mih) {
        report["index_representation"] = backend_report["index_representation"];
        report["index_logical_bytes"] = backend_report["backend_index_logical_bytes"];
        report["index_logical_byte_breakdown"] = backend_report["backend_index_logical_byte_breakdown"];
    }
    std::ofstream output(report_path); if(!output) throw std::runtime_error("cannot write native sparse MIH report");
    output << report.dump(2) << '\n'; std::cout << report.dump(2) << '\n';
    return 0;
}

[[nodiscard]] int global_exact_fixture_test(const std::filesystem::path& fixture_path) {
    try {
        std::ifstream stream(fixture_path);
        if(!stream) throw std::runtime_error("cannot open native global exact MIH fixture");
        nlohmann::json fixture; stream >> fixture;
        if(fixture.value("schema_version", 0) != 1 || fixture.value("family", "") != "mih_global_exact_conformance_fixture_v1" || fixture.value("code_bits", 0) != kCodeBits || fixture.value("construction", "") != "splitmix64_word0_only_zero_extend_to_256_v1" || fixture.value("canonical_order", "") != "hamming_distance_ascending_then_document_position_ascending_v1") throw std::runtime_error("native global exact MIH fixture contract is invalid");
        const auto document_count = fixture.at("document_count").get<std::size_t>();
        const auto query_count = fixture.at("query_count").get<std::size_t>();
        const auto band_count = fixture.at("band_count").get<std::size_t>();
        const auto ks = fixture.at("ks").get<std::vector<std::size_t>>();
        const auto cutoff = fixture.at("expected_cutoff_at_k768_per_query").get<std::vector<std::size_t>>();
        if(document_count < 768U || query_count == 0U || cutoff.size() != query_count || ks != std::vector<std::size_t>{10U, 64U, 128U, 256U, 512U, 768U} || band_count == 0U || kCodeBits % band_count != 0U) throw std::runtime_error("native global exact MIH fixture dimensions are invalid");
        const auto& upstream = fixture.at("upstream_reference");
        if(upstream.value("repository", "") != "https://github.com/norouzi/mih" || upstream.value("commit", "") != "96a629de834c1b974b0c5e378ab1037ee42120ab" || upstream.value("required_cutoff_max", 0U) != 128U || *std::max_element(cutoff.begin(), cutoff.end()) > 128U) throw std::runtime_error("native global exact MIH fixture upstream contract differs");
        Input input; input.document_count = document_count; input.documents.assign(document_count * kWordCount, 0U);
        std::uint64_t document_state = parse_hex_u64(fixture.at("document_seed").get<std::string>());
        for(std::size_t position = 0; position < document_count; ++position) input.documents[position * kWordCount] = splitmix64_next(document_state);
        std::vector<std::uint64_t> queries(query_count * kWordCount, 0U);
        std::uint64_t query_state = parse_hex_u64(fixture.at("query_seed").get<std::string>());
        for(std::size_t position = 0; position < query_count; ++position) queries[position * kWordCount] = splitmix64_next(query_state);
        const std::vector<std::size_t> widths(band_count, kCodeBits / band_count);
        const auto bands = make_bands(widths);
        const SparseIndex index(input.documents, input.document_count, bands, DirectoryMode::FlatOpenAddress);
        const auto hamming = agent_memory::HammingDistanceComputer(kWordCount);
        std::vector<std::uint8_t> canonical;
        constexpr char magic[] = "agent-memory-global-exact-mih-fixture-output-v1";
        canonical.insert(canonical.end(), magic, magic + sizeof(magic) - 1U);
        for(std::size_t query_position = 0; query_position < query_count; ++query_position) {
            const auto* query = queries.data() + query_position * kWordCount;
            for(const auto k : ks) {
                GenerationDeduplicator deduplicator(input.document_count); QueryWorkspace workspace; Diagnostics diagnostics;
                const auto result = run_global_exact_query(index, input, query, bands, deduplicator, hamming, k, kCodeBits, workspace, diagnostics);
                verify_global_exact_conformance(input, query, result.shortlist, hamming, k, result.global_exact);
                if(k == 768U && result.shortlist.back().distance != cutoff[query_position]) throw std::runtime_error("native global exact MIH fixture cutoff differs");
                append_u32_le(canonical, static_cast<std::uint32_t>(query_position)); append_u32_le(canonical, static_cast<std::uint32_t>(k)); append_u64_le(canonical, result.shortlist.size());
                for(const auto& item : result.shortlist) { append_u32_le(canonical, static_cast<std::uint32_t>(item.distance)); append_u32_le(canonical, item.position); }
            }
        }
        if(agent_memory::sha256_bytes_hex(canonical) != fixture.at("canonical_outputs_sha256").get<std::string>()) throw std::runtime_error("native global exact MIH fixture canonical output differs");
    } catch(const std::exception& error) { std::cerr << "native global exact MIH fixture failed: " << error.what() << '\n'; return 1; }
    std::cout << "native global exact MIH fixture passed\n"; return 0;
}

[[nodiscard]] int self_test() {
    try {
        std::vector<std::uint64_t> codes(3U * kWordCount, 0); codes[kWordCount] = std::uint64_t{1} << 18U; codes[2U * kWordCount] = (std::uint64_t{1} << 63U) | 1U;
        const std::vector<std::size_t> widths{18, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17};
        const auto bands = make_bands(widths);
        const SparseIndex sorted_index(codes, 3, bands, DirectoryMode::SortedLowerBound);
        const SparseIndex flat_index(codes, 3, bands, DirectoryMode::FlatOpenAddress);
        const std::vector<std::uint64_t> singleton_codes(kWordCount, 0U);
        const SparseIndex singleton_flat_index(singleton_codes, 1, bands, DirectoryMode::FlatOpenAddress);
        Span singleton_span;
        if(flat_hash_capacity(1U) < 2U || !singleton_flat_index.find(0, 0, singleton_span) || singleton_span.last - singleton_span.first != 1U || singleton_flat_index.find(0, 1U, singleton_span)) throw std::runtime_error("native sparse MIH singleton flat directory differs");
        for(const auto* index : {&sorted_index, &flat_index}) {
            Span span;
            if(!index->find(0, 0, span) || span.last - span.first != 2 || span.values[span.first] != 0 || span.values[span.first + 1U] != 1 || index->find(0, 2, span) || extract_key(codes.data() + 2U * kWordCount, bands[3]) != (std::uint32_t{1} << 11U) || index->logical_bytes() == 0) throw std::runtime_error("sparse directory lookup differs");
        }
        Input input; input.document_count = 3; input.documents = codes;
        const auto hamming = agent_memory::HammingDistanceComputer(kWordCount);
        std::vector<std::uint32_t> reference_candidates;
        std::vector<Scored> reference_shortlist;
        for(const auto* index : {&sorted_index, &flat_index}) for(const auto mode : {DeduplicationMode::TwoPassGenerationArray, DeduplicationMode::StreamingGenerationArray}) {
            GenerationDeduplicator deduplicator(3); QueryWorkspace workspace; Diagnostics diagnostics; std::vector<std::uint32_t> candidates;
            const auto result = run_query(*index, input, codes.data(), codes.data(), bands, std::vector<int>(bands.size(), 0), mode, deduplicator, hamming, 3, workspace, diagnostics, &candidates);
            if(diagnostics.probes != bands.size() || diagnostics.unique_candidates != 3 || result.shortlist.size() != 3 || result.shortlist.front().position != 0 || result.timings.candidate_generator_total_ms < 0.0) throw std::runtime_error("native sparse MIH candidate pipeline differs");
            if(reference_candidates.empty()) { reference_candidates = candidates; reference_shortlist = result.shortlist; }
            else if(candidates != reference_candidates || result.shortlist.size() != reference_shortlist.size()) throw std::runtime_error("native sparse MIH challenger candidate union differs");
            else for(std::size_t position = 0; position < result.shortlist.size(); ++position) if(result.shortlist[position].position != reference_shortlist[position].position || result.shortlist[position].distance != reference_shortlist[position].distance) throw std::runtime_error("native sparse MIH challenger Hamming shortlist differs");
        }
        const auto diagnostic = diagnose_fixed_r56_candidate_union(input, 0U, codes.data(), reference_candidates, reference_shortlist, hamming, 3);
        if(diagnostic.candidate_union_size != 3U || diagnostic.global_fixed_r56_count != 3U || diagnostic.candidate_union_fixed_r56_count != 3U || diagnostic.exact_hamming_top_k_fixed_r56_count != 3U || diagnostic.candidate_union_exact_hamming_top_k_overlap != 3U || diagnostic.mih_shortlist_exact_hamming_top_k_overlap != 3U) throw std::runtime_error("native fixed-r56 candidate diagnostic differs");
        std::vector<std::uint64_t> exact_codes(4U * kWordCount, 0U);
        exact_codes[kWordCount] = std::uint64_t{1};
        exact_codes[2U * kWordCount] = std::uint64_t{2};
        exact_codes[3U * kWordCount] = std::uint64_t{3};
        Input exact_input; exact_input.document_count = 4U; exact_input.documents = exact_codes;
        const SparseIndex exact_index(exact_codes, 4U, bands, DirectoryMode::FlatOpenAddress);
        GenerationDeduplicator exact_deduplicator(4U); QueryWorkspace exact_workspace; Diagnostics exact_diagnostics;
        const auto exact_result = run_global_exact_query(exact_index, exact_input, exact_codes.data(), bands, exact_deduplicator, hamming, 2U, kCodeBits, exact_workspace, exact_diagnostics);
        if(exact_result.shortlist.size() != 2U || exact_result.shortlist[0].position != 0U || exact_result.shortlist[1].position != 1U || exact_result.shortlist[0].distance != 0U || exact_result.shortlist[1].distance != 1U || !exact_result.global_exact.strict_unseen_lower_bound_proved || exact_result.global_exact.kth_distance >= exact_result.global_exact.covered_radius + 1U) throw std::runtime_error("native global exact MIH tie-safe stop differs");
        verify_global_exact_conformance(exact_input, exact_codes.data(), exact_result.shortlist, hamming, 2U, exact_result.global_exact);
        const std::vector<std::size_t> locator_bits{0U, 1U, 18U, 63U};
        const auto locator_codes = pack_locator_codes(codes, locator_bits);
        const std::vector<std::size_t> locator_widths{4U};
        const auto locator_bands = make_bands(locator_widths);
        const SparseIndex locator_index(locator_codes, 3U, locator_bands, DirectoryMode::FlatOpenAddress, locator_bits.size());
        GenerationDeduplicator locator_deduplicator(3U); QueryWorkspace locator_workspace; Diagnostics locator_diagnostics;
        const auto locator_result = run_query(locator_index, input, codes.data(), locator_codes.data(), locator_bands, std::vector<int>{0}, DeduplicationMode::StreamingGenerationArray, locator_deduplicator, hamming, 3U, locator_workspace, locator_diagnostics);
        if(locator_result.shortlist.size() != 1U || locator_result.shortlist[0].position != 0U || locator_diagnostics.unique_candidates != 1U) throw std::runtime_error("native static locator MIH candidate pipeline differs");
#ifdef AGENT_MEMORY_ENABLE_HNSW_BENCHMARK
        Config hnsw_config; hnsw_config.backend = "hnsw"; hnsw_config.hnsw_connectivity = 2; hnsw_config.hnsw_ef_construction = 4; hnsw_config.hnsw_ef_search = 3;
        const HnswIndex hnsw(input, hnsw_config);
        Diagnostics hnsw_diagnostics; QueryWorkspace hnsw_workspace;
        const auto hnsw_result = run_hnsw_query(hnsw, input, codes.data(), hamming, 3, hnsw_workspace, hnsw_diagnostics);
        if(hnsw_result.shortlist.size() != 3 || hnsw_result.shortlist.front().position != 0 || hnsw.logical_bytes() == 0 || hnsw_diagnostics.unique_candidates != 3) throw std::runtime_error("native HNSW candidate pipeline differs");
#endif
        Config invalid; invalid.band_widths = widths; invalid.local_radii.assign(widths.size(), 0); invalid.query_count = 1; invalid.repeat_count = 1; invalid.hamming_limit = 1; invalid.adc_limit = 1; invalid.exact_limit = 1;
        bool rejected = false; try { validate_config(invalid); } catch(const std::invalid_argument&) { rejected = true; }
        if(!rejected) throw std::runtime_error("native sparse MIH invalid schedule was accepted");
        invalid.local_radii.assign(widths.size(), 3); invalid.directory_mode = "unknown";
        rejected = false; try { validate_config(invalid); } catch(const std::invalid_argument&) { rejected = true; }
        if(!rejected) throw std::runtime_error("native sparse MIH invalid directory mode was accepted");
        invalid.directory_mode = "sorted_lower_bound"; invalid.mih_search_mode = "global_exact"; invalid.local_radii.clear(); invalid.global_exact_max_cover_radius = kCodeBits - 1U;
        rejected = false; try { validate_config(invalid); } catch(const std::invalid_argument&) { rejected = true; }
        if(!rejected) throw std::runtime_error("native global exact MIH bounded radius was accepted");
    } catch(const std::exception& error) { std::cerr << "native sparse arbitrary-m MIH self-test failed: " << error.what() << '\n'; return 1; }
    std::cout << "native sparse arbitrary-m MIH self-test passed\n"; return 0;
}

} // namespace

int main(int argc, char** argv) {
    if(argc == 2 && std::string(argv[1]) == "--self-test") return self_test();
    if(argc == 3 && std::string(argv[1]) == "--global-exact-fixture") return global_exact_fixture_test(argv[2]);
    if(argc != 3) { std::cerr << "usage: agent-memory-mih-native-sparse-arbitrary-m <config.json> <report.json>\n"; return 2; }
    try { return run(load_config(argv[1]), argv[2]); }
    catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
