#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

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
    RetrievalQuery replacement_query;
    replacement_query.text = "replacement beta";
    std::shared_ptr<MdbxStorageContext> context;
    {
    context = MdbxStorageContext::open(path.string());
    MdbxResourceImporterOptions importer_options;
    importer_options.context = context;
    importer_options.publication_profile =
        ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
    MdbxResourceImporter importer(std::move(importer_options));

    MdbxKnowledgeUnitStoreOptions unit_options;
    unit_options.context = context;
    MdbxKnowledgeUnitStore units(std::move(unit_options));
    MdbxProjectionStoreOptions projection_options;
    projection_options.context = context;
    MdbxProjectionStore projections(std::move(projection_options));
    MdbxResourceManifestStorageOptions manifest_options;
    manifest_options.context = context;
    MdbxResourceManifestStorage manifests(std::move(manifest_options));

    const auto first = importer.import(resource(
        "resource-projection-aware",
        "durable projection alpha evidence"
    ));
    std::cerr << "stage initial import\n";
    expect(!first.raw_units.empty(), "raw importer profile did not create a unit");
    auto retriever = make_retriever(units, projections, manifests, scope, tokenizer);
    RetrievalQuery query;
    query.text = "projection alpha";
    query.limit = 5;
    const auto initial = retriever.retrieve(query);
    std::cerr << "stage initial retrieve\n";
    expect(initial.size() == 1 &&
           initial.chunks.front().chunk.text == "durable projection alpha evidence",
           "MDBX publication was not retrievable");

    }
    context.reset();

    // Reopen all adapters, publish a second scope and a newer revision, then
    // prove stale projections remain invisible.
    {
    context = MdbxStorageContext::open(path.string());
    MdbxResourceImporterOptions reopened_importer_options;
    reopened_importer_options.context = context;
    reopened_importer_options.publication_profile =
        ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
    MdbxResourceImporter reopened_importer(std::move(reopened_importer_options));
    std::cerr << "stage reopen importer\n";

    (void)reopened_importer.import(resource(
        "resource-projection-aware-other-scope",
        "projection alpha foreign scope",
        "tenant-b"
    ));
    std::cerr << "stage foreign import\n";
    (void)reopened_importer.import(resource(
        "resource-projection-aware",
        "replacement projection beta evidence"
    ));
    std::cerr << "stage replacement import\n";

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
    RetrievalQuery foreign_query;
    foreign_query.text = "foreign";
    expect(reopened_retriever.retrieve(foreign_query).empty(),
           "MDBX retrieval leaked across scopes");
    RetrievalQuery stale_query;
    stale_query.text = "durable";
    expect(reopened_retriever.retrieve(stale_query).empty(),
           "stale resource projection remained visible");
    expect(reopened_retriever.retrieve(replacement_query).size() == 1,
           "reopened MDBX publication was not retrievable");
    std::cerr << "stage replacement retrieve\n";

    }
    context.reset();

    // A fresh reopen verifies interrupted publication remains invisible.
    {
    context = MdbxStorageContext::open(path.string());
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
    std::cerr << "stage interrupted setup\n";
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
    RetrievalQuery interrupted_query;
    interrupted_query.text = "invisible interrupted";
    expect(interrupted_retriever.retrieve(interrupted_query).empty(),
           "interrupted projection became visible without an active manifest");
    std::cerr << "stage interrupted retrieve\n";
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
