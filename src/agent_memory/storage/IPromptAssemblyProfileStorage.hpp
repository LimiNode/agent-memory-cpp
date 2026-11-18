#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_I_PROMPT_ASSEMBLY_PROFILE_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_I_PROMPT_ASSEMBLY_PROFILE_STORAGE_HPP_INCLUDED

/// \file IPromptAssemblyProfileStorage.hpp
/// \brief Durable, revision-addressable storage contract for prompt profiles.

#include <agent_memory/domain/PromptComposition.hpp>

#include <cstdint>
#include <optional>

namespace agent_memory {

    /// \brief Storage contract for immutable prompt assembly profile revisions.
    ///
    /// A profile revision is immutable once published. Implementations retain
    /// historical revisions and never select or activate a profile implicitly.
    class IPromptAssemblyProfileStorage {
    public:
        virtual ~IPromptAssemblyProfileStorage();

        /// \brief Publishes one profile revision.
        /// \exception std::invalid_argument when the profile is invalid.
        /// \exception std::runtime_error when the same identity/revision has
        /// a different payload or the durable record is corrupt.
        virtual void put_profile(PromptAssemblyProfile profile) = 0;

        /// \brief Reads the exact requested profile revision.
        [[nodiscard]] virtual std::optional<PromptAssemblyProfile> find_profile(
            const std::string& profile_id,
            std::uint64_t revision) const = 0;

        /// \brief Reads the highest published revision without activating it.
        [[nodiscard]] virtual std::optional<PromptAssemblyProfile> find_latest_profile(
            const std::string& profile_id) const = 0;
    };

} // namespace agent_memory

#endif
