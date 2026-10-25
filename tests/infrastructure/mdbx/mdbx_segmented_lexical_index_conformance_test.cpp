#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSegmentedLexicalIndex.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <array>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <utility>

namespace {

using namespace agent_memory;

LexicalIndexDescriptor descriptor(const ScopeId& scope) {
    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = 31;
    epoch[15] = 32;
    return LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{
            scope, ProjectionKind::Original, LexicalStatisticsEpoch{epoch}},
        "original",
        1,
        "tokenizer-v1",
        "normalization-v1",
        {kLexicalSegmentLayoutVersion}
    };
}

FlatLexicalUnitInput unit(
    const ScopeId& scope,
    const std::uint64_t id,
    const std::uint64_t revision,
    std::vector<std::string> tokens
) {
    return FlatLexicalUnitInput{
        scope,
        KnowledgeUnitId{id},
        ProjectionKind::Original,
        ResourceId{"resource:" + std::to_string(id)},
        revision,
        revision,
        ProjectionVersionRef{revision, revision, 1, "derivation-v1"},
        std::move(tokens)
    };
}

MdbxSegmentedLexicalIndexOptions segmented_options(
    const std::filesystem::path& path,
    const std::shared_ptr<MdbxStorageContext>& context,
    const ScopeId& scope
) {
    MdbxSegmentedLexicalIndexOptions result;
    result.path = path.string();
    result.context = context;
    result.descriptor = descriptor(scope);
    result.encoding_limits.max_postings_per_segment = 2;
    return result;
}

MdbxPersistentLexicalIndexOptions flat_options(
    const std::filesystem::path& path,
    const std::shared_ptr<MdbxStorageContext>& context,
    const ScopeId& scope
) {
    MdbxPersistentLexicalIndexOptions result;
    result.path = path.string();
    result.context = context;
    result.descriptor = descriptor(scope);
    return result;
}

void require(const bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

void test_oracle_parity(
    const std::filesystem::path& path,
    const ScopeId& scope
) {
    auto context = MdbxStorageContext::open(path.string());
    MdbxSegmentedLexicalIndex segmented{
        segmented_options(path, context, scope)};
    MdbxPersistentLexicalIndex flat{flat_options(path, context, scope)};
    const auto first = unit(scope, 1, 1, {"alpha", "shared", "shared"});
    const auto second = unit(scope, 2, 1, {"beta", "shared"});
    segmented.upsert(first);
    flat.upsert(first);
    segmented.upsert(second);
    flat.upsert(second);
    std::size_t segmented_size = 0;
    FlatLexicalCollectionStats segmented_collection;
    TokenId segmented_token;
    FlatLexicalTokenStats segmented_stats;
    {
        const auto segmented_snapshot = segmented.read_snapshot();
        segmented_size = segmented_snapshot.index.size();
        segmented_collection = segmented_snapshot.index.collection_stats();
        const auto shared = segmented_snapshot.index.find_token_id("shared");
        require(shared.has_value(), "segmented oracle lost shared token");
        segmented_token = *shared;
        const auto stats = segmented_snapshot.index.find_token_stats(*shared);
        require(stats.has_value(), "segmented oracle lost token statistics");
        segmented_stats = *stats;
    }
    const auto flat_snapshot = flat.read_snapshot();
    require(segmented_size == flat_snapshot.index.size(),
        "segmented and flat active unit counts diverged");
    require(segmented_collection.unit_count
            == flat_snapshot.index.collection_stats().unit_count
        && segmented_collection.total_token_count
            == flat_snapshot.index.collection_stats().total_token_count,
        "segmented and flat collection statistics diverged");
    const auto shared_flat = flat_snapshot.index.find_token_id("shared");
    require(shared_flat && segmented_token == *shared_flat,
        "segmented and flat dictionary identities diverged");
    const auto stats_segmented = segmented_stats;
    const auto stats_flat = flat_snapshot.index.find_token_stats(*shared_flat);
    require(stats_flat
            && stats_segmented.document_frequency == stats_flat->document_frequency
            && stats_segmented.collection_frequency == stats_flat->collection_frequency,
        "segmented and flat token statistics diverged");
}

void test_pinned_reader_and_stale_writer(
    const std::filesystem::path& path,
    const ScopeId& scope
) {
    auto context = MdbxStorageContext::open(path.string());
    MdbxSegmentedLexicalIndex first{segmented_options(path, context, scope)};
    MdbxSegmentedLexicalIndex second{segmented_options(path, context, scope)};
    first.upsert(unit(scope, 1, 1, {"old"}));
    {
        auto pinned = first.read_snapshot();
        std::thread writer([&second, &scope] {
            second.upsert(unit(scope, 2, 1, {"new"}));
        });
        writer.join();
        require(pinned.index.find_unit_stats(KnowledgeUnitId{2}) == std::nullopt,
            "pinned reader observed a later publication");
    }
    {
        const auto current = second.read_snapshot();
        require(current.index.find_unit_stats(KnowledgeUnitId{2}).has_value(),
            "new publication is not visible to a fresh reader");
    }
    bool stale_rejected = false;
    try {
        second.upsert(unit(scope, 1, 2, {"replacement"}),
            FlatLexicalExpectedState{
                KnowledgeUnitId{1}, 999, 1,
                ProjectionVersionRef{1, 1, 1, "derivation-v1"}
            });
    } catch(const std::runtime_error&) {
        stale_rejected = true;
    }
    require(stale_rejected, "stale expected unit state was accepted");
}

void test_faults_and_corruption(
    const std::filesystem::path& path,
    const ScopeId& scope
) {
    auto context = MdbxStorageContext::open(path.string());
    MdbxSegmentedLexicalIndex index{segmented_options(path, context, scope)};
    index.upsert(unit(scope, 1, 1, {"stable", "shared"}));
    require(index.active_snapshot().statistics_generation == 2,
        "initial segmented publication generation is wrong");
    for(const auto point : {
        MdbxSegmentedLexicalIndexFaultPoint::BeforeCommit,
        MdbxSegmentedLexicalIndexFaultPoint::AfterWriteBeforeCommit
    }) {
        index.set_failure_injection_for_testing(point);
        bool interrupted = false;
        try {
            index.upsert(unit(scope, 1, 2, {"replacement"}));
        } catch(const std::runtime_error&) {
            interrupted = true;
        }
        require(interrupted, "segmented fault point did not interrupt publication");
        const auto snapshot = index.read_snapshot();
        require(snapshot.index.find_unit_stats(KnowledgeUnitId{1})->unit_revision == 1,
            "failed publication changed active revision");
    }

    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), "agent_memory_lexical_posting_segments");
    std::string from = "seg-v1\x1f";
    std::string to = from;
    to.push_back(static_cast<char>(0xff));
    std::string descriptor_key;
    std::string descriptor_value;
    auto descriptor_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    table.for_each_range(from, to,
        [&table, &descriptor_txn, &descriptor_key, &descriptor_value](
            const std::string& key, const std::string& value) {
            if(key.find("descriptor") == std::string::npos) return true;
            descriptor_key = key;
            descriptor_value = value;
            table.erase(key, descriptor_txn);
            return false;
        }, descriptor_txn);
    require(!descriptor_key.empty(), "segmented descriptor fixture was not found");
    descriptor_txn.commit();
    bool rejected = false;
    try {
        static_cast<void>(index.read_snapshot());
    } catch(const std::runtime_error&) {
        rejected = true;
    }
    require(rejected, "segmented namespace without descriptor was accepted");
    auto restore_descriptor_txn = context->connection()->transaction(
        mdbxc::TransactionMode::WRITABLE);
    table.insert_or_assign(
        descriptor_key, descriptor_value, restore_descriptor_txn);
    restore_descriptor_txn.commit();

    auto unknown_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    table.insert_or_assign(
        descriptor_key.substr(0, descriptor_key.rfind('\x1f'))
            + "\x1funknown", "invalid", unknown_txn);
    unknown_txn.commit();
    rejected = false;
    try {
        static_cast<void>(index.read_snapshot());
    } catch(const std::runtime_error&) {
        rejected = true;
    }
    require(rejected, "segmented unknown row kind was accepted");
    auto remove_unknown_txn = context->connection()->transaction(
        mdbxc::TransactionMode::WRITABLE);
    table.erase(
        descriptor_key.substr(0, descriptor_key.rfind('\x1f'))
            + "\x1funknown", remove_unknown_txn);
    remove_unknown_txn.commit();

    std::string original_segment_key;
    std::string original_segment_value;
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    bool changed_again = table.for_each_range(from, to,
        [&table, &txn, &original_segment_key, &original_segment_value](
            const std::string& key, const std::string& value) {
            if(key.find("\x1fsegment\x1f") == std::string::npos) return true;
            original_segment_key = key;
            original_segment_value = value;
            table.insert_or_assign(key + "9", value, txn);
            table.erase(key, txn);
            return false;
        }, txn);
    static_cast<void>(changed_again);
    txn.commit();
    rejected = false;
    try {
        static_cast<void>(index.read_snapshot());
    } catch(const std::runtime_error&) {
        rejected = true;
    }
    require(rejected, "segment key/value identity mismatch was not rejected");

    auto restore_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    table.erase(original_segment_key + "9", restore_txn);
    table.insert_or_assign(original_segment_key, original_segment_value, restore_txn);
    restore_txn.commit();

    const auto segment_separator = original_segment_key.rfind('\x1f');
    require(segment_separator != std::string::npos,
        "segmented suffix fixture key is malformed");
    const auto segment_prefix = original_segment_key.substr(0, segment_separator + 1U);
    for(const auto& suffix : {std::string{"1:0junk"}, std::string{"01:0"},
                              std::string{"1:0:0"}}) {
        auto mutation_txn = context->connection()->transaction(
            mdbxc::TransactionMode::WRITABLE);
        table.erase(original_segment_key, mutation_txn);
        table.insert_or_assign(segment_prefix + suffix, original_segment_value, mutation_txn);
        mutation_txn.commit();
        rejected = false;
        try { static_cast<void>(index.read_snapshot()); }
        catch(const std::runtime_error&) { rejected = true; }
        require(rejected, "non-canonical segmented suffix was accepted");
        auto restore = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        table.erase(segment_prefix + suffix, restore);
        table.insert_or_assign(original_segment_key, original_segment_value, restore);
        restore.commit();
    }

    auto expect_key_value_mismatch = [&](const std::string& marker,
                                         const char* message) {
        auto mutation_txn = context->connection()->transaction(
            mdbxc::TransactionMode::WRITABLE);
        std::string key;
        std::string value;
        table.for_each_range(from, to,
            [&key, &value, &marker](const std::string& candidate,
                                    const std::string& candidate_value) {
                if(candidate.find(marker) == std::string::npos) return true;
                key = candidate;
                value = candidate_value;
                return false;
            }, mutation_txn);
        require(!key.empty(), message);
        table.erase(key, mutation_txn);
        table.insert_or_assign(key + "-mismatch", value, mutation_txn);
        mutation_txn.commit();
        bool failed = false;
        try { static_cast<void>(index.read_snapshot()); }
        catch(const std::runtime_error&) { failed = true; }
        require(failed, message);
        auto restore = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        table.erase(key + "-mismatch", restore);
        table.insert_or_assign(key, value, restore);
        restore.commit();
    };
    expect_key_value_mismatch(std::string(1, '\x1f') + "unit" + std::string(1, '\x1f'),
        "unit key/value mismatch was accepted");
    expect_key_value_mismatch(std::string(1, '\x1f') + "dictionary" + std::string(1, '\x1f'),
        "dictionary key/value mismatch was accepted");
    expect_key_value_mismatch(std::string(1, '\x1f') + "token-stats" + std::string(1, '\x1f'),
        "token stats key/value mismatch was accepted");
    expect_key_value_mismatch(std::string(1, '\x1f') + "witness" + std::string(1, '\x1f'),
        "witness key/value mismatch was accepted");

    for(const auto& metadata : {
            std::string(1, '\x1f') + "snapshot",
            std::string(1, '\x1f') + "sequence",
            std::string(1, '\x1f') + "high-water",
            std::string(1, '\x1f') + "collection-stats"}) {
        auto mutation_txn = context->connection()->transaction(
            mdbxc::TransactionMode::WRITABLE);
        std::string metadata_key;
        std::string metadata_value;
        table.for_each_range(from, to,
            [&metadata_key, &metadata_value, &metadata](const std::string& key,
                                                         const std::string& value) {
                if(key.size() < metadata.size()
                    || key.compare(key.size() - metadata.size(), metadata.size(), metadata) != 0) {
                    return true;
                }
                metadata_key = key;
                metadata_value = value;
                return false;
            }, mutation_txn);
        require(!metadata_key.empty(), "required metadata fixture row missing");
        table.erase(metadata_key, mutation_txn);
        mutation_txn.commit();
        rejected = false;
        try { static_cast<void>(index.read_snapshot()); }
        catch(const std::runtime_error&) { rejected = true; }
        require(rejected, "partially initialized segmented namespace was accepted");
        auto restore = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        table.insert_or_assign(metadata_key, metadata_value, restore);
        restore.commit();
    }

    auto limited_options = segmented_options(path, context, scope);
    limited_options.encoding_limits.max_namespace_rows = 1;
    bool limited_rejected = false;
    try {
        MdbxSegmentedLexicalIndex limited{limited_options};
        static_cast<void>(limited.read_snapshot());
    } catch(const std::runtime_error&) {
        limited_rejected = true;
    }
    require(limited_rejected, "segmented namespace row resource limit was ignored");

    limited_options.encoding_limits.max_namespace_rows = 8'000'000U;
    limited_options.encoding_limits.max_decoded_value_bytes = 1;
    limited_rejected = false;
    try {
        MdbxSegmentedLexicalIndex limited{limited_options};
        static_cast<void>(limited.read_snapshot());
    } catch(const std::runtime_error&) {
        limited_rejected = true;
    }
    require(limited_rejected, "segmented decoded byte budget was ignored");

    limited_options.encoding_limits.max_decoded_value_bytes = 256U * 1024U * 1024U;
    limited_options.encoding_limits.max_decoded_postings = 1;
    limited_rejected = false;
    try {
        MdbxSegmentedLexicalIndex limited{limited_options};
        static_cast<void>(limited.read_snapshot());
    } catch(const std::runtime_error&) {
        limited_rejected = true;
    }
    require(limited_rejected, "segmented aggregate posting budget was ignored");

    auto corrupt_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    bool changed_corruption = table.for_each_range(from, to,
        [&table, &corrupt_txn](const std::string& key, const std::string& value) {
            if(key.find("\x1fsegment\x1f") == std::string::npos) return true;
            auto corrupt = value;
            corrupt.back() ^= static_cast<char>(0x01);
            table.insert_or_assign(key, corrupt, corrupt_txn);
            return false;
        }, corrupt_txn);
    static_cast<void>(changed_corruption);
    corrupt_txn.commit();
    rejected = false;
    try {
        static_cast<void>(index.read_snapshot());
    } catch(const std::runtime_error&) {
        rejected = true;
    }
    require(rejected, "corrupted segment row was not rejected");
}

} // namespace

int main() {
    try {
        const auto base = std::filesystem::temp_directory_path();
        const ScopeId scope{"scope-conformance"};
        {
            const auto path = base / "agent_memory_segmented_oracle_parity.mdbx";
            std::error_code error;
            std::filesystem::remove(path, error);
            test_oracle_parity(path, scope);
            std::filesystem::remove(path, error);
            require(!error, "oracle parity cleanup failed");
        }
        {
            const auto path = base / "agent_memory_segmented_pinned_reader.mdbx";
            std::error_code error;
            std::filesystem::remove(path, error);
            test_pinned_reader_and_stale_writer(path, scope);
            std::filesystem::remove(path, error);
            require(!error, "pinned reader cleanup failed");
        }
        {
            const auto path = base / "agent_memory_segmented_faults.mdbx";
            std::error_code error;
            std::filesystem::remove(path, error);
            test_faults_and_corruption(path, scope);
            std::filesystem::remove(path, error);
            require(!error, "fault fixture cleanup failed");
        }
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

#else

int main() { return 0; }

#endif
