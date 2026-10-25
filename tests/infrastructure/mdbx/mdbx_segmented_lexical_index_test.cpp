#include <agent_memory/infrastructure/mdbx/MdbxSegmentedLexicalIndex.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <array>
#include <filesystem>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <utility>

namespace {

using namespace agent_memory;

LexicalIndexDescriptor descriptor(const ScopeId& scope) {
    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = 17;
    epoch[15] = 18;
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

MdbxSegmentedLexicalIndexOptions options(
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

void require(const bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

} // namespace

int main() {
    try {
        const auto path = std::filesystem::temp_directory_path()
            / "agent_memory_mdbx_segmented_lexical_index_test.mdbx";
        std::error_code error;
        std::filesystem::remove(path, error);
        const ScopeId scope{"scope-segmented"};

        {
            auto context = MdbxStorageContext::open(path.string());
            MdbxSegmentedLexicalIndex index{options(path, context, scope)};
            index.upsert(unit(scope, 1, 1, {"shared", "shared"}));
            index.upsert(unit(scope, 2, 1, {"shared", "other"}));
            {
                const auto snapshot = index.read_snapshot();
                require(snapshot.frontier.consistency == LexicalReadConsistency::PinnedSnapshot,
                    "segmented reader must expose a pinned lexical snapshot");
                require(snapshot.index.collection_stats().unit_count == 2,
                    "segmented collection statistics are wrong");
                const auto shared = snapshot.index.find_token_id("shared");
                require(shared.has_value(), "segmented dictionary lost shared token");
                const auto stats = snapshot.index.find_token_stats(*shared);
                require(stats && stats->document_frequency == 2
                        && stats->collection_frequency == 3,
                    "segmented token statistics are wrong");
            }

            index.set_failure_injection_for_testing(
                MdbxSegmentedLexicalIndexFaultPoint::AfterWriteBeforeCommit);
            bool interrupted = false;
            try {
                index.upsert(unit(scope, 1, 2, {"replacement"}));
            } catch(const std::runtime_error&) {
                interrupted = true;
            }
            require(interrupted, "segmented post-write fault did not interrupt");
        }

        {
            auto context = MdbxStorageContext::open(path.string());
            MdbxSegmentedLexicalIndex reopened{options(path, context, scope)};
            TokenId old_shared;
            {
                const auto snapshot = reopened.read_snapshot();
                require(snapshot.index.collection_stats().unit_count == 2,
                    "aborted segmented publication changed active units");
                const auto old = snapshot.index.find_unit_stats(KnowledgeUnitId{1});
                require(old && old->unit_revision == 1,
                    "aborted segmented publication replaced old revision");
                const auto shared = snapshot.index.find_token_id("shared");
                require(shared.has_value(), "reopen lost historical shared token");
                old_shared = *shared;
            }
            require(reopened.erase(KnowledgeUnitId{2}),
                "segmented erase did not remove active unit");
            {
                const auto snapshot = reopened.read_snapshot();
                require(snapshot.index.collection_stats().unit_count == 1,
                    "segmented erase did not publish reduced set");
            }
            reopened.upsert(unit(scope, 3, 1, {"new-token"}));
            {
                const auto snapshot = reopened.read_snapshot();
                const auto new_token = snapshot.index.find_token_id("new-token");
                require(new_token && new_token->value() > old_shared.value(),
                    "segmented dictionary reused a historical token id");
            }
            reopened.clear();
            {
                const auto snapshot = reopened.read_snapshot();
                require(snapshot.index.collection_stats().unit_count == 0,
                    "segmented clear did not remove active contributions");
            }
        }

        std::filesystem::remove(path, error);
        require(!error, "segmented test database cleanup failed");
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

#else

int main() { return 0; }

#endif
