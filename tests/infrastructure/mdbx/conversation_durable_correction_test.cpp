#include <agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.hpp>
#include <mdbx_containers/common.hpp>

#include <chrono>
#include <cstdio>
#include <filesystem>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace am = agent_memory;
namespace cd = agent_memory::detail::conversation_durable;
namespace {
constexpr auto kMax = std::numeric_limits<std::uint64_t>::max();
constexpr const char* kTables[] = {"agent_memory_conversation_meta_v1", "agent_memory_conversation_events_v1",
    "agent_memory_conversation_sources_v1"};
constexpr const char* kReason = "normalization.v2";

void expect(bool condition, const char* message) { if(!condition) throw std::runtime_error(message); }
void checked(int code) { if(code != MDBX_SUCCESS) throw std::runtime_error(mdbx_strerror(code)); }
am::ByteView view(const am::ByteBuffer& bytes) { return {bytes.data(), bytes.size()}; }
MDBX_val raw(am::ByteView bytes) { return {const_cast<std::uint8_t*>(bytes.data), bytes.size}; }
am::ByteBuffer copy(const MDBX_val& value) {
    const auto* first = static_cast<const std::uint8_t*>(value.iov_base);
    return value.iov_len ? am::ByteBuffer{first, first + value.iov_len} : am::ByteBuffer{};
}
am::ConversationRef conversation(std::uint64_t id = 1) { return {"correction", am::ConversationId{id}}; }
am::ConversationStoreAccess access() { return {am::ScopeId{"scope-a"}, "host-policy-v1"}; }
am::EntryRef entry(std::uint64_t id, std::uint64_t revision = 1) {
    return {conversation(), am::EntryId{id}, am::EventRevision{revision}};
}
cd::Quotas quotas() { return {8, 1024, 64, 16U * 1024U * 1024U, 1024, 16U * 1024U * 1024U}; }
am::ConversationImportOptions oracle_options(const cd::Quotas& policy) {
    am::ConversationImportOptions result;
    result.store.allocation_namespace = conversation().allocation_namespace;
    result.store.max_revisions_per_entry = static_cast<std::size_t>(policy.revisions_per_entry);
    result.store.max_storage_bytes = static_cast<std::size_t>(policy.canonical_bytes);
    return result;
}
am::ConversationSourceObservation message(const std::string& key, const std::string& text = "source text") {
    am::ConversationSourceObservation value;
    value.conversation = conversation(); value.scope = access().scope; value.producer = "authored-export-v1";
    value.source.identity = am::ConversationSourceIdentity::ExternalEvent;
    value.source.event = am::ExternalObjectRef{"fixture", "archive", "run-a", "event", std::nullopt,
        am::ExternalObjectKey{key}};
    value.source.revision = am::SourceRevisionId{"archive@1"};
    value.source.adapter_recipe = "normalizer.v1";
    value.source.evidence = {am::ArtifactBindingId{"archive-artifact@1"}, "records/" + key};
    value.llm_role = am::ConversationLlmRole::User;
    value.payload = am::ConversationSourceMessage{am::ConversationSourceMessageKind::Created, {text, {}}};
    return value;
}
am::ConversationSourceObservation call(const std::string& key) {
    auto value = message(key); value.llm_role = am::ConversationLlmRole::Assistant;
    value.payload = am::ConversationSourceToolCall{{0, 255, 1}, "shell", am::ArtifactBindingId{"request@1"},
        am::ArtifactBindingId{"effective-request@1"}};
    return value;
}
am::ConversationSourceObservation tool_result(const std::string& key,
    const am::ConversationSourceObservation& target) {
    auto value = message(key); value.llm_role = am::ConversationLlmRole::Tool;
    value.payload = am::ConversationSourceToolResult{am::ConversationSourceTarget{target.conversation, target.scope,
        am::copy_conversation_source_binding(target.source)}, {0, 255, 1},
        am::ConversationSourceResultOrigin::Reported, am::ConversationToolOutcome::Unknown,
        am::ArtifactBindingId{"result@1"}, std::nullopt, std::nullopt};
    return value;
}

struct RawRow final {
    std::size_t table;
    unsigned flags;
    am::ByteBuffer key, value;
    bool operator==(const RawRow& other) const {
        return table == other.table && flags == other.flags && key == other.key && value == other.value;
    }
};
struct Fixture final {
    std::filesystem::path path;
    std::shared_ptr<am::MdbxStorageContext> context;
    cd::Quotas policy;
    explicit Fixture(cd::Quotas input = quotas()) : policy(input) {
        static std::uint64_t serial = 0;
        path = std::filesystem::temp_directory_path() / ("agent_memory_durable_correction_" +
            std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "_" +
            std::to_string(++serial) + ".mdbx");
        open();
    }
    ~Fixture() {
        context.reset(); std::error_code error;
        std::filesystem::remove(path, error); std::filesystem::remove(path.string() + "-lck", error);
    }
    void open() { context = am::MdbxStorageContext::open(path.string(), false, 96); }
    void reopen() { context.reset(); open(); }
    cd::DurableOptions options(bool initialize = true) const {
        MDBX_envinfo info{};
        checked(mdbx_env_info_ex(context->connection()->env_handle(), nullptr, &info, sizeof(info)));
        cd::DurableOptions result(context, conversation().allocation_namespace, policy,
            {info.mi_geo.upper, info.mi_geo.grow});
        result.allow_initialize = initialize;
        // Reserve another complete profile to exercise foreign-profile tokens
        // without changing the original profile's declared environment budget.
        result.other_profile_peak_dbis = 3;
        return result;
    }
    std::vector<RawRow> snapshot() const {
        std::vector<RawRow> result;
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        for(std::size_t table = 0; table < 3; ++table) {
            MDBX_dbi dbi{}; checked(mdbx_dbi_open(txn.handle(), kTables[table], MDBX_DB_DEFAULTS, &dbi));
            unsigned flags = 0; checked(mdbx_dbi_flags(txn.handle(), dbi, &flags));
            result.push_back({table, flags, {}, {}});
            MDBX_cursor* cursor = nullptr; checked(mdbx_cursor_open(txn.handle(), dbi, &cursor));
            MDBX_val key{}, value{}; auto code = mdbx_cursor_get(cursor, &key, &value, MDBX_FIRST);
            try {
                while(code == MDBX_SUCCESS) {
                    result.push_back({table, flags, copy(key), copy(value)});
                    code = mdbx_cursor_get(cursor, &key, &value, MDBX_NEXT);
                }
            } catch(...) { mdbx_cursor_close(cursor); throw; }
            mdbx_cursor_close(cursor); expect(code == MDBX_NOTFOUND, "Snapshot cursor failure");
        }
        return result;
    }
    am::ByteBuffer get(std::size_t table, const am::ByteBuffer& key) const {
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        MDBX_dbi dbi{}; checked(mdbx_dbi_open(txn.handle(), kTables[table], MDBX_DB_DEFAULTS, &dbi));
        auto k = raw(view(key)); MDBX_val value{}; checked(mdbx_get(txn.handle(), dbi, &k, &value));
        return copy(value);
    }
    void put(std::size_t table, const am::ByteBuffer& key, const am::ByteBuffer& value) const {
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        MDBX_dbi dbi{}; checked(mdbx_dbi_open(txn.handle(), kTables[table], MDBX_DB_DEFAULTS, &dbi));
        auto k = raw(view(key)), v = raw(view(value));
        checked(mdbx_put(txn.handle(), dbi, &k, &v, MDBX_UPSERT)); txn.commit();
    }
    cd::Profile profile() const {
        const auto value = get(0, cd::encode_profile_key()); return cd::decode_profile_value(view(value));
    }
    void profile(const cd::Profile& value) const {
        const auto key = cd::encode_profile_key(), body = cd::encode_profile(value);
        put(0, key, cd::wrap_value(value, cd::TableKind::Metadata, view(key), view(body)));
    }
};

void same_write(const am::ConversationWriteResult& actual, const am::ConversationWriteResult& expected) {
    expect(actual.status == expected.status && actual.ref == expected.ref &&
        actual.commit_generation == expected.commit_generation, "Oracle write/ref/generation parity");
}
void same_read(const am::ConversationReadResult& actual, const am::ConversationReadResult& expected) {
    expect(actual.status == expected.status && actual.commit_generation == expected.commit_generation &&
        actual.records.size() == expected.records.size() && actual.messages.empty() &&
        actual.visited_records == expected.visited_records && actual.decoded_bytes == expected.decoded_bytes &&
        actual.output_bytes == expected.output_bytes, "Oracle read/status/frontier/work parity");
    expect(actual.continuation.has_value() == expected.continuation.has_value(), "Oracle continuation presence parity");
    for(std::size_t i = 0; i < actual.records.size(); ++i) {
        const auto& a = actual.records[i]; const auto& b = expected.records[i];
        expect(am::serialize_conversation_envelope(a.envelope) == am::serialize_conversation_envelope(b.envelope) &&
            a.commit_generation == b.commit_generation && a.correction.has_value() == b.correction.has_value(),
            "Oracle canonical AMCE/generation/receipt parity");
        if(a.correction) expect(a.correction->previous_revision == b.correction->previous_revision &&
            a.correction->reason_code == b.correction->reason_code, "Oracle exact correction receipt parity");
    }
}
void no_data(const am::ConversationReadResult& result, am::ConversationReadStatus status) {
    expect(result.status == status && result.records.empty() && result.messages.empty() &&
        !result.commit_generation && !result.visited_records && !result.decoded_bytes && !result.output_bytes &&
        !result.continuation, "Refusal leaked records, frontier, work or continuation");
}
am::ConversationEnvelope exact(cd::MdbxConversationDurable& store, const am::EntryRef& ref) {
    auto result = store.read_exact(access(), ref);
    expect(result.status == am::ConversationReadStatus::Complete && result.records.size() == 1, "Exact revision missing");
    return std::move(result.records.front().envelope);
}
am::ConversationEnvelope next(const am::ConversationEnvelope& previous) {
    auto result = am::copy_conversation_envelope(previous);
    result.metadata.ref.revision = am::EventRevision{previous.metadata.ref.revision.value() + 1};
    return result;
}
void set_text(am::ConversationEnvelope& value, const std::string& text) {
    auto& payload = std::get<am::ConversationPayload>(value.payload);
    if(auto* message_value = std::get_if<am::ConversationMessageCreated>(&payload)) message_value->content.text = text;
    else std::get<am::ConversationSnapshotObserved>(payload).content.text = text;
}
struct Harness final {
    Fixture fixture;
    am::InMemoryConversationImportSink oracle;
    std::unique_ptr<cd::MdbxConversationDurable> store;
    explicit Harness(cd::Quotas policy = quotas()) : fixture(policy), oracle(oracle_options(policy)),
        store(std::make_unique<cd::MdbxConversationDurable>(fixture.options())) {
        same_write(store->create_conversation(access(), conversation()), oracle.create_conversation(access(), conversation()));
    }
    am::ConversationWriteResult admit(const am::ConversationSourceObservation& value) {
        const auto actual = store->import_observation(access(), value);
        same_write(actual, oracle.import_observation(access(), value));
        expect(actual.status == am::ConversationWriteStatus::Applied && actual.ref, "Admission failed");
        return actual;
    }
    am::ConversationWriteResult correct(const am::ConversationEnvelope& value,
        const std::string& reason = kReason) {
        const auto expected = am::EventRevision{value.metadata.ref.revision.value() - 1};
        const auto actual = store->correct(access(), value, expected, reason);
        same_write(actual, oracle.correct(access(), value, expected, reason));
        return actual;
    }
    void compare() const {
        same_read(store->read_history(access(), conversation()), oracle.history().read_history(access(), conversation()));
    }
    void reopen() {
        store.reset(); fixture.reopen(); store = std::make_unique<cd::MdbxConversationDurable>(fixture.options(false));
    }
};

void immutable_corrections_and_original_replay() {
    Harness h; const auto source = message("first"); const auto original = h.admit(source);
    const auto before_profile = h.fixture.profile();
    const auto before_conversation = h.fixture.get(0, cd::encode_conversation_key(conversation().id));
    const auto before_sources = h.fixture.snapshot();
    auto changed = next(exact(*h.store, *original.ref));
    set_text(changed, "corrected text"); changed.metadata.source->adapter_recipe = "normalizer.v2";
    changed.metadata.actor = am::ActorRef{"correction", am::ActorId{std::uint64_t{7}}};
    changed.extensions.push_back({4096, false, {0, 255, 1}});
    const auto corrected = h.correct(changed);
    expect(corrected.status == am::ConversationWriteStatus::Applied && corrected.ref == entry(1, 2) &&
        corrected.commit_generation == 3, "Changed correction did not append revision two");
    const auto after_profile = h.fixture.profile();
    expect(after_profile.entry_count == before_profile.entry_count &&
        after_profile.mapping_count == before_profile.mapping_count &&
        after_profile.mapping_charge == before_profile.mapping_charge &&
        after_profile.revision_count == before_profile.revision_count + 1 &&
        after_profile.generation == before_profile.generation + 1 &&
        after_profile.canonical_charge == before_profile.canonical_charge +
            am::serialize_conversation_envelope(changed).size() + std::string(kReason).size(),
        "Correction counter/charge transition differs from the declared grammar");
    expect(h.fixture.get(0, cd::encode_conversation_key(conversation().id)) == before_conversation,
        "Correction rewrote scope or allocator");
    const auto after_sources = h.fixture.snapshot();
    std::vector<RawRow> old_rows, new_rows;
    for(const auto& row : before_sources) if(row.table == 2) old_rows.push_back(row);
    for(const auto& row : after_sources) if(row.table == 2) new_rows.push_back(row);
    expect(old_rows == new_rows, "Correction changed the original source witness/mapping");
    h.compare();
    same_read(h.store->read_exact(access(), *original.ref), h.oracle.history().read_exact(access(), *original.ref));
    same_read(h.store->read_exact(access(), *corrected.ref), h.oracle.history().read_exact(access(), *corrected.ref));
    const auto stored = h.store->read_exact(access(), *corrected.ref);
    expect(stored.records.front().correction && stored.records.front().correction->previous_revision.value() == 1 &&
        stored.records.front().correction->reason_code == kReason && stored.records.front().envelope.extensions[0].bytes ==
            am::ByteBuffer({0, 255, 1}), "Receipt/unknown advisory extension did not survive exact read");

    const auto unchanged = next(changed); const auto before_noop = h.fixture.snapshot();
    const auto no_change = h.correct(unchanged, "another-valid-reason");
    expect(no_change.status == am::ConversationWriteStatus::NoChange && no_change.ref == corrected.ref &&
        no_change.commit_generation == corrected.commit_generation && h.fixture.snapshot() == before_noop,
        "NoChange consumed revision/generation or persisted its reason");
    const auto stale = h.store->correct(access(), changed, am::EventRevision{std::uint64_t{1}}, kReason);
    same_write(stale, h.oracle.correct(access(), changed, am::EventRevision{std::uint64_t{1}}, kReason));
    expect(stale.status == am::ConversationWriteStatus::Conflict && !stale.ref && !stale.commit_generation &&
        h.fixture.snapshot() == before_noop, "Stale CAS did not precede byte-equal NoChange");

    same_write(h.store->import_observation(access(), source), h.oracle.import_observation(access(), source));
    const auto replay = h.store->import_observation(access(), source);
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == original.ref &&
        replay.commit_generation == original.commit_generation, "Replay returned latest representation instead of original receipt");
    h.reopen(); h.compare();
    same_write(h.store->import_observation(access(), source), replay);
    same_read(h.store->read_exact(access(), *original.ref), h.oracle.history().read_exact(access(), *original.ref));
    same_read(h.store->read_exact(access(), *corrected.ref), h.oracle.history().read_exact(access(), *corrected.ref));
    auto conflicting = message("first"); conflicting.producer = "changed-producer";
    const auto before_conflict = h.fixture.snapshot();
    same_write(h.store->import_observation(access(), conflicting), h.oracle.import_observation(access(), conflicting));
    expect(h.store->import_observation(access(), conflicting).status == am::ConversationWriteStatus::Conflict &&
        h.fixture.snapshot() == before_conflict, "Corrected source accepted a changed original witness");
    expect(h.admit(message("next")).ref->entry.value() == 2, "Correction consumed an EntryId");
    const auto later_noop = h.correct(unchanged);
    expect(later_noop.status == am::ConversationWriteStatus::NoChange && later_noop.commit_generation == 4,
        "Correction NoChange returned its old admission generation instead of current generation");
    expect(h.store->import_observation(access(), source).commit_generation == original.commit_generation,
        "Source replay followed unrelated later generation");
}

template<class Change>
void refusal_unchanged(Harness& h, const am::EntryRef& current, Change change,
    am::ConversationWriteStatus expected_status = am::ConversationWriteStatus::InvalidRequest) {
    auto value = next(exact(*h.store, current)); change(value);
    const auto before = h.fixture.snapshot(); const auto result = h.correct(value);
    expect(result.status == expected_status && !result.ref && !result.commit_generation && h.fixture.snapshot() == before,
        "Invalid correction mutated durable state or leaked a receipt");
}
void immutable_bindings_and_invalid_requests() {
    Harness h; const auto first = h.admit(message("message")); auto snapshot = message("target");
    std::get<am::ConversationSourceMessage>(snapshot.payload).kind = am::ConversationSourceMessageKind::Snapshot;
    const auto snapshot_ref = h.admit(snapshot);
    refusal_unchanged(h, *first.ref, [](auto& e) { e.metadata.source->revision = am::SourceRevisionId{"different@1"}; });
    refusal_unchanged(h, *first.ref, [](auto& e) { e.metadata.source->evidence.artifact = am::ArtifactBindingId{"other-artifact"}; });
    refusal_unchanged(h, *first.ref, [](auto& e) { e.metadata.source->evidence.record_locator = "records/other"; });
    refusal_unchanged(h, *first.ref, [](auto& e) { e.metadata.source->event =
        am::ExternalObjectRef{"fixture", "archive", "another-run", "event", std::nullopt,
            am::ExternalObjectKey{std::string{"message"}}}; });
    refusal_unchanged(h, *first.ref, [](auto& e) { e.metadata.links.push_back({am::ConversationLinkKind::Reply, entry(2)}); });
    refusal_unchanged(h, *first.ref, [](auto& e) { e.payload = am::ConversationPayload{
        am::ConversationSnapshotObserved{{std::string{"not the same occurrence family"}, {}}}}; });
    refusal_unchanged(h, *first.ref, [](auto& e) { e.metadata.source.reset(); });
    refusal_unchanged(h, *first.ref, [](auto& e) { set_text(e, std::string(1, static_cast<char>(0xff))); });
    refusal_unchanged(h, *first.ref, [](auto& e) { e.extensions.push_back({4096, true, {0, 255}}); },
        am::ConversationWriteStatus::Unsupported);
    refusal_unchanged(h, *first.ref, [](auto& e) { e.payload = am::ConversationOpaquePayload{65536, 1, {0, 255}}; },
        am::ConversationWriteStatus::Unsupported);
    for(const auto& reason : {std::string{}, std::string("Bad Reason"), std::string(129, 'a')}) {
        auto value = next(exact(*h.store, *first.ref)); set_text(value, "changed");
        const auto before = h.fixture.snapshot(); const auto result = h.correct(value, reason);
        expect(result.status == am::ConversationWriteStatus::InvalidRequest && h.fixture.snapshot() == before,
            "Invalid correction reason was persisted");
    }
    auto wrong_revision = next(exact(*h.store, *first.ref)); wrong_revision.metadata.ref.revision = am::EventRevision{std::uint64_t{3}};
    same_write(h.store->correct(access(), wrong_revision, am::EventRevision{std::uint64_t{1}}, kReason),
        h.oracle.correct(access(), wrong_revision, am::EventRevision{std::uint64_t{1}}, kReason));
    auto hidden = access(); hidden.scope = am::ScopeId{"hidden"};
    no_data(h.store->read_exact(hidden, *first.ref), am::ConversationReadStatus::NotFound);
    auto proposed = next(exact(*h.store, *first.ref));
    same_write(h.store->correct(hidden, proposed, am::EventRevision{std::uint64_t{1}}, kReason),
        h.oracle.correct(hidden, proposed, am::EventRevision{std::uint64_t{1}}, kReason));
    auto corrected_snapshot = next(exact(*h.store, *snapshot_ref.ref)); set_text(corrected_snapshot, "corrected snapshot");
    expect(h.correct(corrected_snapshot).status == am::ConversationWriteStatus::Applied,
        "Snapshot representation correction differs from ordinary message correction");
    same_read(h.store->read_exact(access(), entry(2, 2)), h.oracle.history().read_exact(access(), entry(2, 2)));
    h.compare();
}

void tool_identity_and_frozen_unresolved_replay() {
    Harness h; const auto source_call = call("late-call");
    const auto source_result = tool_result("early-result", source_call); const auto result = h.admit(source_result);
    auto original = exact(*h.store, *result.ref);
    expect(std::holds_alternative<am::UnresolvedConversationTarget>(
        std::get<am::ConversationToolResultObserved>(original.payload).call), "Missing call was not frozen unresolved");
    auto corrected = next(original);
    auto& payload = std::get<am::ConversationToolResultObserved>(corrected.payload);
    payload.outcome = am::ConversationToolOutcome::Failed; payload.result = am::ArtifactBindingId{"corrected-result@1"};
    corrected.metadata.source->adapter_recipe = "normalizer.v2";
    expect(h.correct(corrected).status == am::ConversationWriteStatus::Applied, "Result representation correction failed");
    const auto actual_call = h.admit(source_call);
    refusal_unchanged(h, entry(1, 2), [actual_call](auto& e) {
        std::get<am::ConversationToolResultObserved>(e.payload).call = *actual_call.ref;
    });
    refusal_unchanged(h, entry(1, 2), [](auto& e) {
        std::get<am::ConversationToolResultObserved>(e.payload).call_id.push_back(42);
    });
    refusal_unchanged(h, *actual_call.ref, [](auto& e) {
        std::get<am::ConversationToolCallObserved>(e.payload).call_id = {1, 255, 0};
    });
    refusal_unchanged(h, *actual_call.ref, [](auto& e) {
        std::get<am::ConversationToolCallObserved>(e.payload).tool_name = "another-tool";
    });
    refusal_unchanged(h, *actual_call.ref, [](auto& e) {
        std::get<am::ConversationToolCallObserved>(e.payload).request = am::ArtifactBindingId{"effective-request@1"};
    });
    auto revised_call = next(exact(*h.store, *actual_call.ref));
    revised_call.metadata.source->adapter_recipe = "normalizer.v2";
    revised_call.metadata.observed_unix_us = std::int64_t{123};
    expect(h.correct(revised_call).status == am::ConversationWriteStatus::Applied, "Call metadata correction failed");
    h.reopen(); h.compare();
    const auto replay = h.store->import_observation(access(), source_result);
    same_write(replay, h.oracle.import_observation(access(), source_result));
    expect(replay.ref == result.ref && replay.commit_generation == result.commit_generation,
        "Correction/reopen/late call rewrote original unresolved replay receipt");
    same_read(h.store->read_exact(access(), *result.ref), h.oracle.history().read_exact(access(), *result.ref));
    expect(std::holds_alternative<am::UnresolvedConversationTarget>(
        std::get<am::ConversationToolResultObserved>(exact(*h.store, *result.ref).payload).call),
        "Original unresolved target was implicitly reconciled");
    auto separate = tool_result("separate-result", source_call); const auto separate_result = h.admit(separate);
    expect(std::get<am::EntryRef>(std::get<am::ConversationToolResultObserved>(
        exact(*h.store, *separate_result.ref).payload).call) == *actual_call.ref,
        "Separate new result did not resolve to original exact call revision");
}

void frontier_filtering_and_revision_pages() {
    Harness h; h.admit(message("one")); h.admit(message("two")); h.admit(message("three"));
    am::ConversationReadLimits one; one.max_items = 1;
    auto old = h.store->read_history(access(), conversation(), one);
    auto oracle_old = h.oracle.history().read_history(access(), conversation(), one);
    same_read(old, oracle_old); expect(old.commit_generation == 4 && old.continuation, "Pinned first page missing");
    auto corrected = next(exact(*h.store, entry(1))); set_text(corrected, "revision two");
    expect(h.correct(corrected).commit_generation == 5, "Mandatory correction generation is not five");
    auto pinned = h.store->read_history_at(access(), conversation(), {}, 4);
    expect(pinned.status == am::ConversationReadStatus::Complete && pinned.records.size() == 3 &&
        pinned.records[0].envelope.metadata.ref == entry(1) && pinned.records[1].envelope.metadata.ref == entry(2) &&
        pinned.records[2].envelope.metadata.ref == entry(3), "Future E1 revision suppressed visible E2/E3");
    h.admit(message("four"));
    corrected = next(corrected); set_text(corrected, "revision three"); h.correct(corrected);
    while(old.continuation) {
        old = h.store->read_history(access(), conversation(), one, old.continuation);
        oracle_old = h.oracle.history().read_history(access(), conversation(), one, oracle_old.continuation);
        same_read(old, oracle_old); expect(old.commit_generation == 4, "Resume silently moved to latest frontier");
    }
    auto actual = h.store->read_history(access(), conversation(), one);
    auto expected = h.oracle.history().read_history(access(), conversation(), one);
    std::vector<am::EntryRef> revisions;
    for(std::size_t pages = 0;; ++pages) {
        expect(pages < 16, "Revision pagination failed to progress"); same_read(actual, expected);
        for(const auto& record : actual.records) revisions.push_back(record.envelope.metadata.ref);
        if(!actual.continuation) break;
        actual = h.store->read_history(access(), conversation(), one, actual.continuation);
        expected = h.oracle.history().read_history(access(), conversation(), one, expected.continuation);
    }
    expect(revisions == std::vector<am::EntryRef>({entry(1), entry(1, 2), entry(1, 3), entry(2), entry(3), entry(4)}),
        "Pair seek skipped/duplicated revisions at a page-size-one boundary");
    const auto final_at_old = h.store->read_history_at(access(), conversation(), one, 4, 2, 1);
    expect(final_at_old.status == am::ConversationReadStatus::Complete && final_at_old.records.size() == 1 &&
        final_at_old.records[0].envelope.metadata.ref == entry(3), "Exactly full old-frontier final page was not Complete");
    h.reopen();
    same_read(h.store->read_history_at(access(), conversation(), {}, 4), pinned);
    same_read(h.store->read_exact(access(), entry(1, 2)), h.oracle.history().read_exact(access(), entry(1, 2)));
}

void trailing_and_many_invisible_revisions() {
    Harness h; h.admit(message("one")); h.admit(message("two"));
    auto candidate = exact(*h.store, entry(2));
    for(std::uint64_t revision = 2; revision <= 4; ++revision) {
        candidate = next(candidate); set_text(candidate, "two-" + std::to_string(revision)); h.correct(candidate);
    }
    am::ConversationReadLimits two; two.max_items = 2; two.max_visited_records = 2;
    const auto trailing = h.store->read_history_at(access(), conversation(), two, 3);
    expect(trailing.status == am::ConversationReadStatus::Complete && trailing.records.size() == 2 &&
        trailing.visited_records == 2 && !trailing.continuation, "Invisible trailing revisions fabricated Partial/input exhaustion");
    candidate = exact(*h.store, entry(1));
    for(std::uint64_t revision = 2; revision <= 33; ++revision) {
        candidate = next(candidate); set_text(candidate, "one-" + std::to_string(revision)); h.correct(candidate);
    }
    auto options = h.fixture.options(false); options.max_read_rows = 8;
    cd::MdbxConversationDurable bounded(options);
    auto page = bounded.read_history_at(access(), conversation(), {}, 3, 1, 1);
    std::size_t pages = 0, outputs = 0;
    for(;;) {
        expect(++pages < 64 && page.commit_generation == 3, "Invisible-prefix continuation did not progress at pinned frontier");
        expect(page.status == am::ConversationReadStatus::BudgetExhausted || page.status == am::ConversationReadStatus::Complete,
            "Invisible raw-work exhaustion has wrong classification");
        if(page.records.empty()) expect(!page.visited_records && !page.decoded_bytes,
            "Invisible physical rows were reported as complete AMCE decodes");
        for(const auto& record : page.records) {
            ++outputs; expect(record.envelope.metadata.ref == entry(2), "Invisible scan returned a future revision or duplicate prefix");
        }
        if(page.status == am::ConversationReadStatus::Complete) break;
        expect(page.continuation.has_value(), "Raw-work exhaustion lost resumable invisible-prefix progress");
        page = bounded.read_history(access(), conversation(), {}, page.continuation);
    }
    expect(pages > 1 && outputs == 1, "Small raw budget did not exercise progress through invisible revisions");
}

void continuations_fail_closed_and_results_own_data() {
    Harness h; h.admit(message("one")); h.admit(message("two"));
    am::ConversationReadLimits one; one.max_items = 1;
    const auto page = h.store->read_history(access(), conversation(), one);
    expect(page.continuation.has_value(), "Continuation fixture has no page token");
    const auto retained_bytes = am::serialize_conversation_envelope(page.records[0].envelope);
    no_data(h.store->read_history(access(), conversation(), one, am::ConversationContinuation{}),
        am::ConversationReadStatus::InvalidContinuation);
    auto policy = access(); policy.policy_binding = "host-policy-v2";
    no_data(h.store->read_history(policy, conversation(), one, page.continuation), am::ConversationReadStatus::InvalidContinuation);
    auto scope = access(); scope.scope = am::ScopeId{"other"};
    no_data(h.store->read_history(scope, conversation(), one, page.continuation), am::ConversationReadStatus::InvalidContinuation);
    auto revoked = access(); revoked.policy_binding.clear();
    no_data(h.store->read_history(revoked, conversation(), one, page.continuation), am::ConversationReadStatus::Denied);
    no_data(h.store->read_history(access(), conversation(2), one, page.continuation), am::ConversationReadStatus::InvalidContinuation);
    {
        cd::MdbxConversationDurable another(h.fixture.options(false));
        no_data(another.read_history(access(), conversation(), one, page.continuation), am::ConversationReadStatus::InvalidContinuation);
        auto foreign_options = h.fixture.options(); foreign_options.table_prefix = "foreign";
        cd::MdbxConversationDurable foreign(foreign_options);
        expect(foreign.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied,
            "Foreign profile setup failed");
        no_data(foreign.read_history(access(), conversation(), one, page.continuation), am::ConversationReadStatus::InvalidContinuation);
    }
    const auto reference = h.oracle.history().read_history(access(), conversation(), one);
    no_data(h.store->read_history(access(), conversation(), one, reference.continuation), am::ConversationReadStatus::InvalidContinuation);
    no_data(h.oracle.history().read_history(access(), conversation(), one, page.continuation), am::ConversationReadStatus::InvalidContinuation);
    const auto current = h.oracle.history().read_current(access(), conversation(), one);
    no_data(h.store->read_history(access(), conversation(), one, current.continuation), am::ConversationReadStatus::InvalidContinuation);
    for(const auto frontier : {std::uint64_t{0}, std::uint64_t{4}, kMax})
        no_data(h.store->read_history_at(access(), conversation(), {}, frontier), am::ConversationReadStatus::InvalidRequest);
    no_data(h.store->read_history_at(access(), conversation(), {}, 3, 1, 0), am::ConversationReadStatus::InvalidRequest);
    no_data(h.store->read_history_at(access(), conversation(), {}, 3, 0, 1), am::ConversationReadStatus::InvalidRequest);
    same_write(h.store->create_conversation(access(), conversation(2)), h.oracle.create_conversation(access(), conversation(2)));
    no_data(h.store->read_history_at(access(), conversation(2), {}, 1), am::ConversationReadStatus::InvalidRequest);
    h.reopen();
    no_data(h.store->read_history(access(), conversation(), one, page.continuation), am::ConversationReadStatus::InvalidContinuation);
    expect(am::serialize_conversation_envelope(page.records[0].envelope) == retained_bytes,
        "History result borrowed transaction/adapter-owned memory");
}

void zero_output_decode_and_local_budgets() {
    Harness h; h.admit(message("one")); auto candidate = next(exact(*h.store, entry(1)));
    set_text(candidate, "corrected"); h.correct(candidate); h.admit(message("two"));
    const auto full = h.store->read_exact(access(), entry(1, 2));
    const auto charge = full.output_bytes, frame = am::serialize_conversation_envelope(candidate).size();
    expect(charge == frame + std::string(kReason).size(), "Exact output omitted correction reason charge");
    am::ConversationReadLimits limits; limits.max_output_bytes = charge;
    same_read(h.store->read_exact(access(), entry(1, 2), limits), h.oracle.history().read_exact(access(), entry(1, 2), limits));
    limits.max_output_bytes = charge - 1;
    expect(h.store->read_exact(access(), entry(1, 2), limits).status == am::ConversationReadStatus::OversizedItem,
        "Reason bytes did not participate in exact output limit");
    limits = {}; limits.max_output_bytes = 0;
    auto oversized = h.store->read_history(access(), conversation(), limits);
    expect(oversized.status == am::ConversationReadStatus::OversizedItem && oversized.records.empty() && oversized.continuation,
        "Oversized first history item has no retry-at-same-seek token");
    same_read(h.store->read_history(access(), conversation(), {}, oversized.continuation),
        h.oracle.history().read_history(access(), conversation()));
    for(unsigned kind = 0; kind < 3; ++kind) {
        limits = {};
        if(kind == 0) limits.max_items = 0;
        if(kind == 1) limits.max_visited_records = 0;
        if(kind == 2) limits.max_decoded_bytes = 0;
        const auto page = h.store->read_history(access(), conversation(), limits);
        expect(page.status == am::ConversationReadStatus::BudgetExhausted && page.records.empty() && page.continuation,
            "Zero history work/item budget is not a real resumable refusal");
        same_read(h.store->read_history(access(), conversation(), {}, page.continuation),
            h.oracle.history().read_history(access(), conversation()));
    }
    limits = {}; limits.max_decoded_bytes = frame - 1;
    expect(h.store->read_exact(access(), entry(1, 2), limits).status == am::ConversationReadStatus::BudgetExhausted,
        "Exact decode limit did not refuse before decode");
    limits = {}; limits.max_items = 65537;
    no_data(h.store->read_history(access(), conversation(), limits), am::ConversationReadStatus::InvalidRequest);
    const auto before = h.fixture.snapshot();
    for(unsigned kind = 0; kind < 3; ++kind) {
        auto options = h.fixture.options(false);
        if(kind == 0) options.max_read_rows = 0;
        if(kind == 1) options.max_read_raw_bytes = 0;
        if(kind == 2) options.max_live_bytes = 0;
        cd::MdbxConversationDurable bounded(options);
        expect(bounded.read_history(access(), conversation()).status == am::ConversationReadStatus::BudgetExhausted &&
            bounded.read_exact(access(), entry(1)).status == am::ConversationReadStatus::BudgetExhausted,
            "Zero local physical work/live budget was ignored or diagnosed as corruption");
        auto changed = next(candidate); set_text(changed, "blocked by local budget");
        const auto result = bounded.correct(access(), changed, am::EventRevision{std::uint64_t{2}}, kReason);
        expect(result.status == am::ConversationWriteStatus::ResourceLimit && !result.ref && !result.commit_generation,
            "Zero local correction budget was ignored");
    }
    expect(h.fixture.snapshot() == before, "Read/local work-budget refusal mutated persistent state");
}

void live_budget_preserves_history_work_and_resume() {
    Harness h; h.admit(message("one")); h.admit(message("two"));
    am::ConversationReadLimits one; one.max_items = 1;
    const auto minimum_budget = [&](bool history) {
        std::uint64_t low = 0, high = h.fixture.options(false).max_live_bytes;
        while(low < high) {
            const auto middle = low + (high - low) / 2;
            auto options = h.fixture.options(false); options.max_live_bytes = middle;
            cd::MdbxConversationDurable bounded(options);
            const auto page = history ? bounded.read_history(access(), conversation(), one) :
                bounded.read_exact(access(), entry(1));
            if(page.records.empty()) low = middle + 1;
            else high = middle;
        }
        return low;
    };
    // Discover platform-specific accounting thresholds, not guessed sizeofs.
    // Before the continuation reservation fix, exact's minimum admits the first
    // history decode, then late token scratch loses the work and retry token.
    const auto exact_minimum = minimum_budget(false);
    {
        auto options = h.fixture.options(false); options.max_live_bytes = exact_minimum;
        cd::MdbxConversationDurable bounded(options);
        const auto page = bounded.read_history(access(), conversation(), one);
        expect(page.status == am::ConversationReadStatus::BudgetExhausted && page.continuation &&
            page.commit_generation == 3, "Tight live budget lost history frontier/continuation after row work");
        expect(page.visited_records == page.records.size(), "Tight live budget lost consumed frame count");
    }
    const auto history_minimum = minimum_budget(true);
    auto options = h.fixture.options(false); options.max_live_bytes = history_minimum;
    cd::MdbxConversationDurable bounded(options);
    const auto page = bounded.read_history(access(), conversation(), one);
    expect(page.status == am::ConversationReadStatus::BudgetExhausted && page.records.size() == 1 &&
        page.continuation && page.commit_generation == 3 && page.visited_records == 1 &&
        page.decoded_bytes == page.output_bytes && page.decoded_bytes > 0 &&
        page.records[0].envelope.metadata.ref == entry(1),
        "One retained history row lost work/frontier/token on next-row live exhaustion");
    const auto tail = bounded.read_history(access(), conversation(), one, page.continuation);
    expect(tail.status == am::ConversationReadStatus::Complete && tail.records.size() == 1 &&
        tail.records[0].envelope.metadata.ref == entry(2) && tail.visited_records == 1 &&
        tail.commit_generation == 3, "Tight live-budget resume skipped or duplicated a revision");
    auto zero_output = one; zero_output.max_output_bytes = 0;
    const auto oversized = bounded.read_history(access(), conversation(), zero_output);
    expect(oversized.status == am::ConversationReadStatus::OversizedItem && oversized.records.empty() &&
        oversized.continuation && oversized.commit_generation == 3 && !oversized.visited_records &&
        !oversized.decoded_bytes, "Tight live-budget oversized item lost same-seek retry token");
    const auto retry = bounded.read_history(access(), conversation(), one, oversized.continuation);
    expect(retry.records.size() == 1 && retry.records[0].envelope.metadata.ref == entry(1),
        "Tight live-budget oversized retry skipped its original item");
    std::printf("Live-budget boundary bytes: exact=%llu history=%llu\n",
        static_cast<unsigned long long>(exact_minimum), static_cast<unsigned long long>(history_minimum));
}

void quota_exact_boundaries_and_generation_exhaustion() {
    auto policy = quotas(); policy.revisions_per_entry = 2;
    Harness h(policy); const auto original = h.admit(message("one"));
    auto corrected = next(exact(*h.store, *original.ref)); set_text(corrected, "revision two"); h.correct(corrected);
    auto no_change = next(corrected); const auto before = h.fixture.snapshot();
    expect(h.correct(no_change).status == am::ConversationWriteStatus::NoChange && h.fixture.snapshot() == before,
        "Reached per-entry quota blocked NoChange or consumed a revision");
    set_text(no_change, "blocked revision three");
    expect(h.correct(no_change).status == am::ConversationWriteStatus::ResourceLimit && h.fixture.snapshot() == before,
        "Per-entry revision quota admitted an extra correction");
    expect(h.admit(message("two")).ref->entry.value() == 2, "Revision quota refusal consumed allocator identity");

    std::uint64_t exact_charge = 0;
    {
        Harness probe; probe.admit(message("boundary")); auto proposal = next(exact(*probe.store, entry(1)));
        set_text(proposal, "boundary corrected");
        exact_charge = probe.fixture.profile().canonical_charge + am::serialize_conversation_envelope(proposal).size() +
            std::string(kReason).size();
    }
    for(const std::uint64_t deficit : {std::uint64_t{1}, std::uint64_t{0}}) {
        policy = quotas(); policy.canonical_bytes = exact_charge - deficit; Harness limited(policy);
        limited.admit(message("boundary")); auto proposal = next(exact(*limited.store, entry(1)));
        set_text(proposal, "boundary corrected"); const auto snapshot = limited.fixture.snapshot();
        const auto result = limited.correct(proposal);
        expect(result.status == (deficit ? am::ConversationWriteStatus::ResourceLimit : am::ConversationWriteStatus::Applied),
            "Canonical frame-plus-reason quota exact boundary is wrong");
        if(deficit) expect(limited.fixture.snapshot() == snapshot, "One-byte-short canonical quota mutated state");
        else expect(limited.fixture.profile().canonical_charge == policy.canonical_bytes,
            "Exact canonical quota charge was not persisted");
    }

    Harness exhausted; const auto admitted = exhausted.admit(message("max-generation"));
    auto profile = exhausted.fixture.profile();
    // Arithmetic boundary seed only, not a claim of a physically populated MAX history.
    profile.generation = kMax; exhausted.fixture.profile(profile);
    auto proposal = next(exact(*exhausted.store, *admitted.ref));
    const auto noop = exhausted.store->correct(access(), proposal, am::EventRevision{std::uint64_t{1}}, kReason);
    expect(noop.status == am::ConversationWriteStatus::NoChange && noop.commit_generation == kMax,
        "Exhausted generation rejected reference-compatible NoChange");
    const auto snapshot = exhausted.fixture.snapshot(); set_text(proposal, "cannot wrap");
    const auto refused = exhausted.store->correct(access(), proposal, am::EventRevision{std::uint64_t{1}}, kReason);
    expect(refused.status == am::ConversationWriteStatus::ResourceLimit && !refused.ref && !refused.commit_generation &&
        exhausted.fixture.snapshot() == snapshot, "Correction generation wrapped or changed counters on exhaustion");
    const auto replay = exhausted.store->import_observation(access(), message("max-generation"));
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == admitted.ref &&
        replay.commit_generation == admitted.commit_generation, "Generation exhaustion blocked original source replay");
}
} // namespace

int main() {
    try {
        std::puts("CASE immutable corrections/original replay");
        immutable_corrections_and_original_replay();
        std::puts("CASE immutable bindings/invalid requests");
        immutable_bindings_and_invalid_requests();
        std::puts("CASE tool identity/frozen unresolved replay");
        tool_identity_and_frozen_unresolved_replay();
        std::puts("CASE frontier filtering/revision pages");
        frontier_filtering_and_revision_pages();
        std::puts("CASE trailing/many invisible revisions");
        trailing_and_many_invisible_revisions();
        std::puts("CASE continuation isolation/owning results");
        continuations_fail_closed_and_results_own_data();
        std::puts("CASE zero/output/decode/local budgets");
        zero_output_decode_and_local_budgets();
        std::puts("CASE live-budget work/continuation boundaries");
        live_budget_preserves_history_work_and_resume();
        std::puts("CASE quota exact boundaries/generation exhaustion");
        quota_exact_boundaries_and_generation_exhaustion();
        std::puts("PASS durable correction/reference parity, immutable receipts/source replay and revision/frontier pagination");
        return 0;
    } catch(const std::exception& error) {
        std::fprintf(stderr, "durable correction semantics: %s\n", error.what());
        return 1;
    }
}
