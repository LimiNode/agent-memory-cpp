#include <agent_memory.hpp>

#include <iostream>

int main() {
    const auto& version = agent_memory::library_version();
    if(agent_memory::library_name() != "Agent Memory C++"
        || version.major != 0 || version.minor != 1 || version.patch != 0) {
        return 1;
    }
    std::cout << agent_memory::library_name() << " "
        << version.major << "." << version.minor << "." << version.patch << "\n";
    return 0;
}
