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
#include <string>
#include <vector>

namespace {
constexpr std::size_t D = 384, N = 1'000'000, SignBytes = 48, ThqBytes = 96;
constexpr std::size_t K = 10;
struct Candidate { double score; std::int32_t id; };
bool better(const Candidate& a, const Candidate& b) { return a.score > b.score || (a.score == b.score && a.id < b.id); }
template <typename T> std::vector<T> read(const std::string& path) {
  std::ifstream in(path, std::ios::binary | std::ios::ate); if (!in) throw std::runtime_error("cannot open " + path);
  auto size = in.tellg(); if (size < 0 || static_cast<std::size_t>(size) % sizeof(T) != 0) throw std::runtime_error("unaligned input");
  std::vector<T> out(static_cast<std::size_t>(size) / sizeof(T)); in.seekg(0); in.read(reinterpret_cast<char*>(out.data()), size); if (!in) throw std::runtime_error("cannot read " + path); return out;
}
struct Payload { std::vector<float> centroids; std::vector<std::uint8_t> rows; };
Payload load_payload(const std::string& path) {
  auto bytes = read<std::uint8_t>(path); if (bytes.size() < 24 || std::memcmp(bytes.data(), "AMTQF01\0", 8) != 0) throw std::runtime_error("TQ1 full header differs");
  std::uint32_t dim = 0, count = 0, width = 0, flags = 0; std::memcpy(&dim, bytes.data() + 8, 4); std::memcpy(&count, bytes.data() + 12, 4); std::memcpy(&width, bytes.data() + 16, 4); std::memcpy(&flags, bytes.data() + 20, 4);
  if (dim != D || count != N || width != SignBytes || flags != 0) throw std::runtime_error("TQ1 full dimensions differ");
  const std::size_t expected = 24 + D * 4 * 4 + N * (SignBytes + 4); if (bytes.size() != expected) throw std::runtime_error("TQ1 full payload size differs");
  Payload p; p.centroids.resize(D * 4); std::memcpy(p.centroids.data(), bytes.data() + 24, p.centroids.size() * sizeof(float)); p.rows.assign(bytes.begin() + 24 + p.centroids.size() * sizeof(float), bytes.end()); return p;
}
std::uint64_t permute_step(std::uint64_t state, std::size_t i, std::vector<std::int64_t>& values) {
  state = state * 6364136223846793005ULL + 1442695040888963407ULL; const std::size_t j = static_cast<std::size_t>((state >> 32) % (i + 1)); std::swap(values[i], values[j]); return state;
}
std::vector<std::int64_t> permutation(std::uint64_t seed) { std::vector<std::int64_t> values(D); for (std::size_t i = 0; i < D; ++i) values[i] = static_cast<std::int64_t>(i); std::uint64_t state = seed; for (std::size_t i = D - 1; i > 0; --i) state = permute_step(state, i, values); return values; }
void wht(std::vector<double>& x) { for (std::size_t offset = 0, left = D; left;) { const std::size_t size = std::size_t(1) << (63 - static_cast<std::size_t>(__builtin_clzll(left))); for (std::size_t h = 1; h < size; h <<= 1) for (std::size_t j = 0; j < h; ++j) for (std::size_t k = offset + j; k < offset + size; k += 2 * h) { const double a = x[k], b = x[k + h]; x[k] = a + b; x[k + h] = a - b; } const double scale = std::sqrt(static_cast<double>(size)); for (std::size_t k = offset; k < offset + size; ++k) x[k] /= scale; offset += size; left -= size; } }
std::vector<double> rotate(const float* input) { std::vector<double> out(D); for (std::size_t i = 0; i < D; ++i) out[i] = input[i]; wht(out); for (const auto seed : {654605292835415893ULL, 8636605637963351413ULL, 1775280196666917949ULL}) { auto p = permutation(seed); std::vector<double> tmp(D); for (std::size_t i = 0; i < D; ++i) tmp[i] = out[p[i]]; out.swap(tmp); wht(out); } return out; }
double pct(std::vector<double> values, double f) { std::sort(values.begin(), values.end()); return values[std::min(values.size() - 1, static_cast<std::size_t>(f * values.size()))]; }
}
int main(int argc, char** argv) {
  try {
    if (argc < 6 || argc > 7) throw std::runtime_error("usage: native-tq1-full-benchmark payload thq queries warmups repeats [raw]");
    const auto payload = load_payload(argv[1]); const auto thq = read<std::uint8_t>(argv[2]); const auto queries = read<float>(argv[3]); const std::size_t warmups = std::stoul(argv[4]), repeats = std::stoul(argv[5]);
    if (thq.size() != N * ThqBytes || queries.size() < 152 * D || warmups == 0 || repeats == 0) throw std::runtime_error("fixture shape differs");
    std::ofstream raw; if (argc == 7) { raw.open(argv[6]); if (!raw) throw std::runtime_error("cannot open raw"); }
    std::vector<double> timings; timings.reserve(152 * repeats);
    for (std::size_t q = 0; q < 152; ++q) {
      const float* query = queries.data() + q * D; double qnorm = 0.0; for (std::size_t d = 0; d < D; ++d) qnorm += static_cast<double>(query[d]) * query[d]; qnorm = std::sqrt(qnorm);
      const auto rq = rotate(query); std::array<float, ThqBytes * 256> base{}; std::array<float, SignBytes * 256> residual{};
      for (std::size_t b = 0; b < ThqBytes; ++b) for (std::size_t v = 0; v < 256; ++v) for (std::size_t lane = 0; lane < 4; ++lane) base[b * 256 + v] += payload.centroids[(b * 4 + lane) * 4 + ((v >> (lane * 2)) & 3U)] * query[b * 4 + lane];
      constexpr float c = 0.7978846F; for (std::size_t b = 0; b < SignBytes; ++b) for (std::size_t v = 0; v < 256; ++v) for (std::size_t bit = 0; bit < 8; ++bit) residual[b * 256 + v] += ((v >> bit) & 1U) ? c * static_cast<float>(rq[b * 8 + bit]) : -c * static_cast<float>(rq[b * 8 + bit]);
      for (std::size_t rep = 0; rep < warmups + repeats; ++rep) {
        const auto begin = std::chrono::steady_clock::now(); std::array<Candidate, K> top{}; std::size_t topn = 0;
        for (std::size_t row = 0; row < N; ++row) { const auto* bytes = payload.rows.data() + row * (SignBytes + 4); double base_score = 0.0, residual_score = 0.0; const auto* tr = thq.data() + row * ThqBytes; for (std::size_t b = 0; b < ThqBytes; ++b) base_score += base[b * 256 + tr[b]]; for (std::size_t b = 0; b < SignBytes; ++b) residual_score += residual[b * 256 + bytes[b]]; float scale = 0.0F; std::memcpy(&scale, bytes + SignBytes, sizeof(float)); const double score = (base_score + residual_score * scale) / std::max(qnorm, 1e-30); Candidate cnd{score, static_cast<std::int32_t>(row)}; if (topn < K) top[topn++] = cnd; else { std::size_t worst = 0; for (std::size_t i = 1; i < K; ++i) if (better(top[worst], top[i])) worst = i; if (better(cnd, top[worst])) top[worst] = cnd; } }
        std::sort(top.begin(), top.end(), better); if (rep >= warmups) { timings.push_back(std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - begin).count()); if (raw) { raw << "{\"query\":" << q << ",\"repeat\":" << (rep - warmups) << ",\"timing_ms\":" << timings.back() << ",\"top10_ids\":["; for (std::size_t i = 0; i < K; ++i) { if (i) raw << ','; raw << top[i].id; } raw << "]}\n"; } }
      }
    }
    std::cout << std::fixed << std::setprecision(6) << "{\"status\":\"EXECUTED\",\"codec\":\"TQ1\",\"mode\":\"full_flat_1m\",\"queries\":152,\"repeats\":" << repeats << ",\"p50_ms\":" << pct(timings,.5) << ",\"p95_ms\":" << pct(timings,.95) << ",\"p99_ms\":" << pct(timings,.99) << "}\n";
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
