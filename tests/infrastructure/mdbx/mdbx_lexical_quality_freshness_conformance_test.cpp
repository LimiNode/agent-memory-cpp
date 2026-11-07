#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSnapshotRevisionBoundRetriever.hpp>
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

void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

struct TemporaryDatabase final {
    TemporaryDatabase() {
        const auto root = std::filesystem::temp_directory_path();
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        for(unsigned attempt = 0; attempt != 64; ++attempt) {
            const auto candidate = root / ("agent-memory-quality-freshness-" +
                std::to_string(nonce) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                directory = candidate;
                return;
            }
        }
        throw std::runtime_error("freshness test workspace reservation failed");
    }
    ~TemporaryDatabase() {
        std::error_code ignored;
        std::filesystem::remove_all(directory, ignored);
    }
    std::filesystem::path directory;
};

ObservedResource resource(std::string body) {
    ObservedResource value;
    value.scope_id = ScopeId{"public"};
    value.resource_id = ResourceId{"quality-freshness-resource"};
    value.source_uri = "memory://quality-freshness";
    value.utf8_body = std::move(body);
    return value;
}

} // namespace

int main() {
    try {
        TemporaryDatabase database;
        auto context = MdbxStorageContext::open(
            (database.directory / "freshness.mdbx").string());
        MdbxResourceImporterOptions importer_options;
        importer_options.context = context;
        importer_options.publication_profile =
            ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
        MdbxResourceImporter importer(std::move(importer_options));

        const auto first = importer.import(resource("source revision one alpha"));
        require(first.raw_units.size() == 1, "initial freshness resource was not imported");

        MdbxKnowledgeUnitStoreOptions unit_options;
        unit_options.context = context;
        MdbxKnowledgeUnitStore units(std::move(unit_options));
        const auto first_unit = units.find(first.raw_units.front());
        require(first_unit && first_unit->revision == 1,
                "initial freshness unit revision is not one");
        const ProjectionAwareLexicalCandidate old_candidate{
            first_unit->id, first_unit->revision, ProjectionKind::Original, 1.0F};

        StandardTokenizer tokenizer;
        MdbxSnapshotRevisionBoundRetriever retriever(
            context, ScopeId{"public"}, tokenizer, {}, "agent_memory");
        require(retriever.admit_candidate(old_candidate).freshness ==
                    RevisionBoundFreshness::Current,
                "initial freshness candidate was not admitted");

        const auto second = importer.import(resource("source revision two beta"));
        require(second.revision.generation == 2 && second.raw_units.size() == 1,
                "source update did not publish a second revision");
        const auto stale = retriever.admit_candidate(old_candidate);
        require(stale.freshness == RevisionBoundFreshness::Stale && stale.text.empty(),
                "old candidate remained admissible after source revision update");

        const auto current_unit = units.find(second.raw_units.front());
        require(current_unit && current_unit->sources.size() == 1,
                "replacement unit lost its source binding");
        const ProjectionAwareLexicalCandidate current_candidate{
            current_unit->id, current_unit->revision, ProjectionKind::Original, 1.0F};
        require(retriever.admit_candidate(current_candidate).freshness ==
                    RevisionBoundFreshness::Current,
                "current revision candidate was not admitted");
        return 0;
    } catch(const std::exception& error) {
        return (std::cerr << "freshness conformance failed: " << error.what() << '\n', 1);
    }
}

#else
int main() { return 0; }
#endif
