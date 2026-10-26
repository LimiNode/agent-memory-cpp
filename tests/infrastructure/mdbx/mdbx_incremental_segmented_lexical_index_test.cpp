#include <agent_memory/infrastructure/mdbx/MdbxSegmentedLexicalIndex.hpp>
#include <agent_memory/lexical/FlatLexicalIndex.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <array>
#include <filesystem>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace agent_memory;
constexpr char separator = '\x1f';

LexicalIndexDescriptor descriptor(const ScopeId& scope, const std::uint32_t layout) {
    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = 77;
    epoch[15] = 78;
    return LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{
            scope, ProjectionKind::Original, LexicalStatisticsEpoch{epoch}},
        "original", 1, "tokenizer-v1", "normalization-v1", {layout}
    };
}

FlatLexicalUnitInput unit(
    const ScopeId& scope,
    const std::uint64_t id,
    const std::uint64_t revision,
    std::vector<std::string> tokens
) {
    return FlatLexicalUnitInput{
        scope, KnowledgeUnitId{id}, ProjectionKind::Original,
        ResourceId{"resource:" + std::to_string(id)}, revision, revision,
        ProjectionVersionRef{revision, revision, 1, "derivation-v1"},
        std::move(tokens)
    };
}

MdbxSegmentedLexicalIndexOptions options(
    const std::filesystem::path& path,
    const std::shared_ptr<MdbxStorageContext>& context,
    const ScopeId& scope,
    const std::uint32_t layout = kLexicalIncrementalSegmentLayoutVersion
) {
    MdbxSegmentedLexicalIndexOptions result;
    result.path = path.string();
    result.context = context;
    result.descriptor = descriptor(scope, layout);
    result.encoding_limits.max_postings_per_segment = 2;
    return result;
}

void require(const bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

void compare_oracle(
    const MdbxSegmentedLexicalIndex& segmented,
    const FlatLexicalIndex& oracle,
    const std::vector<std::string>& tokens
) {
    const auto actual = segmented.read_snapshot();
    require(actual.index.collection_stats().unit_count == oracle.collection_stats().unit_count,
        "v2 oracle unit count mismatch");
    require(actual.index.collection_stats().total_token_count
            == oracle.collection_stats().total_token_count,
        "v2 oracle token count mismatch");
    for(const auto& token : tokens) {
        const auto expected_id = oracle.find_token_id(token);
        const auto actual_id = actual.index.find_token_id(token);
        require(expected_id == actual_id, "v2 oracle token identity mismatch");
        if(expected_id) {
            require(actual.index.find_token_stats(*actual_id)->document_frequency
                    == oracle.find_token_stats(*expected_id)->document_frequency,
                "v2 oracle document frequency mismatch");
            require(actual.index.find_token_stats(*actual_id)->collection_frequency
                    == oracle.find_token_stats(*expected_id)->collection_frequency,
                "v2 oracle collection frequency mismatch");
            const auto actual_postings = actual.index.postings(*actual_id);
            const auto expected_postings = oracle.postings(*expected_id);
            require(actual_postings.size() == expected_postings.size(),
                "v2 oracle posting count mismatch");
            for(std::size_t index = 0; index != actual_postings.size(); ++index) {
                const auto& lhs = actual_postings[index];
                const auto& rhs = expected_postings[index];
                require(lhs.token_id == rhs.token_id && lhs.unit_id == rhs.unit_id
                    && lhs.resource_id == rhs.resource_id
                    && lhs.resource_generation == rhs.resource_generation
                    && lhs.term_frequency == rhs.term_frequency
                    && lhs.projection_version.unit_revision_at_build
                        == rhs.projection_version.unit_revision_at_build
                    && lhs.projection_version.projection_revision
                        == rhs.projection_version.projection_revision
                    && lhs.projection_version.derivation_generation
                        == rhs.projection_version.derivation_generation
                    && lhs.projection_version.derivation_fingerprint
                        == rhs.projection_version.derivation_fingerprint,
                    "v2 oracle postings mismatch");
            }
        }
    }
}

std::map<std::string, std::string> rows(
    const std::shared_ptr<MdbxStorageContext>& context,
    const std::string& prefix
) {
    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), "agent_memory_lexical_posting_segments");
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    std::string to = prefix;
    to.push_back(static_cast<char>(0xff));
    std::map<std::string, std::string> result;
    table.for_each_range(prefix, to,
        [&result](const std::string& key, const std::string& value) {
            result.emplace(key, value);
            return true;
        }, txn);
    return result;
}

std::string v2_prefix(const ScopeId& scope) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string encoded = "seg-v2";
    encoded.push_back(separator);
    for(const auto character : scope.value()) {
        const auto byte = static_cast<unsigned char>(character);
        encoded.push_back(digits[byte >> 4U]);
        encoded.push_back(digits[byte & 0x0fU]);
    }
    encoded.push_back(separator);
    encoded += "1";
    return encoded;
}

void test_incremental_lifecycle(const std::filesystem::path& path) {
    const ScopeId scope{"scope-v2"};
    auto context = MdbxStorageContext::open(path.string());
    MdbxSegmentedLexicalIndex segmented{options(path, context, scope)};
    FlatLexicalIndex oracle{descriptor(scope, kLexicalIncrementalSegmentLayoutVersion),
                            kLexicalIncrementalSegmentLayoutVersion};

    const auto first = unit(scope, 1, 1, {"shared", "shared", "stable"});
    const auto second = unit(scope, 2, 1, {"shared", "changed"});
    segmented.upsert(first);
    oracle.upsert(first);
    compare_oracle(segmented, oracle, {"shared", "stable", "changed"});
    const auto first_sequence = segmented.publication_sequence();
    segmented.upsert(first);
    require(segmented.publication_sequence().value() == first_sequence.value(),
        "v2 no-op changed publication sequence");
    segmented.upsert(second);
    oracle.upsert(second);
    compare_oracle(segmented, oracle, {"shared", "stable", "changed"});

    const auto shared = segmented.read_snapshot().index.find_token_id("shared");
    require(shared.has_value(), "v2 shared token missing");
    const auto before_rows = rows(context, v2_prefix(scope));
    std::string changed_segment;
    for(const auto& row : before_rows) {
        if(row.first.find(std::string(1, separator) + "segment" + separator
                + std::to_string(segmented.read_snapshot().index.find_token_id("changed")->value())
                + ":") != std::string::npos) {
            changed_segment = row.second;
            break;
        }
    }
    require(!changed_segment.empty(), "v2 untouched partition fixture missing");

    const auto expected = FlatLexicalExpectedState{
        KnowledgeUnitId{1}, 1, 1, ProjectionVersionRef{1, 1, 1, "derivation-v1"}};
    segmented.upsert(unit(scope, 1, 2, {"stable", "replacement"}), expected);
    oracle.upsert(unit(scope, 1, 2, {"stable", "replacement"}), expected);
    compare_oracle(segmented, oracle, {"shared", "stable", "changed", "replacement"});
    const auto after_rows = rows(context, v2_prefix(scope));
    std::string changed_segment_after;
    for(const auto& row : after_rows) {
        if(row.first.find(std::string(1, separator) + "segment" + separator
                + std::to_string(segmented.read_snapshot().index.find_token_id("changed")->value())
                + ":") != std::string::npos) {
            changed_segment_after = row.second;
            break;
        }
    }
    require(changed_segment == changed_segment_after,
        "v2 replacement rewrote an untouched partition");

    bool stale_rejected = false;
    try { segmented.upsert(unit(scope, 1, 1, {"old"}), expected); }
    catch(const std::runtime_error&) { stale_rejected = true; }
    require(stale_rejected, "v2 stale revision was accepted");
    bool same_revision_rejected = false;
    try { segmented.upsert(unit(scope, 1, 2, {"different"}),
                           FlatLexicalExpectedState{
                               KnowledgeUnitId{1}, 1, 2,
                               ProjectionVersionRef{2, 2, 1, "derivation-v1"}}); }
    catch(const std::runtime_error&) { same_revision_rejected = true; }
    require(same_revision_rejected, "v2 same-revision conflict was accepted");

    const auto old_stable = segmented.read_snapshot().index.find_token_id("stable");
    segmented.clear();
    require(segmented.read_snapshot().index.collection_stats().unit_count == 0,
        "v2 clear left active units");
    segmented.upsert(unit(scope, 3, 1, {"new"}));
    const auto new_id = segmented.read_snapshot().index.find_token_id("new");
    require(old_stable && new_id && new_id->value() > old_stable->value(),
        "v2 dictionary high-water was reused");
    MdbxSegmentedLexicalIndex reopened{options(path, context, scope)};
    const auto reopened_snapshot = reopened.read_snapshot();
    require(reopened_snapshot.index.find_unit_stats(KnowledgeUnitId{3}).has_value(),
        "v2 reopen lost the active unit");
    const auto reopened_shared = reopened_snapshot.index.find_token_id("shared");
    require(reopened_shared && reopened_shared->value() == shared->value(),
        "v2 reopen changed the historical token identity");
}

void test_reopen_faults_and_namespace_isolation(const std::filesystem::path& path) {
    const ScopeId scope{"scope-v2-isolation"};
    auto context = MdbxStorageContext::open(path.string());
    MdbxSegmentedLexicalIndex v2{options(path, context, scope)};
    MdbxSegmentedLexicalIndex v1{options(path, context, scope, kLexicalSegmentLayoutVersion)};
    v2.upsert(unit(scope, 1, 1, {"v2"}));
    v1.upsert(unit(scope, 2, 1, {"v1"}));
    require(v2.read_snapshot().index.find_unit_stats(KnowledgeUnitId{2}) == std::nullopt,
        "v2 reader crossed into v1 namespace");
    require(v1.read_snapshot().index.find_unit_stats(KnowledgeUnitId{1}) == std::nullopt,
        "v1 reader crossed into v2 namespace");

    auto wrong_epoch = options(path, context, scope);
    auto wrong_epoch_bytes = std::array<std::uint8_t, 16>{};
    wrong_epoch_bytes[0] = 99;
    wrong_epoch_bytes[15] = 100;
    wrong_epoch.descriptor.ref.statistics_epoch =
        LexicalStatisticsEpoch{wrong_epoch_bytes};
    bool wrong_descriptor_rejected = false;
    try {
        MdbxSegmentedLexicalIndex wrong{wrong_epoch};
        static_cast<void>(wrong.read_snapshot());
    } catch(const std::runtime_error&) { wrong_descriptor_rejected = true; }
    require(wrong_descriptor_rejected, "v2 wrong descriptor was accepted");

    auto unknown_layout = options(path, context, scope, 999U);
    bool unknown_layout_rejected = false;
    try { MdbxSegmentedLexicalIndex unknown{unknown_layout}; }
    catch(const std::invalid_argument&) { unknown_layout_rejected = true; }
    require(unknown_layout_rejected, "v2 unknown layout was accepted");

    const ScopeId orphan_scope{"orphan-scope"};
    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), "agent_memory_lexical_posting_segments");
    auto orphan_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    table.insert_or_assign(
        v2_prefix(orphan_scope) + std::string(1, separator) + "unit" +
            std::string(1, separator) + "1", "orphan", orphan_txn);
    orphan_txn.commit();
    bool orphan_rejected = false;
    try {
        MdbxSegmentedLexicalIndex orphan{options(path, context, orphan_scope)};
        orphan.upsert(unit(orphan_scope, 1, 1, {"token"}));
    } catch(const std::runtime_error&) { orphan_rejected = true; }
    require(orphan_rejected, "v2 orphan rows were adopted without a descriptor");

    for(const auto point : {
        MdbxSegmentedLexicalIndexFaultPoint::BeforeCommit,
        MdbxSegmentedLexicalIndexFaultPoint::AfterWriteBeforeCommit
    }) {
        v2.set_failure_injection_for_testing(point);
        bool interrupted = false;
        try { v2.upsert(unit(scope, 1, 2, {"replacement"}),
                        FlatLexicalExpectedState{
                            KnowledgeUnitId{1}, 1, 1,
                            ProjectionVersionRef{1, 1, 1, "derivation-v1"}}); }
        catch(const std::runtime_error&) { interrupted = true; }
        require(interrupted, "v2 fault injection did not interrupt");
        require(v2.read_snapshot().index.find_unit_stats(KnowledgeUnitId{1})->unit_revision == 1,
            "v2 aborted transaction changed active revision");
    }
}

} // namespace

int main() {
    try {
        const auto base = std::filesystem::temp_directory_path();
        const auto lifecycle = base / "agent_memory_mdbx_incremental_segmented_lifecycle.mdbx";
        const auto isolation = base / "agent_memory_mdbx_incremental_segmented_isolation.mdbx";
        std::error_code error;
        std::filesystem::remove(lifecycle, error);
        std::filesystem::remove(isolation, error);
        test_incremental_lifecycle(lifecycle);
        test_reopen_faults_and_namespace_isolation(isolation);
        std::filesystem::remove(lifecycle, error);
        std::filesystem::remove(isolation, error);
        require(!error, "v2 test database cleanup failed");
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

#else

int main() { return 0; }

#endif
