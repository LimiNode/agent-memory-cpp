#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentRouter.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <filesystem>
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

void expect(bool value, int code) {
    if (!value)
        throw std::runtime_error("workspace routing assertion failed: " + std::to_string(code));
}

} // namespace

int main() {
    const auto base = std::filesystem::temp_directory_path();
    const auto path_a = base / "agent_memory_workspace_r0_a.mdbx";
    const auto path_b = base / "agent_memory_workspace_r0_b.mdbx";
    std::error_code ec;
    std::filesystem::remove(path_a, ec);
    std::filesystem::remove(path_b, ec);

    // Build and exercise workspace A, then close it before opening workspace B.
    // The final reopen phase holds both independent contexts simultaneously and
    // verifies their routing and data isolation.
    {
        auto context_a = MdbxStorageContext::open(path_a.string());
        MdbxWorkspaceStorageRegistry registry;
        const auto placement = registry.bind("workspace-a", context_a, 7, "workspace_a");
        MdbxCanonicalContentRouter router(registry);
        const DocumentId shared_id{"shared-document"};
        expect(router.create_document("workspace-a", 7, document("A")), 1);
        const auto edit = router.commit(
            "workspace-a", 7, {shared_id, 0, {ReplaceBlockText{ContentBlockId{"body"}, "A2"}}});
        expect(edit.status == CanonicalEditStatus::Ok, 2);
        expect(placement->workspace_id() == "workspace-a" && placement->generation() == 7, 3);

        bool unknown = false;
        try {
            (void)router.read_current("missing", 1, shared_id);
        } catch (const MdbxUnknownWorkspaceError&) {
            unknown = true;
        }
        expect(unknown, 4);

        bool stale = false;
        try {
            (void)router.read_current("workspace-a", 8, shared_id);
        } catch (const MdbxStalePlacementError&) {
            stale = true;
        }
        expect(stale, 5);

        bool stale_commit = false;
        try {
            (void)router.commit(
                "workspace-a", 8, {shared_id, 1, {ReplaceBlockText{ContentBlockId{"body"}, "stale"}}});
        } catch (const MdbxStalePlacementError&) {
            stale_commit = true;
        }
        expect(stale_commit, 6);
        expect(router.read_block("workspace-a", 7, shared_id, ContentBlockId{"body"})->text == "A2",
               7);

        bool duplicate = false;
        try {
            (void)registry.bind("workspace-a", context_a, 99, "other_prefix");
        } catch (const MdbxPlacementConflictError&) {
            duplicate = true;
        }
        expect(duplicate, 8);
    }

    {
        auto context_b = MdbxStorageContext::open(path_b.string());
        MdbxWorkspaceStorageRegistry registry;
        const auto placement = registry.bind("workspace-b", context_b, 11, "workspace_b");
        MdbxCanonicalContentRouter router(registry);
        expect(placement->generation() == 11, 9);
        expect(router.create_document("workspace-b", 11, document("B")), 10);
    }

    // Rebuild explicit bindings after reopen and verify same local IDs remain
    // independent because their logical workspace is part of the route key.
    {
        auto context_a = MdbxStorageContext::open(path_a.string());
        auto context_b = MdbxStorageContext::open(path_b.string());
        MdbxWorkspaceStorageRegistry registry;
        const auto placement_a = registry.bind("workspace-a", context_a, 7, "workspace_a");
        const auto placement_b = registry.bind("workspace-b", context_b, 11, "workspace_b");
        expect(placement_a->context() != placement_b->context(), 11);
        MdbxCanonicalContentRouter router(registry);
        const DocumentId shared_id{"shared-document"};
        expect(router.read_block("workspace-a", 7, shared_id, ContentBlockId{"body"})->text == "A2",
               12);
        expect(router.read_block("workspace-b", 11, shared_id, ContentBlockId{"body"})->text == "B",
               13);

        bool invalid_generation = false;
        try {
            (void)registry.bind("invalid", context_b, 0);
        } catch (const MdbxWorkspaceRoutingError&) {
            invalid_generation = true;
        }
        expect(invalid_generation, 14);
    }

    std::filesystem::remove(path_a, ec);
    std::filesystem::remove(path_b, ec);
    return 0;
}

#else
int main() {
    return 0;
}
#endif
