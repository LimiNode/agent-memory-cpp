#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

struct Ranked final {
    float score = 0.0F;
    std::uint32_t position = 0;
    std::uint32_t rank = 0;
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

template <typename Value>
std::vector<Value> read_array(const std::filesystem::path& root, const nlohmann::json& payload) {
    const auto path = root / payload.at("file").get<std::string>();
    if(agent_memory::sha256_file_hex(path) != payload.at("sha256").get<std::string>())
        throw std::runtime_error("exact-E5 payload hash differs");
    std::ifstream stream(path, std::ios::binary);
    if(!stream) throw std::runtime_error("cannot read exact-E5 payload");
    std::vector<Value> result(element_count(payload));
    stream.read(reinterpret_cast<char*>(result.data()), static_cast<std::streamsize>(result.size() * sizeof(Value)));
    if(!stream || stream.peek() != std::ifstream::traits_type::eof())
        throw std::runtime_error("exact-E5 payload size differs");
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

std::vector<std::uint32_t> exact_top_10(const std::vector<float>& documents,
                                        const std::vector<float>& queries,
                                        const std::vector<std::uint32_t>& document_ranks,
                                        const std::vector<std::uint32_t>& adc,
                                        std::size_t document_count, std::size_t query_count,
                                        std::size_t dimensions, std::size_t query,
                                        std::size_t adc_limit) {
    if(query >= query_count || adc_limit > adc.size())
        throw std::runtime_error("exact-E5 query or ADC limit differs");
    std::vector<Ranked> scores;
    scores.reserve(adc_limit);
    const auto* query_vector = queries.data() + query * dimensions;
    for(std::size_t index = 0; index < adc_limit; ++index) {
        const auto position = adc[index];
        if(position >= document_count) throw std::runtime_error("exact-E5 document position differs");
        const auto* document = documents.data() + static_cast<std::size_t>(position) * dimensions;
        float score = 0.0F;
        for(std::size_t dimension = 0; dimension < dimensions; ++dimension)
            score += document[dimension] * query_vector[dimension];
        scores.push_back({score, position, document_ranks[position]});
    }
    const auto count = std::min<std::size_t>(10, scores.size());
    if(count < scores.size()) {
        std::nth_element(scores.begin(), scores.begin() + static_cast<std::ptrdiff_t>(count), scores.end(), better);
        scores.resize(count);
    }
    std::sort(scores.begin(), scores.end(), better);
    std::vector<std::uint32_t> result;
    result.reserve(scores.size());
    for(const auto& value : scores) result.push_back(value.position);
    return result;
}

double quantile(std::vector<double> values, double fraction) {
    if(values.empty()) throw std::runtime_error("exact-E5 timing sample is empty");
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

nlohmann::json execute(const std::filesystem::path& contract_path,
                       const std::filesystem::path& manifest_path, bool timings) {
    nlohmann::json contract, manifest;
    std::ifstream contract_stream(contract_path), manifest_stream(manifest_path);
    contract_stream >> contract;
    manifest_stream >> manifest;
    if(contract.value("family", "") != "neuroute_exact_e5_rerank_ablation"
       || manifest.value("family", "") != "neuroute_exact_e5_rerank_materialization"
       || manifest.at("contract_sha256") != agent_memory::sha256_file_hex(contract_path))
        throw std::runtime_error("exact-E5 contract/materialization binding differs");
    const auto warmups = contract.at("native_timing").at("warmup_passes").get<std::size_t>();
    const auto repeats = contract.at("native_timing").at("measured_passes").get<std::size_t>();
    const auto adc_max = contract.at("cascade").at("adc_limits").back().get<std::size_t>();
    const auto root = manifest_path.parent_path();
    nlohmann::json rows = nlohmann::json::array(), datasets = nlohmann::json::array();
    for(const auto& dataset : manifest.at("datasets")) {
        const auto dataset_root = root / dataset.at("id").get<std::string>();
        const auto& common = dataset.at("common");
        const auto documents = read_array<float>(dataset_root, common.at("document_vectors"));
        const auto queries = read_array<float>(dataset_root, common.at("query_vectors"));
        const auto ranks = read_array<std::uint32_t>(dataset_root, common.at("document_id_rank"));
        const auto document_count = dataset.at("document_count").get<std::size_t>();
        const auto query_count = dataset.at("query_count").get<std::size_t>();
        const std::size_t dimensions = 384;
        if(documents.size() != document_count * dimensions || queries.size() != query_count * dimensions
           || ranks.size() != document_count)
            throw std::runtime_error("exact-E5 common payload shape differs");
        datasets.push_back({{"dataset", dataset.at("id")},
                            {"resident_document_fp32_bytes", documents.size() * sizeof(float)},
                            {"resident_query_fp32_bytes", queries.size() * sizeof(float)}});
        for(const auto& route : dataset.at("routes")) {
            const auto route_root = dataset_root / std::to_string(route.at("seed").get<std::uint32_t>());
            const auto adc = read_array<std::uint32_t>(route_root, route.at("adc_positions"));
            if(adc.size() != query_count * adc_max)
                throw std::runtime_error("exact-E5 ADC payload shape differs");
            for(const auto& expected : route.at("expected")) {
                const auto limit = expected.at("adc_limit").get<std::size_t>();
                std::vector<std::uint8_t> digest_bytes;
                for(std::size_t query = 0; query < query_count; ++query) {
                    const std::vector<std::uint32_t> pool(
                        adc.begin() + static_cast<std::ptrdiff_t>(query * adc_max),
                        adc.begin() + static_cast<std::ptrdiff_t>((query + 1) * adc_max));
                    const auto ranked = exact_top_10(documents, queries, ranks, pool,
                                                     document_count, query_count, dimensions, query, limit);
                    if(sequence_sha256(ranked) != expected.at("queries").at(query).at("ranked_sha256"))
                        throw std::runtime_error("exact-E5 per-query replay differs");
                    append_query(digest_bytes, query, ranked);
                }
                const auto digest = agent_memory::sha256_bytes_hex(digest_bytes);
                if(digest != expected.at("exact_sequence_sha256").get<std::string>())
                    throw std::runtime_error("exact-E5 global replay differs");
                nlohmann::json timing = nullptr;
                if(timings) {
                    for(std::size_t warmup = 0; warmup < warmups; ++warmup)
                        for(std::size_t query = 0; query < query_count; ++query) {
                            const std::vector<std::uint32_t> pool(
                                adc.begin() + static_cast<std::ptrdiff_t>(query * adc_max),
                                adc.begin() + static_cast<std::ptrdiff_t>((query + 1) * adc_max));
                            static_cast<void>(exact_top_10(documents, queries, ranks, pool,
                                document_count, query_count, dimensions, query, limit));
                        }
                    std::vector<double> samples;
                    samples.reserve(query_count * repeats);
                    for(std::size_t repeat = 0; repeat < repeats; ++repeat)
                        for(std::size_t query = 0; query < query_count; ++query) {
                            const std::vector<std::uint32_t> pool(
                                adc.begin() + static_cast<std::ptrdiff_t>(query * adc_max),
                                adc.begin() + static_cast<std::ptrdiff_t>((query + 1) * adc_max));
                            const auto start = Clock::now();
                            static_cast<void>(exact_top_10(documents, queries, ranks, pool,
                                document_count, query_count, dimensions, query, limit));
                            samples.push_back(milliseconds(start, Clock::now()));
                        }
                    timing = timing_summary(samples);
                }
                rows.push_back({{"dataset", dataset.at("id")}, {"seed", route.at("seed")},
                                {"adc_limit", limit}, {"query_count", query_count},
                                {"exact_sequence_sha256", digest}, {"timing_ms_per_query", timing}});
            }
        }
    }
    return {{"schema_version", 1}, {"family", "neuroute_exact_e5_rerank_native_result"},
            {"claim_scope", contract.at("claim_scope")},
            {"contract_sha256", agent_memory::sha256_file_hex(contract_path)},
            {"materialization_sha256", agent_memory::sha256_file_hex(manifest_path)},
            {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
            {"timings_recorded", timings}, {"datasets", datasets}, {"rows", rows}};
}

void validate_report(const nlohmann::json& expected, const nlohmann::json& replay) {
    if(expected.value("family", "") != "neuroute_exact_e5_rerank_native_result"
       || expected.at("contract_sha256") != replay.at("contract_sha256")
       || expected.at("materialization_sha256") != replay.at("materialization_sha256")
       || expected.at("evaluator_source_manifest_sha256") != replay.at("evaluator_source_manifest_sha256")
       || expected.at("datasets") != replay.at("datasets") || expected.at("rows").size() != replay.at("rows").size())
        throw std::runtime_error("exact-E5 native report binding differs");
    for(std::size_t index = 0; index < replay.at("rows").size(); ++index) {
        for(const auto* field : {"dataset", "seed", "adc_limit", "query_count", "exact_sequence_sha256"})
            if(expected.at("rows").at(index).at(field) != replay.at("rows").at(index).at(field))
                throw std::runtime_error(std::string("exact-E5 native report differs: ") + field);
        if(!expected.at("rows").at(index).at("timing_ms_per_query").is_object())
            throw std::runtime_error("exact-E5 native timing is absent");
    }
}

int self_test() {
    const std::vector<float> documents{1.0F, 0.0F, 0.5F, 0.5F, 0.0F, 1.0F};
    const std::vector<float> queries{1.0F, 0.0F};
    const std::vector<std::uint32_t> ranks{2, 1, 0}, adc{0, 1, 2};
    const auto ranked = exact_top_10(documents, queries, ranks, adc, 3, 1, 2, 0, 3);
    if(ranked != std::vector<std::uint32_t>({0, 1, 2})
       || sequence_sha256({7, 2, 99}) != "1673c447a7acb075da4fcf6fceaae46afa50428aa1b77fdc6a2868c3248120c1")
        throw std::runtime_error("exact-E5 native self-test differs");
    std::cout << "NeuRoute exact-E5 native self-test passed\n";
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
            std::cout << "NeuRoute exact-E5 native report replay passed\n";
            return 0;
        }
        if(argc != 4) {
            std::cerr << "usage: agent-memory-neuroute-exact-e5-rerank "
                         "<contract.json> <materialization.json> <report.json>\n";
            return 2;
        }
        const auto report = execute(argv[1], argv[2], true);
        std::ofstream output(argv[3]);
        if(!output) throw std::runtime_error("cannot write exact-E5 native report");
        output << report.dump(2) << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
