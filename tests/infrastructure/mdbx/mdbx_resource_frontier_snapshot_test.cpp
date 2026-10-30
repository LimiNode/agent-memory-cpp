#include <agent_memory/infrastructure/mdbx/MdbxResourceFrontierSnapshot.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

namespace {

using namespace agent_memory;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path test_path() {
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path() /
        ("agent_memory_resource_frontier_snapshot_" + std::to_string(stamp) + ".mdbx");
}

ResourceBodyDigest digest(std::uint8_t seed) {
    ResourceBodyDigest value;
    value.bytes.fill(seed);
    return value;
}

SourceRefSummary source(const ResourceId& id, std::uint64_t generation, std::uint8_t seed) {
    SourceRefSummary value;
    value.resource_id = id;
    value.resource_revision = ResourceRevisionRef{id, generation, digest(seed)};
    value.uri = "memory://frontier";
    value.excerpt = TextRange{0, 4};
    value.preview = "root-bound text";
    value.quote_hash = compute_quote_hash(value.preview);
    value.confidence = 1.0;
    return value;
}

KnowledgeUnitCreateRequest request(
    const ScopeId& scope,
    const ResourceId& resource,
    std::uint64_t generation,
    std::uint8_t seed
) {
    KnowledgeUnitCreateRequest value;
    value.kind = KnowledgeUnitKind::Chunk;
    value.scope_id = scope;
    value.primary_text = "frontier unit";
    value.display_text = value.primary_text;
    value.sources.push_back(source(resource, generation, seed));
    value.content_hash_recipe_version = 2;
    value.content_hash = compute_chunk_content_hash(
        value.scope_id, value.primary_text, value.sources, 2);
    value.created_at_ms = 1;
    value.observed_at_ms = 2;
    return value;
}

ResourceManifest manifest(const ResourceId& id, std::uint64_t generation, std::uint8_t seed) {
    ResourceManifest value;
    value.revision.resource_id = id;
    value.revision.generation = generation;
    value.revision.body_digest = digest(seed);
    value.payload_version = ResourceManifestPayloadVersion::V6;
    return value;
}

MdbxResourceFrontierInput input(
    const KnowledgeUnitEnvelope& unit,
    const ResourceId& resource,
    std::uint64_t generation,
    std::uint8_t seed
) {
    return MdbxResourceFrontierInput{
        unit.id,
        unit.revision,
        ResourceRevisionRef{resource, generation, digest(seed)}
    };
}

} // namespace

int main() {
    try {
        const auto path = test_path();
        const ScopeId scope{"frontier-scope"};
        const ResourceId resource{"root-A"};
        const ResourceId second_resource{"root-B"};
        auto context = MdbxStorageContext::open(path.string());
        MdbxResourceManifestStorage manifests({"", "agent_memory", false,
                                               kDefaultMdbxMaxDbs, context});
        MdbxKnowledgeUnitStore units({"", "agent_memory", false,
                                      kDefaultMdbxMaxDbs, context});
        manifests.upsert_manifest(manifest(resource, 1, 7));
        manifests.upsert_manifest(manifest(second_resource, 1, 8));
        const auto stored = units.create_or_get_unit(request(scope, resource, 1, 7));
        const auto second = units.create_or_get_unit(
            request(scope, second_resource, 1, 8));

        auto reader = std::make_unique<MdbxResourceFrontierSnapshotReader>(context);
        auto first = reader->read(scope, {
            input(stored, resource, 1, 7), input(second, second_resource, 1, 8)
        });
        expect(first.status == MdbxResourceFrontierSnapshotStatus::Available,
               "multi-input root frontier was not available");
        expect(first.snapshot && first.snapshot->entries.size() == 2,
               "frontier snapshot did not retain its input");
        expect(static_cast<bool>(first.snapshot->read_transaction_owner),
               "frontier snapshot did not retain its read transaction");

        const auto pinned = first.snapshot->entries.front().root;
        // Release the read pin before exercising a subsequent writer. The
        // materialized entry remains a stable historical value.
        first.snapshot.reset();

        const auto updated = units.update_mutable_fields(
            stored.id, KnowledgeUnitMutablePatch{
                std::string{"frontier unit v2"}, std::nullopt, std::nullopt,
                std::nullopt, std::nullopt});
        const auto changed_unit = reader->read(scope, {input(stored, resource, 1, 7)});
        expect(changed_unit.status == MdbxResourceFrontierSnapshotStatus::UnitRevisionMismatch,
               "unit mutation with an unchanged root was accepted");

        manifests.upsert_manifest(manifest(resource, 2, 8));
        expect(pinned.revision.generation == 1,
               "pinned snapshot observed a later root revision");
        const auto stale = reader->read(scope, {input(updated, resource, 1, 7)});
        expect(stale.status == MdbxResourceFrontierSnapshotStatus::RootRevisionMismatch,
               "stale root revision was accepted");

        manifests.upsert_manifest(manifest(second_resource, 1, 9));
        const auto bad_digest = reader->read(
            scope, {input(second, second_resource, 1, 8)});
        expect(bad_digest.status == MdbxResourceFrontierSnapshotStatus::RootDigestMismatch,
               "root digest mismatch was accepted");

        const auto wrong_scope = reader->read(ScopeId{"other"}, {input(stored, resource, 1, 7)});
        expect(wrong_scope.status == MdbxResourceFrontierSnapshotStatus::ScopeMismatch,
               "cross-scope input was accepted");
        const auto wrong_unit_revision = reader->read(
            scope, {MdbxResourceFrontierInput{stored.id, updated.revision + 1,
                                               ResourceRevisionRef{resource, 1, digest(7)}}});
        expect(wrong_unit_revision.status == MdbxResourceFrontierSnapshotStatus::UnitRevisionMismatch,
               "unit revision mismatch was accepted");

        static_cast<void>(units.update_mutable_fields(updated.id, KnowledgeUnitMutablePatch{
            std::nullopt, std::nullopt, KnowledgeUnitLifecycleState::Superseded,
            std::nullopt, std::nullopt}));
        const auto inactive = reader->read(scope, {input(updated, resource, 1, 7)});
        expect(inactive.status == MdbxResourceFrontierSnapshotStatus::InactiveInput,
               "inactive input was accepted");

        const auto missing_root = units.create_or_get_unit(
            request(scope, ResourceId{"root-missing"}, 1, 9));
        const auto unavailable = reader->read(
            scope, {input(missing_root, ResourceId{"root-missing"}, 1, 9)});
        expect(unavailable.status == MdbxResourceFrontierSnapshotStatus::Unavailable,
               "missing root was not fail-closed");

        reader.reset();
        context.reset();
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        std::filesystem::remove(std::filesystem::path{path.string() + "-lck"}, ignored);
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

#else
int main() { return 0; }
#endif
