#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

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

    void append_size(std::string& payload, std::size_t value) {
        payload += std::to_string(value);
        payload.push_back(':');
    }

    void append_string(std::string& payload, std::string_view value) {
        append_size(payload, value.size());
        payload.append(value.data(), value.size());
    }

    void append_uint64(std::string& payload, std::uint64_t value) {
        append_string(payload, std::to_string(value));
    }

    void append_record(std::string& payload, const agent_memory::DerivedRecordRef& record) {
        append_string(payload, agent_memory::to_string(record.kind));
        append_string(payload, record.chunk_id.value());
        append_string(payload, record.key);
        append_uint64(payload, record.ordinal);
    }

    std::string make_raw_manifest_payload(
        std::string_view version,
        std::string_view resource_id,
        agent_memory::ResourceManifestState state = agent_memory::ResourceManifestState::Active,
        const std::optional<agent_memory::ResourceBodyDigest>& body_digest = std::nullopt,
        const std::vector<agent_memory::DerivedRecordRef>& pending = {}
    ) {
        const agent_memory::DerivedRecordRef record{
            agent_memory::DerivedRecordKind::Chunk,
            agent_memory::ChunkId{"chunk:mdbx:raw"},
            {},
            0
        };

        std::string payload;
        append_string(payload, version);
        append_string(payload, resource_id);
        append_uint64(payload, 7);
        append_uint64(payload, 0xAABBCCDDU);
        append_uint64(payload, 0x11223344U);
        if(version == "agent_memory.resource_manifest.v3" ||
           version == "agent_memory.resource_manifest.v4") {
            if(body_digest) {
                append_string(payload, "sha256");
                std::string bytes;
                bytes.reserve(body_digest->bytes.size());
                for(const auto byte : body_digest->bytes) {
                    bytes.push_back(static_cast<char>(byte));
                }
                append_string(payload, bytes);
            } else {
                append_string(payload, "none");
            }
        }
        if(version != "agent_memory.resource_manifest.v1") {
            append_string(
                payload,
                state == agent_memory::ResourceManifestState::Active ? "active" : "erase_pending"
            );
        }
        append_size(payload, 1);
        append_record(payload, record);
        if(version == "agent_memory.resource_manifest.v4") {
            append_size(payload, pending.size());
            for(const auto& pending_record : pending) {
                append_record(payload, pending_record);
            }
        }
        return payload;
    }

    void write_raw_manifest(
        const std::filesystem::path& database_path,
        std::string key,
        std::string payload
    ) {
        mdbxc::Config config;
        config.pathname = database_path.string();
        config.max_dbs = 16;
        config.no_subdir = true;
        config.relative_to_exe = false;

        const auto connection = mdbxc::Connection::create(config);
        mdbxc::KeyValueTable<std::string, std::string> table(
            connection,
            "agent_memory_test_resource_manifests"
        );
        table.insert_or_assign(std::move(key), std::move(payload));
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

        auto invalid_digest = make_manifest(
            agent_memory::ResourceId{"resource:mdbx:invalid-digest"},
            1,
            "bucket:24:invalid-digest"
        );
        invalid_digest.revision.body_digest = make_body_digest(0x01U);
        invalid_digest.revision.body_digest->algorithm =
            static_cast<agent_memory::ResourceBodyDigestAlgorithm>(255);
        try {
            storage.upsert_manifest(std::move(invalid_digest));
            return fail("MDBX manifest storage must reject an unknown body-digest algorithm");
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
        replacement.pending_reclaim_records.push_back(agent_memory::DerivedRecordRef{
            agent_memory::DerivedRecordKind::VectorRecord,
            agent_memory::ChunkId{"chunk:mdbx:pending"},
            {},
            0
        });
        storage.upsert_manifest(std::move(replacement));
    }

    write_raw_manifest(
        database_path,
        "resource:mdbx:v1",
        make_raw_manifest_payload("agent_memory.resource_manifest.v1", "resource:mdbx:v1")
    );
    write_raw_manifest(
        database_path,
        "resource:mdbx:v2:active",
        make_raw_manifest_payload("agent_memory.resource_manifest.v2", "resource:mdbx:v2:active")
    );
    write_raw_manifest(
        database_path,
        "resource:mdbx:v2:pending",
        make_raw_manifest_payload(
            "agent_memory.resource_manifest.v2",
            "resource:mdbx:v2:pending",
            agent_memory::ResourceManifestState::ErasePending
        )
    );
    write_raw_manifest(
        database_path,
        "resource:mdbx:v3",
        make_raw_manifest_payload(
            "agent_memory.resource_manifest.v3",
            "resource:mdbx:v3",
            agent_memory::ResourceManifestState::Active,
            make_body_digest(0x55U)
        )
    );
    write_raw_manifest(
        database_path,
        "resource:mdbx:wrong-key",
        make_raw_manifest_payload("agent_memory.resource_manifest.v1", "resource:mdbx:payload-owner")
    );

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
            persisted->state != agent_memory::ResourceManifestState::ErasePending ||
            persisted->pending_reclaim_records.size() != 1
        ) {
            return fail("MDBX manifest storage must persist v4 manifest fields");
        }

        if(!agent_memory::is_valid_resource_manifest(*persisted)) {
            return fail("MDBX manifest storage must restore valid manifest");
        }

        const auto v1 = storage.find_manifest(agent_memory::ResourceId{"resource:mdbx:v1"});
        const auto v2_active = storage.find_manifest(
            agent_memory::ResourceId{"resource:mdbx:v2:active"}
        );
        const auto v2_pending = storage.find_manifest(
            agent_memory::ResourceId{"resource:mdbx:v2:pending"}
        );
        const auto v3 = storage.find_manifest(agent_memory::ResourceId{"resource:mdbx:v3"});
        if(
            !v1 || v1->state != agent_memory::ResourceManifestState::Active ||
            v1->revision.body_digest ||
            !v2_active || v2_active->state != agent_memory::ResourceManifestState::Active ||
            v2_active->revision.body_digest ||
            !v2_pending || v2_pending->state != agent_memory::ResourceManifestState::ErasePending ||
            v2_pending->revision.body_digest ||
            !v3 || !v3->revision.body_digest ||
            v3->revision.body_digest->algorithm != agent_memory::ResourceBodyDigestAlgorithm::Sha256 ||
            v3->revision.body_digest->bytes != make_body_digest(0x55U).bytes
        ) {
            return fail("MDBX manifest storage must read v1-v3 compatibility payloads");
        }

        try {
            (void)storage.find_manifest(agent_memory::ResourceId{"resource:mdbx:wrong-key"});
            return fail("MDBX manifest storage must reject a mismatched key and payload resource id");
        } catch(const std::runtime_error&) {
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
