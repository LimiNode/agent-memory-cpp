#include <agent_memory.hpp>
#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>

#include <mdbx_containers/KeyValueTable.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <memory>
#include <numeric>
#include <queue>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

struct StageTiming final {
    double address_generation = 0.0;
    double mdbx_lookup_and_decode = 0.0;
    double generation_array_dedup_and_ceiling = 0.0;
    double hamming_and_top_k = 0.0;
    double binary_adc_and_top_k = 0.0;
    double exact_e5_and_top_k = 0.0;
    double total = 0.0;
};

struct Counters final {
    std::size_t requested_addresses = 0;
    std::size_t accepted_probes = 0;
    std::size_t metadata_hits = 0;
    std::size_t posting_page_reads = 0;
    std::size_t posting_entries = 0;
    std::size_t posting_bytes = 0;
    std::size_t unique_candidates = 0;
    std::size_t hamming_count = 0;
    std::size_t adc_count = 0;
};

struct QueryResult final {
    StageTiming timing;
    Counters counters;
    std::vector<std::uint32_t> addresses;
    std::vector<std::uint32_t> candidates;
    std::vector<std::uint32_t> hamming;
    std::vector<std::uint32_t> adc;
    std::vector<std::uint32_t> exact;
};

struct CommonInput final {
    std::size_t document_count = 0;
    std::size_t query_count = 0;
    std::vector<std::uint8_t> document_codes;
    std::vector<std::uint8_t> query_codes;
    std::vector<float> query_projection;
    std::vector<float> adc_centroids;
    std::vector<std::uint32_t> document_id_rank;
    std::vector<float> document_vectors;
    std::vector<float> query_vectors;
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

std::string require_sha256(const nlohmann::json& value, const char* field) {
    const auto result = value.at(field).get<std::string>();
    if(result.size() != 64 || !std::all_of(result.begin(), result.end(), [](char character) {
        return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f');
    })) throw std::runtime_error(std::string("invalid SHA-256 field: ") + field);
    return result;
}

std::size_t element_count(const nlohmann::json& payload) {
    std::size_t result = 1;
    for(const auto dimension : payload.at("shape")) result *= dimension.get<std::size_t>();
    return result;
}

template<class Value>
std::vector<Value> read_array(const std::filesystem::path& root, const nlohmann::json& payload) {
    const auto path = root / payload.at("file").get<std::string>();
    const auto count = element_count(payload);
    if(!std::filesystem::is_regular_file(path) || std::filesystem::file_size(path) != count * sizeof(Value))
        throw std::runtime_error("native MDBX payload size differs: " + path.string());
    if(agent_memory::sha256_file_hex(path) != require_sha256(payload, "sha256"))
        throw std::runtime_error("native MDBX payload SHA-256 differs: " + path.string());
    std::ifstream stream(path, std::ios::binary);
    std::vector<Value> result(count);
    stream.read(reinterpret_cast<char*>(result.data()), static_cast<std::streamsize>(count * sizeof(Value)));
    if(stream.gcount() != static_cast<std::streamsize>(count * sizeof(Value)))
        throw std::runtime_error("native MDBX payload is truncated: " + path.string());
    return result;
}

std::vector<std::uint8_t> u32_bytes(const std::vector<std::uint32_t>& values) {
    std::vector<std::uint8_t> result(values.size() * 4);
    for(std::size_t index = 0; index < values.size(); ++index)
        for(std::size_t byte = 0; byte < 4; ++byte)
            result[index * 4 + byte] = static_cast<std::uint8_t>(values[index] >> (byte * 8));
    return result;
}

std::string sequence_sha256(const std::vector<std::uint32_t>& values) {
    return agent_memory::sha256_bytes_hex(u32_bytes(values));
}

void append_u32(std::vector<std::uint8_t>& bytes, std::uint32_t value) {
    for(std::size_t byte = 0; byte < 4; ++byte)
        bytes.push_back(static_cast<std::uint8_t>(value >> (byte * 8)));
}

void append_query_sequence(std::vector<std::uint8_t>& bytes, std::size_t query,
                           const std::vector<std::uint32_t>& values) {
    append_u32(bytes, static_cast<std::uint32_t>(query));
    append_u32(bytes, static_cast<std::uint32_t>(values.size()));
    const auto packed = u32_bytes(values);
    bytes.insert(bytes.end(), packed.begin(), packed.end());
}

std::vector<std::uint32_t> learned_addresses(const float* logits, std::size_t bits,
                                             std::size_t count) {
    std::uint32_t base = 0;
    std::vector<std::size_t> order(bits);
    std::iota(order.begin(), order.end(), 0);
    for(std::size_t bit = 0; bit < bits; ++bit) if(logits[bit] >= 0.0F) base |= std::uint32_t{1} << bit;
    std::stable_sort(order.begin(), order.end(), [&](std::size_t left, std::size_t right) {
        return std::abs(logits[left]) < std::abs(logits[right]);
    });
    std::vector<std::uint32_t> masks{0};
    if(count > 1) {
        using State = std::tuple<double, std::uint32_t, std::size_t>;
        std::priority_queue<State, std::vector<State>, std::greater<State>> queue;
        queue.emplace(static_cast<double>(std::abs(logits[order[0]])),
                      std::uint32_t{1} << order[0], 0);
        while(!queue.empty() && masks.size() < count) {
            const auto [cost, mask, last] = queue.top();
            queue.pop();
            masks.push_back(mask);
            const auto following = last + 1;
            if(following < bits) {
                const auto last_bit = std::uint32_t{1} << order[last];
                const auto next_bit = std::uint32_t{1} << order[following];
                queue.emplace(cost - std::abs(logits[order[last]]) + std::abs(logits[order[following]]),
                              mask ^ last_bit ^ next_bit, following);
                queue.emplace(cost + std::abs(logits[order[following]]), mask | next_bit, following);
            }
        }
    }
    std::vector<std::uint32_t> result;
    result.reserve(masks.size());
    for(const auto mask : masks) result.push_back(base ^ mask);
    return result;
}

std::vector<std::uint32_t> pca_addresses(const float* logits, std::size_t bits,
                                         std::size_t count) {
    std::uint32_t base = 0;
    for(std::size_t bit = 0; bit < bits; ++bit) if(logits[bit] >= 0.0F) base |= std::uint32_t{1} << bit;
    std::vector<std::pair<double, std::uint32_t>> masks{{0.0, 0}};
    const auto enumerate = [&](const auto& self, std::size_t first, std::size_t remaining,
                               std::uint32_t mask, double cost) -> void {
        if(remaining == 0) { masks.emplace_back(cost, mask); return; }
        for(std::size_t bit = first; bit + remaining <= bits; ++bit)
            self(self, bit + 1, remaining - 1, mask | (std::uint32_t{1} << bit),
                 cost + std::abs(logits[bit]));
    };
    for(std::size_t flips = 1; flips <= std::min<std::size_t>(3, bits); ++flips)
        enumerate(enumerate, 0, flips, 0, 0.0);
    std::sort(masks.begin(), masks.end(), [](const auto& left, const auto& right) {
        return left.first == right.first ? left.second < right.second : left.first < right.first;
    });
    if(masks.size() > count) masks.resize(count);
    std::vector<std::uint32_t> result;
    result.reserve(masks.size());
    for(const auto& value : masks) result.push_back(base ^ value.second);
    return result;
}

std::string key(std::uint8_t route, std::uint16_t address, char kind, std::uint32_t page = 0) {
    std::string result(8, '\0');
    result[0] = static_cast<char>(route);
    result[1] = static_cast<char>(address >> 8);
    result[2] = static_cast<char>(address);
    result[3] = kind;
    result[4] = static_cast<char>(page >> 24);
    result[5] = static_cast<char>(page >> 16);
    result[6] = static_cast<char>(page >> 8);
    result[7] = static_cast<char>(page);
    return result;
}

void encode_u32(std::string& output, std::size_t offset, std::uint32_t value) {
    for(std::size_t byte = 0; byte < 4; ++byte)
        output[offset + byte] = static_cast<char>(value >> (byte * 8));
}

std::uint32_t decode_u32(const std::string& input, std::size_t offset) {
    if(offset + 4 > input.size()) throw std::runtime_error("native MDBX u32 payload is truncated");
    std::uint32_t result = 0;
    for(std::size_t byte = 0; byte < 4; ++byte)
        result |= static_cast<std::uint32_t>(static_cast<unsigned char>(input[offset + byte])) << (byte * 8);
    return result;
}

class MdbxAddressIndex final {
public:
    MdbxAddressIndex(const std::filesystem::path& path, std::uint8_t route,
                     const std::vector<std::uint16_t>& addresses,
                     std::size_t document_count, std::size_t replication,
                     std::size_t page_entries) : m_path(path), m_route(route) {
        std::filesystem::create_directories(path.parent_path());
        std::filesystem::remove(path);
        mdbxc::Config config;
        config.pathname = path.string();
        config.max_dbs = 4;
        config.no_subdir = true;
        config.relative_to_exe = false;
        m_connection = mdbxc::Connection::create(config);
        m_table = std::make_unique<mdbxc::KeyValueTable<std::string, std::string>>(
            m_connection, "neuroute_postings");
        std::unordered_map<std::uint16_t, std::vector<std::uint32_t>> buckets;
        for(std::size_t document = 0; document < document_count; ++document)
            for(std::size_t copy = 0; copy < replication; ++copy)
                buckets[addresses[document * replication + copy]].push_back(static_cast<std::uint32_t>(document));
        auto transaction = m_connection->transaction(mdbxc::TransactionMode::WRITABLE);
        for(const auto& entry : buckets) {
            const auto pages = (entry.second.size() + page_entries - 1) / page_entries;
            std::string metadata(8, '\0');
            encode_u32(metadata, 0, static_cast<std::uint32_t>(entry.second.size()));
            encode_u32(metadata, 4, static_cast<std::uint32_t>(pages));
            m_table->insert_or_assign(key(route, entry.first, 'm'), metadata, transaction);
            for(std::size_t page = 0; page < pages; ++page) {
                const auto start = page * page_entries;
                const auto stop = std::min(entry.second.size(), start + page_entries);
                std::string payload((stop - start) * 4, '\0');
                for(std::size_t index = start; index < stop; ++index)
                    encode_u32(payload, (index - start) * 4, entry.second[index]);
                m_table->insert_or_assign(key(route, entry.first, 'p', static_cast<std::uint32_t>(page)),
                                          payload, transaction);
            }
        }
        transaction.commit();
    }

    template<class Callback>
    void with_read_transaction(Callback&& callback) const {
        auto transaction = m_connection->transaction(mdbxc::TransactionMode::READ_ONLY);
        callback(transaction);
        transaction.commit();
    }

    template<class Transaction>
    std::vector<std::uint32_t> read(std::uint16_t address, Transaction& transaction,
                                    Counters& counters) const {
        ++counters.requested_addresses;
        const auto metadata = m_table->find(key(m_route, address, 'm'), transaction);
        if(!metadata) return {};
        if(metadata->size() != 8) throw std::runtime_error("native MDBX directory payload differs");
        const auto posting_count = decode_u32(*metadata, 0);
        const auto pages = decode_u32(*metadata, 4);
        ++counters.metadata_hits;
        std::vector<std::uint32_t> result;
        result.reserve(posting_count);
        for(std::uint32_t page = 0; page < pages; ++page) {
            const auto payload = m_table->find(key(m_route, address, 'p', page), transaction);
            if(!payload || payload->size() % 4 != 0)
                throw std::runtime_error("native MDBX posting page differs");
            ++counters.posting_page_reads;
            counters.posting_bytes += payload->size();
            for(std::size_t offset = 0; offset < payload->size(); offset += 4)
                result.push_back(decode_u32(*payload, offset));
        }
        if(result.size() != posting_count) throw std::runtime_error("native MDBX posting count differs");
        counters.posting_entries += result.size();
        return result;
    }

    std::size_t bytes() const { return static_cast<std::size_t>(std::filesystem::file_size(m_path)); }

private:
    std::filesystem::path m_path;
    std::uint8_t m_route;
    std::shared_ptr<mdbxc::Connection> m_connection;
    std::unique_ptr<mdbxc::KeyValueTable<std::string, std::string>> m_table;
};

std::uint16_t hamming_distance(const std::uint8_t* left, const std::uint8_t* right) {
    std::uint16_t result = 0;
    for(std::size_t byte = 0; byte < 32; ++byte) {
        auto value = static_cast<std::uint8_t>(left[byte] ^ right[byte]);
        while(value != 0) { result += value & 1U; value >>= 1U; }
    }
    return result;
}

float numpy_pairwise_sum(const float* values, std::size_t count) {
    if(count < 8) {
        float result = -0.0F;
        for(std::size_t index = 0; index < count; ++index) result += values[index];
        return result;
    }
    if(count <= 128) {
        std::array<float, 8> accumulators{
            values[0], values[1], values[2], values[3],
            values[4], values[5], values[6], values[7],
        };
        std::size_t index = 8;
        for(; index + 7 < count - (count % 8); index += 8)
            for(std::size_t lane = 0; lane < 8; ++lane) accumulators[lane] += values[index + lane];
        float result = ((accumulators[0] + accumulators[1])
                        + (accumulators[2] + accumulators[3]))
            + ((accumulators[4] + accumulators[5])
               + (accumulators[6] + accumulators[7]));
        for(; index < count; ++index) result += values[index];
        return result;
    }
    auto first = count / 2;
    first -= first % 8;
    return numpy_pairwise_sum(values, first) + numpy_pairwise_sum(values + first, count - first);
}

template<class Score>
struct Scored final { Score score; std::uint32_t position; std::uint32_t rank; };

template<class Score>
bool lower_score(const Scored<Score>& left, const Scored<Score>& right) {
    if(left.score != right.score) return left.score < right.score;
    return left.rank < right.rank;
}

QueryResult run_query(const MdbxAddressIndex& index, const CommonInput& input,
                      const float* logits, std::size_t bits, bool learned,
                      std::size_t probes, std::size_t query, std::size_t candidate_limit,
                      std::size_t hamming_top_k, std::size_t adc_top_k, std::size_t exact_top_k,
                      std::vector<std::uint32_t>& generations, std::uint32_t& generation) {
    QueryResult result;
    const auto total_start = Clock::now();
    auto start = Clock::now();
    result.addresses = learned ? learned_addresses(logits, bits, probes)
                               : pca_addresses(logits, bits, probes);
    result.timing.address_generation = milliseconds(start, Clock::now());
    if(++generation == 0) { std::fill(generations.begin(), generations.end(), 0); generation = 1; }
    const auto transaction_start = Clock::now();
    const auto lookup_before = result.timing.mdbx_lookup_and_decode;
    const auto dedup_before = result.timing.generation_array_dedup_and_ceiling;
    index.with_read_transaction([&](auto& transaction) {
        for(const auto address : result.addresses) {
            start = Clock::now();
            const auto posting = index.read(static_cast<std::uint16_t>(address), transaction, result.counters);
            result.timing.mdbx_lookup_and_decode += milliseconds(start, Clock::now());
            start = Clock::now();
            std::size_t fresh = 0;
            for(const auto position : posting) {
                if(position >= generations.size()) throw std::runtime_error("native MDBX posting position differs");
                if(generations[position] != generation) ++fresh;
            }
            if(result.candidates.size() + fresh <= candidate_limit) {
                ++result.counters.accepted_probes;
                for(const auto position : posting) if(generations[position] != generation) {
                    generations[position] = generation;
                    result.candidates.push_back(position);
                }
            }
            result.timing.generation_array_dedup_and_ceiling += milliseconds(start, Clock::now());
        }
    });
    const auto transaction_ms = milliseconds(transaction_start, Clock::now());
    const auto measured_inside = (result.timing.mdbx_lookup_and_decode - lookup_before)
        + (result.timing.generation_array_dedup_and_ceiling - dedup_before);
    result.timing.mdbx_lookup_and_decode += std::max(0.0, transaction_ms - measured_inside);
    start = Clock::now();
    std::sort(result.candidates.begin(), result.candidates.end());
    result.timing.generation_array_dedup_and_ceiling += milliseconds(start, Clock::now());
    result.counters.unique_candidates = result.candidates.size();

    start = Clock::now();
    std::vector<Scored<std::uint16_t>> hamming;
    hamming.reserve(result.candidates.size());
    const auto* query_code = input.query_codes.data() + query * 32;
    for(const auto position : result.candidates)
        hamming.push_back({hamming_distance(input.document_codes.data() + position * 32, query_code),
                           position, input.document_id_rank[position]});
    const auto hamming_limit = std::min(hamming_top_k, hamming.size());
    if(hamming_limit < hamming.size()) {
        std::nth_element(hamming.begin(), hamming.begin() + static_cast<std::ptrdiff_t>(hamming_limit),
                         hamming.end(), lower_score<std::uint16_t>);
        hamming.resize(hamming_limit);
    }
    std::sort(hamming.begin(), hamming.end(), lower_score<std::uint16_t>);
    result.hamming.reserve(hamming.size());
    for(const auto& value : hamming) result.hamming.push_back(value.position);
    result.timing.hamming_and_top_k = milliseconds(start, Clock::now());
    result.counters.hamming_count = result.hamming.size();

    start = Clock::now();
    std::vector<Scored<float>> adc;
    adc.reserve(result.hamming.size());
    const auto* projection = input.query_projection.data() + query * 256;
    for(const auto position : result.hamming) {
        std::array<float, 256> components{};
        const auto* code = input.document_codes.data() + position * 32;
        for(std::size_t bit = 0; bit < 256; ++bit) {
            const auto symbol = (code[bit / 8] >> (bit % 8)) & 1U;
            const auto delta = projection[bit] - input.adc_centroids[bit * 2 + symbol];
            components[bit] = delta * delta;
        }
        const auto distance = numpy_pairwise_sum(components.data(), components.size());
        adc.push_back({distance, position, input.document_id_rank[position]});
    }
    const auto adc_limit = std::min(adc_top_k, adc.size());
    if(adc_limit < adc.size()) {
        std::nth_element(adc.begin(), adc.begin() + static_cast<std::ptrdiff_t>(adc_limit),
                         adc.end(), lower_score<float>);
        adc.resize(adc_limit);
    }
    std::sort(adc.begin(), adc.end(), lower_score<float>);
    result.adc.reserve(adc.size());
    for(const auto& value : adc) result.adc.push_back(value.position);
    result.timing.binary_adc_and_top_k = milliseconds(start, Clock::now());
    result.counters.adc_count = result.adc.size();

    if(!input.document_vectors.empty()) {
        start = Clock::now();
        std::vector<Scored<float>> exact;
        exact.reserve(result.adc.size());
        const auto* query_vector = input.query_vectors.data() + query * 384;
        for(const auto position : result.adc) {
            std::array<float, 384> components{};
            const auto* document = input.document_vectors.data() + static_cast<std::size_t>(position) * 384;
            for(std::size_t dimension = 0; dimension < 384; ++dimension)
                components[dimension] = document[dimension] * query_vector[dimension];
            exact.push_back({-numpy_pairwise_sum(components.data(), components.size()),
                             position, input.document_id_rank[position]});
        }
        const auto limit = std::min(exact_top_k, exact.size());
        if(limit < exact.size()) {
            std::nth_element(exact.begin(), exact.begin() + static_cast<std::ptrdiff_t>(limit),
                             exact.end(), lower_score<float>);
            exact.resize(limit);
        }
        std::sort(exact.begin(), exact.end(), lower_score<float>);
        result.exact.reserve(exact.size());
        for(const auto& value : exact) result.exact.push_back(value.position);
        result.timing.exact_e5_and_top_k = milliseconds(start, Clock::now());
    }
    result.timing.total = milliseconds(total_start, Clock::now());
    return result;
}

void validate_query(const QueryResult& actual, const nlohmann::json& expected) {
    const auto query = std::to_string(expected.at("query").get<std::size_t>());
    const auto fail = [&](const char* field) {
        throw std::runtime_error("native MDBX Python replay differs at query " + query + ": " + field);
    };
    if(actual.addresses.size() != expected.at("requested_address_count").get<std::size_t>()) fail("requested_address_count");
    if(sequence_sha256(actual.addresses) != require_sha256(expected, "requested_address_sha256")) fail("requested_address_sha256");
    if(actual.counters.accepted_probes != expected.at("accepted_probe_count").get<std::size_t>()) fail("accepted_probe_count");
    if(actual.counters.posting_entries != expected.at("posting_entries_requested").get<std::size_t>()) fail("posting_entries_requested");
    if(actual.candidates.size() != expected.at("candidate_count").get<std::size_t>()) fail("candidate_count");
    if(sequence_sha256(actual.candidates) != require_sha256(expected, "candidate_sha256")) fail("candidate_sha256");
    if(actual.hamming.size() != expected.at("hamming_count").get<std::size_t>()) fail("hamming_count");
    if(sequence_sha256(actual.hamming) != require_sha256(expected, "hamming_sha256")) fail("hamming_sha256");
    if(actual.adc.size() != expected.at("adc_count").get<std::size_t>()) fail("adc_count");
    if(sequence_sha256(actual.adc) != require_sha256(expected, "adc_sha256")) fail("adc_sha256");
    if(expected.contains("exact_sha256")
       && sequence_sha256(actual.exact) != require_sha256(expected, "exact_sha256")) fail("exact_sha256");
}

bool same_counters(const Counters& left, const Counters& right) {
    return std::tie(left.requested_addresses, left.accepted_probes, left.metadata_hits,
                    left.posting_page_reads, left.posting_entries, left.posting_bytes,
                    left.unique_candidates, left.hamming_count, left.adc_count)
        == std::tie(right.requested_addresses, right.accepted_probes, right.metadata_hits,
                    right.posting_page_reads, right.posting_entries, right.posting_bytes,
                    right.unique_candidates, right.hamming_count, right.adc_count);
}

double quantile(std::vector<double> values, double fraction) {
    if(values.empty()) throw std::runtime_error("native MDBX timing sample is empty");
    std::sort(values.begin(), values.end());
    const auto position = fraction * static_cast<double>(values.size() - 1);
    const auto lower = static_cast<std::size_t>(std::floor(position));
    const auto upper = static_cast<std::size_t>(std::ceil(position));
    const auto weight = position - static_cast<double>(lower);
    return values[lower] * (1.0 - weight) + values[upper] * weight;
}

double mean(const std::vector<double>& values) {
    return std::accumulate(values.begin(), values.end(), 0.0) / static_cast<double>(values.size());
}

nlohmann::json summarize_timing(const std::vector<std::vector<StageTiming>>& samples) {
    const auto collect = [&](auto member) {
        std::vector<double> medians;
        medians.reserve(samples.size());
        for(const auto& query : samples) {
            std::vector<double> values;
            values.reserve(query.size());
            for(const auto& value : query) values.push_back(value.*member);
            medians.push_back(quantile(values, 0.5));
        }
        return nlohmann::json{{"p50", quantile(medians, 0.5)}, {"p95", quantile(medians, 0.95)},
                              {"p99", quantile(medians, 0.99)}, {"per_query_median", medians}};
    };
    return {
        {"address_generation", collect(&StageTiming::address_generation)},
        {"mdbx_lookup_and_decode", collect(&StageTiming::mdbx_lookup_and_decode)},
        {"generation_array_dedup_and_ceiling", collect(&StageTiming::generation_array_dedup_and_ceiling)},
        {"hamming_and_top_k", collect(&StageTiming::hamming_and_top_k)},
        {"binary_adc_and_top_k", collect(&StageTiming::binary_adc_and_top_k)},
        {"exact_e5_and_top_k", collect(&StageTiming::exact_e5_and_top_k)},
        {"total", collect(&StageTiming::total)},
    };
}

nlohmann::json summarize_work(const std::vector<Counters>& values) {
    const auto average = [&](auto member) {
        std::vector<double> samples;
        samples.reserve(values.size());
        for(const auto& value : values) samples.push_back(static_cast<double>(value.*member));
        return mean(samples);
    };
    return {
        {"requested_addresses_per_query", average(&Counters::requested_addresses)},
        {"accepted_probes_per_query", average(&Counters::accepted_probes)},
        {"metadata_hits_per_query", average(&Counters::metadata_hits)},
        {"posting_page_reads_per_query", average(&Counters::posting_page_reads)},
        {"posting_entries_per_query", average(&Counters::posting_entries)},
        {"posting_bytes_per_query", average(&Counters::posting_bytes)},
        {"unique_candidates_per_query", average(&Counters::unique_candidates)},
        {"hamming_count_per_query", average(&Counters::hamming_count)},
        {"adc_count_per_query", average(&Counters::adc_count)},
    };
}

CommonInput load_common(const std::filesystem::path& root, const nlohmann::json& dataset) {
    CommonInput result;
    result.document_count = dataset.at("document_count").get<std::size_t>();
    result.query_count = dataset.at("query_count").get<std::size_t>();
    const auto& common = dataset.at("common");
    result.document_codes = read_array<std::uint8_t>(root, common.at("document_codes"));
    result.query_codes = read_array<std::uint8_t>(root, common.at("query_codes"));
    result.query_projection = read_array<float>(root, common.at("query_projection"));
    result.adc_centroids = read_array<float>(root, common.at("adc_centroids"));
    result.document_id_rank = read_array<std::uint32_t>(root, common.at("document_id_rank"));
    if(common.contains("document_vectors")) {
        result.document_vectors = read_array<float>(root, common.at("document_vectors"));
        result.query_vectors = read_array<float>(root, common.at("query_vectors"));
    }
    if(result.document_codes.size() != result.document_count * 32
       || result.query_codes.size() != result.query_count * 32
       || result.query_projection.size() != result.query_count * 256
       || result.adc_centroids.size() != 512
       || result.document_id_rank.size() != result.document_count)
        throw std::runtime_error("native MDBX common input shape differs");
    if(!result.document_vectors.empty()
       && (result.document_vectors.size() != result.document_count * 384
           || result.query_vectors.size() != result.query_count * 384))
        throw std::runtime_error("native MDBX exact-E5 input shape differs");
    return result;
}

bool is_relevance_v4(const nlohmann::json& contract) {
    return contract.value("family", "") == "neuroute_relevance_aware_v4_config_only";
}

bool is_frozen_scale(const nlohmann::json& contract) {
    return contract.value("family", "") == "neuroute_frozen_scale_transfer";
}

bool is_modern(const nlohmann::json& contract) {
    return is_relevance_v4(contract) || is_frozen_scale(contract);
}

double candidate_mass_target(const nlohmann::json& contract) {
    return (is_modern(contract) ? (is_frozen_scale(contract) ? contract.at("route") : contract.at("routing"))
                                      : contract.at("candidate_pipeline"))
        .at("candidate_mass_target").get<double>();
}

std::size_t warmup_passes(const nlohmann::json& contract) {
    return (is_modern(contract)
        ? contract.at("native_timing").at("warmup_passes")
        : contract.at("timing").at("warmup_full_query_passes")).get<std::size_t>();
}

std::size_t measured_passes(const nlohmann::json& contract) {
    return (is_modern(contract)
        ? contract.at("native_timing").at("measured_passes")
        : contract.at("timing").at("measured_full_query_passes")).get<std::size_t>();
}

std::size_t hamming_limit(const nlohmann::json& contract) {
    return (is_modern(contract) ? contract.at("cascade")
                                      : contract.at("candidate_pipeline"))
        .at("hamming_limit").get<std::size_t>();
}

std::size_t adc_limit(const nlohmann::json& contract) {
    return (is_modern(contract) ? contract.at("cascade")
                                      : contract.at("candidate_pipeline"))
        .at("adc_limit").get<std::size_t>();
}

std::size_t exact_limit(const nlohmann::json& contract) {
    if(is_frozen_scale(contract)) return contract.at("cascade").at("result_k").get<std::size_t>();
    return 0;
}

nlohmann::json run_row(const nlohmann::json& contract, const nlohmann::json& dataset,
                       const nlohmann::json& route, const nlohmann::json& expected,
                       const CommonInput& input, const std::vector<float>& logits,
                       const MdbxAddressIndex& index, bool timings) {
    const auto probes = expected.at("probes").get<std::size_t>();
    const auto bits = route.at("bits").get<std::size_t>();
    const auto logit_dimensions = route.at("logit_dimensions").get<std::size_t>();
    const auto learned = route.at("kind").get<std::string>() == "learned";
    const auto candidate_limit = static_cast<std::size_t>(std::floor(
        input.document_count * candidate_mass_target(contract)));
    std::vector<std::uint32_t> generations(input.document_count);
    std::uint32_t generation = 0;
    std::vector<Counters> deterministic;
    std::vector<std::uint8_t> candidate_bytes, hamming_bytes, adc_bytes, exact_bytes;
    for(std::size_t query = 0; query < input.query_count; ++query) {
        const auto actual = run_query(index, input, logits.data() + query * logit_dimensions, bits, learned,
                                      probes, query, candidate_limit, hamming_limit(contract), adc_limit(contract),
                                      exact_limit(contract), generations, generation);
        validate_query(actual, expected.at("queries").at(query));
        deterministic.push_back(actual.counters);
        append_query_sequence(candidate_bytes, query, actual.candidates);
        append_query_sequence(hamming_bytes, query, actual.hamming);
        append_query_sequence(adc_bytes, query, actual.adc);
        if(!actual.exact.empty()) append_query_sequence(exact_bytes, query, actual.exact);
    }
    const auto candidate_digest = agent_memory::sha256_bytes_hex(candidate_bytes);
    const auto hamming_digest = agent_memory::sha256_bytes_hex(hamming_bytes);
    const auto adc_digest = agent_memory::sha256_bytes_hex(adc_bytes);
    const auto exact_digest = exact_bytes.empty() ? std::string{} : agent_memory::sha256_bytes_hex(exact_bytes);
    if(candidate_digest != require_sha256(expected, "candidate_sequence_sha256")
       || hamming_digest != require_sha256(expected, "hamming_sequence_sha256")
       || adc_digest != require_sha256(expected, "adc_sequence_sha256"))
        throw std::runtime_error("native MDBX global sequence replay differs");
    if(expected.contains("exact_sequence_sha256")
       && exact_digest != require_sha256(expected, "exact_sequence_sha256"))
        throw std::runtime_error("native MDBX global exact-E5 replay differs");

    nlohmann::json timing = nullptr;
    if(timings) {
        const auto warmups = warmup_passes(contract);
        const auto repeats = measured_passes(contract);
        for(std::size_t warmup = 0; warmup < warmups; ++warmup)
            for(std::size_t query = 0; query < input.query_count; ++query)
                static_cast<void>(run_query(index, input, logits.data() + query * logit_dimensions, bits, learned,
                                            probes, query, candidate_limit, hamming_limit(contract), adc_limit(contract),
                                            exact_limit(contract), generations, generation));
        std::vector<std::vector<StageTiming>> samples(input.query_count);
        for(std::size_t repeat = 0; repeat < repeats; ++repeat) {
            for(std::size_t query = 0; query < input.query_count; ++query) {
                const auto actual = run_query(index, input, logits.data() + query * logit_dimensions, bits, learned,
                                              probes, query, candidate_limit, hamming_limit(contract), adc_limit(contract),
                                              exact_limit(contract), generations, generation);
                if(!same_counters(actual.counters, deterministic[query])
                   || sequence_sha256(actual.candidates)
                        != require_sha256(expected.at("queries").at(query), "candidate_sha256")
                   || sequence_sha256(actual.hamming)
                        != require_sha256(expected.at("queries").at(query), "hamming_sha256")
                    || sequence_sha256(actual.adc)
                         != require_sha256(expected.at("queries").at(query), "adc_sha256")
                    || (expected.at("queries").at(query).contains("exact_sha256")
                        && sequence_sha256(actual.exact)
                           != require_sha256(expected.at("queries").at(query), "exact_sha256")))
                    throw std::runtime_error("native MDBX measured repeat differs");
                samples[query].push_back(actual.timing);
            }
        }
        timing = summarize_timing(samples);
    }
    return {
        {"dataset", dataset.at("id")}, {"route", route.at("id")},
        {"kind", route.at("kind")}, {"seed", route.at("seed")}, {"probes", probes},
        {"query_count", input.query_count}, {"work", summarize_work(deterministic)},
        {"candidate_sequence_sha256", candidate_digest},
        {"hamming_sequence_sha256", hamming_digest}, {"adc_sequence_sha256", adc_digest},
        {"exact_sequence_sha256", exact_digest},
        {"timing_ms", timing},
    };
}

nlohmann::json execute(const std::filesystem::path& contract_path,
                       const std::filesystem::path& manifest_path,
                       const std::filesystem::path& mdbx_root, bool timings) {
    std::ifstream contract_stream(contract_path), manifest_stream(manifest_path);
    nlohmann::json contract, manifest;
    contract_stream >> contract;
    manifest_stream >> manifest;
    const auto legacy = contract.value("family", "") == "neuroute_native_mdbx_cost_protocol"
        && manifest.value("family", "") == "neuroute_native_mdbx_cost_materialization";
    const auto relevance_v4 = is_relevance_v4(contract)
        && manifest.value("family", "") == "neuroute_relevance_aware_v4_native_materialization";
    const auto frozen_scale = is_frozen_scale(contract)
        && manifest.value("family", "") == "neuroute_frozen_scale_transfer_native_materialization";
    if(contract.value("schema_version", 0) != 1 || manifest.value("schema_version", 0) != 1
       || (!legacy && !relevance_v4 && !frozen_scale)
       || manifest.at("contract_sha256").get<std::string>() != agent_memory::sha256_file_hex(contract_path))
        throw std::runtime_error("native MDBX contract/materialization binding differs");
    if(contract.at("storage").at("page_entries").get<std::size_t>() != 256
       || hamming_limit(contract) != 768
       || (!frozen_scale && adc_limit(contract) != 256)
       || (frozen_scale && (adc_limit(contract) != 64 || exact_limit(contract) != 10)))
        throw std::runtime_error("native MDBX fixed pipeline differs");
    nlohmann::json rows = nlohmann::json::array(), indexes = nlohmann::json::array();
    std::uint8_t route_number = 0;
    const auto root = manifest_path.parent_path();
    for(const auto& dataset : manifest.at("datasets")) {
        const auto dataset_root = root / dataset.at("id").get<std::string>();
        const auto input = load_common(dataset_root, dataset);
        for(const auto& route : dataset.at("routes")) {
            const auto route_root = dataset_root / route.at("id").get<std::string>();
            const auto addresses = read_array<std::uint16_t>(route_root, route.at("document_addresses"));
            const auto logits = read_array<float>(route_root, route.at("query_logits"));
            const auto replication = route.at("document_replication").get<std::size_t>();
            const auto logit_dimensions = route.at("logit_dimensions").get<std::size_t>();
            if(addresses.size() != input.document_count * replication
               || logits.size() != input.query_count * logit_dimensions)
                throw std::runtime_error("native MDBX route payload shape differs");
            const auto database_path = mdbx_root / (dataset.at("id").get<std::string>() + "-"
                + route.at("id").get<std::string>() + ".mdbx");
            const auto build_start = Clock::now();
            MdbxAddressIndex index(database_path, route_number++, addresses, input.document_count,
                                   replication, contract.at("storage").at("page_entries").get<std::size_t>());
            const auto build_ms = milliseconds(build_start, Clock::now());
            indexes.push_back({{"dataset", dataset.at("id")}, {"route", route.at("id")},
                               {"build_ms", build_ms}, {"mdbx_file_bytes", index.bytes()},
                               {"posting_entry_count", route.at("posting_entry_count")},
                               {"occupied_address_count", route.at("occupied_address_count")}});
            for(const auto& expected : route.at("expected"))
                rows.push_back(run_row(contract, dataset, route, expected, input, logits, index, timings));
        }
    }
    return {
        {"schema_version", 1}, {"family", frozen_scale ? "neuroute_frozen_scale_transfer_native_result"
            : (relevance_v4 ? "neuroute_relevance_aware_v4_native_result" : "neuroute_native_mdbx_cost_result")},
        {"claim_scope", contract.at("claim_scope")},
        {"contract_sha256", agent_memory::sha256_file_hex(contract_path)},
        {"materialization_sha256", agent_memory::sha256_file_hex(manifest_path)},
        {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
        {"evaluator_build_environment", evaluator_build_environment()},
        {"storage_stack", {{"provenance_authoritative", AGENT_MEMORY_STORAGE_PROVENANCE_AUTHORITATIVE != 0},
                           {"provenance_reason", AGENT_MEMORY_STORAGE_PROVENANCE_REASON},
                           {"libmdbx_commit", AGENT_MEMORY_LIBMDBX_REVISION},
                           {"mdbx_containers_commit", AGENT_MEMORY_MDBX_CONTAINERS_REVISION}}},
        {"timings_recorded", timings}, {"indexes", indexes}, {"rows", rows},
    };
}

void validate_report(const nlohmann::json& expected, const nlohmann::json& replay) {
    if(expected.value("schema_version", 0) != 1
       || expected.value("family", "") != replay.value("family", "")
       || (expected.value("family", "") != "neuroute_native_mdbx_cost_result"
            && expected.value("family", "") != "neuroute_relevance_aware_v4_native_result"
            && expected.value("family", "") != "neuroute_frozen_scale_transfer_native_result")
       || expected.at("contract_sha256") != replay.at("contract_sha256")
       || expected.at("materialization_sha256") != replay.at("materialization_sha256")
       || expected.at("evaluator_source_manifest_sha256") != replay.at("evaluator_source_manifest_sha256")
       || expected.at("storage_stack") != replay.at("storage_stack")
       || expected.at("rows").size() != replay.at("rows").size())
        throw std::runtime_error("native MDBX report replay binding differs");
    for(std::size_t index = 0; index < replay.at("rows").size(); ++index) {
        const auto& left = expected.at("rows").at(index);
        const auto& right = replay.at("rows").at(index);
        for(const auto* field : {"dataset", "route", "kind", "seed", "probes", "query_count",
                                  "work", "candidate_sequence_sha256", "hamming_sequence_sha256",
                                  "adc_sequence_sha256"})
            if(left.at(field) != right.at(field))
                throw std::runtime_error(std::string("native MDBX report replay differs: ") + field);
        if(left.contains("exact_sequence_sha256")
           && left.at("exact_sequence_sha256") != right.at("exact_sequence_sha256"))
            throw std::runtime_error("native MDBX report replay differs: exact_sequence_sha256");
        if(!left.at("timing_ms").is_object()) throw std::runtime_error("native MDBX report timing is absent");
    }
}

int self_test(const std::filesystem::path& database_path) {
    try {
        const std::vector<float> logits{0.1F, -0.2F, 0.3F, -0.4F};
        const auto learned = learned_addresses(logits.data(), 4, 8);
        if(learned.size() != 8 || learned.front() != 5 || sequence_sha256({7, 2, 99})
            != "1673c447a7acb075da4fcf6fceaae46afa50428aa1b77fdc6a2868c3248120c1")
            throw std::runtime_error("native MDBX address/hash self-test differs");
        const std::vector<std::uint16_t> addresses{1, 1, 2, 3, 3};
        MdbxAddressIndex index(database_path, 0, addresses, 5, 1, 2);
        Counters counters;
        std::vector<std::uint32_t> values;
        index.with_read_transaction([&](auto& transaction) { values = index.read(1, transaction, counters); });
        if(values != std::vector<std::uint32_t>({0, 1}) || counters.posting_page_reads != 1)
            throw std::runtime_error("native MDBX lookup self-test differs");
        std::cout << "NeuRoute native MDBX cost self-test passed\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "NeuRoute native MDBX cost self-test failed: " << error.what() << '\n';
        return 1;
    }
}

} // namespace

int main(int argc, char** argv) {
    if(argc == 3 && std::string(argv[1]) == "--self-test") return self_test(argv[2]);
    try {
        if(argc == 6 && std::string(argv[1]) == "--validate") {
            std::ifstream report_stream(argv[5]);
            nlohmann::json report;
            report_stream >> report;
            const auto replay = execute(argv[2], argv[3], argv[4], false);
            validate_report(report, replay);
            std::cout << "NeuRoute native MDBX cost report replay passed\n";
            return 0;
        }
        if(argc != 5) {
            std::cerr << "usage: agent-memory-neuroute-native-mdbx-cost <contract.json> "
                         "<materialization.json> <mdbx-directory> <report.json>\n";
            return 2;
        }
        const auto report = execute(argv[1], argv[2], argv[3], true);
        std::ofstream output(argv[4]);
        if(!output) throw std::runtime_error("cannot write native MDBX cost report");
        output << report.dump(2) << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
