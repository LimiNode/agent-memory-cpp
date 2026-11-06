#include <agent_memory/infrastructure/mdbx/MdbxDerivedPublicationStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <system_error>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

class TestDatabase final {
public:
    TestDatabase() {
        const auto parent = std::filesystem::absolute(std::filesystem::temp_directory_path());
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        for(std::uint64_t attempt = 0; attempt != 128U; ++attempt) {
            m_directory = parent / ("agent-memory-shared-projections-" +
                                    std::to_string(stamp) + "-" + std::to_string(attempt));
            std::error_code error;
            if(std::filesystem::create_directory(m_directory, error)) break;
            if(error && error != std::make_error_code(std::errc::file_exists))
                throw std::runtime_error("failed to reserve MDBX test directory");
            m_directory.clear();
        }
        if(m_directory.empty()) throw std::runtime_error("MDBX test directory collision");
        m_owner = m_directory / ".owner";
        std::ofstream marker(m_owner);
        if(!marker) throw std::runtime_error("failed to mark MDBX test directory");
        m_path = m_directory / "database.mdbx";
    }
    ~TestDatabase() {
        std::error_code error;
        const auto status = std::filesystem::symlink_status(m_owner, error);
        if(!error && std::filesystem::is_regular_file(status))
            std::filesystem::remove_all(m_directory, error);
    }
    [[nodiscard]] const std::filesystem::path& path() const noexcept { return m_path; }
private:
    std::filesystem::path m_directory;
    std::filesystem::path m_owner;
    std::filesystem::path m_path;
};

void append_u64(std::string& value, std::uint64_t number) {
    for(int shift = 56; shift >= 0; shift -= 8)
        value.push_back(static_cast<char>((number >> shift) & 0xffU));
}

void append_string(std::string& value, std::string_view text) {
    value += std::to_string(text.size());
    value.push_back(':');
    value.append(text.data(), text.size());
}

std::string projection_key(
    const agent_memory::ScopeId& scope,
    agent_memory::KnowledgeUnitId unit,
    agent_memory::ProjectionKind kind,
    std::uint64_t revision) {
    std::string key;
    append_string(key, scope.value());
    append_u64(key, unit.value());
    append_u64(key, static_cast<std::uint16_t>(kind));
    append_u64(key, revision);
    return key;
}

std::string publication_field_key(std::string_view publication_id, char field) {
    std::string key = "derived_publication:";
    append_string(key, publication_id);
    key.push_back(field);
    return key;
}

agent_memory::SearchProjection projection() {
    using namespace agent_memory;
    SearchProjection value;
    value.scope_id = ScopeId{"shared-scope"};
    value.unit_id = KnowledgeUnitId{7};
    value.kind = ProjectionKind::Original;
    value.version = {1, 1, 1, "shared-projection.v1"};
    value.body = "original body";
    return value;
}

agent_memory::SearchProjection mixed_generation_projection() {
    auto value = projection();
    value.kind = agent_memory::ProjectionKind::Summary;
    value.version.projection_revision = 2;
    value.version.derivation_generation = 2;
    value.version.derivation_fingerprint = "summary-v2";
    value.body = "summary body";
    return value;
}

agent_memory::DerivedPublicationRequest publication() {
    using namespace agent_memory;
    DerivedPublicationRequest value;
    value.scope_id = ScopeId{"shared-scope"};
    value.publication_id = "shared-publication";
    value.kind = ProjectionKind::QACombined;
    value.output_revision = 1;
    value.body = "derived body";
    value.inputs = {{KnowledgeUnitId{7}, 1, 1, "digest-v1"}};
    value.generation = 1;
    value.dependency_frontier = "source@1";
    return value;
}

} // namespace

int main() {
    using namespace agent_memory;
    TestDatabase database;
    const auto original = projection();
    const auto mixed_generation = mixed_generation_projection();
    const auto derived = publication();
    {
        const auto context = MdbxStorageContext::open(database.path().string());

        MdbxProjectionStoreOptions projection_options;
        projection_options.context = context;
        projection_options.table_prefix = "shared_conformance";
        MdbxProjectionStore projections(projection_options);

        MdbxDerivedPublicationStoreOptions publication_options;
        publication_options.context = context;
        publication_options.table_prefix = "shared_conformance";
        MdbxDerivedPublicationStore publications(publication_options);

        projections.put(original);
        projections.put(mixed_generation);
        publications.publish(derived);
        const auto mixed_rows = projections.list(original.scope_id, original.unit_id);
        expect(mixed_rows.size() == 2U,
               "projection owner could not read its key family");
        expect(publications.read(
                   derived.scope_id, derived.publication_id, derived.inputs,
                   derived.dependency_frontier).status == DerivedPublicationReadStatus::StoredMatch,
               "publication owner could not read its key family");

        auto read_txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto snapshot = publications.read_in_transaction(
            derived.scope_id, derived.publication_id, read_txn);
        expect(snapshot.status == DerivedPublicationReadStatus::StoredMatch,
               "shared DBI snapshot read was not consistent");
        read_txn.commit();

        expect(projections.delete_revision(
                   original.scope_id, original.unit_id, original.kind,
                   original.version.projection_revision),
               "projection owner could not delete its own row");
        expect(publications.read(
                   derived.scope_id, derived.publication_id, derived.inputs,
                   derived.dependency_frontier).status == DerivedPublicationReadStatus::StoredMatch,
               "projection deletion removed a foreign publication family");

        projections.put(original);
        mdbxc::KeyValueTable<std::string, std::string> table(
            context->connection(), "shared_conformance_unit_projections");
        const auto projection_row = projection_key(
            original.scope_id, original.unit_id, original.kind,
            original.version.projection_revision);
        auto original_payload_txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto original_payload = table.find(projection_row, original_payload_txn);
        original_payload_txn.commit();
        expect(original_payload.has_value(), "projection payload was not stored");
        auto malformed_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        table.insert_or_assign(projection_row, "malformed", malformed_txn);
        malformed_txn.commit();
        bool malformed_rejected = false;
        try { static_cast<void>(projections.list(original.scope_id, original.unit_id)); }
        catch(const std::runtime_error&) { malformed_rejected = true; }
        expect(malformed_rejected, "malformed projection payload was accepted");
        auto restore_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        table.insert_or_assign(projection_row, *original_payload, restore_txn);
        restore_txn.commit();

        const auto malformed_key = projection_row + "malformed-key";
        auto malformed_key_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        table.insert_or_assign(malformed_key, "malformed", malformed_key_txn);
        malformed_key_txn.commit();
        bool malformed_key_rejected = false;
        try { static_cast<void>(projections.list(original.scope_id, original.unit_id)); }
        catch(const std::runtime_error&) { malformed_key_rejected = true; }
        expect(malformed_key_rejected, "malformed projection key was accepted");
        auto remove_malformed_key_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        expect(table.erase(malformed_key, remove_malformed_key_txn),
               "malformed projection key cleanup failed");
        remove_malformed_key_txn.commit();

        const auto body_key = publication_field_key(derived.publication_id, 'b');
        auto foreign_delete_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        expect(table.find(body_key, foreign_delete_txn).has_value(),
               "publication body was not stored in the shared DBI");
        foreign_delete_txn.commit();
    }

    MdbxProjectionStoreOptions reopened_projection_options;
    reopened_projection_options.context = MdbxStorageContext::open(database.path().string());
    reopened_projection_options.table_prefix = "shared_conformance";
    MdbxProjectionStore reopened_projections(reopened_projection_options);
    MdbxDerivedPublicationStoreOptions reopened_publication_options;
    reopened_publication_options.context = reopened_projection_options.context;
    reopened_publication_options.table_prefix = "shared_conformance";
    MdbxDerivedPublicationStore reopened_publications(reopened_publication_options);
    expect(reopened_projections.list(original.scope_id, original.unit_id).size() == 2U,
           "projection owner lost its row after reopen");
    expect(reopened_publications.read(
               derived.scope_id, derived.publication_id, derived.inputs,
               derived.dependency_frontier).status == DerivedPublicationReadStatus::StoredMatch,
           "publication owner lost its row after reopen");

    std::cout << "shared unit projections DBI conformance: SAFE for tested key-family/reopen scope\n";
    return 0;
}

#endif
