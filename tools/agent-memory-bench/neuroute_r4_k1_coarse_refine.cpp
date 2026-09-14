#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#if defined(AGENT_MEMORY_NEUROUTE_K1_AOSOA_HAS_AVX2) && \
    AGENT_MEMORY_NEUROUTE_K1_AOSOA_HAS_AVX2
#include <immintrin.h>
#endif

namespace {

constexpr std::size_t dimensions = 384;
constexpr std::size_t record_bytes = dimensions + sizeof(float);
#if defined(AGENT_MEMORY_NEUROUTE_K1_AOSOA_HAS_AVX2) && \
    AGENT_MEMORY_NEUROUTE_K1_AOSOA_HAS_AVX2
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
    require(bytes % sizeof(T) == 0, "coarse/refine input byte count differs");
    std::vector<T> values(bytes / sizeof(T));
    std::ifstream stream(path, std::ios::binary);
    require(static_cast<bool>(stream), "coarse/refine input open failed");
    stream.read(reinterpret_cast<char*>(values.data()),
                static_cast<std::streamsize>(bytes));
    require(static_cast<bool>(stream), "coarse/refine input truncated");
    return values;
}

std::vector<std::size_t> parse_a_values(const std::string& text) {
    std::vector<std::size_t> values;
    std::stringstream stream(text);
    std::string token;
    while (std::getline(stream, token, ',')) {
        require(!token.empty(), "empty coarse/refine A value");
        values.push_back(static_cast<std::size_t>(std::stoull(token)));
    }
    require(!values.empty(), "coarse/refine A grid is empty");
    return values;
}

float int8_dot(const std::uint8_t* record, const float* query) {
    float scale = 0.0F;
    std::memcpy(&scale, record + dimensions, sizeof(scale));
    float result = 0.0F;
    for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
        result += (static_cast<int>(record[dimension]) - 127) * scale *
                  query[dimension];
    }
    return result;
}

#if defined(AGENT_MEMORY_NEUROUTE_K1_AOSOA_HAS_AVX2) && \
    AGENT_MEMORY_NEUROUTE_K1_AOSOA_HAS_AVX2
void score_aosoa_avx2(const std::int8_t* data, std::size_t rows,
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
                            static_cast<float>(values[lane + index]) * multiplier;
                }
            }
        }
        std::copy_n(accum.data(), valid, scores.data() + tile * lanes);
    }
}
#endif

void benchmark(const std::filesystem::path& coarse_path, std::size_t coarse_rows,
               const std::filesystem::path& store_path, std::size_t store_rows,
               const std::filesystem::path& offsets_path,
               const std::filesystem::path& counts_path,
               const std::filesystem::path& queries_path,
               const std::vector<std::size_t>& a_values, std::size_t measured_passes,
               const std::filesystem::path& output_path,
               const std::filesystem::path& order_path,
               const std::filesystem::path* coarse_scale_path,
               const std::string& coarse_layout_mode = "row_scalar",
               std::size_t coarse_lanes = 1) {
    const bool coarse_int8 = coarse_scale_path != nullptr;
    const bool coarse_aosoa = coarse_int8 && coarse_layout_mode == "aosoa_avx2";
    require(coarse_layout_mode == "row_scalar" || coarse_layout_mode == "aosoa_avx2",
            "coarse layout mode differs");
    require(!coarse_aosoa || coarse_lanes > 0, "coarse AoSoA lanes differ");
    const auto coarse = coarse_int8 ? std::vector<float>{}
                                    : read_values<float>(coarse_path);
    const auto coarse_codes = coarse_int8 ? read_values<std::int8_t>(coarse_path)
                                          : std::vector<std::int8_t>{};
    const auto coarse_scales = coarse_int8
        ? read_values<float>(*coarse_scale_path) : std::vector<float>{};
    const auto store = read_values<std::uint8_t>(store_path);
    const auto offsets = read_values<std::uint32_t>(offsets_path);
    const auto counts = read_values<std::uint8_t>(counts_path);
    const auto queries = read_values<float>(queries_path);
    const auto expected_coarse_values = coarse_aosoa
        ? ((coarse_rows + coarse_lanes - 1) / coarse_lanes) * dimensions * coarse_lanes
        : coarse_rows * dimensions;
    require((coarse_int8 ? coarse_codes.size() == expected_coarse_values &&
                            coarse_scales.size() == dimensions
                         : coarse.size() == coarse_rows * dimensions) &&
            store.size() == store_rows * record_bytes &&
            offsets.size() == counts.size() && coarse_rows == offsets.size() &&
            queries.size() % dimensions == 0 && measured_passes >= 1,
            "coarse/refine input shape differs");
    for (const auto a : a_values)
        require(a > 0 && a <= coarse_rows, "coarse/refine A exceeds addresses");

    const auto query_count = queries.size() / dimensions;
    nlohmann::json samples = nlohmann::json::array();
    std::ofstream order_output(order_path, std::ios::binary);
    require(static_cast<bool>(order_output), "coarse/refine order output open failed");
    const std::uint32_t magic = 0x314F5243U;  // CRO1
    const std::uint32_t header[] = {
        magic, 1U, static_cast<std::uint32_t>(query_count),
        static_cast<std::uint32_t>(a_values.size()),
        static_cast<std::uint32_t>(measured_passes)};
    order_output.write(reinterpret_cast<const char*>(header), sizeof(header));
    for (const auto a : a_values) {
        const auto value = static_cast<std::uint32_t>(a);
        order_output.write(reinterpret_cast<const char*>(&value), sizeof(value));
    }
    require(static_cast<bool>(order_output), "coarse/refine order header failed");
    double checksum = 0.0;
    for (std::size_t pass = 0; pass != measured_passes + 1; ++pass) {
        for (std::size_t query_index = 0; query_index != query_count; ++query_index) {
            const auto* query = queries.data() + query_index * dimensions;
            const auto coarse_begin = std::chrono::steady_clock::now();
            std::vector<float> coarse_scores(coarse_rows, 0.0F);
            if (coarse_aosoa) {
#if defined(AGENT_MEMORY_NEUROUTE_K1_AOSOA_HAS_AVX2) && \
    AGENT_MEMORY_NEUROUTE_K1_AOSOA_HAS_AVX2
                score_aosoa_avx2(coarse_codes.data(), coarse_rows, coarse_lanes,
                                 coarse_scales.data(), query, coarse_scores);
#else
                for (std::size_t address = 0; address != coarse_rows; ++address) {
                    const auto tile = address / coarse_lanes;
                    const auto lane = address % coarse_lanes;
                    float score = 0.0F;
                    for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
                        score += static_cast<float>(coarse_codes[(tile * dimensions + dimension) *
                                                                 coarse_lanes + lane]) *
                                 coarse_scales[dimension] * query[dimension];
                    coarse_scores[address] = score;
                }
#endif
            } else for (std::size_t address = 0; address != coarse_rows; ++address) {
                float score = 0.0F;
                if (coarse_int8) {
                    const auto* row = coarse_codes.data() + address * dimensions;
                    for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
                        score += static_cast<float>(row[dimension]) *
                                 coarse_scales[dimension] * query[dimension];
                } else {
                    const auto* row = coarse.data() + address * dimensions;
                    for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
                        score += row[dimension] * query[dimension];
                }
                coarse_scores[address] = score;
            }
            std::vector<std::uint32_t> order(coarse_rows);
            std::iota(order.begin(), order.end(), 0U);
            std::sort(order.begin(), order.end(), [&](std::uint32_t left,
                                                       std::uint32_t right) {
                if (coarse_scores[left] != coarse_scores[right])
                    return coarse_scores[left] > coarse_scores[right];
                return left < right;
            });
            const auto coarse_end = std::chrono::steady_clock::now();
            const double coarse_ms = std::chrono::duration<double, std::milli>(
                coarse_end - coarse_begin).count();
            for (const auto a : a_values) {
                const auto refine_begin = std::chrono::steady_clock::now();
                std::vector<float> refined_scores(a, 0.0F);
                std::vector<std::pair<float, std::uint32_t>> refined;
                refined.reserve(a);
                double local_checksum = 0.0;
                std::uint64_t representatives = 0;
                for (std::size_t rank = 0; rank != a; ++rank) {
                    const auto address = order[rank];
                    float maximum = -1.0e30F;
                    for (std::size_t slot = 0; slot != counts[address]; ++slot) {
                        const auto physical = static_cast<std::size_t>(offsets[address]) + slot;
                        maximum = std::max(maximum,
                            int8_dot(store.data() + physical * record_bytes, query));
                        ++representatives;
                    }
                    refined_scores[rank] = maximum;
                    refined.emplace_back(maximum, address);
                    local_checksum += maximum;
                }
                std::sort(refined.begin(), refined.end(),
                          [](const auto& left, const auto& right) {
                              if (left.first != right.first) return left.first > right.first;
                              return left.second < right.second;
                          });
                const auto refine_end = std::chrono::steady_clock::now();
                checksum += local_checksum;
                if (pass != 0) {
                    for (const auto& item : refined) {
                        const auto address = item.second;
                        order_output.write(reinterpret_cast<const char*>(&address), sizeof(address));
                        order_output.write(reinterpret_cast<const char*>(&item.first), sizeof(item.first));
                    }
                    require(static_cast<bool>(order_output), "coarse/refine order write failed");
                    samples.push_back({
                        {"pass", pass - 1}, {"query", query_index},
                        {"addresses_refined", a},
                        {"coarse_layout", coarse_layout_mode},
                        {"coarse_lanes", coarse_lanes},
                        {"coarse_avx2_compiled", avx2_compiled},
                        {"coarse_ms", coarse_ms},
                        {"refine_ms", std::chrono::duration<double, std::milli>(
                            refine_end - refine_begin).count()},
                        {"representatives_scored", representatives}});
                }
            }
        }
    }
    order_output.close();
    std::ofstream output(output_path);
    require(static_cast<bool>(output), "coarse/refine output open failed");
    output << nlohmann::json{
        {"schema_version", 1},
        {"family", "semantic_r4_k1_coarse_k16_native_samples_v1"},
        {"coarse_encoding", coarse_int8 ? "int8_per_dimension" : "fp32"},
        {"coarse_layout", coarse_layout_mode}, {"coarse_lanes", coarse_lanes},
        {"coarse_avx2_compiled", avx2_compiled},
        {"coarse_rows", coarse_rows}, {"store_rows", store_rows},
        {"queries", query_count}, {"measured_passes", measured_passes},
        {"a_values", a_values}, {"checksum", checksum}, {"samples", samples}
    }.dump(2) << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 13 && std::string(argv[1]) == "--benchmark-coarse-refine") {
            benchmark(argv[2], static_cast<std::size_t>(std::stoull(argv[3])),
                      argv[4], static_cast<std::size_t>(std::stoull(argv[5])),
                      argv[6], argv[7], argv[8], parse_a_values(argv[9]),
                       static_cast<std::size_t>(std::stoull(argv[10])), argv[11], argv[12], nullptr);
            return 0;
        }
        if (argc == 14 && std::string(argv[1]) == "--benchmark-coarse-refine-int8") {
            const std::filesystem::path scale_path = argv[13];
            benchmark(argv[2], static_cast<std::size_t>(std::stoull(argv[3])),
                      argv[4], static_cast<std::size_t>(std::stoull(argv[5])),
                      argv[6], argv[7], argv[8], parse_a_values(argv[9]),
                      static_cast<std::size_t>(std::stoull(argv[10])), argv[11], argv[12],
                      &scale_path);
            return 0;
        }
        if (argc == 16 && std::string(argv[1]) ==
            "--benchmark-coarse-refine-int8-layout") {
            const std::filesystem::path scale_path = argv[13];
            benchmark(argv[2], static_cast<std::size_t>(std::stoull(argv[3])),
                      argv[4], static_cast<std::size_t>(std::stoull(argv[5])),
                      argv[6], argv[7], argv[8], parse_a_values(argv[9]),
                      static_cast<std::size_t>(std::stoull(argv[10])), argv[11], argv[12],
                      &scale_path, argv[15],
                      static_cast<std::size_t>(std::stoull(argv[14])));
            return 0;
        }
        throw std::runtime_error(
            "usage: --benchmark-coarse-refine[-int8[-layout]] COARSE COARSE_ROWS STORE STORE_ROWS OFFSETS COUNTS QUERIES A_VALUES PASSES OUTPUT ORDER_OUTPUT [SCALES [LANES MODE]]");
    } catch (const std::exception& error) {
        std::cerr << "agent-memory-neuroute-r4-k1-coarse-refine: "
                  << error.what() << '\n';
        return 1;
    }
}
