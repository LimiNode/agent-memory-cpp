#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceIndexRecordOwnerStorage.hpp>

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
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
           version == "agent_memory.resource_manifest.v4" ||
           version == "agent_memory.resource_manifest.v5") {
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
        if(version == "agent_memory.resource_manifest.v4" ||
           version == "agent_memory.resource_manifest.v5") {
            append_size(payload, pending.size());
            for(const auto& pending_record : pending) {
                append_record(payload, pending_record);
            }
        }
        if(version == "agent_memory.resource_manifest.v5") {
            append_string(payload, {});
            append_uint64(payload, 0);
        }
        return payload;
    }

    std::string make_raw_manifest_with_record_count(
        std::string_view resource_id,
        std::string_view count
    ) {
        std::string payload;
        append_string(payload, "agent_memory.resource_manifest.v1");
        append_string(payload, resource_id);
        append_uint64(payload, 1);
        append_uint64(payload, 0);
        append_uint64(payload, 1);
        append_string(payload, count);
        return payload;
    }

    std::string make_raw_manifest_with_pending_record_count(
        std::string_view resource_id,
        std::string_view count
    ) {
        const agent_memory::DerivedRecordRef record{
            agent_memory::DerivedRecordKind::Chunk,
            agent_memory::ChunkId{"chunk:mdbx:pending-count"},
            {},
            0
        };

        std::string payload;
        append_string(payload, "agent_memory.resource_manifest.v4");
        append_string(payload, resource_id);
        append_uint64(payload, 1);
        append_uint64(payload, 0);
        append_uint64(payload, 1);
        append_string(payload, "none");
        append_string(payload, "erase_pending");
        append_size(payload, 1);
        append_record(payload, record);
        append_string(payload, count);
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
    static_assert(!std::is_move_constructible<
        agent_memory::MdbxResourceIndexRecordOwnerStorage
    >::value);
    static_assert(!std::is_move_assignable<
        agent_memory::MdbxResourceIndexRecordOwnerStorage
    >::value);

    const DatabaseFileCleanup database_file{test_database_path()};
    const auto& database_path = database_file.path();
    const agent_memory::ResourceId resource_id{"resource:mdbx"};
    const agent_memory::DocumentId owner_document_id{"doc:mdbx:owner"};
    const agent_memory::ChunkId owner_chunk_id{"chunk:mdbx:owner"};

    {
        agent_memory::MdbxResourceIndexRecordOwnerStorage owner_storage(
            agent_memory::MdbxResourceIndexRecordOwnerStorageOptions{
                database_path.string(),
                "agent_memory_test",
                false
            }
        );
        const agent_memory::ResourceIndexRecordOwner owner{
            resource_id,
            7,
            agent_memory::ResourceManifestSchema{"agent_memory.resource_indexer", 1}
        };
        owner_storage.upsert_document_owner(owner_document_id, owner);
        owner_storage.upsert_chunk_owner(owner_chunk_id, owner);
        const auto stored_document_owner = owner_storage.find_document_owner(owner_document_id);
        const auto stored_chunk_owner = owner_storage.find_chunk_owner(owner_chunk_id);
        if(
            !stored_document_owner ||
            !stored_chunk_owner ||
            stored_document_owner->resource_id != resource_id ||
            stored_chunk_owner->generation != 7 ||
            stored_chunk_owner->manifest_schema.schema_id != "agent_memory.resource_indexer"
        ) {
            return fail("MDBX record owner storage must persist document and chunk ownership");
        }

        if(
            !owner_storage.erase_document_owner(owner_document_id) ||
            !owner_storage.erase_chunk_owner(owner_chunk_id) ||
            owner_storage.find_document_owner(owner_document_id) ||
            owner_storage.find_chunk_owner(owner_chunk_id)
        ) {
            return fail("MDBX record owner storage must erase ownership bindings");
        }
    }

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
        "resource:mdbx:v4:pending",
        make_raw_manifest_payload(
            "agent_memory.resource_manifest.v4",
            "resource:mdbx:v4:pending",
            agent_memory::ResourceManifestState::ErasePending,
            make_body_digest(0x66U),
            {agent_memory::DerivedRecordRef{
                agent_memory::DerivedRecordKind::VectorRecord,
                agent_memory::ChunkId{"chunk:mdbx:v4:pending"},
                {},
                0
            }}
        )
    );
    write_raw_manifest(
        database_path,
        "resource:mdbx:wrong-key",
        make_raw_manifest_payload("agent_memory.resource_manifest.v1", "resource:mdbx:payload-owner")
    );
    write_raw_manifest(
        database_path,
        "resource:mdbx:malformed-record-count",
        make_raw_manifest_with_record_count(
            "resource:mdbx:malformed-record-count",
            "18446744073709551615"
        )
    );
    write_raw_manifest(
        database_path,
        "resource:mdbx:malformed-pending-record-count",
        make_raw_manifest_with_pending_record_count(
            "resource:mdbx:malformed-pending-record-count",
            "18446744073709551615"
        )
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
            || persisted->payload_version != agent_memory::ResourceManifestPayloadVersion::V5
        ) {
            return fail("MDBX manifest storage must persist v5 manifest fields");
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
        const auto v4_pending = storage.find_manifest(
            agent_memory::ResourceId{"resource:mdbx:v4:pending"}
        );
        if(
            !v1 || v1->state != agent_memory::ResourceManifestState::Active ||
            v1->revision.body_digest ||
            !v2_active || v2_active->state != agent_memory::ResourceManifestState::Active ||
            v2_active->revision.body_digest ||
            !v2_pending || v2_pending->state != agent_memory::ResourceManifestState::ErasePending ||
            v2_pending->revision.body_digest ||
            !v3 || !v3->revision.body_digest ||
            v3->revision.body_digest->algorithm != agent_memory::ResourceBodyDigestAlgorithm::Sha256 ||
            v3->revision.body_digest->bytes != make_body_digest(0x55U).bytes ||
            !v4_pending ||
            v4_pending->state != agent_memory::ResourceManifestState::ErasePending ||
            !v4_pending->revision.body_digest ||
            v4_pending->revision.body_digest->bytes != make_body_digest(0x66U).bytes ||
            v4_pending->pending_reclaim_records.size() != 1 ||
            v4_pending->pending_reclaim_records[0].kind !=
                agent_memory::DerivedRecordKind::VectorRecord ||
            v1->payload_version != agent_memory::ResourceManifestPayloadVersion::V1 ||
            v2_active->payload_version != agent_memory::ResourceManifestPayloadVersion::V2 ||
            v2_pending->payload_version != agent_memory::ResourceManifestPayloadVersion::V2 ||
            v3->payload_version != agent_memory::ResourceManifestPayloadVersion::V3 ||
            v4_pending->payload_version != agent_memory::ResourceManifestPayloadVersion::V4
        ) {
            return fail("MDBX manifest storage must preserve v1-v4 payload provenance");
        }

        try {
            (void)storage.find_manifest(agent_memory::ResourceId{"resource:mdbx:wrong-key"});
            return fail("MDBX manifest storage must reject a mismatched key and payload resource id");
        } catch(const std::runtime_error&) {
        }

        try {
            (void)storage.find_manifest(
                agent_memory::ResourceId{"resource:mdbx:malformed-pending-record-count"}
            );
            return fail("MDBX manifest storage must reject a malformed pending record count");
        } catch(const std::runtime_error&) {
        }

        try {
            (void)storage.find_manifest(
                agent_memory::ResourceId{"resource:mdbx:malformed-record-count"}
            );
            return fail("MDBX manifest storage must reject a malformed record count");
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
