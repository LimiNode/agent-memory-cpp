#include <agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.hpp>
#include <mdbx_containers/common.hpp>

#include "../../support/allocation_failure.hpp"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <memory>
#include <tuple>
#include <vector>

namespace am = agent_memory;
namespace cd = agent_memory::detail::conversation_durable;
namespace {
constexpr const char* kTables[] = {"agent_memory_conversation_meta_v1",
    "agent_memory_conversation_events_v1", "agent_memory_conversation_sources_v1"};
const std::string kReason = "representation.fix";
void expect(bool condition, const char* message) { if(!condition) throw std::runtime_error(message); }
void checked(int code) { if(code != MDBX_SUCCESS) throw std::runtime_error(mdbx_strerror(code)); }
am::ByteView view(const am::ByteBuffer& value) { return {value.data(), value.size()}; }
am::ByteBuffer copy_bytes(const MDBX_val& value) {
    const auto* first = static_cast<const std::uint8_t*>(value.iov_base);
    return value.iov_len ? am::ByteBuffer(first, first + value.iov_len) : am::ByteBuffer{};
}
struct RawRow final {
    std::size_t table;
    am::ByteBuffer key, value;
    bool operator==(const RawRow& other) const {
        return table == other.table && key == other.key && value == other.value;
    }
};
using Snapshot = std::vector<RawRow>;
void order(Snapshot& rows) {
    std::sort(rows.begin(), rows.end(), [](const RawRow& a, const RawRow& b) {
        return std::tie(a.table, a.key) < std::tie(b.table, b.key);
    });
}
Snapshot snapshot(const std::shared_ptr<am::MdbxStorageContext>& context) {
    Snapshot result;
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    for(std::size_t table = 0; table < 3; ++table) {
        MDBX_dbi dbi{};
        checked(mdbx_dbi_open(txn.handle(), kTables[table], MDBX_DB_DEFAULTS, &dbi));
        unsigned flags = 0;
        checked(mdbx_dbi_flags(txn.handle(), dbi, &flags));
        expect(flags == 0, "Unexpected table flags");
        MDBX_cursor* cursor = nullptr;
        checked(mdbx_cursor_open(txn.handle(), dbi, &cursor));
        MDBX_val key{}, value{};
        auto code = mdbx_cursor_get(cursor, &key, &value, MDBX_FIRST);
        try {
            while(code == MDBX_SUCCESS) {
                result.push_back({table, copy_bytes(key), copy_bytes(value)});
                code = mdbx_cursor_get(cursor, &key, &value, MDBX_NEXT);
            }
        } catch(...) { mdbx_cursor_close(cursor); throw; }
        mdbx_cursor_close(cursor);
        expect(code == MDBX_NOTFOUND, "Snapshot cursor failure");
    }
    txn.commit(); order(result); return result;
}
am::ConversationRef conversation() { return {std::string(64, 'n'), am::ConversationId{std::uint64_t{1}}}; }
am::ConversationStoreAccess access() { return {am::ScopeId{"correction-fault-scope"}, "host-policy-v1"}; }
am::EntryRef ref(std::uint64_t revision = 1) {
    return {conversation(), am::EntryId{std::uint64_t{1}}, am::EventRevision{revision}};
}
am::ConversationSourceObservation observation(std::uint64_t id = 1) {
    am::ConversationSourceObservation input;
    input.conversation = conversation(); input.scope = access().scope;
    input.source.identity = am::ConversationSourceIdentity::ExternalEvent;
    input.source.event = am::ExternalObjectRef{"fixture", "correction-fault", "run", "event", std::nullopt,
        am::ExternalObjectKey{"event-" + std::to_string(id)}};
    input.source.revision = am::SourceRevisionId{"immutable-source@1"};
    input.source.adapter_recipe = "normalizer.v1";
    input.source.evidence = {am::ArtifactBindingId{"immutable-artifact@1"}, "records/" + std::to_string(id)};
    input.producer = "fixture-producer";
    input.payload = am::ConversationSourceMessage{am::ConversationSourceMessageKind::Created,
        {std::string(128, 'a'), {}}};
    return input;
}
struct CommitObservation final { bool has_seen = false; std::size_t allocations = 0; };
struct InjectedFailure final {};
thread_local CommitObservation* g_commit = nullptr;
thread_local bool g_inject = false;
thread_local cd::WriteStep g_step = cd::WriteStep::BeforeWrites;
void observe(cd::WriteStep step) {
    if(g_inject && step == g_step) throw InjectedFailure{};
    if(g_commit && step == cd::WriteStep::BeforeCommit) *g_commit = {true, g_gate.calls};
}
struct Fixture final {
    std::filesystem::path path;
    std::shared_ptr<am::MdbxStorageContext> context;
    std::unique_ptr<cd::MdbxConversationDurable> store;
    am::ConversationEnvelope candidate;
    am::ConversationWriteResult result;
    Snapshot before;
    CommitObservation commit;
    Fixture() {
        static std::uint64_t serial = 0;
        path = std::filesystem::temp_directory_path() / ("agent_memory_correction_fault_" +
            std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "_" +
            std::to_string(++serial) + ".mdbx");
        open(true);
        expect(store->create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied,
            "Baseline create failed");
        expect(store->import_observation(access(), observation()).ref == ref(), "Baseline import failed");
        auto original = store->read_exact(access(), ref());
        candidate = am::copy_conversation_envelope(original.records.at(0).envelope);
        candidate.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
        candidate.metadata.source->adapter_recipe = "normalizer.v2";
        std::get<am::ConversationMessageCreated>(std::get<am::ConversationPayload>(candidate.payload)).content.text =
            std::string(256, 'b');
        before = snapshot(context);
    }
    ~Fixture() {
        store.reset(); context.reset();
        std::error_code error;
        std::filesystem::remove(path, error);
        std::filesystem::remove(path.string() + "-lck", error);
    }
    void open(bool initialize) {
        mdbxc::Config config;
        config.pathname = path.string(); config.no_subdir = true; config.relative_to_exe = false;
        config.max_dbs = 96; config.size_upper = 64U * 1024U * 1024U; config.growth_step = 1024U * 1024U;
        config.sync_durable = true; config.writemap_mode = false;
        context = am::MdbxStorageContext::attach(mdbxc::Connection::create(config));
        MDBX_envinfo info{};
        checked(mdbx_env_info_ex(context->connection()->env_handle(), nullptr, &info, sizeof(info)));
        cd::DurableOptions options(context, conversation().allocation_namespace,
            {2, 4, 8, 16U * 1024U * 1024U, 4, 16U * 1024U * 1024U}, {info.mi_geo.upper, info.mi_geo.grow});
        options.allow_initialize = initialize;
        store = std::make_unique<cd::MdbxConversationDurable>(std::move(options), cd::QualificationHooks{false, observe});
    }
    void reopen() { store.reset(); context.reset(); open(false); }
    void attempt() {
        commit = {}; g_commit = &commit;
        try { result = store->correct(access(), candidate, am::EventRevision{std::uint64_t{1}}, kReason); }
        catch(...) { g_commit = nullptr; throw; }
        g_commit = nullptr;
    }
    Snapshot expected_after() const {
        auto expected = before;
        const auto profile_key = cd::encode_profile_key();
        const auto marker = std::find_if(expected.begin(), expected.end(), [&profile_key](const RawRow& row) {
            return row.table == 0 && row.key == profile_key;
        });
        expect(marker != expected.end(), "Missing profile");
        auto profile = cd::decode_profile_value(view(marker->value));
        const auto frame = am::serialize_conversation_envelope(candidate);
        ++profile.generation; ++profile.revision_count;
        profile.canonical_charge += frame.size() + kReason.size();
        const auto body = cd::encode_profile(profile);
        marker->value = cd::wrap_value(profile, cd::TableKind::Metadata, view(profile_key), view(body));
        const auto revision_key = cd::encode_revision_key({conversation().id, ref().entry, ref(2).revision});
        const auto revision = cd::encode_revision_row({3, cd::CorrectionReceipt{ref().revision, kReason}, frame});
        expected.push_back({1, revision_key, cd::wrap_value(profile, cd::TableKind::Events,
            view(revision_key), view(revision))});
        const auto generation_key = cd::encode_generation_key(3);
        const auto generation = cd::encode_generation_row({cd::GenerationKind::Correction, conversation(),
            std::make_pair(ref().entry, ref(2).revision)});
        expected.push_back({1, generation_key, cd::wrap_value(profile, cd::TableKind::Events,
            view(generation_key), view(generation))});
        order(expected); return expected;
    }
    void verify_committed() {
        expect(snapshot(context) == expected_after(), "Correction left partial/extra physical rows");
        am::ConversationImportOptions options;
        options.store.allocation_namespace = conversation().allocation_namespace;
        am::InMemoryConversationImportSink oracle(options);
        expect(oracle.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied,
            "Oracle create failed");
        expect(oracle.import_observation(access(), observation()).ref == ref(), "Oracle import failed");
        expect(oracle.correct(access(), candidate, ref().revision, kReason).status == am::ConversationWriteStatus::Applied,
            "Oracle correction failed");
        const auto actual = store->read_history(access(), conversation());
        const auto wanted = oracle.history().read_history(access(), conversation());
        expect(actual.status == wanted.status && actual.commit_generation == 3 && actual.records.size() == 2,
            "Correction history differs from oracle");
        for(std::size_t index = 0; index < 2; ++index) {
            expect(am::serialize_conversation_envelope(actual.records[index].envelope) ==
                am::serialize_conversation_envelope(wanted.records[index].envelope) &&
                actual.records[index].commit_generation == wanted.records[index].commit_generation,
                "Exact corrected bytes/generation differ from oracle");
        }
        expect(actual.records[1].correction && actual.records[1].correction->previous_revision == ref().revision &&
            actual.records[1].correction->reason_code == kReason, "Correction receipt missing");
        const auto stable = snapshot(context);
        const auto replay = store->import_observation(access(), observation());
        expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == ref() &&
            replay.commit_generation == 2, "Original replay changed after correction");
        auto changed = observation(); changed.producer = "changed-witness";
        expect(store->import_observation(access(), changed).status == am::ConversationWriteStatus::Conflict,
            "Changed original witness accepted");
        const auto stale = store->correct(access(), candidate, ref().revision, kReason);
        expect(stale.status == am::ConversationWriteStatus::Conflict && !stale.ref && !stale.commit_generation,
            "Stale CAS did not precede NoChange");
        auto no_change = am::copy_conversation_envelope(candidate);
        no_change.metadata.ref.revision = am::EventRevision{std::uint64_t{3}};
        const auto unchanged = store->correct(access(), no_change, ref(2).revision, kReason);
        expect(unchanged.status == am::ConversationWriteStatus::NoChange && unchanged.ref == ref(2) &&
            unchanged.commit_generation == 3 && snapshot(context) == stable, "NoChange/refusal mutated state");
        const auto next = store->import_observation(access(), observation(2));
        expect(next.status == am::ConversationWriteStatus::Applied && next.ref && next.ref->entry.value() == 2 &&
            next.commit_generation == 4, "Correction/failure consumed the source allocator");
    }
};
void allocation_failure_matrix() {
    std::size_t allocations = 0, precommit = 0;
    {
        Fixture fixture;
        { ScopedGate gate(0); fixture.attempt(); }
        allocations = g_gate.calls;
        expect(allocations > 0 && allocations < 10000 && fixture.commit.has_seen,
            "Invalid correction allocation probe");
        precommit = fixture.commit.allocations;
        fixture.verify_committed();
    }
    std::size_t committed_at_boundary = 0, rolled_back_at_boundary = 0;
    for(std::size_t ordinal = 1; ordinal <= allocations; ++ordinal) {
        Fixture fixture;
        const auto after = fixture.expected_after();
        bool has_thrown = false;
        try { ScopedGate gate(ordinal); fixture.attempt(); }
        catch(const std::bad_alloc&) { has_thrown = true; }
        expect(has_thrown && g_gate.has_failed && g_gate.calls == ordinal,
            "Correction allocation ordinal did not propagate bad_alloc");
        if(ordinal <= precommit) expect(!fixture.commit.has_seen, "Precommit allocation crossed commit hook");
        fixture.reopen();
        const auto reopened = snapshot(fixture.context);
        expect(reopened == fixture.before || reopened == after, "Allocation failure left a partial correction");
        if(ordinal <= precommit) expect(reopened == fixture.before, "Precommit failure committed correction");
        else if(reopened == after) ++committed_at_boundary;
        else ++rolled_back_at_boundary;
        if(reopened == fixture.before) {
            fixture.attempt();
            expect(fixture.result.status == am::ConversationWriteStatus::Applied,
                "Rolled-back correction could not retry");
        }
        fixture.verify_committed();
    }
    std::cout << "Correction bad_alloc ordinals: " << allocations << "; before commit: " << precommit <<
        "; boundary committed: " << committed_at_boundary << "; boundary rolled back: " <<
        rolled_back_at_boundary << '\n';
}
void injected_precommit_matrix() {
    std::size_t cases = 0;
    for(const auto step : {cd::WriteStep::BeforeTransaction, cd::WriteStep::BeforeWrites,
        cd::WriteStep::AfterRevision, cd::WriteStep::AfterGeneration, cd::WriteStep::AfterProfile,
        cd::WriteStep::BeforeCommit}) {
        Fixture fixture;
        g_step = step; g_inject = true;
        bool has_thrown = false;
        try { fixture.attempt(); } catch(const InjectedFailure&) { has_thrown = true; }
        g_inject = false;
        expect(has_thrown, "Correction did not reach injected precommit boundary");
        fixture.reopen();
        expect(snapshot(fixture.context) == fixture.before, "Injected correction failure did not roll back all tables");
        fixture.attempt(); fixture.verify_committed(); ++cases;
    }
    std::cout << "Correction injected precommit rollback cases: " << cases << '\n';
}
} // namespace
int main() {
    try {
#if defined(_MSC_VER) && _ITERATOR_DEBUG_LEVEL > 0
        // The checked STL allocates inside noexcept construction. The existing
        // store/import fault suites exclude this allocation-injection profile;
        // keep explicit precommit rollback coverage active in this executable.
        std::cout << "SKIP correction allocation matrix: MSVC checked STL allocates inside noexcept construction\n";
#else
        allocation_failure_matrix();
#endif
        injected_precommit_matrix();
        std::cout << "NOT RUN real device I/O/native commit failure or power loss.\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "correction failure test: " << error.what() << '\n'; return 1;
    }
}
