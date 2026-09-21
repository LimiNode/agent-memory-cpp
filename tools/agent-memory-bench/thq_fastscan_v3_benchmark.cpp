#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

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
  for (int i = 1; i < argc; ++i) {
    const std::string key = argv[i];
    if (key == "--self-test") {
      options.self_test = true;
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
  }
  if (options.self_test) return options;
  if (options.codes.empty() || options.thresholds.empty() || options.queries.empty())
    throw std::runtime_error("--codes, --thresholds and --queries are required");
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

QueryTables make_tables(const float* thresholds, const float* query) {
  QueryTables tables;
  auto started = Clock::now();
  for (std::size_t d = 0; d < kDimensions; ++d) {
    const float q = query[d];
    const float low[4] = {-std::numeric_limits<float>::infinity(), thresholds[d * 3],
                          thresholds[d * 3 + 1], thresholds[d * 3 + 2]};
    const float high[4] = {thresholds[d * 3], thresholds[d * 3 + 1],
                           thresholds[d * 3 + 2], std::numeric_limits<float>::infinity()};
    for (std::size_t level = 0; level < 4; ++level) {
      const float delta = q < low[level] ? low[level] - q
                           : q > high[level] ? q - high[level] : 0.0F;
      tables.coordinate[d * 4 + level] = delta * delta;
    }
  }
  tables.coordinate_setup_ms =
      std::chrono::duration<double, std::milli>(Clock::now() - started).count();

  started = Clock::now();
  for (std::size_t byte = 0; byte < kOrdinalBytes; ++byte) {
    for (std::size_t packed = 0; packed < 256; ++packed) {
      float score = 0.0F;
      for (std::size_t lane = 0; lane < 4; ++lane)
        score += tables.coordinate[(byte * 4 + lane) * 4 + ((packed >> (lane * 2)) & 3U)];
      tables.byte[byte * 256 + packed] = score;
    }
  }
  tables.byte_setup_ms =
      std::chrono::duration<double, std::milli>(Clock::now() - started).count();

  started = Clock::now();
  std::array<float, kPairs> maxima{};
  for (std::size_t pair = 0; pair < kPairs; ++pair) {
    const std::size_t d = pair * 2;
    for (std::size_t index = 0; index < 16; ++index) {
      const float value = tables.coordinate[d * 4 + (index & 3U)] +
                          tables.coordinate[(d + 1) * 4 + ((index >> 2U) & 3U)];
      tables.pair[pair * 16 + index] = value;
      maxima[pair] = std::max(maxima[pair], value);
    }
  }
  tables.pair_setup_ms =
      std::chrono::duration<double, std::milli>(Clock::now() - started).count();

  started = Clock::now();
  for (std::size_t pair = 0; pair < kPairs; ++pair) {
    tables.local_scale[pair] = std::max(maxima[pair] / 255.0F,
                                        std::numeric_limits<float>::min());
    for (std::size_t index = 0; index < 16; ++index) {
      const auto quantized = static_cast<std::uint8_t>(std::clamp(
          std::lround(tables.pair[pair * 16 + index] / tables.local_scale[pair]), 0L, 255L));
      tables.local_u8[pair * 16 + index] = quantized;
      tables.local_u8_packed[pair * 32 + index] = quantized;
      tables.local_u8_packed[pair * 32 + 16 + index] = quantized;
    }
  }
  tables.local_quant_setup_ms =
      std::chrono::duration<double, std::milli>(Clock::now() - started).count();

  started = Clock::now();
  const float largest = *std::max_element(maxima.begin(), maxima.end());
  const double sum_max = std::accumulate(maxima.begin(), maxima.end(), 0.0);
  const double safe_sum = sum_max + 0.5 * static_cast<double>(kPairs);
  const double factor = std::min(largest > 0.0F ? 255.0 / largest : 1.0,
                                 safe_sum > 0.0 ? 65535.0 / safe_sum : 1.0);
  tables.global_scale = static_cast<float>(factor);
  std::uint32_t max_sum = 0;
  for (std::size_t pair = 0; pair < kPairs; ++pair) {
    std::uint8_t pair_max = 0;
    for (std::size_t index = 0; index < 16; ++index) {
      const auto quantized = static_cast<std::uint8_t>(std::clamp(
          std::lround(tables.pair[pair * 16 + index] * tables.global_scale), 0L, 255L));
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
    if (options.output.empty()) std::cout << std::setw(2) << result << '\n';
    else {
      std::ofstream output(options.output);
      if (!output) throw std::runtime_error("cannot open output");
      output << std::setw(2) << result << '\n';
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
