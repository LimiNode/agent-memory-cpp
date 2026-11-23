#include <agent_memory/ingestion/ConversationSourceImporter.hpp>

#include <cstdio>
#include <cstdlib>
#include <limits>
#include <new>
#include <stdexcept>
#if defined(_WIN32)
#include <malloc.h>
#endif

namespace {
// Isolated single-threaded standard replacement functions, not runtime detours.
// Fixtures/assertions allocate outside the one-shot operation gate.
struct AllocationGate final { bool is_enabled = false; bool has_failed = false; std::size_t calls = 0, fail_at = 0; };
thread_local AllocationGate g_gate;
void check_allocation() {
    if(!g_gate.is_enabled) return;
    if(++g_gate.calls == g_gate.fail_at) {
        g_gate.has_failed = true; g_gate.is_enabled = false;
        throw std::bad_alloc{};
    }
}
void* allocate(std::size_t size) {
    check_allocation();
    if(auto* result = std::malloc(size ? size : 1)) return result;
    throw std::bad_alloc{};
}
void* allocate_aligned(std::size_t size, std::size_t alignment) {
    check_allocation();
    if(size > std::numeric_limits<std::size_t>::max() - (alignment - 1)) throw std::bad_alloc{};
    const auto rounded = size ? ((size + alignment - 1) / alignment) * alignment : alignment;
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
    explicit ScopedGate(std::size_t ordinal) { g_gate = {true, false, 0, ordinal}; }
    ~ScopedGate() { g_gate.is_enabled = false; }
};
} // namespace
void* operator new(std::size_t n) { return allocate(n); }
void* operator new[](std::size_t n) { return allocate(n); }
void* operator new(std::size_t n, const std::nothrow_t&) noexcept {
    try { return allocate(n); } catch(const std::bad_alloc&) { return nullptr; }
}
void* operator new[](std::size_t n, const std::nothrow_t&) noexcept {
    try { return allocate(n); } catch(const std::bad_alloc&) { return nullptr; }
}
void* operator new(std::size_t n, std::align_val_t a) { return allocate_aligned(n, static_cast<std::size_t>(a)); }
void* operator new[](std::size_t n, std::align_val_t a) { return allocate_aligned(n, static_cast<std::size_t>(a)); }
void* operator new(std::size_t n, std::align_val_t a, const std::nothrow_t&) noexcept {
    try { return allocate_aligned(n, static_cast<std::size_t>(a)); } catch(const std::bad_alloc&) { return nullptr; }
}
void* operator new[](std::size_t n, std::align_val_t a, const std::nothrow_t&) noexcept {
    try { return allocate_aligned(n, static_cast<std::size_t>(a)); } catch(const std::bad_alloc&) { return nullptr; }
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
void operator delete(void* p, const std::nothrow_t&) noexcept { std::free(p); }
void operator delete[](void* p, const std::nothrow_t&) noexcept { std::free(p); }
void operator delete(void* p, std::align_val_t) noexcept { release_aligned(p); }
void operator delete[](void* p, std::align_val_t) noexcept { release_aligned(p); }
void operator delete(void* p, std::size_t, std::align_val_t) noexcept { release_aligned(p); }
void operator delete[](void* p, std::size_t, std::align_val_t) noexcept { release_aligned(p); }
void operator delete(void* p, std::align_val_t, const std::nothrow_t&) noexcept { release_aligned(p); }
void operator delete[](void* p, std::align_val_t, const std::nothrow_t&) noexcept { release_aligned(p); }

namespace am = agent_memory;
namespace {
void expect(bool value, const char* message) { if(!value) throw std::runtime_error(message); }
am::ConversationRef conversation(std::uint64_t id = 1) { return {std::string(80, 'n'), am::ConversationId{id}}; }
am::ConversationStoreAccess access() { return {am::ScopeId{std::string(64, 's')}, std::string(64, 'p')}; }
am::ConversationImportOptions options() {
    am::ConversationImportOptions result;
    result.store.allocation_namespace = conversation().allocation_namespace;
    result.max_mappings = 2; // Exactly baseline + candidate: rollback cannot consume a row.
    return result;
}
am::ConversationSourceObservation observation(std::uint64_t index) {
    am::ConversationSourceObservation value;
    value.conversation = conversation(); value.scope = access().scope;
    value.source.revision = am::SourceRevisionId{std::string(48, 'r')};
    value.source.adapter_recipe = "authored-normalization-v1";
    value.source.evidence = {am::ArtifactBindingId{std::string(48, 'a')}, "records/" + std::to_string(index)};
    value.producer = std::string(48, 'p');
    value.payload = am::ConversationSourceMessage{am::ConversationSourceMessageKind::Snapshot,
        {std::string(160, 't'), {am::ArtifactBindingId{std::string(48, 'b')}}}};
    return value;
}
enum class Operation { Create, Import, Replay, Result, ExternalEvent };
struct Fixture final {
    explicit Fixture(Operation operation) : baseline(observation(1)), candidate(observation(2)), sink(options()) {
        expect(sink.create_conversation(authority, stream).status == am::ConversationWriteStatus::Applied, "baseline create");
        expect(sink.import_observation(authority, baseline).status == am::ConversationWriteStatus::Applied, "baseline import");
        if(operation == Operation::Result) {
            candidate.payload = am::ConversationSourceToolResult{
                am::ConversationSourceTarget{stream, authority.scope, observation(3).source}, {0, 255},
                am::ConversationSourceResultOrigin::Timeout, am::ConversationToolOutcome::Unknown,
                std::nullopt, true, std::nullopt};
        }
        if(operation == Operation::ExternalEvent) {
            candidate.source.identity = am::ConversationSourceIdentity::ExternalEvent;
            candidate.source.event = am::ExternalObjectRef{"fixture", std::string(48, 'i'), "run-a", "event",
                std::nullopt, am::ExternalObjectKey{std::string(48, 'k')}};
        }
        history = sink.history().read_history(authority, stream);
        current = sink.history().read_current(authority, stream);
        replay = sink.import_observation(authority, baseline);
    }
    void attempt(Operation operation) {
        if(operation == Operation::Create) result = sink.create_conversation(authority, another_stream);
        else result = sink.import_observation(authority, operation == Operation::Replay ? baseline : candidate);
    }
    void verify_unchanged() {
        const auto actual = sink.history().read_history(authority, stream);
        expect(actual.status == history.status && actual.commit_generation == history.commit_generation &&
            actual.records.size() == history.records.size(), "no partial canonical append/generation");
        for(std::size_t i = 0; i < actual.records.size(); ++i)
            expect(am::serialize_conversation_envelope(actual.records[i].envelope) ==
                am::serialize_conversation_envelope(history.records[i].envelope), "history bytes unchanged");
        const auto now = sink.history().read_current(authority, stream);
        expect(now.status == current.status && now.commit_generation == current.commit_generation &&
            now.messages.size() == current.messages.size() && now.messages[0].content->text ==
            current.messages[0].content->text, "current unchanged");
        const auto repeated = sink.import_observation(authority, baseline);
        expect(repeated.status == am::ConversationWriteStatus::NoChange && repeated.ref == replay.ref &&
            repeated.commit_generation == replay.commit_generation, "old mapping unchanged");
        expect(sink.history().read_history(authority, another_stream).status == am::ConversationReadStatus::NotFound,
            "no orphan conversation");
    }
    void verify_success(Operation operation) {
        expect(result.status == (operation == Operation::Replay ? am::ConversationWriteStatus::NoChange :
            am::ConversationWriteStatus::Applied), "retry success");
        if(operation == Operation::Create) {
            expect(result.commit_generation == history.commit_generation + 1 &&
                sink.history().read_history(authority, another_stream).status == am::ConversationReadStatus::Complete,
                "create publishes once");
        } else if(operation == Operation::Replay) {
            expect(result.ref == replay.ref && result.commit_generation == replay.commit_generation, "replay unchanged");
            const auto appended = sink.import_observation(authority, candidate);
            expect(appended.status == am::ConversationWriteStatus::Applied && appended.ref->entry.value() == 2,
                "failed replay consumes no mapping or ID");
        } else {
            expect(result.ref && result.ref->entry.value() == 2 &&
                result.commit_generation == history.commit_generation + 1, "failed staging consumes no ID/generation");
            const auto repeated = sink.import_observation(authority, candidate);
            expect(repeated.status == am::ConversationWriteStatus::NoChange && repeated.ref == result.ref &&
                sink.history().read_history(authority, stream).records.size() == 2, "no orphan mapping or duplicate append");
        }
    }
    const am::ConversationStoreAccess authority = access();
    const am::ConversationRef stream = conversation(), another_stream = conversation(2);
    const am::ConversationSourceObservation baseline;
    am::ConversationSourceObservation candidate;
    am::InMemoryConversationImportSink sink;
    am::ConversationReadResult history, current;
    am::ConversationWriteResult replay, result;
};
std::size_t run_matrix(Operation operation) {
    std::size_t count = 0;
    {
        Fixture probe(operation);
        { ScopedGate gate(0); probe.attempt(operation); }
        count = g_gate.calls;
        // Optimized/library-runtime paths may contain no replaceable allocations.
        // Report zero honestly; do not manufacture an allocation solely for injection.
        expect(count < 10000 && !g_gate.has_failed, "bounded allocation probe");
        probe.verify_success(operation);
    }
    for(std::size_t ordinal = 1; ordinal <= count; ++ordinal) {
        Fixture fixture(operation);
        bool has_thrown = false;
        try { ScopedGate gate(ordinal); fixture.attempt(operation); } catch(const std::bad_alloc&) { has_thrown = true; }
        expect(has_thrown && g_gate.has_failed && g_gate.calls == ordinal, "each ordinal injects actual bad_alloc");
        fixture.verify_unchanged(); fixture.attempt(operation); fixture.verify_success(operation);
    }
    Fixture final_probe(operation);
    { ScopedGate gate(count + 1); final_probe.attempt(operation); }
    expect(!g_gate.has_failed && g_gate.calls == count, "allocation count repeatable");
    final_probe.verify_success(operation);
    std::printf("PASS import operation %d: %zu actual bad_alloc failures\n", static_cast<int>(operation), count);
    return count;
}
} // namespace
int main() {
#if defined(_MSC_VER) && _ITERATOR_DEBUG_LEVEL > 0
    std::puts("SKIP import allocation matrix: MSVC checked STL allocates inside noexcept construction");
    return 77;
#else
    try {
        std::size_t total = 0;
        for(const auto operation : {Operation::Create, Operation::Import, Operation::Replay, Operation::Result,
            Operation::ExternalEvent})
            total += run_matrix(operation);
        std::printf("PASS import atomicity: %zu failures; no malloc/OS/crash/arbitrary-input guarantee\n", total);
        return 0;
    } catch(const std::exception& error) {
        g_gate.is_enabled = false; std::fprintf(stderr, "%s\n", error.what()); return 1;
    }
#endif
}
