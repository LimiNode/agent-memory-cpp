#include <agent_memory/lexical/FlatLexicalIndex.hpp>

#include <array>
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace {

    int fail(const std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    agent_memory::LexicalStatisticsEpoch make_epoch() {
        std::array<std::uint8_t, 16> bytes{};
        bytes.back() = 42;
        return agent_memory::LexicalStatisticsEpoch{bytes};
    }

    agent_memory::LexicalIndexDescriptor make_descriptor() {
        return agent_memory::LexicalIndexDescriptor{
            {
                agent_memory::ScopeId{"scope:flat"},
                agent_memory::ProjectionKind::Original,
                make_epoch()
            },
            "agent_memory.search_projection.original",
            1,
            "standard-tokenizer-v1",
            "lowercase-ascii-v1",
            {1}
        };
    }

    agent_memory::FlatLexicalUnitInput make_input(
        const std::uint64_t unit_id,
        const std::uint64_t unit_revision,
        std::vector<std::string> tokens
    ) {
        return agent_memory::FlatLexicalUnitInput{
            agent_memory::ScopeId{"scope:flat"},
            agent_memory::KnowledgeUnitId{unit_id},
            agent_memory::ProjectionKind::Original,
            agent_memory::ResourceId{"resource:flat"},
            3,
            unit_revision,
            agent_memory::ProjectionVersionRef{
                unit_revision,
                unit_revision,
                unit_revision,
                "original-v1"
            },
            std::move(tokens)
        };
    }

    agent_memory::FlatLexicalExpectedState expected_for(
        const agent_memory::FlatLexicalUnitInput& input
    ) {
        return agent_memory::FlatLexicalExpectedState{
            input.unit_id,
            input.resource_generation,
            input.unit_revision,
            input.projection_version
        };
    }

    int test_upsert_populates_flat_rows_and_stats() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        index.upsert(make_input(1, 7, {"agent", "memory", "agent"}));
        index.upsert(make_input(2, 8, {"agent", "storage"}));

        if(index.size() != 2
            || index.collection_stats().unit_count != 2
            || index.collection_stats().total_token_count != 5) {
            return fail("flat index must maintain collection statistics");
        }

        const auto agent_id = index.find_token_id("agent");
        if(!agent_id) {
            return fail("flat index must allocate a token id");
        }
        const auto agent_stats = index.find_token_stats(*agent_id);
        if(!agent_stats
            || agent_stats->document_frequency != 2
            || agent_stats->collection_frequency != 3) {
            return fail("flat index must maintain token document and collection frequency");
        }

        const auto agent_postings = index.postings(*agent_id);
        if(agent_postings.size() != 2
            || agent_postings[0].unit_id != agent_memory::KnowledgeUnitId{1}
            || agent_postings[0].term_frequency != 2
            || agent_postings[1].unit_id != agent_memory::KnowledgeUnitId{2}
            || agent_postings[1].term_frequency != 1) {
            return fail("flat postings must be ordered and preserve term frequency");
        }

        const auto unit_stats = index.find_unit_stats(agent_memory::KnowledgeUnitId{1});
        if(!unit_stats
            || unit_stats->unit_revision != 7
            || unit_stats->token_count != 3
            || unit_stats->unique_token_count != 2) {
            return fail("flat unit statistics must preserve revision and token counts");
        }
        return 0;
    }

    int test_replace_updates_active_contribution_without_reusing_tokens() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        const auto old = make_input(1, 7, {"old", "old"});
        index.upsert(old);
        const auto old_id = index.find_token_id("old");
        index.upsert(make_input(1, 8, {"new"}), expected_for(old));

        if(!old_id
            || index.find_token_stats(*old_id)
            || index.postings(*old_id).size() != 0
            || index.collection_stats().unit_count != 1
            || index.collection_stats().total_token_count != 1) {
            return fail("replacement must remove the prior active contribution");
        }

        const auto new_id = index.find_token_id("new");
        if(!new_id || new_id->value() <= old_id->value()) {
            return fail("token ids must remain monotonic and non-reused");
        }
        const auto stats = index.find_unit_stats(agent_memory::KnowledgeUnitId{1});
        if(!stats || stats->unit_revision != 8) {
            return fail("replacement must expose the new unit revision");
        }
        return 0;
    }

    int test_erase_clear_and_validation() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        index.upsert(make_input(1, 7, {"agent"}));
        if(!index.erase(agent_memory::KnowledgeUnitId{1})
            || index.erase(agent_memory::KnowledgeUnitId{1})
            || index.size() != 0
            || index.collection_stats().unit_count != 0) {
            return fail("erase must be idempotent and update collection stats");
        }

        try {
            index.upsert(make_input(2, 9, {""}));
            return fail("empty normalized token must be rejected");
        } catch(const std::invalid_argument&) {
        }

        auto wrong_scope = make_input(2, 9, {"agent"});
        wrong_scope.scope_id = agent_memory::ScopeId{"scope:other"};
        try {
            index.upsert(std::move(wrong_scope));
            return fail("cross-scope contribution must be rejected");
        } catch(const std::invalid_argument&) {
        }

        index.upsert(make_input(2, 9, {"agent", "memory"}));
        index.clear();
        if(index.size() != 0
            || index.collection_stats().unit_count != 0
            || !index.find_token_id("agent")) {
            return fail("clear must remove active rows while retaining dictionary ids");
        }
        return 0;
    }

    int test_revision_admission_and_idempotency() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        const auto first = make_input(1, 7, {"stable"});
        index.upsert(first);
        const auto generation = index.active_snapshot().statistics_generation;
        index.upsert(first);
        if(index.active_snapshot().statistics_generation != generation
            || index.collection_stats().total_token_count != 1) {
            return fail("identical revision replay must be an idempotent no-op");
        }

        auto conflict = first;
        conflict.normalized_tokens = {"different"};
        try {
            index.upsert(std::move(conflict));
            return fail("same revision with different content must be rejected");
        } catch(const std::runtime_error&) {
        }

        auto stale = make_input(1, 6, {"stale"});
        try {
            index.upsert(std::move(stale));
            return fail("stale revision must be rejected");
        } catch(const std::runtime_error&) {
        }

        auto fingerprint_conflict = first;
        fingerprint_conflict.projection_version.derivation_fingerprint = "zzz";
        try {
            index.upsert(std::move(fingerprint_conflict), expected_for(first));
            return fail("fingerprint change without numeric advancement must be rejected");
        } catch(const std::runtime_error&) {
        }

        auto stale_generation = make_input(1, 8, {"stale-generation"});
        stale_generation.resource_generation = 2;
        try {
            index.upsert(stale_generation, expected_for(first));
            return fail("resource generation regression must be rejected");
        } catch(const std::runtime_error&) {
        }

        auto newer = make_input(1, 8, {"newer"});
        try {
            index.upsert(newer);
            return fail("replacement without an expected current state must be rejected");
        } catch(const std::runtime_error&) {
        }
        index.upsert(newer, expected_for(first));
        return 0;
    }

    int test_failure_injection_preserves_state() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        const auto original = make_input(1, 7, {"old", "term"});
        index.upsert(original);
        const auto generation = index.active_snapshot().statistics_generation;

        index.set_failure_injection_for_testing(
            agent_memory::FlatLexicalUpsertFailurePoint::AfterTokenPreparation
        );
        try {
            index.upsert(make_input(1, 8, {"prepared"}), expected_for(original));
            return fail("token preparation failure must be injected");
        } catch(const std::runtime_error&) {
        }
        auto old_stats = index.find_unit_stats(agent_memory::KnowledgeUnitId{1});
        if(!old_stats || old_stats->unit_revision != 7
            || index.active_snapshot().statistics_generation != generation
            || !index.find_token_id("old")) {
            return fail("failed preparation must leave the old state unchanged");
        }

        index.set_failure_injection_for_testing(
            agent_memory::FlatLexicalUpsertFailurePoint::AfterDictionaryApplication
        );
        try {
            index.upsert(
                make_input(1, 8, {"dictionary-a", "dictionary-b"}),
                expected_for(original)
            );
            return fail("dictionary application failure must be injected");
        } catch(const std::runtime_error&) {
        }
        const auto dictionary_old_id = index.find_token_id("old");
        if(index.find_token_id("dictionary-a")
            || index.active_snapshot().statistics_generation != generation
            || !index.find_unit_stats(agent_memory::KnowledgeUnitId{1})
            || !dictionary_old_id
            || !index.find_token_stats(*dictionary_old_id)
            || index.find_token_stats(*dictionary_old_id)->document_frequency != 1
            || index.postings(*dictionary_old_id).size() != 1
            || index.collection_stats().total_token_count != 2) {
            return fail("failed dictionary application must restore the old state");
        }

        index.set_failure_injection_for_testing(
            agent_memory::FlatLexicalUpsertFailurePoint::AfterPostingApplication
        );
        try {
            index.upsert(
                make_input(1, 8, {"new-a", "new-b"}),
                expected_for(original)
            );
            return fail("posting application failure must be injected");
        } catch(const std::runtime_error&) {
        }
        old_stats = index.find_unit_stats(agent_memory::KnowledgeUnitId{1});
        const auto old_id = index.find_token_id("old");
        if(!old_stats || old_stats->unit_revision != 7
            || index.active_snapshot().statistics_generation != generation
            || !old_id
            || !index.find_token_stats(*old_id)
            || index.find_token_stats(*old_id)->document_frequency != 1
            || index.postings(*old_id).size() != 1
            || index.find_token_id("new-a")
            || index.collection_stats().total_token_count != 2) {
            return fail("failed posting application must roll back the complete state");
        }

        const auto next = make_input(2, 9, {"new-a"});
        index.upsert(next);
        const auto next_id = index.find_token_id("new-a");
        if(!next_id || next_id->value() != 3) {
            return fail("rollback must restore the next token id without reuse");
        }
        return 0;
    }

    int test_shared_token_statistics_rollback() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        const auto unit_a = make_input(1, 7, {"shared", "old"});
        const auto unit_b = make_input(2, 7, {"shared"});
        index.upsert(unit_a);
        index.upsert(unit_b);
        const auto generation = index.active_snapshot().statistics_generation;
        const auto shared_id = index.find_token_id("shared");
        if(!shared_id
            || !index.find_token_stats(*shared_id)
            || index.find_token_stats(*shared_id)->document_frequency != 2
            || index.find_token_stats(*shared_id)->collection_frequency != 2
            || index.postings(*shared_id).size() != 2) {
            return fail("shared-token fixture must start with DF/CF two");
        }

        const auto failure_points = {
            agent_memory::FlatLexicalUpsertFailurePoint::AfterDictionaryApplication,
            agent_memory::FlatLexicalUpsertFailurePoint::AfterPostingApplication
        };
        for(const auto point : failure_points) {
            index.set_failure_injection_for_testing(point);
            try {
                index.upsert(
                    make_input(1, 8, {"new", "replacement"}),
                    expected_for(unit_a)
                );
                return fail("shared-token replacement failure must be injected");
            } catch(const std::runtime_error&) {
            }

            const auto restored_shared = index.find_token_stats(*shared_id);
            const auto restored_a = index.find_unit_stats(agent_memory::KnowledgeUnitId{1});
            const auto restored_b = index.find_unit_stats(agent_memory::KnowledgeUnitId{2});
            if(!restored_shared
                || restored_shared->document_frequency != 2
                || restored_shared->collection_frequency != 2
                || index.postings(*shared_id).size() != 2
                || !restored_a || restored_a->unit_revision != 7
                || !restored_b || restored_b->unit_revision != 7
                || index.collection_stats().unit_count != 2
                || index.collection_stats().total_token_count != 3
                || index.active_snapshot().statistics_generation != generation
                || index.find_token_id("new")) {
                return fail("shared-token rollback must restore all old state");
            }
        }

        index.upsert(
            make_input(1, 8, {"new", "replacement"}),
            expected_for(unit_a)
        );
        const auto final_shared = index.find_token_stats(*shared_id);
        const auto new_id = index.find_token_id("new");
        const auto new_stats = new_id
            ? index.find_token_stats(*new_id)
            : std::nullopt;
        if(!final_shared
            || final_shared->document_frequency != 1
            || final_shared->collection_frequency != 1
            || index.postings(*shared_id).size() != 1
            || !new_id
            || !new_stats
            || new_stats->document_frequency != 1
            || index.collection_stats().unit_count != 2
            || index.collection_stats().total_token_count != 3) {
            return fail("successful replacement must update shared-token statistics");
        }
        return 0;
    }

    int test_original_projection_only() {
        auto descriptor = make_descriptor();
        descriptor.ref.projection_kind = agent_memory::ProjectionKind::Summary;
        try {
            agent_memory::FlatLexicalIndex index{std::move(descriptor)};
            static_cast<void>(index);
            return fail("flat lexical backend must reject unsupported projections");
        } catch(const std::invalid_argument&) {
        }
        return 0;
    }

} // namespace

int main() {
    int failures = 0;
    failures += test_upsert_populates_flat_rows_and_stats();
    failures += test_replace_updates_active_contribution_without_reusing_tokens();
    failures += test_erase_clear_and_validation();
    failures += test_revision_admission_and_idempotency();
    failures += test_failure_injection_preserves_state();
    failures += test_shared_token_statistics_rollback();
    failures += test_original_projection_only();
    return failures == 0 ? 0 : 1;
}
