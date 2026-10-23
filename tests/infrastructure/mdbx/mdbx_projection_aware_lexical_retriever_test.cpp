#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string>

namespace {

using namespace agent_memory;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path test_path() {
    return std::filesystem::temp_directory_path() /
        "agent_memory_projection_aware_retriever_test.mdbx";
}

ObservedResource resource(std::string id, std::string body, std::string scope = "tenant-a") {
    ObservedResource value;
    value.scope_id = ScopeId{std::move(scope)};
    value.resource_id = ResourceId{std::move(id)};
    value.source_uri = "memory://projection-aware";
    value.utf8_body = std::move(body);
    return value;
}

ProjectionAwareLexicalRetriever make_retriever(
    MdbxKnowledgeUnitStore& units,
    MdbxProjectionStore& projections,
    MdbxResourceManifestStorage& manifests,
    const ScopeId& scope,
    StandardTokenizer& tokenizer
) {
    return ProjectionAwareLexicalRetriever(
        units, projections, manifests, scope, tokenizer);
}

} // namespace

int main() {
    const auto path = test_path();
    std::error_code error;
    std::filesystem::remove(path, error);
    expect(!error && !std::filesystem::exists(path), "MDBX test path was not clean");

    const ScopeId scope{"tenant-a"};
    StandardTokenizer tokenizer;
    RetrievalQuery initial_query;
    initial_query.text = "projection alpha";
    initial_query.limit = 5;
    std::shared_ptr<MdbxStorageContext> context;
    {
    context = MdbxStorageContext::open(path.string());
    const auto import_resource = [&](ObservedResource observed) {
        MdbxResourceImporterOptions importer_options;
        importer_options.context = context;
        importer_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
        MdbxResourceImporter importer(std::move(importer_options));
        return importer.import(std::move(observed));
    };

    const auto first = import_resource(resource(
        "resource-projection-aware",
        "durable projection alpha evidence"
    ));
    expect(!first.raw_units.empty(), "raw importer profile did not create a unit");
    }
    context.reset();

    // Reopen all adapters and prove publication survives a process boundary.
    {
    context = MdbxStorageContext::open(path.string());
    MdbxKnowledgeUnitStoreOptions reopened_unit_options;
    reopened_unit_options.context = context;
    MdbxKnowledgeUnitStore reopened_units(std::move(reopened_unit_options));
    MdbxProjectionStoreOptions reopened_projection_options;
    reopened_projection_options.context = context;
    MdbxProjectionStore reopened_projections(std::move(reopened_projection_options));
    MdbxResourceManifestStorageOptions reopened_manifest_options;
    reopened_manifest_options.context = context;
    MdbxResourceManifestStorage reopened_manifests(std::move(reopened_manifest_options));
    auto reopened_retriever = make_retriever(
        reopened_units,
        reopened_projections,
        reopened_manifests,
        scope,
        tokenizer
    );
    expect(reopened_retriever.retrieve(initial_query).size() == 1,
           "reopened MDBX publication was not retrievable");

    }
    context.reset();

    // A separate fresh environment verifies interrupted publication remains invisible.
    {
    const auto interrupted_path = path.string() + ".interrupted";
    std::filesystem::remove(interrupted_path, error);
    expect(!error && !std::filesystem::exists(interrupted_path),
           "interrupted MDBX test path was not clean");
    context = MdbxStorageContext::open(interrupted_path);
    // An interrupted import leaves staging rows invisible until its manifest commits.
    MdbxResourceImporterOptions interrupted_options;
    interrupted_options.context = context;
    interrupted_options.publication_profile =
        ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
    interrupted_options.fault_injector = [](ResourceImporterFaultPoint point) {
        if(point == ResourceImporterFaultPoint::BeforeManifestPublication) {
            throw std::runtime_error("intentional interrupted publication");
        }
    };
    bool interrupted = false;
    try {
        MdbxResourceImporter interrupted_import(std::move(interrupted_options));
        (void)interrupted_import.import(resource(
            "resource-projection-aware-interrupted",
            "invisible interrupted projection"
        ));
    } catch(const std::runtime_error& error_value) {
        interrupted = std::string{error_value.what()} == "intentional interrupted publication";
    }
    expect(interrupted, "fault fixture did not interrupt manifest publication");
    MdbxKnowledgeUnitStoreOptions interrupted_unit_options;
    interrupted_unit_options.context = context;
    MdbxKnowledgeUnitStore interrupted_units(std::move(interrupted_unit_options));
    MdbxProjectionStoreOptions interrupted_projection_options;
    interrupted_projection_options.context = context;
    MdbxProjectionStore interrupted_projections(std::move(interrupted_projection_options));
    MdbxResourceManifestStorageOptions interrupted_manifest_options;
    interrupted_manifest_options.context = context;
    MdbxResourceManifestStorage interrupted_manifests(std::move(interrupted_manifest_options));
    auto interrupted_retriever = make_retriever(
        interrupted_units,
        interrupted_projections,
        interrupted_manifests,
        scope,
        tokenizer
    );
    RetrievalQuery interrupted_query;
    interrupted_query.text = "invisible interrupted";
    expect(interrupted_retriever.retrieve(interrupted_query).empty(),
           "interrupted projection became visible without an active manifest");
    context.reset();
    const bool interrupted_removed = std::filesystem::remove(interrupted_path, error);
    expect(interrupted_removed && !error && !std::filesystem::exists(interrupted_path),
           "interrupted MDBX test database cleanup failed");
    }

    context.reset();
    const bool removed = std::filesystem::remove(path, error);
    expect(removed && !error && !std::filesystem::exists(path),
           "MDBX test database cleanup failed");
    return 0;
}

#else

int main() { return 0; }

#endif
