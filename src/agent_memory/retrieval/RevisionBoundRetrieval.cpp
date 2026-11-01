#include "RevisionBoundRetrieval.hpp"

#include <charconv>
#include <string>
#include <utility>

namespace agent_memory {
namespace {

std::optional<std::uint64_t> metadata_uint(
    const Metadata& metadata,
    const char* key
) {
    const auto value = metadata.get(key);
    if(!value || value->empty()) return std::nullopt;
    std::uint64_t parsed = 0;
    const auto* first = value->data();
    const auto* last = first + value->size();
    const auto conversion = std::from_chars(first, last, parsed);
    if(conversion.ec != std::errc{} || conversion.ptr != last) return std::nullopt;
    return parsed;
}

bool same_digest(
    const ResourceBodyDigest& left,
    const ResourceBodyDigest& right
) {
    return left.algorithm == right.algorithm && left.bytes == right.bytes;
}

bool has_current_projection(
    const KnowledgeUnitEnvelope& unit,
    IProjectionStore& projections
) {
    for(const auto& projection : projections.list(
            unit.scope_id, unit.id, ProjectionKind::Original)) {
        if(projection.kind == ProjectionKind::Original &&
           projection.version.unit_revision_at_build == unit.revision &&
           projection.version.projection_revision == unit.revision &&
           projection.version.derivation_generation == 1 &&
           projection.body == unit.primary_text &&
           is_valid_search_projection(projection)) {
            return true;
        }
    }
    return false;
}

RevisionBoundRetrievalResult unavailable(
    const ScopeId& scope,
    float score,
    RevisionBoundHydrationOutcome outcome
) {
    RevisionBoundRetrievalResult result;
    result.scope_id = scope;
    result.score = score;
    result.hydration = outcome;
    return result;
}

} // namespace

RevisionBoundLexicalRetriever::RevisionBoundLexicalRetriever(
    IKnowledgeUnitStore& unit_store,
    IProjectionStore& projection_store,
    IResourceManifestStorage& manifest_store,
    ScopeId scope_id,
    ITokenizer& tokenizer,
    ProjectionAwareLexicalRetrieverOptions options
) : m_legacy(
        unit_store,
        projection_store,
        manifest_store,
        scope_id,
        tokenizer,
        std::move(options)
    ),
    m_unit_store(&unit_store),
    m_projection_store(&projection_store),
    m_manifest_store(&manifest_store),
    m_scope_id(std::move(scope_id)) {}

std::vector<RevisionBoundRetrievalResult> RevisionBoundLexicalRetriever::retrieve(
    const RetrievalQuery& query
) const {
    std::vector<RevisionBoundRetrievalResult> results;
    const auto legacy = m_legacy.retrieve(query);
    results.reserve(legacy.chunks.size());
    for(const auto& hit : legacy.chunks) {
        const auto id = metadata_uint(hit.metadata, "knowledge_unit_id");
        const auto revision = metadata_uint(hit.metadata, "knowledge_unit_revision");
        if(!id || !revision ||
           !hit.metadata.get("projection_kind") ||
           *hit.metadata.get("projection_kind") != "Original") {
            results.push_back(unavailable(
                m_scope_id, hit.score, RevisionBoundHydrationOutcome::Unavailable));
            continue;
        }

        const KnowledgeUnitId unit_id{*id};
        const auto unit = m_unit_store->find(unit_id);
        if(!unit || unit->scope_id != m_scope_id ||
           unit->revision != *revision ||
           unit->lifecycle_state != KnowledgeUnitLifecycleState::Active) {
            auto stale = unavailable(
                m_scope_id, hit.score, RevisionBoundHydrationOutcome::Stale);
            stale.knowledge_unit_id = unit_id;
            results.push_back(std::move(stale));
            continue;
        }
        if(!has_current_projection(*unit, *m_projection_store)) {
            auto stale = unavailable(
                m_scope_id, hit.score, RevisionBoundHydrationOutcome::Stale);
            stale.knowledge_unit_id = unit_id;
            stale.unit_revision = unit->revision;
            stale.projection_kind = ProjectionKind::Original;
            results.push_back(std::move(stale));
            continue;
        }
        if(unit->sources.size() != 1 ||
           !is_valid_source_ref_summary(unit->sources.front()) ||
           unit->sources.front().reference_mode != SourceReferenceMode::RevisionBoundQuote ||
           !unit->sources.front().resource_revision) {
            auto unavailable_result = unavailable(
                m_scope_id, hit.score, RevisionBoundHydrationOutcome::Unavailable);
            unavailable_result.knowledge_unit_id = unit_id;
            unavailable_result.unit_revision = unit->revision;
            unavailable_result.projection_kind = ProjectionKind::Original;
            results.push_back(std::move(unavailable_result));
            continue;
        }

        const auto& source = unit->sources.front();
        const auto& source_revision = *source.resource_revision;
        const auto manifest = m_manifest_store->find_manifest(source.resource_id);
        if(!manifest || !is_active_resource_manifest(*manifest) ||
           manifest->revision.resource_id != source_revision.resource_id ||
           manifest->revision.generation != source_revision.generation ||
           !manifest->revision.body_digest ||
           !same_digest(*manifest->revision.body_digest, source_revision.body_digest)) {
            auto stale = unavailable(
                m_scope_id, hit.score, RevisionBoundHydrationOutcome::Stale);
            stale.knowledge_unit_id = unit_id;
            stale.unit_revision = unit->revision;
            stale.projection_kind = ProjectionKind::Original;
            stale.source = source;
            stale.root_resource_id = source_revision.resource_id;
            stale.root_generation = source_revision.generation;
            stale.root_digest = source_revision.body_digest;
            results.push_back(std::move(stale));
            continue;
        }

        RevisionBoundRetrievalResult result;
        result.knowledge_unit_id = unit_id;
        result.scope_id = unit->scope_id;
        result.projection_kind = ProjectionKind::Original;
        result.unit_revision = unit->revision;
        result.root_resource_id = manifest->revision.resource_id;
        result.root_generation = manifest->revision.generation;
        result.root_digest = manifest->revision.body_digest;
        result.source = source;
        result.score = hit.score;
        result.text = unit->primary_text;
        // M0 stores only a bounded inline citation summary. The provenance is
        // verified, but a full canonical byte hydration is not implied.
        result.hydration = RevisionBoundHydrationOutcome::Partial;
        results.push_back(std::move(result));
    }
    return results;
}

} // namespace agent_memory
