#include <agent_memory/infrastructure/mdbx/MdbxFederatedCanonicalReader.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentRouter.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <chrono>
#include <filesystem>
#include <mdbx_containers/KeyValueTable.hpp>
#include <memory>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

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

void corrupt_revision(const std::shared_ptr<MdbxStorageContext>& context,
                      const DocumentId& id,
                      std::uint64_t revision) {
    mdbxc::KeyValueTable<std::string, std::string> revisions(
        context->connection(), "agent_memory_canonical_revisions");
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto row_key = id.value() + std::string{"\x1f"} + std::to_string(revision);
    revisions.insert_or_assign(row_key, "invalid", transaction);
    transaction.commit();
}

} // namespace

int main() {
    using namespace agent_memory;
    const auto directory = temporary_directory();
    const auto path_a = directory / "a.mdbx";
    const auto path_b = directory / "b.mdbx";
    const DocumentId id{"shared-document"};

    {
        auto context_a = MdbxStorageContext::open(path_a.string());
        auto context_b = MdbxStorageContext::open(path_b.string());
        MdbxWorkspaceStorageRegistry registry;
        registry.bind("workspace-b", context_b, 11);
        registry.bind("workspace-a", context_a, 7);
        MdbxCanonicalContentRouter writer(registry);
        expect(writer.create_document("workspace-a", 7, document("A")),
               "create A failed");
        expect(writer.create_document("workspace-b", 11, document("B")),
               "create B failed");

        const auto edit = writer.commit(
            "workspace-a",
            7,
            CanonicalEditRequest{
                id,
                0,
                {ReplaceBlockText{ContentBlockId{"body"}, "A edited"}}});
        expect(edit.status == CanonicalEditStatus::Ok, "historical edit failed");
        expect(edit.revision && edit.revision->revision == 1,
               "edit must publish revision 1");

        MdbxFederatedCanonicalReader reader(registry);
        const auto current = reader.read(
            {id, std::nullopt, {{"workspace-b", 11}, {"workspace-a", 7}}});
        expect(current.status == MdbxFederatedReadStatus::Complete,
               "current read must be complete");
        expect(current.routes.size() == 2, "both route outcomes must be retained");
        expect(current.routes[0].provenance.workspace_id == "workspace-a",
               "routes must be deterministic");
        expect(current.routes[0].revision &&
                   current.routes[0].revision->blocks.front().text == "A edited",
               "workspace A current revision is wrong");
        expect(current.routes[1].revision &&
                   current.routes[1].revision->blocks.front().text == "B",
               "workspace B current revision is wrong");

        const auto historical = reader.read(
            {id, 0, {{"workspace-a", 7}, {"workspace-b", 11}}});
        expect(historical.status == MdbxFederatedReadStatus::Complete,
               "historical read must be complete");
        expect(historical.routes[0].revision &&
                   historical.routes[0].revision->blocks.front().text == "A",
               "workspace A historical revision is wrong");
        expect(historical.routes[1].revision &&
                   historical.routes[1].revision->blocks.front().text == "B",
               "workspace B historical revision is wrong");

        const auto not_found = reader.read(
            {DocumentId{"missing"}, std::nullopt, {{"workspace-a", 7}, {"workspace-b", 11}}});
        expect(not_found.status == MdbxFederatedReadStatus::Complete,
               "not-found routes are a complete read");
        expect(not_found.routes.size() == 2 &&
                   not_found.routes[0].status == MdbxFederatedRouteStatus::NotFound &&
                   not_found.routes[1].status == MdbxFederatedRouteStatus::NotFound,
               "missing document must remain distinct from unavailable");

        bool duplicate = false;
        try {
            (void)reader.read({id, std::nullopt, {{"workspace-a", 7}, {"workspace-a", 7}}});
        } catch (const MdbxWorkspaceRoutingError&) {
            duplicate = true;
        }
        expect(duplicate, "duplicate workspace route must fail closed");

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

        bool over_budget = false;
        try {
            std::vector<MdbxFederatedReadProvenance> routes(
                MdbxFederatedCanonicalReader::kMaxRoutes + 1,
                {"unbound", 1});
            (void)reader.read({id, std::nullopt, std::move(routes)});
        } catch (const std::invalid_argument&) {
            over_budget = true;
        }
        expect(over_budget, "route budget must be enforced before resolution");

        // Corrupt only B's historical row. The backend error is surfaced as an
        // unavailable route while A remains readable and provenance is retained.
        corrupt_revision(context_b, id, 0);
        const auto partial = reader.read(
            {id, 0, {{"workspace-a", 7}, {"workspace-b", 11}}});
        expect(partial.status == MdbxFederatedReadStatus::Partial,
               "one backend failure must produce a partial result");
        expect(partial.routes[0].revision &&
                   partial.routes[0].revision->blocks.front().text == "A",
               "partial read must retain the healthy workspace");
        expect(partial.routes[1].status == MdbxFederatedRouteStatus::Unavailable,
               "corrupt backend must be unavailable, not not-found");
        expect(partial.routes[1].diagnostic == "canonical route unavailable",
               "backend diagnostics must be redacted");

        corrupt_revision(context_a, id, 0);
        const auto unavailable = reader.read(
            {id, 0, {{"workspace-a", 7}, {"workspace-b", 11}}});
        expect(unavailable.status == MdbxFederatedReadStatus::Unavailable,
               "all backend failures must be unavailable");
        expect(unavailable.routes[0].status == MdbxFederatedRouteStatus::Unavailable &&
                   unavailable.routes[1].status == MdbxFederatedRouteStatus::Unavailable,
               "all unavailable routes must retain per-route status");
    }

    // Both independent MDBX environments must remain readable after restart.
    {
        auto context_a = MdbxStorageContext::open(path_a.string());
        auto context_b = MdbxStorageContext::open(path_b.string());
        MdbxWorkspaceStorageRegistry registry;
        registry.bind("workspace-a", context_a, 7);
        registry.bind("workspace-b", context_b, 11);
        MdbxFederatedCanonicalReader reader(registry);
        const auto historical = reader.read(
            {id, 0, {{"workspace-a", 7}, {"workspace-b", 11}}});
        expect(historical.routes.size() == 2 &&
                   historical.routes[0].status == MdbxFederatedRouteStatus::Unavailable &&
                   historical.routes[1].status == MdbxFederatedRouteStatus::Unavailable,
               "corruption must survive restart as unavailable");
    }

    // A separate pair of environments verifies successful current reads
    // after both contexts have been closed and reopened.
    const auto clean_directory = temporary_directory();
    const auto clean_path_a = clean_directory / "a.mdbx";
    const auto clean_path_b = clean_directory / "b.mdbx";
    {
        auto context_a = MdbxStorageContext::open(clean_path_a.string());
        auto context_b = MdbxStorageContext::open(clean_path_b.string());
        MdbxWorkspaceStorageRegistry registry;
        registry.bind("workspace-a", context_a, 7);
        registry.bind("workspace-b", context_b, 11);
        MdbxCanonicalContentRouter writer(registry);
        expect(writer.create_document("workspace-a", 7, document("A")),
               "clean create A failed");
        expect(writer.create_document("workspace-b", 11, document("B")),
               "clean create B failed");
    }
    {
        auto context_a = MdbxStorageContext::open(clean_path_a.string());
        auto context_b = MdbxStorageContext::open(clean_path_b.string());
        MdbxWorkspaceStorageRegistry registry;
        registry.bind("workspace-a", context_a, 7);
        registry.bind("workspace-b", context_b, 11);
        MdbxFederatedCanonicalReader reader(registry);
        const auto reopened = reader.read(
            {id, std::nullopt, {{"workspace-a", 7}, {"workspace-b", 11}}});
        expect(reopened.status == MdbxFederatedReadStatus::Complete &&
                   reopened.routes.size() == 2 &&
                   reopened.routes[0].status == MdbxFederatedRouteStatus::Found &&
                   reopened.routes[1].status == MdbxFederatedRouteStatus::Found,
               "healthy environments must remain readable after reopen");
    }

    std::error_code clean_error;
    std::filesystem::remove_all(clean_directory, clean_error);
    std::error_code error;
    std::filesystem::remove_all(directory, error);
    return error || clean_error ? 1 : 0;
}

#else
int main() {
    return 0;
}
#endif
