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
    float score
) {
    RevisionBoundRetrievalResult result;
    result.scope_id = scope;
    result.score = score;
    result.freshness = RevisionBoundFreshness::Unavailable;
    result.materialization = RevisionBoundMaterialization::Unavailable;
    return result;
}

RevisionBoundRetrievalResult stale(
    const ScopeId& scope,
    float score,
    const KnowledgeUnitId& unit_id,
    std::uint64_t unit_revision
) {
    auto result = unavailable(scope, score);
    result.knowledge_unit_id = unit_id;
    result.unit_revision = unit_revision;
    result.projection_kind = ProjectionKind::Original;
    result.freshness = RevisionBoundFreshness::Stale;
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
    if(!query.metadata_filters.empty()) {
        RevisionBoundRetrievalResponse response;
        response.completeness = RevisionBoundCompleteness::UnsupportedQuery;
        response.diagnostic =
            "revision-bound lexical retrieval does not support metadata filters in M0";
        return response;
    }
    if(query.limit > m_options.max_candidates) {
        RevisionBoundRetrievalResponse response;
        response.completeness = RevisionBoundCompleteness::BudgetExceeded;
        response.diagnostic =
            "revision-bound lexical query limit exceeds max_candidates budget";
        return response;
    }

    ProjectionAwareLexicalRetrieval legacy;
    try {
        legacy = m_legacy.retrieve_with_candidates(query);
    } catch(const ProjectionAwareLexicalRetrievalBudgetExceeded& error) {
        RevisionBoundRetrievalResponse response;
        response.completeness = RevisionBoundCompleteness::BudgetExceeded;
        response.diagnostic = error.what();
        return response;
    }
    legacy.validate_candidate_pairing();
    std::vector<float> scores;
    scores.reserve(legacy.legacy.chunks.size());
    for(const auto& chunk : legacy.legacy.chunks) scores.push_back(chunk.score);
    return admit_candidates(legacy.candidates, scores);
}

RevisionBoundRetrievalResponse RevisionBoundLexicalRetriever::admit_candidates(
    const std::vector<ProjectionAwareLexicalCandidate>& candidates,
    const std::vector<float>& scores
) const {
    if(candidates.size() != scores.size()) {
        throw std::logic_error("revision-bound candidate channels are unpaired");
    }
    RevisionBoundRetrievalResponse response;
    response.candidate_count = candidates.size();
    std::vector<RevisionBoundRetrievalResult> results;
    results.reserve(candidates.size());
    for(std::size_t candidate_index = 0;
        candidate_index < candidates.size(); ++candidate_index) {
        const auto score = scores[candidate_index];
        const auto& identity = candidates[candidate_index];
        if(!is_valid_revision_bound_candidate_identity(identity)) {
            results.push_back(unavailable(m_scope_id, score));
            continue;
        }

        const KnowledgeUnitId unit_id = identity.knowledge_unit_id;
        const auto unit = m_unit_store->find(unit_id);
        if(!unit || unit->scope_id != m_scope_id ||
           unit->lifecycle_state != KnowledgeUnitLifecycleState::Active) {
            results.push_back(stale(
                m_scope_id, score, unit_id, identity.unit_revision));
            continue;
        }
        if(!matches_revision_bound_candidate_identity(identity, *unit)) {
            results.push_back(stale(
                m_scope_id, score, unit_id, unit->revision));
            continue;
        }
        if(!has_current_original_projection(
                *unit, *m_projection_store, *m_manifest_store, m_options)) {
            results.push_back(stale(
                m_scope_id, score, unit_id, unit->revision));
            continue;
        }
        if(unit->sources.size() != 1 ||
           !is_valid_source_ref_summary(unit->sources.front()) ||
           unit->sources.front().reference_mode != SourceReferenceMode::RevisionBoundQuote ||
           !unit->sources.front().resource_revision) {
            auto unavailable_result = unavailable(m_scope_id, score);
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
            auto stale_result = stale(
                m_scope_id, score, unit_id, unit->revision);
            stale_result.source = source;
            stale_result.root_resource_id = source_revision.resource_id;
            stale_result.root_generation = source_revision.generation;
            stale_result.root_digest = source_revision.body_digest;
            results.push_back(std::move(stale_result));
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
        result.score = score;
        result.text = unit->primary_text;
        // M0 stores only a bounded inline citation summary. The provenance is
        // verified, but a full canonical byte hydration is not implied.
        result.freshness = RevisionBoundFreshness::Current;
        result.materialization = RevisionBoundMaterialization::SummaryOnly;
        results.push_back(std::move(result));
    }
    for(const auto& result : results) {
        switch(result.freshness) {
        case RevisionBoundFreshness::Current:
            ++response.admitted_count;
            break;
        case RevisionBoundFreshness::Stale:
            ++response.stale_count;
            break;
        case RevisionBoundFreshness::Unavailable:
            ++response.unavailable_count;
            break;
        }
    }
    if(response.stale_count != 0 || response.unavailable_count != 0) {
        response.diagnostic =
            "complete candidate-window admission; stale/unavailable candidates "
            "are not refilled, so this is not an exhaustive current top-K result";
    }
    response.results = std::move(results);
    return response;
}

} // namespace agent_memory
