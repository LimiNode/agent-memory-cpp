#include <agent_memory/infrastructure/mdbx/MdbxDerivedPublicationStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSnapshotRevisionBoundRetriever.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <future>
#include <iostream>
#include <memory>
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

using namespace agent_memory;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

void make_removable(const std::filesystem::path& path) noexcept {
#ifdef _WIN32
    std::error_code error;
    if(std::filesystem::exists(path, error) && !error)
        SetFileAttributesW(path.wstring().c_str(), FILE_ATTRIBUTE_NORMAL);
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
                ("agent_memory_unit_projection_coownership_" +
                 std::to_string(nonce) + "_" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(directory, error)) {
                m_directory = directory;
                m_path = directory / "coownership.mdbx";
                m_lock_path = std::filesystem::path{m_path.string() + "-lck"};
                return;
            }
            if(error && error != std::make_error_code(std::errc::file_exists))
                throw std::runtime_error("co-ownership test directory creation failed: " +
                                         error.message());
        }
        throw std::runtime_error("co-ownership test directory reservation failed");
    }

    TestDatabase(const TestDatabase&) = delete;
    TestDatabase& operator=(const TestDatabase&) = delete;

    ~TestDatabase() {
        std::error_code ignored;
        cleanup(ignored);
    }

    [[nodiscard]] const std::filesystem::path& path() const noexcept { return m_path; }

    void cleanup_or_fail() {
        std::error_code error;
        cleanup(error);
        expect(!error && m_directory.empty(), "co-ownership test cleanup failed");
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

SearchProjection projection(
    const ScopeId& scope,
    KnowledgeUnitId unit,
    ProjectionKind kind,
    std::uint64_t revision,
    std::string body
) {
    SearchProjection value;
    value.scope_id = scope;
    value.unit_id = unit;
    value.kind = kind;
    value.version = {revision, revision, 1, "coownership.v1"};
    value.body = std::move(body);
    return value;
}

DerivedPublicationRequest publication(
    const ScopeId& scope,
    std::string id,
    std::string body,
    std::uint64_t generation = 1
) {
    DerivedPublicationRequest value;
    value.scope_id = scope;
    value.publication_id = std::move(id);
    value.kind = ProjectionKind::QACombined;
    value.output_revision = generation;
    value.body = std::move(body);
    value.generation = generation;
    value.dependency_frontier = "resource@" + std::to_string(generation);
    value.inputs = {{KnowledgeUnitId{77}, generation, generation, "sha256:coownership"}};
    return value;
}

std::string field_key(std::string_view publication_id, char field) {
    return "derived_publication:" + std::to_string(publication_id.size()) + ":" +
        std::string{publication_id} + field;
}

std::string projection_table_name(std::string_view prefix) {
    return std::string{prefix} + "_unit_projections";
}

void append_u64(std::string& output, std::uint64_t value) {
    for(int shift = 56; shift >= 0; shift -= 8)
        output.push_back(static_cast<char>((value >> shift) & 0xffU));
}

std::string projection_key(
    const ScopeId& scope,
    KnowledgeUnitId unit,
    ProjectionKind kind,
    std::uint64_t revision
) {
    std::string key;
    key += std::to_string(scope.value().size());
    key.push_back(':');
    key += scope.value();
    append_u64(key, unit.value());
    append_u64(key, static_cast<std::uint16_t>(kind));
    append_u64(key, revision);
    return key;
}

void write_field(
    const std::shared_ptr<MdbxStorageContext>& context,
    std::string_view prefix,
    std::string_view publication_id,
    char field,
    std::string value
) {
    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), projection_table_name(prefix));
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    table.insert_or_assign(field_key(publication_id, field), std::move(value), transaction);
    transaction.commit();
}

std::optional<std::string> read_field(
    const std::shared_ptr<MdbxStorageContext>& context,
    std::string_view prefix,
    std::string_view publication_id,
    char field
) {
    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), projection_table_name(prefix));
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    auto value = table.find(field_key(publication_id, field), transaction);
    transaction.commit();
    return value;
}

} // namespace

int main() {
    TestDatabase database;
    const std::string prefix = "coowner_profile";
    const ScopeId scope{"coowner-scope"};
    const auto path = database.path();
    auto context = MdbxStorageContext::open(path.string());
    const KnowledgeUnitId unit{77};
    const auto first_publication = publication(scope, "publication-a", "derived one");

    {
    MdbxProjectionStoreOptions projection_options;
    projection_options.context = context;
    projection_options.table_prefix = prefix;
    MdbxProjectionStore projections(std::move(projection_options));

    MdbxDerivedPublicationStoreOptions publication_options;
    publication_options.context = context;
    publication_options.table_prefix = prefix;
    MdbxDerivedPublicationStore publications(std::move(publication_options));

    MdbxResourceImporterOptions importer_options;
    importer_options.context = context;
    importer_options.table_prefix = prefix;
    importer_options.publication_profile =
        ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
    MdbxResourceImporter importer(std::move(importer_options));
    ObservedResource observed;
    observed.scope_id = scope;
    observed.resource_id = ResourceId{"coownership-resource"};
    observed.source_uri = "memory://coownership";
    observed.utf8_body = "coownership source body";
    const auto imported = importer.import(std::move(observed));
    expect(imported.raw_units.size() == 1,
           "resource importer did not publish a snapshot-reader fixture");

    projections.put(projection(scope, unit, ProjectionKind::Original, 1, "original one"));
    projections.put(projection(scope, unit, ProjectionKind::DenseContextual, 1, "dense one"));
    publications.publish(first_publication);

    // Key-family isolation: each owner can read only its own key family in the
    // shared `_unit_projections` DBI. The snapshot reader sees the same prefix.
    expect(projections.list(scope, unit).size() == 2,
           "projection scan crossed into derived-publication keys");
    expect(publications.read(
               scope, first_publication.publication_id, first_publication.inputs,
               first_publication.dependency_frontier).status ==
               DerivedPublicationReadStatus::StoredMatch,
           "derived publication was not readable beside projections");

    StandardTokenizer tokenizer;
    ProjectionAwareLexicalRetrieverOptions retriever_options;
    retriever_options.expected_manifest_schema_id =
        "agent_memory.m0_resource_importer.raw_chunks_original@" + prefix;
    MdbxSnapshotRevisionBoundRetriever snapshot(
        context, scope, tokenizer, retriever_options, prefix);
    RetrievalQuery query;
    query.text = "coownership source";
    query.limit = 4;
    const auto snapshot_response = snapshot.retrieve_detailed(query);
    expect(snapshot_response.completeness == RevisionBoundCompleteness::Complete,
           "snapshot reader did not preserve shared-prefix query semantics");
    expect(!snapshot_response.results.empty() &&
               snapshot_response.results.front().freshness == RevisionBoundFreshness::Current,
           "snapshot reader could not consume a projection from the shared DBI");

    // Owner-specific deletion must not remove another projection kind or any
    // of the three derived publication fields.
    expect(projections.delete_revision(scope, unit, ProjectionKind::Original, 1),
           "projection owner failed to delete its own row");
    expect(projections.list(scope, unit).size() == 1,
           "projection delete removed a foreign projection row");
    expect(publications.read(
               scope, first_publication.publication_id, first_publication.inputs,
               first_publication.dependency_frontier).status ==
               DerivedPublicationReadStatus::StoredMatch,
           "projection delete removed derived publication fields");

    // A mixed read transaction must expose both owners from one MDBX snapshot.
    {
        auto transaction = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto same_snapshot_projections = projections.list_in_transaction(
            scope, unit, transaction);
        const auto same_snapshot_publication = publications.read_in_transaction(
            scope, first_publication.publication_id, transaction);
        expect(same_snapshot_projections.size() == 1 &&
                   same_snapshot_projections.front().kind == ProjectionKind::DenseContextual,
               "mixed snapshot projection read was inconsistent");
        expect(same_snapshot_publication.status == DerivedPublicationReadStatus::StoredMatch,
               "mixed snapshot derived read was inconsistent");

        std::promise<void> writer_done;
        auto writer_done_future = writer_done.get_future();
        std::exception_ptr writer_error;
        std::thread writer([&] {
            try {
                MdbxProjectionStoreOptions writer_projection_options;
                writer_projection_options.context = context;
                writer_projection_options.table_prefix = prefix;
                MdbxProjectionStore writer_projections(std::move(writer_projection_options));
                writer_projections.put(projection(
                    scope, unit, ProjectionKind::DenseContextual, 2, "dense two"));
                MdbxDerivedPublicationStoreOptions writer_publication_options;
                writer_publication_options.context = context;
                writer_publication_options.table_prefix = prefix;
                MdbxDerivedPublicationStore writer_publications(
                    std::move(writer_publication_options));
                writer_publications.publish(publication(
                    scope, "publication-b", "derived two", 2));
            } catch(...) {
                writer_error = std::current_exception();
            }
            writer_done.set_value();
        });
        if(writer_done_future.wait_for(std::chrono::seconds(3)) != std::future_status::ready) {
            std::cerr << "co-ownership writer timed out\n";
            std::_Exit(124);
        }
        writer.join();
        if(writer_error) std::rethrow_exception(writer_error);

        expect(projections.list_in_transaction(scope, unit, transaction).size() == 1,
               "old mixed snapshot observed a later projection write");
        expect(publications.read_in_transaction(
                   scope, "publication-b", transaction).status ==
                   DerivedPublicationReadStatus::NotFound,
               "old mixed snapshot observed a later publication write");
        transaction.commit();
    }
    expect(projections.list(scope, unit).size() == 2,
           "post-snapshot projection write was not visible after commit");
    expect(publications.read(
               scope, "publication-b", publication(scope, "publication-b", "derived two", 2).inputs,
               "resource@2").status == DerivedPublicationReadStatus::StoredMatch,
           "post-snapshot publication write was not visible after commit");

    // Malformed rows are rejected by the owning reader and do not contaminate
    // the other owner's key family.
    const auto original_projection = projection(scope, unit, ProjectionKind::Original, 3,
                                                "restored original");
    projections.put(original_projection);
    const auto saved_manifest = read_field(
        context, prefix, first_publication.publication_id, 'm');
    expect(saved_manifest.has_value(), "derived manifest fixture was not found");
    write_field(context, prefix, first_publication.publication_id, 'm', "malformed");
    expect(publications.read(
               scope, first_publication.publication_id, first_publication.inputs,
               first_publication.dependency_frontier).status ==
               DerivedPublicationReadStatus::Unavailable,
           "malformed derived manifest was accepted");
    expect(projections.list(scope, unit).size() == 3,
           "malformed derived row affected projection scan");
    write_field(context, prefix, first_publication.publication_id, 'm', *saved_manifest);

    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), projection_table_name(prefix));
    auto projection_transaction = context->connection()->transaction(
        mdbxc::TransactionMode::WRITABLE);
    // Locate a projection payload without depending on private serialization:
    // the owner-specific `list` is the validator under test, so overwrite the
    // exact row through the known key assembled by the public wire contract.
    const auto encoded_key = projection_key(
        scope, unit, ProjectionKind::Original, 3);
    const auto saved_projection = table.find(encoded_key, projection_transaction);
    expect(saved_projection.has_value(), "projection corruption fixture was not found");
    table.insert_or_assign(encoded_key, "malformed", projection_transaction);
    projection_transaction.commit();
    bool malformed_projection_rejected = false;
    try { static_cast<void>(projections.list(scope, unit)); }
    catch(const std::runtime_error&) { malformed_projection_rejected = true; }
    expect(malformed_projection_rejected, "malformed projection payload was accepted");
    // Projection corruption must remain isolated to the projection owner.  A
    // valid derived publication is read through its independent owner before
    // the damaged projection row is restored; otherwise this test would only
    // prove that repair is possible, not that co-ownership survives damage.
    expect(publications.read(
               scope, first_publication.publication_id, first_publication.inputs,
               first_publication.dependency_frontier).status ==
               DerivedPublicationReadStatus::StoredMatch,
           "projection corruption contaminated the derived publication reader");
    auto restore_projection_transaction = context->connection()->transaction(
        mdbxc::TransactionMode::WRITABLE);
    table.insert_or_assign(encoded_key, *saved_projection, restore_projection_transaction);
    restore_projection_transaction.commit();

    }
    context.reset();

    // Reopen parity uses the same custom prefix and confirms that no owner
    // accidentally relies on process-local state.
    {
        auto reopened_context = MdbxStorageContext::open(path.string());
        MdbxProjectionStoreOptions reopened_projection_options;
        reopened_projection_options.context = reopened_context;
        reopened_projection_options.table_prefix = prefix;
        MdbxProjectionStore reopened_projections(std::move(reopened_projection_options));
        MdbxDerivedPublicationStoreOptions reopened_publication_options;
        reopened_publication_options.context = reopened_context;
        reopened_publication_options.table_prefix = prefix;
        MdbxDerivedPublicationStore reopened_publications(
            std::move(reopened_publication_options));
        expect(reopened_projections.list(scope, unit).size() == 3,
               "projection co-ownership rows did not survive reopen");
        expect(reopened_publications.read(
                   scope, first_publication.publication_id, first_publication.inputs,
                   first_publication.dependency_frontier).status ==
                   DerivedPublicationReadStatus::StoredMatch,
               "derived publication did not recover after malformed-row repair and reopen");
        expect(reopened_publications.read(
                   scope, "publication-b", publication(scope, "publication-b", "derived two", 2).inputs,
                   "resource@2").status == DerivedPublicationReadStatus::StoredMatch,
               "second derived publication did not survive reopen");
    }

    std::cout << "co-ownership report: SAFE for tested key isolation, owner scans/deletes, "
                 "reopen parity, malformed-row rejection, foreign-key safety and mixed snapshots; "
                 "INCONCLUSIVE for crash/recovery semantics outside these readers\n";
    database.cleanup_or_fail();
    return 0;
}

#else

int main() { return 0; }

#endif
