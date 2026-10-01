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
#include <stdexcept>
#include <string>
#include <vector>

namespace {
constexpr std::size_t kD = 384, kThqBytes = 96, kQueries = 152, kTop = 128, kK = 10;
struct Candidate { double score; std::int32_t id; };
struct Payload {
  std::uint32_t queries = 0, width = 0, splits = 0, sub = 0, code_bytes = 0;
  std::vector<std::int32_t> ids;
  std::vector<std::uint8_t> codes;
  std::vector<float> norms, centroids, books;
};
template <typename T> std::vector<T> read_file(const std::string& path) {
  std::ifstream stream(path, std::ios::binary | std::ios::ate);
  if (!stream) throw std::runtime_error("cannot open " + path);
  const auto end = stream.tellg();
  if (end < 0 || static_cast<std::size_t>(end) % sizeof(T) != 0) throw std::runtime_error("unaligned input");
  std::vector<T> result(static_cast<std::size_t>(end) / sizeof(T));
  stream.seekg(0); stream.read(reinterpret_cast<char*>(result.data()), end);
  if (!stream) throw std::runtime_error("short input " + path);
  return result;
}
Payload read_payload(const std::string& path) {
  const auto bytes = read_file<std::uint8_t>(path);
  if (bytes.size() < 28 || std::memcmp(bytes.data(), "AMPLSQ01", 8) != 0) throw std::runtime_error("bad PLSQ header");
  auto u32 = [&](std::size_t offset) { std::uint32_t value = 0; std::memcpy(&value, bytes.data() + offset, 4); return value; };
  Payload p{u32(8), u32(12), u32(16), u32(20), u32(24)};
  if (p.queries != kQueries || p.width != kTop || p.splits != 8 || (p.sub != 4 && p.sub != 6) || p.code_bytes != p.splits * p.sub) throw std::runtime_error("PLSQ dimensions differ");
  const std::size_t count = static_cast<std::size_t>(p.queries) * p.width;
  const std::size_t split_dim = kD / p.splits;
  const std::size_t expected = 28 + count * 4 + count * p.code_bytes + count * 4 + kD * 4 * 4 + p.splits * p.sub * 256 * split_dim * 4;
  if (bytes.size() != expected) throw std::runtime_error("PLSQ payload size differs");
  std::size_t offset = 28;
  p.ids.resize(count); std::memcpy(p.ids.data(), bytes.data() + offset, count * 4); offset += count * 4;
  p.codes.resize(count * p.code_bytes); std::memcpy(p.codes.data(), bytes.data() + offset, p.codes.size()); offset += p.codes.size();
  p.norms.resize(count); std::memcpy(p.norms.data(), bytes.data() + offset, count * 4); offset += count * 4;
  p.centroids.resize(kD * 4); std::memcpy(p.centroids.data(), bytes.data() + offset, p.centroids.size() * 4); offset += p.centroids.size() * 4;
  p.books.resize(p.splits * p.sub * 256 * split_dim); std::memcpy(p.books.data(), bytes.data() + offset, p.books.size() * 4);
  auto sorted_ids = p.ids; std::sort(sorted_ids.begin(), sorted_ids.end());
  if (std::adjacent_find(sorted_ids.begin(), sorted_ids.end()) != sorted_ids.end()) throw std::runtime_error("PLSQ payload contains duplicate IDs");
  for (const auto id : p.ids) if (id < 0 || static_cast<std::size_t>(id) >= 1'000'000) throw std::runtime_error("PLSQ payload ID is outside corpus");
  for (const auto norm : p.norms) if (!std::isfinite(norm) || norm <= 0.0F) throw std::runtime_error("PLSQ payload norm is invalid");
  for (const auto value : p.centroids) if (!std::isfinite(value)) throw std::runtime_error("PLSQ centroid is non-finite");
  for (const auto value : p.books) if (!std::isfinite(value)) throw std::runtime_error("PLSQ codebook is non-finite");
  return p;
}
std::array<std::int32_t, kK> top10(const std::array<Candidate, kTop>& values) {
  std::array<std::size_t, kTop> order{}; for (std::size_t i = 0; i < kTop; ++i) order[i] = i;
  std::partial_sort(order.begin(), order.begin() + kK, order.end(), [&](std::size_t a, std::size_t b) {
    return values[a].score > values[b].score || (values[a].score == values[b].score && values[a].id < values[b].id);
  });
  std::array<std::int32_t, kK> result{}; for (std::size_t i = 0; i < kK; ++i) result[i] = values[order[i]].id; return result;
}
double percentile(std::vector<double> values, double p) { std::sort(values.begin(), values.end()); return values[std::min(values.size() - 1, static_cast<std::size_t>(p * values.size()))]; }
double direct_score(const Payload& payload, const std::uint8_t* thq_row,
                    const float* query, double query_norm,
                    const std::uint8_t* code, std::size_t row) {
  double dot = 0.0;
  for (std::size_t d = 0; d < kD; ++d)
    dot += static_cast<double>(payload.centroids[d * 4 +
        ((thq_row[d / 4] >> ((d % 4) * 2)) & 3U)]) * query[d];
  const std::size_t split_dim = kD / payload.splits;
  for (std::size_t split = 0; split < payload.splits; ++split)
    for (std::size_t part = 0; part < payload.sub; ++part) {
      const auto symbol = code[split * payload.sub + part];
      const auto* book = payload.books.data() +
          ((split * payload.sub + part) * 256 + symbol) * split_dim;
      for (std::size_t lane = 0; lane < split_dim; ++lane)
        dot += static_cast<double>(book[lane]) *
               query[split * split_dim + lane];
    }
  return dot / (std::max<double>(payload.norms[row], 1e-30) *
                std::max(query_norm, 1e-30));
}
double dense_score(const Payload& payload, const std::uint8_t* thq_row,
                   const float* query, double query_norm,
                   const std::uint8_t* code, std::size_t row) {
  std::array<double, kD> vector{};
  for (std::size_t d = 0; d < kD; ++d)
    vector[d] = payload.centroids[d * 4 +
        ((thq_row[d / 4] >> ((d % 4) * 2)) & 3U)];
  const std::size_t split_dim = kD / payload.splits;
  for (std::size_t split = 0; split < payload.splits; ++split)
    for (std::size_t part = 0; part < payload.sub; ++part) {
      const auto symbol = code[split * payload.sub + part];
      const auto* book = payload.books.data() +
          ((split * payload.sub + part) * 256 + symbol) * split_dim;
      for (std::size_t lane = 0; lane < split_dim; ++lane)
        vector[split * split_dim + lane] += book[lane];
    }
  double dot = 0.0; for (std::size_t d = 0; d < kD; ++d) dot += vector[d] * query[d];
  return dot / (std::max<double>(payload.norms[row], 1e-30) *
                std::max(query_norm, 1e-30));
}
void self_test() {
  Payload payload; payload.queries = 1; payload.width = 1; payload.splits = 8;
  payload.sub = 4; payload.code_bytes = 32; payload.ids = {7};
  payload.codes.assign(32, 0); payload.norms = {1.0F}; payload.centroids.assign(kD * 4, 0.25F);
  payload.books.assign(8 * 4 * 256 * (kD / 8), 0.5F);
  std::array<std::uint8_t, kThqBytes> thq{}; std::array<float, kD> query{};
  query.fill(1.0F); const double norm = std::sqrt(static_cast<double>(kD));
  const double direct = direct_score(payload, thq.data(), query.data(), norm,
                                     payload.codes.data(), 0);
  const double dense = dense_score(payload, thq.data(), query.data(), norm,
                                   payload.codes.data(), 0);
  if (!std::isfinite(direct) || std::abs(direct - dense) > 1e-9 ||
      std::abs(direct - (864.0 / norm)) > 1e-9)
    throw std::runtime_error("PLSQ direct-score golden self-test failed: " + std::to_string(direct) + "," + std::to_string(dense));
  std::cout << "native-plsq-benchmark self-test PASS\n";
}
int run(int argc, char** argv) {
  if (argc != 7 && argc != 8) throw std::runtime_error("usage: --benchmark payload thq queries expected repeats [raw_output]");
  const auto payload = read_payload(argv[2]);
  const auto thq = read_file<std::uint8_t>(argv[3]);
  const auto queries = read_file<float>(argv[4]);
  const auto expected = read_file<std::int32_t>(argv[5]);
  const auto repeats = static_cast<std::size_t>(std::stoul(argv[6]));
  if (thq.size() != 1'000'000 * kThqBytes || queries.size() != kQueries * kD || expected.size() != kQueries * kK || repeats == 0) throw std::runtime_error("PLSQ fixture shape differs");
  std::vector<double> timings; std::size_t parity = 0; std::uint64_t checksum = 0;
  std::ofstream raw; if (argc == 8) { raw.open(argv[7]); if (!raw) throw std::runtime_error("cannot open raw output"); }
  for (std::size_t repeat = 0; repeat < repeats; ++repeat) for (std::size_t q = 0; q < kQueries; ++q) {
    const auto begin = std::chrono::steady_clock::now();
    std::array<Candidate, kTop> scores{};
    const auto* query = queries.data() + q * kD;
    double query_norm = 0.0; for (std::size_t d = 0; d < kD; ++d) query_norm += static_cast<double>(query[d]) * query[d]; query_norm = std::sqrt(query_norm);
    for (std::size_t row = 0; row < kTop; ++row) {
      const auto id = payload.ids[q * kTop + row];
      const auto* thq_row = thq.data() + static_cast<std::size_t>(id) * kThqBytes;
      const auto* code = payload.codes.data() + (q * kTop + row) * payload.code_bytes;
      scores[row] = {direct_score(payload, thq_row, query, query_norm, code,
                                  q * kTop + row), id};
    }
    const auto result = top10(scores); const auto end = std::chrono::steady_clock::now();
    const double elapsed = std::chrono::duration<double, std::milli>(end - begin).count(); timings.push_back(elapsed);
    for (const auto id : result) checksum = checksum * 1315423911ULL + static_cast<std::uint32_t>(id);
    if (repeat == 0) { bool same = true; for (std::size_t i = 0; i < kK; ++i) same = same && result[i] == expected[q * kK + i]; if (same) ++parity; }
    if (raw) { raw << "{\"repeat\":" << repeat << ",\"query\":" << q << ",\"timing_ms\":" << std::setprecision(12) << elapsed << ",\"top10_ids\":["; for (std::size_t i = 0; i < kK; ++i) { if (i) raw << ','; raw << result[i]; } raw << "]}\n"; }
  }
  std::cout << std::fixed << std::setprecision(6) << "{\"status\":\"EXECUTED\",\"score_mode\":\"direct_packed_dot\",\"profile\":\"PLSQ8x" << payload.sub << "x8\",\"queries\":152,\"repeats\":" << repeats << ",\"parity\":" << parity << ",\"parity_total\":152,\"checksum\":" << checksum << ",\"p50_ms\":" << percentile(timings, .5) << ",\"p95_ms\":" << percentile(timings, .95) << ",\"p99_ms\":" << percentile(timings, .99) << "}\n";
  return 0;
}
}
int main(int argc, char** argv) { try { if (argc == 2 && std::string(argv[1]) == "--self-test") { self_test(); return 0; } if (argc >= 2 && std::string(argv[1]) == "--benchmark") return run(argc, argv); throw std::runtime_error("expected --self-test or --benchmark"); } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; } }
