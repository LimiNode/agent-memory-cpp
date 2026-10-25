#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PUBLICATION_READ_SCOPE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PUBLICATION_READ_SCOPE_HPP_INCLUDED

/// \file MdbxPublicationReadScope.hpp
/// \brief Owner-bound pinned MDBX read frontier for coordinated evidence.

#include "MdbxCanonicalContentStore.hpp"
#include "MdbxKnowledgeUnitStore.hpp"
#include "MdbxPersistentLexicalIndex.hpp"
#include "MdbxProjectionStore.hpp"
#include "MdbxPublicationCoordinator.hpp"
#include "MdbxPublicationWriteScope.hpp"
#include "MdbxResourceManifestStorage.hpp"

#include <memory>
#include <optional>
#include <thread>

#if AGENT_MEMORY_HAS_MDBX
namespace agent_memory {

enum class MdbxPublicationReadCompletion : std::uint8_t {
    Exact,
    Partial,
    Unavailable
};

struct MdbxPublicationReadObservation final {
    PublicationScopeFrontier frontier;
    std::optional<CanonicalDocumentRevision> canonical;
    std::optional<KnowledgeUnitEnvelope> unit;
    std::vector<SearchProjection> projections;
    std::optional<MdbxPersistentLexicalRead> lexical;
    std::optional<ResourceManifest> manifest;
    MdbxPublicationReadCompletion completion = MdbxPublicationReadCompletion::Unavailable;
};

class MdbxPublicationReadParticipant {
public:
    virtual ~MdbxPublicationReadParticipant() = default;
    [[nodiscard]] virtual const MdbxStorageContext& context() const noexcept = 0;
    virtual void read(
        const mdbxc::Transaction& transaction,
        MdbxPublicationReadObservation& observation
    ) = 0;
};

class MdbxPublicationReadScope final {
public:
    static MdbxPublicationReadScope begin(
        std::shared_ptr<MdbxStorageContext> context
    );

    MdbxPublicationReadScope(const MdbxPublicationReadScope&) = delete;
    MdbxPublicationReadScope& operator=(const MdbxPublicationReadScope&) = delete;
    MdbxPublicationReadScope(MdbxPublicationReadScope&&) = delete;
    MdbxPublicationReadScope& operator=(MdbxPublicationReadScope&&) = delete;
    ~MdbxPublicationReadScope() noexcept;

    void read(MdbxPublicationReadParticipant& participant);
    void finish();
    [[nodiscard]] bool active() const noexcept;
    [[nodiscard]] const MdbxPublicationReadObservation& observation() const noexcept;

private:
    explicit MdbxPublicationReadScope(std::shared_ptr<MdbxStorageContext> context);
    void require_owner_thread() const;
    void require_active() const;

    std::shared_ptr<MdbxStorageContext> m_context;
    std::unique_ptr<mdbxc::Transaction> m_transaction;
    std::thread::id m_owner_thread;
    MdbxPublicationReadObservation m_observation;
    bool m_active = true;
};

class MdbxFrontierReadParticipant final : public MdbxPublicationReadParticipant {
public:
    MdbxFrontierReadParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        const MdbxPublicationCoordinator& coordinator
    );
    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void read(const mdbxc::Transaction&, MdbxPublicationReadObservation&) override;
private:
    std::shared_ptr<MdbxStorageContext> m_context;
    const MdbxPublicationCoordinator* m_coordinator;
};

class MdbxCanonicalReadParticipant final : public MdbxPublicationReadParticipant {
public:
    MdbxCanonicalReadParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        const MdbxCanonicalContentStore& store,
        DocumentId document_id
    );
    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void read(const mdbxc::Transaction&, MdbxPublicationReadObservation&) override;
private:
    std::shared_ptr<MdbxStorageContext> m_context;
    const MdbxCanonicalContentStore* m_store;
    DocumentId m_document_id;
};

class MdbxKnowledgeUnitReadParticipant final : public MdbxPublicationReadParticipant {
public:
    MdbxKnowledgeUnitReadParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        const MdbxKnowledgeUnitStore& store,
        KnowledgeUnitId unit_id
    );
    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void read(const mdbxc::Transaction&, MdbxPublicationReadObservation&) override;
private:
    std::shared_ptr<MdbxStorageContext> m_context;
    const MdbxKnowledgeUnitStore* m_store;
    KnowledgeUnitId m_unit_id;
};

class MdbxProjectionReadParticipant final : public MdbxPublicationReadParticipant {
public:
    MdbxProjectionReadParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        const MdbxProjectionStore& store,
        ScopeId scope_id,
        KnowledgeUnitId unit_id,
        ProjectionKind kind
    );
    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void read(const mdbxc::Transaction&, MdbxPublicationReadObservation&) override;
private:
    std::shared_ptr<MdbxStorageContext> m_context;
    const MdbxProjectionStore* m_store;
    ScopeId m_scope_id;
    KnowledgeUnitId m_unit_id;
    ProjectionKind m_kind;
};

class MdbxLexicalReadParticipant final : public MdbxPublicationReadParticipant {
public:
    MdbxLexicalReadParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        const MdbxPersistentLexicalIndex& index
    );
    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void read(const mdbxc::Transaction&, MdbxPublicationReadObservation&) override;
private:
    std::shared_ptr<MdbxStorageContext> m_context;
    const MdbxPersistentLexicalIndex* m_index;
};

class MdbxManifestReadParticipant final : public MdbxPublicationReadParticipant {
public:
    MdbxManifestReadParticipant(
        std::shared_ptr<MdbxStorageContext> context,
        const MdbxResourceManifestStorage& store,
        ResourceId resource_id
    );
    [[nodiscard]] const MdbxStorageContext& context() const noexcept override;
    void read(const mdbxc::Transaction&, MdbxPublicationReadObservation&) override;
private:
    std::shared_ptr<MdbxStorageContext> m_context;
    const MdbxResourceManifestStorage* m_store;
    ResourceId m_resource_id;
};

} // namespace agent_memory
#endif

#endif
