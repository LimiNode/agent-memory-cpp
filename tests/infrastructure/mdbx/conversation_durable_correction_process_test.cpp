#include <agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.hpp>
#include <mdbx_containers/common.hpp>

#include "conversation_process_harness.hpp"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <memory>
#include <sstream>
#include <tuple>
#include <vector>

namespace am = agent_memory;
namespace cd = agent_memory::detail::conversation_durable;
namespace ipc = conversation_process_test;
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
std::string incarnation_text(const cd::Incarnation& incarnation) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(2 * incarnation.size());
    for(const auto byte : incarnation) { result += digits[byte >> 4]; result += digits[byte & 15]; }
    return result;
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
am::ConversationRef conversation() { return {"correction-process", am::ConversationId{std::uint64_t{1}}}; }
am::ConversationStoreAccess access() { return {am::ScopeId{"correction-process-scope"}, "host-policy-v1"}; }
am::EntryRef ref(std::uint64_t revision = 1) {
    return {conversation(), am::EntryId{std::uint64_t{1}}, am::EventRevision{revision}};
}
am::ConversationSourceObservation observation(std::uint64_t id = 1) {
    am::ConversationSourceObservation input;
    input.conversation = conversation(); input.scope = access().scope;
    input.source.identity = am::ConversationSourceIdentity::ExternalEvent;
    input.source.event = am::ExternalObjectRef{"fixture", "correction-process", "run", "event", std::nullopt,
        am::ExternalObjectKey{"event-" + std::to_string(id)}};
    input.source.revision = am::SourceRevisionId{"immutable-source@1"};
    input.source.adapter_recipe = "normalizer.v1";
    input.source.evidence = {am::ArtifactBindingId{"immutable-artifact@1"}, "records/" + std::to_string(id)};
    input.producer = "fixture-producer"; input.llm_role = am::ConversationLlmRole::User;
    input.payload = am::ConversationSourceMessage{am::ConversationSourceMessageKind::Created,
        {std::string("original process evidence"), {}}};
    return input;
}
am::ConversationEnvelope correction(const am::ConversationEnvelope& original, const std::string& variant) {
    auto result = am::copy_conversation_envelope(original);
    result.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
    result.metadata.source->adapter_recipe = "normalizer.v2";
    std::get<am::ConversationMessageCreated>(std::get<am::ConversationPayload>(result.payload)).content.text =
        variant + " corrected representation";
    return result;
}
std::shared_ptr<am::MdbxStorageContext> open_context(const std::filesystem::path& path) {
    mdbxc::Config config;
    config.pathname = path.string(); config.no_subdir = true; config.relative_to_exe = false;
    config.max_dbs = 96; config.size_upper = 64U * 1024U * 1024U; config.growth_step = 1024U * 1024U;
    config.sync_durable = true; config.writemap_mode = false;
    return am::MdbxStorageContext::attach(mdbxc::Connection::create(config));
}
cd::Quotas quotas() { return {2, 4, 8, 16U * 1024U * 1024U, 4, 16U * 1024U * 1024U}; }
cd::DurableOptions options(const std::shared_ptr<am::MdbxStorageContext>& context, bool initialize = false) {
    MDBX_envinfo info{};
    checked(mdbx_env_info_ex(context->connection()->env_handle(), nullptr, &info, sizeof(info)));
    cd::DurableOptions result(context, conversation().allocation_namespace, quotas(),
        {info.mi_geo.upper, info.mi_geo.grow});
    result.allow_initialize = initialize; return result;
}
struct Environment final {
    std::filesystem::path path;
    std::string incarnation;
    Environment() {
        static std::uint64_t serial = 0;
        path = std::filesystem::temp_directory_path() / ("agent_memory_correction_process_" +
            std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "_" +
            std::to_string(++serial) + ".mdbx");
        const auto context = open_context(path);
        cd::MdbxConversationDurable store(options(context, true));
        expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied,
            "Initial conversation failed");
        expect(store.import_observation(access(), observation()).ref == ref(), "Initial import failed");
        const auto rows = snapshot(context);
        const auto profile_key = cd::encode_profile_key();
        const auto marker = std::find_if(rows.begin(), rows.end(), [&profile_key](const RawRow& row) {
            return row.table == 0 && row.key == profile_key;
        });
        expect(marker != rows.end(), "Initial profile missing");
        incarnation = incarnation_text(cd::decode_profile_value(view(marker->value)).incarnation);
    }
    ~Environment() {
        std::error_code error;
        std::filesystem::remove(path, error);
        std::filesystem::remove(path.string() + "-lck", error);
    }
};
void same_write(const am::ConversationWriteResult& actual, const am::ConversationWriteResult& expected) {
    expect(actual.status == expected.status && actual.ref == expected.ref &&
        actual.commit_generation == expected.commit_generation, "Reference write receipt mismatch");
}
void same_read(const am::ConversationReadResult& actual, const am::ConversationReadResult& expected) {
    expect(actual.status == expected.status && actual.commit_generation == expected.commit_generation &&
        actual.records.size() == expected.records.size(), "Reference read count/frontier mismatch");
    for(std::size_t index = 0; index < actual.records.size(); ++index) {
        const auto& a = actual.records[index]; const auto& b = expected.records[index];
        expect(am::serialize_conversation_envelope(a.envelope) == am::serialize_conversation_envelope(b.envelope) &&
            a.commit_generation == b.commit_generation && a.correction.has_value() == b.correction.has_value(),
            "Reference immutable bytes/generation/receipt mismatch");
        if(a.correction) expect(a.correction->previous_revision == b.correction->previous_revision &&
            a.correction->reason_code == b.correction->reason_code, "Reference correction receipt mismatch");
    }
}
// Independent logical counter calculation, not the production preflight/key helper.
std::uint64_t mapping_charge(const am::ConversationSourceObservation& input) {
    const auto external = am::serialize_external_object_ref_bytes(*input.source.event).size();
    const auto value = [](std::size_t size) { return std::uint64_t{4} + size; };
    const auto& text = *std::get<am::ConversationSourceMessage>(input.payload).content.text;
    const auto observed = 128 + value(input.conversation.allocation_namespace.size()) + value(input.scope.value().size()) +
        32 + value(external) + value(input.source.revision.value().size()) + value(input.source.adapter_recipe.size()) +
        value(input.source.evidence.artifact.value().size()) + value(input.source.evidence.record_locator.size()) +
        value(input.producer.size()) + value(text.size());
    return observed + 32 + external + input.source.revision.value().size() + 32 +
        input.conversation.allocation_namespace.size();
}
void validate(const std::shared_ptr<am::MdbxStorageContext>& context, bool corrected, const std::string& variant,
    const std::string& incarnation) {
    cd::MdbxConversationDurable store(options(context));
    am::ConversationImportOptions reference_options;
    reference_options.store.allocation_namespace = conversation().allocation_namespace;
    am::InMemoryConversationImportSink oracle(reference_options);
    expect(oracle.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied,
        "Oracle create failed");
    const auto original_receipt = oracle.import_observation(access(), observation());
    expect(original_receipt.ref == ref(), "Oracle import failed");
    const auto original = oracle.history().read_exact(access(), ref());
    auto candidate = correction(original.records.at(0).envelope, variant);
    if(corrected) expect(oracle.correct(access(), candidate, ref().revision, kReason).ref == ref(2),
        "Oracle correction failed");
    const auto state = snapshot(context);
    const auto profile_key = cd::encode_profile_key();
    const auto marker = std::find_if(state.begin(), state.end(), [&profile_key](const RawRow& row) {
        return row.table == 0 && row.key == profile_key;
    });
    expect(marker != state.end(), "Missing profile marker");
    const auto actual_profile = cd::decode_profile_value(view(marker->value));
    expect(actual_profile.allocation_namespace == conversation().allocation_namespace &&
        incarnation_text(actual_profile.incarnation) == incarnation &&
        actual_profile.quota_policy_version == 1 && std::any_of(actual_profile.incarnation.begin(),
            actual_profile.incarnation.end(), [](std::uint8_t byte) { return byte != 0; }) &&
        cd::encode_descriptor(actual_profile.descriptor) == cd::encode_descriptor(options(context).descriptor),
        "Invalid marker/incarnation/descriptor");
    const auto q = quotas();
    expect(actual_profile.quotas.conversations == q.conversations && actual_profile.quotas.entries == q.entries &&
        actual_profile.quotas.revisions_per_entry == q.revisions_per_entry &&
        actual_profile.quotas.canonical_bytes == q.canonical_bytes && actual_profile.quotas.mapping_rows == q.mapping_rows &&
        actual_profile.quotas.mapping_bytes == q.mapping_bytes, "Persisted quota changed");
    auto expected_profile = actual_profile;
    const auto frame = am::serialize_conversation_envelope(original.records.at(0).envelope);
    const auto corrected_frame = am::serialize_conversation_envelope(candidate);
    expected_profile.generation = corrected ? 3 : 2;
    expected_profile.conversation_count = expected_profile.entry_count = expected_profile.mapping_count = 1;
    expected_profile.revision_count = corrected ? 2 : 1;
    expected_profile.canonical_charge = access().scope.value().size() + frame.size() +
        (corrected ? corrected_frame.size() + kReason.size() : 0);
    expected_profile.mapping_charge = mapping_charge(observation());
    Snapshot expected;
    const auto add = [&expected, &expected_profile](std::size_t table, am::ByteBuffer key, am::ByteBuffer body) {
        const auto kind = table == 0 ? cd::TableKind::Metadata :
            (table == 1 ? cd::TableKind::Events : cd::TableKind::Sources);
        auto value = cd::wrap_value(expected_profile, kind, view(key), view(body));
        expected.push_back({table, std::move(key), std::move(value)});
    };
    add(0, profile_key, cd::encode_profile(expected_profile));
    add(0, cd::encode_conversation_key(conversation().id), cd::encode_conversation_row({access().scope, 1, 2}));
    add(1, cd::encode_generation_key(1), cd::encode_generation_row({cd::GenerationKind::Create,
        conversation(), std::nullopt}));
    add(1, cd::encode_revision_key({conversation().id, ref().entry, ref().revision}),
        cd::encode_revision_row({2, std::nullopt, frame}));
    add(1, cd::encode_generation_key(2), cd::encode_generation_row({cd::GenerationKind::Import,
        conversation(), std::make_pair(ref().entry, ref().revision)}));
    const auto input = observation();
    const auto key = cd::encode_logical_source_key(input.source);
    add(2, cd::encode_source_key({conversation().id, cd::source_digest(view(key)), 0}),
        cd::encode_source_row({key, cd::encode_witness(input), ref(), 2}));
    if(corrected) {
        add(1, cd::encode_revision_key({conversation().id, ref().entry, ref(2).revision}),
            cd::encode_revision_row({3, cd::CorrectionReceipt{ref().revision, kReason}, corrected_frame}));
        add(1, cd::encode_generation_key(3), cd::encode_generation_row({cd::GenerationKind::Correction,
            conversation(), std::make_pair(ref().entry, ref(2).revision)}));
    }
    order(expected);
    expect(state == expected, "Fresh verifier found partial/orphan rows or incorrect counters/witness/allocator");
    same_read(store.read_history(access(), conversation()), oracle.history().read_history(access(), conversation()));
    same_read(store.read_exact(access(), ref()), oracle.history().read_exact(access(), ref()));
    if(corrected) same_read(store.read_exact(access(), ref(2)), oracle.history().read_exact(access(), ref(2)));
    const auto stable = snapshot(context);
    const auto replay = store.import_observation(access(), input);
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == original_receipt.ref &&
        replay.commit_generation == original_receipt.commit_generation, "Correction rewrote original admission receipt");
    auto changed = observation(); changed.producer = "changed-witness";
    const auto refused = store.import_observation(access(), changed);
    expect(refused.status == am::ConversationWriteStatus::Conflict && !refused.ref && !refused.commit_generation,
        "Changed witness did not conflict");
    expect(snapshot(context) == stable, "Replay/refusal changed persistent state");
    same_write(store.correct(access(), candidate, ref().revision, kReason),
        oracle.correct(access(), candidate, ref().revision, kReason));
    if(!corrected) same_read(store.read_exact(access(), ref(2)), oracle.history().read_exact(access(), ref(2)));
    const auto after_correction = snapshot(context);
    auto no_change = am::copy_conversation_envelope(candidate);
    no_change.metadata.ref.revision = am::EventRevision{std::uint64_t{3}};
    same_write(store.correct(access(), no_change, ref(2).revision, kReason),
        oracle.correct(access(), no_change, ref(2).revision, kReason));
    expect(snapshot(context) == after_correction, "NoChange correction mutated state");
    const auto next = store.import_observation(access(), observation(2));
    expect(next.status == am::ConversationWriteStatus::Applied && next.ref && next.ref->entry.value() == 2 &&
        next.commit_generation == 4, "Correction consumed ID/generation or reused a committed identity");
    same_write(next, oracle.import_observation(access(), observation(2)));
    same_read(store.read_history(access(), conversation()), oracle.history().read_history(access(), conversation()));
}
std::string g_boundary;
void barrier() { ipc::send_event("BARRIER"); expect(ipc::receive_command() == "GO", "Expected GO"); }
const char* step_name(cd::WriteStep step) {
    switch(step) {
    case cd::WriteStep::BeforeTransaction: return "before_transaction";
    case cd::WriteStep::BeforeWrites: return "before_writes";
    case cd::WriteStep::AfterRevision: return "after_revision";
    case cd::WriteStep::AfterGeneration: return "after_generation";
    case cd::WriteStep::AfterProfile: return "after_profile";
    case cd::WriteStep::BeforeCommit: return "before_commit";
    default: return "unused";
    }
}
void stop_at_step(cd::WriteStep step) { if(g_boundary == step_name(step)) barrier(); }
void stop_after_commit() noexcept {
    if(g_boundary != "after_wrapper_commit") return;
    try { barrier(); } catch(...) { std::_Exit(3); }
}
void prove_contention(const std::shared_ptr<am::MdbxStorageContext>& context) {
    MDBX_txn* txn = nullptr;
    const auto code = mdbx_txn_begin(context->connection()->env_handle(), nullptr, MDBX_TXN_TRY, &txn);
    if(txn) static_cast<void>(mdbx_txn_abort(txn));
    expect(code == MDBX_BUSY && !txn, "Competing correction did not encounter actual MDBX writer lock");
    ipc::send_event("CONTENDED");
}
void send_receipt(const am::ConversationWriteResult& result) {
    ipc::send_event("RESULT " + std::to_string(static_cast<int>(result.status)) + " " +
        std::to_string(result.ref ? result.ref->entry.value() : 0) + " " +
        std::to_string(result.ref ? result.ref->revision.value() : 0) + " " + std::to_string(result.commit_generation));
    expect(ipc::receive_command() == "ACK", "Expected receipt ACK");
}
struct Receipt final { am::ConversationWriteStatus status; std::uint64_t entry = 0, revision = 0, generation = 0; };
Receipt receive_receipt(ipc::ChildProcess& child) {
    std::istringstream input(child.receive());
    std::string kind; int status = 0; Receipt result{};
    expect(static_cast<bool>(input >> kind >> status >> result.entry >> result.revision >> result.generation) &&
        kind == "RESULT", "Malformed correction receipt");
    input >> std::ws; expect(input.eof(), "Trailing receipt data");
    result.status = static_cast<am::ConversationWriteStatus>(status); return result;
}
int writer_main(int argc, char** argv) {
    expect(argc == 5, "Invalid writer arguments");
    const auto context = open_context(std::filesystem::u8path(argv[2]));
    cd::QualificationHooks hooks; hooks.before_commit_step = stop_at_step; hooks.after_commit = stop_after_commit;
    cd::MdbxConversationDurable store(options(context), hooks);
    const auto original = store.read_exact(access(), ref());
    auto candidate = correction(original.records.at(0).envelope, argv[4]);
    g_boundary = argv[3];
    if(g_boundary == "contender") { barrier(); prove_contention(context); }
    const auto receipt = store.correct(access(), candidate, ref().revision, kReason);
    if(g_boundary == "after_operation_return") barrier();
    send_receipt(receipt); return 0;
}
void verify_fresh(const std::string& executable, const Environment& environment, bool corrected,
    const std::string& variant) {
    ipc::ChildProcess verifier(executable, {"--verify", environment.path.u8string(), corrected ? "1" : "0",
        variant, environment.incarnation});
    expect(verifier.receive() == "VERIFIED", "Fresh correction verification failed"); verifier.wait_success();
}
void crash_matrix(const std::string& executable) {
    std::size_t cases = 0;
    for(const auto* boundary : {"before_transaction", "before_writes", "after_revision", "after_generation",
        "after_profile", "before_commit", "after_wrapper_commit", "after_operation_return"}) {
        Environment environment;
        ipc::ChildProcess writer(executable, {"--writer", environment.path.u8string(), boundary, "left"});
        expect(writer.receive() == "BARRIER", "Correction writer did not acknowledge exact crash boundary");
        writer.terminate_and_wait();
        const bool corrected = std::string(boundary) == "after_wrapper_commit" ||
            std::string(boundary) == "after_operation_return";
        verify_fresh(executable, environment, corrected, "left"); ++cases;
    }
    std::cout << "Acknowledged correction crash cases: " << cases << '\n';
}
void correction_race(const std::string& executable, bool identical, bool reversed) {
    Environment environment;
    const std::string first = reversed ? "right" : "left";
    const std::string second = identical ? first : (reversed ? "left" : "right");
    ipc::ChildProcess left(executable, {"--writer", environment.path.u8string(), "before_commit", first});
    ipc::ChildProcess right(executable, {"--writer", environment.path.u8string(), "contender", second});
    expect(left.receive() == "BARRIER" && right.receive() == "BARRIER", "Correction race did not independently gate");
    right.send("GO");
    expect(right.receive() == "CONTENDED", "Correction race lacks actual writer-lock contention");
    left.send("GO");
    const auto a = receive_receipt(left), b = receive_receipt(right);
    expect(a.status == am::ConversationWriteStatus::Applied && a.entry == 1 && a.revision == 2 && a.generation == 3,
        "First contended correction did not commit exact revision/generation");
    expect(b.status == am::ConversationWriteStatus::Conflict && !b.entry && !b.revision && !b.generation,
        "Competing stale CAS did not precede NoChange or leaked a receipt");
    left.send("ACK"); right.send("ACK"); left.wait_success(); right.wait_success();
    verify_fresh(executable, environment, true, first);
    std::cout << "Contended correction CAS race: " << (identical ? "equal" : "conflicting") <<
        (reversed ? " (reversed operands)" : "") << '\n';
}
} // namespace
int main(int argc, char** argv) {
    try {
        if(argc > 1) {
            const std::string role = argv[1];
            if(role == "--writer") return writer_main(argc, argv);
            expect(role == "--verify" && argc == 6 && (std::string(argv[3]) == "0" || std::string(argv[3]) == "1"),
                "Invalid verifier arguments");
            const auto context = open_context(std::filesystem::u8path(argv[2]));
            validate(context, std::string(argv[3]) == "1", argv[4], argv[5]);
            ipc::send_event("VERIFIED"); return 0;
        }
        const auto executable = std::filesystem::absolute(std::filesystem::u8path(argv[0])).u8string();
        crash_matrix(executable);
        for(bool identical : {true, false}) for(bool reversed : {false, true})
            correction_race(executable, identical, reversed);
        std::cout << "Post-commit means after wrapper return, not native commit internals.\n"
            "NOT RUN real device I/O/native commit failure or storage-device power loss.\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "correction process test: " << error.what() << '\n'; return 1;
    }
}
