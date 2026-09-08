#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
#include <simdbitpacking.h>
#endif

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::size_t dimensions = 384;
constexpr std::size_t pool_size = 64;
constexpr std::size_t result_size = 10;

struct Scored {
    float score = 0.0F;
    std::uint32_t position = 0;
    std::uint32_t rank = 0;
};

bool better(const Scored& left, const Scored& right) {
    return left.score != right.score ? left.score > right.score : left.rank < right.rank;
}

std::size_t element_count(const nlohmann::json& payload) {
    std::size_t result = 1;
    for (const auto& value : payload.at("shape")) {
        result *= value.get<std::size_t>();
    }
    return result;
}

template <class T>
std::vector<T> read_payload(const std::filesystem::path& root,
                            const nlohmann::json& payload) {
    const auto path = root / payload.at("file").get<std::string>();
    if (agent_memory::sha256_file_hex(path) != payload.at("sha256")) {
        throw std::runtime_error("final-codec payload hash differs");
    }
    std::ifstream stream(path, std::ios::binary);
    std::vector<T> values(element_count(payload));
    stream.read(reinterpret_cast<char*>(values.data()),
                static_cast<std::streamsize>(values.size() * sizeof(T)));
    if (!stream || stream.peek() != std::ifstream::traits_type::eof()) {
        throw std::runtime_error("final-codec payload size differs");
    }
    return values;
}

void append_u32(std::vector<std::uint8_t>& bytes, std::uint32_t value) {
    for (int shift = 0; shift != 32; shift += 8) {
        bytes.push_back(static_cast<std::uint8_t>((value >> shift) & 255U));
    }
}

std::string sequence_sha256(const std::vector<std::uint32_t>& values) {
    std::vector<std::uint8_t> bytes;
    for (const auto value : values) {
        append_u32(bytes, value);
    }
    return agent_memory::sha256_bytes_hex(bytes);
}

std::vector<std::uint8_t> scalar_pack(const std::uint8_t* values, unsigned bits) {
    std::vector<std::uint8_t> output(dimensions * bits / 8, 0);
    for (std::size_t index = 0; index != dimensions; ++index) {
        const auto bit = index * bits;
        const auto byte = bit / 8;
        const auto offset = static_cast<unsigned>(bit % 8);
        const auto value = static_cast<std::uint16_t>(values[index]);
        output[byte] |= static_cast<std::uint8_t>(value << offset);
        if (offset + bits > 8) {
            output[byte + 1] |= static_cast<std::uint8_t>(value >> (8 - offset));
        }
    }
    return output;
}

void scalar_unpack(const std::uint8_t* bytes, unsigned bits, std::uint32_t* output) {
    const auto mask = (1U << bits) - 1U;
    for (std::size_t index = 0; index != dimensions; ++index) {
        const auto bit = index * bits;
        const auto byte = bit / 8;
        const auto offset = static_cast<unsigned>(bit % 8);
        std::uint16_t value = static_cast<std::uint16_t>(bytes[byte] >> offset);
        if (offset + bits > 8) {
            value |= static_cast<std::uint16_t>(bytes[byte + 1]) << (8 - offset);
        }
        output[index] = value & mask;
    }
}

struct Layout {
    std::string id;
    unsigned bits = 0;
    std::size_t bytes_per_document = 0;
    std::vector<std::uint8_t> bytes;
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    std::vector<__m128i> simd_words;
#endif
};

Layout make_scalar_layout(const std::vector<std::uint8_t>& raw, unsigned bits) {
    Layout result;
    result.id = "scalar_bp128";
    result.bits = bits;
    result.bytes_per_document = dimensions * bits / 8 + sizeof(float);
    result.bytes.resize(raw.size() / dimensions * dimensions * bits / 8);
    const auto row_bytes = dimensions * bits / 8;
    for (std::size_t row = 0; row != raw.size() / dimensions; ++row) {
        const auto packed = scalar_pack(raw.data() + row * dimensions, bits);
        std::copy(packed.begin(), packed.end(), result.bytes.begin() + row * row_bytes);
    }
    return result;
}

Layout make_raw_layout(const std::vector<std::uint8_t>& raw) {
    Layout result;
    result.id = "raw_int8";
    result.bits = 8;
    result.bytes_per_document = dimensions + sizeof(float);
    result.bytes = raw;
    return result;
}

#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
Layout make_simdcomp_layout(const std::vector<std::uint8_t>& raw, unsigned bits) {
    Layout result;
    result.id = "simdcomp_bp128";
    result.bits = bits;
    result.bytes_per_document = dimensions * bits / 8 + sizeof(float);
    const auto rows = raw.size() / dimensions;
    result.simd_words.resize(rows * 3 * bits);
    std::array<std::uint32_t, dimensions> input{};
    for (std::size_t row = 0; row != rows; ++row) {
        std::copy_n(raw.data() + row * dimensions, dimensions, input.begin());
        for (std::size_t block = 0; block != 3; ++block) {
            simdpackwithoutmask(input.data() + block * 128,
                                result.simd_words.data() + (row * 3 + block) * bits,
                                bits);
        }
    }
    return result;
}
#endif

void decode_row(const Layout& layout, std::size_t row, std::uint32_t* output) {
    if (layout.id == "raw_int8") {
        std::copy_n(layout.bytes.data() + row * dimensions, dimensions, output);
        return;
    }
    if (layout.id == "scalar_bp128") {
        const auto row_bytes = dimensions * layout.bits / 8;
        scalar_unpack(layout.bytes.data() + row * row_bytes, layout.bits, output);
        return;
    }
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    if (layout.id == "simdcomp_bp128") {
        for (std::size_t block = 0; block != 3; ++block) {
            simdunpack(layout.simd_words.data() + (row * 3 + block) * layout.bits,
                       output + block * 128, layout.bits);
        }
        return;
    }
#endif
    throw std::runtime_error("final-codec layout differs");
}

std::vector<Scored> score_query(const Layout& layout, std::size_t query,
                                const std::vector<float>& scales,
                                const std::vector<std::uint32_t>& positions,
                                const std::vector<std::uint32_t>& ranks,
                                const float* query_vector) {
    std::array<std::uint32_t, dimensions> decoded{};
    std::vector<Scored> scored;
    scored.reserve(pool_size);
    const auto maximum = static_cast<int>((1U << (layout.bits - 1U)) - 1U);
    for (std::size_t document = 0; document != pool_size; ++document) {
        const auto row = query * pool_size + document;
        decode_row(layout, row, decoded.data());
        float score = 0.0F;
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
            score += static_cast<float>(static_cast<int>(decoded[dimension]) - maximum) *
                     query_vector[dimension];
        }
        scored.push_back({score * scales[row], positions[row], ranks[row]});
    }
    return scored;
}

std::vector<std::uint32_t> rank_query(const Layout& layout, std::size_t query,
                                      const std::vector<float>& scales,
                                      const std::vector<std::uint32_t>& positions,
                                      const std::vector<std::uint32_t>& ranks,
                                      const float* query_vector) {
    auto scored = score_query(layout, query, scales, positions, ranks, query_vector);
    std::nth_element(scored.begin(), scored.begin() + result_size, scored.end(), better);
    scored.resize(result_size);
    std::sort(scored.begin(), scored.end(), better);
    std::vector<std::uint32_t> result;
    for (const auto& value : scored) {
        result.push_back(value.position);
    }
    return result;
}

double milliseconds(Clock::time_point begin, Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - begin).count();
}

double quantile(std::vector<double> values, double fraction) {
    std::sort(values.begin(), values.end());
    const auto position = fraction * static_cast<double>(values.size() - 1);
    const auto lower = static_cast<std::size_t>(std::floor(position));
    const auto upper = static_cast<std::size_t>(std::ceil(position));
    return values[lower] * (static_cast<double>(upper) - position) +
           values[upper] * (position - static_cast<double>(lower));
}

nlohmann::json summary(const std::vector<double>& values) {
    return {{"mean", std::accumulate(values.begin(), values.end(), 0.0) / values.size()},
            {"p50", quantile(values, 0.50)}, {"p95", quantile(values, 0.95)},
            {"p99", quantile(values, 0.99)}, {"samples", values.size()}};
}

nlohmann::json time_layout(const Layout& layout, std::size_t query_count,
                           const std::vector<float>& scales,
                           const std::vector<std::uint32_t>& positions,
                           const std::vector<std::uint32_t>& ranks,
                           const std::vector<float>& queries,
                           std::size_t warmups, std::size_t passes, std::size_t microbatch) {
    std::array<std::uint32_t, dimensions> decoded{};
    volatile std::uint64_t checksum = 0;
    for (std::size_t warmup = 0; warmup != warmups; ++warmup) {
        for (std::size_t query = 0; query != query_count; ++query) {
            rank_query(layout, query, scales, positions, ranks,
                       queries.data() + query * dimensions);
        }
    }
    std::vector<double> decode_samples;
    std::vector<double> score_samples;
    std::vector<double> rank_samples;
    for (std::size_t pass = 0; pass != passes; ++pass) {
        for (std::size_t query = 0; query != query_count; ++query) {
            auto begin = Clock::now();
            for (std::size_t repeat = 0; repeat != microbatch; ++repeat) {
                for (std::size_t document = 0; document != pool_size; ++document) {
                    decode_row(layout, query * pool_size + document, decoded.data());
                    checksum += decoded[repeat % dimensions];
                }
            }
            auto end = Clock::now();
            decode_samples.push_back(milliseconds(begin, end) * 1.0e6 /
                                     (microbatch * pool_size));

            begin = Clock::now();
            for (std::size_t repeat = 0; repeat != microbatch; ++repeat) {
                const auto scored = score_query(layout, query, scales, positions, ranks,
                                                queries.data() + query * dimensions);
                checksum += static_cast<std::uint64_t>(scored[repeat % pool_size].position);
            }
            end = Clock::now();
            score_samples.push_back(milliseconds(begin, end) / microbatch);

            begin = Clock::now();
            for (std::size_t repeat = 0; repeat != microbatch; ++repeat) {
                const auto ranked = rank_query(layout, query, scales, positions, ranks,
                                               queries.data() + query * dimensions);
                checksum += ranked.front();
            }
            end = Clock::now();
            rank_samples.push_back(milliseconds(begin, end) / microbatch);
        }
    }
    return {{"decode_ns_per_vector", summary(decode_samples)},
            {"decode_and_dot_ms_per_query", summary(score_samples)},
            {"rank_top10_ms_per_query", summary(rank_samples)},
            {"checksum", checksum}};
}

std::vector<Layout> layouts(const std::vector<std::uint8_t>& raw, unsigned bits) {
    if (bits == 8) {
        return {make_raw_layout(raw)};
    }
    std::vector<Layout> result;
    result.push_back(make_scalar_layout(raw, bits));
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    result.push_back(make_simdcomp_layout(raw, bits));
#endif
    return result;
}

nlohmann::json execute(const std::filesystem::path& manifest_path, bool timing) {
    nlohmann::json manifest;
    std::ifstream(manifest_path) >> manifest;
    if (manifest.value("family", "") != "neuroute_final_codec_native_materialization") {
        throw std::runtime_error("final-codec manifest differs");
    }
    const auto root = manifest_path.parent_path();
    const auto warmups = manifest["timing"]["warmup_passes"].get<std::size_t>();
    const auto passes = manifest["timing"]["measured_passes"].get<std::size_t>();
    const auto microbatch = manifest["timing"]["microbatch"].get<std::size_t>();
    nlohmann::json rows = nlohmann::json::array();
    for (const auto& dataset : manifest["datasets"]) {
        const auto dataset_root = root / dataset["id"].get<std::string>();
        const auto queries = read_payload<float>(dataset_root, dataset["queries"]);
        const auto query_count = dataset["query_count"].get<std::size_t>();
        for (const auto& route : dataset["routes"]) {
            const auto route_root = dataset_root / std::to_string(route["seed"].get<std::uint32_t>());
            const auto positions = read_payload<std::uint32_t>(route_root, route["pools"]);
            const auto ranks = read_payload<std::uint32_t>(route_root, route["ranks"]);
            for (const auto& quantizer : route["quantizers"]) {
                const auto bits = quantizer["bits"].get<unsigned>();
                const auto raw = read_payload<std::uint8_t>(route_root, quantizer["raw_codes"]);
                const auto scales = read_payload<float>(route_root, quantizer["scales"]);
                for (const auto& layout : layouts(raw, bits)) {
                    std::vector<std::uint8_t> digest;
                    for (std::size_t query = 0; query != query_count; ++query) {
                        const auto ranked = rank_query(layout, query, scales, positions, ranks,
                                                       queries.data() + query * dimensions);
                        if (sequence_sha256(ranked) !=
                            quantizer["expected"][query]["ranked_sha256"].get<std::string>()) {
                            throw std::runtime_error("final-codec ranking replay differs");
                        }
                        append_u32(digest, static_cast<std::uint32_t>(query));
                        for (const auto value : ranked) {
                            append_u32(digest, value);
                        }
                    }
                    rows.push_back({
                        {"dataset", dataset["id"]}, {"seed", route["seed"]},
                        {"layout", layout.id}, {"bits", bits},
                        {"bytes_per_document", layout.bytes_per_document},
                        {"query_count", query_count},
                        {"sequence_sha256", agent_memory::sha256_bytes_hex(digest)},
                        {"timing", timing ? time_layout(layout, query_count, scales, positions,
                                                       ranks, queries, warmups, passes, microbatch)
                                          : nlohmann::json(nullptr)},
                    });
                }
            }
        }
    }
    return {{"schema_version", 1}, {"family", "neuroute_final_codec_native_result"},
            {"materialization_sha256", agent_memory::sha256_file_hex(manifest_path)},
            {"evaluator_source_manifest_sha256", AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},
            {"simdcomp_available", static_cast<bool>(AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP)},
            {"timings_recorded", timing}, {"rows", rows}};
}

void self_test() {
    std::array<std::uint8_t, dimensions> values{};
    for (std::size_t index = 0; index != dimensions; ++index) {
        values[index] = static_cast<std::uint8_t>(index % 32);
    }
    const auto packed = scalar_pack(values.data(), 5);
    std::array<std::uint32_t, dimensions> decoded{};
    scalar_unpack(packed.data(), 5, decoded.data());
    for (std::size_t index = 0; index != dimensions; ++index) {
        if (decoded[index] != values[index]) {
            throw std::runtime_error("final-codec scalar self-test differs");
        }
    }
#if AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP
    std::vector<std::uint8_t> raw(values.begin(), values.end());
    const auto layout = make_simdcomp_layout(raw, 5);
    decode_row(layout, 0, decoded.data());
    for (std::size_t index = 0; index != dimensions; ++index) {
        if (decoded[index] != values[index]) {
            throw std::runtime_error("final-codec simdcomp self-test differs");
        }
    }
#endif
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--self-test") {
            self_test();
            std::cout << "NeuRoute final-codec native self-test passed\n";
            return 0;
        }
        if (argc == 4 && std::string(argv[1]) == "--validate") {
            nlohmann::json expected;
            std::ifstream(argv[3]) >> expected;
            const auto replay = execute(argv[2], false);
            if (expected["materialization_sha256"] != replay["materialization_sha256"] ||
                expected["evaluator_source_manifest_sha256"] !=
                    replay["evaluator_source_manifest_sha256"] ||
                expected["rows"].size() != replay["rows"].size()) {
                throw std::runtime_error("final-codec native binding differs");
            }
            for (std::size_t index = 0; index != replay["rows"].size(); ++index) {
                if (expected["rows"][index]["sequence_sha256"] !=
                    replay["rows"][index]["sequence_sha256"]) {
                    throw std::runtime_error("final-codec native sequence differs");
                }
            }
            return 0;
        }
        if (argc != 3) {
            std::cerr << "usage: agent-memory-neuroute-final-codec MANIFEST OUTPUT\n";
            return 2;
        }
        const auto result = execute(argv[1], true);
        std::ofstream(argv[2]) << result.dump(2) << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
