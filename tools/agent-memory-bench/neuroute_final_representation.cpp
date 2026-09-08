#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::size_t dimensions = 384;

struct Ranked final {
    float score = 0.0F;
    std::uint32_t position = 0;
    std::uint32_t rank = 0;
};

struct Representation final {
    std::string id;
    std::string kind;
    std::vector<float> fp32;
    std::vector<float> scales;
    std::vector<float> centroids;
    std::vector<std::uint8_t> encoded;
};

bool better(const Ranked& left, const Ranked& right) {
    if(left.score != right.score) return left.score > right.score;
    return left.rank < right.rank;
}

double milliseconds(Clock::time_point start, Clock::time_point stop) {
    return std::chrono::duration<double, std::milli>(stop - start).count();
}

std::size_t element_count(const nlohmann::json& payload) {
    std::size_t result = 1;
    for(const auto& value : payload.at("shape")) result *= value.get<std::size_t>();
    return result;
}

std::filesystem::path payload_path(const std::filesystem::path& root,
                                   const nlohmann::json& payload) {
    const std::filesystem::path value(payload.at("file").get<std::string>());
    return value.is_absolute() ? value : root / value;
}

template <typename Value>
std::vector<Value> read_array(const std::filesystem::path& root, const nlohmann::json& payload) {
    const auto path = payload_path(root, payload);
    if(agent_memory::sha256_file_hex(path) != payload.at("sha256").get<std::string>())
        throw std::runtime_error("final-representation payload hash differs");
    std::ifstream stream(path, std::ios::binary);
    if(!stream) throw std::runtime_error("cannot read final-representation payload");
    std::vector<Value> result(element_count(payload));
    stream.read(reinterpret_cast<char*>(result.data()),
                static_cast<std::streamsize>(result.size() * sizeof(Value)));
    if(!stream || stream.peek() != std::ifstream::traits_type::eof())
        throw std::runtime_error("final-representation payload size differs");
    return result;
}

std::vector<std::uint8_t> read_bytes(const std::filesystem::path& root,
                                     const nlohmann::json& payload) {
    const auto path = payload_path(root, payload);
    if(agent_memory::sha256_file_hex(path) != payload.at("sha256").get<std::string>())
        throw std::runtime_error("final-representation payload hash differs");
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if(!stream) throw std::runtime_error("cannot read final-representation payload");
    const auto size = stream.tellg();
    if(size < 0) throw std::runtime_error("final-representation payload size differs");
    std::vector<std::uint8_t> result(static_cast<std::size_t>(size));
    stream.seekg(0);
    stream.read(reinterpret_cast<char*>(result.data()), size);
    if(!stream) throw std::runtime_error("cannot read final-representation payload bytes");
    return result;
}

void append_u32(std::vector<std::uint8_t>& bytes, std::uint32_t value) {
    for(std::size_t byte = 0; byte < 4; ++byte)
        bytes.push_back(static_cast<std::uint8_t>((value >> (byte * 8)) & 0xffU));
}

void append_query(std::vector<std::uint8_t>& bytes, std::size_t query,
                  const std::vector<std::uint32_t>& values) {
    append_u32(bytes, static_cast<std::uint32_t>(query));
    append_u32(bytes, static_cast<std::uint32_t>(values.size()));
    for(const auto value : values) append_u32(bytes, value);
}

std::string sequence_sha256(const std::vector<std::uint32_t>& values) {
    std::vector<std::uint8_t> bytes;
    bytes.reserve(values.size() * 4);
    for(const auto value : values) append_u32(bytes, value);
    return agent_memory::sha256_bytes_hex(bytes);
}

float half_to_float(std::uint16_t value) {
    const auto sign = static_cast<std::uint32_t>(value & 0x8000U) << 16U;
    auto exponent = static_cast<std::uint32_t>((value >> 10U) & 0x1fU);
    auto mantissa = static_cast<std::uint32_t>(value & 0x03ffU);
    std::uint32_t bits = 0;
    if(exponent == 0U) {
        if(mantissa == 0U) bits = sign;
        else {
            exponent = 127U - 15U + 1U;
            while((mantissa & 0x0400U) == 0U) {
                mantissa <<= 1U;
                --exponent;
            }
            mantissa &= 0x03ffU;
            bits = sign | (exponent << 23U) | (mantissa << 13U);
        }
    } else if(exponent == 0x1fU) {
        bits = sign | 0x7f800000U | (mantissa << 13U);
    } else {
        bits = sign | ((exponent + (127U - 15U)) << 23U) | (mantissa << 13U);
    }
    float result = 0.0F;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}

std::uint8_t unpack(const std::uint8_t* row, std::size_t dimension, std::size_t bits) {
    const auto bit = dimension * bits;
    const auto byte = bit / 8;
    const auto shift = bit % 8;
    std::uint16_t value = static_cast<std::uint16_t>(row[byte] >> shift);
    if(shift + bits > 8)
        value |= static_cast<std::uint16_t>(row[byte + 1]) << (8 - shift);
    return static_cast<std::uint8_t>(value & ((1U << bits) - 1U));
}

Representation load_representation(const std::filesystem::path& root,
                                   const nlohmann::json& specification) {
    Representation result;
    result.id = specification.at("id").get<std::string>();
    result.kind = specification.at("kind").get<std::string>();
    if(result.kind == "fp32") result.fp32 = read_array<float>(root, specification.at("encoded"));
    else if(result.kind != "existing_adc_order") {
        result.encoded = read_bytes(root, specification.at("encoded"));
        if(specification.contains("scale"))
            result.scales = read_array<float>(root, specification.at("scale"));
        if(specification.contains("centroids"))
            result.centroids = read_array<float>(root, specification.at("centroids"));
    }
    return result;
}

void score_pool(const Representation& representation, const std::vector<float>& queries,
                const std::vector<std::uint32_t>& ranks, const std::uint32_t* pool,
                std::size_t document_count, std::size_t query, std::vector<Ranked>& scores) {
    constexpr std::size_t pool_size = 64;
    const auto* query_vector = queries.data() + query * dimensions;
    scores.clear();
    scores.reserve(pool_size);
    for(std::size_t index = 0; index < pool_size; ++index) {
        const auto position = pool[index];
        if(position >= document_count) throw std::runtime_error("final-representation pool position differs");
        float score = 0.0F;
        if(representation.kind == "existing_adc_order") score = -static_cast<float>(index);
        else if(representation.kind == "fp32") {
            const auto* document = representation.fp32.data() + static_cast<std::size_t>(position) * dimensions;
            for(std::size_t dimension = 0; dimension < dimensions; ++dimension)
                score += document[dimension] * query_vector[dimension];
        } else if(representation.kind == "fp16") {
            const auto* document = representation.encoded.data()
                + static_cast<std::size_t>(position) * dimensions * 2;
            for(std::size_t dimension = 0; dimension < dimensions; ++dimension) {
                const auto offset = dimension * 2;
                const auto half = static_cast<std::uint16_t>(document[offset])
                    | (static_cast<std::uint16_t>(document[offset + 1]) << 8U);
                score += half_to_float(half) * query_vector[dimension];
            }
        } else if(representation.kind == "int8_symmetric") {
            const auto* document = reinterpret_cast<const std::int8_t*>(representation.encoded.data())
                + static_cast<std::size_t>(position) * dimensions;
            for(std::size_t dimension = 0; dimension < dimensions; ++dimension)
                score += static_cast<float>(document[dimension]) * query_vector[dimension];
            score *= representation.scales[position];
        } else if(representation.kind == "int4_symmetric"
                  || representation.kind == "ternary_2bit"
                  || representation.kind == "five_level_3bit") {
            const std::size_t bits = representation.kind == "int4_symmetric" ? 4
                : (representation.kind == "ternary_2bit" ? 2 : 3);
            const int offset = representation.kind == "int4_symmetric" ? 7
                : (representation.kind == "ternary_2bit" ? 1 : 2);
            const auto stride = (dimensions * bits + 7) / 8;
            const auto* document = representation.encoded.data()
                + static_cast<std::size_t>(position) * stride;
            for(std::size_t dimension = 0; dimension < dimensions; ++dimension)
                score += static_cast<float>(static_cast<int>(unpack(document, dimension, bits)) - offset)
                    * query_vector[dimension];
            score *= representation.scales[position];
        } else if(representation.kind == "coordinate_binary_adc384") {
            const auto* document = representation.encoded.data()
                + static_cast<std::size_t>(position) * (dimensions / 8);
            for(std::size_t dimension = 0; dimension < dimensions; ++dimension) {
                const auto code = (document[dimension / 8] >> (dimension % 8)) & 1U;
                const auto delta = query_vector[dimension]
                    - representation.centroids[dimension * 2 + code];
                score -= delta * delta;
            }
        } else throw std::runtime_error("unknown final-representation kind");
        scores.push_back({score, position, ranks[position]});
    }
}

std::vector<std::uint32_t> select_top_10(std::vector<Ranked> scores) {
    constexpr std::size_t count = 10;
    std::nth_element(scores.begin(), scores.begin() + count, scores.end(), better);
    scores.resize(count);
    std::sort(scores.begin(), scores.end(), better);
    std::vector<std::uint32_t> result;
    result.reserve(count);
    for(const auto& value : scores) result.push_back(value.position);
    return result;
}

double quantile(std::vector<double> values, double fraction) {
    if(values.empty()) throw std::runtime_error("final-representation timing sample is empty");
    std::sort(values.begin(), values.end());
    const auto position = fraction * static_cast<double>(values.size() - 1);
    const auto lower = static_cast<std::size_t>(std::floor(position));
    const auto upper = static_cast<std::size_t>(std::ceil(position));
    const auto weight = position - static_cast<double>(lower);
    return values[lower] * (1.0 - weight) + values[upper] * weight;
}

nlohmann::json timing_summary(const std::vector<double>& values) {
    return {{"mean", std::accumulate(values.begin(), values.end(), 0.0) / values.size()},
            {"p50", quantile(values, 0.5)}, {"p95", quantile(values, 0.95)},
            {"p99", quantile(values, 0.99)}, {"sample_count", values.size()}};
}

const nlohmann::json& expected_for(const nlohmann::json& route, const std::string& id) {
    for(const auto& expected : route.at("expected"))
        if(expected.at("representation") == id) return expected;
    throw std::runtime_error("final-representation expected row is absent");
}

nlohmann::json execute(const std::filesystem::path& contract_path,
                       const std::filesystem::path& manifest_path, bool timings) {
    nlohmann::json contract, manifest;
    std::ifstream contract_stream(contract_path), manifest_stream(manifest_path);
    contract_stream >> contract;
    manifest_stream >> manifest;
    if(contract.value("family", "") != "neuroute_final_representation_frontier"
       || manifest.value("family", "") != "neuroute_final_representation_materialization"
       || manifest.at("contract_sha256") != agent_memory::sha256_file_hex(contract_path))
        throw std::runtime_error("final-representation contract/materialization binding differs");
    const auto warmups = contract.at("native_timing").at("warmup_passes").get<std::size_t>();
    const auto repeats = contract.at("native_timing").at("measured_passes").get<std::size_t>();
    const auto microbatch = contract.at("native_timing").at("microbatch_repeats").get<std::size_t>();
    const auto root = manifest_path.parent_path();
    nlohmann::json rows = nlohmann::json::array(), datasets = nlohmann::json::array();
    for(const auto& dataset : manifest.at("datasets")) {
        const auto dataset_root = root / dataset.at("id").get<std::string>();
        const auto queries = read_array<float>(dataset_root, dataset.at("query_vectors"));
        const auto ranks = read_array<std::uint32_t>(dataset_root, dataset.at("document_id_rank"));
        const auto document_count = dataset.at("document_count").get<std::size_t>();
        const auto query_count = dataset.at("query_count").get<std::size_t>();
        if(queries.size() != query_count * dimensions || ranks.size() != document_count)
            throw std::runtime_error("final-representation common payload shape differs");
        datasets.push_back({{"dataset", dataset.at("id")}, {"document_count", document_count},
                            {"query_count", query_count}});
        for(const auto& specification : dataset.at("representations")) {
            const auto representation = load_representation(dataset_root / "representations", specification);
            for(const auto& route : dataset.at("routes")) {
                const auto seed = route.at("seed").get<std::uint32_t>();
                const auto pool = read_array<std::uint32_t>(dataset_root / std::to_string(seed), route.at("pool"));
                if(pool.size() != query_count * 64)
                    throw std::runtime_error("final-representation pool payload shape differs");
                const auto& expected = expected_for(route, representation.id);
                std::vector<std::uint8_t> digest_bytes;
                std::vector<Ranked> scores;
                for(std::size_t query = 0; query < query_count; ++query) {
                    score_pool(representation, queries, ranks, pool.data() + query * 64,
                               document_count, query, scores);
                    const auto ranked = select_top_10(scores);
                    if(sequence_sha256(ranked) != expected.at("queries").at(query).at("ranked_sha256"))
                        throw std::runtime_error("final-representation per-query replay differs");
                    append_query(digest_bytes, query, ranked);
                }
                const auto digest = agent_memory::sha256_bytes_hex(digest_bytes);
                if(digest != expected.at("ranked_sequence_sha256").get<std::string>())
                    throw std::runtime_error("final-representation global replay differs");
                nlohmann::json timing = nullptr;
                if(timings) {
                    for(std::size_t warmup = 0; warmup < warmups; ++warmup)
                        for(std::size_t query = 0; query < query_count; ++query)
                            for(std::size_t batch = 0; batch < microbatch; ++batch) {
                                score_pool(representation, queries, ranks, pool.data() + query * 64,
                                           document_count, query, scores);
                                static_cast<void>(select_top_10(scores));
                            }
                    std::vector<double> decode, selection, total;
                    decode.reserve(query_count * repeats);
                    selection.reserve(query_count * repeats);
                    total.reserve(query_count * repeats);
                    for(std::size_t repeat = 0; repeat < repeats; ++repeat)
                        for(std::size_t query = 0; query < query_count; ++query) {
                            double decode_sum = 0.0, selection_sum = 0.0;
                            const auto total_start = Clock::now();
                            for(std::size_t batch = 0; batch < microbatch; ++batch) {
                                const auto decode_start = Clock::now();
                                score_pool(representation, queries, ranks, pool.data() + query * 64,
                                           document_count, query, scores);
                                const auto selection_start = Clock::now();
                                decode_sum += milliseconds(decode_start, selection_start);
                                static_cast<void>(select_top_10(scores));
                                selection_sum += milliseconds(selection_start, Clock::now());
                            }
                            const auto divisor = static_cast<double>(microbatch);
                            decode.push_back(decode_sum / divisor);
                            selection.push_back(selection_sum / divisor);
                            total.push_back(milliseconds(total_start, Clock::now()) / divisor);
                        }
                    timing = {{"decode_and_score", timing_summary(decode)},
                              {"top10_selection", timing_summary(selection)},
                              {"total", timing_summary(total)}};
                }
                rows.push_back({{"dataset", dataset.at("id")}, {"seed", seed},
                                {"representation", representation.id}, {"query_count", query_count},
                                {"ranked_sequence_sha256", digest}, {"timing_ms_per_query", timing}});
            }
        }
    }
    return {{"schema_version", 1}, {"family", "neuroute_final_representation_native_result"},
            {"claim_scope", contract.at("claim_scope")},
            {"contract_sha256", agent_memory::sha256_file_hex(contract_path)},
            {"materialization_sha256", agent_memory::sha256_file_hex(manifest_path)},
            {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
            {"timings_recorded", timings}, {"datasets", datasets}, {"rows", rows}};
}

void validate_report(const nlohmann::json& expected, const nlohmann::json& replay) {
    if(expected.value("family", "") != "neuroute_final_representation_native_result"
       || expected.at("contract_sha256") != replay.at("contract_sha256")
       || expected.at("materialization_sha256") != replay.at("materialization_sha256")
       || expected.at("evaluator_source_manifest_sha256") != replay.at("evaluator_source_manifest_sha256")
       || expected.at("datasets") != replay.at("datasets") || expected.at("rows").size() != replay.at("rows").size())
        throw std::runtime_error("final-representation native report binding differs");
    for(std::size_t index = 0; index < replay.at("rows").size(); ++index) {
        for(const auto* field : {"dataset", "seed", "representation", "query_count", "ranked_sequence_sha256"})
            if(expected.at("rows").at(index).at(field) != replay.at("rows").at(index).at(field))
                throw std::runtime_error(std::string("final-representation native report differs: ") + field);
        if(!expected.at("rows").at(index).at("timing_ms_per_query").is_object())
            throw std::runtime_error("final-representation native timing is absent");
    }
}

int self_test() {
    const std::uint8_t packed[]{136U, 8U};
    if(unpack(packed, 0, 3) != 0U || unpack(packed, 1, 3) != 1U
       || unpack(packed, 2, 3) != 2U || unpack(packed, 3, 3) != 4U
       || std::abs(half_to_float(0x3c00U) - 1.0F) > 1e-6F
       || sequence_sha256({7, 2, 99}) != "1673c447a7acb075da4fcf6fceaae46afa50428aa1b77fdc6a2868c3248120c1")
        throw std::runtime_error("final-representation native self-test differs");
    std::cout << "NeuRoute final-representation native self-test passed\n";
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    try {
        if(argc == 2 && std::string(argv[1]) == "--self-test") return self_test();
        if(argc == 5 && std::string(argv[1]) == "--validate") {
            nlohmann::json expected;
            std::ifstream stream(argv[4]);
            stream >> expected;
            validate_report(expected, execute(argv[2], argv[3], false));
            std::cout << "NeuRoute final-representation native report replay passed\n";
            return 0;
        }
        if(argc != 4) {
            std::cerr << "usage: agent-memory-neuroute-final-representation "
                         "<contract.json> <materialization.json> <report.json>\n";
            return 2;
        }
        const auto report = execute(argv[1], argv[2], true);
        std::ofstream output(argv[3]);
        if(!output) throw std::runtime_error("cannot write final-representation native report");
        output << report.dump(2) << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
