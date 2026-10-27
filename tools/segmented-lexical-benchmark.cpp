#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSegmentedLexicalIndex.hpp>
#include <agent_memory/lexical/FlatLexicalIndex.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <array>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

using namespace agent_memory;
using Clock = std::chrono::steady_clock;

struct Corpus final {
    std::map<std::pair<std::string, std::size_t>, std::vector<std::string>> rows;

    static Corpus load(const std::filesystem::path& path) {
        std::ifstream input(path, std::ios::binary);
        if(!input) throw std::runtime_error("cannot open benchmark corpus: " + path.string());
        Corpus corpus;
        std::string line;
        std::size_t line_number = 0;
        while(std::getline(input, line)) {
            ++line_number;
            if(line.empty() || line[0] == '#') continue;
            std::istringstream fields(line);
            std::string workload;
            std::string index_text;
            std::string tokens_text;
            if(!std::getline(fields, workload, '\t')
                || !std::getline(fields, index_text, '\t')
                || !std::getline(fields, tokens_text)) {
                throw std::runtime_error("malformed benchmark corpus line "
                    + std::to_string(line_number));
            }
            std::size_t index = 0;
            try { index = std::stoull(index_text); }
            catch(const std::exception&) {
                throw std::runtime_error("invalid benchmark corpus index at line "
                    + std::to_string(line_number));
            }
            std::vector<std::string> tokens;
            std::size_t begin = 0;
            while(begin <= tokens_text.size()) {
                const auto separator = tokens_text.find('\x1f', begin);
                const auto end = separator == std::string::npos
                    ? tokens_text.size() : separator;
                if(end == begin) throw std::runtime_error(
                    "empty benchmark corpus token at line " + std::to_string(line_number));
                tokens.emplace_back(tokens_text.substr(begin, end - begin));
                if(separator == std::string::npos) break;
                begin = separator + 1U;
            }
            const auto key = std::make_pair(workload, index);
            if(!corpus.rows.emplace(key, std::move(tokens)).second) {
                throw std::runtime_error("duplicate benchmark corpus row at line "
                    + std::to_string(line_number));
            }
        }
        if(corpus.rows.empty()) throw std::runtime_error("benchmark corpus is empty");
        return corpus;
    }

    const std::vector<std::string>* find(
        const std::string& workload,
        const std::size_t index
    ) const {
        const auto found = rows.find(std::make_pair(workload, index));
        return found == rows.end() ? nullptr : &found->second;
    }
};

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
    const std::string& workload,
    const Corpus* corpus = nullptr
) {
    std::vector<std::string> tokens;
    if(corpus != nullptr) {
        const auto* corpus_tokens = corpus->find(workload, index);
        if(corpus_tokens != nullptr) tokens = *corpus_tokens;
    }
    if(tokens.empty() && corpus != nullptr) {
        throw std::runtime_error("benchmark corpus row is missing for " + workload
            + "/" + std::to_string(index));
    }
    if(tokens.empty() && workload == "unique+anchor") {
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
    } else if(workload == "long-document") {
        tokens.assign(256U, "long-shared");
        tokens.push_back("anchor");
    } else if(workload == "small-insert") {
        tokens = {"insert-" + std::to_string(index), "insert-anchor"};
    } else if(workload == "small-delete") {
        tokens = {"delete-" + std::to_string(index), "delete-anchor"};
    } else if(workload == "inactive-history") {
        tokens = {"history-" + std::to_string(index), "history-anchor"};
    } else if(workload == "no-op") {
        tokens = {"anchor", "no-op", "stable-" + std::to_string(index % 32U)};
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

bool same_version(
    const ProjectionVersionRef& lhs,
    const ProjectionVersionRef& rhs
) {
    return lhs.unit_revision_at_build == rhs.unit_revision_at_build
        && lhs.projection_revision == rhs.projection_revision
        && lhs.derivation_generation == rhs.derivation_generation
        && lhs.derivation_fingerprint == rhs.derivation_fingerprint;
}

void require_logical_match(
    const FlatLexicalIndex& expected,
    const FlatLexicalIndex& actual,
    const std::size_t units,
    bool& token_id_parity
) {
    const auto expected_collection = expected.collection_stats();
    const auto actual_collection = actual.collection_stats();
    if(expected_collection.unit_count != actual_collection.unit_count
        || expected_collection.total_token_count != actual_collection.total_token_count) {
        throw std::runtime_error("benchmark collection statistics differ from oracle");
    }
    const auto expected_dictionary = expected.token_dictionary_for_storage();
    const auto actual_dictionary = actual.token_dictionary_for_storage();
    if(expected_dictionary.size() != actual_dictionary.size()) {
        throw std::runtime_error("benchmark token dictionary size differs from oracle");
    }
    for(const auto& expected_entry : expected_dictionary) {
        const auto actual_token_id = actual.find_token_id(expected_entry.first);
        if(!actual_token_id) {
            throw std::runtime_error("benchmark token dictionary differs from oracle");
        }
        if(expected_entry.second != *actual_token_id) token_id_parity = false;
        const auto expected_stats = expected.find_token_stats(expected_entry.second);
        const auto actual_stats = actual.find_token_stats(*actual_token_id);
        if(expected_stats.has_value() != actual_stats.has_value()) {
            throw std::runtime_error("benchmark token activity differs from oracle");
        }
        if(expected_stats && (expected_stats->document_frequency != actual_stats->document_frequency
            || expected_stats->collection_frequency != actual_stats->collection_frequency)) {
            throw std::runtime_error("benchmark DF/CF differs from oracle");
        }
        const auto expected_postings = expected.postings(expected_entry.second);
        const auto actual_postings = actual.postings(*actual_token_id);
        if(expected_postings.size() != actual_postings.size()) {
            throw std::runtime_error("benchmark posting count differs from oracle");
        }
        for(std::size_t posting = 0; posting < expected_postings.size(); ++posting) {
            const auto& lhs = expected_postings[posting];
            const auto& rhs = actual_postings[posting];
            if(lhs.unit_id != rhs.unit_id || lhs.resource_id != rhs.resource_id
                || lhs.resource_generation != rhs.resource_generation
                || !same_version(lhs.projection_version, rhs.projection_version)
                || lhs.term_frequency != rhs.term_frequency) {
                throw std::runtime_error("benchmark postings differ from oracle");
            }
        }
    }
    for(std::size_t index = 0; index < units; ++index) {
        const KnowledgeUnitId unit_id{static_cast<std::uint64_t>(index + 1U)};
        const auto expected_stats = expected.find_unit_stats(unit_id);
        const auto actual_stats = actual.find_unit_stats(unit_id);
        if(!expected_stats || !actual_stats
            || expected_stats->resource_id != actual_stats->resource_id
            || expected_stats->resource_generation != actual_stats->resource_generation
            || expected_stats->unit_revision != actual_stats->unit_revision
            || !same_version(expected_stats->projection_version, actual_stats->projection_version)
            || expected_stats->token_count != actual_stats->token_count
            || expected_stats->unique_token_count != actual_stats->unique_token_count) {
            throw std::runtime_error("benchmark unit revisions differ from oracle");
        }
    }
}

template<typename IndexT>
void require_expected_state_conflict(
    IndexT& index,
    FlatLexicalUnitInput input
) {
    input.resource_generation = 99;
    input.unit_revision = 99;
    input.projection_version = ProjectionVersionRef{99, 99, 1, "conflict-probe"};
    try {
        index.upsert(std::move(input), FlatLexicalExpectedState{
            KnowledgeUnitId{1}, 999, 999,
            ProjectionVersionRef{999, 999, 1, "stale"}});
    } catch(const std::runtime_error&) {
        return;
    }
    throw std::runtime_error("benchmark backend accepted stale expected state");
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
                 "\tdb_bytes_before_update\tdb_bytes_after_update"
                 "\tsnapshot_validation_ms\tprobe_read_ms\treopen_validation_ms"
                 "\tstale_state_ms\tno_op_batch_ms\tno_op_operation_count"
                 "\thistorical_preparation_ms\tinactive_history_count"
                 "\toperation_type\toperation_count\tsnapshot_scope"
                 "\treopen_snapshot_scope\ttoken_id_parity\n";
}

void run_flat_reference(
    const std::string& workload,
    const std::size_t units,
    const std::size_t repeat,
    const Corpus* corpus,
    const std::size_t inactive_history_count
) {
    FlatLexicalIndex index{descriptor()};
    const auto started = Clock::now();
    for(std::size_t index_value = 0; index_value < units; ++index_value) {
        index.upsert(unit(index_value, workload, corpus));
    }
    const auto write_done = Clock::now();
    const auto probe_token = unit(0, workload, corpus).normalized_tokens.front();
    const auto probe_started = Clock::now();
    const auto probe = index.find_token_id(probe_token);
    if(!probe) throw std::runtime_error("benchmark probe token missing");
    static_cast<void>(index.postings(*probe));
    const auto read_done = Clock::now();
    if(workload == "inactive-history") {
        for(std::uint64_t revision = 2;
            revision <= inactive_history_count + 1U; ++revision) {
            auto historical = unit(0, workload, corpus);
            historical.resource_generation = revision;
            historical.unit_revision = revision;
            historical.projection_version = ProjectionVersionRef{
                revision, revision, 1, "inactive-history"};
            historical.normalized_tokens.push_back(
                "inactive-" + std::to_string(revision));
            index.upsert(historical, FlatLexicalExpectedState{
                KnowledgeUnitId{1}, revision - 1U, revision - 1U,
                ProjectionVersionRef{revision - 1U, revision - 1U, 1,
                    revision == 2U ? "benchmark-v1" : "inactive-history"}});
        }
    }
    const auto update_started = Clock::now();
    const auto original = unit(0, workload, corpus);
    if(workload == "unique+anchor" || workload == "shared-hot"
        || workload == "long-document" || workload == "inactive-history") {
        auto replacement = original;
        const std::uint64_t next_revision = workload == "inactive-history"
            ? inactive_history_count + 2U : 2U;
        const std::uint64_t expected_revision = next_revision - 1U;
        replacement.resource_generation = next_revision;
        replacement.unit_revision = next_revision;
        replacement.projection_version = ProjectionVersionRef{
            next_revision, next_revision, 1, "benchmark-replacement"};
        replacement.normalized_tokens.push_back("point-update");
        index.upsert(replacement, FlatLexicalExpectedState{
            KnowledgeUnitId{1}, expected_revision, expected_revision,
            ProjectionVersionRef{expected_revision, expected_revision, 1,
                expected_revision == 1U ? "benchmark-v1" : "inactive-history"}});
    } else if(workload == "isolated-sparse-replacement") {
        auto replacement = original;
        replacement.resource_generation = 2;
        replacement.unit_revision = 2;
        replacement.projection_version = ProjectionVersionRef{2, 2, 1, "benchmark-v2"};
        replacement.normalized_tokens = {"sp-0", "sr"};
        index.upsert(replacement, FlatLexicalExpectedState{
            KnowledgeUnitId{1}, 1, 1,
            ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
    } else if(workload == "zipf-like" || workload == "small-delete") {
        static_cast<void>(index.erase(KnowledgeUnitId{1}));
    } else if(workload == "no-op") {
        for(std::size_t iteration = 0; iteration < 16U; ++iteration) {
            index.upsert(original, FlatLexicalExpectedState{
                KnowledgeUnitId{1}, 1, 1,
                ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
        }
    } else {
        index.upsert(unit(units, workload, corpus));
    }
    const auto update_done = Clock::now();
    if(workload == "zipf-like" || workload == "small-delete") {
        index.upsert(original);
    } else if(workload == "mixed-utf8" || workload == "small-insert") {
        static_cast<void>(index.erase(
            KnowledgeUnitId{static_cast<std::uint64_t>(units + 1U)}));
    }
    if(index.collection_stats().unit_count != units) {
        throw std::runtime_error("flat reference final unit count mismatch");
    }
    const auto stale_started = Clock::now();
    require_expected_state_conflict(index, unit(0, workload, corpus));
    const auto stale_done = Clock::now();
    const bool no_op = workload == "no-op";
    const auto operation_type = no_op ? "no-op" : workload == "small-insert"
        ? "insert" : workload == "small-delete" || workload == "zipf-like"
        ? "erase" : "replace";
    std::cout << "flat-reference\t" << workload << '\t' << units << '\t' << repeat
              << "\t0\t0\t" << milliseconds(write_done - started)
              << '\t' << milliseconds(read_done - write_done)
              << "\t0\t0\t0\t0\t0\t0\t"
              << index.collection_stats().unit_count
              << '\t' << index.collection_stats().total_token_count
              ;
    for(int field = 0; field != 12; ++field) std::cout << "\t0";
    std::cout << '\t' << (no_op ? milliseconds(update_done - update_started) / 16.0
        : milliseconds(update_done - update_started));
    for(int field = 0; field != 10; ++field) std::cout << "\t0";
    std::cout << '\t' << 0.0 << '\t' << milliseconds(read_done - probe_started)
              << '\t' << 0.0 << '\t' << milliseconds(stale_done - stale_started)
              << '\t' << (no_op ? milliseconds(update_done - update_started) : 0.0)
              << '\t' << (no_op ? 16U : 0U) << '\t' << 0.0 << '\t'
              << 0U << '\t' << operation_type << '\t' << (no_op ? 16U : 1U)
              << "\tlogical_lookup\tnot_applicable\ttrue\n";
}

template<typename IndexT, typename OptionsT>
void run_durable(
    const std::string& backend,
    const std::string& workload,
    const std::size_t units,
    const std::size_t repeat,
    const std::filesystem::path& path,
    OptionsT options,
    const bool diagnostics_enabled,
    const Corpus* corpus,
    const std::size_t inactive_history_count
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
    Clock::time_point snapshot_validation_started;
    Clock::time_point snapshot_validation_finished;
    Clock::time_point probe_started;
    Clock::time_point probe_finished;
    Clock::time_point update_started;
    Clock::time_point update_finished;
    Clock::time_point historical_started = Clock::now();
    Clock::time_point historical_finished = historical_started;
    Clock::time_point stale_started;
    Clock::time_point stale_finished;
    std::uintmax_t db_bytes_before_update = 0;
    std::uintmax_t db_bytes_after_update = 0;
    PhysicalCounters counters;
    MdbxSegmentedLexicalIndexDiagnostics initial_diagnostics;
    MdbxSegmentedLexicalIndexDiagnostics update_diagnostics;
    MdbxSegmentedLexicalIndexDiagnostics snapshot_diagnostics;
    MdbxSegmentedLexicalIndexDiagnostics reopen_diagnostics;
    FlatLexicalIndex oracle{descriptor()};
    bool token_id_parity = true;
    {
        write_started = Clock::now();
        for(std::size_t index_value = 0; index_value < units; ++index_value) {
            index->upsert(unit(index_value, workload, corpus));
        }
        write_finished = Clock::now();
        for(std::size_t index_value = 0; index_value < units; ++index_value) {
            oracle.upsert(unit(index_value, workload, corpus));
        }
        if(workload == "inactive-history") {
            historical_started = Clock::now();
            for(std::uint64_t revision = 2;
                revision <= inactive_history_count + 1U; ++revision) {
                auto historical = unit(0, workload, corpus);
                historical.resource_generation = revision;
                historical.unit_revision = revision;
                historical.projection_version = ProjectionVersionRef{
                    revision, revision, 1, "inactive-history"};
                historical.normalized_tokens.push_back(
                    "inactive-" + std::to_string(revision));
                const FlatLexicalExpectedState expected{
                    KnowledgeUnitId{1}, revision - 1U, revision - 1U,
                    ProjectionVersionRef{revision - 1U, revision - 1U, 1,
                        revision == 2U ? "benchmark-v1" : "inactive-history"}};
                index->upsert(historical, expected);
                oracle.upsert(historical, expected);
            }
            historical_finished = Clock::now();
        }
        if constexpr(std::is_same_v<OptionsT, MdbxSegmentedLexicalIndexOptions>) {
            initial_diagnostics = diagnostics;
            diagnostics = {};
        }
        db_bytes_before_update = database_bytes(path);
        update_started = Clock::now();
        const auto original = unit(0, workload, corpus);
        if(workload == "unique+anchor" || workload == "shared-hot"
            || workload == "long-document" || workload == "inactive-history") {
            auto replacement = original;
            const std::uint64_t next_revision = workload == "inactive-history"
                ? inactive_history_count + 2U : 2U;
            const std::uint64_t expected_revision = next_revision - 1U;
            replacement.resource_generation = next_revision;
            replacement.unit_revision = next_revision;
            replacement.projection_version = ProjectionVersionRef{
                next_revision, next_revision, 1, "benchmark-replacement"};
            replacement.normalized_tokens.push_back("point-update");
            index->upsert(
                replacement,
                FlatLexicalExpectedState{
                    KnowledgeUnitId{1}, expected_revision, expected_revision,
                    ProjectionVersionRef{expected_revision, expected_revision, 1,
                        expected_revision == 1U ? "benchmark-v1" : "inactive-history"}});
            oracle.upsert(replacement, FlatLexicalExpectedState{
                KnowledgeUnitId{1}, expected_revision, expected_revision,
                ProjectionVersionRef{expected_revision, expected_revision, 1,
                    expected_revision == 1U ? "benchmark-v1" : "inactive-history"}});
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
            oracle.upsert(replacement, FlatLexicalExpectedState{
                KnowledgeUnitId{1}, 1, 1,
                ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
        } else if(workload == "zipf-like" || workload == "small-delete") {
            static_cast<void>(index->erase(KnowledgeUnitId{1}));
            static_cast<void>(oracle.erase(KnowledgeUnitId{1}));
        } else if(workload == "no-op") {
            for(std::size_t iteration = 0; iteration < 16U; ++iteration) {
                index->upsert(original, FlatLexicalExpectedState{
                    KnowledgeUnitId{1}, 1, 1,
                    ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
                oracle.upsert(original, FlatLexicalExpectedState{
                    KnowledgeUnitId{1}, 1, 1,
                    ProjectionVersionRef{1, 1, 1, "benchmark-v1"}});
            }
        } else {
            auto inserted = unit(units, workload, corpus);
            index->upsert(inserted);
            oracle.upsert(inserted);
        }
        update_finished = Clock::now();
        if constexpr(std::is_same_v<OptionsT, MdbxSegmentedLexicalIndexOptions>) {
            update_diagnostics = diagnostics;
            diagnostics = {};
        }
        db_bytes_after_update = database_bytes(path);
        // Restore the logical corpus before snapshot/reopen so every backend has
        // the same final state while the measured operation remains isolated.
        if(workload == "zipf-like" || workload == "small-delete") {
            index->upsert(original);
            oracle.upsert(original);
        } else if(workload == "mixed-utf8" || workload == "small-insert") {
            static_cast<void>(index->erase(
                KnowledgeUnitId{static_cast<std::uint64_t>(units + 1U)}));
            static_cast<void>(oracle.erase(
                KnowledgeUnitId{static_cast<std::uint64_t>(units + 1U)}));
        }
        snapshot_started = Clock::now();
        snapshot_validation_started = snapshot_started;
        {
            const auto snapshot = index->read_snapshot();
            snapshot_finished = Clock::now();
            snapshot_validation_started = Clock::now();
            require_logical_match(oracle, snapshot.index, units, token_id_parity);
            snapshot_validation_finished = Clock::now();
            const auto probe_token = unit(0, workload, corpus).normalized_tokens.front();
            probe_started = Clock::now();
            const auto probe = snapshot.index.find_token_id(probe_token);
            if(!probe) throw std::runtime_error("benchmark probe token missing");
            static_cast<void>(snapshot.index.postings(*probe));
            probe_finished = Clock::now();
        }
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
    Clock::time_point reopen_snapshot_finished;
    Clock::time_point reopen_validation_started;
    Clock::time_point reopen_validation_finished;
    {
        const auto reopened_snapshot = reopened.read_snapshot();
        reopen_snapshot_finished = Clock::now();
        reopen_validation_started = Clock::now();
        require_logical_match(oracle, reopened_snapshot.index, units, token_id_parity);
        reopen_validation_finished = Clock::now();
        active_units = reopened_snapshot.index.collection_stats().unit_count;
        total_tokens = reopened_snapshot.index.collection_stats().total_token_count;
    }
    stale_started = Clock::now();
    require_expected_state_conflict(reopened, unit(0, workload, corpus));
    stale_finished = Clock::now();
    if(active_units != units || total_tokens == 0U) {
        throw std::runtime_error("durable benchmark final logical state mismatch");
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
              << '\t' << milliseconds(reopen_snapshot_finished - reopen_snapshot_started)
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
              << '\t' << milliseconds(snapshot_validation_finished - snapshot_validation_started)
              << '\t' << milliseconds(probe_finished - probe_started)
              << '\t' << milliseconds(reopen_validation_finished - reopen_validation_started)
              << '\t' << milliseconds(stale_finished - stale_started)
              << '\t' << (workload == "no-op"
                  ? milliseconds(update_finished - update_started) / 16.0 : 0.0)
              << '\t' << (workload == "no-op" ? 16U : 0U)
              << '\t' << milliseconds(historical_finished - historical_started)
              << '\t' << (workload == "inactive-history" ? inactive_history_count : 0U)
              << '\t' << (workload == "no-op" ? "no-op" : "mutation")
              << '\t' << (workload == "no-op" ? 16U : 1U)
              << "\tdurable_read_snapshot\tdurable_read_snapshot\t"
              << (token_id_parity ? "true" : "false")
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
    const std::string workload_filter_argument = argc > 5 ? argv[5] : "";
    const std::string workload_filter = workload_filter_argument == "-"
        || workload_filter_argument == "all" ? "" : workload_filter_argument;
    const auto corpus = argc > 6 && std::string_view{argv[6]}.size() != 0U
        ? std::optional<Corpus>{Corpus::load(argv[6])} : std::nullopt;
    const auto* corpus_ptr = corpus ? &*corpus : nullptr;
    std::set<std::string> backend_filter;
    if(argc > 7 && std::string_view{argv[7]}.size() != 0U) {
        std::istringstream values(argv[7]);
        std::string value;
        while(std::getline(values, value, ',')) backend_filter.insert(value);
    }
    const auto backend_enabled = [&backend_filter](const std::string& value) {
        return backend_filter.empty() || backend_filter.count(value) != 0U;
    };
    const std::size_t inactive_history_count = argc > 8
        ? std::stoull(argv[8]) : 64U;
    print_header();
    for(const std::string workload : {
        "unique+anchor", "shared-hot", "isolated-sparse-replacement",
        "zipf-like", "mixed-utf8", "long-document", "small-insert",
        "small-delete", "inactive-history", "no-op"}) {
        if(!workload_filter.empty() && workload != workload_filter) continue;
        for(std::size_t repeat = 0; repeat < repeats; ++repeat) {
            if(backend_enabled("flat-reference")) {
                run_flat_reference(workload, units, repeat, corpus_ptr,
                    inactive_history_count);
            }
            if(backend_enabled("flat-v2")) {
                MdbxPersistentLexicalIndexOptions options;
                options.path = (base.string() + "-flat-v2");
                options.descriptor = descriptor();
                run_durable<MdbxPersistentLexicalIndex>(
                    "flat-v2", workload, units, repeat,
                    options.path, options, diagnostics_enabled, corpus_ptr,
                    inactive_history_count);
            }
            if(backend_enabled("segmented-v1")
                && workload != "isolated-sparse-replacement") {
                MdbxSegmentedLexicalIndexOptions options;
                options.path = (base.string() + "-segmented-v1");
                options.descriptor = descriptor();
                options.encoding_limits.max_postings_per_segment = 128;
                run_durable<MdbxSegmentedLexicalIndex>(
                    "segmented-v1", workload, units, repeat,
                    options.path, options, diagnostics_enabled, corpus_ptr,
                    inactive_history_count);
            }
            if(backend_enabled("segmented-v2")) {
                MdbxSegmentedLexicalIndexOptions options;
                options.path = (base.string() + "-segmented-v2");
                options.descriptor = descriptor(kLexicalIncrementalSegmentLayoutVersion);
                options.encoding_limits.max_postings_per_segment = 128;
                run_durable<MdbxSegmentedLexicalIndex>(
                    "segmented-v2", workload, units, repeat,
                    options.path, options, diagnostics_enabled, corpus_ptr,
                    inactive_history_count);
            }
        }
    }
    return 0;
}

#else

int main() { return 0; }

#endif
