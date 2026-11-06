#include <agent_memory/infrastructure/mdbx/MdbxVerifiedArtifactHydration.hpp>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <utility>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

struct TempTree final {
    TempTree() {
        const auto parent = std::filesystem::absolute(std::filesystem::temp_directory_path());
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        for(std::uint64_t attempt = 0; attempt != 1000U; ++attempt) {
            root = parent / ("agent-memory-verified-hydration-" +
                             std::to_string(stamp) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(root, error)) break;
            if(error && error != std::make_error_code(std::errc::file_exists))
                throw std::runtime_error("failed to reserve test workspace");
            root.clear();
        }
        if(root.empty()) throw std::runtime_error("could not reserve test workspace");
        owner = root / ".owner";
        std::ofstream marker(owner, std::ios::binary);
        if(!marker) throw std::runtime_error("failed to mark test workspace");
    }
    ~TempTree() {
        std::error_code error;
        const auto parent = std::filesystem::absolute(std::filesystem::temp_directory_path());
        const auto status = std::filesystem::symlink_status(owner, error);
        if(!error && std::filesystem::is_regular_file(status) && root.parent_path() == parent &&
           root.filename().string().rfind("agent-memory-verified-hydration-", 0) == 0)
            std::filesystem::remove_all(root, error);
    }
    std::filesystem::path root;
    std::filesystem::path owner;
};

agent_memory::MdbxArtifactCatalogOptions options(const TempTree& tree) {
    agent_memory::MdbxArtifactCatalogOptions result;
    result.path = (tree.root / "catalog.mdbx").string();
    result.table_prefix = "verified_hydration_test";
    result.raw_cas_root = tree.root / "cas";
    result.raw_cas_options.max_read_range_bytes = 1024;
    return result;
}

agent_memory::ArtifactCatalogIngestRequest ingest_request() {
    agent_memory::ArtifactCatalogIngestRequest request;
    request.source_revision_id = agent_memory::SourceRevisionId("article@1");
    request.binding_id = agent_memory::ArtifactBindingId("article-body@1");
    request.role = "source_original";
    request.retention = agent_memory::ArtifactRetentionClass::SourceOriginal;
    request.media_type = "text/markdown";
    request.body = "normalized source bytes\n";
    return request;
}

std::filesystem::path cas_path(
    const agent_memory::FileRawBlobStore& cas,
    const agent_memory::ArtifactId& artifact_id) {
    return cas.root() / "sha256" / artifact_id.value().substr(7, 2) /
        (artifact_id.value().substr(7) + ".raw");
}

} // namespace

int main() {
    using namespace agent_memory;
    TempTree tree;
    MdbxArtifactCatalog catalog(options(tree));
    const auto request = ingest_request();
    const auto published = catalog.ingest(request);
    expect(published.status == ArtifactCatalogStatus::Ok && published.record,
           "artifact catalog ingest failed");

    MdbxVerifiedArtifactHydration hydration(catalog);
    VerifiedHydrationRequest read;
    read.source_revision_id = request.source_revision_id;
    read.binding_id = request.binding_id;
    read.expected_scope = "workspace-a";
    read.binding_scope = "workspace-a";
    read.current_source_revision_id = request.source_revision_id;
    read.expected_artifact_id = published.record->artifact.id;
    read.expected_digest = published.record->artifact.digest;
    read.offset = 2;
    read.length = 10;
    const auto hydrated = hydration.hydrate(read);
    expect(hydrated.status == VerifiedHydrationStatus::Hydrated,
           "exact source hydration did not succeed");
    expect(hydrated.bytes == request.body.substr(2, 10),
           "hydration returned the wrong byte range");
    expect(hydrated.source_revision_id == request.source_revision_id,
           "hydration lost source revision identity");

    auto wrong_scope = read;
    wrong_scope.binding_scope = "workspace-b";
    const auto wrong_scope_result = hydration.hydrate(wrong_scope);
    expect(wrong_scope_result.status == VerifiedHydrationStatus::WrongScope &&
               wrong_scope_result.bytes.empty(),
           "wrong-scope hydration exposed bytes");

    auto stale = read;
    stale.current_source_revision_id = SourceRevisionId("article@2");
    const auto stale_result = hydration.hydrate(stale);
    expect(stale_result.status == VerifiedHydrationStatus::Stale && stale_result.bytes.empty(),
           "stale hydration exposed bytes");

    auto forged_digest = read;
    forged_digest.expected_digest->value[0] ^= 0x01U;
    const auto forged_result = hydration.hydrate(forged_digest);
    expect(forged_result.status == VerifiedHydrationStatus::Corrupt && forged_result.bytes.empty(),
           "forged digest was accepted");

    auto forged_artifact = read;
    forged_artifact.expected_artifact_id =
        ArtifactId("sha256:" + std::string(64, '0'));
    const auto forged_artifact_result = hydration.hydrate(forged_artifact);
    expect(forged_artifact_result.status == VerifiedHydrationStatus::Corrupt &&
               forged_artifact_result.bytes.empty(),
           "forged artifact identity was accepted");

    auto out_of_range = read;
    out_of_range.offset = published.record->artifact.size_bytes;
    out_of_range.length = 1;
    const auto out_of_range_result = hydration.hydrate(out_of_range);
    expect(out_of_range_result.status == VerifiedHydrationStatus::InvalidArgument &&
               out_of_range_result.bytes.empty(),
           "out-of-range hydration was accepted");

    auto missing = read;
    missing.binding_id = ArtifactBindingId("missing-binding");
    const auto missing_result = hydration.hydrate(missing);
    expect(missing_result.status == VerifiedHydrationStatus::Missing && missing_result.bytes.empty(),
           "missing binding did not fail closed");

    TempTree summary_tree;
    {
        auto summary_options = options(summary_tree);
        summary_options.fault_injector = [](ArtifactCatalogFaultPoint point) {
            if(point == ArtifactCatalogFaultPoint::BeforeReceiptFinalization)
                throw std::runtime_error("leave receipt pending for summary-only test");
        };
        MdbxArtifactCatalog pending(std::move(summary_options));
        bool interrupted = false;
        try {
            static_cast<void>(pending.ingest(ingest_request()));
        } catch(const std::runtime_error&) {
            interrupted = true;
        }
        expect(interrupted, "summary-only fixture did not interrupt finalization");
        const auto record = pending.read_binding(ArtifactBindingId("article-body@1"));
        expect(record.status == ArtifactCatalogStatus::Ok && record.record,
               "pending receipt fixture is not readable");
        MdbxVerifiedArtifactHydration pending_hydration(pending);
        auto summary_read = read;
        summary_read.expected_artifact_id = record.record->artifact.id;
        summary_read.expected_digest = record.record->artifact.digest;
        const auto summary = pending_hydration.hydrate(summary_read);
        expect(summary.status == VerifiedHydrationStatus::SummaryOnly &&
                   summary.bytes.empty(),
               "unfinalized body was not reported as summary-only");
    }
    {
        MdbxArtifactCatalog recovered(options(summary_tree));
        const auto record = recovered.read_binding(ArtifactBindingId("article-body@1"));
        expect(record.status == ArtifactCatalogStatus::Ok && record.record,
               "recovery lost the pending binding");
        MdbxVerifiedArtifactHydration recovered_hydration(recovered);
        auto recovered_read = read;
        recovered_read.expected_artifact_id = record.record->artifact.id;
        recovered_read.expected_digest = record.record->artifact.digest;
        const auto recovered_result = recovered_hydration.hydrate(recovered_read);
        expect(recovered_result.status == VerifiedHydrationStatus::Hydrated &&
                   recovered_result.bytes == std::string("normalized source bytes\n").substr(2, 10),
               "recovery did not restore verified hydration");
    }

    TempTree missing_body_tree;
    {
        MdbxArtifactCatalog missing_catalog(options(missing_body_tree));
        const auto published_missing = missing_catalog.ingest(ingest_request());
        expect(published_missing.status == ArtifactCatalogStatus::Ok && published_missing.record,
               "missing-body fixture ingest failed");
        std::error_code error;
        std::filesystem::remove(
            cas_path(missing_catalog.raw_cas(), published_missing.record->artifact.id), error);
        expect(!error, "failed to remove CAS body");
        MdbxVerifiedArtifactHydration missing_hydration(missing_catalog);
        auto missing_body_read = read;
        missing_body_read.expected_artifact_id = published_missing.record->artifact.id;
        missing_body_read.expected_digest = published_missing.record->artifact.digest;
        const auto missing_body_result = missing_hydration.hydrate(missing_body_read);
        expect(missing_body_result.status == VerifiedHydrationStatus::Missing &&
                   missing_body_result.bytes.empty(),
               "missing CAS body did not fail closed");
    }

    TempTree corrupt_tree;
    {
        MdbxArtifactCatalog corrupt_catalog(options(corrupt_tree));
        const auto published_corrupt = corrupt_catalog.ingest(ingest_request());
        expect(published_corrupt.status == ArtifactCatalogStatus::Ok && published_corrupt.record,
               "corrupt-body fixture ingest failed");
        std::ofstream body(
            cas_path(corrupt_catalog.raw_cas(), published_corrupt.record->artifact.id),
            std::ios::binary | std::ios::trunc);
        body << "tampered";
        expect(body.good(), "failed to corrupt CAS body");
        MdbxVerifiedArtifactHydration corrupt_hydration(corrupt_catalog);
        auto corrupt_read = read;
        corrupt_read.expected_artifact_id = published_corrupt.record->artifact.id;
        corrupt_read.expected_digest = published_corrupt.record->artifact.digest;
        const auto corrupt_result = corrupt_hydration.hydrate(corrupt_read);
        expect(corrupt_result.status == VerifiedHydrationStatus::Corrupt &&
                   corrupt_result.bytes.empty(),
               "corrupt CAS body was accepted");
    }

    std::cout << "verified artifact hydration: PASS\n";
    return 0;
}
