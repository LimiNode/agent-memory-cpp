#include <algorithm>
#include <array>
#include <chrono>
#include <cfenv>
#include <cfloat>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#if defined(__SSE__) || defined(_M_X64) || defined(_M_IX86)
#include <xmmintrin.h>
#endif

#if AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2
#include <immintrin.h>
#endif

namespace {

using Clock = std::chrono::steady_clock;
using Json = nlohmann::json;

constexpr std::size_t kDimensions = 384;
constexpr std::size_t kOrdinalBytes = 96;
constexpr std::size_t kPairs = 192;
constexpr std::size_t kDocuments = 1'000'000;
constexpr std::size_t kBlock = 32;
constexpr std::size_t kTop = 128;
constexpr std::size_t kCandidateRecordBytes = 148;

struct Options {
  std::string codes;
  std::string thresholds;
  std::string queries;
  std::string candidate_flat;
  std::string candidate_raw;
  std::string documents;
  std::string qrel_ids;
  std::string qrel_scores;
  std::string output;
  std::size_t query_limit = 8;
  std::size_t repeats = 3;
  std::size_t warmups = 1;
  std::string mode = "both";
  bool self_test = false;
  bool certificate_self_test = false;
  bool certificate_benchmark = false;
};

struct QueryTables {
  std::array<float, kDimensions * 4> coordinate{};
  std::array<float, kOrdinalBytes * 256> byte{};
  std::array<float, kPairs * 16> pair{};
  std::array<std::uint8_t, kPairs * 16> local_u8{};
  std::array<float, kPairs> local_scale{};
  std::array<std::uint8_t, kPairs * 32> local_u8_packed{};
  std::array<std::uint8_t, kPairs * 16> global_u8{};
  std::array<std::uint8_t, kPairs * 32> global_u8_packed{};
  float global_scale = 1.0F;
  std::uint32_t global_max_sum = 0;
  double coordinate_setup_ms = 0.0;
  double byte_setup_ms = 0.0;
  double pair_setup_ms = 0.0;
  double local_quant_setup_ms = 0.0;
  double global_quant_setup_ms = 0.0;
};

struct Layouts {
  std::size_t documents = 0;
  std::size_t padded = 0;
  std::vector<std::uint8_t> plane_major;
  std::vector<std::uint8_t> block32;
};

struct Candidates {
  std::vector<std::int32_t> ids;
  std::vector<std::size_t> offsets;
};

struct TimingSample {
  double workload_ms = 0.0;
  double total_ms = 0.0;
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

Options parse_options(int argc, char** argv) {
  Options options;
  bool profile_override = false;
  for (int i = 1; i < argc; ++i) {
    const std::string key = argv[i];
    if (key == "--self-test") {
      options.self_test = true;
      continue;
    }
    if (key == "--certificate-self-test") {
      options.certificate_self_test = true;
      continue;
    }
    if (key == "--certificate-benchmark") {
      options.certificate_benchmark = true;
      continue;
    }
    if (i + 1 >= argc) throw std::runtime_error("missing value for " + key);
    const std::string value = argv[++i];
    if (key == "--codes") options.codes = value;
    else if (key == "--thresholds") options.thresholds = value;
    else if (key == "--queries") options.queries = value;
    else if (key == "--candidate-flat") options.candidate_flat = value;
    else if (key == "--candidate-raw") options.candidate_raw = value;
    else if (key == "--documents") options.documents = value;
    else if (key == "--qrel-ids") options.qrel_ids = value;
    else if (key == "--qrel-scores") options.qrel_scores = value;
    else if (key == "--output") options.output = value;
    else if (key == "--query-limit") options.query_limit = std::stoull(value);
    else if (key == "--repeats") options.repeats = std::stoull(value);
    else if (key == "--warmups") options.warmups = std::stoull(value);
    else if (key == "--mode") options.mode = value;
    else throw std::runtime_error("unknown option " + key);
    if (key == "--query-limit" || key == "--repeats" || key == "--warmups" || key == "--mode")
      profile_override = true;
  }
  if (static_cast<int>(options.self_test) + static_cast<int>(options.certificate_self_test) +
      static_cast<int>(options.certificate_benchmark) > 1)
    throw std::runtime_error("benchmark and self-test modes are mutually exclusive");
  if (options.self_test || options.certificate_self_test) return options;
  if (options.codes.empty() || options.thresholds.empty() || options.queries.empty())
    throw std::runtime_error("--codes, --thresholds and --queries are required");
  if (options.certificate_benchmark) {
    if (profile_override || !options.candidate_flat.empty() || !options.candidate_raw.empty() ||
        !options.documents.empty() || !options.qrel_ids.empty() || !options.qrel_scores.empty())
      throw std::runtime_error("certificate benchmark has fixed dense profiles and no quality/gather inputs");
    return options;
  }
  if (options.query_limit == 0 || options.repeats == 0)
    throw std::runtime_error("query-limit and repeats must be positive");
  if (options.mode != "dense" && options.mode != "production" && options.mode != "both")
    throw std::runtime_error("--mode must be dense, production or both");
  if (options.mode != "dense" &&
      (options.candidate_flat.empty() || options.candidate_raw.empty()))
    throw std::runtime_error("production mode requires candidate flat/raw inputs");
  const bool any_quality = !options.documents.empty() || !options.qrel_ids.empty() ||
                           !options.qrel_scores.empty();
  const bool all_quality = !options.documents.empty() && !options.qrel_ids.empty() &&
                           !options.qrel_scores.empty();
  if (any_quality && !all_quality)
    throw std::runtime_error("quality replay requires documents and both qrel inputs");
  return options;
}

std::uint8_t level_at(const std::uint8_t* code, std::size_t coordinate) {
  return static_cast<std::uint8_t>((code[coordinate / 4] >> ((coordinate & 3U) * 2U)) & 3U);
}

std::uint8_t quantize_u8(float value) {
  if (!std::isfinite(value)) throw std::runtime_error("nonfinite quantization input");
  return static_cast<std::uint8_t>(std::lround(std::clamp(value, 0.0F, 255.0F)));
}

QueryTables make_tables(const float* thresholds, const float* query,
                         bool legacy_luts = true, bool global_quantization = true) {
  QueryTables tables;
  auto started = Clock::now();
  for (std::size_t d = 0; d < kDimensions; ++d) {
    const float q = query[d];
    if (!std::isfinite(q) || !std::isfinite(thresholds[d * 3]) ||
        !std::isfinite(thresholds[d * 3 + 1]) || !std::isfinite(thresholds[d * 3 + 2]) ||
        thresholds[d * 3] > thresholds[d * 3 + 1] ||
        thresholds[d * 3 + 1] > thresholds[d * 3 + 2])
      throw std::runtime_error("invalid query or thresholds");
    const float low[4] = {-std::numeric_limits<float>::infinity(), thresholds[d * 3],
                          thresholds[d * 3 + 1], thresholds[d * 3 + 2]};
    const float high[4] = {thresholds[d * 3], thresholds[d * 3 + 1],
                           thresholds[d * 3 + 2], std::numeric_limits<float>::infinity()};
    for (std::size_t level = 0; level < 4; ++level) {
      const float delta = q < low[level] ? low[level] - q
                           : q > high[level] ? q - high[level] : 0.0F;
      tables.coordinate[d * 4 + level] = delta * delta;
      if (!std::isfinite(tables.coordinate[d * 4 + level]))
        throw std::runtime_error("coordinate LUT overflow");
    }
  }
  tables.coordinate_setup_ms =
      std::chrono::duration<double, std::milli>(Clock::now() - started).count();

  if (legacy_luts) {
    started = Clock::now();
    for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
      for (std::size_t packed = 0; packed < 256; ++packed) {
        float score = 0.0F;
        for (std::size_t lane = 0; lane < 4; ++lane)
          score += tables.coordinate[(byte * 4 + lane) * 4 + ((packed >> (lane * 2)) & 3U)];
        tables.byte[byte * 256 + packed] = score;
        if (!std::isfinite(score)) throw std::runtime_error("byte LUT overflow");
      }
    }
    tables.byte_setup_ms =
        std::chrono::duration<double, std::milli>(Clock::now() - started).count();
  }

  started = Clock::now();
  std::array<float, kPairs> maxima{};
  for (std::size_t pair = 0; pair < kPairs; ++pair) {
    const std::size_t d = pair * 2;
    for (std::size_t index = 0; index < 16; ++index) {
      const float value = tables.coordinate[d * 4 + (index & 3U)] +
                          tables.coordinate[(d + 1) * 4 + ((index >> 2U) & 3U)];
      tables.pair[pair * 16 + index] = value;
      if (!std::isfinite(value)) throw std::runtime_error("pair LUT overflow");
      maxima[pair] = std::max(maxima[pair], value);
    }
  }
  tables.pair_setup_ms =
      std::chrono::duration<double, std::milli>(Clock::now() - started).count();

  if (legacy_luts) {
    started = Clock::now();
    for (std::size_t pair = 0; pair < kPairs; ++pair) {
      tables.local_scale[pair] = std::max(maxima[pair] / 255.0F,
                                          std::numeric_limits<float>::min());
      for (std::size_t index = 0; index < 16; ++index) {
        const auto quantized = quantize_u8(
            tables.pair[pair * 16 + index] / tables.local_scale[pair]);
        tables.local_u8[pair * 16 + index] = quantized;
        tables.local_u8_packed[pair * 32 + index] = quantized;
        tables.local_u8_packed[pair * 32 + 16 + index] = quantized;
      }
    }
    tables.local_quant_setup_ms =
        std::chrono::duration<double, std::milli>(Clock::now() - started).count();
  }

  if (!global_quantization) return tables;
  started = Clock::now();
  const float largest = *std::max_element(maxima.begin(), maxima.end());
  const double sum_max = std::accumulate(maxima.begin(), maxima.end(), 0.0);
  const double safe_sum = sum_max + 0.5 * static_cast<double>(kPairs);
  const double factor = std::min(largest > 0.0F ? 255.0 / largest : 1.0,
                                 safe_sum > 0.0 ? 65535.0 / safe_sum : 1.0);
  tables.global_scale = static_cast<float>(factor);
  if (!(tables.global_scale > 0.0F) || !std::isfinite(tables.global_scale))
    throw std::runtime_error("invalid global quantization scale");
  std::uint32_t max_sum = 0;
  for (std::size_t pair = 0; pair < kPairs; ++pair) {
    std::uint8_t pair_max = 0;
    for (std::size_t index = 0; index < 16; ++index) {
      const auto quantized = quantize_u8(
          tables.pair[pair * 16 + index] * tables.global_scale);
      tables.global_u8[pair * 16 + index] = quantized;
      tables.global_u8_packed[pair * 32 + index] = quantized;
      tables.global_u8_packed[pair * 32 + 16 + index] = quantized;
      pair_max = std::max(pair_max, quantized);
    }
    max_sum += pair_max;
  }
  if (max_sum > std::numeric_limits<std::uint16_t>::max())
    throw std::runtime_error("global uint16 quantization overflow");
  tables.global_max_sum = max_sum;
  tables.global_quant_setup_ms =
      std::chrono::duration<double, std::milli>(Clock::now() - started).count();
  return tables;
}

float score_byte(const std::uint8_t* code, const QueryTables& tables) {
  float score = 0.0F;
  for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte)
    score += tables.byte[byte * 256 + code[byte]];
  return score;
}

float score_byte_unrolled4(const std::uint8_t* code, const QueryTables& tables) {
  float s0 = 0.0F, s1 = 0.0F, s2 = 0.0F, s3 = 0.0F;
  for (std::size_t byte = 0; byte < kOrdinalBytes; byte += 4) {
    s0 += tables.byte[byte * 256 + code[byte]];
    s1 += tables.byte[(byte + 1) * 256 + code[byte + 1]];
    s2 += tables.byte[(byte + 2) * 256 + code[byte + 2]];
    s3 += tables.byte[(byte + 3) * 256 + code[byte + 3]];
  }
  return (s0 + s1) + (s2 + s3);
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

float score_pair_local_u8(const std::uint8_t* code, const QueryTables& tables) {
  float score = 0.0F;
  for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
    const auto value = code[byte];
    score += tables.local_u8[(byte * 2) * 16 + (value & 0x0FU)] * tables.local_scale[byte * 2];
    score += tables.local_u8[(byte * 2 + 1) * 16 + (value >> 4U)] * tables.local_scale[byte * 2 + 1];
  }
  return score;
}

Layouts pack_layouts(const std::uint8_t* doc_major, std::size_t documents) {
  Layouts result;
  result.documents = documents;
  result.padded = ((documents + kBlock - 1) / kBlock) * kBlock;
  result.plane_major.assign(kOrdinalBytes * result.padded, 0);
  result.block32.assign((result.padded / kBlock) * kOrdinalBytes * kBlock, 0);
  for (std::size_t id = 0; id < documents; ++id) {
    const auto* code = doc_major + id * kOrdinalBytes;
    const std::size_t block = id / kBlock;
    const std::size_t lane = id % kBlock;
    for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
      result.plane_major[byte * result.padded + id] = code[byte];
      result.block32[(block * kOrdinalBytes + byte) * kBlock + lane] = code[byte];
    }
  }
  return result;
}

void gather_codes(const std::vector<std::uint8_t>& all_codes,
                  const std::int32_t* ids, std::size_t count,
                  std::vector<std::uint8_t>& gathered) {
  gathered.resize(count * kOrdinalBytes);
  for (std::size_t i = 0; i < count; ++i)
    std::copy_n(all_codes.data() + static_cast<std::size_t>(ids[i]) * kOrdinalBytes,
                kOrdinalBytes, gathered.data() + i * kOrdinalBytes);
}

#if AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2
__m256 lookup16_fp32(const float* lut, __m256i indices) {
  const __m256 low = _mm256_loadu_ps(lut);
  const __m256 high = _mm256_loadu_ps(lut + 8);
  const __m256i local = _mm256_and_si256(indices, _mm256_set1_epi32(7));
  const __m256 lo_values = _mm256_permutevar8x32_ps(low, local);
  const __m256 hi_values = _mm256_permutevar8x32_ps(high, local);
  const __m256 mask = _mm256_castsi256_ps(
      _mm256_cmpgt_epi32(indices, _mm256_set1_epi32(7)));
  return _mm256_blendv_ps(lo_values, hi_values, mask);
}

void score_pair_fp32_avx2_block32(const Layouts& layouts,
                                  const QueryTables& tables,
                                  std::vector<float>& output) {
  output.assign(layouts.padded, 0.0F);
  for (std::size_t block = 0; block < layouts.padded / kBlock; ++block) {
    const auto* block_data = layouts.block32.data() + block * kOrdinalBytes * kBlock;
    for (std::size_t lane = 0; lane < kBlock; lane += 8) {
      __m256 sum = _mm256_setzero_ps();
      for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
        const __m128i packed8 = _mm_loadl_epi64(reinterpret_cast<const __m128i*>(
            block_data + byte * kBlock + lane));
        const __m256i values = _mm256_cvtepu8_epi32(packed8);
        const __m256i low = _mm256_and_si256(values, _mm256_set1_epi32(0x0F));
        const __m256i high = _mm256_srli_epi32(values, 4);
        sum = _mm256_add_ps(sum, lookup16_fp32(tables.pair.data() + (byte * 2) * 16, low));
        sum = _mm256_add_ps(sum, lookup16_fp32(tables.pair.data() + (byte * 2 + 1) * 16, high));
      }
      _mm256_storeu_ps(output.data() + block * kBlock + lane, sum);
    }
  }
}

void score_local_u8_avx2_plane(const Layouts& layouts,
                               const QueryTables& tables,
                               std::vector<float>& output) {
  output.assign(layouts.padded, 0.0F);
  for (std::size_t block = 0; block < layouts.padded; block += kBlock) {
    __m256 sums[4] = {_mm256_setzero_ps(), _mm256_setzero_ps(),
                     _mm256_setzero_ps(), _mm256_setzero_ps()};
    for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
      const __m256i packed = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(
          layouts.plane_major.data() + byte * layouts.padded + block));
      const __m256i low = _mm256_shuffle_epi8(
          _mm256_loadu_si256(reinterpret_cast<const __m256i*>(
              tables.local_u8_packed.data() + (byte * 2) * 32)),
          _mm256_and_si256(packed, _mm256_set1_epi8(0x0F)));
      const __m256i high = _mm256_shuffle_epi8(
          _mm256_loadu_si256(reinterpret_cast<const __m256i*>(
              tables.local_u8_packed.data() + (byte * 2 + 1) * 32)),
          _mm256_and_si256(_mm256_srli_epi16(packed, 4), _mm256_set1_epi8(0x0F)));
      const __m128i low0 = _mm256_castsi256_si128(low);
      const __m128i low1 = _mm256_extracti128_si256(low, 1);
      const __m128i high0 = _mm256_castsi256_si128(high);
      const __m128i high1 = _mm256_extracti128_si256(high, 1);
      const __m256 low_scale = _mm256_set1_ps(tables.local_scale[byte * 2]);
      const __m256 high_scale = _mm256_set1_ps(tables.local_scale[byte * 2 + 1]);
      const __m128i halves[4] = {low0, _mm_srli_si128(low0, 8), low1, _mm_srli_si128(low1, 8)};
      const __m128i high_halves[4] = {high0, _mm_srli_si128(high0, 8), high1, _mm_srli_si128(high1, 8)};
      for (int part = 0; part < 4; ++part) {
        sums[part] = _mm256_add_ps(sums[part], _mm256_mul_ps(
            _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(halves[part])), low_scale));
        sums[part] = _mm256_add_ps(sums[part], _mm256_mul_ps(
            _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(high_halves[part])), high_scale));
      }
    }
    for (int part = 0; part < 4; ++part)
      _mm256_storeu_ps(output.data() + block + part * 8, sums[part]);
  }
}

template <bool BlockMajor>
void score_global_u16_avx2(const Layouts& layouts, const QueryTables& tables,
                           std::vector<std::uint16_t>& output) {
  output.assign(layouts.padded, 0);
  for (std::size_t block = 0; block < layouts.padded; block += kBlock) {
    __m256i sum0 = _mm256_setzero_si256();
    __m256i sum1 = _mm256_setzero_si256();
    for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
      const std::uint8_t* source = BlockMajor
          ? layouts.block32.data() + ((block / kBlock) * kOrdinalBytes + byte) * kBlock
          : layouts.plane_major.data() + byte * layouts.padded + block;
      const __m256i packed = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(source));
      const __m256i low = _mm256_shuffle_epi8(
          _mm256_loadu_si256(reinterpret_cast<const __m256i*>(
              tables.global_u8_packed.data() + (byte * 2) * 32)),
          _mm256_and_si256(packed, _mm256_set1_epi8(0x0F)));
      const __m256i high = _mm256_shuffle_epi8(
          _mm256_loadu_si256(reinterpret_cast<const __m256i*>(
              tables.global_u8_packed.data() + (byte * 2 + 1) * 32)),
          _mm256_and_si256(_mm256_srli_epi16(packed, 4), _mm256_set1_epi8(0x0F)));
      const __m128i lo0 = _mm256_castsi256_si128(low);
      const __m128i lo1 = _mm256_extracti128_si256(low, 1);
      const __m128i hi0 = _mm256_castsi256_si128(high);
      const __m128i hi1 = _mm256_extracti128_si256(high, 1);
      sum0 = _mm256_add_epi16(sum0, _mm256_cvtepu8_epi16(lo0));
      sum0 = _mm256_add_epi16(sum0, _mm256_cvtepu8_epi16(hi0));
      sum1 = _mm256_add_epi16(sum1, _mm256_cvtepu8_epi16(lo1));
      sum1 = _mm256_add_epi16(sum1, _mm256_cvtepu8_epi16(hi1));
    }
    _mm256_storeu_si256(reinterpret_cast<__m256i*>(output.data() + block), sum0);
    _mm256_storeu_si256(reinterpret_cast<__m256i*>(output.data() + block + 16), sum1);
  }
}
#else
void score_pair_fp32_avx2_block32(const Layouts&, const QueryTables&, std::vector<float>&) {
  throw std::runtime_error("AVX2 is not compiled");
}
void score_local_u8_avx2_plane(const Layouts&, const QueryTables&, std::vector<float>&) {
  throw std::runtime_error("AVX2 is not compiled");
}
template <bool BlockMajor>
void score_global_u16_avx2(const Layouts&, const QueryTables&, std::vector<std::uint16_t>&) {
  throw std::runtime_error("AVX2 is not compiled");
}
#endif

template <typename Score>
std::vector<std::size_t> top_indices(const Score& scores, const std::int32_t* ids,
                                     std::size_t count, std::size_t top = kTop) {
  std::vector<std::size_t> order(count);
  std::iota(order.begin(), order.end(), 0);
  const auto better = [&](std::size_t lhs, std::size_t rhs) {
    if (scores[lhs] != scores[rhs]) return scores[lhs] < scores[rhs];
    return ids[lhs] < ids[rhs];
  };
  top = std::min(top, count);
  if (top < count) std::nth_element(order.begin(), order.begin() + top, order.end(), better);
  order.resize(top);
  std::sort(order.begin(), order.end(), better);
  return order;
}

std::uint32_t float_bits(float value) {
  std::uint32_t bits;
  std::memcpy(&bits, &value, sizeof(bits));
  return bits;
}

void require_certificate_environment() {
#if defined(__FAST_MATH__) || (defined(__FINITE_MATH_ONLY__) && __FINITE_MATH_ONLY__ > 0) || defined(_M_FP_FAST)
  throw std::runtime_error("certificate rejects fast-math compilation");
#endif
  if (!std::numeric_limits<float>::is_iec559 ||
      !std::numeric_limits<double>::is_iec559 || sizeof(float) != 4 || sizeof(double) != 8 ||
      std::numeric_limits<float>::digits != 24 || std::numeric_limits<double>::digits != 53 ||
      FLT_EVAL_METHOD != 0 || std::fegetround() != FE_TONEAREST)
    throw std::runtime_error("certificate requires binary32/binary64 RN without excess precision");
  volatile float tiny = std::numeric_limits<float>::denorm_min();
  volatile float normal = std::numeric_limits<float>::min();
  volatile float one = 1.0F, two = 2.0F, half = 0.5F;
  volatile double tiny64 = std::numeric_limits<double>::denorm_min();
  volatile double normal64 = std::numeric_limits<double>::min();
  volatile double one64 = 1.0, two64 = 2.0, half64 = 0.5;
  if (float_bits(tiny * one) != 1U || float_bits(tiny * two) != 2U ||
      float_bits(normal * half) != 0x00400000U || tiny64 * one64 != tiny64 ||
      tiny64 * two64 != std::ldexp(1.0, -1073) ||
      normal64 * half64 != std::ldexp(1.0, -1023))
    throw std::runtime_error("certificate rejects FTZ/DAZ or missing gradual underflow");
}

double round_down(double value) {
  return std::nextafter(value, -std::numeric_limits<double>::infinity());
}

double round_up(double value) {
  return std::nextafter(value, std::numeric_limits<double>::infinity());
}

struct ScoreEnvelope {
  double residual_low = 0.0;
  double residual_high = 0.0;
  double accumulation_error = 0.0;
};

ScoreEnvelope make_score_envelope(const QueryTables& tables) {
  require_certificate_environment();
  if (!(tables.global_scale > 0.0F) || !std::isfinite(tables.global_scale))
    throw std::runtime_error("certificate requires a finite positive scale");
  ScoreEnvelope result;
  double absolute_sum = 0.0;
  for (std::size_t pair = 0; pair < kPairs; ++pair) {
    double low = std::numeric_limits<double>::infinity();
    double high = -std::numeric_limits<double>::infinity();
    double largest = 0.0;
    for (std::size_t index = 0; index < 16; ++index) {
      const auto position = pair * 16 + index;
      const double value = tables.pair[position];
      if (!std::isfinite(value)) throw std::runtime_error("nonfinite pair table");
      const double quotient = static_cast<double>(tables.global_u8[position]) /
                              static_cast<double>(tables.global_scale);
      low = std::min(low, round_down(value - round_up(quotient)));
      high = std::max(high, round_up(value - round_down(quotient)));
      largest = std::max(largest, std::abs(value));
    }
    result.residual_low = round_down(result.residual_low + low);
    result.residual_high = round_up(result.residual_high + high);
    absolute_sum = round_up(absolute_sum + largest);
  }
  const double nu = round_up(static_cast<double>(kPairs) * std::ldexp(1.0, -24));
  const double denominator = round_down(1.0 - nu);
  const double gamma = round_up(nu / denominator);
  const double underflow = round_up(
      round_up(static_cast<double>(kPairs) * std::ldexp(1.0, -150)) / denominator);
  result.accumulation_error = round_up(round_up(gamma * absolute_sum) + underflow);
  if (round_up(absolute_sum + result.accumulation_error) >
      static_cast<double>(std::numeric_limits<float>::max()))
    throw std::runtime_error("certificate cannot exclude FP32 prefix overflow");
  return result;
}

std::pair<double, double> score_bounds(std::uint16_t integer_score,
                                      const QueryTables& tables,
                                      const ScoreEnvelope& envelope) {
  const double quotient = static_cast<double>(integer_score) /
                          static_cast<double>(tables.global_scale);
  return {round_down(round_down(round_down(quotient) + envelope.residual_low) -
                     envelope.accumulation_error),
          round_up(round_up(round_up(quotient) + envelope.residual_high) +
                   envelope.accumulation_error)};
}

std::uint16_t integer_pair_score(const std::uint8_t* code, const QueryTables& tables) {
  std::uint16_t sum = 0;
  for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
    sum += tables.global_u8[(byte * 2) * 16 + (code[byte] & 0x0FU)];
    sum += tables.global_u8[(byte * 2 + 1) * 16 + (code[byte] >> 4U)];
  }
  return sum;  // At most 192*255 = 48960, independent of global_max_sum metadata.
}

struct RefinedTop {
  std::vector<std::size_t> selected;
  std::vector<float> scores;
  std::size_t exact_evaluations = 0;
  // Conservative sum of vector payload capacities across lifetimes, not RSS.
  std::size_t vector_capacity_bytes = 0;
};

bool must_refine(double lower, float tau) {
  return lower <= static_cast<double>(tau);
}

RefinedTop certified_top(const std::vector<std::uint8_t>& codes,
                         const std::vector<std::int32_t>& ids,
                         const QueryTables& tables, std::size_t top) {
  if (codes.size() % kOrdinalBytes != 0 || codes.size() / kOrdinalBytes != ids.size())
    throw std::runtime_error("certificate code/ID shape mismatch");
  auto sorted_ids = ids;
  std::sort(sorted_ids.begin(), sorted_ids.end());
  if ((!sorted_ids.empty() && sorted_ids.front() < 0) ||
      std::adjacent_find(sorted_ids.begin(), sorted_ids.end()) != sorted_ids.end())
    throw std::runtime_error("certificate requires distinct nonnegative IDs");
  const auto envelope = make_score_envelope(tables);
  RefinedTop result;
  result.vector_capacity_bytes = sorted_ids.capacity() * sizeof(std::int32_t);
  top = std::min(top, ids.size());
  if (top == 0) return result;
  std::vector<std::uint16_t> integer_scores(ids.size());
  for (std::size_t i = 0; i < ids.size(); ++i)
    integer_scores[i] = integer_pair_score(codes.data() + i * kOrdinalBytes, tables);
  const auto seeds = top_indices(integer_scores, ids.data(), ids.size(), top);
  result.scores.assign(ids.size(), std::numeric_limits<float>::infinity());
  float tau = -std::numeric_limits<float>::infinity();
  for (const auto i : seeds) {
    result.scores[i] = score_pair(codes.data() + i * kOrdinalBytes, tables);
    if (!std::isfinite(result.scores[i])) throw std::runtime_error("nonfinite exact incumbent");
    tau = std::max(tau, result.scores[i]);
    ++result.exact_evaluations;
  }
  for (std::size_t i = 0; i < ids.size(); ++i) {
    if (std::isfinite(result.scores[i])) continue;
    // Keep equality: a lower-ID tie may displace an incumbent.
    if (must_refine(score_bounds(integer_scores[i], tables, envelope).first, tau)) {
      result.scores[i] = score_pair(codes.data() + i * kOrdinalBytes, tables);
      if (!std::isfinite(result.scores[i])) throw std::runtime_error("nonfinite exact refinement");
      ++result.exact_evaluations;
    }
  }
  result.selected = top_indices(result.scores, ids.data(), ids.size(), top);
  result.vector_capacity_bytes += integer_scores.capacity() * sizeof(std::uint16_t) +
      seeds.capacity() * sizeof(std::size_t) + result.scores.capacity() * sizeof(float) +
      result.selected.capacity() * sizeof(std::size_t);
  return result;
}

double overlap(const std::vector<std::size_t>& lhs,
               const std::vector<std::size_t>& rhs) {
  std::size_t matched = 0;
  for (const auto value : lhs)
    matched += std::find(rhs.begin(), rhs.end(), value) != rhs.end();
  return lhs.empty() ? 1.0 : static_cast<double>(matched) / lhs.size();
}

double percentile(std::vector<double> values, double fraction) {
  if (values.empty()) return 0.0;
  std::sort(values.begin(), values.end());
  const double position = fraction * static_cast<double>(values.size() - 1);
  const auto lower = static_cast<std::size_t>(position);
  const auto upper = std::min(lower + 1, values.size() - 1);
  const double weight = position - lower;
  return values[lower] * (1.0 - weight) + values[upper] * weight;
}

Json timing_summary(const std::vector<TimingSample>& samples) {
  std::vector<double> workload;
  std::vector<double> total;
  for (const auto& sample : samples) {
    workload.push_back(sample.workload_ms);
    total.push_back(sample.total_ms);
  }
  return {{"samples", samples.size()},
          {"workload_p50_ms", percentile(workload, 0.50)},
          {"workload_p95_ms", percentile(workload, 0.95)},
          {"workload_p99_ms", percentile(workload, 0.99)},
          {"total_p50_ms", percentile(total, 0.50)},
          {"total_p95_ms", percentile(total, 0.95)},
          {"total_p99_ms", percentile(total, 0.99)}};
}

Candidates load_candidates(const std::string& flat_path, const std::string& raw_path) {
  std::ifstream raw_stream(raw_path);
  if (!raw_stream) throw std::runtime_error("cannot open candidate raw");
  Json raw;
  raw_stream >> raw;
  const auto& rows = raw.at("rows");
  Candidates result;
  result.offsets.push_back(0);
  for (const auto& row : rows)
    result.offsets.push_back(result.offsets.back() + row.at("candidate_count").get<std::size_t>());
  const auto bytes = read_file<std::uint8_t>(flat_path);
  if (bytes.size() != result.offsets.back() * kCandidateRecordBytes)
    throw std::runtime_error("candidate flat/raw cardinality mismatch");
  result.ids.resize(result.offsets.back());
  for (std::size_t i = 0; i < result.ids.size(); ++i) {
    std::int32_t id = 0;
    std::memcpy(&id, bytes.data() + i * kCandidateRecordBytes, sizeof(id));
    if (id < 0 || id >= static_cast<std::int32_t>(kDocuments))
      throw std::runtime_error("candidate ID outside corpus");
    result.ids[i] = id;
  }
  return result;
}

double ndcg10(const std::vector<std::int32_t>& ids, const std::int64_t* qrel_ids,
              const float* qrel_scores) {
  std::unordered_map<std::int64_t, float> grades;
  std::vector<double> ideal;
  for (std::size_t i = 0; i < 20; ++i) {
    if (qrel_ids[i] >= 0 && qrel_scores[i] > 0.0F) {
      grades[qrel_ids[i]] = qrel_scores[i];
      ideal.push_back(std::exp2(qrel_scores[i]) - 1.0);
    }
  }
  std::sort(ideal.begin(), ideal.end(), std::greater<double>());
  ideal.resize(std::min<std::size_t>(10, ideal.size()));
  double dcg = 0.0, idcg = 0.0;
  for (std::size_t i = 0; i < std::min<std::size_t>(10, ids.size()); ++i) {
    const auto found = grades.find(ids[i]);
    const double gain = found == grades.end() ? 0.0 : std::exp2(found->second) - 1.0;
    dcg += gain / std::log2(static_cast<double>(i + 2));
  }
  for (std::size_t i = 0; i < ideal.size(); ++i)
    idcg += ideal[i] / std::log2(static_cast<double>(i + 2));
  return idcg == 0.0 ? 0.0 : dcg / idcg;
}

std::vector<std::int32_t> fp32_rerank(const std::vector<float>& documents,
                                      const float* query,
                                      const std::int32_t* ids,
                                      const std::vector<std::size_t>& selected) {
  std::vector<std::pair<float, std::int32_t>> scored;
  scored.reserve(selected.size());
  for (const auto position : selected) {
    const auto id = ids[position];
    const float* document = documents.data() + static_cast<std::size_t>(id) * kDimensions;
    float score = 0.0F;
    for (std::size_t d = 0; d < kDimensions; ++d) score += document[d] * query[d];
    scored.emplace_back(score, id);
  }
  std::sort(scored.begin(), scored.end(), [](const auto& lhs, const auto& rhs) {
    return lhs.first == rhs.first ? lhs.second < rhs.second : lhs.first > rhs.first;
  });
  std::vector<std::int32_t> result;
  for (std::size_t i = 0; i < std::min<std::size_t>(10, scored.size()); ++i)
    result.push_back(scored[i].second);
  return result;
}

void require_encloses(const std::pair<double, double>& bounds, float reference) {
  if (!std::isfinite(reference) || !(bounds.first <= reference && reference <= bounds.second))
    throw std::runtime_error("certificate escaped reference score");
}

void require_same_top(const RefinedTop& actual, const std::vector<float>& reference,
                      const std::vector<std::int32_t>& ids, std::size_t top) {
  const auto expected = top_indices(reference, ids.data(), ids.size(), top);
  if (actual.selected != expected) throw std::runtime_error("certified ordered-ID mismatch");
  for (const auto i : expected) {
    if (float_bits(actual.scores.at(i)) != float_bits(reference[i]))
      throw std::runtime_error("certified selected score-bit mismatch");
  }
}

void require_refined_bits(const RefinedTop& actual, const std::vector<float>& reference) {
  std::size_t evaluated = 0;
  for (std::size_t i = 0; i < actual.scores.size(); ++i) {
    if (actual.scores[i] == std::numeric_limits<float>::infinity()) continue;
    if (!std::isfinite(actual.scores[i]) || float_bits(actual.scores[i]) != float_bits(reference.at(i)))
      throw std::runtime_error("refined score-bit mismatch");
    ++evaluated;
  }
  if (evaluated != actual.exact_evaluations)
    throw std::runtime_error("refined score count mismatch");
}

Json selected_payload(const RefinedTop& result, const std::vector<std::int32_t>& ids) {
  Json selected_ids = Json::array(), selected_bits = Json::array();
  for (const auto position : result.selected) {
    selected_ids.push_back(ids.at(position));
    selected_bits.push_back(float_bits(result.scores.at(position)));
  }
  return {{"selected_ids", selected_ids}, {"selected_score_bits", selected_bits}};
}

RefinedTop exhaustive_pair_top(const std::vector<std::uint8_t>& codes,
                               const std::vector<std::int32_t>& ids,
                               const QueryTables& tables, const Layouts* layouts,
                               std::size_t top) {
  require_certificate_environment();
  RefinedTop result;
  if (layouts) score_pair_fp32_avx2_block32(*layouts, tables, result.scores);
  else {
    result.scores.resize(ids.size());
    for (std::size_t i = 0; i < ids.size(); ++i)
      result.scores[i] = score_pair(codes.data() + i * kOrdinalBytes, tables);
  }
  for (std::size_t i = 0; i < ids.size(); ++i)
    if (!std::isfinite(result.scores[i])) throw std::runtime_error("nonfinite baseline score");
  result.selected = top_indices(result.scores, ids.data(), ids.size(), top);
  result.exact_evaluations = ids.size();
  result.vector_capacity_bytes = result.scores.capacity() * sizeof(float) +
                                  result.selected.capacity() * sizeof(std::size_t);
  return result;
}

double nearest_rank(std::vector<double> samples, std::size_t percent) {
  if (samples.empty() || percent == 0 || percent > 100)
    throw std::runtime_error("invalid nearest-rank sample distribution");
  std::sort(samples.begin(), samples.end());
  return samples[(samples.size() * percent + 99) / 100 - 1];
}

bool near_exhaustive(std::size_t exact_sum, std::size_t total_scores) {
  // Fixed profiles contain at most 40M scores; these integer products cannot overflow.
  return exact_sum * 10 >= total_scores * 9;
}

void write_result(const Json& result, const std::string& path) {
  if (path.empty()) std::cout << std::setw(2) << result << '\n';
  else {
    std::ofstream output(path);
    if (!output) throw std::runtime_error("cannot open output");
    output << std::setw(2) << result << '\n';
    if (!output) throw std::runtime_error("cannot write output");
  }
}

void certificate_benchmark(const Options& options) {
  require_certificate_environment();
  if (!AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2)
    throw std::runtime_error("matched certificate benchmark requires pair-AVX2 eligibility");
  constexpr std::size_t query_count = 8, repeats = 5, warmups = 1;
  constexpr std::uint32_t order_seed = 20261120;
  const std::array<std::size_t, 3> counts = {4096, 65536, kDocuments};
  const std::array<std::size_t, 2> top_values = {16, 128};
  const std::array<std::string, 3> arms = {
      "pair_fp32_scalar", "pair_fp32_avx2_block32", "certified_pair_fixed_tau"};
  const std::array<std::array<std::size_t, 3>, 6> permutations = {{
      {{0, 1, 2}}, {{0, 2, 1}}, {{1, 0, 2}}, {{1, 2, 0}}, {{2, 0, 1}}, {{2, 1, 0}}}};
  const auto elapsed_ms = [](Clock::time_point started) {
    return std::chrono::duration<double, std::milli>(Clock::now() - started).count();
  };
  const auto codes = read_file<std::uint8_t>(options.codes);
  const auto thresholds = read_file<float>(options.thresholds);
  const auto queries = read_file<float>(options.queries);
  if (codes.size() != kDocuments * kOrdinalBytes || thresholds.size() != kDimensions * 3 ||
      queries.size() % kDimensions != 0 || queries.size() / kDimensions < query_count)
    throw std::runtime_error("certificate source shape mismatch");

  std::array<std::vector<float>, query_count> reference_scores;
  Json reference_cases = Json::array();
  std::size_t checked_scores = 0;
  // Global admission gate: no trial is timed until every profile has passed.
  for (const auto count : counts) {
    const std::vector<std::uint8_t> prefix(codes.begin(), codes.begin() + count * kOrdinalBytes);
    std::vector<std::int32_t> ids(count);
    std::iota(ids.begin(), ids.end(), 0);
    const auto layouts = pack_layouts(prefix.data(), count);
    for (std::size_t qi = 0; qi < query_count; ++qi) {
      const auto tables = make_tables(thresholds.data(), queries.data() + qi * kDimensions, false);
      const auto envelope = make_score_envelope(tables);
      const auto scalar = exhaustive_pair_top(prefix, ids, tables, nullptr, top_values.back());
      const auto simd = exhaustive_pair_top(prefix, ids, tables, &layouts, top_values.back());
      require_refined_bits(simd, scalar.scores);
      require_same_top(simd, scalar.scores, ids, top_values.back());
      for (std::size_t i = 0; i < count; ++i) {
        const auto* code = prefix.data() + i * kOrdinalBytes;
        require_encloses(score_bounds(integer_pair_score(code, tables), tables, envelope), scalar.scores[i]);
      }
      checked_scores += count;
      for (const auto top : top_values) {
        const auto certified = certified_top(prefix, ids, tables, top);
        require_refined_bits(certified, scalar.scores);
        require_same_top(certified, scalar.scores, ids, top);
        auto item = selected_payload(certified, ids);
        item.update(Json{{"documents", count}, {"top_k", top}, {"query_index", qi},
                         {"certified_exact_evaluations", certified.exact_evaluations}});
        reference_cases.push_back(std::move(item));
      }
      if (count == counts.back()) reference_scores[qi] = scalar.scores;
    }
  }

  Json profiles = Json::array(), raw_samples = Json::array(), summaries = Json::array();
  Json decisions = Json::array();
  std::mt19937 order_rng(order_seed);
  std::size_t trial_id = 0, warmup_executions = 0;
  for (const auto count : counts) {
    auto started = Clock::now();
    const std::vector<std::uint8_t> prefix(codes.begin(), codes.begin() + count * kOrdinalBytes);
    const double prefix_copy_ms = elapsed_ms(started);
    started = Clock::now();
    std::vector<std::int32_t> ids(count);
    std::iota(ids.begin(), ids.end(), 0);
    const double ids_setup_ms = elapsed_ms(started);
    started = Clock::now();
    const auto layouts = pack_layouts(prefix.data(), count);
    const double layout_build_ms = elapsed_ms(started);
    profiles.push_back({{"documents", count}, {"prefix_copy_ms", prefix_copy_ms},
                        {"prefix_code_capacity_bytes", prefix.capacity()}, {"ids_setup_ms", ids_setup_ms},
                        {"ids_count", ids.size()}, {"ids_capacity_bytes", ids.capacity() * sizeof(std::int32_t)},
                        {"layout_build_ms", layout_build_ms}, {"padded_documents", layouts.padded},
                        {"plane_major_capacity_bytes", layouts.plane_major.capacity()},
                        {"block32_capacity_bytes", layouts.block32.capacity()}});
    for (const auto top : top_values) {
      std::array<std::vector<double>, 3> times;
      std::array<std::size_t, 3> exact_sums{};
      for (std::size_t qi = 0; qi < query_count; ++qi) {
        for (std::size_t iteration = 0; iteration < warmups + repeats; ++iteration) {
          const bool warmup = iteration < warmups;
          const auto order = warmup ? permutations.front() : permutations[order_rng() % permutations.size()];
          for (std::size_t position = 0; position < order.size(); ++position) {
            const auto arm = order[position];
            // One stopwatch includes all per-query component work and allocation.
            started = Clock::now();
            const auto tables = make_tables(thresholds.data(), queries.data() + qi * kDimensions,
                                             false, arm == 2);
            const auto result = arm == 2 ? certified_top(prefix, ids, tables, top) :
                exhaustive_pair_top(prefix, ids, tables, arm == 1 ? &layouts : nullptr, top);
            const double total_ms = elapsed_ms(started);
            if (!std::isfinite(total_ms) || !(total_ms > 0.0))
              throw std::runtime_error("invalid enclosing sample time");
            require_refined_bits(result, reference_scores[qi]);
            require_same_top(result, reference_scores[qi], ids, top);
            if (warmup) { ++warmup_executions; continue; }
            const double fraction = static_cast<double>(result.exact_evaluations) / count;
            times[arm].push_back(total_ms);
            exact_sums[arm] += result.exact_evaluations;
            auto row = selected_payload(result, ids);
            row.update(Json{{"trial_id", trial_id}, {"documents", count}, {"top_k", top},
                            {"query_index", qi}, {"repeat", iteration - warmups}, {"arm", arms[arm]},
                            {"order_position", position}, {"total_ms", total_ms},
                            {"exact_evaluations", result.exact_evaluations}, {"refinement_fraction", fraction},
                            {"seed_count", arm == 2 ? top : 0},
                            {"nonseed_refinements", arm == 2 ? result.exact_evaluations - top : 0},
                            {"vector_capacity_bytes", result.vector_capacity_bytes},
                            {"coordinate_lut_bytes", tables.coordinate.size() * sizeof(float)},
                            {"pair_lut_bytes", tables.pair.size() * sizeof(float)},
                            {"global_u8_lut_bytes", arm == 2 ? tables.global_u8.size() : 0},
                            {"global_u8_constructed_bytes", arm == 2 ?
                                tables.global_u8.size() + tables.global_u8_packed.size() : 0},
                            {"query_tables_allocated_bytes", sizeof(QueryTables)}});
            raw_samples.push_back(std::move(row));
          }
          if (!warmup) ++trial_id;
        }
      }
      for (std::size_t arm = 0; arm < arms.size(); ++arm)
        summaries.push_back({{"documents", count}, {"top_k", top}, {"arm", arms[arm]},
                             {"sample_count", times[arm].size()}, {"p50_ms", nearest_rank(times[arm], 50)},
                             {"p95_ms", nearest_rank(times[arm], 95)},
                             {"mean_refinement_fraction", static_cast<double>(exact_sums[arm]) /
                                  (count * times[arm].size())}});
      const std::size_t baseline = nearest_rank(times[1], 50) < nearest_rank(times[0], 50) ? 1 : 0;
      const double baseline_median = nearest_rank(times[baseline], 50);
      const double baseline_p95 = nearest_rank(times[baseline], 95);
      const double certified_median = nearest_rank(times[2], 50);
      const double certified_p95 = nearest_rank(times[2], 95);
      const double median_ratio = certified_median / baseline_median;
      const double tail_ratio = certified_p95 / baseline_p95;
      const double mean_fraction = static_cast<double>(exact_sums[2]) / (count * times[2].size());
      decisions.push_back({{"documents", count}, {"top_k", top}, {"baseline_arm", arms[baseline]},
                           {"certified_p50_ratio", median_ratio}, {"certified_p95_ratio", tail_ratio},
                           {"mean_refinement_fraction", mean_fraction},
                           {"decision", certified_median <= 0.9 * baseline_median &&
                                certified_p95 <= baseline_p95 &&
                                !near_exhaustive(exact_sums[2], count * times[2].size()) ?
                                "CONTINUE_RESEARCH" : "KEEP_CURRENT"}});
    }
  }
#if defined(__clang__)
  const Json compiler = {{"id", "Clang"}, {"version", __clang_version__}};
#elif defined(_MSC_VER)
  const Json compiler = {{"id", "MSVC"}, {"version", _MSC_FULL_VER}};
#else
  const Json compiler = {{"id", "GNU"}, {"version", __VERSION__}};
#endif
  std::size_t reference_capacity = 0;
  for (const auto& values : reference_scores) reference_capacity += values.capacity() * sizeof(float);
  Json output = {{"schema_version", 1}, {"family", "r6_certified_adc_dense"}, {"status", "EXECUTED"},
      {"quality_status", "NOT_EXECUTED"}, {"source_identity", "EXTERNAL_MANIFEST_REQUIRED"},
      {"configuration", {{"document_counts", counts}, {"top_k", top_values},
                          {"query_indices", {0, 1, 2, 3, 4, 5, 6, 7}}, {"repeats", repeats},
                          {"warmups", warmups}, {"order_seed", order_seed}, {"arms", arms},
                          {"order_policy", "mt19937_raw_modulo_6_permutations"},
                          {"quantile", "nearest_rank"}, {"maximum_median_ratio", 0.9},
                          {"maximum_p95_ratio", 1.0}, {"near_exhaustive_mean_fraction", 0.90}}},
      {"compiler", compiler}, {"cplusplus", __cplusplus}, {"flt_eval_method", FLT_EVAL_METHOD},
      {"sizeof_size_t", sizeof(std::size_t)},
      {"dependencies", {{"nlohmann_json", {{"major", NLOHMANN_JSON_VERSION_MAJOR},
                                            {"minor", NLOHMANN_JSON_VERSION_MINOR},
                                            {"patch", NLOHMANN_JSON_VERSION_PATCH}}}}},
      {"avx2_compiled", static_cast<bool>(AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2)},
      {"fp_requirements", "IEEE binary32/binary64 RN, gradual underflow, strict operations, no contraction"},
      {"inputs", {{"codes", {{"path", options.codes}, {"bytes", codes.size()}}},
                   {"thresholds", {{"path", options.thresholds}, {"bytes", thresholds.size() * sizeof(float)}}},
                   {"queries", {{"path", options.queries}, {"bytes", queries.size() * sizeof(float)}}}}},
      {"preflight", {{"status", "PASS"}, {"score_comparisons", checked_scores},
                      {"enclosure_checks", checked_scores}, {"certified_cases", reference_cases.size()},
                      {"all_refined_score_bits", "PASS"}, {"pair_avx2_bitwise_eligibility", "PASS"}}},
      {"common_memory", {{"codes_capacity_bytes", codes.capacity()},
                          {"thresholds_capacity_bytes", thresholds.capacity() * sizeof(float)},
                          {"queries_capacity_bytes", queries.capacity() * sizeof(float)},
                          {"reference_scores_capacity_bytes", reference_capacity},
                          {"accounting", "vector payload capacities, not RSS/allocator metadata/stack"}}},
      {"persistent_profiles", profiles}, {"reference_cases", reference_cases}, {"raw_samples", raw_samples},
      {"summaries", summaries}, {"profile_decisions", decisions}, {"trial_count", trial_id},
      {"sample_count", raw_samples.size()}, {"warmup_executions", warmup_executions},
      {"timing_scope", "one full per-query component stopwatch; persistent dense layouts prebuilt; no gather"},
      {"excluded_costs", {"input file I/O", "one-time prefix/ID/layout setup reported separately",
                           "preflight and post-sample correctness checks", "JSON output and external receipt"}},
      {"limitations", {"eight exposed queries, not held-out quality", "repeats are not independent queries",
                        "sample p50/p95, not qualified serving tails", "no storage/final-rerank/production claim",
                        "JSON retention/allocator/stack overhead not measured; no process memory claim"}}};
  write_result(output, options.output);
}

void certificate_self_test() {
  require_certificate_environment();
  std::size_t fixtures = 0, rejected_inputs = 0, mutations = 0, environment_controls = 0;
  const auto rejects = [](const std::function<void()>& action) {
    try { action(); } catch (const std::runtime_error&) { return true; }
    return false;
  };
  const auto require = [](bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
  };
  const auto negative = [&](const std::function<void()>& action) {
    require(rejects(action), "negative input control was accepted");
    ++rejected_inputs;
  };
  const std::array<std::size_t, 2> uneven_counts = {899999, 900001};
  const auto exact_sum = std::accumulate(uneven_counts.begin(), uneven_counts.end(), std::size_t{0});
  require(near_exhaustive(exact_sum, 2 * kDocuments) &&
              !near_exhaustive(exact_sum - 1, 2 * kDocuments),
          "exact 90-percent uneven-count stop boundary failed");
  ++fixtures;
  const auto check = [&](const QueryTables& tables, std::size_t count, std::size_t top) {
    std::mt19937 rng(20261120);
    std::vector<std::uint8_t> codes(count * kOrdinalBytes);
    for (auto& value : codes) value = static_cast<std::uint8_t>(rng() & 255U);
    std::vector<std::int32_t> ids(count);
    for (std::size_t i = 0; i < count; ++i) ids[i] = static_cast<std::int32_t>(count - i);
    const auto envelope = make_score_envelope(tables);
    std::vector<float> reference(count);
    for (std::size_t i = 0; i < count; ++i) {
      const auto* code = codes.data() + i * kOrdinalBytes;
      reference[i] = score_pair(code, tables);
      require_encloses(score_bounds(integer_pair_score(code, tables), tables, envelope), reference[i]);
    }
    const auto actual = certified_top(codes, ids, tables, top);
    require_same_top(actual, reference, ids, top);
    require(actual.exact_evaluations >= std::min(top, count) && actual.exact_evaluations <= count,
            "invalid exact-evaluation count");
#if AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2
    std::vector<float> simd;
    score_pair_fp32_avx2_block32(pack_layouts(codes.data(), count), tables, simd);
    for (std::size_t i = 0; i < count; ++i)
      require(float_bits(simd[i]) == float_bits(reference[i]), "pair-AVX2 score-bit mismatch");
    require(top_indices(simd, ids.data(), count, top) ==
                top_indices(reference, ids.data(), count, top), "pair-AVX2 ordered-ID mismatch");
#endif
    ++fixtures;
  };
  std::vector<float> thresholds(kDimensions * 3), query(kDimensions);
  for (std::size_t d = 0; d < kDimensions; ++d) {
    thresholds[d * 3] = -0.5F;
    thresholds[d * 3 + 1] = 0.0F;
    thresholds[d * 3 + 2] = 0.5F;
    query[d] = static_cast<float>(static_cast<int>(d % 33) - 16) / 8.0F;
  }
  // Keep large test fixtures off the default 1 MiB Windows thread stack.
  const auto table_fixtures = std::make_unique<QueryTables[]>(6);
  auto& ordinary = table_fixtures[0];
  auto& certificate_tables = table_fixtures[1];
  auto& reference_tables = table_fixtures[2];
  auto& special = table_fixtures[3];
  auto& displaced_seed = table_fixtures[4];
  auto& invalid = table_fixtures[5];
  ordinary = make_tables(thresholds.data(), query.data());
  certificate_tables = make_tables(thresholds.data(), query.data(), false);
  reference_tables = make_tables(thresholds.data(), query.data(), false, false);
  for (std::size_t i = 0; i < ordinary.pair.size(); ++i)
    require(float_bits(ordinary.pair[i]) == float_bits(certificate_tables.pair[i]) &&
                float_bits(ordinary.pair[i]) == float_bits(reference_tables.pair[i]),
            "minimal LUT construction changed pair-table bits");
  require(ordinary.global_u8 == certificate_tables.global_u8 &&
              float_bits(ordinary.global_scale) == float_bits(certificate_tables.global_scale),
          "minimal LUT construction changed global quantization");
  ++fixtures;
  for (const auto count : {0U, 1U, 31U, 32U, 33U, 47U, 65U})
    for (const auto top : {0U, 7U, count + 3U}) check(ordinary, count, top);

  const std::array<std::uint8_t, kOrdinalBytes> zero_code{};
  const auto golden = [&](float middle, std::uint32_t expected) {
    special.pair.fill(0.0F);
    for (std::size_t j = 0; j < 16; ++j) {
      special.pair[j] = 1.0F;
      special.pair[16 + j] = middle;
      special.pair[32 + j] = -1.0F;
    }
    require(float_bits(score_pair(zero_code.data(), special)) == expected,
            "literal sequential-FP32 golden mismatch");
    check(special, 47, 7);
  };
  golden(std::ldexp(1.0F, -24), 0x00000000U);
  golden(3.0F * std::ldexp(1.0F, -24), 0x34800000U);
  special.pair.fill(std::numeric_limits<float>::denorm_min());
  require(float_bits(score_pair(zero_code.data(), special)) == 0x000000c0U,
          "literal subnormal accumulation golden mismatch");
  check(special, 33, 7);
  special.pair.fill(0.0F);
  check(special, 65, 7);  // All ties; reverse IDs must win, not scan order.
  for (std::size_t j = 0; j < 16; ++j)
    special.pair[j] = 1.0F + static_cast<float>(j) * std::ldexp(1.0F, -23);
  check(special, 47, 7);
  for (std::size_t p = 0; p < kPairs; ++p)
    for (std::size_t j = 0; j < 16; ++j) {
      const float value = static_cast<float>(static_cast<int>(j) - 4) * 32.0F + 0.5F;
      special.pair[p * 16 + j] = value;
      special.global_u8[p * 16 + j] = quantize_u8(value);
    }
  special.global_scale = 1.0F;
  check(special, 65, 7);  // Includes half steps and both clipping boundaries.
  special.pair.fill(std::numeric_limits<float>::max() / 1024.0F);
  check(special, 31, 7);

  displaced_seed.pair[0] = 1.0F;
  displaced_seed.pair[2] = 1000.0F;
  displaced_seed.global_u8[1] = 1;
  displaced_seed.global_u8[2] = 255;
  std::vector<std::uint8_t> three_codes(3 * kOrdinalBytes, 0);
  three_codes[kOrdinalBytes] = 1;
  three_codes[2 * kOrdinalBytes] = 2;
  const auto displaced = certified_top(three_codes, {3, 2, 1}, displaced_seed, 1);
  require_same_top(displaced, {1.0F, 0.0F, 1000.0F}, {3, 2, 1}, 1);
  require(displaced.exact_evaluations == 2 && displaced.selected == std::vector<std::size_t>{1},
          "nonseed refinement must displace seed while far candidate is pruned");
  ++fixtures;
  require(must_refine(1.0, 1.0F) && !must_refine(round_up(1.0), 1.0F),
          "strict cutoff boundary control failed");
  ++fixtures;
  const std::vector<std::uint8_t> one_code(kOrdinalBytes);
  for (const float scale : {0.0F, -1.0F, std::numeric_limits<float>::infinity(),
                            std::numeric_limits<float>::quiet_NaN()}) {
    invalid = ordinary;
    invalid.global_scale = scale;
    negative([&] { certified_top(one_code, {1}, invalid, 0); });
  }
  for (const float value : {std::numeric_limits<float>::infinity(),
                            -std::numeric_limits<float>::infinity(),
                            std::numeric_limits<float>::quiet_NaN()}) {
    invalid = ordinary;
    invalid.pair[0] = value;
    negative([&] { certified_top(one_code, {1}, invalid, 0); });
    auto bad_query = query;
    std::fill(bad_query.begin(), bad_query.end(), value);
    negative([&] { make_tables(thresholds.data(), bad_query.data()); });
    auto bad_thresholds = thresholds;
    bad_thresholds[0] = value;
    negative([&] { make_tables(bad_thresholds.data(), query.data()); });
    negative([&] { quantize_u8(value); });
  }
  invalid = ordinary;
  invalid.pair.fill(std::numeric_limits<float>::max() / 128.0F);
  negative([&] { certified_top(one_code, {1}, invalid, 0); });
  negative([&] { certified_top({0}, {}, ordinary, 0); });
  negative([&] { certified_top(one_code, {}, ordinary, 0); });
  negative([&] { certified_top(one_code, {-1}, ordinary, 0); });
  negative([&] { certified_top(std::vector<std::uint8_t>(2 * kOrdinalBytes), {1, 1}, ordinary, 0); });
  auto bad_thresholds = thresholds;
  bad_thresholds[0] = 1.0F;
  negative([&] { make_tables(bad_thresholds.data(), query.data()); });
  for (const float value : {1.0e19F, std::numeric_limits<float>::max()}) {
    auto bad_query = query;
    std::fill(bad_query.begin(), bad_query.end(), value);
    negative([&] { make_tables(thresholds.data(), bad_query.data()); });
  }
  const auto reference_score = score_pair(one_code.data(), ordinary);
  auto corrupt_bounds = score_bounds(integer_pair_score(one_code.data(), ordinary), ordinary,
                                     make_score_envelope(ordinary));
  corrupt_bounds.first = round_up(static_cast<double>(reference_score));
  require(rejects([&] { require_encloses(corrupt_bounds, reference_score); }),
          "corrupt lower-bound mutation escaped");
  ++mutations;
  corrupt_bounds = {-std::numeric_limits<double>::infinity(),
                    round_down(static_cast<double>(reference_score))};
  require(rejects([&] { require_encloses(corrupt_bounds, reference_score); }),
          "corrupt upper-bound mutation escaped");
  ++mutations;
  auto corrupt_result = certified_top(one_code, {1}, ordinary, 1);
  corrupt_result.scores[0] = std::nextafter(reference_score,
                                          std::numeric_limits<float>::infinity());
  require(rejects([&] { require_same_top(corrupt_result, {reference_score}, {1}, 1); }),
          "corrupt score-bit mutation escaped");
  ++mutations;
  corrupt_result.selected.clear();
  require(rejects([&] { require_same_top(corrupt_result, {reference_score}, {1}, 1); }),
          "corrupt ordered-result mutation escaped");
  ++mutations;
  const int original_rounding = std::fegetround();
  require(std::fesetround(FE_UPWARD) == 0, "cannot exercise non-RN control");
  const bool rounding_rejected = rejects(require_certificate_environment);
  const bool rounding_restored = std::fesetround(original_rounding) == 0;
  require(rounding_restored && rounding_rejected, "non-RN environment control failed");
  ++environment_controls;
#if defined(__SSE__) || defined(_M_X64) || defined(_M_IX86)
  const unsigned original_csr = _mm_getcsr();
  for (const unsigned mode : {0x8000U, 0x0040U}) {
    _mm_setcsr(original_csr | mode);
    const bool rejected = rejects(require_certificate_environment);
    _mm_setcsr(original_csr);
    require(rejected, "FTZ/DAZ environment control failed");
    ++environment_controls;
  }
#endif
  require_certificate_environment();
  std::cout << Json{{"status", "PASS"}, {"correctness_fixtures", fixtures},
                    {"invalid_input_controls", rejected_inputs}, {"mutation_controls", mutations},
                    {"environment_controls", environment_controls},
                    {"pair_avx2_bitwise_eligibility", AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2 ?
                         "PASS_ON_FIXTURES" : "NOT_COMPILED"},
                    {"performance_status", "NOT_EXECUTED"}, {"quality_status", "NOT_EXECUTED"}}
                   .dump(2) << '\n';
}

void self_test() {
  constexpr std::size_t count = 47;
  std::mt19937 rng(20260921);
  std::uniform_int_distribution<int> byte_distribution(0, 255);
  std::normal_distribution<float> normal(0.0F, 1.0F);
  std::vector<std::uint8_t> codes(count * kOrdinalBytes);
  for (auto& value : codes) value = static_cast<std::uint8_t>(byte_distribution(rng));
  std::vector<float> thresholds(kDimensions * 3);
  for (std::size_t d = 0; d < kDimensions; ++d) {
    thresholds[d * 3] = -0.5F;
    thresholds[d * 3 + 1] = 0.0F;
    thresholds[d * 3 + 2] = 0.5F;
  }
  std::vector<float> query(kDimensions);
  for (auto& value : query) value = normal(rng);
  const auto tables = make_tables(thresholds.data(), query.data());
  const auto layouts = pack_layouts(codes.data(), count);
  std::vector<float> pair_simd;
  std::vector<float> local_simd;
  std::vector<std::uint16_t> global_plane;
  std::vector<std::uint16_t> global_block;
  score_pair_fp32_avx2_block32(layouts, tables, pair_simd);
  score_local_u8_avx2_plane(layouts, tables, local_simd);
  score_global_u16_avx2<false>(layouts, tables, global_plane);
  score_global_u16_avx2<true>(layouts, tables, global_block);
  for (std::size_t id = 0; id < count; ++id) {
    const auto* code = codes.data() + id * kOrdinalBytes;
    const float byte_score = score_byte(code, tables);
    const float pair_score = score_pair(code, tables);
    const float tolerance = 1.0e-5F * std::max(1.0F, std::abs(byte_score));
    if (std::abs(byte_score - pair_score) > tolerance)
      throw std::runtime_error("exact byte/pair parity failed");
    if (std::abs(pair_score - pair_simd[id]) > tolerance)
      throw std::runtime_error("exact pair SIMD parity failed");
    const float local_score = score_pair_local_u8(code, tables);
    if (std::abs(local_score - local_simd[id]) >
        1.0e-5F * std::max(1.0F, std::abs(local_score)))
      throw std::runtime_error("prepacked local LUT parity failed");
    if (global_plane[id] != global_block[id])
      throw std::runtime_error("global plane/block parity failed");
  }
  std::cout << "THQ FastScan v3 self-test: PASS\n";
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const auto options = parse_options(argc, argv);
    if (options.certificate_self_test) {
      certificate_self_test();
      return 0;
    }
    if (options.certificate_benchmark) {
      certificate_benchmark(options);
      return 0;
    }
    if (options.self_test) {
      self_test();
      return 0;
    }
    const auto codes = read_file<std::uint8_t>(options.codes);
    const auto thresholds = read_file<float>(options.thresholds);
    const auto queries = read_file<float>(options.queries);
    if (codes.size() != kDocuments * kOrdinalBytes ||
        thresholds.size() != kDimensions * 3 || queries.size() % kDimensions != 0)
      throw std::runtime_error("source shape mismatch");
    const std::size_t query_count = std::min(options.query_limit, queries.size() / kDimensions);
    const bool dense = options.mode != "production";
    const bool production = options.mode != "dense";
    const auto dense_layouts = dense ? pack_layouts(codes.data(), kDocuments) : Layouts{};
    const auto candidates = production ? load_candidates(options.candidate_flat, options.candidate_raw)
                                       : Candidates{};
    if (production && candidates.offsets.size() - 1 < query_count)
      throw std::runtime_error("candidate stream has fewer queries than requested");
    const bool quality = !options.documents.empty();
    const auto documents = quality ? read_file<float>(options.documents) : std::vector<float>{};
    const auto qrel_ids = quality ? read_file<std::int64_t>(options.qrel_ids) : std::vector<std::int64_t>{};
    const auto qrel_scores = quality ? read_file<float>(options.qrel_scores) : std::vector<float>{};
    if (quality && (documents.size() != kDocuments * kDimensions ||
                    qrel_ids.size() < query_count * 20 || qrel_scores.size() < query_count * 20))
      throw std::runtime_error("quality source shape mismatch");

    const std::vector<std::string> arms = {
        "byte_lut_scalar", "byte_lut_unrolled4", "pair_fp32_scalar",
        "pair_fp32_avx2_block32", "pair_u8_scalar", "pair_u8_prepacked_plane",
        "pair_u8_global_u16_plane", "pair_u8_global_u16_block32"};
    std::unordered_map<std::string, std::vector<TimingSample>> dense_timings;
    std::unordered_map<std::string, std::vector<TimingSample>> production_timings;
    std::unordered_map<std::string, double> dense_top128_overlap_sum;
    std::unordered_map<std::string, double> dense_max_error;
    std::unordered_map<std::string, double> top128_overlap_sum;
    std::unordered_map<std::string, double> final_top10_overlap_sum;
    std::unordered_map<std::string, double> ndcg_sum;
    std::unordered_map<std::string, double> max_error;
    double checksum = 0.0;
    std::mt19937 order_rng(20260921);

    for (std::size_t qi = 0; qi < query_count; ++qi) {
      const float* query = queries.data() + qi * kDimensions;
      const auto tables = make_tables(thresholds.data(), query);
      const auto setup_ms = [&](const std::string& arm) {
        double value = tables.coordinate_setup_ms;
        if (arm.rfind("byte_", 0) == 0) return value + tables.byte_setup_ms;
        value += tables.pair_setup_ms;
        if (arm == "pair_u8_scalar" || arm == "pair_u8_prepacked_plane")
          value += tables.local_quant_setup_ms;
        if (arm.rfind("pair_u8_global", 0) == 0) value += tables.global_quant_setup_ms;
        return value;
      };

      auto run_arm = [&](const std::string& arm, const std::uint8_t* doc_major,
                         std::size_t count, const Layouts* persistent_layout,
                         bool include_pack, const std::int32_t* score_ids,
                         std::vector<float>& float_scores,
                         std::vector<std::uint16_t>& integer_scores) {
        Layouts temporary;
        const auto started = Clock::now();
        const Layouts* layouts = persistent_layout;
        if (include_pack && (arm == "pair_fp32_avx2_block32" ||
                             arm == "pair_u8_prepacked_plane" ||
                             arm.rfind("pair_u8_global", 0) == 0)) {
          temporary = pack_layouts(doc_major, count);
          layouts = &temporary;
        }
        if (arm == "byte_lut_scalar" || arm == "byte_lut_unrolled4" ||
            arm == "pair_fp32_scalar" || arm == "pair_u8_scalar") {
          float_scores.resize(count);
          for (std::size_t id = 0; id < count; ++id) {
            const auto* code = doc_major + id * kOrdinalBytes;
            if (arm == "byte_lut_scalar") float_scores[id] = score_byte(code, tables);
            else if (arm == "byte_lut_unrolled4") float_scores[id] = score_byte_unrolled4(code, tables);
            else if (arm == "pair_fp32_scalar") float_scores[id] = score_pair(code, tables);
            else float_scores[id] = score_pair_local_u8(code, tables);
          }
        } else if (arm == "pair_fp32_avx2_block32") {
          score_pair_fp32_avx2_block32(*layouts, tables, float_scores);
        } else if (arm == "pair_u8_prepacked_plane") {
          score_local_u8_avx2_plane(*layouts, tables, float_scores);
        } else if (arm == "pair_u8_global_u16_plane") {
          score_global_u16_avx2<false>(*layouts, tables, integer_scores);
        } else if (arm == "pair_u8_global_u16_block32") {
          score_global_u16_avx2<true>(*layouts, tables, integer_scores);
        }
        std::vector<std::int32_t> sequential;
        const std::int32_t* ids = score_ids;
        if (ids == nullptr) {
          sequential.resize(count);
          std::iota(sequential.begin(), sequential.end(), 0);
          ids = sequential.data();
        }
        const auto selected = arm.rfind("pair_u8_global", 0) == 0
            ? top_indices(integer_scores, ids, count)
            : top_indices(float_scores, ids, count);
        const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
        checksum += selected.empty() ? 0.0 : ids[selected.front()];
        return std::make_pair(TimingSample{elapsed, elapsed + setup_ms(arm)}, selected);
      };

      if (dense) {
        std::vector<float> dense_reference(kDocuments);
        std::vector<std::int32_t> dense_ids(kDocuments);
        std::iota(dense_ids.begin(), dense_ids.end(), 0);
        for (std::size_t id = 0; id < kDocuments; ++id)
          dense_reference[id] = score_byte(codes.data() + id * kOrdinalBytes, tables);
        const auto dense_reference_top = top_indices(
            dense_reference, dense_ids.data(), kDocuments);
        for (std::size_t warmup = 0; warmup < options.warmups; ++warmup) {
          for (const auto& arm : arms) {
            std::vector<float> fs;
            std::vector<std::uint16_t> is;
            run_arm(arm, codes.data(), kDocuments, &dense_layouts, false,
                    nullptr, fs, is);
          }
        }
        for (std::size_t repeat = 0; repeat < options.repeats; ++repeat) {
          auto order = arms;
          std::shuffle(order.begin(), order.end(), order_rng);
          for (const auto& arm : order) {
            std::vector<float> fs;
            std::vector<std::uint16_t> is;
            auto [timing, selected] = run_arm(arm, codes.data(), kDocuments,
                                              &dense_layouts, false, nullptr,
                                              fs, is);
            dense_timings[arm].push_back(timing);
            if (repeat == 0) {
              dense_top128_overlap_sum[arm] += overlap(dense_reference_top, selected);
              if (arm == "pair_fp32_scalar" || arm == "pair_fp32_avx2_block32" ||
                  arm.rfind("byte_", 0) == 0) {
                for (std::size_t i = 0; i < kDocuments; ++i)
                  dense_max_error[arm] = std::max(dense_max_error[arm],
                      std::abs(static_cast<double>(fs[i]) - dense_reference[i]));
              }
            }
          }
        }
      }

      if (production) {
        const auto begin = candidates.offsets[qi];
        const auto count = candidates.offsets[qi + 1] - begin;
        const auto* ids = candidates.ids.data() + begin;
        std::vector<std::uint8_t> gathered;
        gather_codes(codes, ids, count, gathered);
        std::vector<float> reference_scores(count);
        for (std::size_t i = 0; i < count; ++i)
          reference_scores[i] = score_byte(gathered.data() + i * kOrdinalBytes, tables);
        const auto reference_top = top_indices(reference_scores, ids, count);
        std::vector<std::size_t> exact_candidate_top;
        std::vector<std::int32_t> exact_top10;
        if (quality) {
          std::vector<float> exact_scores(count);
          for (std::size_t i = 0; i < count; ++i) {
            const float* document = documents.data() + static_cast<std::size_t>(ids[i]) * kDimensions;
            float score = 0.0F;
            for (std::size_t d = 0; d < kDimensions; ++d) score += document[d] * query[d];
            exact_scores[i] = -score;
          }
          exact_candidate_top = top_indices(exact_scores, ids, count, 10);
          for (const auto position : exact_candidate_top) exact_top10.push_back(ids[position]);
        }
        for (std::size_t warmup = 0; warmup < options.warmups; ++warmup) {
          for (const auto& arm : arms) {
            gather_codes(codes, ids, count, gathered);
            std::vector<float> fs;
            std::vector<std::uint16_t> is;
            run_arm(arm, gathered.data(), count, nullptr, true, ids, fs, is);
          }
        }
        for (std::size_t repeat = 0; repeat < options.repeats; ++repeat) {
          auto order = arms;
          std::shuffle(order.begin(), order.end(), order_rng);
          for (const auto& arm : order) {
            const auto gather_started = Clock::now();
            gather_codes(codes, ids, count, gathered);
            const double gather_ms = std::chrono::duration<double, std::milli>(
                Clock::now() - gather_started).count();
            std::vector<float> fs;
            std::vector<std::uint16_t> is;
            auto [timing, selected] = run_arm(arm, gathered.data(), count,
                                              nullptr, true, ids, fs, is);
            timing.workload_ms += gather_ms;
            timing.total_ms += gather_ms;
            production_timings[arm].push_back(timing);
            if (repeat != 0) continue;
            top128_overlap_sum[arm] += overlap(reference_top, selected);
            if (arm == "pair_fp32_scalar" || arm == "pair_fp32_avx2_block32" ||
                arm.rfind("byte_", 0) == 0) {
              for (std::size_t i = 0; i < count; ++i)
                max_error[arm] = std::max(max_error[arm],
                    std::abs(static_cast<double>(fs[i]) - reference_scores[i]));
            }
            if (quality) {
              const auto reranked = fp32_rerank(documents, query, ids, selected);
              std::size_t survived = 0;
              for (const auto id : exact_top10)
                survived += std::find(reranked.begin(), reranked.end(), id) != reranked.end();
              final_top10_overlap_sum[arm] += static_cast<double>(survived) / 10.0;
              ndcg_sum[arm] += ndcg10(reranked, qrel_ids.data() + qi * 20,
                                      qrel_scores.data() + qi * 20);
            }
          }
        }
      }
    }

    Json result = {{"schema_version", 1},
                   {"family", "thq_fastscan_v3_benchmark"},
                   {"status", "EXECUTED"},
                   {"documents", kDocuments},
                   {"queries", query_count},
                   {"repeats", options.repeats},
                   {"warmups", options.warmups},
                   {"mode", options.mode},
                   {"avx2_compiled", static_cast<bool>(AGENT_MEMORY_THQ_FASTSCAN_HAS_AVX2)},
                   {"arm_order_randomized", true},
                   {"checksum", checksum},
                   {"timing_scope", {{"dense", "score plus top128; persistent layouts prebuilt"},
                                      {"production", "gathered candidate codes plus per-arm pack where required, score and top128"},
                                      {"total", "workload plus arm-specific query table setup"}}}};
    for (const auto& arm : arms) {
      if (dense) {
        result["dense"][arm] = timing_summary(dense_timings[arm]);
        result["dense"][arm]["mean_top128_overlap"] =
            dense_top128_overlap_sum[arm] / query_count;
        if (arm == "pair_fp32_scalar" || arm == "pair_fp32_avx2_block32" ||
            arm.rfind("byte_", 0) == 0)
          result["dense"][arm]["max_abs_exact_score_error"] = dense_max_error[arm];
      }
      if (production) {
        result["production"][arm] = timing_summary(production_timings[arm]);
        result["production"][arm]["mean_top128_overlap"] = top128_overlap_sum[arm] / query_count;
        if (arm == "pair_fp32_scalar" || arm == "pair_fp32_avx2_block32" ||
            arm.rfind("byte_", 0) == 0)
          result["production"][arm]["max_abs_exact_score_error"] = max_error[arm];
        if (quality) {
          result["production"][arm]["mean_final_fp32_top10_overlap"] =
              final_top10_overlap_sum[arm] / query_count;
          result["production"][arm]["mean_qrels_ndcg10"] = ndcg_sum[arm] / query_count;
        }
      }
    }
    result["limitations"] = {"local single-host timing", "no cache/NUMA counters",
                              "candidate codes are gathered from doc-major THQ storage",
                              "full 152-query confirmation remains required after bounded replay"};
    write_result(result, options.output);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
