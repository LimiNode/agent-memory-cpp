#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

#if AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2
#include <immintrin.h>
#endif

namespace {
constexpr std::size_t kDimensions = 384;
constexpr std::size_t kOrdinalBytes = 96;
constexpr std::size_t kDocuments = 1'000'000;
using Clock = std::chrono::steady_clock;

struct QueryTables {
  std::array<float, kDimensions * 4> coordinate{};
  std::array<float, 192 * 16> pair{};
  std::array<std::uint8_t, 192 * 16> pair_u8{};
  std::array<float, 192> pair_scales{};
  std::array<std::uint8_t, kDimensions> levels{};
};

template <typename T>
std::vector<T> read_file(const std::string& path) {
  std::ifstream input(path, std::ios::binary | std::ios::ate);
  if (!input) throw std::runtime_error("cannot open " + path);
  const auto end = input.tellg();
  if (end < 0 || static_cast<std::size_t>(end) % sizeof(T) != 0)
    throw std::runtime_error("unaligned file " + path);
  std::vector<T> result(static_cast<std::size_t>(end) / sizeof(T));
  input.seekg(0);
  input.read(reinterpret_cast<char*>(result.data()), end);
  if (!input) throw std::runtime_error("cannot read " + path);
  return result;
}

std::uint8_t level_at(const std::uint8_t* code, std::size_t coordinate) {
  const auto shift = static_cast<unsigned>((coordinate & 3U) * 2U);
  return static_cast<std::uint8_t>((code[coordinate / 4] >> shift) & 3U);
}

std::array<std::size_t, 128> top128(const std::vector<float>& scores) {
  std::array<std::size_t, 128> result{};
  std::vector<std::size_t> ids(scores.size());
  std::iota(ids.begin(), ids.end(), 0);
  const auto better = [&](std::size_t lhs, std::size_t rhs) {
    return scores[lhs] == scores[rhs] ? lhs < rhs : scores[lhs] < scores[rhs];
  };
  std::nth_element(ids.begin(), ids.begin() + result.size(), ids.end(), better);
  std::copy_n(ids.begin(), result.size(), result.begin());
  std::sort(result.begin(), result.end(), better);
  return result;
}

bool same_top(const std::array<std::size_t, 128>& lhs,
              const std::array<std::size_t, 128>& rhs) {
  return lhs == rhs;
}

double top_overlap(const std::array<std::size_t, 128>& lhs,
                   const std::array<std::size_t, 128>& rhs) {
  std::size_t found = 0;
  for (const auto id : lhs)
    if (std::find(rhs.begin(), rhs.end(), id) != rhs.end()) ++found;
  return static_cast<double>(found) / 128.0;
}

QueryTables make_tables(const std::vector<float>& thresholds,
                        const std::vector<float>& query) {
  QueryTables tables;
  for (std::size_t d = 0; d < kDimensions; ++d) {
    const float q = query[d];
    const float t0 = thresholds[d * 3];
    const float t1 = thresholds[d * 3 + 1];
    const float t2 = thresholds[d * 3 + 2];
    tables.levels[d] = static_cast<std::uint8_t>(q > t0) +
                       static_cast<std::uint8_t>(q > t1) +
                       static_cast<std::uint8_t>(q > t2);
    const float low[4] = {-std::numeric_limits<float>::infinity(), t0, t1, t2};
    const float high[4] = {t0, t1, t2, std::numeric_limits<float>::infinity()};
    for (std::size_t level = 0; level < 4; ++level) {
      const float delta = q < low[level] ? low[level] - q
                         : q > high[level] ? q - high[level] : 0.0F;
      tables.coordinate[d * 4 + level] = delta * delta;
    }
  }
  for (std::size_t pair = 0; pair < 192; ++pair) {
    const std::size_t d = pair * 2;
    float maximum = 0.0F;
    for (std::size_t index = 0; index < 16; ++index) {
      const auto left = static_cast<std::uint8_t>(index & 3U);
      const auto right = static_cast<std::uint8_t>((index >> 2U) & 3U);
      const float value = tables.coordinate[d * 4 + left] +
                          tables.coordinate[(d + 1) * 4 + right];
      tables.pair[pair * 16 + index] = value;
      maximum = std::max(maximum, value);
    }
    tables.pair_scales[pair] = std::max(maximum / 255.0F,
                                       std::numeric_limits<float>::min());
  }
  for (std::size_t pair = 0; pair < 192; ++pair) {
    for (std::size_t index = 0; index < 16; ++index) {
      const float value = tables.pair[pair * 16 + index] / tables.pair_scales[pair];
      tables.pair_u8[pair * 16 + index] = static_cast<std::uint8_t>(
          std::clamp(std::lround(value), 0L, 255L));
    }
  }
  return tables;
}

float score_coordinate(const std::uint8_t* code, const QueryTables& tables) {
  float score = 0.0F;
  for (std::size_t d = 0; d < kDimensions; ++d)
    score += tables.coordinate[d * 4 + level_at(code, d)];
  return score;
}

float score_pair(const std::uint8_t* code, const QueryTables& tables) {
  float score = 0.0F;
  for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
    const auto value = code[byte];
    score += tables.pair[(byte * 2) * 16 + (value & 0x0FU)];
    score += tables.pair[(byte * 2 + 1) * 16 + (value >> 4U)];
  }
  return score;
}

std::uint32_t score_pair_u8(const std::uint8_t* code,
                            const QueryTables& tables) {
  std::uint32_t score = 0;
  for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
    const auto value = code[byte];
    score += tables.pair_u8[(byte * 2) * 16 + (value & 0x0FU)];
    score += tables.pair_u8[(byte * 2 + 1) * 16 + (value >> 4U)];
  }
  return score;
}

float score_pair_u8_scaled(const std::uint8_t* code, const QueryTables& tables) {
  float score = 0.0F;
  for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
    const auto value = code[byte];
    score += static_cast<float>(tables.pair_u8[(byte * 2) * 16 + (value & 0x0FU)]) * tables.pair_scales[byte * 2];
    score += static_cast<float>(tables.pair_u8[(byte * 2 + 1) * 16 + (value >> 4U)]) * tables.pair_scales[byte * 2 + 1];
  }
  return score;
}

#if AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2
void score_pair_u8_avx2_block(const std::vector<std::uint8_t>& transposed,
                              std::size_t block_start, std::size_t count,
                              const QueryTables& tables, float* output) {
  __m256 sums[4] = {_mm256_setzero_ps(), _mm256_setzero_ps(),
                    _mm256_setzero_ps(), _mm256_setzero_ps()};
  for (std::size_t pair = 0; pair < 192; ++pair) {
    alignas(32) std::uint8_t lut_bytes[32];
    for (std::size_t i = 0; i < 16; ++i) {
      lut_bytes[i] = tables.pair_u8[pair * 16 + i];
      lut_bytes[i + 16] = lut_bytes[i];
    }
    const __m256i lut = _mm256_load_si256(reinterpret_cast<const __m256i*>(lut_bytes));
    alignas(32) std::uint8_t indices[32]{};
    const auto* source = transposed.data() + pair * kDocuments + block_start;
    std::copy_n(source, count, indices);
    const __m256i values = _mm256_shuffle_epi8(lut, _mm256_load_si256(reinterpret_cast<const __m256i*>(indices)));
    const __m128i low = _mm256_castsi256_si128(values);
    const __m128i high = _mm256_extracti128_si256(values, 1);
    const __m256 scale = _mm256_set1_ps(tables.pair_scales[pair]);
    sums[0] = _mm256_add_ps(sums[0], _mm256_mul_ps(_mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(low)), scale));
    sums[1] = _mm256_add_ps(sums[1], _mm256_mul_ps(_mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(_mm_srli_si128(low, 8))), scale));
    sums[2] = _mm256_add_ps(sums[2], _mm256_mul_ps(_mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(high)), scale));
    sums[3] = _mm256_add_ps(sums[3], _mm256_mul_ps(_mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(_mm_srli_si128(high, 8))), scale));
  }
  _mm256_storeu_ps(output, sums[0]);
  _mm256_storeu_ps(output + 8, sums[1]);
  _mm256_storeu_ps(output + 16, sums[2]);
  _mm256_storeu_ps(output + 24, sums[3]);
  (void)count;
}
#else
void score_pair_u8_avx2_block(const std::vector<std::uint8_t>&,
                              std::size_t, std::size_t,
                              const QueryTables&, float*) {}
#endif

template <typename Score>
std::vector<double> benchmark(const std::vector<std::uint8_t>& codes,
                              std::size_t documents, std::size_t queries,
                              std::size_t repeats, Score scorer,
                              const std::vector<QueryTables>& tables) {
  std::vector<double> timings;
  std::vector<float> scores(documents);
  for (std::size_t repeat = 0; repeat < repeats; ++repeat) {
    for (std::size_t query = 0; query < queries; ++query) {
      const auto started = Clock::now();
      for (std::size_t id = 0; id < documents; ++id)
        scores[id] = scorer(codes.data() + id * kOrdinalBytes, tables[query]);
      volatile float guard = scores[query % documents];
      (void)guard;
      timings.push_back(std::chrono::duration<double, std::milli>(Clock::now() - started).count());
    }
  }
  return timings;
}

double percentile(std::vector<double> values, double fraction) {
  std::sort(values.begin(), values.end());
  if (values.empty()) return 0.0;
  return values[static_cast<std::size_t>(fraction * (values.size() - 1))];
}

}  // namespace

int main(int argc, char** argv) {
  try {
    if (argc < 4 || argc > 6) {
      std::cerr << "usage: thq_fastscan_kernel_benchmark CODES THRESHOLDS QUERIES [query-limit] [repeats]\n";
      return 2;
    }
    const auto raw_codes = read_file<std::uint8_t>(argv[1]);
    const auto thresholds = read_file<float>(argv[2]);
    const auto queries = read_file<float>(argv[3]);
    if (raw_codes.size() != kDocuments * kOrdinalBytes || thresholds.size() != kDimensions * 3 ||
        queries.size() % kDimensions != 0)
      throw std::runtime_error("source shape mismatch");
    const std::size_t query_limit = argc >= 5 ? std::stoull(argv[4]) : queries.size() / kDimensions;
    const std::size_t repeats = argc >= 6 ? std::stoull(argv[5]) : 1;
    const std::size_t query_count = std::min(query_limit, queries.size() / kDimensions);
    if (query_count == 0 || repeats == 0) throw std::runtime_error("query-limit and repeats must be positive");
    std::vector<QueryTables> tables;
    tables.reserve(query_count);
    for (std::size_t q = 0; q < query_count; ++q)
      tables.push_back(make_tables(thresholds, std::vector<float>(queries.begin() + q * kDimensions,
                                                                   queries.begin() + (q + 1) * kDimensions)));
    const std::vector<std::uint8_t> codes = raw_codes;
    std::vector<std::uint8_t> transposed(192 * kDocuments);
    for (std::size_t id = 0; id < kDocuments; ++id) {
      const auto* code = codes.data() + id * kOrdinalBytes;
      for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
        const auto value = code[byte];
        transposed[(byte * 2) * kDocuments + id] = value & 0x0FU;
        transposed[(byte * 2 + 1) * kDocuments + id] = value >> 4U;
      }
    }
    std::vector<float> reference(kDocuments);
    std::vector<float> pair_scores(kDocuments);
    std::vector<std::uint32_t> quantized(kDocuments);
    std::size_t pair_exact_mismatches = 0;
    std::size_t quantized_top_mismatches = 0;
    std::size_t avx_top_mismatches = 0;
    double quantized_top_overlap = 0.0;
    double avx_top_overlap = 0.0;
    float avx_max_abs_error = 0.0F;
    for (std::size_t q = 0; q < query_count; ++q) {
      for (std::size_t id = 0; id < kDocuments; ++id) {
        const auto* code = codes.data() + id * kOrdinalBytes;
        reference[id] = score_coordinate(code, tables[q]);
        pair_scores[id] = score_pair(code, tables[q]);
        quantized[id] = score_pair_u8(code, tables[q]);
      }
      for (std::size_t id = 0; id < kDocuments; ++id)
        if (std::abs(reference[id] - pair_scores[id]) > 1.0e-5F) ++pair_exact_mismatches;
      const auto ref_top = top128(reference);
      std::vector<float> qscore(kDocuments);
      for (std::size_t id = 0; id < kDocuments; ++id)
        qscore[id] = score_pair_u8_scaled(codes.data() + id * kOrdinalBytes, tables[q]);
      const auto quantized_top = top128(qscore);
      quantized_top_overlap += top_overlap(ref_top, quantized_top);
      if (!same_top(ref_top, quantized_top)) ++quantized_top_mismatches;
#if AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2
      std::vector<float> avx_score(kDocuments);
      for (std::size_t block = 0; block < kDocuments; block += 32) {
        const auto count = std::min<std::size_t>(32, kDocuments - block);
        score_pair_u8_avx2_block(transposed, block, count, tables[q], avx_score.data() + block);
      }
      const auto avx_top = top128(avx_score);
      avx_top_overlap += top_overlap(ref_top, avx_top);
      if (!same_top(ref_top, avx_top)) ++avx_top_mismatches;
      for (std::size_t id = 0; id < kDocuments; ++id)
        avx_max_abs_error = std::max(avx_max_abs_error, std::abs(avx_score[id] - qscore[id]));
#endif
    }
    const auto scalar = benchmark(codes, kDocuments, query_count, repeats,
                                  [](const auto* code, const auto& table) { return score_coordinate(code, table); }, tables);
    const auto pair = benchmark(codes, kDocuments, query_count, repeats,
                                [](const auto* code, const auto& table) { return score_pair(code, table); }, tables);
    const auto quant = benchmark(codes, kDocuments, query_count, repeats,
                                 [](const auto* code, const auto& table) { return score_pair_u8_scaled(code, table); }, tables);
    std::vector<double> avx;
#if AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2
    std::vector<float> avx_scores(kDocuments);
    for (std::size_t repeat = 0; repeat < repeats; ++repeat) {
      for (std::size_t query = 0; query < query_count; ++query) {
        const auto started = Clock::now();
        for (std::size_t block = 0; block < kDocuments; block += 32) {
          const auto count = std::min<std::size_t>(32, kDocuments - block);
          score_pair_u8_avx2_block(transposed, block, count, tables[query], avx_scores.data() + block);
        }
        volatile float guard = avx_scores[query % kDocuments];
        (void)guard;
        avx.push_back(std::chrono::duration<double, std::milli>(Clock::now() - started).count());
      }
    }
#endif
    std::cout << std::setprecision(9)
              << "{\"family\":\"thq_fastscan_kernel_benchmark_v1\",\"documents\":" << kDocuments
              << ",\"queries\":" << query_count << ",\"repeats\":" << repeats
              << ",\"avx2_compiled\":" << (AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2 ? "true" : "false")
              << ",\"pair_exact_mismatches\":" << pair_exact_mismatches
              << ",\"quantized_top128_mismatches\":" << quantized_top_mismatches
              << ",\"avx2_top128_mismatches\":" << avx_top_mismatches
              << ",\"quantized_top128_mean_overlap\":" << (quantized_top_overlap / query_count)
              << ",\"avx2_top128_mean_overlap\":" << (avx_top_overlap / query_count)
              << ",\"avx2_max_abs_error\":" << avx_max_abs_error
              << ",\"kernels\":{\"coordinate_fp32\":{\"p50_ms\":" << percentile(scalar, .5)
              << ",\"p95_ms\":" << percentile(scalar, .95) << "},\"pair_lut_fp32\":{\"p50_ms\":"
              << percentile(pair, .5) << ",\"p95_ms\":" << percentile(pair, .95)
              << "},\"pair_lut_u8\":{\"p50_ms\":" << percentile(quant, .5)
              << ",\"p95_ms\":" << percentile(quant, .95) << "},\"pair_lut_u8_avx2\":{\"p50_ms\":"
              << percentile(avx, .5) << ",\"p95_ms\":" << percentile(avx, .95) << "}}}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
