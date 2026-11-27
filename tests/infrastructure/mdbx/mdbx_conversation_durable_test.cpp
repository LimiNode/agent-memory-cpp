#include <agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.hpp>
#include <mdbx_containers/common.hpp>

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace am = agent_memory;
namespace cd = agent_memory::detail::conversation_durable;

namespace {
constexpr std::uint64_t kMapUpper = 64U * 1024U * 1024U;
constexpr std::uint64_t kMapGrowth = 1024U * 1024U;
const char* const kTables[] = {"agent_memory_conversation_meta_v1",
    "agent_memory_conversation_events_v1", "agent_memory_conversation_sources_v1"};

void expect(bool value, const char* message) {
    if(!value) throw std::runtime_error(message);
}
void checked(int code, const char* operation) {
    if(code != MDBX_SUCCESS) throw std::runtime_error(std::string(operation) + ": " + mdbx_strerror(code));
}
template<class Function> void rejects(Function&& function, const char* message) {
    bool rejected = false;
    try { function(); } catch(const std::exception&) { rejected = true; }
    expect(rejected, message);
}
template<class Function> void rejects_codec(Function&& function, cd::CodecCode expected, const char* message) {
    try { function(); }
    catch(const cd::CodecError& error) { expect(error.code() == expected, message); return; }
    throw std::runtime_error(message);
}
am::ByteView view(const am::ByteBuffer& bytes) { return {bytes.data(), bytes.size()}; }
MDBX_val raw(am::ByteView bytes) {
    MDBX_val result{};
    result.iov_base = const_cast<std::uint8_t*>(bytes.data);
    result.iov_len = bytes.size;
    return result;
}
am::ConversationRef conversation(std::uint64_t id = 1) { return {"import", am::ConversationId{id}}; }
am::ConversationStoreAccess access() { return {am::ScopeId{"scope-a"}, "host-policy-v1"}; }
cd::Quotas quotas() { return {64, 1024, 64, 16U * 1024U * 1024U, 1024, 16U * 1024U * 1024U}; }
am::ConversationImportOptions oracle_options() {
    am::ConversationImportOptions result;
    result.store.allocation_namespace = "import";
    return result;
}
am::ExternalObjectRef external(std::string key, std::string domain = "run-a", std::string kind = "event") {
    return {"fixture", "source-instance", std::move(domain), std::move(kind), std::nullopt,
        am::ExternalObjectKey{std::move(key)}};
}
am::ConversationSourceBinding source(std::string key, std::string domain = "run-a") {
    am::ConversationSourceBinding result;
    result.identity = am::ConversationSourceIdentity::ExternalEvent;
    result.event = external(key, domain);
    result.object = external("shared-message", domain, "message");
    result.revision = am::SourceRevisionId{"retained-archive@1"};
    result.adapter_recipe = "authored-reference-fixture-v1";
    result.evidence = {am::ArtifactBindingId{"archive-artifact@1"}, "records/" + key};
    return result;
}
am::ConversationSourceObservation message(std::string key) {
    am::ConversationSourceObservation result;
    result.conversation = conversation(); result.scope = access().scope;
    result.source = source(std::move(key)); result.producer = "fixture-exporter-v1";
    result.actor = am::ActorRef{"import", am::ActorId{std::uint64_t{2}}};
    result.observer = am::ActorRef{"import", am::ActorId{std::uint64_t{3}}};
    result.llm_role = am::ConversationLlmRole::User;
    result.payload = am::ConversationSourceMessage{am::ConversationSourceMessageKind::Created,
        {std::string("source text"), {}}};
    return result;
}
am::ConversationSourceObservation call(std::string key, std::string domain = "run-a") {
    auto result = message(key); result.source = source(std::move(key), std::move(domain));
    result.llm_role = am::ConversationLlmRole::Assistant;
    result.payload = am::ConversationSourceToolCall{{0, 255, 1}, "shell",
        am::ArtifactBindingId{"original-arguments-A"}, am::ArtifactBindingId{"effective-arguments-B"}};
    return result;
}
am::ConversationSourceObservation tool_result(std::string key, const am::ConversationSourceObservation& target,
    am::ConversationSourceResultOrigin origin = am::ConversationSourceResultOrigin::Reported) {
    auto result = message(std::move(key)); result.llm_role = am::ConversationLlmRole::Tool;
    result.payload = am::ConversationSourceToolResult{
        am::ConversationSourceTarget{target.conversation, target.scope, am::copy_conversation_source_binding(target.source)},
        {0, 255, 1}, origin, am::ConversationToolOutcome::Succeeded,
        am::ArtifactBindingId{"result-artifact"}, false, std::int64_t{0}};
    return result;
}
am::ConversationSourceObservation composite_call() {
    auto result = call("late-composite");
    std::vector<am::ExternalIdentityComponent> components{
        {"signed", std::int64_t{-9}}, {"unsigned", std::uint64_t{42}}, {"text", std::string("001")}};
    result.source.event = am::ExternalObjectRef{"fixture", "source-instance", "run-a", "event",
        am::ExternalObjectKey{std::string("parent")}, am::ExternalObjectKey{std::move(components)}};
    return result;
}

struct Fixture final {
    std::filesystem::path path;
    std::shared_ptr<am::MdbxStorageContext> context;
    std::int64_t max_dbs = 96;
    bool is_owner = true;
    explicit Fixture(const char* label, std::int64_t capacity = 96) : max_dbs(capacity) {
        path = std::filesystem::temp_directory_path() / (std::string("agent_memory_conversation_") + label +
            std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".mdbx");
        open();
    }
    explicit Fixture(std::filesystem::path existing) : path(std::move(existing)), is_owner(false) { open(); }
    ~Fixture() {
        context.reset();
        if(is_owner) {
            std::error_code error;
            std::filesystem::remove(path, error);
            std::filesystem::remove(path.string() + "-lck", error);
        }
    }
    void open(bool is_sync = true, bool is_writemap = false) {
        mdbxc::Config config;
        config.pathname = path.string(); config.no_subdir = true; config.relative_to_exe = false;
        config.max_dbs = max_dbs; config.size_upper = kMapUpper; config.growth_step = kMapGrowth;
        config.sync_durable = is_sync; config.writemap_mode = is_writemap;
        context = am::MdbxStorageContext::attach(mdbxc::Connection::create(config));
        // SYNC_DURABLE is zero; the wrapper's false setting alone does not
        // select a weaker flag. Exercise the actual environment, not the bool.
        if(!is_sync) checked(mdbx_env_set_flags(context->connection()->env_handle(), MDBX_SAFE_NOSYNC, true),
            "select unsafe actual sync flags");
    }
    void reopen() { context.reset(); open(); }
    cd::DurableOptions options(bool initialize = true) const {
        MDBX_envinfo info{};
        checked(mdbx_env_info_ex(context->connection()->env_handle(), nullptr, &info, sizeof(info)), "environment info");
        cd::DurableOptions result(context, "import", quotas(), {info.mi_geo.upper, info.mi_geo.grow});
        result.allow_initialize = initialize;
        return result;
    }
};

using Snapshot = std::vector<std::tuple<std::string, std::string, std::string>>;
Snapshot snapshot(Fixture& fixture) {
    Snapshot result;
    auto transaction = fixture.context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    for(const auto* table : kTables) {
        MDBX_dbi dbi{};
        const int opened = mdbx_dbi_open(transaction.handle(), table, MDBX_DB_ACCEDE, &dbi);
        if(opened == MDBX_NOTFOUND) continue;
        checked(opened, "snapshot open");
        unsigned table_flags = 0;
        checked(mdbx_dbi_flags(transaction.handle(), dbi, &table_flags), "snapshot flags");
        result.emplace_back(table, std::string{}, std::to_string(table_flags));
        MDBX_cursor* cursor = nullptr;
        checked(mdbx_cursor_open(transaction.handle(), dbi, &cursor), "snapshot cursor");
        MDBX_val key{}, value{};
        int code = mdbx_cursor_get(cursor, &key, &value, MDBX_FIRST);
        while(code == MDBX_SUCCESS) {
            result.emplace_back(table, std::string(static_cast<const char*>(key.iov_base), key.iov_len),
                std::string(static_cast<const char*>(value.iov_base), value.iov_len));
            code = mdbx_cursor_get(cursor, &key, &value, MDBX_NEXT);
        }
        mdbx_cursor_close(cursor);
        expect(code == MDBX_NOTFOUND, "snapshot scan failed");
    }
    transaction.commit();
    return result;
}
am::ByteBuffer profile_bytes(Fixture& fixture) {
    const auto rows = snapshot(fixture);
    const auto key = cd::encode_profile_key();
    const std::string expected(reinterpret_cast<const char*>(key.data()), key.size());
    for(const auto& row : rows) {
        if(std::get<0>(row) == kTables[0] && std::get<1>(row) == expected) {
            const auto& bytes = std::get<2>(row);
            return {bytes.begin(), bytes.end()};
        }
    }
    throw std::runtime_error("profile absent");
}
void put_raw(Fixture& fixture, const char* table, const am::ByteBuffer& key, const am::ByteBuffer& value) {
    auto transaction = fixture.context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    MDBX_dbi dbi{}; checked(mdbx_dbi_open(transaction.handle(), table, MDBX_DB_DEFAULTS, &dbi), "mutation open");
    auto raw_key = raw(view(key)); auto raw_value = raw(view(value));
    checked(mdbx_put(transaction.handle(), dbi, &raw_key, &raw_value, MDBX_UPSERT), "mutation put");
    transaction.commit();
}
void same_write(const am::ConversationWriteResult& actual, const am::ConversationWriteResult& expected) {
    expect(actual.status == expected.status && actual.ref == expected.ref &&
        actual.commit_generation == expected.commit_generation, "oracle write/ref/generation parity");
}
void same_read(const am::ConversationReadResult& actual, const am::ConversationReadResult& expected) {
    expect(actual.status == expected.status && actual.commit_generation == expected.commit_generation &&
        actual.records.size() == expected.records.size(), "oracle read status/frontier/count parity");
    for(std::size_t i = 0; i < actual.records.size(); ++i) {
        expect(am::serialize_conversation_envelope(actual.records[i].envelope) ==
            am::serialize_conversation_envelope(expected.records[i].envelope) &&
            actual.records[i].commit_generation == expected.records[i].commit_generation,
            "oracle canonical AMCE/generation parity");
    }
}
void same_current(const am::ConversationReadResult& actual, const am::ConversationReadResult& expected) {
    expect(actual.status == expected.status && actual.commit_generation == expected.commit_generation &&
        actual.messages.size() == expected.messages.size() && actual.output_bytes == expected.output_bytes &&
        actual.visited_records == expected.visited_records && actual.decoded_bytes == expected.decoded_bytes,
        "oracle Current status/frontier/count/budget parity");
    for(std::size_t i = 0; i < actual.messages.size(); ++i) {
        const auto& a = actual.messages[i];
        const auto& b = expected.messages[i];
        expect(a.origin == b.origin && a.state == b.state && a.content_ref == b.content_ref && a.status == b.status &&
            a.is_snapshot_origin == b.is_snapshot_origin && a.content.has_value() == b.content.has_value(),
            "oracle Current identity/state parity");
        if(a.content) expect(a.content->text == b.content->text && a.content->attachments == b.content->attachments,
            "oracle Current content parity");
    }
}
am::ConversationWriteResult admit(cd::MdbxConversationDurable& store, const am::ConversationSourceObservation& input) {
    const auto result = store.import_observation(access(), input);
    expect(result.status == am::ConversationWriteStatus::Applied && result.ref.has_value(), "durable source admission");
    return result;
}
am::ConversationEnvelope exact(cd::MdbxConversationDurable& store, const am::EntryRef& ref) {
    const auto result = store.read_exact(access(), ref);
    expect(result.status == am::ConversationReadStatus::Complete && result.records.size() == 1, "durable exact");
    return result.records.front().envelope;
}

void authored_source_parity() {
    Fixture fixture("authored"); cd::MdbxConversationDurable store(fixture.options());
    am::InMemoryConversationImportSink oracle(oracle_options());
    same_write(store.create_conversation(access(), conversation()), oracle.create_conversation(access(), conversation()));
    const auto hidden = am::ConversationStoreAccess{am::ScopeId{"scope-b"}, "host-policy-v1"};
    const auto before_hidden_create = snapshot(fixture);
    same_write(store.create_conversation(hidden, conversation()), oracle.create_conversation(hidden, conversation()));
    expect(store.create_conversation(hidden, conversation()).status == am::ConversationWriteStatus::NotFound &&
        before_hidden_create == snapshot(fixture), "hidden create differs from oracle or mutates state");
    const auto compare = [&](const am::ConversationSourceObservation& input) {
        const auto actual = store.import_observation(access(), input);
        const auto expected = oracle.import_observation(access(), input);
        same_write(actual, expected);
        if(actual.ref) same_read(store.read_exact(access(), *actual.ref), oracle.history().read_exact(access(), *expected.ref));
        same_current(store.read_current(access(), conversation()), oracle.history().read_current(access(), conversation()));
        return actual;
    };
    const auto first = compare(message("message"));
    compare(message("message"));
    auto changed = message("message"); changed.producer = "changed"; compare(changed);
    auto revision = message("object-revision");
    revision.source.identity = am::ConversationSourceIdentity::ObjectRevision;
    revision.source.event.reset(); revision.source.provider_revision_marker = std::string("marker\0\xff", 8);
    compare(revision); compare(revision);
    revision.source.identity = am::ConversationSourceIdentity::LocalObservation;
    revision.source.provider_revision_marker.reset();
    std::get<am::ConversationSourceMessage>(revision.payload).kind = am::ConversationSourceMessageKind::Snapshot;
    compare(revision);
    const auto request = call("call"); const auto admitted_call = compare(request);
    const auto normalized = std::get<am::ConversationToolCallObserved>(exact(store, *admitted_call.ref).payload);
    expect(normalized.request == am::ArtifactBindingId{"original-arguments-A"}, "original request retained, not effective");
    auto missing_request = call("missing-request");
    std::get<am::ConversationSourceToolCall>(missing_request.payload).original_request = am::ArtifactBindingId{};
    compare(missing_request);
    auto effective = request;
    std::get<am::ConversationSourceToolCall>(effective.payload).effective_request = am::ArtifactBindingId{"different-B"};
    compare(effective);
    unsigned index = 0;
    for(const auto origin : {am::ConversationSourceResultOrigin::PermissionDenied,
        am::ConversationSourceResultOrigin::Recovery, am::ConversationSourceResultOrigin::Timeout,
        am::ConversationSourceResultOrigin::Repaired, am::ConversationSourceResultOrigin::Unknown}) {
        auto input = tool_result("synthetic-" + std::to_string(index++), request, origin);
        auto& flags = std::get<am::ConversationSourceToolResult>(input.payload);
        flags.is_error = true; flags.exit_code = 1; flags.reported_outcome = am::ConversationToolOutcome::Failed;
        const auto admitted = compare(input);
        expect(std::get<am::ConversationToolResultObserved>(exact(store, *admitted.ref).payload).outcome ==
            am::ConversationToolOutcome::Unknown, "synthetic origin is not an execution outcome");
        flags.is_error = false; compare(input);
    }
    compare(call("call", "run-b"));
    compare(tool_result("attempt-1", request)); compare(tool_result("attempt-2", request));
    for(const auto outcome : {am::ConversationToolOutcome::Unknown, am::ConversationToolOutcome::Succeeded,
        am::ConversationToolOutcome::Failed, am::ConversationToolOutcome::Cancelled}) {
        auto input = tool_result("reported-" + std::to_string(static_cast<int>(outcome)), request);
        std::get<am::ConversationSourceToolResult>(input.payload).reported_outcome = outcome;
        compare(input);
    }
    auto denied = message("denied"); denied.scope = am::ScopeId{"other"}; compare(denied);
    auto wrong_target = tool_result("wrong-target", request);
    std::get<am::ConversationSourceTarget>(std::get<am::ConversationSourceToolResult>(wrong_target.payload).call).scope =
        am::ScopeId{"other"};
    compare(wrong_target);
    same_read(store.read_history(access(), conversation()), oracle.history().read_history(access(), conversation()));
    expect(first.ref->entry.value() == 1, "first committed entry is one");
}

void quotas_and_owning_outputs() {
    Fixture fixture("quota"); auto options = fixture.options();
    options.quotas.entries = 1; options.quotas.mapping_rows = 1;
    am::ConversationReadResult retained;
    am::ByteBuffer canonical;
    {
        cd::MdbxConversationDurable store(options);
        expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied, "quota create");
        auto input = message("one"); const auto admitted = admit(store, input);
        retained = store.read_exact(access(), *admitted.ref);
        canonical = am::serialize_conversation_envelope(retained.records.front().envelope);
        std::get<am::ConversationSourceMessage>(input.payload).content.text = "caller changed input";
        expect(am::serialize_conversation_envelope(exact(store, *admitted.ref)) == canonical, "stored bytes own caller input");
        const auto before = snapshot(fixture);
        const auto replay = store.import_observation(access(), message("one"));
        expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == admitted.ref &&
            replay.commit_generation == admitted.commit_generation, "replay works at entry/mapping quota");
        expect(store.import_observation(access(), input).status == am::ConversationWriteStatus::Conflict, "conflict precedes quota");
        expect(store.import_observation(access(), message("two")).status == am::ConversationWriteStatus::ResourceLimit,
            "new mapping refused at quota");
        expect(before == snapshot(fixture), "quota/replay/conflict mutate no physical rows");
        auto limits = am::ConversationReadLimits{}; limits.max_items = 0;
        const auto bounded = store.read_history(access(), conversation(), limits);
        expect(bounded.records.empty() && bounded.status != am::ConversationReadStatus::Complete,
            "zero output budget never fabricates complete history");
    }
    options.context.reset();
    fixture.context.reset();
    expect(am::serialize_conversation_envelope(retained.records.front().envelope) == canonical,
        "returned envelope owns bytes after environment shutdown");
    fixture.open();
    auto reopen_options = fixture.options(false); reopen_options.quotas = options.quotas;
    cd::MdbxConversationDurable reopened(reopen_options);
    expect(reopened.import_observation(access(), message("one")).status == am::ConversationWriteStatus::NoChange,
        "persisted quota permits replay after reopen");
}

void zero_quotas_forbid_new_consumption() {
    for(unsigned resource = 0; resource < 6; ++resource) {
        Fixture fixture("zero_quota"); auto options = fixture.options();
        if(resource == 0) options.quotas.conversations = 0;
        if(resource == 1) options.quotas.entries = 0;
        if(resource == 2) options.quotas.mapping_rows = 0;
        if(resource == 3) options.quotas.canonical_bytes = 0;
        if(resource == 4) options.quotas.mapping_bytes = 0;
        if(resource == 5) options.quotas.revisions_per_entry = 0;
        cd::MdbxConversationDurable store(options);
        const auto creation = store.create_conversation(access(), conversation());
        if(resource == 0 || resource == 3) {
            expect(creation.status == am::ConversationWriteStatus::ResourceLimit,
                "zero conversation/canonical quota forbids conversation scope charge");
        } else {
            expect(creation.status == am::ConversationWriteStatus::Applied, "zero event quota still permits conversation");
            const auto before = snapshot(fixture);
            expect(store.import_observation(access(), message("blocked")).status == am::ConversationWriteStatus::ResourceLimit,
                "zero event/mapping quota is not unlimited");
            expect(before == snapshot(fixture), "zero-quota refusal consumes no state");
        }
    }
}

std::uint64_t source_value_size(Fixture& fixture) {
    std::uint64_t result = 0;
    for(const auto& row : snapshot(fixture))
        if(std::get<0>(row) == kTables[2] && !std::get<1>(row).empty()) result += std::get<2>(row).size();
    return result;
}
void collision_bucket_admission() {
    cd::QualificationHooks hooks; hooks.force_digest_collision = true;
    std::uint64_t one_row_bytes = 0;
    {
        Fixture fixture("collision_rows"); auto options = fixture.options(); options.descriptor.bucket_rows = 2;
        cd::MdbxConversationDurable store(options, hooks);
        expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied, "collision create");
        const auto first = admit(store, message("a")); one_row_bytes = source_value_size(fixture);
        const auto second = admit(store, message("b"));
        expect(first.ref != second.ref, "digest collision does not coalesce distinct full keys");
        const auto before = snapshot(fixture);
        expect(store.import_observation(access(), message("c")).status == am::ConversationWriteStatus::ResourceLimit,
            "post-insert bucket row ceiling");
        for(const auto* key : {"a", "b"}) expect(store.import_observation(access(), message(key)).status ==
            am::ConversationWriteStatus::NoChange, "old colliding mapping remains replayable at row ceiling");
        auto changed = message("b"); changed.producer = "changed";
        expect(store.import_observation(access(), changed).status == am::ConversationWriteStatus::Conflict,
            "changed witness checked for exact colliding key");
        expect(before == snapshot(fixture), "collision refusal/replays mutate no rows");
    }
    expect(one_row_bytes > 0, "source row physical charge measured");
    for(const bool is_exact : {false, true}) {
        Fixture fixture(is_exact ? "collision_bytes_exact" : "collision_bytes_over"); auto options = fixture.options();
        options.descriptor.bucket_bytes = 2 * one_row_bytes - (is_exact ? 0 : 1);
        cd::MdbxConversationDurable store(options, hooks);
        expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied, "byte bucket create");
        admit(store, message("a"));
        expect(source_value_size(fixture) == one_row_bytes, "matched row grammar for byte control");
        const auto before = snapshot(fixture);
        const auto second = store.import_observation(access(), message("b"));
        expect(second.status == (is_exact ? am::ConversationWriteStatus::Applied : am::ConversationWriteStatus::ResourceLimit),
            "aggregate bucket bytes exact/one-over boundary, not per-candidate bytes");
        if(!is_exact) expect(before == snapshot(fixture), "byte bucket refusal is atomic");
        expect(store.import_observation(access(), message("a")).status == am::ConversationWriteStatus::NoChange,
            "later admission cannot strand earlier replay");
    }
}

bool is_hook_enabled = false;
cd::WriteStep selected_step = cd::WriteStep::BeforeWrites;
void fail_at_step(cd::WriteStep step) {
    if(is_hook_enabled && step == selected_step) throw std::runtime_error("qualification injected rollback");
}
void rollback_each_write_boundary() {
    for(const auto step : {cd::WriteStep::BeforeWrites, cd::WriteStep::AfterRevision, cd::WriteStep::AfterSource,
        cd::WriteStep::AfterGeneration, cd::WriteStep::BeforeCommit}) {
        Fixture fixture("rollback"); cd::QualificationHooks hooks; hooks.before_commit_step = fail_at_step;
        {
            cd::MdbxConversationDurable store(fixture.options(), hooks);
            expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied, "rollback create");
            admit(store, message("baseline")); const auto before = snapshot(fixture);
            selected_step = step; is_hook_enabled = true;
            rejects([&] { (void)store.import_observation(access(), message("candidate")); }, "write hook did not throw");
            is_hook_enabled = false;
            expect(before == snapshot(fixture), "injected failure leaves no tentative rows/counters");
        }
        fixture.reopen(); cd::MdbxConversationDurable store(fixture.options(false));
        const auto admitted = admit(store, message("candidate"));
        expect(admitted.ref->entry.value() == 2 && admitted.commit_generation == 3,
            "rollback does not consume durable entry/generation");
        expect(store.import_observation(access(), message("baseline")).status == am::ConversationWriteStatus::NoChange,
            "rollback preserves prior mapping");
    }
}

void rejected_open_is_nonmutating() {
    Fixture fixture("open_refusal");
    {
        cd::MdbxConversationDurable store(fixture.options());
        expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied, "open baseline create");
        admit(store, message("baseline"));
    }
    const auto before = snapshot(fixture);
    auto wrong_namespace = fixture.options(false); wrong_namespace.allocation_namespace = "foreign";
    rejects([&] { cd::MdbxConversationDurable store(wrong_namespace); }, "foreign namespace accepted");
    auto wrong_descriptor = fixture.options(false); wrong_descriptor.descriptor.bucket_rows += 1;
    rejects([&] { cd::MdbxConversationDurable store(wrong_descriptor); }, "incompatible descriptor accepted");
    auto wrong_prefix = fixture.options(); wrong_prefix.table_prefix = "Bad Prefix";
    rejects([&] { cd::MdbxConversationDurable store(wrong_prefix); }, "prefix sanitized instead of rejected");
    auto insufficient_composition = fixture.options(false); insufficient_composition.other_profile_peak_dbis = 78;
    rejects([&] { cd::MdbxConversationDurable store(insufficient_composition); }, "DBI free-slot reserve not enforced");
    auto wrong_map = fixture.options(false); wrong_map.map_policy.upper_bytes -= kMapGrowth;
    rejects([&] { cd::MdbxConversationDurable store(wrong_map); }, "mismatched actual map policy accepted");
    expect(before == snapshot(fixture), "rejected options mutate existing rows");
    {
        cd::MdbxConversationDurable store(fixture.options(false));
        auto foreign = message("foreign"); foreign.conversation.allocation_namespace = "foreign";
        expect(store.import_observation(access(), foreign).status != am::ConversationWriteStatus::Applied,
            "foreign request compact-key seek accepted");
        auto denied = access(); denied.scope = am::ScopeId{"other"};
        expect(store.read_exact(denied, {conversation(), am::EntryId{std::uint64_t{1}}, am::EventRevision{std::uint64_t{1}}}).records.empty(),
            "denied read leaks owning record");
    }
    expect(before == snapshot(fixture), "foreign request mutates rows");
    auto malformed = profile_bytes(fixture); malformed[0] = 0x7f;
    put_raw(fixture, kTables[0], cd::encode_profile_key(), malformed);
    const auto unknown = snapshot(fixture);
    rejects([&] { cd::MdbxConversationDurable store(fixture.options()); }, "unknown value schema accepted/stamped");
    expect(unknown == snapshot(fixture), "unknown schema rewritten on open");

    Fixture capacity("capacity", 18); const auto empty = snapshot(capacity);
    rejects([&] { cd::MdbxConversationDurable store(capacity.options()); }, "insufficient environment DBI capacity accepted");
    expect(empty == snapshot(capacity), "capacity refusal creates partial profile tables");

    Fixture flags("flags");
    {
        auto transaction = flags.context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        MDBX_dbi dbi{};
        checked(mdbx_dbi_open(transaction.handle(), kTables[1], MDBX_CREATE | MDBX_INTEGERKEY, &dbi), "wrong-flags fixture");
        transaction.commit();
    }
    const auto wrong_flags = snapshot(flags);
    rejects([&] { cd::MdbxConversationDurable store(flags.options()); }, "wrong table comparator accepted");
    expect(wrong_flags == snapshot(flags), "wrong-flags refusal stamps profile");

    for(const bool is_writemap : {false, true}) {
        Fixture weak(is_writemap ? "writemap" : "nosync"); weak.context.reset(); weak.open(is_writemap, is_writemap);
        const auto weak_before = snapshot(weak);
        rejects([&] { cd::MdbxConversationDurable store(weak.options()); }, "unsafe environment flags accepted");
        expect(weak_before == snapshot(weak), "unsafe flag refusal creates tables");
    }
}

void persisted_corruption_is_not_a_local_budget() {
    Fixture fixture("corruption");
    cd::MdbxConversationDurable store(fixture.options());
    (void)store.create_conversation(access(), conversation());
    const auto admitted = admit(store, message("one"));
    auto raw_profile = profile_bytes(fixture);
    raw_profile.insert(raw_profile.end(), 5000, 0);
    put_raw(fixture, kTables[0], cd::encode_profile_key(), raw_profile);
    const auto malformed = snapshot(fixture);
    rejects_codec([&] { (void)store.read_exact(access(), *admitted.ref); }, cd::CodecCode::Corruption,
        "oversized stored profile misdiagnosed as a local read budget");
    rejects_codec([&] { (void)store.import_observation(access(), message("one")); }, cd::CodecCode::Corruption,
        "oversized stored profile misdiagnosed as an import budget");
    expect(snapshot(fixture) == malformed, "corruption refusal changed persisted bytes");

    Fixture bucket("corrupt_bucket"); cd::MdbxConversationDurable bucket_store(bucket.options());
    (void)bucket_store.create_conversation(access(), conversation());
    const auto first = admit(bucket_store, message("one"));
    for(const auto& row : snapshot(bucket)) {
        if(std::get<0>(row) != kTables[2] || std::get<1>(row).empty()) continue;
        am::ByteBuffer key(std::get<1>(row).begin(), std::get<1>(row).end());
        am::ByteBuffer value(std::get<2>(row).begin(), std::get<2>(row).end());
        value.resize(8U * 1024U * 1024U + 1);
        put_raw(bucket, kTables[2], key, value);
    }
    rejects_codec([&] { (void)bucket_store.import_observation(access(), message("one")); }, cd::CodecCode::Corruption,
        "persisted bucket above admission bound misdiagnosed as local budget");
    expect(bucket_store.read_exact(access(), *first.ref).records.size() == 1, "source corruption erased independent history");
}

void local_budgets_and_complete_environment_capacity() {
    Fixture fixture("local_budgets"); am::EntryRef retained;
    {
        cd::MdbxConversationDurable store(fixture.options());
        (void)store.create_conversation(access(), conversation());
        auto input = message("many_objects");
        auto& content = std::get<am::ConversationSourceMessage>(input.payload).content;
        content.text.reset(); content.attachments.assign(256, am::ArtifactBindingId{"x"});
        retained = *admit(store, input).ref;
    }
    auto tight = fixture.options(false); tight.max_live_bytes = 4096;
    cd::MdbxConversationDurable small_heap(tight);
    expect(small_heap.read_exact(access(), retained).status == am::ConversationReadStatus::BudgetExhausted,
        "short attachment objects bypass live-buffer bound");
    const auto before = snapshot(fixture);
    expect(small_heap.import_observation(access(), message("new")).status == am::ConversationWriteStatus::ResourceLimit,
        "AMXR preflight allocates before tiny live budget refuses");
    expect(before == snapshot(fixture), "local live-budget refusal mutates journal");
    auto zero = fixture.options(false); zero.max_read_rows = zero.max_read_raw_bytes = 0;
    cd::MdbxConversationDurable no_read_work(zero);
    expect(no_read_work.read_exact(access(), retained).status == am::ConversationReadStatus::BudgetExhausted,
        "zero local read work cannot masquerade as corruption or NotFound");

    {
        auto transaction = fixture.context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        MDBX_dbi host{}; checked(mdbx_dbi_open(transaction.handle(), "host_table", MDBX_CREATE, &host), "host table");
        transaction.commit();
    }
    const auto with_host = snapshot(fixture);
    rejects_codec([&] { cd::MdbxConversationDurable unaccounted(fixture.options(false)); },
        cd::CodecCode::UnsupportedSchema, "unaccounted host table silently consumes migration reserve");
    auto accounted = fixture.options(false); accounted.other_profile_peak_dbis = 9;
    cd::MdbxConversationDurable composed(accounted);
    expect(composed.read_exact(access(), retained).records.size() == 1, "explicit whole-environment peak rejected");
    expect(with_host == snapshot(fixture), "capacity verification changes journal");
}

void schema_initialization_rolls_back_atomically() {
    Fixture fixture("schema_abort"); const auto empty = snapshot(fixture);
    cd::QualificationHooks hooks; hooks.before_commit_step = fail_at_step;
    selected_step = cd::WriteStep::BeforeCommit; is_hook_enabled = true;
    rejects([&] { cd::MdbxConversationDurable aborted(fixture.options(), hooks); }, "schema failure hook did not throw");
    is_hook_enabled = false;
    expect(empty == snapshot(fixture), "schema abort left partial DBIs or marker");
    cd::MdbxConversationDurable retry(fixture.options());
    expect(retry.create_conversation(access(), conversation()).commit_generation == 1, "schema retry consumed a generation");
}

void replay_memory_is_not_writer_preparation() {
    Fixture fixture("replay_memory");
    auto input = message("maximum_default_text");
    std::get<am::ConversationSourceMessage>(input.payload).content.text = std::string(1024U * 1024U, 'x');
    am::ConversationWriteResult receipt;
    {
        cd::MdbxConversationDurable store(fixture.options());
        (void)store.create_conversation(access(), conversation());
        receipt = admit(store, input);
    }
    fixture.reopen();
    auto options = fixture.options(false);
    options.max_live_bytes = 12U * 1024U * 1024U;
    cd::MdbxConversationDurable reader(options);
    const auto replay = reader.import_observation(access(), input);
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == receipt.ref &&
        replay.commit_generation == receipt.commit_generation,
        "replay incorrectly reserves future writer buffers");
    auto changed = message("maximum_default_text");
    std::get<am::ConversationSourceMessage>(changed.payload).content.text = std::string(1024U * 1024U, 'x');
    changed.producer = "different-producer";
    expect(reader.import_observation(access(), changed).status == am::ConversationWriteStatus::Conflict,
        "changed witness needs no future writer buffers");
    input.source = source("new_maximum_default_text");
    expect(reader.import_observation(access(), input).status == am::ConversationWriteStatus::ResourceLimit,
        "new admission bypasses writer preparation budget");
}

void history_paging_and_frontier() {
    Fixture fixture("paging"); cd::MdbxConversationDurable store(fixture.options());
    expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied, "paging create");
    const auto first = admit(store, message("first"));
    const auto second = admit(store, message("second"));
    admit(store, message("third"));
    am::ConversationReadLimits limits; limits.max_items = 1;
    const auto page = store.read_history(access(), conversation(), limits);
    expect(page.status == am::ConversationReadStatus::Partial && page.records.size() == 1 &&
        page.records.front().envelope.metadata.ref == *first.ref, "bounded history first page");
    const auto tail = store.read_history_at(access(), conversation(), {}, page.commit_generation,
        first.ref->entry.value(), first.ref->revision.value());
    expect(tail.status == am::ConversationReadStatus::Complete && tail.records.size() == 2 &&
        tail.records.front().envelope.metadata.ref == *second.ref, "exclusive seek resumes pinned frontier");
    const auto pinned = store.read_history_at(access(), conversation(), {}, first.commit_generation);
    expect(pinned.records.size() == 1 && pinned.commit_generation == first.commit_generation,
        "qualification frontier excludes later admissions");
}

void state_payload_admission() {
    Fixture fixture("state");
    cd::MdbxConversationDurable store(fixture.options());
    am::InMemoryConversationImportSink oracle(oracle_options());
    same_write(store.create_conversation(access(), conversation()), oracle.create_conversation(access(), conversation()));
    const auto compare = [&](const am::ConversationSourceObservation& input) {
        const auto actual = store.import_observation(access(), input);
        const auto expected = oracle.import_observation(access(), input);
        same_write(actual, expected);
        if(actual.ref) same_read(store.read_exact(access(), *actual.ref), oracle.history().read_exact(access(), *expected.ref));
        same_current(store.read_current(access(), conversation()), oracle.history().read_current(access(), conversation()));
        return actual;
    };
    const auto base = compare(message("state-base"));
    auto edited = message("state-edit");
    edited.payload = am::ConversationEdited{*base.ref, {std::string{"edited"}, {}}};
    const auto edit = compare(edited);
    auto tombstoned = message("state-tombstone");
    tombstoned.payload = am::ConversationTombstoned{*edit.ref};
    const auto tombstone = compare(tombstoned);
    auto restored = message("state-restore");
    restored.payload = am::ConversationRestored{*tombstone.ref, *base.ref};
    const auto restore = compare(restored);
    expect(std::get<am::ConversationRestored>(std::get<am::ConversationPayload>(
        exact(store, *restore.ref).payload)).retained_state == *base.ref, "durable restore target retained");
    auto missing = message("state-missing");
    missing.payload = am::ConversationEdited{
        am::EntryRef{conversation(), am::EntryId{std::uint64_t{99}}, am::EventRevision{std::uint64_t{1}}},
        {std::string{"rejected"}, {}}};
    const auto refusal = compare(missing);
    expect(refusal.status == am::ConversationWriteStatus::InvalidRequest && !refusal.ref,
        "durable missing state target refusal");
    missing.source = source("state-missing-retry");
    missing.payload = am::ConversationEdited{*base.ref, {std::string{"retry"}, {}}};
    const auto retry = compare(missing);
    expect(retry.ref && retry.ref->entry.value() == restore.ref->entry.value() + 1,
        "durable state refusal leaves allocator unchanged");
    {
        Fixture ambiguous_fixture("state_after_tombstone");
        cd::MdbxConversationDurable ambiguous_store(ambiguous_fixture.options());
        am::InMemoryConversationImportSink ambiguous_oracle(oracle_options());
        same_write(ambiguous_store.create_conversation(access(), conversation()),
            ambiguous_oracle.create_conversation(access(), conversation()));
        const auto compare_ambiguous = [&](const am::ConversationSourceObservation& input) {
            const auto actual = ambiguous_store.import_observation(access(), input);
            const auto expected = ambiguous_oracle.import_observation(access(), input);
            same_write(actual, expected);
            same_current(ambiguous_store.read_current(access(), conversation()),
                ambiguous_oracle.history().read_current(access(), conversation()));
            return actual;
        };
        const auto ambiguous_base = compare_ambiguous(message("state-ambiguous-base"));
        auto ambiguous_tombstone = message("state-ambiguous-tombstone");
        ambiguous_tombstone.payload = am::ConversationTombstoned{*ambiguous_base.ref};
        const auto ambiguous_deleted = compare_ambiguous(ambiguous_tombstone);
        auto edit_after_tombstone = message("state-edit-after-tombstone");
        edit_after_tombstone.payload = am::ConversationEdited{
            *ambiguous_deleted.ref, {std::string{"must-not-restore"}, {}}};
        const auto ambiguous_edit = compare_ambiguous(edit_after_tombstone);
        expect(ambiguous_edit.ref && ambiguous_store.read_current(access(), conversation()).messages.size() == 1 &&
            ambiguous_store.read_current(access(), conversation()).messages.front().status ==
                am::ConversationMessageState::Ambiguous,
            "edit after tombstone is retained but leaves Current ambiguous");
    }
    am::ConversationReadLimits tiny;
    tiny.max_visited_records = 1;
    const auto limited = store.read_current(access(), conversation(), tiny);
    expect(limited.status == am::ConversationReadStatus::BudgetExhausted && limited.messages.empty() &&
        limited.continuation && limited.visited_records == 1, "incomplete Current fold exposes no prefix");
    same_current(store.read_current(access(), conversation(), {}, limited.continuation),
        oracle.history().read_current(access(), conversation()));
}

void current_state_frontier_parity() {
    Fixture fixture("current_state");
    auto store = std::make_unique<cd::MdbxConversationDurable>(fixture.options());
    am::InMemoryConversationImportSink oracle(oracle_options());
    same_write(store->create_conversation(access(), conversation()), oracle.create_conversation(access(), conversation()));
    const auto compare = [&](const am::ConversationSourceObservation& input) {
        const auto actual = store->import_observation(access(), input);
        same_write(actual, oracle.import_observation(access(), input));
        same_current(store->read_current(access(), conversation()), oracle.history().read_current(access(), conversation()));
        return actual;
    };
    const auto root = compare(message("current-root"));
    compare(message("independent-root"));
    const auto current_call_input = call("current-tool-call");
    compare(current_call_input);
    compare(tool_result("current-tool-result", current_call_input));
    const auto before = store->read_current(access(), conversation());
    am::ConversationReadLimits one;
    one.max_items = 1;
    const auto page = store->read_current(access(), conversation(), one);
    expect(page.status == am::ConversationReadStatus::Partial && page.messages.size() == 1 && page.continuation,
        "Current output page pins frontier");
    auto corrected = exact(*store, *root.ref);
    corrected.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
    corrected.payload = am::ConversationPayload{am::ConversationMessageCreated{{std::string{"root-corrected"}, {}}}};
    same_write(store->correct(access(), corrected, root.ref->revision, "normalization"),
        oracle.correct(access(), corrected, root.ref->revision, "normalization"));
    same_current(store->read_current(access(), conversation()), oracle.history().read_current(access(), conversation()));
    auto edited = message("current-edit");
    edited.payload = am::ConversationEdited{*root.ref, {std::string{"linear"}, {}}};
    const auto edit = compare(edited);
    auto tombstoned = message("current-tombstone");
    tombstoned.payload = am::ConversationTombstoned{*edit.ref};
    const auto tombstone = compare(tombstoned);
    auto restored = message("current-restore");
    restored.payload = am::ConversationRestored{*tombstone.ref, *root.ref};
    const auto restore = compare(restored);
    auto tombstoned_again = message("current-tombstone-again");
    tombstoned_again.payload = am::ConversationTombstoned{*restore.ref};
    const auto deleted_again = compare(tombstoned_again);
    auto restored_again = message("current-restore-again");
    restored_again.payload = am::ConversationRestored{*deleted_again.ref, *restore.ref};
    compare(restored_again);
    auto current = store->read_current(access(), conversation());
    expect(current.messages.front().content_ref == root.ref && current.messages.front().content->text == "source text",
        "Restore-to-Restore preserves frozen original content");
    auto bad_restore = message("bad-restore");
    bad_restore.payload = am::ConversationRestored{*deleted_again.ref, *tombstone.ref};
    const auto immutable = snapshot(fixture);
    expect(compare(bad_restore).status == am::ConversationWriteStatus::InvalidRequest && snapshot(fixture) == immutable,
        "tombstone is not retained content and refusal is atomic");
    auto sibling = message("current-sibling");
    sibling.payload = am::ConversationEdited{*root.ref, {std::string{"sibling"}, {}}};
    compare(sibling);
    expect(store->read_current(access(), conversation()).messages.front().status == am::ConversationMessageState::Ambiguous,
        "sibling state does not choose a last writer");
    same_current(store->read_current_at(access(), conversation(), {}, before.commit_generation), before);
    const auto resumed = store->read_current(access(), conversation(), one, page.continuation);
    expect(resumed.status == am::ConversationReadStatus::Complete && resumed.commit_generation == page.commit_generation &&
        resumed.messages.size() == 1 && resumed.messages.front().origin == before.messages.back().origin,
        "Current continuation excludes later corrections and admissions");
    expect(store->read_history(access(), conversation(), {}, page.continuation).status == am::ConversationReadStatus::InvalidContinuation,
        "Current continuation cannot resume History");
    const auto history_page = store->read_history(access(), conversation(), one);
    expect(store->read_current(access(), conversation(), {}, history_page.continuation).status ==
        am::ConversationReadStatus::InvalidContinuation, "History continuation cannot resume Current");
    am::ConversationReadLimits no_output;
    no_output.max_output_bytes = 0;
    const auto oversized = store->read_current(access(), conversation(), no_output);
    expect(oversized.status == am::ConversationReadStatus::OversizedItem && oversized.messages.empty() && oversized.continuation,
        "Current oversized first root is not skipped");
    same_current(store->read_current(access(), conversation(), {}, oversized.continuation),
        oracle.history().read_current(access(), conversation()));
    store.reset(); fixture.reopen(); store = std::make_unique<cd::MdbxConversationDurable>(fixture.options(false));
    same_current(store->read_current(access(), conversation()), oracle.history().read_current(access(), conversation()));
    expect(store->read_current(access(), conversation(), {}, page.continuation).status ==
        am::ConversationReadStatus::InvalidContinuation, "Current continuation is instance-bound after reopen");
}

void historical_profile_refusal() {
    Fixture fixture("historical_profile");
    { cd::MdbxConversationDurable store(fixture.options()); }
    auto bytes = profile_bytes(fixture);
    const std::string marker = cd::kSchemaMarker;
    const auto found = std::search(bytes.begin(), bytes.end(), marker.begin(), marker.end());
    expect(found != bytes.end(), "profile marker fixture");
    const auto offset = static_cast<std::size_t>(found - bytes.begin());
    bytes[offset + marker.find(".v2") + 2] = '1';
    bytes[offset + marker.find("witness2") + 7] = '1';
    put_raw(fixture, kTables[0], cd::encode_profile_key(), bytes);
    const auto before = snapshot(fixture);
    for(bool initialize : {false, true}) {
        rejects_codec([&] { cd::MdbxConversationDurable rejected(fixture.options(initialize)); },
            cd::CodecCode::UnsupportedSchema, "historical profile must be explicitly unsupported");
        expect(snapshot(fixture) == before, "historical profile refusal is nonmutating");
    }
}

int child_mode(const std::string& mode, const std::filesystem::path& path) {
    Fixture fixture(path);
    if(mode == "--seed") {
        cd::MdbxConversationDurable store(fixture.options());
        expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied, "child create");
        admit(store, message("original"));
        const auto late = composite_call(); const auto early = admit(store, tool_result("early", late));
        expect(std::holds_alternative<am::UnresolvedConversationTarget>(
            std::get<am::ConversationToolResultObserved>(exact(store, *early.ref).payload).call), "child missing call unresolved");
        return 0;
    }
    expect(mode == "--reopen", "unknown child mode");
    cd::MdbxConversationDurable store(fixture.options(false));
    am::InMemoryConversationImportSink oracle(oracle_options());
    (void)oracle.create_conversation(access(), conversation());
    const auto original = oracle.import_observation(access(), message("original"));
    const auto late = composite_call(); const auto early_input = tool_result("early", late);
    const auto early = oracle.import_observation(access(), early_input);
    same_read(store.read_history(access(), conversation()), oracle.history().read_history(access(), conversation()));
    const auto original_replay = store.import_observation(access(), message("original"));
    expect(original_replay.status == am::ConversationWriteStatus::NoChange && original_replay.ref == original.ref &&
        original_replay.commit_generation == original.commit_generation, "fresh process original replay");
    const auto frozen = am::serialize_conversation_envelope(exact(store, *early.ref));
    const auto unresolved = std::get<am::UnresolvedConversationTarget>(
        std::get<am::ConversationToolResultObserved>(exact(store, *early.ref).payload).call);
    expect(unresolved.external == late.source.event && unresolved.evidence.artifact == late.source.evidence.artifact &&
        unresolved.evidence.record_locator == late.source.evidence.record_locator, "reopen retains exact composite target/evidence");
    const auto late_admission = admit(store, late);
    expect(late_admission.ref->entry.value() == 3 && late_admission.commit_generation == 4, "reopen allocator has no gap");
    const auto frozen_replay = store.import_observation(access(), early_input);
    expect(frozen_replay.status == am::ConversationWriteStatus::NoChange && frozen_replay.ref == early.ref &&
        frozen_replay.commit_generation == early.commit_generation &&
        am::serialize_conversation_envelope(exact(store, *early.ref)) == frozen,
        "late call never rewrites original frozen unresolved replay");
    const auto resolved = admit(store, tool_result("resolved", late));
    expect(std::get<am::EntryRef>(std::get<am::ConversationToolResultObserved>(exact(store, *resolved.ref).payload).call) ==
        *late_admission.ref, "separate occurrence resolves newly present call");
    auto changed = message("original"); changed.producer = "changed";
    expect(store.import_observation(access(), changed).status == am::ConversationWriteStatus::Conflict,
        "fresh process changed witness conflicts");
    return 0;
}
void fresh_process_reopen(const std::filesystem::path& executable) {
    Fixture fixture("process"); fixture.context.reset();
    const auto run = [&](const char* mode) {
        std::string command = "\"" + executable.string() + "\" " + mode + " \"" + fixture.path.string() + "\"";
#ifdef _WIN32
        command = "\"" + command + "\"";
#endif
        expect(std::system(command.c_str()) == 0, "independent fresh-process qualification failed");
    };
    run("--seed"); run("--reopen");
    fixture.open(); cd::MdbxConversationDurable store(fixture.options(false));
    const auto history = store.read_history(access(), conversation());
    expect(history.status == am::ConversationReadStatus::Complete && history.records.size() == 4 &&
        history.commit_generation == 5, "parent independently reopens complete child admissions");
}
} // namespace

int main(int argc, char** argv) {
    try {
        if(argc == 3) return child_mode(argv[1], std::filesystem::path(argv[2]));
        expect(argc == 1, "usage: test [--seed|--reopen environment]");
        const auto run = [](const char* name, void (*fixture)()) {
            try { fixture(); }
            catch(const std::exception& error) { throw std::runtime_error(std::string(name) + ": " + error.what()); }
        };
        run("authored_source_parity", authored_source_parity);
        run("state_payload_admission", state_payload_admission);
        run("current_state_frontier_parity", current_state_frontier_parity);
        run("historical_profile_refusal", historical_profile_refusal);
        run("quotas_and_owning_outputs", quotas_and_owning_outputs);
        run("zero_quotas", zero_quotas_forbid_new_consumption);
        run("collision_bucket_admission", collision_bucket_admission);
        run("rollback_each_write_boundary", rollback_each_write_boundary);
        run("rejected_open_is_nonmutating", rejected_open_is_nonmutating);
        run("history_paging_and_frontier", history_paging_and_frontier);
        run("persisted_corruption", persisted_corruption_is_not_a_local_budget);
        run("local_budgets_and_capacity", local_budgets_and_complete_environment_capacity);
        run("schema_initialization_rollback", schema_initialization_rolls_back_atomically);
        run("replay_memory", replay_memory_is_not_writer_preparation);
        fresh_process_reopen(std::filesystem::absolute(argv[0]));
        std::cout << "internal durable Conversation qualification passed (bounded Current fixtures; no power-loss claim)\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "durable Conversation qualification: " << error.what() << '\n';
        return 1;
    }
}
