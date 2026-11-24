#include <agent_memory/storage/ConversationStore.hpp>

#include <iostream>
#include <stdexcept>

namespace am = agent_memory;
namespace {
void expect(bool value, const char* message) { if(!value) throw std::runtime_error(message); }
am::ConversationRef conversation() { return {"revision-test", am::ConversationId{std::uint64_t{1}}}; }
am::EntryRef ref(std::uint64_t id, std::uint64_t revision = 1) {
    return {conversation(), am::EntryId{id}, am::EventRevision{revision}};
}
am::ConversationStoreAccess access() { return {am::ScopeId{"scope"}, "host.v1"}; }
am::ConversationStoreOptions options() {
    am::ConversationStoreOptions value;
    value.allocation_namespace = "revision-test";
    return value;
}
am::ConversationEnvelope message(std::uint64_t id, std::string text, std::uint64_t revision = 1) {
    am::ConversationEntry entry;
    entry.ref = ref(id, revision);
    entry.scope = access().scope;
    am::ConversationSourceBinding source;
    source.revision = am::SourceRevisionId{"source@1"};
    source.adapter_recipe = "normalizer.v1";
    source.evidence = {am::ArtifactBindingId{"archive"}, "row:" + std::to_string(id)};
    entry.source = std::move(source);
    entry.payload = am::ConversationMessageCreated{{std::move(text), {}}};
    return am::make_conversation_envelope(std::move(entry));
}
void append(am::InMemoryConversationStore& store, const am::ConversationEnvelope& value) {
    expect(store.append(access(), value).status == am::ConversationWriteStatus::Applied, "append");
}
void correct(am::InMemoryConversationStore& store, std::uint64_t id, std::uint64_t revision,
    std::string text) {
    expect(store.correct(access(), message(id, std::move(text), revision),
        am::EventRevision{revision - 1}, "normalization").status == am::ConversationWriteStatus::Applied,
        "correction");
}
am::ConversationEnvelope edit(std::uint64_t id, am::EntryRef target, std::string text) {
    auto value = message(id, "unused");
    value.payload = am::ConversationPayload{am::ConversationEdited{std::move(target), {std::move(text), {}}}};
    return value;
}
void exact_revisions_do_not_invent_distinct_occurrences() {
    am::InMemoryConversationStore store(options());
    expect(store.create_conversation(access(), conversation()).status == am::ConversationWriteStatus::Applied,
        "create");
    append(store, message(1, "root-v1"));
    append(store, message(2, "independent root"));
    am::ConversationReadLimits one;
    one.max_items = 1;
    const auto pinned = store.read_current(access(), conversation(), one);
    expect(pinned.continuation.has_value(), "pinned frontier");
    expect(pinned.messages.at(0).origin == ref(1) && pinned.messages[0].content->text == "root-v1",
        "frontier before correction retains original representation");
    append(store, edit(3, ref(1), "descendant"));
    correct(store, 1, 2, "root-v2");
    correct(store, 1, 3, "root-v3");
    auto current = store.read_current(access(), conversation());
    expect(current.messages.at(0).state == ref(3) && current.messages[0].origin == ref(1, 3) &&
        current.messages[0].content->text == "descendant", "correction does not replace descendant");
    const auto resume = store.read_current(access(), conversation(), one, pinned.continuation);
    expect(resume.commit_generation == pinned.commit_generation && resume.messages.size() == 1 &&
        resume.messages[0].origin == ref(2), "correction cannot alter pinned frontier");

    // Different representation revisions of one occurrence do not create two heads.
    append(store, edit(4, ref(1, 2), "sibling targeting revision two"));
    current = store.read_current(access(), conversation());
    expect(current.messages[0].status == am::ConversationMessageState::Ambiguous &&
        current.messages[0].state.entry.empty() && !current.messages[0].content,
        "historical revision sibling is ambiguous, not merged or last-writer-wins");
    const auto original = store.read_exact(access(), ref(1));
    const auto second = store.read_exact(access(), ref(1, 2));
    expect(std::get<am::ConversationMessageCreated>(std::get<am::ConversationPayload>(original.records[0].envelope.payload))
        .content.text == "root-v1", "original immutable");
    expect(std::get<am::ConversationMessageCreated>(std::get<am::ConversationPayload>(second.records[0].envelope.payload))
        .content.text == "root-v2", "historical correction immutable");
    expect(std::get<am::ConversationEdited>(std::get<am::ConversationPayload>(
        store.read_exact(access(), ref(4)).records[0].envelope.payload)).affected == ref(1, 2),
        "exact revision binding retained");
}
void corrected_head_can_advance_and_restore_is_exact() {
    am::InMemoryConversationStore store(options());
    (void)store.create_conversation(access(), conversation());
    append(store, message(1, "old"));
    correct(store, 1, 2, "new");
    append(store, edit(2, ref(1), "linear"));
    expect(store.read_current(access(), conversation()).messages[0].state == ref(2),
        "historical representation of current occurrence may advance it");
    auto tombstone = message(3, "unused");
    tombstone.payload = am::ConversationPayload{am::ConversationTombstoned{ref(2)}};
    append(store, tombstone);
    auto restore = message(4, "unused");
    restore.payload = am::ConversationPayload{am::ConversationRestored{ref(3), ref(1)}};
    append(store, restore);
    const auto restored = store.read_current(access(), conversation());
    expect(restored.messages[0].content_ref == ref(1) && restored.messages[0].content->text == "old",
        "restore reads exact historical revision, not corrected latest");
    append(store, edit(5, ref(3), "non-head edit"));
    expect(store.read_current(access(), conversation()).messages[0].status == am::ConversationMessageState::Ambiguous,
        "edit of tombstoned/non-head source does not restore implicitly");
    const auto before = store.read_history(access(), conversation());
    expect(store.append(access(), edit(6, ref(1, 99), "invalid")).status ==
        am::ConversationWriteStatus::InvalidRequest, "nonexistent exact revision rejected");
    auto other = ref(1);
    other.conversation = {"revision-test", am::ConversationId{std::uint64_t{2}}};
    expect(store.append(access(), edit(6, other, "cross conversation")).status ==
        am::ConversationWriteStatus::InvalidRequest, "cross-conversation state rejected");
    auto denied = access();
    denied.scope = am::ScopeId{"other"};
    expect(store.read_exact(denied, ref(1)).records.empty(), "denied target leaks no record");
    const auto after = store.read_history(access(), conversation());
    expect(before.commit_generation == after.commit_generation && before.records.size() == after.records.size(),
        "invalid targets change no history or frontier");
}
}
int main() {
    exact_revisions_do_not_invent_distinct_occurrences();
    corrected_head_can_advance_and_restore_is_exact();
    std::cout << "conversation revision targets: PASS\n";
}
