#if defined(_WIN32)
#include <windows.h>
#ifdef small
#undef small
#endif
#endif
#include <mdbx_containers/KeyValueTable.hpp>

#ifdef small
#undef small
#endif

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
constexpr std::size_t kDimension = 384;
constexpr std::size_t kThqBytes = 96;
constexpr std::size_t kInt8Bytes = 384;
constexpr std::size_t kRowBytes = kThqBytes + kInt8Bytes + sizeof(float);
constexpr std::size_t kSegmentRows = 4096;

template <class T> std::vector<T> read_binary(const std::filesystem::path& path) {
  std::ifstream stream(path, std::ios::binary | std::ios::ate);
  if (!stream) throw std::runtime_error("cannot open " + path.string());
  const auto end = stream.tellg();
  if (end < 0 || static_cast<std::size_t>(end) % sizeof(T) != 0)
    throw std::runtime_error("unaligned input " + path.string());
  std::vector<T> values(static_cast<std::size_t>(end) / sizeof(T));
  stream.seekg(0);
  stream.read(reinterpret_cast<char*>(values.data()), static_cast<std::streamsize>(values.size() * sizeof(T)));
  if (!stream) throw std::runtime_error("short input " + path.string());
  return values;
}

void write_u32(std::string& key, std::uint32_t value) {
  key.resize(4);
  for (std::size_t byte = 0; byte < 4; ++byte)
    key[byte] = static_cast<char>(value >> (byte * 8));
}

std::string key(std::uint32_t value) { std::string result; write_u32(result, value); return result; }

struct Row final { std::int8_t code[kInt8Bytes]; float scale; };

std::string packed_row(const std::vector<std::uint8_t>& thq, const std::vector<std::int8_t>& codes,
                       const std::vector<float>& scales, std::size_t id) {
  std::string result(kRowBytes, '\0');
  std::copy_n(reinterpret_cast<const char*>(thq.data() + id * kThqBytes), kThqBytes, result.data());
  std::copy_n(reinterpret_cast<const char*>(codes.data() + id * kInt8Bytes), kInt8Bytes,
              result.data() + kThqBytes);
  std::copy_n(reinterpret_cast<const char*>(scales.data() + id), sizeof(float), result.data() + kThqBytes + kInt8Bytes);
  return result;
}

std::vector<std::uint32_t> top10(const float* query, const std::vector<std::uint32_t>& ids,
                                 const std::unordered_map<std::uint32_t, Row>& rows) {
  struct Score { float value; std::uint32_t id; };
  std::vector<Score> scores;
  scores.reserve(ids.size());
  for (const auto id : ids) {
    const auto it = rows.find(id);
    if (it == rows.end()) throw std::runtime_error("missing payload row");
    float score = 0.0F;
    for (std::size_t dimension = 0; dimension < kDimension; ++dimension)
      score += query[dimension] * static_cast<float>(it->second.code[dimension]);
    scores.push_back({score * it->second.scale, id});
  }
  std::sort(scores.begin(), scores.end(), [](const Score& left, const Score& right) {
    if (left.value != right.value) return left.value > right.value;
    return left.id < right.id;
  });
  if (scores.size() > 10) scores.resize(10);
  std::vector<std::uint32_t> result;
  for (const auto score : scores) result.push_back(score.id);
  return result;
}

class Store final {
 public:
  Store(const std::filesystem::path& path, const std::string& mode, bool recreate)
      : path_(path), mode_(mode) {
    if (recreate) std::filesystem::remove(path_);
    mdbxc::Config config;
    config.pathname = path_.string();
    config.max_dbs = 4;
    config.no_subdir = true;
    config.relative_to_exe = false;
    connection_ = mdbxc::Connection::create(config);
    table_ = std::make_unique<mdbxc::KeyValueTable<std::string, std::string>>(connection_, "payload");
  }

  void materialize(const std::vector<std::uint8_t>& thq, const std::vector<std::int8_t>& codes,
                   const std::vector<float>& scales, std::size_t documents) {
    if (thq.size() != documents * kThqBytes || codes.size() != documents * kInt8Bytes || scales.size() != documents)
      throw std::runtime_error("materialization input shape differs");
    auto transaction = connection_->transaction(mdbxc::TransactionMode::WRITABLE);
    if (mode_ == "row") {
      for (std::size_t id = 0; id < documents; ++id)
        table_->insert_or_assign(key(static_cast<std::uint32_t>(id)), packed_row(thq, codes, scales, id), transaction);
    } else if (mode_ == "segment") {
      for (std::size_t segment = 0; segment * kSegmentRows < documents; ++segment) {
        const auto begin = segment * kSegmentRows;
        const auto end = std::min(documents, begin + kSegmentRows);
        std::string payload((end - begin) * kRowBytes, '\0');
        for (std::size_t id = begin; id < end; ++id) {
          const auto row = packed_row(thq, codes, scales, id);
          std::copy(row.begin(), row.end(), payload.begin() + (id - begin) * kRowBytes);
        }
        table_->insert_or_assign(key(static_cast<std::uint32_t>(segment)), payload, transaction);
      }
    } else throw std::runtime_error("mode must be row or segment");
    transaction.commit();
  }

  template <class Callback> void read_rows(const std::vector<std::uint32_t>& ids, Callback&& callback) const {
    auto transaction = connection_->transaction(mdbxc::TransactionMode::READ_ONLY);
    std::unordered_map<std::uint32_t, Row> rows;
    rows.reserve(ids.size());
    if (mode_ == "row") {
      for (const auto id : ids) {
        const auto payload = table_->find(key(id), transaction);
        if (!payload || payload->size() != kRowBytes) throw std::runtime_error("row payload missing or malformed");
        Row row{};
        std::copy_n(reinterpret_cast<const std::int8_t*>(payload->data() + kThqBytes), kInt8Bytes, row.code);
        std::copy_n(reinterpret_cast<const char*>(payload->data() + kThqBytes + kInt8Bytes), sizeof(float), reinterpret_cast<char*>(&row.scale));
        rows.emplace(id, row);
      }
    } else {
      std::unordered_map<std::uint32_t, std::string> segments;
      for (const auto id : ids) {
        const auto segment = id / static_cast<std::uint32_t>(kSegmentRows);
        if (segments.find(segment) == segments.end()) {
          const auto payload = table_->find(key(segment), transaction);
          if (!payload || payload->size() % kRowBytes != 0) throw std::runtime_error("segment payload missing or malformed");
          segments.emplace(segment, *payload);
        }
        const auto& payload = segments.at(segment);
        const auto offset = (id % kSegmentRows) * kRowBytes;
        if (offset + kRowBytes > payload.size()) throw std::runtime_error("segment row offset differs");
        Row row{};
        std::copy_n(reinterpret_cast<const std::int8_t*>(payload.data() + offset + kThqBytes), kInt8Bytes, row.code);
        std::copy_n(reinterpret_cast<const char*>(payload.data() + offset + kThqBytes + kInt8Bytes), sizeof(float), reinterpret_cast<char*>(&row.scale));
        rows.emplace(id, row);
      }
    }
    callback(rows);
    transaction.commit();
  }

  std::size_t bytes() const { return static_cast<std::size_t>(std::filesystem::file_size(path_)); }

 private:
  std::filesystem::path path_;
  std::string mode_;
  std::shared_ptr<mdbxc::Connection> connection_;
  std::unique_ptr<mdbxc::KeyValueTable<std::string, std::string>> table_;
};

double percentile(std::vector<double> values, double p) {
  if (values.empty()) return 0.0;
  std::sort(values.begin(), values.end());
  return values[std::min(values.size() - 1, static_cast<std::size_t>(p * values.size()))];
}

void lifecycle_smoke(const std::filesystem::path& path) {
  std::filesystem::remove(path);
  {
    mdbxc::Config config; config.pathname = path.string(); config.max_dbs = 4; config.no_subdir = true; config.relative_to_exe = false;
    auto connection = mdbxc::Connection::create(config);
    auto table = std::make_unique<mdbxc::KeyValueTable<std::string, std::string>>(connection, "lifecycle");
    { auto tx = connection->transaction(mdbxc::TransactionMode::WRITABLE); table->insert_or_assign("active", "g1", tx); table->insert_or_assign("doc:1", "live", tx); tx.commit(); }
    { auto tx = connection->transaction(mdbxc::TransactionMode::WRITABLE); table->insert_or_assign("doc:1", "updated", tx); table->insert_or_assign("doc:2", "tombstone", tx); tx.commit(); }
    { auto tx = connection->transaction(mdbxc::TransactionMode::READ_ONLY); const auto value = table->find("doc:1", tx); if (!value || *value != "updated") throw std::runtime_error("update visibility failed"); tx.commit(); }
    { auto tx = connection->transaction(mdbxc::TransactionMode::WRITABLE); table->insert_or_assign("generation:g2", "ready", tx); table->insert_or_assign("active", "g2", tx); tx.commit(); }
    { auto tx = connection->transaction(mdbxc::TransactionMode::READ_ONLY); const auto active = table->find("active", tx); const auto tombstone = table->find("doc:2", tx); if (!active || *active != "g2" || !tombstone || *tombstone != "tombstone") throw std::runtime_error("publication/tombstone failed"); tx.commit(); }
  }
  std::filesystem::remove(path);
}
}

int main(int argc, char** argv) {
  try {
    if (argc == 3 && std::string(argv[1]) == "--lifecycle-smoke") { lifecycle_smoke(argv[2]); std::cout << "{\"status\":\"PASS\"}\n"; return 0; }
    if (argc == 8 && std::string(argv[1]) == "--materialize") {
      const std::string mode = argv[2];
      const std::filesystem::path db = argv[3];
      const auto thq = read_binary<std::uint8_t>(argv[4]);
      const auto codes = read_binary<std::int8_t>(argv[5]);
      const auto scales = read_binary<float>(argv[6]);
      const auto documents = static_cast<std::size_t>(std::stoull(argv[7]));
      Store store(db, mode, true);
      const auto begin = Clock::now();
      store.materialize(thq, codes, scales, documents);
      const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
      std::cout << std::fixed << std::setprecision(3) << "{\"status\":\"MATERIALIZED\",\"mode\":\""
                << mode << "\",\"documents\":" << documents << ",\"db_bytes\":" << store.bytes()
                << ",\"materialize_ms\":" << elapsed << "}\n";
      return 0;
    }
    if (argc < 10 || std::string(argv[1]) != "--benchmark") {
      std::cerr << "usage: --benchmark row|segment db thq codes scales queries candidates expected query_count repeats\n"; return 2;
    }
    const std::string mode = argv[2];
    const std::filesystem::path db = argv[3];
    const auto thq = read_binary<std::uint8_t>(argv[4]);
    const auto codes = read_binary<std::int8_t>(argv[5]);
    const auto scales = read_binary<float>(argv[6]);
    const auto queries = read_binary<float>(argv[7]);
    const auto candidates = read_binary<std::uint32_t>(argv[8]);
    const auto expected = read_binary<std::uint32_t>(argv[9]);
    const std::size_t query_count = argc > 10 ? std::stoull(argv[10]) : expected.size() / 10;
    const std::size_t repeats = argc > 11 ? std::stoull(argv[11]) : 5;
    if (query_count == 0 || queries.size() < query_count * kDimension || candidates.size() != query_count * 128 || expected.size() != query_count * 10)
      throw std::runtime_error("serving fixture shape differs");
    const auto documents = codes.size() / kInt8Bytes;
    if (thq.size() != documents * kThqBytes || scales.size() != documents) throw std::runtime_error("payload shape differs");
    Store store(db, mode, false);
    const auto first_start = Clock::now();
    std::size_t parity = 0;
    store.read_rows(std::vector<std::uint32_t>(candidates.begin(), candidates.begin() + 128), [&](const auto&) {});
    const double reopen_ms = std::chrono::duration<double, std::milli>(Clock::now() - first_start).count();
    std::vector<double> timings; timings.reserve(query_count * repeats);
    std::uint64_t checksum = 0;
    for (std::size_t repeat = 0; repeat < repeats; ++repeat) for (std::size_t query = 0; query < query_count; ++query) {
      std::vector<std::uint32_t> ids(candidates.begin() + query * 128, candidates.begin() + (query + 1) * 128);
      const auto begin = Clock::now();
      store.read_rows(ids, [&](const auto& rows) {
        const auto result = top10(queries.data() + query * kDimension, ids, rows);
        for (std::size_t index = 0; index < result.size(); ++index) checksum = checksum * 1315423911ULL + result[index];
        if (repeat == 0) {
          bool same = result.size() == 10;
          for (std::size_t index = 0; same && index < result.size(); ++index) same = result[index] == expected[query * 10 + index];
          if (same) ++parity;
        }
      });
      timings.push_back(std::chrono::duration<double, std::milli>(Clock::now() - begin).count());
    }
    std::cout << std::fixed << std::setprecision(6)
              << "{\"status\":\"EXECUTED\",\"mode\":\"" << mode << "\",\"documents\":" << documents
              << ",\"queries\":" << query_count << ",\"repeats\":" << repeats << ",\"db_bytes\":" << store.bytes()
              << ",\"reopen_first_query_ms\":" << reopen_ms << ",\"p50_ms\":" << percentile(timings, .5)
              << ",\"p95_ms\":" << percentile(timings, .95) << ",\"p99_ms\":" << percentile(timings, .99)
              << ",\"parity\":" << parity << ",\"parity_total\":" << query_count
              << ",\"checksum\":" << checksum << "}\n";
    return parity == query_count ? 0 : 5;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
