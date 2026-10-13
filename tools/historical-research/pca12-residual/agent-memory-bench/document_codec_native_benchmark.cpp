#include <algorithm>
#include <cmath>
#include <chrono>
#include <cstring>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <numeric>
#include <queue>
#include <random>
#include <string>
#include <vector>
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
#include <immintrin.h>
#endif

namespace {
constexpr std::size_t kDimensions = 384;
using Clock = std::chrono::steady_clock;

enum class Kernel {
  Fp32, Fp32Avx2, Fp32Fma, Fp16, Int4Scalar, Int4Fused, Int4IntegerAvx2, Int8Packed, Int8Integer,
  Int8IntegerAvx2, Int10Packed, Int10Int16, Int12Packed, Int12Int16,
  ItqHamming, ItqHammingUnrolled, ItqHammingAvx2, ItqAdc,
  Thq3, Thq3Unrolled, Thq3Avx2, Thq4, Thq4Unrolled, Thq4Avx2,
  Thq5, Thq5Unrolled, Thq5Avx2
};

struct Method { std::string id; std::size_t bytes; Kernel kernel; bool hamming = false; };

struct TopEntry {
  float score = 0.0f;
  std::size_t id = 0;
};

bool better(const TopEntry& lhs, const TopEntry& rhs) {
  return lhs.score > rhs.score ||
         (lhs.score == rhs.score && lhs.id < rhs.id);
}

bool worse(const TopEntry& lhs, const TopEntry& rhs) {
  return better(rhs, lhs);
}

std::vector<TopEntry> topk_scan(const std::vector<float>& scores, std::size_t k) {
  std::vector<TopEntry> result;
  result.reserve(std::min(k, scores.size()));
  for (std::size_t id = 0; id < scores.size(); ++id) {
    TopEntry candidate{scores[id], id};
    if (result.size() < k) result.push_back(candidate);
    else {
      auto minimum = std::min_element(result.begin(), result.end(),
                                      [](const TopEntry& a, const TopEntry& b) { return worse(a, b); });
      if (better(candidate, *minimum)) *minimum = candidate;
    }
  }
  std::sort(result.begin(), result.end(), better);
  return result;
}

struct WorseFirst {
  bool operator()(const TopEntry& lhs, const TopEntry& rhs) const { return better(lhs, rhs); }
};

std::vector<TopEntry> topk_heap(const std::vector<float>& scores, std::size_t k) {
  std::priority_queue<TopEntry, std::vector<TopEntry>, WorseFirst> heap;
  for (std::size_t id = 0; id < scores.size(); ++id) {
    TopEntry candidate{scores[id], id};
    if (heap.size() < k) heap.push(candidate);
    else if (better(candidate, heap.top())) { heap.pop(); heap.push(candidate); }
  }
  std::vector<TopEntry> result;
  while (!heap.empty()) { result.push_back(heap.top()); heap.pop(); }
  std::sort(result.begin(), result.end(), better);
  return result;
}

std::vector<TopEntry> topk_nth(const std::vector<float>& scores, std::size_t k) {
  std::vector<TopEntry> result;
  result.reserve(scores.size());
  for (std::size_t id = 0; id < scores.size(); ++id) result.push_back({scores[id], id});
  if (result.size() > k) {
    std::nth_element(result.begin(), result.begin() + k, result.end(), better);
    result.resize(k);
  }
  std::sort(result.begin(), result.end(), better);
  return result;
}

std::vector<TopEntry> topk_block(const std::vector<float>& scores, std::size_t k) {
  constexpr std::size_t kBlock = 1024;
  std::vector<TopEntry> merged;
  for (std::size_t start = 0; start < scores.size(); start += kBlock) {
    const std::size_t end = std::min(scores.size(), start + kBlock);
    std::vector<float> block(scores.begin() + start, scores.begin() + end);
    auto local = topk_nth(block, std::min(k, block.size()));
    for (TopEntry& entry : local) entry.id += start;
    merged.insert(merged.end(), local.begin(), local.end());
  }
  if (merged.size() > k) {
    std::nth_element(merged.begin(), merged.begin() + k, merged.end(), better);
    merged.resize(k);
  }
  std::sort(merged.begin(), merged.end(), better);
  return merged;
}

std::vector<TopEntry> topk_histogram(const std::vector<std::uint16_t>& distances,
                                     std::size_t k) {
  std::uint16_t maximum = 0;
  for (std::uint16_t distance : distances) maximum = std::max(maximum, distance);
  std::vector<std::size_t> histogram(static_cast<std::size_t>(maximum) + 1, 0);
  for (std::uint16_t distance : distances) ++histogram[distance];
  std::size_t remaining = k;
  std::uint16_t cutoff = maximum;
  for (std::size_t distance = 0; distance < histogram.size(); ++distance) {
    if (remaining <= histogram[distance]) { cutoff = static_cast<std::uint16_t>(distance); break; }
    remaining -= histogram[distance];
  }
  std::vector<TopEntry> result;
  for (std::size_t id = 0; id < distances.size(); ++id)
    if (distances[id] <= cutoff) result.push_back({-static_cast<float>(distances[id]), id});
  std::sort(result.begin(), result.end(), better);
  if (result.size() > k) result.resize(k);
  return result;
}

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

#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
float dot_avx2_fma(const float* a, const float* b) {
  __m256 sum0 = _mm256_setzero_ps();
  __m256 sum1 = _mm256_setzero_ps();
  __m256 sum2 = _mm256_setzero_ps();
  __m256 sum3 = _mm256_setzero_ps();
  for (std::size_t i = 0; i < kDimensions; i += 32) {
    sum0 = _mm256_fmadd_ps(_mm256_loadu_ps(a + i), _mm256_loadu_ps(b + i), sum0);
    sum1 = _mm256_fmadd_ps(_mm256_loadu_ps(a + i + 8), _mm256_loadu_ps(b + i + 8), sum1);
    sum2 = _mm256_fmadd_ps(_mm256_loadu_ps(a + i + 16), _mm256_loadu_ps(b + i + 16), sum2);
    sum3 = _mm256_fmadd_ps(_mm256_loadu_ps(a + i + 24), _mm256_loadu_ps(b + i + 24), sum3);
  }
  const __m256 sum = _mm256_add_ps(_mm256_add_ps(sum0, sum1), _mm256_add_ps(sum2, sum3));
  alignas(32) float lanes[8];
  _mm256_store_ps(lanes, sum);
  return std::accumulate(lanes, lanes + 8, 0.0f);
}
#endif

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

std::uint32_t hamming_wordwise(const std::uint8_t* lhs,
                                const std::uint8_t* rhs,
                                std::size_t size) {
  std::uint32_t distance = 0;
  std::size_t i = 0;
  for (; i + 8 <= size; i += 8) {
    std::uint64_t x = 0, y = 0;
    std::memcpy(&x, lhs + i, sizeof(x));
    std::memcpy(&y, rhs + i, sizeof(y));
    distance += popcount64(x ^ y);
  }
  for (; i < size; ++i) distance += popcount32(lhs[i] ^ rhs[i]);
  return distance;
}

std::uint32_t hamming_unrolled(const std::uint8_t* lhs,
                               const std::uint8_t* rhs,
                               std::size_t size) {
  std::uint32_t distance0 = 0;
  std::uint32_t distance1 = 0;
  std::uint32_t distance2 = 0;
  std::uint32_t distance3 = 0;
  std::size_t i = 0;
  for (; i + 32 <= size; i += 32) {
    std::uint64_t x0 = 0, x1 = 0, x2 = 0, x3 = 0;
    std::memcpy(&x0, lhs + i, sizeof(x0));
    std::memcpy(&x1, lhs + i + 8, sizeof(x1));
    std::memcpy(&x2, lhs + i + 16, sizeof(x2));
    std::memcpy(&x3, lhs + i + 24, sizeof(x3));
    std::uint64_t y0 = 0, y1 = 0, y2 = 0, y3 = 0;
    std::memcpy(&y0, rhs + i, sizeof(y0));
    std::memcpy(&y1, rhs + i + 8, sizeof(y1));
    std::memcpy(&y2, rhs + i + 16, sizeof(y2));
    std::memcpy(&y3, rhs + i + 24, sizeof(y3));
    distance0 += popcount64(x0 ^ y0);
    distance1 += popcount64(x1 ^ y1);
    distance2 += popcount64(x2 ^ y2);
    distance3 += popcount64(x3 ^ y3);
  }
  std::uint32_t distance = distance0 + distance1 + distance2 + distance3;
  for (; i + 8 <= size; i += 8) {
    std::uint64_t x = 0, y = 0;
    std::memcpy(&x, lhs + i, sizeof(x));
    std::memcpy(&y, rhs + i, sizeof(y));
    distance += popcount64(x ^ y);
  }
  for (; i < size; ++i) distance += popcount32(lhs[i] ^ rhs[i]);
  return distance;
}

#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
std::uint32_t hamming_avx2(const std::uint8_t* lhs,
                           const std::uint8_t* rhs,
                           std::size_t size) {
  const __m256i mask = _mm256_set1_epi8(0x0f);
  const __m256i lut = _mm256_setr_epi8(
      0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4,
      0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4);
  const __m256i zero = _mm256_setzero_si256();
  __m256i sums = zero;
  std::size_t i = 0;
  for (; i + 32 <= size; i += 32) {
    const __m256i value = _mm256_xor_si256(
        _mm256_loadu_si256(reinterpret_cast<const __m256i*>(lhs + i)),
        _mm256_loadu_si256(reinterpret_cast<const __m256i*>(rhs + i)));
    const __m256i low = _mm256_and_si256(value, mask);
    const __m256i high = _mm256_and_si256(_mm256_srli_epi16(value, 4), mask);
    const __m256i counts = _mm256_add_epi8(
        _mm256_shuffle_epi8(lut, low), _mm256_shuffle_epi8(lut, high));
    sums = _mm256_add_epi64(sums, _mm256_sad_epu8(counts, zero));
  }
  alignas(32) std::uint64_t lanes[4];
  _mm256_store_si256(reinterpret_cast<__m256i*>(lanes), sums);
  std::uint32_t distance = static_cast<std::uint32_t>(lanes[0] + lanes[1] + lanes[2] + lanes[3]);
  for (; i < size; ++i) distance += popcount32(lhs[i] ^ rhs[i]);
  return distance;
}
#endif

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

// Runtime payloads are row-major and contiguous.  The small Packed objects are
// retained only as an offline encoding convenience; scorers never chase one
// heap allocation per document.
struct PackedStore {
  std::vector<std::uint8_t> bytes;
  std::vector<float> scales;
  std::size_t stride = 0;
  int bits = 1;
};

PackedStore materialize_packed(const std::vector<Packed>& records) {
  PackedStore store;
  if (records.empty()) return store;
  store.stride = records.front().bytes.size();
  store.bits = records.front().bits;
  store.bytes.resize(records.size() * store.stride);
  store.scales.resize(records.size());
  for (std::size_t i = 0; i < records.size(); ++i) {
    if (records[i].bytes.size() != store.stride) std::abort();
    std::memcpy(store.bytes.data() + i * store.stride,
                records[i].bytes.data(), store.stride);
    store.scales[i] = records[i].scale;
  }
  return store;
}

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

float score_itq208(const std::uint8_t* bytes, const float* query) {
  float score = 0.0f;
  for (std::size_t i = 0; i < 208; ++i) {
    const bool positive = (bytes[i / 8] >> (i % 8)) & 1u;
    score += (positive ? 1.0f : -1.0f) * query[i];
  }
  return score;
}

float score_scalar(const std::uint8_t* bytes, float scale, int bits,
                   const float* query) {
  const int limit = (1 << (bits - 1)) - 1;
  const std::uint32_t offset = static_cast<std::uint32_t>(limit + 1);
  std::uint64_t accumulator = 0;
  int available = 0;
  std::size_t input = 0;
  float score = 0.0f;
  const std::uint32_t mask = (1u << bits) - 1u;
  for (std::size_t i = 0; i < kDimensions; ++i) {
    while (available < bits) { accumulator |= static_cast<std::uint64_t>(bytes[input++]) << available; available += 8; }
    const int code = static_cast<int>(accumulator & mask) - static_cast<int>(offset);
    accumulator >>= bits; available -= bits;
    score += static_cast<float>(code) * scale * query[i];
  }
  return score;
}

struct DirectInt8 { std::vector<std::int8_t> values; float scale; };

struct DirectInt8Store {
  std::vector<std::int8_t> values;
  std::vector<float> scales;
};

DirectInt8Store materialize_int8(const std::vector<DirectInt8>& records) {
  DirectInt8Store store;
  store.values.resize(records.size() * kDimensions);
  store.scales.resize(records.size());
  for (std::size_t i = 0; i < records.size(); ++i) {
    std::memcpy(store.values.data() + i * kDimensions, records[i].values.data(),
                kDimensions * sizeof(std::int8_t));
    store.scales[i] = records[i].scale;
  }
  return store;
}

DirectInt8 encode_int8_direct(const float* vector) {
  DirectInt8 result{std::vector<std::int8_t>(kDimensions), 1.0f};
  float maximum = 0.0f;
  for (std::size_t i = 0; i < kDimensions; ++i) maximum = std::max(maximum, std::abs(vector[i]));
  result.scale = maximum > 0.0f ? maximum / 127.0f : 1.0f;
  for (std::size_t i = 0; i < kDimensions; ++i)
    result.values[i] = static_cast<std::int8_t>(std::max(-127, std::min(127, static_cast<int>(std::lrint(vector[i] / result.scale)))));
  return result;
}

float score_int8_integer(const std::int8_t* values, float scale,
                         const std::int16_t* query_code, float query_scale) {
  // Query quantization is performed once per request, not once per document.
  std::int64_t sum = 0;
  for (std::size_t i = 0; i < kDimensions; ++i) {
    sum += static_cast<std::int16_t>(values[i]) * query_code[i];
  }
  return static_cast<float>(sum) * scale * query_scale;
}

#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
float score_int8_integer_avx2(const std::int8_t* values, float scale,
                              const std::int16_t* query_code,
                              float query_scale) {
  __m256i sum0 = _mm256_setzero_si256();
  __m256i sum1 = _mm256_setzero_si256();
  for (std::size_t i = 0; i < kDimensions; i += 32) {
    const __m128i doc0 = _mm_loadu_si128(reinterpret_cast<const __m128i*>(values + i));
    const __m128i doc1 = _mm_loadu_si128(reinterpret_cast<const __m128i*>(values + i + 16));
    const __m256i doc16_0 = _mm256_cvtepi8_epi16(doc0);
    const __m256i doc16_1 = _mm256_cvtepi8_epi16(doc1);
    const __m256i query0 = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(query_code + i));
    const __m256i query1 = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(query_code + i + 16));
    sum0 = _mm256_add_epi32(sum0, _mm256_madd_epi16(doc16_0, query0));
    sum1 = _mm256_add_epi32(sum1, _mm256_madd_epi16(doc16_1, query1));
  }
  sum0 = _mm256_add_epi32(sum0, sum1);
  alignas(32) std::int32_t lanes[8];
  _mm256_store_si256(reinterpret_cast<__m256i*>(lanes), sum0);
  std::int64_t sum = 0;
  for (std::int32_t lane : lanes) sum += lane;
  return static_cast<float>(sum) * scale * query_scale;
}
#endif

struct DirectInt16 { std::vector<std::int16_t> values; float scale; int bits; };

struct DirectInt16Store {
  std::vector<std::int16_t> values;
  std::vector<float> scales;
  int bits = 0;
};

DirectInt16Store materialize_int16(const std::vector<DirectInt16>& records) {
  DirectInt16Store store;
  if (records.empty()) return store;
  store.bits = records.front().bits;
  store.values.resize(records.size() * kDimensions);
  store.scales.resize(records.size());
  for (std::size_t i = 0; i < records.size(); ++i) {
    std::memcpy(store.values.data() + i * kDimensions, records[i].values.data(),
                kDimensions * sizeof(std::int16_t));
    store.scales[i] = records[i].scale;
  }
  return store;
}

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

float score_int16_direct(const std::int16_t* values, float scale,
                         const float* query) {
  float score = 0.0f;
  for (std::size_t i = 0; i < kDimensions; ++i)
    score += static_cast<float>(values[i]) * scale * query[i];
  return score;
}

float score_int4_fused(const std::uint8_t* bytes, float scale,
                       const float* query) {
  float score = 0.0f;
  for (std::size_t i = 0; i < kDimensions; i += 2) {
    const std::uint8_t byte = bytes[i / 2];
    score += static_cast<float>(static_cast<int>(byte & 15u) - 8) * scale * query[i];
    score += static_cast<float>(static_cast<int>(byte >> 4) - 8) * scale * query[i + 1];
  }
  return score;
}

float score_int4_integer_scalar(const std::uint8_t* bytes, float scale,
                               const std::int16_t* query_even,
                               const std::int16_t* query_odd,
                               float query_scale) {
  std::int64_t total = 0;
  for (std::size_t byte = 0; byte < kDimensions / 2; ++byte) {
    total += (static_cast<int>(bytes[byte] & 0x0fu) - 8) * query_even[byte];
    total += (static_cast<int>(bytes[byte] >> 4) - 8) * query_odd[byte];
  }
  return static_cast<float>(total) * scale * query_scale;
}

#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
float score_int4_integer_avx2(const std::uint8_t* bytes, float scale,
                              const std::int16_t* query_even,
                              const std::int16_t* query_odd,
                              float query_scale) {
  const __m256i offset = _mm256_set1_epi16(8);
  __m256i sum = _mm256_setzero_si256();
  for (std::size_t byte = 0; byte < kDimensions / 2; byte += 16) {
    const __m128i packed = _mm_loadu_si128(reinterpret_cast<const __m128i*>(bytes + byte));
    const __m128i low8 = _mm_and_si128(packed, _mm_set1_epi8(0x0f));
    const __m128i high8 = _mm_and_si128(_mm_srli_epi16(packed, 4), _mm_set1_epi8(0x0f));
    const __m256i low16 = _mm256_sub_epi16(_mm256_cvtepu8_epi16(low8), offset);
    const __m256i high16 = _mm256_sub_epi16(_mm256_cvtepu8_epi16(high8), offset);
    const __m256i q_even = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(query_even + byte));
    const __m256i q_odd = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(query_odd + byte));
    sum = _mm256_add_epi32(sum, _mm256_add_epi32(_mm256_madd_epi16(low16, q_even), _mm256_madd_epi16(high16, q_odd)));
  }
  alignas(32) std::int32_t lanes[8];
  _mm256_store_si256(reinterpret_cast<__m256i*>(lanes), sum);
  std::int64_t total = 0;
  for (std::int32_t lane : lanes) total += lane;
  return static_cast<float>(total) * scale * query_scale;
}
#endif

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

float score_hamming(const std::uint8_t* bytes, const std::uint8_t* query,
                    std::size_t size) {
  return -static_cast<float>(hamming_wordwise(bytes, query, size));
}

float score_hamming_unrolled(const std::uint8_t* bytes, const std::uint8_t* query,
                             std::size_t size) {
  return -static_cast<float>(hamming_unrolled(bytes, query, size));
}

#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
float score_hamming_avx2(const std::uint8_t* bytes, const std::uint8_t* query,
                         std::size_t size) {
  return -static_cast<float>(hamming_avx2(bytes, query, size));
}
#endif

std::uint64_t checksum(float value) {
  return static_cast<std::uint64_t>(std::llround(std::abs(value) * 1000.0));
}

}  // namespace

int main(int argc, char** argv) {
  std::size_t records = 5000;
  std::size_t iterations = 100;
  std::size_t top_k = 256;
  bool json = false;
  for (int i = 1; i < argc; ++i) {
    const std::string arg(argv[i]);
    if (arg == "--records" && i + 1 < argc) records = std::stoul(argv[++i]);
    else if (arg == "--iterations" && i + 1 < argc) iterations = std::stoul(argv[++i]);
    else if (arg == "--top-k" && i + 1 < argc) top_k = std::stoul(argv[++i]);
    else if (arg == "--json") json = true;
  }
  std::mt19937 rng(20260905);
  const auto vectors = random_vectors(records, rng);
  const auto query = random_vectors(1, rng);
  if (top_k == 0 || top_k > records) return 2;
  const std::vector<Method> methods = {
      {"fp32", 1536, Kernel::Fp32}, {"fp32_avx2", 1536, Kernel::Fp32Avx2},
      {"fp32_fma", 1536, Kernel::Fp32Fma}, {"fp16", 768, Kernel::Fp16},
      {"int4_scalar", 196, Kernel::Int4Scalar}, {"int4_fused", 196, Kernel::Int4Fused},
      {"int4_integer_avx2", 196, Kernel::Int4IntegerAvx2},
      {"int8_scalar", 388, Kernel::Int8Packed}, {"int8_integer", 388, Kernel::Int8Integer},
      {"int8_integer_avx2", 388, Kernel::Int8IntegerAvx2},
      {"int10_packed", 484, Kernel::Int10Packed}, {"int10_int16_control", 772, Kernel::Int10Int16},
      {"int12_packed", 580, Kernel::Int12Packed}, {"int12_int16_control", 772, Kernel::Int12Int16},
      {"itq208_hamming", 26, Kernel::ItqHamming, true},
      {"itq208_hamming_unrolled", 26, Kernel::ItqHammingUnrolled, true},
      {"itq208_hamming_avx2", 26, Kernel::ItqHammingAvx2, true},
      {"itq208_adc", 26, Kernel::ItqAdc},
      {"thq3_quantile", 96, Kernel::Thq3, true}, {"thq3_quantile_unrolled", 96, Kernel::Thq3Unrolled, true}, {"thq3_quantile_avx2", 96, Kernel::Thq3Avx2, true},
      {"thq4_quantile", 144, Kernel::Thq4, true}, {"thq4_quantile_unrolled", 144, Kernel::Thq4Unrolled, true}, {"thq4_quantile_avx2", 144, Kernel::Thq4Avx2, true},
      {"thq5_quantile", 192, Kernel::Thq5, true}, {"thq5_quantile_unrolled", 192, Kernel::Thq5Unrolled, true}, {"thq5_quantile_avx2", 192, Kernel::Thq5Avx2, true}};
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
  const PackedStore packed4 = materialize_packed(packed[3]);
  const PackedStore packed8 = materialize_packed(packed[5]);
  const PackedStore packed10 = materialize_packed(packed[7]);
  const PackedStore packed12 = materialize_packed(packed[9]);
  const PackedStore itq_hamming = materialize_packed(packed[11]);
  const PackedStore itq_adc = materialize_packed(packed[12]);
  const PackedStore thq3 = materialize_packed(packed[13]);
  const PackedStore thq4 = materialize_packed(packed[14]);
  const PackedStore thq5 = materialize_packed(packed[15]);
  const DirectInt8Store int8_store = materialize_int8(direct_int8);
  const DirectInt16Store int10_store = materialize_int16(direct_int10);
  const DirectInt16Store int12_store = materialize_int16(direct_int12);
  const Packed query_itq = encode_itq208(query.data());
  float query_max = 0.0f;
  for (float value : query) query_max = std::max(query_max, std::abs(value));
  const float query_scale = query_max > 0.0f ? query_max / 127.0f : 1.0f;
  std::vector<std::int16_t> query_int8(kDimensions);
  for (std::size_t i = 0; i < kDimensions; ++i)
    query_int8[i] = static_cast<std::int16_t>(std::lrint(query[i] / query_scale));
  float query_int4_max = 0.0f;
  for (float value : query) query_int4_max = std::max(query_int4_max, std::abs(value));
  const float query_int4_scale = query_int4_max > 0.0f ? query_int4_max / 7.0f : 1.0f;
  std::vector<std::int16_t> query_int4_even(kDimensions / 2), query_int4_odd(kDimensions / 2);
  for (std::size_t i = 0; i < kDimensions / 2; ++i) {
    query_int4_even[i] = static_cast<std::int16_t>(std::max(-7, std::min(7, static_cast<int>(std::lrint(query[2 * i] / query_int4_scale)))));
    query_int4_odd[i] = static_cast<std::int16_t>(std::max(-7, std::min(7, static_cast<int>(std::lrint(query[2 * i + 1] / query_int4_scale)))));
  }
  std::vector<Packed> thq_query;
  thq_query.push_back(encode_thq(query.data(), 3));
  thq_query.push_back(encode_thq(query.data(), 4));
  thq_query.push_back(encode_thq(query.data(), 5));
  std::uint64_t final_checksum = 0;
  auto score_one = [&](const Method& method, std::size_t i) {
    switch (method.kernel) {
      case Kernel::Fp32: return dot(&vectors[i * kDimensions], query.data());
      case Kernel::Fp32Avx2: return dot_avx2(&vectors[i * kDimensions], query.data());
      case Kernel::Fp32Fma:
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
        return dot_avx2_fma(&vectors[i * kDimensions], query.data());
#else
        return dot(&vectors[i * kDimensions], query.data());
#endif
      case Kernel::Fp16: { float value = 0.0f; for (std::size_t d = 0; d < kDimensions; ++d) value += fp16_value(fp16(vectors[i * kDimensions + d])) * query[d]; return value; }
      case Kernel::Int4Scalar: return score_scalar(packed4.bytes.data() + i * packed4.stride, packed4.scales[i], packed4.bits, query.data());
      case Kernel::Int4Fused: return score_int4_fused(packed4.bytes.data() + i * packed4.stride, packed4.scales[i], query.data());
      case Kernel::Int4IntegerAvx2:
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
        return score_int4_integer_avx2(packed4.bytes.data() + i * packed4.stride, packed4.scales[i], query_int4_even.data(), query_int4_odd.data(), query_int4_scale);
#else
        return score_int4_fused(packed4.bytes.data() + i * packed4.stride, packed4.scales[i], query.data());
#endif
      case Kernel::Int8Packed: return score_scalar(packed8.bytes.data() + i * packed8.stride, packed8.scales[i], packed8.bits, query.data());
      case Kernel::Int8Integer: return score_int8_integer(int8_store.values.data() + i * kDimensions, int8_store.scales[i], query_int8.data(), query_scale);
      case Kernel::Int8IntegerAvx2:
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
        return score_int8_integer_avx2(int8_store.values.data() + i * kDimensions, int8_store.scales[i], query_int8.data(), query_scale);
#else
        return score_int8_integer(int8_store.values.data() + i * kDimensions, int8_store.scales[i], query_int8.data(), query_scale);
#endif
      case Kernel::Int10Packed: return score_scalar(packed10.bytes.data() + i * packed10.stride, packed10.scales[i], packed10.bits, query.data());
      case Kernel::Int10Int16: return score_int16_direct(int10_store.values.data() + i * kDimensions, int10_store.scales[i], query.data());
      case Kernel::Int12Packed: return score_scalar(packed12.bytes.data() + i * packed12.stride, packed12.scales[i], packed12.bits, query.data());
      case Kernel::Int12Int16: return score_int16_direct(int12_store.values.data() + i * kDimensions, int12_store.scales[i], query.data());
      case Kernel::ItqHamming: return score_hamming(itq_hamming.bytes.data() + i * itq_hamming.stride, query_itq.bytes.data(), itq_hamming.stride);
      case Kernel::ItqHammingUnrolled: return score_hamming_unrolled(itq_hamming.bytes.data() + i * itq_hamming.stride, query_itq.bytes.data(), itq_hamming.stride);
      case Kernel::ItqHammingAvx2:
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
        return score_hamming_avx2(itq_hamming.bytes.data() + i * itq_hamming.stride, query_itq.bytes.data(), itq_hamming.stride);
#else
        return score_hamming(itq_hamming.bytes.data() + i * itq_hamming.stride, query_itq.bytes.data(), itq_hamming.stride);
#endif
      case Kernel::ItqAdc: return score_itq208(itq_adc.bytes.data() + i * itq_adc.stride, query.data());
      case Kernel::Thq3: return score_hamming(thq3.bytes.data() + i * thq3.stride, thq_query[0].bytes.data(), thq3.stride);
      case Kernel::Thq3Unrolled: return score_hamming_unrolled(thq3.bytes.data() + i * thq3.stride, thq_query[0].bytes.data(), thq3.stride);
      case Kernel::Thq3Avx2:
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
        return score_hamming_avx2(thq3.bytes.data() + i * thq3.stride, thq_query[0].bytes.data(), thq3.stride);
#else
        return score_hamming(thq3.bytes.data() + i * thq3.stride, thq_query[0].bytes.data(), thq3.stride);
#endif
      case Kernel::Thq4: return score_hamming(thq4.bytes.data() + i * thq4.stride, thq_query[1].bytes.data(), thq4.stride);
      case Kernel::Thq4Unrolled: return score_hamming_unrolled(thq4.bytes.data() + i * thq4.stride, thq_query[1].bytes.data(), thq4.stride);
      case Kernel::Thq4Avx2:
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
        return score_hamming_avx2(thq4.bytes.data() + i * thq4.stride, thq_query[1].bytes.data(), thq4.stride);
#else
        return score_hamming(thq4.bytes.data() + i * thq4.stride, thq_query[1].bytes.data(), thq4.stride);
#endif
      case Kernel::Thq5: return score_hamming(thq5.bytes.data() + i * thq5.stride, thq_query[2].bytes.data(), thq5.stride);
      case Kernel::Thq5Unrolled: return score_hamming_unrolled(thq5.bytes.data() + i * thq5.stride, thq_query[2].bytes.data(), thq5.stride);
      case Kernel::Thq5Avx2:
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
        return score_hamming_avx2(thq5.bytes.data() + i * thq5.stride, thq_query[2].bytes.data(), thq5.stride);
#else
        return score_hamming(thq5.bytes.data() + i * thq5.stride, thq_query[2].bytes.data(), thq5.stride);
#endif
    }
    return 0.0f;
  };
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
  for (std::size_t i = 0; i < std::min<std::size_t>(records, 16); ++i) {
    const float int4_reference = score_int4_integer_scalar(
        packed4.bytes.data() + i * packed4.stride, packed4.scales[i],
        query_int4_even.data(), query_int4_odd.data(), query_int4_scale);
    const float int4_vectorized = score_int4_integer_avx2(
        packed4.bytes.data() + i * packed4.stride, packed4.scales[i],
        query_int4_even.data(), query_int4_odd.data(), query_int4_scale);
    if (std::abs(int4_reference - int4_vectorized) > 1.0e-3f) std::abort();
    for (const PackedStore* store : {&itq_hamming, &thq3, &thq4, &thq5}) {
      const std::size_t query_stride = store == &itq_hamming ? itq_hamming.stride : store->stride;
      const std::uint8_t* query_bytes = store == &itq_hamming ? query_itq.bytes.data() :
          (store == &thq3 ? thq_query[0].bytes.data() : (store == &thq4 ? thq_query[1].bytes.data() : thq_query[2].bytes.data()));
      const std::uint8_t* document_bytes = store->bytes.data() + i * store->stride;
      if (hamming_wordwise(document_bytes, query_bytes, query_stride) != hamming_avx2(document_bytes, query_bytes, query_stride)) std::abort();
    }
  }
#endif
  (void)json;
  std::cout << "{\"records\":" << records << ",\"iterations\":" << iterations
            << ",\"top_k\":" << top_k << ",\"layout\":\"contiguous_row_major\",\"topk_contract\":\"score_desc_id_asc\",\"backend\":\""
#if AGENT_MEMORY_DOCUMENT_CODEC_HAS_AVX2
            << "avx2"
#else
            << "portable_scalar"
#endif
            << "\",\"methods\":[";
  for (std::size_t method = 0; method < methods.size(); ++method) {
    std::vector<double> samples;
    std::vector<double> materialize_samples, topk_samples, heap_samples, nth_samples, block_samples, histogram_samples;
    samples.reserve(iterations);
    materialize_samples.reserve(iterations); topk_samples.reserve(iterations);
    heap_samples.reserve(iterations);
    nth_samples.reserve(iterations); block_samples.reserve(iterations);
    histogram_samples.reserve(iterations);
    std::vector<TopEntry> reference;
    for (std::size_t iteration = 0; iteration < iterations; ++iteration) {
      const auto start = Clock::now();
      float sum = 0.0f;
      for (std::size_t i = 0; i < records; ++i) sum += score_one(methods[method], i);
      final_checksum += checksum(sum) + static_cast<std::uint64_t>(iteration + 1);
      samples.push_back(std::chrono::duration<double, std::milli>(Clock::now() - start).count());
      const auto materialize_start = Clock::now();
      std::vector<float> materialized_scores(records);
      float materialized_sum = 0.0f;
      for (std::size_t i = 0; i < records; ++i) {
        materialized_scores[i] = score_one(methods[method], i);
        materialized_sum += materialized_scores[i];
      }
      final_checksum += checksum(materialized_sum);
      materialize_samples.push_back(std::chrono::duration<double, std::milli>(Clock::now() - materialize_start).count());
      auto measure_selector = [&](auto selector, std::vector<double>& output) {
        const auto selector_start = Clock::now();
        std::vector<float> scores(records);
        for (std::size_t i = 0; i < records; ++i) scores[i] = score_one(methods[method], i);
        const auto selected = selector(scores);
        float top_sum = 0.0f;
        for (const TopEntry& entry : selected) top_sum += entry.score;
        final_checksum += checksum(top_sum);
        output.push_back(std::chrono::duration<double, std::milli>(Clock::now() - selector_start).count());
        if (reference.empty()) reference = selected;
        else if (selected.size() != reference.size() || !std::equal(selected.begin(), selected.end(), reference.begin(), [](const TopEntry& a, const TopEntry& b) { return a.id == b.id; })) std::abort();
      };
      measure_selector([&](const std::vector<float>& scores) { return topk_scan(scores, top_k); }, topk_samples);
      measure_selector([&](const std::vector<float>& scores) { return topk_heap(scores, top_k); }, heap_samples);
      measure_selector([&](const std::vector<float>& scores) { return topk_nth(scores, top_k); }, nth_samples);
      measure_selector([&](const std::vector<float>& scores) { return topk_block(scores, top_k); }, block_samples);
      if (methods[method].hamming) {
        measure_selector([&](const std::vector<float>& scores) {
          std::vector<std::uint16_t> distances(scores.size());
          for (std::size_t i = 0; i < scores.size(); ++i) distances[i] = static_cast<std::uint16_t>(-scores[i]);
          return topk_histogram(distances, top_k);
        }, histogram_samples);
      }
    }
    std::sort(samples.begin(), samples.end());
    auto quantile = [&](double q) { return samples[std::min(samples.size() - 1, static_cast<std::size_t>(q * samples.size()))]; };
    if (method) std::cout << ",";
    auto top_quantile = [&](std::vector<double>& values, double q) { std::sort(values.begin(), values.end()); return values[std::min(values.size() - 1, static_cast<std::size_t>(q * values.size()))]; };
    std::cout << "{\"id\":\"" << methods[method].id << "\",\"bytes_per_record\":" << methods[method].bytes
              << ",\"p50_score_ms\":" << quantile(.50) << ",\"p95_score_ms\":" << quantile(.95)
              << ",\"p99_score_ms\":" << quantile(.99)
              << ",\"p95_score_materialize_ms\":" << top_quantile(materialize_samples, .95)
              << ",\"p50_score_topk_ms\":" << top_quantile(topk_samples, .50)
              << ",\"p95_score_topk_ms\":" << top_quantile(topk_samples, .95)
              << ",\"p99_score_topk_ms\":" << top_quantile(topk_samples, .99)
              << ",\"p95_topk_heap_ms\":" << top_quantile(heap_samples, .95)
              << ",\"p95_topk_nth_element_ms\":" << top_quantile(nth_samples, .95)
              << ",\"p95_topk_block_ms\":" << top_quantile(block_samples, .95);
    if (methods[method].hamming) std::cout << ",\"p95_topk_histogram_ms\":" << top_quantile(histogram_samples, .95);
    std::cout << "}";
  }
  std::cout << "],\"checksum\":" << final_checksum << ",\"scope\":\"native codec-kernel ceiling study; encoding is offline; compact payloads are contiguous at runtime\"}\n";
  return 0;
}
