#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>

#include <filesystem>
#include <iostream>
#include <utility>

int main() {
    using namespace agent_memory;
    const auto path = std::filesystem::temp_directory_path() /
        "agent_memory_resource_importer_example.mdbx";
    std::error_code error;
    std::filesystem::remove(path, error);

    MdbxResourceImporterOptions options;
    options.path = path.string();
    MdbxResourceImporter importer{std::move(options)};

    ObservedResource resource;
    resource.scope_id = ScopeId{"example"};
    resource.resource_id = ResourceId{"resource:welcome"};
    resource.source_uri = "https://example.test/welcome";
    resource.utf8_body = "Durable lexical resource memory is searchable.";
    const auto result = importer.import(std::move(resource));

    const auto hits = importer.search(ScopeId{"example"}, "searchable", 5);
    if(result.revision.generation != 1 || hits.size() != 1) {
        std::cerr << "resource importer example failed\n";
        return 1;
    }
    std::cout << "imported " << result.resource_id.value()
              << " and found " << hits.size() << " lexical hit(s)\n";
    std::filesystem::remove(path, error);
    return 0;
}
