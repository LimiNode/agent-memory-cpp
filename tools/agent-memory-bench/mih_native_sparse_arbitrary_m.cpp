#include <agent_memory.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr std::size_t kCodeBits = 256;
constexpr std::size_t kWordCount = kCodeBits / 64;
using Clock = std::chrono::steady_clock;

struct Config final {
    std::filesystem::path input_directory;
    std::vector<std::size_t> band_widths;
    std::vector<int> local_radii;
    std::size_t query_count = 0;
    std::size_t repeat_count = 0;
    std::size_t hamming_limit = 768;
};

struct Input final {
    std::size_t document_count = 0;
    std::size_t query_count = 0;
    std::vector<std::uint64_t> documents;
    std::vector<std::uint64_t> queries;
    nlohmann::json manifest;
};

struct Diagnostics final {
    std::size_t probes = 0;
    std::size_t empty_probes = 0;
    std::size_t posting_visits = 0;
    std::size_t unique_candidates = 0;
    std::size_t candidate_checksum = 0;
};

struct Timings final {
    double key_enumeration_ms = 0.0;
    double bucket_lookup_ms = 0.0;
    double posting_traversal_ms = 0.0;
    double deduplication_ms = 0.0;
    double hamming_ms = 0.0;
    double top_k_ms = 0.0;
    double candidate_generator_total_ms = 0.0;
    double cascade_total_ms = 0.0;
};

[[nodiscard]] double milliseconds(const Clock::time_point start, const Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - start).count();
}

[[nodiscard]] std::string required_sha256(const nlohmann::json& value, const char* field) {
    const auto result = value.value(field, "");
    if(result.size() != 64 || result.find_first_not_of("0123456789abcdef") != std::string::npos) throw std::runtime_error(std::string("input manifest ") + field + " is invalid");
    return result;
}

[[nodiscard]] std::vector<std::uint64_t> read_words(const std::filesystem::path& path, const std::size_t count, const std::string& expected_sha256) {
    if(agent_memory::sha256_file_hex(path) != expected_sha256) throw std::runtime_error("native sparse MIH code payload SHA-256 differs");
    std::vector<std::uint64_t> values(count);
    std::ifstream input(path, std::ios::binary);
    input.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(count * sizeof(std::uint64_t)));
    if(input.gcount() != static_cast<std::streamsize>(count * sizeof(std::uint64_t))) throw std::runtime_error("native sparse MIH code payload is truncated");
    return values;
}

[[nodiscard]] Input load_input(const std::filesystem::path& root) {
    std::ifstream stream(root / "manifest.json");
    if(!stream) throw std::runtime_error("cannot open native sparse MIH input manifest");
    Input result; stream >> result.manifest;
    const auto& manifest = result.manifest;
    if(manifest.value("schema_version", 0) != 1 || manifest.value("family", "") != "mih_storage_benchmark_input_v1" || manifest.value("code_bits", 0) != kCodeBits || manifest.value("word_count", 0) != kWordCount) throw std::runtime_error("native sparse MIH input contract is invalid");
    result.document_count = manifest.at("document_count").get<std::size_t>();
    result.query_count = manifest.at("query_count").get<std::size_t>();
    if(result.document_count == 0 || result.query_count == 0) throw std::runtime_error("native sparse MIH input is empty");
    result.documents = read_words(root / manifest.at("document_codes_file").get<std::string>(), result.document_count * kWordCount, required_sha256(manifest, "document_codes_sha256"));
    result.queries = read_words(root / manifest.at("query_codes_file").get<std::string>(), result.query_count * kWordCount, required_sha256(manifest, "query_codes_sha256"));
    return result;
}

[[nodiscard]] Config load_config(const std::filesystem::path& path) {
    std::ifstream stream(path); if(!stream) throw std::runtime_error("cannot open native sparse MIH config");
    nlohmann::json value; stream >> value;
    Config result;
    result.input_directory = value.at("input_directory").get<std::string>();
    result.band_widths = value.at("band_widths").get<std::vector<std::size_t>>();
    result.local_radii = value.at("local_radii").get<std::vector<int>>();
    result.query_count = value.at("query_count").get<std::size_t>();
    result.repeat_count = value.at("repeat_count").get<std::size_t>();
    result.hamming_limit = value.value("hamming_limit", result.hamming_limit);
    if(result.band_widths.empty() || result.band_widths.size() != result.local_radii.size() || result.query_count == 0 || result.repeat_count == 0 || result.hamming_limit == 0 || std::accumulate(result.band_widths.begin(), result.band_widths.end(), std::size_t{0}) != kCodeBits) throw std::invalid_argument("native sparse MIH config is invalid");
    for(std::size_t index = 0; index < result.band_widths.size(); ++index) if(result.band_widths[index] == 0 || result.band_widths[index] > 32 || result.local_radii[index] < 0 || result.local_radii[index] > static_cast<int>(result.band_widths[index])) throw std::invalid_argument("native sparse MIH schedule is invalid");
    return result;
}

struct Band final {
    std::size_t offset = 0;
    std::size_t width = 0;
};

struct Entry final {
    std::uint32_t key = 0;
    std::uint32_t position = 0;
};

struct Directory final {
    std::vector<std::uint32_t> keys;
    std::vector<std::uint32_t> offsets;
    std::vector<std::uint32_t> postings;
};

[[nodiscard]] std::uint32_t extract_key(const std::uint64_t* code, const Band& band) {
    if(band.width == 0 || band.width > 32 || band.offset + band.width > kCodeBits) {
        throw std::invalid_argument("native sparse MIH band is invalid");
    }
    const auto word = band.offset / 64;
    const auto shift = band.offset % 64;
    std::uint64_t value = code[word] >> shift;
    if(shift + band.width > 64) {
        value |= code[word + 1] << (64 - shift);
    }
    const auto mask = band.width == 32 ? std::numeric_limits<std::uint32_t>::max() : (std::uint32_t{1} << band.width) - 1U;
    return static_cast<std::uint32_t>(value) & mask;
}

class SparseIndex final {
public:
    SparseIndex(const std::vector<std::uint64_t>& codes, std::size_t document_count, std::vector<Band> bands)
        : m_bands(std::move(bands)), m_directories(m_bands.size()) {
        if(document_count == 0 || codes.size() != document_count * kWordCount || m_bands.empty()) {
            throw std::invalid_argument("native sparse MIH input is invalid");
        }
        std::size_t expected_offset = 0;
        for(const auto& band : m_bands) {
            if(band.offset != expected_offset || band.width == 0 || band.width > 32) {
                throw std::invalid_argument("native sparse MIH band partition is invalid");
            }
            expected_offset += band.width;
        }
        if(expected_offset != kCodeBits) throw std::invalid_argument("native sparse MIH band coverage differs");
        for(std::size_t band_index = 0; band_index < m_bands.size(); ++band_index) {
            std::vector<Entry> entries;
            entries.reserve(document_count);
            for(std::size_t position = 0; position < document_count; ++position) {
                entries.push_back({extract_key(codes.data() + position * kWordCount, m_bands[band_index]), static_cast<std::uint32_t>(position)});
            }
            std::sort(entries.begin(), entries.end(), [](const Entry& left, const Entry& right) { return left.key == right.key ? left.position < right.position : left.key < right.key; });
            auto& directory = m_directories[band_index];
            for(const auto& entry : entries) {
                if(directory.keys.empty() || directory.keys.back() != entry.key) {
                    directory.keys.push_back(entry.key);
                    directory.offsets.push_back(static_cast<std::uint32_t>(directory.postings.size()));
                }
                directory.postings.push_back(entry.position);
            }
            directory.offsets.push_back(static_cast<std::uint32_t>(directory.postings.size()));
        }
    }

    [[nodiscard]] const std::vector<std::uint32_t>* find(std::size_t band_index, std::uint32_t key, std::size_t& first, std::size_t& last) const {
        const auto& directory = m_directories.at(band_index);
        const auto found = std::lower_bound(directory.keys.begin(), directory.keys.end(), key);
        if(found == directory.keys.end() || *found != key) return nullptr;
        const auto index = static_cast<std::size_t>(found - directory.keys.begin());
        first = directory.offsets[index];
        last = directory.offsets[index + 1];
        return &directory.postings;
    }

    [[nodiscard]] std::size_t logical_bytes() const noexcept {
        std::size_t result = 0;
        for(const auto& directory : m_directories) result += directory.keys.size() * sizeof(std::uint32_t) + directory.offsets.size() * sizeof(std::uint32_t) + directory.postings.size() * sizeof(std::uint32_t);
        return result;
    }

private:
    std::vector<Band> m_bands;
    std::vector<Directory> m_directories;
};

class GenerationDeduplicator final {
public:
    explicit GenerationDeduplicator(std::size_t document_count) : m_generation(document_count, 0) {}

    void next_query() {
        if(m_current == std::numeric_limits<std::uint32_t>::max()) {
            std::fill(m_generation.begin(), m_generation.end(), 0);
            m_current = 1;
        } else {
            ++m_current;
        }
    }

    [[nodiscard]] bool visit(std::uint32_t position) noexcept {
        if(m_generation[position] == m_current) return false;
        m_generation[position] = m_current;
        return true;
    }

private:
    std::vector<std::uint32_t> m_generation;
    std::uint32_t m_current = 0;
};

struct Scored final {
    std::uint32_t position = 0;
    std::size_t distance = 0;
};

[[nodiscard]] bool closer(const Scored& left, const Scored& right) noexcept {
    return left.distance == right.distance ? left.position < right.position : left.distance < right.distance;
}

template<class Callback>
void enumerate_keys(const std::uint32_t base, const std::size_t width, const int radius, Callback&& callback) {
    const auto recurse = [&](const auto& self, const std::size_t first, const int remaining, const std::uint32_t value) -> void {
        if(remaining == 0) {
            callback(value);
            return;
        }
        for(std::size_t bit = first; bit + static_cast<std::size_t>(remaining) <= width; ++bit) self(self, bit + 1, remaining - 1, value ^ (std::uint32_t{1} << bit));
    };
    for(int distance = 0; distance <= radius; ++distance) recurse(recurse, 0, distance, base);
}

[[nodiscard]] std::vector<Band> make_bands(const std::vector<std::size_t>& widths) {
    std::vector<Band> result; result.reserve(widths.size());
    std::size_t offset = 0;
    for(const auto width : widths) {
        result.push_back({offset, width});
        offset += width;
    }
    return result;
}

[[nodiscard]] std::size_t select_top_k(std::vector<Scored>& values, const std::size_t limit) {
    const auto count = std::min(limit, values.size());
    if(count < values.size()) {
        std::nth_element(values.begin(), values.begin() + static_cast<std::ptrdiff_t>(count), values.end(), closer);
        values.resize(count);
    }
    std::sort(values.begin(), values.end(), closer);
    return count;
}

[[nodiscard]] Timings run_query(const SparseIndex& index, const Input& input, const std::uint64_t* query, const std::vector<Band>& bands, const std::vector<int>& radii, GenerationDeduplicator& deduplicator, const agent_memory::HammingDistanceComputer& hamming, const std::size_t hamming_limit, Diagnostics& diagnostics) {
    const auto total_start = Clock::now();
    std::vector<std::pair<const std::vector<std::uint32_t>*, std::pair<std::size_t, std::size_t>>> spans;
    const auto enumeration_start = Clock::now();
    std::vector<std::vector<std::uint32_t>> keys(bands.size());
    for(std::size_t band = 0; band < bands.size(); ++band) enumerate_keys(extract_key(query, bands[band]), bands[band].width, radii[band], [&](const std::uint32_t key) { keys[band].push_back(key); });
    const auto enumeration_end = Clock::now();
    const auto lookup_start = Clock::now();
    for(std::size_t band = 0; band < keys.size(); ++band) for(const auto key : keys[band]) {
        ++diagnostics.probes;
        std::size_t first = 0, last = 0;
        const auto* postings = index.find(band, key, first, last);
        if(postings == nullptr) ++diagnostics.empty_probes;
        else spans.push_back({postings, {first, last}});
    }
    const auto lookup_end = Clock::now();
    std::vector<std::uint32_t> visited;
    const auto traversal_start = Clock::now();
    for(const auto& span : spans) {
        diagnostics.posting_visits += span.second.second - span.second.first;
    }
    visited.reserve(diagnostics.posting_visits);
    for(const auto& span : spans) visited.insert(visited.end(), span.first->begin() + static_cast<std::ptrdiff_t>(span.second.first), span.first->begin() + static_cast<std::ptrdiff_t>(span.second.second));
    const auto traversal_end = Clock::now();
    std::vector<std::uint32_t> candidates;
    candidates.reserve(visited.size());
    deduplicator.next_query();
    const auto dedup_start = Clock::now();
    for(const auto position : visited) if(deduplicator.visit(position)) candidates.push_back(position);
    const auto dedup_end = Clock::now();
    diagnostics.unique_candidates = candidates.size();
    for(const auto position : candidates) diagnostics.candidate_checksum += static_cast<std::size_t>(position) + 1U;
    const auto hamming_start = Clock::now();
    std::vector<Scored> scored; scored.reserve(candidates.size());
    for(const auto position : candidates) scored.push_back({position, hamming.distance_words(query, input.documents.data() + static_cast<std::size_t>(position) * kWordCount)});
    const auto hamming_end = Clock::now();
    const auto top_k_start = Clock::now();
    static_cast<void>(select_top_k(scored, hamming_limit));
    const auto top_k_end = Clock::now();
    return {milliseconds(enumeration_start, enumeration_end), milliseconds(lookup_start, lookup_end), milliseconds(traversal_start, traversal_end), milliseconds(dedup_start, dedup_end), milliseconds(hamming_start, hamming_end), milliseconds(top_k_start, top_k_end), milliseconds(total_start, dedup_end), milliseconds(total_start, top_k_end)};
}

[[nodiscard]] int self_test() {
    try {
        std::vector<std::uint64_t> codes(3 * kWordCount, 0);
        codes[kWordCount] = 1U << 17;
        codes[2 * kWordCount] = (std::uint64_t{1} << 63) | 1U;
        const std::vector<Band> bands{{0, 18}, {18, 17}, {35, 17}, {52, 17}, {69, 17}, {86, 17}, {103, 17}, {120, 17}, {137, 17}, {154, 17}, {171, 17}, {188, 17}, {205, 17}, {222, 17}, {239, 17}};
        const SparseIndex index(codes, 3, bands);
        std::size_t first = 0, last = 0;
        const auto* postings = index.find(0, 0, first, last);
        if(postings == nullptr || last - first != 2 || (*postings)[first] != 0 || (*postings)[first + 1] != 1 || index.logical_bytes() == 0) throw std::runtime_error("sparse directory lookup differs");
        GenerationDeduplicator dedup(3); dedup.next_query();
        if(!dedup.visit(1) || dedup.visit(1)) throw std::runtime_error("generation deduplication differs");
    } catch(const std::exception& error) {
        std::cerr << "native sparse arbitrary-m MIH self-test failed: " << error.what() << '\n';
        return 1;
    }
    std::cout << "native sparse arbitrary-m MIH self-test passed\n";
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    if(argc == 2 && std::string(argv[1]) == "--self-test") return self_test();
    std::cerr << "usage: agent-memory-mih-native-sparse-arbitrary-m --self-test\n";
    return 2;
}
