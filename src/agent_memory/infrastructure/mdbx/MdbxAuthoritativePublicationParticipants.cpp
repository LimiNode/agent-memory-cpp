#include "MdbxAuthoritativePublicationParticipants.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <stdexcept>
#include <algorithm>
#include <string>
#include <utility>

namespace agent_memory {

namespace {

void require_participant_context(
    const std::shared_ptr<MdbxStorageContext>& supplied,
    const MdbxStorageContext& store_context,
    const char* role
) {
    if(!supplied || !supplied->connection()
        || !store_context.connection()
        || supplied->connection() != store_context.connection()) {
        throw std::invalid_argument(
            std::string("publication ") + role
            + " participant context does not own the underlying store connection"
        );
    }
}

} // namespace

MdbxCanonicalPublicationParticipant::MdbxCanonicalPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxCanonicalContentStore& store,
    CanonicalDocumentRevision initial
) : m_context(std::move(context)),
    m_store(&store),
    m_document_id(initial.document_id),
    m_initial(std::move(initial)) {
    require_participant_context(m_context, store.context(), "canonical");
}

MdbxCanonicalPublicationParticipant::MdbxCanonicalPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxCanonicalContentStore& store,
    CanonicalEditRequest request
) : m_context(std::move(context)),
    m_store(&store),
    m_document_id(request.document_id),
    m_request(std::move(request)) {
    require_participant_context(m_context, store.context(), "canonical");
}

const MdbxStorageContext& MdbxCanonicalPublicationParticipant::context() const noexcept {
    return *m_context;
}

void MdbxCanonicalPublicationParticipant::apply(const mdbxc::Transaction& transaction) {
    if(m_applied) {
        throw std::logic_error("canonical publication participant was already applied");
    }
    if(m_initial) {
        if(!m_store->create_document_in_transaction(*m_initial, transaction)) {
            throw std::runtime_error("canonical publication participant rejected creation");
        }
        m_applied = true;
        return;
    }
    if(m_request) {
        const auto result = m_store->commit_in_transaction(*m_request, transaction);
        if(!result.succeeded()) {
            throw std::runtime_error("canonical publication participant rejected edit");
        }
        m_applied = true;
        return;
    }
    throw std::logic_error("canonical publication participant has no operation");
}

void MdbxCanonicalPublicationParticipant::reset_after_abort() noexcept {
    m_applied = false;
}

bool MdbxCanonicalPublicationParticipant::validates(
    const PublicationPlan& plan,
    const mdbxc::Transaction& transaction
) const {
    if(!m_applied) {
        return false;
    }
    const auto current = m_store->read_current_in_transaction(m_document_id, transaction);
    return current
        && current->document_id == plan.target_evidence.canonical_document_id
        && current->revision == plan.target_evidence.canonical_revision;
}

MdbxKnowledgeUnitPublicationParticipant::MdbxKnowledgeUnitPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxKnowledgeUnitStore& store,
    std::vector<KnowledgeUnitCreateRequest> requests
) : m_context(std::move(context)),
    m_store(&store),
    m_requests(std::move(requests)) {
    require_participant_context(m_context, store.context(), "knowledge-unit");
}

const MdbxStorageContext& MdbxKnowledgeUnitPublicationParticipant::context() const noexcept {
    return *m_context;
}

void MdbxKnowledgeUnitPublicationParticipant::apply(
    const mdbxc::Transaction& transaction
) {
    if(m_applied) {
        throw std::logic_error("knowledge-unit publication participant was already applied");
    }
    m_result = m_store->create_or_get_units_in_transaction(m_requests, transaction);
    m_applied = true;
}

void MdbxKnowledgeUnitPublicationParticipant::reset_after_abort() noexcept {
    m_applied = false;
    m_result.clear();
}

bool MdbxKnowledgeUnitPublicationParticipant::validates(
    const PublicationPlan& plan,
    const mdbxc::Transaction& transaction
) const {
    if(!m_applied) {
        return false;
    }
    const auto persisted = m_store->find_in_transaction(
        plan.target_evidence.unit_id, transaction
    );
    if(!persisted) {
        return false;
    }
    return persisted->id.value() == plan.target_evidence.unit_id.value()
        && persisted->scope_id == plan.identity.scope_id
        && persisted->revision == plan.target_evidence.unit_revision
        && std::any_of(persisted->sources.begin(), persisted->sources.end(), [&plan](const auto& source) {
            return source.resource_id == plan.target_evidence.resource_id
                && source.resource_revision
                && source.resource_revision->generation
                    == plan.target_evidence.resource_generation;
        });
}

MdbxManifestPublicationParticipant::MdbxManifestPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxResourceManifestStorage& store,
    ResourceManifest manifest,
    std::optional<ResourceManifest> expected
) : m_context(std::move(context)),
    m_store(&store),
    m_manifest(std::move(manifest)),
    m_expected(std::move(expected)) {
    require_participant_context(m_context, store.context(), "manifest");
}

const MdbxStorageContext& MdbxManifestPublicationParticipant::context() const noexcept {
    return *m_context;
}

void MdbxManifestPublicationParticipant::apply(const mdbxc::Transaction& transaction) {
    if(m_applied) {
        throw std::logic_error("manifest publication participant was already applied");
    }
    m_store->upsert_manifest_in_transaction(m_manifest, m_expected, transaction);
    m_applied = true;
}

void MdbxManifestPublicationParticipant::reset_after_abort() noexcept {
    m_applied = false;
}

bool MdbxManifestPublicationParticipant::validates(
    const PublicationPlan& plan,
    const mdbxc::Transaction& transaction
) const {
    if(!m_applied) {
        return false;
    }
    const auto current = m_store->find_manifest_in_transaction(
        plan.target_evidence.resource_id, transaction
    );
    return current
        && current->revision.resource_id == plan.target_evidence.resource_id
        && current->revision.generation == plan.target_evidence.resource_generation
        && current->state == plan.target_evidence.manifest_state
        && current->schema.schema_id == plan.target_evidence.manifest_schema.schema_id
        && current->schema.schema_version == plan.target_evidence.manifest_schema.schema_version
        && current->payload_version == static_cast<ResourceManifestPayloadVersion>(
            plan.target_evidence.manifest_payload_version
        )
        && compute_manifest_binding_digest(*current)
            == plan.target_evidence.manifest_binding_digest;
}

} // namespace agent_memory

#endif
