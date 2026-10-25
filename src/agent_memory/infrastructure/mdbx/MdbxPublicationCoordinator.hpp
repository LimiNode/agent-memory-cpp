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

    /// \brief Advances only coordinator metadata for an explicitly weaker profile.
    ///
    /// This path never claims a complete publication and is intended for
    /// migration/recovery tooling, not for authoritative data publication.
    [[nodiscard]] PublicationOutcome publish_metadata_only(const PublicationPlan& plan);

    /// \brief Publishes metadata and supplied authoritative participants atomically.
    ///
    /// Participants must belong to this context and must not own, commit, abort,
    /// or retain the transaction. All five authoritative roles are required;
    /// use `publish_metadata_only()` for an explicitly weaker metadata path.
    [[nodiscard]] PublicationOutcome publish(
        const PublicationPlan& plan,
        const std::vector<MdbxPublicationParticipant*>& participants
    );

    [[nodiscard]] PublicationScopeFrontier frontier() const;
    [[nodiscard]] PublicationScopeFrontier frontier_in_transaction(
        const mdbxc::Transaction& transaction
    ) const;

    /// \brief Checks that schema, profile and fencing metadata are complete.
    ///
    /// Read-side admission must fail closed when any coordinator prerequisite
    /// is absent or malformed, even if a stale frontier/receipt remains.
    [[nodiscard]] bool metadata_valid_in_transaction(
        const mdbxc::Transaction& transaction
    ) const noexcept;

    [[nodiscard]] std::optional<PublicationReceipt> receipt_in_transaction(
        const std::string& publication_id,
        const mdbxc::Transaction& transaction
    ) const;

    /// \brief Returns the receipt that advanced the current committed frontier.
    [[nodiscard]] std::optional<PublicationReceipt> latest_receipt_in_transaction(
        const mdbxc::Transaction& transaction
    ) const;

    [[nodiscard]] std::optional<PublicationReceipt> receipt(
        const std::string& publication_id
    ) const;

private:
    class Impl;

    [[nodiscard]] PublicationOutcome publish_impl(
        const PublicationPlan& plan,
        const std::vector<MdbxPublicationParticipant*>& participants,
        bool allow_metadata_only
    );

    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory
#endif

#endif
