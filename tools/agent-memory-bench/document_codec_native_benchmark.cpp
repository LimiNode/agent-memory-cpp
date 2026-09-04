#include <algorithm>
#include <cmath>
#include <chrono>
#include <cstring>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <numeric>
#include <random>
#include <string>
#include <vector>

namespace {
constexpr std::size_t kDimensions = 384;
using Clock = std::chrono::steady_clock;

struct Method { std::string id; std::size_t bytes; };

float dot(const float* a, const float* b) {
  float value = 0.0f;
  for (std::size_t i = 0; i < kDimensions; ++i) value += a[i] * b[i];
  return value;
}

std::uint16_t fp16(float value) {
  // Deterministic scalar half conversion; this benchmark is portable by default.
  std::uint32_t bits;
  std::memcpy(&bits, &value, sizeof(bits));
  const std::uint32_t sign = (bits >> 16) & 0x8000u;
  int exponent = static_cast<int>((bits >> 23) & 0xffu) - 127 + 15;
  std::uint32_t mantissa = bits & 0x7fffffu;
  if (exponent <= 0) return static_cast<std::uint16_t>(sign);
  if (exponent >= 31) return static_cast<std::uint16_t>(sign | 0x7c00u);
  return static_cast<std::uint16_t>(sign | (static_cast<std::uint32_t>(exponent) << 10) |
                                    (mantissa >> 13));
}

float fp16_value(std::uint16_t value) {
  const std::uint32_t sign = (value & 0x8000u) << 16;
  const std::uint32_t exponent = (value >> 10) & 31u;
  const std::uint32_t mantissa = value & 1023u;
  std::uint32_t bits;
  if (exponent == 0) bits = sign;
  else if (exponent == 31) bits = sign | 0x7f800000u | (mantissa << 13);
  else bits = sign | ((exponent - 15u + 127u) << 23) | (mantissa << 13);
  float result;
  std::memcpy(&result, &bits, sizeof(result));
  return result;
}

std::vector<float> random_vectors(std::size_t count, std::mt19937& rng) {
  std::normal_distribution<float> distribution(0.0f, 1.0f);
  std::vector<float> values(count * kDimensions);
  for (float& value : values) value = distribution(rng);
  return values;
}

struct Packed { std::vector<std::uint8_t> bytes; float scale; int bits; };

Packed encode_scalar(const float* vector, int bits) {
  const std::size_t count = (kDimensions * static_cast<std::size_t>(bits) + 7) / 8;
  Packed result{std::vector<std::uint8_t>(count, 0), 0.0f, bits};
  float maximum = 0.0f;
  for (std::size_t i = 0; i < kDimensions; ++i) maximum = std::max(maximum, std::abs(vector[i]));
  const int limit = (1 << (bits - 1)) - 1;
  result.scale = maximum > 0.0f ? maximum / static_cast<float>(limit) : 1.0f;
  std::uint64_t accumulator = 0;
  int available = 0;
  std::size_t output = 0;
  for (std::size_t i = 0; i < kDimensions; ++i) {
    int code = static_cast<int>(std::lrint(vector[i] / result.scale));
    code = std::max(-limit, std::min(limit, code));
    const std::uint32_t encoded = static_cast<std::uint32_t>(code + limit + 1);
    accumulator |= static_cast<std::uint64_t>(encoded) << available;
    available += bits;
    while (available >= 8) { result.bytes[output++] = static_cast<std::uint8_t>(accumulator); accumulator >>= 8; available -= 8; }
  }
  if (available) result.bytes[output] = static_cast<std::uint8_t>(accumulator);
  return result;
}

Packed encode_itq208(const float* vector) {
  Packed result{std::vector<std::uint8_t>(26, 0), 1.0f, 1};
  for (std::size_t i = 0; i < 208; ++i)
    if (vector[i] >= 0.0f) result.bytes[i / 8] |= static_cast<std::uint8_t>(1u << (i % 8));
  return result;
}

float score_itq208(const Packed& packed, const float* query) {
  float score = 0.0f;
  for (std::size_t i = 0; i < 208; ++i) {
    const bool positive = (packed.bytes[i / 8] >> (i % 8)) & 1u;
    score += (positive ? 1.0f : -1.0f) * query[i];
  }
  return score;
}

float score_scalar(const Packed& packed, const float* query) {
  const int limit = (1 << (packed.bits - 1)) - 1;
  const std::uint32_t offset = static_cast<std::uint32_t>(limit + 1);
  std::uint64_t accumulator = 0;
  int available = 0;
  std::size_t input = 0;
  float score = 0.0f;
  const std::uint32_t mask = (1u << packed.bits) - 1u;
  for (std::size_t i = 0; i < kDimensions; ++i) {
    while (available < packed.bits) { accumulator |= static_cast<std::uint64_t>(packed.bytes[input++]) << available; available += 8; }
    const int code = static_cast<int>(accumulator & mask) - static_cast<int>(offset);
    accumulator >>= packed.bits; available -= packed.bits;
    score += static_cast<float>(code) * packed.scale * query[i];
  }
  return score;
}

std::uint64_t checksum(float value) {
  return static_cast<std::uint64_t>(std::llround(std::abs(value) * 1000.0));
}

}  // namespace

int main(int argc, char** argv) {
  std::size_t records = 5000;
  std::size_t iterations = 100;
  bool json = false;
  for (int i = 1; i < argc; ++i) {
    const std::string arg(argv[i]);
    if (arg == "--records" && i + 1 < argc) records = std::stoul(argv[++i]);
    else if (arg == "--iterations" && i + 1 < argc) iterations = std::stoul(argv[++i]);
    else if (arg == "--json") json = true;
  }
  std::mt19937 rng(20260905);
  const auto vectors = random_vectors(records, rng);
  const auto query = random_vectors(1, rng);
  const std::vector<Method> methods = {{"fp32", 1536}, {"fp16", 768},
      {"int4", 196}, {"int8", 388}, {"int10", 484}, {"int12", 580},
      {"itq208_adc", 26}};
  std::vector<std::vector<Packed>> packed(6);
  for (std::size_t method = 0; method < 6; ++method) {
    const int bits = method == 2 ? 4 : method == 3 ? 8 : method == 4 ? 10 : 12;
    if (method >= 2) { packed[method].reserve(records); for (std::size_t i = 0; i < records; ++i) packed[method].push_back(encode_scalar(&vectors[i * kDimensions], bits)); }
  }
  packed.resize(7);
  packed[6].reserve(records);
  for (std::size_t i = 0; i < records; ++i) packed[6].push_back(encode_itq208(&vectors[i * kDimensions]));
  std::vector<float> query_scores(records);
  std::uint64_t final_checksum = 0;
  (void)json;
  std::cout << "{\"records\":" << records << ",\"iterations\":" << iterations << ",\"methods\":[";
  for (std::size_t method = 0; method < methods.size(); ++method) {
    std::vector<double> samples;
    samples.reserve(iterations);
    for (std::size_t iteration = 0; iteration < iterations; ++iteration) {
      const auto start = Clock::now();
      float sum = 0.0f;
      for (std::size_t i = 0; i < records; ++i) {
        if (method == 0) sum += dot(&vectors[i * kDimensions], query.data());
        else if (method == 1) { float value = 0.0f; for (std::size_t d = 0; d < kDimensions; ++d) value += fp16_value(fp16(vectors[i * kDimensions + d])) * query[d]; sum += value; }
        else if (method == 6) sum += score_itq208(packed[6][i], query.data());
        else sum += score_scalar(packed[method][i], query.data());
      }
      final_checksum += checksum(sum) + static_cast<std::uint64_t>(iteration + 1);
      samples.push_back(std::chrono::duration<double, std::milli>(Clock::now() - start).count());
    }
    std::sort(samples.begin(), samples.end());
    auto quantile = [&](double q) { return samples[std::min(samples.size() - 1, static_cast<std::size_t>(q * samples.size()))]; };
    if (method) std::cout << ",";
    std::cout << "{\"id\":\"" << methods[method].id << "\",\"bytes_per_record\":" << methods[method].bytes
              << ",\"p50_ms\":" << quantile(.50) << ",\"p95_ms\":" << quantile(.95)
              << ",\"p99_ms\":" << quantile(.99) << "}";
  }
  std::cout << "],\"checksum\":" << final_checksum << ",\"scope\":\"portable native microbenchmark; encoding is offline\"}\n";
  return 0;
}
