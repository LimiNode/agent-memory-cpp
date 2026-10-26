#include "MdbxPublicationReadScope.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <stdexcept>
#include <utility>

namespace agent_memory {

MdbxPublicationReadScope::MdbxPublicationReadScope(
    std::shared_ptr<MdbxStorageContext> context
) : m_context(std::move(context)),
    m_transaction(std::make_unique<mdbxc::Transaction>(
        m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY)
    )),
    m_owner_thread(std::this_thread::get_id()) {}

MdbxPublicationReadScope MdbxPublicationReadScope::begin(
    std::shared_ptr<MdbxStorageContext> context
) {
    if(!context || !context->connection()) {
        throw std::invalid_argument("publication read scope requires an MDBX context");
    }
    return MdbxPublicationReadScope(std::move(context));
}

MdbxPublicationReadScope::~MdbxPublicationReadScope() noexcept {
    if(m_active && m_transaction && m_owner_thread == std::this_thread::get_id()) {
        try { m_transaction->rollback(); } catch(...) {}
    }
}

void MdbxPublicationReadScope::require_owner_thread() const {
    if(m_owner_thread != std::this_thread::get_id()) {
        throw std::logic_error("publication read scope used from a non-owner thread");
    }
}

void MdbxPublicationReadScope::require_active() const {
    require_owner_thread();
    if(!m_active || !m_transaction) {
        throw std::logic_error("publication read scope is no longer active");
    }
}

void MdbxPublicationReadScope::read(MdbxPublicationReadParticipant& participant) {
    require_active();
    if(participant.context().connection() != m_context->connection()) {
        throw std::invalid_argument("publication read participant belongs to another context");
    }
    participant.read(*m_transaction, m_observation);
}

void MdbxPublicationReadScope::finish() {
    require_active();
    const bool projection_identity_matches =
        m_observation.unit.has_value()
        && !m_observation.projections.empty()
        && std::all_of(
            m_observation.projections.begin(),
            m_observation.projections.end(),
            [this](const SearchProjection& projection) {
                return projection.scope_id == m_observation.frontier.scope_id
                    && projection.unit_id == m_observation.unit->id;
            }
        );
    const bool unit_resource_matches_manifest =
        m_observation.unit
        && m_observation.manifest
        && !m_observation.unit->sources.empty()
        && m_observation.unit->sources.front().resource_id
            == m_observation.manifest->revision.resource_id;
    const bool lexical_identity_matches =
        m_observation.lexical
        && m_observation.lexical->frontier.index_descriptor.ref.scope_id
            == m_observation.frontier.scope_id
        && m_observation.lexical->frontier.consistency
            == LexicalReadConsistency::PinnedSnapshot
        && is_valid_lexical_read_frontier(m_observation.lexical->frontier);
    const bool has_authoritative_evidence =
        !m_observation.frontier.scope_id.empty()
        && m_observation.canonical.has_value()
        && m_observation.unit.has_value()
        && projection_identity_matches
        && lexical_identity_matches
        && m_observation.manifest.has_value()
        && unit_resource_matches_manifest;
    m_observation.completion = has_authoritative_evidence
        ? MdbxPublicationReadCompletion::Exact
        : (!m_observation.frontier.scope_id.empty()
            ? MdbxPublicationReadCompletion::Partial
            : MdbxPublicationReadCompletion::Unavailable);
    m_transaction->commit();
    m_active = false;
}

bool MdbxPublicationReadScope::active() const noexcept { return m_active; }

const MdbxPublicationReadObservation& MdbxPublicationReadScope::observation() const noexcept {
    return m_observation;
}

MdbxFrontierReadParticipant::MdbxFrontierReadParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    const MdbxPublicationCoordinator& coordinator
) : m_context(std::move(context)), m_coordinator(&coordinator) {}
const MdbxStorageContext& MdbxFrontierReadParticipant::context() const noexcept { return *m_context; }
void MdbxFrontierReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) { observation.frontier = m_coordinator->frontier_in_transaction(transaction); }

MdbxCanonicalReadParticipant::MdbxCanonicalReadParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    const MdbxCanonicalContentStore& store,
    DocumentId document_id
) : m_context(std::move(context)), m_store(&store), m_document_id(std::move(document_id)) {}
const MdbxStorageContext& MdbxCanonicalReadParticipant::context() const noexcept { return *m_context; }
void MdbxCanonicalReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) { observation.canonical = m_store->read_current_in_transaction(m_document_id, transaction); }

MdbxKnowledgeUnitReadParticipant::MdbxKnowledgeUnitReadParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    const MdbxKnowledgeUnitStore& store,
    KnowledgeUnitId unit_id
) : m_context(std::move(context)), m_store(&store), m_unit_id(std::move(unit_id)) {}
const MdbxStorageContext& MdbxKnowledgeUnitReadParticipant::context() const noexcept { return *m_context; }
void MdbxKnowledgeUnitReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) { observation.unit = m_store->find_in_transaction(m_unit_id, transaction); }

MdbxProjectionReadParticipant::MdbxProjectionReadParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    const MdbxProjectionStore& store,
    ScopeId scope_id,
    KnowledgeUnitId unit_id,
    ProjectionKind kind
) : m_context(std::move(context)), m_store(&store), m_scope_id(std::move(scope_id)),
    m_unit_id(std::move(unit_id)), m_kind(kind) {}
const MdbxStorageContext& MdbxProjectionReadParticipant::context() const noexcept { return *m_context; }
void MdbxProjectionReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) { observation.projections = m_store->list_in_transaction(m_scope_id, m_unit_id, transaction, m_kind); }

MdbxLexicalReadParticipant::MdbxLexicalReadParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    const MdbxPersistentLexicalIndex& index
) : m_context(std::move(context)), m_index(&index) {}
const MdbxStorageContext& MdbxLexicalReadParticipant::context() const noexcept { return *m_context; }
void MdbxLexicalReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) {
    observation.lexical = m_index->read_in_transaction(transaction);
    observation.lexical->frontier.consistency = LexicalReadConsistency::PinnedSnapshot;
}

MdbxManifestReadParticipant::MdbxManifestReadParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    const MdbxResourceManifestStorage& store,
    ResourceId resource_id
) : m_context(std::move(context)), m_store(&store), m_resource_id(std::move(resource_id)) {}
const MdbxStorageContext& MdbxManifestReadParticipant::context() const noexcept { return *m_context; }
void MdbxManifestReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) { observation.manifest = m_store->find_manifest_in_transaction(m_resource_id, transaction); }

} // namespace agent_memory

#endif
