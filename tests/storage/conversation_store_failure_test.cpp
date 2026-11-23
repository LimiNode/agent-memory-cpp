#include <agent_memory/storage/ConversationStore.hpp>

#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <new>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#if defined(_WIN32)
#include <malloc.h>
#endif

namespace {
// This executable is single-threaded. Arguments, fixtures and assertions are
// allocated outside the gate; only the attempted store operation is measured.
// Standard replacement allocation functions are used, not allocator detours.
struct AllocationGate final {
    bool is_enabled = false;
    bool has_failed = false;
    std::size_t calls = 0;
    std::size_t fail_at = 0;
};
thread_local AllocationGate g_gate;

void check_allocation() {
    if(!g_gate.is_enabled) return;
    ++g_gate.calls;
    if(g_gate.fail_at != 0 && g_gate.calls == g_gate.fail_at) {
        g_gate.has_failed = true;
        // Fail once, so exception unwinding cannot encounter a second injected
        // failure. Assertions and state inspection never run with injection on.
        g_gate.is_enabled = false;
        throw std::bad_alloc{};
    }
}

void* allocate(std::size_t size) {
    check_allocation();
    if(auto* pointer = std::malloc(size == 0 ? 1 : size)) return pointer;
    throw std::bad_alloc{};
}

void* allocate_aligned(std::size_t size, std::size_t alignment) {
    check_allocation();
    if(size > std::numeric_limits<std::size_t>::max() - (alignment - 1)) throw std::bad_alloc{};
    const auto rounded = size == 0 ? alignment : ((size + alignment - 1) / alignment) * alignment;
#if defined(_WIN32)
    if(auto* pointer = _aligned_malloc(rounded, alignment)) return pointer;
#else
    void* pointer = nullptr;
    if(posix_memalign(&pointer, alignment, rounded) == 0) return pointer;
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

class ScopedAllocationGate final {
public:
    explicit ScopedAllocationGate(std::size_t fail_at) noexcept { g_gate = {true, false, 0, fail_at}; }
    ~ScopedAllocationGate() { g_gate.is_enabled = false; }
    ScopedAllocationGate(const ScopedAllocationGate&) = delete;
    ScopedAllocationGate& operator=(const ScopedAllocationGate&) = delete;
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
    try { return allocate_aligned(size, static_cast<std::size_t>(alignment)); }
    catch(const std::bad_alloc&) { return nullptr; }
}
void* operator new[](std::size_t size, std::align_val_t alignment, const std::nothrow_t&) noexcept {
    try { return allocate_aligned(size, static_cast<std::size_t>(alignment)); }
    catch(const std::bad_alloc&) { return nullptr; }
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
namespace {
void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

am::ConversationStoreAccess access() {
    return {am::ScopeId{std::string(64, 's')}, std::string(64, 'p')};
}

am::ConversationRef conversation(std::uint64_t id = 1) {
    return {std::string(80, 'a'), am::ConversationId{id}};
}

am::EntryRef ref(std::uint64_t entry, std::uint64_t revision = 1) {
    return {conversation(), am::EntryId{entry}, am::EventRevision{revision}};
}

am::ConversationEnvelope message(std::uint64_t entry, bool is_corrected = false) {
    am::ConversationEntry value;
    value.ref = ref(entry, is_corrected ? 2 : 1);
    value.scope = access().scope;
    am::ConversationSourceBinding source;
    source.revision = am::SourceRevisionId{std::string(48, 'r')};
    source.adapter_recipe = is_corrected ? "normalization-v2" : "normalization-v1";
    source.evidence = {am::ArtifactBindingId{std::string(48, 'b')}, "records/" + std::to_string(entry)};
    value.source = std::move(source);
    value.payload = am::ConversationMessageCreated{{std::string(160, is_corrected ? 'c' : 'x'),
        {am::ArtifactBindingId{std::string(48, 'i')}, am::ArtifactBindingId{std::string(48, 'j')}}}};
    auto envelope = am::make_conversation_envelope(std::move(value));
    envelope.extensions.push_back({65536, false, {0, 255, 0, 255}});
    return envelope;
}

am::ByteBuffer metadata_bytes(const am::ConversationMetadata& metadata) {
    am::ConversationEnvelope value;
    value.metadata = metadata;
    value.payload = am::ConversationPayload{am::ConversationMessageCreated{{std::string("metadata"), {}}}};
    return am::serialize_conversation_envelope(value);
}

void expect_same_result(const am::ConversationReadResult& actual, const am::ConversationReadResult& expected) {
    expect(actual.status == expected.status && actual.commit_generation == expected.commit_generation &&
        actual.visited_records == expected.visited_records && actual.decoded_bytes == expected.decoded_bytes &&
        actual.output_bytes == expected.output_bytes && actual.records.size() == expected.records.size() &&
        actual.messages.size() == expected.messages.size() &&
        actual.continuation.has_value() == expected.continuation.has_value(), "read result changed after failure");
    for(std::size_t i = 0; i < expected.records.size(); ++i) {
        const auto& a = actual.records[i];
        const auto& b = expected.records[i];
        expect(am::serialize_conversation_envelope(a.envelope) == am::serialize_conversation_envelope(b.envelope) &&
            a.commit_generation == b.commit_generation && a.correction.has_value() == b.correction.has_value(),
            "immutable history changed after failure");
        if(a.correction) expect(a.correction->previous_revision == b.correction->previous_revision &&
            a.correction->reason_code == b.correction->reason_code, "correction receipt changed after failure");
    }
    for(std::size_t i = 0; i < expected.messages.size(); ++i) {
        const auto& a = actual.messages[i];
        const auto& b = expected.messages[i];
        expect(a.origin == b.origin && a.state == b.state && a.content_ref == b.content_ref && a.status == b.status &&
            a.is_snapshot_origin == b.is_snapshot_origin && a.content.has_value() == b.content.has_value() &&
            metadata_bytes(a.origin_metadata) == metadata_bytes(b.origin_metadata), "current state changed after failure");
        if(a.content) expect(a.content->text == b.content->text && a.content->attachments == b.content->attachments,
            "current content changed after failure");
    }
}

enum class Operation { Create, Append, Correct, Exact, History, Current };

bool is_read(Operation operation) {
    return operation == Operation::Exact || operation == Operation::History || operation == Operation::Current;
}

const char* operation_name(Operation operation) {
    switch(operation) {
    case Operation::Create: return "create_conversation";
    case Operation::Append: return "append";
    case Operation::Correct: return "correct";
    case Operation::Exact: return "read_exact";
    case Operation::History: return "read_history/continuation";
    case Operation::Current: return "read_current/continuation";
    }
    throw std::runtime_error("invalid operation");
}

am::ConversationStoreOptions options(Operation operation) {
    am::ConversationStoreOptions value;
    value.allocation_namespace = conversation().allocation_namespace;
    value.max_conversations = 2;
    value.max_entries = 4;
    value.max_revisions_per_entry = 2;
    value.max_storage_bytes = access().scope.value().size();
    for(std::uint64_t entry = 1; entry <= 3; ++entry)
        value.max_storage_bytes += am::serialize_conversation_envelope(message(entry)).size();
    if(is_read(operation))
        value.max_storage_bytes += am::serialize_conversation_envelope(message(1, true)).size() + 80;
    if(operation == Operation::Create) value.max_storage_bytes += access().scope.value().size();
    else if(operation == Operation::Correct)
        value.max_storage_bytes += am::serialize_conversation_envelope(message(1, true)).size() + 80;
    else value.max_storage_bytes += am::serialize_conversation_envelope(message(4)).size();
    return value;
}

struct Fixture final {
    explicit Fixture(Operation operation) : store(options(operation)), exact_ref(ref(1, is_read(operation) ? 2 : 1)),
        candidate(message(operation == Operation::Correct ? 1 : 4, operation == Operation::Correct)) {
        expect(store.create_conversation(authority, stream).status == am::ConversationWriteStatus::Applied, "fixture create");
        for(std::uint64_t entry = 1; entry <= 3; ++entry)
            expect(store.append(authority, message(entry)).status == am::ConversationWriteStatus::Applied, "fixture append");
        if(is_read(operation)) expect(store.correct(authority, message(1, true), ref(1).revision, reason).status ==
            am::ConversationWriteStatus::Applied, "fixture receipt before read-result injection");
        one.max_items = 1;
        history = store.read_history(authority, stream);
        current = store.read_current(authority, stream);
        exact = store.read_exact(authority, exact_ref);
        history_first = store.read_history(authority, stream, one);
        current_first = store.read_current(authority, stream, one);
        expect(history_first.continuation && current_first.continuation, "fixture continuations");
        history_second = store.read_history(authority, stream, one, history_first.continuation);
        current_second = store.read_current(authority, stream, one, current_first.continuation);
        history_tail = store.read_history(authority, stream, {}, history_first.continuation);
        current_tail = store.read_current(authority, stream, {}, current_first.continuation);
    }

    void attempt(Operation operation) {
        switch(operation) {
        case Operation::Create: write = store.create_conversation(authority, new_stream); break;
        case Operation::Append: write = store.append(authority, candidate); break;
        case Operation::Correct: write = store.correct(authority, candidate, exact_ref.revision, reason); break;
        case Operation::Exact: read = store.read_exact(authority, exact_ref); break;
        case Operation::History: read = store.read_history(authority, stream, one, history_first.continuation); break;
        case Operation::Current: read = store.read_current(authority, stream, one, current_first.continuation); break;
        }
    }

    void verify_unchanged() const {
        expect_same_result(store.read_history(authority, stream), history);
        expect_same_result(store.read_current(authority, stream), current);
        expect_same_result(store.read_exact(authority, exact_ref), exact);
        expect_same_result(store.read_history(authority, stream, {}, history_first.continuation), history_tail);
        expect_same_result(store.read_current(authority, stream, {}, current_first.continuation), current_tail);
        expect(store.read_exact(authority, ref(4)).status == am::ConversationReadStatus::NotFound &&
            store.read_exact(authority, ref(1, exact_ref.revision.value() + 1)).status == am::ConversationReadStatus::NotFound &&
            store.read_history(authority, new_stream).status == am::ConversationReadStatus::NotFound,
            "failure published a new identity or revision");
    }

    void verify_read_capacity() {
        const auto result = store.append(authority, candidate);
        expect(result.status == am::ConversationWriteStatus::Applied && result.ref == candidate.metadata.ref &&
            result.commit_generation == history.commit_generation + 1, "failed reads must not consume entry or byte capacity");
        expect_same_result(store.read_history(authority, stream, {}, history_first.continuation), history_tail);
        expect_same_result(store.read_current(authority, stream, {}, current_first.continuation), current_tail);
    }

    void verify_success(Operation operation) {
        if(operation == Operation::Exact) {
            expect_same_result(read, exact);
            verify_unchanged();
            verify_read_capacity();
            return;
        }
        if(operation == Operation::History || operation == Operation::Current) {
            const bool is_history = operation == Operation::History;
            const auto& expected = is_history ? history_second : current_second;
            expect_same_result(read, expected);
            expect(read.continuation.has_value(), "retry must mint a usable continuation");
            const auto actual_last = is_history ? store.read_history(authority, stream, {}, read.continuation) :
                store.read_current(authority, stream, {}, read.continuation);
            const auto expected_last = is_history ? store.read_history(authority, stream, {}, expected.continuation) :
                store.read_current(authority, stream, {}, expected.continuation);
            expect_same_result(actual_last, expected_last);
            verify_unchanged();
            verify_read_capacity();
            return;
        }
        expect(write.status == am::ConversationWriteStatus::Applied &&
            write.commit_generation == history.commit_generation + 1, "successful retry must commit exactly once");
        if(operation == Operation::Create) {
            expect(!write.ref, "conversation creation has no entry reference");
            const auto created = store.read_history(authority, new_stream);
            expect(created.status == am::ConversationReadStatus::Complete && created.records.empty(), "new empty conversation");
        } else {
            expect(write.ref == candidate.metadata.ref, "write result must retain the exact long namespace");
            const auto added = store.read_exact(authority, candidate.metadata.ref);
            expect(added.status == am::ConversationReadStatus::Complete && added.records.size() == 1 &&
                am::serialize_conversation_envelope(added.records[0].envelope) ==
                    am::serialize_conversation_envelope(candidate), "successful retry stores exact candidate");
            if(operation == Operation::Correct) expect(added.records[0].correction &&
                added.records[0].correction->previous_revision == exact_ref.revision &&
                added.records[0].correction->reason_code == reason, "successful correction retains the long receipt");
        }
        // These pre-existing cursors retain the old frontier even after retry commits.
        expect_same_result(store.read_history(authority, stream, {}, history_first.continuation), history_tail);
        expect_same_result(store.read_current(authority, stream, {}, current_first.continuation), current_tail);
        const auto old = store.read_exact(authority, exact_ref);
        expect(am::serialize_conversation_envelope(old.records.at(0).envelope) ==
            am::serialize_conversation_envelope(exact.records[0].envelope), "retry must preserve old exact bytes");
    }

    am::InMemoryConversationStore store;
    const am::ConversationStoreAccess authority = access();
    const am::ConversationRef stream = conversation();
    const am::ConversationRef new_stream = conversation(2);
    const am::EntryRef exact_ref;
    const std::string reason = std::string(80, 'r');
    const am::ConversationEnvelope candidate;
    am::ConversationReadLimits one;
    am::ConversationReadResult history, current, exact, history_first, current_first;
    am::ConversationReadResult history_second, current_second, history_tail, current_tail, read;
    am::ConversationWriteResult write;
};

std::size_t run_fault_matrix(Operation operation) {
    std::size_t allocation_count = 0;
    {
        Fixture probe(operation);
        { ScopedAllocationGate gate(0); probe.attempt(operation); }
        allocation_count = g_gate.calls;
        expect(!g_gate.has_failed && allocation_count != 0 && allocation_count < 10000, "bounded nonempty allocation probe");
        probe.verify_success(operation);
    }
    for(std::size_t ordinal = 1; ordinal <= allocation_count; ++ordinal) {
        Fixture fixture(operation);
        bool has_thrown = false;
        try {
            ScopedAllocationGate gate(ordinal);
            fixture.attempt(operation);
        } catch(const std::bad_alloc&) { has_thrown = true; }
        expect(has_thrown && g_gate.has_failed && g_gate.calls == ordinal, "every measured ordinal must inject bad_alloc");
        fixture.verify_unchanged();
        // Successful retry also checks the exact remaining byte capacity and
        // next ID/revision: failed staging must consume none of those resources.
        fixture.attempt(operation);
        fixture.verify_success(operation);
    }
    {
        Fixture final_probe(operation);
        { ScopedAllocationGate gate(allocation_count + 1); final_probe.attempt(operation); }
        expect(!g_gate.has_failed && g_gate.calls == allocation_count, "successful path allocation count must be repeatable");
        final_probe.verify_success(operation);
    }
    std::printf("PASS %s: %zu allocation failures; retry and pinned cursors unchanged\n", operation_name(operation), allocation_count);
    return allocation_count;
}
} // namespace

int main() {
#if defined(_MSC_VER) && _ITERATOR_DEBUG_LEVEL > 0
    // The repository's checked-STL reproducer documents proxy allocations in
    // noexcept string construction. Failing those terminates before rollback.
    // This is an explicit qualification gap, not a passing fault-injection lane.
    std::puts("SKIP conversation store allocation matrix: MSVC checked STL allocates inside noexcept construction");
    return 77;
#else
    try {
        std::size_t failures = 0;
        for(const auto operation : {Operation::Create, Operation::Append, Operation::Correct,
            Operation::Exact, Operation::History, Operation::Current}) failures += run_fault_matrix(operation);
        std::printf("PASS conversation store: %zu actual one-shot allocation failures\n", failures);
        std::puts("Coverage: replaceable C++ allocation calls on these six fixtures; no malloc/OS/host allocator failures,");
        std::puts("no asynchronous failures, checked-STL faults, arbitrary input proof or persistent-backend claim.");
        return 0;
    } catch(const std::exception& error) {
        g_gate.is_enabled = false;
        std::fprintf(stderr, "FAIL conversation store allocation matrix: %s\n", error.what());
        return 1;
    }
#endif
}
