#pragma once
#ifndef AGENT_MEMORY_HEADER_LEXICAL_PERSISTENT_LEXICAL_INDEX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_LEXICAL_PERSISTENT_LEXICAL_INDEX_HPP_INCLUDED

/// \file PersistentLexicalIndex.hpp
/// \brief Dependency-free identity and read-frontier contracts for persistent lexical indexes.

#include <agent_memory/domain/SearchProjection.hpp>

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace agent_memory {

    /// \brief Durable, non-reused identity of one lexical-index incarnation.
    class LexicalStatisticsEpoch final {
    public:
        LexicalStatisticsEpoch() = default;
        explicit constexpr LexicalStatisticsEpoch(
            std::array<std::uint8_t, 16> bytes
        ) noexcept : m_bytes(bytes) {}

        [[nodiscard]] constexpr const std::array<std::uint8_t, 16>& bytes() const noexcept {
            return m_bytes;
        }

        [[nodiscard]] bool empty() const noexcept;

    private:
        std::array<std::uint8_t, 16> m_bytes{};
    };

    /// \brief Monotonic collection-wide publication frontier.
    class LexicalPublicationSequence final {
    public:
        LexicalPublicationSequence() = default;
        explicit constexpr LexicalPublicationSequence(std::uint64_t value) noexcept
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

    /// \brief Stable address of one immutable lexical index descriptor.
    struct LexicalIndexDescriptorRef final {
        ScopeId scope_id;
        ProjectionKind projection_kind = ProjectionKind::Original;
        LexicalStatisticsEpoch statistics_epoch;
    };

    /// \brief Immutable durable decoding identity for one lexical index epoch.
    ///
    /// Persisted descriptors are append-only. Changing any fingerprint or schema
    /// requires a new statistics epoch; the active snapshot never rewrites a
    /// descriptor in place.
    struct LexicalIndexDescriptor final {
        LexicalIndexDescriptorRef ref;
        std::string projection_schema_id;
        std::uint32_t projection_schema_version = 0;
        std::string tokenizer_fingerprint;
        std::string normalization_fingerprint;
        std::vector<std::uint32_t> supported_posting_layout_versions;
    };

    /// \brief Mutable pointer to one complete aggregate view of an immutable descriptor.
    struct LexicalActiveSnapshot final {
        /// The DBI key supplies scope and projection; the value carries only
        /// the epoch, generation, and layout payload.
        LexicalStatisticsEpoch statistics_epoch;
        std::uint64_t statistics_generation = 0;
        std::uint32_t posting_layout_version = 0;
    };

    /// \brief Runtime decoding capabilities admitted before any posting is scored.
    struct LexicalIndexReaderProfile final {
        std::string projection_schema_id;
        std::uint32_t projection_schema_version = 0;
        std::string tokenizer_fingerprint;
        std::string normalization_fingerprint;
        std::vector<std::uint32_t> supported_posting_layout_versions;
    };

    /// \brief Result of fail-closed descriptor and active-snapshot admission.
    enum class LexicalIndexAdmission : std::uint8_t {
        Compatible,
        InvalidDescriptor,
        InvalidReaderProfile,
        InvalidActiveSnapshot,
        DescriptorIdentityMismatch,
        ProjectionSchemaMismatch,
        TokenizerFingerprintMismatch,
        NormalizationFingerprintMismatch,
        UnsupportedPostingLayout
    };

    /// \brief Consistency guarantee supplied by the storage adapter for one frontier.
    enum class LexicalReadConsistency : std::uint8_t {
        /// Independent authoritative reads; useful for validation but not an exact snapshot.
        NonLinearizable,
        /// All evidence is owned by one pinned storage snapshot or equivalent sequence.
        PinnedSnapshot
    };

    /// \brief Structural observation collected from one candidate frontier.
    ///
    /// This is not an authoritative freshness validator. The storage adapter
    /// must populate the scope, unit revision, manifest binding flags, and
    /// resource revision from the same pinned transaction before an exact
    /// route can use the observation.
    struct LexicalUnitRevisionObservation final {
        ScopeId scope_id;
        KnowledgeUnitId unit_id;
        std::uint64_t unit_revision = 0;
        ResourceId resource_id;
        std::uint64_t resource_generation = 0;
        ProjectionVersionRef projection_version;
        ResourceManifestState manifest_state = ResourceManifestState::Active;
        bool has_active_unit_binding = false;
        bool has_active_projection_binding = false;
    };

    /// \brief Metadata captured by one persistent lexical read session.
    ///
    /// The owning adapter is responsible for keeping its transaction or
    /// equivalent snapshot alive. This dependency-free value records the
    /// resulting identity and candidate evidence; it does not manufacture a
    /// pinned transaction from independent reads.
    struct LexicalReadFrontier final {
        LexicalPublicationSequence publication_sequence;
        LexicalActiveSnapshot active_snapshot;
        LexicalIndexDescriptor index_descriptor;
        LexicalReadConsistency consistency = LexicalReadConsistency::NonLinearizable;
        std::vector<LexicalUnitRevisionObservation> observed_unit_revisions;
    };

    [[nodiscard]] bool operator==(
        const LexicalStatisticsEpoch& lhs,
        const LexicalStatisticsEpoch& rhs
    ) noexcept;

    [[nodiscard]] bool operator!=(
        const LexicalStatisticsEpoch& lhs,
        const LexicalStatisticsEpoch& rhs
    ) noexcept;

    [[nodiscard]] bool operator==(
        const LexicalIndexDescriptorRef& lhs,
        const LexicalIndexDescriptorRef& rhs
    ) noexcept;

    [[nodiscard]] bool operator!=(
        const LexicalIndexDescriptorRef& lhs,
        const LexicalIndexDescriptorRef& rhs
    ) noexcept;

    [[nodiscard]] bool is_valid_lexical_index_descriptor(
        const LexicalIndexDescriptor& descriptor
    ) noexcept;

    [[nodiscard]] bool is_valid_lexical_active_snapshot(
        const LexicalActiveSnapshot& snapshot
    ) noexcept;

    [[nodiscard]] bool is_valid_lexical_index_reader_profile(
        const LexicalIndexReaderProfile& profile
    ) noexcept;

    /// \brief Validates immutable descriptor identity and runtime decoding compatibility.
    [[nodiscard]] LexicalIndexAdmission check_lexical_index_admission(
        const LexicalIndexDescriptor& descriptor,
        const LexicalActiveSnapshot& snapshot,
        const LexicalIndexReaderProfile& reader_profile
    ) noexcept;

    [[nodiscard]] bool is_valid_lexical_unit_revision_observation(
        const LexicalUnitRevisionObservation& observation
    ) noexcept;

    /// \brief Returns whether the frontier is internally coherent.
    /// \note A valid non-linearizable frontier is not sufficient for an Exact route.
    /// \throws std::bad_alloc if duplicate-check bookkeeping cannot be allocated.
    [[nodiscard]] bool is_valid_lexical_read_frontier(
        const LexicalReadFrontier& frontier
    );

    /// \brief Returns structural eligibility for an Exact lexical route.
    ///
    /// This does not prove that a live MDBX read transaction is pinned. The
    /// adapter owns that proof; posting-partition completeness remains a
    /// separate query-time condition.
    [[nodiscard]] bool supports_exact_lexical_read(
        const LexicalReadFrontier& frontier,
        const LexicalIndexReaderProfile& reader_profile
    );

} // namespace agent_memory

#endif
