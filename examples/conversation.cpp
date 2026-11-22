#include <agent_memory/domain.hpp>

#include <cstdint>
#include <iostream>

int main() {
    // The message identity does not identify an individual creation or edit event.
    const agent_memory::ExternalObjectRef message{
        "org.telegram", "api.telegram.org", "public", "message",
        agent_memory::ExternalObjectKey{std::int64_t{-100123}},
        agent_memory::ExternalObjectKey{std::uint64_t{42}}};

    agent_memory::ConversationSourceBinding source;
    source.identity = agent_memory::ConversationSourceIdentity::LocalObservation;
    source.object = message;
    source.revision = agent_memory::SourceRevisionId{"normalized-import@1"};
    source.adapter_recipe = "normalized-chat-snapshot-v1";
    source.evidence = {agent_memory::ArtifactBindingId{"retained-record@1"}, "records/0"};
    // No provider event ID or reliable upstream revision marker was observed.

    agent_memory::ConversationEntry entry;
    entry.ref = {{"example-store", agent_memory::ConversationId{std::uint64_t{7}}},
        agent_memory::EntryId{std::uint64_t{12}}, agent_memory::EventRevision{std::uint64_t{1}}};
    entry.scope = agent_memory::ScopeId{"example-scope"};
    entry.source = source;
    entry.actor = agent_memory::ActorRef{"example-store", agent_memory::ActorId{std::uint64_t{3}}};
    entry.llm_role = agent_memory::ConversationLlmRole::User;
    entry.observer = agent_memory::ActorRef{"example-store", agent_memory::ActorId{std::uint64_t{9}}};
    entry.observed_unix_us = std::int64_t{1700000001000000};
    // Unknown source time stays absent; actor, recorded role and observer are independent.
    agent_memory::ConversationSnapshotObserved snapshot;
    snapshot.content.attachments.emplace_back("attachment@1");
    entry.payload = snapshot;

    const auto issues = agent_memory::validate_conversation_entry(entry);
    if(!issues.empty()) {
        std::cerr << "Snapshot field validation failed with " << issues.size() << " issue(s)\n";
        return 1;
    }

    // This checks values only: no store, provider history, dedupe or admission is supplied.
    std::cout << "Snapshot fields validated without inventing source change history\n";
    return 0;
}
