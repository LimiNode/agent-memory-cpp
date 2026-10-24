#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.hpp>

#include <mdbx_containers/KeyValueTable.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <filesystem>
#include <array>
#include <iostream>
#include <optional>
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

LexicalIndexDescriptor descriptor_with_layouts(
    const ScopeId& scope,
    std::uint8_t seed,
    std::size_t count
) {
    auto result = descriptor(scope, seed);
    result.supported_posting_layout_versions.clear();
    result.supported_posting_layout_versions.reserve(count);
    for(std::size_t index = 0; index < count; ++index) {
        result.supported_posting_layout_versions.push_back(
            static_cast<std::uint32_t>(index + 1U));
    }
    return result;
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

std::string legacy_key(const ScopeId& scope) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string encoded;
    for(const auto character : scope.value()) {
        const auto byte = static_cast<unsigned char>(character);
        encoded.push_back(digits[byte >> 4U]);
        encoded.push_back(digits[byte & 0x0fU]);
    }
    return "flat-v1" + std::string(1, '\x1f') + encoded
        + std::string(1, '\x1f')
        + std::to_string(static_cast<std::uint16_t>(ProjectionKind::Original));
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

            first.set_failure_injection_for_testing(
                MdbxPersistentLexicalIndexFaultPoint::AfterWriteBeforeCommit);
            interrupted = false;
            try {
                first.upsert(unit(scope_a, 1, 2, "resource-a", {"replacement"}),
                    FlatLexicalExpectedState{
                        KnowledgeUnitId{1}, 1, 1,
                        ProjectionVersionRef{1, 1, 1, "derivation-v1"}
                    });
            } catch(const std::runtime_error&) {
                interrupted = true;
            }
            require(interrupted, "post-write fault injection did not interrupt publication");
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
                require(snapshot.frontier.active_snapshot.statistics_generation == 3
                        && snapshot.frontier.publication_sequence.value() == 3
                        && snapshot.frontier.active_snapshot.statistics_epoch
                            == descriptor(scope_a, 7).ref.statistics_epoch,
                    "aborted publication changed durable snapshot metadata");
                const auto shared = snapshot.index.find_token_id("shared");
                const auto shared_stats = shared
                    ? snapshot.index.find_token_stats(*shared) : std::nullopt;
                require(shared_stats && shared_stats->document_frequency == 2
                        && shared_stats->collection_frequency == 3,
                    "aborted publication changed durable token statistics");
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

        {
            auto context = MdbxStorageContext::open(path.string());
            MdbxPersistentLexicalIndex reopened{options(path, context, scope_a, 7)};
            const auto alpha_id = reopened.read_snapshot().index.find_token_id("alpha");
            const auto shared_id = reopened.read_snapshot().index.find_token_id("shared");
            require(alpha_id && shared_id && alpha_id->value() == 1
                    && shared_id->value() == 2,
                "durable token dictionary was not restored before replay");
            require(reopened.erase(KnowledgeUnitId{1}),
                "unique-token unit was not erased before allocator test");
            reopened.upsert(unit(scope_a, 3, 1, "resource-d", {"gamma"}));
            const auto gamma_id = reopened.read_snapshot().index.find_token_id("gamma");
            require(gamma_id && gamma_id->value() > shared_id->value(),
                "token allocator reused an id after erase");
            reopened.clear();
        }

        {
            auto context = MdbxStorageContext::open(path.string());
            MdbxPersistentLexicalIndex reopened{options(path, context, scope_a, 7)};
            const auto before = reopened.read_snapshot().index.find_token_id("gamma");
            require(before && reopened.read_snapshot().index.collection_stats().unit_count == 0,
                "clear did not preserve the historical dictionary after reopen");
            reopened.upsert(unit(scope_a, 4, 1, "resource-e", {"delta"}));
            const auto delta = reopened.read_snapshot().index.find_token_id("delta");
            require(delta && delta->value() > before->value(),
                "token allocator regressed after clear and reopen");
        }

        {
            auto context = MdbxStorageContext::open(path.string());
            auto bounded = options(path, context, scope_a, 7);
            bounded.max_active_units = 1;
            MdbxPersistentLexicalIndex limited{std::move(bounded)};
            bool rejected = false;
            try {
                limited.upsert(unit(scope_a, 5, 1, "resource-f", {"epsilon"}));
            } catch(const std::runtime_error&) {
                rejected = true;
            }
            require(rejected, "bounded active-unit admission accepted an oversized state");
        }

        const auto bounded_path = std::filesystem::temp_directory_path()
            / "agent_memory_mdbx_persistent_lexical_index_bounded_test.mdbx";
        std::filesystem::remove(bounded_path, error);
        {
            auto context = MdbxStorageContext::open(bounded_path.string());
            auto bounded = options(bounded_path, context, scope_a, 7);
            bounded.max_total_tokens = 1;
            bounded.max_dictionary_entries = 3;
            MdbxPersistentLexicalIndex limited{std::move(bounded)};
            limited.upsert(unit(scope_a, 1, 1, "bounded-a", {"a"}));
            require(limited.erase(KnowledgeUnitId{1}),
                "bounded sequence failed to erase A");
            limited.upsert(unit(scope_a, 2, 1, "bounded-b", {"b"}));
            require(limited.erase(KnowledgeUnitId{2}),
                "bounded sequence failed to erase B");
            limited.upsert(unit(scope_a, 3, 1, "bounded-c", {"c"}));
            limited.set_failure_injection_for_testing(
                MdbxPersistentLexicalIndexFaultPoint::BeforeCommit);
            bool interrupted = false;
            try {
                limited.upsert(unit(scope_a, 3, 2, "bounded-c", {"b"}),
                    FlatLexicalExpectedState{
                        KnowledgeUnitId{3}, 1, 1,
                        ProjectionVersionRef{1, 1, 1, "derivation-v1"}
                    });
            } catch(const std::runtime_error&) {
                interrupted = true;
            }
            require(interrupted, "bounded sequence fault injection did not fire");
        }
        {
            auto context = MdbxStorageContext::open(bounded_path.string());
            auto bounded = options(bounded_path, context, scope_a, 7);
            bounded.max_total_tokens = 1;
            bounded.max_dictionary_entries = 3;
            MdbxPersistentLexicalIndex reopened{std::move(bounded)};
            {
                const auto snapshot = reopened.read_snapshot();
                const auto restored_unit = snapshot.index.find_unit_stats(KnowledgeUnitId{3});
                require(snapshot.index.collection_stats().unit_count == 1
                        && snapshot.index.find_token_id("c")
                        && restored_unit && restored_unit->unit_revision == 1,
                "bounded state was not readable after aborted replacement");
            }
            bool dictionary_rejected = false;
            try {
                reopened.upsert(unit(scope_a, 4, 1, "bounded-d", {"d"}));
            } catch(const std::runtime_error&) {
                dictionary_rejected = true;
            }
            require(dictionary_rejected,
                "historical dictionary admission exceeded its configured limit");
        }
        std::filesystem::remove(bounded_path, error);
        require(!error, "bounded test database cleanup failed");

        const auto layout_path = std::filesystem::temp_directory_path()
            / "agent_memory_mdbx_persistent_lexical_index_layout_test.mdbx";
        std::filesystem::remove(layout_path, error);
        {
            auto context = MdbxStorageContext::open(layout_path.string());
            auto layout_options = options(layout_path, context, scope_a, 7);
            layout_options.descriptor = descriptor_with_layouts(scope_a, 7, 256);
            MdbxPersistentLexicalIndex accepted{std::move(layout_options)};
            accepted.upsert(unit(scope_a, 1, 1, "layout-resource", {"layout"}));
        }
        {
            auto context = MdbxStorageContext::open(layout_path.string());
            auto layout_options = options(layout_path, context, scope_a, 7);
            layout_options.descriptor = descriptor_with_layouts(scope_a, 7, 256);
            MdbxPersistentLexicalIndex reopened{std::move(layout_options)};
            require(reopened.read_snapshot().index.collection_stats().unit_count == 1,
                "256-entry descriptor layout did not survive reopen");
        }
        std::filesystem::remove(layout_path, error);
        require(!error, "layout test database cleanup failed");
        const auto layout_reject_path = std::filesystem::temp_directory_path()
            / "agent_memory_mdbx_persistent_lexical_index_layout_reject.mdbx";
        std::filesystem::remove(layout_reject_path, error);
        {
            auto context = MdbxStorageContext::open(layout_reject_path.string());
            auto layout_options = options(layout_reject_path, context, scope_a, 7);
            layout_options.descriptor = descriptor_with_layouts(scope_a, 7, 257);
            bool rejected = false;
            try {
                MdbxPersistentLexicalIndex invalid{std::move(layout_options)};
                static_cast<void>(invalid);
            } catch(const std::invalid_argument&) {
                rejected = true;
            }
            require(rejected, "257-entry descriptor layout was accepted by the adapter");
        }
        std::filesystem::remove(layout_reject_path, error);
        require(!error, "layout reject test database cleanup failed");

        const auto legacy_path = std::filesystem::temp_directory_path()
            / "agent_memory_mdbx_persistent_lexical_index_legacy_test.mdbx";
        std::filesystem::remove(legacy_path, error);
        {
            auto legacy_context = MdbxStorageContext::open(legacy_path.string());
            {
                mdbxc::KeyValueTable<std::string, std::string> legacy_table(
                    legacy_context->connection(), "agent_memory_lexical_posting_segments");
                auto txn = legacy_context->connection()->transaction(
                    mdbxc::TransactionMode::WRITABLE);
                legacy_table.insert_or_assign(legacy_key(scope_a), "legacy-v1-payload", txn);
                txn.commit();
            }
            legacy_context.reset();
            bool legacy_rejected = false;
            try {
                auto check_context = MdbxStorageContext::open(legacy_path.string());
                MdbxPersistentLexicalIndex rejected{
                    options(legacy_path, check_context, scope_a, 7)};
                static_cast<void>(rejected);
            } catch(const std::runtime_error&) {
                legacy_rejected = true;
            }
            require(legacy_rejected,
                "legacy flat-v1 key was silently ignored instead of requiring rebuild");
        }
        std::filesystem::remove(legacy_path, error);
        require(!error, "legacy test database cleanup failed");

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
