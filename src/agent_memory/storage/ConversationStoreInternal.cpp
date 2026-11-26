#include "ConversationStoreInternal.hpp"

namespace agent_memory::detail {
namespace {

const EntryRef* affected(const ConversationEnvelope& envelope) {
    const auto* payload = std::get_if<ConversationPayload>(&envelope.payload);
    if(!payload) return nullptr;
    if(const auto* value = std::get_if<ConversationEdited>(payload)) return &value->affected;
    if(const auto* value = std::get_if<ConversationTombstoned>(payload)) return &value->affected;
    if(const auto* value = std::get_if<ConversationRestored>(payload)) return &value->affected;
    return nullptr;
}

bool same_evidence(const ConversationImportEvidence& a, const ConversationImportEvidence& b) {
    return a.artifact == b.artifact && a.record_locator == b.record_locator;
}

bool same_target(const ConversationLinkTarget& a, const ConversationLinkTarget& b) {
    if(const auto* ref = std::get_if<EntryRef>(&a)) {
        const auto* other = std::get_if<EntryRef>(&b);
        return other && *ref == *other;
    }
    const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&a);
    const auto* other = std::get_if<UnresolvedConversationTarget>(&b);
    return unresolved && other && unresolved->external == other->external &&
        same_evidence(unresolved->evidence, other->evidence);
}

} // namespace

int conversation_payload_family(const ConversationEnvelope& envelope) {
    if(const auto* value = std::get_if<ConversationPayload>(&envelope.payload)) {
        return static_cast<int>(value->index());
    }
    if(std::holds_alternative<ConversationToolCallObserved>(envelope.payload)) return 5;
    if(std::holds_alternative<ConversationToolResultObserved>(envelope.payload)) return 6;
    return -1;
}

bool same_conversation_immutable_binding(const ConversationEnvelope& a, const ConversationEnvelope& b) {
    if(a.metadata.ref.conversation != b.metadata.ref.conversation || a.metadata.ref.entry != b.metadata.ref.entry ||
        a.metadata.scope.value() != b.metadata.scope.value() || conversation_payload_family(a) != conversation_payload_family(b) ||
        !a.metadata.source || !b.metadata.source) return false;
    const auto& source = *a.metadata.source;
    const auto& other = *b.metadata.source;
    if(source.identity != other.identity || source.object != other.object || source.event != other.event ||
        source.revision != other.revision || source.provider_revision_marker != other.provider_revision_marker ||
        !same_evidence(source.evidence, other.evidence)) return false;
    if(a.metadata.links.size() != b.metadata.links.size()) return false;
    for(std::size_t i = 0; i < a.metadata.links.size(); ++i) {
        if(a.metadata.links[i].kind != b.metadata.links[i].kind ||
            !same_target(a.metadata.links[i].target, b.metadata.links[i].target)) return false;
    }
    const auto* target = affected(a);
    if(target && *target != *affected(b)) return false;
    if(conversation_payload_family(a) == 4) {
        return std::get<ConversationRestored>(std::get<ConversationPayload>(a.payload)).retained_state ==
            std::get<ConversationRestored>(std::get<ConversationPayload>(b.payload)).retained_state;
    }
    if(const auto* call = std::get_if<ConversationToolCallObserved>(&a.payload)) {
        const auto& old = std::get<ConversationToolCallObserved>(b.payload);
        return call->call_id == old.call_id && call->tool_name == old.tool_name && call->request == old.request;
    }
    if(const auto* result = std::get_if<ConversationToolResultObserved>(&a.payload)) {
        const auto& old = std::get<ConversationToolResultObserved>(b.payload);
        return result->call_id == old.call_id && same_target(result->call, old.call);
    }
    return true;
}

} // namespace agent_memory::detail
