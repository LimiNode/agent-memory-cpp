#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

constexpr std::size_t kDimension = 384U;
constexpr std::size_t kBinaryBits = 512U;
constexpr std::size_t kBinaryBytes = kBinaryBits / 8U;
constexpr std::size_t kTopK = 64U;

template <class T>
std::vector<T> read_binary(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("cannot open " + path.string());
    }

    stream.seekg(0, std::ios::end);
    const auto bytes = static_cast<std::size_t>(stream.tellg());
    stream.seekg(0);
    if (bytes % sizeof(T) != 0U) {
        throw std::runtime_error("invalid payload width: " + path.string());
    }

    std::vector<T> values(bytes / sizeof(T));
    stream.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(bytes));
    if (!stream) {
        throw std::runtime_error("cannot read " + path.string());
    }
    return values;
}

float dot(const float* left, const float* right, const std::size_t count) {
    float result = 0.0F;
    for (std::size_t index = 0U; index < count; ++index) {
        result += left[index] * right[index];
    }
    return result;
}

unsigned popcount8(std::uint8_t value) {
    unsigned result = 0U;
    while (value != 0U) {
        value = static_cast<std::uint8_t>(value & static_cast<std::uint8_t>(value - 1U));
        ++result;
    }
    return result;
}

std::uint16_t fp16(const float value) {
    union {
        float floating;
        std::uint32_t integer;
    } bits{value};

    const auto sign = (bits.integer >> 16U) & 0x8000U;
    auto exponent = static_cast<int>((bits.integer >> 23U) & 0xffU) - 127 + 15;
    auto mantissa = bits.integer & 0x7fffffU;
    if (exponent <= 0) {
        return static_cast<std::uint16_t>(sign);
    }
    if (exponent >= 31) {
        return static_cast<std::uint16_t>(sign | 0x7c00U);
    }

    mantissa = (mantissa + 0x1000U) >> 13U;
    if (mantissa == 0x400U) {
        mantissa = 0U;
        ++exponent;
    }
    if (exponent >= 31) {
        return static_cast<std::uint16_t>(sign | 0x7c00U);
    }
    return static_cast<std::uint16_t>(
        sign | (static_cast<std::uint32_t>(exponent) << 10U) | mantissa);
}

float from_fp16(const std::uint16_t value) {
    const auto sign = (value & 0x8000U) == 0U ? 1.0F : -1.0F;
    const auto exponent = static_cast<int>((value >> 10U) & 0x1fU);
    const auto mantissa = static_cast<float>(value & 0x3ffU) / 1024.0F;
    if (exponent == 0) {
        return sign * std::ldexp(mantissa, -14);
    }
    return sign * std::ldexp(1.0F + mantissa, exponent - 15);
}

std::size_t overlap(const std::vector<std::uint32_t>& left, const std::vector<std::uint32_t>& right) {
    std::size_t result = 0U;
    for (const auto value : left) {
        result += std::find(right.begin(), right.end(), value) != right.end() ? 1U : 0U;
    }
    return result;
}

std::vector<std::uint32_t> top(std::vector<float> scores) {
    std::vector<std::uint32_t> ids(scores.size());
    for (std::size_t index = 0U; index < ids.size(); ++index) {
        ids[index] = static_cast<std::uint32_t>(index);
    }

    const auto limit = std::min(kTopK, ids.size());
    std::partial_sort(ids.begin(), ids.begin() + static_cast<std::ptrdiff_t>(limit), ids.end(),
        [&scores](const std::uint32_t left, const std::uint32_t right) {
            return scores[left] == scores[right] ? left < right : scores[left] > scores[right];
        });
    ids.resize(limit);
    return ids;
}

double percentile(const std::vector<double>& samples, const double fraction) {
    auto ordered = samples;
    std::sort(ordered.begin(), ordered.end());
    const auto position = static_cast<std::size_t>(
        std::floor(fraction * static_cast<double>(ordered.size() - 1U)));
    return ordered[position];
}

float score(const std::string& method,
    const std::size_t centroid,
    const std::size_t query,
    const std::vector<float>& centroids,
    const std::vector<float>& query_vectors,
    const std::vector<std::uint16_t>& half,
    const std::vector<std::int8_t>& int8,
    const std::vector<float>& scales,
    const std::vector<std::uint8_t>& centroid_codes,
    const std::vector<std::uint8_t>& query_codes,
    const std::vector<float>& query_projection) {
    const auto centroid_offset = centroid * kDimension;
    const auto query_offset = query * kDimension;

    if (method == "fp32") {
        return dot(centroids.data() + centroid_offset, query_vectors.data() + query_offset, kDimension);
    }
    if (method == "fp16") {
        float result = 0.0F;
        for (std::size_t dimension = 0U; dimension < kDimension; ++dimension) {
            result += from_fp16(half[centroid_offset + dimension]) * query_vectors[query_offset + dimension];
        }
        return result;
    }
    if (method == "int8") {
        float result = 0.0F;
        for (std::size_t dimension = 0U; dimension < kDimension; ++dimension) {
            result += static_cast<float>(int8[centroid_offset + dimension]) * scales[centroid] *
                query_vectors[query_offset + dimension];
        }
        return result;
    }
    if (method == "symmetric_hamming") {
        float result = 0.0F;
        const auto centroid_code_offset = centroid * kBinaryBytes;
        const auto query_code_offset = query * kBinaryBytes;
        for (std::size_t byte = 0U; byte < kBinaryBytes; ++byte) {
            result -= static_cast<float>(popcount8(static_cast<std::uint8_t>(
                centroid_codes[centroid_code_offset + byte] ^ query_codes[query_code_offset + byte])));
        }
        return result;
    }

    float result = 0.0F;
    const auto centroid_code_offset = centroid * kBinaryBytes;
    const auto query_projection_offset = query * kBinaryBits;
    for (std::size_t bit = 0U; bit < kBinaryBits; ++bit) {
        const auto code = centroid_codes[centroid_code_offset + bit / 8U];
        const auto sign = ((code >> (bit % 8U)) & 1U) == 0U ? -1.0F : 1.0F;
        result += query_projection[query_projection_offset + bit] * sign;
    }
    return result;
}

int run(const std::filesystem::path& root,
    const std::filesystem::path& report,
    const std::size_t warmups,
    const std::size_t repeats) {
    std::ifstream manifest_stream(root / "manifest.json");
    if (!manifest_stream) {
        throw std::runtime_error("native centroid manifest missing");
    }
    nlohmann::json manifest;
    manifest_stream >> manifest;
    if (manifest.value("family", "") != "native_centroid_routing_materialization_v1" ||
        manifest.value("dimension", 0U) != kDimension || manifest.value("query_count", 0U) == 0U) {
        throw std::runtime_error("native centroid manifest differs");
    }

    const auto centroid_count = manifest.at("centroid_count").get<std::size_t>();
    const auto query_count = manifest.at("query_count").get<std::size_t>();
    const auto& outputs = manifest.at("outputs");
    const auto centroids = read_binary<float>(root / outputs.at("centroids").at("path").get<std::string>());
    const auto query_vectors = read_binary<float>(root / outputs.at("queries").at("path").get<std::string>());
    const auto centroid_codes = read_binary<std::uint8_t>(root / outputs.at("centroid_codes").at("path").get<std::string>());
    const auto query_codes = read_binary<std::uint8_t>(root / outputs.at("query_codes").at("path").get<std::string>());
    const auto query_projection = read_binary<float>(root / outputs.at("query_projection").at("path").get<std::string>());
    if (centroids.size() != centroid_count * kDimension || query_vectors.size() != query_count * kDimension ||
        centroid_codes.size() != centroid_count * kBinaryBytes || query_codes.size() != query_count * kBinaryBytes ||
        query_projection.size() != query_count * kBinaryBits) {
        throw std::runtime_error("native centroid payload shape differs");
    }

    std::vector<std::uint16_t> half(centroids.size());
    std::vector<std::int8_t> int8(centroids.size());
    std::vector<float> scales(centroid_count);
    for (std::size_t centroid = 0U; centroid < centroid_count; ++centroid) {
        float maximum = 0.0F;
        const auto offset = centroid * kDimension;
        for (std::size_t dimension = 0U; dimension < kDimension; ++dimension) {
            const auto value = centroids[offset + dimension];
            half[offset + dimension] = fp16(value);
            maximum = std::max(maximum, std::abs(value));
        }
        scales[centroid] = maximum == 0.0F ? 1.0F : maximum / 127.0F;
        for (std::size_t dimension = 0U; dimension < kDimension; ++dimension) {
            int8[offset + dimension] = static_cast<std::int8_t>(
                std::round(centroids[offset + dimension] / scales[centroid]));
        }
    }

    const std::vector<std::string> methods{
        "fp32", "fp16", "int8", "symmetric_hamming", "asymmetric_sign_dot"};
    std::vector<std::vector<std::uint32_t>> oracle(query_count);
    for (std::size_t query = 0U; query < query_count; ++query) {
        std::vector<float> scores(centroid_count);
        for (std::size_t centroid = 0U; centroid < centroid_count; ++centroid) {
            scores[centroid] = dot(centroids.data() + centroid * kDimension,
                query_vectors.data() + query * kDimension, kDimension);
        }
        oracle[query] = top(std::move(scores));
    }

    nlohmann::json rows = nlohmann::json::array();
    for (const auto& method : methods) {
        std::vector<double> samples;
        double recall = 0.0;
        for (std::size_t pass = 0U; pass < warmups + repeats; ++pass) {
            double current_recall = 0.0;
            const auto begin = Clock::now();
            for (std::size_t query = 0U; query < query_count; ++query) {
                std::vector<float> scores(centroid_count);
                for (std::size_t centroid = 0U; centroid < centroid_count; ++centroid) {
                    scores[centroid] = score(method, centroid, query, centroids, query_vectors, half, int8,
                        scales, centroid_codes, query_codes, query_projection);
                }
                const auto ranked = top(std::move(scores));
                current_recall += static_cast<double>(overlap(oracle[query], ranked)) /
                    static_cast<double>(kTopK);
            }
            const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - begin).count() /
                static_cast<double>(query_count);
            if (pass >= warmups) {
                samples.push_back(elapsed);
                recall = current_recall / static_cast<double>(query_count);
            }
        }
        rows.push_back({
            {"method", method},
            {"float_top64_recall", recall},
            {"routing_ms_per_query_samples", samples},
            {"routing_p50_ms_per_query", percentile(samples, 0.50)},
            {"routing_p95_ms_per_query", percentile(samples, 0.95)},
        });
    }

    const auto fp32_bytes = centroid_count * kDimension * sizeof(float);
    const nlohmann::json output{
        {"schema_version", 1},
        {"family", "native_centroid_routing_scan_preflight_v1"},
        {"materialization_manifest_sha256", manifest.value("input_manifest_sha256", "")},
        {"centroid_count", centroid_count},
        {"query_count", query_count},
        {"warmup_repeats", warmups},
        {"measured_repeats", repeats},
        {"payload_bytes", {
            {"fp32", fp32_bytes},
            {"fp16", fp32_bytes / 2U},
            {"int8", centroid_count * kDimension * sizeof(std::int8_t) + centroid_count * sizeof(float)},
            {"binary512", centroid_count * kBinaryBytes},
        }},
        {"rows", rows},
        {"timing_scope", "warm_in_memory_centroid_scans_only_excludes_query_encoding_candidate_mass_list_selection_and_document_cascade_v1"},
    };
    std::ofstream stream(report);
    if (!stream) {
        throw std::runtime_error("cannot write native centroid report");
    }
    stream << output.dump(2) << '\n';
    return 0;
}

void self_test() {
    if (popcount8(0b10110100U) != 4U || from_fp16(fp16(1.0F)) != 1.0F ||
        std::abs(from_fp16(fp16(0.125F)) - 0.125F) > 0.0001F) {
        throw std::runtime_error("native centroid scan primitive self-test differs");
    }
    const auto ranked = top({0.25F, 0.75F, 0.75F, 0.5F});
    if (ranked.size() != 4U || ranked[0] != 1U || ranked[1] != 2U) {
        throw std::runtime_error("native centroid scan ordering self-test differs");
    }
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--self-test") {
            self_test();
            std::cout << "native centroid routing scan self-test passed\n";
            return 0;
        }
        if (argc != 5) {
            std::cerr << "usage: agent-memory-native-centroid-routing-scan <materialization-dir> <report.json> <warmups> <repeats>\n";
            return 2;
        }
        return run(argv[1], argv[2], static_cast<std::size_t>(std::stoul(argv[3])),
            static_cast<std::size_t>(std::stoul(argv[4])));
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
