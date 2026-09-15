#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <numeric>
#include <string>
#include <vector>

namespace {
constexpr std::size_t kDocuments = 1000000;
constexpr std::size_t kDimension = 384;
struct Candidate { float score; std::int32_t id; };
bool better(const Candidate& a, const Candidate& b) {
  return a.score < b.score || (a.score == b.score && a.id < b.id);
}
template <typename T> std::vector<T> read(const std::string& path) {
  std::ifstream in(path, std::ios::binary | std::ios::ate);
  if (!in) throw std::runtime_error("cannot open " + path);
  const auto end = in.tellg();
  if (end < 0 || static_cast<std::size_t>(end) % sizeof(T) != 0)
    throw std::runtime_error("unaligned payload " + path);
  std::vector<T> out(static_cast<std::size_t>(end) / sizeof(T));
  in.seekg(0); in.read(reinterpret_cast<char*>(out.data()), end);
  if (!in) throw std::runtime_error("cannot read " + path);
  return out;
}
std::vector<Candidate> top128(const std::vector<std::uint8_t>& codes,
                              const std::vector<float>& thresholds,
                              const float* query) {
  std::vector<Candidate> all; all.reserve(kDocuments);
  for (std::size_t id = 0; id < kDocuments; ++id) {
    const auto* row = codes.data() + id * 96; float score = 0.0f;
    for (std::size_t d = 0; d < kDimension; ++d) {
      const auto level = static_cast<std::uint8_t>((row[d / 4] >> ((d % 4) * 2)) & 3U);
      const float lo = level == 0 ? -std::numeric_limits<float>::infinity() : thresholds[d * 3 + level - 1];
      const float hi = level == 3 ? std::numeric_limits<float>::infinity() : thresholds[d * 3 + level];
      const float delta = query[d] < lo ? lo - query[d] : (query[d] > hi ? query[d] - hi : 0.0f);
      score += delta * delta;
    }
    all.push_back({score, static_cast<std::int32_t>(id)});
  }
  std::partial_sort(all.begin(), all.begin() + 128, all.end(), better);
  all.resize(128); return all;
}
template <typename CodeT>
Candidate best_int8(const std::vector<CodeT>& codes, const std::vector<float>& scales,
                    const float* query, std::int32_t id, float power) {
  const auto* row = codes.data() + static_cast<std::size_t>(id) * kDimension;
  const float scale = scales[static_cast<std::size_t>(id)]; float score = 0.0f;
  static const auto power_table = [] {
    std::array<float, 128> table{};
    for (std::size_t i = 0; i < table.size(); ++i) table[i] = std::pow(static_cast<float>(i), 1.0f / 0.625f);
    return table;
  }();
  const float power_scale = power == 1.0f ? 1.0f : std::pow(scale, 1.0f / power);
  for (std::size_t d = 0; d < kDimension; ++d) {
    float value = static_cast<float>(row[d]) * scale;
    if (power != 1.0f) value = std::copysign(power_table[static_cast<std::size_t>(std::abs(static_cast<int>(row[d]))) ] * power_scale, value);
    score += value * query[d];
  }
  return {score, id};
}
template <typename CodeT>
std::pair<Candidate, std::uint64_t> direct(const std::vector<CodeT>& codes,
                                           const std::vector<float>& scales,
                                           const float* query, float power) {
  Candidate best{-std::numeric_limits<float>::infinity(), 0};
  for (std::int32_t id = 0; id < static_cast<std::int32_t>(kDocuments); ++id) {
    const Candidate c = best_int8(codes, scales, query, id, power);
    if (c.score > best.score || (c.score == best.score && c.id < best.id)) best = c;
  }
  return {best, static_cast<std::uint64_t>((kDocuments * 388 + 4095) / 4096)};
}
template <typename CodeT>
std::pair<Candidate, std::uint64_t> cascade(const std::vector<std::uint8_t>& thq,
                                            const std::vector<float>& thresholds,
                                            const std::vector<CodeT>& codes,
                                            const std::vector<float>& scales,
                                            const float* query, float power) {
  const auto coarse = top128(thq, thresholds, query);
  Candidate best{-std::numeric_limits<float>::infinity(), 0};
  std::vector<std::uint64_t> pages;
  for (const auto& c : coarse) {
    const Candidate exact = best_int8(codes, scales, query, c.id, power);
    if (exact.score > best.score || (exact.score == best.score && exact.id < best.id)) best = exact;
    pages.push_back((static_cast<std::uint64_t>(c.id) * 388) / 4096);
  }
  std::sort(pages.begin(), pages.end()); pages.erase(std::unique(pages.begin(), pages.end()), pages.end());
  return {best, pages.size()};
}
}
int main(int argc, char** argv) {
  if (argc != 9) { std::cerr << "usage: benchmark thq thresholds linear linear_scales power power_scales query_file query_count\n"; return 2; }
  try {
    const auto thq = read<std::uint8_t>(argv[1]); const auto thresholds = read<float>(argv[2]);
    const auto linear = read<std::int8_t>(argv[3]); const auto linear_scales = read<float>(argv[4]);
    const auto power = read<std::int8_t>(argv[5]); const auto power_scales = read<float>(argv[6]);
    if (thq.size() != kDocuments * 96 || thresholds.size() != kDimension * 3 ||
        linear.size() != kDocuments * kDimension || power.size() != kDocuments * kDimension ||
        linear_scales.size() != kDocuments || power_scales.size() != kDocuments)
      throw std::runtime_error("payload shape differs");
    const std::size_t q = static_cast<std::size_t>(std::stoull(argv[8]));
    std::ifstream query_stream(argv[7], std::ios::binary);
    if (!query_stream) throw std::runtime_error("cannot open query payload");
    std::vector<float> queries(q * kDimension); query_stream.read(reinterpret_cast<char*>(queries.data()), static_cast<std::streamsize>(queries.size() * sizeof(float)));
    if (!query_stream) throw std::runtime_error("query payload is truncated");
    double direct_ms = 0, cascade_ms = 0, direct_power_ms = 0, cascade_power_ms = 0;
    for (std::size_t i = 0; i < q; ++i) {
      const float* query = queries.data() + i * kDimension;
      const auto begin = std::chrono::steady_clock::now(); const auto d = direct(linear, linear_scales, query, 1.0f); const auto mid = std::chrono::steady_clock::now();
      const auto c = cascade(thq, thresholds, linear, linear_scales, query, 1.0f); const auto after_c = std::chrono::steady_clock::now();
      const auto dp = direct(power, power_scales, query, 0.625f); const auto after_dp = std::chrono::steady_clock::now();
      const auto cp = cascade(thq, thresholds, power, power_scales, query, 0.625f); const auto end = std::chrono::steady_clock::now();
      direct_ms += std::chrono::duration<double, std::milli>(mid - begin).count(); cascade_ms += std::chrono::duration<double, std::milli>(after_c - mid).count();
      direct_power_ms += std::chrono::duration<double, std::milli>(after_dp - after_c).count(); cascade_power_ms += std::chrono::duration<double, std::milli>(end - after_dp).count();
      std::cout << "{\"query\":" << i << ",\"direct_linear_top1\":" << d.first.id << ",\"cascade_linear_top1\":" << c.first.id << ",\"direct_power0625_top1\":" << dp.first.id << ",\"cascade_power0625_top1\":" << cp.first.id << "}\n";
    }
    std::cerr << "{\"queries\":" << q << ",\"direct_linear_mean_ms\":" << direct_ms / q << ",\"cascade_linear_mean_ms\":" << cascade_ms / q << ",\"direct_power0625_mean_ms\":" << direct_power_ms / q << ",\"cascade_power0625_mean_ms\":" << cascade_power_ms / q << "}\n";
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
