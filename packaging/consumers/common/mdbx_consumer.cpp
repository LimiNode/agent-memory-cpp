#include <agent_memory/core/LibraryInfo.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>

#include <filesystem>
#include <random>
#include <stdexcept>
#include <string>

namespace {

class TemporaryDirectory final {
public:
    TemporaryDirectory() {
        const auto root = std::filesystem::temp_directory_path();
        std::random_device random;
        for(std::size_t attempt = 0; attempt != 32; ++attempt) {
            const auto candidate = root / ("agent-memory-mdbx-consumer-"
                + std::to_string(random()) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                path_ = candidate;
                return;
            }
        }
        throw std::runtime_error("unable to create a reserved temporary directory");
    }

    ~TemporaryDirectory() {
        std::error_code error;
        std::filesystem::remove_all(path_, error);
    }

    TemporaryDirectory(const TemporaryDirectory&) = delete;
    TemporaryDirectory& operator=(const TemporaryDirectory&) = delete;

    [[nodiscard]] const std::filesystem::path& path() const noexcept { return path_; }

private:
    std::filesystem::path path_;
};

} // namespace

int main() {
    try {
        const TemporaryDirectory temporary_directory;
        const auto database_path = temporary_directory.path() / "database.mdbx";
        auto context = agent_memory::MdbxStorageContext::open(database_path.string());
        if(!context || !context->connection()) return 1;
        context.reset();
        return agent_memory::library_name() == "Agent Memory C++" ? 0 : 1;
    } catch(...) {
        return 1;
    }
}
