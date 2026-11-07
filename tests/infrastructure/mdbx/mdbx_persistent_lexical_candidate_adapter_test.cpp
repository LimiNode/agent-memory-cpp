#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalCandidateAdapter.hpp>
#include <agent_memory/lexical/FlatLexicalIndex.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <array>
#include <algorithm>
#include <filesystem>
#include <iostream>
#include <map>
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
        ProjectionVersionRef{revision, revision, 1,
                             "m0.original.primary_text.v1"},
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

class TestUnits final : public IKnowledgeUnitStore {
public:
    void upsert(KnowledgeUnitEnvelope value) override {
        values[value.id.value()] = std::move(value);
    }
    bool erase(const KnowledgeUnitId& id) override {
        return values.erase(id.value()) != 0;
    }
    std::vector<KnowledgeUnitId> scan_by_scope_and_kind(
        const ScopeId& scope, KnowledgeUnitKind kind
    ) const override {
        std::vector<KnowledgeUnitId> result;
        for(const auto& entry : values) {
            if(entry.second.scope_id == scope && entry.second.kind == kind) {
                result.emplace_back(entry.first);
            }
        }
        return result;
    }
    std::vector<KnowledgeUnitId> scan_by_scope(
        const ScopeId& scope
    ) const override {
        std::vector<KnowledgeUnitId> result;
        for(const auto& entry : values) {
            if(entry.second.scope_id == scope) result.emplace_back(entry.first);
        }
        return result;
    }
    KnowledgeUnitEnvelope create_or_get_unit(
        KnowledgeUnitCreateRequest
    ) override { throw std::logic_error("unused"); }
    std::optional<KnowledgeUnitEnvelope> find(
        const KnowledgeUnitId& id
    ) const override {
        const auto found = values.find(id.value());
        return found == values.end()
            ? std::nullopt : std::optional<KnowledgeUnitEnvelope>{found->second};
    }
    std::optional<KnowledgeUnitId> find_by_content_key(
        const KnowledgeUnitKey&
    ) const override { return std::nullopt; }
    KnowledgeUnitEnvelope update_mutable_fields(
        const KnowledgeUnitId&, KnowledgeUnitMutablePatch
    ) override { throw std::logic_error("unused"); }
private:
    std::map<std::uint64_t, KnowledgeUnitEnvelope> values;
};

class TestProjections final : public IProjectionStore {
public:
    void put(SearchProjection value) override {
        values.push_back(std::move(value));
    }
    std::vector<SearchProjection> list(
        const ScopeId& scope,
        const KnowledgeUnitId& id,
        std::optional<ProjectionKind> kind
    ) const override {
        std::vector<SearchProjection> result;
        for(const auto& value : values) {
            if(value.scope_id == scope && value.unit_id == id &&
               (!kind || value.kind == *kind)) result.push_back(value);
        }
        return result;
    }
    bool delete_revision(
        const ScopeId&, const KnowledgeUnitId&, ProjectionKind, std::uint64_t
    ) override { return false; }
private:
    std::vector<SearchProjection> values;
};

class TestManifests final : public IResourceManifestStorage {
public:
    void upsert_manifest(ResourceManifest value) override {
        values[value.revision.resource_id.value()] = std::move(value);
    }
    std::optional<ResourceManifest> find_manifest(
        const ResourceId& id
    ) const override {
        const auto found = values.find(id.value());
        return found == values.end()
            ? std::nullopt : std::optional<ResourceManifest>{found->second};
    }
    bool erase_manifest(const ResourceId& id) override {
        return values.erase(id.value()) != 0;
    }
private:
    std::map<std::string, ResourceManifest> values;
};

SourceRefSummary source_ref(const ResourceId& resource) {
    SourceRefSummary value;
    value.resource_id = resource;
    value.uri = "memory://" + resource.value();
    value.resource_revision = ResourceRevisionRef{resource, 1, ResourceBodyDigest{}};
    value.preview = "shared evidence";
    value.quote_hash = compute_quote_hash(value.preview);
    value.excerpt = TextRange{0, value.preview.size()};
    value.confidence = 1.0;
    return value;
}

KnowledgeUnitEnvelope admitted_unit(
    const ScopeId& scope,
    const KnowledgeUnitId& id,
    const ResourceId& resource,
    const std::string& text
) {
    KnowledgeUnitEnvelope value;
    value.id = id;
    value.kind = KnowledgeUnitKind::Chunk;
    value.scope_id = scope;
    value.primary_text = text;
    value.display_text = text;
    value.sources = {source_ref(resource)};
    value.revision = 1;
    value.content_hash_recipe_version = 2;
    return value;
}

ResourceManifest admitted_manifest(
    const ResourceId& resource,
    const KnowledgeUnitId& id,
    std::uint64_t generation = 1
) {
    ResourceManifest value;
    value.revision.resource_id = resource;
    value.revision.generation = generation;
    value.revision.body_digest = ResourceBodyDigest{};
    value.records = {
        {DerivedRecordKind::Custom, {},
         "knowledge_unit:" + std::to_string(id.value()), 0},
        {DerivedRecordKind::Custom, {},
         "original_projection:" + std::to_string(id.value()), 0}
    };
    value.schema = ResourceManifestSchema{
        "agent_memory.m0_resource_importer.raw_chunks_original@agent_memory", 1};
    value.payload_version = ResourceManifestPayloadVersion::V6;
    return value;
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

            TestUnits units;
            TestProjections projections;
            TestManifests manifests;
            const auto admitted_first = admitted_unit(
                scope, KnowledgeUnitId{1}, ResourceId{"resource-a"},
                "alpha shared evidence");
            const auto admitted_second = admitted_unit(
                scope, KnowledgeUnitId{2}, ResourceId{"resource-b"},
                "beta shared evidence");
            units.upsert(admitted_first);
            units.upsert(admitted_second);
            projections.put(make_original_projection(
                admitted_first, admitted_first.revision));
            projections.put(make_original_projection(
                admitted_second, admitted_second.revision));
            manifests.upsert_manifest(admitted_manifest(
                ResourceId{"resource-a"}, admitted_first.id));
            manifests.upsert_manifest(admitted_manifest(
                ResourceId{"resource-b"}, admitted_second.id));
            const auto current = admit_persistent_lexical_candidates(
                scope, initial, units, projections, manifests, tokenizer);
            require(current.completeness == RevisionBoundCompleteness::Complete &&
                        current.admitted_count == 2,
                    "current persistent candidates were not admitted");
            for(const auto& result : current.results) {
                require(result.freshness == RevisionBoundFreshness::Current &&
                            result.materialization == RevisionBoundMaterialization::SummaryOnly &&
                            !result.text.empty(),
                        "current admission must retain canonical text");
            }
            MdbxPersistentLexicalCandidateAdapter posting_bounded{
                index, tokenizer, PersistentLexicalCandidateOptions{8, 1, 4}};
            const auto posting_truncated = posting_bounded.generate(query);
            require(posting_truncated.completeness ==
                        PersistentLexicalCandidateCompleteness::Truncated,
                    "posting budget did not report truncation");

            auto forged_scope = initial;
            forged_scope.candidates.front().scope_id = ScopeId{"other-scope"};
            const auto scope_rejected = admit_persistent_lexical_candidates(
                scope, forged_scope, units, projections, manifests, tokenizer);
            require(scope_rejected.stale_count == 1 &&
                        scope_rejected.results.front().text.empty(),
                    "forged candidate scope was not rejected");

            auto forged_projection = initial;
            forged_projection.candidates.front().projection_version.derivation_generation = 9;
            const auto projection_rejected = admit_persistent_lexical_candidates(
                scope, forged_projection, units, projections, manifests, tokenizer);
            require(projection_rejected.stale_count == 1 &&
                        projection_rejected.results.front().text.empty(),
                    "forged projection version was not rejected");

            auto stale_source = initial;
            manifests.upsert_manifest(admitted_manifest(
                ResourceId{"resource-b"}, admitted_second.id, 2));
            const auto stale_rejected = admit_persistent_lexical_candidates(
                scope, stale_source, units, projections, manifests, tokenizer);
            require(stale_rejected.stale_count == 1 &&
                        stale_rejected.results.front().text.empty(),
                    "source-generation drift was not rejected");
            manifests.upsert_manifest(admitted_manifest(
                ResourceId{"resource-b"}, admitted_second.id));

            auto truncated = initial;
            truncated.completeness = PersistentLexicalCandidateCompleteness::Truncated;
            const auto truncated_result = admit_persistent_lexical_candidates(
                scope, truncated, units, projections, manifests, tokenizer);
            require(truncated_result.completeness == RevisionBoundCompleteness::BudgetExceeded,
                    "truncated candidate response was not marked incomplete");
            PersistentLexicalCandidateResponse unsupported;
            unsupported.completeness = PersistentLexicalCandidateCompleteness::UnsupportedQuery;
            unsupported.diagnostic = "metadata filters are unsupported";
            const auto unsupported_result = admit_persistent_lexical_candidates(
                scope, unsupported, units, projections, manifests, tokenizer);
            require(unsupported_result.completeness == RevisionBoundCompleteness::UnsupportedQuery &&
                        !unsupported_result.diagnostic.empty(),
                    "unsupported candidate response lost its diagnostic");

            index.upsert(unit(scope, 1, 2, "resource-a", {"updated"}),
                         FlatLexicalExpectedState{
                             KnowledgeUnitId{1}, 1, 1,
                             ProjectionVersionRef{1, 1, 1,
                                                  "m0.original.primary_text.v1"}
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
