#include <agent_memory/core/LibraryInfo.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>

#include <filesystem>

int main() {
    const auto database_path =
        std::filesystem::current_path() / "agent-memory-packaging-consumer.mdbx";
    try {
        auto context = agent_memory::MdbxStorageContext::open(database_path.string());
        if(!context || !context->connection()) return 1;
        context.reset();
        std::filesystem::remove(database_path);
        std::filesystem::remove(database_path.string() + "-lck");
        return agent_memory::library_name() == "Agent Memory C++" ? 0 : 1;
    } catch(...) {
        return 1;
    }
}
