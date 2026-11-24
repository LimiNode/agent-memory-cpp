#include <agent_memory/ingestion.hpp>

#include <cstdint>
#include <iostream>

namespace am = agent_memory;

int main() {
    am::ConversationImportOptions options;
    options.store.allocation_namespace = "example-import";
    am::InMemoryConversationImportSink sink(options);
    am::IConversationImportSink& importer = sink;
    const am::ConversationStoreAccess access{am::ScopeId{"example-scope"}, "host-policy-v1"};
    const am::ConversationRef conversation{"example-import", am::ConversationId{std::uint64_t{1}}};
    if(importer.create_conversation(access, conversation).status != am::ConversationWriteStatus::Applied) return 1;

    // The host has already retained and admitted this immutable record artifact.
    // No upstream event identity is available: this is a snapshot, not a creation.
    am::ConversationSourceObservation record;
    record.conversation = conversation;
    record.scope = access.scope;
    record.source.revision = am::SourceRevisionId{"retained-transcript@1"};
    record.source.adapter_recipe = "example-transcript-v1";
    record.source.evidence = {am::ArtifactBindingId{"retained-record@1"}, "records/0"};
    record.producer = "example-exporter-v1";
    record.actor = am::ActorRef{"example-import", am::ActorId{std::uint64_t{2}}};
    record.observer = am::ActorRef{"example-import", am::ActorId{std::uint64_t{3}}};
    record.llm_role = am::ConversationLlmRole::User;
    record.payload = am::ConversationSourceMessage{
        am::ConversationSourceMessageKind::Snapshot, {std::string("Prefer C++17."), {}}};
    const auto admitted = importer.import_observation(access, record);
    if(admitted.status != am::ConversationWriteStatus::Applied || !admitted.ref) return 1;
    const auto replay = importer.import_observation(access, record);
    if(replay.status != am::ConversationWriteStatus::NoChange || replay.ref != admitted.ref ||
        replay.commit_generation != admitted.commit_generation) return 1;

    const auto original = importer.history().read_exact(access, *admitted.ref);
    if(original.status != am::ConversationReadStatus::Complete || original.records.size() != 1) return 1;
    auto corrected = original.records[0].envelope;
    corrected.metadata.ref.revision = am::EventRevision{std::uint64_t{2}};
    corrected.metadata.source->adapter_recipe = "example-transcript-v2";
    corrected.payload = am::ConversationPayload{
        am::ConversationSnapshotObserved{{std::string("The user prefers C++17."), {}}}};
    if(importer.correct(access, corrected, admitted.ref->revision, "normalization").status !=
        am::ConversationWriteStatus::Applied) return 1;
    // Replay still names the original admission; it cannot undo the correction.
    if(importer.import_observation(access, record).ref != admitted.ref) return 1;
    const auto current = importer.history().read_current(access, conversation);
    if(current.status != am::ConversationReadStatus::Complete || current.messages.size() != 1 ||
        !current.messages[0].is_snapshot_origin || !current.messages[0].content ||
        current.messages[0].content->text != std::optional<std::string>{"The user prefers C++17."}) return 1;
    std::cout << "source import/replay, exact evidence and CAS correction verified\n";
}
