#include <agent_memory/infrastructure/mdbx/MdbxDerivedPublicationStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxCurrentDerivedPublicationReader.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceFrontierSnapshot.hpp>
#include <agent_memory/domain/Sha256.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

void make_removable(const std::filesystem::path& path) noexcept {
#ifdef _WIN32
    std::error_code error;
    if(std::filesystem::exists(path, error) && !error) {
        SetFileAttributesW(path.wstring().c_str(), FILE_ATTRIBUTE_NORMAL);
    }
#else
    static_cast<void>(path);
#endif
}

class TestDatabase final {
public:
    static TestDatabase create(std::string_view prefix) {
        const auto root = std::filesystem::temp_directory_path();
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        for(std::uint64_t attempt = 0; attempt < 64; ++attempt) {
            const auto directory = root /
                (std::string{prefix} + "_" + std::to_string(nonce) + "_" +
                 std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(directory, error)) {
                return TestDatabase(directory, directory / "database.mdbx");
            }
            if(error && error != std::make_error_code(std::errc::file_exists)) {
                throw std::runtime_error("MDBX test directory creation failed: " +
                                         error.message());
            }
        }
        throw std::runtime_error("MDBX test directory reservation failed");
    }

    TestDatabase(const TestDatabase&) = delete;
    TestDatabase& operator=(const TestDatabase&) = delete;
    TestDatabase(TestDatabase&&) = delete;
    TestDatabase& operator=(TestDatabase&&) = delete;

    ~TestDatabase() {
        std::error_code ignored;
        cleanup(ignored);
    }

    [[nodiscard]] const std::filesystem::path& path() const noexcept { return m_path; }

    void cleanup_or_fail() {
        std::error_code error;
        cleanup(error);
        expect(!error && m_directory.empty(), "MDBX test database cleanup failed");
    }

private:
    TestDatabase(std::filesystem::path directory, std::filesystem::path path)
        : m_directory(std::move(directory)),
          m_path(std::move(path)),
          m_lock_path(std::filesystem::path{m_path.string() + "-lck"}) {}

    void cleanup(std::error_code& result) noexcept {
        if(m_directory.empty()) return;
        result.clear();
        std::filesystem::remove(m_path, result);
        if(result) return;
        result.clear();
        make_removable(m_lock_path);
        std::filesystem::remove(m_lock_path, result);
        if(result) return;
        result.clear();
        std::filesystem::remove(m_directory, result);
        if(!result) {
            m_directory.clear();
            m_path.clear();
            m_lock_path.clear();
        }
    }

    std::filesystem::path m_directory;
    std::filesystem::path m_path;
    std::filesystem::path m_lock_path;
};

agent_memory::DerivedPublicationRequest fixture_request(
    std::string body = "The answer is 42.",
    std::uint64_t revision = 1,
    std::string frontier = "question@1;answer@1"
) {
    using namespace agent_memory;
    DerivedPublicationRequest request;
    request.scope_id = ScopeId{"workspace-a"};
    request.publication_id = "forum-qa-42";
    request.kind = ProjectionKind::QACombined;
    request.output_revision = revision;
    request.body = std::move(body);
    request.generation = revision;
    request.dependency_frontier = std::move(frontier);
    request.inputs = {
        {KnowledgeUnitId{101}, 1, 7, "question-digest-v1"},
        {KnowledgeUnitId{102}, 1, 7, "answer-digest-v1"}
    };
    return request;
}

std::string body_key(std::string_view id) {
    return "derived_publication:" + std::to_string(id.size()) + ":" + std::string{id} + "b";
}

std::string field_key(std::string_view id, char field) {
    auto key = body_key(id);
    key.back() = field;
    return key;
}

std::string marker_for(std::string_view manifest, std::string_view body) {
    std::string marker;
    const auto append_string = [&marker](std::string_view value) {
        marker += std::to_string(value.size());
        marker.push_back(':');
        marker.append(value.data(), value.size());
    };
    const auto append_digest = [&marker](const auto& digest) {
        marker.append(reinterpret_cast<const char*>(digest.data()), digest.size());
    };
    append_string("agent_memory.derived_publication.v1");
    marker.push_back(static_cast<char>(1));
    append_digest(agent_memory::sha256(manifest));
    append_digest(agent_memory::sha256(body));
    return marker;
}

agent_memory::ResourceBodyDigest frontier_digest(std::uint8_t seed) {
    agent_memory::ResourceBodyDigest value;
    value.bytes.fill(seed);
    return value;
}

agent_memory::SourceRefSummary frontier_source(
    const agent_memory::ResourceId& resource,
    std::uint64_t generation,
    std::uint8_t seed
) {
    using namespace agent_memory;
    SourceRefSummary value;
    value.resource_id = resource;
    value.resource_revision = ResourceRevisionRef{resource, generation, frontier_digest(seed)};
    value.uri = "memory://fenced-publication";
    value.excerpt = TextRange{0, 4};
    value.preview = "fenced unit";
    value.quote_hash = compute_quote_hash(value.preview);
    value.confidence = 1.0;
    return value;
}

agent_memory::KnowledgeUnitCreateRequest frontier_unit_request(
    const agent_memory::ScopeId& scope,
    const agent_memory::ResourceId& resource,
    std::uint64_t generation,
    std::uint8_t seed
) {
    using namespace agent_memory;
    KnowledgeUnitCreateRequest value;
    value.kind = KnowledgeUnitKind::Chunk;
    value.scope_id = scope;
    value.primary_text = "fenced publication unit";
    value.display_text = value.primary_text;
    value.sources.push_back(frontier_source(resource, generation, seed));
    value.content_hash_recipe_version = 2;
    value.content_hash = compute_chunk_content_hash(
        value.scope_id, value.primary_text, value.sources, 2);
    value.created_at_ms = 1;
    value.observed_at_ms = 2;
    return value;
}

agent_memory::ResourceManifest frontier_manifest(
    const agent_memory::ResourceId& resource,
    std::uint64_t generation,
    std::uint8_t seed
) {
    agent_memory::ResourceManifest value;
    value.revision.resource_id = resource;
    value.revision.generation = generation;
    value.revision.body_digest = frontier_digest(seed);
    value.payload_version = agent_memory::ResourceManifestPayloadVersion::V6;
    return value;
}

void write_field(const std::shared_ptr<agent_memory::MdbxStorageContext>& context,
                 std::string_view publication_id, char field, std::string value) {
    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), "agent_memory_unit_projections");
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    table.insert_or_assign(field_key(publication_id, field), std::move(value), txn);
    txn.commit();
}

std::optional<std::string> read_field(
    const std::shared_ptr<agent_memory::MdbxStorageContext>& context,
    std::string_view publication_id,
    char field
) {
    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), "agent_memory_unit_projections");
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto value = table.find(field_key(publication_id, field), txn);
    txn.commit();
    return value;
}

void expect_invalid_publish(agent_memory::MdbxDerivedPublicationStore& store,
                            agent_memory::DerivedPublicationRequest request,
                            const char* message) {
    bool rejected = false;
    try { store.publish(std::move(request)); }
    catch(const std::invalid_argument&) { rejected = true; }
    expect(rejected, message);
}

} // namespace

int main() {
    using namespace agent_memory;
    auto database = TestDatabase::create("agent_memory_derived_publication_store");
    const auto& path = database.path();

    const auto request = fixture_request();
    {
        auto context = MdbxStorageContext::open(path.string());
        MdbxDerivedPublicationStore store({"", "agent_memory", false,
                                           kDefaultMdbxMaxDbs, context});
        store.publish(request);
        store.publish(request); // exact replay is idempotent.

        const auto active = store.read(
            request.scope_id, request.publication_id, request.inputs, request.dependency_frontier);
        expect(active.status == DerivedPublicationReadStatus::StoredMatch && active.publication &&
                   active.publication->request.body == request.body,
               "published QA projection did not produce a storage match");

        auto stale_inputs = request.inputs;
        stale_inputs[1].unit_revision = 2;
        const auto stale = store.read(
            request.scope_id, request.publication_id, stale_inputs, request.dependency_frontier);
        expect(stale.status == DerivedPublicationReadStatus::StoredMismatch && !stale.publication,
               "changed source revision was not reported as a stored mismatch");

        const auto stale_frontier = store.read(
            request.scope_id, request.publication_id, request.inputs, "question@2;answer@1");
        expect(stale_frontier.status == DerivedPublicationReadStatus::StoredMismatch,
               "changed dependency frontier was not reported as a stored mismatch");

        const auto denied = store.read(
            ScopeId{"workspace-b"}, request.publication_id, request.inputs, request.dependency_frontier);
        expect(denied.status == DerivedPublicationReadStatus::ScopeMismatch,
               "cross-scope publication was admitted");

        bool conflict = false;
        try { store.publish(fixture_request("different answer")); }
        catch(const std::invalid_argument&) { conflict = true; }
        expect(conflict, "conflicting publication replay was accepted");

        const auto original = fixture_request("Original question and answer evidence.");
        MdbxProjectionStore projections({"", "agent_memory", false,
                                         kDefaultMdbxMaxDbs, context});
        SearchProjection evidence;
        evidence.scope_id = request.scope_id;
        evidence.unit_id = request.inputs.front().unit_id;
        evidence.kind = ProjectionKind::Original;
        evidence.version = {1, 1, 1, "m0.original.primary_text.v1"};
        evidence.body = original.body;
        projections.put(evidence);
        const auto evidence_rows = projections.list(
            request.scope_id, request.inputs.front().unit_id, ProjectionKind::Original);
        expect(evidence_rows.size() == 1 && evidence_rows.front().body == original.body,
               "original evidence was not independently retrievable");

        const auto missing = store.read(
            request.scope_id, "missing", request.inputs, request.dependency_frontier);
        expect(missing.status == DerivedPublicationReadStatus::NotFound,
               "missing publication was not reported as absent");

        auto oversized_body = fixture_request();
        oversized_body.publication_id = "oversized-body";
        oversized_body.body.assign(16U * 1024U * 1024U + 1U, 'x');
        expect_invalid_publish(store, std::move(oversized_body),
                               "oversized body was accepted");

        auto oversized_frontier = fixture_request();
        oversized_frontier.publication_id = "oversized-frontier";
        oversized_frontier.dependency_frontier.assign(1024U * 1024U + 1U, 'x');
        expect_invalid_publish(store, std::move(oversized_frontier),
                               "oversized dependency frontier was accepted");

        auto oversized_digest = fixture_request();
        oversized_digest.publication_id = "oversized-digest";
        oversized_digest.inputs.front().source_digest.assign(4096U + 1U, 'x');
        expect_invalid_publish(store, std::move(oversized_digest),
                               "oversized source digest was accepted");

        auto oversized_manifest = fixture_request();
        oversized_manifest.publication_id = "oversized-manifest";
        oversized_manifest.inputs.clear();
        oversized_manifest.inputs.reserve(5000);
        for(std::uint64_t id = 0; id < 5000; ++id)
            oversized_manifest.inputs.push_back(
                {KnowledgeUnitId{1000 + id}, 1, 7, std::string(4096, 'd')});
        expect_invalid_publish(store, std::move(oversized_manifest),
                               "oversized manifest was accepted");

        auto oversized_id = fixture_request();
        oversized_id.publication_id.assign(257, 'x');
        expect_invalid_publish(store, std::move(oversized_id),
                               "oversized publication id was accepted");

        const auto corrupt_body = fixture_request("corrupt body");
        auto corrupt_body_request = corrupt_body;
        corrupt_body_request.publication_id = "corrupt-body";
        store.publish(corrupt_body_request);
        write_field(context, corrupt_body_request.publication_id, 'b', "tampered body");
        expect(store.read(corrupt_body_request.scope_id, corrupt_body_request.publication_id,
                          corrupt_body_request.inputs, corrupt_body_request.dependency_frontier).status ==
                   DerivedPublicationReadStatus::Unavailable,
               "corrupted body was not rejected");

        auto corrupt_manifest_request = fixture_request("corrupt manifest");
        corrupt_manifest_request.publication_id = "corrupt-manifest";
        store.publish(corrupt_manifest_request);
        write_field(context, corrupt_manifest_request.publication_id, 'm', "not-a-manifest");
        expect(store.read(corrupt_manifest_request.scope_id, corrupt_manifest_request.publication_id,
                          corrupt_manifest_request.inputs, corrupt_manifest_request.dependency_frontier).status ==
                   DerivedPublicationReadStatus::Unavailable,
               "corrupted manifest was not rejected");

        auto corrupt_marker_request = fixture_request("corrupt marker");
        corrupt_marker_request.publication_id = "corrupt-marker";
        store.publish(corrupt_marker_request);
        write_field(context, corrupt_marker_request.publication_id, 'a', "not-a-marker");
        expect(store.read(corrupt_marker_request.scope_id, corrupt_marker_request.publication_id,
                          corrupt_marker_request.inputs, corrupt_marker_request.dependency_frontier).status ==
                   DerivedPublicationReadStatus::Unavailable,
               "corrupted marker was not rejected");

        auto trailing_request = fixture_request("trailing manifest bytes");
        trailing_request.publication_id = "trailing-manifest";
        store.publish(trailing_request);
        mdbxc::KeyValueTable<std::string, std::string> corruption_table(
            context->connection(), "agent_memory_unit_projections");
        auto read_value = [&corruption_table, &context](const std::string& key) {
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
            const auto value = corruption_table.find(key, txn);
            txn.commit();
            return value.value();
        };
        const auto trailing_manifest = read_value(field_key(trailing_request.publication_id, 'm')) + "x";
        write_field(context, trailing_request.publication_id, 'm', trailing_manifest);
        write_field(context, trailing_request.publication_id, 'a',
                    marker_for(trailing_manifest, trailing_request.body));
        expect(store.read(trailing_request.scope_id, trailing_request.publication_id,
                          trailing_request.inputs, trailing_request.dependency_frontier).status ==
                   DerivedPublicationReadStatus::Unavailable,
               "manifest trailing bytes were accepted");

        auto malformed_request = fixture_request("malformed manifest");
        malformed_request.publication_id = "malformed-manifest";
        store.publish(malformed_request);
        write_field(context, malformed_request.publication_id, 'm', "1:x");
        write_field(context, malformed_request.publication_id, 'a', marker_for("1:x", malformed_request.body));
        expect(store.read(malformed_request.scope_id, malformed_request.publication_id,
                          malformed_request.inputs, malformed_request.dependency_frontier).status ==
                   DerivedPublicationReadStatus::Unavailable,
               "malformed manifest was accepted");

        MdbxResourceManifestStorage manifests({"", "agent_memory", false,
                                               kDefaultMdbxMaxDbs, context});
        MdbxKnowledgeUnitStore units({"", "agent_memory", false,
                                      kDefaultMdbxMaxDbs, context});
        const ScopeId fenced_scope{"fenced-scope"};
        const ResourceId fenced_resource_a{"fenced|root:@a"};
        const ResourceId fenced_resource_b{"fenced|root:@b"};
        manifests.upsert_manifest(frontier_manifest(fenced_resource_a, 1, 21));
        manifests.upsert_manifest(frontier_manifest(fenced_resource_b, 1, 22));
        const auto fenced_unit_a = units.create_or_get_unit(
            frontier_unit_request(fenced_scope, fenced_resource_a, 1, 21));
        const auto fenced_unit_b = units.create_or_get_unit(
            frontier_unit_request(fenced_scope, fenced_resource_b, 1, 22));
        MdbxResourceFrontierSnapshotReader frontier_reader(context);
        auto frontier_result = frontier_reader.read(fenced_scope, {
            {fenced_unit_a.id, fenced_unit_a.revision,
             ResourceRevisionRef{fenced_resource_a, 1, frontier_digest(21)}},
            {fenced_unit_b.id, fenced_unit_b.revision,
             ResourceRevisionRef{fenced_resource_b, 1, frontier_digest(22)}}
        });
        expect(frontier_result.status == MdbxResourceFrontierSnapshotStatus::Available
                   && frontier_result.snapshot,
               "fenced publication fixture did not obtain a B1 frontier");
        const auto original_identity = frontier_result.snapshot->identity_token();
        expect(original_identity.rfind("mdbx-frontier-v2", 0) == 0
                   && original_identity.find("fenced|root:@a") != std::string::npos,
               "frontier identity did not use the versioned length-prefixed encoding");
        // The B1 reader pins a read transaction for stable reads. Release it
        // before opening B2's writable transaction on the same MDBX thread.
        frontier_result.snapshot->read_transaction_owner.reset();
        auto reversed_result = frontier_reader.read(fenced_scope, {
            {fenced_unit_b.id, fenced_unit_b.revision,
             ResourceRevisionRef{fenced_resource_b, 1, frontier_digest(22)}},
            {fenced_unit_a.id, fenced_unit_a.revision,
             ResourceRevisionRef{fenced_resource_a, 1, frontier_digest(21)}}
        });
        expect(reversed_result.status == MdbxResourceFrontierSnapshotStatus::Available
                   && reversed_result.snapshot
                   && reversed_result.snapshot->identity_token() != original_identity,
               "frontier identity did not preserve input order");
        reversed_result.snapshot->read_transaction_owner.reset();
        const auto fenced_request = MdbxFencedDerivedPublicationRequest{
            "fenced-publication", ProjectionKind::QACombined, 1,
            "fenced answer", 1, "pipeline@1"};
        const auto fenced = store.publish_fenced(
            *frontier_result.snapshot, fenced_request);
        expect(fenced.status == MdbxFencedDerivedPublicationStatus::Published
                   && fenced.publication,
               "fenced publication was not committed");
        const auto fenced_read = store.read(
            fenced_scope, "fenced-publication", fenced.publication->request.inputs,
            fenced.publication->request.dependency_frontier);
        expect(fenced_read.status == DerivedPublicationReadStatus::StoredMatch,
               "fenced publication did not produce a durable storage match");
        const auto identity_conflict = store.publish_fenced(
            *frontier_result.snapshot,
            MdbxFencedDerivedPublicationRequest{
                "fenced-publication", ProjectionKind::QACombined, 1,
                "conflicting body", 1, "pipeline@1"});
        expect(identity_conflict.status == MdbxFencedDerivedPublicationStatus::InvalidInput,
               "conflicting replay of a publication identity was accepted");
        expect(read_field(context, "fenced-publication", 'b') ==
                   std::optional<std::string>{"fenced answer"},
               "publication identity conflict damaged the stored body");

        const auto expect_injected_rollback = [&](MdbxDerivedPublicationFaultPoint point,
                                                  const char* publication_id) {
            MdbxDerivedPublicationStoreOptions injected_options;
            injected_options.context = context;
            injected_options.table_prefix = "agent_memory";
            injected_options.fault_injector = [point](
                const MdbxDerivedPublicationFaultPoint current) {
                if(current == point) {
                    throw std::runtime_error("injected derived publication failure");
                }
            };
            {
                MdbxDerivedPublicationStore injected_store(std::move(injected_options));
                const auto result = injected_store.publish_fenced(
                    *frontier_result.snapshot,
                    MdbxFencedDerivedPublicationRequest{
                        publication_id, ProjectionKind::QACombined, 1,
                        "injected body", 1, "pipeline@1"});
                expect(result.status == MdbxFencedDerivedPublicationStatus::Unavailable,
                       "injected publication failure was reported as committed");
            }
            expect(!read_field(context, publication_id, 'b')
                       && !read_field(context, publication_id, 'm')
                       && !read_field(context, publication_id, 'a'),
                   "injected publication failure left a partial physical row");
        };
        expect_injected_rollback(
            MdbxDerivedPublicationFaultPoint::AfterBodyWrite, "fenced-fault-body");
        expect_injected_rollback(
            MdbxDerivedPublicationFaultPoint::AfterManifestWrite, "fenced-fault-manifest");

        auto forged_result = frontier_reader.read(fenced_scope, {
            {fenced_unit_a.id, fenced_unit_a.revision,
             ResourceRevisionRef{fenced_resource_a, 1, frontier_digest(21)}},
            {fenced_unit_b.id, fenced_unit_b.revision,
             ResourceRevisionRef{fenced_resource_b, 1, frontier_digest(22)}}
        });
        expect(forged_result.status == MdbxResourceFrontierSnapshotStatus::Available
                   && forged_result.snapshot,
               "forged frontier fixture did not obtain a B1 frontier");
        forged_result.snapshot->entries.front().unit.revision++;
        forged_result.snapshot->read_transaction_owner.reset();
        const auto forged = store.publish_fenced(
            *forged_result.snapshot,
            MdbxFencedDerivedPublicationRequest{
                "fenced-forged", ProjectionKind::QACombined, 1,
                "must not publish", 1, "pipeline@1"});
        expect(forged.status == MdbxFencedDerivedPublicationStatus::InvalidInput,
               "caller-mutated frontier was accepted as a trusted witness");
        expect(!read_field(context, "fenced-forged", 'b')
                   && !read_field(context, "fenced-forged", 'm')
                   && !read_field(context, "fenced-forged", 'a'),
               "untrusted frontier left a physical publication row");

        manifests.upsert_manifest(frontier_manifest(fenced_resource_b, 2, 23));
        const auto stale_fenced = store.publish_fenced(
            *frontier_result.snapshot,
            MdbxFencedDerivedPublicationRequest{
                "fenced-stale", ProjectionKind::QACombined, 1,
                "must not publish", 1, "pipeline@1"});
        expect(stale_fenced.status == MdbxFencedDerivedPublicationStatus::FenceRejected,
               "root mutation between frontier and commit was accepted");
        expect(store.read(fenced_scope, "fenced-stale", {}, "").status
                   == DerivedPublicationReadStatus::NotFound,
               "fenced rejection left partial publication rows");
        expect(!read_field(context, "fenced-stale", 'b')
                   && !read_field(context, "fenced-stale", 'm')
                   && !read_field(context, "fenced-stale", 'a'),
               "root fence rejection left a physical publication row");

        // Restore the other root so the next case isolates a unit-revision
        // mutation rather than inheriting the previous root mismatch.
        manifests.upsert_manifest(frontier_manifest(fenced_resource_b, 1, 22));
        const auto fenced_unit_revision = units.update_mutable_fields(
            fenced_unit_a.id, KnowledgeUnitMutablePatch{
                std::string{"fenced publication unit v2"}, std::nullopt,
                std::nullopt, std::nullopt, std::nullopt});
        static_cast<void>(fenced_unit_revision);
        const auto stale_unit = store.publish_fenced(
            *frontier_result.snapshot,
            MdbxFencedDerivedPublicationRequest{
                "fenced-stale-unit", ProjectionKind::QACombined, 1,
                "must not publish", 1, "pipeline@1"});
        expect(stale_unit.status == MdbxFencedDerivedPublicationStatus::FenceRejected,
               "KnowledgeUnit mutation without root mutation was accepted");
        expect(store.read(fenced_scope, "fenced-stale-unit", {}, "").status
                   == DerivedPublicationReadStatus::NotFound,
               "unit fence rejection left partial publication rows");
        expect(!read_field(context, "fenced-stale-unit", 'b')
                   && !read_field(context, "fenced-stale-unit", 'm')
                   && !read_field(context, "fenced-stale-unit", 'a'),
               "unit fence rejection left a physical publication row");
    }

    {
        auto context = MdbxStorageContext::open(path.string());
        MdbxDerivedPublicationStore store({"", "agent_memory", false,
                                           kDefaultMdbxMaxDbs, context});
        const auto reopened = store.read(
            request.scope_id, request.publication_id, request.inputs, request.dependency_frontier);
        expect(reopened.status == DerivedPublicationReadStatus::StoredMatch,
               "publication did not survive reopen as a storage match");
        for(const auto publication_id : {"fenced-fault-body", "fenced-fault-manifest"}) {
            expect(store.read(ScopeId{"fenced-scope"}, publication_id, {}, "").status
                       == DerivedPublicationReadStatus::NotFound,
                   "reopen exposed a publication rejected by injected failure");
        }

        mdbxc::KeyValueTable<std::string, std::string> table(
            context->connection(), "agent_memory_unit_projections");
        {
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            table.insert_or_assign(body_key("aborted"), "body written before injected failure", txn);
            try {
                throw std::runtime_error("injected failure before commit");
            } catch(const std::runtime_error&) {
                // Transaction destruction rolls back the uncommitted body write.
            }
        }
        const auto aborted = store.read(
            request.scope_id, "aborted", request.inputs, request.dependency_frontier);
        expect(aborted.status == DerivedPublicationReadStatus::NotFound,
               "uncommitted publication field became visible after failure");

        {
            auto partial_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            table.insert_or_assign(body_key("partial"), "body without marker", partial_txn);
            partial_txn.commit();
        }
        const auto partial = store.read(
            request.scope_id, "partial", request.inputs, request.dependency_frontier);
        expect(partial.status == DerivedPublicationReadStatus::Unavailable,
               "body without a durable marker was admitted");
    }

    auto custom_database = TestDatabase::create(
        "agent_memory_derived_publication_store_custom_prefix");
    const auto& custom_path = custom_database.path();
    {
        auto context = MdbxStorageContext::open(custom_path.string());
        MdbxResourceManifestStorageOptions manifest_options;
        manifest_options.context = context;
        manifest_options.table_prefix = "custom_b2";
        MdbxResourceManifestStorage manifests(std::move(manifest_options));
        MdbxKnowledgeUnitStoreOptions unit_options;
        unit_options.context = context;
        unit_options.table_prefix = "custom_b2";
        MdbxKnowledgeUnitStore units(std::move(unit_options));
        const ScopeId scope{"custom-scope"};
        const ResourceId resource{"custom-root"};
        manifests.upsert_manifest(frontier_manifest(resource, 1, 31));
        const auto unit = units.create_or_get_unit(
            frontier_unit_request(scope, resource, 1, 31));
        MdbxResourceFrontierSnapshotReader reader(context, "custom_b2");
        auto snapshot = reader.read(scope, {
            {unit.id, unit.revision,
             ResourceRevisionRef{resource, 1, frontier_digest(31)}}
        });
        expect(snapshot.status == MdbxResourceFrontierSnapshotStatus::Available
                   && snapshot.snapshot,
               "custom-prefix frontier was unavailable");
        snapshot.snapshot->read_transaction_owner.reset();
        MdbxDerivedPublicationStoreOptions publication_options;
        publication_options.context = context;
        publication_options.table_prefix = "custom_b2";
        MdbxDerivedPublicationStore custom_store(std::move(publication_options));
        const auto custom_publication = custom_store.publish_fenced(
            *snapshot.snapshot,
            MdbxFencedDerivedPublicationRequest{
                "custom-publication", ProjectionKind::QACombined, 1,
                "custom body", 1, "pipeline@1"});
        expect(custom_publication.status == MdbxFencedDerivedPublicationStatus::Published
                   && custom_publication.publication,
               "custom table prefix did not share the trusted storage domain");
        expect(custom_store.read(
                   scope, "custom-publication",
                   custom_publication.publication->request.inputs,
                   custom_publication.publication->request.dependency_frontier).status
                   == DerivedPublicationReadStatus::StoredMatch,
               "custom-prefix publication was not readable");
    }
    custom_database.cleanup_or_fail();

    auto current_database = TestDatabase::create("agent_memory_current_derived_reader");
    {
        auto context = MdbxStorageContext::open(current_database.path().string());
        const ScopeId scope{"current-scope"};
        const ResourceId resource{"current-root"};
        MdbxResourceManifestStorage manifests({"", "agent_memory", false,
                                                kDefaultMdbxMaxDbs, context});
        MdbxKnowledgeUnitStore units({"", "agent_memory", false,
                                      kDefaultMdbxMaxDbs, context});
        manifests.upsert_manifest(frontier_manifest(resource, 1, 41));
        const auto unit = units.create_or_get_unit(
            frontier_unit_request(scope, resource, 1, 41));
        MdbxResourceFrontierSnapshotReader frontier_reader(context);
        auto frontier = frontier_reader.read(scope, {
            {unit.id, unit.revision, ResourceRevisionRef{resource, 1, frontier_digest(41)}}
        });
        expect(frontier.status == MdbxResourceFrontierSnapshotStatus::Available && frontier.snapshot,
               "current-read fixture frontier was unavailable");
        frontier.snapshot->read_transaction_owner.reset();
        MdbxDerivedPublicationStore publications({"", "agent_memory", false,
                                                  kDefaultMdbxMaxDbs, context});
        const auto published = publications.publish_fenced(
            *frontier.snapshot,
            MdbxFencedDerivedPublicationRequest{
                "current-publication", ProjectionKind::QACombined, 1,
                "current body", 1, "pipeline@1"});
        expect(published.status == MdbxFencedDerivedPublicationStatus::Published,
               "current-read fixture publication failed");

        MdbxCurrentDerivedPublicationReader reader(context);
        const auto current = reader.read_current(scope, "current-publication");
        expect(current.status == MdbxCurrentDerivedPublicationStatus::Current && current.publication &&
                   current.publication->request.body == "current body",
               "current publication was not revalidated as Current");
        const auto historical = reader.read_historical(scope, "current-publication");
        expect(historical.status == MdbxCurrentDerivedPublicationStatus::Historical && historical.publication,
               "exact historical publication was not readable");

        reader.set_read_hook_for_testing([&] {
            std::thread writer([&] {
                auto writer_context = MdbxStorageContext::open(current_database.path().string());
                MdbxResourceManifestStorage writer_manifests({"", "agent_memory", false,
                                                               kDefaultMdbxMaxDbs, writer_context});
                writer_manifests.upsert_manifest(frontier_manifest(resource, 2, 42));
            });
            writer.join();
        });
        const auto raced_current = reader.read_current(scope, "current-publication");
        expect(raced_current.status == MdbxCurrentDerivedPublicationStatus::Current && raced_current.publication,
               "one read transaction did not preserve a consistent pre-write snapshot");
        reader.set_read_hook_for_testing({});
        const auto stale = reader.read_current(scope, "current-publication");
        expect(stale.status == MdbxCurrentDerivedPublicationStatus::Stale && !stale.publication,
               "changed root revision was still returned as Current");
        const auto absent = reader.read_current(scope, "missing-current-publication");
        expect(absent.status == MdbxCurrentDerivedPublicationStatus::NotFound,
               "missing current publication was not reported as NotFound");
    }
    current_database.cleanup_or_fail();
    database.cleanup_or_fail();
    std::cout << "mdbx derived publication store tests passed\n";
    return 0;
}

#else
int main() { return 0; }
#endif
