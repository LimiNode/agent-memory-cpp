#include <agent_memory/infrastructure/mdbx/MdbxResourceFrontierSnapshot.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <atomic>
#include <filesystem>
#include <future>
#include <iostream>
#include <memory>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

using namespace agent_memory;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

class TestDatabase final {
public:
    static TestDatabase create() {
        const auto root = std::filesystem::temp_directory_path();
        const auto nonce = static_cast<std::uint64_t>(
            std::chrono::high_resolution_clock::now().time_since_epoch().count())
            ^ static_cast<std::uint64_t>(std::random_device{}());
        for(std::uint64_t attempt = 0; attempt < 64; ++attempt) {
            const auto directory = root / ("agent_memory_frontier_snapshot_"
                + std::to_string(nonce) + "_" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(directory, error)) {
                return TestDatabase(directory, directory / "frontier.mdbx",
                    std::filesystem::path{(directory / "frontier.mdbx").string() + "-lck"});
            }
            if(error && error != std::make_error_code(std::errc::file_exists)) {
                throw std::runtime_error("MDBX fixture directory creation failed");
            }
        }
        throw std::runtime_error("MDBX fixture directory reservation failed");
    }

    TestDatabase(const TestDatabase&) = delete;
    TestDatabase& operator=(const TestDatabase&) = delete;
    TestDatabase(TestDatabase&& other) noexcept
        : directory(std::move(other.directory)), path_value(std::move(other.path_value)),
          lock_path(std::move(other.lock_path)), cleaned(other.cleaned) {
        other.cleaned = true;
    }
    ~TestDatabase() { if(!cleaned) cleanup_noexcept(); }

    [[nodiscard]] const std::filesystem::path& path() const noexcept { return path_value; }

    void cleanup_or_throw() {
        remove_checked(path_value);
        remove_checked(lock_path);
        remove_checked(directory);
        cleaned = true;
    }

    static void remove_checked(const std::filesystem::path& path) {
        if(!std::filesystem::exists(path)) return;
        std::error_code error;
        const auto removed = std::filesystem::remove(path, error);
        expect(removed && !error && !std::filesystem::exists(path),
               "MDBX fixture cleanup failed");
    }

private:
    TestDatabase(std::filesystem::path directory_value,
                 std::filesystem::path path,
                 std::filesystem::path lock)
        : directory(std::move(directory_value)), path_value(std::move(path)),
          lock_path(std::move(lock)) {}

    void cleanup_noexcept() noexcept {
        std::error_code ignored;
        std::filesystem::remove(path_value, ignored);
        ignored.clear();
        std::filesystem::remove(lock_path, ignored);
        ignored.clear();
        std::filesystem::remove(directory, ignored);
    }

    std::filesystem::path directory;
    std::filesystem::path path_value;
    std::filesystem::path lock_path;
    bool cleaned = false;
};

class ExternalSentinel final {
public:
    static ExternalSentinel create() {
        const auto root = std::filesystem::temp_directory_path();
        const auto nonce = static_cast<std::uint64_t>(
            std::chrono::high_resolution_clock::now().time_since_epoch().count())
            ^ static_cast<std::uint64_t>(std::random_device{}());
        for(std::uint64_t attempt = 0; attempt < 64; ++attempt) {
            const auto path = root / ("agent_memory_frontier_snapshot_sentinel_"
                + std::to_string(nonce) + "_" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(path, error)) return ExternalSentinel(path);
            if(error && error != std::make_error_code(std::errc::file_exists)) {
                throw std::runtime_error("sentinel creation failed");
            }
        }
        throw std::runtime_error("sentinel reservation failed");
    }

    ExternalSentinel(const ExternalSentinel&) = delete;
    ExternalSentinel& operator=(const ExternalSentinel&) = delete;
    ExternalSentinel(ExternalSentinel&& other) noexcept
        : path_value(std::move(other.path_value)), cleaned(other.cleaned) {
        other.cleaned = true;
    }
    ~ExternalSentinel() {
        if(!cleaned) {
            std::error_code ignored;
            std::filesystem::remove(path_value, ignored);
        }
    }
    void verify_and_cleanup() {
        expect(std::filesystem::is_directory(path_value), "external sentinel disappeared");
        TestDatabase::remove_checked(path_value);
        cleaned = true;
    }

private:
    explicit ExternalSentinel(std::filesystem::path path) : path_value(std::move(path)) {}
    std::filesystem::path path_value;
    bool cleaned = false;
};

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
        auto sentinel = ExternalSentinel::create();
        auto database = TestDatabase::create();
        const ScopeId scope{"frontier-scope"};
        const ResourceId resource{"root-A"};
        const ResourceId second_resource{"root-B"};
        auto context = MdbxStorageContext::open(database.path().string());
        auto manifests = std::make_unique<MdbxResourceManifestStorage>(
            MdbxResourceManifestStorageOptions{"", "agent_memory", false,
                                               kDefaultMdbxMaxDbs, context});
        auto units = std::make_unique<MdbxKnowledgeUnitStore>(
            MdbxKnowledgeUnitStoreOptions{"", "agent_memory", false,
                                          kDefaultMdbxMaxDbs, context});
        manifests->upsert_manifest(manifest(resource, 1, 7));
        manifests->upsert_manifest(manifest(second_resource, 1, 8));
        const auto stored = units->create_or_get_unit(request(scope, resource, 1, 7));
        const auto second = units->create_or_get_unit(
            request(scope, second_resource, 1, 8));

        auto reader = std::make_unique<MdbxResourceFrontierSnapshotReader>(context);
        const auto empty = reader->read(scope, {});
        expect(empty.status == MdbxResourceFrontierSnapshotStatus::Unavailable,
               "empty frontier input was accepted");
        const auto duplicate = reader->read(scope, {
            input(stored, resource, 1, 7), input(stored, resource, 1, 7)
        });
        expect(duplicate.status == MdbxResourceFrontierSnapshotStatus::Unavailable,
               "duplicate frontier input was accepted");
        std::vector<MdbxResourceFrontierInput> oversized_inputs(
            kMdbxResourceFrontierMaxInputs + 1, input(stored, resource, 1, 7));
        const auto oversized = reader->read(scope, oversized_inputs);
        expect(oversized.status == MdbxResourceFrontierSnapshotStatus::Unavailable,
               "oversized frontier input was accepted");

        auto first = reader->read(scope, {
            input(stored, resource, 1, 7), input(second, second_resource, 1, 8)
        });
        expect(first.status == MdbxResourceFrontierSnapshotStatus::Available,
               "multi-input root frontier was not available");
        expect(first.snapshot && first.snapshot->entries.size() == 2,
               "frontier snapshot did not retain its input");
        expect(first.snapshot->read_transaction_owner
                   && first.snapshot->read_transaction_owner->active(),
               "frontier snapshot did not retain its read transaction");

        const auto pinned = first.snapshot->entries.front().root;
        // Release the read pin before exercising a subsequent writer. The
        // materialized entry remains a stable historical value.
        first.snapshot.reset();

        std::promise<void> first_read;
        auto first_read_ready = first_read.get_future().share();
        std::promise<void> writer_finished;
        auto writer_finished_ready = writer_finished.get_future().share();
        std::atomic<bool> writer_failed{false};
        reader->set_read_hook_for_testing([&](std::size_t index) {
            if(index != 0) return;
            first_read.set_value();
            writer_finished_ready.wait();
        });
        std::promise<bool> coherent;
        auto coherent_ready = coherent.get_future();
        std::thread reader_thread([&] {
            const auto result = reader->read(scope, {
                input(stored, resource, 1, 7), input(second, second_resource, 1, 8)
            });
            coherent.set_value(
                result.status == MdbxResourceFrontierSnapshotStatus::Available
                && result.snapshot
                && result.snapshot->entries.size() == 2
                && result.snapshot->entries[1].root.revision.generation == 1);
        });
        first_read_ready.wait();
        std::thread writer([&] {
            try {
                MdbxResourceManifestStorage writer_manifests(
                    MdbxResourceManifestStorageOptions{"", "agent_memory", false,
                                                       kDefaultMdbxMaxDbs, context});
                writer_manifests.upsert_manifest(manifest(second_resource, 2, 9));
            } catch(...) {
                writer_failed = true;
            }
            writer_finished.set_value();
        });
        reader_thread.join();
        writer.join();
        expect(!writer_failed.load(), "concurrent manifest update failed");
        expect(coherent_ready.get(),
               "frontier read observed a mixed root revision across one transaction");
        reader->set_read_hook_for_testing({});
        const auto post_writer = reader->read(
            scope, {input(second, second_resource, 1, 8)});
        expect(post_writer.status == MdbxResourceFrontierSnapshotStatus::RootRevisionMismatch,
               "frontier read did not observe the committed writer revision");

        const auto updated = units->update_mutable_fields(
            stored.id, KnowledgeUnitMutablePatch{
                std::string{"frontier unit v2"}, std::nullopt, std::nullopt,
                std::nullopt, std::nullopt});
        const auto changed_unit = reader->read(scope, {input(stored, resource, 1, 7)});
        expect(changed_unit.status == MdbxResourceFrontierSnapshotStatus::UnitRevisionMismatch,
               "unit mutation with an unchanged root was accepted");

        manifests->upsert_manifest(manifest(resource, 2, 8));
        expect(pinned.revision.generation == 1,
               "pinned snapshot observed a later root revision");
        const auto stale = reader->read(scope, {input(updated, resource, 1, 7)});
        expect(stale.status == MdbxResourceFrontierSnapshotStatus::RootRevisionMismatch,
               "stale root revision was accepted");

        manifests->upsert_manifest(manifest(second_resource, 1, 9));
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

        static_cast<void>(units->update_mutable_fields(updated.id, KnowledgeUnitMutablePatch{
            std::nullopt, std::nullopt, KnowledgeUnitLifecycleState::Superseded,
            std::nullopt, std::nullopt}));
        const auto inactive = reader->read(scope, {input(updated, resource, 1, 7)});
        expect(inactive.status == MdbxResourceFrontierSnapshotStatus::InactiveInput,
               "inactive input was accepted");

        const auto missing_root = units->create_or_get_unit(
            request(scope, ResourceId{"root-missing"}, 1, 9));
        const auto unavailable = reader->read(
            scope, {input(missing_root, ResourceId{"root-missing"}, 1, 9)});
        expect(unavailable.status == MdbxResourceFrontierSnapshotStatus::Unavailable,
               "missing root was not fail-closed");

        reader.reset();
        units.reset();
        manifests.reset();
        context.reset();
        database.cleanup_or_throw();
        sentinel.verify_and_cleanup();
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

#else
int main() { return 0; }
#endif
