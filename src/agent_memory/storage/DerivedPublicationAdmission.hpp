#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_DERIVED_PUBLICATION_ADMISSION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_DERIVED_PUBLICATION_ADMISSION_HPP_INCLUDED

/// \file DerivedPublicationAdmission.hpp
/// \brief Authoritative KnowledgeUnit-backed admission for derived publications.

#include "IDerivedPublicationStore.hpp"
#include "IKnowledgeUnitStore.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

    struct DerivedPublicationFrontierSnapshot final {
        std::vector<DerivedPublicationInput> inputs;
        std::string dependency_frontier;
    };

    enum class DerivedPublicationFrontierStatus : std::uint8_t {
        Available,
        MissingInput,
        ScopeMismatch,
        InactiveInput,
        InvalidInput,
        Unavailable
    };

    struct DerivedPublicationFrontierResult final {
        DerivedPublicationFrontierStatus status = DerivedPublicationFrontierStatus::Unavailable;
        std::optional<DerivedPublicationFrontierSnapshot> snapshot;
    };

    /// \brief Trusted current frontier reader backed by canonical unit state.
    ///
    /// The provider derives revisions and source witnesses from the store; it
    /// never accepts caller-supplied revision metadata as authoritative.
    class IDerivedPublicationFrontierProvider {
    public:
        virtual ~IDerivedPublicationFrontierProvider();

        [[nodiscard]] virtual DerivedPublicationFrontierResult snapshot(
            const ScopeId& scope_id,
            const std::vector<KnowledgeUnitId>& unit_ids
        ) const = 0;
    };

    /// \brief M0 KnowledgeUnit-backed frontier provider.
    class KnowledgeUnitDerivedPublicationFrontierProvider final
        : public IDerivedPublicationFrontierProvider {
    public:
        explicit KnowledgeUnitDerivedPublicationFrontierProvider(
            const IKnowledgeUnitStore& units
        );

        [[nodiscard]] DerivedPublicationFrontierResult snapshot(
            const ScopeId& scope_id,
            const std::vector<KnowledgeUnitId>& unit_ids
        ) const override;

    private:
        const IKnowledgeUnitStore* m_units;
    };

    enum class DerivedPublicationAdmissionStatus : std::uint8_t {
        Published,
        StoredMatch,
        StoredMismatch,
        NotFound,
        ScopeMismatch,
        MissingInput,
        InactiveInput,
        Rejected,
        Unavailable
    };

    struct DerivedPublicationAdmissionResult final {
        DerivedPublicationAdmissionStatus status = DerivedPublicationAdmissionStatus::Unavailable;
        std::optional<DerivedPublication> publication;
    };

    /// \brief Validates derived publication metadata against canonical units.
    ///
    /// This gate owns the caller-to-authority comparison. It does not claim a
    /// cross-store atomic commit: a coordinator/fenced transaction remains a
    /// separate integration capability.
    class DerivedPublicationAdmission final {
    public:
        DerivedPublicationAdmission(
            IDerivedPublicationStore& publications,
            const IDerivedPublicationFrontierProvider& frontier_provider
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
        const IDerivedPublicationFrontierProvider* m_frontier_provider;
    };

} // namespace agent_memory

#endif
