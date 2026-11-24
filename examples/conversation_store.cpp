#include <agent_memory/storage.hpp>

#include <cstdint>
#include <iostream>
#include <optional>
#include <string>

namespace am = agent_memory;

int main() {
    am::ConversationStoreOptions options;
    options.allocation_namespace = "example-store";
    am::InMemoryConversationStore store(options);

    // The trusted host authorizes each operation; these values are not credentials.
    const am::ConversationStoreAccess access{am::ScopeId{"example-scope"}, "host-policy-v1"};
    const am::ConversationRef conversation{"example-store", am::ConversationId{std::uint64_t{7}}};
    if(store.create_conversation(access, conversation).status != am::ConversationWriteStatus::Applied) return 1;

    am::ConversationEntry entry;
    entry.ref = {conversation, am::EntryId{std::uint64_t{1}}, am::EventRevision{std::uint64_t{1}}};
    entry.scope = access.scope;
    am::ConversationSourceBinding source;
    source.revision = am::SourceRevisionId{"retained-transcript@1"};
    source.adapter_recipe = "transcript-normalization-v1";
    source.evidence = {am::ArtifactBindingId{"transcript-artifact@1"}, "records/0"};
    entry.source = source;
    entry.llm_role = am::ConversationLlmRole::User;
    entry.payload = am::ConversationSnapshotObserved{{std::string("Prefer C++17."), {}}};
    const auto original = am::make_conversation_envelope(entry);
    const auto admitted = store.append(access, original);
    if(admitted.status != am::ConversationWriteStatus::Applied) return 1;
    const auto replay = store.append(access, original);
    if(replay.status != am::ConversationWriteStatus::NoChange ||
       replay.commit_generation != admitted.commit_generation) return 1;

    auto corrected = original;
    corrected.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
    const auto unchanged = store.correct(access, corrected, entry.ref.revision, "unchanged");
    if(unchanged.status != am::ConversationWriteStatus::NoChange ||
       unchanged.commit_generation != admitted.commit_generation) return 1;

    // Correct the local representation of this occurrence; do not invent a source edit.
    corrected.metadata.source->adapter_recipe = "transcript-normalization-v2";
    corrected.payload = am::ConversationPayload{
        am::ConversationSnapshotObserved{{std::string("The user prefers C++17."), {}}}};
    const auto correction = store.correct(access, corrected, entry.ref.revision, "normalization");
    if(correction.status != am::ConversationWriteStatus::Applied || !correction.ref) return 1;
    const auto stale = store.correct(access, corrected, entry.ref.revision, "normalization");
    if(stale.status != am::ConversationWriteStatus::Conflict) return 1;

    const auto retained = store.read_exact(access, entry.ref);
    if(retained.status != am::ConversationReadStatus::Complete || retained.records.size() != 1 ||
       am::serialize_conversation_envelope(retained.records[0].envelope) !=
           am::serialize_conversation_envelope(original)) return 1;

    am::ConversationReadLimits limits;
    limits.max_items = 1;
    const auto first = store.read_history(access, conversation, limits);
    if(first.status != am::ConversationReadStatus::Partial || first.records.size() != 1 ||
       !first.continuation) return 1;
    const auto second = store.read_history(access, conversation, limits, first.continuation);
    if(second.status != am::ConversationReadStatus::Complete || second.records.size() != 1 ||
       second.commit_generation != first.commit_generation ||
       second.records[0].envelope.metadata.ref != *correction.ref || !second.records[0].correction) return 1;

    const auto current = store.read_current(access, conversation, limits);
    if(current.status != am::ConversationReadStatus::Complete || current.messages.size() != 1 ||
       !current.messages[0].is_snapshot_origin || !current.messages[0].content ||
       current.messages[0].content->text != std::optional<std::string>{"The user prefers C++17."}) return 1;

    // All evidence remains instance-local. No artifact is hydrated and nothing is executed.
    std::cout << "Conversation reference store preserved exact history and a corrected snapshot\n";
    return 0;
}
