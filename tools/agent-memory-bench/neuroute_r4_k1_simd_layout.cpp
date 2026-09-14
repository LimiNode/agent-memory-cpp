#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

#if defined(AGENT_MEMORY_NEUROUTE_K1_LAYOUT_HAS_AVX2) && \
    AGENT_MEMORY_NEUROUTE_K1_LAYOUT_HAS_AVX2
#include <immintrin.h>
#endif

namespace {

constexpr std::size_t dimensions = 384;
constexpr std::size_t page_bytes = 4096;
#if defined(AGENT_MEMORY_NEUROUTE_K1_LAYOUT_HAS_AVX2) && \
    AGENT_MEMORY_NEUROUTE_K1_LAYOUT_HAS_AVX2
constexpr bool avx2_compiled = true;
#else
constexpr bool avx2_compiled = false;
#endif

void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

template <typename T>
std::vector<T> read_values(const std::filesystem::path& path) {
    const auto bytes = std::filesystem::file_size(path);
    require(bytes % sizeof(T) == 0, "K1 layout input byte count differs");
    std::vector<T> values(bytes / sizeof(T));
    std::ifstream stream(path, std::ios::binary);
    require(static_cast<bool>(stream), "K1 layout input open failed");
    stream.read(reinterpret_cast<char*>(values.data()),
                static_cast<std::streamsize>(bytes));
    require(static_cast<bool>(stream), "K1 layout input truncated");
    return values;
}

std::uint64_t fnv1a(const float* values, std::size_t count) {
    // A deterministic checksum is sufficient for the benchmark binding; the
    // independent Python audit additionally compares top-address identities.
    std::uint64_t hash = 1469598103934665603ULL;
    const auto* bytes = reinterpret_cast<const std::uint8_t*>(values);
    for (std::size_t i = 0; i != count * sizeof(float); ++i) {
        hash ^= bytes[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::uint64_t fnv1a(const std::uint32_t* values, std::size_t count) {
    std::uint64_t hash = 1469598103934665603ULL;
    const auto* bytes = reinterpret_cast<const std::uint8_t*>(values);
    for (std::size_t i = 0; i != count * sizeof(std::uint32_t); ++i) {
        hash ^= bytes[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::vector<std::uint8_t> read_bytes(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    require(static_cast<bool>(stream), "K1 layout byte input open failed");
    const auto size = stream.tellg();
    require(size >= 0, "K1 layout byte input size failed");
    std::vector<std::uint8_t> values(static_cast<std::size_t>(size));
    stream.seekg(0);
    stream.read(reinterpret_cast<char*>(values.data()), size);
    require(static_cast<bool>(stream), "K1 layout byte input truncated");
    return values;
}

std::vector<std::uint32_t> ranked_ids(const std::vector<float>& scores) {
    std::vector<std::uint32_t> order(scores.size());
    std::iota(order.begin(), order.end(), 0U);
    std::sort(order.begin(), order.end(), [&](std::uint32_t left,
                                             std::uint32_t right) {
        if (scores[left] != scores[right]) return scores[left] > scores[right];
        return left < right;
    });
    return order;
}

std::uint64_t prefix_set_checksum(const std::vector<std::uint32_t>& order,
                                  std::size_t count) {
    count = std::min(count, order.size());
    std::vector<std::uint32_t> values(order.begin(), order.begin() + count);
    std::sort(values.begin(), values.end());
    return fnv1a(values.data(), values.size());
}

#if defined(AGENT_MEMORY_NEUROUTE_K1_LAYOUT_HAS_AVX2) && \
    AGENT_MEMORY_NEUROUTE_K1_LAYOUT_HAS_AVX2
void score_aosoa_avx2(const std::uint8_t* data, std::size_t rows,
                      std::size_t lanes, const float* scales,
                      const float* query, std::vector<float>& scores) {
    const auto tiles = (rows + lanes - 1) / lanes;
    scores.assign(rows, 0.0F);
    for (std::size_t tile = 0; tile != tiles; ++tile) {
        const auto valid = std::min(lanes, rows - tile * lanes);
        std::vector<float> accum(lanes, 0.0F);
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
            const auto* values = data + (tile * dimensions + dimension) * lanes;
            const float multiplier = scales[dimension] * query[dimension];
            for (std::size_t lane = 0; lane < valid; lane += 8) {
                const auto count = std::min<std::size_t>(8, valid - lane);
                if (count == 8) {
                    const auto bytes = _mm_loadl_epi64(
                        reinterpret_cast<const __m128i*>(values + lane));
                    const auto ints = _mm256_cvtepi8_epi32(bytes);
                    const auto floats = _mm256_cvtepi32_ps(ints);
                    const auto product = _mm256_mul_ps(
                        floats, _mm256_set1_ps(multiplier));
                    auto current = _mm256_loadu_ps(accum.data() + lane);
                    current = _mm256_add_ps(current, product);
                    _mm256_storeu_ps(accum.data() + lane, current);
                } else {
                    for (std::size_t index = 0; index != count; ++index)
                        accum[lane + index] +=
                            static_cast<float>(static_cast<std::int8_t>(values[lane + index])) *
                            multiplier;
                }
            }
        }
        std::copy_n(accum.data(), valid, scores.data() + tile * lanes);
    }
}
#endif

std::vector<float> score_row_scalar(const std::uint8_t* data, std::size_t rows,
                                    const float* scales, const float* query) {
    std::vector<float> scores(rows, 0.0F);
    for (std::size_t row = 0; row != rows; ++row) {
        const auto* values = data + row * dimensions;
        float score = 0.0F;
        for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
            score += static_cast<float>(static_cast<std::int8_t>(values[dimension])) *
                     scales[dimension] * query[dimension];
        scores[row] = score;
    }
    return scores;
}

void benchmark(const std::filesystem::path& data_path, std::size_t rows,
               const std::filesystem::path& scale_path,
               const std::filesystem::path& query_path, std::size_t query_count,
               std::size_t lanes, const std::string& mode,
               std::size_t measured_passes, const std::filesystem::path& output_path) {
    const auto data = read_bytes(data_path);
    const auto scales = read_values<float>(scale_path);
    const auto queries = read_values<float>(query_path);
    require(scales.size() == dimensions && queries.size() == query_count * dimensions,
            "K1 layout shape differs");
    const bool scalar = mode == "row_scalar";
    const bool simd = mode == "aosoa_avx2";
    require(scalar || simd, "K1 layout mode differs");
    const auto expected = scalar ? rows * dimensions
                                  : ((rows + lanes - 1) / lanes) * dimensions * lanes;
    require(data.size() == expected, "K1 layout data size differs");
#if !(defined(AGENT_MEMORY_NEUROUTE_K1_LAYOUT_HAS_AVX2) && \
      AGENT_MEMORY_NEUROUTE_K1_LAYOUT_HAS_AVX2)
    require(!simd, "K1 layout AVX2 is not enabled in this build");
#endif
    std::vector<float> scores;
    nlohmann::json samples = nlohmann::json::array();
    std::uint64_t checksum = 0;
    for (std::size_t pass = 0; pass != measured_passes + 1; ++pass) {
        for (std::size_t query_index = 0; query_index != query_count; ++query_index) {
            const auto* query = queries.data() + query_index * dimensions;
            const auto begin = std::chrono::steady_clock::now();
            if (scalar) {
                scores = score_row_scalar(data.data(), rows, scales.data(), query);
            } else {
#if defined(AGENT_MEMORY_NEUROUTE_K1_LAYOUT_HAS_AVX2) && \
    AGENT_MEMORY_NEUROUTE_K1_LAYOUT_HAS_AVX2
                score_aosoa_avx2(data.data(), rows, lanes, scales.data(), query, scores);
#endif
            }
            const auto end = std::chrono::steady_clock::now();
            const auto order = ranked_ids(scores);
            const std::vector<std::uint32_t> ids(order.begin(), order.begin() +
                std::min<std::size_t>(128, order.size()));
            checksum ^= fnv1a(scores.data(), scores.size());
            checksum ^= fnv1a(ids.data(), ids.size());
            if (pass != 0) {
                nlohmann::json sample = {
                    {"pass", pass - 1}, {"query", query_index},
                    {"mode", mode}, {"lanes", lanes},
                    {"rows", rows}, {"logical_bytes", rows * dimensions},
                    {"physical_bytes", data.size()},
                    {"logical_pages_4k", (rows * dimensions + page_bytes - 1) / page_bytes},
                    {"physical_pages_4k", (data.size() + page_bytes - 1) / page_bytes},
                    {"tile_bytes", scalar ? 0 : lanes * dimensions},
                    {"tile_pages_4k", scalar ? 0 : (lanes * dimensions + page_bytes - 1) / page_bytes},
                    {"elapsed_ms", std::chrono::duration<double, std::milli>(end - begin).count()},
                    {"score_checksum", fnv1a(scores.data(), scores.size())},
                    {"top128_checksum", fnv1a(ids.data(), ids.size())},
                    {"top128_ids", ids},
                    {"prefix_set_checksums", {
                        {"128", prefix_set_checksum(order, 128)},
                        {"8192", prefix_set_checksum(order, 8192)},
                        {"16384", prefix_set_checksum(order, 16384)}}},
                    {"simd_used", simd}};
                if (pass == 1) {
                    sample["prefix_ids"] = {
                        {"8192", std::vector<std::uint32_t>(
                            order.begin(), order.begin() + std::min<std::size_t>(8192, order.size()))},
                        {"16384", std::vector<std::uint32_t>(
                            order.begin(), order.begin() + std::min<std::size_t>(16384, order.size()))}};
                }
                samples.push_back(std::move(sample));
            }
        }
    }
    std::ofstream output(output_path);
    require(static_cast<bool>(output), "K1 layout output open failed");
    output << nlohmann::json{
        {"schema_version", 1}, {"family", "semantic_r4_k1_simd_layout_samples_v1"},
        {"mode", mode}, {"lanes", lanes}, {"rows", rows},
        {"queries", query_count}, {"measured_passes", measured_passes},
        {"avx2_compiled", avx2_compiled}, {"checksum", checksum}, {"samples", samples}}
        .dump(2) << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 11 && std::string(argv[1]) == "--benchmark") {
            benchmark(argv[2], static_cast<std::size_t>(std::stoull(argv[3])), argv[4],
                     argv[5], static_cast<std::size_t>(std::stoull(argv[6])),
                     static_cast<std::size_t>(std::stoull(argv[7])), argv[8],
                     static_cast<std::size_t>(std::stoull(argv[9])), argv[10]);
            return 0;
        }
        throw std::runtime_error(
            "usage: --benchmark DATA ROWS SCALES QUERIES QUERY_COUNT LANES MODE PASSES OUTPUT");
    } catch (const std::exception& error) {
        return (std::cerr << "agent-memory-neuroute-r4-k1-simd-layout: "
                          << error.what() << '\n'), 1;
    }
}
