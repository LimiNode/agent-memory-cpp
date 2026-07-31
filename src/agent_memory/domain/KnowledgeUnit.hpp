#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_KNOWLEDGE_UNIT_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_KNOWLEDGE_UNIT_HPP_INCLUDED

/// \file KnowledgeUnit.hpp
/// \brief Canonical M0 knowledge-unit identity, lifecycle, and envelope values.

#include "Document.hpp"
#include "Resource.hpp"

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace agent_memory {

    /// \brief Opaque local identifier of a durable knowledge-unit occurrence.
    /// \note Storage allocates non-zero values monotonically and never reuses them.
    class KnowledgeUnitId final {
    public:
        KnowledgeUnitId() = default;
        explicit constexpr KnowledgeUnitId(std::uint64_t value) noexcept
            : m_value(value) {}

        /// \brief Returns the stored numeric value.
        [[nodiscard]] constexpr std::uint64_t value() const noexcept {
            return m_value;
        }

        /// \brief Returns whether no storage identifier has been assigned.
        [[nodiscard]] constexpr bool empty() const noexcept {
            return m_value == 0;
        }

    private:
        std::uint64_t m_value = 0;
    };

    /// \brief Namespace boundary for tenant- and application-owned knowledge.
    class ScopeId final {
    public:
        ScopeId() = default;
        explicit ScopeId(std::string value);

        /// \brief Returns the explicit global scope identifier.
        [[nodiscard]] static ScopeId global();

        /// \brief Returns the scope text used in storage keys.
        [[nodiscard]] const std::string& value() const noexcept;

        /// \brief Returns whether the scope is unspecified.
        [[nodiscard]] bool empty() const noexcept;

    private:
        std::string m_value;
    };

    /// \brief Stable, append-only discriminator of a knowledge-unit payload.
    enum class KnowledgeUnitKind : std::uint16_t {
        Chunk = 0,
        QAPair = 1,
        Fact = 2,
        Event = 3,
        Entity = 4,
        Relation = 5,
        Summary = 6,
        CompiledArticle = 7,
        ConversationEpisode = 8,
        Note = 9,
        Task = 10,
        Decision = 11,
        Custom = 12,
        Playbook = 13,
        DomainMap = 14,
        CapabilityMap = 15,
        Procedure = 16
    };

    /// \brief Durable lifecycle state; transient ranking suppression is separate.
    enum class KnowledgeUnitLifecycleState : std::uint8_t {
        Active,
        Superseded,
        Deprecated,
        Erased
    };

    /// \brief Truncated canonical content hash used for content-key lookup.
    /// \note The versioned hash-computation pipeline is introduced separately.
    struct ContentHash final {
        std::array<std::uint8_t, 16> bytes{};
    };

    /// \brief Immutable content-addressing key for a knowledge unit.
    struct KnowledgeUnitKey final {
        KnowledgeUnitKind kind = KnowledgeUnitKind::Chunk;
        ScopeId scope_id;
        ContentHash content_hash;
    };

    /// \brief Revision of a resource body that anchors a source excerpt.
    struct ResourceRevisionRef final {
        ResourceId resource_id;
        std::uint64_t generation = 0;
        ResourceBodyDigest body_digest;
    };

    /// \brief Declares whether a citation preview is original or derived text.
    enum class SourceTextOrigin : std::uint8_t {
        OriginalText,
        DerivedExtraction
    };

    /// \brief Inline, bounded citation summary retained with an M0 envelope.
    /// \note Durable cross-environment anchor binding belongs to the optional
    ///       global-identity profile and is intentionally not carried here.
    struct SourceRefSummary final {
        ResourceId resource_id;
        std::optional<ResourceRevisionRef> resource_revision;
        std::string uri;
        TextRange excerpt;
        std::array<std::uint8_t, 16> quote_hash{};
        double confidence = 0.0;
        SourceTextOrigin text_origin = SourceTextOrigin::OriginalText;
        std::optional<std::uint64_t> observed_at_ms;
        std::string preview;
    };

    /// \brief Lookup-critical canonical record for a durable knowledge unit.
    struct KnowledgeUnitEnvelope final {
        KnowledgeUnitId id;
        KnowledgeUnitKind kind = KnowledgeUnitKind::Chunk;
        ScopeId scope_id;
        std::string primary_text;
        std::string display_text;
        KnowledgeUnitLifecycleState lifecycle_state = KnowledgeUnitLifecycleState::Active;
        std::vector<SourceRefSummary> sources;
        std::int64_t created_at_ms = 0;
        std::int64_t updated_at_ms = 0;
        std::int64_t observed_at_ms = 0;
        std::uint64_t revision = 0;
        ContentHash content_hash;
        std::uint16_t content_hash_recipe_version = 0;
        double priority_weight = 0.0;
        std::vector<KnowledgeUnitId> supersedes;
        std::optional<KnowledgeUnitId> superseded_by;
        std::optional<KnowledgeUnitId> derived_from;
    };

    /// \brief Returns the stable lowercase name of a knowledge-unit kind.
    [[nodiscard]] std::string_view to_string(KnowledgeUnitKind kind) noexcept;

    /// \brief Parses a lowercase or mixed-case knowledge-unit kind name.
    /// \return True when parsing succeeds.
    bool parse_knowledge_unit_kind(std::string_view text, KnowledgeUnitKind& kind);

    /// \brief Returns whether a knowledge-unit kind is a defined stable wire value.
    [[nodiscard]] bool is_valid_knowledge_unit_kind(KnowledgeUnitKind kind) noexcept;

    /// \brief Returns whether a lifecycle state is a defined durable value.
    [[nodiscard]] bool is_valid_knowledge_unit_lifecycle_state(
        KnowledgeUnitLifecycleState state
    ) noexcept;

    /// \brief Returns whether a durable lifecycle transition is permitted.
    [[nodiscard]] bool is_valid_knowledge_unit_lifecycle_transition(
        KnowledgeUnitLifecycleState from,
        KnowledgeUnitLifecycleState to
    ) noexcept;

    /// \brief Validates the compact M0 provenance summary shape.
    /// \note This structural check does not recompute the quote hash. The
    ///       canonical SHA-256 hash pipeline is a separate storage-layer step.
    [[nodiscard]] bool is_valid_source_ref_summary(
        const SourceRefSummary& summary
    ) noexcept;

    /// \brief Validates the dependency-free M0 knowledge-unit envelope invariants.
    [[nodiscard]] bool is_valid_knowledge_unit_envelope(
        const KnowledgeUnitEnvelope& envelope
    ) noexcept;

    [[nodiscard]] constexpr bool operator==(
        const KnowledgeUnitId& lhs,
        const KnowledgeUnitId& rhs
    ) noexcept {
        return lhs.value() == rhs.value();
    }

    [[nodiscard]] constexpr bool operator!=(
        const KnowledgeUnitId& lhs,
        const KnowledgeUnitId& rhs
    ) noexcept {
        return !(lhs == rhs);
    }

    [[nodiscard]] constexpr bool operator<(
        const KnowledgeUnitId& lhs,
        const KnowledgeUnitId& rhs
    ) noexcept {
        return lhs.value() < rhs.value();
    }

    [[nodiscard]] bool operator==(const ScopeId& lhs, const ScopeId& rhs) noexcept;
    [[nodiscard]] bool operator!=(const ScopeId& lhs, const ScopeId& rhs) noexcept;
    [[nodiscard]] bool operator<(const ScopeId& lhs, const ScopeId& rhs) noexcept;

    [[nodiscard]] bool operator==(const ContentHash& lhs, const ContentHash& rhs) noexcept;
    [[nodiscard]] bool operator!=(const ContentHash& lhs, const ContentHash& rhs) noexcept;
    [[nodiscard]] bool operator<(const ContentHash& lhs, const ContentHash& rhs) noexcept;

    [[nodiscard]] bool operator==(
        const KnowledgeUnitKey& lhs,
        const KnowledgeUnitKey& rhs
    ) noexcept;

    [[nodiscard]] bool operator!=(
        const KnowledgeUnitKey& lhs,
        const KnowledgeUnitKey& rhs
    ) noexcept;

    [[nodiscard]] bool operator<(
        const KnowledgeUnitKey& lhs,
        const KnowledgeUnitKey& rhs
    ) noexcept;

} // namespace agent_memory

#endif
