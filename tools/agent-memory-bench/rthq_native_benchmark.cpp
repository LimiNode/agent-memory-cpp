#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <numeric>
#include <string>
#include <vector>

#if defined(_MSC_VER)
#include <intrin.h>
#endif

namespace {
using Clock = std::chrono::steady_clock;

std::uint32_t popcount64(std::uint64_t value) {
#if defined(_MSC_VER)
  return static_cast<std::uint32_t>(__popcnt64(value));
#else
  return static_cast<std::uint32_t>(__builtin_popcountll(value));
#endif
}

std::uint32_t hamming(const std::uint8_t* lhs, const std::uint8_t* rhs,
                      std::size_t bytes) {
  std::uint32_t result = 0;
  for (std::size_t offset = 0; offset < bytes; offset += 8) {
    std::uint64_t left = 0;
    std::uint64_t right = 0;
    std::memcpy(&left, lhs + offset, 8);
    std::memcpy(&right, rhs + offset, 8);
    result += popcount64(left ^ right);
  }
  return result;
}

std::vector<std::size_t> topk(const std::vector<std::uint16_t>& distances,
                              std::size_t k) {
  std::vector<std::size_t> ids(distances.size());
  std::iota(ids.begin(), ids.end(), 0);
  std::nth_element(ids.begin(), ids.begin() + k, ids.end(),
                   [&](std::size_t a, std::size_t b) {
                     return distances[a] < distances[b] ||
                            (distances[a] == distances[b] && a < b);
                   });
  ids.resize(k);
  std::sort(ids.begin(), ids.end(), [&](std::size_t a, std::size_t b) {
    return distances[a] < distances[b] ||
           (distances[a] == distances[b] && a < b);
  });
  return ids;
}

double quantile(std::vector<double> values, double q) {
  std::sort(values.begin(), values.end());
  return values[std::min(values.size() - 1,
                         static_cast<std::size_t>(q * values.size()))];
}
}  // namespace

int main(int argc, char** argv) {
  if (argc < 2) {
    std::cerr << "usage: rthq_native_benchmark CODE_PATH [records] [queries] [iterations]\n";
    return 2;
  }
  const std::string path = argv[1];
  const std::size_t records = argc > 2 ? std::stoull(argv[2]) : 1000000;
  const std::size_t queries = argc > 3 ? std::stoull(argv[3]) : 152;
  const std::size_t iterations = argc > 4 ? std::stoull(argv[4]) : 3;
  constexpr std::size_t kBytes = 144;
  constexpr std::size_t k = 256;
  if (records <= k || queries == 0 || iterations == 0) return 2;

  std::vector<std::uint8_t> codes(records * kBytes);
  std::ifstream input(path, std::ios::binary);
  input.read(reinterpret_cast<char*>(codes.data()),
             static_cast<std::streamsize>(codes.size()));
  if (input.gcount() != static_cast<std::streamsize>(codes.size())) return 3;
  const std::size_t query_count = std::min(queries, records);
  std::vector<double> scan_ms;
  std::vector<double> select_ms;
  std::uint64_t checksum = 0;
  for (std::size_t iteration = 0; iteration < iterations; ++iteration) {
    const auto scan_start = Clock::now();
    for (std::size_t query = 0; query < query_count; ++query) {
      const auto* query_code = codes.data() + query * kBytes;
      std::vector<std::uint16_t> distances(records);
      for (std::size_t id = 0; id < records; ++id) {
        distances[id] = static_cast<std::uint16_t>(hamming(
            codes.data() + id * kBytes, query_code, kBytes));
      }
      checksum += distances[query];
    }
    scan_ms.push_back(
        std::chrono::duration<double, std::milli>(Clock::now() - scan_start)
            .count());
    const auto select_start = Clock::now();
    for (std::size_t query = 0; query < query_count; ++query) {
      const auto* query_code = codes.data() + query * kBytes;
      std::vector<std::uint16_t> distances(records);
      for (std::size_t id = 0; id < records; ++id) {
        distances[id] = static_cast<std::uint16_t>(hamming(
            codes.data() + id * kBytes, query_code, kBytes));
      }
      const auto selected = topk(distances, k);
      checksum += selected.front() + selected.back();
    }
    select_ms.push_back(
        std::chrono::duration<double, std::milli>(Clock::now() - select_start)
            .count());
  }
  std::cout << "{\"records\":" << records << ",\"queries\":"
            << query_count << ",\"iterations\":" << iterations
            << ",\"bytes_per_record\":" << kBytes
            << ",\"bits\":1152,\"levels\":4"
            << ",\"ranking_metric\":\"plain_hamming\""
            << ",\"tie_policy\":\"distance_ascending_then_document_id_ascending\""
            << ",\"scan_total_ms_p50\":" << quantile(scan_ms, .50)
            << ",\"scan_total_ms_p95\":" << quantile(scan_ms, .95)
            << ",\"scan_ms_per_query_p50\":"
            << quantile(scan_ms, .50) / query_count
            << ",\"scan_ms_per_query_p95\":"
            << quantile(scan_ms, .95) / query_count
            << ",\"scan_plus_topk_total_ms_p50\":"
            << quantile(select_ms, .50)
            << ",\"scan_plus_topk_ms_per_query_p50\":"
            << quantile(select_ms, .50) / query_count
            << ",\"checksum\":" << checksum << "}\n";
}
