#include <agent_memory/ingestion/ConversationSourceImporter.hpp>

#include <algorithm>
#include <atomic>
#include <iostream>
#include <stdexcept>
#include <thread>

namespace am = agent_memory;
namespace {
void expect(bool value, const char* message) { if(!value) throw std::runtime_error(message); }
am::ConversationRef conversation(std::uint64_t id = 1) { return {"import", am::ConversationId{id}}; }
am::ConversationStoreAccess access() { return {am::ScopeId{"scope-a"}, "host-policy-v1"}; }
am::ConversationImportOptions options() {
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
    result.conversation = conversation();
    result.scope = access().scope;
    result.source = source(std::move(key));
    result.producer = "fixture-exporter-v1";
    result.actor = am::ActorRef{"import", am::ActorId{std::uint64_t{2}}};
    result.observer = am::ActorRef{"import", am::ActorId{std::uint64_t{3}}};
    result.llm_role = am::ConversationLlmRole::User;
    result.payload = am::ConversationSourceMessage{am::ConversationSourceMessageKind::Created,
        {std::string("source text"), {}}};
    return result;
}
am::ConversationSourceObservation call(std::string key, std::string domain = "run-a") {
    auto result = message(key);
    result.source = source(std::move(key), std::move(domain));
    result.llm_role = am::ConversationLlmRole::Assistant;
    result.payload = am::ConversationSourceToolCall{{0, 255, 1}, "shell",
        am::ArtifactBindingId{"original-arguments-A"}, am::ArtifactBindingId{"effective-arguments-B"}};
    return result;
}
am::ConversationSourceObservation tool_result(std::string key, const am::ConversationSourceObservation& target,
    am::ConversationSourceResultOrigin origin = am::ConversationSourceResultOrigin::Reported) {
    auto result = message(std::move(key));
    result.llm_role = am::ConversationLlmRole::Tool;
    result.payload = am::ConversationSourceToolResult{
        am::ConversationSourceTarget{target.conversation, target.scope, target.source}, {0, 255, 1}, origin,
        am::ConversationToolOutcome::Succeeded, am::ArtifactBindingId{"result-artifact"}, false, std::int64_t{0}};
    return result;
}
void create(am::IConversationImportSink& sink, std::uint64_t id = 1) {
    expect(sink.create_conversation(access(), conversation(id)).status == am::ConversationWriteStatus::Applied, "create");
}
am::ConversationWriteResult import(am::IConversationImportSink& sink, const am::ConversationSourceObservation& value) {
    const auto result = sink.import_observation(access(), value);
    expect(result.status == am::ConversationWriteStatus::Applied && result.ref.has_value(), "source admitted");
    return result;
}
am::ConversationEnvelope exact(am::IConversationImportSink& sink, const am::EntryRef& ref) {
    const auto result = sink.history().read_exact(access(), ref);
    expect(result.status == am::ConversationReadStatus::Complete && result.records.size() == 1, "exact source revision");
    return result.records[0].envelope;
}
void replay_and_identity() {
    am::InMemoryConversationImportSink sink(options());
    create(sink);
    const auto input = message("event-1");
    const auto first = import(sink, input);
    const auto replay = sink.import_observation(access(), input);
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == first.ref &&
        replay.commit_generation == first.commit_generation, "stable upstream replay");
    auto other = input;
    other.source = source("event-2"); // Same external object, different occurrence.
    const auto second = import(sink, other);
    expect(second.ref->entry.value() == 2, "object identity is not occurrence identity");

    auto revision = message("object-revision");
    revision.source.identity = am::ConversationSourceIdentity::ObjectRevision;
    revision.source.event.reset();
    revision.source.provider_revision_marker = "provider-revision-1";
    const auto third = import(sink, revision);
    expect(sink.import_observation(access(), revision).ref == third.ref, "object revision replay");
    revision.source.provider_revision_marker.reset();
    expect(sink.import_observation(access(), revision).status == am::ConversationWriteStatus::InvalidRequest,
        "missing reliable marker is not a provider revision");
    revision.source.identity = am::ConversationSourceIdentity::LocalObservation;
    std::get<am::ConversationSourceMessage>(revision.payload).kind = am::ConversationSourceMessageKind::Snapshot;
    const auto snapshot = import(sink, revision);
    expect(sink.import_observation(access(), revision).ref == snapshot.ref, "same retained snapshot replay");
    revision.source.evidence.record_locator += "/another-observation";
    expect(import(sink, revision).ref->entry != snapshot.ref->entry, "distinct local observations not content deduped");
    std::get<am::ConversationSourceMessage>(revision.payload).kind = am::ConversationSourceMessageKind::Created;
    revision.source.evidence.record_locator += "/creation";
    expect(sink.import_observation(access(), revision).status == am::ConversationWriteStatus::InvalidRequest,
        "snapshot cannot fabricate creation");

    auto changed = input;
    changed.producer = "different-producer";
    expect(sink.import_observation(access(), changed).status == am::ConversationWriteStatus::Conflict, "producer witness");
    changed = input; changed.actor->id = am::ActorId{std::uint64_t{4}};
    expect(sink.import_observation(access(), changed).status == am::ConversationWriteStatus::Conflict, "actor witness");
    changed = input; changed.source.adapter_recipe = "normalization-v2";
    expect(sink.import_observation(access(), changed).status == am::ConversationWriteStatus::Conflict, "recipe is not key");
    changed = input; std::get<am::ConversationSourceMessage>(changed.payload).content.text = "different";
    expect(sink.import_observation(access(), changed).status == am::ConversationWriteStatus::Conflict, "payload witness");
    const auto envelope = exact(sink, *first.ref);
    expect(envelope.metadata.actor == input.actor && envelope.metadata.observer == input.observer &&
        envelope.metadata.actor != envelope.metadata.observer && envelope.metadata.source->evidence.artifact ==
        input.source.evidence.artifact, "producer, actor and observer remain separate with source evidence");

    auto corrected = envelope;
    corrected.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
    corrected.metadata.source->adapter_recipe = "normalization-v2";
    corrected.payload = am::ConversationPayload{am::ConversationMessageCreated{{std::string("corrected"), {}}}};
    const auto correction = sink.correct(access(), corrected, first.ref->revision, "normalization");
    expect(correction.status == am::ConversationWriteStatus::Applied && correction.ref->entry == first.ref->entry,
        "explicit correction not new source occurrence");
    expect(sink.correct(access(), corrected, first.ref->revision, "normalization").status ==
        am::ConversationWriteStatus::Conflict, "stale CAS");
    expect(sink.import_observation(access(), input).ref == first.ref, "original replay pinned after correction");
    expect(am::serialize_conversation_envelope(exact(sink, *first.ref)) == am::serialize_conversation_envelope(envelope),
        "immutable original representation");
    expect(std::get<am::ConversationMessageCreated>(std::get<am::ConversationPayload>(
        exact(sink, *correction.ref).payload)).content.text == std::optional<std::string>{"corrected"}, "corrected exact");
}

void eight_tool_cases_and_frozen_resolution() {
    am::InMemoryConversationImportSink sink(options()); create(sink);
    const auto request = call("call");
    const auto admitted_call = import(sink, request);
    const auto normalized_call = std::get<am::ConversationToolCallObserved>(exact(sink, *admitted_call.ref).payload);
    expect(normalized_call.request == am::ArtifactBindingId{"original-arguments-A"}, "original A not effective B");
    auto missing_request = call("missing-request");
    std::get<am::ConversationSourceToolCall>(missing_request.payload).original_request = am::ArtifactBindingId{};
    expect(sink.import_observation(access(), missing_request).status == am::ConversationWriteStatus::InvalidRequest,
        "effective arguments cannot substitute for missing original request");
    auto rewritten = request;
    std::get<am::ConversationSourceToolCall>(rewritten.payload).effective_request = am::ArtifactBindingId{"different-B"};
    expect(sink.import_observation(access(), rewritten).status == am::ConversationWriteStatus::Conflict, "effective witness");
    std::size_t index = 0;
    for(const auto origin : {am::ConversationSourceResultOrigin::PermissionDenied,
        am::ConversationSourceResultOrigin::Recovery, am::ConversationSourceResultOrigin::Timeout}) {
        auto observed = tool_result("ambiguous-" + std::to_string(index++), request, origin);
        auto& raw = std::get<am::ConversationSourceToolResult>(observed.payload);
        raw.exit_code = 1; raw.is_error = true; raw.reported_outcome = am::ConversationToolOutcome::Failed;
        const auto result = import(sink, observed);
        const auto normalized = std::get<am::ConversationToolResultObserved>(exact(sink, *result.ref).payload);
        expect(normalized.outcome == am::ConversationToolOutcome::Unknown &&
            std::get<am::EntryRef>(normalized.call) == *admitted_call.ref, "synthetic/error shape is not execution outcome");
        raw.origin = am::ConversationSourceResultOrigin::Repaired;
        expect(sink.import_observation(access(), observed).status == am::ConversationWriteStatus::Conflict,
            "different source assertions cannot replay because both normalize Unknown");
    }
    const auto second_run = call("call", "run-b");
    expect(import(sink, second_run).ref != admitted_call.ref, "same call token different run not deduped");
    const auto attempt1 = import(sink, tool_result("attempt-1", request));
    const auto attempt2 = import(sink, tool_result("attempt-2", request));
    expect(attempt1.ref != attempt2.ref, "multiple result attempts remain separate source occurrences");
    const auto report = std::get<am::ConversationToolResultObserved>(exact(sink, *attempt1.ref).payload);
    expect(report.outcome == am::ConversationToolOutcome::Succeeded, "success retained as report, no effect verification");
    for(const auto outcome : {am::ConversationToolOutcome::Unknown, am::ConversationToolOutcome::Failed,
        am::ConversationToolOutcome::Cancelled}) {
        auto observed = tool_result("reported-" + std::to_string(static_cast<int>(outcome)), request);
        std::get<am::ConversationSourceToolResult>(observed.payload).reported_outcome = outcome;
        const auto result = import(sink, observed);
        expect(std::get<am::ConversationToolResultObserved>(exact(sink, *result.ref).payload).outcome == outcome,
            "explicit source-reported outcomes preserved independently of flags");
    }

    const auto late_call = call("late-call");
    auto early_result = tool_result("early-result", late_call);
    std::get<am::ConversationSourceToolResult>(early_result.payload).result.reset();
    const auto early = import(sink, early_result);
    const auto before = exact(sink, *early.ref);
    const auto& unresolved = std::get<am::ConversationToolResultObserved>(before.payload);
    expect(!unresolved.result && std::holds_alternative<am::UnresolvedConversationTarget>(unresolved.call),
        "partial archive missing call/body remains unresolved");
    const auto late = import(sink, late_call);
    const auto replay = sink.import_observation(access(), early_result);
    expect(replay.status == am::ConversationWriteStatus::NoChange && replay.ref == early.ref &&
        am::serialize_conversation_envelope(exact(sink, *early.ref)) == am::serialize_conversation_envelope(before),
        "later call does not silently reconcile earlier unresolved replay");
    auto new_result = tool_result("new-result", late_call);
    const auto fresh = import(sink, new_result);
    expect(std::get<am::EntryRef>(std::get<am::ConversationToolResultObserved>(exact(sink, *fresh.ref).payload).call) ==
        *late.ref, "new occurrence resolves exact original call");
    auto revised_call = exact(sink, *late.ref);
    revised_call.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
    revised_call.metadata.source->adapter_recipe = "normalization-v2";
    expect(sink.correct(access(), revised_call, late.ref->revision, "normalization").status ==
        am::ConversationWriteStatus::Applied, "call representation correction");
    const auto after_correction = import(sink, tool_result("post-correction-result", late_call));
    expect(std::get<am::EntryRef>(std::get<am::ConversationToolResultObserved>(
        exact(sink, *after_correction.ref).payload).call) == *late.ref, "source-key lookup never substitutes latest revision");
    auto changed_target = early_result;
    std::get<am::ConversationSourceToolResult>(changed_target.payload).call = *late.ref;
    expect(sink.import_observation(access(), changed_target).status == am::ConversationWriteStatus::Conflict,
        "changed declared target conflicts even when it could now resolve");
}

std::size_t minimum_budget(bool is_mapping) {
    // Discover the logical accounting boundary, without freezing a second size recipe in tests.
    std::size_t lower = 0, upper = 2U * 1024U * 1024U;
    while(lower < upper) {
        const auto middle = lower + (upper - lower) / 2;
        auto limits = options();
        if(is_mapping) limits.max_mapping_bytes = middle; else limits.max_observation_bytes = middle;
        am::InMemoryConversationImportSink sink(limits); create(sink);
        const auto result = sink.import_observation(access(), message("a"));
        expect(result.status == am::ConversationWriteStatus::Applied ||
            result.status == am::ConversationWriteStatus::ResourceLimit, "budget probe has only admission/limit outcomes");
        if(result.status == am::ConversationWriteStatus::Applied) upper = middle;
        else lower = middle + 1;
    }
    return lower;
}
void exact_budget_boundaries() {
    const auto observation_bytes = minimum_budget(false);
    expect(observation_bytes > 1, "finite nonempty observation charge");
    auto limits = options(); limits.max_observation_bytes = observation_bytes - 1;
    am::InMemoryConversationImportSink too_small(limits); create(too_small);
    expect(too_small.import_observation(access(), message("a")).status == am::ConversationWriteStatus::ResourceLimit,
        "observation immediately below budget refuses");
    limits.max_observation_bytes = observation_bytes;
    am::InMemoryConversationImportSink exact_input(limits); create(exact_input);
    expect(import(exact_input, message("a")).ref->entry.value() == 1, "exact observation budget accepts");

    const auto mapping_bytes = minimum_budget(true);
    expect(mapping_bytes > observation_bytes, "mapping additionally charges key/reference");
    limits = options(); limits.max_mapping_bytes = 2 * mapping_bytes;
    am::InMemoryConversationImportSink two(limits); create(two);
    import(two, message("a"));
    auto malformed = message("b");
    std::get<am::ConversationSourceMessage>(malformed.payload).content.text = std::string(1, static_cast<char>(255));
    expect(two.import_observation(access(), malformed).status == am::ConversationWriteStatus::InvalidRequest,
        "canonical append refusal removes tentative mapping");
    expect(import(two, message("b")).ref->entry.value() == 2, "refusal consumes neither mapping bytes nor next ID");
    expect(two.import_observation(access(), message("c")).status == am::ConversationWriteStatus::ResourceLimit,
        "aggregate mapping budget is conjunctive");
}

void refused_targets_and_budgets() {
    am::InMemoryConversationImportSink sink(options()); create(sink); create(sink, 2);
    const auto request = call("request"); const auto first = import(sink, request);
    const auto baseline = sink.history().read_history(access(), conversation());
    auto wrong_access = access(); wrong_access.scope = am::ScopeId{"scope-b"};
    const auto hidden = sink.import_observation(wrong_access, request);
    expect(hidden.status == am::ConversationWriteStatus::Denied && !hidden.ref && !hidden.commit_generation,
        "replay cannot leak hidden mapping");
    auto result = tool_result("result", request);
    auto& raw = std::get<am::ConversationSourceToolResult>(result.payload);
    std::get<am::ConversationSourceTarget>(raw.call).scope = am::ScopeId{"scope-b"};
    expect(sink.import_observation(access(), result).status == am::ConversationWriteStatus::Denied, "denied source target");
    std::get<am::ConversationSourceTarget>(raw.call).scope = access().scope;
    std::get<am::ConversationSourceTarget>(raw.call).conversation = conversation(2);
    expect(sink.import_observation(access(), result).status == am::ConversationWriteStatus::InvalidRequest,
        "cross conversation source target");
    raw.call = am::EntryRef{conversation(), am::EntryId{std::uint64_t{99}}, am::EventRevision{std::uint64_t{1}}};
    expect(sink.import_observation(access(), result).status == am::ConversationWriteStatus::InvalidRequest,
        "missing exact target cannot fallback unresolved");
    raw.call = *first.ref; raw.call_id = {9};
    expect(sink.import_observation(access(), result).status == am::ConversationWriteStatus::InvalidRequest, "token mismatch");
    raw.call_id = {0, 255, 1};
    const auto retry = import(sink, result);
    expect(retry.ref->entry.value() == 2 && retry.commit_generation == baseline.commit_generation + 1,
        "refused inputs consume no ID, mapping or generation");

    auto limited = options(); limited.max_mappings = 1;
    am::InMemoryConversationImportSink one(limited); create(one);
    const auto original = import(one, message("a"));
    const auto refusal = one.import_observation(access(), message("b"));
    expect(refusal.status == am::ConversationWriteStatus::ResourceLimit && !refusal.ref && !refusal.commit_generation,
        "mapping row budget");
    expect(one.import_observation(access(), message("a")).ref == original.ref, "replay allowed at capacity");
    for(const bool is_mapping : {false, true}) {
        auto zero = options();
        if(is_mapping) zero.max_mapping_bytes = 0; else zero.max_observation_bytes = 0;
        am::InMemoryConversationImportSink empty(zero); create(empty);
        expect(empty.import_observation(access(), message("a")).status == am::ConversationWriteStatus::ResourceLimit &&
            empty.history().read_history(access(), conversation()).records.empty(), "zero budget forbids, not unlimited");
    }
    auto invalid = options(); invalid.max_mappings = 65537;
    bool has_thrown = false;
    try { am::InMemoryConversationImportSink rejected(invalid); } catch(const std::invalid_argument&) { has_thrown = true; }
    expect(has_thrown, "unsupported options reject");
    auto malformed = message("invalid"); malformed.producer = std::string(1, static_cast<char>(255));
    expect(sink.import_observation(access(), malformed).status == am::ConversationWriteStatus::InvalidRequest, "producer UTF-8");
    malformed = message("invalid"); malformed.source.evidence.artifact = am::ArtifactBindingId{};
    expect(sink.import_observation(access(), malformed).status == am::ConversationWriteStatus::InvalidRequest, "retained evidence");
}

void concurrent_admission() {
    am::InMemoryConversationImportSink sink(options()); create(sink);
    const auto same = message("same");
    std::atomic<unsigned> applied{0}, replayed{0}, failed{0};
    std::vector<std::thread> threads;
    for(unsigned i = 0; i < 8; ++i) threads.emplace_back([&] {
        const auto result = sink.import_observation(access(), same);
        if(result.status == am::ConversationWriteStatus::Applied) ++applied;
        else if(result.status == am::ConversationWriteStatus::NoChange) ++replayed;
        else ++failed;
    });
    for(auto& thread : threads) thread.join();
    threads.clear();
    expect(applied == 1 && replayed == 7 && failed == 0, "concurrent duplicate atomicity");
    for(unsigned i = 0; i < 16; ++i) threads.emplace_back([&, i] {
        if(sink.import_observation(access(), message("unique-" + std::to_string(i))).status !=
            am::ConversationWriteStatus::Applied) ++failed;
    });
    for(auto& thread : threads) thread.join();
    const auto history = sink.history().read_history(access(), conversation());
    expect(failed == 0 && history.status == am::ConversationReadStatus::Complete && history.records.size() == 17,
        "concurrent distinct source mappings");
    for(std::size_t i = 0; i < history.records.size(); ++i)
        expect(history.records[i].envelope.metadata.ref.entry.value() == i + 1 &&
            history.records[i].commit_generation == i + 2, "consecutive committed IDs in admission order");
    am::InMemoryConversationImportSink independent(options()); create(independent);
    expect(import(independent, same).ref->entry.value() == 1, "instance-local dedupe boundary");
}
} // namespace
int main() {
    try {
        replay_and_identity(); eight_tool_cases_and_frozen_resolution(); refused_targets_and_budgets();
        exact_budget_boundaries(); concurrent_admission();
        std::cout << "PASS authored source-shaped reference adapter fixtures; no upstream SDK execution claim\n";
        return 0;
    } catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
