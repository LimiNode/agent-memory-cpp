#include <agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.hpp>
#include <agent_memory/ingestion/ConversationSourceImporterInternal.hpp>
#include <mdbx_containers/common.hpp>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <type_traits>
#include <vector>
#if defined(_WIN32)
#include <malloc.h>
#endif

namespace {
// Isolated replacement functions, like support/allocation_failure.hpp.
// Request sizes measure newly allocated C++ buffers, not MDBX malloc/mmap,
// allocator metadata, process RSS, or preexisting fixture storage. Cumulative
// requested bytes are a conservative live-byte bound, not a peak measurement.
struct AllocationGate final {
    bool is_enabled = false;
    bool has_failed = false;
    std::size_t calls = 0, fail_at = 0;
    std::size_t largest = 0, total_bytes = 0;
};
thread_local AllocationGate g_gate;
void charge_allocation(std::size_t requested) {
    if(g_gate.is_enabled && ++g_gate.calls == g_gate.fail_at) {
        g_gate.has_failed = true;
        g_gate.is_enabled = false;
        throw std::bad_alloc{};
    }
    if(g_gate.is_enabled) {
        if(requested > std::numeric_limits<std::size_t>::max() - g_gate.total_bytes) throw std::bad_alloc{};
        g_gate.largest = std::max(g_gate.largest, requested);
        g_gate.total_bytes += requested;
    }
}
void* allocate(std::size_t requested) {
    charge_allocation(requested);
    if(auto* result = std::malloc(requested ? requested : 1)) return result;
    throw std::bad_alloc{};
}
void* allocate_aligned(std::size_t requested, std::size_t alignment) {
    charge_allocation(requested);
    if(requested > std::numeric_limits<std::size_t>::max() - (alignment - 1)) throw std::bad_alloc{};
    const auto rounded = requested ? ((requested + alignment - 1) / alignment) * alignment : alignment;
#if defined(_WIN32)
    if(auto* result = _aligned_malloc(rounded, alignment)) return result;
#else
    void* result = nullptr;
    if(posix_memalign(&result, alignment, rounded) == 0) return result;
#endif
    throw std::bad_alloc{};
}
void release_aligned(void* pointer) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}
struct ScopedGate final {
    explicit ScopedGate(std::size_t ordinal) {
        g_gate = {true, false, 0, ordinal, 0, 0};
    }
    ~ScopedGate() { g_gate.is_enabled = false; }
};
} // namespace

void* operator new(std::size_t size) { return allocate(size); }
void* operator new[](std::size_t size) { return allocate(size); }
void* operator new(std::size_t size, const std::nothrow_t&) noexcept {
    try { return allocate(size); } catch(const std::bad_alloc&) { return nullptr; }
}
void* operator new[](std::size_t size, const std::nothrow_t&) noexcept {
    try { return allocate(size); } catch(const std::bad_alloc&) { return nullptr; }
}
void* operator new(std::size_t size, std::align_val_t alignment) {
    return allocate_aligned(size, static_cast<std::size_t>(alignment));
}
void* operator new[](std::size_t size, std::align_val_t alignment) {
    return allocate_aligned(size, static_cast<std::size_t>(alignment));
}
void* operator new(std::size_t size, std::align_val_t alignment, const std::nothrow_t&) noexcept {
    try { return allocate_aligned(size, static_cast<std::size_t>(alignment)); } catch(const std::bad_alloc&) { return nullptr; }
}
void* operator new[](std::size_t size, std::align_val_t alignment, const std::nothrow_t&) noexcept {
    try { return allocate_aligned(size, static_cast<std::size_t>(alignment)); } catch(const std::bad_alloc&) { return nullptr; }
}
void operator delete(void* pointer) noexcept { std::free(pointer); }
void operator delete[](void* pointer) noexcept { std::free(pointer); }
void operator delete(void* pointer, std::size_t) noexcept { std::free(pointer); }
void operator delete[](void* pointer, std::size_t) noexcept { std::free(pointer); }
void operator delete(void* pointer, const std::nothrow_t&) noexcept { std::free(pointer); }
void operator delete[](void* pointer, const std::nothrow_t&) noexcept { std::free(pointer); }
void operator delete(void* pointer, std::align_val_t) noexcept { release_aligned(pointer); }
void operator delete[](void* pointer, std::align_val_t) noexcept { release_aligned(pointer); }
void operator delete(void* pointer, std::size_t, std::align_val_t) noexcept { release_aligned(pointer); }
void operator delete[](void* pointer, std::size_t, std::align_val_t) noexcept { release_aligned(pointer); }
void operator delete(void* pointer, std::align_val_t, const std::nothrow_t&) noexcept { release_aligned(pointer); }
void operator delete[](void* pointer, std::align_val_t, const std::nothrow_t&) noexcept { release_aligned(pointer); }

namespace am = agent_memory;
namespace cd = agent_memory::detail::conversation_durable;
namespace {
constexpr const char* kTables[] = {"agent_memory_conversation_meta_v1", "agent_memory_conversation_events_v1",
    "agent_memory_conversation_sources_v1"};
void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
void checked(int code) {
    if(code != MDBX_SUCCESS) throw std::runtime_error(mdbx_strerror(code));
}
am::ByteView view(const am::ByteBuffer& value) { return {value.data(), value.size()}; }
am::ByteBuffer copy(const MDBX_val& value) {
    const auto* first = static_cast<const std::uint8_t*>(value.iov_base);
    return value.iov_len ? am::ByteBuffer{first, first + value.iov_len} : am::ByteBuffer{};
}
struct RawRow final {
    std::size_t table;
    unsigned flags;
    am::ByteBuffer key, value;
    bool operator==(const RawRow& other) const {
        return table == other.table && flags == other.flags && key == other.key && value == other.value;
    }
};
using Snapshot = std::vector<RawRow>;
Snapshot snapshot(const std::shared_ptr<am::MdbxStorageContext>& context) {
    Snapshot result;
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    for(std::size_t table = 0; table < 3; ++table) {
        MDBX_dbi dbi{};
        checked(mdbx_dbi_open(transaction.handle(), kTables[table], MDBX_DB_DEFAULTS, &dbi));
        unsigned flags = 0;
        checked(mdbx_dbi_flags(transaction.handle(), dbi, &flags));
        result.push_back({table, flags, {}, {}});
        MDBX_cursor* cursor = nullptr;
        checked(mdbx_cursor_open(transaction.handle(), dbi, &cursor));
        MDBX_val key{}, value{};
        int code = mdbx_cursor_get(cursor, &key, &value, MDBX_FIRST);
        try {
            while(code == MDBX_SUCCESS) {
                result.push_back({table, flags, copy(key), copy(value)});
                code = mdbx_cursor_get(cursor, &key, &value, MDBX_NEXT);
            }
        } catch(...) { mdbx_cursor_close(cursor); throw; }
        mdbx_cursor_close(cursor);
        expect(code == MDBX_NOTFOUND, "Raw snapshot cursor failure");
    }
    transaction.commit();
    return result;
}

am::ConversationRef conversation(std::uint64_t id = 1) {
    return {std::string(80, 'n'), am::ConversationId{id}};
}
am::ConversationStoreAccess access() {
    return {am::ScopeId{std::string(64, 's')}, std::string(64, 'p')};
}
am::ExternalObjectRef external(std::uint64_t id) {
    const auto key = [id] {
        std::vector<am::ExternalIdentityComponent> parts;
        parts.push_back({"signed", std::int64_t{-9}});
        parts.push_back({"unsigned", id});
        parts.push_back({std::string(48, 'k'), std::string(160, 'v')});
        return am::ExternalObjectKey{std::move(parts)};
    };
    return {"fixture", std::string(48, 'i'), "run-a", "event", key(), key()};
}
am::ConversationSourceObservation observation(std::uint64_t id) {
    am::ConversationSourceObservation result;
    result.conversation = conversation(); result.scope = access().scope;
    result.source.identity = am::ConversationSourceIdentity::ExternalEvent;
    result.source.object = external(id); result.source.event = external(id);
    result.source.revision = am::SourceRevisionId{std::string(48, 'r')};
    result.source.adapter_recipe = "durable-allocation-fixture-v1";
    result.source.evidence = {am::ArtifactBindingId{std::string(48, 'a')}, "records/" + std::to_string(id)};
    result.producer = std::string(48, 'p');
    result.actor = am::ActorRef{std::string(64, 'n'), am::ActorId{std::uint64_t{1}}};
    result.observer = am::ActorRef{std::string(64, 'o'), am::ActorId{std::uint64_t{2}}};
    result.payload = am::ConversationSourceToolCall{{0, 255, 1}, std::string(48, 't'),
        am::ArtifactBindingId{std::string(48, 'q')}, am::ArtifactBindingId{std::string(48, 'e')}};
    return result;
}

enum class Operation { Create, CompositeImport };
struct CommitObservation final {
    bool has_seen_before_commit = false;
    std::size_t allocations_before_commit = 0;
};
thread_local CommitObservation* g_commit = nullptr;
void observe_commit(cd::WriteStep step) {
    if(g_commit && step == cd::WriteStep::BeforeCommit) {
        g_commit->has_seen_before_commit = true;
        g_commit->allocations_before_commit = g_gate.calls;
    }
}

struct Fixture final {
    const am::ConversationStoreAccess authority = access();
    const am::ConversationRef stream = conversation(), another = conversation(2);
    am::ConversationSourceObservation baseline = observation(1), candidate = observation(2);
    const am::ByteBuffer candidate_before = cd::encode_witness(candidate);
    std::filesystem::path path;
    std::shared_ptr<am::MdbxStorageContext> context;
    std::unique_ptr<cd::MdbxConversationDurable> store;
    am::ConversationWriteResult baseline_receipt, result;
    Snapshot before;
    CommitObservation commit;
    Fixture() {
        static std::uint64_t serial = 0;
        path = std::filesystem::temp_directory_path() / ("agent_memory_durable_bad_alloc_" +
            std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "_" +
            std::to_string(++serial) + ".mdbx");
        open(true);
        expect(store->create_conversation(authority, stream).status == am::ConversationWriteStatus::Applied,
            "Baseline create failed");
        baseline_receipt = store->import_observation(authority, baseline);
        expect(baseline_receipt.status == am::ConversationWriteStatus::Applied &&
            baseline_receipt.ref->entry.value() == 1 && baseline_receipt.commit_generation == 2, "Baseline import failed");
        before = snapshot(context);
    }
    ~Fixture() {
        store.reset(); context.reset();
        std::error_code error;
        // Exact fixture-owned files only; never recursively remove a shared temp path.
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
        cd::DurableOptions options(context, stream.allocation_namespace,
            {2, 2, 1, 16U * 1024U * 1024U, 2, 16U * 1024U * 1024U}, {info.mi_geo.upper, info.mi_geo.grow});
        options.allow_initialize = initialize;
        store = std::make_unique<cd::MdbxConversationDurable>(std::move(options), cd::QualificationHooks{false, observe_commit});
    }
    void reopen() { store.reset(); context.reset(); open(false); }
    void attempt(Operation operation) {
        commit = {};
        g_commit = &commit;
        try {
            if(operation == Operation::Create) result = store->create_conversation(authority, another);
            else result = store->import_observation(authority, candidate);
        } catch(...) { g_commit = nullptr; throw; }
        g_commit = nullptr;
    }
    Snapshot expected_after(Operation operation) const {
        // Derive the exact next physical state from the published row/charge
        // grammar, retaining this fixture's incarnation. No trial commit or
        // restoration is used to obtain the expected state.
        auto expected = before;
        const auto profile_key = cd::encode_profile_key();
        const auto original = std::find_if(before.begin(), before.end(), [&profile_key](const RawRow& row) {
            return row.table == 0 && row.key == profile_key;
        });
        expect(original != before.end(), "Baseline profile absent");
        auto profile = cd::decode_profile_value(view(original->value));
        const auto upsert = [&expected, &profile](std::size_t table, cd::TableKind kind,
            am::ByteBuffer key, const am::ByteBuffer& body) {
            auto value = cd::wrap_value(profile, kind, view(key), view(body));
            const auto found = std::find_if(expected.begin(), expected.end(), [table, &key](const RawRow& row) {
                return row.table == table && row.key == key;
            });
            if(found == expected.end()) expected.push_back({table, 0, std::move(key), std::move(value)});
            else found->value = std::move(value);
        };
        ++profile.generation;
        if(operation == Operation::Create) {
            ++profile.conversation_count;
            profile.canonical_charge += authority.scope.value().size();
            upsert(0, cd::TableKind::Metadata, cd::encode_conversation_key(another.id),
                cd::encode_conversation_row({authority.scope, profile.generation, 1}));
            upsert(1, cd::TableKind::Events, cd::encode_generation_key(profile.generation),
                cd::encode_generation_row({cd::GenerationKind::Create, another, std::nullopt}));
        } else {
            const am::EntryRef ref{stream, am::EntryId{std::uint64_t{2}}, am::EventRevision{std::uint64_t{1}}};
            auto envelope = am::detail::normalize_observation(candidate, ref);
            auto frame = am::serialize_conversation_envelope(envelope, profile.descriptor.codec);
            const auto preflight = am::detail::preflight_observation(candidate, profile.descriptor.codec.fields,
                static_cast<std::size_t>(profile.descriptor.max_observation_bytes));
            expect(preflight.status == am::ConversationWriteStatus::Applied, "Candidate preflight failed");
            const auto logical = am::detail::source_key(candidate.source);
            ++profile.entry_count; ++profile.revision_count; ++profile.mapping_count;
            profile.canonical_charge += frame.size();
            profile.mapping_charge += preflight.bytes + logical.charge() + 32 + stream.allocation_namespace.size();
            upsert(1, cd::TableKind::Events, cd::encode_revision_key({stream.id, ref.entry, ref.revision}),
                cd::encode_revision_row({profile.generation, std::nullopt, std::move(frame)}));
            auto full_key = cd::encode_logical_source_key(candidate.source);
            const auto physical_source = cd::encode_source_key({stream.id, cd::source_digest(view(full_key)), 0});
            upsert(2, cd::TableKind::Sources, physical_source,
                cd::encode_source_row({std::move(full_key), cd::encode_witness(candidate), ref, profile.generation}));
            upsert(1, cd::TableKind::Events, cd::encode_generation_key(profile.generation),
                cd::encode_generation_row({cd::GenerationKind::Import, stream, std::make_pair(ref.entry, ref.revision)}));
            upsert(0, cd::TableKind::Metadata, cd::encode_conversation_key(stream.id),
                cd::encode_conversation_row({authority.scope, 1, 3}));
        }
        upsert(0, cd::TableKind::Metadata, profile_key, cd::encode_profile(profile));
        std::sort(expected.begin(), expected.end(), [](const RawRow& a, const RawRow& b) {
            return a.table != b.table ? a.table < b.table : a.key < b.key;
        });
        return expected;
    }
    void verify_unchanged(Operation operation) {
        expect(snapshot(context) == before, "bad_alloc changed raw rows/flags/profile/counters/generation");
        expect(cd::encode_witness(candidate) == candidate_before, "bad_alloc changed original composite witness");
        const auto history = store->read_history(authority, stream);
        expect(history.status == am::ConversationReadStatus::Complete && history.commit_generation == 2 &&
            history.records.size() == 1 && history.records.front().envelope.metadata.ref == *baseline_receipt.ref,
            "bad_alloc changed canonical history");
        const auto replay = store->import_observation(authority, baseline);
        expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == baseline_receipt.ref &&
            replay.commit_generation == baseline_receipt.commit_generation, "bad_alloc damaged original replay");
        const am::EntryRef tentative{stream, am::EntryId{std::uint64_t{2}}, am::EventRevision{std::uint64_t{1}}};
        expect(store->read_exact(authority, tentative).status == am::ConversationReadStatus::NotFound,
            "bad_alloc retained tentative revision");
        if(operation == Operation::Create)
            expect(store->read_history(authority, another).status == am::ConversationReadStatus::NotFound,
                "bad_alloc retained tentative conversation");
        reopen();
        expect(snapshot(context) == before, "Reopen found partial durable admission after bad_alloc");
    }
    bool verify_commit_boundary(const Snapshot& after) {
        expect(result.status != am::ConversationWriteStatus::Applied && !result.ref && !result.commit_generation,
            "Commit exception exposed speculative Applied/receipt");
        expect(cd::encode_witness(candidate) == candidate_before, "Commit exception changed original witness");
        const auto actual = snapshot(context);
        const bool has_committed = actual == after;
        expect(has_committed || actual == before, "Commit exception left partial rows/counters/profile");
        reopen();
        expect(snapshot(context) == actual, "Reopen changed unresolved commit outcome");
        const auto replay = store->import_observation(authority, baseline);
        expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == baseline_receipt.ref &&
            replay.commit_generation == baseline_receipt.commit_generation, "Commit exception damaged original replay");
        return has_committed;
    }
    void verify_success(Operation operation, am::ConversationWriteStatus expected_status = am::ConversationWriteStatus::Applied) {
        expect(result.status == expected_status && result.commit_generation == 3,
            "Successful retry consumed generation or refused admission");
        if(operation == Operation::Create) {
            expect(!result.ref && store->read_history(authority, another).status == am::ConversationReadStatus::Complete,
                "Create receipt/history mismatch");
            const auto next = store->import_observation(authority, candidate);
            expect(next.status == am::ConversationWriteStatus::Applied && next.ref->entry.value() == 2 &&
                next.commit_generation == 4, "Create failure consumed import allocator");
        } else {
            expect(result.ref && result.ref->entry.value() == 2 && result.ref->revision.value() == 1,
                "Import retry consumed entry identity");
            const auto replay = store->import_observation(authority, candidate);
            expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == result.ref &&
                replay.commit_generation == result.commit_generation, "Import retry left orphan or duplicate mapping");
        }
        const auto history = store->read_history(authority, stream);
        expect(history.status == am::ConversationReadStatus::Complete && history.records.size() == 2,
            "Committed candidate history absent");
        auto changed = observation(2);
        changed.producer = "changed-original-witness";
        const auto unchanged = snapshot(context);
        const auto conflict = store->import_observation(authority, changed);
        expect(conflict.status == am::ConversationWriteStatus::Conflict && !conflict.ref &&
            !conflict.commit_generation && snapshot(context) == unchanged,
            "Changed witness after retry must conflict without mutation or receipt");
        const auto canonical = am::serialize_conversation_envelope(history.records.back().envelope);
        store.reset(); context.reset();
        expect(am::serialize_conversation_envelope(history.records.back().envelope) == canonical,
            "Owning history result retained transaction/environment views");
        if(operation == Operation::CompositeImport)
            expect(result.ref->conversation.allocation_namespace == stream.allocation_namespace,
                "Owning import receipt namespace did not survive environment close");
    }
};

void run_matrix(Operation operation) {
    std::size_t count = 0, precommit_count = 0, largest = 0, total_bytes = 0;
    {
        Fixture fixture;
        { ScopedGate gate(0); fixture.attempt(operation); }
        count = g_gate.calls; largest = g_gate.largest; total_bytes = g_gate.total_bytes;
        expect(!g_gate.has_failed && count > 0 && count < 10000, "Invalid allocation probe");
        expect(fixture.commit.has_seen_before_commit && fixture.commit.allocations_before_commit <= count,
            "Successful operation did not cross its observed commit boundary");
        precommit_count = fixture.commit.allocations_before_commit;
        expect(snapshot(fixture.context) == fixture.expected_after(operation), "Successful physical state differs from row grammar");
        fixture.verify_success(operation);
    }
    const bool trace = std::getenv("AGENT_MEMORY_TRACE_ALLOCATIONS") != nullptr;
    std::size_t boundary_committed = 0, boundary_rolled_back = 0;
    for(std::size_t ordinal = 1; ordinal <= count; ++ordinal) {
        Fixture fixture;
        const auto after = fixture.expected_after(operation);
        if(trace) std::fprintf(stderr, "durable operation %d ordinal %zu/%zu\n", static_cast<int>(operation), ordinal, count);
        bool has_thrown = false;
        try { ScopedGate gate(ordinal); fixture.attempt(operation); }
        catch(const std::bad_alloc&) { has_thrown = true; }
        expect(has_thrown && g_gate.has_failed && g_gate.calls == ordinal, "Every ordinal must propagate actual bad_alloc");
        if(ordinal <= precommit_count) {
            expect(!fixture.commit.has_seen_before_commit, "Precommit ordinal crossed native commit boundary");
            fixture.verify_unchanged(operation);
            fixture.attempt(operation); fixture.verify_success(operation);
        } else {
            expect(fixture.commit.has_seen_before_commit && fixture.commit.allocations_before_commit == precommit_count,
                "Commit-boundary allocation classification drift");
            // The pinned transaction wrapper may allocate after native commit.
            // An exception is not a rollback claim: exact fresh-reopen state
            // must be either the complete before state or complete after state.
            const bool has_committed = fixture.verify_commit_boundary(after);
            if(has_committed) ++boundary_committed; else ++boundary_rolled_back;
            fixture.attempt(operation);
            expect(snapshot(fixture.context) == after, "Retry did not converge to one complete admission");
            fixture.verify_success(operation, has_committed ? am::ConversationWriteStatus::NoChange :
                am::ConversationWriteStatus::Applied);
        }
    }
    {
        Fixture fixture;
        { ScopedGate gate(count + 1); fixture.attempt(operation); }
        expect(!g_gate.has_failed && g_gate.calls == count && fixture.commit.has_seen_before_commit &&
            fixture.commit.allocations_before_commit == precommit_count, "Successful exact allocation count drift");
        fixture.verify_success(operation);
    }
    std::printf("PASS durable operation %d: %zu actual bad_alloc failures; precommit-rollback=%zu, "
        "commit-boundary=%zu (committed=%zu, rolled-back=%zu); success allocations=%zu, "
        "largest=%zu bytes, cumulative-new-C++-bytes=%zu\n", static_cast<int>(operation), count, precommit_count,
        count - precommit_count, boundary_committed, boundary_rolled_back, count, largest, total_bytes);
    std::fflush(stdout);
}
} // namespace

int main() {
#if defined(_MSC_VER) && _ITERATOR_DEBUG_LEVEL > 0
    std::puts("SKIP durable allocation matrix: MSVC checked STL allocates inside noexcept construction");
    return 77;
#else
    try {
        static_assert(std::is_nothrow_move_constructible_v<am::ConversationWriteResult>);
        run_matrix(Operation::Create); run_matrix(Operation::CompositeImport);
        std::puts("PASS durable allocation atomicity; no malloc/OS/crash/power-loss/RSS guarantee");
        return 0;
    } catch(const std::exception& error) {
        g_gate.is_enabled = false; g_commit = nullptr;
        std::fprintf(stderr, "durable allocation qualification: %s\n", error.what()); return 1;
    }
#endif
}
