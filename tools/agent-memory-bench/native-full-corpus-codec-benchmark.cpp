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
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

namespace {
constexpr std::size_t kDocuments = 1000000;
constexpr std::size_t kDimension = 384;
constexpr std::size_t kThqBytes = 96;
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

struct QueryLut {
  std::array<float, kDimension * 4> coordinate{};
  std::array<float, kThqBytes * 256> byte{};
};

float thq_score_row(const std::uint8_t* row, const QueryLut& lut);

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
  return lut;
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

std::vector<Candidate> thq_top128_candidates(
    const std::vector<std::uint8_t>& codes, const QueryLut& lut,
    const std::vector<std::int32_t>& ids) {
  std::vector<Candidate> all;
  all.reserve(ids.size());
  for (const auto id : ids) {
    const auto* row = codes.data() + static_cast<std::size_t>(id) * kThqBytes;
    all.push_back({thq_score_row(row, lut), id});
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
  if (stages == 0 || dimensions != kDimension || count == 0)
    throw std::runtime_error("invalid LSQ payload dimensions");
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
  if (!std::is_sorted(out.ids.begin(), out.ids.end()))
    throw std::runtime_error("LSQ payload IDs must be sorted");
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

std::array<double, kThqBytes * 256> build_thq_dot_byte_lut(
    const LsqPayload& payload, const float* query) {
  std::array<double, kThqBytes * 256> lut{};
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
  result.top10 = exact_cosine_top10_lsq(payload, thq, ids, query);
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
  result.score_ms = elapsed_ms(begin, std::chrono::steady_clock::now());
  return result;
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

void emit_timing_summary(const char* name, const std::vector<double>& values) {
  const double sum = std::accumulate(values.begin(), values.end(), 0.0);
  std::cerr << '"' << name << "\":{\"mean_ms\":" << sum / values.size()
            << ",\"p50_ms\":" << percentile(values, 0.50)
            << ",\"p95_ms\":" << percentile(values, 0.95)
            << ",\"p99_ms\":" << percentile(values, 0.99) << '}';
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
}

int main(int argc, char** argv) {
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
    const Candidate tie_a{1.0f, 7};
    const Candidate tie_b{1.0f, 8};
    if (!better(tie_a, tie_b) || better(tie_b, tie_a))
      throw std::runtime_error("deterministic tie policy differs");
    const DenseCandidate dense_tie_a{1.0, 7};
    const DenseCandidate dense_tie_b{1.0, 8};
    if (!better_dense_desc(dense_tie_a, dense_tie_b) ||
        better_dense_desc(dense_tie_b, dense_tie_a))
      throw std::runtime_error("dense deterministic tie policy differs");
    if (namespaced_pages({0}, kThqBytes, 0) != 1 ||
        namespaced_pages({42}, kThqBytes, 0) != 2 ||
        namespaced_pages({0, 42}, kThqBytes, 0) != 2)
      throw std::runtime_error("cross-page record accounting differs");
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
