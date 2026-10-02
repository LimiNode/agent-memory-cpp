#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

#if defined(AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2) && AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2
#include <immintrin.h>
#endif

namespace {
constexpr std::size_t kDocuments = 1000000;
constexpr std::size_t kDimension = 384;
constexpr std::size_t kThqBytes = 96;
constexpr std::size_t kThqPairs = kDimension / 2;
constexpr std::size_t kBlockSize = 32;
constexpr std::size_t kPageBytes = 4096;
constexpr std::size_t kCandidateRecordBytes = 148;
struct Candidate { float score; std::int32_t id; };
struct DenseCandidate { double score; std::int32_t id; };
struct CascadeResult {
  Candidate best;
  std::uint64_t thq_scan_pages;
  std::uint64_t rerank_payload_pages;
  std::vector<Candidate> coarse;
};

void validate_lsq_ids(const std::vector<std::int32_t>& ids) {
  if (!std::is_sorted(ids.begin(), ids.end()) ||
      std::adjacent_find(ids.begin(), ids.end()) != ids.end())
    throw std::runtime_error("LSQ payload IDs must be strictly increasing");
  if (std::any_of(ids.begin(), ids.end(), [](std::int32_t id) {
        return id < 0 || static_cast<std::size_t>(id) >= kDocuments;
      }))
    throw std::runtime_error("LSQ payload ID is outside the document corpus");
}

void validate_lsq_header(std::uint32_t stages, std::uint32_t dimensions,
                         std::uint32_t count) {
  if (stages == 0 || dimensions != kDimension || count == 0 ||
      count > kDocuments)
    throw std::runtime_error("invalid LSQ payload dimensions");
}

template <typename T> bool all_finite(const std::vector<T>& values) {
  return std::all_of(values.begin(), values.end(), [](const T value) {
    return std::isfinite(static_cast<double>(value));
  });
}
struct LsqPayload {
  std::uint32_t stages = 0;
  std::uint32_t dimensions = 0;
  std::vector<std::int32_t> ids;
  std::vector<std::uint8_t> codes;
  std::vector<float> codebooks;
  std::vector<float> centroids;
  std::vector<float> norms;
  std::size_t serialized_bytes = 0;
};
struct LsqScoredRows {
  std::vector<DenseCandidate> top10;
  std::vector<double> scores;
  double prepare_ms = 0.0;
  double score_ms = 0.0;
};
bool better(const Candidate& a, const Candidate& b) {
  return a.score < b.score || (a.score == b.score && a.id < b.id);
}
bool better_desc(const Candidate& a, const Candidate& b) {
  return a.score > b.score || (a.score == b.score && a.id < b.id);
}
bool better_dense_desc(const DenseCandidate& a, const DenseCandidate& b) {
  return a.score > b.score || (a.score == b.score && a.id < b.id);
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

std::size_t file_bytes(const std::string& path) {
  std::ifstream in(path, std::ios::binary | std::ios::ate);
  if (!in) throw std::runtime_error("cannot open " + path);
  const auto end = in.tellg();
  if (end < 0) throw std::runtime_error("cannot measure " + path);
  return static_cast<std::size_t>(end);
}

struct QueryLut {
  std::array<float, kDimension * 4> coordinate{};
  std::array<float, kThqBytes * 256> byte{};
  std::array<float, kThqPairs * 16> pair{};
};

struct ThqBlock32Layout {
  std::vector<std::uint8_t> codes;
  std::size_t documents = 0;
  std::size_t padded_documents = 0;
};

float thq_score_row(const std::uint8_t* row, const QueryLut& lut);
float thq_score_row_unrolled4(const std::uint8_t* row, const QueryLut& lut);

QueryLut build_lut(const std::vector<float>& thresholds, const float* query) {
  QueryLut lut;
  for (std::size_t d = 0; d < kDimension; ++d) {
    for (std::size_t level = 0; level < 4; ++level) {
      const float lo = level == 0 ? -std::numeric_limits<float>::infinity()
                                  : thresholds[d * 3 + level - 1];
      const float hi = level == 3 ? std::numeric_limits<float>::infinity()
                                  : thresholds[d * 3 + level];
      const float delta = query[d] < lo ? lo - query[d]
                                        : (query[d] > hi ? query[d] - hi : 0.0f);
      lut.coordinate[d * 4 + level] = delta * delta;
    }
  }
  for (std::size_t byte = 0; byte < kThqBytes; ++byte) {
    for (std::size_t packed = 0; packed < 256; ++packed) {
      float score = 0.0f;
      for (std::size_t lane = 0; lane < 4; ++lane)
        score += lut.coordinate[(byte * 4 + lane) * 4 + ((packed >> (lane * 2)) & 3U)];
      lut.byte[byte * 256 + packed] = score;
    }
  }
  for (std::size_t pair = 0; pair < kThqPairs; ++pair) {
    for (std::size_t packed = 0; packed < 16; ++packed) {
      lut.pair[pair * 16 + packed] =
          lut.coordinate[(pair * 2) * 4 + (packed & 3U)] +
          lut.coordinate[(pair * 2 + 1) * 4 + (packed >> 2U)];
    }
  }
  return lut;
}

ThqBlock32Layout pack_thq_block32(const std::vector<std::uint8_t>& doc_major) {
  ThqBlock32Layout result;
  if (doc_major.empty() || doc_major.size() % kThqBytes != 0)
    throw std::runtime_error("THQ block32 input is not a whole document set");
  result.documents = doc_major.size() / kThqBytes;
  result.padded_documents =
      ((result.documents + kBlockSize - 1) / kBlockSize) * kBlockSize;
  result.codes.assign((result.padded_documents / kBlockSize) *
                          kThqBytes * kBlockSize,
                      0);
  for (std::size_t id = 0; id < result.documents; ++id) {
    const std::size_t block = id / kBlockSize;
    const std::size_t lane = id % kBlockSize;
    for (std::size_t byte = 0; byte < kThqBytes; ++byte) {
      result.codes[(block * kThqBytes + byte) * kBlockSize + lane] =
          doc_major[id * kThqBytes + byte];
    }
  }
  return result;
}

std::vector<Candidate> thq_top128(const std::vector<std::uint8_t>& codes,
                                  const QueryLut& lut) {
  std::vector<Candidate> all;
  all.reserve(kDocuments);
  for (std::size_t id = 0; id < kDocuments; ++id) {
    const auto* row = codes.data() + id * kThqBytes;
    float score = 0.0f;
    for (std::size_t byte = 0; byte < kThqBytes; ++byte)
      score += lut.byte[byte * 256 + row[byte]];
    all.push_back({score, static_cast<std::int32_t>(id)});
  }
  std::partial_sort(all.begin(), all.begin() + 128, all.end(), better);
  all.resize(128);
  return all;
}

template <std::size_t Capacity, typename Compare>
class FixedCandidateHeap final {
 public:
  bool empty() const { return size_ == 0; }
  std::size_t size() const { return size_; }
  const Candidate& top() const { return values_[0]; }

  void push(const Candidate value) {
    if (size_ == Capacity) throw std::runtime_error("fixed candidate heap is full");
    values_[size_] = value;
    sift_up(size_++);
  }

  void replace_top(const Candidate value) {
    if (size_ == 0) throw std::runtime_error("fixed candidate heap is empty");
    values_[0] = value;
    sift_down(0);
  }

  Candidate pop_top() {
    if (size_ == 0) throw std::runtime_error("fixed candidate heap is empty");
    const Candidate result = values_[0];
    values_[0] = values_[--size_];
    if (size_ != 0) sift_down(0);
    return result;
  }

 private:
  void sift_up(std::size_t index) {
    while (index != 0) {
      const std::size_t parent = (index - 1) / 2;
      if (!Compare{}(values_[parent], values_[index])) break;
      std::swap(values_[parent], values_[index]);
      index = parent;
    }
  }

  void sift_down(std::size_t index) {
    while (true) {
      const std::size_t left = index * 2 + 1;
      if (left >= size_) break;
      std::size_t child = left;
      const std::size_t right = left + 1;
      if (right < size_ && Compare{}(values_[left], values_[right])) child = right;
      if (!Compare{}(values_[index], values_[child])) break;
      std::swap(values_[index], values_[child]);
      index = child;
    }
  }

  std::array<Candidate, Capacity> values_{};
  std::size_t size_ = 0;
};

struct WorseCandidate {
  bool operator()(const Candidate& lhs, const Candidate& rhs) const {
    return better(lhs, rhs);
  }
};

struct WorseDescendingCandidate {
  bool operator()(const Candidate& lhs, const Candidate& rhs) const {
    return better_desc(lhs, rhs);
  }
};

template <std::size_t Capacity, typename Compare, typename Better>
std::array<Candidate, Capacity> bounded_top_from_scores(
    const std::vector<float>& scores, std::size_t count, Better is_better) {
  if (count < Capacity || scores.size() < count)
    throw std::runtime_error("bounded score buffer is shorter than requested top-k");
  FixedCandidateHeap<Capacity, Compare> heap;
  for (std::size_t id = 0; id < count; ++id) {
    const Candidate candidate{scores[id], static_cast<std::int32_t>(id)};
    if (heap.size() < Capacity) heap.push(candidate);
    else if (is_better(candidate, heap.top())) heap.replace_top(candidate);
  }
  std::array<Candidate, Capacity> result{};
  for (std::size_t i = result.size(); i-- > 0;) result[i] = heap.pop_top();
  std::sort(result.begin(), result.end(), is_better);
  return result;
}

std::array<Candidate, 128> thq_top128_bounded(
    const std::vector<std::uint8_t>& codes, const QueryLut& lut) {
  FixedCandidateHeap<128, WorseCandidate> heap;
  for (std::size_t id = 0; id < kDocuments; ++id) {
    const Candidate candidate{thq_score_row_unrolled4(codes.data() + id * kThqBytes, lut),
                              static_cast<std::int32_t>(id)};
    if (heap.size() < 128) {
      heap.push(candidate);
    } else if (better(candidate, heap.top())) {
      heap.replace_top(candidate);
    }
  }
  std::array<Candidate, 128> result{};
  for (std::size_t i = result.size(); i-- > 0;) {
    result[i] = heap.pop_top();
  }
  std::sort(result.begin(), result.end(), better);
  return result;
}

std::vector<Candidate> thq_top128_candidates(
    const std::vector<std::uint8_t>& codes, const QueryLut& lut,
    const std::vector<std::int32_t>& ids) {
  std::vector<Candidate> all;
  all.reserve(ids.size());
  for (const auto id : ids) {
    const auto* row = codes.data() + static_cast<std::size_t>(id) * kThqBytes;
    all.push_back({thq_score_row_unrolled4(row, lut), id});
  }
  const auto limit = std::min<std::size_t>(128, all.size());
  std::partial_sort(all.begin(), all.begin() + limit, all.end(), better);
  all.resize(limit);
  return all;
}

float thq_score_row(const std::uint8_t* row, const QueryLut& lut) {
  float score = 0.0f;
  for (std::size_t byte = 0; byte < kThqBytes; ++byte)
    score += lut.byte[byte * 256 + row[byte]];
  return score;
}

float thq_score_row_unrolled4(const std::uint8_t* row, const QueryLut& lut) {
  float score0 = 0.0f;
  float score1 = 0.0f;
  float score2 = 0.0f;
  float score3 = 0.0f;
  for (std::size_t byte = 0; byte < kThqBytes; byte += 4) {
    score0 += lut.byte[(byte + 0) * 256 + row[byte + 0]];
    score1 += lut.byte[(byte + 1) * 256 + row[byte + 1]];
    score2 += lut.byte[(byte + 2) * 256 + row[byte + 2]];
    score3 += lut.byte[(byte + 3) * 256 + row[byte + 3]];
  }
  return (score0 + score1) + (score2 + score3);
}

#if defined(AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2) && AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2
__m256 lookup16_fp32(const float* table, __m256i indices) {
  const __m256 low = _mm256_loadu_ps(table);
  const __m256 high = _mm256_loadu_ps(table + 8);
  const __m256i local = _mm256_and_si256(indices, _mm256_set1_epi32(7));
  const __m256 low_values = _mm256_permutevar8x32_ps(low, local);
  const __m256 high_values = _mm256_permutevar8x32_ps(high, local);
  const __m256 mask = _mm256_castsi256_ps(
      _mm256_cmpgt_epi32(indices, _mm256_set1_epi32(7)));
  return _mm256_blendv_ps(low_values, high_values, mask);
}
#endif

void score_thq_block32(const ThqBlock32Layout& layout, const QueryLut& lut,
                       std::vector<float>& scores) {
  if (scores.size() < layout.padded_documents)
    throw std::runtime_error("THQ score workspace is shorter than block32 layout");
#if defined(AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2) && AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2
  for (std::size_t block = 0; block < layout.padded_documents / kBlockSize;
       ++block) {
    const auto* block_data =
        layout.codes.data() + block * kThqBytes * kBlockSize;
    for (std::size_t lane = 0; lane < kBlockSize; lane += 8) {
      __m256 sums[4] = {_mm256_setzero_ps(), _mm256_setzero_ps(),
                        _mm256_setzero_ps(), _mm256_setzero_ps()};
      for (std::size_t byte = 0; byte < kThqBytes; byte += 4) {
        for (std::size_t part = 0; part < 4; ++part) {
          const std::size_t position = byte + part;
          const __m128i packed8 = _mm_loadl_epi64(
              reinterpret_cast<const __m128i*>(
                  block_data + position * kBlockSize + lane));
          const __m256i indices = _mm256_cvtepu8_epi32(packed8);
          sums[part] = _mm256_add_ps(
              sums[part],
              _mm256_i32gather_ps(lut.byte.data() + position * 256,
                                  indices, sizeof(float)));
        }
      }
      const __m256 score = _mm256_add_ps(_mm256_add_ps(sums[0], sums[1]),
                                         _mm256_add_ps(sums[2], sums[3]));
      _mm256_storeu_ps(scores.data() + block * kBlockSize + lane, score);
    }
  }
#else
  for (std::size_t id = 0; id < layout.padded_documents; ++id) {
    const std::size_t block = id / kBlockSize;
    const std::size_t lane = id % kBlockSize;
    float score = 0.0f;
    for (std::size_t byte = 0; byte < kThqBytes; ++byte) {
      const auto packed =
          layout.codes[(block * kThqBytes + byte) * kBlockSize + lane];
      score += lut.byte[byte * 256 + packed];
    }
    scores[id] = score;
  }
#endif
}

template <typename CodeT>
Candidate best_int8(const std::vector<CodeT>& codes, const std::vector<float>& scales,
                    const std::vector<float>& power_gains, const float* query,
                    std::int32_t id, bool nonlinear) {
  const auto* row = codes.data() + static_cast<std::size_t>(id) * kDimension;
  float score = 0.0f;
  if (!nonlinear) {
    float dot = 0.0f;
    for (std::size_t d = 0; d < kDimension; ++d)
      dot += static_cast<float>(row[d]) * query[d];
    score = dot * scales[static_cast<std::size_t>(id)];
  } else {
    static const auto code_gain = [] {
      std::array<float, 128> table{};
      for (std::size_t i = 0; i < table.size(); ++i)
        table[i] = std::pow(static_cast<float>(i), 1.6f);
      return table;
    }();
    const float gain = power_gains[static_cast<std::size_t>(id)];
    for (std::size_t d = 0; d < kDimension; ++d) {
      const int code = static_cast<int>(row[d]);
      const float decoded = std::copysign(code_gain[static_cast<std::size_t>(std::abs(code))] * gain,
                                          static_cast<float>(code));
      score += decoded * query[d];
    }
  }
  return {score, id};
}

float score_int8_float_scalar(const std::int8_t* values, float scale,
                              const float* query) {
  float dot = 0.0f;
  for (std::size_t d = 0; d < kDimension; ++d)
    dot += static_cast<float>(values[d]) * query[d];
  return dot * scale;
}

#if defined(AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2) && AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2
float score_int8_float_avx2(const std::int8_t* values, float scale,
                            const float* query) {
  __m256 sum0 = _mm256_setzero_ps();
  __m256 sum1 = _mm256_setzero_ps();
  for (std::size_t offset = 0; offset < kDimension; offset += 16) {
    const __m128i bytes = _mm_loadu_si128(
        reinterpret_cast<const __m128i*>(values + offset));
    const __m128i sign = _mm_cmpgt_epi8(_mm_setzero_si128(), bytes);
    const __m256i signed16 = _mm256_set_m128i(
        _mm_unpackhi_epi8(bytes, sign), _mm_unpacklo_epi8(bytes, sign));
    const __m256i lo32 = _mm256_cvtepi16_epi32(
        _mm256_castsi256_si128(signed16));
    const __m256i hi32 = _mm256_cvtepi16_epi32(
        _mm256_extracti128_si256(signed16, 1));
    sum0 = _mm256_add_ps(sum0, _mm256_mul_ps(
        _mm256_cvtepi32_ps(lo32), _mm256_loadu_ps(query + offset)));
    sum1 = _mm256_add_ps(sum1, _mm256_mul_ps(
        _mm256_cvtepi32_ps(hi32), _mm256_loadu_ps(query + offset + 8)));
  }
  const __m256 sum = _mm256_add_ps(sum0, sum1);
  const __m128 halves = _mm_add_ps(_mm256_castps256_ps128(sum),
                                   _mm256_extractf128_ps(sum, 1));
  const __m128 pairs = _mm_add_ps(halves, _mm_movehl_ps(halves, halves));
  const __m128 total = _mm_add_ss(pairs, _mm_shuffle_ps(pairs, pairs, 0x55));
  return _mm_cvtss_f32(total) * scale;
}
#endif

float score_int8_production(const std::vector<std::int8_t>& codes,
                            const std::vector<float>& scales,
                            const float* query, std::int32_t id) {
  const auto index = static_cast<std::size_t>(id);
  const auto* row = codes.data() + index * kDimension;
#if defined(AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2) && AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2
  return score_int8_float_avx2(row, scales[index], query);
#else
  return score_int8_float_scalar(row, scales[index], query);
#endif
}

void score_int8_dense(const std::vector<std::int8_t>& codes,
                      const std::vector<float>& scales, const float* query,
                      std::vector<float>& scores) {
  if (scores.size() < kDocuments)
    throw std::runtime_error("INT8 score workspace is shorter than corpus");
  for (std::size_t id = 0; id < kDocuments; ++id)
    scores[id] = score_int8_production(codes, scales, query,
                                       static_cast<std::int32_t>(id));
}

std::array<Candidate, 10> direct_top10_production(
    const std::vector<std::int8_t>& codes, const std::vector<float>& scales,
    const float* query) {
  FixedCandidateHeap<10, WorseDescendingCandidate> heap;
  for (std::size_t id = 0; id < kDocuments; ++id) {
    const Candidate candidate{score_int8_production(codes, scales, query,
                                                    static_cast<std::int32_t>(id)),
                              static_cast<std::int32_t>(id)};
    if (heap.size() < 10) heap.push(candidate);
    else if (better_desc(candidate, heap.top())) {
      heap.replace_top(candidate);
    }
  }
  std::array<Candidate, 10> result{};
  for (std::size_t i = result.size(); i-- > 0;) {
    result[i] = heap.pop_top();
  }
  std::sort(result.begin(), result.end(), better_desc);
  return result;
}

std::array<Candidate, 10> rerank_top10_production(
    const std::vector<std::int8_t>& codes, const std::vector<float>& scales,
    const float* query, const std::array<Candidate, 128>& coarse) {
  std::array<Candidate, 128> scored = coarse;
  for (auto& candidate : scored)
    candidate.score = score_int8_production(codes, scales, query, candidate.id);
  std::partial_sort(scored.begin(), scored.begin() + 10, scored.end(), better_desc);
  std::array<Candidate, 10> result{};
  std::copy_n(scored.begin(), result.size(), result.begin());
  return result;
}

template <typename CodeT>
std::pair<Candidate, std::uint64_t> direct(const std::vector<CodeT>& codes,
                                           const std::vector<float>& scales,
                                           const std::vector<float>& power_gains,
                                           const float* query, bool nonlinear) {
  Candidate best{-std::numeric_limits<float>::infinity(), 0};
  for (std::int32_t id = 0; id < static_cast<std::int32_t>(kDocuments); ++id) {
    const Candidate c = best_int8(codes, scales, power_gains, query, id, nonlinear);
    if (c.score > best.score || (c.score == best.score && c.id < best.id)) best = c;
  }
  // The two payload files are separate page namespaces: 384-byte codes and
  // 4-byte scales are not an interleaved 388-byte record.
  const auto code_pages = (kDocuments * kDimension + kPageBytes - 1) / kPageBytes;
  const auto scale_pages = (kDocuments * sizeof(float) + kPageBytes - 1) / kPageBytes;
  return {best, static_cast<std::uint64_t>(code_pages + scale_pages)};
}

template <typename CodeT>
CascadeResult cascade(const std::vector<std::uint8_t>& thq,
                                            const std::vector<float>& thresholds,
                                            const std::vector<CodeT>& codes,
                                            const std::vector<float>& scales,
                                            const std::vector<float>& power_gains,
                                            const float* query, bool nonlinear) {
  const QueryLut lut = build_lut(thresholds, query);
  const auto coarse = thq_top128(thq, lut);
  Candidate best{-std::numeric_limits<float>::infinity(), 0};
  std::unordered_set<std::uint64_t> pages;
  for (const auto& c : coarse) {
    const Candidate exact = best_int8(codes, scales, power_gains, query, c.id, nonlinear);
    if (exact.score > best.score || (exact.score == best.score && exact.id < best.id)) best = exact;
    const auto id = static_cast<std::uint64_t>(c.id);
    const auto code_begin = id * kDimension;
    const auto code_end = code_begin + kDimension - 1;
    for (auto page = code_begin / kPageBytes; page <= code_end / kPageBytes; ++page)
      pages.insert(page); // code-file namespace
    const auto scale_page = (id * sizeof(float)) / kPageBytes;
    pages.insert((1ULL << 48) | scale_page); // distinct scale-file namespace
  }
  const auto thq_scan_pages = (kDocuments * kThqBytes + kPageBytes - 1) / kPageBytes;
  return {best, static_cast<std::uint64_t>(thq_scan_pages), pages.size(), coarse};
}

template <typename CodeT>
std::vector<Candidate> exact_top10(const std::vector<CodeT>& codes,
                                   const std::vector<float>& scales,
                                   const std::vector<float>& power_gains,
                                   const float* query,
                                   const std::vector<std::int32_t>& ids,
                                   bool nonlinear) {
  std::vector<Candidate> all;
  all.reserve(ids.size());
  for (const auto id : ids)
    all.push_back(best_int8(codes, scales, power_gains, query, id, nonlinear));
  const auto limit = std::min<std::size_t>(10, all.size());
  std::partial_sort(all.begin(), all.begin() + limit, all.end(), better_desc);
  all.resize(limit);
  return all;
}

std::uint64_t namespaced_pages(const std::vector<std::int32_t>& ids,
                               std::size_t record_bytes,
                               std::uint64_t namespace_tag) {
  std::unordered_set<std::uint64_t> pages;
  for (const auto id : ids) {
    const auto begin = static_cast<std::uint64_t>(id) * record_bytes;
    const auto end = begin + record_bytes - 1;
    for (auto page = begin / kPageBytes; page <= end / kPageBytes; ++page)
      pages.insert(namespace_tag | page);
  }
  return pages.size();
}

std::uint64_t packed_lsq_pages(const LsqPayload& payload,
                               const std::vector<std::int32_t>& ids) {
  std::unordered_set<std::uint64_t> pages;
  for (const auto id : ids) {
    const auto position = std::lower_bound(payload.ids.begin(), payload.ids.end(), id);
    if (position == payload.ids.end() || *position != id)
      throw std::runtime_error("LSQ payload is missing page-accounting candidate");
    const auto row = static_cast<std::uint64_t>(position - payload.ids.begin());
    const auto code_begin = row * payload.stages;
    const auto code_end = code_begin + payload.stages - 1;
    for (auto page = code_begin / kPageBytes; page <= code_end / kPageBytes; ++page)
      pages.insert(page); // packed candidate-local code namespace
    pages.insert((1ULL << 48) | ((row * sizeof(float)) / kPageBytes));
  }
  return pages.size();
}

std::uint64_t lsq_model_pages(const LsqPayload& payload) {
  const auto model_bytes = payload.codebooks.size() * sizeof(float) +
                           payload.centroids.size() * sizeof(float);
  return (model_bytes + kPageBytes - 1) / kPageBytes;
}

std::uint64_t full_corpus_lsq_pages(const LsqPayload& payload) {
  const auto code_pages = (kDocuments * payload.stages + kPageBytes - 1) / kPageBytes;
  const auto norm_pages = (kDocuments * sizeof(float) + kPageBytes - 1) / kPageBytes;
  return static_cast<std::uint64_t>(code_pages + norm_pages);
}

double elapsed_ms(std::chrono::steady_clock::time_point begin,
                  std::chrono::steady_clock::time_point end);

LsqPayload read_lsq_payload(const std::string& path) {
  const auto bytes = read<std::uint8_t>(path);
  constexpr std::size_t kHeader = 20;
  if (bytes.size() < kHeader || std::memcmp(bytes.data(), "AMLSQ01", 7) != 0)
    throw std::runtime_error("invalid LSQ payload header");
  auto u32 = [&](std::size_t offset) {
    std::uint32_t value = 0;
    std::memcpy(&value, bytes.data() + offset, sizeof(value));
    return value;
  };
  const std::uint32_t stages = u32(8);
  const std::uint32_t dimensions = u32(12);
  const std::uint32_t count = u32(16);
  constexpr std::uint32_t kCodebookSize = 256;
  validate_lsq_header(stages, dimensions, count);
  const std::size_t ids_bytes = static_cast<std::size_t>(count) * sizeof(std::int32_t);
  const std::size_t codes_bytes = static_cast<std::size_t>(count) * stages;
  const std::size_t books_bytes = static_cast<std::size_t>(stages) * kCodebookSize * dimensions * sizeof(float);
  const std::size_t centroid_bytes = static_cast<std::size_t>(4) * dimensions * sizeof(float);
  const std::size_t norm_bytes = static_cast<std::size_t>(count) * sizeof(float);
  const std::size_t expected = kHeader + ids_bytes + codes_bytes + books_bytes + centroid_bytes + norm_bytes;
  if (bytes.size() != expected) throw std::runtime_error("LSQ payload size differs");
  LsqPayload out;
  out.stages = stages;
  out.dimensions = dimensions;
  out.ids.resize(count);
  out.codes.resize(static_cast<std::size_t>(count) * stages);
  out.codebooks.resize(static_cast<std::size_t>(stages) * kCodebookSize * dimensions);
  out.centroids.resize(static_cast<std::size_t>(4) * dimensions);
  out.norms.resize(count);
  std::size_t offset = kHeader;
  auto copy = [&](void* dst, std::size_t size) {
    std::memcpy(dst, bytes.data() + offset, size);
    offset += size;
  };
  copy(out.ids.data(), ids_bytes);
  copy(out.codes.data(), codes_bytes);
  copy(out.codebooks.data(), books_bytes);
  copy(out.centroids.data(), centroid_bytes);
  copy(out.norms.data(), norm_bytes);
  validate_lsq_ids(out.ids);
  if (!all_finite(out.codebooks) || !all_finite(out.centroids))
    throw std::runtime_error("LSQ payload model contains non-finite values");
  if (!std::all_of(out.norms.begin(), out.norms.end(), [](const float value) {
        return std::isfinite(value) && value > 0.0f;
      }))
    throw std::runtime_error("LSQ payload norms must be finite and positive");
  out.serialized_bytes = bytes.size();
  return out;
}

std::vector<DenseCandidate> exact_cosine_top10_lsq(
    const LsqPayload& payload, const std::vector<std::uint8_t>& thq,
    const std::vector<std::int32_t>& ids,
    const float* query) {
  double query_norm = 0.0;
  for (std::size_t d = 0; d < kDimension; ++d)
    query_norm += static_cast<double>(query[d]) * static_cast<double>(query[d]);
  query_norm = std::sqrt(std::max(query_norm, std::numeric_limits<double>::min()));
  std::vector<DenseCandidate> scored;
  scored.reserve(ids.size());
  for (const auto id : ids) {
    const auto position = std::lower_bound(payload.ids.begin(), payload.ids.end(), id);
    if (position == payload.ids.end() || *position != id)
      throw std::runtime_error("LSQ payload is missing candidate document");
    const auto row = static_cast<std::size_t>(position - payload.ids.begin());
    const auto* code = payload.codes.data() + row * payload.stages;
    double dot = 0.0;
    const auto* thq_row = thq.data() + static_cast<std::size_t>(id) * kThqBytes;
    for (std::size_t d = 0; d < kDimension; ++d) {
      const auto level = (thq_row[d / 4] >> ((d % 4) * 2)) & 3U;
      dot += static_cast<double>(payload.centroids[d * 4 + level]) * query[d];
    }
    for (std::size_t stage = 0; stage < payload.stages; ++stage) {
      const auto* book = payload.codebooks.data() +
          (stage * 256ULL + code[stage]) * kDimension;
      for (std::size_t d = 0; d < kDimension; ++d)
        dot += static_cast<double>(book[d]) * query[d];
    }
    const double denominator = std::max(static_cast<double>(payload.norms[row]) * query_norm,
                                        std::numeric_limits<double>::min());
    scored.push_back({dot / denominator, id});
  }
  const auto limit = std::min<std::size_t>(10, scored.size());
  std::partial_sort(scored.begin(), scored.begin() + limit, scored.end(), better_dense_desc);
  scored.resize(limit);
  return scored;
}

std::vector<std::size_t> lsq_rows(const LsqPayload& payload,
                                  const std::vector<std::int32_t>& ids) {
  std::vector<std::size_t> rows;
  rows.reserve(ids.size());
  for (const auto id : ids) {
    const auto position = std::lower_bound(payload.ids.begin(), payload.ids.end(), id);
    if (position == payload.ids.end() || *position != id)
      throw std::runtime_error("LSQ payload is missing candidate document");
    rows.push_back(static_cast<std::size_t>(position - payload.ids.begin()));
  }
  return rows;
}

std::vector<DenseCandidate> lsq_top10(const std::vector<std::int32_t>& ids,
                                      const std::vector<double>& scores) {
  if (ids.size() != scores.size()) throw std::runtime_error("LSQ score count differs");
  std::vector<DenseCandidate> ranked;
  ranked.reserve(ids.size());
  for (std::size_t i = 0; i < ids.size(); ++i) ranked.push_back({scores[i], ids[i]});
  const auto limit = std::min<std::size_t>(10, ranked.size());
  std::partial_sort(ranked.begin(), ranked.begin() + limit, ranked.end(),
                    better_dense_desc);
  ranked.resize(limit);
  return ranked;
}

std::vector<double> build_thq_dot_byte_lut(
    const LsqPayload& payload, const float* query) {
  std::vector<double> lut(kThqBytes * 256, 0.0);
  for (std::size_t byte = 0; byte < kThqBytes; ++byte) {
    for (std::size_t packed = 0; packed < 256; ++packed) {
      double dot = 0.0;
      for (std::size_t lane = 0; lane < 4; ++lane) {
        const auto dimension = byte * 4 + lane;
        const auto level = (packed >> (lane * 2)) & 3U;
        dot += static_cast<double>(payload.centroids[dimension * 4 + level]) *
               query[dimension];
      }
      lut[byte * 256 + packed] = dot;
    }
  }
  return lut;
}

LsqScoredRows score_lsq_lut(const LsqPayload& payload,
                            const std::vector<std::uint8_t>& thq,
                            const std::vector<std::int32_t>& ids,
                            const float* query, bool sparse) {
  const auto rows = lsq_rows(payload, ids);
  double query_norm = 0.0;
  for (std::size_t d = 0; d < kDimension; ++d)
    query_norm += static_cast<double>(query[d]) * query[d];
  query_norm = std::sqrt(std::max(query_norm, std::numeric_limits<double>::min()));

  const auto prepare_begin = std::chrono::steady_clock::now();
  const auto base_lut = build_thq_dot_byte_lut(payload, query);
  const double missing = std::numeric_limits<double>::quiet_NaN();
  std::vector<double> stage_lut(payload.stages * 256ULL, missing);
  for (std::size_t stage = 0; stage < payload.stages; ++stage) {
    std::array<bool, 256> used{};
    if (sparse) {
      for (const auto row : rows)
        used[payload.codes[row * payload.stages + stage]] = true;
    } else {
      used.fill(true);
    }
    for (std::size_t code = 0; code < 256; ++code) {
      if (!used[code]) continue;
      const auto* book = payload.codebooks.data() +
                         (stage * 256ULL + code) * kDimension;
      double dot = 0.0;
      for (std::size_t d = 0; d < kDimension; ++d)
        dot += static_cast<double>(book[d]) * query[d];
      stage_lut[stage * 256 + code] = dot;
    }
  }
  const auto prepare_end = std::chrono::steady_clock::now();

  LsqScoredRows result;
  result.scores.reserve(ids.size());
  const auto score_begin = std::chrono::steady_clock::now();
  for (std::size_t i = 0; i < ids.size(); ++i) {
    const auto id = ids[i];
    const auto row = rows[i];
    const auto* thq_row = thq.data() + static_cast<std::size_t>(id) * kThqBytes;
    const auto* code = payload.codes.data() + row * payload.stages;
    double dot = 0.0;
    for (std::size_t byte = 0; byte < kThqBytes; ++byte)
      dot += base_lut[byte * 256 + thq_row[byte]];
    for (std::size_t stage = 0; stage < payload.stages; ++stage) {
      const double contribution = stage_lut[stage * 256 + code[stage]];
      if (!std::isfinite(contribution))
        throw std::runtime_error("sparse LSQ LUT misses a used codeword");
      dot += contribution;
    }
    const double denominator = std::max(
        static_cast<double>(payload.norms[row]) * query_norm,
        std::numeric_limits<double>::min());
    result.scores.push_back(dot / denominator);
  }
  const auto score_end = std::chrono::steady_clock::now();
  result.top10 = lsq_top10(ids, result.scores);
  result.prepare_ms = elapsed_ms(prepare_begin, prepare_end);
  result.score_ms = elapsed_ms(score_begin, score_end);
  return result;
}

LsqScoredRows score_lsq_gather(const LsqPayload& payload,
                               const std::vector<std::uint8_t>& thq,
                               const std::vector<std::int32_t>& ids,
                               const float* query) {
  const auto begin = std::chrono::steady_clock::now();
  LsqScoredRows result;
  const auto rows = lsq_rows(payload, ids);
  double query_norm = 0.0;
  for (std::size_t d = 0; d < kDimension; ++d)
    query_norm += static_cast<double>(query[d]) * query[d];
  query_norm = std::sqrt(std::max(query_norm, std::numeric_limits<double>::min()));
  result.scores.reserve(ids.size());
  for (std::size_t i = 0; i < ids.size(); ++i) {
    const auto row = rows[i];
    const auto* code = payload.codes.data() + row * payload.stages;
    const auto* thq_row = thq.data() + static_cast<std::size_t>(ids[i]) * kThqBytes;
    double dot = 0.0;
    for (std::size_t d = 0; d < kDimension; ++d) {
      const auto level = (thq_row[d / 4] >> ((d % 4) * 2)) & 3U;
      dot += static_cast<double>(payload.centroids[d * 4 + level]) * query[d];
    }
    for (std::size_t stage = 0; stage < payload.stages; ++stage) {
      const auto* book = payload.codebooks.data() +
                         (stage * 256ULL + code[stage]) * kDimension;
      for (std::size_t d = 0; d < kDimension; ++d)
        dot += static_cast<double>(book[d]) * query[d];
    }
    result.scores.push_back(dot / std::max(
        static_cast<double>(payload.norms[row]) * query_norm,
        std::numeric_limits<double>::min()));
  }
  result.top10 = lsq_top10(ids, result.scores);
  result.score_ms = elapsed_ms(begin, std::chrono::steady_clock::now());
  return result;
}

// Full-corpus packed LSQ path.  The timed loop uses only the THQ byte LUT,
// stage code LUTs and a bounded top-10 heap; it never reconstructs FP32 rows
// or materializes a million Candidate objects.
LsqScoredRows score_lsq_full_flat(const LsqPayload& payload,
                                  const std::vector<std::uint8_t>& thq,
                                  const float* query) {
  if (payload.ids.size() != kDocuments || payload.ids.front() != 0 ||
      payload.ids.back() != static_cast<std::int32_t>(kDocuments - 1))
    throw std::runtime_error("full-flat LSQ payload must contain ordered corpus IDs");
  const auto prepare_begin = std::chrono::steady_clock::now();
  double query_norm = 0.0;
  for (std::size_t d = 0; d < kDimension; ++d)
    query_norm += static_cast<double>(query[d]) * query[d];
  query_norm = std::sqrt(std::max(query_norm, std::numeric_limits<double>::min()));
  const auto base_lut = build_thq_dot_byte_lut(payload, query);
  std::vector<double> stage_lut(payload.stages * 256ULL);
  for (std::size_t stage = 0; stage < payload.stages; ++stage) {
    for (std::size_t code = 0; code < 256; ++code) {
      const auto* book = payload.codebooks.data() +
          (stage * 256ULL + code) * kDimension;
      double dot = 0.0;
      for (std::size_t d = 0; d < kDimension; ++d)
        dot += static_cast<double>(book[d]) * query[d];
      stage_lut[stage * 256 + code] = dot;
    }
  }
  const auto prepare_end = std::chrono::steady_clock::now();
  FixedCandidateHeap<10, WorseDescendingCandidate> heap;
  const auto score_begin = std::chrono::steady_clock::now();
  for (std::size_t row = 0; row < kDocuments; ++row) {
    const auto* thq_row = thq.data() + row * kThqBytes;
    const auto* code = payload.codes.data() + row * payload.stages;
    double dot = 0.0;
    for (std::size_t byte = 0; byte < kThqBytes; ++byte)
      dot += base_lut[byte * 256 + thq_row[byte]];
    for (std::size_t stage = 0; stage < payload.stages; ++stage)
      dot += stage_lut[stage * 256 + code[stage]];
    const double score = dot / std::max(
        static_cast<double>(payload.norms[row]) * query_norm,
        std::numeric_limits<double>::min());
    const Candidate candidate{static_cast<float>(score),
                              static_cast<std::int32_t>(row)};
    if (heap.size() < 10) heap.push(candidate);
    else if (better_desc(candidate, heap.top())) heap.replace_top(candidate);
  }
  const auto score_end = std::chrono::steady_clock::now();
  LsqScoredRows result;
  result.top10.resize(10);
  for (std::size_t i = result.top10.size(); i-- > 0;)
  {
    const auto candidate = heap.pop_top();
    result.top10[i] = {static_cast<double>(candidate.score), candidate.id};
  }
  std::sort(result.top10.begin(), result.top10.end(), better_dense_desc);
  result.prepare_ms = elapsed_ms(prepare_begin, prepare_end);
  result.score_ms = elapsed_ms(score_begin, score_end);
  return result;
}

double percentile(std::vector<double> values, double fraction);

int run_lsq_full_flat(int argc, char** argv) {
  if (argc != 8 && argc != 9)
    throw std::runtime_error(
        "usage: benchmark --lsq-full-flat payload thq queries query_count warmups repeats [raw_output]");
  const auto payload = read_lsq_payload(argv[2]);
  const auto thq = read<std::uint8_t>(argv[3]);
  const auto queries = read<float>(argv[4]);
  const auto query_count = static_cast<std::size_t>(std::stoull(argv[5]));
  const auto warmups = static_cast<std::size_t>(std::stoull(argv[6]));
  const auto repeats = static_cast<std::size_t>(std::stoull(argv[7]));
  std::ofstream raw;
  if (argc == 9) { raw.open(argv[8]); if (!raw) throw std::runtime_error("cannot open LSQ raw output"); }
  if (thq.size() != kDocuments * kThqBytes || queries.size() < query_count * kDimension ||
      query_count == 0 || warmups == 0 || repeats == 0)
    throw std::runtime_error("LSQ full-flat fixture shape differs");
  std::vector<double> prepare, score, total;
  for (std::size_t qi = 0; qi < query_count; ++qi) {
    const float* query = queries.data() + qi * kDimension;
    LsqScoredRows last;
    for (std::size_t i = 0; i < warmups; ++i) last = score_lsq_full_flat(payload, thq, query);
    for (std::size_t i = 0; i < repeats; ++i) {
      const auto begin = std::chrono::steady_clock::now();
      last = score_lsq_full_flat(payload, thq, query);
      const auto end = std::chrono::steady_clock::now();
      prepare.push_back(last.prepare_ms); score.push_back(last.score_ms);
      total.push_back(elapsed_ms(begin, end));
      if (raw) {
        raw << "{\"query\":" << qi << ",\"repeat\":" << i << ",\"timing_ms\":" << total.back() << ",\"top10_ids\":[";
        for (std::size_t index = 0; index < last.top10.size(); ++index) { if (index) raw << ','; raw << last.top10[index].id; }
        raw << "]}\n";
      }
    }
    std::cout << "{\"query\":" << qi << ",\"top10_ids\":[";
    for (std::size_t i = 0; i < last.top10.size(); ++i) {
      if (i) std::cout << ',';
      std::cout << last.top10[i].id;
    }
    std::cout << "]}\n";
  }
  std::cerr << "{\"family\":\"native_lsq_full_flat_v1\",\"documents\":"
            << payload.ids.size() << ",\"queries\":" << query_count
            << ",\"repeats\":" << repeats << ",\"mean_ms\":"
            << std::accumulate(total.begin(), total.end(), 0.0) / total.size()
            << ",\"p50_ms\":" << percentile(total, .50)
            << ",\"p95_ms\":" << percentile(total, .95)
            << ",\"p99_ms\":" << percentile(total, .99) << "}\n";
  return 0;
}

int run_lsq_candidate_gate(int argc, char** argv) {
  if (argc != 10)
    throw std::runtime_error("usage: benchmark --lsq-candidate-gate thq thresholds model candidate_flat offsets query_file query_count payload_bytes");
  const auto thq = read<std::uint8_t>(argv[2]);
  const auto thresholds = read<float>(argv[3]);
  const auto payload = read_lsq_payload(argv[4]);
  const auto flat = read<std::uint8_t>(argv[5]);
  const auto offsets = read<std::uint64_t>(argv[6]);
  const std::size_t query_count = static_cast<std::size_t>(std::stoull(argv[8]));
  const std::size_t payload_bytes = static_cast<std::size_t>(std::stoull(argv[9]));
  if (thq.size() != kDocuments * kThqBytes || thresholds.size() != kDimension * 3 ||
      offsets.size() != query_count + 1 || offsets.front() != 0 ||
      payload_bytes != payload.stages + sizeof(float))
    throw std::runtime_error("LSQ candidate cascade payload shape differs");
  const auto queries = read<float>(argv[7]);
  if (queries.size() != query_count * kDimension)
    throw std::runtime_error("LSQ candidate query shape differs");
  std::vector<std::int32_t> candidate_ids;
  std::size_t candidate_record_bytes = 0;
  for (const std::size_t width : {std::size_t{100}, kCandidateRecordBytes})
    if (flat.size() % width == 0 && offsets.back() == flat.size() / width)
      candidate_record_bytes = width;
  if (candidate_record_bytes != 0) {
    candidate_ids.resize(flat.size() / candidate_record_bytes);
    for (std::size_t i = 0; i < candidate_ids.size(); ++i)
      std::memcpy(&candidate_ids[i], flat.data() + i * candidate_record_bytes,
                  sizeof(std::int32_t));
  } else if (flat.size() % sizeof(std::int32_t) == 0 &&
             offsets.back() == flat.size() / sizeof(std::int32_t)) {
    candidate_ids.resize(flat.size() / sizeof(std::int32_t));
    std::memcpy(candidate_ids.data(), flat.data(), flat.size());
  } else {
    throw std::runtime_error("LSQ candidate IDs do not match offsets");
  }
  for (const auto id : candidate_ids)
    if (id < 0 || id >= static_cast<std::int32_t>(kDocuments))
      throw std::runtime_error("LSQ candidate ID out of range");
  for (std::size_t qi = 0; qi < query_count; ++qi) {
    const auto begin = static_cast<std::size_t>(offsets[qi]);
    const auto end = static_cast<std::size_t>(offsets[qi + 1]);
    std::vector<std::int32_t> ids(candidate_ids.begin() + begin, candidate_ids.begin() + end);
    if (ids.empty()) throw std::runtime_error("LSQ candidate query is empty");
    const float* query = queries.data() + qi * kDimension;
    const auto thq_begin = std::chrono::steady_clock::now();
    const auto lut = build_lut(thresholds, query);
    const auto coarse = thq_top128_candidates(thq, lut, ids);
    const auto thq_end = std::chrono::steady_clock::now();
    std::vector<std::int32_t> coarse_ids;
    coarse_ids.reserve(coarse.size());
    for (const auto& candidate : coarse) coarse_ids.push_back(candidate.id);
    const auto codec_begin = std::chrono::steady_clock::now();
    const auto gather = score_lsq_gather(payload, thq, coarse_ids, query);
    const auto full_lut = score_lsq_lut(payload, thq, coarse_ids, query, false);
    const auto sparse_lut = score_lsq_lut(payload, thq, coarse_ids, query, true);
    const auto codec_end = std::chrono::steady_clock::now();
    if (gather.top10.size() != full_lut.top10.size() ||
        gather.top10.size() != sparse_lut.top10.size())
      throw std::runtime_error("LSQ scorer top10 cardinality differs");
    double full_error = 0.0, sparse_error = 0.0;
    for (std::size_t i = 0; i < gather.scores.size(); ++i) {
      full_error = std::max(full_error, std::abs(gather.scores[i] - full_lut.scores[i]));
      sparse_error = std::max(sparse_error, std::abs(gather.scores[i] - sparse_lut.scores[i]));
    }
    for (std::size_t i = 0; i < gather.top10.size(); ++i)
      if (gather.top10[i].id != full_lut.top10[i].id ||
          gather.top10[i].id != sparse_lut.top10[i].id)
        throw std::runtime_error("LSQ scorer ordered top10 parity differs");
    auto emit_ids = [](const auto& values) {
      std::cout << '[';
      for (std::size_t i = 0; i < values.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << values[i].id;
      }
      std::cout << ']';
    };
    const auto thq_pages = namespaced_pages(ids, kThqBytes, 1ULL << 47);
    const auto codec_pages = packed_lsq_pages(payload, coarse_ids);
    const auto model_pages = lsq_model_pages(payload);
    std::cout << "{\"query\":" << qi << ",\"candidate_count\":" << ids.size()
              << ",\"thq4_top128_ids\":";
    emit_ids(coarse);
    std::cout << ",\"top10_ids\":";
    emit_ids(gather.top10);
    std::cout << ",\"full_lut_top10_ids\":";
    emit_ids(full_lut.top10);
    std::cout << ",\"sparse_lut_top10_ids\":";
    emit_ids(sparse_lut.top10);
    std::cout << ",\"timing_ms\":{\"thq4_prefilter\":"
              << elapsed_ms(thq_begin, thq_end) << ",\"gather_dot\":"
              << gather.score_ms << ",\"full_lut_prepare\":" << full_lut.prepare_ms
              << ",\"full_lut_score\":" << full_lut.score_ms
              << ",\"sparse_lut_prepare\":" << sparse_lut.prepare_ms
              << ",\"sparse_lut_score\":" << sparse_lut.score_ms
              << ",\"all_codec_variants\":" << elapsed_ms(codec_begin, codec_end)
              << ",\"total\":"
              << elapsed_ms(thq_begin, codec_end) << "},\"thq_pages\":"
              << thq_pages << ",\"codec_pages\":" << codec_pages
              << ",\"model_pages\":" << model_pages
              << ",\"full_corpus_codec_pages\":" << full_corpus_lsq_pages(payload)
              << ",\"codec_layout\":\"candidate_local_packed_rows\""
              << ",\"logical_payload_bytes\":" << payload_bytes
              << ",\"max_abs_score_error\":{\"full_lut\":" << full_error
              << ",\"sparse_lut\":" << sparse_error << "}}\n";
  }
  std::cerr << "{\"queries\":" << query_count
            << ",\"timing_scope\":\"native THQ byte-LUT plus direct compressed LSQ cosine scorer; FP32 final norm sidecar included\"}\n";
  return 0;
}

template <typename CodeT>
std::uint64_t exact_payload_pages(const std::vector<std::int32_t>& ids) {
  return namespaced_pages(ids, kDimension, 0) +
         namespaced_pages(ids, sizeof(float), 1ULL << 48);
}

double elapsed_ms(std::chrono::steady_clock::time_point begin,
                  std::chrono::steady_clock::time_point end) {
  return std::chrono::duration<double, std::milli>(end - begin).count();
}

double percentile(std::vector<double> values, double fraction) {
  if (values.empty()) throw std::runtime_error("cannot summarize empty timing series");
  std::sort(values.begin(), values.end());
  const double position = fraction * static_cast<double>(values.size() - 1);
  const auto lower = static_cast<std::size_t>(position);
  const auto upper = std::min(lower + 1, values.size() - 1);
  const double weight = position - static_cast<double>(lower);
  return values[lower] * (1.0 - weight) + values[upper] * weight;
}

int run_int8_full_flat(int argc, char** argv) {
  if (argc != 8)
    throw std::runtime_error("usage: benchmark --int8-full-flat codes scales queries warmups repeats raw_output");
  const auto codes = read<std::int8_t>(argv[2]); const auto scales = read<float>(argv[3]);
  const auto queries = read<float>(argv[4]); const auto warmups = static_cast<std::size_t>(std::stoull(argv[5]));
  const auto repeats = static_cast<std::size_t>(std::stoull(argv[6])); std::ofstream raw(argv[7]);
  if (codes.size() != kDocuments * kDimension || scales.size() != kDocuments || queries.size() < 152 * kDimension || !raw || warmups == 0 || repeats == 0)
    throw std::runtime_error("INT8 full-flat fixture shape differs");
  std::vector<double> timings; timings.reserve(152 * repeats);
  for (std::size_t qi = 0; qi < 152; ++qi) {
    const float* query = queries.data() + qi * kDimension;
    for (std::size_t rep = 0; rep < warmups + repeats; ++rep) {
      const auto begin = std::chrono::steady_clock::now(); const auto top = direct_top10_production(codes, scales, query);
      if (rep >= warmups) {
        const double elapsed = elapsed_ms(begin, std::chrono::steady_clock::now()); timings.push_back(elapsed);
        raw << "{\"query\":" << qi << ",\"repeat\":" << (rep - warmups) << ",\"timing_ms\":" << elapsed << ",\"top10_ids\":[";
        for (std::size_t index = 0; index < top.size(); ++index) { if (index) raw << ','; raw << top[index].id; }
        raw << "]}\n";
      }
    }
  }
  std::cout << std::fixed << std::setprecision(6) << "{\"status\":\"EXECUTED\",\"codec\":\"INT8\",\"metric\":\"reconstructed_cosine\",\"mode\":\"full_flat_1m\",\"queries\":152,\"repeats\":" << repeats << ",\"p50_ms\":" << percentile(timings, .5) << ",\"p95_ms\":" << percentile(timings, .95) << ",\"p99_ms\":" << percentile(timings, .99) << "}\n";
  return 0;
}

double query_median_percentile(const std::vector<double>& values,
                               std::size_t query_count, std::size_t repeats,
                               double fraction) {
  if (values.size() != query_count * repeats || repeats == 0)
    throw std::runtime_error("timing series shape differs for query medians");
  std::vector<double> medians;
  medians.reserve(query_count);
  for (std::size_t query = 0; query < query_count; ++query) {
    std::vector<double> samples(values.begin() + query * repeats,
                                values.begin() + (query + 1) * repeats);
    medians.push_back(percentile(std::move(samples), 0.5));
  }
  return percentile(std::move(medians), fraction);
}

void emit_timing_summary(const char* name, const std::vector<double>& values,
                         std::size_t query_count, std::size_t repeats) {
  const double sum = std::accumulate(values.begin(), values.end(), 0.0);
  std::cerr << '"' << name << "\":{\"mean_ms\":" << sum / values.size()
            << ",\"p50_ms\":" << percentile(values, 0.50)
            << ",\"p95_ms\":" << percentile(values, 0.95)
            << ",\"p99_ms\":" << percentile(values, 0.99)
            << ",\"query_median_p50_ms\":" << query_median_percentile(values, query_count, repeats, 0.50)
            << ",\"query_median_p95_ms\":" << query_median_percentile(values, query_count, repeats, 0.95)
            << ",\"query_median_p99_ms\":" << query_median_percentile(values, query_count, repeats, 0.99)
            << '}';
}

// Legacy benchmark modes report one sample per query and are not grouped into
// repeated per-query measurements. Keep their compact summary contract rather
// than manufacturing query-median percentiles from an incompatible shape.
void emit_timing_summary(const char* name, const std::vector<double>& values) {
  if (values.empty()) throw std::runtime_error("cannot summarize empty timing series");
  const double sum = std::accumulate(values.begin(), values.end(), 0.0);
  std::cerr << '"' << name << "\":{\"mean_ms\":" << sum / values.size()
            << ",\"p50_ms\":" << percentile(values, 0.50)
            << ",\"p95_ms\":" << percentile(values, 0.95)
            << ",\"p99_ms\":" << percentile(values, 0.99)
            << '}';
}

void emit_samples(const std::vector<double>& values) {
  std::cout << '[';
  for (std::size_t i = 0; i < values.size(); ++i) {
    if (i != 0) std::cout << ',';
    std::cout << values[i];
  }
  std::cout << ']';
}

int run_production_control(int argc, char** argv) {
  if (argc != 13)
    throw std::runtime_error(
        "usage: benchmark --production-control thq thresholds linear linear_scales power power_scales query_file query_count warmups repeats seed");
  const auto thq = read<std::uint8_t>(argv[2]);
  const auto thresholds = read<float>(argv[3]);
  const auto linear = read<std::int8_t>(argv[4]);
  const auto linear_scales = read<float>(argv[5]);
  const std::size_t query_count = static_cast<std::size_t>(std::stoull(argv[9]));
  const std::size_t warmups = static_cast<std::size_t>(std::stoull(argv[10]));
  const std::size_t repeats = static_cast<std::size_t>(std::stoull(argv[11]));
  const std::uint32_t seed = static_cast<std::uint32_t>(std::stoul(argv[12]));
  if (query_count == 0 || warmups == 0 || repeats == 0 ||
      thq.size() != kDocuments * kThqBytes || thresholds.size() != kDimension * 3 ||
      linear.size() != kDocuments * kDimension ||
      linear_scales.size() != kDocuments ||
      file_bytes(argv[6]) != kDocuments * kDimension ||
      file_bytes(argv[7]) != kDocuments * sizeof(float))
    throw std::runtime_error("production control payload shape differs");
  const auto queries = read<float>(argv[8]);
  if (queries.size() < query_count * kDimension)
    throw std::runtime_error("production control query payload is shorter than query_count");

  const auto thq_block32 = pack_thq_block32(thq);
  std::vector<float> thq_scores(thq_block32.padded_documents);
  std::vector<float> direct_scores(kDocuments);
  std::vector<double> direct_prepare_ms, direct_score_ms, direct_topk_ms,
      direct_total_ms;
  std::vector<double> cascade_prepare_ms, cascade_score_ms,
      cascade_topk_ms, cascade_rerank_ms, cascade_total_ms;
  for (auto* values : {&direct_prepare_ms, &direct_score_ms, &direct_topk_ms,
                       &direct_total_ms,
                       &cascade_prepare_ms, &cascade_score_ms,
                       &cascade_topk_ms, &cascade_rerank_ms,
                       &cascade_total_ms})
    values->reserve(query_count * repeats);
  const auto emit_ids = [](const auto& values) {
    std::cout << '[';
    for (std::size_t i = 0; i < values.size(); ++i) {
      if (i) std::cout << ',';
      std::cout << values[i].id;
    }
    std::cout << ']';
  };
  for (std::size_t qi = 0; qi < query_count; ++qi) {
    const float* query = queries.data() + qi * kDimension;
    std::array<Candidate, 10> last_direct{}, last_cascade{};
    std::array<Candidate, 128> last_coarse{};
    std::vector<double> query_direct_prepare, query_direct_score,
        query_direct_topk, query_direct_total;
    std::vector<double> query_cascade_prepare, query_cascade_score,
        query_cascade_topk, query_cascade_rerank, query_cascade_total;
    for (auto* values : {&query_direct_prepare, &query_direct_score,
                         &query_direct_topk,
                         &query_direct_total, &query_cascade_prepare,
                         &query_cascade_score, &query_cascade_topk,
                         &query_cascade_rerank, &query_cascade_total})
      values->reserve(repeats);
    for (std::size_t warmup = 0; warmup < warmups; ++warmup) {
      score_int8_dense(linear, linear_scales, query, direct_scores);
      last_direct = bounded_top_from_scores<10, WorseDescendingCandidate>(
          direct_scores, kDocuments, better_desc);
      const QueryLut lut = build_lut(thresholds, query);
      score_thq_block32(thq_block32, lut, thq_scores);
      last_coarse = bounded_top_from_scores<128, WorseCandidate>(
          thq_scores, kDocuments, better);
      last_cascade = rerank_top10_production(linear, linear_scales, query, last_coarse);
    }
    for (std::size_t repeat = 0; repeat < repeats; ++repeat) {
      std::array<int, 2> order{0, 1};
      std::mt19937 rng(seed ^ static_cast<std::uint32_t>(qi * 0x9e3779b9U + repeat));
      std::shuffle(order.begin(), order.end(), rng);
      for (const int arm : order) {
        if (arm == 0) {
          const auto total_begin = std::chrono::steady_clock::now();
          score_int8_dense(linear, linear_scales, query, direct_scores);
          const auto score_end = std::chrono::steady_clock::now();
          last_direct = bounded_top_from_scores<10, WorseDescendingCandidate>(
              direct_scores, kDocuments, better_desc);
          const auto topk_end = std::chrono::steady_clock::now();
          query_direct_prepare.push_back(0.0);
          query_direct_score.push_back(elapsed_ms(total_begin, score_end));
          query_direct_topk.push_back(elapsed_ms(score_end, topk_end));
          query_direct_total.push_back(elapsed_ms(total_begin, topk_end));
          direct_prepare_ms.push_back(0.0);
          direct_score_ms.push_back(query_direct_score.back());
          direct_topk_ms.push_back(query_direct_topk.back());
          direct_total_ms.push_back(query_direct_total.back());
        } else {
          const auto total_begin = std::chrono::steady_clock::now();
          const QueryLut lut = build_lut(thresholds, query);
          const auto prepare_end = std::chrono::steady_clock::now();
          score_thq_block32(thq_block32, lut, thq_scores);
          const auto score_end = std::chrono::steady_clock::now();
          last_coarse = bounded_top_from_scores<128, WorseCandidate>(
              thq_scores, kDocuments, better);
          const auto topk_end = std::chrono::steady_clock::now();
          last_cascade = rerank_top10_production(linear, linear_scales, query, last_coarse);
          const auto rerank_end = std::chrono::steady_clock::now();
          query_cascade_prepare.push_back(elapsed_ms(total_begin, prepare_end));
          query_cascade_score.push_back(elapsed_ms(prepare_end, score_end));
          query_cascade_topk.push_back(elapsed_ms(score_end, topk_end));
          query_cascade_rerank.push_back(elapsed_ms(topk_end, rerank_end));
          query_cascade_total.push_back(elapsed_ms(total_begin, rerank_end));
          cascade_prepare_ms.push_back(query_cascade_prepare.back());
          cascade_score_ms.push_back(query_cascade_score.back());
          cascade_topk_ms.push_back(query_cascade_topk.back());
          cascade_rerank_ms.push_back(query_cascade_rerank.back());
          cascade_total_ms.push_back(query_cascade_total.back());
        }
      }
    }
    if (last_direct.size() != last_cascade.size())
      throw std::runtime_error("production top10 cardinality differs");
    std::vector<std::int32_t> coarse_ids;
    coarse_ids.reserve(last_coarse.size());
    for (const auto& candidate : last_coarse) coarse_ids.push_back(candidate.id);
    const auto code_pages = namespaced_pages(coarse_ids, kDimension, 1ULL << 48);
    const auto scale_pages = namespaced_pages(coarse_ids, sizeof(float), 1ULL << 49);
    const auto scalar_coarse = thq_top128_bounded(thq, build_lut(thresholds, query));
    bool thq_parity = true;
    for (std::size_t i = 0; i < last_coarse.size(); ++i)
      thq_parity = thq_parity && last_coarse[i].id == scalar_coarse[i].id;
    FixedCandidateHeap<10, WorseDescendingCandidate> scalar_heap;
    double max_abs_error = 0.0;
    double max_relative_error = 0.0;
    for (std::size_t id = 0; id < kDocuments; ++id) {
      const float scalar = score_int8_float_scalar(
          linear.data() + id * kDimension, linear_scales[id], query);
      const float production = direct_scores[id];
      const double absolute = std::abs(static_cast<double>(production) - scalar);
      max_abs_error = std::max(max_abs_error, absolute);
      max_relative_error = std::max(
          max_relative_error,
          absolute / std::max(std::abs(static_cast<double>(scalar)), 1e-12));
      const Candidate candidate{scalar, static_cast<std::int32_t>(id)};
      if (scalar_heap.size() < 10) scalar_heap.push(candidate);
      else if (better_desc(candidate, scalar_heap.top()))
        scalar_heap.replace_top(candidate);
    }
    std::array<Candidate, 10> scalar_top10{};
    for (std::size_t i = scalar_top10.size(); i-- > 0;)
      scalar_top10[i] = scalar_heap.pop_top();
    std::sort(scalar_top10.begin(), scalar_top10.end(), better_desc);
    bool int8_top10_parity = true;
    bool cascade_top10_parity = true;
    for (std::size_t i = 0; i < last_direct.size(); ++i) {
      int8_top10_parity = int8_top10_parity &&
                          last_direct[i].id == scalar_top10[i].id;
      cascade_top10_parity = cascade_top10_parity &&
                             last_direct[i].id == last_cascade[i].id;
    }
    std::cout << "{\"query\":" << qi << ",\"direct_top10\":";
    emit_ids(last_direct);
    std::cout << ",\"cascade_top10\":";
    emit_ids(last_cascade);
    std::cout << ",\"cascade_thq_top128_ids\":";
    emit_ids(last_coarse);
    std::cout << ",\"page_proxy\":{\"thq_scan\":"
              << ((kDocuments * kThqBytes + kPageBytes - 1) / kPageBytes)
              << ",\"rerank_codes\":" << code_pages
              << ",\"rerank_scales\":" << scale_pages
              << ",\"rerank_payload\":" << code_pages + scale_pages
              << "},\"parity\":{\"thq_block32_vs_unrolled\":"
              << (thq_parity ? "true" : "false")
              << ",\"int8_avx2_vs_scalar_top10\":"
              << (int8_top10_parity ? "true" : "false")
              << ",\"direct_vs_cascade_top10\":"
              << (cascade_top10_parity ? "true" : "false")
              << "},\"int8_score_error\":{\"max_absolute\":"
              << max_abs_error << ",\"max_relative\":" << max_relative_error
              << "},\"timing_ms\":{\"direct\":{\"prepare\":";
    emit_samples(query_direct_prepare);
    std::cout << ",\"score\":";
    emit_samples(query_direct_score);
    std::cout << ",\"topk\":";
    emit_samples(query_direct_topk);
    std::cout << ",\"total\":";
    emit_samples(query_direct_total);
    std::cout << "},\"cascade\":{\"prepare\":";
    emit_samples(query_cascade_prepare);
    std::cout << ",\"score\":";
    emit_samples(query_cascade_score);
    std::cout << ",\"topk\":";
    emit_samples(query_cascade_topk);
    std::cout << ",\"rerank\":";
    emit_samples(query_cascade_rerank);
    std::cout << ",\"total\":";
    emit_samples(query_cascade_total);
    std::cout << "}}}\n";
  }
  std::cerr << "{\"queries\":" << query_count
            << ",\"warmups\":" << warmups << ",\"repeats\":" << repeats
            << ",\"arm_order\":\"per-query randomized with fixed seed\""
            << ",\"timing_scope\":\"query preparation, scoring, bounded top-k and rerank separated; page and parity audits outside timed path\""
            << ",\"thq_dense_kernel\":\"exact byte-LUT AVX2 gather block32 when enabled, scalar fallback otherwise\""
            << ",\"thq_sparse_kernel\":\"exact doc-major byte-LUT unrolled4\""
            << ",\"int8_kernel\":\"exact float-query AVX2 register accumulation when enabled, scalar fallback otherwise\",\"arms\":{";
  std::cerr << "\"direct\":{";
  emit_timing_summary("prepare", direct_prepare_ms, query_count, repeats);
  std::cerr << ',';
  emit_timing_summary("score", direct_score_ms, query_count, repeats);
  std::cerr << ',';
  emit_timing_summary("topk", direct_topk_ms, query_count, repeats);
  std::cerr << ',';
  emit_timing_summary("total", direct_total_ms, query_count, repeats);
  std::cerr << "},\"cascade\":{";
  emit_timing_summary("prepare", cascade_prepare_ms, query_count, repeats);
  std::cerr << ',';
  emit_timing_summary("score", cascade_score_ms, query_count, repeats);
  std::cerr << ',';
  emit_timing_summary("topk", cascade_topk_ms, query_count, repeats);
  std::cerr << ',';
  emit_timing_summary("rerank", cascade_rerank_ms, query_count, repeats);
  std::cerr << ',';
  emit_timing_summary("total", cascade_total_ms, query_count, repeats);
  std::cerr << "}}}\n";
  return 0;
}

int run_candidate_gate(int argc, char** argv) {
  if (argc != 12)
    throw std::runtime_error("usage: benchmark --candidate-gate thq thresholds linear linear_scales power power_scales candidate_flat offsets query_file query_count");
  const auto thq = read<std::uint8_t>(argv[2]);
  const auto thresholds = read<float>(argv[3]);
  const auto linear = read<std::int8_t>(argv[4]);
  const auto linear_scales = read<float>(argv[5]);
  const auto power = read<std::int8_t>(argv[6]);
  const auto power_scales = read<float>(argv[7]);
  const auto flat = read<std::uint8_t>(argv[8]);
  const auto offsets = read<std::uint64_t>(argv[9]);
  const std::size_t q = static_cast<std::size_t>(std::stoull(argv[11]));
  if (thq.size() != kDocuments * kThqBytes || thresholds.size() != kDimension * 3 ||
      linear.size() != kDocuments * kDimension || power.size() != kDocuments * kDimension ||
      linear_scales.size() != kDocuments || power_scales.size() != kDocuments ||
      flat.size() % kCandidateRecordBytes != 0 || offsets.size() != q + 1 || offsets.front() != 0 ||
      offsets.back() != flat.size() / kCandidateRecordBytes)
    throw std::runtime_error("candidate gate payload shape differs");
  if (q == 0) throw std::runtime_error("candidate gate query count is zero");
  for (std::size_t i = 1; i < offsets.size(); ++i)
    if (offsets[i] < offsets[i - 1]) throw std::runtime_error("candidate offsets decrease");
  std::vector<std::int32_t> candidate_ids(flat.size() / kCandidateRecordBytes);
  for (std::size_t i = 0; i < candidate_ids.size(); ++i) {
    std::int32_t id = 0;
    std::memcpy(&id, flat.data() + i * kCandidateRecordBytes, sizeof(id));
    if (id < 0 || id >= static_cast<std::int32_t>(kDocuments))
      throw std::runtime_error("candidate id out of range");
    candidate_ids[i] = id;
  }
  std::vector<float> linear_gains(kDocuments, 1.0f);
  std::vector<float> power_gains(kDocuments);
  for (std::size_t id = 0; id < kDocuments; ++id)
    power_gains[id] = std::pow(power_scales[id], 1.6f);
  std::ifstream query_stream(argv[10], std::ios::binary);
  if (!query_stream) throw std::runtime_error("cannot open candidate query payload");
  std::vector<float> queries(q * kDimension);
  query_stream.read(reinterpret_cast<char*>(queries.data()),
                    static_cast<std::streamsize>(queries.size() * sizeof(float)));
  if (!query_stream) throw std::runtime_error("candidate query payload is truncated");
  std::vector<double> direct_linear_ms, direct_power_ms, thq_prefilter_ms;
  std::vector<double> linear_rerank_ms, power_rerank_ms;
  std::vector<double> cascade_linear_ms, cascade_power_ms;
  for (auto* values : {&direct_linear_ms, &direct_power_ms, &thq_prefilter_ms,
                       &linear_rerank_ms, &power_rerank_ms,
                       &cascade_linear_ms, &cascade_power_ms})
    values->reserve(q);
  for (std::size_t qi = 0; qi < q; ++qi) {
    const auto begin_id = static_cast<std::size_t>(offsets[qi]);
    const auto end_id = static_cast<std::size_t>(offsets[qi + 1]);
    std::vector<std::int32_t> ids(candidate_ids.begin() + begin_id,
                                  candidate_ids.begin() + end_id);
    if (ids.size() < 5000 || ids.size() > 5099)
      throw std::runtime_error("candidate count outside frozen 5000..5099 contract");
    std::unordered_set<std::int32_t> unique(ids.begin(), ids.end());
    if (unique.size() != ids.size())
      throw std::runtime_error("candidate IDs are duplicated within a query");
    const float* query = queries.data() + qi * kDimension;
    const auto direct_linear_begin = std::chrono::steady_clock::now();
    const auto direct_linear = exact_top10(linear, linear_scales, linear_gains,
                                           query, ids, false);
    const auto direct_linear_end = std::chrono::steady_clock::now();
    const auto direct_power_begin = direct_linear_end;
    const auto direct_power = exact_top10(power, power_scales, power_gains,
                                          query, ids, true);
    const auto direct_power_end = std::chrono::steady_clock::now();
    const auto thq_begin = direct_power_end;
    const auto lut = build_lut(thresholds, query);
    const auto coarse = thq_top128_candidates(thq, lut, ids);
    const auto thq_end = std::chrono::steady_clock::now();
    std::vector<std::int32_t> coarse_ids;
    coarse_ids.reserve(coarse.size());
    for (const auto& candidate : coarse) coarse_ids.push_back(candidate.id);
    const auto linear_rerank_begin = std::chrono::steady_clock::now();
    const auto cascade_linear = exact_top10(linear, linear_scales, linear_gains,
                                            query, coarse_ids, false);
    const auto linear_rerank_end = std::chrono::steady_clock::now();
    const auto power_rerank_begin = linear_rerank_end;
    const auto cascade_power = exact_top10(power, power_scales, power_gains,
                                           query, coarse_ids, true);
    const auto power_rerank_end = std::chrono::steady_clock::now();
    const double query_direct_linear_ms = elapsed_ms(direct_linear_begin, direct_linear_end);
    const double query_direct_power_ms = elapsed_ms(direct_power_begin, direct_power_end);
    const double query_thq_ms = elapsed_ms(thq_begin, thq_end);
    const double query_linear_rerank_ms = elapsed_ms(linear_rerank_begin, linear_rerank_end);
    const double query_power_rerank_ms = elapsed_ms(power_rerank_begin, power_rerank_end);
    direct_linear_ms.push_back(query_direct_linear_ms);
    direct_power_ms.push_back(query_direct_power_ms);
    thq_prefilter_ms.push_back(query_thq_ms);
    linear_rerank_ms.push_back(query_linear_rerank_ms);
    power_rerank_ms.push_back(query_power_rerank_ms);
    cascade_linear_ms.push_back(query_thq_ms + query_linear_rerank_ms);
    cascade_power_ms.push_back(query_thq_ms + query_power_rerank_ms);
    auto emit = [](const std::vector<Candidate>& values) {
      std::cout << '[';
      for (std::size_t i = 0; i < values.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << values[i].id;
      }
      std::cout << ']';
    };
    std::cout << "{\"query\":" << qi << ",\"candidate_count\":" << ids.size()
              << ",\"direct_linear_top10\":"; emit(direct_linear);
    std::cout << ",\"cascade_linear_top10\":"; emit(cascade_linear);
    std::cout << ",\"direct_power0625_top10\":"; emit(direct_power);
    std::cout << ",\"cascade_power0625_top10\":"; emit(cascade_power);
    std::cout << ",\"cascade_thq_top128_ids\":"; emit(coarse);
    std::cout << ",\"cascade_thq_top128_scores\":[" << std::setprecision(9);
    for (std::size_t i = 0; i < coarse.size(); ++i) {
      if (i) std::cout << ',';
      std::cout << coarse[i].score;
    }
    std::cout << ']';
    std::cout << ",\"direct_payload_pages\":" << exact_payload_pages<std::int8_t>(ids)
              << ",\"cascade_thq_pages\":" << namespaced_pages(ids, kThqBytes, 1ULL << 49)
              << ",\"cascade_payload_pages\":" << exact_payload_pages<std::int8_t>(coarse_ids)
              << ",\"latency_ms\":{\"direct_linear\":" << query_direct_linear_ms
              << ",\"direct_power0625\":" << query_direct_power_ms
              << ",\"thq4_prefilter\":" << query_thq_ms
              << ",\"linear_top128_rerank\":" << query_linear_rerank_ms
              << ",\"power0625_top128_rerank\":" << query_power_rerank_ms
              << ",\"cascade_linear_total\":" << query_thq_ms + query_linear_rerank_ms
              << ",\"cascade_power0625_total\":" << query_thq_ms + query_power_rerank_ms
              << "}}\n";
  }
  std::cerr << "{\"queries\":" << q
            << ",\"timing_scope\":\"native_scalar_candidate_gate; no OS-page latency claim\""
            << ",\"percentile_method\":\"linear interpolation over per-query samples\",\"arms\":{";
  emit_timing_summary("direct_linear", direct_linear_ms); std::cerr << ',';
  emit_timing_summary("direct_power0625", direct_power_ms); std::cerr << ',';
  emit_timing_summary("thq4_prefilter", thq_prefilter_ms); std::cerr << ',';
  emit_timing_summary("linear_top128_rerank", linear_rerank_ms); std::cerr << ',';
  emit_timing_summary("power0625_top128_rerank", power_rerank_ms); std::cerr << ',';
  emit_timing_summary("cascade_linear_total", cascade_linear_ms); std::cerr << ',';
  emit_timing_summary("cascade_power0625_total", cascade_power_ms);
  std::cerr << "}}\n";
  return 0;
}

std::vector<Candidate> exact_cosine_top10(const std::vector<float>& documents,
                                          const std::vector<std::int32_t>& ids,
                                          const float* query) {
  float query_norm = 0.0f;
  for (std::size_t d = 0; d < kDimension; ++d)
    query_norm += query[d] * query[d];
  query_norm = std::sqrt(std::max(query_norm, std::numeric_limits<float>::min()));
  std::vector<Candidate> scored;
  scored.reserve(ids.size());
  for (const auto id : ids) {
    const auto* row = documents.data() + static_cast<std::size_t>(id) * kDimension;
    float dot = 0.0f;
    float norm = 0.0f;
    for (std::size_t d = 0; d < kDimension; ++d) {
      dot += row[d] * query[d];
      norm += row[d] * row[d];
    }
    const float denominator = std::max(std::sqrt(norm) * query_norm,
                                       std::numeric_limits<float>::min());
    scored.push_back({dot / denominator, id});
  }
  const auto limit = std::min<std::size_t>(10, scored.size());
  std::partial_sort(scored.begin(), scored.begin() + limit, scored.end(), better_desc);
  scored.resize(limit);
  return scored;
}

std::vector<Candidate> exact_cosine_top10_int8(
    const std::vector<std::int8_t>& codes, const std::vector<float>& scales,
    const std::vector<std::int32_t>& ids, const float* query) {
  float query_norm = 0.0f;
  for (std::size_t d = 0; d < kDimension; ++d)
    query_norm += query[d] * query[d];
  query_norm = std::sqrt(std::max(query_norm, std::numeric_limits<float>::min()));
  std::vector<Candidate> scored;
  scored.reserve(ids.size());
  for (const auto id : ids) {
    const auto index = static_cast<std::size_t>(id);
    const auto* row = codes.data() + index * kDimension;
    const float scale = scales[index];
    float dot = 0.0f;
    float norm = 0.0f;
    for (std::size_t d = 0; d < kDimension; ++d) {
      const float value = static_cast<float>(row[d]) * scale;
      dot += value * query[d];
      norm += value * value;
    }
    const float denominator = std::max(std::sqrt(norm) * query_norm,
                                       std::numeric_limits<float>::min());
    scored.push_back({dot / denominator, id});
  }
  const auto limit = std::min<std::size_t>(10, scored.size());
  std::partial_sort(scored.begin(), scored.begin() + limit, scored.end(), better_desc);
  scored.resize(limit);
  return scored;
}

std::vector<DenseCandidate> exact_cosine_top10_dense(
    const std::vector<float>& vectors, const std::vector<std::int32_t>& ids,
    const std::vector<std::int32_t>& selected_ids, const float* query) {
  double query_norm = 0.0;
  for (std::size_t d = 0; d < kDimension; ++d)
    query_norm += static_cast<double>(query[d]) * static_cast<double>(query[d]);
  query_norm = std::sqrt(std::max(query_norm, std::numeric_limits<double>::min()));
  std::vector<DenseCandidate> scored;
  scored.reserve(ids.size());
  for (const auto id : ids) {
    const auto position = std::lower_bound(selected_ids.begin(), selected_ids.end(), id);
    if (position == selected_ids.end() || *position != id)
      throw std::runtime_error("dense codec payload is missing candidate document");
    const auto row = static_cast<std::size_t>(position - selected_ids.begin());
    const auto* vector = vectors.data() + row * kDimension;
    double dot = 0.0;
    double norm = 0.0;
    for (std::size_t d = 0; d < kDimension; ++d) {
      dot += static_cast<double>(vector[d]) * static_cast<double>(query[d]);
      norm += static_cast<double>(vector[d]) * static_cast<double>(vector[d]);
    }
    const double denominator = std::max(std::sqrt(norm) * query_norm,
                                        std::numeric_limits<double>::min());
    scored.push_back({dot / denominator, id});
  }
  const auto limit = std::min<std::size_t>(10, scored.size());
  std::partial_sort(scored.begin(), scored.begin() + limit, scored.end(),
                    better_dense_desc);
  scored.resize(limit);
  return scored;
}

int run_dense_candidate_gate(int argc, char** argv) {
  if (argc != 11)
    throw std::runtime_error("usage: benchmark --dense-candidate-gate thq thresholds codec_ids codec_vectors candidate_flat offsets query_file query_count payload_bytes");
  const auto thq = read<std::uint8_t>(argv[2]);
  const auto thresholds = read<float>(argv[3]);
  const auto selected_ids = read<std::int32_t>(argv[4]);
  const auto vectors = read<float>(argv[5]);
  const auto flat = read<std::uint8_t>(argv[6]);
  const auto offsets = read<std::uint64_t>(argv[7]);
  const std::size_t query_count = static_cast<std::size_t>(std::stoull(argv[9]));
  const std::size_t payload_bytes = static_cast<std::size_t>(std::stoull(argv[10]));
  if (thq.size() != kDocuments * kThqBytes || thresholds.size() != kDimension * 3 ||
      selected_ids.empty() || vectors.size() != selected_ids.size() * kDimension ||
      flat.size() % kCandidateRecordBytes != 0 || offsets.size() != query_count + 1 ||
      offsets.front() != 0 || offsets.back() != flat.size() / kCandidateRecordBytes ||
      payload_bytes == 0)
    throw std::runtime_error("dense candidate cascade payload shape differs");
  if (!std::is_sorted(selected_ids.begin(), selected_ids.end()))
    throw std::runtime_error("dense codec IDs must be sorted");
  const auto queries = read<float>(argv[8]);
  if (queries.size() != query_count * kDimension)
    throw std::runtime_error("dense candidate query shape differs");
  std::vector<std::int32_t> candidate_ids(flat.size() / kCandidateRecordBytes);
  for (std::size_t i = 0; i < candidate_ids.size(); ++i) {
    std::int32_t id = 0;
    std::memcpy(&id, flat.data() + i * kCandidateRecordBytes, sizeof(id));
    if (id < 0 || id >= static_cast<std::int32_t>(kDocuments))
      throw std::runtime_error("dense candidate ID out of range");
    candidate_ids[i] = id;
  }
  for (std::size_t i = 1; i < selected_ids.size(); ++i)
    if (selected_ids[i] == selected_ids[i - 1])
      throw std::runtime_error("dense codec IDs contain duplicates");
  for (std::size_t qi = 0; qi < query_count; ++qi) {
    const auto begin = static_cast<std::size_t>(offsets[qi]);
    const auto end = static_cast<std::size_t>(offsets[qi + 1]);
    std::vector<std::int32_t> ids(candidate_ids.begin() + begin,
                                  candidate_ids.begin() + end);
    if (ids.empty()) throw std::runtime_error("dense candidate query is empty");
    const float* query = queries.data() + qi * kDimension;
    const auto thq_begin = std::chrono::steady_clock::now();
    const auto lut = build_lut(thresholds, query);
    const auto coarse = thq_top128_candidates(thq, lut, ids);
    const auto thq_end = std::chrono::steady_clock::now();
    std::vector<std::int32_t> coarse_ids;
    coarse_ids.reserve(coarse.size());
    for (const auto& candidate : coarse) coarse_ids.push_back(candidate.id);
    const auto codec_begin = std::chrono::steady_clock::now();
    const auto reranked = exact_cosine_top10_dense(vectors, coarse_ids, selected_ids, query);
    const auto codec_end = std::chrono::steady_clock::now();
    auto emit_ids = [](const auto& values) {
      std::cout << '[';
      for (std::size_t i = 0; i < values.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << values[i].id;
      }
      std::cout << ']';
    };
    const auto thq_pages = namespaced_pages(ids, kThqBytes, 1ULL << 47);
    const auto touched_pages = namespaced_pages(coarse_ids, payload_bytes, 1ULL << 48);
    std::cout << "{\"query\":" << qi << ",\"candidate_count\":" << ids.size()
              << ",\"thq4_top128_ids\":";
    emit_ids(coarse);
    std::cout << ",\"top10_ids\":";
    emit_ids(reranked);
    std::cout << ",\"top10_scores\":[" << std::setprecision(17);
    for (std::size_t i = 0; i < reranked.size(); ++i) {
      if (i) std::cout << ',';
      std::cout << reranked[i].score;
    }
    std::cout << ']';
    std::cout << ",\"timing_ms\":{\"thq4_prefilter\":"
              << elapsed_ms(thq_begin, thq_end) << ",\"codec_rerank\":"
              << elapsed_ms(codec_begin, codec_end) << ",\"total\":"
              << elapsed_ms(thq_begin, codec_end) << "},\"thq_pages\":"
              << thq_pages << ",\"codec_pages\":" << touched_pages
              << ",\"logical_payload_bytes\":" << payload_bytes << "}\n";
  }
  std::cerr << "{\"queries\":" << query_count
            << ",\"timing_scope\":\"native scalar THQ byte-LUT over frozen candidates plus native cosine rerank over predecoded codec rows; decode cost excluded\"}\n";
  return 0;
}

int run_fp32_candidate_gate(int argc, char** argv) {
  if (argc != 9)
    throw std::runtime_error("usage: benchmark --fp32-candidate-gate thq thresholds documents candidate_flat offsets query_file query_count");
  const auto thq = read<std::uint8_t>(argv[2]);
  const auto thresholds = read<float>(argv[3]);
  const auto documents = read<float>(argv[4]);
  const auto flat = read<std::uint8_t>(argv[5]);
  const auto offsets = read<std::uint64_t>(argv[6]);
  const std::size_t query_count = static_cast<std::size_t>(std::stoull(argv[8]));
  if (thq.size() != kDocuments * kThqBytes || thresholds.size() != kDimension * 3 ||
      documents.size() != kDocuments * kDimension || flat.size() % kCandidateRecordBytes != 0 ||
      offsets.size() != query_count + 1 || offsets.front() != 0 ||
      offsets.back() != flat.size() / kCandidateRecordBytes)
    throw std::runtime_error("FP32 candidate cascade payload shape differs");
  const auto queries = read<float>(argv[7]);
  if (queries.size() != query_count * kDimension)
    throw std::runtime_error("FP32 candidate cascade query shape differs");
  std::vector<std::int32_t> candidate_ids(flat.size() / kCandidateRecordBytes);
  for (std::size_t i = 0; i < candidate_ids.size(); ++i) {
    std::int32_t id = 0;
    std::memcpy(&id, flat.data() + i * kCandidateRecordBytes, sizeof(id));
    if (id < 0 || id >= static_cast<std::int32_t>(kDocuments))
      throw std::runtime_error("FP32 candidate ID out of range");
    candidate_ids[i] = id;
  }
  double total_ms = 0.0;
  for (std::size_t qi = 0; qi < query_count; ++qi) {
    const auto begin_id = static_cast<std::size_t>(offsets[qi]);
    const auto end_id = static_cast<std::size_t>(offsets[qi + 1]);
    std::vector<std::int32_t> ids(candidate_ids.begin() + begin_id,
                                  candidate_ids.begin() + end_id);
    if (ids.empty()) throw std::runtime_error("FP32 candidate query is empty");
    const float* query = queries.data() + qi * kDimension;
    const auto start = std::chrono::steady_clock::now();
    const auto lut = build_lut(thresholds, query);
    const auto coarse = thq_top128_candidates(thq, lut, ids);
    std::vector<std::int32_t> coarse_ids;
    coarse_ids.reserve(coarse.size());
    for (const auto& candidate : coarse) coarse_ids.push_back(candidate.id);
    const auto reranked = exact_cosine_top10(documents, coarse_ids, query);
    const auto elapsed = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    total_ms += elapsed;
    std::cout << "{\"query\":" << qi << ",\"candidate_count\":" << ids.size()
              << ",\"latency_ms\":" << elapsed << ",\"thq4_top128_ids\":[";
    for (std::size_t j = 0; j < coarse_ids.size(); ++j) {
      if (j != 0) std::cout << ',';
      std::cout << coarse_ids[j];
    }
    std::cout << "],\"fp32_cosine_top10_ids\":[";
    for (std::size_t j = 0; j < reranked.size(); ++j) {
      if (j != 0) std::cout << ',';
      std::cout << reranked[j].id;
    }
    std::cout << "]}\n";
  }
  std::cerr << "{\"queries\":" << query_count
            << ",\"timing_scope\":\"native scalar THQ byte-LUT plus FP32 cosine oracle over candidate top128; no OS-page latency claim\",\"mean_ms\":"
            << total_ms / static_cast<double>(query_count) << "}\n";
  return 0;
}

int run_int8_cosine_candidate_gate(int argc, char** argv) {
  if (argc != 10)
    throw std::runtime_error("usage: benchmark --int8-cosine-candidate-gate thq thresholds codes scales candidate_flat offsets query_file query_count");
  const auto thq = read<std::uint8_t>(argv[2]);
  const auto thresholds = read<float>(argv[3]);
  const auto codes = read<std::int8_t>(argv[4]);
  const auto scales = read<float>(argv[5]);
  const auto flat = read<std::uint8_t>(argv[6]);
  const auto offsets = read<std::uint64_t>(argv[7]);
  const std::size_t query_count = static_cast<std::size_t>(std::stoull(argv[9]));
  if (thq.size() != kDocuments * kThqBytes || thresholds.size() != kDimension * 3 ||
      codes.size() != kDocuments * kDimension || scales.size() != kDocuments ||
      flat.size() % kCandidateRecordBytes != 0 || offsets.size() != query_count + 1 ||
      offsets.front() != 0 || offsets.back() != flat.size() / kCandidateRecordBytes)
    throw std::runtime_error("INT8 cosine candidate cascade payload shape differs");
  const auto queries = read<float>(argv[8]);
  if (queries.size() != query_count * kDimension)
    throw std::runtime_error("INT8 cosine candidate cascade query shape differs");
  std::vector<std::int32_t> candidate_ids(flat.size() / kCandidateRecordBytes);
  for (std::size_t i = 0; i < candidate_ids.size(); ++i) {
    std::int32_t id = 0;
    std::memcpy(&id, flat.data() + i * kCandidateRecordBytes, sizeof(id));
    if (id < 0 || id >= static_cast<std::int32_t>(kDocuments))
      throw std::runtime_error("INT8 cosine candidate ID out of range");
    candidate_ids[i] = id;
  }
  double total_ms = 0.0;
  for (std::size_t qi = 0; qi < query_count; ++qi) {
    const auto begin_id = static_cast<std::size_t>(offsets[qi]);
    const auto end_id = static_cast<std::size_t>(offsets[qi + 1]);
    std::vector<std::int32_t> ids(candidate_ids.begin() + begin_id,
                                  candidate_ids.begin() + end_id);
    if (ids.empty()) throw std::runtime_error("INT8 cosine candidate query is empty");
    const float* query = queries.data() + qi * kDimension;
    const auto start = std::chrono::steady_clock::now();
    const auto lut = build_lut(thresholds, query);
    const auto coarse = thq_top128_candidates(thq, lut, ids);
    std::vector<std::int32_t> coarse_ids;
    coarse_ids.reserve(coarse.size());
    for (const auto& candidate : coarse) coarse_ids.push_back(candidate.id);
    const auto reranked = exact_cosine_top10_int8(codes, scales, coarse_ids, query);
    const auto elapsed = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    total_ms += elapsed;
    std::cout << "{\"query\":" << qi << ",\"candidate_count\":" << ids.size()
              << ",\"latency_ms\":" << elapsed << ",\"thq4_top128_ids\":[";
    for (std::size_t j = 0; j < coarse_ids.size(); ++j) {
      if (j != 0) std::cout << ',';
      std::cout << coarse_ids[j];
    }
    std::cout << "],\"int8_cosine_top10_ids\":[";
    for (std::size_t j = 0; j < reranked.size(); ++j) {
      if (j != 0) std::cout << ',';
      std::cout << reranked[j].id;
    }
    std::cout << "]}\n";
  }
  std::cerr << "{\"queries\":" << query_count
            << ",\"timing_scope\":\"native scalar THQ byte-LUT plus INT8 decode and FP32 cosine over candidate top128; no OS-page latency claim\",\"mean_ms\":"
            << total_ms / static_cast<double>(query_count) << "}\n";
  return 0;
}

int run_int8_matched_r4(int argc, char** argv) {
  if (argc != 12)
    throw std::runtime_error("usage: benchmark --int8-matched-r4 thq thresholds codes scales candidate_flat offsets query_file warmups repeats raw_output");
  const auto thq = read<std::uint8_t>(argv[2]);
  const auto thresholds = read<float>(argv[3]);
  const auto codes = read<std::int8_t>(argv[4]);
  const auto scales = read<float>(argv[5]);
  const auto flat = read<std::uint8_t>(argv[6]);
  const auto offsets = read<std::uint64_t>(argv[7]);
  const auto queries = read<float>(argv[8]);
  const std::size_t warmups = static_cast<std::size_t>(std::stoull(argv[9]));
  const std::size_t repeats = static_cast<std::size_t>(std::stoull(argv[10]));
  const std::size_t record_bytes = flat.size() % 100 == 0 && offsets.size() == 153 && offsets.back() != 0 &&
      flat.size() / 100 == offsets.back() ? 100 :
      (flat.size() % 148 == 0 && offsets.size() == 153 && offsets.back() != 0 && flat.size() / 148 == offsets.back() ? 148 : 0);
  if (thq.size() != kDocuments * kThqBytes || thresholds.size() != kDimension * 3 ||
      codes.size() != kDocuments * kDimension || scales.size() != kDocuments || record_bytes == 0 ||
      offsets.size() != 153 ||
      offsets.front() != 0 || offsets.back() != flat.size() / record_bytes ||
      queries.size() != 152 * kDimension || warmups == 0 || repeats == 0)
    throw std::runtime_error("INT8 matched R4 fixture shape differs");
  std::ofstream raw(argv[11]);
  if (!raw) throw std::runtime_error("cannot open INT8 matched raw output");
  std::vector<std::int32_t> candidate_ids(flat.size() / record_bytes);
  for (std::size_t i = 0; i < candidate_ids.size(); ++i) {
    std::memcpy(&candidate_ids[i], flat.data() + i * record_bytes, sizeof(std::int32_t));
    if (candidate_ids[i] < 0 || candidate_ids[i] >= static_cast<std::int32_t>(kDocuments))
      throw std::runtime_error("INT8 matched candidate ID is out of range");
  }
  std::vector<double> total_samples, thq_samples, rerank_samples;
  total_samples.reserve(152 * repeats);
  thq_samples.reserve(152 * repeats);
  rerank_samples.reserve(152 * repeats);
  for (std::size_t qi = 0; qi < 152; ++qi) {
    const auto begin_id = static_cast<std::size_t>(offsets[qi]);
    const auto end_id = static_cast<std::size_t>(offsets[qi + 1]);
    std::vector<std::int32_t> ids(candidate_ids.begin() + begin_id, candidate_ids.begin() + end_id);
    if (ids.size() < 128) throw std::runtime_error("INT8 matched candidate row is narrower than top128");
    const float* query = queries.data() + qi * kDimension;
    for (std::size_t rep = 0; rep < warmups + repeats; ++rep) {
      const auto total_begin = std::chrono::steady_clock::now();
      const auto thq_begin = total_begin;
      const auto lut = build_lut(thresholds, query);
      const auto coarse = thq_top128_candidates(thq, lut, ids);
      const auto thq_end = std::chrono::steady_clock::now();
      std::vector<std::int32_t> coarse_ids;
      coarse_ids.reserve(coarse.size());
      for (const auto& candidate : coarse) coarse_ids.push_back(candidate.id);
      const auto rerank_begin = std::chrono::steady_clock::now();
      const auto reranked = exact_cosine_top10_int8(codes, scales, coarse_ids, query);
      const auto rerank_end = std::chrono::steady_clock::now();
      const double thq_ms = elapsed_ms(thq_begin, thq_end);
      const double rerank_ms = elapsed_ms(rerank_begin, rerank_end);
      const double total_ms = elapsed_ms(total_begin, rerank_end);
      if (rep >= warmups) {
        thq_samples.push_back(thq_ms); rerank_samples.push_back(rerank_ms); total_samples.push_back(total_ms);
        raw << "{\"repeat\":" << (rep - warmups) << ",\"query\":" << qi
            << ",\"thq4_top128_ids\":[";
        for (std::size_t i = 0; i < coarse_ids.size(); ++i) { if (i) raw << ','; raw << coarse_ids[i]; }
        raw << "],\"top10_ids\":[";
        for (std::size_t i = 0; i < reranked.size(); ++i) { if (i) raw << ','; raw << reranked[i].id; }
        raw << "],\"timing_ms\":{\"thq4_prefilter\":" << std::setprecision(12) << thq_ms
            << ",\"codec_rerank\":" << rerank_ms << ",\"total\":" << total_ms << "}}\n";
      }
    }
  }
  std::cout << std::fixed << std::setprecision(6)
            << "{\"status\":\"EXECUTED\",\"scope\":\"frozen R4 candidate stream -> THQ top128 -> INT8 final reranker\",\"queries\":152,\"warmups\":" << warmups
            << ",\"repeats\":" << repeats << ",\"parity\":\"deferred_to_independent_audit\",\"thq_p50_ms\":" << percentile(thq_samples, .50)
            << ",\"thq_p95_ms\":" << percentile(thq_samples, .95) << ",\"thq_p99_ms\":" << percentile(thq_samples, .99)
            << ",\"rerank_p50_ms\":" << percentile(rerank_samples, .50) << ",\"rerank_p95_ms\":" << percentile(rerank_samples, .95)
            << ",\"rerank_p99_ms\":" << percentile(rerank_samples, .99) << ",\"total_p50_ms\":" << percentile(total_samples, .50)
            << ",\"total_p95_ms\":" << percentile(total_samples, .95) << ",\"total_p99_ms\":" << percentile(total_samples, .99) << "}\n";
  return 0;
}
}

int main(int argc, char** argv) {
  if (argc >= 2 && std::string(argv[1]) == "--lsq-full-flat") {
    try { return run_lsq_full_flat(argc, argv); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
  }
  if (argc >= 2 && std::string(argv[1]) == "--int8-full-flat") {
    try { return run_int8_full_flat(argc, argv); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
  }
  if (argc >= 2 && std::string(argv[1]) == "--production-control") {
    try { return run_production_control(argc, argv); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
  }
  if (argc >= 2 && std::string(argv[1]) == "--candidate-gate") {
    try { return run_candidate_gate(argc, argv); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
  }
  if (argc >= 2 && std::string(argv[1]) == "--fp32-candidate-gate") {
    try { return run_fp32_candidate_gate(argc, argv); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
  }
  if (argc >= 2 && std::string(argv[1]) == "--int8-cosine-candidate-gate") {
    try { return run_int8_cosine_candidate_gate(argc, argv); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
  }
  if (argc >= 2 && std::string(argv[1]) == "--int8-matched-r4") {
    try { return run_int8_matched_r4(argc, argv); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
  }
  if (argc >= 2 && std::string(argv[1]) == "--dense-candidate-gate") {
    try { return run_dense_candidate_gate(argc, argv); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
  }
  if (argc >= 2 && std::string(argv[1]) == "--lsq-candidate-gate") {
    try { return run_lsq_candidate_gate(argc, argv); }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
  }
  if (argc == 2 && std::string(argv[1]) == "--self-test") {
    std::vector<float> thresholds(kDimension * 3);
    std::vector<float> query(kDimension);
    for (std::size_t d = 0; d < kDimension; ++d) {
      thresholds[d * 3 + 0] = -0.5f;
      thresholds[d * 3 + 1] = 0.5f;
      thresholds[d * 3 + 2] = 1.5f;
      query[d] = 0.25f;
    }
    const QueryLut lut = build_lut(thresholds, query.data());
    for (std::size_t byte = 0; byte < kThqBytes; ++byte) {
      for (std::size_t packed = 0; packed < 256; ++packed) {
        float expected = 0.0f;
        for (std::size_t lane = 0; lane < 4; ++lane)
          expected += lut.coordinate[(byte * 4 + lane) * 4 +
                                     ((packed >> (lane * 2)) & 3U)];
        if (lut.byte[byte * 256 + packed] != expected)
          throw std::runtime_error("byte LUT parity differs");
      }
    }
    std::vector<std::uint8_t> row(kThqBytes);
    for (std::size_t byte = 0; byte < kThqBytes; ++byte)
      row[byte] = static_cast<std::uint8_t>((byte * 37U) & 255U);
    float reference = 0.0f;
    for (std::size_t byte = 0; byte < kThqBytes; ++byte)
      reference += lut.byte[byte * 256 + row[byte]];
    if (thq_score_row(row.data(), lut) != reference)
      throw std::runtime_error("packed THQ score parity differs");
    if (std::abs(thq_score_row_unrolled4(row.data(), lut) - reference) > 1e-3f)
      throw std::runtime_error("unrolled THQ score parity differs");
    {
      constexpr std::size_t synthetic_documents = 37;
      std::vector<std::uint8_t> synthetic_doc_major(
          synthetic_documents * kThqBytes);
      for (std::size_t id = 0; id < synthetic_documents; ++id) {
        for (std::size_t byte = 0; byte < kThqBytes; ++byte)
          synthetic_doc_major[id * kThqBytes + byte] =
              static_cast<std::uint8_t>((id * 13U + byte * 7U) & 255U);
      }
      const auto synthetic_layout = pack_thq_block32(synthetic_doc_major);
      if (synthetic_layout.documents != synthetic_documents ||
          synthetic_layout.padded_documents != 64)
        throw std::runtime_error("synthetic THQ block32 padding differs");
      std::vector<float> synthetic_scores(synthetic_layout.padded_documents,
                                          -1.0f);
      score_thq_block32(synthetic_layout, lut, synthetic_scores);
      for (std::size_t id = 0; id < synthetic_documents; ++id) {
        const float expected = thq_score_row_unrolled4(
            synthetic_doc_major.data() + id * kThqBytes, lut);
        if (std::abs(synthetic_scores[id] - expected) > 1e-3f)
          throw std::runtime_error("synthetic THQ block32 score parity differs");
      }
      // The padded tail is intentionally outside the logical document count;
      // callers must pass synthetic_layout.documents to top-k, never padded.
      if (synthetic_scores.size() != synthetic_layout.padded_documents)
        throw std::runtime_error("synthetic THQ score workspace shape differs");
    }
    for (std::size_t pair = 0; pair < kThqPairs; ++pair) {
      for (std::size_t packed = 0; packed < 16; ++packed) {
        const float expected =
            lut.coordinate[(pair * 2) * 4 + (packed & 3U)] +
            lut.coordinate[(pair * 2 + 1) * 4 + (packed >> 2U)];
        if (lut.pair[pair * 16 + packed] != expected)
          throw std::runtime_error("pair THQ LUT parity differs");
      }
    }
    std::vector<std::int8_t> int8_row(kDimension);
    for (std::size_t d = 0; d < kDimension; ++d)
      int8_row[d] = static_cast<std::int8_t>(static_cast<int>(d % 255) - 127);
    float scalar_dot = 0.0f;
    for (std::size_t d = 0; d < kDimension; ++d)
      scalar_dot += static_cast<float>(int8_row[d]) * query[d];
    const std::vector<float> one_scale{0.03125f};
    const float production_dot = score_int8_production(
        int8_row, one_scale, query.data(), 0);
    if (std::abs(production_dot - scalar_dot * one_scale[0]) > 1e-3f)
      throw std::runtime_error("production INT8 score parity differs");
#if defined(AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2) && AGENT_MEMORY_NATIVE_FULL_CORPUS_HAS_AVX2
    std::mt19937 parity_rng(20260930U);
    std::uniform_int_distribution<int> code_distribution(-127, 127);
    std::uniform_real_distribution<float> query_distribution(-1.0f, 1.0f);
    for (std::size_t sample = 0; sample < 1024; ++sample) {
      for (std::size_t d = 0; d < kDimension; ++d) {
        int8_row[d] = static_cast<std::int8_t>(code_distribution(parity_rng));
        query[d] = query_distribution(parity_rng);
      }
      if (sample == 0) {
        for (std::size_t d = 0; d < kDimension; ++d) {
          int8_row[d] = static_cast<std::int8_t>((d & 1U) == 0 ? 127 : -127);
          query[d] = (d & 2U) == 0 ? 1.0f : -1.0f;
        }
      }
      const float scalar = score_int8_float_scalar(
          int8_row.data(), one_scale[0], query.data());
      const float avx2 = score_int8_float_avx2(
          int8_row.data(), one_scale[0], query.data());
      const float tolerance = std::max(0.02f, std::abs(scalar) * 2e-5f);
      if (!std::isfinite(avx2) || std::abs(avx2 - scalar) > tolerance)
        throw std::runtime_error("random/adversarial INT8 AVX2 parity differs");
    }
#endif
    const Candidate tie_a{1.0f, 7};
    const Candidate tie_b{1.0f, 8};
    if (!better(tie_a, tie_b) || better(tie_b, tie_a))
      throw std::runtime_error("deterministic tie policy differs");
    const DenseCandidate dense_tie_a{1.0, 7};
    const DenseCandidate dense_tie_b{1.0, 8};
    if (!better_dense_desc(dense_tie_a, dense_tie_b) ||
        better_dense_desc(dense_tie_b, dense_tie_a))
      throw std::runtime_error("dense deterministic tie policy differs");
    FixedCandidateHeap<3, WorseCandidate> ascending_heap;
    FixedCandidateHeap<3, WorseDescendingCandidate> descending_heap;
    for (const Candidate candidate : {Candidate{3.0f, 3}, Candidate{1.0f, 1},
                                      Candidate{2.0f, 2}}) {
      ascending_heap.push(candidate);
      descending_heap.push(candidate);
    }
    if (ascending_heap.top().score != 3.0f || descending_heap.top().score != 1.0f)
      throw std::runtime_error("bounded top-k heap polarity differs");
    if (namespaced_pages({0}, kThqBytes, 0) != 1 ||
        namespaced_pages({42}, kThqBytes, 0) != 2 ||
        namespaced_pages({0, 42}, kThqBytes, 0) != 2)
      throw std::runtime_error("cross-page record accounting differs");
    const std::array<std::vector<std::int32_t>, 3> invalid_id_cases = {
        std::vector<std::int32_t>{2, 2, 3},
        std::vector<std::int32_t>{-1, 2},
        std::vector<std::int32_t>{0, 1000000}};
    for (const auto& invalid : invalid_id_cases) {
      try {
        validate_lsq_ids(invalid);
      } catch (const std::runtime_error&) {
        continue;
      }
      throw std::runtime_error("LSQ invalid-ID self-test accepted malformed IDs");
    }
    bool rejected_oversized_count = false;
    try {
      validate_lsq_header(32, kDimension, static_cast<std::uint32_t>(kDocuments) + 1);
    } catch (const std::runtime_error&) {
      // Expected: oversized payload counts must fail closed.
      rejected_oversized_count = true;
    }
    if (!rejected_oversized_count)
      throw std::runtime_error("LSQ oversized-count self-test accepted malformed header");
    LsqPayload page_payload;
    page_payload.stages = 32;
    page_payload.ids = {100, 900000};
    page_payload.codebooks.resize(page_payload.stages * 256ULL * kDimension);
    page_payload.centroids.resize(4 * kDimension);
    if (packed_lsq_pages(page_payload, {100, 900000}) != 2 ||
        lsq_model_pages(page_payload) != 3074 ||
        full_corpus_lsq_pages(page_payload) != 8790)
      throw std::runtime_error("packed LSQ page accounting differs");
    LsqPayload scorer_payload;
    scorer_payload.stages = 2;
    scorer_payload.dimensions = kDimension;
    scorer_payload.ids.resize(16);
    std::iota(scorer_payload.ids.begin(), scorer_payload.ids.end(), 0);
    scorer_payload.codes.resize(16 * scorer_payload.stages);
    scorer_payload.codebooks.resize(scorer_payload.stages * 256ULL * kDimension);
    scorer_payload.centroids.resize(4 * kDimension);
    scorer_payload.norms.resize(16, 1.0f);
    for (std::size_t i = 0; i < scorer_payload.codes.size(); ++i)
      scorer_payload.codes[i] = static_cast<std::uint8_t>((i * 17) & 255U);
    for (std::size_t i = 0; i < scorer_payload.codebooks.size(); ++i)
      scorer_payload.codebooks[i] = static_cast<float>((static_cast<int>(i % 23) - 11) * 0.0001);
    for (std::size_t i = 0; i < scorer_payload.centroids.size(); ++i)
      scorer_payload.centroids[i] = static_cast<float>((static_cast<int>(i % 13) - 6) * 0.001);
    std::vector<std::uint8_t> scorer_thq(16 * kThqBytes);
    for (std::size_t i = 0; i < scorer_thq.size(); ++i)
      scorer_thq[i] = static_cast<std::uint8_t>((i * 29) & 255U);
    std::vector<std::int32_t> scorer_ids(16);
    std::iota(scorer_ids.begin(), scorer_ids.end(), 0);
    const auto gather = score_lsq_gather(scorer_payload, scorer_thq, scorer_ids,
                                         query.data());
    const auto full = score_lsq_lut(scorer_payload, scorer_thq, scorer_ids,
                                    query.data(), false);
    const auto sparse = score_lsq_lut(scorer_payload, scorer_thq, scorer_ids,
                                      query.data(), true);
    for (std::size_t i = 0; i < gather.scores.size(); ++i) {
      if (std::abs(gather.scores[i] - full.scores[i]) > 1e-12 ||
          std::abs(gather.scores[i] - sparse.scores[i]) > 1e-12)
        throw std::runtime_error("LSQ LUT score parity differs");
    }
    for (std::size_t i = 0; i < gather.top10.size(); ++i)
      if (gather.top10[i].id != full.top10[i].id ||
          gather.top10[i].id != sparse.top10[i].id)
        throw std::runtime_error("LSQ LUT top10 parity differs");
    std::cout << "native-full-corpus-codec-benchmark self-test PASS\n";
    return 0;
  }
  if (argc != 9) {
    std::cerr << "usage: benchmark thq thresholds linear linear_scales power power_scales query_file query_count\n";
    return 2;
  }
  try {
    const auto thq = read<std::uint8_t>(argv[1]);
    const auto thresholds = read<float>(argv[2]);
    const auto linear = read<std::int8_t>(argv[3]);
    const auto linear_scales = read<float>(argv[4]);
    const auto power = read<std::int8_t>(argv[5]);
    const auto power_scales = read<float>(argv[6]);
    if (thq.size() != kDocuments * kThqBytes || thresholds.size() != kDimension * 3 ||
        linear.size() != kDocuments * kDimension || power.size() != kDocuments * kDimension ||
        linear_scales.size() != kDocuments || power_scales.size() != kDocuments)
      throw std::runtime_error("payload shape differs");
    std::vector<float> linear_gains(kDocuments, 1.0f);
    std::vector<float> power_gains(kDocuments);
    for (std::size_t id = 0; id < kDocuments; ++id)
      power_gains[id] = std::pow(power_scales[id], 1.6f);
    const std::size_t q = static_cast<std::size_t>(std::stoull(argv[8]));
    std::ifstream query_stream(argv[7], std::ios::binary);
    if (!query_stream) throw std::runtime_error("cannot open query payload");
    std::vector<float> queries(q * kDimension);
    query_stream.read(reinterpret_cast<char*>(queries.data()),
                      static_cast<std::streamsize>(queries.size() * sizeof(float)));
    if (!query_stream) throw std::runtime_error("query payload is truncated");
    double direct_ms = 0, cascade_ms = 0, direct_power_ms = 0, cascade_power_ms = 0;
    for (std::size_t i = 0; i < q; ++i) {
      const float* query = queries.data() + i * kDimension;
      const auto begin = std::chrono::steady_clock::now();
      const auto d = direct(linear, linear_scales, linear_gains, query, false);
      const auto mid = std::chrono::steady_clock::now();
      const auto c = cascade(thq, thresholds, linear, linear_scales, linear_gains, query, false);
      const auto after_c = std::chrono::steady_clock::now();
      const auto dp = direct(power, power_scales, power_gains, query, true);
      const auto after_dp = std::chrono::steady_clock::now();
      const auto cp = cascade(thq, thresholds, power, power_scales, power_gains, query, true);
      const auto end = std::chrono::steady_clock::now();
      direct_ms += std::chrono::duration<double, std::milli>(mid - begin).count();
      cascade_ms += std::chrono::duration<double, std::milli>(after_c - mid).count();
      direct_power_ms += std::chrono::duration<double, std::milli>(after_dp - after_c).count();
      cascade_power_ms += std::chrono::duration<double, std::milli>(end - after_dp).count();
      std::cout << "{\"query\":" << i << ",\"direct_linear_top1\":" << d.first.id
                << ",\"cascade_linear_top1\":" << c.best.id
                << ",\"direct_power0625_top1\":" << dp.first.id
                << ",\"cascade_power0625_top1\":" << cp.best.id
                << ",\"direct_code_scale_pages\":" << d.second
                << ",\"cascade_thq_scan_pages\":" << c.thq_scan_pages
                << ",\"cascade_rerank_payload_pages\":" << c.rerank_payload_pages
                << ",\"cascade_total_namespaced_pages\":"
                << c.thq_scan_pages + c.rerank_payload_pages
                << ",\"cascade_thq_top128_ids\":[";
      for (std::size_t j = 0; j < c.coarse.size(); ++j) {
        if (j != 0) std::cout << ',';
        std::cout << c.coarse[j].id;
      }
      std::cout << "],\"cascade_thq_top128_scores\":[" << std::setprecision(9);
      for (std::size_t j = 0; j < c.coarse.size(); ++j) {
        if (j != 0) std::cout << ',';
        std::cout << c.coarse[j].score;
      }
      std::cout << "]}\n";
    }
    std::cerr << "{\"queries\":" << q
              << ",\"timing_scope\":\"full_corpus_scalar_scan_control; THQ uses per-query byte LUT; power gain is precomputed outside query loop\""
              << ",\"direct_linear_mean_ms\":" << direct_ms / q
              << ",\"cascade_linear_mean_ms\":" << cascade_ms / q
              << ",\"direct_power0625_mean_ms\":" << direct_power_ms / q
              << ",\"cascade_power0625_mean_ms\":" << cascade_power_ms / q << "}\n";
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
