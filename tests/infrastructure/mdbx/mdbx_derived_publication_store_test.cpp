#include <agent_memory/infrastructure/mdbx/MdbxDerivedPublicationStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path test_path() {
    return std::filesystem::temp_directory_path() /
        "agent_memory_m0_derived_publication_store_test.mdbx";
}

agent_memory::DerivedPublicationRequest fixture_request(
    std::string body = "The answer is 42.",
    std::uint64_t revision = 1,
    std::string frontier = "question@1;answer@1"
) {
    using namespace agent_memory;
    DerivedPublicationRequest request;
    request.scope_id = ScopeId{"workspace-a"};
    request.publication_id = "forum-qa-42";
    request.kind = ProjectionKind::QACombined;
    request.output_revision = revision;
    request.body = std::move(body);
    request.generation = revision;
    request.dependency_frontier = std::move(frontier);
    request.inputs = {
        {KnowledgeUnitId{101}, 1, 7, "question-digest-v1"},
        {KnowledgeUnitId{102}, 1, 7, "answer-digest-v1"}
    };
    return request;
}

std::string body_key(std::string_view id) {
    return "derived_publication:" + std::to_string(id.size()) + ":" + std::string{id} + "b";
}

} // namespace

int main() {
    using namespace agent_memory;
    const auto path = test_path();
    std::error_code error;
    std::filesystem::remove(path, error);
    expect(!error && !std::filesystem::exists(path), "derived publication test database is not clean");

    const auto request = fixture_request();
    {
        auto context = MdbxStorageContext::open(path.string());
        MdbxDerivedPublicationStore store({"", "agent_memory", false,
                                           kDefaultMdbxMaxDbs, context});
        store.publish(request);
        store.publish(request); // exact replay is idempotent.

        const auto active = store.read(
            request.scope_id, request.publication_id, request.inputs, request.dependency_frontier);
        expect(active.status == DerivedPublicationReadStatus::Active && active.publication &&
                   active.publication->request.body == request.body,
               "published QA projection was not admitted");

        auto stale_inputs = request.inputs;
        stale_inputs[1].unit_revision = 2;
        const auto stale = store.read(
            request.scope_id, request.publication_id, stale_inputs, request.dependency_frontier);
        expect(stale.status == DerivedPublicationReadStatus::Stale && !stale.publication,
               "changed source revision was not rejected as stale");

        const auto stale_frontier = store.read(
            request.scope_id, request.publication_id, request.inputs, "question@2;answer@1");
        expect(stale_frontier.status == DerivedPublicationReadStatus::Stale,
               "changed dependency frontier was not rejected as stale");

        const auto denied = store.read(
            ScopeId{"workspace-b"}, request.publication_id, request.inputs, request.dependency_frontier);
        expect(denied.status == DerivedPublicationReadStatus::ScopeMismatch,
               "cross-scope publication was admitted");

        bool conflict = false;
        try { store.publish(fixture_request("different answer")); }
        catch(const std::invalid_argument&) { conflict = true; }
        expect(conflict, "conflicting publication replay was accepted");

        const auto original = fixture_request("Original question and answer evidence.");
        MdbxProjectionStore projections({"", "agent_memory", false,
                                         kDefaultMdbxMaxDbs, context});
        SearchProjection evidence;
        evidence.scope_id = request.scope_id;
        evidence.unit_id = request.inputs.front().unit_id;
        evidence.kind = ProjectionKind::Original;
        evidence.version = {1, 1, 1, "m0.original.primary_text.v1"};
        evidence.body = original.body;
        projections.put(evidence);
        const auto evidence_rows = projections.list(
            request.scope_id, request.inputs.front().unit_id, ProjectionKind::Original);
        expect(evidence_rows.size() == 1 && evidence_rows.front().body == original.body,
               "original evidence was not independently retrievable");

        const auto missing = store.read(
            request.scope_id, "missing", request.inputs, request.dependency_frontier);
        expect(missing.status == DerivedPublicationReadStatus::NotFound,
               "missing publication was not reported as absent");
    }

    {
        auto context = MdbxStorageContext::open(path.string());
        MdbxDerivedPublicationStore store({"", "agent_memory", false,
                                           kDefaultMdbxMaxDbs, context});
        const auto reopened = store.read(
            request.scope_id, request.publication_id, request.inputs, request.dependency_frontier);
        expect(reopened.status == DerivedPublicationReadStatus::Active,
               "publication did not survive reopen");

        mdbxc::KeyValueTable<std::string, std::string> table(
            context->connection(), "agent_memory_unit_projections");
        {
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            table.insert_or_assign(body_key("aborted"), "body written before injected failure", txn);
            try {
                throw std::runtime_error("injected failure before commit");
            } catch(const std::runtime_error&) {
                // Transaction destruction rolls back the uncommitted body write.
            }
        }
        const auto aborted = store.read(
            request.scope_id, "aborted", request.inputs, request.dependency_frontier);
        expect(aborted.status == DerivedPublicationReadStatus::NotFound,
               "uncommitted publication field became visible after failure");

        {
            auto partial_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            table.insert_or_assign(body_key("partial"), "body without marker", partial_txn);
            partial_txn.commit();
        }
        const auto partial = store.read(
            request.scope_id, "partial", request.inputs, request.dependency_frontier);
        expect(partial.status == DerivedPublicationReadStatus::Unavailable,
               "body without a durable marker was admitted");
    }

    const auto removed = std::filesystem::remove(path, error);
    expect(!error && (removed || !std::filesystem::exists(path)),
           "derived publication test database could not be removed");
    std::cout << "mdbx derived publication store tests passed\n";
    return 0;
}

#else
int main() { return 0; }
#endif
