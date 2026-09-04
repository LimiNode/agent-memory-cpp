#include <algorithm>
#include <array>
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
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
#include <immintrin.h>
#endif

namespace {
constexpr std::size_t kDimensions = 384;
using Clock = std::chrono::steady_clock;

struct Method { std::string id; std::size_t bytes; };

float dot(const float* a, const float* b) {
  float value = 0.0f;
  for (std::size_t i = 0; i < kDimensions; ++i) value += a[i] * b[i];
  return value;
}

float dot_avx2(const float* a, const float* b) {
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
  __m256 sum0 = _mm256_setzero_ps();
  __m256 sum1 = _mm256_setzero_ps();
  __m256 sum2 = _mm256_setzero_ps();
  __m256 sum3 = _mm256_setzero_ps();
  for (std::size_t i = 0; i < kDimensions; i += 32) {
    sum0 = _mm256_add_ps(sum0, _mm256_mul_ps(_mm256_loadu_ps(a + i), _mm256_loadu_ps(b + i)));
    sum1 = _mm256_add_ps(sum1, _mm256_mul_ps(_mm256_loadu_ps(a + i + 8), _mm256_loadu_ps(b + i + 8)));
    sum2 = _mm256_add_ps(sum2, _mm256_mul_ps(_mm256_loadu_ps(a + i + 16), _mm256_loadu_ps(b + i + 16)));
    sum3 = _mm256_add_ps(sum3, _mm256_mul_ps(_mm256_loadu_ps(a + i + 24), _mm256_loadu_ps(b + i + 24)));
  }
  __m256 sum = _mm256_add_ps(_mm256_add_ps(sum0, sum1), _mm256_add_ps(sum2, sum3));
  alignas(32) float lanes[8];
  _mm256_store_ps(lanes, sum);
  return std::accumulate(lanes, lanes + 8, 0.0f);
#else
  return dot(a, b);
#endif
}

std::uint32_t popcount32(std::uint32_t value) {
#if defined(_MSC_VER)
  return static_cast<std::uint32_t>(__popcnt(value));
#else
  return static_cast<std::uint32_t>(__builtin_popcount(value));
#endif
}

std::uint32_t popcount64(std::uint64_t value) {
#if defined(_MSC_VER)
  return static_cast<std::uint32_t>(__popcnt64(value));
#else
  return static_cast<std::uint32_t>(__builtin_popcountll(value));
#endif
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

struct DirectInt8 { std::vector<std::int8_t> values; float scale; };

DirectInt8 encode_int8_direct(const float* vector) {
  DirectInt8 result{std::vector<std::int8_t>(kDimensions), 1.0f};
  float maximum = 0.0f;
  for (std::size_t i = 0; i < kDimensions; ++i) maximum = std::max(maximum, std::abs(vector[i]));
  result.scale = maximum > 0.0f ? maximum / 127.0f : 1.0f;
  for (std::size_t i = 0; i < kDimensions; ++i)
    result.values[i] = static_cast<std::int8_t>(std::max(-127, std::min(127, static_cast<int>(std::lrint(vector[i] / result.scale)))));
  return result;
}

float score_int8_integer(const DirectInt8& code, const std::int16_t* query_code,
                         float query_scale) {
  // Query quantization is performed once per request, not once per document.
  std::int64_t sum = 0;
  for (std::size_t i = 0; i < kDimensions; ++i) {
    sum += static_cast<std::int16_t>(code.values[i]) * query_code[i];
  }
  return static_cast<float>(sum) * code.scale * query_scale;
}

struct DirectInt16 { std::vector<std::int16_t> values; float scale; int bits; };

DirectInt16 encode_int16_direct(const float* vector, int bits) {
  DirectInt16 result{std::vector<std::int16_t>(kDimensions), 1.0f, bits};
  float maximum = 0.0f;
  for (std::size_t i = 0; i < kDimensions; ++i) maximum = std::max(maximum, std::abs(vector[i]));
  const int limit = (1 << (bits - 1)) - 1;
  result.scale = maximum > 0.0f ? maximum / static_cast<float>(limit) : 1.0f;
  for (std::size_t i = 0; i < kDimensions; ++i)
    result.values[i] = static_cast<std::int16_t>(std::max(-limit, std::min(limit, static_cast<int>(std::lrint(vector[i] / result.scale)))));
  return result;
}

float score_int16_direct(const DirectInt16& code, const float* query) {
  float score = 0.0f;
  for (std::size_t i = 0; i < kDimensions; ++i)
    score += static_cast<float>(code.values[i]) * code.scale * query[i];
  return score;
}

float score_int4_fused(const Packed& packed, const float* query) {
  float score = 0.0f;
  for (std::size_t i = 0; i < kDimensions; i += 2) {
    const std::uint8_t byte = packed.bytes[i / 2];
    score += static_cast<float>(static_cast<int>(byte & 15u) - 8) * packed.scale * query[i];
    score += static_cast<float>(static_cast<int>(byte >> 4) - 8) * packed.scale * query[i + 1];
  }
  return score;
}

Packed encode_thq(const float* vector, int levels) {
  const int bits = levels - 1;
  Packed result{std::vector<std::uint8_t>((kDimensions * bits + 7) / 8, 0), 1.0f, bits};
  // Normal-quantile thresholds approximate the quantile THQ setup used by the Python study.
  const float thresholds[7] = {-0.8416f, -0.5244f, -0.2533f, 0.0f, 0.2533f, 0.5244f, 0.8416f};
  std::size_t bit = 0;
  for (std::size_t coordinate = 0; coordinate < kDimensions; ++coordinate)
    for (int level = 0; level < bits; ++level, ++bit)
      if (vector[coordinate] > thresholds[(level * 7) / std::max(1, bits)]) result.bytes[bit / 8] |= static_cast<std::uint8_t>(1u << (bit % 8));
  return result;
}

float score_hamming(const Packed& code, const Packed& query) {
  std::uint32_t distance = 0;
  std::size_t i = 0;
  for (; i + 8 <= code.bytes.size(); i += 8) {
    std::uint64_t lhs = 0, rhs = 0;
    std::memcpy(&lhs, code.bytes.data() + i, sizeof(lhs));
    std::memcpy(&rhs, query.bytes.data() + i, sizeof(rhs));
    distance += popcount64(lhs ^ rhs);
  }
  for (; i < code.bytes.size(); ++i) distance += popcount32(code.bytes[i] ^ query.bytes[i]);
  return -static_cast<float>(distance);
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
  const std::vector<Method> methods = {
      {"fp32", 1536}, {"fp32_avx2", 1536}, {"fp16", 768},
      {"int4_scalar", 196}, {"int4_fused", 196},
      {"int8_scalar", 388}, {"int8_integer", 388},
      {"int10_packed", 484}, {"int10_int16_control", 772},
      {"int12_packed", 580}, {"int12_int16_control", 772},
      {"itq208_hamming", 26}, {"itq208_adc", 26},
      {"thq3_quantile", 96}, {"thq4_quantile", 144},
      {"thq5_quantile", 192}};
  std::vector<std::vector<Packed>> packed(16);
  for (std::size_t i = 0; i < records; ++i) {
    packed[3].push_back(encode_scalar(&vectors[i * kDimensions], 4));
    packed[5].push_back(encode_scalar(&vectors[i * kDimensions], 8));
    packed[7].push_back(encode_scalar(&vectors[i * kDimensions], 10));
    packed[9].push_back(encode_scalar(&vectors[i * kDimensions], 12));
    packed[11].push_back(encode_itq208(&vectors[i * kDimensions]));
    packed[12].push_back(encode_itq208(&vectors[i * kDimensions]));
    packed[13].push_back(encode_thq(&vectors[i * kDimensions], 3));
    packed[14].push_back(encode_thq(&vectors[i * kDimensions], 4));
    packed[15].push_back(encode_thq(&vectors[i * kDimensions], 5));
  }
  std::vector<DirectInt8> direct_int8;
  direct_int8.reserve(records);
  for (std::size_t i = 0; i < records; ++i) direct_int8.push_back(encode_int8_direct(&vectors[i * kDimensions]));
  std::vector<DirectInt16> direct_int10, direct_int12;
  direct_int10.reserve(records); direct_int12.reserve(records);
  for (std::size_t i = 0; i < records; ++i) {
    direct_int10.push_back(encode_int16_direct(&vectors[i * kDimensions], 10));
    direct_int12.push_back(encode_int16_direct(&vectors[i * kDimensions], 12));
  }
  const Packed query_itq = encode_itq208(query.data());
  float query_max = 0.0f;
  for (float value : query) query_max = std::max(query_max, std::abs(value));
  const float query_scale = query_max > 0.0f ? query_max / 127.0f : 1.0f;
  std::vector<std::int16_t> query_int8(kDimensions);
  for (std::size_t i = 0; i < kDimensions; ++i)
    query_int8[i] = static_cast<std::int16_t>(std::lrint(query[i] / query_scale));
  std::vector<Packed> thq_query;
  thq_query.push_back(encode_thq(query.data(), 3));
  thq_query.push_back(encode_thq(query.data(), 4));
  thq_query.push_back(encode_thq(query.data(), 5));
  std::vector<float> query_scores(records);
  std::uint64_t final_checksum = 0;
  auto score_one = [&](std::size_t method, std::size_t i) {
    if (method == 0) return dot(&vectors[i * kDimensions], query.data());
    if (method == 1) return dot_avx2(&vectors[i * kDimensions], query.data());
    if (method == 2) { float value = 0.0f; for (std::size_t d = 0; d < kDimensions; ++d) value += fp16_value(fp16(vectors[i * kDimensions + d])) * query[d]; return value; }
    if (method == 3) return score_scalar(packed[3][i], query.data());
    if (method == 4) return score_int4_fused(packed[3][i], query.data());
    if (method == 5) return score_scalar(packed[5][i], query.data());
    if (method == 6) return score_int8_integer(direct_int8[i], query_int8.data(), query_scale);
    if (method == 7) return score_scalar(packed[7][i], query.data());
    if (method == 8) return score_int16_direct(direct_int10[i], query.data());
    if (method == 9) return score_scalar(packed[9][i], query.data());
    if (method == 10) return score_int16_direct(direct_int12[i], query.data());
    if (method == 11) return score_hamming(packed[11][i], query_itq);
    if (method == 12) return score_itq208(packed[12][i], query.data());
    if (method == 13) return score_hamming(packed[13][i], thq_query[0]);
    if (method == 14) return score_hamming(packed[14][i], thq_query[1]);
    return score_hamming(packed[15][i], thq_query[2]);
  };
  (void)json;
  std::cout << "{\"records\":" << records << ",\"iterations\":" << iterations
            << ",\"top_k\":256,\"backend\":\""
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
            << "avx2"
#else
            << "portable_scalar"
#endif
            << "\",\"methods\":[";
  for (std::size_t method = 0; method < methods.size(); ++method) {
    std::vector<double> samples;
    std::vector<double> topk_samples;
    samples.reserve(iterations);
    topk_samples.reserve(iterations);
    for (std::size_t iteration = 0; iteration < iterations; ++iteration) {
      const auto start = Clock::now();
      float sum = 0.0f;
      for (std::size_t i = 0; i < records; ++i) sum += score_one(method, i);
      final_checksum += checksum(sum) + static_cast<std::uint64_t>(iteration + 1);
      samples.push_back(std::chrono::duration<double, std::milli>(Clock::now() - start).count());
      const auto top_start = Clock::now();
      std::array<float, 256> top{};
      std::size_t top_count = 0;
      for (std::size_t i = 0; i < records; ++i) {
        const float value = score_one(method, i);
        if (top_count < top.size()) top[top_count++] = value;
        else {
          const auto minimum = std::min_element(top.begin(), top.end());
          if (value > *minimum) *minimum = value;
        }
      }
      float top_sum = 0.0f;
      for (std::size_t i = 0; i < top_count; ++i) top_sum += top[i];
      final_checksum += checksum(top_sum);
      topk_samples.push_back(std::chrono::duration<double, std::milli>(Clock::now() - top_start).count());
    }
    std::sort(samples.begin(), samples.end());
    auto quantile = [&](double q) { return samples[std::min(samples.size() - 1, static_cast<std::size_t>(q * samples.size()))]; };
    if (method) std::cout << ",";
    auto top_quantile = [&](double q) { std::sort(topk_samples.begin(), topk_samples.end()); return topk_samples[std::min(topk_samples.size() - 1, static_cast<std::size_t>(q * topk_samples.size()))]; };
    std::cout << "{\"id\":\"" << methods[method].id << "\",\"bytes_per_record\":" << methods[method].bytes
              << ",\"p50_score_ms\":" << quantile(.50) << ",\"p95_score_ms\":" << quantile(.95)
              << ",\"p99_score_ms\":" << quantile(.99)
              << ",\"p50_score_topk_ms\":" << top_quantile(.50)
              << ",\"p95_score_topk_ms\":" << top_quantile(.95)
              << ",\"p99_score_topk_ms\":" << top_quantile(.99) << "}";
  }
  std::cout << "],\"checksum\":" << final_checksum << ",\"scope\":\"portable native microbenchmark; encoding is offline\"}\n";
  return 0;
}
