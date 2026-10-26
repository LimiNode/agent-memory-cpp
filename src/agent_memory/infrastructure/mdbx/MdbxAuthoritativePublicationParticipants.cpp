#include "MdbxAuthoritativePublicationParticipants.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <stdexcept>
#include <utility>

namespace agent_memory {

MdbxCanonicalPublicationParticipant::MdbxCanonicalPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxCanonicalContentStore& store,
    CanonicalDocumentRevision initial
) : m_context(std::move(context)),
    m_store(&store),
    m_initial(std::move(initial)) {}

MdbxCanonicalPublicationParticipant::MdbxCanonicalPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxCanonicalContentStore& store,
    CanonicalEditRequest request
) : m_context(std::move(context)),
    m_store(&store),
    m_request(std::move(request)) {}

const MdbxStorageContext& MdbxCanonicalPublicationParticipant::context() const noexcept {
    return *m_context;
}

void MdbxCanonicalPublicationParticipant::apply(const mdbxc::Transaction& transaction) {
    if(m_initial) {
        if(!m_store->create_document_in_transaction(std::move(*m_initial), transaction)) {
            throw std::runtime_error("canonical publication participant rejected creation");
        }
        m_initial.reset();
        return;
    }
    if(m_request) {
        const auto result = m_store->commit_in_transaction(*m_request, transaction);
        if(!result.succeeded()) {
            throw std::runtime_error("canonical publication participant rejected edit");
        }
        m_request.reset();
        return;
    }
    throw std::logic_error("canonical publication participant has no operation");
}

MdbxKnowledgeUnitPublicationParticipant::MdbxKnowledgeUnitPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxKnowledgeUnitStore& store,
    std::vector<KnowledgeUnitCreateRequest> requests
) : m_context(std::move(context)),
    m_store(&store),
    m_requests(std::move(requests)) {}

const MdbxStorageContext& MdbxKnowledgeUnitPublicationParticipant::context() const noexcept {
    return *m_context;
}

void MdbxKnowledgeUnitPublicationParticipant::apply(
    const mdbxc::Transaction& transaction
) {
    (void)m_store->create_or_get_units_in_transaction(std::move(m_requests), transaction);
    m_requests.clear();
}

MdbxManifestPublicationParticipant::MdbxManifestPublicationParticipant(
    std::shared_ptr<MdbxStorageContext> context,
    MdbxResourceManifestStorage& store,
    ResourceManifest manifest,
    std::optional<ResourceManifest> expected
) : m_context(std::move(context)),
    m_store(&store),
    m_manifest(std::move(manifest)),
    m_expected(std::move(expected)) {}

const MdbxStorageContext& MdbxManifestPublicationParticipant::context() const noexcept {
    return *m_context;
}

void MdbxManifestPublicationParticipant::apply(const mdbxc::Transaction& transaction) {
    m_store->upsert_manifest_in_transaction(
        std::move(m_manifest), m_expected, transaction
    );
}

} // namespace agent_memory

#endif
