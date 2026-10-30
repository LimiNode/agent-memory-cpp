#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_DERIVED_PUBLICATION_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_DERIVED_PUBLICATION_STORE_HPP_INCLUDED

/// \file MdbxDerivedPublicationStore.hpp
/// \brief MDBX-backed atomic derived publication vertical slice.

#include <agent_memory/storage/IDerivedPublicationStore.hpp>

#include <cstdint>
#include <memory>
#include <string>

#if AGENT_MEMORY_HAS_MDBX
#include "MdbxStorageContext.hpp"

namespace agent_memory {

    struct MdbxDerivedPublicationStoreOptions final {
        std::string path;
        std::string table_prefix = "agent_memory";
        bool relative_to_exe = false;
        std::int64_t max_dbs = kDefaultMdbxMaxDbs;
        std::shared_ptr<MdbxStorageContext> context;
    };

    class MdbxDerivedPublicationStore final : public IDerivedPublicationStore {
    public:
        explicit MdbxDerivedPublicationStore(MdbxDerivedPublicationStoreOptions options);
        ~MdbxDerivedPublicationStore() override;

        MdbxDerivedPublicationStore(const MdbxDerivedPublicationStore&) = delete;
        MdbxDerivedPublicationStore& operator=(const MdbxDerivedPublicationStore&) = delete;

        void publish(DerivedPublicationRequest request) override;

        [[nodiscard]] DerivedPublicationReadResult read(
            const ScopeId& access_scope,
            const std::string& publication_id,
            const std::vector<DerivedPublicationInput>& current_inputs,
            const std::string& current_dependency_frontier
        ) const override;

    private:
        class Impl;
        std::unique_ptr<Impl> m_impl;
    };

} // namespace agent_memory
#endif

#endif
