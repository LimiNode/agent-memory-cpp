#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSegmentedLexicalIndex.hpp>
#include <agent_memory/lexical/FlatLexicalIndex.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <array>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

using namespace agent_memory;
using Clock = std::chrono::steady_clock;

LexicalIndexDescriptor descriptor() {
    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = 91;
    epoch[15] = 92;
    return LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{
            ScopeId{"scope-benchmark"},
            ProjectionKind::Original,
            LexicalStatisticsEpoch{epoch}
        },
        "original",
        1,
        "tokenizer-v1",
        "normalization-v1",
        {kLexicalSegmentLayoutVersion}
    };
}

FlatLexicalUnitInput unit(
    const std::size_t index,
    const std::string& workload
) {
    std::vector<std::string> tokens;
    if(workload == "unique+anchor") {
        tokens = {"token-" + std::to_string(index), "anchor"};
    } else if(workload == "shared-hot") {
        tokens = {"shared-hot", "shared-hot", "anchor"};
    } else if(workload == "zipf-like") {
        const auto rank = (index * 37U + 11U) % 100U;
        const auto bucket = rank < 50U ? 1U : rank < 75U ? 2U
            : rank < 88U ? 3U : rank < 95U ? 4U : 5U;
        tokens = {"zipf-" + std::to_string(bucket), "anchor"};
        if(index % 3U == 0) tokens.push_back("shared-hot");
    } else {
        tokens = {"токен-" + std::to_string(index), "code::v1", "anchor"};
    }
    return FlatLexicalUnitInput{
        ScopeId{"scope-benchmark"},
        KnowledgeUnitId{static_cast<std::uint64_t>(index + 1U)},
        ProjectionKind::Original,
        ResourceId{"resource-benchmark-" + std::to_string(index)},
        1,
        1,
        ProjectionVersionRef{1, 1, 1, "benchmark-v1"},
        std::move(tokens)
    };
}

double milliseconds(const Clock::duration duration) {
    return std::chrono::duration<double, std::milli>(duration).count();
}

std::uintmax_t database_bytes(const std::filesystem::path& path) {
    std::error_code error;
    const auto size = std::filesystem::file_size(path, error);
    return error ? 0U : size;
}

struct PhysicalCounters final {
    std::size_t live_rows_observed = 0;
    std::size_t live_value_bytes_observed = 0;
    std::size_t diagnostic_scans = 0;
    std::size_t observed_live_key_value_bytes = 0;
};

PhysicalCounters scan_rows(
    const std::shared_ptr<MdbxStorageContext>& context,
    const bool segmented
) {
    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), "agent_memory_lexical_posting_segments");
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    PhysicalCounters counters;
    const std::string from = segmented ? "seg-v1" : "flat-v1";
    const std::string to = segmented ? "seg-v2" : "flat-v3";
    table.for_each_range(from, to,
        [&counters](const std::string& key, const std::string& value) {
            ++counters.live_rows_observed;
            counters.live_value_bytes_observed += value.size();
            counters.observed_live_key_value_bytes += key.size() + value.size();
            return true;
        }, txn);
    counters.diagnostic_scans = 1;
    return counters;
}

void print_header() {
    std::cout << "backend\tworkload\tunits\trepeat\tenvironment_ms\tadapter_init_ms"
                 "\twrite_ms\tsnapshot_ms\twrite_diagnostic_ms\tclose_ms"
                 "\treopen_ms\treopen_snapshot_ms\treopen_diagnostic_ms"
                 "\tdb_bytes\tactive_units\ttotal_tokens"
                 "\tdiagnostic_scans\tlive_rows_observed\tlive_value_bytes_observed"
                 "\tobserved_live_key_value_bytes\n";
}

void run_flat_reference(
    const std::string& workload,
    const std::size_t units,
    const std::size_t repeat
) {
    FlatLexicalIndex index{descriptor()};
    const auto started = Clock::now();
    for(std::size_t index_value = 0; index_value < units; ++index_value) {
        index.upsert(unit(index_value, workload));
    }
    const auto write_done = Clock::now();
    static_cast<void>(index.postings(
        index.find_token_id("anchor").value()));
    const auto read_done = Clock::now();
    std::cout << "flat-reference\t" << workload << '\t' << units << '\t' << repeat
              << "\t0\t0\t" << milliseconds(write_done - started)
              << '\t' << milliseconds(read_done - write_done)
              << "\t0\t0\t0\t0\t0\t0\t"
              << index.collection_stats().unit_count
              << '\t' << index.collection_stats().total_token_count
              << "\t0\t0\t0\t0\n";
}

template<typename IndexT, typename OptionsT>
void run_durable(
    const std::string& backend,
    const std::string& workload,
    const std::size_t units,
    const std::size_t repeat,
    const std::filesystem::path& path,
    OptionsT options,
    const bool diagnostics_enabled
) {
    const auto remove_database_file = [](const std::filesystem::path& target) {
        std::error_code error;
        std::filesystem::remove(target, error);
        if(error && error != std::errc::no_such_file_or_directory) {
            throw std::runtime_error("cannot remove benchmark database: " + target.string());
        }
        if(std::filesystem::exists(target, error) || error) {
            throw std::runtime_error("benchmark database was not removed: " + target.string());
        }
    };
    remove_database_file(path);
    remove_database_file(path.string() + "-lck");

    const auto environment_started = Clock::now();
    auto context = MdbxStorageContext::open(path.string());
    const auto environment_done = Clock::now();
    options.path = path.string();
    options.context = context;
    const auto adapter_started = Clock::now();
    auto index = std::make_unique<IndexT>(options);
    const auto adapter_done = Clock::now();
    Clock::time_point write_started;
    Clock::time_point write_finished;
    Clock::time_point snapshot_started;
    Clock::time_point snapshot_finished;
    Clock::time_point diagnostic_started;
    Clock::time_point diagnostic_finished;
    PhysicalCounters counters;
    {
        write_started = Clock::now();
        for(std::size_t index_value = 0; index_value < units; ++index_value) {
            index->upsert(unit(index_value, workload));
        }
        write_finished = Clock::now();
        snapshot_started = Clock::now();
        {
            const auto snapshot = index->read_snapshot();
            const auto anchor = snapshot.index.find_token_id("anchor");
            if(!anchor) throw std::runtime_error("benchmark anchor token missing");
            static_cast<void>(snapshot.index.postings(*anchor));
        }
        snapshot_finished = Clock::now();
        diagnostic_started = Clock::now();
        if(diagnostics_enabled) {
            counters = scan_rows(context, backend == "segmented-v1");
        }
        diagnostic_finished = Clock::now();
    }
    const auto before_close = Clock::now();
    index.reset();
    options.context.reset();
    context.reset();
    const auto close_done = Clock::now();

    OptionsT reopen_options;
    reopen_options.path = path.string();
    reopen_options.descriptor = descriptor();
    if constexpr(std::is_same_v<OptionsT, MdbxSegmentedLexicalIndexOptions>) {
        reopen_options.encoding_limits.max_postings_per_segment = 128;
    }
    const auto reopen_started = Clock::now();
    const auto reopen_context = MdbxStorageContext::open(path.string());
    reopen_options.context = reopen_context;
    IndexT reopened{reopen_options};
    const auto reopen_done = Clock::now();
    std::size_t active_units = 0;
    std::size_t total_tokens = 0;
    const auto reopen_snapshot_started = Clock::now();
    {
        const auto reopened_snapshot = reopened.read_snapshot();
        active_units = reopened_snapshot.index.collection_stats().unit_count;
        total_tokens = reopened_snapshot.index.collection_stats().total_token_count;
    }
    const auto reopen_snapshot_done = Clock::now();
    const auto reopen_diagnostic_started = Clock::now();
    PhysicalCounters reopen_counters;
    if(diagnostics_enabled) {
        reopen_counters = scan_rows(reopen_context, backend == "segmented-v1");
        if(counters.live_rows_observed != reopen_counters.live_rows_observed ||
           counters.live_value_bytes_observed != reopen_counters.live_value_bytes_observed ||
           counters.observed_live_key_value_bytes != reopen_counters.observed_live_key_value_bytes) {
            throw std::runtime_error("benchmark live-row observation changed across reopen");
        }
    }
    const auto reopen_diagnostic_done = Clock::now();
    std::cout << backend << '\t' << workload << '\t' << units << '\t' << repeat
              << '\t' << milliseconds(environment_done - environment_started)
              << '\t' << milliseconds(adapter_done - adapter_started)
              << '\t' << milliseconds(write_finished - write_started)
              << '\t' << milliseconds(snapshot_finished - snapshot_started)
              << '\t' << milliseconds(diagnostic_finished - diagnostic_started)
              << '\t' << milliseconds(close_done - before_close)
              << '\t' << milliseconds(reopen_done - reopen_started)
              << '\t' << milliseconds(reopen_snapshot_done - reopen_snapshot_started)
              << '\t' << milliseconds(reopen_diagnostic_done - reopen_diagnostic_started)
              << '\t' << database_bytes(path)
              << '\t' << active_units
              << '\t' << total_tokens
              << '\t' << counters.diagnostic_scans + reopen_counters.diagnostic_scans
              << '\t' << (diagnostics_enabled ? reopen_counters.live_rows_observed : 0U)
              << '\t' << (diagnostics_enabled ? reopen_counters.live_value_bytes_observed : 0U)
              << '\t' << (diagnostics_enabled ? reopen_counters.observed_live_key_value_bytes : 0U)
              << '\n';
}

} // namespace

int main(int argc, char** argv) {
    const std::size_t units = argc > 1 ? std::stoull(argv[1]) : 128U;
    const std::size_t repeats = argc > 2 ? std::stoull(argv[2]) : 3U;
    const std::filesystem::path base = argc > 3
        ? argv[3]
        : (std::filesystem::temp_directory_path() / "agent_memory_segmented_benchmark");
    const bool diagnostics_enabled = argc > 4
        ? std::stoull(argv[4]) != 0U
        : true;
    print_header();
    for(const std::string workload : {
        "unique+anchor", "shared-hot", "zipf-like", "mixed-utf8"}) {
        for(std::size_t repeat = 0; repeat < repeats; ++repeat) {
            run_flat_reference(workload, units, repeat);
            {
                MdbxPersistentLexicalIndexOptions options;
                options.path = (base.string() + "-flat-v2");
                options.descriptor = descriptor();
                run_durable<MdbxPersistentLexicalIndex>(
                    "flat-v2", workload, units, repeat,
                    options.path, options, diagnostics_enabled);
            }
            {
                MdbxSegmentedLexicalIndexOptions options;
                options.path = (base.string() + "-segmented-v1");
                options.descriptor = descriptor();
                options.encoding_limits.max_postings_per_segment = 128;
                run_durable<MdbxSegmentedLexicalIndex>(
                    "segmented-v1", workload, units, repeat,
                    options.path, options, diagnostics_enabled);
            }
        }
    }
    return 0;
}

#else

int main() { return 0; }

#endif
