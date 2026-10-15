#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <filesystem>
#include <stdexcept>
#include <string>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path test_path(const char* suffix) {
    return std::filesystem::temp_directory_path() /
        (std::string{"agent_memory_m0_importer_"} + suffix + ".mdbx");
}

agent_memory::ObservedResource original(
    std::string resource_id,
    std::string uri,
    std::string body
) {
    agent_memory::ObservedResource resource;
    resource.scope_id = agent_memory::ScopeId{"tenant-a"};
    resource.resource_id = agent_memory::ResourceId{std::move(resource_id)};
    resource.source_uri = std::move(uri);
    resource.utf8_body = std::move(body);
    return resource;
}

} // namespace

int main() {
    using namespace agent_memory;
    const auto path = test_path("contract");
    std::error_code error;
    std::filesystem::remove(path, error);

    {
        MdbxResourceImporterOptions options;
        options.path = path.string();
        MdbxResourceImporter importer{std::move(options)};
        const auto first = importer.import(original(
            "resource:one",
            "https://example.test/one",
            "Durable importer keeps this searchable source."
        ));
        expect(first.revision.generation == 1, "initial import generation mismatch");
        expect(first.revision.body_digest.has_value(), "body digest was not published");
        expect(first.document_id.value().find("durable-lexical:") == 0,
               "document identity is not lexical-profile stable");
        const auto manifest = importer.find_manifest(first.resource_id);
        expect(manifest && manifest->records.size() == 2,
               "import did not publish document and chunk manifest records");
        expect(manifest->revision.text_origin == SourceTextOrigin::OriginalText,
               "original import changed text origin");
        expect(importer.search(ScopeId{"tenant-a"}, "searchable", 5).size() == 1,
               "imported source was not searchable");

        const auto uri_only = importer.import(original(
            "resource:one",
            "https://example.test/renamed",
            "Durable importer keeps this searchable source."
        ));
        expect(uri_only.revision.generation == 1,
               "metadata-only update created a false body revision");

        bool rejected = false;
        auto derived = original("resource:derived", "https://example.test/derived", "derived");
        derived.text_origin = SourceTextOrigin::DerivedExtraction;
        try { (void)importer.import(std::move(derived)); }
        catch(const std::invalid_argument&) { rejected = true; }
        expect(rejected, "derived text without provenance was accepted");
    }

    {
        MdbxResourceImporterOptions options;
        options.path = path.string();
        MdbxResourceImporter importer{std::move(options)};
        expect(importer.search(ScopeId{"tenant-a"}, "searchable", 5).size() == 1,
               "reopen lost imported lexical source");
        const auto manifest = importer.find_manifest(ResourceId{"resource:one"});
        expect(manifest && manifest->revision.generation == 1,
               "reopen lost imported manifest");
    }

    const auto recovery_path = test_path("recovery");
    std::filesystem::remove(recovery_path, error);
    {
        MdbxResourceImporterOptions options;
        options.path = recovery_path.string();
        options.fault_injector = [](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::BeforeManifestPublication) {
                throw std::runtime_error("importer interruption");
            }
        };
        MdbxResourceImporter importer{std::move(options)};
        bool interrupted = false;
        try {
            (void)importer.import(original(
                "resource:recovery",
                "https://example.test/recovery",
                "Recovery intent completes the publication."
            ));
        } catch(const std::runtime_error&) { interrupted = true; }
        expect(interrupted, "importer fault injection did not interrupt publication");
    }
    {
        MdbxResourceImporterOptions options;
        options.path = recovery_path.string();
        MdbxResourceImporter importer{std::move(options)};
        const auto manifest = importer.find_manifest(ResourceId{"resource:recovery"});
        expect(manifest && manifest->revision.generation == 1,
               "reopen did not recover pending importer intent");
        expect(importer.search(ScopeId{"tenant-a"}, "publication", 5).size() == 1,
               "recovered import was not searchable");
    }

    std::filesystem::remove(path, error);
    std::filesystem::remove(recovery_path, error);
    return 0;
}

#else
int main() { return 0; }
#endif
