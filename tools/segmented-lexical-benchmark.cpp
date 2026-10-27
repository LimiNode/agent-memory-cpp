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
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

using namespace agent_memory;
using Clock = std::chrono::steady_clock;

LexicalIndexDescriptor descriptor(
    const std::uint32_t layout = kLexicalSegmentLayoutVersion
) {
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
        {layout}
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
    } else if(workload == "isolated-sparse-replacement") {
        // Deliberately avoid a shared anchor: this workload measures whether a
        // point replacement stays local as unrelated partitions are added.
        tokens = {
            "sp-" + std::to_string(index),
            "sl-" + std::to_string(index)};
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
    const std::string_view namespace_prefix
) {
    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), "agent_memory_lexical_posting_segments");
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    PhysicalCounters counters;
    const std::string from{namespace_prefix};
    std::string to = from;
    to.push_back(static_cast<char>(0xff));
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
                 "\tobserved_live_key_value_bytes\tpoint_reads\trange_rows_visited"
                 "\tkv_inserts_or_assigns\tkv_deletes\tbytes_decoded\tbytes_encoded"
                 "\ttouched_tokens\tdecoded_postings\tpoint_update_ms"
                 "\tupdate_point_reads\tupdate_range_rows_visited"
                 "\tupdate_kv_inserts_or_assigns\tupdate_kv_deletes"
                 "\tupdate_bytes_decoded\tupdate_bytes_encoded"
                 "\tupdate_touched_tokens\tupdate_decoded_postings"
                 "\tdb_bytes_before_update\tdb_bytes_after_update\n";
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
    const auto probe_token = workload == "isolated-sparse-replacement"
        ? "sp-0" : "anchor";
    const auto probe = index.find_token_id(probe_token);
    if(!probe) throw std::runtime_error("benchmark probe token missing");
    static_cast<void>(index.postings(*probe));
    const auto read_done = Clock::now();
    const auto update_started = Clock::now();
    const auto original = unit(0, workload);
    if(workload == "unique+anchor" || workload == "shared-hot") {
        auto replacement = original;
        replacement.resource_generation = 2;
        replacement.unit_revision = 2;
        replacement.projection_version = ProjectionVersionRef{2, 2, 1, "benchmark-v2"};
        replacement.normalized_tokens.push_back("point-update");
        index.upsert(replacement, FlatLexicalExpectedState{
            KnowledgeUnitId{1}, 1, 1,
            ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
    } else if(workload == "isolated-sparse-replacement") {
        auto replacement = original;
        replacement.resource_generation = 2;
        replacement.unit_revision = 2;
        replacement.projection_version = ProjectionVersionRef{2, 2, 1, "benchmark-v2"};
        replacement.normalized_tokens = {"sp-0", "sr"};
        index.upsert(replacement, FlatLexicalExpectedState{
            KnowledgeUnitId{1}, 1, 1,
            ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
    } else if(workload == "zipf-like") {
        static_cast<void>(index.erase(KnowledgeUnitId{1}));
    } else if(workload == "no-op") {
        index.upsert(original, FlatLexicalExpectedState{
            KnowledgeUnitId{1}, 1, 1,
            ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
    } else {
        index.upsert(unit(units, workload));
    }
    const auto update_done = Clock::now();
    if(workload == "zipf-like") {
        index.upsert(original);
    } else if(workload == "mixed-utf8") {
        static_cast<void>(index.erase(
            KnowledgeUnitId{static_cast<std::uint64_t>(units + 1U)}));
    }
    std::cout << "flat-reference\t" << workload << '\t' << units << '\t' << repeat
              << "\t0\t0\t" << milliseconds(write_done - started)
              << '\t' << milliseconds(read_done - write_done)
              << "\t0\t0\t0\t0\t0\t0\t"
              << index.collection_stats().unit_count
              << '\t' << index.collection_stats().total_token_count
              ;
    for(int field = 0; field != 12; ++field) std::cout << "\t0";
    std::cout << '\t' << milliseconds(update_done - update_started);
    for(int field = 0; field != 10; ++field) std::cout << "\t0";
    std::cout << '\n';
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
    MdbxSegmentedLexicalIndexDiagnostics diagnostics;
    if constexpr(std::is_same_v<OptionsT, MdbxSegmentedLexicalIndexOptions>) {
        options.diagnostics = diagnostics_enabled ? &diagnostics : nullptr;
    }
    const auto adapter_started = Clock::now();
    auto index = std::make_unique<IndexT>(options);
    const auto adapter_done = Clock::now();
    Clock::time_point write_started;
    Clock::time_point write_finished;
    Clock::time_point snapshot_started;
    Clock::time_point snapshot_finished;
    Clock::time_point diagnostic_started;
    Clock::time_point diagnostic_finished;
    Clock::time_point update_started;
    Clock::time_point update_finished;
    std::uintmax_t db_bytes_before_update = 0;
    std::uintmax_t db_bytes_after_update = 0;
    PhysicalCounters counters;
    MdbxSegmentedLexicalIndexDiagnostics initial_diagnostics;
    MdbxSegmentedLexicalIndexDiagnostics update_diagnostics;
    MdbxSegmentedLexicalIndexDiagnostics snapshot_diagnostics;
    MdbxSegmentedLexicalIndexDiagnostics reopen_diagnostics;
    {
        write_started = Clock::now();
        for(std::size_t index_value = 0; index_value < units; ++index_value) {
            index->upsert(unit(index_value, workload));
        }
        write_finished = Clock::now();
        if constexpr(std::is_same_v<OptionsT, MdbxSegmentedLexicalIndexOptions>) {
            initial_diagnostics = diagnostics;
            diagnostics = {};
        }
        db_bytes_before_update = database_bytes(path);
        update_started = Clock::now();
        const auto original = unit(0, workload);
        if(workload == "unique+anchor" || workload == "shared-hot") {
            auto replacement = original;
            replacement.resource_generation = 2;
            replacement.unit_revision = 2;
            replacement.projection_version = ProjectionVersionRef{2, 2, 1, "benchmark-v2"};
            replacement.normalized_tokens.push_back("point-update");
            index->upsert(
                replacement,
                FlatLexicalExpectedState{
                    KnowledgeUnitId{1}, 1, 1,
                    ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
        } else if(workload == "isolated-sparse-replacement") {
            auto replacement = original;
            replacement.resource_generation = 2;
            replacement.unit_revision = 2;
            replacement.projection_version = ProjectionVersionRef{2, 2, 1, "benchmark-v2"};
            replacement.normalized_tokens = {
                "sp-0", "sr"};
            index->upsert(
                replacement,
                FlatLexicalExpectedState{
                    KnowledgeUnitId{1}, 1, 1,
                    ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
        } else if(workload == "zipf-like") {
            static_cast<void>(index->erase(KnowledgeUnitId{1}));
        } else if(workload == "no-op") {
            index->upsert(
                original,
                FlatLexicalExpectedState{
                    KnowledgeUnitId{1}, 1, 1,
                    ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
        } else {
            auto inserted = unit(units, workload);
            index->upsert(inserted);
        }
        update_finished = Clock::now();
        if constexpr(std::is_same_v<OptionsT, MdbxSegmentedLexicalIndexOptions>) {
            update_diagnostics = diagnostics;
            diagnostics = {};
        }
        db_bytes_after_update = database_bytes(path);
        // Restore the logical corpus before snapshot/reopen so every backend has
        // the same final state while the measured operation remains isolated.
        if(workload == "zipf-like") {
            index->upsert(original);
        } else if(workload != "unique+anchor" && workload != "shared-hot"
            && workload != "isolated-sparse-replacement" && workload != "no-op") {
            static_cast<void>(index->erase(
                KnowledgeUnitId{static_cast<std::uint64_t>(units + 1U)}));
        }
        snapshot_started = Clock::now();
        {
            const auto snapshot = index->read_snapshot();
            const auto probe_token = workload == "isolated-sparse-replacement"
                ? "sp-0" : "anchor";
            const auto probe = snapshot.index.find_token_id(probe_token);
            if(!probe) throw std::runtime_error("benchmark probe token missing");
            static_cast<void>(snapshot.index.postings(*probe));
        }
        snapshot_finished = Clock::now();
        if constexpr(std::is_same_v<OptionsT, MdbxSegmentedLexicalIndexOptions>) {
            snapshot_diagnostics = diagnostics;
            diagnostics = {};
        }
        diagnostic_started = Clock::now();
        if(diagnostics_enabled) {
            counters = scan_rows(context,
                backend == "segmented-v2" ? "seg-v2" :
                backend == "segmented-v1" ? "seg-v1" : "flat-v2");
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
    reopen_options.descriptor = options.descriptor;
    if constexpr(std::is_same_v<OptionsT, MdbxSegmentedLexicalIndexOptions>) {
        reopen_options.encoding_limits.max_postings_per_segment = 128;
        reopen_options.diagnostics = diagnostics_enabled ? &diagnostics : nullptr;
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
    if constexpr(std::is_same_v<OptionsT, MdbxSegmentedLexicalIndexOptions>) {
        reopen_diagnostics = diagnostics;
    }
    const auto reopen_diagnostic_started = Clock::now();
    PhysicalCounters reopen_counters;
    if(diagnostics_enabled) {
        reopen_counters = scan_rows(reopen_context,
            backend == "segmented-v2" ? "seg-v2" :
            backend == "segmented-v1" ? "seg-v1" : "flat-v2");
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
              << '\t' << initial_diagnostics.point_reads
              << '\t' << initial_diagnostics.range_rows_visited
              << '\t' << initial_diagnostics.kv_inserts_or_assigns
              << '\t' << initial_diagnostics.kv_deletes
              << '\t' << initial_diagnostics.bytes_decoded
              << '\t' << initial_diagnostics.bytes_encoded
              << '\t' << initial_diagnostics.touched_tokens
              << '\t' << initial_diagnostics.decoded_postings
              << '\t' << milliseconds(update_finished - update_started)
              << '\t' << update_diagnostics.point_reads
              << '\t' << update_diagnostics.range_rows_visited
              << '\t' << update_diagnostics.kv_inserts_or_assigns
              << '\t' << update_diagnostics.kv_deletes
              << '\t' << update_diagnostics.bytes_decoded
              << '\t' << update_diagnostics.bytes_encoded
              << '\t' << update_diagnostics.touched_tokens
              << '\t' << update_diagnostics.decoded_postings
              << '\t' << db_bytes_before_update
              << '\t' << db_bytes_after_update
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
    const std::string workload_filter = argc > 5 ? argv[5] : "";
    print_header();
    for(const std::string workload : {
        "unique+anchor", "shared-hot", "isolated-sparse-replacement",
        "zipf-like", "mixed-utf8", "no-op"}) {
        if(!workload_filter.empty() && workload != workload_filter) continue;
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
            if(workload != "isolated-sparse-replacement") {
                MdbxSegmentedLexicalIndexOptions options;
                options.path = (base.string() + "-segmented-v1");
                options.descriptor = descriptor();
                options.encoding_limits.max_postings_per_segment = 128;
                run_durable<MdbxSegmentedLexicalIndex>(
                    "segmented-v1", workload, units, repeat,
                    options.path, options, diagnostics_enabled);
            }
            {
                MdbxSegmentedLexicalIndexOptions options;
                options.path = (base.string() + "-segmented-v2");
                options.descriptor = descriptor(kLexicalIncrementalSegmentLayoutVersion);
                options.encoding_limits.max_postings_per_segment = 128;
                run_durable<MdbxSegmentedLexicalIndex>(
                    "segmented-v2", workload, units, repeat,
                    options.path, options, diagnostics_enabled);
            }
        }
    }
    return 0;
}

#else

int main() { return 0; }

#endif
