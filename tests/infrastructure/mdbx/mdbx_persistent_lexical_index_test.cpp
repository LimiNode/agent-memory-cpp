#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <filesystem>
#include <array>
#include <iostream>
#include <stdexcept>
#include <utility>

namespace {

using namespace agent_memory;

LexicalIndexDescriptor descriptor(const ScopeId& scope, std::uint8_t seed) {
    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = seed;
    epoch[15] = static_cast<std::uint8_t>(seed + 1U);
    return LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{scope, ProjectionKind::Original, LexicalStatisticsEpoch{epoch}},
        "original",
        1,
        "tokenizer-v1",
        "normalization-v1",
        {1}
    };
}

FlatLexicalUnitInput unit(
    const ScopeId& scope,
    std::uint64_t id,
    std::uint64_t revision,
    std::string resource,
    std::vector<std::string> tokens
) {
    return FlatLexicalUnitInput{
        scope,
        KnowledgeUnitId{id},
        ProjectionKind::Original,
        ResourceId{std::move(resource)},
        revision,
        revision,
        ProjectionVersionRef{revision, revision, 1, "derivation-v1"},
        std::move(tokens)
    };
}

void require(bool condition, const char* message) {
    if(!condition) {
        throw std::runtime_error(message);
    }
}

MdbxPersistentLexicalIndexOptions options(
    const std::filesystem::path& path,
    const std::shared_ptr<MdbxStorageContext>& context,
    const ScopeId& scope,
    std::uint8_t seed
) {
    MdbxPersistentLexicalIndexOptions result;
    result.path = path.string();
    result.context = context;
    result.descriptor = descriptor(scope, seed);
    return result;
}

} // namespace

int main() {
    try {
        const auto path = std::filesystem::temp_directory_path()
            / "agent_memory_mdbx_persistent_lexical_index_test.mdbx";
        std::error_code error;
        std::filesystem::remove(path, error);

        const ScopeId scope_a{"scope-a"};
        const ScopeId scope_b{"scope-b"};
        {
            auto context = MdbxStorageContext::open(path.string());
            MdbxPersistentLexicalIndex first{options(path, context, scope_a, 7)};
            first.upsert(unit(scope_a, 1, 1, "resource-a", {"alpha", "shared"}));
            first.upsert(unit(scope_a, 2, 1, "resource-b", {"beta", "shared", "shared"}));
            first.upsert(unit(scope_a, 2, 1, "resource-b", {"beta", "shared", "shared"}));
            require(first.publication_sequence().value() == 3,
                "idempotent replay advanced the publication sequence");

            {
                const auto snapshot = first.read_snapshot();
                require(snapshot.frontier.consistency == LexicalReadConsistency::NonLinearizable,
                    "lexical-only snapshot was admitted as a full frontier");
                require(static_cast<bool>(snapshot.read_transaction_owner),
                    "snapshot did not retain its read transaction");
                require(snapshot.index.active_snapshot().statistics_generation
                        == snapshot.frontier.active_snapshot.statistics_generation,
                    "reopened index metadata disagrees with its frontier");
                require(snapshot.frontier.observed_unit_revisions.empty(),
                    "lexical-only frontier fabricated canonical observations");
                require(snapshot.index.collection_stats().unit_count == 2,
                    "collection unit count is wrong");
                const auto shared = snapshot.index.find_token_id("shared");
                require(shared.has_value(), "shared token was not persisted");
                const auto shared_stats = snapshot.index.find_token_stats(*shared);
                require(shared_stats && shared_stats->document_frequency == 2
                        && shared_stats->collection_frequency == 3,
                    "shared token statistics are wrong");
            }

            first.set_failure_injection_for_testing(
                MdbxPersistentLexicalIndexFaultPoint::BeforeCommit);
            bool interrupted = false;
            try {
                first.upsert(unit(scope_a, 1, 2, "resource-a", {"replacement"}),
                    FlatLexicalExpectedState{
                        KnowledgeUnitId{1}, 1, 1,
                        ProjectionVersionRef{1, 1, 1, "derivation-v1"}
                    });
            } catch(const std::runtime_error&) {
                interrupted = true;
            }
            require(interrupted, "fault injection did not interrupt publication");
        }

        {
            auto context = MdbxStorageContext::open(path.string());
            MdbxPersistentLexicalIndex reopened{options(path, context, scope_a, 7)};
            {
                const auto snapshot = reopened.read_snapshot();
                require(snapshot.index.collection_stats().unit_count == 2,
                    "aborted publication changed the active unit count");
                const auto old = snapshot.index.find_unit_stats(KnowledgeUnitId{1});
                require(old && old->unit_revision == 1,
                    "aborted publication replaced the old revision");
            }

            bool stale_rejected = false;
            try {
                reopened.upsert(unit(scope_a, 1, 3, "resource-a", {"new"}),
                    FlatLexicalExpectedState{
                        KnowledgeUnitId{1}, 99, 1,
                        ProjectionVersionRef{1, 1, 1, "derivation-v1"}
                    });
            } catch(const std::runtime_error&) {
                stale_rejected = true;
            }
            require(stale_rejected, "stale expected state was accepted");

            bool descriptor_rejected = false;
            try {
                MdbxPersistentLexicalIndex incompatible{options(path, context, scope_a, 99)};
                static_cast<void>(incompatible);
            } catch(const std::runtime_error&) {
                descriptor_rejected = true;
            }
            require(descriptor_rejected, "incompatible epoch was admitted");

            require(reopened.erase(KnowledgeUnitId{2}), "active unit was not erased");
            require(reopened.read_snapshot().index.collection_stats().unit_count == 1,
                "erase did not publish the reduced active set");

            MdbxPersistentLexicalIndex other_scope{options(path, context, scope_b, 8)};
            other_scope.upsert(unit(scope_b, 1, 1, "resource-c", {"shared"}));
            require(other_scope.read_snapshot().index.collection_stats().unit_count == 1,
                "second scope was not isolated");
            require(reopened.read_snapshot().index.collection_stats().unit_count == 1,
                "scope A was affected by scope B");
        }

        std::filesystem::remove(path, error);
        require(!error, "test database cleanup failed");
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

#else
int main() { return 0; }
#endif
