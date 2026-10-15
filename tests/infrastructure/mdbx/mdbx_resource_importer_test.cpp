#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <array>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

void expect_digest(const agent_memory::ResourceBodyDigest& digest) {
    constexpr std::array<std::uint8_t, 32> kAbcSha256{{
        0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea,
        0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22, 0x23,
        0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c,
        0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad
    }};
    expect(digest.bytes == kAbcSha256, "SHA-256 abc vector mismatch");
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

void use_test_clock(
    agent_memory::MdbxResourceImporterOptions& options,
    std::uint64_t now_ms
) {
    options.ownership_lease_ms = 100;
    options.clock_ms = [now_ms] { return now_ms; };
}

std::string canonical_document_id(
    std::string_view scope,
    std::string_view resource
) {
    static constexpr char digits[] = "0123456789abcdef";
    const auto encode = [&](std::string_view value) {
        std::string encoded;
        encoded.reserve(value.size() * 2U);
        for(const auto byte : value) {
            const auto octet = static_cast<unsigned char>(byte);
            encoded.push_back(digits[octet >> 4U]);
            encoded.push_back(digits[octet & 0x0fU]);
        }
        return encoded;
    };
    return "durable-lexical:" + encode(scope) + ':' + encode(resource);
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
        bool oversized_search_rejected = false;
        try {
            (void)importer.search(
                ScopeId{"tenant-a"},
                "searchable",
                kMdbxResourceImporterMaxSearchLimit + 1
            );
        } catch(const std::invalid_argument&) { oversized_search_rejected = true; }
        expect(oversized_search_rejected,
               "search accepted a limit beyond its bounded refill window");

        const auto uri_only = importer.import(original(
            "resource:one",
            "https://example.test/renamed",
            "Durable importer keeps this searchable source."
        ));
        expect(uri_only.revision.generation == 1,
               "metadata-only update created a false body revision");

        bool pipeline_rejected = false;
        auto pipeline_changed = original(
            "resource:one", "https://example.test/renamed", "Durable importer keeps this searchable source."
        );
        pipeline_changed.pipeline_config_hash = 2;
        try { (void)importer.import(std::move(pipeline_changed)); }
        catch(const std::runtime_error&) { pipeline_rejected = true; }
        expect(pipeline_rejected,
               "pipeline metadata changed without a new body generation");

        auto sha = importer.import(original("resource:sha", "https://example.test/sha", "abc"));
        expect(sha.revision.body_digest.has_value(), "SHA vector import has no digest");
        expect_digest(*sha.revision.body_digest);

        bool scope_rejected = false;
        auto foreign_scope = original(
            "resource:one", "https://example.test/foreign", "Durable importer keeps this searchable source."
        );
        foreign_scope.scope_id = ScopeId{"tenant-b"};
        try { (void)importer.import(std::move(foreign_scope)); }
        catch(const std::invalid_argument&) { scope_rejected = true; }
        expect(scope_rejected, "ResourceId was reused across scopes");

        bool rejected = false;
        auto derived = original("resource:derived", "https://example.test/derived", "derived");
        derived.text_origin = SourceTextOrigin::DerivedExtraction;
        try { (void)importer.import(derived); }
        catch(const std::invalid_argument&) { rejected = true; }
        expect(rejected, "derived text without provenance was accepted");

        derived.derived_text_provenance = DerivedTextProvenance{
            "text/html", "example-extractor", "1"
        };
        const auto derived_result = importer.import(std::move(derived));
        const auto derived_manifest = importer.find_manifest(derived_result.resource_id);
        expect(
            derived_manifest &&
                derived_manifest->revision.text_origin == SourceTextOrigin::DerivedExtraction &&
                derived_manifest->revision.derived_text_provenance &&
                derived_manifest->revision.derived_text_provenance->extractor_id ==
                    "example-extractor",
            "valid derived provenance was not persisted"
        );

        auto malformed = original("resource:malformed", "https://example.test/malformed", "x");
        malformed.utf8_body = std::string{"\xc3\x28", 2};
        bool malformed_rejected = false;
        try { (void)importer.import(std::move(malformed)); }
        catch(const std::invalid_argument&) { malformed_rejected = true; }
        expect(malformed_rejected, "malformed UTF-8 was accepted");
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
        use_test_clock(options, 100);
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
        expect(importer.search(ScopeId{"tenant-a"}, "publication", 5).empty(),
               "pending lexical publication was visible before manifest commit");
    }
    {
        MdbxResourceImporterOptions options;
        options.path = recovery_path.string();
        use_test_clock(options, 1'000);
        MdbxResourceImporter importer{std::move(options)};
        const auto manifest = importer.find_manifest(ResourceId{"resource:recovery"});
        expect(manifest && manifest->revision.generation == 1,
               "reopen did not recover pending importer intent");
        expect(importer.search(ScopeId{"tenant-a"}, "publication", 5).size() == 1,
               "recovered import was not searchable");
    }

    for(const auto point : {
        ResourceImporterFaultPoint::AfterIntent,
        ResourceImporterFaultPoint::BeforeLexicalPublication,
        ResourceImporterFaultPoint::AfterLexicalPublication,
        ResourceImporterFaultPoint::BeforeManifestPublication,
        ResourceImporterFaultPoint::AfterManifestPublication
    }) {
        const auto fault_path = test_path(("fault_" +
            std::to_string(static_cast<int>(point))).c_str());
        std::filesystem::remove(fault_path, error);
        {
            MdbxResourceImporterOptions options;
            options.path = fault_path.string();
            use_test_clock(options, 100);
            options.fault_injector = [point](ResourceImporterFaultPoint observed) {
                if(observed == point) throw std::runtime_error("fault fixture interruption");
            };
            MdbxResourceImporter importer{std::move(options)};
            bool interrupted = false;
            try {
                (void)importer.import(original(
                    "resource:fault", "https://example.test/fault", "fault recovery source"
                ));
            } catch(const std::runtime_error&) { interrupted = true; }
            expect(interrupted, "fault point did not interrupt import");
            expect(importer.search(ScopeId{"tenant-a"}, "recovery", 5).empty(),
                   "faulted import leaked an uncommitted lexical result");
        }
        {
            MdbxResourceImporterOptions options;
            options.path = fault_path.string();
            use_test_clock(options, 1'000);
            MdbxResourceImporter importer{std::move(options)};
            expect(importer.search(ScopeId{"tenant-a"}, "recovery", 5).size() == 1,
                   "reopen did not recover faulted import");
        }
        std::filesystem::remove(fault_path, error);
    }

    const auto refill_path = test_path("search_refill");
    std::filesystem::remove(refill_path, error);
    {
        const auto context = MdbxStorageContext::open(refill_path.string());
        MdbxResourceImporterOptions committed_options;
        committed_options.context = context;
        MdbxResourceImporter committed{std::move(committed_options)};
        (void)committed.import(original(
            "resource:visible", "https://example.test/visible",
            "shared retrieval phrase visible"
        ));

        MdbxResourceImporterOptions pending_options;
        pending_options.context = context;
        pending_options.fault_injector = [](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::AfterLexicalPublication) {
                throw std::runtime_error("leave lexical intent pending");
            }
        };
        MdbxResourceImporter pending{std::move(pending_options)};
        bool interrupted = false;
        try {
            (void)pending.import(original(
                "resource:pending", "https://example.test/pending",
                "shared retrieval phrase pending"
            ));
        } catch(const std::runtime_error&) { interrupted = true; }
        expect(interrupted, "search refill fixture did not leave an intent pending");
        const auto visible = pending.search(ScopeId{"tenant-a"}, "shared retrieval", 1);
        expect(visible.size() == 1 && visible.front().resource_id == ResourceId{"resource:visible"},
               "pending filtering did not refill the bounded lexical candidate set");
    }
    std::filesystem::remove(refill_path, error);

    const auto foreign_manifest_path = test_path("foreign_schema");
    std::filesystem::remove(foreign_manifest_path, error);
    {
        const auto context = MdbxStorageContext::open(foreign_manifest_path.string());
        MdbxResourceManifestStorageOptions storage_options;
        storage_options.context = context;
        MdbxResourceManifestStorage storage{std::move(storage_options)};
        ResourceManifest foreign;
        foreign.revision.resource_id = ResourceId{"resource:foreign-schema"};
        foreign.revision.generation = 1;
        foreign.revision.content_hash = 1;
        foreign.records = {
            DerivedRecordRef{
                DerivedRecordKind::Document,
                ChunkId{},
                "foreign-document",
                0
            }
        };
        foreign.schema = ResourceManifestSchema{"other.writer", 7};
        storage.upsert_manifest(std::move(foreign));

        MdbxResourceImporterOptions options;
        options.context = context;
        MdbxResourceImporter importer{std::move(options)};
        bool rejected = false;
        try {
            (void)importer.import(original(
                "resource:foreign-schema",
                "https://example.test/foreign-schema",
                "foreign schema must remain untouched"
            ));
        } catch(const std::runtime_error&) { rejected = true; }
        expect(rejected, "foreign manifest schema was accepted for overwrite");
        const mdbxc::KeyValueTable<std::string, std::string> bindings(
            context->connection(),
            "agent_memory_resource_import_bindings"
        );
        expect(!bindings.find("resource:foreign-schema"),
               "failed foreign-schema import left a durable scope binding");
    }
    std::filesystem::remove(foreign_manifest_path, error);

    const auto concurrency_path = test_path("concurrency");
    std::filesystem::remove(concurrency_path, error);
    {
        const auto context = MdbxStorageContext::open(concurrency_path.string());
        MdbxResourceImporterOptions first_options;
        first_options.context = context;
        use_test_clock(first_options, 100);
        first_options.fault_injector = [](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::AfterIntent) {
                throw std::runtime_error("leave pending intent");
            }
        };
        MdbxResourceImporter first{std::move(first_options)};
        bool first_interrupted = false;
        try {
            (void)first.import(original(
                "resource:concurrent", "https://example.test/concurrent", "concurrent source"
            ));
        } catch(const std::runtime_error&) { first_interrupted = true; }
        expect(first_interrupted, "concurrency fixture did not leave pending intent");
        MdbxResourceImporterOptions second_options;
        second_options.context = context;
        std::uint64_t second_now = 150;
        second_options.ownership_lease_ms = 100;
        second_options.clock_ms = [&second_now] { return second_now; };
        MdbxResourceImporter second{std::move(second_options)};
        expect(!second.find_manifest(ResourceId{"resource:concurrent"}),
               "opening another importer revoked an unexpired owner lease");
        bool foreign_rejected = false;
        try {
            (void)second.import(original(
                "resource:concurrent", "https://example.test/other", "different source"
            ));
        } catch(const std::runtime_error&) { foreign_rejected = true; }
        expect(foreign_rejected, "foreign concurrent payload replaced pending intent");
        bool same_payload_rejected = false;
        try {
            (void)second.import(original(
                "resource:concurrent", "https://example.test/concurrent", "concurrent source"
            ));
        } catch(const std::runtime_error&) { same_payload_rejected = true; }
        expect(same_payload_rejected,
               "same payload was allowed to execute without owning the live lease");
        second_now = 1'000;
        const auto recovered_result = second.import(original(
            "resource:concurrent", "https://example.test/other", "new concurrent source"
        ));
        expect(recovered_result.revision.generation == 2,
               "an existing executor did not recover P before importing Q on demand");
        const auto completed = second.find_manifest(ResourceId{"resource:concurrent"});
        expect(completed && completed->revision.generation == 2 &&
                   completed->revision.source_uri == "https://example.test/other",
               "on-demand recovery did not publish the replacement payload");
    }
    std::filesystem::remove(concurrency_path, error);

    const auto fencing_path = test_path("publication_fencing");
    std::filesystem::remove(fencing_path, error);
    {
        const auto context = MdbxStorageContext::open(fencing_path.string());
        std::mutex coordination_mutex;
        std::condition_variable coordination;
        bool paused_before_lexical = false;
        bool resume_stale_writer = false;
        bool stale_writer_fenced = false;

        MdbxResourceImporterOptions stale_options;
        stale_options.context = context;
        use_test_clock(stale_options, 100);
        stale_options.fault_injector = [&](ResourceImporterFaultPoint point) {
            if(point != ResourceImporterFaultPoint::BeforeLexicalPublication) return;
            std::unique_lock<std::mutex> lock(coordination_mutex);
            paused_before_lexical = true;
            coordination.notify_all();
            coordination.wait(lock, [&] { return resume_stale_writer; });
        };
        MdbxResourceImporter stale{std::move(stale_options)};
        std::thread stale_thread([&] {
            try {
                (void)stale.import(original(
                    "resource:fenced",
                    "https://example.test/fenced-p",
                    "obsoletealpha"
                ));
            } catch(const std::runtime_error&) {
                stale_writer_fenced = true;
            }
        });

        {
            std::unique_lock<std::mutex> lock(coordination_mutex);
            coordination.wait(lock, [&] { return paused_before_lexical; });
        }

        MdbxResourceImporterOptions successor_options;
        successor_options.context = context;
        use_test_clock(successor_options, 1'000);
        MdbxResourceImporter successor{std::move(successor_options)};
        const auto recovered = successor.find_manifest(ResourceId{"resource:fenced"});
        expect(recovered && recovered->revision.generation == 1,
               "expired owner recovery did not publish payload P");
        const auto latest = successor.import(original(
            "resource:fenced",
            "https://example.test/fenced-q",
            "currentomega"
        ));
        expect(latest.revision.generation == 2,
               "successor did not publish payload Q after recovery");

        {
            std::lock_guard<std::mutex> lock(coordination_mutex);
            resume_stale_writer = true;
        }
        coordination.notify_all();
        stale_thread.join();
        expect(stale_writer_fenced,
               "stale executor was not rejected at lexical publication");
        MdbxCanonicalContentStore canonical(MdbxCanonicalContentStoreOptions{
            {}, "agent_memory", false, kDefaultMdbxMaxDbs, context
        });
        const auto current_canonical = canonical.read_current(DocumentId{
            canonical_document_id("tenant-a", "resource:fenced")
        });
        expect(current_canonical && !current_canonical->blocks.empty() &&
                   current_canonical->blocks.front().text == "currentomega",
               "stale executor changed the active canonical head after fencing");
        const auto repeated = successor.import(original(
            "resource:fenced",
            "https://example.test/fenced-q",
            "currentomega"
        ));
        expect(repeated.revision.generation == 2,
               "idempotent resource import did not publish its current manifest generation");
        const auto after_repeat = canonical.read_current(DocumentId{
            canonical_document_id("tenant-a", "resource:fenced")
        });
        expect(after_repeat && after_repeat->revision == current_canonical->revision,
               "idempotent payload advanced the canonical revision");
        expect(successor.search(ScopeId{"tenant-a"}, "currentomega", 5).size() == 1,
               "stale executor replaced payload Q in lexical search");
        expect(successor.search(ScopeId{"tenant-a"}, "obsoletealpha", 5).empty(),
               "stale payload P remained active after payload Q");

        MdbxResourceImporterOptions reopen_options;
        reopen_options.context = context;
        use_test_clock(reopen_options, 1'000);
        MdbxResourceImporter reopened{std::move(reopen_options)};
        const auto reopened_manifest = reopened.find_manifest(ResourceId{"resource:fenced"});
        expect(reopened_manifest && reopened_manifest->revision.generation == 2,
               "reopen lost the successor manifest generation");
        expect(reopened.search(ScopeId{"tenant-a"}, "currentomega", 5).size() == 1,
               "reopen did not preserve payload Q lexical visibility");
        const auto reopened_canonical = canonical.read_current(DocumentId{
            canonical_document_id("tenant-a", "resource:fenced")
        });
        expect(reopened_canonical && !reopened_canonical->blocks.empty() &&
                   reopened_canonical->blocks.front().text == "currentomega",
               "reopen changed the fenced canonical head");
    }
    std::filesystem::remove(fencing_path, error);

    std::filesystem::remove(path, error);
    std::filesystem::remove(recovery_path, error);
    return 0;
}

#else
int main() { return 0; }
#endif
