#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_AUTHORITATIVE_PUBLICATION_PARTICIPANTS_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_AUTHORITATIVE_PUBLICATION_PARTICIPANTS_HPP_INCLUDED

/// \file MdbxAuthoritativePublicationParticipants.hpp
/// \brief Coordinator-owned canonical, unit and manifest participant seams.

#include "MdbxCanonicalContentStore.hpp"
#include "MdbxKnowledgeUnitStore.hpp"
#include "MdbxPublicationWriteScope.hpp"
#include "MdbxResourceManifestStorage.hpp"

#include <memory>
#include <optional>
#include <vector>

#if AGENT_MEMORY_HAS_MDBX
namespace agent_memory {

/// \brief Applies one canonical creation or edit without owning the transaction.
class MdbxCanonicalPublicationParticipant final : public MdbxPublicationParticipant {
public:
    MdbxCanonicalPublicationParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        MdbxCanonicalContentStore& store,
        CanonicalDocumentRevision initial
    );
    MdbxCanonicalPublicationParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        MdbxCanonicalContentStore& store,
        CanonicalEditRequest request
    );

    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void apply(const mdbxc::Transaction& transaction) override;

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    MdbxCanonicalContentStore* m_store;
    std::optional<CanonicalDocumentRevision> m_initial;
    std::optional<CanonicalEditRequest> m_request;
};

/// \brief Applies knowledge-unit envelope writes without owning the transaction.
class MdbxKnowledgeUnitPublicationParticipant final : public MdbxPublicationParticipant {
public:
    MdbxKnowledgeUnitPublicationParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        MdbxKnowledgeUnitStore& store,
        std::vector<KnowledgeUnitCreateRequest> requests
    );

    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void apply(const mdbxc::Transaction& transaction) override;

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    MdbxKnowledgeUnitStore* m_store;
    std::vector<KnowledgeUnitCreateRequest> m_requests;
};

/// \brief Applies one manifest binding update without owning the transaction.
class MdbxManifestPublicationParticipant final : public MdbxPublicationParticipant {
public:
    MdbxManifestPublicationParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        MdbxResourceManifestStorage& store,
        ResourceManifest manifest,
        std::optional<ResourceManifest> expected
    );

    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void apply(const mdbxc::Transaction& transaction) override;

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    MdbxResourceManifestStorage* m_store;
    ResourceManifest m_manifest;
    std::optional<ResourceManifest> m_expected;
};

} // namespace agent_memory
#endif

#endif
