#include <agent_memory/infrastructure/mdbx/MdbxDurableLexicalDocumentMemory.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <filesystem>
#include <iostream>
#include <utility>

int main() {
    using namespace agent_memory;
    const auto path = std::filesystem::temp_directory_path() /
        "agent_memory_durable_lexical_document_memory_example.mdbx";
    std::error_code error;
    std::filesystem::remove(path, error);

    MdbxDurableLexicalDocumentMemoryOptions options;
    options.path = path.string();
    {
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};
        const auto generation = memory.upsert_text(
            ScopeId{"demo"}, ResourceId{"welcome"},
            "https://example.test/welcome",
            "Durable lexical memory survives reopening.");
        if (generation != 1) return 3;
        const auto hits = memory.search(ScopeId{"demo"}, "survives reopening", 1);
        if (hits.size() != 1) return 1;
        std::cout << hits.front().resource_id.value() << " " << hits.front().score
                  << " " << hits.front().text << '\n';
    }

    options.path = path.string();
    MdbxDurableLexicalDocumentMemory reopened{std::move(options)};
    if (reopened.search(ScopeId{"demo"}, "reopening", 1).size() != 1) return 2;
    std::filesystem::remove(path, error);
    return 0;
}

#else
int main() { return 0; }
#endif
