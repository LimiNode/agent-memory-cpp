#include <agent_memory/infrastructure/mdbx/MdbxArtifactCatalog.hpp>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

struct TempTree final {
    explicit TempTree(const char* suffix) {
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        root = std::filesystem::temp_directory_path() /
            (std::string{"agent-memory-artifact-catalog-"} + suffix + "-" + std::to_string(stamp));
        std::filesystem::create_directories(root);
    }
    ~TempTree() { std::error_code error; std::filesystem::remove_all(root, error); }
    std::filesystem::path root;
};

agent_memory::MdbxArtifactCatalogOptions options(const TempTree& tree) {
    agent_memory::MdbxArtifactCatalogOptions result;
    result.path = (tree.root / "catalog.mdbx").string();
    result.table_prefix = "raw_catalog_test";
    result.raw_cas_root = tree.root / "cas";
    return result;
}

agent_memory::ArtifactCatalogIngestRequest request(std::string body = "# A\n\nUTF-8 body\n") {
    agent_memory::ArtifactCatalogIngestRequest result;
    result.source_revision_id = agent_memory::SourceRevisionId("A@1");
    result.binding_id = agent_memory::ArtifactBindingId("binding-A-1");
    result.role = "source_original";
    result.retention = agent_memory::ArtifactRetentionClass::SourceOriginal;
    result.media_type = "text/markdown";
    result.bound_at_ms = 100;
    result.body = std::move(body);
    return result;
}

} // namespace

int main() {
    using namespace agent_memory;
    TempTree tree("lifecycle");
    const auto first_request = request();
    {
        MdbxArtifactCatalog catalog(options(tree));
        const auto published = catalog.ingest(first_request);
        expect(published.status == ArtifactCatalogStatus::Ok && published.record,
               "initial Raw artifact ingest failed");
        const auto body = catalog.read_body(first_request.binding_id);
        expect(body.status == ArtifactCatalogStatus::Ok && body.bytes == first_request.body,
               "initial Raw artifact body did not round-trip");
        expect(catalog.gc_candidates().empty(),
               "referenced Raw artifact was incorrectly reported as a GC candidate");
        const auto by_revision = catalog.read_source_revision(first_request.source_revision_id);
        expect(by_revision.status == ArtifactCatalogStatus::Ok && by_revision.record,
               "source revision binding was not readable");
        const auto retry = catalog.ingest(first_request);
        expect(retry.status == ArtifactCatalogStatus::Ok,
               "idempotent ingest retry was rejected");

        auto second_request = request("# A\n\nrevision two\n");
        second_request.source_revision_id = SourceRevisionId("A@2");
        second_request.binding_id = ArtifactBindingId("binding-A-2");
        const auto second = catalog.ingest(second_request);
        expect(second.status == ArtifactCatalogStatus::Ok && second.record,
               "second source revision ingest failed");
        const auto first_again = catalog.read_source_revision(SourceRevisionId("A@1"));
        expect(first_again.status == ArtifactCatalogStatus::Ok && first_again.record &&
                   first_again.record->artifact.id != second.record->artifact.id,
               "A@1 was not preserved after A@2 publication");
        auto conflicting = first_request;
        conflicting.body = "different bytes";
        const auto conflict = catalog.ingest(conflicting);
        expect(conflict.status == ArtifactCatalogStatus::Conflict,
               "binding overwrite conflict was not rejected");
    }
    {
        MdbxArtifactCatalog reopened(options(tree));
        const auto body = reopened.read_body(first_request.binding_id);
        expect(body.status == ArtifactCatalogStatus::Ok && body.bytes == first_request.body,
               "reopen lost Raw catalog body");
    }

    for(const auto point : {ArtifactCatalogFaultPoint::BeforeIntent,
                            ArtifactCatalogFaultPoint::AfterIntent,
                            ArtifactCatalogFaultPoint::BeforeCasPublication,
                            ArtifactCatalogFaultPoint::AfterCasPublication,
                            ArtifactCatalogFaultPoint::BeforeCatalogCommit,
                            ArtifactCatalogFaultPoint::AfterCatalogCommit,
                            ArtifactCatalogFaultPoint::BeforeReceiptFinalization}) {
        TempTree recovery_tree("recovery");
        auto fault_options = options(recovery_tree);
        bool injected = false;
        fault_options.fault_injector = [&](ArtifactCatalogFaultPoint actual) {
            if(actual == point) {
                injected = true;
                throw std::runtime_error("injected artifact publication fault");
            }
        };
        try {
            MdbxArtifactCatalog faulty(std::move(fault_options));
            static_cast<void>(faulty.ingest(first_request));
        } catch(const std::runtime_error&) {
            expect(injected, "fault injector did not run");
        }
        auto recovered_options = options(recovery_tree);
        MdbxArtifactCatalog recovered(std::move(recovered_options));
        if(point == ArtifactCatalogFaultPoint::BeforeIntent ||
           point == ArtifactCatalogFaultPoint::AfterIntent ||
           point == ArtifactCatalogFaultPoint::BeforeCasPublication) {
            const auto retried = recovered.ingest(first_request);
            expect(retried.status == ArtifactCatalogStatus::Ok,
                   "pending intent could not be retried after pre-CAS failure");
        }
        const auto body = recovered.read_body(first_request.binding_id);
        expect(body.status == ArtifactCatalogStatus::Ok && body.bytes == first_request.body,
               (std::string{"interrupted Raw publication did not recover at point "} +
                std::to_string(static_cast<int>(point))).c_str());
    }

    TempTree corruption_tree("corrupt");
    {
        MdbxArtifactCatalog catalog(options(corruption_tree));
        const auto published = catalog.ingest(first_request);
        expect(published.status == ArtifactCatalogStatus::Ok && published.record,
               "corruption fixture setup failed");
        const auto path = catalog.raw_cas().root() / "sha256" /
            published.record->artifact.id.value().substr(7, 2) /
            (published.record->artifact.id.value().substr(7) + ".raw");
        std::ofstream output(path, std::ios::binary | std::ios::trunc);
        output << "tampered";
    }
    {
        MdbxArtifactCatalog reopened(options(corruption_tree));
        const auto body = reopened.read_body(first_request.binding_id);
        expect(body.status == ArtifactCatalogStatus::Corrupt,
               "corrupt Raw body was accepted by catalog read");
    }

    std::cout << "MDBX artifact catalog: PASS\n";
    return 0;
}
