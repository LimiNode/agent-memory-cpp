#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_DERIVED_PUBLICATION_ADMISSION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_DERIVED_PUBLICATION_ADMISSION_HPP_INCLUDED

/// \file DerivedPublicationAdmission.hpp
/// \brief KnowledgeUnit-local admission for derived publications.

#include "IDerivedPublicationStore.hpp"
#include "IKnowledgeUnitStore.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

    struct DerivedPublicationUnitSnapshot final {
        std::vector<DerivedPublicationInput> inputs;
        std::string dependency_frontier;
    };

    enum class DerivedPublicationUnitSnapshotStatus : std::uint8_t {
        Available,
        MissingInput,
        ScopeMismatch,
        InactiveInput,
        InvalidInput,
        Unavailable
    };

    struct DerivedPublicationUnitSnapshotResult final {
        DerivedPublicationUnitSnapshotStatus status =
            DerivedPublicationUnitSnapshotStatus::Unavailable;
        std::optional<DerivedPublicationUnitSnapshot> snapshot;
    };

    /// \brief Reads current KnowledgeUnit state and its stored source references.
    ///
    /// The provider never accepts caller-supplied unit revision metadata as
    /// authoritative. Sequential `find()` calls are not a consistent read
    /// snapshot, and stored `ResourceRevisionRef` values do not prove that the
    /// referenced Resource revision is still current.
    class IDerivedPublicationUnitSnapshotProvider {
    public:
        virtual ~IDerivedPublicationUnitSnapshotProvider();

        [[nodiscard]] virtual DerivedPublicationUnitSnapshotResult snapshot(
            const ScopeId& scope_id,
            const std::vector<KnowledgeUnitId>& unit_ids
        ) const = 0;
    };

    /// \brief M0 KnowledgeUnit-local snapshot provider.
    class KnowledgeUnitDerivedPublicationSnapshotProvider final
        : public IDerivedPublicationUnitSnapshotProvider {
    public:
        explicit KnowledgeUnitDerivedPublicationSnapshotProvider(
            const IKnowledgeUnitStore& units
        );

        [[nodiscard]] DerivedPublicationUnitSnapshotResult snapshot(
            const ScopeId& scope_id,
            const std::vector<KnowledgeUnitId>& unit_ids
        ) const override;

    private:
        const IKnowledgeUnitStore* m_units;
    };

    enum class DerivedPublicationAdmissionStatus : std::uint8_t {
        PublishedAfterUnitCheck,
        KnowledgeUnitLocalMatch,
        StoredMismatch,
        NotFound,
        ScopeMismatch,
        MissingInput,
        InactiveInput,
        InvalidInput,
        Rejected,
        Unavailable
    };

    struct DerivedPublicationAdmissionResult final {
        DerivedPublicationAdmissionStatus status = DerivedPublicationAdmissionStatus::Unavailable;
        std::optional<DerivedPublication> publication;
    };

    /// \brief Validates publication metadata against current KnowledgeUnits.
    ///
    /// This gate validates the caller-declared ordered input list against
    /// sequential current-unit reads. It neither proves that the list is a
    /// complete causal lineage nor validates current root Resource revisions.
    /// It also does not claim a consistent multi-unit snapshot or a cross-store
    /// atomic commit. Those require a later resource-frontier provider and
    /// coordinator/fenced transaction. Results from this slice must not be used
    /// as proof of production active freshness.
    class DerivedPublicationAdmission final {
    public:
        DerivedPublicationAdmission(
            IDerivedPublicationStore& publications,
            const IDerivedPublicationUnitSnapshotProvider& unit_snapshot_provider
        );

        [[nodiscard]] DerivedPublicationAdmissionResult publish(
            DerivedPublicationRequest request
        );

        [[nodiscard]] DerivedPublicationAdmissionResult read(
            const ScopeId& access_scope,
            const std::string& publication_id,
            const std::vector<DerivedPublicationInput>& current_inputs,
            const std::string& current_dependency_frontier
        ) const;

    private:
        IDerivedPublicationStore* m_publications;
        const IDerivedPublicationUnitSnapshotProvider* m_unit_snapshot_provider;
    };

} // namespace agent_memory

#endif
