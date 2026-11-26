#include <agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.hpp>
#include <mdbx_containers/common.hpp>

#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <filesystem>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace am = agent_memory;
namespace cd = agent_memory::detail::conversation_durable;

namespace {
constexpr std::uint64_t kMiB = 1024U * 1024U;
constexpr std::uint64_t kNormalMapBytes = 64U * kMiB;
constexpr std::uint64_t kGrowthBytes = kMiB;
struct SnapshotRow final {
    std::string table;
    unsigned flags;
    am::ByteBuffer key, value;
    bool operator==(const SnapshotRow& other) const {
        return table == other.table && flags == other.flags && key == other.key && value == other.value;
    }
};
using Snapshot = std::vector<SnapshotRow>;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
void checked(int code, const char* operation) {
    if(code != MDBX_SUCCESS) throw std::runtime_error(std::string(operation) + ": " + mdbx_strerror(code));
}
am::ByteBuffer copy(const MDBX_val& value) {
    const auto* first = static_cast<const std::uint8_t*>(value.iov_base);
    return value.iov_len ? am::ByteBuffer{first, first + value.iov_len} : am::ByteBuffer{};
}
am::ConversationRef conversation() { return {"native_failures", am::ConversationId{std::uint64_t{1}}}; }
am::ConversationStoreAccess access() { return {am::ScopeId{"scope"}, "host-policy"}; }
cd::Quotas quotas() { return {8, 32, 4, 16U * kMiB, 32, 16U * kMiB}; }
am::ConversationSourceObservation observation(const char* key, std::size_t text_bytes = 16) {
    am::ConversationSourceObservation input;
    input.conversation = conversation(); input.scope = access().scope;
    input.source.identity = am::ConversationSourceIdentity::ExternalEvent;
    input.source.event = am::ExternalObjectRef{"fixture", "instance", "run", "event", std::nullopt,
        am::ExternalObjectKey{std::string{key}}};
    input.source.revision = am::SourceRevisionId{"revision"}; input.source.adapter_recipe = "native-failure-v1";
    input.source.evidence = {am::ArtifactBindingId{"artifact"}, std::string{"records/"} + key};
    input.producer = "fixture";
    input.payload = am::ConversationSourceMessage{am::ConversationSourceMessageKind::Snapshot,
        {std::string(text_bytes, 'x'), {}}};
    return input;
}

template<typename Function>
void backend_failure(Function operation, int expected_code, const char* message) {
    try { operation(); }
    catch(const mdbxc::MdbxException& error) {
        expect(error.error_code() == expected_code, message);
        expect(error.what() && *error.what(), "Native failure lost its diagnostic");
        std::printf("OBSERVED native error %d: %s\n", error.error_code(), error.what());
        return;
    }
    throw std::runtime_error(message);
}
template<typename Function>
void unsupported(Function operation, const char* message) {
    try { operation(); }
    catch(const cd::CodecError& error) {
        expect(error.code() == cd::CodecCode::UnsupportedSchema, message);
        return;
    }
    throw std::runtime_error(message);
}

class Cursor final {
public:
    Cursor(MDBX_txn* txn, MDBX_dbi dbi) { checked(mdbx_cursor_open(txn, dbi, &m_cursor), "open snapshot cursor"); }
    ~Cursor() { mdbx_cursor_close(m_cursor); }
    Cursor(const Cursor&) = delete;
    Cursor& operator=(const Cursor&) = delete;
    MDBX_cursor* handle() const { return m_cursor; }
private:
    MDBX_cursor* m_cursor = nullptr;
};

struct Fixture final {
    std::filesystem::path path;
    std::shared_ptr<am::MdbxStorageContext> context;
    std::uint64_t map_bytes;
    std::int64_t max_dbs;
    bool use_writemap = false;

    explicit Fixture(const char* label, std::uint64_t size = kNormalMapBytes, std::int64_t dbis = 96)
        : map_bytes(size), max_dbs(dbis) {
        static unsigned serial = 0;
        path = std::filesystem::temp_directory_path() / (std::string{"agent_memory_native_failure_"} + label +
            std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "_" +
            std::to_string(++serial) + ".mdbx");
        open();
    }
    ~Fixture() {
        context.reset();
        std::error_code error;
        std::filesystem::remove(path, error);
        std::filesystem::remove(path.string() + "-lck", error);
    }
    void open() {
        mdbxc::Config config;
        config.pathname = path.string(); config.relative_to_exe = false; config.no_subdir = true;
        config.max_dbs = max_dbs; config.max_readers = 4;
        config.size_lower = static_cast<std::int64_t>(kMiB);
        config.size_now = static_cast<std::int64_t>(kMiB);
        config.size_upper = static_cast<std::int64_t>(map_bytes);
        config.growth_step = static_cast<std::int64_t>(kGrowthBytes);
        config.shrink_threshold = 0; config.writemap_mode = use_writemap;
        context = am::MdbxStorageContext::attach(mdbxc::Connection::create(config));
    }
    void reopen() { context.reset(); open(); }
    MDBX_env* env() const { return context->connection()->env_handle(); }
    cd::DurableOptions options(bool initialize = false, std::uint64_t host_dbis = 0) const {
        MDBX_envinfo info{};
        checked(mdbx_env_info_ex(env(), nullptr, &info, sizeof(info)), "inspect actual geometry");
        cd::DurableOptions result(context, conversation().allocation_namespace, quotas(),
            {info.mi_geo.upper, info.mi_geo.grow});
        result.allow_initialize = initialize; result.other_profile_peak_dbis = host_dbis;
        return result;
    }
    Snapshot snapshot() const {
        Snapshot result;
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        MDBX_dbi main = 0;
        checked(mdbx_dbi_open(txn.handle(), nullptr, MDBX_DB_DEFAULTS, &main), "open main table");
        std::vector<std::string> names;
        {
            Cursor cursor(txn.handle(), main);
            MDBX_val key{}, value{};
            auto code = mdbx_cursor_get(cursor.handle(), &key, &value, MDBX_FIRST);
            while(code == MDBX_SUCCESS) {
                // The main table contains textual DBI names in this fixture.
                names.emplace_back(static_cast<const char*>(key.iov_base), key.iov_len);
                code = mdbx_cursor_get(cursor.handle(), &key, &value, MDBX_NEXT);
            }
            expect(code == MDBX_NOTFOUND, "Main-table snapshot failed");
        }
        for(const auto& name : names) {
            MDBX_dbi dbi = 0;
            checked(mdbx_dbi_open(txn.handle(), name.c_str(), MDBX_DB_DEFAULTS, &dbi), "open snapshot table");
            unsigned flags = 0;
            checked(mdbx_dbi_flags(txn.handle(), dbi, &flags), "snapshot table flags");
            result.push_back({name, flags, {}, {}});
            Cursor cursor(txn.handle(), dbi);
            MDBX_val key{}, value{};
            auto code = mdbx_cursor_get(cursor.handle(), &key, &value, MDBX_FIRST);
            while(code == MDBX_SUCCESS) {
                result.push_back({name, flags, copy(key), copy(value)});
                code = mdbx_cursor_get(cursor.handle(), &key, &value, MDBX_NEXT);
            }
            expect(code == MDBX_NOTFOUND, "Table snapshot failed");
        }
        txn.commit();
        return result;
    }
};

am::ConversationWriteResult populate(Fixture& fixture) {
    cd::MdbxConversationDurable store(fixture.options(true));
    const auto created = store.create_conversation(access(), conversation());
    expect(created.status == am::ConversationWriteStatus::Applied && created.commit_generation == 1,
        "Native fixture create failed");
    const auto admitted = store.import_observation(access(), observation("baseline"));
    expect(admitted.status == am::ConversationWriteStatus::Applied && admitted.ref &&
        admitted.ref->entry.value() == 1 && admitted.commit_generation == 2, "Native fixture import failed");
    return admitted;
}
void baseline_survives(cd::MdbxConversationDurable& store, const am::ConversationWriteResult& original) {
    const auto replay = store.import_observation(access(), observation("baseline"));
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == original.ref &&
        replay.commit_generation == original.commit_generation, "Original replay changed after native failure");
    const auto read = store.read_exact(access(), *original.ref);
    expect(read.status == am::ConversationReadStatus::Complete && read.records.size() == 1,
        "Original history lost after native failure");
}

void map_full_rolls_back_and_retries_after_explicit_growth() {
    Fixture fixture("map_full", kMiB);
    const auto original = populate(fixture);
    const auto before = fixture.snapshot();
    const auto candidate = observation("map_full_candidate", 600U * 1024U);
    {
        cd::MdbxConversationDurable store(fixture.options());
        backend_failure([&] { (void)store.import_observation(access(), candidate); }, MDBX_MAP_FULL,
            "Full map did not propagate MDBX_MAP_FULL");
        expect(fixture.snapshot() == before, "Map-full write left partial rows/counters");
        baseline_survives(store, original);
    }
    fixture.reopen();
    expect(fixture.snapshot() == before, "Map-full rollback changed after fresh environment reopen");
    {
        cd::MdbxConversationDurable store(fixture.options());
        backend_failure([&] { (void)store.import_observation(access(), candidate); }, MDBX_MAP_FULL,
            "Unchanged full-map policy unexpectedly admitted retry");
    }
    // Only the host changes geometry, while no store/transaction is alive.
    fixture.context.reset(); fixture.map_bytes = kNormalMapBytes; fixture.open();
    expect(fixture.snapshot() == before, "Explicit map growth rewrote journal bytes");
    cd::MdbxConversationDurable store(fixture.options()); baseline_survives(store, original);
    const auto applied = store.import_observation(access(), candidate);
    expect(applied.status == am::ConversationWriteStatus::Applied && applied.ref &&
        applied.ref->entry.value() == 2 && applied.commit_generation == 3, "Map-full retry consumed an ID/generation");
    const auto replay = store.import_observation(access(), candidate);
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == applied.ref &&
        replay.commit_generation == applied.commit_generation, "Map-full retry lost durable idempotence");
}

// Raw readers belong to their worker threads; the connection lifecycle stays
// stable until every worker has aborted its transaction and joined.
class ReaderPressure final {
public:
    explicit ReaderPressure(MDBX_env* env) : m_env(env) {}
    ~ReaderPressure() {
        { std::lock_guard<std::mutex> lock(m_mutex); m_is_released = true; }
        m_changed.notify_all();
        for(auto& worker : m_workers) worker.join();
    }
    ReaderPressure(const ReaderPressure&) = delete;
    ReaderPressure& operator=(const ReaderPressure&) = delete;
    void fill(unsigned limit) {
        expect(limit > 0 && limit <= 256, "Unexpected reader-slot count for bounded failure fixture");
        m_workers.reserve(static_cast<std::size_t>(limit) + 1);
        for(unsigned index = 0; index <= limit; ++index) {
            m_workers.emplace_back([this] {
                MDBX_txn* txn = nullptr;
                const auto code = mdbx_txn_begin(m_env, nullptr, MDBX_TXN_RDONLY, &txn);
                std::unique_lock<std::mutex> lock(m_mutex);
                m_last_code = code; ++m_started; m_changed.notify_all();
                if(code == MDBX_SUCCESS) m_changed.wait(lock, [this] { return m_is_released; });
                lock.unlock();
                if(txn) static_cast<void>(mdbx_txn_abort(txn));
            });
            std::unique_lock<std::mutex> lock(m_mutex);
            expect(m_changed.wait_for(lock, std::chrono::seconds(10), [&] { return m_started > index; }),
                "Reader-slot fixture startup timed out");
            if(m_last_code != MDBX_SUCCESS) {
                expect(m_last_code == MDBX_READERS_FULL, "Reader pressure failed for a different native reason");
                std::printf("OBSERVED native reader-slot exhaustion after %u active readers\n", index);
                return;
            }
        }
        throw std::runtime_error("Reader slots did not exhaust at the actual environment limit");
    }
private:
    MDBX_env* m_env;
    std::mutex m_mutex;
    std::condition_variable m_changed;
    std::vector<std::thread> m_workers;
    unsigned m_started = 0;
    int m_last_code = MDBX_SUCCESS;
    bool m_is_released = false;
};

void readers_full_is_a_transaction_start_failure() {
    Fixture fixture("readers_full"); const auto original = populate(fixture);
    const auto before = fixture.snapshot();
    {
        const auto read_options = fixture.options();
        cd::MdbxConversationDurable store(read_options);
        // Sticky TLS slots would let the calling thread reuse its old slot and
        // conceal exhaustion. Release it before independent native readers fill.
        const auto unregistered = mdbx_thread_unregister(fixture.env());
        expect(unregistered == MDBX_SUCCESS || unregistered == MDBX_RESULT_TRUE, "Release main reader slot failed");
        unsigned maximum = 0;
        checked(mdbx_env_get_maxreaders(fixture.env(), &maximum), "inspect reader-slot limit");
        {
            ReaderPressure pressure(fixture.env()); pressure.fill(maximum);
            backend_failure([&] { (void)store.read_exact(access(), *original.ref); }, MDBX_READERS_FULL,
                "Exact read concealed reader-slot transaction-start failure");
            backend_failure([&] { (void)store.read_history(access(), conversation()); }, MDBX_READERS_FULL,
                "History read concealed reader-slot transaction-start failure");
            backend_failure([&] { cd::MdbxConversationDurable reopened(read_options); }, MDBX_READERS_FULL,
                "Read-only profile open concealed reader-slot failure");
        }
        expect(fixture.snapshot() == before, "Reader-slot refusal changed rows/counters");
    }
    fixture.reopen();
    expect(fixture.snapshot() == before, "Reader-slot refusal changed fresh reopen state");
    cd::MdbxConversationDurable store(fixture.options()); baseline_survives(store, original);
    const auto next = store.import_observation(access(), observation("after_readers_full"));
    expect(next.status == am::ConversationWriteStatus::Applied && next.ref && next.ref->entry.value() == 2 &&
        next.commit_generation == 3, "Reader-slot failure consumed allocator/generation");
}

void dbi_exhaustion_and_reserved_capacity_refusal_are_distinct() {
    Fixture fixture("dbis_full", kNormalMapBytes, 19); const auto original = populate(fixture);
    const auto before = fixture.snapshot();
    MDBX_dbi maximum = 0;
    checked(mdbx_env_get_maxdbs(fixture.env(), &maximum), "inspect DBI capacity");
    expect(maximum == 19, "Unexpected actual DBI capacity");
    {
        auto txn = fixture.context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        int code = MDBX_SUCCESS;
        unsigned count = 0;
        for(; count <= maximum; ++count) {
            MDBX_dbi dbi = 0; const auto name = "native_capacity_" + std::to_string(count);
            code = mdbx_dbi_open(txn.handle(), name.c_str(), MDBX_CREATE, &dbi);
            if(code != MDBX_SUCCESS) break;
        }
        expect(code == MDBX_DBS_FULL && count == maximum - 3,
            "Native DBI exhaustion did not return MDBX_DBS_FULL at capacity");
        std::printf("OBSERVED native DBI exhaustion after %u tentative host tables\n", count);
        // No commit: every tentative DBI must disappear with this transaction.
    }
    expect(fixture.snapshot() == before, "Native DBI exhaustion left tentative tables");
    fixture.reopen();
    expect(fixture.snapshot() == before, "Aborted DBIs appeared after fresh reopen");
    {
        auto txn = fixture.context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        for(unsigned index = 0; index < maximum - 3; ++index) {
            MDBX_dbi dbi = 0; const auto name = "host_capacity_" + std::to_string(index);
            checked(mdbx_dbi_open(txn.handle(), name.c_str(), MDBX_CREATE, &dbi), "fill host DBI capacity");
        }
        txn.commit();
    }
    const auto full = fixture.snapshot();
    unsupported([&] { cd::MdbxConversationDurable store(fixture.options(false, maximum - 3)); },
        "Full composition failed to preserve the mandatory sixteen-slot reserve");
    {
        auto initializing = fixture.options(true, maximum); initializing.table_prefix = "another";
        // A missing table also needs a DBI handle during open, before the
        // adapter can inspect composition/reserves. This is a native failure,
        // unlike validation of the already-open existing profile above.
        backend_failure([&] { cd::MdbxConversationDurable store(initializing); }, MDBX_DBS_FULL,
            "Missing profile open concealed native DBI-handle exhaustion");
    }
    expect(fixture.snapshot() == full, "Capacity validation changed journal/host tables");
    fixture.context.reset(); fixture.max_dbs = 64; fixture.open();
    expect(fixture.snapshot() == full, "Explicit capacity increase rewrote tables");
    cd::MdbxConversationDurable store(fixture.options(false, maximum - 3)); baseline_survives(store, original);
    const auto next = store.import_observation(access(), observation("after_dbis_full"));
    expect(next.status == am::ConversationWriteStatus::Applied && next.ref && next.ref->entry.value() == 2 &&
        next.commit_generation == 3, "DBI failure consumed allocator/generation");
}

void actual_flags_geometry_and_profile_fail_closed() {
    Fixture fixture("fail_closed"); const auto original = populate(fixture);
    const auto before = fixture.snapshot();
    {
        cd::MdbxConversationDurable store(fixture.options());
        for(const auto flag : {MDBX_NOMETASYNC, MDBX_SAFE_NOSYNC}) {
            checked(mdbx_env_set_flags(fixture.env(), flag, true), "enable actual unsafe sync flag");
            unsigned actual = 0; checked(mdbx_env_get_flags(fixture.env(), &actual), "inspect actual sync flags");
            expect((actual & flag) != 0, "Unsafe flag fixture did not change the actual environment");
            unsupported([&] { (void)store.import_observation(access(), observation("unsafe")); },
                "Actual unsafe sync flag accepted a journal write");
            unsupported([&] { (void)store.read_exact(access(), *original.ref); },
                "Actual unsafe sync flag accepted a journal read");
            unsupported([&] { cd::MdbxConversationDurable opened(fixture.options(true)); },
                "Actual unsafe sync flag accepted profile open");
            checked(mdbx_env_set_flags(fixture.env(), flag, false), "restore actual durable sync flags");
            expect(fixture.snapshot() == before, "Unsafe-flag validation mutated tables");
        }
        checked(mdbx_env_set_geometry(fixture.env(), -1, -1, -1, 2U * kGrowthBytes, -1, -1),
            "host changes actual growth policy");
        unsupported([&] { (void)store.import_observation(access(), observation("wrong_geometry")); },
            "Changed actual geometry accepted by existing store");
        unsupported([&] { (void)store.read_history(access(), conversation()); },
            "Changed actual geometry accepted by existing reader");
        expect(fixture.snapshot() == before, "Geometry validation mutated journal rows");
    }
    fixture.reopen();
    expect(fixture.snapshot() == before, "Validation refusal changed fresh reopen state");
    auto mismatch = fixture.options(); mismatch.descriptor.bucket_rows += 1;
    unsupported([&] { cd::MdbxConversationDurable store(mismatch); }, "Immutable descriptor mismatch accepted");
    mismatch = fixture.options(); mismatch.quotas.entries += 1;
    unsupported([&] { cd::MdbxConversationDurable store(mismatch); }, "Persisted quota mismatch accepted");
    mismatch = fixture.options(); mismatch.map_policy.upper_bytes += kMiB;
    unsupported([&] { cd::MdbxConversationDurable store(mismatch); }, "Declared/actual map mismatch accepted");
    expect(fixture.snapshot() == before, "Profile mismatch rewrote persisted state");
    {
        cd::MdbxConversationDurable store(fixture.options()); baseline_survives(store, original);
        const auto next = store.import_observation(access(), observation("after_validation"));
        expect(next.status == am::ConversationWriteStatus::Applied && next.ref && next.ref->entry.value() == 2 &&
            next.commit_generation == 3, "Validation refusal consumed allocator/generation");
    }
    Fixture writemap("writemap"); writemap.context.reset(); writemap.use_writemap = true; writemap.open();
    const auto empty = writemap.snapshot(); expect(empty.empty(), "Writemap fixture is not empty");
    unsupported([&] { cd::MdbxConversationDurable store(writemap.options(true)); },
        "Actual WRITEMAP environment accepted profile initialization");
    expect(writemap.snapshot() == empty, "WRITEMAP refusal created partial tables");
    writemap.context.reset(); writemap.use_writemap = false; writemap.open();
    cd::MdbxConversationDurable valid(writemap.options(true));
    expect(valid.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied,
        "Fresh durable reopen failed after WRITEMAP refusal");
}
} // namespace

int main() {
    try {
        map_full_rolls_back_and_retries_after_explicit_growth();
        readers_full_is_a_transaction_start_failure();
        dbi_exhaustion_and_reserved_capacity_refusal_are_distinct();
        actual_flags_geometry_and_profile_fail_closed();
        std::puts("PASS native map-full/readers-full/DBI failures and separate fail-closed environment/profile validation");
        std::puts("NOT RUN real device I/O, native commit/device failures or power-loss qualification");
        return 0;
    } catch(const std::exception& error) {
        std::fprintf(stderr, "durable native failures: %s\n", error.what());
        return 1;
    }
}
