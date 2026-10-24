#include <agent_memory/lexical/FlatLexicalIndex.hpp>

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
        index.upsert(make_input(1, 7, {"old", "old"}));
        const auto old_id = index.find_token_id("old");
        index.upsert(make_input(1, 8, {"new"}));

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

} // namespace

int main() {
    int failures = 0;
    failures += test_upsert_populates_flat_rows_and_stats();
    failures += test_replace_updates_active_contribution_without_reusing_tokens();
    failures += test_erase_clear_and_validation();
    return failures == 0 ? 0 : 1;
}
