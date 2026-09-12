#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

#if defined(_MSC_VER)
#include <intrin.h>
#endif

namespace {
using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
constexpr std::size_t kDimension = 384;
constexpr std::size_t kThermometerBytes = 144;
constexpr std::size_t kOrdinalBytes = 96;

template <typename T>
std::vector<T> read_file(const std::string& path) {
  std::ifstream stream(path, std::ios::binary | std::ios::ate);
  if (!stream) throw std::runtime_error("cannot open " + path);
  const auto end = stream.tellg();
  if (end < 0 || static_cast<std::size_t>(end) % sizeof(T) != 0)
    throw std::runtime_error("unaligned payload " + path);
  std::vector<T> result(static_cast<std::size_t>(end) / sizeof(T));
  stream.seekg(0);
  stream.read(reinterpret_cast<char*>(result.data()), end);
  if (!stream) throw std::runtime_error("cannot read " + path);
  return result;
}

std::string path_from(const json& row) { return row.at("path").get<std::string>(); }

std::uint32_t popcount64(std::uint64_t value) {
#if defined(_MSC_VER)
  return static_cast<std::uint32_t>(__popcnt64(value));
#else
  return static_cast<std::uint32_t>(__builtin_popcountll(value));
#endif
}

std::uint16_t thermometer_hamming(const std::uint8_t* lhs,
                                  const std::uint8_t* rhs) {
  std::uint16_t result = 0;
  for (std::size_t i = 0; i < kThermometerBytes; i += 8) {
    std::uint64_t left = 0, right = 0;
    std::memcpy(&left, lhs + i, 8); std::memcpy(&right, rhs + i, 8);
    result = static_cast<std::uint16_t>(result + popcount64(left ^ right));
  }
  return result;
}

std::uint8_t ordinal_level(const std::uint8_t* code, std::size_t coordinate) {
  const std::size_t bit = coordinate * 3;
  const std::size_t byte = bit / 8;
  const unsigned shift = static_cast<unsigned>(bit % 8);
  std::uint16_t window = code[byte];
  if (byte + 1 < kThermometerBytes) window |= static_cast<std::uint16_t>(code[byte + 1]) << 8;
  const std::uint8_t bits = static_cast<std::uint8_t>((window >> shift) & 0x7U);
  return static_cast<std::uint8_t>((bits & 1U) + ((bits >> 1U) & 1U) + ((bits >> 2U) & 1U));
}

void pack_ordinal(const std::uint8_t* thermometer, std::uint8_t* packed) {
  std::fill(packed, packed + kOrdinalBytes, std::uint8_t{0});
  for (std::size_t coordinate = 0; coordinate < kDimension; ++coordinate) {
    const auto level = ordinal_level(thermometer, coordinate);
    const std::size_t bit = coordinate * 2;
    packed[bit / 8] |= static_cast<std::uint8_t>(level << (bit % 8));
  }
}

std::uint8_t packed_level(const std::uint8_t* packed, std::size_t coordinate) {
  const std::size_t bit = coordinate * 2;
  return static_cast<std::uint8_t>((packed[bit / 8] >> (bit % 8)) & 0x3U);
}

struct Input {
  std::size_t documents = 0;
  std::size_t queries = 0;
  std::vector<std::uint8_t> thermometer;
  std::vector<std::uint8_t> packed;
  std::vector<std::uint8_t> query_codes;
  std::vector<float> queries_f32;
  std::vector<float> thresholds;
  std::vector<std::int64_t> teachers;
};

Input load(const std::string& manifest_path, std::size_t query_limit) {
  std::ifstream input(manifest_path);
  if (!input) throw std::runtime_error("manifest missing");
  json manifest; input >> manifest;
  const auto n = manifest.at("documents").get<std::size_t>();
  const auto q = std::min(query_limit, manifest.at("queries").get<std::size_t>());
  const auto& refs = manifest.at("references");
  const auto& outputs = manifest.at("outputs");
  Input result;
  result.documents = n;
  result.queries = q;
  result.thermometer = read_file<std::uint8_t>(path_from(outputs.at("thq4_document_codes")));
  result.query_codes = read_file<std::uint8_t>(path_from(outputs.at("thq4_query_codes")));
  result.queries_f32 = read_file<float>(path_from(refs.at("queries")));
  result.thresholds = read_file<float>(path_from(outputs.at("thq4_thresholds")));
  result.teachers = read_file<std::int64_t>(path_from(refs.at("teacher_ids")));
  if (result.thermometer.size() != n * kThermometerBytes ||
      result.query_codes.size() < q * kThermometerBytes ||
      result.queries_f32.size() < q * kDimension ||
      result.thresholds.size() != kDimension * 3 ||
      result.teachers.size() < q * 10)
    throw std::runtime_error("manifest payload shape differs");
  result.packed.resize(n * kOrdinalBytes);
  for (std::size_t id = 0; id < n; ++id)
    pack_ordinal(result.thermometer.data() + id * kThermometerBytes,
                 result.packed.data() + id * kOrdinalBytes);
  if (result.query_codes.size() < q * kThermometerBytes)
    throw std::runtime_error("query code payload is truncated");
  return result;
}

template <typename Score>
std::vector<std::size_t> topk(const std::vector<Score>& scores, std::size_t k) {
  k = std::min(k, scores.size());
  std::vector<std::size_t> ids(scores.size());
  std::iota(ids.begin(), ids.end(), 0);
  auto less = [&](std::size_t lhs, std::size_t rhs) {
    return scores[lhs] == scores[rhs] ? lhs < rhs : scores[lhs] < scores[rhs];
  };
  if (k < ids.size()) std::nth_element(ids.begin(), ids.begin() + k, ids.end(), less);
  ids.resize(k);
  std::sort(ids.begin(), ids.end(), less);
  return ids;
}

double survival(const std::vector<std::size_t>& ids, const std::vector<std::int64_t>& teachers,
                std::size_t query) {
  std::size_t found = 0;
  for (std::size_t rank = 0; rank < 10; ++rank)
    if (std::find(ids.begin(), ids.end(), static_cast<std::size_t>(teachers[query * 10 + rank])) != ids.end()) ++found;
  return static_cast<double>(found) / 10.0;
}

double percentile(std::vector<double> values, double fraction) {
  std::sort(values.begin(), values.end());
  return values.empty() ? 0.0 : values[static_cast<std::size_t>(fraction * (values.size() - 1))];
}
}  // namespace

int main(int argc, char** argv) {
  try {
    if (argc < 3 || argc > 5) {
      std::cerr << "usage: native_packed_thq_adc_benchmark MANIFEST OUTPUT [query-limit] [repeats]\n";
      return 2;
    }
    const auto query_limit = argc >= 4 ? std::stoull(argv[3]) : 152;
    const auto repeats = argc >= 5 ? std::stoull(argv[4]) : 1;
    if (repeats == 0) return 2;
    const auto input = load(argv[1], query_limit);
    std::vector<double> hamming_ms, ordinal_ms, adc_ms;
    double hamming_survival = 0.0, ordinal_survival = 0.0, adc_survival = 0.0;
    std::vector<std::uint16_t> hamming_scores(input.documents);
    std::vector<std::uint16_t> ordinal_scores(input.documents);
    std::vector<float> adc_scores(input.documents);
    std::vector<std::uint8_t> query_levels(input.queries * kDimension);
    for (std::size_t query = 0; query < input.queries; ++query)
      for (std::size_t coordinate = 0; coordinate < kDimension; ++coordinate)
        query_levels[query * kDimension + coordinate] = ordinal_level(input.query_codes.data() + query * kThermometerBytes, coordinate);
    for (std::size_t repeat = 0; repeat < repeats; ++repeat) {
      for (std::size_t query = 0; query < input.queries; ++query) {
        const auto* query_code = input.query_codes.data() + query * kThermometerBytes;
        std::array<std::array<float, 4>, kDimension> adc_lut{};
        for (std::size_t coordinate = 0; coordinate < kDimension; ++coordinate) {
          const auto* thresholds = input.thresholds.data() + coordinate * 3;
          const auto query_value = input.queries_f32[query * kDimension + coordinate];
          adc_lut[coordinate][0] = std::max(query_value - thresholds[0], 0.0F);
          adc_lut[coordinate][1] = query_value < thresholds[0] ? thresholds[0] - query_value : (query_value >= thresholds[1] ? query_value - thresholds[1] : 0.0F);
          adc_lut[coordinate][2] = query_value < thresholds[1] ? thresholds[1] - query_value : (query_value >= thresholds[2] ? query_value - thresholds[2] : 0.0F);
          adc_lut[coordinate][3] = std::max(thresholds[2] - query_value, 0.0F);
        }
        auto started = Clock::now();
        for (std::size_t id = 0; id < input.documents; ++id)
          hamming_scores[id] = thermometer_hamming(input.thermometer.data() + id * kThermometerBytes, query_code);
        hamming_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - started).count());
        hamming_survival += survival(topk(hamming_scores, 256), input.teachers, query);

        started = Clock::now();
        for (std::size_t id = 0; id < input.documents; ++id) {
          std::uint16_t score = 0;
          for (std::size_t coordinate = 0; coordinate < kDimension; ++coordinate) {
            const auto doc_level = packed_level(input.packed.data() + id * kOrdinalBytes, coordinate);
            const auto query_level = query_levels[query * kDimension + coordinate];
            score = static_cast<std::uint16_t>(score + (doc_level > query_level ? doc_level - query_level : query_level - doc_level));
          }
          ordinal_scores[id] = score;
        }
        ordinal_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - started).count());
        ordinal_survival += survival(topk(ordinal_scores, 256), input.teachers, query);

        for (std::size_t id = 0; id < input.documents; ++id)
          if (hamming_scores[id] != ordinal_scores[id])
            throw std::runtime_error("thermometer/packed ordinal parity failure");

        started = Clock::now();
        for (std::size_t id = 0; id < input.documents; ++id) {
          float score = 0.0F;
          const auto* code = input.packed.data() + id * kOrdinalBytes;
          for (std::size_t coordinate = 0; coordinate < kDimension; ++coordinate) {
            const auto level = packed_level(code, coordinate);
            const auto distance = adc_lut[coordinate][level];
            score += distance * distance;
          }
          adc_scores[id] = score;
        }
        adc_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - started).count());
        adc_survival += survival(topk(adc_scores, 256), input.teachers, query);
      }
    }
    const auto runs = static_cast<double>(input.queries * repeats);
    std::ofstream output(argv[2]);
    output << "{\"schema_version\":1,\"family\":\"native_packed_thq_adc_benchmark_v1\","
           << "\"documents\":" << input.documents << ",\"queries\":" << input.queries
           << ",\"repeats\":" << repeats << ",\"thermometer_bytes_per_document\":144"
           << ",\"packed_ordinal_bytes_per_document\":96,\"k\":256"
           << ",\"hamming_survival_256\":" << hamming_survival / runs
           << ",\"ordinal_l1_survival_256\":" << ordinal_survival / runs
           << ",\"adc_squared_survival_256\":" << adc_survival / runs
           << ",\"hamming_scan_ms_p50\":" << percentile(hamming_ms, .5)
           << ",\"hamming_scan_ms_p95\":" << percentile(hamming_ms, .95)
           << ",\"ordinal_scan_ms_p50\":" << percentile(ordinal_ms, .5)
           << ",\"ordinal_scan_ms_p95\":" << percentile(ordinal_ms, .95)
           << ",\"adc_scan_ms_p50\":" << percentile(adc_ms, .5)
           << ",\"adc_scan_ms_p95\":" << percentile(adc_ms, .95)
           << ",\"production_activation\":false}\n";
    return output ? 0 : 3;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
