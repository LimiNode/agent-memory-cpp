#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr std::size_t dimensions = 384;
constexpr std::size_t page_bytes = 4096;

void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

std::vector<std::uint32_t> read_ids(const std::filesystem::path& path,
                                    std::size_t count) {
    require(std::filesystem::file_size(path) == count * sizeof(std::uint32_t),
            "candidate id fixture size differs");
    std::vector<std::uint32_t> ids(count);
    std::ifstream stream(path, std::ios::binary);
    require(static_cast<bool>(stream), "candidate id fixture open failed");
    stream.read(reinterpret_cast<char*>(ids.data()),
                static_cast<std::streamsize>(ids.size() * sizeof(std::uint32_t)));
    require(static_cast<bool>(stream), "candidate id fixture truncated");
    return ids;
}

std::uint64_t checksum(const std::uint8_t* data, std::size_t size,
                       std::uint64_t state = 1469598103934665603ULL) {
    for (std::size_t i = 0; i != size; ++i) {
        state ^= data[i];
        state *= 1099511628211ULL;
    }
    return state;
}

struct ReadStats {
    std::uint64_t requested_bytes = 0;
    std::uint64_t read_calls = 0;
    std::set<std::uint64_t> pages;
    std::uint64_t checksum_value = 1469598103934665603ULL;
};

void account_range(ReadStats& stats, std::uint64_t offset, std::uint64_t size) {
    stats.requested_bytes += size;
    if (size == 0) return;
    const auto first = offset / page_bytes;
    const auto last = (offset + size - 1) / page_bytes;
    for (auto page = first; page <= last; ++page) stats.pages.insert(page);
}

void read_range(std::ifstream& stream, std::vector<std::uint8_t>& buffer,
                ReadStats& stats, std::uint64_t offset, std::uint64_t size) {
    require(size <= buffer.size(), "read range exceeds scratch buffer");
    stream.clear();
    stream.seekg(static_cast<std::streamoff>(offset));
    require(static_cast<bool>(stream), "read range seek failed");
    stream.read(reinterpret_cast<char*>(buffer.data()),
                static_cast<std::streamsize>(size));
    require(static_cast<std::uint64_t>(stream.gcount()) == size,
            "read range truncated");
    ++stats.read_calls;
    account_range(stats, offset, size);
    stats.checksum_value = checksum(buffer.data(), static_cast<std::size_t>(size),
                                    stats.checksum_value);
}

ReadStats full_scan(const std::filesystem::path& path, std::uint64_t bytes) {
    std::ifstream stream(path, std::ios::binary);
    require(static_cast<bool>(stream), "full scan open failed");
    constexpr std::size_t chunk = 1U << 20;
    std::vector<std::uint8_t> buffer(chunk);
    ReadStats stats;
    std::uint64_t offset = 0;
    while (offset < bytes) {
        const auto size = std::min<std::uint64_t>(chunk, bytes - offset);
        read_range(stream, buffer, stats, offset, size);
        offset += size;
    }
    return stats;
}

ReadStats candidate_gather(const std::filesystem::path& path, std::size_t rows,
                           std::size_t lanes, const std::string& mode,
                           const std::vector<std::uint32_t>& ids) {
    const auto file_bytes = std::filesystem::file_size(path);
    const bool row_major = mode == "row_scalar";
    require(row_major || mode == "aosoa_avx2", "page read mode differs");
    const auto tile_bytes = lanes * dimensions;
    const auto expected = row_major ? rows * dimensions
                                    : ((rows + lanes - 1) / lanes) * tile_bytes;
    require(file_bytes == expected, "page read layout size differs");
    std::ifstream stream(path, std::ios::binary);
    require(static_cast<bool>(stream), "candidate gather open failed");
    std::vector<std::uint8_t> buffer(tile_bytes);
    std::set<std::uint64_t> ranges;
    ReadStats stats;
    for (const auto id : ids) {
        require(id < rows, "candidate id out of range");
        const auto tile = row_major ? static_cast<std::uint64_t>(id)
                                    : static_cast<std::uint64_t>(id / lanes);
        const auto offset = row_major ? tile * dimensions : tile * tile_bytes;
        const auto size = row_major ? dimensions : tile_bytes;
        if (ranges.insert(offset).second) read_range(stream, buffer, stats, offset, size);
    }
    return stats;
}

void benchmark(const std::filesystem::path& data_path, std::size_t rows,
               std::size_t lanes, const std::string& mode,
               const std::filesystem::path& ids_path, std::size_t query_count,
               std::size_t candidate_count, std::size_t measured_passes,
               const std::string& operation, const std::filesystem::path& output) {
    const auto ids = read_ids(ids_path, query_count * candidate_count);
    const auto file_bytes = std::filesystem::file_size(data_path);
    const auto logical_bytes = rows * dimensions;
    nlohmann::json samples = nlohmann::json::array();
    std::uint64_t invocation_checksum = 0;
    for (std::size_t pass = 0; pass != measured_passes + 1; ++pass) {
        // A full sidecar scan is query-independent.  Measure it once per
        // pass; candidate gather remains one sample per query.
        const auto effective_queries = operation == "full_scan" ? std::size_t{1} : query_count;
        for (std::size_t query = 0; query != effective_queries; ++query) {
            const auto begin = std::chrono::steady_clock::now();
            ReadStats stats;
            if (operation == "full_scan") {
                stats = full_scan(data_path, file_bytes);
            } else if (operation == "candidate_gather") {
                const auto first = ids.begin() + query * candidate_count;
                stats = candidate_gather(data_path, rows, lanes, mode,
                                         std::vector<std::uint32_t>(
                                             first, first + candidate_count));
            } else {
                throw std::runtime_error("page read operation differs");
            }
            const auto end = std::chrono::steady_clock::now();
            invocation_checksum ^= stats.checksum_value;
            if (pass == 0) continue;  // one untimed/open warm-up pass
            samples.push_back({
                {"pass", pass - 1}, {"query", query}, {"mode", mode},
                {"lanes", lanes}, {"operation", operation},
                {"rows", rows}, {"logical_payload_bytes", logical_bytes},
                {"file_bytes", file_bytes},
                {"requested_read_bytes", stats.requested_bytes},
                {"unique_file_pages_4k", stats.pages.size()},
                {"read_calls", stats.read_calls},
                {"checksum", stats.checksum_value},
                {"elapsed_ms", std::chrono::duration<double, std::milli>(end - begin).count()}});
        }
    }
    std::ofstream stream(output);
    require(static_cast<bool>(stream), "page read output open failed");
    stream << nlohmann::json{
        {"schema_version", 1},
        {"family", "semantic_r4_k1_page_read_samples_v1"},
        {"mode", mode}, {"lanes", lanes}, {"operation", operation},
        {"queries", query_count}, {"candidate_count", candidate_count},
        {"effective_queries", operation == "full_scan" ? 1 : query_count},
        {"measured_passes", measured_passes},
        {"warmup_passes", 1}, {"invocation_checksum", invocation_checksum},
        {"samples", samples}}.dump(2) << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 12 && std::string(argv[1]) == "--benchmark") {
            benchmark(argv[2], static_cast<std::size_t>(std::stoull(argv[3])),
                      static_cast<std::size_t>(std::stoull(argv[4])), argv[5], argv[6],
                      static_cast<std::size_t>(std::stoull(argv[7])),
                      static_cast<std::size_t>(std::stoull(argv[8])),
                      static_cast<std::size_t>(std::stoull(argv[9])), argv[10], argv[11]);
            return 0;
        }
        throw std::runtime_error(
            "usage: --benchmark DATA ROWS LANES MODE IDS QUERY_COUNT CANDIDATES PASSES OP OUTPUT");
    } catch (const std::exception& error) {
        return (std::cerr << "agent-memory-neuroute-r4-k1-page-read: "
                          << error.what() << '\n'), 1;
    }
}
