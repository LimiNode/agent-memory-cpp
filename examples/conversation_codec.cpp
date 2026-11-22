#include <agent_memory/domain.hpp>

#include <cstdint>
#include <iostream>

int main() {
    agent_memory::ConversationEntry entry;
    entry.ref = {{"example-store", agent_memory::ConversationId{std::uint64_t{7}}},
        agent_memory::EntryId{std::uint64_t{12}}, agent_memory::EventRevision{std::uint64_t{1}}};
    entry.scope = agent_memory::ScopeId{"example-scope"};
    agent_memory::ConversationSourceBinding source;
    source.revision = agent_memory::SourceRevisionId{"retained-record@1"};
    source.adapter_recipe = "normalized-snapshot-v1";
    source.evidence = {agent_memory::ArtifactBindingId{"snapshot-artifact@1"}, "records/0"};
    entry.source = source;
    entry.observed_unix_us = std::int64_t{1700000000000000};
    entry.payload = agent_memory::ConversationSnapshotObserved{
        {std::nullopt, {agent_memory::ArtifactBindingId{"attachment@1"}}}};

    const auto bytes = agent_memory::serialize_conversation_entry(entry);
    const auto decoded = agent_memory::deserialize_conversation_entry(bytes);
    if(decoded.ref != entry.ref || decoded.scope != entry.scope || decoded.source_unix_us ||
       !std::holds_alternative<agent_memory::ConversationSnapshotObserved>(decoded.payload) ||
       agent_memory::serialize_conversation_entry(decoded) != bytes) return 1;

    // Encoding values does not append a journal, allocate IDs or prove upstream history.
    std::cout << "Conversation event codec preserved an evidence-qualified snapshot\n";
    return 0;
}
