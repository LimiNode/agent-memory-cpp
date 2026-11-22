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
    const auto decoded = agent_memory::deserialize_conversation_entry({bytes.data(), bytes.size()});
    if(decoded.ref != entry.ref || decoded.scope != entry.scope || decoded.source_unix_us ||
       !std::holds_alternative<agent_memory::ConversationSnapshotObserved>(decoded.payload) ||
       agent_memory::serialize_conversation_entry(decoded) != bytes) return 1;

    auto call = agent_memory::make_conversation_envelope(entry);
    call.metadata.ref.entry = agent_memory::EntryId{std::uint64_t{13}};
    call.payload = agent_memory::ConversationToolCallObserved{
        {0x00, 0xff, 0x01}, "example-tool", agent_memory::ArtifactBindingId{"request@1"}};
    call.extensions = {{7, false, {0x00, 0xff}}};
    const auto call_bytes = agent_memory::serialize_conversation_envelope(call);
    const auto retained = agent_memory::deserialize_conversation_envelope({call_bytes.data(), call_bytes.size()});
    const auto& typed = agent_memory::require_typed_conversation_payload(retained);
    if(!std::holds_alternative<agent_memory::ConversationToolCallObserved>(typed) ||
       agent_memory::serialize_conversation_envelope(retained) != call_bytes) return 1;

    auto result = agent_memory::make_conversation_envelope(entry);
    result.metadata.ref.entry = agent_memory::EntryId{std::uint64_t{14}};
    result.payload = agent_memory::ConversationToolResultObserved{call.metadata.ref, {0x00, 0xff, 0x01},
        agent_memory::ConversationToolOutcome::Unknown, agent_memory::ArtifactBindingId{"result@1"}};
    const auto result_bytes = agent_memory::serialize_conversation_envelope(result);
    const auto result_read = agent_memory::deserialize_conversation_envelope({result_bytes.data(), result_bytes.size()});
    const auto* observation = std::get_if<agent_memory::ConversationToolResultObserved>(&result_read.payload);
    if(!observation || !std::holds_alternative<agent_memory::EntryRef>(observation->call) ||
       std::get<agent_memory::EntryRef>(observation->call) != call.metadata.ref ||
       observation->outcome != agent_memory::ConversationToolOutcome::Unknown) return 1;

    // A partial import retains unresolved call evidence, not a fabricated local EntryRef.
    result.payload = agent_memory::ConversationToolResultObserved{
        agent_memory::UnresolvedConversationTarget{std::nullopt, entry.source->evidence},
        {0x00, 0xff, 0x01}, agent_memory::ConversationToolOutcome::Cancelled, std::nullopt};
    const auto partial_bytes = agent_memory::serialize_conversation_envelope(result);
    const auto partial_read = agent_memory::deserialize_conversation_envelope(
        {partial_bytes.data(), partial_bytes.size()});
    const auto& partial = std::get<agent_memory::ConversationToolResultObserved>(partial_read.payload);
    if(!std::holds_alternative<agent_memory::UnresolvedConversationTarget>(partial.call) ||
       partial.result || partial.outcome != agent_memory::ConversationToolOutcome::Cancelled ||
       agent_memory::serialize_conversation_envelope(partial_read) != partial_bytes) return 1;

    // No request is executed, and result presence does not imply successful execution.
    std::cout << "Conversation event codec preserved an evidence-qualified snapshot\n";
    return 0;
}
