#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

namespace {

using namespace agent_memory;

void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

struct TemporaryDatabase final {
    TemporaryDatabase() {
        const auto root = std::filesystem::temp_directory_path();
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        for(unsigned attempt = 0; attempt != 64; ++attempt) {
            const auto candidate = root / ("agent-memory-quality-scope-" +
                std::to_string(nonce) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                directory = candidate;
                return;
            }
        }
        throw std::runtime_error("scope conformance workspace reservation failed");
    }

    ~TemporaryDatabase() {
        std::error_code ignored;
        std::filesystem::remove_all(directory, ignored);
    }

    std::filesystem::path directory;
};

ObservedResource resource(
    std::string id,
    std::string scope,
    std::string body
) {
    ObservedResource value;
    value.scope_id = ScopeId{std::move(scope)};
    value.resource_id = ResourceId{std::move(id)};
    value.source_uri = "memory://quality-scope-conformance";
    value.utf8_body = std::move(body);
    return value;
}

} // namespace

int main() {
    try {
        TemporaryDatabase database;
        auto context = MdbxStorageContext::open(
            (database.directory / "scope.mdbx").string());

        MdbxResourceImporterOptions importer_options;
        importer_options.context = context;
        importer_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
        MdbxResourceImporter importer(std::move(importer_options));
        const auto public_import = importer.import(resource(
            "quality-public-resource", "public", "public deployment documentation"));
        const auto private_import = importer.import(resource(
            "quality-private-resource", "private", "private deployment tool"));
        require(public_import.raw_units.size() == 1 &&
                    private_import.raw_units.size() == 1,
                "scope fixture resources were not imported");

        MdbxKnowledgeUnitStoreOptions unit_options;
        unit_options.context = context;
        MdbxKnowledgeUnitStore units(std::move(unit_options));
        MdbxProjectionStoreOptions projection_options;
        projection_options.context = context;
        MdbxProjectionStore projections(std::move(projection_options));
        MdbxResourceManifestStorageOptions manifest_options;
        manifest_options.context = context;
        MdbxResourceManifestStorage manifests(std::move(manifest_options));

        StandardTokenizer tokenizer;
        ProjectionAwareLexicalRetriever retriever{
            units, projections, manifests, ScopeId{"public"}, tokenizer};
        RetrievalQuery query;
        query.text = "private deployment tool";
        query.limit = 5;
        const auto result = retriever.retrieve_with_candidates(query);
        result.validate_candidate_pairing();
        for(const auto& candidate : result.candidates) {
            const auto unit = units.find(candidate.knowledge_unit_id);
            require(unit && unit->scope_id == ScopeId{"public"},
                    "public retrieval returned a cross-scope candidate");
        }
        require(std::none_of(
                    result.candidates.begin(), result.candidates.end(),
                    [&](const auto& candidate) {
                        return candidate.knowledge_unit_id == private_import.raw_units.front();
                    }),
                "public retrieval returned the forbidden private resource");
        return 0;
    } catch(const std::exception& error) {
        return (std::cerr << "scope conformance failed: " << error.what() << '\n', 1);
    }
}

#else
int main() { return 0; }
#endif
