#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_I_PROJECTION_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_I_PROJECTION_STORE_HPP_INCLUDED

/// \file IProjectionStore.hpp
/// \brief Backend-neutral persistence contract for search projections.

#include <agent_memory/domain/SearchProjection.hpp>

#include <cstdint>
#include <optional>
#include <vector>

namespace agent_memory {

    class IProjectionStore {
    public:
        virtual ~IProjectionStore();

        /// Stores one immutable projection revision or replays the same value.
        virtual void put(SearchProjection projection) = 0;

        /// Lists all revisions for one unit in deterministic key order.
        [[nodiscard]] virtual std::vector<SearchProjection> list(
            const ScopeId& scope_id,
            const KnowledgeUnitId& unit_id,
            std::optional<ProjectionKind> kind = std::nullopt
        ) const = 0;

        /// Deletes one derived projection revision. The operation is idempotent.
        [[nodiscard]] virtual bool delete_revision(
            const ScopeId& scope_id,
            const KnowledgeUnitId& unit_id,
            ProjectionKind kind,
            std::uint64_t projection_revision
        ) = 0;
    };

} // namespace agent_memory

#endif
