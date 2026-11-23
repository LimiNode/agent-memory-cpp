#include <agent_memory/storage/ConversationStore.hpp>

#include <atomic>
#include <iostream>
#include <stdexcept>
#include <thread>

namespace am = agent_memory;
namespace {
// An independent backend can mint its own state without friendship to the reference backend.
class ForeignContinuationBackend : public am::IConversationStore {
public:
    static am::ConversationContinuation token() {
        struct State final : am::ConversationContinuation::BackendState {};
        return make_continuation(std::make_shared<const State>());
    }
};
void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
am::ConversationRef conversation(std::uint64_t id = 1) { return {"reference", am::ConversationId{id}}; }
am::EntryRef ref(std::uint64_t entry, std::uint64_t revision = 1, std::uint64_t id = 1) {
    return {conversation(id), am::EntryId{entry}, am::EventRevision{revision}};
}
am::ConversationStoreAccess access(std::string scope = "scope-a", std::string policy = "principal-a.v1") {
    return {am::ScopeId{std::move(scope)}, std::move(policy)};
}
am::ConversationStoreOptions options() {
    am::ConversationStoreOptions result;
    result.allocation_namespace = "reference";
    return result;
}
am::ConversationEnvelope message(std::uint64_t id, std::string text = "original", std::uint64_t revision = 1) {
    am::ConversationEntry entry;
    entry.ref = ref(id, revision);
    entry.scope = access().scope;
    am::ConversationSourceBinding source;
    source.revision = am::SourceRevisionId{"retained@1"};
    source.adapter_recipe = "fixture-v1";
    source.evidence = {am::ArtifactBindingId{"retained-binding"}, "record:" + std::to_string(id)};
    entry.source = source;
    entry.actor = am::ActorRef{"reference", am::ActorId{std::uint64_t{7}}};
    entry.observer = am::ActorRef{"reference", am::ActorId{std::uint64_t{9}}};
    entry.llm_role = am::ConversationLlmRole::User;
    entry.payload = am::ConversationMessageCreated{{std::move(text), {}}};
    return am::make_conversation_envelope(std::move(entry));
}
void create(am::InMemoryConversationStore& store) {
    expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied, "create");
}
void append(am::InMemoryConversationStore& store, const am::ConversationEnvelope& value) {
    expect(store.append(access(), value).status == am::ConversationWriteStatus::Applied, "append");
}
std::string current_text(const am::ConversationReadResult& result, std::size_t index = 0) {
    expect(result.status == am::ConversationReadStatus::Complete && result.messages.size() > index &&
        result.messages[index].content && result.messages[index].content->text, "complete live view");
    return *result.messages[index].content->text;
}

void immutable_history_and_cas() {
    am::InMemoryConversationStore store(options());
    create(store);
    auto original = message(1);
    const auto admitted = store.append(access(), original);
    expect(admitted.status == am::ConversationWriteStatus::Applied, "original admitted");
    expect(store.append(access(), original).commit_generation == admitted.commit_generation, "replay no generation");
    expect(store.append(access(), message(1, "collision")).status == am::ConversationWriteStatus::Conflict, "collision");
    auto no_op = message(1, "original", 2);
    expect(store.correct(access(), no_op, am::EventRevision{std::uint64_t{1}}, "unchanged").status ==
        am::ConversationWriteStatus::NoChange, "no-op does not publish proposed revision");
    auto correction = message(1, "corrected", 2);
    correction.metadata.source->adapter_recipe = "fixture-v2";
    expect(store.correct(access(), correction, am::EventRevision{std::uint64_t{1}}, "normalization").status ==
        am::ConversationWriteStatus::Applied, "correction");
    expect(store.correct(access(), correction, am::EventRevision{std::uint64_t{1}}, "normalization").status ==
        am::ConversationWriteStatus::Conflict, "stale CAS precedes NoChange");
    expect(store.correct(access(), message(1, "future", 100), am::EventRevision{std::uint64_t{99}}, "future").status ==
        am::ConversationWriteStatus::Conflict, "future expected revision conflicts");
    expect(store.append(access(), original).status == am::ConversationWriteStatus::NoChange, "old replay cannot revert");
    const auto old = store.read_exact(access(), ref(1));
    const auto latest = store.read_exact(access(), ref(1, 2));
    expect(am::serialize_conversation_envelope(old.records.at(0).envelope) == am::serialize_conversation_envelope(original),
        "immutable exact evidence and binary extensions");
    expect(old.records[0].envelope.metadata.source->adapter_recipe == "fixture-v1" &&
        latest.records.at(0).envelope.metadata.source->adapter_recipe == "fixture-v2", "old/new recipe");
    expect(latest.records[0].correction && latest.records[0].correction->reason_code == "normalization" &&
        latest.records[0].correction->previous_revision.value() == 1, "correction receipt");
    expect(current_text(store.read_current(access(), conversation())) == "corrected", "fold latest representation");
    auto retarget = message(1, "changed identity", 3);
    retarget.metadata.source->evidence.record_locator = "different-occurrence";
    expect(store.correct(access(), retarget, am::EventRevision{std::uint64_t{2}}, "retarget").status ==
        am::ConversationWriteStatus::InvalidRequest, "source occurrence cannot rebind");
    expect(store.read_history(access(), conversation()).records.size() == 2, "refusal atomic");
}

void fold_and_exact_restore() {
    am::InMemoryConversationStore store(options());
    create(store);
    append(store, message(1, "v1"));
    auto edit = message(2);
    edit.payload = am::ConversationPayload{am::ConversationEdited{ref(1), {std::string("v2"), {}}}};
    append(store, edit);
    expect(current_text(store.read_current(access(), conversation())) == "v2", "edit fold");
    auto removed = message(3);
    removed.payload = am::ConversationPayload{am::ConversationTombstoned{ref(2)}};
    append(store, removed);
    const auto deleted = store.read_current(access(), conversation());
    expect(deleted.messages.at(0).status == am::ConversationMessageState::Deleted &&
        !deleted.messages[0].content && !deleted.messages[0].content_ref, "tombstone hides but does not erase");
    auto correction = message(1, "corrected-v1", 2);
    expect(store.correct(access(), correction, am::EventRevision{std::uint64_t{1}}, "normalization").status ==
        am::ConversationWriteStatus::Applied, "correct root after descendants");
    auto restored = message(4);
    restored.payload = am::ConversationPayload{am::ConversationRestored{ref(3), ref(1)}};
    append(store, restored);
    const auto view = store.read_current(access(), conversation());
    expect(current_text(view) == "v1" && view.messages[0].content_ref == ref(1), "restore exact not latest");
    expect(view.messages[0].origin == ref(1, 2) && view.messages[0].state == ref(4), "root and state exact refs");
    expect(view.messages[0].origin_metadata.actor->id.value() == 7 &&
        view.messages[0].origin_metadata.observer->id.value() == 9 &&
        view.messages[0].origin_metadata.llm_role == am::ConversationLlmRole::User, "origin metadata");
    auto sibling = message(5);
    sibling.payload = am::ConversationPayload{am::ConversationEdited{ref(1), {std::string("sibling"), {}}}};
    append(store, sibling);
    const auto ambiguous = store.read_current(access(), conversation());
    expect(ambiguous.messages[0].status == am::ConversationMessageState::Ambiguous &&
        !ambiguous.messages[0].content && ambiguous.messages[0].state.entry.empty(), "no silent LWW");
    auto snapshot = message(6);
    snapshot.payload = am::ConversationPayload{am::ConversationSnapshotObserved{{std::string("observed"), {}}}};
    append(store, snapshot);
    const auto snapshots = store.read_current(access(), conversation());
    expect(snapshots.messages.size() == 2 && snapshots.messages[1].is_snapshot_origin &&
        snapshots.messages[1].origin == ref(6), "snapshot does not fabricate or merge history");
    auto cross_chain = message(7);
    cross_chain.payload = am::ConversationPayload{am::ConversationRestored{ref(5), ref(6)}};
    expect(store.append(access(), cross_chain).status == am::ConversationWriteStatus::InvalidRequest, "cross-chain restore");
    cross_chain.payload = am::ConversationPayload{am::ConversationRestored{ref(5), ref(3)}};
    expect(store.append(access(), cross_chain).status == am::ConversationWriteStatus::InvalidRequest, "tombstone lacks content");
    expect(store.read_history(access(), conversation()).records.size() == 7, "all original/corrected facts retained");
}

void snapshots_and_budgets() {
    am::InMemoryConversationStore store(options());
    create(store);
    append(store, message(1, "one")); append(store, message(2, "two")); append(store, message(3, "three"));
    am::ConversationReadLimits one;
    one.max_items = 1;
    const auto first = store.read_history(access(), conversation(), one);
    expect(first.status == am::ConversationReadStatus::Partial && first.continuation, "history page");
    const auto current_first = store.read_current(access(), conversation(), one);
    expect(current_first.continuation.has_value(), "current page");
    auto corrected = message(2, "new-two", 2);
    expect(store.correct(access(), corrected, am::EventRevision{std::uint64_t{1}}, "correction").status ==
        am::ConversationWriteStatus::Applied, "change between pages");
    append(store, message(4, "four"));
    const auto second = store.read_history(access(), conversation(), one, first.continuation);
    expect(second.records.at(0).envelope.metadata.ref == ref(2) && second.commit_generation == first.commit_generation,
        "history frontier pins revision, not max EntryId");
    const auto last = store.read_history(access(), conversation(), {}, second.continuation);
    expect(last.status == am::ConversationReadStatus::Complete && last.records.size() == 1 &&
        last.records[0].envelope.metadata.ref == ref(3), "new append excluded from old frontier");
    const auto old_current = store.read_current(access(), conversation(), {}, current_first.continuation);
    expect(old_current.messages.size() == 2 && current_text(old_current) == "two", "current frontier after correction");
    expect(store.read_history(access(), conversation()).records.size() == 5, "fresh history includes correction");
    am::ConversationReadLimits zero;
    zero.max_output_bytes = 0;
    const auto oversized = store.read_history(access(), conversation(), zero);
    expect(oversized.status == am::ConversationReadStatus::OversizedItem && oversized.records.empty() &&
        oversized.continuation && oversized.visited_records == 0, "oversized is not skipped");
    expect(store.read_history(access(), conversation(), one, oversized.continuation).records.at(0).envelope.metadata.ref ==
        ref(1), "retry same seek with larger budget");
    zero = {};
    zero.max_visited_records = 1;
    const auto unfinished = store.read_current(access(), conversation(), zero);
    expect(unfinished.status == am::ConversationReadStatus::BudgetExhausted && unfinished.messages.empty() &&
        unfinished.visited_records == 1 && unfinished.continuation, "unfinished fold not current");
    zero = {};
    zero.max_decoded_bytes = am::serialize_conversation_envelope(message(1, "one")).size() - 1;
    expect(store.read_exact(access(), ref(1), zero).status == am::ConversationReadStatus::BudgetExhausted, "input byte bound");
    zero = {};
    zero.max_items = 0;
    expect(store.read_history(access(), conversation(), zero).status == am::ConversationReadStatus::BudgetExhausted,
        "zero not unlimited");
    zero = {};
    zero.max_items = 65537;
    expect(store.read_history(access(), conversation(), zero).status == am::ConversationReadStatus::InvalidRequest,
        "implementation ceiling");
    const std::optional<am::ConversationContinuation> empty{am::ConversationContinuation{}};
    expect(store.read_history(access(), conversation(), {}, empty).status == am::ConversationReadStatus::InvalidContinuation,
        "explicit empty resume token invalid");
    expect(store.read_current(access(), conversation(), {}, first.continuation).status == am::ConversationReadStatus::InvalidContinuation,
        "view binding");
    expect(store.read_history(access("scope-a", "principal-a.v2"), conversation(), {}, first.continuation).status ==
        am::ConversationReadStatus::InvalidContinuation, "changed policy invalidates continuation");
    expect(store.read_history(access("scope-b"), conversation(), {}, first.continuation).status ==
        am::ConversationReadStatus::InvalidContinuation, "scope binding");
    expect(store.read_exact(access("scope-b"), ref(1)).records.empty(), "denied scope no content");
    expect(store.read_exact(access("scope-a", ""), ref(1)).status == am::ConversationReadStatus::Denied, "no host context");
    am::InMemoryConversationStore other(options()); create(other);
    const auto foreign = other.read_history(access(), conversation(), {}, first.continuation);
    expect(foreign.status == am::ConversationReadStatus::InvalidContinuation && foreign.records.empty() &&
        foreign.visited_records == 0, "instance binding and no leaked counts");
    expect(store.read_history(access(), conversation(), {}, ForeignContinuationBackend::token()).status ==
        am::ConversationReadStatus::InvalidContinuation, "backend-neutral token rejects foreign backend state");
    expect(store.read_history(access(), conversation(2), {}, first.continuation).status ==
        am::ConversationReadStatus::InvalidContinuation, "conversation binding");
    std::optional<am::ConversationContinuation> stale;
    {
        am::InMemoryConversationStore destroyed(options()); create(destroyed);
        append(destroyed, message(1)); append(destroyed, message(2));
        stale = destroyed.read_history(access(), conversation(), one).continuation;
    }
    expect(store.read_history(access(), conversation(), {}, stale).status ==
        am::ConversationReadStatus::InvalidContinuation, "destroyed instance frontier cannot resume in another store");
}

void tool_observations_and_extensions() {
    am::InMemoryConversationStore store(options()); create(store);
    auto call = message(1);
    call.payload = am::ConversationToolCallObserved{{0, 255, 1}, "tool", am::ArtifactBindingId{"request-a"}};
    append(store, call);
    auto independent = message(2);
    independent.payload = am::ConversationToolCallObserved{{0, 255, 1}, "tool", am::ArtifactBindingId{"request-b"}};
    append(store, independent);
    auto result = message(3);
    result.payload = am::ConversationToolResultObserved{ref(2), {0, 255, 1}, am::ConversationToolOutcome::Unknown, std::nullopt};
    append(store, result);
    auto unresolved = message(4);
    unresolved.payload = am::ConversationToolResultObserved{
        am::UnresolvedConversationTarget{std::nullopt, {am::ArtifactBindingId{"result-evidence"}, "result-record"}},
        {0, 255, 1}, am::ConversationToolOutcome::Failed, std::nullopt};
    append(store, unresolved);
    auto invalid = message(5);
    invalid.payload = am::ConversationToolResultObserved{ref(3), {0, 255, 1}, am::ConversationToolOutcome::Succeeded, std::nullopt};
    expect(store.append(access(), invalid).status == am::ConversationWriteStatus::InvalidRequest, "result cannot target result");
    invalid.payload = am::ConversationToolResultObserved{ref(1), {8}, am::ConversationToolOutcome::Unknown, std::nullopt};
    expect(store.append(access(), invalid).status == am::ConversationWriteStatus::InvalidRequest, "exact token match");
    invalid.payload = am::ConversationToolResultObserved{ref(1), {0, 255, 1}, am::ConversationToolOutcome::Unknown, std::nullopt};
    append(store, invalid);
    expect(store.read_history(access(), conversation()).records.size() == 5, "no global call token dedup");
    expect(store.read_current(access(), conversation()).messages.empty(), "tools do not execute or become messages");
    auto extension = message(6);
    extension.extensions.push_back({65536, false, {0, 255}});
    append(store, extension);
    expect(store.read_exact(access(), ref(6)).records.at(0).envelope.extensions.at(0).bytes == am::ByteBuffer({0, 255}),
        "advisory extension lossless");
    auto opaque = message(7);
    opaque.payload = am::ConversationOpaquePayload{4096, 1, {0, 255}};
    expect(store.append(access(), opaque).status == am::ConversationWriteStatus::Unsupported, "retention not typed admission");
    opaque = message(7);
    opaque.extensions.push_back({65536, true, {0, 255}});
    expect(store.append(access(), opaque).status == am::ConversationWriteStatus::Unsupported, "critical metadata unsupported");
    expect(store.append(access(), message(8)).status == am::ConversationWriteStatus::Conflict, "failed admission consumes no ID");
    append(store, message(7));
}

void storage_limits_and_concurrency() {
    auto bounded = options(); bounded.max_entries = 1; bounded.max_revisions_per_entry = 1;
    am::InMemoryConversationStore store(bounded); create(store);
    append(store, message(1));
    expect(store.append(access(), message(2)).status == am::ConversationWriteStatus::ResourceLimit, "entry cap");
    expect(store.correct(access(), message(1, "change", 2), am::EventRevision{std::uint64_t{1}}, "correction").status ==
        am::ConversationWriteStatus::ResourceLimit, "revision cap");
    am::InMemoryConversationStore concurrent(options()); create(concurrent);
    std::atomic<int> applied{0}, conflicts{0};
    auto writer = [&concurrent, &applied, &conflicts](std::string text) {
        const auto status = concurrent.append(access(), message(1, std::move(text))).status;
        if(status == am::ConversationWriteStatus::Applied) ++applied;
        if(status == am::ConversationWriteStatus::Conflict) ++conflicts;
    };
    std::thread a(writer, "a"), b(writer, "b"); a.join(); b.join();
    expect(applied == 1 && conflicts == 1 && concurrent.read_history(access(), conversation()).records.size() == 1,
        "concurrent admission atomic");
    expect(concurrent.append(access(), message(3)).status == am::ConversationWriteStatus::Conflict, "no lower behind higher");
    append(concurrent, message(2));
    const auto ordered = concurrent.read_history(access(), conversation());
    expect(ordered.records.at(0).envelope.metadata.ref.entry.value() == 1 &&
        ordered.records.at(1).envelope.metadata.ref.entry.value() == 2, "committed order");
}

void target_corrections_and_deleted_edits() {
    am::InMemoryConversationStore store(options()); create(store);
    append(store, message(1)); append(store, message(2));
    auto linked = message(3);
    linked.metadata.links.push_back({am::ConversationLinkKind::Reply, ref(1)});
    append(store, linked);
    auto changed = linked;
    changed.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
    changed.metadata.links[0].target = ref(2);
    const auto generation = store.read_history(access(), conversation()).commit_generation;
    expect(store.correct(access(), changed, linked.metadata.ref.revision, "retarget").status ==
        am::ConversationWriteStatus::InvalidRequest, "correction cannot retarget metadata links");
    changed = linked;
    changed.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
    changed.metadata.links[0].kind = am::ConversationLinkKind::Thread;
    expect(store.correct(access(), changed, linked.metadata.ref.revision, "reclassify").status ==
        am::ConversationWriteStatus::InvalidRequest, "correction cannot reclassify links");
    auto unresolved = message(4);
    unresolved.metadata.links.push_back({am::ConversationLinkKind::Reply,
        am::UnresolvedConversationTarget{std::nullopt, {am::ArtifactBindingId{"orphan-evidence"}, "record"}}});
    append(store, unresolved);
    expect(std::holds_alternative<am::UnresolvedConversationTarget>(
        store.read_exact(access(), ref(4)).records.at(0).envelope.metadata.links.at(0).target), "orphan preserved");
    auto resolved = unresolved;
    resolved.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
    resolved.metadata.links[0].target = ref(1);
    expect(store.correct(access(), resolved, unresolved.metadata.ref.revision, "resolve").status ==
        am::ConversationWriteStatus::InvalidRequest, "resolution needs separate reconciliation");
    auto invalid = message(5);
    invalid.payload = am::ConversationPayload{am::ConversationEdited{ref(99), {std::string("missing"), {}}}};
    expect(store.append(access(), invalid).status == am::ConversationWriteStatus::InvalidRequest, "missing target");
    invalid.payload = am::ConversationPayload{am::ConversationEdited{ref(1, 2), {std::string("future"), {}}}};
    expect(store.append(access(), invalid).status == am::ConversationWriteStatus::InvalidRequest, "future target revision");
    invalid.payload = am::ConversationPayload{am::ConversationTombstoned{ref(1)}};
    append(store, invalid);
    auto after_deleted = message(6);
    after_deleted.payload = am::ConversationPayload{am::ConversationEdited{ref(5), {std::string("not restored"), {}}}};
    append(store, after_deleted);
    const auto view = store.read_current(access(), conversation());
    expect(view.messages.at(0).status == am::ConversationMessageState::Ambiguous && !view.messages[0].content,
        "edit after tombstone cannot silently assert restoration");
    expect(store.read_history(access(), conversation()).commit_generation == generation + 3,
        "rejected target changes consume no generation");

    expect(store.create_conversation(access("scope-b"), conversation(2)).status == am::ConversationWriteStatus::Applied,
        "separate hidden conversation");
    auto hidden = message(1); hidden.metadata.ref = ref(1, 1, 2); hidden.metadata.scope = access("scope-b").scope;
    expect(store.append(access("scope-b"), hidden).status == am::ConversationWriteStatus::Applied, "hidden record");
    invalid = message(7);
    invalid.metadata.links.push_back({am::ConversationLinkKind::Quote, ref(1, 1, 2)});
    expect(store.append(access(), invalid).status == am::ConversationWriteStatus::InvalidRequest, "cross-scope target refused");
    append(store, message(7));
}

void profiles_snapshots_and_read_edges() {
    auto invalid_options = options(); invalid_options.max_conversations = 0;
    bool is_rejected = false;
    try { am::InMemoryConversationStore invalid(invalid_options); }
    catch(const std::invalid_argument&) { is_rejected = true; }
    expect(is_rejected, "invalid options");
    invalid_options = options(); invalid_options.codec_limits.max_frame_bytes = 256U * 1024U * 1024U;
    is_rejected = false;
    try { am::InMemoryConversationStore invalid(invalid_options); }
    catch(const am::ConversationCodecError& e) { is_rejected = e.code() == am::ConversationCodecCode::InvalidLimits; }
    expect(is_rejected, "invalid codec profile");
    auto bounded = options(); bounded.max_conversations = 1;
    am::InMemoryConversationStore limited(bounded); create(limited);
    expect(limited.create_conversation(access(), conversation(2)).status == am::ConversationWriteStatus::ResourceLimit,
        "conversation cap");
    bounded = options(); bounded.max_storage_bytes = 1;
    am::InMemoryConversationStore no_scope(bounded);
    expect(no_scope.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::ResourceLimit,
        "scope retained bytes charged before creation");

    auto original = message(1, "x"); original.metadata.source->adapter_recipe = "a";
    const auto frame_size = am::serialize_conversation_envelope(original).size();
    bounded = options(); bounded.codec_limits.max_frame_bytes = frame_size;
    bounded.max_storage_bytes = access().scope.value().size() + 2 * frame_size + 1;
    am::InMemoryConversationStore exact(bounded); create(exact); append(exact, original);
    auto correction = original;
    correction.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
    correction.metadata.source->adapter_recipe = "b";
    expect(exact.correct(access(), correction, original.metadata.ref.revision, "r").status ==
        am::ConversationWriteStatus::Applied, "exact maximum frame correction without synthetic expansion");
    auto next = original; next.metadata.ref = ref(2);
    expect(exact.append(access(), next).status == am::ConversationWriteStatus::ResourceLimit,
        "exact storage cap includes scope and receipt");

    am::InMemoryConversationStore snapshots(options()); create(snapshots);
    auto first = message(1);
    first.metadata.source->object = am::ExternalObjectRef{"provider", "instance", "domain", "message", std::nullopt,
        am::ExternalObjectKey{std::string("same-message")}};
    first.payload = am::ConversationPayload{am::ConversationSnapshotObserved{{std::string("state-a"), {}}}};
    append(snapshots, first);
    auto second = message(2);
    second.metadata.source->object = first.metadata.source->object;
    second.payload = am::ConversationPayload{am::ConversationSnapshotObserved{{std::string("state-b"), {}}}};
    append(snapshots, second);
    expect(snapshots.read_current(access(), conversation()).messages.size() == 2 &&
        snapshots.read_history(access(), conversation()).records.size() == 2,
        "same external snapshot object never fabricates source history or dedupes observations");
    am::ConversationReadLimits limits;
    limits.max_decoded_bytes = am::serialize_conversation_envelope(first).size();
    const auto partial = snapshots.read_history(access(), conversation(), limits);
    expect(partial.status == am::ConversationReadStatus::BudgetExhausted && partial.records.size() == 1 &&
        partial.continuation && partial.visited_records == 1, "bounded history keeps complete prefix");
    const auto rest = snapshots.read_history(access(), conversation(), {}, partial.continuation);
    expect(rest.records.at(0).envelope.metadata.ref == ref(2), "input budget continuation does not skip");
    limits = {}; limits.max_output_bytes = 1;
    const auto oversized = snapshots.read_current(access(), conversation(), limits);
    expect(oversized.status == am::ConversationReadStatus::OversizedItem && oversized.messages.empty() &&
        oversized.continuation, "current output byte bound");
    expect(current_text(snapshots.read_current(access(), conversation(), {}, oversized.continuation)) == "state-a",
        "oversized current retry keeps seek");
}

void concurrent_corrections() {
    auto config = options(); config.allocation_namespace = std::string(80, 'a');
    am::InMemoryConversationStore store(config);
    auto original = message(1); original.metadata.ref.conversation.allocation_namespace = config.allocation_namespace;
    expect(store.create_conversation(access(), original.metadata.ref.conversation).status == am::ConversationWriteStatus::Applied,
        "long namespace");
    append(store, original);
    const auto before = store.read_history(access(), original.metadata.ref.conversation).commit_generation;
    std::atomic<int> applied{0}, conflicts{0};
    auto writer = [&store, &original, &applied, &conflicts](std::string text) {
        auto corrected = original;
        corrected.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
        corrected.payload = am::ConversationPayload{am::ConversationMessageCreated{{std::move(text), {}}}};
        const auto status = store.correct(access(), corrected, original.metadata.ref.revision, "normalization").status;
        if(status == am::ConversationWriteStatus::Applied) ++applied;
        if(status == am::ConversationWriteStatus::Conflict) ++conflicts;
    };
    std::thread a(writer, "a"), b(writer, "b"); a.join(); b.join();
    const auto history = store.read_history(access(), original.metadata.ref.conversation);
    expect(applied == 1 && conflicts == 1 && history.records.size() == 2 && history.commit_generation == before + 1,
        "corrections serialize CAS and publish only one immutable revision");
}
} // namespace

int main() {
    try {
        immutable_history_and_cas(); fold_and_exact_restore(); snapshots_and_budgets();
        tool_observations_and_extensions(); storage_limits_and_concurrency();
        target_corrections_and_deleted_edits(); profiles_snapshots_and_read_edges(); concurrent_corrections();
        std::cout << "conversation reference store: PASS\n";
        return 0;
    } catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
