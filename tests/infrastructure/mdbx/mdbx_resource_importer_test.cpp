#include <agent_memory/infrastructure/mdbx/MdbxResourceImporter.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <array>
#include <filesystem>
#include <stdexcept>
#include <string>

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
        MdbxResourceImporter importer{std::move(options)};
        const auto manifest = importer.find_manifest(ResourceId{"resource:recovery"});
        expect(manifest && manifest->revision.generation == 1,
               "reopen did not recover pending importer intent");
        expect(importer.search(ScopeId{"tenant-a"}, "publication", 5).size() == 1,
               "recovered import was not searchable");
    }

    for(const auto point : {
        ResourceImporterFaultPoint::AfterIntent,
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
        first_options.fault_injector = [](ResourceImporterFaultPoint point) {
            if(point == ResourceImporterFaultPoint::AfterIntent) {
                throw std::runtime_error("leave pending intent");
            }
        };
        MdbxResourceImporter first{std::move(first_options)};
        MdbxResourceImporterOptions second_options;
        second_options.context = context;
        MdbxResourceImporter second{std::move(second_options)};
        bool first_interrupted = false;
        try {
            (void)first.import(original(
                "resource:concurrent", "https://example.test/concurrent", "concurrent source"
            ));
        } catch(const std::runtime_error&) { first_interrupted = true; }
        expect(first_interrupted, "concurrency fixture did not leave pending intent");
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
               "same payload was allowed to execute without an ownership token");
        MdbxResourceImporterOptions recovery_options;
        recovery_options.context = context;
        MdbxResourceImporter recovery{std::move(recovery_options)};
        const auto completed = recovery.find_manifest(ResourceId{"resource:concurrent"});
        expect(completed && completed->revision.generation == 1,
               "a new executor did not recover the fenced pending import");
    }
    std::filesystem::remove(concurrency_path, error);

    std::filesystem::remove(path, error);
    std::filesystem::remove(recovery_path, error);
    return 0;
}

#else
int main() { return 0; }
#endif
