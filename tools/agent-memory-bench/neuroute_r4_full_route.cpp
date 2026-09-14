#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr std::size_t dimensions = 384;

void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

template <typename T>
std::vector<T> read_values(const std::filesystem::path& path) {
    const auto bytes = std::filesystem::file_size(path);
    require(bytes % sizeof(T) == 0, "R4 full route byte count differs");
    std::vector<T> result(bytes / sizeof(T));
    std::ifstream stream(path, std::ios::binary);
    require(static_cast<bool>(stream), "R4 full route input open failed");
    stream.read(reinterpret_cast<char*>(result.data()),
                static_cast<std::streamsize>(bytes));
    require(static_cast<bool>(stream), "R4 full route input read failed");
    return result;
}

inline float record_dot(const std::uint8_t* record, const float* query) {
    constexpr int maximum = 127;
    float amplitude = 0.0F;
    std::memcpy(&amplitude, record + dimensions, sizeof(amplitude));
    float result = 0.0F;
    for (std::size_t dimension = 0; dimension != dimensions; ++dimension) {
        result += static_cast<float>(static_cast<int>(record[dimension]) - maximum)
            * amplitude * query[dimension];
    }
    return result;
}

void benchmark_route(const std::filesystem::path& store_path, std::size_t rows,
                     const std::filesystem::path& offsets_path,
                     const std::filesystem::path& counts_path,
                     const std::filesystem::path& queries_path,
                     std::size_t measured_passes,
                     const std::filesystem::path& order_path,
                     const std::filesystem::path& output_path) {
    constexpr std::size_t record_bytes = dimensions + sizeof(float);
    const auto store = read_values<std::uint8_t>(store_path);
    const auto offsets = read_values<std::uint32_t>(offsets_path);
    const auto counts = read_values<std::uint8_t>(counts_path);
    const auto queries = read_values<float>(queries_path);
    require(store.size() == rows * record_bytes && offsets.size() == counts.size(),
            "R4 full route store/index shape differs");
    require(queries.size() % dimensions == 0 && measured_passes >= 1,
            "R4 full route query shape differs");
    const auto query_count = queries.size() / dimensions;
    const auto address_count = offsets.size();
    require(address_count > 0 && query_count > 0, "R4 full route is empty");
    for (std::size_t address = 0; address != address_count; ++address) {
        require(static_cast<std::size_t>(offsets[address]) + counts[address] <= rows,
                "R4 full route posting range differs");
    }

    std::vector<std::uint32_t> all_addresses(address_count);
    for (std::size_t address = 0; address != address_count; ++address)
        all_addresses[address] = static_cast<std::uint32_t>(address);
    std::vector<float> sorted_scores(query_count * address_count);
    std::vector<std::uint32_t> sorted_addresses(query_count * address_count);
    nlohmann::json samples = nlohmann::json::array();
    double checksum = 0.0;
    for (std::size_t pass = 0; pass != measured_passes + 1; ++pass) {
        for (std::size_t query_index = 0; query_index != query_count; ++query_index) {
            const auto score_begin = std::chrono::steady_clock::now();
            std::vector<float> scores(address_count);
            std::uint64_t representatives = 0;
            double local_checksum = 0.0;
            for (std::size_t address = 0; address != address_count; ++address) {
                float maximum = -std::numeric_limits<float>::infinity();
                for (std::size_t slot = 0; slot != counts[address]; ++slot) {
                    const auto physical = static_cast<std::size_t>(offsets[address]) + slot;
                    maximum = std::max(maximum, record_dot(
                        store.data() + physical * record_bytes,
                        queries.data() + query_index * dimensions));
                    ++representatives;
                }
                scores[address] = maximum;
                local_checksum += maximum;
            }
            const auto score_end = std::chrono::steady_clock::now();
            const auto sort_begin = score_end;
            std::vector<std::uint32_t> order = all_addresses;
            std::stable_sort(order.begin(), order.end(),
                [&scores](std::uint32_t left, std::uint32_t right) {
                    if (scores[left] != scores[right]) return scores[left] > scores[right];
                    return left < right;
                });
            const auto sort_end = std::chrono::steady_clock::now();
            const auto offset = query_index * address_count;
            if (pass == 1) {
                for (std::size_t rank = 0; rank != address_count; ++rank) {
                    sorted_addresses[offset + rank] = order[rank];
                    sorted_scores[offset + rank] = scores[order[rank]];
                }
            }
            checksum += local_checksum;
            if (pass != 0) samples.push_back({
                {"pass", pass - 1}, {"query", query_index},
                {"addresses_scored", address_count},
                {"representatives_scored", representatives},
                {"score_ms", std::chrono::duration<double, std::milli>(
                    score_end - score_begin).count()},
                {"sort_ms", std::chrono::duration<double, std::milli>(
                    sort_end - sort_begin).count()},
                {"score_sort_ms", std::chrono::duration<double, std::milli>(
                    sort_end - score_begin).count()}});
        }
    }

    std::ofstream order_output(order_path, std::ios::binary);
    require(static_cast<bool>(order_output), "R4 full route order output open failed");
    const std::uint32_t query_header = static_cast<std::uint32_t>(query_count);
    const std::uint32_t address_header = static_cast<std::uint32_t>(address_count);
    order_output.write(reinterpret_cast<const char*>(&query_header), sizeof(query_header));
    order_output.write(reinterpret_cast<const char*>(&address_header), sizeof(address_header));
    order_output.write(reinterpret_cast<const char*>(sorted_addresses.data()),
                       static_cast<std::streamsize>(sorted_addresses.size() * sizeof(std::uint32_t)));
    order_output.write(reinterpret_cast<const char*>(sorted_scores.data()),
                       static_cast<std::streamsize>(sorted_scores.size() * sizeof(float)));
    require(static_cast<bool>(order_output), "R4 full route order output write failed");
    order_output.close();
    std::ofstream output(output_path);
    require(static_cast<bool>(output), "R4 full route result output open failed");
    output << nlohmann::json{
        {"schema_version", 2},
        {"family", "neuroute_r4_full_route_native_scores_v1"},
        {"bits", 8}, {"compander", "uniform"}, {"rows", rows},
        {"queries", query_count}, {"addresses", address_count},
        {"measured_passes", measured_passes}, {"checksum", checksum},
        {"record_bytes", record_bytes},
        {"store_bytes", std::filesystem::file_size(store_path)},
        {"store_sha256", agent_memory::sha256_file_hex(store_path)},
        {"offsets_bytes", std::filesystem::file_size(offsets_path)},
        {"offsets_sha256", agent_memory::sha256_file_hex(offsets_path)},
        {"counts_bytes", std::filesystem::file_size(counts_path)},
        {"counts_sha256", agent_memory::sha256_file_hex(counts_path)},
        {"queries_bytes", std::filesystem::file_size(queries_path)},
        {"queries_sha256", agent_memory::sha256_file_hex(queries_path)},
        {"order_path", order_path.string()},
        {"order_bytes", std::filesystem::file_size(order_path)},
        {"order_sha256", agent_memory::sha256_file_hex(order_path)},
        {"samples", samples}}
        .dump(2) << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 13 && std::string(argv[1]) == "--benchmark-route") {
            require(std::string(argv[2]) == "8" && std::string(argv[3]) == "uniform",
                    "R4 full route representation differs");
            benchmark_route(argv[5], static_cast<std::size_t>(std::stoull(argv[6])),
                            argv[7], argv[8], argv[9],
                            static_cast<std::size_t>(std::stoull(argv[10])),
                            argv[11], argv[12]);
            return 0;
        }
        throw std::runtime_error("usage: --benchmark-route 8 uniform 0 STORE ROWS OFFSETS COUNTS QUERIES PASSES ORDER OUTPUT");
    } catch (const std::exception& error) {
        std::cerr << "agent-memory-neuroute-r4-full-route: " << error.what() << '\n';
        return 1;
    }
}
