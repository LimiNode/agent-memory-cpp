#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>

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

#ifndef AGENT_MEMORY_HAS_MDBX
#    error "AGENT_MEMORY_HAS_MDBX must be defined by the agent_memory target"
#endif

#if !AGENT_MEMORY_HAS_MDBX
#    error "Resource frontier fencing test requires AGENT_MEMORY_ENABLE_MDBX=ON"
#endif

namespace {

int fail(std::string_view message) {
    std::cerr << message << '\n';
    return 1;
}

std::filesystem::path test_path() {
    const auto now = std::chrono::steady_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path() /
        ("agent_memory_mdbx_resource_frontier_fencing_" +
         std::to_string(now) + "_" + std::to_string(std::random_device{}()) + ".mdbx");
}

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

} // namespace

int main() {
    using namespace agent_memory;

    const auto path = test_path();
    std::error_code error;
    std::filesystem::remove(path, error);

    const std::string guard_table = "agent_memory_frontier_fixture_guard";
    const std::string token_table = "agent_memory_frontier_fixture_tokens";
    const std::string key = "resource-A";
    auto context = MdbxStorageContext::open(path.string());
    {
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

    // Simulate A@1 -> A@2 after preparation by advancing the durable fence.
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
    if(!rejected) {
        return fail("stale root frontier token was accepted");
    }
    const auto after_rejection = manifests.find_manifest(ResourceId{"resource-A"});
    if(!after_rejection || after_rejection->revision.generation != 1) {
        return fail("stale fenced update changed the visible root manifest");
    }

    // A retry prepared against the new frontier may publish A@2.
    manifests.upsert_manifest_if_unchanged_guarded(
        make_manifest(2), after_rejection,
        MdbxManifestPublicationGuard{
            guard_table, key, "", token_table, "token-2"
        }
    );
    const auto current = manifests.find_manifest(ResourceId{"resource-A"});
    if(!current || current->revision.generation != 2) {
        return fail("frontier retry did not publish the new root revision");
    }
    }

    context.reset();
    std::filesystem::remove(path, error);
    if(error || std::filesystem::exists(path)) {
        return fail("resource frontier fencing fixture database cleanup failed");
    }
    return 0;
}
