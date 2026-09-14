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

namespace {

constexpr std::size_t dimensions = 384;
constexpr std::size_t record_bytes = dimensions + sizeof(float);

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

void benchmark(const std::filesystem::path& coarse_path, std::size_t coarse_rows,
               const std::filesystem::path& store_path, std::size_t store_rows,
               const std::filesystem::path& offsets_path,
               const std::filesystem::path& counts_path,
               const std::filesystem::path& queries_path,
               const std::vector<std::size_t>& a_values, std::size_t measured_passes,
               const std::filesystem::path& output_path,
               const std::filesystem::path& order_path) {
    const auto coarse = read_values<float>(coarse_path);
    const auto store = read_values<std::uint8_t>(store_path);
    const auto offsets = read_values<std::uint32_t>(offsets_path);
    const auto counts = read_values<std::uint8_t>(counts_path);
    const auto queries = read_values<float>(queries_path);
    require(coarse.size() == coarse_rows * dimensions &&
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
            for (std::size_t address = 0; address != coarse_rows; ++address) {
                float score = 0.0F;
                const auto* row = coarse.data() + address * dimensions;
                for (std::size_t dimension = 0; dimension != dimensions; ++dimension)
                    score += row[dimension] * query[dimension];
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
                       static_cast<std::size_t>(std::stoull(argv[10])), argv[11], argv[12]);
            return 0;
        }
        throw std::runtime_error(
            "usage: --benchmark-coarse-refine COARSE COARSE_ROWS STORE STORE_ROWS OFFSETS COUNTS QUERIES A_VALUES PASSES OUTPUT ORDER_OUTPUT");
    } catch (const std::exception& error) {
        std::cerr << "agent-memory-neuroute-r4-k1-coarse-refine: "
                  << error.what() << '\n';
        return 1;
    }
}
