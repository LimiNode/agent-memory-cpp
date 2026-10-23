#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#include <functional>
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
    using FindHook = std::function<void(const KnowledgeUnitId&, std::size_t)>;

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
        const auto call_number = ++m_find_calls[id.value()];
        if(m_find_hook) m_find_hook(id, call_number);
        const auto it = m_values.find(id.value());
        return it == m_values.end() ? std::nullopt : std::optional<KnowledgeUnitEnvelope>{it->second};
    }
    std::optional<KnowledgeUnitId> find_by_content_key(const KnowledgeUnitKey&) const override { return std::nullopt; }
    KnowledgeUnitEnvelope update_mutable_fields(const KnowledgeUnitId&, KnowledgeUnitMutablePatch) override { throw std::logic_error("unused"); }
    void set_find_hook(FindHook hook) { m_find_hook = std::move(hook); }
private:
    std::map<std::uint64_t, KnowledgeUnitEnvelope> m_values;
    mutable std::map<std::uint64_t, std::size_t> m_find_calls;
    FindHook m_find_hook;
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
    value.records.push_back(DerivedRecordRef{DerivedRecordKind::Custom, {}, "knowledge_unit:" + std::to_string(id), 0});
    value.records.push_back(DerivedRecordRef{DerivedRecordKind::Custom, {}, "original_projection:" + std::to_string(id), 0});
    value.schema = ResourceManifestSchema{"agent_memory.m0_resource_importer.raw_chunks_original@agent_memory", 1};
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
    const ScopeId foreign_scope{"tenant-b"};
    const ResourceId foreign_resource{"resource-b"};
    auto foreign = envelope(3, "durable alpha foreign", foreign_scope, foreign_resource);
    units.upsert(first);
    units.upsert(second);
    units.upsert(foreign);
    auto published = manifest(resource, 1);
    published.records.push_back(DerivedRecordRef{DerivedRecordKind::Custom, {}, "knowledge_unit:2", 1});
    published.records.push_back(DerivedRecordRef{DerivedRecordKind::Custom, {}, "original_projection:2", 1});
    manifests.upsert_manifest(std::move(published));
    manifests.upsert_manifest(manifest(foreign_resource, 3));
    projections.put(make_original_projection(first, first.revision));
    projections.put(make_original_projection(second, second.revision));
    projections.put(make_original_projection(foreign, foreign.revision));

    StandardTokenizer tokenizer;
    ProjectionAwareLexicalRetriever retriever(units, projections, manifests, scope, tokenizer);
    RetrievalQuery query;
    query.text = "alpha";
    query.limit = 5;
    const auto hit = retriever.retrieve(query);
    if(hit.size() != 1 || hit.chunks.front().chunk.text != first.primary_text) return fail("published projection must hydrate canonical text");

    RetrievalQuery foreign_query;
    foreign_query.text = "foreign";
    foreign_query.limit = 5;
    if(!retriever.retrieve(foreign_query).empty()) return fail("retrieval leaked across scopes");

    auto inconsistent = envelope(4, "canonical mismatch", scope, resource);
    units.upsert(inconsistent);
    auto inconsistent_projection = make_original_projection(inconsistent, inconsistent.revision);
    inconsistent_projection.body = "wrong derived body";
    projections.put(std::move(inconsistent_projection));
    auto with_inconsistent = *manifests.find_manifest(resource);
    with_inconsistent.records.push_back(DerivedRecordRef{DerivedRecordKind::Custom, {}, "knowledge_unit:4", 2});
    with_inconsistent.records.push_back(DerivedRecordRef{DerivedRecordKind::Custom, {}, "original_projection:4", 2});
    manifests.upsert_manifest(std::move(with_inconsistent));
    RetrievalQuery mismatch_query;
    mismatch_query.text = "wrong";
    if(!retriever.retrieve(mismatch_query).empty()) return fail("mismatched Original body must be rejected");

    auto stale = first;
    stale.revision = 2;
    stale.primary_text = "changed alpha";
    units.upsert(stale);
    if(!retriever.retrieve(query).empty()) return fail("stale projection must be rejected after unit revision changes");

    auto inactive = manifest(resource, 1);
    inactive.state = ResourceManifestState::ErasePending;
    manifests.upsert_manifest(inactive);
    if(!retriever.retrieve(query).empty()) return fail("erase-pending manifest must not admit hits");

    ProjectionAwareLexicalRetrieverOptions bounded_options;
    bounded_options.max_scanned_units = 1;
    ProjectionAwareLexicalRetriever bounded(
        units, projections, manifests, scope, tokenizer, bounded_options);
    bool budget_threw = false;
    try { (void)bounded.retrieve(query); }
    catch(const ProjectionAwareLexicalRetrievalBudgetExceeded&) { budget_threw = true; }
    if(!budget_threw) return fail("unit scan budget must fail closed explicitly");

    {
        const ResourceId race_resource{"resource-race"};
        Units race_units;
        Projections race_projections;
        Manifests race_manifests;
        const auto race_envelope = envelope(
            20,
            "alpha race evidence",
            scope,
            race_resource
        );
        race_units.upsert(race_envelope);
        race_projections.put(make_original_projection(
            race_envelope,
            race_envelope.revision
        ));
        race_manifests.upsert_manifest(manifest(race_resource, 20));
        race_units.set_find_hook([&](const KnowledgeUnitId& id, std::size_t call_number) {
            if(id.value() != race_envelope.id.value() || call_number != 2) return;
            auto replacement = *race_manifests.find_manifest(race_resource);
            replacement.state = ResourceManifestState::ErasePending;
            race_manifests.upsert_manifest(std::move(replacement));
        });

        ProjectionAwareLexicalRetriever race_retriever(
            race_units,
            race_projections,
            race_manifests,
            scope,
            tokenizer
        );
        if(!race_retriever.retrieve(query).empty()) {
            return fail("manifest replacement before hydration must reject the hit");
        }
    }

    {
        const ResourceId byte_budget_resource{"resource-byte-budget"};
        Units byte_budget_units;
        Projections byte_budget_projections;
        Manifests byte_budget_manifests;
        const auto budget_first = envelope(
            30,
            "alpha first candidate",
            scope,
            byte_budget_resource
        );
        const auto budget_second = envelope(
            31,
            "alpha second candidate",
            scope,
            byte_budget_resource
        );
        byte_budget_units.upsert(budget_first);
        byte_budget_units.upsert(budget_second);
        byte_budget_projections.put(make_original_projection(
            budget_first,
            budget_first.revision
        ));
        byte_budget_projections.put(make_original_projection(
            budget_second,
            budget_second.revision
        ));
        auto byte_budget_manifest = manifest(byte_budget_resource, 30);
        byte_budget_manifest.records.push_back(DerivedRecordRef{
            DerivedRecordKind::Custom,
            {},
            "knowledge_unit:31",
            1
        });
        byte_budget_manifest.records.push_back(DerivedRecordRef{
            DerivedRecordKind::Custom,
            {},
            "original_projection:31",
            1
        });
        byte_budget_manifests.upsert_manifest(std::move(byte_budget_manifest));

        ProjectionAwareLexicalRetrieverOptions byte_budget_options;
        byte_budget_options.max_indexed_projection_bytes =
            budget_first.primary_text.size() + budget_second.primary_text.size() - 1;
        ProjectionAwareLexicalRetriever byte_budget_retriever(
            byte_budget_units,
            byte_budget_projections,
            byte_budget_manifests,
            scope,
            tokenizer,
            byte_budget_options
        );
        bool byte_budget_threw = false;
        bool returned_partial_result = false;
        try {
            const auto partial = byte_budget_retriever.retrieve(query);
            returned_partial_result = !partial.empty();
        } catch(const ProjectionAwareLexicalRetrievalBudgetExceeded&) {
            byte_budget_threw = true;
        }
        if(!byte_budget_threw || returned_partial_result) {
            return fail("projection byte budget must throw without partial top-K");
        }
    }
    return 0;
}
