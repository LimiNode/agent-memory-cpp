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
#include <unordered_map>
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
struct FlatPayload { std::size_t count=0, splits=8, sub=6, code_bytes=48; std::vector<std::uint8_t> codes; std::vector<float> norms, centroids, books; };
struct ThqCandidate { double score; std::int32_t id; };
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
  // The same document may legitimately occur in several query-local candidate
  // pages.  Uniqueness is therefore enforced per query row, not across the
  // whole payload; only duplicate IDs within one 128-wide page are malformed.
  for (std::size_t query = 0; query < p.queries; ++query) {
    auto begin = p.ids.begin() + static_cast<std::ptrdiff_t>(query * p.width);
    auto end = begin + p.width;
    auto sorted_ids = std::vector<std::int32_t>(begin, end);
    std::sort(sorted_ids.begin(), sorted_ids.end());
    if (std::adjacent_find(sorted_ids.begin(), sorted_ids.end()) != sorted_ids.end())
      throw std::runtime_error("PLSQ payload contains duplicate IDs within query page");
  }
  for (const auto id : p.ids)
    if (id < 0 || static_cast<std::size_t>(id) >= 1'000'000)
      throw std::runtime_error("PLSQ payload ID is outside corpus");
  for (const auto norm : p.norms) if (!std::isfinite(norm) || norm <= 0.0F) throw std::runtime_error("PLSQ payload norm is invalid");
  for (const auto value : p.centroids) if (!std::isfinite(value)) throw std::runtime_error("PLSQ centroid is non-finite");
  for (const auto value : p.books) if (!std::isfinite(value)) throw std::runtime_error("PLSQ codebook is non-finite");
  return p;
}
FlatPayload read_flat_payload(const std::string& path) {
  const auto bytes = read_file<std::uint8_t>(path);
  if (bytes.size() < 24 || std::memcmp(bytes.data(), "AMPLSQF1", 8) != 0) throw std::runtime_error("bad full PLSQ header");
  auto u32=[&](std::size_t off){std::uint32_t v=0;std::memcpy(&v,bytes.data()+off,4);return v;};
  FlatPayload p; p.count=u32(8); p.splits=u32(12); p.sub=u32(16); p.code_bytes=u32(20);
  if (p.count != 1'000'000 || p.splits != 8 || (p.sub != 4 && p.sub != 6) || p.code_bytes != p.splits*p.sub) throw std::runtime_error("full PLSQ dimensions differ");
  const std::size_t split_dim=kD/p.splits, expected=24+p.count*p.code_bytes+p.count*4+kD*4*4+p.splits*p.sub*256*split_dim*4;
  if (bytes.size()!=expected) throw std::runtime_error("full PLSQ payload size differs");
  std::size_t off=24; p.codes.resize(p.count*p.code_bytes); std::memcpy(p.codes.data(),bytes.data()+off,p.codes.size()); off+=p.codes.size(); p.norms.resize(p.count); std::memcpy(p.norms.data(),bytes.data()+off,p.norms.size()*4); off+=p.norms.size()*4; p.centroids.resize(kD*4); std::memcpy(p.centroids.data(),bytes.data()+off,p.centroids.size()*4); off+=p.centroids.size()*4; p.books.resize(p.splits*p.sub*256*split_dim); std::memcpy(p.books.data(),bytes.data()+off,p.books.size()*4); return p;
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
                   const std::uint8_t* code, std::size_t row);

std::vector<std::uint64_t> read_u64(const std::string& path) {
  return read_file<std::uint64_t>(path);
}
int run_flat(int argc, char** argv) {
  if (argc != 8 && argc != 9) throw std::runtime_error("usage: --flat payload thq queries query_count warmups repeats [raw_output]");
  const auto payload = read_flat_payload(argv[2]);
  const auto thq = read_file<std::uint8_t>(argv[3]);
  const auto queries = read_file<float>(argv[4]);
  const auto qcount = static_cast<std::size_t>(std::stoul(argv[5]));
  const auto warmups = static_cast<std::size_t>(std::stoul(argv[6]));
  const auto repeats = static_cast<std::size_t>(std::stoul(argv[7]));
  if (thq.size() != 1'000'000 * kThqBytes || queries.size() < qcount * kD ||
      qcount == 0 || warmups == 0 || repeats == 0)
    throw std::runtime_error("full PLSQ fixture shape differs");
  std::vector<double> timings;
  timings.reserve(qcount * repeats);
  std::ofstream raw;
  if (argc == 9) { raw.open(argv[8]); if (!raw) throw std::runtime_error("cannot open flat raw output"); }
  const auto better_local = [](const Candidate& a, const Candidate& b) {
    return a.score > b.score || (a.score == b.score && a.id < b.id);
  };
  for (std::size_t q = 0; q < qcount; ++q) {
    const float* query = queries.data() + q * kD;
    double qnorm = 0.0;
    for (std::size_t d = 0; d < kD; ++d) qnorm += static_cast<double>(query[d]) * query[d];
    qnorm = std::sqrt(qnorm);
    for (std::size_t rep = 0; rep < warmups + repeats; ++rep) {
      const auto start = std::chrono::steady_clock::now();
      std::array<float, kThqBytes * 256> base{};
      for (std::size_t b = 0; b < kThqBytes; ++b)
        for (std::size_t packed = 0; packed < 256; ++packed)
          for (std::size_t lane = 0; lane < 4; ++lane) {
            const auto d = b * 4 + lane;
            base[b * 256 + packed] += payload.centroids[d * 4 + ((packed >> (lane * 2)) & 3U)] * query[d];
          }
      const auto sd = kD / payload.splits;
      std::vector<float> lut(payload.splits * payload.sub * 256);
      for (std::size_t s = 0; s < payload.splits; ++s)
        for (std::size_t part = 0; part < payload.sub; ++part)
          for (std::size_t sym = 0; sym < 256; ++sym) {
            double dot = 0.0;
            const auto* book = payload.books.data() + ((s * payload.sub + part) * 256 + sym) * sd;
            for (std::size_t lane = 0; lane < sd; ++lane) dot += static_cast<double>(book[lane]) * query[s * sd + lane];
            lut[(s * payload.sub + part) * 256 + sym] = static_cast<float>(dot);
          }
      std::array<Candidate, 10> top{};
      std::size_t topn = 0;
      for (std::size_t row = 0; row < payload.count; ++row) {
        double dot = 0.0;
        const auto* tr = thq.data() + row * kThqBytes;
        for (std::size_t b = 0; b < kThqBytes; ++b) dot += base[b * 256 + tr[b]];
        const auto* code = payload.codes.data() + row * payload.code_bytes;
        for (std::size_t i = 0; i < payload.code_bytes; ++i) dot += lut[i * 256 + code[i]];
        const Candidate c{dot / (std::max<double>(payload.norms[row], 1e-30) * std::max(qnorm, 1e-30)), static_cast<std::int32_t>(row)};
        if (topn < 10) top[topn++] = c;
        else { std::size_t worst = 0; for (std::size_t i = 1; i < 10; ++i) if (better_local(top[worst], top[i])) worst = i; if (better_local(c, top[worst])) top[worst] = c; }
      }
      std::sort(top.begin(), top.end(), better_local);
      if (rep >= warmups) {
        const double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
        timings.push_back(elapsed);
        if (raw) {
          raw << "{\"repeat\":" << (rep - warmups) << ",\"query\":" << q << ",\"timing_ms\":" << std::setprecision(12) << elapsed << ",\"top10_ids\":[";
          for (std::size_t i = 0; i < top.size(); ++i) { if (i) raw << ','; raw << top[i].id; }
          raw << "]}\n";
        }
      }
    }
  }
  std::sort(timings.begin(), timings.end());
  const auto pct = [&](double p) { return timings[std::min(timings.size() - 1, static_cast<std::size_t>(p * timings.size()))]; };
  std::cout << std::fixed << std::setprecision(6) << "{\"status\":\"EXECUTED\",\"scope\":\"full 1M packed PLSQ flat scan\",\"queries\":" << qcount << ",\"repeats\":" << repeats << ",\"p50_ms\":" << pct(.5) << ",\"p95_ms\":" << pct(.95) << ",\"p99_ms\":" << pct(.99) << "}\n";
  return 0;
}

int run_matched(int argc, char** argv) {
  if (argc != 11)
    throw std::runtime_error("usage: --matched payload thq thresholds candidate_flat offsets queries expected repeats raw_output");
  const auto payload = read_payload(argv[2]);
  const auto thq = read_file<std::uint8_t>(argv[3]);
  const auto thresholds = read_file<float>(argv[4]);
  const auto flat = read_file<std::uint8_t>(argv[5]);
  const auto offsets = read_u64(argv[6]);
  const auto queries = read_file<float>(argv[7]);
  const auto expected = read_file<std::int32_t>(argv[8]);
  const auto repeats = static_cast<std::size_t>(std::stoul(argv[9]));
  if (thq.size() != 1'000'000 * kThqBytes || thresholds.size() != kD * 3 ||
      offsets.size() != kQueries + 1 || queries.size() != kQueries * kD ||
      expected.size() != kQueries * kK ||
      flat.size() % 148 != 0 || offsets.back() != flat.size() / 148 || repeats == 0)
    throw std::runtime_error("matched PLSQ fixture shape differs");
  std::ofstream raw(argv[10]);
  if (!raw) throw std::runtime_error("cannot open matched raw output");
  std::vector<std::unordered_map<std::int32_t, std::size_t>> row_by_query(kQueries);
  for (std::size_t q = 0; q < kQueries; ++q) {
    const auto first = static_cast<std::size_t>(offsets[q]);
    const auto last = static_cast<std::size_t>(offsets[q + 1]);
    for (std::size_t pos = first; pos < last; ++pos) {
      std::int32_t id = 0;
      std::memcpy(&id, flat.data() + pos * 148, sizeof(id));
      auto it = std::find(payload.ids.begin() + static_cast<std::ptrdiff_t>(q * kTop),
                          payload.ids.begin() + static_cast<std::ptrdiff_t>((q + 1) * kTop), id);
      if (it == payload.ids.begin() + static_cast<std::ptrdiff_t>((q + 1) * kTop))
        throw std::runtime_error("candidate ID is not represented in packed PLSQ payload");
      row_by_query[q].emplace(id, static_cast<std::size_t>(it - payload.ids.begin()));
    }
  }
  std::vector<double> timings;
  std::size_t parity = 0;
  for (std::size_t repeat = 0; repeat < repeats; ++repeat) {
    for (std::size_t q = 0; q < kQueries; ++q) {
      const auto begin = std::chrono::steady_clock::now();
      std::array<float, kD * 4> coordinate{};
      for (std::size_t d = 0; d < kD; ++d) {
        for (std::size_t level = 0; level < 4; ++level) {
          const float lo = level == 0 ? -std::numeric_limits<float>::infinity() : thresholds[d * 3 + level - 1];
          const float hi = level == 3 ? std::numeric_limits<float>::infinity() : thresholds[d * 3 + level];
          const float value = queries[q * kD + d];
          const float delta = value < lo ? lo - value : (value > hi ? value - hi : 0.0f);
          coordinate[d * 4 + level] = delta * delta;
        }
      }
      const auto first = static_cast<std::size_t>(offsets[q]);
      const auto last = static_cast<std::size_t>(offsets[q + 1]);
      std::vector<ThqCandidate> coarse;
      coarse.reserve(last - first);
      for (std::size_t pos = first; pos < last; ++pos) {
        std::int32_t id = 0;
        std::memcpy(&id, flat.data() + pos * 148, sizeof(id));
        const auto* row = thq.data() + static_cast<std::size_t>(id) * kThqBytes;
        double score = 0.0;
        for (std::size_t b = 0; b < kThqBytes; ++b) {
          const auto packed = row[b];
          for (std::size_t lane = 0; lane < 4; ++lane)
            score += coordinate[(b * 4 + lane) * 4 + ((packed >> (lane * 2)) & 3U)];
        }
        coarse.push_back({score, id});
      }
      if (coarse.size() < kTop) throw std::runtime_error("matched candidate page is narrower than top128");
      std::partial_sort(coarse.begin(), coarse.begin() + kTop, coarse.end(), [](const auto& a, const auto& b) {
        return a.score < b.score || (a.score == b.score && a.id < b.id);
      });
      const auto query = queries.data() + q * kD;
      double query_norm = 0.0; for (std::size_t d = 0; d < kD; ++d) query_norm += static_cast<double>(query[d]) * query[d]; query_norm = std::sqrt(query_norm);
      std::array<Candidate, kTop> scores{};
      for (std::size_t i = 0; i < kTop; ++i) {
        const auto id = coarse[i].id;
        const auto mapping = row_by_query[q].find(id);
        if (mapping == row_by_query[q].end())
          throw std::runtime_error("matched THQ top128 is not represented in packed payload");
        const auto row = mapping->second;
        scores[i] = {direct_score(payload, thq.data() + static_cast<std::size_t>(id) * kThqBytes, query, query_norm, payload.codes.data() + row * payload.code_bytes, row), id};
      }
      const auto result = top10(scores);
      const auto elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - begin).count();
      timings.push_back(elapsed);
      if (repeat == 0) {
        bool same = true;
        for (std::size_t i = 0; i < kK; ++i) same = same && result[i] == expected[q * kK + i];
        if (same) ++parity;
      }
      raw << "{\"repeat\":" << repeat << ",\"query\":" << q << ",\"timing_ms\":" << std::setprecision(12) << elapsed << ",\"top10_ids\":[";
      for (std::size_t i = 0; i < kK; ++i) { if (i) raw << ','; raw << result[i]; }
      raw << "]}\n";
    }
  }
  std::cout << std::fixed << std::setprecision(6) << "{\"status\":\"EXECUTED\",\"scope\":\"R4 candidate stream -> THQ top128 -> PLSQ packed scorer\",\"queries\":152,\"repeats\":" << repeats << ",\"parity\":" << parity << ",\"p50_ms\":" << percentile(timings, .5) << ",\"p95_ms\":" << percentile(timings, .95) << ",\"p99_ms\":" << percentile(timings, .99) << "}\n";
  return 0;
}
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
int main(int argc, char** argv) { try { if (argc == 2 && std::string(argv[1]) == "--self-test") { self_test(); return 0; } if (argc >= 2 && std::string(argv[1]) == "--benchmark") return run(argc, argv); if (argc >= 2 && std::string(argv[1]) == "--matched") return run_matched(argc, argv); if (argc >= 2 && std::string(argv[1]) == "--flat") return run_flat(argc, argv); throw std::runtime_error("expected --self-test, --benchmark, --matched or --flat"); } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; } }
