#include <agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.hpp>
#include <mdbx_containers/common.hpp>

#include "conversation_process_harness.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace am = agent_memory;
namespace cd = agent_memory::detail::conversation_durable;
namespace ipc = conversation_process_test;

namespace {
constexpr std::uint64_t kMapUpper = 64U * 1024U * 1024U;
constexpr std::uint64_t kMapGrowth = 1024U * 1024U;
constexpr const char* kTables[] = {"agent_memory_conversation_meta_v1",
    "agent_memory_conversation_events_v1", "agent_memory_conversation_sources_v1"};

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
void checked(int code) {
    if(code != MDBX_SUCCESS) throw std::runtime_error(mdbx_strerror(code));
}
am::ByteView view(const am::ByteBuffer& bytes) { return {bytes.data(), bytes.size()}; }
am::ByteBuffer copy_bytes(const MDBX_val& value) {
    const auto* begin = static_cast<const std::uint8_t*>(value.iov_base);
    return value.iov_len ? am::ByteBuffer(begin, begin + value.iov_len) : am::ByteBuffer{};
}
am::ConversationStoreAccess access() { return {am::ScopeId{"process-scope"}, "host-policy-v1"}; }
am::ConversationRef conversation(std::uint64_t id) { return {"process", am::ConversationId{id}}; }

struct Operation final {
    bool is_create = false;
    std::uint64_t conversation_id = 1;
    unsigned source_id = 0;
    bool is_changed = false;
};
Operation create(std::uint64_t id) { return {true, id, 0, false}; }
Operation import(unsigned id, std::uint64_t stream = 1, bool changed = false) {
    return {false, stream, id, changed};
}
std::string encode_operation(const Operation& operation) {
    return std::string(operation.is_create ? "c:" : "i:") + std::to_string(operation.conversation_id) + ":" +
        std::to_string(operation.source_id) + ":" + (operation.is_changed ? "1" : "0");
}
Operation decode_operation(std::string text) {
    std::replace(text.begin(), text.end(), ':', ' ');
    std::istringstream input(text);
    char kind = 0;
    Operation operation;
    unsigned changed = 0;
    expect(static_cast<bool>(input >> kind >> operation.conversation_id >> operation.source_id >> changed),
        "Malformed operation argument");
    expect((kind == 'c' || kind == 'i') && changed <= 1 && operation.conversation_id != 0,
        "Invalid operation argument");
    input >> std::ws;
    expect(input.eof(), "Trailing operation argument");
    operation.is_create = kind == 'c';
    operation.is_changed = changed != 0;
    return operation;
}
using Plan = std::vector<Operation>;
std::string encode_plan(const Plan& plan) {
    std::string result;
    for(const auto& operation : plan) {
        if(!result.empty()) result += ',';
        result += encode_operation(operation);
    }
    return result.empty() ? "-" : result;
}
Plan decode_plan(const std::string& text) {
    Plan result;
    if(text == "-") return result;
    std::istringstream input(text);
    std::string item;
    while(std::getline(input, item, ',')) result.push_back(decode_operation(item));
    return result;
}

am::ConversationSourceObservation observation(const Operation& operation) {
    const auto key = "event-00" + std::to_string(operation.source_id);
    am::ConversationSourceObservation result;
    result.conversation = conversation(operation.conversation_id);
    result.scope = access().scope;
    result.source.identity = am::ConversationSourceIdentity::ExternalEvent;
    result.source.event = am::ExternalObjectRef{"fixture", "process-source", "run", "event", std::nullopt,
        am::ExternalObjectKey{key}};
    result.source.revision = am::SourceRevisionId{"retained@1"};
    result.source.adapter_recipe = "process-fixture-v1";
    result.source.evidence = {am::ArtifactBindingId{"immutable-artifact@1"}, "records/" + key};
    result.producer = operation.is_changed ? "changed-producer" : "fixture-producer";
    result.llm_role = am::ConversationLlmRole::User;
    result.payload = am::ConversationSourceMessage{am::ConversationSourceMessageKind::Created,
        {std::string("authored process evidence"), {}}};
    return result;
}
am::ConversationWriteResult apply(cd::MdbxConversationDurable& store, const Operation& operation) {
    return operation.is_create ? store.create_conversation(access(), conversation(operation.conversation_id)) :
        store.import_observation(access(), observation(operation));
}
am::ConversationWriteResult apply(am::InMemoryConversationImportSink& store, const Operation& operation) {
    return operation.is_create ? store.create_conversation(access(), conversation(operation.conversation_id)) :
        store.import_observation(access(), observation(operation));
}
void same_write(const am::ConversationWriteResult& actual, const am::ConversationWriteResult& expected) {
    expect(actual.status == expected.status && actual.ref == expected.ref &&
        actual.commit_generation == expected.commit_generation, "Oracle status/ref/generation mismatch");
}
void same_read(const am::ConversationReadResult& actual, const am::ConversationReadResult& expected) {
    expect(actual.status == expected.status && actual.commit_generation == expected.commit_generation &&
        actual.records.size() == expected.records.size(), "Oracle read status/frontier/count mismatch");
    for(std::size_t index = 0; index < actual.records.size(); ++index) {
        expect(actual.records[index].commit_generation == expected.records[index].commit_generation &&
            am::serialize_conversation_envelope(actual.records[index].envelope) ==
                am::serialize_conversation_envelope(expected.records[index].envelope),
            "Oracle canonical frame or admission generation mismatch");
    }
}

bool uses_collision(const std::string& profile) {
    return profile == "collision" || profile == "rows" || profile == "bytes";
}
cd::Quotas quotas(const std::string& profile) {
    cd::Quotas result{16, 64, 8, 4U * 1024U * 1024U, 64, 4U * 1024U * 1024U};
    if(profile == "quota") result.entries = result.mapping_rows = 2;
    return result;
}
std::shared_ptr<am::MdbxStorageContext> open_context(const std::filesystem::path& path) {
    mdbxc::Config config;
    config.pathname = path.string();
    config.no_subdir = true;
    config.relative_to_exe = false;
    config.max_dbs = 96;
    config.size_upper = kMapUpper;
    config.growth_step = kMapGrowth;
    config.sync_durable = true;
    config.writemap_mode = false;
    return am::MdbxStorageContext::attach(mdbxc::Connection::create(config));
}
cd::DurableOptions options(const std::shared_ptr<am::MdbxStorageContext>& context,
    const std::string& profile, bool initialize) {
    MDBX_envinfo info{};
    checked(mdbx_env_info_ex(context->connection()->env_handle(), nullptr, &info, sizeof(info)));
    cd::DurableOptions result(context, "process", quotas(profile), {info.mi_geo.upper, info.mi_geo.grow});
    result.allow_initialize = initialize;
    if(profile == "rows") result.descriptor.bucket_rows = 2;
    if(profile == "bytes") {
        const auto input = observation(import(1));
        const auto body = cd::encode_source_row({cd::encode_logical_source_key(input.source),
            cd::encode_witness(input), {conversation(1), am::EntryId{std::uint64_t{1}},
                am::EventRevision{std::uint64_t{1}}}, 2});
        // All three authored inputs have equal encoded lengths. Exactly two
        // complete rows fit; either contender alone fits beside the seed.
        result.descriptor.bucket_bytes = 2 * (body.size() + cd::kValueOverheadBytes);
    }
    return result;
}
cd::QualificationHooks hooks(const std::string& profile) {
    cd::QualificationHooks result;
    result.force_digest_collision = uses_collision(profile);
    return result;
}

struct RawRow final {
    std::size_t table = 0;
    am::ByteBuffer key, value;
    bool operator==(const RawRow& other) const {
        return table == other.table && key == other.key && value == other.value;
    }
};
struct Snapshot final {
    std::array<bool, 3> tables{};
    std::vector<RawRow> rows;
    bool operator==(const Snapshot& other) const { return tables == other.tables && rows == other.rows; }
};
Snapshot snapshot(MDBX_txn* transaction) {
    Snapshot result;
    for(std::size_t table = 0; table < result.tables.size(); ++table) {
        MDBX_dbi dbi{};
        const auto opened = mdbx_dbi_open(transaction, kTables[table], MDBX_DB_DEFAULTS, &dbi);
        if(opened == MDBX_NOTFOUND) continue;
        checked(opened);
        result.tables[table] = true;
        unsigned flags = 0;
        checked(mdbx_dbi_flags(transaction, dbi, &flags));
        expect(flags == 0, "Unexpected DBI comparator/flags");
        MDBX_cursor* cursor = nullptr;
        checked(mdbx_cursor_open(transaction, dbi, &cursor));
        MDBX_val key{}, value{};
        auto code = mdbx_cursor_get(cursor, &key, &value, MDBX_FIRST);
        try {
            while(code == MDBX_SUCCESS) {
                result.rows.push_back({table, copy_bytes(key), copy_bytes(value)});
                code = mdbx_cursor_get(cursor, &key, &value, MDBX_NEXT);
            }
        } catch(...) { mdbx_cursor_close(cursor); throw; }
        mdbx_cursor_close(cursor);
        expect(code == MDBX_NOTFOUND, "Raw snapshot cursor failure");
    }
    return result;
}
Snapshot snapshot(const std::shared_ptr<am::MdbxStorageContext>& context) {
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    auto result = snapshot(transaction.handle());
    transaction.commit();
    return result;
}
const RawRow& find_row(const Snapshot& state, std::size_t table, const am::ByteBuffer& key) {
    const auto found = std::find_if(state.rows.begin(), state.rows.end(), [table, &key](const RawRow& row) {
        return row.table == table && row.key == key;
    });
    expect(found != state.rows.end(), "Missing required physical row");
    return *found;
}

// Independent fixture charge calculation: no production preflight/source_key
// helper is used to determine the expected persisted counters.
std::uint64_t mapping_charge(const am::ConversationSourceObservation& input) {
    const auto external_bytes = am::serialize_external_object_ref_bytes(*input.source.event).size();
    const auto value = [](std::size_t size) { return std::uint64_t{4} + size; };
    const auto& text = *std::get<am::ConversationSourceMessage>(input.payload).content.text;
    const auto observation_bytes = 128 + value(input.conversation.allocation_namespace.size()) +
        value(input.scope.value().size()) + 32 + value(external_bytes) + value(input.source.revision.value().size()) +
        value(input.source.adapter_recipe.size()) + value(input.source.evidence.artifact.value().size()) +
        value(input.source.evidence.record_locator.size()) + value(input.producer.size()) + value(text.size());
    const auto logical_key_charge = 32 + external_bytes + input.source.revision.value().size();
    return observation_bytes + logical_key_charge + 32 + input.conversation.allocation_namespace.size();
}

void validate_state(const std::shared_ptr<am::MdbxStorageContext>& context, const std::string& profile,
    const Plan& plan) {
    cd::MdbxConversationDurable store(options(context, profile, false), hooks(profile));
    const auto state = snapshot(context);
    expect(std::all_of(state.tables.begin(), state.tables.end(), [](bool present) { return present; }),
        "Schema DBIs did not commit together");
    const auto profile_key = cd::encode_profile_key();
    const auto& marker = find_row(state, 0, profile_key);
    const auto persisted = cd::decode_profile_value(view(marker.value));
    const auto configured = options(context, profile, false);
    expect(persisted.allocation_namespace == "process" && persisted.quota_policy_version == 1 &&
        cd::encode_descriptor(persisted.descriptor) == cd::encode_descriptor(configured.descriptor),
        "Marker/descriptor mismatch");
    const auto expected_quotas = quotas(profile);
    expect(persisted.quotas.conversations == expected_quotas.conversations &&
        persisted.quotas.entries == expected_quotas.entries &&
        persisted.quotas.revisions_per_entry == expected_quotas.revisions_per_entry &&
        persisted.quotas.canonical_bytes == expected_quotas.canonical_bytes &&
        persisted.quotas.mapping_rows == expected_quotas.mapping_rows &&
        persisted.quotas.mapping_bytes == expected_quotas.mapping_bytes, "Persistent quota mismatch");
    expect(std::any_of(persisted.incarnation.begin(), persisted.incarnation.end(),
        [](std::uint8_t byte) { return byte != 0; }), "Missing profile incarnation");

    am::ConversationImportOptions reference_options;
    reference_options.store.allocation_namespace = "process";
    am::InMemoryConversationImportSink oracle(reference_options);
    std::map<std::uint64_t, std::uint64_t> next_entry;
    std::map<am::ByteBuffer, std::uint32_t> next_slot;
    std::uint64_t canonical = 0, mapping = 0, entries = 0;
    for(std::size_t index = 0; index < plan.size(); ++index) {
        const auto& operation = plan[index];
        const auto generation = static_cast<std::uint64_t>(index + 1);
        const auto receipt = apply(oracle, operation);
        expect(receipt.status == am::ConversationWriteStatus::Applied && receipt.commit_generation == generation,
            "Invalid committed-order reference plan");
        const auto generation_key = cd::encode_generation_key(generation);
        const auto& physical_generation = find_row(state, 1, generation_key);
        const auto decoded_generation = cd::decode_generation_row(cd::unwrap_value(persisted, cd::TableKind::Events,
            view(generation_key), view(physical_generation.value)));
        expect(decoded_generation.conversation == conversation(operation.conversation_id) &&
            decoded_generation.kind == (operation.is_create ? cd::GenerationKind::Create : cd::GenerationKind::Import),
            "Generation log has wrong kind/conversation");
        if(operation.is_create) {
            expect(next_entry.emplace(operation.conversation_id, 1).second && !decoded_generation.entry,
                "Duplicate create or unexpected generation entry");
            canonical += access().scope.value().size();
            const auto key = cd::encode_conversation_key(am::ConversationId{operation.conversation_id});
            const auto& physical = find_row(state, 0, key);
            const auto row = cd::decode_conversation_row(cd::unwrap_value(persisted, cd::TableKind::Metadata,
                view(key), view(physical.value)));
            expect(row.scope.value() == access().scope.value() && row.created_generation == generation,
                "Conversation scope or create generation mismatch");
            continue;
        }
        ++entries;
        expect(receipt.ref && receipt.ref->entry.value() == next_entry.at(operation.conversation_id)++ &&
            receipt.ref->revision.value() == 1 && decoded_generation.entry &&
            decoded_generation.entry->first == receipt.ref->entry && decoded_generation.entry->second == receipt.ref->revision,
            "Committed ID gap, reuse, or generation binding mismatch");
        auto expected = oracle.history().read_exact(access(), *receipt.ref);
        expected.commit_generation = plan.size();
        same_read(store.read_exact(access(), *receipt.ref), expected);
        const auto frame = am::serialize_conversation_envelope(expected.records.at(0).envelope);
        canonical += frame.size();
        const auto revision_key = cd::encode_revision_key({receipt.ref->conversation.id,
            receipt.ref->entry, receipt.ref->revision});
        const auto& physical_revision = find_row(state, 1, revision_key);
        const auto revision = cd::decode_revision_row(cd::unwrap_value(persisted, cd::TableKind::Events,
            view(revision_key), view(physical_revision.value)));
        expect(revision.generation == generation && !revision.receipt && revision.frame == frame,
            "Immutable canonical revision differs from oracle");
        const auto input = observation(operation);
        const auto full_key = cd::encode_logical_source_key(input.source);
        const auto digest = uses_collision(profile) ? cd::Digest{} : cd::source_digest(view(full_key));
        auto source_key = cd::encode_source_key({receipt.ref->conversation.id, digest, 0});
        auto bucket_key = source_key;
        bucket_key.resize(bucket_key.size() - 4);
        const auto slot = next_slot[bucket_key]++;
        source_key = cd::encode_source_key({receipt.ref->conversation.id, digest, slot});
        const auto& physical_source = find_row(state, 2, source_key);
        const auto source = cd::decode_source_row(cd::unwrap_value(persisted, cd::TableKind::Sources,
            view(source_key), view(physical_source.value)));
        expect(source.key == full_key && source.witness == cd::encode_witness(input) &&
            source.ref == *receipt.ref && source.generation == generation,
            "Original source key/witness/admission receipt lost");
        mapping += mapping_charge(input);
    }
    for(const auto& item : next_entry) {
        const auto key = cd::encode_conversation_key(am::ConversationId{item.first});
        const auto& physical = find_row(state, 0, key);
        const auto row = cd::decode_conversation_row(cd::unwrap_value(persisted, cd::TableKind::Metadata,
            view(key), view(physical.value)));
        expect(row.next_entry == item.second, "Persistent allocator mismatch");
        const auto stream = conversation(item.first);
        same_read(store.read_history(access(), stream), oracle.history().read_history(access(), stream));
    }
    expect(persisted.generation == plan.size() && persisted.conversation_count == next_entry.size() &&
        persisted.entry_count == entries && persisted.revision_count == entries && persisted.mapping_count == entries &&
        persisted.canonical_charge == canonical && persisted.mapping_charge == mapping,
        "Profile counters/charges/generation differ from committed plan");
    // Exact total catches orphan or extra rows, including unknown row families.
    expect(state.rows.size() == 1 + next_entry.size() + plan.size() + 2 * entries,
        "Orphan, duplicate, or partial physical rows");
}

struct Environment final {
    std::filesystem::path path;
    explicit Environment(const std::string& label) {
        static std::uint64_t serial = 0;
        path = std::filesystem::temp_directory_path() / ("agent_memory_process_" + label + "_" +
            std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "_" +
            std::to_string(++serial) + ".mdbx");
        // Establish the empty native environment, but no schema/table marker.
        const auto context = open_context(path);
    }
    ~Environment() {
        std::error_code error;
        std::filesystem::remove(path, error);
        std::filesystem::remove(path.string() + "-lck", error);
    }
};
void prepare(const Environment& environment, const std::string& profile, const Plan& plan) {
    const auto context = open_context(environment.path);
    cd::MdbxConversationDurable store(options(context, profile, true), hooks(profile));
    for(const auto& operation : plan) {
        expect(apply(store, operation).status == am::ConversationWriteStatus::Applied, "Fixture preparation failed");
    }
}

std::string g_boundary;
void barrier() {
    ipc::send_event("BARRIER");
    expect(ipc::receive_command() == "GO", "Expected barrier GO command");
}
void prove_writer_contention(const std::shared_ptr<am::MdbxStorageContext>& context) {
    // The parent keeps the other process at its precommit barrier until this
    // native nonblocking acquisition proves that the real MDBX writer lock is
    // occupied. Simultaneously releasing two pre-transaction gates alone would
    // not prove contention: the scheduler could run both operations serially.
    MDBX_txn* transaction = nullptr;
    const auto code = mdbx_txn_begin(context->connection()->env_handle(), nullptr, MDBX_TXN_TRY, &transaction);
    if(transaction) static_cast<void>(mdbx_txn_abort(transaction));
    expect(code == MDBX_BUSY && !transaction, "Competing process did not encounter the native writer lock");
    ipc::send_event("CONTENDED");
}
const char* step_name(cd::WriteStep step) {
    switch(step) {
    case cd::WriteStep::BeforeTransaction: return "before_transaction";
    case cd::WriteStep::BeforeWrites: return "before_writes";
    case cd::WriteStep::AfterRevision: return "after_revision";
    case cd::WriteStep::AfterSource: return "after_source";
    case cd::WriteStep::AfterGeneration: return "after_generation";
    case cd::WriteStep::AfterConversation: return "after_conversation";
    case cd::WriteStep::AfterProfile: return "after_profile";
    case cd::WriteStep::BeforeCommit: return "before_commit";
    case cd::WriteStep::BeforeSchemaCommit: return "before_schema_commit";
    }
    throw std::runtime_error("Unknown private write step");
}
void stop_at_step(cd::WriteStep step) { if(g_boundary == step_name(step)) barrier(); }
void stop_after_commit() noexcept {
    if(g_boundary != "after_wrapper_commit") return;
    try { barrier(); } catch(...) { std::_Exit(3); }
}
void send_receipt(const am::ConversationWriteResult& receipt) {
    ipc::send_event("RESULT " + std::to_string(static_cast<int>(receipt.status)) + " " +
        std::to_string(receipt.ref ? receipt.ref->entry.value() : 0) + " " +
        std::to_string(receipt.commit_generation));
    expect(ipc::receive_command() == "ACK", "Expected receipt ACK");
}
struct Receipt final {
    am::ConversationWriteStatus status;
    std::uint64_t entry = 0, generation = 0;
};
Receipt receive_receipt(ipc::ChildProcess& child) {
    const auto text = child.receive();
    std::istringstream input(text);
    std::string kind;
    int status = 0;
    Receipt result{};
    expect(static_cast<bool>(input >> kind >> status >> result.entry >> result.generation) && kind == "RESULT",
        "Invalid child receipt");
    input >> std::ws;
    expect(input.eof(), "Trailing receipt data");
    result.status = static_cast<am::ConversationWriteStatus>(status);
    return result;
}
std::vector<std::string> writer_arguments(const Environment& environment, const std::string& profile,
    const Operation& operation, const std::string& boundary) {
    return {"--writer", environment.path.u8string(), profile, encode_operation(operation), boundary};
}
int writer_main(int argc, char** argv) {
    expect(argc == 6, "Invalid writer arguments");
    const std::string profile = argv[3];
    g_boundary = argv[5];
    const bool is_schema = std::string(argv[4]) == "schema";
    const bool is_initialize = std::string(argv[4]) == "initialize";
    const auto context = open_context(std::filesystem::u8path(argv[2]));
    auto test_hooks = hooks(profile);
    test_hooks.before_commit_step = stop_at_step;
    test_hooks.after_commit = stop_after_commit;
    if(is_initialize) {
        barrier();
        if(g_boundary == "contender_init") prove_writer_contention(context);
    }
    cd::MdbxConversationDurable store(options(context, profile, is_schema || is_initialize), test_hooks);
    if(is_schema || is_initialize) {
        if(g_boundary == "after_operation_return") barrier();
        ipc::send_event("INITIALIZED");
        expect(ipc::receive_command() == "ACK", "Expected initialization ACK");
        return 0;
    }
    if(g_boundary == "contender") {
        barrier();
        prove_writer_contention(context);
    }
    const auto receipt = apply(store, decode_operation(argv[4]));
    if(g_boundary == "after_operation_return") barrier();
    send_receipt(receipt);
    return 0;
}

void check_replays(const std::shared_ptr<am::MdbxStorageContext>& context, const std::string& profile,
    const Plan& plan) {
    cd::MdbxConversationDurable store(options(context, profile, false), hooks(profile));
    am::ConversationImportOptions reference_options;
    reference_options.store.allocation_namespace = "process";
    am::InMemoryConversationImportSink oracle(reference_options);
    std::vector<am::ConversationWriteResult> receipts;
    for(const auto& operation : plan) receipts.push_back(apply(oracle, operation));
    const auto before = snapshot(context);
    for(std::size_t index = 0; index < plan.size(); ++index) {
        const auto& operation = plan[index];
        same_write(apply(store, operation), apply(oracle, operation));
        if(!operation.is_create) {
            auto changed = operation;
            changed.is_changed = !changed.is_changed;
            const auto conflict = apply(store, changed);
            expect(conflict.status == am::ConversationWriteStatus::Conflict && !conflict.ref &&
                conflict.commit_generation == 0, "Changed witness did not refuse without leaking receipt");
            const auto replay = apply(store, operation);
            expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == receipts[index].ref &&
                replay.commit_generation == receipts[index].commit_generation, "Replay lost original admission receipt");
        }
    }
    expect(snapshot(context) == before, "Replay/conflict mutated durable rows or counters");
}
int verifier_main(int argc, char** argv) {
    expect(argc == 8, "Invalid verifier arguments");
    const std::string profile = argv[3];
    const bool is_initialized = std::string(argv[4]) == "1";
    auto plan = decode_plan(argv[5]);
    const auto context = open_context(std::filesystem::u8path(argv[2]));
    if(!is_initialized) {
        const auto empty = snapshot(context);
        expect(empty.rows.empty() && std::none_of(empty.tables.begin(), empty.tables.end(),
            [](bool present) { return present; }), "Uncommitted schema left partial DBIs/marker");
        cd::MdbxConversationDurable initialize(options(context, profile, true), hooks(profile));
    }
    validate_state(context, profile, plan);
    check_replays(context, profile, plan);
    if(plan.empty() && std::string(argv[6]) == "-") {
        cd::MdbxConversationDurable store(options(context, profile, false), hooks(profile));
        for(const auto& operation : Plan{create(1), import(1)}) {
            const auto receipt = apply(store, operation);
            expect(receipt.status == am::ConversationWriteStatus::Applied &&
                receipt.commit_generation == plan.size() + 1, "Recovered schema cannot admit consecutive operations");
            plan.push_back(operation);
        }
        validate_state(context, profile, plan);
        check_replays(context, profile, plan);
    }
    if(std::string(argv[6]) != "-") {
        const auto retry = decode_operation(argv[6]);
        const bool was_committed = std::string(argv[7]) == "1";
        cd::MdbxConversationDurable store(options(context, profile, false), hooks(profile));
        const auto receipt = apply(store, retry);
        expect(receipt.status == (was_committed ? am::ConversationWriteStatus::NoChange :
            am::ConversationWriteStatus::Applied), "Unacknowledged outcome retry did not converge");
        if(!was_committed) plan.push_back(retry);
        if(!retry.is_create) {
            expect(receipt.ref && receipt.ref->entry.value() == 2 && receipt.commit_generation == 3,
                "Crash recovery consumed or reused an import ID/generation");
        }
        validate_state(context, profile, plan);
        check_replays(context, profile, plan);
        const auto next = import(9, retry.conversation_id);
        const auto appended = apply(store, next);
        const auto expected_entry = retry.is_create ? 1U : 3U;
        expect(appended.status == am::ConversationWriteStatus::Applied && appended.ref &&
            appended.ref->entry.value() == expected_entry && appended.commit_generation == plan.size() + 1,
            "Committed identities were reused or next allocator/generation has a gap");
        plan.push_back(next);
        validate_state(context, profile, plan);
    }
    ipc::send_event("VERIFIED");
    return 0;
}
void verify_fresh(const std::string& executable, const Environment& environment, const std::string& profile,
    const Plan& plan, bool initialized = true, const std::string& retry = "-", bool committed = false) {
    ipc::ChildProcess verifier(executable, {"--verify", environment.path.u8string(), profile,
        initialized ? "1" : "0", encode_plan(plan), retry, committed ? "1" : "0"});
    expect(verifier.receive() == "VERIFIED", "Fresh-process verification failed");
    verifier.wait_success();
}

void crash_matrix(const std::string& executable) {
    std::size_t cases = 0;
    for(const auto* boundary : {"before_schema_commit", "before_commit", "after_wrapper_commit",
        "after_operation_return"}) {
        Environment environment("schema");
        ipc::ChildProcess writer(executable, {"--writer", environment.path.u8string(), "plain", "schema", boundary});
        expect(writer.receive() == "BARRIER", "Schema writer did not reach exact barrier");
        writer.terminate_and_wait();
        const bool committed = std::string(boundary) == "after_wrapper_commit" ||
            std::string(boundary) == "after_operation_return";
        verify_fresh(executable, environment, "plain", {}, committed);
        ++cases;
    }
    for(const bool is_create : {true, false}) {
        const auto candidate = is_create ? create(2) : import(2);
        std::vector<std::string> boundaries{"before_transaction", "before_writes"};
        if(!is_create) { boundaries.push_back("after_revision"); boundaries.push_back("after_source"); }
        for(const auto* boundary : {"after_generation", "after_conversation", "after_profile", "before_commit",
            "after_wrapper_commit", "after_operation_return"}) boundaries.push_back(boundary);
        for(const auto& boundary : boundaries) {
            Environment environment(is_create ? "create" : "import");
            Plan plan{create(1), import(1)};
            prepare(environment, "plain", plan);
            ipc::ChildProcess writer(executable, writer_arguments(environment, "plain", candidate, boundary));
            expect(writer.receive() == "BARRIER", "Writer did not reach exact crash barrier");
            writer.terminate_and_wait();
            const bool committed = boundary == "after_wrapper_commit" || boundary == "after_operation_return";
            if(committed) plan.push_back(candidate);
            verify_fresh(executable, environment, "plain", plan, true, encode_operation(candidate), committed);
            ++cases;
        }
    }
    std::cout << "Acknowledged process crash cases: " << cases << '\n';
}

void race(const std::string& executable, const std::string& scenario, bool reverse) {
    const auto profile = scenario == "identical" || scenario == "conflict" || scenario == "distinct" ||
        scenario == "conversations" ? std::string("plain") : scenario;
    Environment environment(scenario);
    Plan plan{create(1)};
    if(scenario == "conversations") plan.push_back(create(2));
    if(profile == "quota" || profile == "rows" || profile == "bytes") plan.push_back(import(1));
    prepare(environment, profile, plan);
    auto first = import(2);
    auto second = scenario == "identical" ? first :
        (scenario == "conflict" ? import(2, 1, true) : import(3, scenario == "conversations" ? 2 : 1));
    if(reverse) std::swap(first, second);
    ipc::ChildProcess left(executable, writer_arguments(environment, profile, first, "before_commit"));
    ipc::ChildProcess right(executable, writer_arguments(environment, profile, second, "contender"));
    expect(left.receive() == "BARRIER" && right.receive() == "BARRIER", "Writers did not independently open/gate");
    right.send("GO");
    expect(right.receive() == "CONTENDED", "Native competing-writer contention was not acknowledged");
    left.send("GO");
    const auto left_receipt = receive_receipt(left), right_receipt = receive_receipt(right);
    left.send("ACK"); right.send("ACK");
    left.wait_success(); right.wait_success();
    const bool left_applied = left_receipt.status == am::ConversationWriteStatus::Applied;
    const bool right_applied = right_receipt.status == am::ConversationWriteStatus::Applied;
    if(scenario == "identical") {
        expect(left_applied != right_applied &&
            (left_applied ? right_receipt.status : left_receipt.status) == am::ConversationWriteStatus::NoChange &&
            left_receipt.entry == right_receipt.entry && left_receipt.generation == right_receipt.generation,
            "Identical race did not return Applied plus original NoChange");
    } else if(scenario == "conflict" || profile == "quota" || profile == "rows" || profile == "bytes") {
        const auto refused = left_applied ? right_receipt : left_receipt;
        expect(left_applied != right_applied && refused.status == (scenario == "conflict" ?
            am::ConversationWriteStatus::Conflict : am::ConversationWriteStatus::ResourceLimit) &&
            refused.entry == 0 && refused.generation == 0, "Race refusal leaked data or exceeded persistent budget");
    } else {
        expect(left_applied && right_applied && left_receipt.generation != right_receipt.generation,
            "Distinct races did not both commit independently");
    }
    std::vector<std::pair<Receipt, Operation>> committed;
    if(left_applied) committed.emplace_back(left_receipt, first);
    if(right_applied) committed.emplace_back(right_receipt, second);
    std::sort(committed.begin(), committed.end(), [](const auto& a, const auto& b) {
        return a.first.generation < b.first.generation;
    });
    for(const auto& item : committed) {
        expect(item.first.generation == plan.size() + 1, "Race consumed a refused generation");
        const auto prior_imports = std::count_if(plan.begin(), plan.end(), [&item](const Operation& operation) {
            return !operation.is_create && operation.conversation_id == item.second.conversation_id;
        });
        expect(item.first.entry == static_cast<std::uint64_t>(prior_imports) + 1,
            "Race receipt did not return the consecutive committed Entry ID");
        plan.push_back(item.second);
    }
    verify_fresh(executable, environment, profile, plan);
    std::cout << "Independent-process race with MDBX_BUSY proof: " << scenario <<
        (reverse ? " (reversed operands)" : "") << '\n';
}
void initialization_race(const std::string& executable) {
    Environment environment("initialization");
    ipc::ChildProcess left(executable, {"--writer", environment.path.u8string(), "plain", "schema", "before_schema_commit"});
    ipc::ChildProcess right(executable, {"--writer", environment.path.u8string(), "plain", "initialize", "contender_init"});
    expect(left.receive() == "BARRIER" && right.receive() == "BARRIER", "Initialization gate missing");
    right.send("GO");
    expect(right.receive() == "CONTENDED", "Concurrent schema initialization did not encounter the writer lock");
    left.send("GO");
    expect(left.receive() == "INITIALIZED" && right.receive() == "INITIALIZED", "Concurrent initialization failed");
    left.send("ACK"); right.send("ACK");
    left.wait_success(); right.wait_success();
    verify_fresh(executable, environment, "plain", {});
    std::cout << "Independent-process race: initialization/attach\n";
}
int reader_main(int argc, char** argv) {
    expect(argc == 3, "Invalid reader arguments");
    const auto context = open_context(std::filesystem::u8path(argv[2]));
    {
        cd::MdbxConversationDurable store(options(context, "plain", false));
    }
    // The native snapshot stays pinned while an independently opened writer
    // commits. Close it before opening a fresh store read on the same thread.
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto before = snapshot(transaction.handle());
    ipc::send_event("SNAPSHOT");
    expect(ipc::receive_command() == "CHECK", "Expected committed-writer check command");
    expect(snapshot(transaction.handle()) == before, "Committed writer changed a pinned read snapshot");
    transaction.commit();
    validate_state(context, "plain", {create(1), import(1), import(2)});
    ipc::send_event("READ_VERIFIED");
    return 0;
}
void reader_race(const std::string& executable) {
    Environment environment("reader");
    prepare(environment, "plain", {create(1), import(1)});
    ipc::ChildProcess reader(executable, {"--reader", environment.path.u8string()});
    expect(reader.receive() == "SNAPSHOT", "Reader did not pin native transaction");
    ipc::ChildProcess writer(executable, writer_arguments(environment, "plain", import(2), "before_transaction"));
    expect(writer.receive() == "BARRIER", "Reader-race writer gate missing");
    writer.send("GO");
    const auto receipt = receive_receipt(writer);
    expect(receipt.status == am::ConversationWriteStatus::Applied && receipt.entry == 2 && receipt.generation == 3,
        "Reader blocked/corrupted independent committed admission");
    writer.send("ACK"); writer.wait_success();
    reader.send("CHECK");
    expect(reader.receive() == "READ_VERIFIED", "Reader snapshot/fresh-read parity failed");
    reader.wait_success();
    verify_fresh(executable, environment, "plain", {create(1), import(1), import(2)});
    std::cout << "Independent-process race: pinned reader/committed writer\n";
}
} // namespace

int main(int argc, char** argv) {
    try {
        if(argc > 1) {
            const std::string role = argv[1];
            if(role == "--writer") return writer_main(argc, argv);
            if(role == "--verify") return verifier_main(argc, argv);
            if(role == "--reader") return reader_main(argc, argv);
            throw std::runtime_error("Unknown child role");
        }
        const auto executable = std::filesystem::absolute(std::filesystem::u8path(argv[0])).u8string();
        crash_matrix(executable);
        for(const bool reverse : {false, true}) {
            for(const auto* scenario : {"identical", "conflict", "distinct", "conversations", "collision", "quota",
                "rows", "bytes"}) race(executable, scenario, reverse);
        }
        initialization_race(executable);
        reader_race(executable);
        std::cout << "Process termination tests do not qualify storage-device power loss.\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "conversation durable process test: " << error.what() << '\n';
        return 1;
    }
}
