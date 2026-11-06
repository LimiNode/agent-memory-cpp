#include <agent_memory.hpp>

#include <iostream>

int main() {
    const auto& version = agent_memory::library_version();
#if !defined(AGENT_MEMORY_EXPECTED_VERSION_MAJOR) \
    || !defined(AGENT_MEMORY_EXPECTED_VERSION_MINOR) \
    || !defined(AGENT_MEMORY_EXPECTED_VERSION_PATCH)
#error "The packaging consumer must declare the package version"
#endif
    if(agent_memory::library_name() != "Agent Memory C++"
        || version.major != AGENT_MEMORY_EXPECTED_VERSION_MAJOR
        || version.minor != AGENT_MEMORY_EXPECTED_VERSION_MINOR
        || version.patch != AGENT_MEMORY_EXPECTED_VERSION_PATCH) {
        return 1;
    }
    std::cout << agent_memory::library_name() << " "
        << version.major << "." << version.minor << "." << version.patch << "\n";
    return 0;
}
