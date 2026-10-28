#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PROJECTION_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PROJECTION_STORE_HPP_INCLUDED

/// \file MdbxProjectionStore.hpp
/// \brief MDBX-backed revision-aware search projection store.

#include <agent_memory/storage/IProjectionStore.hpp>

#include <cstdint>
#include <memory>
#include <string>

#if AGENT_MEMORY_HAS_MDBX
#include "MdbxStorageContext.hpp"

namespace agent_memory {

    struct MdbxProjectionStoreOptions final {
        std::string path;
        std::string table_prefix = "agent_memory";
        bool relative_to_exe = false;
        std::int64_t max_dbs = kDefaultMdbxMaxDbs;
        std::shared_ptr<MdbxStorageContext> context;
    };

    class MdbxProjectionStore final : public IProjectionStore {
    public:
        explicit MdbxProjectionStore(MdbxProjectionStoreOptions options);
        ~MdbxProjectionStore() override;

        MdbxProjectionStore(const MdbxProjectionStore&) = delete;
        MdbxProjectionStore& operator=(const MdbxProjectionStore&) = delete;

        void put(SearchProjection projection) override;

        [[nodiscard]] std::vector<SearchProjection> list(
            const ScopeId& scope_id,
            const KnowledgeUnitId& unit_id,
            std::optional<ProjectionKind> kind = std::nullopt
        ) const override;

        [[nodiscard]] bool delete_revision(
            const ScopeId& scope_id,
            const KnowledgeUnitId& unit_id,
            ProjectionKind kind,
            std::uint64_t projection_revision
        ) override;

    private:
        class Impl;
        std::unique_ptr<Impl> m_impl;
    };

} // namespace agent_memory
#endif

#endif
