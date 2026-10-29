#include <agent_memory/infrastructure/mdbx/MdbxDerivedPublicationStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxProjectionStore.hpp>
#include <agent_memory/domain/Sha256.hpp>

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

std::string field_key(std::string_view id, char field) {
    auto key = body_key(id);
    key.back() = field;
    return key;
}

std::string marker_for(std::string_view manifest, std::string_view body) {
    std::string marker;
    const auto append_string = [&marker](std::string_view value) {
        marker += std::to_string(value.size());
        marker.push_back(':');
        marker.append(value.data(), value.size());
    };
    const auto append_digest = [&marker](const auto& digest) {
        marker.append(reinterpret_cast<const char*>(digest.data()), digest.size());
    };
    append_string("agent_memory.derived_publication.v1");
    marker.push_back(static_cast<char>(1));
    append_digest(agent_memory::sha256(manifest));
    append_digest(agent_memory::sha256(body));
    return marker;
}

void write_field(const std::shared_ptr<agent_memory::MdbxStorageContext>& context,
                 std::string_view publication_id, char field, std::string value) {
    mdbxc::KeyValueTable<std::string, std::string> table(
        context->connection(), "agent_memory_unit_projections");
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    table.insert_or_assign(field_key(publication_id, field), std::move(value), txn);
    txn.commit();
}

void expect_invalid_publish(agent_memory::MdbxDerivedPublicationStore& store,
                            agent_memory::DerivedPublicationRequest request,
                            const char* message) {
    bool rejected = false;
    try { store.publish(std::move(request)); }
    catch(const std::invalid_argument&) { rejected = true; }
    expect(rejected, message);
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
        expect(active.status == DerivedPublicationReadStatus::StoredMatch && active.publication &&
                   active.publication->request.body == request.body,
               "published QA projection did not produce a storage match");

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

        auto oversized_body = fixture_request();
        oversized_body.publication_id = "oversized-body";
        oversized_body.body.assign(16U * 1024U * 1024U + 1U, 'x');
        expect_invalid_publish(store, std::move(oversized_body),
                               "oversized body was accepted");

        auto oversized_frontier = fixture_request();
        oversized_frontier.publication_id = "oversized-frontier";
        oversized_frontier.dependency_frontier.assign(1024U * 1024U + 1U, 'x');
        expect_invalid_publish(store, std::move(oversized_frontier),
                               "oversized dependency frontier was accepted");

        auto oversized_digest = fixture_request();
        oversized_digest.publication_id = "oversized-digest";
        oversized_digest.inputs.front().source_digest.assign(4096U + 1U, 'x');
        expect_invalid_publish(store, std::move(oversized_digest),
                               "oversized source digest was accepted");

        auto oversized_id = fixture_request();
        oversized_id.publication_id.assign(257, 'x');
        expect_invalid_publish(store, std::move(oversized_id),
                               "oversized publication id was accepted");

        const auto corrupt_body = fixture_request("corrupt body");
        auto corrupt_body_request = corrupt_body;
        corrupt_body_request.publication_id = "corrupt-body";
        store.publish(corrupt_body_request);
        write_field(context, corrupt_body_request.publication_id, 'b', "tampered body");
        expect(store.read(corrupt_body_request.scope_id, corrupt_body_request.publication_id,
                          corrupt_body_request.inputs, corrupt_body_request.dependency_frontier).status ==
                   DerivedPublicationReadStatus::Unavailable,
               "corrupted body was not rejected");

        auto corrupt_manifest_request = fixture_request("corrupt manifest");
        corrupt_manifest_request.publication_id = "corrupt-manifest";
        store.publish(corrupt_manifest_request);
        write_field(context, corrupt_manifest_request.publication_id, 'm', "not-a-manifest");
        expect(store.read(corrupt_manifest_request.scope_id, corrupt_manifest_request.publication_id,
                          corrupt_manifest_request.inputs, corrupt_manifest_request.dependency_frontier).status ==
                   DerivedPublicationReadStatus::Unavailable,
               "corrupted manifest was not rejected");

        auto corrupt_marker_request = fixture_request("corrupt marker");
        corrupt_marker_request.publication_id = "corrupt-marker";
        store.publish(corrupt_marker_request);
        write_field(context, corrupt_marker_request.publication_id, 'a', "not-a-marker");
        expect(store.read(corrupt_marker_request.scope_id, corrupt_marker_request.publication_id,
                          corrupt_marker_request.inputs, corrupt_marker_request.dependency_frontier).status ==
                   DerivedPublicationReadStatus::Unavailable,
               "corrupted marker was not rejected");

        auto trailing_request = fixture_request("trailing manifest bytes");
        trailing_request.publication_id = "trailing-manifest";
        store.publish(trailing_request);
        mdbxc::KeyValueTable<std::string, std::string> corruption_table(
            context->connection(), "agent_memory_unit_projections");
        auto read_value = [&corruption_table, &context](const std::string& key) {
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
            const auto value = corruption_table.find(key, txn);
            txn.commit();
            return value.value();
        };
        const auto trailing_manifest = read_value(field_key(trailing_request.publication_id, 'm')) + "x";
        write_field(context, trailing_request.publication_id, 'm', trailing_manifest);
        write_field(context, trailing_request.publication_id, 'a',
                    marker_for(trailing_manifest, trailing_request.body));
        expect(store.read(trailing_request.scope_id, trailing_request.publication_id,
                          trailing_request.inputs, trailing_request.dependency_frontier).status ==
                   DerivedPublicationReadStatus::Unavailable,
               "manifest trailing bytes were accepted");

        auto malformed_request = fixture_request("malformed manifest");
        malformed_request.publication_id = "malformed-manifest";
        store.publish(malformed_request);
        write_field(context, malformed_request.publication_id, 'm', "1:x");
        write_field(context, malformed_request.publication_id, 'a', marker_for("1:x", malformed_request.body));
        expect(store.read(malformed_request.scope_id, malformed_request.publication_id,
                          malformed_request.inputs, malformed_request.dependency_frontier).status ==
                   DerivedPublicationReadStatus::Unavailable,
               "malformed manifest was accepted");
    }

    {
        auto context = MdbxStorageContext::open(path.string());
        MdbxDerivedPublicationStore store({"", "agent_memory", false,
                                           kDefaultMdbxMaxDbs, context});
        const auto reopened = store.read(
            request.scope_id, request.publication_id, request.inputs, request.dependency_frontier);
        expect(reopened.status == DerivedPublicationReadStatus::StoredMatch,
               "publication did not survive reopen as a storage match");

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
