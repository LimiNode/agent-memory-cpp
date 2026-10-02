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
#include <vector>

namespace {
constexpr std::size_t kDocuments = 1000000;
constexpr std::size_t kDimension = 384;
constexpr std::size_t kThqBytes = 96;
constexpr std::size_t kTqBytes = 48;
constexpr std::size_t kPqSubspaces = 8;
constexpr std::size_t kPqWidth = 48;
constexpr double kTqCentroid = 0.7978846;
constexpr std::array<std::uint64_t, 3> kSeeds{
    654605292835415893ULL, 8636605637963351413ULL,
    1775280196666917949ULL};

struct Candidate { double score; std::int32_t id; };
struct Payload {
  std::uint32_t count = 0;
  bool has_tq_norm = false;
  std::vector<std::int32_t> ids;
  std::vector<std::uint8_t> signs;
  std::vector<float> scales;
  std::vector<std::uint8_t> pq_codes;
  std::vector<float> tq_norms;
  std::vector<float> final_norms;
  std::vector<float> thq_centroids;
  std::vector<float> pq_centroids;
};

template <typename T> std::vector<T> read(const std::string& path) {
  std::ifstream in(path, std::ios::binary | std::ios::ate);
  if (!in) throw std::runtime_error("cannot open " + path);
  const auto end = in.tellg();
  if (end < 0 || static_cast<std::size_t>(end) % sizeof(T) != 0)
    throw std::runtime_error("unaligned payload " + path);
  std::vector<T> result(static_cast<std::size_t>(end) / sizeof(T));
  in.seekg(0);
  in.read(reinterpret_cast<char*>(result.data()), end);
  if (!in) throw std::runtime_error("cannot read " + path);
  return result;
}

Payload read_payload(const std::string& path) {
  const auto bytes = read<std::uint8_t>(path);
  constexpr std::size_t header = 24;
  if (bytes.size() < header || std::memcmp(bytes.data(), "AMTQP01", 7) != 0)
    throw std::runtime_error("invalid TQ1/PQ8 payload header");
  auto u32 = [&](std::size_t offset) {
    std::uint32_t value = 0;
    std::memcpy(&value, bytes.data() + offset, sizeof(value));
    return value;
  };
  const auto dimension = u32(8);
  const auto count = u32(12);
  const auto subspaces = u32(16);
  const auto flags = u32(20);
  if (dimension != kDimension || count == 0 || count > kDocuments ||
      subspaces != kPqSubspaces ||
      (flags & ~1U) != 0)
    throw std::runtime_error("invalid TQ1/PQ8 payload dimensions");
  const bool has_tq_norm = (flags & 1U) != 0;
  const std::size_t expected = header + count * sizeof(std::int32_t) +
      count * kTqBytes + count * sizeof(float) + count * kPqSubspaces +
      (has_tq_norm ? count * sizeof(float) : 0) + count * sizeof(float) +
      kDimension * 4 * sizeof(float) +
      kPqSubspaces * 256 * kPqWidth * sizeof(float);
  if (bytes.size() != expected) throw std::runtime_error("TQ1/PQ8 payload size differs");
  Payload result;
  result.count = count;
  result.has_tq_norm = has_tq_norm;
  result.ids.resize(count);
  result.signs.resize(count * kTqBytes);
  result.scales.resize(count);
  result.pq_codes.resize(count * kPqSubspaces);
  if (has_tq_norm) result.tq_norms.resize(count);
  result.final_norms.resize(count);
  result.thq_centroids.resize(kDimension * 4);
  result.pq_centroids.resize(kPqSubspaces * 256 * kPqWidth);
  std::size_t offset = header;
  auto copy = [&](void* destination, std::size_t size) {
    std::memcpy(destination, bytes.data() + offset, size);
    offset += size;
  };
  copy(result.ids.data(), result.ids.size() * sizeof(std::int32_t));
  copy(result.signs.data(), result.signs.size());
  copy(result.scales.data(), result.scales.size() * sizeof(float));
  copy(result.pq_codes.data(), result.pq_codes.size());
  if (has_tq_norm) copy(result.tq_norms.data(), result.tq_norms.size() * sizeof(float));
  copy(result.final_norms.data(), result.final_norms.size() * sizeof(float));
  copy(result.thq_centroids.data(), result.thq_centroids.size() * sizeof(float));
  copy(result.pq_centroids.data(), result.pq_centroids.size() * sizeof(float));
  if (!std::is_sorted(result.ids.begin(), result.ids.end()) ||
      std::adjacent_find(result.ids.begin(), result.ids.end()) != result.ids.end())
    throw std::runtime_error("TQ1/PQ8 payload IDs are not strictly sorted");
  if (std::any_of(result.ids.begin(), result.ids.end(), [](std::int32_t id) {
        return id < 0 || static_cast<std::size_t>(id) >= kDocuments;
      }))
    throw std::runtime_error("TQ1/PQ8 payload ID is outside the document corpus");
  auto finite = [](const auto& values) {
    for (const auto value : values)
      if (!std::isfinite(static_cast<double>(value))) return false;
    return true;
  };
  if (!finite(result.scales) || !finite(result.final_norms) ||
      std::any_of(result.final_norms.begin(), result.final_norms.end(),
                  [](float value) { return value <= 0.0F; }) ||
      (result.has_tq_norm &&
       (!finite(result.tq_norms) ||
        std::any_of(result.tq_norms.begin(), result.tq_norms.end(),
                    [](float value) { return value <= 0.0F; }))) ||
      !finite(result.thq_centroids) || !finite(result.pq_centroids))
    throw std::runtime_error("TQ1/PQ8 payload contains non-finite values");
  return result;
}

std::vector<std::size_t> chunks(std::size_t dimension) {
  std::vector<std::size_t> result;
  while (dimension != 0) {
    std::size_t size = 1;
    while ((size << 1U) <= dimension) size <<= 1U;
    result.push_back(size);
    dimension -= size;
  }
  return result;
}

void wht_normalized(std::vector<double>& values) {
  std::size_t offset = 0;
  for (const auto size : chunks(values.size())) {
    for (std::size_t width = 1; width < size; width <<= 1U) {
      for (std::size_t base = 0; base < size; base += 2 * width) {
        for (std::size_t lane = 0; lane < width; ++lane) {
          const double left = values[offset + base + lane];
          const double right = values[offset + base + width + lane];
          values[offset + base + lane] = left + right;
          values[offset + base + width + lane] = left - right;
        }
      }
    }
    const double scale = 1.0 / std::sqrt(static_cast<double>(size));
    for (std::size_t index = 0; index < size; ++index)
      values[offset + index] *= scale;
    offset += size;
  }
}

std::vector<std::size_t> permutation(std::size_t dimension, std::uint64_t seed) {
  std::vector<std::size_t> values(dimension);
  std::iota(values.begin(), values.end(), 0);
  std::uint64_t state = seed;
  for (std::size_t index = dimension - 1; index > 0; --index) {
    state = state * 6364136223846793005ULL + 1442695040888963407ULL;
    const auto other = static_cast<std::size_t>((state >> 32U) % (index + 1));
    std::swap(values[index], values[other]);
  }
  return values;
}

std::vector<double> rotate_query(const float* query) {
  std::vector<double> result(query, query + kDimension);
  wht_normalized(result);
  for (const auto seed : kSeeds) {
    const auto order = permutation(kDimension, seed);
    std::vector<double> permuted(kDimension);
    for (std::size_t index = 0; index < kDimension; ++index)
      permuted[index] = result[order[index]];
    result = std::move(permuted);
    wht_normalized(result);
  }
  return result;
}

std::vector<double> build_thq_dot_lut(
    const Payload& payload, const float* query) {
  std::vector<double> result(kThqBytes * 256);
  for (std::size_t byte = 0; byte < kThqBytes; ++byte) {
    for (std::size_t packed = 0; packed < 256; ++packed) {
      double dot = 0.0;
      for (std::size_t lane = 0; lane < 4; ++lane) {
        const auto dimension = byte * 4 + lane;
        const auto level = (packed >> (lane * 2)) & 3U;
        dot += static_cast<double>(payload.thq_centroids[dimension * 4 + level]) *
               query[dimension];
      }
      result[byte * 256 + packed] = dot;
    }
  }
  return result;
}

std::vector<double> build_tq_dot_lut(
    const std::vector<double>& rotated_query) {
  std::vector<double> result(kTqBytes * 256);
  for (std::size_t byte = 0; byte < kTqBytes; ++byte) {
    for (std::size_t packed = 0; packed < 256; ++packed) {
      double dot = 0.0;
      for (std::size_t lane = 0; lane < 8; ++lane) {
        const double sign = ((packed >> lane) & 1U) ? 1.0 : -1.0;
        dot += sign * kTqCentroid * rotated_query[byte * 8 + lane];
      }
      result[byte * 256 + packed] = dot;
    }
  }
  return result;
}

std::vector<double> build_pq_dot_lut(const Payload& payload, const float* query) {
  std::vector<double> result(kPqSubspaces * 256);
  for (std::size_t subspace = 0; subspace < kPqSubspaces; ++subspace) {
    for (std::size_t code = 0; code < 256; ++code) {
      const auto* center = payload.pq_centroids.data() +
          (subspace * 256 + code) * kPqWidth;
      double dot = 0.0;
      for (std::size_t lane = 0; lane < kPqWidth; ++lane)
        dot += static_cast<double>(center[lane]) *
               query[subspace * kPqWidth + lane];
      result[subspace * 256 + code] = dot;
    }
  }
  return result;
}

std::vector<double> build_interval_lut(
    const std::vector<float>& thresholds, const float* query) {
  std::vector<double> result(kThqBytes * 256);
  for (std::size_t byte = 0; byte < kThqBytes; ++byte) {
    for (std::size_t packed = 0; packed < 256; ++packed) {
      double score = 0.0;
      for (std::size_t lane = 0; lane < 4; ++lane) {
        const auto dimension = byte * 4 + lane;
        const auto level = (packed >> (lane * 2)) & 3U;
        const double lower = level == 0 ? -std::numeric_limits<double>::infinity()
                                        : thresholds[dimension * 3 + level - 1];
        const double upper = level == 3 ? std::numeric_limits<double>::infinity()
                                        : thresholds[dimension * 3 + level];
        const double delta = query[dimension] < lower ? lower - query[dimension]
            : (query[dimension] > upper ? query[dimension] - upper : 0.0);
        score += delta * delta;
      }
      result[byte * 256 + packed] = score;
    }
  }
  return result;
}

bool descending(const Candidate& left, const Candidate& right) {
  return left.score > right.score ||
         (left.score == right.score && left.id < right.id);
}

bool ascending(const Candidate& left, const Candidate& right) {
  return left.score < right.score ||
         (left.score == right.score && left.id < right.id);
}

std::vector<std::int32_t> top_ids(std::vector<Candidate> values,
                                  std::size_t count, bool higher_is_better) {
  const auto limit = std::min(count, values.size());
  std::partial_sort(values.begin(), values.begin() + limit, values.end(),
                    higher_is_better ? descending : ascending);
  std::vector<std::int32_t> result;
  result.reserve(limit);
  for (std::size_t index = 0; index < limit; ++index)
    result.push_back(values[index].id);
  return result;
}

std::size_t payload_row(const Payload& payload, std::int32_t id) {
  const auto position = std::lower_bound(payload.ids.begin(), payload.ids.end(), id);
  if (position == payload.ids.end() || *position != id)
    throw std::runtime_error("TQ1/PQ8 payload misses a candidate");
  return static_cast<std::size_t>(position - payload.ids.begin());
}

void emit_ids(const std::vector<std::int32_t>& values) {
  std::cout << '[';
  for (std::size_t index = 0; index < values.size(); ++index) {
    if (index != 0) std::cout << ',';
    std::cout << values[index];
  }
  std::cout << ']';
}

void emit_scores(const std::vector<Candidate>& values) {
  std::cout << '[';
  for (std::size_t index = 0; index < values.size(); ++index) {
    if (index != 0) std::cout << ',';
    std::cout << std::setprecision(17) << values[index].score;
  }
  std::cout << ']';
}

double milliseconds(std::chrono::steady_clock::time_point begin,
                    std::chrono::steady_clock::time_point end) {
  return std::chrono::duration<double, std::milli>(end - begin).count();
}

double nearest_percentile(std::vector<double> values, double fraction) {
  std::sort(values.begin(), values.end());
  const auto rank = std::max<std::size_t>(1, static_cast<std::size_t>(std::ceil(fraction * values.size())));
  return values[std::min(values.size() - 1, rank - 1)];
}

int run_full_flat(int argc, char** argv) {
  if (argc != 8)
    throw std::runtime_error("usage: benchmark --full-flat payload thq queries warmups repeats raw_output");
  const auto payload = read_payload(argv[2]);
  const auto thq = read<std::uint8_t>(argv[3]);
  const auto queries = read<float>(argv[4]);
  const auto warmups = static_cast<std::size_t>(std::stoull(argv[5]));
  const auto repeats = static_cast<std::size_t>(std::stoull(argv[6]));
  std::ofstream raw(argv[7]);
  if (!raw || payload.count != kDocuments || thq.size() != kDocuments * kThqBytes ||
      queries.size() < 152 * kDimension || warmups == 0 || repeats == 0)
    throw std::runtime_error("TQ1/PQ8 full-flat fixture shape differs");
  std::vector<double> timings; timings.reserve(152 * repeats);
  for (std::size_t query_index = 0; query_index < 152; ++query_index) {
    const auto* query = queries.data() + query_index * kDimension;
    const auto base_lut = build_thq_dot_lut(payload, query);
    const auto rotated_query = rotate_query(query);
    const auto tq_lut = build_tq_dot_lut(rotated_query);
    const auto pq_lut = build_pq_dot_lut(payload, query);
    double query_norm = 0.0;
    for (std::size_t dimension = 0; dimension < kDimension; ++dimension)
      query_norm += static_cast<double>(query[dimension]) * query[dimension];
    query_norm = std::sqrt(std::max(query_norm, std::numeric_limits<double>::min()));
    for (std::size_t repeat = 0; repeat < warmups + repeats; ++repeat) {
      const auto begin = std::chrono::steady_clock::now();
      std::array<Candidate, 10> top{}; std::size_t top_count = 0;
      for (std::size_t row = 0; row < payload.count; ++row) {
        double numerator = 0.0;
        const auto* thq_row = thq.data() + row * kThqBytes;
        const auto* sign_row = payload.signs.data() + row * kTqBytes;
        const auto* pq_row = payload.pq_codes.data() + row * kPqSubspaces;
        for (std::size_t byte = 0; byte < kThqBytes; ++byte) numerator += base_lut[byte * 256 + thq_row[byte]];
        for (std::size_t byte = 0; byte < kTqBytes; ++byte) numerator += tq_lut[byte * 256 + sign_row[byte]] * payload.scales[row];
        for (std::size_t subspace = 0; subspace < kPqSubspaces; ++subspace) numerator += pq_lut[subspace * 256 + pq_row[subspace]];
        const Candidate candidate{numerator / std::max(static_cast<double>(payload.final_norms[row]) * query_norm, std::numeric_limits<double>::min()), payload.ids[row]};
        if (top_count < top.size()) top[top_count++] = candidate;
        else { std::size_t worst = 0; for (std::size_t i = 1; i < top.size(); ++i) if (descending(top[worst], top[i])) worst = i; if (descending(candidate, top[worst])) top[worst] = candidate; }
      }
      std::sort(top.begin(), top.end(), descending);
      if (repeat >= warmups) {
        const double elapsed = milliseconds(begin, std::chrono::steady_clock::now()); timings.push_back(elapsed);
        raw << "{\"query\":" << query_index << ",\"repeat\":" << (repeat - warmups) << ",\"timing_ms\":" << elapsed << ",\"top10_ids\":[";
        for (std::size_t index = 0; index < top.size(); ++index) { if (index) raw << ','; raw << top[index].id; }
        raw << "]}\n";
      }
    }
  }
  std::cout << std::fixed << std::setprecision(6) << "{\"status\":\"EXECUTED\",\"codec\":\"TQ1+PQ8\",\"metric\":\"reconstructed_cosine\",\"mode\":\"full_flat_1m\",\"queries\":152,\"repeats\":" << repeats << ",\"p50_ms\":" << nearest_percentile(timings, .5) << ",\"p95_ms\":" << nearest_percentile(timings, .95) << ",\"p99_ms\":" << nearest_percentile(timings, .99) << "}\n";
  return 0;
}

int run_gate(int argc, char** argv) {
  if (argc != 10)
    throw std::runtime_error("usage: benchmark --candidate-gate payload thq thresholds candidate_flat offsets queries query_count expected_side_bytes");
  const auto payload = read_payload(argv[2]);
  const auto thq = read<std::uint8_t>(argv[3]);
  const auto thresholds = read<float>(argv[4]);
  const auto flat = read<std::uint8_t>(argv[5]);
  const auto offsets = read<std::uint64_t>(argv[6]);
  const auto queries = read<float>(argv[7]);
  const auto query_count = static_cast<std::size_t>(std::stoull(argv[8]));
  const auto expected_side_bytes = static_cast<std::size_t>(std::stoull(argv[9]));
  const std::size_t side_bytes = kTqBytes + sizeof(float) + kPqSubspaces +
      sizeof(float) + (payload.has_tq_norm ? sizeof(float) : 0);
  if (thq.size() != kDocuments * kThqBytes ||
      thresholds.size() != kDimension * 3 || offsets.size() != query_count + 1 ||
      queries.size() != query_count * kDimension || side_bytes != expected_side_bytes)
    throw std::runtime_error("TQ1/PQ8 candidate gate shape differs");
  if (offsets.front() != 0 ||
      !std::is_sorted(offsets.begin(), offsets.end()))
    throw std::runtime_error("candidate offsets are not monotonic from zero");
  std::size_t record_bytes = 0;
  for (const std::size_t width : {std::size_t{100}, std::size_t{148}})
    if (flat.size() % width == 0 && offsets.back() == flat.size() / width)
      record_bytes = width;
  if (record_bytes == 0) throw std::runtime_error("candidate record width differs");
  std::vector<std::int32_t> candidate_ids(flat.size() / record_bytes);
  for (std::size_t index = 0; index < candidate_ids.size(); ++index)
    std::memcpy(&candidate_ids[index], flat.data() + index * record_bytes,
                sizeof(std::int32_t));
  for (const auto id : candidate_ids)
    if (id < 0 || static_cast<std::size_t>(id) >= kDocuments)
      throw std::runtime_error("candidate ID is outside the document corpus");

  for (std::size_t query_index = 0; query_index < query_count; ++query_index) {
    const auto* query = queries.data() + query_index * kDimension;
    const auto coarse_begin = std::chrono::steady_clock::now();
    const auto interval_lut = build_interval_lut(thresholds, query);
    std::vector<Candidate> coarse_scores;
    for (std::size_t index = offsets[query_index]; index < offsets[query_index + 1]; ++index) {
      const auto id = candidate_ids[index];
      const auto* row = thq.data() + static_cast<std::size_t>(id) * kThqBytes;
      double score = 0.0;
      for (std::size_t byte = 0; byte < kThqBytes; ++byte)
        score += interval_lut[byte * 256 + row[byte]];
      coarse_scores.push_back({score, id});
    }
    const auto coarse = top_ids(std::move(coarse_scores), 128, false);
    const auto coarse_end = std::chrono::steady_clock::now();

    const auto prepare_begin = std::chrono::steady_clock::now();
    const auto base_lut = build_thq_dot_lut(payload, query);
    const auto rotated_query = rotate_query(query);
    const auto tq_lut = build_tq_dot_lut(rotated_query);
    const auto pq_lut = build_pq_dot_lut(payload, query);
    double query_norm = 0.0;
    for (std::size_t dimension = 0; dimension < kDimension; ++dimension)
      query_norm += static_cast<double>(query[dimension]) * query[dimension];
    query_norm = std::sqrt(std::max(query_norm, std::numeric_limits<double>::min()));
    const auto prepare_end = std::chrono::steady_clock::now();

    const auto score_begin = std::chrono::steady_clock::now();
    std::vector<Candidate> tq_scores, pq_scores;
    tq_scores.reserve(coarse.size());
    pq_scores.reserve(coarse.size());
    for (const auto id : coarse) {
      const auto row_index = payload_row(payload, id);
      const auto* thq_row = thq.data() + static_cast<std::size_t>(id) * kThqBytes;
      const auto* sign_row = payload.signs.data() + row_index * kTqBytes;
      const auto* pq_row = payload.pq_codes.data() + row_index * kPqSubspaces;
      double numerator = 0.0;
      for (std::size_t byte = 0; byte < kThqBytes; ++byte)
        numerator += base_lut[byte * 256 + thq_row[byte]];
      double tq_dot = 0.0;
      for (std::size_t byte = 0; byte < kTqBytes; ++byte)
        tq_dot += tq_lut[byte * 256 + sign_row[byte]];
      numerator += tq_dot * payload.scales[row_index];
      if (payload.has_tq_norm) {
        const double denominator = std::max(
            static_cast<double>(payload.tq_norms[row_index]) * query_norm,
            std::numeric_limits<double>::min());
        tq_scores.push_back({numerator / denominator, id});
      }
      for (std::size_t subspace = 0; subspace < kPqSubspaces; ++subspace)
        numerator += pq_lut[subspace * 256 + pq_row[subspace]];
      const double final_denominator = std::max(
          static_cast<double>(payload.final_norms[row_index]) * query_norm,
          std::numeric_limits<double>::min());
      pq_scores.push_back({numerator / final_denominator, id});
    }
    const auto tq_top10 = payload.has_tq_norm
        ? top_ids(tq_scores, 10, true) : std::vector<std::int32_t>{};
    const auto pq_top10 = top_ids(pq_scores, 10, true);
    const auto score_end = std::chrono::steady_clock::now();

    std::cout << "{\"query\":" << query_index << ",\"candidate_count\":"
              << offsets[query_index + 1] - offsets[query_index]
              << ",\"thq4_top128_ids\":";
    emit_ids(coarse);
    std::cout << ",\"tq1_top10_ids\":";
    emit_ids(tq_top10);
    std::cout << ",\"tq1_pq8_top10_ids\":";
    emit_ids(pq_top10);
    std::cout << ",\"tq1_scores\":";
    emit_scores(tq_scores);
    std::cout << ",\"tq1_pq8_scores\":";
    emit_scores(pq_scores);
    std::cout << ",\"timing_ms\":{\"thq4_prefilter\":"
              << milliseconds(coarse_begin, coarse_end)
              << ",\"query_prepare\":" << milliseconds(prepare_begin, prepare_end)
              << ",\"score_top128\":" << milliseconds(score_begin, score_end)
              << ",\"total\":" << milliseconds(coarse_begin, score_end)
              << "},\"side_bytes_per_document\":" << side_bytes
              << ",\"has_tq_intermediate_norm\":"
              << (payload.has_tq_norm ? "true" : "false") << "}\n";
  }
  return 0;
}
}  // namespace

int main(int argc, char** argv) {
  try {
    if (argc == 2 && std::string(argv[1]) == "--self-test") {
      std::vector<float> query(kDimension);
      for (std::size_t index = 0; index < kDimension; ++index)
        query[index] = static_cast<float>((static_cast<int>(index % 17) - 8) * 0.01);
      const auto rotated = rotate_query(query.data());
      double source_norm = 0.0;
      for (const auto value : query)
        source_norm += static_cast<double>(value) * value;
      const double rotated_norm = std::inner_product(rotated.begin(), rotated.end(),
                                                     rotated.begin(), 0.0);
      const double norm_error = std::abs(source_norm - rotated_norm);
      if (norm_error > 1e-10 * std::max(1.0, source_norm))
        throw std::runtime_error("TQ rotation norm parity differs: error=" +
                                 std::to_string(norm_error));
      const auto tq_lut = build_tq_dot_lut(rotated);
      for (std::size_t byte = 0; byte < kTqBytes; ++byte) {
        for (std::size_t packed = 0; packed < 256; ++packed) {
          double expected = 0.0;
          for (std::size_t lane = 0; lane < 8; ++lane)
            expected += (((packed >> lane) & 1U) ? 1.0 : -1.0) *
                        kTqCentroid * rotated[byte * 8 + lane];
          if (std::abs(expected - tq_lut[byte * 256 + packed]) > 1e-14)
            throw std::runtime_error("TQ packed byte LUT parity differs");
        }
      }
      std::cout << "native-tq1-pq-direct-benchmark self-test PASS\n";
      return 0;
    }
    if (argc >= 2 && std::string(argv[1]) == "--candidate-gate")
      return run_gate(argc, argv);
    if (argc >= 2 && std::string(argv[1]) == "--full-flat")
      return run_full_flat(argc, argv);
    throw std::runtime_error("expected --self-test or --candidate-gate");
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
