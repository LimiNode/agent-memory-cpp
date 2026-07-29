#include <agent_memory.hpp>

#include <iostream>
#include <map>
#include <optional>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    class InMemoryResourceManifestStorage final
        : public agent_memory::IResourceManifestStorage {
    public:
        void fail_next_valid_replacement() noexcept {
            m_fail_next_valid_replacement = true;
        }

        void upsert_manifest(agent_memory::ResourceManifest manifest) override {
            if(!agent_memory::is_valid_resource_manifest(manifest)) {
                throw std::invalid_argument("invalid resource manifest");
            }

            if(m_fail_next_valid_replacement) {
                m_fail_next_valid_replacement = false;
                throw std::runtime_error("simulated valid replacement failure");
            }

            const agent_memory::ResourceId resource_id = manifest.revision.resource_id;
            m_manifests[resource_id] = std::move(manifest);
        }

        [[nodiscard]] std::optional<agent_memory::ResourceManifest> find_manifest(
            const agent_memory::ResourceId& resource_id
        ) const override {
            const auto it = m_manifests.find(resource_id);
            if(it == m_manifests.end()) {
                return std::nullopt;
            }
            return it->second;
        }

        [[nodiscard]] bool erase_manifest(
            const agent_memory::ResourceId& resource_id
        ) override {
            return m_manifests.erase(resource_id) > 0;
        }

    private:
        bool m_fail_next_valid_replacement = false;
        std::map<agent_memory::ResourceId, agent_memory::ResourceManifest> m_manifests;
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
                    agent_memory::ChunkId{"chunk:resource:0"},
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

    bool rejects_manifest(
        InMemoryResourceManifestStorage& storage,
        agent_memory::ResourceManifest manifest
    ) {
        try {
            storage.upsert_manifest(std::move(manifest));
        } catch(const std::invalid_argument&) {
            return true;
        }

        return false;
    }

} // namespace

int main() {
    InMemoryResourceManifestStorage storage;
    const agent_memory::ResourceId resource_id{"resource:contract"};

    storage.upsert_manifest(make_manifest(resource_id, 1, "bucket:24:alpha"));

    const auto stored = storage.find_manifest(resource_id);
    if(!stored) {
        return fail("manifest storage must return inserted manifest");
    }

    if(stored->revision.generation != 1 || stored->records.size() != 2) {
        return fail("manifest storage must retain revision and records");
    }

    storage.upsert_manifest(make_manifest(resource_id, 2, "bucket:24:beta"));

    const auto replaced = storage.find_manifest(resource_id);
    if(
        !replaced ||
        replaced->revision.generation != 2 ||
        replaced->records.size() != 2 ||
        replaced->records[1].key != "bucket:24:beta"
    ) {
        return fail("upsert must replace manifests by resource id");
    }

    if(!agent_memory::is_valid_resource_manifest(*replaced)) {
        return fail("stored manifest must satisfy strict validation");
    }

    storage.fail_next_valid_replacement();
    try {
        storage.upsert_manifest(make_manifest(resource_id, 3, "bucket:24:failed"));
        return fail("storage must surface a valid replacement failure");
    } catch(const std::runtime_error&) {
    }

    const auto after_valid_replacement_failure = storage.find_manifest(resource_id);
    if(
        !after_valid_replacement_failure
        || after_valid_replacement_failure->revision.generation != 2
        || after_valid_replacement_failure->records[1].key != "bucket:24:beta"
    ) {
        return fail("valid replacement failure must preserve the visible manifest");
    }

    auto invalid_empty_resource = make_manifest({}, 3, "bucket:24:invalid");
    if(agent_memory::is_valid_resource_manifest(invalid_empty_resource)) {
        return fail("manifest with empty resource id must be invalid");
    }

    if(!rejects_manifest(storage, std::move(invalid_empty_resource))) {
        return fail("storage must reject manifest with empty resource id");
    }

    auto invalid_extra_key = make_manifest(
        resource_id,
        4,
        "bucket:24:invalid"
    );
    invalid_extra_key.records.front().key = "unexpected";

    if(agent_memory::has_required_reference(invalid_extra_key.records.front())) {
        if(agent_memory::is_valid_derived_record_ref(invalid_extra_key.records.front())) {
            return fail("chunk ref with extra key must be invalid");
        }
    } else {
        return fail("chunk ref with chunk id must still have required reference");
    }

    if(!rejects_manifest(storage, std::move(invalid_extra_key))) {
        return fail("storage must reject manifest with extra ref fields");
    }

    const auto after_rejected_replacement = storage.find_manifest(resource_id);
    if(
        !after_rejected_replacement
        || after_rejected_replacement->revision.generation != 2
        || after_rejected_replacement->records[1].key != "bucket:24:beta"
    ) {
        return fail("throwing manifest replacement must preserve the visible manifest");
    }

    auto invalid_missing_key = make_manifest(
        agent_memory::ResourceId{"resource:invalid-bucket"},
        5,
        {}
    );

    if(agent_memory::has_required_reference(invalid_missing_key.records[1])) {
        return fail("bucket ref without key must miss required reference");
    }

    if(!rejects_manifest(storage, std::move(invalid_missing_key))) {
        return fail("storage must reject manifest with missing key");
    }

    if(!storage.erase_manifest(resource_id)) {
        return fail("erase must report removed manifest");
    }

    if(storage.find_manifest(resource_id)) {
        return fail("erase must remove manifest state");
    }

    if(storage.erase_manifest(resource_id)) {
        return fail("erase of missing manifest must report false");
    }

    return 0;
}
