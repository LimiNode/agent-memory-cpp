#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSnapshotRevisionBoundRetriever.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <filesystem>
#include <future>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

using namespace agent_memory;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path test_path() {
    return std::filesystem::temp_directory_path() /
        "agent_memory_snapshot_revision_bound_retriever_test.mdbx";
}

ObservedResource resource() {
    ObservedResource value;
    value.scope_id = ScopeId{"tenant-a"};
    value.resource_id = ResourceId{"snapshot-resource"};
    value.source_uri = "memory://snapshot";
    value.utf8_body = "snapshot projection alpha evidence";
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
    const auto path = test_path();
    std::error_code error;
    std::filesystem::remove(path, error);
    expect(!error && !std::filesystem::exists(path), "MDBX test path was not clean");

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
    const auto imported = importer.import(resource());
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
    retriever.set_read_hook_for_testing([&] {
        writer_start.set_value();
        if(writer_done_future.wait_for(std::chrono::seconds(3)) !=
           std::future_status::ready) {
            std::cerr << "snapshot writer wait timed out\n";
            std::_Exit(124);
        }
    });
    const auto raced = retriever.retrieve_detailed(query);
    if(writer_done_future.wait_for(std::chrono::seconds(3)) !=
       std::future_status::ready) {
        std::cerr << "snapshot writer completion timed out\n";
        std::_Exit(125);
    }
    if(writer.joinable()) writer.join();
    if(writer_error) std::rethrow_exception(writer_error);
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
    }
    }

    context.reset();

    // Reopen the same custom-prefix database and exercise fail-closed
    // admission cases against the persisted revision-three publication.
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
        unit_id, 3, ProjectionKind::Original, 0.75F};
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
               scope, unit_id, ProjectionKind::Original, 3),
           "fixture projection was not removable");
    auto malformed = make_original_projection(*reopened_units.find(unit_id), 3);
    malformed.version.derivation_fingerprint = "wrong.recipe.v1";
    reopened_projections.put(std::move(malformed));
    const auto wrong_projection = reopened_retriever.admit_candidate(reopened_candidate);
    expect(wrong_projection.freshness == RevisionBoundFreshness::Stale &&
               wrong_projection.text.empty(),
           "wrong projection fingerprint was admitted");
    expect(reopened_projections.delete_revision(
               scope, unit_id, ProjectionKind::Original, 3),
           "malformed projection was not removable");

    const auto current_unit = reopened_units.find(unit_id);
    expect(current_unit.has_value(), "reopened unit disappeared before root test");
    reopened_projections.put(make_original_projection(*current_unit, 3));
    expect(reopened_manifests.erase_manifest(ResourceId{"snapshot-resource"}),
           "fixture root manifest was not removable");
    const auto missing_root = reopened_retriever.admit_candidate(reopened_candidate);
    expect(missing_root.freshness == RevisionBoundFreshness::Stale &&
               missing_root.text.empty(),
           "missing root was admitted");
    }
    context.reset();

    const bool removed = std::filesystem::remove(path, error);
    expect(removed && !error && !std::filesystem::exists(path),
           "MDBX snapshot test database cleanup failed");
    std::cout << "mdbx snapshot revision-bound retriever tests passed\n";
    return 0;
}

#else

int main() { return 0; }

#endif
