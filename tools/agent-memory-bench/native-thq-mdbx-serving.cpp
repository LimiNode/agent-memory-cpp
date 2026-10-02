#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#ifdef small
#undef small
#endif
#ifdef max
#undef max
#endif
#endif
#include <mdbx_containers/KeyValueTable.hpp>

#ifdef small
#undef small
#endif
#ifdef max
#undef max
#endif

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
constexpr std::size_t kDimension = 384;
constexpr std::size_t kThqBytes = 96;
constexpr std::size_t kInt8Bytes = 384;
constexpr std::size_t kFinalRowBytes = kInt8Bytes + sizeof(float);
constexpr std::size_t kSegmentRows = 4096;
constexpr std::size_t kMaxCandidates = 128;
constexpr std::size_t kTopK = 10;

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

struct Row final { std::int8_t code[kInt8Bytes]; float scale; };

std::string packed_final_row(const std::vector<std::int8_t>& codes,
                             const std::vector<float>& scales, std::size_t id) {
  std::string result(kFinalRowBytes, '\0');
  std::memcpy(result.data(), codes.data() + id * kInt8Bytes, kInt8Bytes);
  std::memcpy(result.data() + kInt8Bytes, scales.data() + id, sizeof(float));
  return result;
}

std::string legacy_big_endian_key(std::uint32_t value) {
  std::string result(4, '\0');
  for (std::size_t byte = 0; byte < 4; ++byte)
    result[byte] = static_cast<char>(value >> ((3 - byte) * 8));
  return result;
}

struct ScoredId final { float value; std::uint32_t id; };

std::size_t score_candidates(const float* query, const std::uint32_t* ids,
                             std::size_t count,
                             const std::array<Row, kMaxCandidates>& rows,
                             std::array<ScoredId, kMaxCandidates>& scores,
                             bool exact_cosine) {
  double query_norm = 0.0;
  if (exact_cosine) {
    for (std::size_t dimension = 0; dimension < kDimension; ++dimension)
      query_norm += static_cast<double>(query[dimension]) * query[dimension];
    query_norm = std::sqrt(std::max(query_norm, std::numeric_limits<double>::min()));
  }
  for (std::size_t index = 0; index < count; ++index) {
    double dot = 0.0;
    double code_norm = 0.0;
    for (std::size_t dimension = 0; dimension < kDimension; ++dimension) {
      dot += static_cast<double>(query[dimension]) * rows[index].code[dimension];
      if (exact_cosine) code_norm += static_cast<double>(rows[index].code[dimension]) * rows[index].code[dimension];
    }
    const double score = exact_cosine
        ? dot / std::max(std::sqrt(std::max(code_norm, std::numeric_limits<double>::min())) * query_norm,
                         std::numeric_limits<double>::min())
        : dot * rows[index].scale;
    scores[index] = {static_cast<float>(score), ids[index]};
  }
  return count;
}

std::array<ScoredId, kTopK> select_top10(const std::array<ScoredId, kMaxCandidates>& scores,
                                         std::size_t count) {
  std::array<ScoredId, kTopK> result{};
  std::size_t result_count = 0;
  for (std::size_t index = 0; index < count; ++index) {
    const auto candidate = scores[index];
    std::size_t position = result_count;
    if (position < kTopK) ++result_count;
    else if (candidate.value < result[kTopK - 1].value ||
             (candidate.value == result[kTopK - 1].value && candidate.id >= result[kTopK - 1].id))
      continue;
    if (position > kTopK - 1) position = kTopK - 1;
    while (position > 0 &&
           (candidate.value > result[position - 1].value ||
            (candidate.value == result[position - 1].value && candidate.id < result[position - 1].id))) {
      result[position] = result[position - 1];
      --position;
    }
    result[position] = candidate;
  }
  return result;
}

class Store final {
 public:
  Store(const std::filesystem::path& path, const std::string& mode, bool recreate,
        std::size_t segment_rows = kSegmentRows)
      : path_(path), mode_(mode), segment_rows_(segment_rows) {
    if (segment_rows_ == 0) throw std::runtime_error("segment rows must be positive");
    if (recreate) std::filesystem::remove(path_);
    mdbxc::Config config;
    config.pathname = path_.string();
    config.max_dbs = 4;
    config.no_subdir = true;
    config.relative_to_exe = false;
    connection_ = mdbxc::Connection::create(config);
    // uint32_t selects MDBX_INTEGERKEY in mdbx-containers, preserving numeric
    // ordering without an application-level endian or string-key encoding.
    table_ = std::make_unique<mdbxc::KeyValueTable<std::uint32_t, std::string>>(connection_, "payload");
  }

  void materialize(const std::vector<std::uint8_t>& thq, const std::vector<std::int8_t>& codes,
                   const std::vector<float>& scales, std::size_t documents,
                   std::size_t batch_documents = 0) {
    if (thq.size() != documents * kThqBytes || codes.size() != documents * kInt8Bytes || scales.size() != documents)
      throw std::runtime_error("materialization input shape differs");
    if (batch_documents == 0) batch_documents = documents;
    if (mode_ == "segment" && batch_documents % segment_rows_ != 0 && batch_documents < documents)
      throw std::runtime_error("segment batch must align to segment_rows");
    std::size_t durable_commits = 0;
    for (std::size_t batch_begin = 0; batch_begin < documents; batch_begin += batch_documents) {
      const auto batch_end = std::min(documents, batch_begin + batch_documents);
      auto transaction = connection_->transaction(mdbxc::TransactionMode::WRITABLE);
      if (mode_ == "row") {
        for (std::size_t id = batch_begin; id < batch_end; ++id)
          table_->insert_or_assign(static_cast<std::uint32_t>(id), packed_final_row(codes, scales, id), transaction);
      } else if (mode_ == "segment") {
        const auto first_segment = batch_begin / segment_rows_;
        const auto last_segment = (batch_end + segment_rows_ - 1) / segment_rows_;
        for (std::size_t segment = first_segment; segment < last_segment; ++segment) {
          const auto begin = segment * segment_rows_;
          const auto end = std::min(documents, begin + segment_rows_);
          std::string payload((end - begin) * kFinalRowBytes, '\0');
          for (std::size_t id = begin; id < end; ++id)
            std::memcpy(payload.data() + (id - begin) * kFinalRowBytes,
                        codes.data() + id * kInt8Bytes, kInt8Bytes);
          for (std::size_t id = begin; id < end; ++id)
            std::memcpy(payload.data() + (id - begin) * kFinalRowBytes + kInt8Bytes,
                        scales.data() + id, sizeof(float));
          table_->insert_or_assign(static_cast<std::uint32_t>(segment), payload, transaction);
        }
      } else throw std::runtime_error("mode must be row or segment");
      transaction.commit();
      ++durable_commits;
    }
    durable_commits_ = durable_commits;
  }

  template <class Callback> void read_rows(const std::uint32_t* ids, std::size_t count,
                                           Callback&& callback) const {
    if (count == 0 || count > kMaxCandidates) throw std::runtime_error("candidate count differs");
    auto transaction = connection_->transaction(mdbxc::TransactionMode::READ_ONLY);
    std::array<Row, kMaxCandidates> rows{};
    if (mode_ == "row") {
      for (std::size_t index = 0; index < count; ++index) {
        const auto id = ids[index];
        const auto payload = table_->find(id, transaction);
        if (!payload || payload->size() != kFinalRowBytes) throw std::runtime_error("row payload missing or malformed");
        std::memcpy(rows[index].code, payload->data(), kInt8Bytes);
        std::memcpy(&rows[index].scale, payload->data() + kInt8Bytes, sizeof(float));
      }
    } else {
      struct SegmentCache final { std::uint32_t id = 0; std::string payload; };
      std::array<SegmentCache, kMaxCandidates> segments{};
      std::size_t segment_count = 0;
      for (std::size_t index = 0; index < count; ++index) {
        const auto id = ids[index];
        const auto segment = id / static_cast<std::uint32_t>(segment_rows_);
        std::size_t cache_index = 0;
        while (cache_index < segment_count && segments[cache_index].id != segment) ++cache_index;
        if (cache_index == segment_count) {
          const auto payload = table_->find(segment, transaction);
          if (!payload || payload->size() % kFinalRowBytes != 0) throw std::runtime_error("segment payload missing or malformed");
          if (segment_count == segments.size()) throw std::runtime_error("segment cache is full");
          segments[segment_count].id = segment;
          segments[segment_count].payload = std::move(*payload);
          cache_index = segment_count++;
        }
        const auto& payload = segments[cache_index].payload;
        const auto offset = (id % segment_rows_) * kFinalRowBytes;
        if (offset + kFinalRowBytes > payload.size()) throw std::runtime_error("segment row offset differs");
        std::memcpy(rows[index].code, payload.data() + offset, kInt8Bytes);
        std::memcpy(&rows[index].scale, payload.data() + offset + kInt8Bytes, sizeof(float));
      }
    }
    callback(ids, count, rows);
    transaction.commit();
  }

  std::size_t bytes() const { return static_cast<std::size_t>(std::filesystem::file_size(path_)); }
  std::size_t durable_commits() const { return durable_commits_; }

 private:
  std::filesystem::path path_;
  std::string mode_;
  std::size_t segment_rows_;
  std::size_t durable_commits_ = 0;
  std::shared_ptr<mdbxc::Connection> connection_;
  std::unique_ptr<mdbxc::KeyValueTable<std::uint32_t, std::string>> table_;
};

void emit_samples(const std::vector<double>& values) {
  std::cout << '[';
  for (std::size_t index = 0; index < values.size(); ++index) {
    if (index != 0) std::cout << ',';
    std::cout << values[index];
  }
  std::cout << ']';
}

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

void integer_key_smoke(const std::filesystem::path& path) {
  std::filesystem::remove(path);
  {
    mdbxc::Config config;
    config.pathname = path.string();
    config.max_dbs = 4;
    config.no_subdir = true;
    config.relative_to_exe = false;
    auto connection = mdbxc::Connection::create(config);
    auto table = std::make_unique<mdbxc::KeyValueTable<std::uint32_t, std::string>>(
        connection, "integer_keys");
    auto transaction = connection->transaction(mdbxc::TransactionMode::WRITABLE);
    table->insert_or_assign(100U, "one-hundred", transaction);
    table->insert_or_assign(2U, "two", transaction);
    table->insert_or_assign(10U, "ten", transaction);
    transaction.commit();

    const auto ordered = table->range<std::vector>(0U, std::numeric_limits<std::uint32_t>::max());
    if (ordered.size() != 3 || ordered[0].first != 2U || ordered[1].first != 10U ||
        ordered[2].first != 100U) {
      throw std::runtime_error("numeric MDBX key ordering failed");
    }
    const auto value = table->find(10U);
    if (!value || *value != "ten") throw std::runtime_error("numeric MDBX key lookup failed");
  }
  std::filesystem::remove(path);
}

void migrate_legacy_payload(const std::filesystem::path& source,
                            const std::filesystem::path& destination,
                            std::size_t segment_rows, std::size_t documents) {
  std::filesystem::remove(destination);
  mdbxc::Config source_config;
  source_config.pathname = source.string();
  source_config.max_dbs = 4;
  source_config.no_subdir = true;
  source_config.relative_to_exe = false;
  auto source_connection = mdbxc::Connection::create(source_config);
  auto source_table = std::make_unique<mdbxc::KeyValueTable<std::string, std::string>>(
      source_connection, "payload");
  mdbxc::Config destination_config;
  destination_config.pathname = destination.string();
  destination_config.max_dbs = 4;
  destination_config.no_subdir = true;
  destination_config.relative_to_exe = false;
  auto destination_connection = mdbxc::Connection::create(destination_config);
  auto destination_table = std::make_unique<mdbxc::KeyValueTable<std::uint32_t, std::string>>(
      destination_connection, "payload");
  auto transaction = destination_connection->transaction(mdbxc::TransactionMode::WRITABLE);
  const auto segments = (documents + segment_rows - 1) / segment_rows;
  for (std::size_t segment = 0; segment < segments; ++segment) {
    const auto payload = source_table->find(legacy_big_endian_key(static_cast<std::uint32_t>(segment)));
    if (!payload) throw std::runtime_error("legacy payload segment missing");
    destination_table->insert_or_assign(static_cast<std::uint32_t>(segment), *payload, transaction);
  }
  transaction.commit();
}
}

int main(int argc, char** argv) {
  try {
    if (argc == 3 && std::string(argv[1]) == "--lifecycle-smoke") { lifecycle_smoke(argv[2]); std::cout << "{\"status\":\"PASS\"}\n"; return 0; }
    if (argc == 3 && std::string(argv[1]) == "--integer-key-self-test") { integer_key_smoke(argv[2]); std::cout << "{\"status\":\"PASS\"}\n"; return 0; }
    if (argc == 6 && std::string(argv[1]) == "--migrate-legacy") {
      migrate_legacy_payload(argv[2], argv[3], std::stoull(argv[4]), std::stoull(argv[5]));
      std::cout << "{\"status\":\"MIGRATED\"}\n";
      return 0;
    }
    if ((argc >= 8 && argc <= 10) && std::string(argv[1]) == "--materialize") {
      const std::string mode = argv[2];
      const std::filesystem::path db = argv[3];
      const auto thq = read_binary<std::uint8_t>(argv[4]);
      const auto codes = read_binary<std::int8_t>(argv[5]);
      const auto scales = read_binary<float>(argv[6]);
      const auto documents = static_cast<std::size_t>(std::stoull(argv[7]));
      const auto segment_rows = argc >= 9 ? static_cast<std::size_t>(std::stoull(argv[8])) : kSegmentRows;
      const auto batch_documents = argc == 10 ? static_cast<std::size_t>(std::stoull(argv[9])) : 0;
      Store store(db, mode, true, segment_rows);
      const auto begin = Clock::now();
      store.materialize(thq, codes, scales, documents, batch_documents);
      const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
      std::cout << std::fixed << std::setprecision(3) << "{\"status\":\"MATERIALIZED\",\"mode\":\""
                << mode << "\",\"documents\":" << documents << ",\"segment_rows\":" << segment_rows
                << ",\"db_bytes\":" << store.bytes()
                << ",\"materialize_ms\":" << elapsed << ",\"batch_documents\":"
                << (batch_documents == 0 ? documents : batch_documents)
                << ",\"durable_commits\":" << store.durable_commits() << "}\n";
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
    const std::size_t segment_rows = argc > 12 ? std::stoull(argv[12]) : kSegmentRows;
    const std::string metric = argc > 13 ? argv[13] : "scaled_dot";
    if (metric != "scaled_dot" && metric != "reconstructed_cosine_exact")
      throw std::runtime_error("metric must be scaled_dot or reconstructed_cosine_exact");
    if (query_count == 0 || queries.size() < query_count * kDimension || candidates.size() != query_count * 128 || expected.size() != query_count * 10)
      throw std::runtime_error("serving fixture shape differs");
    const auto documents = codes.size() / kInt8Bytes;
    if (thq.size() != documents * kThqBytes || scales.size() != documents) throw std::runtime_error("payload shape differs");
    Store store(db, mode, false, segment_rows);
    const auto first_start = Clock::now();
    std::size_t parity = 0;
    store.read_rows(candidates.data(), 128, [&](const auto*, std::size_t, const auto&) {});
    const double reopen_ms = std::chrono::duration<double, std::milli>(Clock::now() - first_start).count();
    std::vector<double> timings; timings.reserve(query_count * repeats);
    std::vector<double> read_timings; read_timings.reserve(query_count * repeats);
    std::vector<double> score_timings; score_timings.reserve(query_count * repeats);
    std::vector<double> topk_timings; topk_timings.reserve(query_count * repeats);
    std::uint64_t checksum = 0;
    for (std::size_t repeat = 0; repeat < repeats; ++repeat) for (std::size_t query = 0; query < query_count; ++query) {
      const auto* ids = candidates.data() + query * 128;
      const auto begin = Clock::now();
      double read_ms = 0.0;
      double score_ms = 0.0;
      double topk_ms = 0.0;
      store.read_rows(ids, 128, [&](const auto* selected_ids, std::size_t count, const auto& rows) {
        const auto score_begin = Clock::now();
        std::array<ScoredId, kMaxCandidates> scores{};
        const auto scored = score_candidates(queries.data() + query * kDimension,
                                              selected_ids, count, rows, scores,
                                              metric == "reconstructed_cosine_exact");
        const auto score_end = Clock::now();
        const auto result = select_top10(scores, scored);
        const auto topk_end = Clock::now();
        score_ms = std::chrono::duration<double, std::milli>(score_end - score_begin).count();
        topk_ms = std::chrono::duration<double, std::milli>(topk_end - score_end).count();
        for (std::size_t index = 0; index < kTopK; ++index) checksum = checksum * 1315423911ULL + result[index].id;
        if (repeat == 0) {
          bool same = true;
          for (std::size_t index = 0; same && index < kTopK; ++index) same = result[index].id == expected[query * 10 + index];
          if (same) ++parity;
        }
      });
      const auto end = Clock::now();
      const auto total_ms = std::chrono::duration<double, std::milli>(end - begin).count();
      timings.push_back(total_ms);
      score_timings.push_back(score_ms);
      topk_timings.push_back(topk_ms);
      read_ms = std::max(0.0, total_ms - score_ms - topk_ms);
      read_timings.push_back(read_ms);
    }
    std::cout << std::fixed << std::setprecision(6)
              << "{\"status\":\"EXECUTED\",\"mode\":\"" << mode << "\",\"metric\":\"" << metric << "\",\"documents\":" << documents
              << ",\"segment_rows\":" << segment_rows << ",\"queries\":" << query_count
              << ",\"repeats\":" << repeats << ",\"db_bytes\":" << store.bytes()
              << ",\"reopen_first_query_ms\":" << reopen_ms << ",\"p50_ms\":" << percentile(timings, .5)
              << ",\"p95_ms\":" << percentile(timings, .95) << ",\"p99_ms\":" << percentile(timings, .99)
              << ",\"read_decode_p50_ms\":" << percentile(read_timings, .5)
              << ",\"read_decode_p95_ms\":" << percentile(read_timings, .95)
              << ",\"read_decode_p99_ms\":" << percentile(read_timings, .99)
              << ",\"score_p50_ms\":" << percentile(score_timings, .5)
              << ",\"score_p95_ms\":" << percentile(score_timings, .95)
              << ",\"score_p99_ms\":" << percentile(score_timings, .99)
              << ",\"topk_p50_ms\":" << percentile(topk_timings, .5)
              << ",\"topk_p95_ms\":" << percentile(topk_timings, .95)
              << ",\"topk_p99_ms\":" << percentile(topk_timings, .99)
              << ",\"parity\":" << parity << ",\"parity_total\":" << query_count
              << ",\"checksum\":" << checksum << ",\"samples_ms\":";
    emit_samples(timings);
    std::cout << "}\n";
    return parity == query_count ? 0 : 5;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
