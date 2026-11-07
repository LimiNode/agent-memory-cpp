#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalCandidateAdapter.hpp>
#include <agent_memory/lexical/FlatLexicalIndex.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <array>
#include <algorithm>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {

using namespace agent_memory;

LexicalIndexDescriptor descriptor(const ScopeId& scope) {
    std::array<std::uint8_t, 16> bytes{};
    bytes[0] = 7;
    bytes[15] = 8;
    return LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{scope, ProjectionKind::Original,
                                  LexicalStatisticsEpoch{bytes}},
        "original", 1, "standard-v1", "lowercase-v1", {1}
    };
}

FlatLexicalUnitInput unit(
    const ScopeId& scope,
    std::uint64_t id,
    std::uint64_t revision,
    const char* resource,
    std::vector<std::string> tokens
) {
    return FlatLexicalUnitInput{
        scope, KnowledgeUnitId{id}, ProjectionKind::Original,
        ResourceId{resource}, revision, revision,
        ProjectionVersionRef{revision, revision, 1, "recipe-v1"},
        std::move(tokens)
    };
}

MdbxPersistentLexicalIndexOptions options(
    const std::filesystem::path& path,
    const std::shared_ptr<MdbxStorageContext>& context,
    const ScopeId& scope
) {
    MdbxPersistentLexicalIndexOptions result;
    result.path = path.string();
    result.context = context;
    result.table_prefix = "candidate_adapter_test";
    result.descriptor = descriptor(scope);
    return result;
}

void require(bool value, const char* message) {
    if(!value) throw std::runtime_error(message);
}

} // namespace

int main() {
    try {
        const auto path = std::filesystem::temp_directory_path() /
            "agent_memory_persistent_candidate_adapter_test.mdbx";
        std::error_code error;
        std::filesystem::remove(path, error);
        const ScopeId scope{"candidate-scope"};
        {
            auto context = MdbxStorageContext::open(path.string());
            MdbxPersistentLexicalIndex index{options(path, context, scope)};
            index.upsert(unit(scope, 1, 1, "resource-a", {"alpha", "shared"}));
            index.upsert(unit(scope, 2, 1, "resource-b", {"beta", "shared", "shared"}));
            StandardTokenizer tokenizer;
            MdbxPersistentLexicalCandidateAdapter adapter{
                index, tokenizer, PersistentLexicalCandidateOptions{8, 8, 4}};

            // The dependency-free flat index is the rebuild-per-query logical
            // control. Compare candidate membership only: this adapter uses a
            // bounded TF ranking and does not claim exact BM25 score parity.
            FlatLexicalIndex baseline{descriptor(scope)};
            baseline.upsert(unit(scope, 1, 1, "resource-a", {"alpha", "shared"}));
            baseline.upsert(unit(scope, 2, 1, "resource-b", {"beta", "shared", "shared"}));

            RetrievalQuery query;
            query.text = "shared";
            query.limit = 8;
            const auto initial = adapter.generate(query);
            require(initial.completeness == PersistentLexicalCandidateCompleteness::Complete,
                    "initial candidate window was not complete");
            require(initial.candidates.size() == 2 &&
                        initial.candidates.front().identity.knowledge_unit_id.value() == 2,
                    "persistent candidate ranking did not use term frequency");
            require(initial.candidates.front().scope_id == scope &&
                        initial.candidates.front().resource_generation == 1,
                    "candidate provenance was not typed");
            const auto baseline_token = baseline.find_token_id("shared");
            require(baseline_token.has_value(), "baseline did not index shared token");
            const auto baseline_postings = baseline.postings(*baseline_token);
            std::vector<KnowledgeUnitId> baseline_ids;
            for(const auto& posting : baseline_postings) baseline_ids.push_back(posting.unit_id);
            std::vector<KnowledgeUnitId> persistent_ids;
            for(const auto& candidate : initial.candidates) {
                persistent_ids.push_back(candidate.identity.knowledge_unit_id);
            }
            std::sort(baseline_ids.begin(), baseline_ids.end());
            std::sort(persistent_ids.begin(), persistent_ids.end());
            require(baseline_ids == persistent_ids,
                    "persistent candidates changed baseline membership");

            index.upsert(unit(scope, 1, 2, "resource-a", {"updated"}),
                         FlatLexicalExpectedState{
                             KnowledgeUnitId{1}, 1, 1,
                             ProjectionVersionRef{1, 1, 1, "recipe-v1"}
                         });
            query.text = "alpha";
            require(adapter.generate(query).candidates.empty(),
                    "updated unit retained a stale lexical candidate");
            query.text = "updated";
            const auto updated = adapter.generate(query);
            require(updated.candidates.size() == 1 &&
                        updated.candidates.front().identity.unit_revision == 2,
                    "updated candidate did not expose the new revision");

            require(index.erase(KnowledgeUnitId{2}), "candidate erase failed");
            query.text = "shared";
            require(adapter.generate(query).candidates.empty(),
                    "erased unit remained in persistent candidate output");

            MdbxPersistentLexicalCandidateAdapter bounded{
                index, tokenizer, PersistentLexicalCandidateOptions{1, 1, 4}};
            query.text = "updated";
            require(bounded.generate(query).completeness ==
                        PersistentLexicalCandidateCompleteness::Complete,
                    "single-posting candidate was incorrectly truncated");
        }

        {
            auto context = MdbxStorageContext::open(path.string());
            MdbxPersistentLexicalIndex reopened{options(path, context, scope)};
            StandardTokenizer tokenizer;
            MdbxPersistentLexicalCandidateAdapter adapter{reopened, tokenizer};
            RetrievalQuery query;
            query.text = "updated";
            query.limit = 4;
            const auto result = adapter.generate(query);
            require(result.candidates.size() == 1 &&
                        result.candidates.front().identity.unit_revision == 2,
                    "reopen lost the current persistent candidate");
        }
        std::filesystem::remove(path, error);
        std::cout << "persistent lexical candidate adapter: PASS\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "persistent lexical candidate adapter: " << error.what() << '\n';
        return 1;
    }
}

#else
int main() { return 0; }
#endif
