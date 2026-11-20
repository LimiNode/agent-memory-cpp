#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <filesystem>
#include <map>
#include <stdexcept>
#include <string>

static_assert(__cplusplus >= 201703L, "MDBX consumers must see the actual C++17 language level");

#if defined(_MSC_VER) && !defined(NOMINMAX)
#error "MSVC MDBX consumers must disable Windows min/max macros"
#endif

int main() {
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto path = std::filesystem::temp_directory_path()
        / ("agent_memory_mdbx_bulk_" + std::to_string(nonce) + ".mdbx");
    {
        auto context = agent_memory::MdbxStorageContext::open(path.string());
        mdbxc::KeyValueTable<std::string, std::string> table(context->connection(), "bulk");
        table.insert_or_assign("key", "value");
        const std::map<std::string, std::string> expected{{"key", "value"}};
        if(table.retrieve_all() != expected) throw std::runtime_error("automatic bulk retrieval failed");
        auto read = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        if(table.retrieve_all(read) != expected) throw std::runtime_error("snapshot bulk retrieval failed");
        read.commit();
    }
    std::error_code error;
    std::filesystem::remove(path, error);
}
