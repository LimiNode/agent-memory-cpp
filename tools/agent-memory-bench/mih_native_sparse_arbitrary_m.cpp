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
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

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
#ifndef AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256
#define AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256 "unconfigured"
#endif
#ifndef AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256
#define AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256 "unconfigured"
#endif

struct Config final {
    std::filesystem::path input_directory;
    std::vector<std::size_t> band_widths;
    std::vector<int> local_radii;
    std::size_t query_count = 0;
    std::size_t warmup_count = 1;
    std::size_t repeat_count = 0;
    std::size_t hamming_limit = 768;
    std::size_t adc_limit = 256;
    std::size_t exact_limit = 256;
    std::uint64_t query_seed = 20260815;
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

struct QueryResult final { Timings timings; std::vector<Scored> shortlist; };

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

void validate_config(const Config& value) {
    if(value.band_widths.empty() || value.band_widths.size() != value.local_radii.size() || value.query_count == 0 || value.repeat_count == 0 || value.hamming_limit == 0 || value.adc_limit == 0 || value.exact_limit == 0 || value.adc_limit > value.hamming_limit || value.exact_limit > value.adc_limit || std::accumulate(value.band_widths.begin(), value.band_widths.end(), std::size_t{0}) != kCodeBits) throw std::invalid_argument("native sparse MIH config is invalid");
    for(std::size_t index = 0; index < value.band_widths.size(); ++index) if(value.band_widths[index] == 0 || value.band_widths[index] > 32 || value.local_radii[index] < 0 || value.local_radii[index] > static_cast<int>(value.band_widths[index])) throw std::invalid_argument("native sparse MIH schedule is invalid");
    if(std::accumulate(value.local_radii.begin(), value.local_radii.end(), std::size_t{0}, [](const std::size_t total, const int radius) { return total + static_cast<std::size_t>(radius) + 1U; }) < 57U) throw std::invalid_argument("native sparse MIH schedule does not preserve fixed-r56 inclusion");
}

[[nodiscard]] double milliseconds(const Clock::time_point start, const Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - start).count();
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
    result.band_widths = value.at("band_widths").get<std::vector<std::size_t>>();
    result.local_radii = value.at("local_radii").get<std::vector<int>>();
    result.query_count = value.at("query_count").get<std::size_t>();
    result.warmup_count = value.value("warmup_count", result.warmup_count);
    result.repeat_count = value.at("repeat_count").get<std::size_t>();
    result.hamming_limit = value.value("hamming_limit", result.hamming_limit);
    result.adc_limit = value.value("adc_limit", result.adc_limit);
    result.exact_limit = value.value("exact_limit", result.exact_limit);
    result.query_seed = value.value("query_seed", result.query_seed);
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

struct Directory final { std::vector<std::uint32_t> keys; std::vector<std::uint32_t> offsets; std::vector<std::uint32_t> postings; };

class SparseIndex final {
public:
    SparseIndex(const std::vector<std::uint64_t>& codes, const std::size_t document_count, std::vector<Band> bands)
        : m_bands(std::move(bands)), m_directories(m_bands.size()) {
        if(document_count == 0 || codes.size() != document_count * kWordCount || m_bands.empty()) throw std::invalid_argument("native sparse MIH input is invalid");
        std::size_t expected_offset = 0;
        for(const auto& band : m_bands) { if(band.offset != expected_offset || band.width == 0 || band.width > 32) throw std::invalid_argument("native sparse MIH band partition is invalid"); expected_offset += band.width; }
        if(expected_offset != kCodeBits) throw std::invalid_argument("native sparse MIH band coverage differs");
        for(std::size_t band_index = 0; band_index < m_bands.size(); ++band_index) {
            std::vector<Entry> entries; entries.reserve(document_count);
            for(std::size_t position = 0; position < document_count; ++position) entries.push_back({extract_key(codes.data() + position * kWordCount, m_bands[band_index]), static_cast<std::uint32_t>(position)});
            std::sort(entries.begin(), entries.end(), [](const Entry& left, const Entry& right) { return left.key == right.key ? left.position < right.position : left.key < right.key; });
            auto& directory = m_directories[band_index]; directory.keys.reserve(document_count); directory.offsets.reserve(document_count + 1U); directory.postings.reserve(document_count);
            for(const auto& entry : entries) { if(directory.keys.empty() || directory.keys.back() != entry.key) { directory.keys.push_back(entry.key); directory.offsets.push_back(static_cast<std::uint32_t>(directory.postings.size())); } directory.postings.push_back(entry.position); }
            directory.offsets.push_back(static_cast<std::uint32_t>(directory.postings.size()));
        }
    }

    [[nodiscard]] bool find(const std::size_t band_index, const std::uint32_t key, Span& result) const {
        const auto& directory = m_directories.at(band_index);
        const auto found = std::lower_bound(directory.keys.begin(), directory.keys.end(), key);
        if(found == directory.keys.end() || *found != key) return false;
        const auto index = static_cast<std::size_t>(found - directory.keys.begin());
        result = {directory.postings.data(), directory.offsets[index], directory.offsets[index + 1U]};
        return true;
    }

    [[nodiscard]] std::size_t logical_bytes() const noexcept {
        std::size_t result = 0;
        for(const auto& directory : m_directories) result += directory.keys.size() * sizeof(std::uint32_t) + directory.offsets.size() * sizeof(std::uint32_t) + directory.postings.size() * sizeof(std::uint32_t);
        return result;
    }

    [[nodiscard]] nlohmann::json logical_byte_breakdown() const {
        std::size_t keys = 0, offsets = 0, postings = 0;
        for(const auto& directory : m_directories) { keys += directory.keys.size() * sizeof(std::uint32_t); offsets += directory.offsets.size() * sizeof(std::uint32_t); postings += directory.postings.size() * sizeof(std::uint32_t); }
        return {{"sorted_unique_keys", keys}, {"offsets", offsets}, {"contiguous_uint32_postings", postings}, {"total", keys + offsets + postings}};
    }

private:
    std::vector<Band> m_bands;
    std::vector<Directory> m_directories;
};

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

[[nodiscard]] std::size_t select_top_k(std::vector<Scored>& values, const std::size_t limit) {
    const auto count = std::min(limit, values.size());
    if(count < values.size()) { std::nth_element(values.begin(), values.begin() + static_cast<std::ptrdiff_t>(count), values.end(), closer); values.resize(count); }
    std::sort(values.begin(), values.end(), closer);
    return count;
}

[[nodiscard]] QueryResult run_query(const SparseIndex& index, const Input& input, const std::uint64_t* query, const std::vector<Band>& bands, const std::vector<int>& radii, GenerationDeduplicator& deduplicator, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit, QueryWorkspace& workspace, Diagnostics& diagnostics, std::vector<std::uint32_t>* raw_candidates = nullptr) {
    workspace.clear();
    const auto candidate_start = Clock::now();
    const auto enumeration_start = Clock::now();
    for(std::size_t band = 0; band < bands.size(); ++band) enumerate_keys(extract_key(query, bands[band]), bands[band].width, radii[band], [&](const std::uint32_t key) { workspace.probes.push_back({band, key}); });
    const auto enumeration_end = Clock::now();
    const auto lookup_start = Clock::now();
    for(const auto& probe : workspace.probes) {
        ++diagnostics.probes;
        Span span;
        if(index.find(probe.band, probe.key, span)) { ++diagnostics.non_empty_probes; workspace.spans.push_back(span); diagnostics.posting_lengths.push_back(static_cast<std::uint32_t>(span.last - span.first)); }
        else ++diagnostics.empty_probes;
    }
    const auto lookup_end = Clock::now();
    const auto traversal_start = Clock::now();
    for(const auto& span : workspace.spans) { diagnostics.posting_visits += span.last - span.first; workspace.visited.insert(workspace.visited.end(), span.values + static_cast<std::ptrdiff_t>(span.first), span.values + static_cast<std::ptrdiff_t>(span.last)); }
    const auto traversal_end = Clock::now();
    deduplicator.next_query();
    const auto dedup_start = Clock::now();
    for(const auto position : workspace.visited) if(deduplicator.visit(position)) workspace.candidates.push_back(position);
    const auto dedup_end = Clock::now();
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
    result.timings = {milliseconds(enumeration_start, enumeration_end), milliseconds(lookup_start, lookup_end), milliseconds(traversal_start, traversal_end), milliseconds(dedup_start, dedup_end), milliseconds(hamming_start, hamming_end), milliseconds(top_k_start, top_k_end), milliseconds(candidate_start, top_k_end)};
    result.shortlist = workspace.scored;
    return result;
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

void verify_candidate_conformance(const Input& input, const std::uint64_t* query, const std::vector<std::uint32_t>& candidates, const std::vector<Scored>& shortlist, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit) {
    std::vector<bool> present(input.document_count, false);
    for(const auto position : candidates) present[position] = true;
    std::vector<Scored> expected;
    for(std::size_t position = 0; position < input.document_count; ++position) {
        const auto distance = hamming.distance_words(query, input.documents.data() + position * kWordCount);
        if(distance <= 56U && !present[position]) throw std::runtime_error("native sparse MIH fixed-r56 candidate inclusion differs");
        if(present[position]) expected.push_back({static_cast<std::uint32_t>(position), distance});
    }
    static_cast<void>(select_top_k(expected, hamming_limit));
    if(expected.size() != shortlist.size()) throw std::runtime_error("native sparse MIH Hamming shortlist size differs");
    for(std::size_t index = 0; index < expected.size(); ++index) if(expected[index].position != shortlist[index].position || expected[index].distance != shortlist[index].distance) throw std::runtime_error("native sparse MIH Hamming shortlist differs");
}

[[nodiscard]] nlohmann::json timing_series(const std::vector<double>& values) { return values; }

[[nodiscard]] bool same_candidate_diagnostics(const Diagnostics& left, const Diagnostics& right) {
    return left.probes == right.probes && left.non_empty_probes == right.non_empty_probes && left.empty_probes == right.empty_probes &&
        left.posting_visits == right.posting_visits && left.unique_candidates == right.unique_candidates &&
        left.candidate_checksum == right.candidate_checksum && left.shortlist_checksum == right.shortlist_checksum &&
        left.posting_lengths == right.posting_lengths;
}

[[nodiscard]] int run(const Config& config, const std::filesystem::path& report_path) {
    const auto input = load_input(config.input_directory);
    if(config.query_count > input.query_count || config.hamming_limit > input.document_count) throw std::invalid_argument("native sparse MIH query or Hamming limit exceeds input");
    const auto bands = make_bands(config.band_widths);
    const SparseIndex index(input.documents, input.document_count, bands);
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
            const auto result = run_query(index, input, input.queries.data() + position * kWordCount, bands, config.local_radii, deduplicator, hamming, config.hamming_limit, workspace, local, verify ? &raw_candidates : nullptr);
            std::uint64_t checksum = 0;
            const auto adc_ms = binary_adc_rerank(input, input.query_projections.data() + position * kCodeBits, result.shortlist, config.adc_limit, adc_scored, adc_positions, checksum);
            const auto exact_count = std::min(config.exact_limit, adc_positions.size());
            const auto exact_ms = exact_rerank(input, input.query_vectors.data() + position * input.embedding_dimension, adc_positions, exact_count, vector_computer, exact_scored, checksum);
            if(checksum == 0) throw std::runtime_error("native sparse MIH cascade checksum is invalid");
            const auto cascade_ms = milliseconds(cascade_start, Clock::now());
            if(verify) verify_candidate_conformance(input, input.queries.data() + position * kWordCount, raw_candidates, result.shortlist, hamming, config.hamming_limit);
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
    {
        GenerationDeduplicator verification_deduplicator(input.document_count);
        QueryWorkspace verification_workspace;
        for(const auto position : query_positions) {
            Diagnostics verification_diagnostics;
            std::vector<std::uint32_t> raw_candidates;
            const auto result = run_query(index, input, input.queries.data() + position * kWordCount, bands, config.local_radii, verification_deduplicator, hamming, config.hamming_limit, verification_workspace, verification_diagnostics, &raw_candidates);
            verify_candidate_conformance(input, input.queries.data() + position * kWordCount, raw_candidates, result.shortlist, hamming, config.hamming_limit);
        }
    }

    const auto per_query = static_cast<double>(config.query_count);
    const auto mean_posting_length = diagnostics.non_empty_probes == 0 ? 0.0 : static_cast<double>(diagnostics.posting_visits) / static_cast<double>(diagnostics.non_empty_probes);
    std::vector<double> posting_length_values; posting_length_values.reserve(diagnostics.posting_lengths.size()); for(const auto value : diagnostics.posting_lengths) posting_length_values.push_back(static_cast<double>(value));
    nlohmann::json report{
        {"schema_version", 1},
        {"family", "mih_native_sparse_arbitrary_m_v1"},
        {"input_manifest", input.manifest},
        {"input_manifest_sha256", input.manifest_sha256},
        {"benchmark_config_sha256", config.sha256},
        {"benchmark_source_bundle_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
        {"benchmark_source_files_sha256", {{"tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp", AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_SOURCE_SHA256}, {"tools/agent-memory-bench/materialize-mih-storage-input.py", AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_MATERIALIZER_SOURCE_SHA256}, {"src/agent_memory/index/VectorSimilarityComputer.cpp", AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_VECTOR_SIMILARITY_SOURCE_SHA256}, {"src/agent_memory/index/BinarySignature.cpp", AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_BINARY_SIGNATURE_SOURCE_SHA256}}},
        {"build_environment", build_environment()},
        {"index_representation", "sorted_unique_uint32_keys_plus_uint32_offsets_plus_contiguous_uint32_postings_v1"},
        {"index_logical_bytes", index.logical_bytes()},
        {"index_logical_byte_breakdown", index.logical_byte_breakdown()},
        {"query_count", config.query_count}, {"query_seed", config.query_seed}, {"query_selection_algorithm", "std_mt19937_64_shuffle_v1"}, {"selected_query_positions", query_positions},
        {"warmup_count", config.warmup_count}, {"repeat_count", config.repeat_count},
        {"band_widths", config.band_widths}, {"local_radii", config.local_radii}, {"fixed_radius", 56}, {"fixed_radius_exact_inclusion", "sum_local_radius_plus_one_at_least_57_v1"},
        {"hamming_limit", config.hamming_limit}, {"adc_limit", config.adc_limit}, {"exact_limit", config.exact_limit},
        {"deduplication", "uint32_generation_array_v1"}, {"hamming_backend", std::string(agent_memory::hamming_distance_backend_name(hamming.backend()))}, {"exact_vector_similarity_backend", std::string(agent_memory::vector_similarity_backend_name(vector_computer.backend()))},
        {"counters_per_query", {{"bucket_probes", static_cast<double>(diagnostics.probes) / per_query}, {"non_empty_probes", static_cast<double>(diagnostics.non_empty_probes) / per_query}, {"empty_probes", static_cast<double>(diagnostics.empty_probes) / per_query}, {"posting_visits", static_cast<double>(diagnostics.posting_visits) / per_query}, {"unique_candidates", static_cast<double>(diagnostics.unique_candidates) / per_query}, {"unique_candidates_per_posting_visit", diagnostics.posting_visits == 0 ? 0.0 : static_cast<double>(diagnostics.unique_candidates) / static_cast<double>(diagnostics.posting_visits)}, {"mean_posting_length_touched", mean_posting_length}, {"p95_posting_length_touched", posting_length_values.empty() ? 0.0 : percentile(posting_length_values, 0.95)}, {"candidate_checksum", diagnostics.candidate_checksum}, {"shortlist_checksum", diagnostics.shortlist_checksum}}},
        {"latency_ms_per_query", {{"key_enumeration", percentiles(keys)}, {"bucket_lookup", percentiles(lookups)}, {"posting_traversal", percentiles(traversal)}, {"generation_dedup", percentiles(deduplication)}, {"full_hamming_scoring", percentiles(hamming_samples)}, {"top_k_selection", percentiles(top_k)}, {"binary_adc", percentiles(adc)}, {"exact_rerank", percentiles(exact)}, {"candidate_generator_total", percentiles(candidate_generator)}, {"cascade_total", percentiles(cascade)}}},
        {"timing_ms_per_query_samples", {{"key_enumeration", timing_series(keys)}, {"bucket_lookup", timing_series(lookups)}, {"posting_traversal", timing_series(traversal)}, {"generation_dedup", timing_series(deduplication)}, {"full_hamming_scoring", timing_series(hamming_samples)}, {"top_k_selection", timing_series(top_k)}, {"binary_adc", timing_series(adc)}, {"exact_rerank", timing_series(exact)}, {"candidate_generator_total", timing_series(candidate_generator)}, {"cascade_total", timing_series(cascade)}}},
        {"conformance", {{"candidate_union_fixed_r56_checked", true}, {"hamming_shortlist_checked", true}, {"checked_query_count", config.query_count}}},
        {"timing_scope", "Warm in-memory immutable sparse index. Candidate-generator total is independently timed from local-key enumeration through stable Hamming top-K; cascade total independently times that generator plus ADC and exact rerank. Stage values are separately timed components and must not be summed as a latency replacement. Excludes query encoding, full-corpus conformance scan, cold-cache I/O, index build, and process-wide memory."},
    };
    std::ofstream output(report_path); if(!output) throw std::runtime_error("cannot write native sparse MIH report");
    output << report.dump(2) << '\n'; std::cout << report.dump(2) << '\n';
    return 0;
}

[[nodiscard]] int self_test() {
    try {
        std::vector<std::uint64_t> codes(3U * kWordCount, 0); codes[kWordCount] = std::uint64_t{1} << 18U; codes[2U * kWordCount] = (std::uint64_t{1} << 63U) | 1U;
        const std::vector<std::size_t> widths{18, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17};
        const auto bands = make_bands(widths); const SparseIndex index(codes, 3, bands);
        Span span; if(!index.find(0, 0, span) || span.last - span.first != 2 || span.values[span.first] != 0 || span.values[span.first + 1U] != 1 || extract_key(codes.data() + 2U * kWordCount, bands[3]) != (std::uint32_t{1} << 11U) || index.logical_bytes() == 0) throw std::runtime_error("sparse directory lookup differs");
        Input input; input.document_count = 3; input.documents = codes;
        GenerationDeduplicator deduplicator(3); QueryWorkspace workspace; Diagnostics diagnostics; const auto hamming = agent_memory::HammingDistanceComputer(kWordCount);
        const auto result = run_query(index, input, codes.data(), bands, std::vector<int>(bands.size(), 0), deduplicator, hamming, 3, workspace, diagnostics);
        if(diagnostics.probes != bands.size() || diagnostics.unique_candidates != 3 || result.shortlist.size() != 3 || result.shortlist.front().position != 0 || result.timings.candidate_generator_total_ms < 0.0) throw std::runtime_error("native sparse MIH candidate pipeline differs");
        Config invalid; invalid.band_widths = widths; invalid.local_radii.assign(widths.size(), 0); invalid.query_count = 1; invalid.repeat_count = 1; invalid.hamming_limit = 1; invalid.adc_limit = 1; invalid.exact_limit = 1;
        bool rejected = false; try { validate_config(invalid); } catch(const std::invalid_argument&) { rejected = true; }
        if(!rejected) throw std::runtime_error("native sparse MIH invalid schedule was accepted");
    } catch(const std::exception& error) { std::cerr << "native sparse arbitrary-m MIH self-test failed: " << error.what() << '\n'; return 1; }
    std::cout << "native sparse arbitrary-m MIH self-test passed\n"; return 0;
}

} // namespace

int main(int argc, char** argv) {
    if(argc == 2 && std::string(argv[1]) == "--self-test") return self_test();
    if(argc != 3) { std::cerr << "usage: agent-memory-mih-native-sparse-arbitrary-m <config.json> <report.json>\n"; return 2; }
    try { return run(load_config(argv[1]), argv[2]); }
    catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
