#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#include <iostream>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using namespace agent_memory;

class Units final : public IKnowledgeUnitStore {
public:
    void upsert(KnowledgeUnitEnvelope value) override { m_values[value.id.value()] = std::move(value); }
    bool erase(const KnowledgeUnitId& id) override { return m_values.erase(id.value()) != 0; }
    std::vector<KnowledgeUnitId> scan_by_scope_and_kind(const ScopeId& scope, KnowledgeUnitKind kind) const override {
        std::vector<KnowledgeUnitId> ids;
        for(const auto& [id, value] : m_values) if(value.scope_id.value() == scope.value() && value.kind == kind) ids.emplace_back(id);
        return ids;
    }
    std::vector<KnowledgeUnitId> scan_by_scope(const ScopeId& scope) const override {
        std::vector<KnowledgeUnitId> ids;
        for(const auto& [id, value] : m_values) if(value.scope_id.value() == scope.value()) ids.emplace_back(id);
        return ids;
    }
    KnowledgeUnitEnvelope create_or_get_unit(KnowledgeUnitCreateRequest) override { throw std::logic_error("unused"); }
    std::optional<KnowledgeUnitEnvelope> find(const KnowledgeUnitId& id) const override {
        const auto it = m_values.find(id.value());
        return it == m_values.end() ? std::nullopt : std::optional<KnowledgeUnitEnvelope>{it->second};
    }
    std::optional<KnowledgeUnitId> find_by_content_key(const KnowledgeUnitKey&) const override { return std::nullopt; }
    KnowledgeUnitEnvelope update_mutable_fields(const KnowledgeUnitId&, KnowledgeUnitMutablePatch) override { throw std::logic_error("unused"); }
private:
    std::map<std::uint64_t, KnowledgeUnitEnvelope> m_values;
};

class Projections final : public IProjectionStore {
public:
    void put(SearchProjection value) override { m_values.push_back(std::move(value)); }
    std::vector<SearchProjection> list(const ScopeId& scope, const KnowledgeUnitId& id, std::optional<ProjectionKind> kind) const override {
        std::vector<SearchProjection> result;
        for(const auto& value : m_values) if(value.scope_id.value() == scope.value() && value.unit_id == id && (!kind || value.kind == *kind)) result.push_back(value);
        return result;
    }
    bool delete_revision(const ScopeId&, const KnowledgeUnitId&, ProjectionKind, std::uint64_t) override { return false; }
private:
    std::vector<SearchProjection> m_values;
};

class Manifests final : public IResourceManifestStorage {
public:
    void upsert_manifest(ResourceManifest value) override { m_values[value.revision.resource_id.value()] = std::move(value); }
    std::optional<ResourceManifest> find_manifest(const ResourceId& id) const override {
        const auto it = m_values.find(id.value());
        return it == m_values.end() ? std::nullopt : std::optional<ResourceManifest>{it->second};
    }
    bool erase_manifest(const ResourceId& id) override { return m_values.erase(id.value()) != 0; }
private:
    std::map<std::string, ResourceManifest> m_values;
};

SourceRefSummary source(const ResourceId& id) {
    SourceRefSummary value;
    value.resource_id = id;
    value.resource_revision = ResourceRevisionRef{id, 1, ResourceBodyDigest{}};
    value.preview = "alpha beta";
    value.quote_hash = compute_quote_hash(value.preview);
    value.excerpt = TextRange{0, value.preview.size()};
    return value;
}

KnowledgeUnitEnvelope envelope(std::uint64_t id, std::string text, const ScopeId& scope, const ResourceId& resource) {
    KnowledgeUnitEnvelope value;
    value.id = KnowledgeUnitId{id};
    value.kind = KnowledgeUnitKind::Chunk;
    value.scope_id = scope;
    value.primary_text = std::move(text);
    value.display_text = value.primary_text;
    value.sources = {source(resource)};
    value.revision = 1;
    value.content_hash_recipe_version = 2;
    return value;
}

ResourceManifest manifest(const ResourceId& resource, std::uint64_t id) {
    ResourceManifest value;
    value.revision.resource_id = resource;
    value.revision.generation = 1;
    value.revision.body_digest = ResourceBodyDigest{};
    value.records.push_back(DerivedRecordRef{DerivedRecordKind::Custom, {}, "original_projection:" + std::to_string(id), 0});
    value.schema = ResourceManifestSchema{"m0.lexical_with_raw_and_original_projections", 1};
    value.payload_version = ResourceManifestPayloadVersion::V6;
    return value;
}

int fail(const char* message) { std::cerr << message << '\n'; return 1; }

} // namespace

int main() {
    using namespace agent_memory;
    const ScopeId scope{"tenant-a"};
    const ResourceId resource{"resource-a"};
    Units units;
    Projections projections;
    Manifests manifests;
    auto first = envelope(1, "durable alpha evidence", scope, resource);
    auto second = envelope(2, "other beta evidence", scope, resource);
    units.upsert(first);
    units.upsert(second);
    auto published = manifest(resource, 1);
    published.records.push_back(DerivedRecordRef{DerivedRecordKind::Custom, {}, "original_projection:2", 1});
    manifests.upsert_manifest(std::move(published));
    projections.put(make_original_projection(first, first.revision));
    projections.put(make_original_projection(second, second.revision));

    StandardTokenizer tokenizer;
    ProjectionAwareLexicalRetriever retriever(units, projections, manifests, scope, tokenizer);
    RetrievalQuery query;
    query.text = "alpha";
    query.limit = 5;
    const auto hit = retriever.retrieve(query);
    if(hit.size() != 1 || hit.chunks.front().chunk.text != first.primary_text) return fail("published projection must hydrate canonical text");

    auto stale = first;
    stale.revision = 2;
    stale.primary_text = "changed alpha";
    units.upsert(stale);
    if(!retriever.retrieve(query).empty()) return fail("stale projection must be rejected after unit revision changes");

    auto inactive = manifest(resource, 1);
    inactive.state = ResourceManifestState::ErasePending;
    manifests.upsert_manifest(inactive);
    if(!retriever.retrieve(query).empty()) return fail("erase-pending manifest must not admit hits");
    return 0;
}
