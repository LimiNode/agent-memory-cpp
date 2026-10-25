#pragma once
#ifndef AGENT_MEMORY_HEADER_LEXICAL_PUBLICATION_PLAN_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_LEXICAL_PUBLICATION_PLAN_HPP_INCLUDED

/// \file PublicationPlan.hpp
/// \brief Dependency-free publication-plan and scope-frontier conformance model.

#include <agent_memory/domain/SearchProjection.hpp>

#include <cstdint>
#include <map>
#include <optional>
#include <string>

namespace agent_memory {

    /// \brief Scope-wide coordinator sequence, distinct from lexical-local sequence values.
    class PublicationSequence final {
    public:
        PublicationSequence() = default;
        explicit constexpr PublicationSequence(std::uint64_t value) noexcept
            : m_value(value) {}

        [[nodiscard]] constexpr std::uint64_t value() const noexcept {
            return m_value;
        }

        [[nodiscard]] constexpr bool empty() const noexcept {
            return m_value == 0;
        }

    private:
        std::uint64_t m_value = 0;
    };

    /// \brief Stable scope-bound address of one proposed publication.
    struct PublicationIdentity final {
        ScopeId scope_id;
        std::string publication_id;
    };

    /// \brief Durable identity of the coordinated writer profile admitted for a scope.
    struct PublicationProfileIdentity final {
        ScopeId scope_id;
        std::string profile_id;
        std::uint32_t profile_version = 0;
    };

    /// \brief Fencing evidence that a writer is still the admitted owner.
    struct PublicationFencingIdentity final {
        std::string owner_id;
        std::uint64_t epoch = 0;
        std::string token_digest;
    };

    /// \brief Identity and coverage of the proposed derived/canonical payload.
    ///
    /// A digest is not sufficient on its own: `canonical_coverage` records what
    /// the digest covers so a partial payload cannot masquerade as a full one.
    struct PublicationPayloadIdentity final {
        std::string payload_id;
        std::string payload_digest;
        std::string canonical_coverage;
    };

    /// \brief Revision and binding evidence expected before one resource changes.
    struct PublicationEvidence final {
        ResourceId resource_id;
        std::uint64_t resource_generation = 0;
        std::uint64_t canonical_revision = 0;
        std::uint64_t unit_revision = 0;
        ProjectionVersionRef projection_version;
        ResourceManifestSchema manifest_schema;
        std::uint64_t manifest_revision = 0;
        ResourceManifestState manifest_state = ResourceManifestState::Active;
        std::uint32_t manifest_payload_version = 0;
        std::string manifest_binding_digest;
    };

    /// \brief One scope-wide frontier observed before or committed by a plan.
    struct PublicationScopeFrontier final {
        ScopeId scope_id;
        PublicationSequence sequence;
    };

    /// \brief Complete deterministic input to one modeled publication.
    struct PublicationPlan final {
        PublicationIdentity identity;
        PublicationScopeFrontier expected_frontier;
        PublicationProfileIdentity profile;
        PublicationFencingIdentity fencing;
        PublicationEvidence expected_evidence;
        PublicationEvidence target_evidence;
        PublicationPayloadIdentity payload;
    };

    /// \brief Receipt returned by a first commit or an idempotent replay.
    struct PublicationReceipt final {
        PublicationIdentity identity;
        PublicationScopeFrontier committed_frontier;
        PublicationPayloadIdentity payload;
        bool replay = false;
    };

    /// \brief Deterministic result of applying a plan to the conformance model.
    enum class PublicationOutcomeCode : std::uint8_t {
        Committed,
        Replayed,
        InvalidPlan,
        NotAdmitted,
        ScopeMismatch,
        ProfileMismatch,
        FencingMismatch,
        StaleFrontier,
        BindingMismatch,
        PublicationIdentityConflict,
        SequenceOverflow,
        CommitFailure,
        InvalidModel
    };

    struct PublicationOutcome final {
        PublicationOutcomeCode code = PublicationOutcomeCode::InvalidPlan;
        std::optional<PublicationReceipt> receipt;
    };

    /// \brief Result of admitting or rotating a model writer identity.
    enum class PublicationAdmissionCode : std::uint8_t {
        Admitted,
        AlreadyAdmitted,
        InvalidProfile,
        InvalidFencing,
        ProfileConflict,
        FencingConflict,
        InvalidModel
    };

    struct PublicationAdmissionResult final {
        PublicationAdmissionCode code = PublicationAdmissionCode::InvalidProfile;
    };

    [[nodiscard]] bool is_valid_publication_identity(
        const PublicationIdentity& identity
    ) noexcept;

    [[nodiscard]] bool is_valid_publication_profile(
        const PublicationProfileIdentity& profile
    ) noexcept;

    [[nodiscard]] bool is_valid_publication_fencing(
        const PublicationFencingIdentity& fencing
    ) noexcept;

    [[nodiscard]] bool is_valid_publication_payload(
        const PublicationPayloadIdentity& payload
    ) noexcept;

    [[nodiscard]] bool is_valid_publication_evidence(
        const PublicationEvidence& evidence
    ) noexcept;

    [[nodiscard]] bool is_valid_publication_plan(
        const PublicationPlan& plan
    ) noexcept;

    /// \brief Deterministic in-memory state machine for PR1 conformance tests.
    ///
    /// This model performs no storage writes and does not claim MDBX durability,
    /// process-crash recovery, or cross-process writer exclusion. A successful
    /// transition prepares a complete replacement state and installs it only
    /// after every admission, CAS, binding, replay, and overflow check passes.
    class PublicationConformanceModel final {
    public:
        PublicationConformanceModel(
            ScopeId scope_id,
            PublicationFencingIdentity initial_fencing,
            PublicationSequence initial_sequence = PublicationSequence{}
        );

        [[nodiscard]] PublicationAdmissionResult admit_profile(
            PublicationProfileIdentity profile
        );

        [[nodiscard]] PublicationAdmissionResult rotate_fencing(
            PublicationFencingIdentity fencing
        );

        /// \brief Arms one deterministic pre-install commit failure for tests.
        void fail_next_commit_for_testing() noexcept;

        [[nodiscard]] PublicationOutcome publish(
            const PublicationPlan& plan
        );

        [[nodiscard]] const PublicationScopeFrontier& frontier() const noexcept;

        [[nodiscard]] bool has_admitted_profile() const noexcept;

        [[nodiscard]] const std::optional<PublicationProfileIdentity>& profile() const noexcept;

        [[nodiscard]] bool valid() const noexcept;

        [[nodiscard]] std::optional<PublicationEvidence> binding(
            const ResourceId& resource_id
        ) const;

        [[nodiscard]] std::optional<PublicationReceipt> receipt(
            const std::string& publication_id
        ) const;

    private:
        struct CommittedPublication final {
            PublicationPlan plan;
            PublicationReceipt receipt;
        };

        [[nodiscard]] bool matches_existing_binding(
            const PublicationEvidence& expected,
            const PublicationEvidence& current
        ) const noexcept;

        [[nodiscard]] bool equivalent_replay(
            const PublicationPlan& plan,
            const CommittedPublication& committed
        ) const noexcept;

        ScopeId m_scope_id;
        PublicationFencingIdentity m_fencing;
        std::optional<PublicationProfileIdentity> m_profile;
        PublicationScopeFrontier m_frontier;
        std::map<ResourceId, PublicationEvidence> m_bindings;
        std::map<std::string, CommittedPublication> m_committed;
        bool m_valid = false;
        bool m_fail_next_commit = false;
    };

} // namespace agent_memory

#endif
