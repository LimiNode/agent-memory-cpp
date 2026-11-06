#include <agent_memory/infrastructure/mdbx/MdbxArtifactCatalog.hpp>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <system_error>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

struct TempTree final {
    explicit TempTree(const char* suffix) {
        const auto parent = std::filesystem::absolute(std::filesystem::temp_directory_path());
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        const auto prefix = std::string{"agent-memory-artifact-catalog-"} + suffix + "-";
        for(std::uint64_t attempt = 0; attempt != 1000U; ++attempt) {
            root = parent / (prefix + std::to_string(stamp) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(root, error)) break;
            if(error && error != std::make_error_code(std::errc::file_exists))
                throw std::runtime_error("failed to reserve test workspace");
            root.clear();
        }
        if(root.empty()) throw std::runtime_error("could not reserve unique test workspace");
        owner_marker = root / ".owner";
        std::ofstream marker(owner_marker, std::ios::binary);
        if(!marker) throw std::runtime_error("failed to mark test workspace ownership");
    }
    ~TempTree() {
        const auto parent = std::filesystem::absolute(std::filesystem::temp_directory_path());
        std::error_code error;
        const auto status = std::filesystem::symlink_status(owner_marker, error);
        if(error || !std::filesystem::is_regular_file(status) ||
           root.parent_path() != parent || root.filename().string().rfind(
               "agent-memory-artifact-catalog-", 0) != 0)
            return;
        std::filesystem::remove_all(root, error);
    }
    std::filesystem::path root;
    std::filesystem::path owner_marker;
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
        second_request.binding_id = ArtifactBindingId("binding-A-2");
        second_request.role = "extracted_text";
        const auto second = catalog.ingest(second_request);
        expect(second.status == ArtifactCatalogStatus::Ok && second.record,
               "second binding for one source revision failed");
        const auto bindings = catalog.read_source_revision_bindings(SourceRevisionId("A@1"));
        expect(bindings.status == ArtifactCatalogStatus::Ok && bindings.records.size() == 2U,
               "source revision did not enumerate both artifact bindings");
        expect(catalog.read_source_revision(SourceRevisionId("A@1")).status ==
                   ArtifactCatalogStatus::Conflict,
               "ambiguous source revision lookup silently selected a binding");
        auto third_request = request("# A\n\nrevision two\n");
        third_request.source_revision_id = SourceRevisionId("A@2");
        third_request.binding_id = ArtifactBindingId("binding-A-3");
        const auto third = catalog.ingest(third_request);
        expect(third.status == ArtifactCatalogStatus::Ok && third.record,
               "second source revision ingest failed");
        auto conflicting = first_request;
        conflicting.body = "different bytes";
        const auto conflict = catalog.ingest(conflicting);
        expect(conflict.status == ArtifactCatalogStatus::Conflict,
               "binding overwrite conflict was not rejected");
    }

    TempTree pending_tree("pending");
    {
        auto fault_options = options(pending_tree);
        fault_options.fault_injector = [](ArtifactCatalogFaultPoint point) {
            if(point == ArtifactCatalogFaultPoint::AfterIntent)
                throw std::runtime_error("injected pending intent fault");
        };
        try {
            MdbxArtifactCatalog faulty(std::move(fault_options));
            static_cast<void>(faulty.ingest(first_request));
        } catch(const std::runtime_error&) {
        }
    }
    {
        MdbxArtifactCatalog catalog(options(pending_tree));
        auto conflicting = first_request;
        conflicting.body = "different pending bytes";
        expect(catalog.ingest(conflicting).status == ArtifactCatalogStatus::Conflict,
               "pending intent was overwritten by a conflicting request");
        expect(catalog.ingest(first_request).status == ArtifactCatalogStatus::Ok,
               "original pending intent could not be resumed");
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
    std::filesystem::path corrupt_path;
    {
        MdbxArtifactCatalog catalog(options(corruption_tree));
        const auto published = catalog.ingest(first_request);
        expect(published.status == ArtifactCatalogStatus::Ok && published.record,
               "corruption fixture setup failed");
        corrupt_path = catalog.raw_cas().root() / "sha256" /
            published.record->artifact.id.value().substr(7, 2) /
            (published.record->artifact.id.value().substr(7) + ".raw");
        std::ofstream output(corrupt_path, std::ios::binary | std::ios::trunc);
        output << "tampered";
    }
    {
        MdbxArtifactCatalog reopened(options(corruption_tree));
        const auto body = reopened.read_body(first_request.binding_id);
        expect(body.status == ArtifactCatalogStatus::Corrupt,
               "corrupt Raw body was accepted by catalog read");
        std::error_code error;
        std::filesystem::remove(corrupt_path, error);
        expect(!error, "failed to remove CAS body for missing-body control");
        expect(reopened.read_body(first_request.binding_id).status == ArtifactCatalogStatus::NotFound,
               "missing Raw body did not return NotFound");
    }

    TempTree bounded_tree("bounded");
    auto bounded_options = options(bounded_tree);
    bounded_options.max_full_body_bytes = 4;
    {
        MdbxArtifactCatalog catalog(std::move(bounded_options));
        expect(catalog.ingest(first_request).status == ArtifactCatalogStatus::Ok,
               "bounded fixture setup failed");
        expect(catalog.read_body(first_request.binding_id).status ==
                   ArtifactCatalogStatus::InvalidArgument,
               "oversized full-body materialization was not rejected");
        const auto range = catalog.read_body_range(first_request.binding_id, 0, 4);
        expect(range.status == ArtifactCatalogStatus::Ok && range.bytes == first_request.body.substr(0, 4),
               "bounded range read failed");
        expect(catalog.read_body_range(first_request.binding_id, 1000, 1).status ==
                   ArtifactCatalogStatus::InvalidArgument,
               "out-of-range body read was accepted");
    }

    TempTree foreign_tree("foreign");
    const auto foreign_file = foreign_tree.root / "keep.txt";
    {
        std::ofstream output(foreign_file, std::ios::binary);
        output << "must survive";
    }
    {
        TempTree owner_tree("owner");
        static_cast<void>(owner_tree);
    }
    expect(std::filesystem::is_regular_file(foreign_file),
           "test workspace cleanup removed a foreign workspace");

    std::cout << "MDBX artifact catalog: PASS\n";
    return 0;
}
