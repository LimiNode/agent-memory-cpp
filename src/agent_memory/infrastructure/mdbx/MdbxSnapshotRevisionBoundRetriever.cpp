#include "MdbxSnapshotRevisionBoundRetriever.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxKnowledgeUnitStore.hpp"
#include "MdbxProjectionStore.hpp"
#include "MdbxResourceManifestStorage.hpp"

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <stdexcept>
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

RevisionBoundRetrievalResult admit_in_transaction(
    const ProjectionAwareLexicalCandidate& candidate,
    const ScopeId& scope,
    MdbxKnowledgeUnitStore& units,
    MdbxProjectionStore& projections,
    MdbxResourceManifestStorage& manifests,
    const mdbxc::Transaction& transaction,
    const ProjectionAwareLexicalRetrieverOptions& options
) {
    if(!is_valid_revision_bound_candidate_identity(candidate)) {
        return unavailable(scope, candidate.score);
    }
    const auto unit = units.find_in_transaction(candidate.knowledge_unit_id,
                                                transaction);
    if(!unit || unit->scope_id != scope ||
       unit->lifecycle_state != KnowledgeUnitLifecycleState::Active) {
        return stale(scope, candidate.score, candidate.knowledge_unit_id,
                     candidate.unit_revision);
    }
    if(unit->revision != candidate.unit_revision) {
        return stale(scope, candidate.score, candidate.knowledge_unit_id,
                     unit->revision);
    }
    const OriginalProjectionListReader read_projections =
        [&projections, &transaction](
            const ScopeId& scope_id,
            const KnowledgeUnitId& unit_id,
            ProjectionKind kind
        ) {
            return projections.list_in_transaction(
                scope_id, unit_id, transaction, kind);
        };
    const OriginalManifestReader read_manifest =
        [&manifests, &transaction](const ResourceId& resource_id) {
            return manifests.find_manifest_in_transaction(resource_id, transaction);
        };
    if(!has_current_original_projection_with_readers(
            *unit, read_projections, read_manifest, options)) {
        return stale(scope, candidate.score, candidate.knowledge_unit_id,
                     unit->revision);
    }
    if(unit->sources.size() != 1 ||
       !is_valid_source_ref_summary(unit->sources.front()) ||
       unit->sources.front().reference_mode != SourceReferenceMode::RevisionBoundQuote ||
       !unit->sources.front().resource_revision) {
        auto result = unavailable(scope, candidate.score);
        result.knowledge_unit_id = unit->id;
        result.unit_revision = unit->revision;
        result.freshness = RevisionBoundFreshness::Current;
        result.projection_kind = ProjectionKind::Original;
        return result;
    }

    const auto& source = unit->sources.front();
    const auto& source_revision = *source.resource_revision;
    const auto manifest = manifests.find_manifest_in_transaction(
        source.resource_id, transaction);
    if(!manifest || !is_active_resource_manifest(*manifest) ||
       manifest->revision.resource_id != source_revision.resource_id ||
       manifest->revision.generation != source_revision.generation ||
       !manifest->revision.body_digest ||
       !same_digest(*manifest->revision.body_digest, source_revision.body_digest)) {
        auto result = stale(scope, candidate.score, unit->id, unit->revision);
        result.source = source;
        result.root_resource_id = source_revision.resource_id;
        result.root_generation = source_revision.generation;
        result.root_digest = source_revision.body_digest;
        return result;
    }

    RevisionBoundRetrievalResult result;
    result.knowledge_unit_id = unit->id;
    result.scope_id = unit->scope_id;
    result.projection_kind = ProjectionKind::Original;
    result.unit_revision = unit->revision;
    result.root_resource_id = manifest->revision.resource_id;
    result.root_generation = manifest->revision.generation;
    result.root_digest = manifest->revision.body_digest;
    result.source = source;
    result.score = candidate.score;
    result.text = unit->primary_text;
    result.freshness = RevisionBoundFreshness::Current;
    result.materialization = RevisionBoundMaterialization::SummaryOnly;
    return result;
}

} // namespace

MdbxSnapshotRevisionBoundRetriever::MdbxSnapshotRevisionBoundRetriever(
    std::shared_ptr<MdbxStorageContext> context,
    ScopeId scope_id,
    ITokenizer& tokenizer,
    ProjectionAwareLexicalRetrieverOptions options,
    std::string table_prefix
) : m_context(std::move(context)),
    m_scope_id(std::move(scope_id)),
    m_tokenizer(&tokenizer),
    m_options(std::move(options)),
    m_table_prefix(std::move(table_prefix)) {
    if(!m_context) throw std::invalid_argument(
        "snapshot revision-bound retriever requires a storage context");
    if(m_scope_id.empty()) throw std::invalid_argument(
        "snapshot revision-bound scope must not be empty");
    if(m_table_prefix.empty()) m_table_prefix = "agent_memory";
}

void MdbxSnapshotRevisionBoundRetriever::set_read_hook_for_testing(
    std::function<void()> hook
) {
    m_read_hook_for_testing = std::move(hook);
}

RevisionBoundRetrievalResult MdbxSnapshotRevisionBoundRetriever::admit_candidate(
    const ProjectionAwareLexicalCandidate& candidate
) const {
    MdbxKnowledgeUnitStoreOptions unit_options;
    unit_options.context = m_context;
    unit_options.table_prefix = m_table_prefix;
    MdbxKnowledgeUnitStore units(std::move(unit_options));
    MdbxProjectionStoreOptions projection_options;
    projection_options.context = m_context;
    projection_options.table_prefix = m_table_prefix;
    MdbxProjectionStore projections(std::move(projection_options));
    MdbxResourceManifestStorageOptions manifest_options;
    manifest_options.context = m_context;
    manifest_options.table_prefix = m_table_prefix;
    MdbxResourceManifestStorage manifests(std::move(manifest_options));
    auto transaction = m_context->connection()->transaction(
        mdbxc::TransactionMode::READ_ONLY);
    const auto result = admit_in_transaction(
        candidate, m_scope_id, units, projections, manifests, transaction,
        m_options);
    transaction.commit();
    return result;
}

std::vector<RevisionBoundRetrievalResult>
MdbxSnapshotRevisionBoundRetriever::retrieve(const RetrievalQuery& query) const {
    const auto response = retrieve_detailed(query);
    if(response.completeness == RevisionBoundCompleteness::UnsupportedQuery) {
        throw RevisionBoundUnsupportedQuery(response.diagnostic);
    }
    if(response.completeness == RevisionBoundCompleteness::BudgetExceeded) {
        throw ProjectionAwareLexicalRetrievalBudgetExceeded(response.diagnostic);
    }
    return response.results;
}

RevisionBoundRetrievalResponse
MdbxSnapshotRevisionBoundRetriever::retrieve_detailed(
    const RetrievalQuery& query
) const {
    RevisionBoundRetrievalResponse response;
    if(!query.metadata_filters.empty()) {
        response.completeness = RevisionBoundCompleteness::UnsupportedQuery;
        response.diagnostic =
            "snapshot revision-bound lexical retrieval does not support metadata filters in M0";
        return response;
    }
    if(query.limit > m_options.max_candidates) {
        response.completeness = RevisionBoundCompleteness::BudgetExceeded;
        response.diagnostic =
            "snapshot revision-bound lexical query limit exceeds max_candidates budget";
        return response;
    }

    MdbxKnowledgeUnitStoreOptions unit_options;
    unit_options.context = m_context;
    unit_options.table_prefix = m_table_prefix;
    MdbxKnowledgeUnitStore units(std::move(unit_options));
    MdbxProjectionStoreOptions projection_options;
    projection_options.context = m_context;
    projection_options.table_prefix = m_table_prefix;
    MdbxProjectionStore projections(std::move(projection_options));
    MdbxResourceManifestStorageOptions manifest_options;
    manifest_options.context = m_context;
    manifest_options.table_prefix = m_table_prefix;
    MdbxResourceManifestStorage manifests(std::move(manifest_options));
    ProjectionAwareLexicalRetriever producer(
        units, projections, manifests, m_scope_id, *m_tokenizer, m_options);

    ProjectionAwareLexicalRetrieval candidates;
    try {
        candidates = producer.retrieve_with_candidates(query);
    } catch(const ProjectionAwareLexicalRetrievalBudgetExceeded& error) {
        response.completeness = RevisionBoundCompleteness::BudgetExceeded;
        response.diagnostic = error.what();
        return response;
    }
    candidates.validate_candidate_pairing();

    auto transaction = m_context->connection()->transaction(
        mdbxc::TransactionMode::READ_ONLY);
    if(m_read_hook_for_testing) m_read_hook_for_testing();
    response.results.reserve(candidates.candidates.size());
    for(const auto& candidate : candidates.candidates) {
        response.results.push_back(admit_in_transaction(
            candidate, m_scope_id, units, projections, manifests, transaction,
            m_options));
    }
    transaction.commit();
    return response;
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
