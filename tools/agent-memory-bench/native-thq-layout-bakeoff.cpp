#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
std::vector<std::uint8_t> read_all(const std::string& path) {
  std::ifstream in(path, std::ios::binary | std::ios::ate);
  if (!in) throw std::runtime_error("cannot open input");
  const auto size = static_cast<std::size_t>(in.tellg()); in.seekg(0);
  std::vector<std::uint8_t> data(size); in.read(reinterpret_cast<char*>(data.data()), size);
  if (!in) throw std::runtime_error("short input"); return data;
}
double percentile(std::vector<double> values, double p) {
  std::sort(values.begin(), values.end());
  return values[std::min(values.size() - 1, static_cast<std::size_t>(p * values.size()))];
}
}

int main(int argc, char** argv) {
  if (argc != 5) { std::cerr << "usage: native-thq-layout-bakeoff CANONICAL96 DUPLICATE100 COUNTS_U32 PAGE_BYTES\n"; return 2; }
  const auto canonical = read_all(argv[1]);
  const auto duplicate = read_all(argv[2]);
  const auto counts = read_all(argv[3]);
  const std::size_t page = std::stoull(argv[4]);
  if (canonical.size() % 96 || duplicate.size() % 100 || counts.size() % 4) return 3;
  std::vector<double> canonical_ms, duplicate_ms;
  std::uint64_t checksum = 0, entries = 0, duplicate_reads = 0;
  std::size_t offset = 0;
  for (std::size_t q = 0; q < counts.size() / 4; ++q) {
    std::uint32_t count = 0; std::memcpy(&count, counts.data() + q * 4, 4);
    if (offset + static_cast<std::size_t>(count) * 100 > duplicate.size()) return 4;
    const auto start = Clock::now();
    std::vector<std::size_t> pages;
    pages.reserve(count);
    for (std::uint32_t i = 0; i < count; ++i) {
      const auto* record = duplicate.data() + (offset + i * 100);
      std::uint32_t id = 0; std::memcpy(&id, record, 4);
      if (static_cast<std::size_t>(id) * 96 + 96 > canonical.size()) return 5;
      const auto* code = canonical.data() + static_cast<std::size_t>(id) * 96;
      checksum += code[i % 96]; pages.push_back((static_cast<std::size_t>(id) * 96) / page);
    }
    canonical_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - start).count());
    const auto duplicate_start = Clock::now();
    for (std::uint32_t i = 0; i < count; ++i) checksum += duplicate[offset + i * 100 + 4 + (i % 96)];
    duplicate_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - duplicate_start).count());
    std::sort(pages.begin(), pages.end()); pages.erase(std::unique(pages.begin(), pages.end()), pages.end());
    entries += count; duplicate_reads += count;
    offset += static_cast<std::size_t>(count) * 100;
  }
  std::cout << "{\"queries\":" << canonical_ms.size() << ",\"entries\":" << entries
            << ",\"canonical_bytes\":" << canonical.size() << ",\"duplicated_bytes\":" << duplicate.size()
            << ",\"duplicate_read_ratio\":1.0,\"canonical_p50_ms\":" << percentile(canonical_ms, .5)
            << ",\"canonical_p95_ms\":" << percentile(canonical_ms, .95)
            << ",\"duplicate_p50_ms\":" << percentile(duplicate_ms, .5)
            << ",\"duplicate_p95_ms\":" << percentile(duplicate_ms, .95)
            << ",\"checksum\":" << checksum << "}\n";
}
