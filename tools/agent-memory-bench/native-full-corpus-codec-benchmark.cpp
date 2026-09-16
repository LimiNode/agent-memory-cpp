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
struct CascadeResult {
  Candidate best;
  std::uint64_t thq_scan_pages;
  std::uint64_t rerank_payload_pages;
  std::vector<Candidate> coarse;
};
bool better(const Candidate& a, const Candidate& b) {
  return a.score < b.score || (a.score == b.score && a.id < b.id);
}
bool better_desc(const Candidate& a, const Candidate& b) {
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
}

int main(int argc, char** argv) {
  if (argc >= 2 && std::string(argv[1]) == "--candidate-gate") {
    try { return run_candidate_gate(argc, argv); }
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
