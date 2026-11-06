#include <agent_memory/retrieval/RevisionBoundRetrieval.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#include <iostream>
#include <functional>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
using namespace agent_memory;

class Units final : public IKnowledgeUnitStore {
public:
    mutable std::size_t scan_count = 0;
    mutable std::size_t find_count = 0;
    std::function<void(std::size_t)> before_find;
    void upsert(KnowledgeUnitEnvelope value) override { values[value.id.value()] = std::move(value); }
    bool erase(const KnowledgeUnitId& id) override { return values.erase(id.value()) != 0; }
    std::vector<KnowledgeUnitId> scan_by_scope_and_kind(const ScopeId& scope, KnowledgeUnitKind kind) const override {
        std::vector<KnowledgeUnitId> result;
        for(const auto& entry : values) if(entry.second.scope_id == scope && entry.second.kind == kind) result.emplace_back(entry.first);
        return result;
    }
    std::vector<KnowledgeUnitId> scan_by_scope(const ScopeId& scope) const override {
        ++scan_count;
        std::vector<KnowledgeUnitId> result;
        for(const auto& entry : values) if(entry.second.scope_id == scope) result.emplace_back(entry.first);
        return result;
    }
    KnowledgeUnitEnvelope create_or_get_unit(KnowledgeUnitCreateRequest) override { throw std::logic_error("unused"); }
    std::optional<KnowledgeUnitEnvelope> find(const KnowledgeUnitId& id) const override {
        ++find_count;
        if(before_find) before_find(find_count);
        const auto it = values.find(id.value());
        return it == values.end() ? std::nullopt : std::optional<KnowledgeUnitEnvelope>{it->second};
    }
    std::optional<KnowledgeUnitId> find_by_content_key(const KnowledgeUnitKey&) const override { return std::nullopt; }
    KnowledgeUnitEnvelope update_mutable_fields(const KnowledgeUnitId&, KnowledgeUnitMutablePatch) override { throw std::logic_error("unused"); }
private:
    std::map<std::uint64_t, KnowledgeUnitEnvelope> values;
};

class Projections final : public IProjectionStore {
public:
    void put(SearchProjection value) override { values.push_back(std::move(value)); }
    std::vector<SearchProjection> list(const ScopeId& scope, const KnowledgeUnitId& id, std::optional<ProjectionKind> kind) const override {
        std::vector<SearchProjection> result;
        for(const auto& value : values) if(value.scope_id == scope && value.unit_id == id && (!kind || value.kind == *kind)) result.push_back(value);
        return result;
    }
    bool delete_revision(const ScopeId&, const KnowledgeUnitId&, ProjectionKind, std::uint64_t) override { return false; }
private:
    std::vector<SearchProjection> values;
};

class Manifests final : public IResourceManifestStorage {
public:
    mutable std::size_t find_count = 0;
    std::function<void(std::size_t)> before_find;
    void upsert_manifest(ResourceManifest value) override { values[value.revision.resource_id.value()] = std::move(value); }
    std::optional<ResourceManifest> find_manifest(const ResourceId& id) const override {
        ++find_count;
        if(before_find) before_find(find_count);
        const auto it = values.find(id.value());
        return it == values.end() ? std::nullopt : std::optional<ResourceManifest>{it->second};
    }
    bool erase_manifest(const ResourceId& id) override { return values.erase(id.value()) != 0; }
private:
    std::map<std::string, ResourceManifest> values;
};

SourceRefSummary source(const ResourceId& resource, std::uint64_t generation = 1) {
    SourceRefSummary value;
    value.resource_id = resource;
    value.uri = "memory://resource-a";
    value.resource_revision = ResourceRevisionRef{resource, generation, ResourceBodyDigest{}};
    value.preview = "alpha evidence";
    value.quote_hash = compute_quote_hash(value.preview);
    value.excerpt = TextRange{0, value.preview.size()};
    value.confidence = 1.0;
    return value;
}

ResourceManifest manifest(const ResourceId& resource, std::uint64_t id, std::uint64_t generation = 1) {
    ResourceManifest value;
    value.revision.resource_id = resource;
    value.revision.generation = generation;
    value.revision.body_digest = ResourceBodyDigest{};
    value.records = {
        {DerivedRecordKind::Custom, {}, "knowledge_unit:" + std::to_string(id), 0},
        {DerivedRecordKind::Custom, {}, "original_projection:" + std::to_string(id), 0}
    };
    value.schema = ResourceManifestSchema{"agent_memory.m0_resource_importer.raw_chunks_original@agent_memory", 1};
    value.payload_version = ResourceManifestPayloadVersion::V6;
    return value;
}

KnowledgeUnitEnvelope unit(
    const ScopeId& scope,
    const ResourceId& resource,
    std::uint64_t generation = 1
) {
    KnowledgeUnitEnvelope value;
    value.id = KnowledgeUnitId{7};
    value.kind = KnowledgeUnitKind::Chunk;
    value.scope_id = scope;
    value.primary_text = "durable alpha evidence";
    value.display_text = value.primary_text;
    value.sources = {source(resource, generation)};
    value.revision = 1;
    value.content_hash_recipe_version = 2;
    return value;
}

int fail(const char* message) { std::cerr << message << '\n'; return 1; }

int test_budget_and_pairing_contracts() {
    const ScopeId scope{"budget-scope"};
    Units units;
    Projections projections;
    Manifests manifests;
    for(std::uint64_t id = 1; id <= 3; ++id) {
        const ResourceId resource{"budget-resource-" + std::to_string(id)};
        auto value = unit(scope, resource);
        value.id = KnowledgeUnitId{id};
        units.upsert(value);
        projections.put(make_original_projection(value, value.revision));
        manifests.upsert_manifest(manifest(resource, id));
    }
    StandardTokenizer tokenizer;
    ProjectionAwareLexicalRetrieverOptions options;
    options.max_candidates = 2;
    RevisionBoundLexicalRetriever retriever(units, projections, manifests, scope, tokenizer, options);
    RetrievalQuery query;
    query.text = "alpha";
    for(const std::size_t limit : {0U, 1U, 2U}) {
        query.limit = limit;
        const auto detailed = retriever.retrieve_detailed(query);
        const auto simple = retriever.retrieve(query);
        if(detailed.completeness != RevisionBoundCompleteness::Complete ||
           !detailed.diagnostic.empty() || detailed.results.size() != limit || simple.size() != limit) {
            return fail("limits below/at the cap must agree in both APIs");
        }
    }
    const auto rejects_budget = [&](RevisionBoundLexicalRetriever& tested) {
        const auto detailed = tested.retrieve_detailed(query);
        if(detailed.completeness != RevisionBoundCompleteness::BudgetExceeded ||
           !detailed.results.empty() || detailed.diagnostic.empty()) return false;
        try { (void)tested.retrieve(query); }
        catch(const ProjectionAwareLexicalRetrievalBudgetExceeded& error) {
            return detailed.diagnostic == error.what();
        }
        return false;
    };
    query.limit = 3;
    units.scan_count = units.find_count = manifests.find_count = 0;
    if(!rejects_budget(retriever) || units.scan_count != 0 || units.find_count != 0 ||
       manifests.find_count != 0) {
        return fail("over-cap request must fail before store reads in both APIs");
    }
    // The old producer still caps results for legacy callers.
    ProjectionAwareLexicalRetriever legacy(units, projections, manifests, scope, tokenizer, options);
    if(legacy.retrieve(query).size() != 2) return fail("legacy cap behavior must remain unchanged");
    auto paired = legacy.retrieve_with_candidates(query);
    paired.validate_candidate_pairing();
    if(paired.candidates.size() != 2) return fail("producer must return paired candidates");
    for(std::size_t i = 0; i < paired.candidates.size(); ++i) {
        if(paired.candidates[i].score != paired.legacy.chunks[i].score ||
           paired.candidates[i].unit_revision != 1) return fail("candidate rank/score pairing changed");
    }
    const auto rejects_pairing = [](const ProjectionAwareLexicalRetrieval& value) {
        try { value.validate_candidate_pairing(); }
        catch(const std::logic_error& error) { return std::string(error.what()).find("unpaired") != std::string::npos; }
        return false;
    };
    auto short_candidates = paired;
    short_candidates.candidates.pop_back();
    auto short_legacy = paired;
    short_legacy.legacy.chunks.pop_back();
    if(!rejects_pairing(short_candidates) || !rejects_pairing(short_legacy)) {
        return fail("unequal parallel channel sizes must fail closed");
    }
    ProjectionAwareLexicalRetrieval{}.validate_candidate_pairing();

    query.limit = 1;
    auto scan_options = options;
    scan_options.max_scanned_units = 2;
    RevisionBoundLexicalRetriever scan_limited(units, projections, manifests, scope, tokenizer, scan_options);
    if(!rejects_budget(scan_limited)) return fail("scan budget must agree in both APIs");
    auto byte_options = options;
    byte_options.max_indexed_projection_bytes = 1;
    RevisionBoundLexicalRetriever byte_limited(units, projections, manifests, scope, tokenizer, byte_options);
    if(!rejects_budget(byte_limited)) return fail("byte budget must agree in both APIs");
    return 0;
}

int test_result_state_invariants() {
    // Each case starts fresh, with deterministic mutations after producer admission.
    for(int scenario = 0; scenario < 4; ++scenario) {
        const ScopeId scope{"state-scope"};
        const ResourceId resource{"state-resource"};
        Units units;
        Projections projections;
        Manifests manifests;
        auto value = unit(scope, resource);
        if(scenario == 1) value.sources.push_back(value.sources.front());
        units.upsert(value);
        projections.put(make_original_projection(value, value.revision));
        manifests.upsert_manifest(manifest(resource, value.id.value()));
        if(scenario == 2) {
            units.before_find = [&](std::size_t count) {
                if(count == 3) { ++value.revision; units.upsert(value); }
            };
        }
        if(scenario == 3) {
            manifests.before_find = [&](std::size_t count) {
                if(count == 4) manifests.upsert_manifest(manifest(resource, value.id.value(), 2));
            };
        }
        StandardTokenizer tokenizer;
        RevisionBoundLexicalRetriever retriever(units, projections, manifests, scope, tokenizer);
        RetrievalQuery query;
        query.text = "alpha";
        query.limit = 1;
        const auto response = retriever.retrieve_detailed(query);
        if(response.results.size() != 1) return fail("state regression must exercise final adapter admission");
        const auto& result = response.results.front();
        if(scenario == 0) {
            if(result.freshness != RevisionBoundFreshness::Current ||
               result.materialization != RevisionBoundMaterialization::SummaryOnly ||
               result.text != value.primary_text || !result.source || !result.root_digest) {
                return fail("Current/SummaryOnly requires text and exact provenance pair");
            }
        } else {
            if(!result.text.empty() || result.materialization != RevisionBoundMaterialization::Unavailable ||
               result.freshness != (scenario == 1 ? RevisionBoundFreshness::Current : RevisionBoundFreshness::Stale)) {
                return fail("unmaterialized or stale results must have no text");
            }
            if(scenario == 3) {
                if(!result.source || !result.root_digest || result.root_generation != 1 ||
                   result.source->resource_revision->generation != 1) {
                    return fail("stale diagnostic provenance must retain the exact old revision");
                }
            } else if(result.source || result.root_digest || !result.root_resource_id.empty() || result.root_generation != 0) {
                return fail("unverified source provenance must be absent");
            }
        }
    }
    const RevisionBoundRetrievalResult unavailable;
    if(unavailable.freshness != RevisionBoundFreshness::Unavailable ||
       unavailable.materialization != RevisionBoundMaterialization::Unavailable ||
       !unavailable.text.empty() || unavailable.source || unavailable.root_digest) {
        return fail("default unavailable result must not expose content or provenance");
    }
    return 0;
}
} // namespace

int main() {
    using namespace agent_memory;
    if(test_budget_and_pairing_contracts() != 0 || test_result_state_invariants() != 0) return 1;
    std::optional<ProjectionAwareLexicalCandidate> missing_identity;
    if(has_valid_revision_bound_candidate_identity(missing_identity)) {
        return fail("missing typed candidate identity must be rejected");
    }
    ProjectionAwareLexicalCandidate invalid_identity;
    if(is_valid_revision_bound_candidate_identity(invalid_identity)) {
        return fail("zero-valued candidate identity must be rejected");
    }
    invalid_identity.knowledge_unit_id = KnowledgeUnitId{7};
    invalid_identity.unit_revision = 1;
    invalid_identity.projection_kind = ProjectionKind::Summary;
    if(is_valid_revision_bound_candidate_identity(invalid_identity)) {
        return fail("wrong projection candidate identity must be rejected");
    }

    const ScopeId scope{"tenant-a"};
    const ResourceId resource{"resource-a"};
    Units units;
    Projections projections;
    Manifests manifests;
    const auto value = unit(scope, resource);
    units.upsert(value);
    projections.put(make_original_projection(value, value.revision));
    manifests.upsert_manifest(manifest(resource, value.id.value()));

    StandardTokenizer tokenizer;
    RevisionBoundLexicalRetriever retriever(units, projections, manifests, scope, tokenizer);
    RetrievalQuery query;
    query.text = "alpha";
    query.limit = 1;
    const auto results = retriever.retrieve(query);
    if(results.size() != 1) return fail("revision-bound adapter must return one candidate");
    const auto& current = results.front();
    if(current.knowledge_unit_id != value.id || current.scope_id != scope ||
       current.projection_kind != ProjectionKind::Original || current.unit_revision != 1 ||
       current.root_resource_id != resource || current.root_generation != 1 ||
       !current.root_digest || !current.source ||
       current.freshness != RevisionBoundFreshness::Current ||
       current.materialization != RevisionBoundMaterialization::SummaryOnly ||
       current.text != value.primary_text) {
        std::cerr << "size=" << results.size() << " id=" << current.knowledge_unit_id.value()
                  << " scope=" << current.scope_id.value() << " rev=" << current.unit_revision
                  << " root=" << current.root_resource_id.value() << " gen=" << current.root_generation
                  << " digest=" << static_cast<bool>(current.root_digest)
                  << " source=" << static_cast<bool>(current.source)
                  << " freshness=" << static_cast<int>(current.freshness)
                  << " text=" << current.text << '\n';
        return fail("current result must preserve revision-bound identity and source");
    }

    const auto detailed = retriever.retrieve_detailed(query);
    if(detailed.completeness != RevisionBoundCompleteness::Complete ||
       detailed.results.size() != 1) {
        return fail("successful retrieval must report complete query status");
    }
    if(!matches_revision_bound_candidate_identity(
            ProjectionAwareLexicalCandidate{
                value.id, value.revision, ProjectionKind::Original},
            value)) {
        return fail("matching typed candidate identity must be accepted");
    }
    auto mismatched_identity = ProjectionAwareLexicalCandidate{
        value.id, value.revision + 1, ProjectionKind::Original};
    if(matches_revision_bound_candidate_identity(mismatched_identity, value)) {
        return fail("revision-mismatched candidate identity must be rejected");
    }

    RetrievalQuery unsupported_query = query;
    unsupported_query.metadata_filters.push_back(MetadataFilter{"kind", "chunk"});
    const auto unsupported = retriever.retrieve_detailed(unsupported_query);
    if(unsupported.completeness != RevisionBoundCompleteness::UnsupportedQuery ||
       unsupported.results.size() != 0 || unsupported.diagnostic.empty()) {
        return fail("unsupported metadata filters must have an explicit diagnostic");
    }
    bool unsupported_threw = false;
    try { (void)retriever.retrieve(unsupported_query); }
    catch(const RevisionBoundUnsupportedQuery&) { unsupported_threw = true; }
    if(!unsupported_threw) {
        return fail("unsupported metadata filters must not look like no matches");
    }

    auto stale_manifest = *manifests.find_manifest(resource);
    stale_manifest.state = ResourceManifestState::ErasePending;
    manifests.upsert_manifest(std::move(stale_manifest));
    const auto stale = retriever.retrieve(query);
    if(!stale.empty()) return fail("stale source must not be returned as hydrated text");

    // A source update is not admitted until the new projection and manifest
    // are both published. This is the manual KB-0 composition seam.
    auto updated = unit(scope, resource, 2);
    updated.revision = 2;
    updated.primary_text = "updated alpha evidence";
    updated.display_text = updated.primary_text;
    units.upsert(updated);
    auto updated_manifest = manifest(resource, updated.id.value(), 2);
    manifests.upsert_manifest(updated_manifest);
    if(!retriever.retrieve(query).empty()) return fail("A@2 must wait for reindex/publication");
    projections.put(make_original_projection(updated, updated.revision));
    const auto refreshed = retriever.retrieve(query);
    if(refreshed.size() != 1 || refreshed.front().unit_revision != 2 ||
       refreshed.front().root_generation != 2 || refreshed.front().text != updated.primary_text) {
        return fail("reindexed A@2 must be admitted as the current result");
    }

    // Missing source and interrupted/corrupt publication fail closed.
    auto no_source = updated;
    no_source.sources.clear();
    units.upsert(no_source);
    if(!retriever.retrieve(query).empty()) return fail("missing source must fail closed");
    units.upsert(updated);
    auto corrupt = updated_manifest;
    corrupt.records.front().ordinal = 99;
    manifests.upsert_manifest(std::move(corrupt));
    if(!retriever.retrieve(query).empty()) return fail("corrupt binding must fail closed");
    auto interrupted = updated_manifest;
    interrupted.records.pop_back();
    manifests.upsert_manifest(std::move(interrupted));
    if(!retriever.retrieve(query).empty()) return fail("interrupted publication must fail closed");

    const ScopeId foreign_scope{"tenant-b"};
    RevisionBoundLexicalRetriever foreign_retriever(units, projections, manifests, foreign_scope, tokenizer);
    if(!foreign_retriever.retrieve(query).empty()) return fail("wrong scope must not produce candidates");
    return 0;
}
