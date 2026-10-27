#include "MdbxPublicationReadScope.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <stdexcept>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::uint8_t kFrontierFamily = 1U << 0U;
constexpr std::uint8_t kCanonicalFamily = 1U << 1U;
constexpr std::uint8_t kUnitFamily = 1U << 2U;
constexpr std::uint8_t kProjectionFamily = 1U << 3U;
constexpr std::uint8_t kLexicalFamily = 1U << 4U;
constexpr std::uint8_t kManifestFamily = 1U << 5U;
constexpr std::uint8_t kAllReadFamilies = kFrontierFamily | kCanonicalFamily
    | kUnitFamily | kProjectionFamily | kLexicalFamily | kManifestFamily;

std::uint8_t read_family(const MdbxPublicationReadParticipant& participant) noexcept {
    if(dynamic_cast<const MdbxFrontierReadParticipant*>(&participant)) {
        return kFrontierFamily;
    }
    if(dynamic_cast<const MdbxCanonicalReadParticipant*>(&participant)) {
        return kCanonicalFamily;
    }
    if(dynamic_cast<const MdbxKnowledgeUnitReadParticipant*>(&participant)) {
        return kUnitFamily;
    }
    if(dynamic_cast<const MdbxProjectionReadParticipant*>(&participant)) {
        return kProjectionFamily;
    }
    if(dynamic_cast<const MdbxLexicalReadParticipant*>(&participant)) {
        return kLexicalFamily;
    }
    if(dynamic_cast<const MdbxManifestReadParticipant*>(&participant)) {
        return kManifestFamily;
    }
    return 0;
}

bool same_receipt_authority(
    const PublicationReceipt& left,
    const PublicationReceipt& right
) noexcept {
    return left.identity.scope_id == right.identity.scope_id
        && left.identity.publication_id == right.identity.publication_id
        && left.committed_frontier.scope_id == right.committed_frontier.scope_id
        && left.committed_frontier.sequence.value()
            == right.committed_frontier.sequence.value()
        && left.payload.payload_id == right.payload.payload_id
        && left.payload.payload_digest == right.payload.payload_digest
        && left.payload.canonical_coverage == right.payload.canonical_coverage
        && left.status == right.status
        && same_publication_evidence(left.target_evidence, right.target_evidence);
}

void require_store_context(
    const std::shared_ptr<MdbxStorageContext>& participant_context,
    const MdbxStorageContext& store_context,
    const char* family
) {
    if(!participant_context || !participant_context->connection()
        || store_context.connection() != participant_context->connection()) {
        throw std::invalid_argument(
            std::string("publication ") + family
            + " participant is bound to a different storage context"
        );
    }
}

} // namespace

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
    const auto family = read_family(participant);
    if(family == 0) {
        throw std::invalid_argument("unknown publication read participant");
    }
    if((m_read_families & family) != 0) {
        throw std::invalid_argument("duplicate publication read family");
    }
    if(participant.context().connection() != m_context->connection()) {
        throw std::invalid_argument("publication read participant belongs to another context");
    }
    participant.read(*m_transaction, m_observation);
    m_read_families |= family;
}

void MdbxPublicationReadScope::finish() {
    require_active();
    if(m_read_families != kAllReadFamilies) {
        throw std::logic_error("publication target read requires every authoritative family");
    }
    const auto& frontier = m_observation.frontier;
    const auto receipt_matches_frontier =
        m_observation.receipt
        && m_observation.receipt->status == PublicationReceiptStatus::Complete
        && m_observation.receipt->identity.scope_id == frontier.scope_id
        && m_observation.receipt->committed_frontier.scope_id == frontier.scope_id
        && m_observation.receipt->committed_frontier.sequence.value()
            == frontier.sequence.value()
        && frontier.sequence.value() != 0;
    const auto receipt_evidence_available = receipt_matches_frontier;
    const auto& target_evidence = m_observation.receipt
        ? m_observation.receipt->target_evidence
        : PublicationEvidence{};
    const auto projection_version_matches = [](const auto& left, const auto& right) {
        return left.unit_revision_at_build == right.unit_revision_at_build
            && left.projection_revision == right.projection_revision
            && left.derivation_generation == right.derivation_generation
            && left.derivation_fingerprint == right.derivation_fingerprint;
    };
    const auto target_source_matches = [&target_evidence](const auto& unit) {
        return std::any_of(
            unit.sources.begin(),
            unit.sources.end(),
            [&target_evidence](const auto& source) {
                return source.resource_id == target_evidence.resource_id
                    && source.resource_revision
                    && source.resource_revision->generation
                        == target_evidence.resource_generation;
            }
        );
    };
    const bool canonical_matches_target = m_observation.canonical
        && m_observation.canonical->document_id == target_evidence.canonical_document_id
        && m_observation.canonical->revision == target_evidence.canonical_revision;
    const bool unit_matches_target = m_observation.unit
        && m_observation.unit->id.value() == target_evidence.unit_id.value()
        && m_observation.unit->revision == target_evidence.unit_revision
        && target_source_matches(*m_observation.unit);
    const bool manifest_matches_target = m_observation.manifest
        && m_observation.manifest->revision.resource_id == target_evidence.resource_id
        && m_observation.manifest->revision.generation == target_evidence.resource_generation
        && m_observation.manifest->state == target_evidence.manifest_state
        && m_observation.manifest->schema.schema_id == target_evidence.manifest_schema.schema_id
        && m_observation.manifest->schema.schema_version == target_evidence.manifest_schema.schema_version
        && m_observation.manifest->revision.generation != 0
        && static_cast<std::uint32_t>(m_observation.manifest->payload_version)
            == target_evidence.manifest_payload_version
        && compute_manifest_binding_digest(*m_observation.manifest)
            == target_evidence.manifest_binding_digest;
    const bool projection_identity_matches =
        m_observation.unit.has_value()
        && !m_observation.projections.empty()
        && std::all_of(
            m_observation.projections.begin(),
            m_observation.projections.end(),
            [this, &frontier](const SearchProjection& projection) {
                return projection.scope_id == frontier.scope_id
                    && projection.unit_id == m_observation.unit->id;
            }
        );
    const bool unit_resource_matches_manifest =
        m_observation.unit
        && m_observation.manifest
        && m_observation.manifest->revision.resource_id == target_evidence.resource_id
        && target_source_matches(*m_observation.unit);
    const bool lexical_membership_matches =
        m_observation.lexical
        && m_observation.receipt
        && std::any_of(
            m_observation.lexical->frontier.observed_unit_revisions.begin(),
            m_observation.lexical->frontier.observed_unit_revisions.end(),
            [this, &projection_version_matches, &target_evidence](const auto& observation) {
                if(!m_observation.unit) {
                    return false;
                }
                return observation.scope_id == m_observation.frontier.scope_id
                    && observation.unit_id == m_observation.unit->id
                    && observation.unit_revision == m_observation.unit->revision
                    && observation.resource_id == target_evidence.resource_id
                    && m_observation.receipt->target_evidence.manifest_state
                        == ResourceManifestState::Active
                    && observation.resource_generation == target_evidence.resource_generation
                    && projection_version_matches(
                        observation.projection_version,
                        m_observation.receipt->target_evidence.projection_version
                    );
            }
        );
    const bool projection_version_is_target = m_observation.unit
        && std::any_of(
            m_observation.projections.begin(),
            m_observation.projections.end(),
            [&target_evidence, &projection_version_matches](const auto& projection) {
                return projection_version_matches(
                    projection.version, target_evidence.projection_version
                ) && compute_projection_payload_digest(projection)
                    == target_evidence.projection_payload_digest;
            }
        );
    const bool lexical_identity_matches =
        m_observation.lexical
        && m_observation.lexical->frontier.index_descriptor.ref.scope_id
            == frontier.scope_id
        && m_observation.lexical->frontier.consistency
            == LexicalReadConsistency::PinnedSnapshot
        && is_valid_lexical_index_descriptor(
            m_observation.lexical->frontier.index_descriptor
        )
        && is_valid_lexical_active_snapshot(
            m_observation.lexical->frontier.active_snapshot
        )
        && m_observation.lexical->frontier.index_descriptor.ref.statistics_epoch
            == m_observation.lexical->frontier.active_snapshot.statistics_epoch
        && std::find(
            m_observation.lexical->frontier.index_descriptor
                .supported_posting_layout_versions.begin(),
            m_observation.lexical->frontier.index_descriptor
                .supported_posting_layout_versions.end(),
            m_observation.lexical->frontier.active_snapshot.posting_layout_version
        ) != m_observation.lexical->frontier.index_descriptor
                .supported_posting_layout_versions.end();
    const bool has_authoritative_evidence =
        m_observation.coordinator_metadata_valid
        &&
        receipt_evidence_available
        && !frontier.scope_id.empty()
        && m_observation.canonical.has_value()
        && m_observation.unit.has_value()
        && canonical_matches_target
        && unit_matches_target
        && projection_identity_matches
        && projection_version_is_target
        && lexical_identity_matches
        && lexical_membership_matches
        && m_observation.manifest.has_value()
        && unit_resource_matches_manifest
        && manifest_matches_target;
    m_observation.completion = has_authoritative_evidence
        ? MdbxPublicationReadCompletion::TargetExact
        : (!frontier.scope_id.empty() && frontier.sequence.value() != 0
            && m_observation.receipt
            && m_observation.receipt->status == PublicationReceiptStatus::Complete
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
) : m_context(std::move(context)), m_coordinator(&coordinator) {
    require_store_context(m_context, coordinator.context(), "frontier");
}
const MdbxStorageContext& MdbxFrontierReadParticipant::context() const noexcept { return *m_context; }
void MdbxFrontierReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) {
    try {
        observation.frontier = m_coordinator->frontier_in_transaction(transaction);
        observation.coordinator_metadata_valid =
            m_coordinator->metadata_valid_in_transaction(transaction);
        observation.receipt = m_coordinator->latest_receipt_in_transaction(transaction);
        if(!observation.receipt) {
            return;
        }
        const auto publication_receipt = m_coordinator->receipt_in_transaction(
            observation.receipt->identity.publication_id, transaction
        );
        if(!publication_receipt
            || !same_receipt_authority(*observation.receipt, *publication_receipt)) {
            observation.coordinator_metadata_valid = false;
            return;
        }
        if(observation.receipt->status == PublicationReceiptStatus::Complete) {
            const auto binding = m_coordinator->binding_in_transaction(
                observation.receipt->target_evidence.resource_id, transaction
            );
            if(!binding || !same_publication_evidence(
                    *binding, observation.receipt->target_evidence
                )) {
                observation.coordinator_metadata_valid = false;
            }
        }
    } catch(const std::exception&) {
        observation.coordinator_metadata_valid = false;
    }
}

MdbxCanonicalReadParticipant::MdbxCanonicalReadParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    const MdbxCanonicalContentStore& store,
    DocumentId document_id
) : m_context(std::move(context)), m_store(&store), m_document_id(std::move(document_id)) {
    require_store_context(m_context, store.context(), "canonical");
}
const MdbxStorageContext& MdbxCanonicalReadParticipant::context() const noexcept { return *m_context; }
void MdbxCanonicalReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) { observation.canonical = m_store->read_current_in_transaction(m_document_id, transaction); }

MdbxKnowledgeUnitReadParticipant::MdbxKnowledgeUnitReadParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    const MdbxKnowledgeUnitStore& store,
    KnowledgeUnitId unit_id
) : m_context(std::move(context)), m_store(&store), m_unit_id(std::move(unit_id)) {
    require_store_context(m_context, store.context(), "knowledge-unit");
}
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
    m_unit_id(std::move(unit_id)), m_kind(kind) {
    require_store_context(m_context, store.context(), "projection");
}
const MdbxStorageContext& MdbxProjectionReadParticipant::context() const noexcept { return *m_context; }
void MdbxProjectionReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) { observation.projections = m_store->list_in_transaction(m_scope_id, m_unit_id, transaction, m_kind); }

MdbxLexicalReadParticipant::MdbxLexicalReadParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    const MdbxPersistentLexicalIndex& index
) : m_context(std::move(context)), m_index(&index) {
    require_store_context(m_context, index.context(), "lexical");
}
const MdbxStorageContext& MdbxLexicalReadParticipant::context() const noexcept { return *m_context; }
void MdbxLexicalReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) {
    observation.lexical = m_index->read_in_transaction(transaction);
    observation.lexical->frontier.consistency = LexicalReadConsistency::PinnedSnapshot;
    for(const auto& stats : observation.lexical->index.active_unit_stats()) {
        LexicalUnitRevisionObservation revision;
        revision.scope_id = observation.lexical->frontier.index_descriptor.ref.scope_id;
        revision.unit_id = stats.unit_id;
        revision.unit_revision = stats.unit_revision;
        revision.resource_id = stats.resource_id;
        revision.resource_generation = stats.resource_generation;
        revision.projection_version = stats.projection_version;
        observation.lexical->frontier.observed_unit_revisions.push_back(
            std::move(revision)
        );
    }
}

MdbxManifestReadParticipant::MdbxManifestReadParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    const MdbxResourceManifestStorage& store,
    ResourceId resource_id
) : m_context(std::move(context)), m_store(&store), m_resource_id(std::move(resource_id)) {
    require_store_context(m_context, store.context(), "manifest");
}
const MdbxStorageContext& MdbxManifestReadParticipant::context() const noexcept { return *m_context; }
void MdbxManifestReadParticipant::read(
    const mdbxc::Transaction& transaction,
    MdbxPublicationReadObservation& observation
) { observation.manifest = m_store->find_manifest_in_transaction(m_resource_id, transaction); }

} // namespace agent_memory

#endif
