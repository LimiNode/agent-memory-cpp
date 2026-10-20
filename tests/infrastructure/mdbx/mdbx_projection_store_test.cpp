#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

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
    std::string body,
    agent_memory::ProjectionKind kind = agent_memory::ProjectionKind::Original
) {
    agent_memory::SearchProjection value;
    value.scope_id = scope;
    value.unit_id = id;
    value.kind = kind;
    value.version = {
        unit_revision,
        projection_revision,
        1,
        "m0.original.primary_text.v1"
    };
    value.body = std::move(body);
    return value;
}

void append_string(std::string& out, std::string_view value) {
    out += std::to_string(value.size());
    out.push_back(':');
    out.append(value.data(), value.size());
}

void append_u64(std::string& out, std::uint64_t value) {
    for(int shift = 56; shift >= 0; shift -= 8)
        out.push_back(static_cast<char>((value >> shift) & 0xffU));
}

std::string projection_key(const agent_memory::ScopeId& scope,
                           agent_memory::KnowledgeUnitId id,
                           agent_memory::ProjectionKind kind,
                           std::uint64_t projection_revision) {
    std::string key;
    append_string(key, scope.value());
    append_u64(key, id.value());
    append_u64(key, static_cast<std::uint16_t>(kind));
    append_u64(key, projection_revision);
    return key;
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
        store.put(projection(scope, id, 1, 1, "first body"));
        store.put(projection(scope, id, 2, 2, "second body"));
        auto rich = projection(scope, id, 2, 2, "тело проекции", ProjectionKind::DenseContextual);
        rich.title = "Заголовок";
        rich.heading = "Раздел";
        rich.code = "λ = 42;";
        rich.tags = {"тег", "utf8"};
        rich.symbols = {"λ", "函数"};
        store.put(rich);
        store.put(rich);
        store.put(projection(other_scope, id, 1, 1, "foreign body"));
        const auto values = store.list(scope, id, ProjectionKind::Original);
        expect(values.size() == 2, "projection list lost a revision");
        expect(values[0].version.projection_revision == 1 &&
                   values[1].version.projection_revision == 2,
               "projection revisions are not deterministic");
        expect(store.list(other_scope, id).size() == 1,
               "projection store leaked across scopes");
        const auto all_values = store.list(scope, id);
        expect(all_values.size() == 3, "projection list lost a projection kind");
        const auto rich_it = std::find_if(
            all_values.begin(), all_values.end(), [](const SearchProjection& value) {
                return value.kind == ProjectionKind::DenseContextual;
            });
        expect(rich_it != all_values.end() && rich_it->title == "Заголовок"
                   && rich_it->heading == "Раздел" && rich_it->body == "тело проекции"
                   && rich_it->code == "λ = 42;"
                   && rich_it->tags == std::vector<std::string>{"тег", "utf8"}
                   && rich_it->symbols == std::vector<std::string>{"λ", "函数"},
               "projection fields did not round-trip");
        expect(!store.delete_revision(scope, id, ProjectionKind::Summary, 1),
               "deleting an absent projection unexpectedly succeeded");
        expect(store.delete_revision(scope, id, ProjectionKind::Original, 1),
               "projection revision was not deleted");
        expect(store.list(scope, id).size() == 2,
               "projection deletion removed the wrong revision");
    }
    {
        auto context = MdbxStorageContext::open(path.string());
        MdbxProjectionStoreOptions options;
        options.context = context;
        MdbxProjectionStore store(std::move(options));
        const auto values = store.list(scope, id);
        expect(values.size() == 2,
               "projection revisions did not survive reopen");
        const auto original_values = store.list(scope, id, ProjectionKind::Original);
        expect(original_values.size() == 1 && original_values.front().body == "second body",
               "original projection revision did not survive reopen");
        bool rejected = false;
        try {
            store.put(projection(scope, id, 2, 2, "different body"));
        } catch(const std::invalid_argument&) { rejected = true; }
        expect(rejected, "projection revision accepted conflicting content");

        mdbxc::KeyValueTable<std::string, std::string> table(
            context->connection(), "agent_memory_unit_projections");
        const auto original_key = projection_key(scope, id, ProjectionKind::Original, 2);
        const auto rich_key = projection_key(scope, id, ProjectionKind::DenseContextual, 2);
        auto corruption_txn = context->connection()->transaction(
            mdbxc::TransactionMode::WRITABLE);
        const auto original_payload = table.find(original_key, corruption_txn);
        const auto rich_payload = table.find(rich_key, corruption_txn);
        expect(original_payload && rich_payload, "projection corruption fixture is incomplete");
        table.insert_or_assign(original_key, *rich_payload, corruption_txn);
        corruption_txn.commit();
        bool identity_threw = false;
        try { (void)store.list(scope, id); }
        catch(const std::runtime_error&) { identity_threw = true; }
        expect(identity_threw, "projection key/payload mismatch was accepted");

        auto restore_txn = context->connection()->transaction(
            mdbxc::TransactionMode::WRITABLE);
        table.insert_or_assign(original_key, *original_payload, restore_txn);
        restore_txn.commit();
        auto malformed = *original_payload;
        const auto colon = malformed.find(':');
        expect(colon != std::string::npos, "projection payload fixture has no length prefix");
        malformed.replace(0, colon, std::to_string(std::numeric_limits<std::size_t>::max()));
        auto malformed_txn = context->connection()->transaction(
            mdbxc::TransactionMode::WRITABLE);
        table.insert_or_assign(original_key, malformed, malformed_txn);
        malformed_txn.commit();
        bool malformed_threw = false;
        try { (void)store.list(scope, id); }
        catch(const std::runtime_error&) { malformed_threw = true; }
        expect(malformed_threw, "oversized projection length was accepted");
    }
    expect(std::filesystem::remove(path, error) || !std::filesystem::exists(path),
           "projection test database could not be removed");
    std::cout << "mdbx projection store tests passed\n";
    return 0;
}

#else
int main() { return 0; }
#endif
