#include <agent_memory/ingestion/ConversationSourceImporter.hpp>

#include "../support/allocation_failure.hpp"

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
enum class Operation { Create, Import, Replay, Result, ExternalEvent, CompositeEvent,
    CompositeObject, CompositeReplay, CompositeConflict, ExactResult, UnresolvedResult, ResolvedSourceResult,
    MissingCompositeSourceResult };
bool is_replay(Operation op) { return op == Operation::Replay || op == Operation::CompositeReplay; }
am::ExternalObjectRef external(std::uint64_t index) {
    const auto key = [=] {
        std::vector<am::ExternalIdentityComponent> parts;
        parts.push_back({"signed", std::int64_t{-9}});
        parts.push_back({"unsigned", index});
        parts.push_back({std::string(48, 'k'), std::string(160, 'v')});
        return am::ExternalObjectKey{std::move(parts)};
    };
    return {"fixture", std::string(48, 'i'), "run-a", "event", key(), key()};
}
void bind_external(am::ConversationSourceBinding& source, std::uint64_t index, bool is_object = false) {
    source.object = external(index);
    if(is_object) {
        source.identity = am::ConversationSourceIdentity::ObjectRevision;
        source.provider_revision_marker = std::string(48, 'm');
    } else {
        source.identity = am::ConversationSourceIdentity::ExternalEvent;
        source.event = external(index);
    }
}
bool same_source(const am::ConversationSourceBinding& a, const am::ConversationSourceBinding& b) {
    return a.identity == b.identity && a.object == b.object && a.event == b.event && a.revision == b.revision &&
        a.provider_revision_marker == b.provider_revision_marker && a.adapter_recipe == b.adapter_recipe &&
        a.evidence.artifact == b.evidence.artifact && a.evidence.record_locator == b.evidence.record_locator;
}
struct Fixture final {
    explicit Fixture(Operation operation) : baseline(observation(1)), candidate(observation(2)), missing_call(observation(3)),
        sink([operation] {
            auto value = options();
            // Only this fixture needs baseline + result + later call + fresh result.
            if(operation == Operation::MissingCompositeSourceResult) value.max_mappings = 4;
            return value;
        }()) {
        if(operation == Operation::CompositeEvent || operation == Operation::CompositeObject ||
            operation == Operation::CompositeReplay || operation == Operation::CompositeConflict ||
            operation == Operation::ResolvedSourceResult) {
            bind_external(baseline.source, 1, operation == Operation::CompositeObject);
            bind_external(candidate.source, 2, operation == Operation::CompositeObject);
        }
        if(operation == Operation::ExactResult || operation == Operation::ResolvedSourceResult)
            baseline.payload = am::ConversationSourceToolCall{{0, 255}, "tool", am::ArtifactBindingId{"request"}, std::nullopt};
        expect(sink.create_conversation(authority, stream).status == am::ConversationWriteStatus::Applied, "baseline create");
        const auto initial = sink.import_observation(authority, baseline);
        expect(initial.status == am::ConversationWriteStatus::Applied, "baseline import");
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
        if(operation == Operation::CompositeConflict) {
            candidate = baseline; // Setup is outside every fault gate.
            candidate.producer[0] = 'q';
        }
        if(operation == Operation::ExactResult) {
            candidate.payload = am::ConversationSourceToolResult{*initial.ref, {0, 255}};
        } else if(operation == Operation::ResolvedSourceResult) {
            candidate.payload = am::ConversationSourceToolResult{
                am::ConversationSourceTarget{stream, authority.scope, am::copy_conversation_source_binding(baseline.source)},
                {0, 255}};
        } else if(operation == Operation::UnresolvedResult) {
            bind_external(candidate.source, 2);
            candidate.payload = am::ConversationSourceToolResult{
                am::UnresolvedConversationTarget{external(3), {am::ArtifactBindingId{"unresolved-evidence"}, "record/3"}},
                {0, 255}};
        } else if(operation == Operation::MissingCompositeSourceResult) {
            bind_external(missing_call.source, 3);
            missing_call.payload = am::ConversationSourceToolCall{{0, 255}, "tool", am::ArtifactBindingId{"request"},
                std::nullopt};
            candidate.payload = am::ConversationSourceToolResult{
                am::ConversationSourceTarget{stream, authority.scope, am::copy_conversation_source_binding(missing_call.source)},
                {0, 255}};
        }
        baseline_before = am::copy_conversation_source_binding(baseline.source);
        candidate_before = am::copy_conversation_source_binding(candidate.source);
        producer_before = candidate.producer;
        if(const auto* tool = std::get_if<am::ConversationSourceToolResult>(&candidate.payload)) {
            if(const auto* target = std::get_if<am::ConversationSourceTarget>(&tool->call))
                target_source_before = am::copy_conversation_source_binding(target->source);
            if(const auto* target = std::get_if<am::UnresolvedConversationTarget>(&tool->call))
                if(target->external) target_external_before = am::copy_external_object_ref(*target->external);
        }
        history = sink.history().read_history(authority, stream);
        current = sink.history().read_current(authority, stream);
        replay = sink.import_observation(authority, baseline);
    }
    void attempt(Operation operation) {
        if(operation == Operation::Create) result = sink.create_conversation(authority, another_stream);
        else result = sink.import_observation(authority, is_replay(operation) ? baseline : candidate);
    }
    void verify_unchanged() {
        expect(same_source(baseline.source, baseline_before) && same_source(candidate.source, candidate_before) &&
            candidate.producer == producer_before, "input identity/witness unchanged");
        if(target_source_before) expect(same_source(*target_source_before,
            std::get<am::ConversationSourceTarget>(std::get<am::ConversationSourceToolResult>(candidate.payload).call).source),
            "source-shaped target input unchanged");
        if(target_external_before) expect(target_external_before ==
            std::get<am::UnresolvedConversationTarget>(std::get<am::ConversationSourceToolResult>(candidate.payload).call).external,
            "unresolved target input unchanged");
        const auto actual = sink.history().read_history(authority, stream);
        expect(actual.status == history.status && actual.commit_generation == history.commit_generation &&
            actual.records.size() == history.records.size(), "no partial canonical append/generation");
        for(std::size_t i = 0; i < actual.records.size(); ++i)
            expect(am::serialize_conversation_envelope(actual.records[i].envelope) ==
                am::serialize_conversation_envelope(history.records[i].envelope), "history bytes unchanged");
        const auto now = sink.history().read_current(authority, stream);
        expect(now.status == current.status && now.commit_generation == current.commit_generation &&
            now.messages.size() == current.messages.size(), "current unchanged");
        for(std::size_t i = 0; i < now.messages.size(); ++i)
            expect(now.messages[i].content->text == current.messages[i].content->text, "current content unchanged");
        const auto repeated = sink.import_observation(authority, baseline);
        expect(repeated.status == am::ConversationWriteStatus::NoChange && repeated.ref == replay.ref &&
            repeated.commit_generation == replay.commit_generation, "old mapping unchanged");
        expect(sink.history().read_history(authority, another_stream).status == am::ConversationReadStatus::NotFound,
            "no orphan conversation");
    }
    void verify_success(Operation operation) {
        if(operation == Operation::CompositeConflict) {
            expect(result.status == am::ConversationWriteStatus::Conflict, "changed source witness conflicts");
            verify_unchanged();
            const auto next = sink.import_observation(authority, observation(2));
            expect(next.status == am::ConversationWriteStatus::Applied && next.ref->entry.value() == 2,
                "conflict consumes no mapping or ID");
            return;
        }
        expect(result.status == (is_replay(operation) ? am::ConversationWriteStatus::NoChange :
            am::ConversationWriteStatus::Applied), "retry success");
        if(operation == Operation::Create) {
            expect(result.commit_generation == history.commit_generation + 1 &&
                sink.history().read_history(authority, another_stream).status == am::ConversationReadStatus::Complete,
                "create publishes once");
        } else if(is_replay(operation)) {
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
            if(operation == Operation::MissingCompositeSourceResult) verify_frozen_missing_target();
        }
    }
    void verify_frozen_missing_target() {
        const auto admitted = sink.history().read_exact(authority, *result.ref);
        expect(admitted.status == am::ConversationReadStatus::Complete && admitted.records.size() == 1,
            "missing composite target committed exactly once");
        const auto& target = std::get<am::UnresolvedConversationTarget>(
            std::get<am::ConversationToolResultObserved>(admitted.records[0].envelope.payload).call);
        expect(target.external == missing_call.source.event &&
            target.evidence.artifact == missing_call.source.evidence.artifact &&
            target.evidence.record_locator == missing_call.source.evidence.record_locator,
            "fallback retains exact composite external reference and evidence");
        const auto frozen = am::serialize_conversation_envelope(admitted.records[0].envelope);
        const auto call = sink.import_observation(authority, missing_call);
        expect(call.status == am::ConversationWriteStatus::Applied && call.ref->entry.value() == 3 &&
            call.commit_generation == result.commit_generation + 1, "missing call had no partial mapping or consumed ID");
        const auto repeated = sink.import_observation(authority, candidate);
        expect(repeated.status == am::ConversationWriteStatus::NoChange && repeated.ref == result.ref &&
            repeated.commit_generation == result.commit_generation, "later call cannot reconcile frozen replay");
        const auto retained = sink.history().read_exact(authority, *result.ref);
        expect(retained.records.size() == 1 &&
            am::serialize_conversation_envelope(retained.records[0].envelope) == frozen, "unresolved bytes remain frozen");
        auto fresh = observation(4);
        fresh.payload = std::get<am::ConversationSourceToolResult>(candidate.payload);
        const auto resolved = sink.import_observation(authority, fresh);
        expect(resolved.status == am::ConversationWriteStatus::Applied && resolved.ref->entry.value() == 4 &&
            resolved.commit_generation == call.commit_generation + 1, "all four mapping rows remain available after retry");
        const auto latest = sink.history().read_exact(authority, *resolved.ref);
        expect(latest.records.size() == 1 && std::get<am::EntryRef>(
            std::get<am::ConversationToolResultObserved>(latest.records[0].envelope.payload).call) == *call.ref,
            "new source-target result resolves the now-present exact call");
    }
    const am::ConversationStoreAccess authority = access();
    const am::ConversationRef stream = conversation(), another_stream = conversation(2);
    am::ConversationSourceObservation baseline;
    am::ConversationSourceObservation candidate;
    am::ConversationSourceObservation missing_call;
    am::ConversationSourceBinding baseline_before, candidate_before;
    std::optional<am::ConversationSourceBinding> target_source_before;
    std::optional<am::ExternalObjectRef> target_external_before;
    std::string producer_before;
    am::InMemoryConversationImportSink sink;
    am::ConversationReadResult history, current;
    am::ConversationWriteResult replay, result;
};
std::size_t run_matrix(Operation operation) {
    std::size_t count = 0;
    const bool is_trace_enabled = std::getenv("AGENT_MEMORY_TRACE_ALLOCATIONS") != nullptr;
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
        if(is_trace_enabled) std::fprintf(stderr, "operation %d ordinal %zu/%zu\n",
            static_cast<int>(operation), ordinal, count);
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
    std::fflush(stdout);
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
            Operation::ExternalEvent, Operation::CompositeEvent, Operation::CompositeObject,
            Operation::CompositeReplay, Operation::CompositeConflict, Operation::ExactResult,
            Operation::UnresolvedResult, Operation::ResolvedSourceResult, Operation::MissingCompositeSourceResult})
            total += run_matrix(operation);
        std::printf("PASS import atomicity: %zu failures; no malloc/OS/crash/arbitrary-input guarantee\n", total);
        return 0;
    } catch(const std::exception& error) {
        g_gate.is_enabled = false; std::fprintf(stderr, "%s\n", error.what()); return 1;
    }
#endif
}
