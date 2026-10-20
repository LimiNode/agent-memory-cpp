#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/retrieval/OriginalProjection.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>

#include <filesystem>
#include <iostream>
#include <stdexcept>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path test_path() {
    return std::filesystem::temp_directory_path() /
        "agent_memory_m0_projection_store_test.mdbx";
}

agent_memory::SearchProjection projection(
    const agent_memory::ScopeId& scope,
    agent_memory::KnowledgeUnitId id,
    std::uint64_t unit_revision,
    std::uint64_t projection_revision,
    std::string body
) {
    agent_memory::SearchProjection value;
    value.scope_id = scope;
    value.unit_id = id;
    value.kind = agent_memory::ProjectionKind::Original;
    value.version = {
        unit_revision,
        projection_revision,
        1,
        "m0.original.primary_text.v1"
    };
    value.body = std::move(body);
    return value;
}

} // namespace

int main() {
    using namespace agent_memory;
    const auto path = test_path();
    std::error_code error;
    std::filesystem::remove(path, error);
    const ScopeId scope{"tenant-a"};
    const ScopeId other_scope{"tenant-b"};
    const KnowledgeUnitId id{42};
    {
        MdbxProjectionStore store({path.string()});
        store.put(projection(scope, id, 1, 1, "first body"));
        store.put(projection(scope, id, 2, 2, "second body"));
        store.put(projection(other_scope, id, 1, 1, "foreign body"));
        const auto values = store.list(scope, id, ProjectionKind::Original);
        expect(values.size() == 2, "projection list lost a revision");
        expect(values[0].version.projection_revision == 1 &&
                   values[1].version.projection_revision == 2,
               "projection revisions are not deterministic");
        expect(store.list(other_scope, id).size() == 1,
               "projection store leaked across scopes");
        expect(!store.delete_revision(scope, id, ProjectionKind::Summary, 1),
               "deleting an absent projection unexpectedly succeeded");
        expect(store.delete_revision(scope, id, ProjectionKind::Original, 1),
               "projection revision was not deleted");
        expect(store.list(scope, id).size() == 1,
               "projection deletion removed the wrong revision");
    }
    {
        MdbxProjectionStore store({path.string()});
        const auto values = store.list(scope, id);
        expect(values.size() == 1 && values.front().body == "second body",
               "projection revision did not survive reopen");
        bool rejected = false;
        try {
            store.put(projection(scope, id, 2, 2, "different body"));
        } catch(const std::invalid_argument&) { rejected = true; }
        expect(rejected, "projection revision accepted conflicting content");
    }
    expect(std::filesystem::remove(path, error) || !std::filesystem::exists(path),
           "projection test database could not be removed");
    std::cout << "mdbx projection store tests passed\n";
    return 0;
}

#else
int main() { return 0; }
#endif
