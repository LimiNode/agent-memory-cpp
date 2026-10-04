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
#include <mdbx.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;

struct Config final {
  std::filesystem::path db;
  std::filesystem::path payload;
  std::string layout;
  std::size_t documents = 0;
  std::size_t row_bytes = 0;
  std::size_t segment_rows = 4096;
  std::size_t batch_rows = 65536;
};

std::vector<std::uint8_t> read_binary(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary | std::ios::ate);
  if (!input) throw std::runtime_error("cannot open payload: " + path.string());
  const auto end = input.tellg();
  if (end < 0) throw std::runtime_error("cannot size payload: " + path.string());
  std::vector<std::uint8_t> bytes(static_cast<std::size_t>(end));
  input.seekg(0);
  input.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  if (!input) throw std::runtime_error("short payload: " + path.string());
  return bytes;
}

std::uint64_t hash_bytes(const std::uint8_t* data, std::size_t size) {
  std::uint64_t hash = 1469598103934665603ULL;
  for (std::size_t index = 0; index < size; ++index) {
    hash ^= data[index];
    hash *= 1099511628211ULL;
  }
  return hash;
}

Config load_config(const std::filesystem::path& path) {
  const auto value = Json::parse(std::ifstream(path));
  Config config;
  config.db = value.at("db").get<std::string>();
  config.payload = value.at("payload").get<std::string>();
  config.layout = value.at("layout").get<std::string>();
  config.documents = value.at("documents").get<std::size_t>();
  config.row_bytes = value.at("row_bytes").get<std::size_t>();
  config.segment_rows = value.value("segment_rows", std::size_t{4096});
  config.batch_rows = value.value("batch_rows", std::size_t{65536});
  if ((config.layout != "row_kv" && config.layout != "segmented") ||
      config.documents == 0 || config.row_bytes == 0 || config.segment_rows == 0 || config.batch_rows == 0)
    throw std::runtime_error("invalid MDBX finalist config");
  return config;
}

class Store final {
 public:
  explicit Store(const Config& config, bool recreate)
      : config_(config) {
    if (recreate) std::filesystem::remove(config_.db);
    mdbxc::Config db_config;
    db_config.pathname = config_.db.string();
    db_config.max_dbs = 4;
    db_config.no_subdir = true;
    db_config.relative_to_exe = false;
    connection_ = mdbxc::Connection::create(db_config);
    payload_table_ = std::make_unique<mdbxc::KeyValueTable<std::uint32_t, std::string>>(connection_, "payload");
    metadata_table_ = std::make_unique<mdbxc::KeyValueTable<std::string, std::string>>(connection_, "metadata");
  }

  void mark_building() {
    auto tx = connection_->transaction(mdbxc::TransactionMode::WRITABLE);
    metadata_table_->insert_or_assign("state", "building", tx);
    tx.commit();
  }

  void materialize() {
    const auto payload = read_binary(config_.payload);
    const auto expected = config_.documents * config_.row_bytes;
    if (payload.size() != expected) throw std::runtime_error("payload shape differs");
    std::size_t commits = 0;
    for (std::size_t begin = 0; begin < config_.documents; begin += config_.batch_rows) {
      const auto end = std::min(config_.documents, begin + config_.batch_rows);
      auto tx = connection_->transaction(mdbxc::TransactionMode::WRITABLE);
      if (config_.layout == "row_kv") {
        for (std::size_t id = begin; id < end; ++id)
          payload_table_->insert_or_assign(static_cast<std::uint32_t>(id),
              std::string(reinterpret_cast<const char*>(payload.data() + id * config_.row_bytes), config_.row_bytes), tx);
      } else {
        const auto first = begin / config_.segment_rows;
        const auto last = (end + config_.segment_rows - 1) / config_.segment_rows;
        for (std::size_t segment = first; segment < last; ++segment) {
          const auto segment_begin = segment * config_.segment_rows;
          const auto segment_end = std::min(config_.documents, segment_begin + config_.segment_rows);
          const auto bytes = (segment_end - segment_begin) * config_.row_bytes;
          std::string blob(reinterpret_cast<const char*>(payload.data() + segment_begin * config_.row_bytes), bytes);
          payload_table_->insert_or_assign(static_cast<std::uint32_t>(segment), std::move(blob), tx);
        }
      }
      tx.commit();
      ++commits;
    }
    auto tx = connection_->transaction(mdbxc::TransactionMode::WRITABLE);
    metadata_table_->insert_or_assign("state", "complete", tx);
    metadata_table_->insert_or_assign("documents", std::to_string(config_.documents), tx);
    metadata_table_->insert_or_assign("row_bytes", std::to_string(config_.row_bytes), tx);
    metadata_table_->insert_or_assign("layout", config_.layout, tx);
    tx.commit();
    commits_ = commits + 1;
  }

  void verify() const {
    check_complete();
    const auto payload = read_binary(config_.payload);
    if (payload.size() != config_.documents * config_.row_bytes) throw std::runtime_error("source payload shape differs");
    auto tx = connection_->transaction(mdbxc::TransactionMode::READ_ONLY);
    if (config_.layout == "row_kv") {
      for (std::size_t id = 0; id < config_.documents; ++id) {
        const auto value = payload_table_->find(static_cast<std::uint32_t>(id), tx);
        if (!value || value->size() != config_.row_bytes || std::memcmp(value->data(), payload.data() + id * config_.row_bytes, config_.row_bytes) != 0)
          throw std::runtime_error("row payload parity differs");
      }
    } else {
      const auto segments = (config_.documents + config_.segment_rows - 1) / config_.segment_rows;
      for (std::size_t segment = 0; segment < segments; ++segment) {
        const auto value = payload_table_->find(static_cast<std::uint32_t>(segment), tx);
        const auto begin = segment * config_.segment_rows;
        const auto bytes = std::min(config_.documents, begin + config_.segment_rows) - begin;
        if (!value || value->size() != bytes * config_.row_bytes || std::memcmp(value->data(), payload.data() + begin * config_.row_bytes, value->size()) != 0)
          throw std::runtime_error("segment payload parity differs");
      }
    }
    tx.commit();
  }

  // This deliberately checks only durable metadata.  It is safe to use before
  // a first-query timing sample; full byte parity belongs to --verify.
  void check_complete() const {
    const auto state = metadata_table_->find("state");
    const auto documents = metadata_table_->find("documents");
    const auto row_bytes = metadata_table_->find("row_bytes");
    const auto layout = metadata_table_->find("layout");
    if (!state || *state != "complete") throw std::runtime_error("MDBX build is not complete");
    if (!documents || *documents != std::to_string(config_.documents) ||
        !row_bytes || *row_bytes != std::to_string(config_.row_bytes) ||
        !layout || *layout != config_.layout)
      throw std::runtime_error("MDBX metadata does not match config");
  }

  struct SpaceStats final {
    std::uint64_t page_size = 0;
    std::uint64_t data_pages = 0;
    std::uint64_t data_bytes = 0;
    std::uint64_t used_pages = 0;
    std::uint64_t used_bytes = 0;
    std::uint64_t allocated_file_bytes = 0;
    std::uint64_t environment_file_bytes = 0;
    // Allocated file space beyond the last used page; this is not MDBX GC/free-page accounting.
    std::uint64_t allocated_tail_bytes = 0;
  };

  SpaceStats space_stats() const {
    auto tx = connection_->transaction(mdbxc::TransactionMode::READ_ONLY);
    MDBX_stat stat{};
    MDBX_envinfo info{};
    if (mdbx_env_stat_ex(connection_->env_handle(), tx.handle(), &stat, sizeof(stat)) != MDBX_SUCCESS)
      throw std::runtime_error("cannot read MDBX environment statistics");
    if (mdbx_env_info_ex(connection_->env_handle(), tx.handle(), &info, sizeof(info)) != MDBX_SUCCESS)
      throw std::runtime_error("cannot read MDBX environment information");
    tx.commit();
    SpaceStats result;
    result.page_size = stat.ms_psize;
    result.data_pages = stat.ms_branch_pages + stat.ms_leaf_pages + stat.ms_overflow_pages;
    result.data_bytes = result.data_pages * result.page_size;
    result.used_pages = info.mi_last_pgno + 1;
    result.used_bytes = result.used_pages * info.mi_dxb_pagesize;
    result.allocated_file_bytes = info.mi_dxb_fallocated;
    result.environment_file_bytes = info.mi_dxb_fsize;
    result.allocated_tail_bytes = result.allocated_file_bytes > result.used_bytes
        ? result.allocated_file_bytes - result.used_bytes : 0;
    return result;
  }

  std::size_t bytes() const { return static_cast<std::size_t>(std::filesystem::file_size(config_.db)); }
  std::size_t commits() const { return commits_; }

  struct ReadStats final { std::uint64_t checksum = 0; std::size_t reads = 0; std::size_t logical_value_bytes_fetched = 0; std::size_t useful_bytes = 0; };

  ReadStats read(const std::vector<std::uint32_t>& ids, bool one_transaction) const {
    if (ids.empty()) throw std::runtime_error("candidate row is empty");
    ReadStats stats;
    stats.useful_bytes = ids.size() * config_.row_bytes;
    auto consume_bytes = [&](const std::uint8_t* data, std::size_t size, std::size_t id) {
      if (size != config_.row_bytes) throw std::runtime_error("row payload size differs");
      stats.checksum ^= hash_bytes(data, size) + id + 0x9e3779b97f4a7c15ULL + (stats.checksum << 6) + (stats.checksum >> 2);
    };
    auto consume = [&](const std::string& value, std::size_t id) {
      consume_bytes(reinterpret_cast<const std::uint8_t*>(value.data()), value.size(), id);
    };
    if (config_.layout == "row_kv") {
      if (one_transaction) {
        auto tx = connection_->transaction(mdbxc::TransactionMode::READ_ONLY);
        for (std::size_t index = 0; index < ids.size(); ++index) {
          const auto value = payload_table_->find(ids[index], tx);
          if (!value) throw std::runtime_error("row lookup missing");
          consume(*value, index); ++stats.reads; stats.logical_value_bytes_fetched += value->size();
        }
        tx.commit();
      } else {
        for (std::size_t index = 0; index < ids.size(); ++index) {
          const auto value = payload_table_->find(ids[index]);
          if (!value) throw std::runtime_error("row lookup missing");
          consume(*value, index); ++stats.reads; stats.logical_value_bytes_fetched += value->size();
        }
      }
      return stats;
    }
    auto tx = connection_->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto segment_count = (config_.documents + config_.segment_rows - 1) / config_.segment_rows;
    std::vector<std::string> cache(segment_count);
    std::vector<bool> loaded(segment_count, false);
    for (std::size_t index = 0; index < ids.size(); ++index) {
      const auto segment = ids[index] / static_cast<std::uint32_t>(config_.segment_rows);
      if (!loaded[segment]) {
        const auto value = payload_table_->find(segment, tx);
        if (!value) throw std::runtime_error("segment lookup missing");
        cache[segment] = std::move(*value);
        loaded[segment] = true;
        ++stats.reads; stats.logical_value_bytes_fetched += cache[segment].size();
      }
      const auto offset = (ids[index] % config_.segment_rows) * config_.row_bytes;
      if (offset + config_.row_bytes > cache[segment].size()) throw std::runtime_error("segment row offset differs");
      consume_bytes(reinterpret_cast<const std::uint8_t*>(cache[segment].data()) + offset, config_.row_bytes, index);
    }
    tx.commit();
    return stats;
  }

 private:
  Config config_;
  std::size_t commits_ = 0;
  std::shared_ptr<mdbxc::Connection> connection_;
  std::unique_ptr<mdbxc::KeyValueTable<std::uint32_t, std::string>> payload_table_;
  std::unique_ptr<mdbxc::KeyValueTable<std::string, std::string>> metadata_table_;
};

double percentile(std::vector<double> values, double fraction) {
  if (values.empty()) throw std::runtime_error("empty timing samples");
  std::sort(values.begin(), values.end());
  const auto index = std::min(values.size() - 1, static_cast<std::size_t>(std::ceil(fraction * values.size())) - 1);
  return values[index];
}

std::vector<std::vector<std::uint32_t>> load_candidates(const std::filesystem::path& path, std::size_t queries, std::size_t width, std::size_t documents) {
  const auto bytes = read_binary(path);
  if (bytes.size() != queries * width * sizeof(std::uint32_t)) throw std::runtime_error("candidate shape differs");
  std::vector<std::vector<std::uint32_t>> result(queries, std::vector<std::uint32_t>(width));
  for (std::size_t query = 0; query < queries; ++query)
    std::memcpy(result[query].data(), bytes.data() + query * width * sizeof(std::uint32_t), width * sizeof(std::uint32_t));
  for (const auto& row : result) {
    std::vector<std::uint32_t> sorted = row;
    std::sort(sorted.begin(), sorted.end());
    if (sorted.front() >= documents || sorted.back() >= documents || std::adjacent_find(sorted.begin(), sorted.end()) != sorted.end())
      throw std::runtime_error("candidate IDs are out of range or duplicated");
  }
  return result;
}

void self_test(const std::filesystem::path& root) {
  std::filesystem::create_directories(root);
  const auto payload = root / "payload.bin";
  const auto db = root / "self-test.mdbx";
  {
    std::ofstream output(payload, std::ios::binary);
    for (std::uint32_t id = 0; id < 32; ++id) output.write(reinterpret_cast<const char*>(&id), sizeof(id));
  }
  Config config{db, payload, "row_kv", 32, sizeof(std::uint32_t), 8, 8};
  { Store store(config, true); store.mark_building(); store.materialize(); store.verify(); if (store.bytes() == 0 || store.commits() != 5) throw std::runtime_error("self-test materialization differs"); }
  { Store store(config, false); store.verify(); }
  Config segmented{root / "segment.mdbx", payload, "segmented", 32, sizeof(std::uint32_t), 8, 8};
  { Store store(segmented, true); store.mark_building(); store.materialize(); store.verify(); }
  Config incomplete{root / "incomplete.mdbx", payload, "row_kv", 32, sizeof(std::uint32_t), 8, 8};
  { Store store(incomplete, true); store.mark_building(); }
  try { Store store(incomplete, false); store.verify(); throw std::runtime_error("incomplete build was accepted"); }
  catch (const std::runtime_error& error) { if (std::string(error.what()).find("not complete") == std::string::npos) throw; }
  const auto truncated = root / "truncated.bin";
  { std::ofstream output(truncated, std::ios::binary); std::uint32_t value = 1; output.write(reinterpret_cast<const char*>(&value), sizeof(value)); }
  Config malformed{root / "malformed.mdbx", truncated, "row_kv", 32, sizeof(std::uint32_t), 8, 8};
  try { Store store(malformed, true); store.mark_building(); store.materialize(); throw std::runtime_error("truncated payload was accepted"); }
  catch (const std::runtime_error& error) { if (std::string(error.what()).find("payload shape") == std::string::npos) throw; }
  std::error_code cleanup_error;
  std::filesystem::remove_all(root, cleanup_error);
}
}

int main(int argc, char** argv) {
  try {
    if (argc == 3 && std::string(argv[1]) == "--self-test") { self_test(argv[2]); std::cout << "{\"status\":\"PASS\"}\n"; return 0; }
    if (argc != 3) { std::cerr << "usage: --materialize|--verify|--coldish|--benchmark config.json\n"; return 2; }
    const std::string command = argv[1];
    const auto config_path = std::filesystem::path(argv[2]);
    const Config config = load_config(config_path);
    if (command == "--materialize") {
      Store store(config, true); store.mark_building(); const auto begin = Clock::now(); store.materialize(); store.verify();
      const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
      const auto space = store.space_stats();
      std::cout << std::fixed << std::setprecision(6) << "{\"status\":\"MATERIALIZED\",\"mdbx_allocated_file_bytes\":" << space.allocated_file_bytes
                << ",\"environment_file_size_bytes\":" << space.environment_file_bytes << ",\"mdbx_page_size\":" << space.page_size
                << ",\"mdbx_used_pages\":" << space.used_pages << ",\"mdbx_used_bytes\":" << space.used_bytes
                << ",\"mdbx_data_pages\":" << space.data_pages << ",\"mdbx_data_bytes\":" << space.data_bytes
                << ",\"mdbx_allocated_tail_bytes\":" << space.allocated_tail_bytes
                << ",\"db_bytes\":" << store.bytes() << ",\"materialize_ms\":" << elapsed << ",\"durable_commits\":" << store.commits() << "}\n";
      return 0;
    }
    if (command == "--verify") { Store store(config, false); store.verify(); std::cout << "{\"status\":\"PASS\"}\n"; return 0; }
    if (command == "--coldish") {
      const auto value = Json::parse(std::ifstream(config_path));
      const auto candidates = load_candidates(value.at("candidates").get<std::string>(), value.at("query_count").get<std::size_t>(), value.at("candidate_width").get<std::size_t>(), config.documents);
      const auto one_transaction = value.value("access", std::string("query_transaction")) == "query_transaction";
      Store store(config, false); store.check_complete();
      const auto space = store.space_stats();
      const auto begin = Clock::now(); const auto stats = store.read(candidates.front(), one_transaction);
      const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
      std::cout << std::fixed << std::setprecision(6) << "{\"status\":\"COLDISH\",\"reopen_coldish_first_query_ms\":" << elapsed
                << ",\"mdbx_allocated_file_bytes\":" << space.allocated_file_bytes << ",\"environment_file_size_bytes\":" << space.environment_file_bytes
                << ",\"mdbx_page_size\":" << space.page_size << ",\"mdbx_used_pages\":" << space.used_pages << ",\"mdbx_used_bytes\":" << space.used_bytes
                << ",\"mdbx_data_pages\":" << space.data_pages << ",\"mdbx_data_bytes\":" << space.data_bytes
                << ",\"mdbx_allocated_tail_bytes\":" << space.allocated_tail_bytes << "}\n";
      return 0;
    }
    if (command != "--benchmark") throw std::runtime_error("unknown MDBX harness command");
    const auto value = Json::parse(std::ifstream(config_path));
    const auto candidates = load_candidates(value.at("candidates").get<std::string>(), value.at("query_count").get<std::size_t>(), value.at("candidate_width").get<std::size_t>(), config.documents);
    const auto repeats = value.value("repeats", std::size_t{5});
    const auto warmups = value.value("warmups", std::size_t{1});
    const auto one_transaction = value.value("access", std::string("query_transaction")) == "query_transaction";
    Store store(config, false); store.check_complete();
    auto first = Clock::now(); (void)store.read(candidates.front(), one_transaction); const auto reopen_ms = std::chrono::duration<double, std::milli>(Clock::now() - first).count();
    const auto space = store.space_stats();
    std::vector<double> timings; std::vector<double> reads; std::vector<double> fetched; std::vector<double> useful; std::uint64_t checksum = 0;
    for (std::size_t iteration = 0; iteration < warmups + repeats; ++iteration) for (const auto& row : candidates) {
      const auto begin = Clock::now(); const auto stats = store.read(row, one_transaction); const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
      if (iteration >= warmups) { timings.push_back(elapsed); reads.push_back(static_cast<double>(stats.reads)); fetched.push_back(static_cast<double>(stats.logical_value_bytes_fetched)); useful.push_back(static_cast<double>(stats.useful_bytes)); checksum ^= stats.checksum + 0x9e3779b97f4a7c15ULL + (checksum << 6) + (checksum >> 2); }
    }
    std::cout << std::fixed << std::setprecision(6) << "{\"status\":\"EXECUTED\",\"access\":\"" << (one_transaction ? "query_transaction" : "point_lookup") << "\",\"queries\":" << candidates.size() << ",\"width\":" << candidates.front().size() << ",\"warmups\":" << warmups << ",\"repeats\":" << repeats << ",\"mdbx_allocated_file_bytes\":" << space.allocated_file_bytes << ",\"environment_file_size_bytes\":" << space.environment_file_bytes << ",\"mdbx_page_size\":" << space.page_size << ",\"mdbx_used_pages\":" << space.used_pages << ",\"mdbx_used_bytes\":" << space.used_bytes << ",\"mdbx_data_pages\":" << space.data_pages << ",\"mdbx_data_bytes\":" << space.data_bytes << ",\"mdbx_allocated_tail_bytes\":" << space.allocated_tail_bytes << ",\"db_bytes\":" << store.bytes() << ",\"reopen_coldish_first_query_ms\":" << reopen_ms << ",\"p50_ms\":" << percentile(timings, .5) << ",\"p95_ms\":" << percentile(timings, .95) << ",\"p99_ms\":" << percentile(timings, .99) << ",\"median_reads\":" << percentile(reads, .5) << ",\"median_logical_value_bytes_fetched\":" << percentile(fetched, .5) << ",\"median_useful_bytes\":" << percentile(useful, .5) << ",\"checksum\":" << checksum << ",\"samples_ms\":[";
    for (std::size_t index = 0; index < timings.size(); ++index) { if (index) std::cout << ','; std::cout << timings[index]; }
    std::cout << "],\"samples_reads\":[";
    for (std::size_t index = 0; index < reads.size(); ++index) { if (index) std::cout << ','; std::cout << reads[index]; }
    std::cout << "],\"samples_logical_value_bytes_fetched\":[";
    for (std::size_t index = 0; index < fetched.size(); ++index) { if (index) std::cout << ','; std::cout << fetched[index]; }
    std::cout << "],\"samples_useful_bytes\":[";
    for (std::size_t index = 0; index < useful.size(); ++index) { if (index) std::cout << ','; std::cout << useful[index]; }
    std::cout << "]}\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << "mdbx_finalist_harness: " << error.what() << '\n'; return 1; }
}
