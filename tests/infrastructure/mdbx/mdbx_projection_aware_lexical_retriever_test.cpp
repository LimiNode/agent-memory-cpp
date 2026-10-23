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

    context.reset();
    const bool removed = std::filesystem::remove(path, error);
    expect(removed && !error && !std::filesystem::exists(path),
           "MDBX test database cleanup failed");
    return 0;
}

#else

int main() { return 0; }

#endif
