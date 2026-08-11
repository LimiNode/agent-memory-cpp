#include <agent_memory.hpp>
#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>

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
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::size_t kCodeBits = 256;
constexpr std::size_t kWordCount = 4;

struct Config final {
    std::filesystem::path input;
    std::size_t query_count = 128;
    std::size_t repeat_count = 5;
    std::uint64_t query_seed = 20260811;
    std::size_t band_count = 16;
    int local_probe_radius = 0;
    int global_radius = 64;
    std::size_t hamming_limit = 512;
};

struct Input final {
    std::size_t document_count = 0;
    std::size_t query_count = 0;
    std::vector<std::uint64_t> documents;
    std::vector<std::uint64_t> queries;
    nlohmann::json manifest;
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

[[nodiscard]] Input load_input(const std::filesystem::path& root) {
    std::ifstream input(root / "manifest.json");
    if(!input) throw std::runtime_error("cannot open native MIH input manifest");
    Input result;
    input >> result.manifest;
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
    config.input = value.at("input_directory").get<std::string>();
    config.query_count = read_size(value, "query_count", config.query_count);
    config.repeat_count = read_size(value, "repeat_count", config.repeat_count);
    config.query_seed = value.value("query_seed", config.query_seed);
    config.band_count = read_size(value, "band_count", config.band_count);
    config.local_probe_radius = value.value("local_probe_radius", config.local_probe_radius);
    config.global_radius = value.value("global_radius", config.global_radius);
    config.hamming_limit = read_size(value, "hamming_limit", config.hamming_limit);
    if(config.query_count == 0 || config.repeat_count == 0 || config.band_count == 0 || kCodeBits % config.band_count != 0 ||
       config.hamming_limit == 0 || config.local_probe_radius < 0 || config.global_radius < -1) {
        throw std::invalid_argument("native MIH config is invalid");
    }
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
    std::vector<Scored>* selected = nullptr
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
        const auto full = full_score(input, input.queries.data(), computer);
        std::vector<std::uint32_t> all_positions{0, 1, 2, 3};
        require_guarantee(full, all_positions, config);
        const auto radius_64 = global_schedule(64, 16);
        if(radius_64.size() != 16 || radius_64.front() != 4 ||
           !std::all_of(radius_64.begin() + 1, radius_64.end(), [](int value) { return value == 3; })) {
            throw std::runtime_error("global MIH radius schedule is invalid");
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
    QueryDiagnostics expected{};
    for(std::size_t repeat = 0; repeat < config.repeat_count; ++repeat) {
        GenerationDeduplicator deduplicator(input.document_count);
        Timings aggregate{};
        QueryDiagnostics aggregate_diagnostics{};
        for(const auto position : query_positions) {
            QueryDiagnostics current{};
            const auto timing = run_query(index, input, input.queries.data() + position * kWordCount, radii, deduplicator, computer, config.hamming_limit, current);
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
        {"schema_version", 1},
        {"family", "mih_native_hot_path_v1"},
        {"input_manifest", input.manifest},
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
        {"timing_ms_per_query_median", {
            {"probe_enumeration", median(probes_samples)},
            {"posting_traversal", median(traversal_samples)},
            {"generation_array_dedup", median(dedup_samples)},
            {"full_hamming_on_candidates", median(hamming_samples)},
            {"top_k_selection", median(top_samples)},
            {"candidate_generator_to_hamming_top_k_total", median(total_samples)},
        }},
        {"hamming_top_k_recall", hamming_recall_sum / divisor},
        {"timing_scope", "warm direct-address CSR bucket spans, posting copy, generation-array deduplication, full Hamming over unique candidates, and stable top-k; excludes query encoding, ADC, exact E5 rerank, full-corpus oracle, cold-cache I/O, and process-wide memory"},
    };
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
