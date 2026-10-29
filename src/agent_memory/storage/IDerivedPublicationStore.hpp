#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_I_DERIVED_PUBLICATION_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_I_DERIVED_PUBLICATION_STORE_HPP_INCLUDED

/// \file IDerivedPublicationStore.hpp
/// \brief Backend-neutral contract for atomic, storage-only derived publication.

#include <agent_memory/domain/SearchProjection.hpp>

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

    /// \brief Exact source identity captured by a derived publication manifest.
    struct DerivedPublicationInput final {
        /// The current slice is single-scope.  Inputs intentionally carry no
        /// federated scope reference; that contract belongs to a later
        /// integration layer.
        KnowledgeUnitId unit_id;
        std::uint64_t unit_revision = 0;
        std::uint64_t source_generation = 0;
        std::string source_digest;
    };

    /// \brief Input and output state committed as one derived publication.
    struct DerivedPublicationRequest final {
        ScopeId scope_id;
        /// Globally unique within this local backend; scope is still persisted
        /// and checked on read.  This is not the federated identity contract.
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
        /// Stored bytes and caller-supplied inputs/frontier match.  This is a
        /// storage match only; it is never an authoritative freshness claim.
        StoredMatch,
        NotFound,
        /// Persisted metadata differs from caller-supplied values. This does
        /// not by itself prove that the canonical source is stale.
        StoredMismatch,
        ScopeMismatch,
        Unavailable
    };

    /// \brief Committed publication returned after marker and storage checks.
    struct DerivedPublication final {
        DerivedPublicationRequest request;
    };

    /// \brief Result that keeps stored mismatches and unavailable rows distinct from absence.
    struct DerivedPublicationReadResult final {
        DerivedPublicationReadStatus status = DerivedPublicationReadStatus::NotFound;
        std::optional<DerivedPublication> publication;
    };

    /// \brief Atomic persistence boundary for derived publications.
    class IDerivedPublicationStore {
    public:
        virtual ~IDerivedPublicationStore();

        /// Stores or replays one publication. Body, manifest and marker are one
        /// durable transaction; conflicting identity is rejected. This method
        /// does not admit canonical input existence, lifecycle, or authoritative
        /// freshness; its marker means durable storage only.
        virtual void publish(DerivedPublicationRequest request) = 0;

        /// Reads one publication for an access scope and caller-provided source
        /// frontier. A mismatched value is StoredMismatch and a different
        /// access scope is denied. A matching caller value returns StoredMatch, not
        /// authoritative Active: no trusted frontier provider is consulted by
        /// this storage-only slice. Canonical admission is a future
        /// integration/conformance layer.
        [[nodiscard]] virtual DerivedPublicationReadResult read(
            const ScopeId& access_scope,
            const std::string& publication_id,
            const std::vector<DerivedPublicationInput>& current_inputs,
            const std::string& current_dependency_frontier
        ) const = 0;
    };

} // namespace agent_memory

#endif
