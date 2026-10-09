#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentRouter.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <random>
#include <stdexcept>
#include <string>
#include <system_error>
#include <utility>

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

class ScopedTempDirectory final {
  public:
    static ScopedTempDirectory create() {
        const auto root = std::filesystem::temp_directory_path();
        std::random_device random;
        const auto timestamp = static_cast<std::uint64_t>(
            std::chrono::high_resolution_clock::now().time_since_epoch().count());
        for (std::uint64_t attempt = 0; attempt < 32; ++attempt) {
            const auto suffix = static_cast<std::uint64_t>(random()) ^ timestamp ^ attempt;
            const auto candidate = root / ("agent_memory_workspace_r0_" + std::to_string(suffix));
            std::error_code ec;
            if (std::filesystem::create_directory(candidate, ec))
                return ScopedTempDirectory(candidate);
            if (ec && ec != std::make_error_code(std::errc::file_exists))
                throw std::runtime_error("failed to create isolated MDBX test directory: " +
                                         ec.message());
        }
        throw std::runtime_error("failed to allocate an isolated MDBX test directory");
    }

    ScopedTempDirectory(const ScopedTempDirectory&) = delete;
    ScopedTempDirectory& operator=(const ScopedTempDirectory&) = delete;

    ~ScopedTempDirectory() {
        if (m_cleaned)
            return;
        std::error_code ignored;
        std::filesystem::remove_all(m_path, ignored);
    }

    [[nodiscard]] const std::filesystem::path& path() const noexcept {
        return m_path;
    }

    [[nodiscard]] bool cleanup() noexcept {
        if (m_cleaned)
            return true;
        std::error_code ec;
        std::filesystem::remove_all(m_path, ec);
        m_cleaned = !ec;
        return m_cleaned;
    }

  private:
    explicit ScopedTempDirectory(std::filesystem::path path) : m_path(std::move(path)) {}

    std::filesystem::path m_path;
    bool m_cleaned = false;
};

} // namespace

int main() {
    auto temporary = ScopedTempDirectory::create();
    const auto path_a = temporary.path() / "workspace-a.mdbx";
    const auto path_b = temporary.path() / "workspace-b.mdbx";
    const DocumentId shared_id{"shared-document"};

    {
        // Keep B alive while A is created, edited, released and reopened. Both
        // physical environments and their routers are active during the initial
        // writes, while the same local DocumentId remains independent in each.
        auto context_b = MdbxStorageContext::open(path_b.string());
        MdbxWorkspaceStorageRegistry registry_b;
        // The same prefix is valid in a different physical MDBX environment.
        const auto placement_b = registry_b.bind("workspace-b", context_b, 11, "workspace_a");
        const auto placement_b_alt =
            registry_b.bind("workspace-b-alt", context_b, 3, "workspace_b_alt");
        MdbxCanonicalContentRouter router_b(registry_b);

        expect(router_b.create_document("workspace-b", 11, document("B")), 2);
        expect(router_b.create_document("workspace-b-alt", 3, document("B-alt")), 3);
        expect(router_b.read_block("workspace-b", 11, shared_id, ContentBlockId{"body"})
                       .value()
                       .text == "B",
               5);
        expect(router_b.read_block("workspace-b-alt", 3, shared_id, ContentBlockId{"body"})
                       .value()
                       .text == "B-alt",
               6);
        expect(placement_b->canonical_content() != placement_b_alt->canonical_content(), 8);

        {
            auto context_a = MdbxStorageContext::open(path_a.string());
            MdbxWorkspaceStorageRegistry registry_a;
            const auto placement_a = registry_a.bind("workspace-a", context_a, 7, "workspace_a");
            MdbxCanonicalContentRouter router_a(registry_a);
            expect(router_a.create_document("workspace-a", 7, document("A")), 9);
            expect(router_a.read_block("workspace-a", 7, shared_id, ContentBlockId{"body"})
                           .value()
                           .text == "A",
                   10);
            expect(placement_a->context() != placement_b->context(), 11);

            const auto edit_a = router_a.commit(
                "workspace-a", 7,
                {shared_id, 0, {ReplaceBlockText{ContentBlockId{"body"}, "A2"}}});
            const auto edit_b = router_b.commit(
                "workspace-b", 11,
                {shared_id, 0, {ReplaceBlockText{ContentBlockId{"body"}, "B2"}}});
            expect(edit_a.status == CanonicalEditStatus::Ok &&
                       edit_b.status == CanonicalEditStatus::Ok,
                   12);

            bool unknown = false;
            try {
                (void)router_a.read_current("missing", 1, shared_id);
            } catch (const MdbxUnknownWorkspaceError&) {
                unknown = true;
            }
            expect(unknown, 13);

            bool stale_read = false;
            try {
                (void)router_a.read_current("workspace-a", 8, shared_id);
            } catch (const MdbxStalePlacementError&) {
                stale_read = true;
            }
            expect(stale_read, 14);

            bool stale_write = false;
            try {
                (void)router_a.commit("workspace-a", 8,
                                      {shared_id,
                                       1,
                                       {ReplaceBlockText{ContentBlockId{"body"}, "stale"}}});
            } catch (const MdbxStalePlacementError&) {
                stale_write = true;
            }
            expect(stale_write, 15);
            expect(router_a.read_block("workspace-a", 7, shared_id, ContentBlockId{"body"})
                           .value()
                           .text == "A2",
                   16);

            bool duplicate = false;
            try {
                (void)registry_a.bind("workspace-a", context_a, 99, "other_prefix");
            } catch (const MdbxPlacementConflictError&) {
                duplicate = true;
            }
            expect(duplicate, 17);

            bool namespace_conflict = false;
            try {
                (void)registry_a.bind("workspace-a-alias", context_a, 1, "workspace_a");
            } catch (const MdbxPlacementConflictError&) {
                namespace_conflict = true;
            }
            expect(namespace_conflict, 18);

            auto attached_context = MdbxStorageContext::attach(context_a->connection());
            bool attached_namespace_conflict = false;
            try {
                (void)registry_a.bind("workspace-a-attached", attached_context, 1, "workspace_a");
            } catch (const MdbxPlacementConflictError&) {
                attached_namespace_conflict = true;
            }
            expect(attached_namespace_conflict, 19);

            bool normalized_namespace_conflict = false;
            try {
                (void)registry_a.bind("workspace-a-normalized", context_a, 1, "workspace-a");
            } catch (const MdbxPlacementConflictError&) {
                normalized_namespace_conflict = true;
            }
            expect(normalized_namespace_conflict, 20);
        }

        // A's context and registry are now destroyed while B remains active.
        expect(router_b.read_block("workspace-b", 11, shared_id, ContentBlockId{"body"})
                       .value()
                       .text == "B2",
               21);
        const auto edit_b_again = router_b.commit(
            "workspace-b", 11,
            {shared_id, 1, {ReplaceBlockText{ContentBlockId{"body"}, "B3"}}});
        expect(edit_b_again.status == CanonicalEditStatus::Ok, 22);

        // Reopen A independently while B is still active and validate all read
        // surfaces plus the historical revisions in both physical files.
        auto reopened_a = MdbxStorageContext::open(path_a.string());
        MdbxWorkspaceStorageRegistry reopened_registry_a;
        const auto reopened_placement_a =
            reopened_registry_a.bind("workspace-a", reopened_a, 7, "workspace_a");
        expect(reopened_placement_a->generation() == 7, 23);
        MdbxCanonicalContentRouter reopened_router_a(reopened_registry_a);
        expect(reopened_router_a.read_current("workspace-a", 7, shared_id).value().revision == 1,
               24);
        expect(reopened_router_a.read_revision("workspace-a", 7, shared_id, 0)
                           .value()
                           .blocks.front()
                           .text == "A",
               25);
        expect(reopened_router_a.read_block("workspace-a", 7, shared_id, ContentBlockId{"body"})
                           .value()
                           .text == "A2",
               26);
        expect(reopened_router_a.materialize_markdown("workspace-a", 7, shared_id).value() ==
                   "A2\n",
               27);
        expect(router_b.read_current("workspace-b", 11, shared_id).value().revision == 2, 28);
        expect(router_b.read_revision("workspace-b", 11, shared_id, 1)
                           .value()
                           .blocks.front()
                           .text == "B2",
               29);
        expect(router_b.materialize_markdown("workspace-b", 11, shared_id).value() == "B3\n", 30);

        bool invalid_generation = false;
        try {
            (void)registry_b.bind("invalid", context_b, 0);
        } catch (const MdbxWorkspaceRoutingError&) {
            invalid_generation = true;
        }
        expect(invalid_generation, 31);

        // The registry is intentionally local to its composition root. This
        // boundary test demonstrates that independent registries do not provide a
        // process-global collision authority; the host must coordinate them.
        auto shared_context = MdbxStorageContext::open((temporary.path() / "shared.mdbx").string());
        MdbxWorkspaceStorageRegistry independent_registry_a;
        MdbxWorkspaceStorageRegistry independent_registry_b;
        const auto independent_placement_a =
            independent_registry_a.bind("independent-a", shared_context, 1, "shared");
        const auto independent_placement_b =
            independent_registry_b.bind("independent-b", shared_context, 1, "shared");
        expect(independent_placement_a && independent_placement_b, 32);
    }

    // All contexts/stores are destroyed before the temporary directory is
    // removed; cleanup failure is an explicit test failure, not a silent leak.
    if (!temporary.cleanup())
        return 33;
    return 0;
}

#else
int main() {
    return 0;
}
#endif
