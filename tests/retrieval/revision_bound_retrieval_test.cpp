#include <agent_memory/retrieval/RevisionBoundRetrieval.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#include <iostream>
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
    void upsert(KnowledgeUnitEnvelope value) override { values[value.id.value()] = std::move(value); }
    bool erase(const KnowledgeUnitId& id) override { return values.erase(id.value()) != 0; }
    std::vector<KnowledgeUnitId> scan_by_scope_and_kind(const ScopeId& scope, KnowledgeUnitKind kind) const override {
        std::vector<KnowledgeUnitId> result;
        for(const auto& entry : values) if(entry.second.scope_id == scope && entry.second.kind == kind) result.emplace_back(entry.first);
        return result;
    }
    std::vector<KnowledgeUnitId> scan_by_scope(const ScopeId& scope) const override {
        std::vector<KnowledgeUnitId> result;
        for(const auto& entry : values) if(entry.second.scope_id == scope) result.emplace_back(entry.first);
        return result;
    }
    KnowledgeUnitEnvelope create_or_get_unit(KnowledgeUnitCreateRequest) override { throw std::logic_error("unused"); }
    std::optional<KnowledgeUnitEnvelope> find(const KnowledgeUnitId& id) const override {
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
    void upsert_manifest(ResourceManifest value) override { values[value.revision.resource_id.value()] = std::move(value); }
    std::optional<ResourceManifest> find_manifest(const ResourceId& id) const override {
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
} // namespace

int main() {
    using namespace agent_memory;
    RetrievedChunk missing_identity;
    if(has_valid_revision_bound_candidate_identity(missing_identity)) {
        return fail("missing typed candidate identity must be rejected");
    }
    RetrievedChunk::CandidateIdentity invalid_identity;
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
       current.hydration != RevisionBoundHydrationOutcome::Partial ||
       current.freshness != RevisionBoundFreshness::Current ||
       current.materialization != RevisionBoundMaterialization::SummaryOnly ||
       current.text != value.primary_text) {
        std::cerr << "size=" << results.size() << " id=" << current.knowledge_unit_id.value()
                  << " scope=" << current.scope_id.value() << " rev=" << current.unit_revision
                  << " root=" << current.root_resource_id.value() << " gen=" << current.root_generation
                  << " digest=" << static_cast<bool>(current.root_digest)
                  << " source=" << static_cast<bool>(current.source)
                  << " hydration=" << static_cast<int>(current.hydration)
                  << " text=" << current.text << '\n';
        return fail("current result must preserve revision-bound identity and source");
    }

    const auto detailed = retriever.retrieve_detailed(query);
    if(detailed.completeness != RevisionBoundCompleteness::Complete ||
       detailed.results.size() != 1) {
        return fail("successful retrieval must report complete query status");
    }
    if(!matches_revision_bound_candidate_identity(
            RetrievedChunk::CandidateIdentity{
                value.id, value.revision, ProjectionKind::Original},
            value)) {
        return fail("matching typed candidate identity must be accepted");
    }
    auto mismatched_identity = RetrievedChunk::CandidateIdentity{
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
