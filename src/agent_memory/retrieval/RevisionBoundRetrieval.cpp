#include "RevisionBoundRetrieval.hpp"

#include <string>
#include <utility>

namespace agent_memory {
namespace {

bool same_digest(
    const ResourceBodyDigest& left,
    const ResourceBodyDigest& right
) {
    return left.algorithm == right.algorithm && left.bytes == right.bytes;
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
    result.freshness = outcome == RevisionBoundHydrationOutcome::Stale
        ? RevisionBoundFreshness::Stale
        : RevisionBoundFreshness::Unavailable;
    result.materialization = RevisionBoundMaterialization::Unavailable;
    return result;
}

} // namespace

bool is_valid_revision_bound_candidate_identity(
    const ProjectionAwareLexicalCandidate& identity,
    ProjectionKind expected_kind
) noexcept {
    return !identity.knowledge_unit_id.empty() &&
           identity.unit_revision != 0 &&
           is_valid_projection_kind(identity.projection_kind) &&
           identity.projection_kind == expected_kind;
}

bool has_valid_revision_bound_candidate_identity(
    const std::optional<ProjectionAwareLexicalCandidate>& candidate,
    ProjectionKind expected_kind
) noexcept {
    return candidate && is_valid_revision_bound_candidate_identity(
        *candidate, expected_kind);
}

bool matches_revision_bound_candidate_identity(
    const ProjectionAwareLexicalCandidate& identity,
    const KnowledgeUnitEnvelope& envelope,
    ProjectionKind expected_kind
) noexcept {
    return is_valid_revision_bound_candidate_identity(identity, expected_kind) &&
           identity.knowledge_unit_id == envelope.id &&
           identity.unit_revision == envelope.revision;
}

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
        options
    ),
    m_unit_store(&unit_store),
    m_projection_store(&projection_store),
    m_manifest_store(&manifest_store),
    m_scope_id(std::move(scope_id)),
    m_options(std::move(options)) {}

std::vector<RevisionBoundRetrievalResult> RevisionBoundLexicalRetriever::retrieve(
    const RetrievalQuery& query
) const {
    const auto response = retrieve_detailed(query);
    if(response.completeness == RevisionBoundCompleteness::UnsupportedQuery) {
        throw RevisionBoundUnsupportedQuery(response.diagnostic);
    }
    if(response.completeness == RevisionBoundCompleteness::BudgetExceeded) {
        throw ProjectionAwareLexicalRetrievalBudgetExceeded(response.diagnostic);
    }
    return response.results;
}

RevisionBoundRetrievalResponse RevisionBoundLexicalRetriever::retrieve_detailed(
    const RetrievalQuery& query
) const {
    RevisionBoundRetrievalResponse response;
    if(!query.metadata_filters.empty()) {
        response.completeness = RevisionBoundCompleteness::UnsupportedQuery;
        response.diagnostic =
            "revision-bound lexical retrieval does not support metadata filters in M0";
        return response;
    }

    std::vector<RevisionBoundRetrievalResult> results;
    ProjectionAwareLexicalRetrieval legacy;
    try {
        legacy = m_legacy.retrieve_with_candidates(query);
    } catch(const ProjectionAwareLexicalRetrievalBudgetExceeded& error) {
        response.completeness = RevisionBoundCompleteness::BudgetExceeded;
        response.diagnostic = error.what();
        return response;
    }
    results.reserve(legacy.candidates.size());
    for(std::size_t candidate_index = 0;
        candidate_index < legacy.candidates.size(); ++candidate_index) {
        const auto& hit = legacy.legacy.chunks[candidate_index];
        const auto& identity = legacy.candidates[candidate_index];
        if(!has_valid_revision_bound_candidate_identity(identity)) {
            results.push_back(unavailable(
                m_scope_id, hit.score, RevisionBoundHydrationOutcome::Unavailable));
            continue;
        }

        const KnowledgeUnitId unit_id = identity.knowledge_unit_id;
        const auto unit = m_unit_store->find(unit_id);
        if(!unit || unit->scope_id != m_scope_id ||
           unit->lifecycle_state != KnowledgeUnitLifecycleState::Active) {
            auto stale = unavailable(
                m_scope_id, hit.score, RevisionBoundHydrationOutcome::Stale);
            stale.knowledge_unit_id = unit_id;
            results.push_back(std::move(stale));
            continue;
        }
        if(!matches_revision_bound_candidate_identity(identity, *unit)) {
            auto stale = unavailable(
                m_scope_id, hit.score, RevisionBoundHydrationOutcome::Stale);
            stale.knowledge_unit_id = unit_id;
            stale.unit_revision = unit->revision;
            stale.projection_kind = ProjectionKind::Original;
            results.push_back(std::move(stale));
            continue;
        }
        if(!has_current_original_projection(
                *unit, *m_projection_store, *m_manifest_store, m_options)) {
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
            unavailable_result.freshness = RevisionBoundFreshness::Current;
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
        result.freshness = RevisionBoundFreshness::Current;
        result.materialization = RevisionBoundMaterialization::SummaryOnly;
        result.hydration = RevisionBoundHydrationOutcome::Partial;
        results.push_back(std::move(result));
    }
    response.results = std::move(results);
    return response;
}

} // namespace agent_memory
