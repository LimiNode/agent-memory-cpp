#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSnapshotRevisionBoundRetriever.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKb0RetrievalFacade.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <future>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <system_error>
#include <thread>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

namespace {

using namespace agent_memory;

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
    TestDatabase() {
        const auto root = std::filesystem::temp_directory_path();
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        for(std::uint64_t attempt = 0; attempt < 64; ++attempt) {
            const auto directory = root /
                ("agent_memory_snapshot_revision_bound_" +
                 std::to_string(nonce) + "_" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(directory, error)) {
                m_directory = directory;
                m_path = directory / "snapshot.mdbx";
                m_lock_path = std::filesystem::path{m_path.string() + "-lck"};
                return;
            }
            if(error && error != std::make_error_code(std::errc::file_exists)) {
                throw std::runtime_error(
                    "snapshot test directory creation failed: " + error.message());
            }
        }
        throw std::runtime_error("snapshot test directory reservation failed");
    }

    TestDatabase(const TestDatabase&) = delete;
    TestDatabase& operator=(const TestDatabase&) = delete;

    ~TestDatabase() {
        std::error_code ignored;
        cleanup(ignored);
    }

    [[nodiscard]] const std::filesystem::path& path() const noexcept {
        return m_path;
    }

    void cleanup_or_fail() {
        std::error_code error;
        cleanup(error);
        expect(!error && m_directory.empty(),
               "snapshot test database cleanup failed");
    }

private:
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

ObservedResource resource(std::string body) {
    ObservedResource value;
    value.scope_id = ScopeId{"tenant-a"};
    value.resource_id = ResourceId{"snapshot-resource"};
    value.source_uri = "memory://snapshot";
    value.utf8_body = std::move(body);
    return value;
}

KnowledgeUnitMutablePatch display_patch(const char* value) {
    return KnowledgeUnitMutablePatch{
        std::string{value}, std::nullopt, std::nullopt, std::nullopt, std::nullopt};
}

void update_display(
    const std::shared_ptr<MdbxStorageContext>& context,
    const KnowledgeUnitId& id,
    const char* value,
    const std::string& table_prefix
) {
    MdbxKnowledgeUnitStoreOptions options;
    options.context = context;
    options.table_prefix = table_prefix;
    MdbxKnowledgeUnitStore units(std::move(options));
    static_cast<void>(units.update_mutable_fields(id, display_patch(value)));
}

} // namespace

int main() {
    TestDatabase database;
    const auto& path = database.path();

    const ScopeId scope{"tenant-a"};
    const std::string table_prefix = "snapshot_custom";
    StandardTokenizer tokenizer;
    RetrievalQuery query;
    query.text = "projection alpha";
    query.limit = 4;
    auto context = MdbxStorageContext::open(path.string());
    KnowledgeUnitId unit_id;

    {
    MdbxResourceImporterOptions importer_options;
    importer_options.context = context;
    importer_options.table_prefix = table_prefix;
    importer_options.publication_profile =
        ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
    MdbxResourceImporter importer(std::move(importer_options));
    const auto imported = importer.import(
        resource("snapshot projection alpha evidence"));
    expect(imported.raw_units.size() == 1, "snapshot fixture did not publish one raw unit");
    unit_id = imported.raw_units.front();

    MdbxKnowledgeUnitStoreOptions unit_options;
    unit_options.context = context;
    unit_options.table_prefix = table_prefix;
    MdbxKnowledgeUnitStore units(std::move(unit_options));
    const auto initial = units.find(unit_id);
    expect(initial && initial->revision == 1, "snapshot fixture revision is not one");
    ProjectionAwareLexicalCandidate candidate{
        unit_id, initial->revision, ProjectionKind::Original, 0.75F};

    {
    ProjectionAwareLexicalRetrieverOptions retriever_options;
    retriever_options.expected_manifest_schema_id =
        "agent_memory.m0_resource_importer.raw_chunks_original@" + table_prefix;
    MdbxSnapshotRevisionBoundRetriever retriever(
        context, scope, tokenizer, retriever_options, table_prefix);
    const auto current = retriever.admit_candidate(candidate);
    expect(current.freshness == RevisionBoundFreshness::Current &&
               current.materialization == RevisionBoundMaterialization::SummaryOnly &&
               current.text == initial->primary_text,
           "A@1 was not admitted from one snapshot");

    MdbxKb0RetrievalFacade facade(
        context, scope, tokenizer, retriever_options, table_prefix);
    const auto traced = facade.retrieve_with_trace(query);
    expect(traced.response.completeness == RevisionBoundCompleteness::Complete &&
               traced.trace.query_identity.rfind("kb0.retrieval.v1:", 0) == 0 &&
               traced.trace.query_digest.size() == 64 &&
               traced.trace.scope_id == scope && traced.trace.limit == query.limit &&
               traced.trace.max_candidates == retriever_options.max_candidates &&
               traced.trace.projection_kind == ProjectionKind::Original &&
               traced.trace.final_admission_snapshot_consistent &&
               !traced.trace.ranking_snapshot_consistent &&
               traced.trace.admitted_count > 0 && !traced.trace.hits.empty() &&
               traced.trace.hits.front().unit_revision == initial->revision &&
               traced.trace.hits.front().root_generation ==
                   initial->sources.front().resource_revision->generation &&
               traced.trace.hits.front().freshness == RevisionBoundFreshness::Current &&
               traced.trace.hits.front().materialization ==
                   RevisionBoundMaterialization::SummaryOnly,
           "KB-0 trace did not retain exact A@1 admission evidence");

    RetrievalQuery unsupported = query;
    unsupported.metadata_filters.push_back(MetadataFilter{"kind", "chunk"});
    const auto unsupported_trace = facade.retrieve_with_trace(unsupported);
    expect(unsupported_trace.response.completeness ==
               RevisionBoundCompleteness::UnsupportedQuery &&
               !unsupported_trace.trace.final_admission_snapshot_consistent &&
               unsupported_trace.trace.ranking_snapshot_consistent == false &&
               unsupported_trace.response.results.empty() &&
               !unsupported_trace.trace.diagnostic.empty(),
           "unsupported KB-0 query was not traced explicitly");

    auto over_budget = query;
    over_budget.limit = retriever_options.max_candidates + 1U;
    const auto budget_trace = facade.retrieve_with_trace(over_budget);
    expect(budget_trace.response.completeness ==
               RevisionBoundCompleteness::BudgetExceeded &&
               !budget_trace.trace.final_admission_snapshot_consistent &&
               budget_trace.response.results.empty(),
           "over-budget KB-0 query was not traced explicitly");

    const auto updated = units.update_mutable_fields(
        unit_id, display_patch("updated display at revision two"));
    expect(updated.revision == 2, "unit mutation did not advance revision");
    const auto stale = retriever.admit_candidate(candidate);
    expect(stale.freshness == RevisionBoundFreshness::Stale && stale.text.empty(),
           "A@1 remained Current after A@2");

    MdbxProjectionStoreOptions projection_options;
    projection_options.context = context;
    projection_options.table_prefix = table_prefix;
    MdbxProjectionStore projections(std::move(projection_options));
    projections.put(make_original_projection(updated, updated.revision));
    candidate.unit_revision = updated.revision;
    const auto republished = retriever.admit_candidate(candidate);
    expect(republished.freshness == RevisionBoundFreshness::Current &&
               republished.unit_revision == updated.revision,
           "republished A@2 was not admitted");

    std::promise<void> writer_start;
    auto writer_start_future = writer_start.get_future();
    std::promise<void> writer_done;
    auto writer_done_future = writer_done.get_future();
    std::exception_ptr writer_error;
    std::thread writer([&] {
        try {
            writer_start_future.wait();
            update_display(context, unit_id, "updated display at revision three", table_prefix);
        } catch(...) {
            writer_error = std::current_exception();
        }
        writer_done.set_value();
    });
    bool writer_started = false;
    const auto release_writer = [&] {
        if(writer_started) return;
        writer_started = true;
        writer_start.set_value();
    };
    retriever.set_read_hook_for_testing([&] {
        release_writer();
        if(writer_done_future.wait_for(std::chrono::seconds(3)) !=
           std::future_status::ready) {
            std::cerr << "snapshot writer wait timed out\n";
            std::_Exit(124);
        }
    });
    RevisionBoundRetrievalResponse raced;
    std::exception_ptr read_error;
    try {
        raced = retriever.retrieve_detailed(query);
    } catch(...) {
        read_error = std::current_exception();
        release_writer();
    }
    if(writer_done_future.wait_for(std::chrono::seconds(3)) !=
       std::future_status::ready) {
        std::cerr << "snapshot writer completion timed out\n";
        std::_Exit(125);
    }
    writer.join();
    if(writer_error) std::rethrow_exception(writer_error);
    if(read_error) std::rethrow_exception(read_error);
    retriever.set_read_hook_for_testing({});
    expect(!raced.results.empty() &&
               raced.results.front().freshness == RevisionBoundFreshness::Current &&
               raced.results.front().unit_revision == updated.revision,
           "final admission did not preserve the pre-write snapshot");

    const auto revision_three = units.find(unit_id);
    expect(revision_three && revision_three->revision == 3,
           "writer did not publish revision three");
    const auto after_race = retriever.admit_candidate(candidate);
    expect(after_race.freshness == RevisionBoundFreshness::Stale &&
               after_race.text.empty(),
           "post-race candidate did not become stale");
    projections.put(make_original_projection(*revision_three, revision_three->revision));
    candidate.unit_revision = revision_three->revision;

    // A real source update changes both the resource digest and generation.
    // The old unit remains historical staging data, but the new manifest no
    // longer admits its revision-bound candidate.
    const auto imported_v2 = importer.import(
        resource("snapshot projection alpha resource revision two"));
    expect(imported_v2.revision.generation == 2 &&
               imported_v2.revision.body_digest && imported.revision.body_digest &&
               imported_v2.revision.body_digest->bytes !=
                   imported.revision.body_digest->bytes,
           "resource update did not create a new digest-bound revision");
    expect(imported_v2.raw_units.size() == 1,
           "resource update did not publish one replacement raw unit");
    const auto old_after_resource_update = retriever.admit_candidate(candidate);
    expect(old_after_resource_update.freshness == RevisionBoundFreshness::Stale &&
               old_after_resource_update.text.empty(),
           "old resource revision remained Current after source update");
    const auto resource_v2_unit = units.find(imported_v2.raw_units.front());
    expect(resource_v2_unit && resource_v2_unit->sources.size() == 1 &&
               resource_v2_unit->sources.front().resource_revision &&
               resource_v2_unit->sources.front().resource_revision->generation == 2 &&
               resource_v2_unit->sources.front().resource_revision->body_digest.bytes ==
                   imported_v2.revision.body_digest->bytes,
           "replacement unit lost the Resource A@2 binding");
    const ProjectionAwareLexicalCandidate resource_v2_candidate{
        resource_v2_unit->id, resource_v2_unit->revision,
        ProjectionKind::Original, 0.80F};
    const auto resource_v2_current = retriever.admit_candidate(resource_v2_candidate);
    expect(resource_v2_current.freshness == RevisionBoundFreshness::Current &&
               resource_v2_current.root_generation == 2 &&
               resource_v2_current.root_digest &&
               resource_v2_current.root_digest->bytes ==
                   imported_v2.revision.body_digest->bytes,
           "resource A@2 was not admitted with its exact root revision");
    ResourceImportResult imported_v3;
    facade.set_candidate_hook_for_testing([&] {
        imported_v3 = importer.import(
            resource("snapshot projection alpha resource revision three"));
    });
    const auto invalidated_trace = facade.retrieve_with_trace(query);
    const auto& invalidated_window = invalidated_trace.response;
    facade.set_candidate_hook_for_testing({});
    expect(imported_v3.revision.generation == 3 &&
               imported_v3.revision.body_digest &&
               imported_v3.revision.body_digest->bytes !=
                   imported_v2.revision.body_digest->bytes,
           "candidate-generation source update did not publish Resource A@3");
    expect(invalidated_window.completeness == RevisionBoundCompleteness::Complete &&
               invalidated_window.candidate_count > 0 &&
               invalidated_window.admitted_count == 0 &&
               invalidated_window.stale_count > 0 &&
               invalidated_window.unavailable_count == 0 &&
               !invalidated_window.diagnostic.empty() &&
               !invalidated_window.results.empty() &&
               invalidated_window.results.front().text.empty(),
           "source update between ranking and admission was not reported boundedly");
    expect(invalidated_trace.trace.final_admission_snapshot_consistent &&
               !invalidated_trace.trace.ranking_snapshot_consistent &&
               invalidated_trace.trace.stale_count == invalidated_window.stale_count,
           "KB-0 trace lost the bounded candidate-hook race evidence");
    const auto resource_v3_unit = units.find(imported_v3.raw_units.front());
    expect(resource_v3_unit && resource_v3_unit->sources.size() == 1 &&
               resource_v3_unit->sources.front().resource_revision &&
               resource_v3_unit->sources.front().resource_revision->generation == 3,
           "replacement unit lost the Resource A@3 binding");
    const auto resource_v3_current = retriever.admit_candidate(
        ProjectionAwareLexicalCandidate{
            resource_v3_unit->id, resource_v3_unit->revision,
            ProjectionKind::Original, 0.85F});
    expect(resource_v3_current.freshness == RevisionBoundFreshness::Current &&
               resource_v3_current.root_generation == 3,
           "Resource A@3 was not admitted after bounded invalidation");
    unit_id = resource_v3_unit->id;
    }
    }

    context.reset();

    // Reopen the same custom-prefix database and exercise fail-closed
    // admission cases against the persisted Resource A@2 publication.
    context = MdbxStorageContext::open(path.string());
    {
    MdbxKnowledgeUnitStoreOptions reopened_unit_options;
    reopened_unit_options.context = context;
    reopened_unit_options.table_prefix = table_prefix;
    MdbxKnowledgeUnitStore reopened_units(std::move(reopened_unit_options));
    MdbxProjectionStoreOptions reopened_projection_options;
    reopened_projection_options.context = context;
    reopened_projection_options.table_prefix = table_prefix;
    MdbxProjectionStore reopened_projections(std::move(reopened_projection_options));
    MdbxResourceManifestStorageOptions reopened_manifest_options;
    reopened_manifest_options.context = context;
    reopened_manifest_options.table_prefix = table_prefix;
    MdbxResourceManifestStorage reopened_manifests(std::move(reopened_manifest_options));
    ProjectionAwareLexicalRetrieverOptions reopened_options;
    reopened_options.expected_manifest_schema_id =
        "agent_memory.m0_resource_importer.raw_chunks_original@" + table_prefix;
    MdbxSnapshotRevisionBoundRetriever reopened_retriever(
        context, scope, tokenizer, reopened_options, table_prefix);
    ProjectionAwareLexicalCandidate reopened_candidate{
        unit_id, 1, ProjectionKind::Original, 0.80F};
    const auto reopened_current = reopened_retriever.admit_candidate(reopened_candidate);
    expect(reopened_current.freshness == RevisionBoundFreshness::Current,
           "reopened revision three was not admitted");

    MdbxSnapshotRevisionBoundRetriever wrong_scope_retriever(
        context, ScopeId{"tenant-b"}, tokenizer, reopened_options, table_prefix);
    const auto wrong_scope = wrong_scope_retriever.admit_candidate(reopened_candidate);
    expect(wrong_scope.freshness == RevisionBoundFreshness::Stale &&
               wrong_scope.text.empty(),
           "wrong-scope candidate was admitted");

    const auto missing = reopened_retriever.admit_candidate(
        ProjectionAwareLexicalCandidate{
            KnowledgeUnitId{999999}, 3, ProjectionKind::Original, 0.1F});
    expect(missing.freshness == RevisionBoundFreshness::Stale && missing.text.empty(),
           "missing unit was admitted");

    expect(reopened_projections.delete_revision(
               scope, unit_id, ProjectionKind::Original,
               reopened_candidate.unit_revision),
           "fixture projection was not removable");
    auto malformed = make_original_projection(
        *reopened_units.find(unit_id), reopened_candidate.unit_revision);
    malformed.version.derivation_fingerprint = "wrong.recipe.v1";
    reopened_projections.put(std::move(malformed));
    const auto wrong_projection = reopened_retriever.admit_candidate(reopened_candidate);
    expect(wrong_projection.freshness == RevisionBoundFreshness::Stale &&
               wrong_projection.text.empty(),
           "wrong projection fingerprint was admitted");
    expect(reopened_projections.delete_revision(
               scope, unit_id, ProjectionKind::Original,
               reopened_candidate.unit_revision),
           "malformed projection was not removable");

    const auto current_unit = reopened_units.find(unit_id);
    expect(current_unit.has_value(), "reopened unit disappeared before root test");
    reopened_projections.put(make_original_projection(
        *current_unit, reopened_candidate.unit_revision));
    expect(reopened_manifests.erase_manifest(ResourceId{"snapshot-resource"}),
           "fixture root manifest was not removable");
    const auto missing_root = reopened_retriever.admit_candidate(reopened_candidate);
    expect(missing_root.freshness == RevisionBoundFreshness::Stale &&
               missing_root.text.empty(),
           "missing root was admitted");
    }
    context.reset();
    database.cleanup_or_fail();
    std::cout << "mdbx snapshot revision-bound retriever tests passed\n";
    return 0;
}

#else

int main() { return 0; }

#endif
