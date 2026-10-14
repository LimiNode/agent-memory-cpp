#include <agent_memory/infrastructure/mdbx/MdbxDurableLexicalDocumentMemory.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <filesystem>
#include <memory>
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

    const ScopeId recovery_scope{"recovery"};
    const ResourceId recovery_resource{"interrupted-create"};
    {
        MdbxDurableLexicalDocumentMemoryOptions options;
        options.path = path.string();
        options.fault_injector = [](DurableLexicalDocumentMemoryFaultPoint point) {
            if(point == DurableLexicalDocumentMemoryFaultPoint::AfterCanonicalWrite) {
                throw std::runtime_error("injected interruption after canonical write");
            }
        };
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};
        bool interrupted = false;
        try {
            static_cast<void>(memory.upsert_text(
                recovery_scope,
                recovery_resource,
                "https://example.test/recovery-v1",
                "Recovery preserves the first canonical publication."
            ));
        } catch(const std::runtime_error&) {
            interrupted = true;
        }
        expect(interrupted, "after-canonical fault injection did not interrupt upsert");
    }

    {
        MdbxDurableLexicalDocumentMemoryOptions options;
        options.path = path.string();
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};
        const auto recovered = memory.search(recovery_scope, "first canonical", 5);
        expect(recovered.size() == 1, "reopen did not recover interrupted create");
        expect(recovered.front().source_uri == "https://example.test/recovery-v1",
               "recovered create lost its URI");
        expect(memory.upsert_text(
                   recovery_scope,
                   recovery_resource,
                   "https://example.test/recovery-v1",
                   "Recovery preserves the first canonical publication.") == 1,
               "retry after recovered create was not idempotent");
    }

    {
        MdbxDurableLexicalDocumentMemoryOptions options;
        options.path = path.string();
        options.fault_injector = [](DurableLexicalDocumentMemoryFaultPoint point) {
            if(point == DurableLexicalDocumentMemoryFaultPoint::BeforeCatalogPublication) {
                throw std::runtime_error("injected interruption before catalog publication");
            }
        };
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};
        bool interrupted = false;
        try {
            static_cast<void>(memory.upsert_text(
                recovery_scope,
                recovery_resource,
                "https://example.test/recovery-v2",
                "Recovery publishes the updated canonical revision."
            ));
        } catch(const std::runtime_error&) {
            interrupted = true;
        }
        expect(interrupted, "before-catalog fault injection did not interrupt upsert");
    }

    {
        MdbxDurableLexicalDocumentMemoryOptions options;
        options.path = path.string();
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};
        const auto recovered = memory.search(recovery_scope, "updated canonical", 5);
        expect(recovered.size() == 1, "reopen did not recover interrupted update");
        expect(recovered.front().source_uri == "https://example.test/recovery-v2",
               "recovered update mixed body and URI revisions");
        expect(recovered.front().generation == 2,
               "recovered update changed canonical generation unexpectedly");
    }

    const ScopeId tokenless_scope{"tokenless"};
    const ResourceId tokenless_resource{"punctuation"};
    {
        MdbxDurableLexicalDocumentMemoryOptions options;
        options.path = path.string();
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};
        expect(memory.upsert_text(
                   tokenless_scope,
                   ResourceId{"searchable"},
                   "https://example.test/searchable",
                   "Ordinary searchable neighbor remains available.") == 1,
               "searchable neighbor insert failed");
        expect(memory.upsert_text(
                   tokenless_scope,
                   tokenless_resource,
                   "https://example.test/punctuation",
                   "!!!") == 1,
               "tokenless canonical text was not retained");
        expect(memory.search(tokenless_scope, "ordinary", 5).size() == 1,
               "tokenless text disturbed searchable neighbor");
    }

    {
        MdbxDurableLexicalDocumentMemoryOptions options;
        options.path = path.string();
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};
        expect(memory.search(tokenless_scope, "ordinary", 5).size() == 1,
               "tokenless text blocked database reopen");
        expect(memory.upsert_text(
                   tokenless_scope,
                   tokenless_resource,
                   "https://example.test/punctuation",
                   "!!!") == 1,
               "tokenless no-op changed canonical generation");
        expect(memory.upsert_text(
                   tokenless_scope,
                   tokenless_resource,
                   "https://example.test/punctuation-v2",
                   "Punctuation document is searchable now.") == 2,
               "tokenless document did not retain its canonical generation");
        expect(memory.search(tokenless_scope, "punctuation", 5).size() == 1,
               "formerly tokenless document was not indexed after update");
    }

    const ScopeId shared_scope{"shared-context"};
    {
        auto context = MdbxStorageContext::open(
            path.string(),
            false,
            kDefaultMdbxMaxDbs
        );
        MdbxDurableLexicalDocumentMemoryOptions options_a;
        options_a.context = context;
        MdbxDurableLexicalDocumentMemoryOptions options_b;
        options_b.context = context;
        MdbxDurableLexicalDocumentMemory memory_a{std::move(options_a)};
        MdbxDurableLexicalDocumentMemory memory_b{std::move(options_b)};

        expect(memory_a.upsert_text(
                   shared_scope,
                   ResourceId{"a"},
                   "https://example.test/shared-a",
                   "Alpha is written by the first instance.") == 1,
               "first shared-context insert failed");
        expect(memory_b.upsert_text(
                   shared_scope,
                   ResourceId{"b"},
                   "https://example.test/shared-b",
                   "Beta is written by the stale second instance.") == 1,
               "second shared-context insert failed");
        expect(memory_a.search(shared_scope, "beta", 5).size() == 1,
               "first instance did not observe second instance publication");
        expect(memory_b.search(shared_scope, "alpha", 5).size() == 1,
               "second instance overwrote or missed first instance publication");

        expect(memory_a.upsert_text(
                   shared_scope,
                   ResourceId{"a"},
                   "https://example.test/shared-a-v2",
                   "Gamma is the targeted replacement.") == 2,
               "targeted shared-context update failed");
        expect(memory_b.search(shared_scope, "gamma", 5).size() == 1,
               "stale instance did not refresh the changed resource");
        expect(memory_b.search(shared_scope, "beta", 5).size() == 1,
               "targeted update lost the independent resource");
        expect(memory_b.search(shared_scope, "alpha", 5).empty(),
               "targeted update retained stale terms");
    }

    {
        auto context = MdbxStorageContext::open(
            path.string(),
            false,
            kDefaultMdbxMaxDbs
        );
        MdbxDurableLexicalDocumentMemoryOptions options_b;
        options_b.context = context;
        MdbxDurableLexicalDocumentMemory memory_b{std::move(options_b)};
        bool interleaved = false;
        MdbxDurableLexicalDocumentMemoryOptions options_a;
        options_a.context = context;
        options_a.fault_injector = [&](DurableLexicalDocumentMemoryFaultPoint point) {
            if(point == DurableLexicalDocumentMemoryFaultPoint::AfterCanonicalWrite &&
               !interleaved) {
                interleaved = true;
                expect(memory_b.upsert_text(
                           shared_scope,
                           ResourceId{"interleaved-b"},
                           "https://example.test/interleaved-b",
                           "Interleavedbeta body.") == 1,
                       "interleaved publication failed");
            }
        };
        MdbxDurableLexicalDocumentMemory memory_a{std::move(options_a)};
        expect(memory_a.upsert_text(
                   shared_scope,
                   ResourceId{"interleaved-a"},
                   "https://example.test/interleaved-a",
                   "Interleavedalpha body.") == 1,
               "interleaved primary publication failed");
        const auto alpha = memory_a.search(shared_scope, "interleavedalpha", 5);
        const auto beta = memory_a.search(shared_scope, "interleavedbeta", 5);
        expect(alpha.size() == 1 &&
                   alpha.front().source_uri == "https://example.test/interleaved-a" &&
                   alpha.front().generation == 1,
               "interleaved primary resource was not reconciled");
        expect(beta.size() == 1 &&
                   beta.front().source_uri == "https://example.test/interleaved-b" &&
                   beta.front().generation == 1,
               "interleaved foreign resource was lost from the local snapshot");
    }

    {
        auto context = MdbxStorageContext::open(
            path.string(),
            false,
            kDefaultMdbxMaxDbs
        );
        bool injected = false;
        MdbxDurableLexicalDocumentMemoryOptions options;
        options.context = context;
        options.fault_injector = [&](DurableLexicalDocumentMemoryFaultPoint point) {
            if(point == DurableLexicalDocumentMemoryFaultPoint::AfterCatalogPublication &&
               !injected) {
                injected = true;
                throw std::runtime_error("post-publication local failure");
            }
        };
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};
        bool failed = false;
        try {
            static_cast<void>(memory.upsert_text(
                shared_scope,
                ResourceId{"post-publication"},
                "https://example.test/post-publication",
                "Post publication recovery body."));
        } catch(const std::runtime_error&) {
            failed = true;
        }
        expect(failed, "post-publication fault was not injected");
        const auto recovered = memory.search(shared_scope, "recovery", 5);
        expect(recovered.size() == 1 &&
                   recovered.front().source_uri == "https://example.test/post-publication" &&
                   recovered.front().generation == 1,
               "post-publication local failure did not force reconciliation");
    }

    {
        MdbxDurableLexicalDocumentMemoryOptions options;
        options.path = path.string();
        MdbxDurableLexicalDocumentMemory memory{std::move(options)};
        expect(memory.search(shared_scope, "gamma", 5).size() == 1,
               "reopen lost first per-resource catalog entry");
        expect(memory.search(shared_scope, "beta", 5).size() == 1,
               "reopen lost second per-resource catalog entry");
    }

    std::filesystem::remove(path, error);

    return 0;
}

#else
int main() { return 0; }
#endif
