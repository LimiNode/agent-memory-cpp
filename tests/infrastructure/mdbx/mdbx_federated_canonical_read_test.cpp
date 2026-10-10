#include <agent_memory/infrastructure/mdbx/MdbxFederatedCanonicalReader.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentRouter.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <chrono>
#include <filesystem>
#include <mdbx_containers/KeyValueTable.hpp>
#include <random>
#include <stdexcept>
#include <string>

namespace {

using namespace agent_memory;

CanonicalDocumentRevision document(const char* text) {
    return {DocumentId{"shared-document"},
            0,
            {},
            {{ContentBlockId{"body"},
              1,
              ContentBlockKind::Paragraph,
              std::nullopt,
              text}}};
}

void expect(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}

std::filesystem::path temporary_directory() {
    const auto root = std::filesystem::temp_directory_path();
    const auto stamp = static_cast<std::uint64_t>(
        std::chrono::high_resolution_clock::now().time_since_epoch().count());
    const auto path = root / ("agent_memory_r1_federated_" + std::to_string(stamp) +
                              "_" + std::to_string(std::random_device{}()));
    std::filesystem::create_directory(path);
    return path;
}

} // namespace

int main() {
    using namespace agent_memory;
    const auto directory = temporary_directory();
    const auto path_a = directory / "a.mdbx";
    const auto path_b = directory / "b.mdbx";
    const DocumentId id{"shared-document"};

    auto context_a = MdbxStorageContext::open(path_a.string());
    auto context_b = MdbxStorageContext::open(path_b.string());
    MdbxWorkspaceStorageRegistry registry;
    registry.bind("workspace-b", context_b, 11);
    registry.bind("workspace-a", context_a, 7);
    MdbxCanonicalContentRouter writer(registry);
    expect(writer.create_document("workspace-a", 7, document("A")), "create A failed");
    expect(writer.create_document("workspace-b", 11, document("B")), "create B failed");

    MdbxFederatedCanonicalReader reader(registry);
    const auto complete = reader.read({id,
                                       std::nullopt,
                                       {{"workspace-b", 11}, {"workspace-a", 7}}});
    expect(complete.status == MdbxFederatedReadStatus::Complete, "read must be complete");
    expect(complete.documents.size() == 2, "both workspaces must contribute a document");
    expect(complete.routes.size() == 2, "both route outcomes must be retained");
    expect(complete.routes[0].provenance.workspace_id == "workspace-a",
           "routes must be deterministic");
    expect(complete.documents[0].blocks.front().text == "A", "workspace A leaked or reordered");
    expect(complete.documents[1].blocks.front().text == "B", "workspace B leaked or reordered");

    bool stale = false;
    try {
        (void)reader.read({id, std::nullopt, {{"workspace-a", 8}}});
    } catch (const MdbxStalePlacementError&) {
        stale = true;
    }
    expect(stale, "stale generation must fail closed");

    bool unknown = false;
    try {
        (void)reader.read({id, std::nullopt, {{"missing", 1}}});
    } catch (const MdbxUnknownWorkspaceError&) {
        unknown = true;
    }
    expect(unknown, "unknown workspace must fail closed");

    // Corrupt only B's revision row. The backend error is surfaced as an
    // unavailable route while A remains readable and provenance is retained.
    mdbxc::KeyValueTable<std::string, std::string> revisions(
        context_b->connection(), "agent_memory_canonical_revisions");
    auto transaction = context_b->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    revisions.insert_or_assign(std::string{"shared-document\x1f0"}, "invalid", transaction);
    transaction.commit();

    const auto partial = reader.read({id,
                                      std::nullopt,
                                      {{"workspace-a", 7}, {"workspace-b", 11}}});
    expect(partial.status == MdbxFederatedReadStatus::Partial,
           "one backend failure must produce a partial result");
    expect(partial.documents.size() == 1 &&
               partial.documents.front().blocks.front().text == "A",
           "partial read must retain the healthy workspace only");
    expect(partial.routes[1].status == MdbxFederatedRouteStatus::Unavailable,
           "corrupt backend must be unavailable, not not-found");
    expect(!partial.routes[1].diagnostic.empty(), "unavailable route needs diagnostics");

    context_b.reset();
    context_a.reset();
    std::error_code error;
    std::filesystem::remove_all(directory, error);
    return error ? 1 : 0;
}

#else
int main() {
    return 0;
}
#endif
