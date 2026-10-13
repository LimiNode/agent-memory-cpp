#include <agent_memory/infrastructure/mdbx/MdbxDurableLexicalDocumentMemory.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <filesystem>
#include <stdexcept>
#include <utility>

namespace {
void expect(bool condition, const char* message) {
    if(!condition) {
        throw std::runtime_error(message);
    }
}
} // namespace

int main() {
    using namespace agent_memory;
    const auto path = std::filesystem::temp_directory_path() /
        "agent_memory_durable_lexical_document_memory_test.mdbx";
    std::error_code error;
    std::filesystem::remove(path, error);

    const ScopeId scope_a{"tenant-a"};
    const ScopeId scope_b{"tenant-b"};
    const ResourceId resource{"resource-1"};
    {
        MdbxDurableLexicalDocumentMemoryOptions options;
        options.path = path.string();
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};

        expect(memory.upsert_text(scope_a, resource, "https://example.test/a",
                                   "Durable lexical memory stores a searchable body.") == 1,
               "initial scope-a generation mismatch");
        expect(memory.upsert_text(scope_b, resource, "https://example.test/b",
                                  "The other tenant has an isolated body.") == 1,
               "initial scope-b generation mismatch");

        const auto hits = memory.search(scope_a, "searchable body", 5);
        expect(hits.size() == 1, "initial lexical search hit count mismatch");
        expect(hits.front().scope_id == scope_a, "scope leaked into lexical hit");
        expect(hits.front().resource_id == resource, "resource identity mismatch");
        expect(hits.front().source_uri == "https://example.test/a", "source URI mismatch");
        expect(hits.front().text.find("searchable body") != std::string::npos,
               "canonical body was not hydrated");
        expect(hits.front().generation == 1, "initial generation mismatch");
        expect(memory.search(scope_b, "searchable", 5).empty(), "scope isolation failed");

        bool rejected = false;
        try { (void)memory.search(scope_a, "", 5); } catch (const std::invalid_argument&) { rejected = true; }
        expect(rejected, "empty query was accepted");
        rejected = false;
        try { (void)memory.search(scope_a, "body", 0); } catch (const std::invalid_argument&) { rejected = true; }
        expect(rejected, "zero limit was accepted");
        rejected = false;
        try { (void)memory.upsert_text(scope_a, ResourceId{"empty"}, "uri", ""); }
        catch (const std::invalid_argument&) { rejected = true; }
        expect(rejected, "empty body was accepted");
    }

    {
        MdbxDurableLexicalDocumentMemoryOptions options;
        options.path = path.string();
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};
        const auto reopened = memory.search(scope_a, "searchable", 5);
        expect(reopened.size() == 1, "reopen lost lexical hit");
        expect(reopened.front().generation == 1, "reopen changed generation");

        expect(memory.upsert_text(scope_a, resource, "https://example.test/a-v2",
                                  "Updated durable body for lexical retrieval.") == 2,
               "body update did not advance generation");
        const auto updated = memory.search(scope_a, "updated", 5);
        expect(updated.size() == 1, "updated lexical hit count mismatch");
        expect(updated.front().resource_id == resource, "update changed resource identity");
        expect(updated.front().source_uri == "https://example.test/a-v2", "updated URI mismatch");
        expect(updated.front().text == "Updated durable body for lexical retrieval.", "updated body mismatch");
        expect(updated.front().generation == 2, "updated generation mismatch");
        expect(memory.search(scope_a, "searchable", 5).empty(), "stale lexical term remained");
        expect(memory.upsert_text(scope_a, resource, "https://example.test/a-v3",
                                  "Updated durable body for lexical retrieval.") == 2,
               "URI-only update changed generation");
        const auto uri_only = memory.search(scope_a, "updated", 5);
        expect(uri_only.size() == 1, "URI-only update lost lexical hit");
        expect(uri_only.front().source_uri == "https://example.test/a-v3", "URI-only update not hydrated");
        expect(uri_only.front().generation == 2, "URI-only update changed generation");
    }

    std::filesystem::remove(path, error);
    return 0;
}

#else
int main() { return 0; }
#endif
