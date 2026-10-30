#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <mutex>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>

#ifdef _WIN32
#    define WIN32_LEAN_AND_MEAN
#    include <windows.h>
#endif

#ifndef AGENT_MEMORY_HAS_MDBX
#    error "AGENT_MEMORY_HAS_MDBX must be defined by the agent_memory target"
#endif

#if !AGENT_MEMORY_HAS_MDBX
#    error "Resource frontier fencing test requires AGENT_MEMORY_ENABLE_MDBX=ON"
#endif

namespace {

void expect(bool condition, std::string_view message) {
    if(!condition) {
        throw std::runtime_error(std::string{message});
    }
}

void make_removable(const std::filesystem::path& path) noexcept {
#ifdef _WIN32
    if(std::filesystem::exists(path)) {
        SetFileAttributesW(path.wstring().c_str(), FILE_ATTRIBUTE_NORMAL);
    }
#else
    static_cast<void>(path);
#endif
}

class TestDatabase final {
public:
    static TestDatabase create(std::string_view tag) {
        const auto root = std::filesystem::temp_directory_path();
        const auto nonce = static_cast<std::uint64_t>(
            std::chrono::high_resolution_clock::now().time_since_epoch().count()
        ) ^ static_cast<std::uint64_t>(std::random_device{}());
        std::filesystem::path sentinel;
        for(std::uint64_t sentinel_attempt = 0; sentinel_attempt < 64; ++sentinel_attempt) {
            const auto sentinel_candidate = root /
                ("agent_memory_resource_frontier_" + std::string{tag} + "_" +
                 std::to_string(nonce) + "_sentinel_" + std::to_string(sentinel_attempt));
            std::error_code error;
            if(std::filesystem::create_directory(sentinel_candidate, error)) {
                sentinel = sentinel_candidate;
                break;
            }
            if(error && error != std::make_error_code(std::errc::file_exists)) {
                throw std::runtime_error(
                    "resource frontier sentinel creation failed: " + error.message()
                );
            }
        }
        if(sentinel.empty()) {
            throw std::runtime_error("resource frontier sentinel reservation failed");
        }
        for(std::uint64_t attempt = 0; attempt < 64; ++attempt) {
            const auto candidate = root /
                ("agent_memory_resource_frontier_" + std::string{tag} + "_" +
                 std::to_string(nonce) + "_database_" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                return TestDatabase(
                    candidate,
                    candidate / "frontier.mdbx",
                    std::filesystem::path{(candidate / "frontier.mdbx").string() + "-lck"},
                    sentinel
                );
            }
            if(error && error != std::make_error_code(std::errc::file_exists)) {
                std::error_code cleanup_error;
                std::filesystem::remove(sentinel, cleanup_error);
                throw std::runtime_error(
                    "resource frontier database directory creation failed: " + error.message()
                );
            }
        }
        std::error_code cleanup_error;
        std::filesystem::remove(sentinel, cleanup_error);
        throw std::runtime_error("resource frontier test directory reservation failed");
    }

    TestDatabase(const TestDatabase&) = delete;
    TestDatabase& operator=(const TestDatabase&) = delete;

    TestDatabase(TestDatabase&& other) noexcept
        : m_directory(std::move(other.m_directory))
        , m_path(std::move(other.m_path))
        , m_lock_path(std::move(other.m_lock_path))
        , m_sentinel(std::move(other.m_sentinel))
        , m_cleaned(other.m_cleaned) {
        other.m_cleaned = true;
    }

    ~TestDatabase() {
        if(!m_cleaned) {
            cleanup_noexcept();
        }
    }

    [[nodiscard]] const std::filesystem::path& path() const noexcept {
        return m_path;
    }

    void verify_sentinel() const {
        expect(
            !m_sentinel.empty() && std::filesystem::is_directory(m_sentinel),
            "pre-existing sentinel disappeared during MDBX fixture"
        );
    }

    void cleanup_or_throw() {
        remove_if_present(m_path, "resource frontier database");
        remove_if_present(m_lock_path, "resource frontier lock");
        remove_if_present(m_directory, "resource frontier database directory");
        remove_if_present(m_sentinel, "resource frontier sentinel");
        m_cleaned = true;
    }

private:
    TestDatabase(
        std::filesystem::path directory,
        std::filesystem::path path,
        std::filesystem::path lock_path,
        std::filesystem::path sentinel
    )
        : m_directory(std::move(directory))
        , m_path(std::move(path))
        , m_lock_path(std::move(lock_path))
        , m_sentinel(std::move(sentinel)) {}

    static void remove_if_present(
        const std::filesystem::path& path,
        std::string_view description
    ) {
        if(path.empty() || !std::filesystem::exists(path)) {
            return;
        }
        make_removable(path);
        std::error_code error;
        const auto removed = std::filesystem::remove(path, error);
        expect(
            removed && !error && !std::filesystem::exists(path),
            std::string{description} + " cleanup failed"
        );
    }

    void cleanup_noexcept() noexcept {
        std::error_code ignored;
        make_removable(m_path);
        make_removable(m_lock_path);
        std::filesystem::remove(m_path, ignored);
        ignored.clear();
        std::filesystem::remove(m_lock_path, ignored);
        ignored.clear();
        std::filesystem::remove(m_directory, ignored);
        ignored.clear();
        std::filesystem::remove(m_sentinel, ignored);
    }

    std::filesystem::path m_directory;
    std::filesystem::path m_path;
    std::filesystem::path m_lock_path;
    std::filesystem::path m_sentinel;
    bool m_cleaned = false;
};

agent_memory::ResourceManifest make_manifest(std::uint64_t generation) {
    agent_memory::ResourceManifest manifest;
    manifest.revision.resource_id = agent_memory::ResourceId{"resource-A"};
    manifest.revision.generation = generation;
    manifest.revision.content_hash = 100U + generation;
    manifest.revision.pipeline_config_hash = 7U;
    agent_memory::ResourceBodyDigest digest;
    digest.bytes.fill(static_cast<std::uint8_t>(generation));
    manifest.revision.body_digest = digest;
    manifest.payload_version = agent_memory::ResourceManifestPayloadVersion::V6;
    manifest.schema = agent_memory::ResourceManifestSchema{
        "resource-frontier-fencing-fixture", 1
    };
    return manifest;
}

void run_fixture(std::string_view tag) {
    using namespace agent_memory;

    auto database = TestDatabase::create(tag);
    const std::string guard_table = "agent_memory_frontier_fixture_guard";
    const std::string token_table = "agent_memory_frontier_fixture_tokens";
    const std::string key = "resource-A";
    {
        auto context = MdbxStorageContext::open(database.path().string());
        mdbxc::KeyValueTable<std::string, std::string> guards(
            context->connection(), guard_table
        );
        mdbxc::KeyValueTable<std::string, std::string> tokens(
            context->connection(), token_table
        );
        guards.insert_or_assign(key, "intent-1");
        tokens.insert_or_assign(key, "token-1");

        MdbxResourceManifestStorageOptions options;
        options.context = context;
        options.publication_guard_table_name = guard_table;
        options.publication_token_guard_table_name = token_table;
        MdbxResourceManifestStorage manifests(std::move(options));
        const auto first = make_manifest(1);
        manifests.upsert_manifest(first);

        // This fixture proves only stale-token rejection of a guarded manifest
        // update; it does not prove root freshness of a derived publication.
        tokens.insert_or_assign(key, "token-2");
        bool rejected = false;
        try {
            manifests.upsert_manifest_if_unchanged_guarded(
                make_manifest(2), first,
                MdbxManifestPublicationGuard{
                    guard_table, key, "", token_table, "token-1"
                }
            );
        } catch(const std::runtime_error&) {
            rejected = true;
        }
        expect(rejected, "stale root frontier token was accepted");
        const auto after_rejection = manifests.find_manifest(ResourceId{"resource-A"});
        expect(
            after_rejection && after_rejection->revision.generation == 1,
            "stale fenced update changed the visible root manifest"
        );

        // A retry prepared against the new frontier may publish A@2.
        manifests.upsert_manifest_if_unchanged_guarded(
            make_manifest(2), after_rejection,
            MdbxManifestPublicationGuard{
                guard_table, key, "", token_table, "token-2"
            }
        );
        const auto current = manifests.find_manifest(ResourceId{"resource-A"});
        expect(
            current && current->revision.generation == 2,
            "frontier retry did not publish the new root revision"
        );
        database.verify_sentinel();
    }
    database.cleanup_or_throw();
}

} // namespace

int main() {
    try {
        std::exception_ptr first_error;
        std::mutex error_mutex;
        const auto run = [&](std::string_view tag) {
            try {
                run_fixture(tag);
            } catch(...) {
                std::lock_guard<std::mutex> lock(error_mutex);
                if(!first_error) {
                    first_error = std::current_exception();
                }
            }
        };
        std::thread first([&] { run("parallel-a"); });
        std::thread second([&] { run("parallel-b"); });
        first.join();
        second.join();
        if(first_error) {
            std::rethrow_exception(first_error);
        }
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
