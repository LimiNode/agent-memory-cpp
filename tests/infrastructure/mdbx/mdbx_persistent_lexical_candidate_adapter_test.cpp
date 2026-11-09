#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalCandidateAdapter.hpp>
#include <agent_memory/lexical/FlatLexicalIndex.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <array>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
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

class TestMdbxWorkspace final {
public:
    TestMdbxWorkspace() {
        const auto base = std::filesystem::temp_directory_path();
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        for(std::uint64_t attempt = 0; attempt < 1000; ++attempt) {
            const auto sequence = next_sequence.fetch_add(1);
            const auto candidate = base /
                ("agent_memory_candidate_adapter_" +
                 std::to_string(stamp) + "_" + std::to_string(sequence));
            std::error_code error;
            if(!std::filesystem::create_directory(candidate, error)) continue;
            m_root = candidate;
            m_database = m_root / "database.mdbx";
            m_marker = m_root / "owner.marker";
            std::ofstream marker(m_marker, std::ios::binary);
            marker << "agent-memory-candidate-adapter-owner-v1";
            if(!marker) throw std::runtime_error("failed to create workspace marker");
            m_foreign = base / (candidate.filename().string() + ".foreign");
            return;
        }
        throw std::runtime_error("failed to allocate exclusive MDBX workspace");
    }

    ~TestMdbxWorkspace() noexcept {
        try {
            std::ifstream marker(m_marker, std::ios::binary);
            std::string value;
            std::getline(marker, value);
            if(value == "agent-memory-candidate-adapter-owner-v1") {
                std::error_code error;
                std::filesystem::remove_all(m_root, error);
            }
        } catch(...) {
        }
    }

    [[nodiscard]] const std::filesystem::path& database_path() const noexcept {
        return m_database;
    }
    [[nodiscard]] const std::filesystem::path& foreign_path() const noexcept {
        return m_foreign;
    }

private:
    inline static std::atomic<std::uint64_t> next_sequence{0};
    std::filesystem::path m_root;
    std::filesystem::path m_database;
    std::filesystem::path m_marker;
    std::filesystem::path m_foreign;
};

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
    std::filesystem::path foreign_path;
    try {
        {
            TestMdbxWorkspace workspace;
            foreign_path = workspace.foreign_path();
            {
                std::ofstream foreign(foreign_path, std::ios::binary);
                foreign << "foreign-sentinel-v1";
            }
            const auto path = workspace.database_path();
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
            require(baseline.postings_prefix(*baseline_token, 1).size() == 1,
                    "bounded posting prefix did not cap physical consumption");
            require(is_consistent_flat_lexical_posting_count(2, 2) &&
                        !is_consistent_flat_lexical_posting_count(2, 1) &&
                        !is_consistent_flat_lexical_posting_count(1, 2),
                    "posting/statistics consistency seam accepted a mismatch");
            require(
                persistent_lexical_candidate_detail::classify_posting_window(3, 2, false) ==
                    PersistentLexicalCandidateCompleteness::InconsistentSnapshot &&
                persistent_lexical_candidate_detail::classify_posting_window(3, 3, true) ==
                    PersistentLexicalCandidateCompleteness::Truncated &&
                persistent_lexical_candidate_detail::classify_posting_window(3, 3, false) ==
                    PersistentLexicalCandidateCompleteness::Complete,
                "posting-window completeness classification is unsound");

            const auto shared_persistent_token = index.read_snapshot().index
                .find_token_id("shared");
            require(shared_persistent_token.has_value(),
                    "persistent shared token was not materialized");
            const auto physical_shared_postings = index.read_snapshot().index
                .posting_count(*shared_persistent_token);
            require(physical_shared_postings == 2,
                    "persistent physical posting count changed unexpectedly");

            index.set_statistics_fault_for_testing(
                *shared_persistent_token,
                FlatLexicalStatisticsFaultPoint::RemoveTokenStatistics);
            const auto missing_statistics_generated = adapter.generate(query);
            require(missing_statistics_generated.completeness ==
                        PersistentLexicalCandidateCompleteness::InconsistentSnapshot &&
                        missing_statistics_generated.candidates.empty(),
                    "generate accepted a token with missing statistics");

            index.set_statistics_fault_for_testing(
                *shared_persistent_token,
                FlatLexicalStatisticsFaultPoint::DocumentFrequencyTooHigh);
            const auto df_too_high_generated = adapter.generate(query);
            require(df_too_high_generated.completeness ==
                        PersistentLexicalCandidateCompleteness::InconsistentSnapshot &&
                        df_too_high_generated.candidates.empty(),
                    "generate accepted DF greater than physical postings");

            index.set_statistics_fault_for_testing(
                *shared_persistent_token,
                FlatLexicalStatisticsFaultPoint::DocumentFrequencyTooLow);
            const auto df_too_low_generated = adapter.generate(query);
            require(df_too_low_generated.completeness ==
                        PersistentLexicalCandidateCompleteness::InconsistentSnapshot &&
                        df_too_low_generated.candidates.empty(),
                    "generate accepted DF lower than physical postings");
            require(index.read_snapshot().index.posting_count(
                        *shared_persistent_token) == physical_shared_postings,
                    "statistics fault changed physical persistent postings");

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

            auto unsupported_with_candidates = initial;
            unsupported_with_candidates.completeness =
                PersistentLexicalCandidateCompleteness::UnsupportedQuery;
            const auto malformed_unsupported = admit_persistent_lexical_candidates(
                scope, unsupported_with_candidates,
                units, projections, manifests, tokenizer);
            require(malformed_unsupported.completeness ==
                        RevisionBoundCompleteness::InconsistentSnapshot &&
                        malformed_unsupported.results.empty(),
                    "unsupported response with candidates was not rejected");

            auto conflicting_observations = initial;
            conflicting_observations.candidates.push_back(
                conflicting_observations.candidates.front());
            conflicting_observations.candidates.back().resource_generation = 2;
            const auto conflict_result = admit_persistent_lexical_candidates(
                scope, conflicting_observations,
                units, projections, manifests, tokenizer);
            require(conflict_result.completeness ==
                        RevisionBoundCompleteness::InconsistentSnapshot &&
                        conflict_result.results.empty(),
                    "conflicting unit observations were not rejected");

            auto duplicate_observations = initial;
            duplicate_observations.candidates.push_back(
                duplicate_observations.candidates.front());
            const auto duplicate_result = admit_persistent_lexical_candidates(
                scope, duplicate_observations,
                units, projections, manifests, tokenizer);
            require(duplicate_result.completeness ==
                        RevisionBoundCompleteness::InconsistentSnapshot &&
                        duplicate_result.results.empty(),
                    "duplicate unit observations were not rejected");

            auto conflicting_revision = initial;
            conflicting_revision.candidates.push_back(
                conflicting_revision.candidates.front());
            conflicting_revision.candidates.back().identity.unit_revision = 99;
            const auto revision_conflict_result = admit_persistent_lexical_candidates(
                scope, conflicting_revision,
                units, projections, manifests, tokenizer);
            require(revision_conflict_result.completeness ==
                        RevisionBoundCompleteness::InconsistentSnapshot &&
                        revision_conflict_result.results.empty(),
                    "conflicting unit revisions were not rejected");

            PersistentLexicalCandidateResponse missing_statistics;
            missing_statistics.completeness =
                PersistentLexicalCandidateCompleteness::InconsistentSnapshot;
            missing_statistics.diagnostic =
                "persistent lexical token is missing collection statistics";
            const auto missing_statistics_result = admit_persistent_lexical_candidates(
                scope, missing_statistics,
                units, projections, manifests, tokenizer);
            require(missing_statistics_result.completeness ==
                        RevisionBoundCompleteness::InconsistentSnapshot &&
                        missing_statistics_result.results.empty(),
                    "missing statistics outcome was not fail-closed");

            PersistentLexicalCandidateResponse df_mismatch;
            df_mismatch.completeness =
                PersistentLexicalCandidateCompleteness::InconsistentSnapshot;
            df_mismatch.diagnostic =
                "persistent lexical posting/statistics counts disagree";
            const auto df_mismatch_result = admit_persistent_lexical_candidates(
                scope, df_mismatch, units, projections, manifests, tokenizer);
            require(df_mismatch_result.completeness ==
                        RevisionBoundCompleteness::InconsistentSnapshot &&
                        df_mismatch_result.results.empty(),
                    "DF/physical mismatch outcome was not fail-closed");

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
        }
        {
            std::ifstream foreign(foreign_path, std::ios::binary);
            std::string value;
            std::getline(foreign, value);
            require(value == "foreign-sentinel-v1",
                    "exclusive workspace cleanup modified a foreign file");
        }
        std::error_code error;
        std::filesystem::remove(foreign_path, error);
        std::cout << "persistent lexical candidate adapter: PASS\n";
        return 0;
    } catch(const std::exception& error) {
        std::error_code cleanup_error;
        if(!foreign_path.empty()) std::filesystem::remove(foreign_path, cleanup_error);
        std::cerr << "persistent lexical candidate adapter: " << error.what() << '\n';
        return 1;
    }
}

#else
int main() { return 0; }
#endif
