#include <agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.hpp>
#include <mdbx_containers/common.hpp>

#include <chrono>
#include <cstdio>
#include <filesystem>
#include <limits>
#include <stdexcept>
#include <vector>

namespace am = agent_memory;
namespace cd = agent_memory::detail::conversation_durable;
namespace {
constexpr auto kMax = std::numeric_limits<std::uint64_t>::max();
constexpr const char* kTables[] = {"agent_memory_conversation_meta_v1", "agent_memory_conversation_events_v1",
    "agent_memory_conversation_sources_v1"};
void expect(bool condition, const char* message) { if(!condition) throw std::runtime_error(message); }
void checked(int code) { if(code != MDBX_SUCCESS) throw std::runtime_error(mdbx_strerror(code)); }
am::ByteView view(const am::ByteBuffer& value) { return {value.data(), value.size()}; }
MDBX_val raw(am::ByteView value) { return {const_cast<std::uint8_t*>(value.data), value.size}; }
am::ByteBuffer copy(const MDBX_val& value) {
    const auto* first = static_cast<const std::uint8_t*>(value.iov_base);
    return value.iov_len ? am::ByteBuffer{first, first + value.iov_len} : am::ByteBuffer{};
}
am::ConversationRef conversation(std::uint64_t id = 1) { return {"boundaries", am::ConversationId{id}}; }
am::ConversationStoreAccess access() { return {am::ScopeId{"scope"}, "host-policy"}; }
cd::Quotas quotas() { return {8, 32, 4, 16U * 1024U * 1024U, 32, 16U * 1024U * 1024U}; }
am::ConversationSourceObservation observation(const char* key = "first") {
    am::ConversationSourceObservation input;
    input.conversation = conversation(); input.scope = access().scope;
    input.source.identity = am::ConversationSourceIdentity::ExternalEvent;
    input.source.event = am::ExternalObjectRef{"fixture", "instance", "run-a", "event", std::nullopt,
        am::ExternalObjectKey{std::string{key}}};
    input.source.revision = am::SourceRevisionId{"revision"}; input.source.adapter_recipe = "boundary-v1";
    input.source.evidence = {am::ArtifactBindingId{"artifact"}, "records/first"}; input.producer = "fixture";
    input.payload = am::ConversationSourceMessage{am::ConversationSourceMessageKind::Snapshot,
        {std::string{"source text"}, {}}};
    return input;
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
    cd::StorageDescriptor descriptor;
    explicit Fixture(cd::Quotas input = quotas()) : policy(input) {
        static std::uint64_t serial = 0;
        path = std::filesystem::temp_directory_path() / ("agent_memory_durable_boundaries_" +
            std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "_" +
            std::to_string(++serial) + ".mdbx");
        open();
    }
    ~Fixture() {
        context.reset(); std::error_code error;
        std::filesystem::remove(path, error); std::filesystem::remove(path.string() + "-lck", error);
    }
    void open() {
        // Exercise owned open, not Connection::create plus attach. This API's
        // actual default geometry is explicitly accepted by the host fixture.
        context = am::MdbxStorageContext::open(path.string(), false, 96);
    }
    void reopen() { context.reset(); open(); }
    cd::DurableOptions options(bool initialize = true) const {
        MDBX_envinfo info{};
        checked(mdbx_env_info_ex(context->connection()->env_handle(), nullptr, &info, sizeof(info)));
        cd::DurableOptions result(context, conversation().allocation_namespace, policy,
            {info.mi_geo.upper, info.mi_geo.grow});
        result.allow_initialize = initialize;
        result.descriptor = descriptor;
        return result;
    }
    std::vector<RawRow> snapshot() const {
        std::vector<RawRow> result;
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        for(std::size_t table = 0; table < 3; ++table) {
            MDBX_dbi dbi{}; const auto opened = mdbx_dbi_open(txn.handle(), kTables[table], MDBX_DB_DEFAULTS, &dbi);
            if(opened == MDBX_NOTFOUND) continue;
            checked(opened);
            unsigned flags = 0; checked(mdbx_dbi_flags(txn.handle(), dbi, &flags)); result.push_back({table, flags, {}, {}});
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
        txn.commit(); return result;
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
        auto k = raw(view(key)); auto v = raw(view(value)); checked(mdbx_put(txn.handle(), dbi, &k, &v, MDBX_UPSERT)); txn.commit();
    }
    cd::Profile profile() const { const auto value = get(0, cd::encode_profile_key()); return cd::decode_profile_value(view(value)); }
    void profile(const cd::Profile& value) const {
        const auto key = cd::encode_profile_key(); const auto body = cd::encode_profile(value);
        put(0, key, cd::wrap_value(value, cd::TableKind::Metadata, view(key), view(body)));
    }
    cd::ConversationRow conversation_row() const {
        const auto p = profile(); const auto key = cd::encode_conversation_key(conversation().id); const auto value = get(0, key);
        return cd::decode_conversation_row(cd::unwrap_value(p, cd::TableKind::Metadata, view(key), view(value)));
    }
    void next_entry(std::uint64_t next) const {
        const auto p = profile(); const auto key = cd::encode_conversation_key(conversation().id);
        auto row = conversation_row(); row.next_entry = next; const auto body = cd::encode_conversation_row(row);
        put(0, key, cd::wrap_value(p, cd::TableKind::Metadata, view(key), view(body)));
    }
};

void create(Fixture& fixture) {
    cd::MdbxConversationDurable store(fixture.options());
    const auto result = store.create_conversation(access(), conversation());
    expect(result.status == am::ConversationWriteStatus::Applied && result.commit_generation == 1, "Boundary fixture create");
}
void refused_unchanged(Fixture& fixture, cd::MdbxConversationDurable& store,
    const am::ConversationSourceObservation& input) {
    const auto before = fixture.snapshot(); const auto result = store.import_observation(access(), input);
    expect(result.status == am::ConversationWriteStatus::ResourceLimit && !result.ref && !result.commit_generation,
        "Boundary refusal must be ResourceLimit without a hidden receipt");
    expect(fixture.snapshot() == before, "Boundary refusal changed physical rows/counters");
}

template<typename Function>
void corrupt_unchanged(Fixture& fixture, Function operation, const char* message) {
    const auto before = fixture.snapshot(); bool is_corruption = false;
    try { operation(); }
    catch(const cd::CodecError& error) { is_corruption = error.code() == cd::CodecCode::Corruption; }
    expect(is_corruption, message);
    expect(fixture.snapshot() == before, "Corruption refusal changed persisted rows");
}

void profile_size_and_local_budget() {
    Fixture fixture; create(fixture);
    const auto key = cd::encode_profile_key(); const auto original = fixture.get(0, key);
    const auto charge = key.size() + original.size();
    auto missing = observation(); missing.conversation = conversation(2);
    for(const auto budget : {charge, charge - 1, std::size_t{0}}) {
        auto options = fixture.options(false); options.max_read_raw_bytes = budget;
        // Bootstrap has its own fixed schema budget, even when subsequent reads
        // have no raw-byte allowance. A valid profile never becomes corruption.
        cd::MdbxConversationDurable store(options); const auto before = fixture.snapshot();
        const auto read = store.read_history(access(), conversation(2));
        const auto write = store.import_observation(access(), missing);
        expect(read.status == (budget == charge ? am::ConversationReadStatus::NotFound :
            am::ConversationReadStatus::BudgetExhausted), "Valid profile local read boundary misclassified");
        expect(write.status == (budget == charge ? am::ConversationWriteStatus::NotFound :
            am::ConversationWriteStatus::ResourceLimit), "Valid profile local write boundary misclassified");
        expect(!write.ref && !write.commit_generation && fixture.snapshot() == before,
            "Local profile budget refusal changed persisted state");
    }
    for(const auto size : {std::size_t{65537}, std::size_t{65536}, cd::kMaxProfileValueBytes + 1,
            cd::kMaxProfileValueBytes}) {
        auto malformed = original; malformed.resize(size, 0);
        fixture.put(0, key, malformed);
        corrupt_unchanged(fixture, [&fixture] { cd::MdbxConversationDurable store(fixture.options(false)); },
            "Malformed profile bootstrap must report corruption, not local ResourceLimit");
        fixture.put(0, key, original);
        auto options = fixture.options(false);
        // At the size ceiling the deliberately malformed grammar needs decode
        // budget. Beyond it the already-fetched impossible length is decisive.
        options.max_read_raw_bytes = size == cd::kMaxProfileValueBytes ? key.size() + size : 0;
        cd::MdbxConversationDurable store(options); fixture.put(0, key, malformed);
        corrupt_unchanged(fixture, [&store] { (void)store.read_history(access(), conversation(2)); },
            "Malformed loaded profile read must report corruption");
        corrupt_unchanged(fixture, [&store, &missing] { (void)store.import_observation(access(), missing); },
            "Malformed loaded profile write must report corruption");
        fixture.put(0, key, original);
        if(size == cd::kMaxProfileValueBytes) {
            options.max_read_raw_bytes = 0; cd::MdbxConversationDurable bounded(options);
            fixture.put(0, key, malformed); const auto before = fixture.snapshot();
            expect(bounded.read_history(access(), conversation(2)).status == am::ConversationReadStatus::BudgetExhausted &&
                bounded.import_observation(access(), missing).status == am::ConversationWriteStatus::ResourceLimit &&
                fixture.snapshot() == before, "Possible profile length must not force decode beyond local budget");
            fixture.put(0, key, original);
        }
    }
}

void fetched_row_size_precedes_local_budget() {
    Fixture fixture;
    fixture.descriptor.codec.max_frame_bytes = 2048;
    fixture.descriptor.bucket_bytes = 4096;
    create(fixture);
    am::ConversationWriteResult admitted;
    { cd::MdbxConversationDurable store(fixture.options(false)); admitted = store.import_observation(access(), observation()); }
    expect(admitted.status == am::ConversationWriteStatus::Applied && admitted.ref, "Size-boundary import failed");
    const auto profile_key = cd::encode_profile_key(); const auto profile_value = fixture.get(0, profile_key);
    const auto conversation_key = cd::encode_conversation_key(conversation().id);
    const auto conversation_value = fixture.get(0, conversation_key);
    const auto revision_key = cd::encode_revision_key({conversation().id, admitted.ref->entry, admitted.ref->revision});
    const auto revision_value = fixture.get(1, revision_key);
    const auto generation_key = cd::encode_generation_key(admitted.commit_generation);
    const auto source_body = cd::encode_logical_source_key(observation().source);
    const auto source_key = cd::encode_source_key({conversation().id, cd::source_digest(view(source_body)), 0});
    const auto profile_charge = profile_key.size() + profile_value.size();
    const auto conversation_charge = conversation_key.size() + conversation_value.size();
    const auto revision_charge = revision_key.size() + revision_value.size();
    const auto frame_limit = fixture.descriptor.codec.max_frame_bytes;
    struct Case final { std::size_t table; am::ByteBuffer key; std::size_t value_limit, prior_bytes; };
    const Case cases[] = {
        {0, conversation_key, frame_limit + 20 + cd::kValueOverheadBytes, profile_charge},
        {1, revision_key, frame_limit + 153 + cd::kValueOverheadBytes, profile_charge + conversation_charge},
        {1, generation_key, cd::kMaxGenerationValueBytes, profile_charge + conversation_charge + revision_charge},
        {2, source_key, static_cast<std::size_t>(fixture.descriptor.bucket_bytes), profile_charge + conversation_charge}
    };
    for(const auto& selected : cases) {
        const auto original = fixture.get(selected.table, selected.key);
        auto options = fixture.options(false); options.max_read_raw_bytes = selected.prior_bytes;
        cd::MdbxConversationDurable store(options);
        auto at_limit = original; at_limit.resize(selected.value_limit, 0);
        fixture.put(selected.table, selected.key, at_limit);
        // Equality alone proves no corruption: do not decode malformed bytes
        // that remain within the structural ceiling after local work is spent.
        if(selected.table == 2) refused_unchanged(fixture, store, observation());
        else {
            const auto before = fixture.snapshot(); const auto result = store.read_exact(access(), *admitted.ref);
            expect(result.status == am::ConversationReadStatus::BudgetExhausted && fixture.snapshot() == before,
                "Possible fetched row length must retain local budget refusal");
        }
        auto oversized = original; oversized.resize(selected.value_limit + 1, 0);
        fixture.put(selected.table, selected.key, oversized);
        if(selected.table == 2) {
            corrupt_unchanged(fixture, [&store] { (void)store.import_observation(access(), observation()); },
                "Oversized fetched source row must report corruption before local budget");
        } else {
            corrupt_unchanged(fixture, [&store, &admitted] { (void)store.read_exact(access(), *admitted.ref); },
                "Oversized fetched metadata/event row must report corruption before local budget");
            if(selected.key != generation_key) {
                corrupt_unchanged(fixture, [&store] { (void)store.read_history(access(), conversation()); },
                    "Oversized fetched history row must report corruption before local budget");
            }
            if(selected.table == 0) {
                corrupt_unchanged(fixture, [&store] { (void)store.create_conversation(access(), conversation()); },
                    "Oversized existing conversation must report corruption before local budget");
            }
        }
        fixture.put(selected.table, selected.key, original);
        // The same low budget must not falsely label the valid original row.
        if(selected.table == 2) {
            const auto before = fixture.snapshot(); const auto result = store.import_observation(access(), observation());
            expect(result.status == am::ConversationWriteStatus::ResourceLimit && fixture.snapshot() == before,
                "Valid fetched source row must retain local resource refusal");
        } else {
            const auto before = fixture.snapshot(); const auto result = store.read_exact(access(), *admitted.ref);
            expect(result.status == am::ConversationReadStatus::BudgetExhausted && fixture.snapshot() == before,
                "Valid fetched metadata/event row must retain local budget refusal");
        }
    }
}

void allocator_exhaustion() {
    Fixture fixture; create(fixture);
    // Qualification-only arithmetic seed, not a claim of a physically populated
    // MAX-sized history. Every rewritten value retains its valid checksum.
    fixture.next_entry(kMax - 1);
    am::ConversationWriteResult admitted;
    {
        cd::MdbxConversationDurable store(fixture.options(false));
        admitted = store.import_observation(access(), observation());
        expect(admitted.status == am::ConversationWriteStatus::Applied && admitted.ref &&
            admitted.ref->entry.value() == kMax - 1 && admitted.commit_generation == 2,
            "Allocator failed last allocatable EntryId");
        expect(fixture.conversation_row().next_entry == kMax, "Allocator did not persist exhausted-next sentinel");
        refused_unchanged(fixture, store, observation("blocked"));
        const auto replay = store.import_observation(access(), observation());
        expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == admitted.ref &&
            replay.commit_generation == admitted.commit_generation, "Exhausted allocator blocked original replay");
        const auto owned = store.read_exact(access(), *admitted.ref);
        expect(owned.status == am::ConversationReadStatus::Complete && owned.records.size() == 1,
            "Last allocatable EntryId is not readable");
    }
    fixture.reopen(); cd::MdbxConversationDurable reopened(fixture.options(false));
    expect(fixture.conversation_row().next_entry == kMax, "Reopen lost allocator exhaustion sentinel");
    refused_unchanged(fixture, reopened, observation("after-reopen"));
    const auto replay = reopened.import_observation(access(), observation());
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == admitted.ref,
        "Reopen cannot replay exhausted allocator");
}

void generation_exhaustion() {
    Fixture fixture; create(fixture);
    auto seeded = fixture.profile(); seeded.generation = kMax - 1; fixture.profile(seeded);
    am::ConversationWriteResult last;
    {
        cd::MdbxConversationDurable store(fixture.options(false));
        last = store.import_observation(access(), observation());
        expect(last.status == am::ConversationWriteStatus::Applied && last.commit_generation == kMax &&
            last.ref->entry.value() == 1, "Generation did not admit UINT64_MAX exactly");
        expect(fixture.profile().generation == kMax, "Last generation not persisted");
        refused_unchanged(fixture, store, observation("after-max-generation"));
        const auto before = fixture.snapshot(); const auto creation = store.create_conversation(access(), conversation(2));
        expect(creation.status == am::ConversationWriteStatus::ResourceLimit && fixture.snapshot() == before,
            "Create wrapped exhausted generation");
    }
    fixture.reopen(); cd::MdbxConversationDurable store(fixture.options(false));
    const auto replay = store.import_observation(access(), observation());
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == last.ref && replay.commit_generation == kMax,
        "Exhausted generation prevented original replay");
    refused_unchanged(fixture, store, observation("reopened-max-generation"));
}

void quota_and_counter_no_wrap() {
    const cd::Quotas maximum{kMax - 1, kMax - 1, kMax - 1, kMax - 1, kMax - 1, kMax - 1};
    {
        Fixture fixture(maximum); create(fixture); cd::MdbxConversationDurable store(fixture.options(false));
        expect(store.import_observation(access(), observation()).status == am::ConversationWriteStatus::Applied,
            "Explicit UINT64_MAX-1 quotas are not accepted");
    }
    for(unsigned selected = 0; selected < 4; ++selected) {
        Fixture fixture(maximum); create(fixture); auto p = fixture.profile();
        if(selected == 0) p.canonical_charge = kMax - 2;
        if(selected == 1) p.mapping_charge = kMax - 2;
        if(selected == 2) p.revision_count = kMax;
        if(selected == 3) { p.conversation_count = kMax - 1; p.generation = kMax - 1; }
        fixture.profile(p); cd::MdbxConversationDurable store(fixture.options(false));
        if(selected < 3) refused_unchanged(fixture, store, observation());
        else {
            const auto before = fixture.snapshot(); const auto result = store.create_conversation(access(), conversation(2));
            expect(result.status == am::ConversationWriteStatus::ResourceLimit && fixture.snapshot() == before,
                "Conversation quota overflow/wrap was not refused atomically");
        }
        const auto after = fixture.profile();
        expect(after.generation == p.generation && after.canonical_charge == p.canonical_charge &&
            after.mapping_charge == p.mapping_charge && after.revision_count == p.revision_count,
            "Counter overflow refusal wrapped a value");
    }
    constexpr std::uint64_t cd::Quotas::* fields[] = {&cd::Quotas::conversations, &cd::Quotas::entries,
        &cd::Quotas::revisions_per_entry, &cd::Quotas::canonical_bytes, &cd::Quotas::mapping_rows, &cd::Quotas::mapping_bytes};
    for(const auto field : fields) {
        Fixture fixture; const auto before = fixture.snapshot();
        auto options = fixture.options(); options.quotas.*field = kMax; bool rejected = false;
        try { cd::MdbxConversationDurable store(options); } catch(const std::invalid_argument&) { rejected = true; }
        expect(rejected && fixture.snapshot() == before, "Reserved UINT64_MAX quota initialized or changed tables");
    }
}

void owned_context_lifetime() {
    Fixture fixture;
    am::ConversationReadResult retained; am::ByteBuffer canonical;
    {
        auto store = std::make_unique<cd::MdbxConversationDurable>(fixture.options());
        fixture.context.reset(); // The adapter now retains the sole owned context.
        expect(store->create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied,
            "Owned context was not retained by durable adapter");
        const auto admitted = store->import_observation(access(), observation());
        expect(admitted.status == am::ConversationWriteStatus::Applied, "Owned context import failed");
        retained = store->read_exact(access(), *admitted.ref);
        expect(retained.status == am::ConversationReadStatus::Complete && retained.records.size() == 1,
            "Owned context exact read failed");
        canonical = am::serialize_conversation_envelope(retained.records.front().envelope);
    }
    expect(am::serialize_conversation_envelope(retained.records.front().envelope) == canonical,
        "Owned result outlived environment incorrectly");
    fixture.open(); cd::MdbxConversationDurable reopened(fixture.options(false));
    const auto replay = reopened.import_observation(access(), observation());
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref && replay.ref->entry.value() == 1 &&
        replay.commit_generation == 2, "Owned open/reopen did not retain exact source replay");
}

void invalid_dto_error_order() {
    Fixture fixture; cd::MdbxConversationDurable store(fixture.options());
    am::ConversationImportOptions options; options.store.allocation_namespace = conversation().allocation_namespace;
    am::InMemoryConversationImportSink oracle(options);
    expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied &&
        oracle.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied, "Error-order create");
    const auto compare = [&fixture, &store, &oracle](const am::ConversationStoreAccess& authority,
        const am::ConversationSourceObservation& input, am::ConversationWriteStatus expected, const char* message) {
        const auto before = fixture.snapshot();
        const auto actual = store.import_observation(authority, input); const auto reference = oracle.import_observation(authority, input);
        if(actual.status != reference.status || reference.status != expected) {
            std::fprintf(stderr, "error-order case %s: durable=%d reference=%d expected=%d\n", message,
                static_cast<int>(actual.status), static_cast<int>(reference.status), static_cast<int>(expected));
            throw std::runtime_error(message);
        }
        expect(!actual.ref && !actual.commit_generation && before == fixture.snapshot(), "Invalid DTO leaked receipt or changed rows");
    };
    auto input = observation(); auto authority = access();
    authority.policy_binding = "Bad"; input.producer.clear();
    compare(authority, input, am::ConversationWriteStatus::Denied, "Access precedes invalid DTO");
    authority = access(); input = observation(); input.scope = am::ScopeId{"other"}; input.source.revision = {};
    compare(authority, input, am::ConversationWriteStatus::Denied, "Input scope precedes source validation");
    input = observation(); input.conversation.id = am::ConversationId{std::uint64_t{2}}; input.producer.clear();
    compare(access(), input, am::ConversationWriteStatus::NotFound, "Missing conversation precedes producer validation");
    input = observation(); input.conversation.allocation_namespace = "invalid namespace";
    compare(access(), input, am::ConversationWriteStatus::NotFound, "Invalid namespace does not reveal conversation existence");
    input = observation(); input.conversation.id = am::ConversationId{};
    compare(access(), input, am::ConversationWriteStatus::NotFound, "Zero local ID has no existing conversation");
    input = observation(); authority = access(); authority.scope = am::ScopeId{"other"}; input.scope = authority.scope; input.producer.clear();
    compare(authority, input, am::ConversationWriteStatus::NotFound, "Stored scope precedes producer validation");

    const auto result = [] {
        auto input = observation();
        input.payload = am::ConversationSourceToolResult{am::ConversationSourceTarget{conversation(), am::ScopeId{"denied"},
            am::copy_conversation_source_binding(input.source)}, {0, 255}};
        return input;
    };
    input = result(); input.source.event.reset();
    compare(access(), input, am::ConversationWriteStatus::InvalidRequest, "Invalid source precedes denied target");
    input = result(); input.source.revision = am::SourceRevisionId{std::string(4097, 'r')}; input.producer.clear();
    compare(access(), input, am::ConversationWriteStatus::ResourceLimit, "Byte preflight precedes invalid producer and target");
    input = result(); input.producer = "\xc0\xaf";
    compare(access(), input, am::ConversationWriteStatus::InvalidRequest, "Producer UTF-8 precedes target policy");
    input = result(); input.actor = am::ActorRef{"invalid namespace", am::ActorId{std::uint64_t{1}}};
    compare(access(), input, am::ConversationWriteStatus::Denied, "Target policy precedes normalized actor validation");
    input = result();
    compare(access(), input, am::ConversationWriteStatus::Denied, "Denied source-shaped target");
    const auto actual = store.import_observation(access(), observation()); const auto reference = oracle.import_observation(access(), observation());
    expect(actual.status == am::ConversationWriteStatus::Applied && actual.ref == reference.ref && actual.commit_generation == 2 &&
        actual.ref->entry.value() == 1, "Invalid DTO refusals consumed canonical ID/generation");
}
} // namespace

int main() {
    try {
        profile_size_and_local_budget(); fetched_row_size_precedes_local_budget();
        allocator_exhaustion(); generation_exhaustion(); quota_and_counter_no_wrap(); owned_context_lifetime(); invalid_dto_error_order();
        std::puts("PASS durable structural/local budgets, allocator/generation/quota boundaries, owned context and DTO order");
        return 0;
    } catch(const std::exception& error) { std::fprintf(stderr, "durable boundaries: %s\n", error.what()); return 1; }
}
