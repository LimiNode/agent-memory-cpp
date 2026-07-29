#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>

#include <chrono>
#include <filesystem>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#ifndef AGENT_MEMORY_HAS_MDBX
#   error "AGENT_MEMORY_HAS_MDBX must be defined by the agent_memory target"
#endif

#if !AGENT_MEMORY_HAS_MDBX
#   error "MDBX resource manifest storage test requires AGENT_MEMORY_ENABLE_MDBX=ON"
#endif

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    std::filesystem::path test_database_path() {
        const auto now = std::chrono::steady_clock::now().time_since_epoch().count();
        const auto random_suffix = std::random_device{}();
        return std::filesystem::temp_directory_path() / (
            "agent_memory_mdbx_resource_manifest_storage_" +
            std::to_string(now) +
            "_" +
            std::to_string(random_suffix) +
            ".mdbx"
        );
    }

    class DatabaseFileCleanup final {
    public:
        explicit DatabaseFileCleanup(std::filesystem::path path)
            : m_path(std::move(path)) {}

        ~DatabaseFileCleanup() {
            std::error_code error;
            std::filesystem::remove(m_path, error);
        }

        [[nodiscard]] const std::filesystem::path& path() const noexcept {
            return m_path;
        }

    private:
        std::filesystem::path m_path;
    };

    agent_memory::ResourceManifest make_manifest(
        agent_memory::ResourceId resource_id,
        std::uint64_t generation,
        std::string bucket_key
    ) {
        return agent_memory::ResourceManifest{
            agent_memory::ResourceRevision{
                std::move(resource_id),
                generation,
                0xAABBCCDDU,
                0x11223344U
            },
            {
                agent_memory::DerivedRecordRef{
                    agent_memory::DerivedRecordKind::Chunk,
                    agent_memory::ChunkId{"chunk:mdbx:0"},
                    {},
                    0
                },
                agent_memory::DerivedRecordRef{
                    agent_memory::DerivedRecordKind::BinaryBucketPosting,
                    {},
                    std::move(bucket_key),
                    1
                }
            }
        };
    }

    agent_memory::ResourceBodyDigest make_body_digest(std::uint8_t value) {
        agent_memory::ResourceBodyDigest digest;
        digest.bytes.fill(value);
        return digest;
    }

} // namespace

int main() {
    const DatabaseFileCleanup database_file{test_database_path()};
    const auto& database_path = database_file.path();
    const agent_memory::ResourceId resource_id{"resource:mdbx"};

    {
        agent_memory::MdbxResourceManifestStorage storage(
            agent_memory::MdbxResourceManifestStorageOptions{
                database_path.string(),
                "agent_memory_test",
                false
            }
        );

        try {
            storage.upsert_manifest(agent_memory::ResourceManifest{});
            return fail("MDBX manifest storage must reject invalid manifests");
        } catch(const std::invalid_argument&) {
        }

        storage.upsert_manifest(make_manifest(resource_id, 1, "bucket:24:alpha"));

        const auto stored = storage.find_manifest(resource_id);
        if(!stored) {
            return fail("MDBX manifest storage must return inserted manifest");
        }

        if(stored->revision.generation != 1 || stored->records.size() != 2) {
            return fail("MDBX manifest storage must restore revision and records");
        }

        if(stored->records[1].key != "bucket:24:alpha") {
            return fail("MDBX manifest storage must restore posting key");
        }

        auto replacement = make_manifest(resource_id, 2, "bucket:24:beta");
        replacement.revision.body_digest = make_body_digest(0x3CU);
        replacement.state = agent_memory::ResourceManifestState::ErasePending;
        storage.upsert_manifest(std::move(replacement));
    }

    {
        agent_memory::MdbxResourceManifestStorage storage(
            agent_memory::MdbxResourceManifestStorageOptions{
                database_path.string(),
                "agent_memory_test",
                false
            }
        );

        const auto persisted = storage.find_manifest(resource_id);
        if(!persisted) {
            return fail("MDBX manifest storage must persist manifests across reopen");
        }

        if(
            persisted->revision.generation != 2 ||
            persisted->records.size() != 2 ||
            persisted->records[1].key != "bucket:24:beta" ||
            !persisted->revision.body_digest ||
            persisted->revision.body_digest->bytes != make_body_digest(0x3CU).bytes ||
            persisted->state != agent_memory::ResourceManifestState::ErasePending
        ) {
            return fail("MDBX manifest storage must persist v3 manifest fields");
        }

        if(!agent_memory::is_valid_resource_manifest(*persisted)) {
            return fail("MDBX manifest storage must restore valid manifest");
        }

        if(!storage.erase_manifest(resource_id)) {
            return fail("MDBX manifest erase must report removed manifest");
        }

        if(storage.find_manifest(resource_id)) {
            return fail("MDBX manifest erase must remove manifest state");
        }

        if(storage.erase_manifest(resource_id)) {
            return fail("MDBX manifest erase of missing resource must report false");
        }
    }

    return 0;
}
