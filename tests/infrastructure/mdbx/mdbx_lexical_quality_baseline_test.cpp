#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include <agent_memory/retrieval/ProjectionAwareLexicalRetriever.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>
#include <agent_memory/domain/Sha256.hpp>
#include <nlohmann/json.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

using namespace agent_memory;
using Json = nlohmann::json;

void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::string sha256_hex(std::string_view bytes) {
    const auto digest = agent_memory::sha256(bytes);
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for(const auto byte : digest) output << std::setw(2) << static_cast<unsigned>(byte);
    return output.str();
}

std::string read_file(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    require(input.good(), "quality fixture could not be opened");
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

std::string normalized_document(const Json& document) {
    std::string result = document.at("title").get<std::string>() + "\n"
        + document.at("heading").get<std::string>() + "\n"
        + document.at("body").get<std::string>() + "\n"
        + document.at("code").get<std::string>() + "\n";
    for(const auto& tag : document.at("tags")) result += tag.get<std::string>() + " ";
    result += "\n" + document.at("summary").get<std::string>() + "\n"
        + document.at("question").get<std::string>();
    return result;
}

struct Workspace final {
    Workspace() {
        const auto root = std::filesystem::temp_directory_path();
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        for(unsigned attempt = 0; attempt < 64; ++attempt) {
            const auto candidate = root / ("agent-memory-quality-"
                + std::to_string(nonce) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error)) {
                directory = candidate;
                return;
            }
        }
        throw std::runtime_error("quality workspace reservation failed");
    }
    ~Workspace() {
        std::error_code ignored;
        std::filesystem::remove_all(directory, ignored);
    }
    std::filesystem::path path(const char* name) const { return directory / name; }
    std::filesystem::path directory;
};

} // namespace

int main(int argc, char** argv) {
    try {
        require(argc >= 3, "usage: quality-baseline <fixture.json> <receipt.json>");
        const auto fixture_path = std::filesystem::path{argv[1]};
        const auto receipt_path = std::filesystem::path{argv[2]};
        const auto fixture_bytes = read_file(fixture_path);
        const auto fixture = Json::parse(fixture_bytes);
        require(fixture.at("fixture_id") == "independent_retrieval_quality_v1",
            "unsupported quality fixture");

        Workspace workspace;
        std::shared_ptr<MdbxStorageContext> context =
            MdbxStorageContext::open(workspace.path("quality.mdbx").string());
        std::unordered_map<std::uint64_t, std::string> unit_to_document;
        {
            MdbxResourceImporterOptions options;
            options.context = context;
            options.table_prefix = "quality_baseline";
            options.publication_profile =
                ResourceImportPublicationProfile::LexicalWithRawAndOriginalProjections;
            MdbxResourceImporter importer{std::move(options)};
            for(const auto& document : fixture.at("documents")) {
                ObservedResource resource;
                resource.scope_id = ScopeId{document.at("scope_id").get<std::string>()};
                resource.resource_id = ResourceId{document.at("id").get<std::string>()};
                resource.source_uri = "memory://independent-quality-fixture/"
                    + document.at("id").get<std::string>();
                resource.utf8_body = normalized_document(document);
                const auto imported = importer.import(std::move(resource));
                require(!imported.raw_units.empty(), "fixture document was not imported");
                unit_to_document.emplace(imported.raw_units.front().value(),
                    document.at("id").get<std::string>());
            }
        }

        MdbxKnowledgeUnitStoreOptions unit_options;
        unit_options.context = context;
        unit_options.table_prefix = "quality_baseline";
        MdbxKnowledgeUnitStore units{std::move(unit_options)};
        MdbxProjectionStoreOptions projection_options;
        projection_options.context = context;
        projection_options.table_prefix = "quality_baseline";
        MdbxProjectionStore projections{std::move(projection_options)};
        MdbxResourceManifestStorageOptions manifest_options;
        manifest_options.context = context;
        manifest_options.table_prefix = "quality_baseline";
        MdbxResourceManifestStorage manifests{std::move(manifest_options)};
        StandardTokenizer tokenizer;
        ProjectionAwareLexicalRetriever retriever{
            units, projections, manifests, ScopeId{"public"}, tokenizer,
            ProjectionAwareLexicalRetrieverOptions{
                ProjectionKind::Original, 256, 4096, 8U * 1024U * 1024U,
                "agent_memory.m0_resource_importer.raw_chunks_original@quality_baseline", 1}};

        Json runs = Json::array();
        for(const auto& query : fixture.at("queries")) {
            RetrievalQuery request;
            request.text = query.at("text").get<std::string>();
            request.limit = 5;
            const auto result = retriever.retrieve_with_candidates(request);
            result.validate_candidate_pairing();
            Json returned = Json::array();
            for(std::size_t index = 0; index < result.candidates.size(); ++index) {
                const auto found = unit_to_document.find(result.candidates[index].knowledge_unit_id.value());
                require(found != unit_to_document.end(), "retrieval returned unknown unit identity");
                returned.push_back({
                    {"document_id", found->second},
                    {"score", result.candidates[index].score},
                    {"knowledge_unit_id", result.candidates[index].knowledge_unit_id.value()}
                });
            }
            runs.push_back({
                {"query_id", query.at("id")},
                {"scope_id", query.at("scope_id")},
                {"returned", std::move(returned)}
            });
        }
        context.reset();

        Json receipt{
            {"schema_version", 1},
            {"receipt_id", "lexical_quality_baseline_v1"},
            {"execution_status", "EXECUTED"},
            {"retrieval_path", "ProjectionAwareLexicalRetriever::retrieve_with_candidates"},
            {"fixture_id", fixture.at("fixture_id")},
            {"fixture_sha256", sha256_hex(fixture_bytes)},
            {"document_count", fixture.at("documents").size()},
            {"query_count", fixture.at("queries").size()},
            {"score_semantics", "producer score returned by ProjectionAwareLexicalRetriever; no score recomputation"},
            {"runs", std::move(runs)}
        };
        std::ofstream output(receipt_path, std::ios::binary);
        require(output.good(), "quality receipt could not be opened for writing");
        output << receipt.dump(2) << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "lexical quality baseline failed: " << error.what() << '\n';
        return 1;
    }
}

#else
int main() { return 0; }
#endif
