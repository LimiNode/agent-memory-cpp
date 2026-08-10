#include <agent_memory.hpp>
#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>

#include <mdbx_containers/KeyValueTable.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::size_t kBandCount = 16;
constexpr std::size_t kBandBits = 16;
constexpr std::size_t kBucketCount = std::size_t{1} << kBandBits;

struct Config final {
    std::filesystem::path input;
    std::filesystem::path mdbx_path;
    std::size_t query_count = 128;
    std::size_t repeat_count = 5;
    std::size_t page_entries = 256;
    std::uint64_t query_seed = 20260810;
    std::vector<std::size_t> global_radii{48, 56, 64};
};

struct Input final {
    std::size_t document_count = 0;
    std::size_t query_count = 0;
    std::vector<std::uint64_t> documents;
    std::vector<std::uint64_t> queries;
    nlohmann::json manifest;
};

struct LookupDiagnostics final {
    std::size_t bucket_probes = 0;
    std::size_t metadata_hits = 0;
    std::size_t posting_page_reads = 0;
    std::size_t visited_postings = 0;
    std::size_t unique_candidates = 0;
    std::uint64_t candidate_checksum = 0;
};

nlohmann::json evaluator_build_environment() {
    return {
        {"compiler_id", AGENT_MEMORY_EVALUATOR_COMPILER_ID},
        {"compiler_version", AGENT_MEMORY_EVALUATOR_COMPILER_VERSION},
        {"cxx_standard", AGENT_MEMORY_EVALUATOR_CXX_STANDARD},
        {"cxx_extensions", AGENT_MEMORY_EVALUATOR_CXX_EXTENSIONS != 0},
        {"generator", AGENT_MEMORY_EVALUATOR_GENERATOR},
        {"build_configuration", AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION},
        {"system_name", AGENT_MEMORY_EVALUATOR_SYSTEM_NAME},
        {"system_processor", AGENT_MEMORY_EVALUATOR_SYSTEM_PROCESSOR},
        {"pointer_bits", AGENT_MEMORY_EVALUATOR_POINTER_BITS},
        {"configured_environment_sha256", AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256},
        {"base_cxx_flags_sha256", AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256},
        {"active_configuration_flags_sha256", AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256},
    };
}

double milliseconds(Clock::time_point start, Clock::time_point stop) {
    return std::chrono::duration<double, std::milli>(stop - start).count();
}

double median(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    const auto middle = values.size() / 2;
    return values.size() % 2 == 0 ? (values[middle - 1] + values[middle]) / 2.0 : values[middle];
}

std::size_t read_size(const nlohmann::json& value, const char* field, std::size_t fallback) {
    if(!value.contains(field)) return fallback;
    if(!value.at(field).is_number_unsigned()) throw std::invalid_argument(std::string("invalid unsigned field: ") + field);
    return value.at(field).get<std::size_t>();
}

Config load_config(const std::filesystem::path& path) {
    std::ifstream stream(path);
    nlohmann::json value;
    stream >> value;
    Config result;
    result.input = value.at("input_directory").get<std::string>();
    result.mdbx_path = value.at("mdbx_path").get<std::string>();
    result.query_count = read_size(value, "query_count", result.query_count);
    result.repeat_count = read_size(value, "repeat_count", result.repeat_count);
    result.page_entries = read_size(value, "page_entries", result.page_entries);
    result.query_seed = value.value("query_seed", result.query_seed);
    if(value.contains("global_radii")) result.global_radii = value.at("global_radii").get<std::vector<std::size_t>>();
    if(result.input.empty() || result.mdbx_path.empty() || result.query_count == 0 || result.repeat_count == 0 || result.page_entries == 0 || result.global_radii.empty()) throw std::invalid_argument("MIH storage benchmark config is invalid");
    if(std::any_of(result.global_radii.begin(), result.global_radii.end(), [](std::size_t radius) { return radius != 48 && radius != 56 && radius != 64; })) throw std::invalid_argument("global radius is outside the predeclared MIH matrix");
    return result;
}

std::string required_sha256(const nlohmann::json& value, const char* field) {
    const auto result = value.at(field).get<std::string>();
    if(result.size() != 64 || !std::all_of(result.begin(), result.end(), [](char character) {
        return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f');
    })) {
        throw std::runtime_error(std::string("invalid SHA-256 field: ") + field);
    }
    return result;
}

std::vector<std::uint64_t> read_words(
    const std::filesystem::path& path,
    std::size_t expected_words,
    const std::string& expected_sha256) {
    const auto expected_bytes = expected_words * sizeof(std::uint64_t);
    if(!std::filesystem::is_regular_file(path) || std::filesystem::file_size(path) != expected_bytes) {
        throw std::runtime_error("packed code payload size is invalid");
    }
    if(agent_memory::sha256_file_hex(path) != expected_sha256) {
        throw std::runtime_error("packed code payload SHA-256 is invalid");
    }
    std::ifstream stream(path, std::ios::binary);
    std::vector<std::uint64_t> values(expected_words);
    stream.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(values.size() * sizeof(std::uint64_t)));
    if(stream.gcount() != static_cast<std::streamsize>(values.size() * sizeof(std::uint64_t))) throw std::runtime_error("packed code payload is truncated");
    return values;
}

Input load_input(const std::filesystem::path& root) {
    std::ifstream stream(root / "manifest.json");
    Input result;
    stream >> result.manifest;
    const auto& manifest = result.manifest;
    if(manifest.value("schema_version", 0) != 1 || manifest.value("family", "") != "mih_storage_benchmark_input_v1" || manifest.value("code_bits", 0) != 256 || manifest.value("word_count", 0) != 4 || manifest.value("itq_iterations", 0) != 50) throw std::runtime_error("storage input manifest contract is invalid");
    result.document_count = manifest.at("document_count").get<std::size_t>();
    result.query_count = manifest.at("query_count").get<std::size_t>();
    if(result.document_count == 0 || result.query_count == 0) throw std::runtime_error("storage input is empty");
    result.documents = read_words(
        root / manifest.at("document_codes_file").get<std::string>(),
        result.document_count * 4,
        required_sha256(manifest, "document_codes_sha256"));
    result.queries = read_words(
        root / manifest.at("query_codes_file").get<std::string>(),
        result.query_count * 4,
        required_sha256(manifest, "query_codes_sha256"));
    return result;
}

std::uint16_t bucket_key(const std::uint64_t* words, std::size_t band) noexcept {
    const auto offset = band * kBandBits;
    return static_cast<std::uint16_t>((words[offset / 64] >> (offset % 64)) & 0xFFFFU);
}

std::vector<int> schedule(std::size_t radius) {
    const auto quotient = radius / kBandCount;
    const auto remainder = radius % kBandCount;
    std::vector<int> radii(kBandCount, static_cast<int>(quotient) - 1);
    for(std::size_t index = 0; index <= remainder; ++index) radii[index] = static_cast<int>(quotient);
    return radii;
}

template<class Callback>
void probe_keys(std::uint16_t key, int radius, Callback&& callback) {
    const auto enumerate = [&](const auto& self, std::size_t first, int remaining, std::uint16_t value) -> void {
        if(remaining == 0) {
            callback(value);
            return;
        }
        for(std::size_t bit = first; bit + static_cast<std::size_t>(remaining) <= kBandBits; ++bit) {
            self(self, bit + 1, remaining - 1, static_cast<std::uint16_t>(value ^ (std::uint16_t{1} << bit)));
        }
    };
    for(int distance = 0; distance <= radius; ++distance) enumerate(enumerate, 0, distance, key);
}

class CsrIndex final {
public:
    explicit CsrIndex(const Input& input) : m_offsets(kBandCount * (kBucketCount + 1), 0) {
        for(std::size_t band = 0; band < kBandCount; ++band) for(std::size_t document = 0; document < input.document_count; ++document) ++m_offsets[band * (kBucketCount + 1) + bucket_key(input.documents.data() + document * 4, band) + 1];
        std::size_t cursor = 0;
        for(std::size_t band = 0; band < kBandCount; ++band) {
            auto* offsets = m_offsets.data() + band * (kBucketCount + 1);
            for(std::size_t key = 0; key < kBucketCount; ++key) { const auto count = offsets[key + 1]; offsets[key] = cursor; cursor += count; offsets[key + 1] = cursor; }
        }
        m_postings.resize(cursor); auto fill = m_offsets;
        for(std::size_t band = 0; band < kBandCount; ++band) for(std::size_t document = 0; document < input.document_count; ++document) { const auto key = bucket_key(input.documents.data() + document * 4, band); m_postings[fill[band * (kBucketCount + 1) + key]++] = static_cast<std::uint32_t>(document); }
    }
    LookupDiagnostics lookup(const std::uint64_t* query, std::size_t radius, std::vector<unsigned char>& seen) const {
        LookupDiagnostics output; const auto radii = schedule(radius);
        for(std::size_t band = 0; band < kBandCount; ++band) probe_keys(bucket_key(query, band), radii[band], [&](std::uint16_t key) { ++output.bucket_probes; const auto base = band * (kBucketCount + 1); for(auto index = m_offsets[base + key]; index < m_offsets[base + key + 1]; ++index) { const auto position = m_postings[index]; ++output.visited_postings; if(seen[position] == 0) { seen[position] = 1; ++output.unique_candidates; output.candidate_checksum += position + 1; } } });
        return output;
    }
    std::size_t bytes() const noexcept { return m_offsets.size() * sizeof(std::size_t) + m_postings.size() * sizeof(std::uint32_t); }
private: std::vector<std::size_t> m_offsets; std::vector<std::uint32_t> m_postings;
};

std::string key(char type, std::size_t band, std::uint16_t bucket, std::uint16_t page = 0) {
    std::string value(6, '\0'); value[0] = type; value[1] = static_cast<char>(band); value[2] = static_cast<char>(bucket >> 8); value[3] = static_cast<char>(bucket); value[4] = static_cast<char>(page >> 8); value[5] = static_cast<char>(page); return value;
}
std::string encode(const std::vector<std::uint32_t>& values, std::size_t start, std::size_t stop) { std::string result((stop - start) * 4, '\0'); for(std::size_t index = start; index < stop; ++index) { const auto value = values[index]; for(std::size_t byte = 0; byte < 4; ++byte) result[(index - start) * 4 + byte] = static_cast<char>(value >> (byte * 8)); } return result; }
std::uint32_t decode_u32(const std::string& value, std::size_t offset) { if(offset + 4 > value.size()) throw std::runtime_error("MDBX posting payload is truncated"); return static_cast<std::uint32_t>(static_cast<unsigned char>(value[offset])) | (static_cast<std::uint32_t>(static_cast<unsigned char>(value[offset + 1])) << 8) | (static_cast<std::uint32_t>(static_cast<unsigned char>(value[offset + 2])) << 16) | (static_cast<std::uint32_t>(static_cast<unsigned char>(value[offset + 3])) << 24); }

class MdbxIndex final {
public:
    MdbxIndex(const Input& input, std::filesystem::path path, std::size_t page_entries) : m_page_entries(page_entries) {
        std::filesystem::remove(path); mdbxc::Config config; config.pathname = path.string(); config.max_dbs = 4; config.no_subdir = true; config.relative_to_exe = false; m_connection = mdbxc::Connection::create(config); m_table = std::make_unique<mdbxc::KeyValueTable<std::string, std::string>>(m_connection, "mih_postings");
        std::unordered_map<std::uint32_t, std::vector<std::uint32_t>> buckets;
        for(std::size_t band = 0; band < kBandCount; ++band) for(std::size_t document = 0; document < input.document_count; ++document) buckets[(static_cast<std::uint32_t>(band) << 16) | bucket_key(input.documents.data() + document * 4, band)].push_back(static_cast<std::uint32_t>(document));
        auto transaction = m_connection->transaction(mdbxc::TransactionMode::WRITABLE);
        for(const auto& entry : buckets) { const auto band = entry.first >> 16; const auto bucket = static_cast<std::uint16_t>(entry.first); const auto pages = (entry.second.size() + page_entries - 1) / page_entries; m_table->insert_or_assign(key('m', band, bucket), std::to_string(pages), transaction); for(std::size_t page = 0; page < pages; ++page) { const auto start = page * page_entries; m_table->insert_or_assign(key('p', band, bucket, static_cast<std::uint16_t>(page)), encode(entry.second, start, std::min(entry.second.size(), start + page_entries)), transaction); } }
        transaction.commit(); m_path = std::move(path);
    }
    LookupDiagnostics lookup(const std::uint64_t* query, std::size_t radius, std::vector<unsigned char>& seen) const {
        LookupDiagnostics output; const auto radii = schedule(radius); auto transaction = m_connection->transaction(mdbxc::TransactionMode::READ_ONLY);
        for(std::size_t band = 0; band < kBandCount; ++band) probe_keys(bucket_key(query, band), radii[band], [&](std::uint16_t bucket) { ++output.bucket_probes; const auto pages = m_table->find(key('m', band, bucket), transaction); if(!pages) return; const auto count = static_cast<std::size_t>(std::stoul(*pages)); ++output.metadata_hits; for(std::size_t page = 0; page < count; ++page) { const auto payload = m_table->find(key('p', band, bucket, static_cast<std::uint16_t>(page)), transaction); if(!payload || payload->size() % 4 != 0) throw std::runtime_error("MDBX posting page is invalid"); ++output.posting_page_reads; for(std::size_t offset = 0; offset < payload->size(); offset += 4) { const auto position = decode_u32(*payload, offset); ++output.visited_postings; if(position >= seen.size()) throw std::runtime_error("MDBX posting position is invalid"); if(seen[position] == 0) { seen[position] = 1; ++output.unique_candidates; output.candidate_checksum += position + 1; } } } });
        transaction.commit(); return output;
    }
    void empty_read_transaction() const {
        auto transaction = m_connection->transaction(mdbxc::TransactionMode::READ_ONLY);
        transaction.commit();
    }
    std::size_t bytes() const { return static_cast<std::size_t>(std::filesystem::file_size(m_path)); }
private: std::size_t m_page_entries; std::filesystem::path m_path; std::shared_ptr<mdbxc::Connection> m_connection; std::unique_ptr<mdbxc::KeyValueTable<std::string, std::string>> m_table;
};

void validate_candidate_unions(
    const CsrIndex& csr,
    const MdbxIndex& mdbx,
    const Input& input,
    const std::vector<std::size_t>& query_positions,
    const std::vector<std::size_t>& global_radii) {
    for(const auto radius : global_radii) {
        for(const auto position : query_positions) {
            std::vector<unsigned char> csr_seen(input.document_count);
            std::vector<unsigned char> mdbx_seen(input.document_count);
            static_cast<void>(csr.lookup(input.queries.data() + position * 4, radius, csr_seen));
            static_cast<void>(mdbx.lookup(input.queries.data() + position * 4, radius, mdbx_seen));
            if(csr_seen != mdbx_seen) {
                throw std::runtime_error("CSR and MDBX candidate unions differ during untimed preflight");
            }
        }
    }
}

std::size_t probe_count(std::size_t radius) {
    std::size_t count = 0;
    for(const auto local_radius : schedule(radius)) {
        probe_keys(0, local_radius, [&](std::uint16_t) { ++count; });
    }
    return count;
}

Input synthetic_input() {
    Input input;
    input.document_count = 5;
    input.query_count = 2;
    input.documents = {
        0, 0, 0, 0,
        1, 0, 0, 0,
        0, 2, 0, 0,
        0, 0, 4, 0,
        0, 0, 0, 8,
    };
    input.queries = {
        0, 0, 0, 0,
        1, 2, 4, 8,
    };
    return input;
}

void self_test_input_payload_validation(const std::filesystem::path& mdbx_path) {
    const auto root = mdbx_path.parent_path() / "mih-storage-benchmark-payload-self-test";
    std::filesystem::create_directories(root);
    const auto document_path = root / "document.bin";
    const auto query_path = root / "query.bin";
    const std::array<std::uint64_t, 4> code{0, 0, 0, 0};
    for(const auto* path : {&document_path, &query_path}) {
        std::ofstream output(*path, std::ios::binary | std::ios::trunc);
        output.write(reinterpret_cast<const char*>(code.data()), static_cast<std::streamsize>(sizeof(code)));
    }
    nlohmann::json manifest{
        {"schema_version", 1},
        {"family", "mih_storage_benchmark_input_v1"},
        {"code_bits", 256},
        {"word_count", 4},
        {"itq_iterations", 50},
        {"document_count", 1},
        {"query_count", 1},
        {"document_codes_file", document_path.filename().string()},
        {"query_codes_file", query_path.filename().string()},
        {"document_codes_sha256", agent_memory::sha256_file_hex(document_path)},
        {"query_codes_sha256", agent_memory::sha256_file_hex(query_path)},
    };
    std::ofstream(root / "manifest.json") << manifest.dump(2) << '\n';
    static_cast<void>(load_input(root));
    {
        std::fstream output(document_path, std::ios::binary | std::ios::in | std::ios::out);
        output.put(static_cast<char>(1));
    }
    bool rejected = false;
    try {
        static_cast<void>(load_input(root));
    } catch(const std::runtime_error&) {
        rejected = true;
    }
    if(!rejected) throw std::runtime_error("modified packed code payload was accepted");
}

int self_test(const std::filesystem::path& mdbx_path) {
    try {
        if(probe_count(48) != 2752 || probe_count(56) != 7232 || probe_count(64) != 12972) {
            throw std::runtime_error("MIH probe schedule count is invalid");
        }
        bool rejected_truncated_payload = false;
        try {
            static_cast<void>(decode_u32("bad", 0));
        } catch(const std::runtime_error&) {
            rejected_truncated_payload = true;
        }
        if(!rejected_truncated_payload) throw std::runtime_error("truncated posting payload was accepted");
        self_test_input_payload_validation(mdbx_path);

        const auto input = synthetic_input();
        CsrIndex csr(input);
        MdbxIndex mdbx(input, mdbx_path, 2);
        for(const auto radius : {std::size_t{48}, std::size_t{56}, std::size_t{64}}) {
            for(std::size_t query = 0; query < input.query_count; ++query) {
                std::vector<unsigned char> csr_seen(input.document_count);
                std::vector<unsigned char> mdbx_seen(input.document_count);
                const auto csr_result = csr.lookup(input.queries.data() + query * 4, radius, csr_seen);
                const auto mdbx_result = mdbx.lookup(input.queries.data() + query * 4, radius, mdbx_seen);
                if(csr_result.bucket_probes != mdbx_result.bucket_probes ||
                    csr_result.visited_postings != mdbx_result.visited_postings ||
                    csr_result.unique_candidates != mdbx_result.unique_candidates ||
                    csr_result.candidate_checksum != mdbx_result.candidate_checksum ||
                    csr_seen != mdbx_seen) {
                    throw std::runtime_error("CSR and MDBX self-test candidate unions differ");
                }
            }
        }
        std::cout << "MIH storage benchmark self-test passed\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "MIH storage benchmark self-test failed: " << error.what() << '\n';
        return 1;
    }
}

int run(const Config& config, const std::filesystem::path& report_path) {
    const auto input = load_input(config.input); if(config.query_count > input.query_count) throw std::invalid_argument("configured query count exceeds input");
    const auto csr_start = Clock::now(); CsrIndex csr(input); const auto csr_build_ms = milliseconds(csr_start, Clock::now());
    const auto mdbx_start = Clock::now(); MdbxIndex mdbx(input, config.mdbx_path, config.page_entries); const auto mdbx_build_ms = milliseconds(mdbx_start, Clock::now());
    std::vector<std::size_t> query_positions(input.query_count); std::iota(query_positions.begin(), query_positions.end(), 0); std::mt19937_64 random(config.query_seed); std::shuffle(query_positions.begin(), query_positions.end(), random); query_positions.resize(config.query_count);
    validate_candidate_unions(csr, mdbx, input, query_positions, config.global_radii);
    nlohmann::json rows = nlohmann::json::array();
    for(const auto radius : config.global_radii) {
        std::vector<double> csr_samples, mdbx_samples, transaction_samples; LookupDiagnostics expected{};
        for(std::size_t repeat = 0; repeat < config.repeat_count; ++repeat) { LookupDiagnostics csr_total{}, mdbx_total{}; std::vector<unsigned char> csr_seen(input.document_count), mdbx_seen(input.document_count); auto start = Clock::now(); for(const auto position : query_positions) { std::fill(csr_seen.begin(), csr_seen.end(), 0); const auto value = csr.lookup(input.queries.data() + position * 4, radius, csr_seen); csr_total.bucket_probes += value.bucket_probes; csr_total.visited_postings += value.visited_postings; csr_total.unique_candidates += value.unique_candidates; csr_total.candidate_checksum += value.candidate_checksum; } csr_samples.push_back(milliseconds(start, Clock::now())); start = Clock::now(); for(const auto position : query_positions) { std::fill(mdbx_seen.begin(), mdbx_seen.end(), 0); const auto value = mdbx.lookup(input.queries.data() + position * 4, radius, mdbx_seen); mdbx_total.bucket_probes += value.bucket_probes; mdbx_total.metadata_hits += value.metadata_hits; mdbx_total.posting_page_reads += value.posting_page_reads; mdbx_total.visited_postings += value.visited_postings; mdbx_total.unique_candidates += value.unique_candidates; mdbx_total.candidate_checksum += value.candidate_checksum; } mdbx_samples.push_back(milliseconds(start, Clock::now())); if(csr_total.bucket_probes != mdbx_total.bucket_probes || csr_total.visited_postings != mdbx_total.visited_postings || csr_total.unique_candidates != mdbx_total.unique_candidates || csr_total.candidate_checksum != mdbx_total.candidate_checksum) throw std::runtime_error("CSR and MDBX candidate unions differ"); expected = mdbx_total; }
        for(std::size_t repeat = 0; repeat < config.repeat_count; ++repeat) { const auto start = Clock::now(); for(std::size_t query = 0; query < config.query_count; ++query) mdbx.empty_read_transaction(); transaction_samples.push_back(milliseconds(start, Clock::now())); }
        rows.push_back({{"global_radius", radius}, {"csr_warm_lookup_decode_dedup_ms_median", median(csr_samples)}, {"mdbx_warm_lookup_decode_dedup_ms_median", median(mdbx_samples)}, {"mdbx_empty_read_transaction_ms_median", median(transaction_samples)}, {"bucket_metadata_lookups_per_query", expected.bucket_probes / config.query_count}, {"mdbx_metadata_hits_per_query", expected.metadata_hits / config.query_count}, {"mdbx_posting_page_reads_per_query", expected.posting_page_reads / config.query_count}, {"visited_postings_per_query", expected.visited_postings / config.query_count}, {"unique_candidates_per_query", expected.unique_candidates / config.query_count}, {"candidate_checksum_timing_diagnostic", expected.candidate_checksum}});
    }
    nlohmann::json report{{"schema_version", 4}, {"family", "mih_mdbx_csr_storage_benchmark_v1"}, {"timing_scope", "warm bucket metadata lookup, posting-page lookup, decode, candidate deduplication, and one MDBX read-transaction lifecycle per query; excludes query encoding, Hamming ranking, ADC, exact rerank, cold-cache I/O, and OS-cache eviction"}, {"candidate_union_preflight", "all selected query/radius CSR and MDBX seen vectors compared exactly before timing"}, {"input_manifest", input.manifest}, {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256}, {"evaluator_build_environment", evaluator_build_environment()}, {"storage_stack", {{"provenance_authoritative", AGENT_MEMORY_STORAGE_PROVENANCE_AUTHORITATIVE != 0}, {"provenance_reason", AGENT_MEMORY_STORAGE_PROVENANCE_REASON}, {"libmdbx_commit", AGENT_MEMORY_LIBMDBX_REVISION}, {"mdbx_containers_commit", AGENT_MEMORY_MDBX_CONTAINERS_REVISION}}}, {"query_count", config.query_count}, {"query_seed", config.query_seed}, {"query_selection_algorithm", "std_mt19937_64_shuffle_v1"}, {"selected_query_positions", query_positions}, {"repeat_count", config.repeat_count}, {"page_entries", config.page_entries}, {"csr_build_ms", csr_build_ms}, {"csr_logical_bytes", csr.bytes()}, {"mdbx_build_ms", mdbx_build_ms}, {"mdbx_file_bytes", mdbx.bytes()}, {"rows", rows}};
    std::ofstream output(report_path); if(!output) throw std::runtime_error("cannot write benchmark report"); output << report.dump(2) << '\n'; std::cout << report.dump(2) << '\n'; return 0;
}
} // namespace

int main(int argc, char** argv) {
    if(argc == 3 && std::string(argv[1]) == "--self-test") return self_test(argv[2]);
    if(argc != 3) {
        std::cerr << "usage: agent-memory-mih-storage-bench <config.json> <report.json>\n";
        return 2;
    }
    try {
        return run(load_config(argv[1]), argv[2]);
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
