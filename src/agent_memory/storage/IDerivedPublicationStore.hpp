#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_I_DERIVED_PUBLICATION_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_I_DERIVED_PUBLICATION_STORE_HPP_INCLUDED

/// \file IDerivedPublicationStore.hpp
/// \brief Backend-neutral contract for an atomic, freshness-bound derived publication.

#include <agent_memory/domain/SearchProjection.hpp>

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

    /// \brief Exact source identity captured by a derived publication manifest.
    struct DerivedPublicationInput final {
        KnowledgeUnitId unit_id;
        std::uint64_t unit_revision = 0;
        std::uint64_t source_generation = 0;
        std::string source_digest;
    };

    /// \brief Input and output state committed as one derived publication.
    struct DerivedPublicationRequest final {
        ScopeId scope_id;
        /// Globally unique within one backend; scope is still persisted and checked on read.
        std::string publication_id;
        ProjectionKind kind = ProjectionKind::QACombined;
        std::uint64_t output_revision = 0;
        std::string body;
        std::vector<DerivedPublicationInput> inputs;
        std::uint64_t generation = 0;
        std::string dependency_frontier;
    };

    /// \brief Durable state of one publication read.
    enum class DerivedPublicationReadStatus : std::uint8_t {
        Active,
        NotFound,
        Stale,
        ScopeMismatch,
        Unavailable
    };

    /// \brief Committed publication returned after marker and freshness checks.
    struct DerivedPublication final {
        DerivedPublicationRequest request;
    };

    /// \brief Result that keeps stale and unavailable distinct from absence.
    struct DerivedPublicationReadResult final {
        DerivedPublicationReadStatus status = DerivedPublicationReadStatus::NotFound;
        std::optional<DerivedPublication> publication;
    };

    /// \brief Atomic publication and bounded freshness admission boundary.
    class IDerivedPublicationStore {
    public:
        virtual ~IDerivedPublicationStore();

        /// Stores or replays one publication. Body, manifest and marker are one
        /// durable transaction; conflicting identity is rejected.
        virtual void publish(DerivedPublicationRequest request) = 0;

        /// Reads one publication for an access scope and current source frontier.
        /// A mismatched frontier is Stale and a different access scope is denied.
        [[nodiscard]] virtual DerivedPublicationReadResult read(
            const ScopeId& access_scope,
            const std::string& publication_id,
            const std::vector<DerivedPublicationInput>& current_inputs,
            const std::string& current_dependency_frontier
        ) const = 0;
    };

} // namespace agent_memory

#endif
