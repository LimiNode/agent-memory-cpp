#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PUBLICATION_COORDINATOR_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PUBLICATION_COORDINATOR_HPP_INCLUDED

/// \file MdbxPublicationCoordinator.hpp
/// \brief Durable scope frontier and publication receipt metadata adapter.

#include <agent_memory/lexical/PublicationPlan.hpp>

#include "MdbxStorageContext.hpp"
#include "MdbxPublicationWriteScope.hpp"

#include <memory>
#include <optional>
#include <string>
#include <vector>

#if AGENT_MEMORY_HAS_MDBX
namespace agent_memory {

/// \brief Configuration for the experimental metadata-only coordinator.
struct MdbxPublicationCoordinatorOptions final {
    std::shared_ptr<MdbxStorageContext> context;
    ScopeId scope_id;
    std::string table_name = "agent_memory_publication_coordinator_v1";
};

/// \brief Durable profile, fencing, frontier and receipt metadata coordinator.
///
/// This PR-B slice owns only coordinator metadata. It does not publish
/// canonical, unit, projection, lexical or manifest rows. The profile remains
/// experimental until every writer for a scope participates in the same
/// transaction boundary.
class MdbxPublicationCoordinator final {
public:
    explicit MdbxPublicationCoordinator(MdbxPublicationCoordinatorOptions options);
    ~MdbxPublicationCoordinator();

    MdbxPublicationCoordinator(const MdbxPublicationCoordinator&) = delete;
    MdbxPublicationCoordinator& operator=(const MdbxPublicationCoordinator&) = delete;

    MdbxPublicationCoordinator(MdbxPublicationCoordinator&&) noexcept;
    MdbxPublicationCoordinator& operator=(MdbxPublicationCoordinator&&) noexcept;

    [[nodiscard]] PublicationAdmissionResult admit_profile(
        PublicationProfileIdentity profile,
        PublicationFencingIdentity fencing
    );

    [[nodiscard]] PublicationAdmissionResult rotate_fencing(
        PublicationFencingIdentity fencing
    );

    [[nodiscard]] PublicationOutcome publish(const PublicationPlan& plan);

    /// \brief Publishes metadata and supplied authoritative participants atomically.
    ///
    /// Participants must belong to this context and must not own, commit, abort,
    /// or retain the transaction. An empty vector preserves the experimental
    /// metadata-only PR-B behavior; production profiles must provide the
    /// canonical, unit and manifest participants.
    [[nodiscard]] PublicationOutcome publish(
        const PublicationPlan& plan,
        const std::vector<MdbxPublicationParticipant*>& participants
    );

    [[nodiscard]] PublicationScopeFrontier frontier() const;

    [[nodiscard]] std::optional<PublicationReceipt> receipt(
        const std::string& publication_id
    ) const;

private:
    class Impl;

    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory
#endif

#endif
