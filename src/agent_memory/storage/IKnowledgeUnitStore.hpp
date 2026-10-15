#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_I_KNOWLEDGE_UNIT_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_I_KNOWLEDGE_UNIT_STORE_HPP_INCLUDED

/// \file IKnowledgeUnitStore.hpp
/// \brief M0 persistence contract for knowledge-unit envelopes.

#include <agent_memory/domain/KnowledgeUnit.hpp>

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

    /// \brief Input for creating or deduplicating one knowledge unit.
    struct KnowledgeUnitCreateRequest final {
        KnowledgeUnitKind kind = KnowledgeUnitKind::Chunk;
        ScopeId scope_id;
        std::string primary_text;
        std::string display_text;
        ContentHash content_hash;
        std::uint16_t content_hash_recipe_version = 1;
        std::vector<SourceRefSummary> sources;
        std::int64_t created_at_ms = 0;
        std::int64_t observed_at_ms = 0;
        double priority_weight = 0.0;
    };

    /// \brief Mutable fields accepted by the narrow M0 store.
    /// \note Identity fields (kind, scope, primary text and content hash) are
    ///       intentionally absent and cannot be changed in place.
    struct KnowledgeUnitMutablePatch final {
        std::optional<std::string> display_text;
        std::optional<std::vector<SourceRefSummary>> sources;
        std::optional<KnowledgeUnitLifecycleState> lifecycle_state;
        std::optional<std::int64_t> observed_at_ms;
        std::optional<double> priority_weight;
    };

    /// \brief Backend-neutral durable knowledge-unit store contract.
    class IKnowledgeUnitStore {
    public:
        virtual ~IKnowledgeUnitStore();

        /// \brief Persists an envelope while preserving its immutable key.
        virtual void upsert(KnowledgeUnitEnvelope envelope) = 0;

        /// \brief Removes an envelope without reusing its numeric id.
        [[nodiscard]] virtual bool erase(const KnowledgeUnitId& id) = 0;

        /// \brief Lists ids for one kind in deterministic insertion order.
        [[nodiscard]] virtual std::vector<KnowledgeUnitId> scan_by_kind(
            KnowledgeUnitKind kind
        ) const = 0;

        /// \brief Lists ids for one scope across all defined kinds.
        [[nodiscard]] virtual std::vector<KnowledgeUnitId> scan_by_scope(
            const ScopeId& scope_id
        ) const = 0;

        /// \brief Creates a unit or returns the existing content-key match.
        /// \return The persisted envelope, including its monotonic id.
        /// \throws std::invalid_argument for an invalid request.
        [[nodiscard]] virtual KnowledgeUnitEnvelope create_or_get_unit(
            KnowledgeUnitCreateRequest request
        ) = 0;

        /// \brief Finds a unit by its opaque storage id.
        [[nodiscard]] virtual std::optional<KnowledgeUnitEnvelope> find(
            const KnowledgeUnitId& id
        ) const = 0;

        /// \brief Finds the current unit id for a content-addressing key.
        [[nodiscard]] virtual std::optional<KnowledgeUnitId> find_by_content_key(
            const KnowledgeUnitKey& key
        ) const = 0;

        /// \brief Applies only mutable envelope fields.
        /// \return The updated envelope.
        /// \throws std::invalid_argument for an invalid patch or id.
        [[nodiscard]] virtual KnowledgeUnitEnvelope update_mutable_fields(
            const KnowledgeUnitId& id,
            KnowledgeUnitMutablePatch patch
        ) = 0;
    };

} // namespace agent_memory

#endif
