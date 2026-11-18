#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PROMPT_ASSEMBLY_PROFILE_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PROMPT_ASSEMBLY_PROFILE_STORAGE_HPP_INCLUDED

/// \file MdbxPromptAssemblyProfileStorage.hpp
/// \brief MDBX-backed immutable prompt assembly profile history.

#include <agent_memory/storage/IPromptAssemblyProfileStorage.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include "MdbxStorageContext.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <string>

namespace agent_memory {

    /// \brief Configuration for durable prompt profile storage.
    struct MdbxPromptAssemblyProfileStorageOptions final {
        std::string path;
        std::string table_prefix = "agent_memory";
        bool relative_to_exe = false;
        std::int64_t max_dbs = kDefaultMdbxMaxDbs;
        std::shared_ptr<MdbxStorageContext> context;
    };

    /// \brief Durable, exact-revision MDBX implementation.
    ///
    /// Historical rows are retained. This adapter stores a profile; it does
    /// not decide whether any caller may use or activate it.
    class MdbxPromptAssemblyProfileStorage final
        : public IPromptAssemblyProfileStorage {
    public:
        explicit MdbxPromptAssemblyProfileStorage(
            MdbxPromptAssemblyProfileStorageOptions options);
        ~MdbxPromptAssemblyProfileStorage() override;

        MdbxPromptAssemblyProfileStorage(
            const MdbxPromptAssemblyProfileStorage&) = delete;
        MdbxPromptAssemblyProfileStorage& operator=(
            const MdbxPromptAssemblyProfileStorage&) = delete;

        MdbxPromptAssemblyProfileStorage(
            MdbxPromptAssemblyProfileStorage&& other) noexcept;
        MdbxPromptAssemblyProfileStorage& operator=(
            MdbxPromptAssemblyProfileStorage&& other) noexcept;

        void put_profile(PromptAssemblyProfile profile) override;

        [[nodiscard]] std::optional<PromptAssemblyProfile> find_profile(
            const std::string& profile_id,
            std::uint64_t revision) const override;

        [[nodiscard]] std::optional<PromptAssemblyProfile> find_latest_profile(
            const std::string& profile_id) const override;

    private:
        class Impl;
        std::unique_ptr<Impl> m_impl;
    };

} // namespace agent_memory
#endif

#endif
