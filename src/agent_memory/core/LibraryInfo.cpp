#include "LibraryInfo.hpp"

namespace agent_memory {

    namespace {

#ifndef AGENT_MEMORY_VERSION_MAJOR
#define AGENT_MEMORY_VERSION_MAJOR 0
#endif
#ifndef AGENT_MEMORY_VERSION_MINOR
#define AGENT_MEMORY_VERSION_MINOR 0
#endif
#ifndef AGENT_MEMORY_VERSION_PATCH
#define AGENT_MEMORY_VERSION_PATCH 0
#endif

        constexpr Version CURRENT_VERSION{
            AGENT_MEMORY_VERSION_MAJOR,
            AGENT_MEMORY_VERSION_MINOR,
            AGENT_MEMORY_VERSION_PATCH
        };

    } // namespace

    std::string_view library_name() noexcept {
        return "Agent Memory C++";
    }

    std::string_view library_description() noexcept {
        return "Embedded C++17 memory and retrieval toolkit for AI agents";
    }

    const Version& library_version() noexcept {
        return CURRENT_VERSION;
    }

} // namespace agent_memory
