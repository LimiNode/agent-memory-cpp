#include "Conversation.hpp"

#include <tuple>
#include <utility>

namespace agent_memory {
namespace {

// Keep the independently qualified AMXR codec unchanged. This domain's text
// fields use the same shortest-form Unicode-scalar profile, without normalization.
bool valid_utf8(std::string_view text) noexcept {
    for(std::size_t i = 0; i < text.size();) {
        const auto lead = static_cast<unsigned char>(text[i]);
        std::size_t width = 0;
        if(lead <= 0x7fU) width = 1;
        else if(lead >= 0xc2U && lead <= 0xdfU) width = 2;
        else if(lead >= 0xe0U && lead <= 0xefU) width = 3;
        else if(lead >= 0xf0U && lead <= 0xf4U) width = 4;
        else return false;
        if(width > text.size() - i) return false;
        for(std::size_t j = 1; j < width; ++j) {
            if((static_cast<unsigned char>(text[i + j]) & 0xc0U) != 0x80U) return false;
        }
        if(width == 3 && lead == 0xe0U && static_cast<unsigned char>(text[i + 1]) < 0xa0U) return false;
        if(width == 3 && lead == 0xedU && static_cast<unsigned char>(text[i + 1]) >= 0xa0U) return false;
        if(width == 4 && lead == 0xf0U && static_cast<unsigned char>(text[i + 1]) < 0x90U) return false;
        if(width == 4 && lead == 0xf4U && static_cast<unsigned char>(text[i + 1]) >= 0x90U) return false;
        i += width;
    }
    return true;
}

bool same_occurrence(const EntryRef& a, const EntryRef& b) noexcept {
    return a.conversation == b.conversation && a.entry == b.entry;
}

class EntryValidator final {
public:
    std::vector<ConversationValidationIssue> issues;

    void issue(const std::string& field, ConversationValidationCode code) {
        issues.push_back({field, code});
    }

    void bounded_bytes(const std::string& field, std::string_view bytes, std::size_t maximum) {
        if(bytes.empty()) issue(field, ConversationValidationCode::MissingBinding);
        else if(bytes.size() > maximum) issue(field, ConversationValidationCode::LimitExceeded);
    }

    void text(const std::string& field, std::string_view value, std::size_t maximum, bool required) {
        if(value.size() > maximum) issue(field, ConversationValidationCode::LimitExceeded);
        else if(required && value.empty()) issue(field, ConversationValidationCode::InvalidEvidence);
        else if(!valid_utf8(value)) issue(field, ConversationValidationCode::InvalidUtf8);
    }

    void evidence(const std::string& field, const ConversationImportEvidence& value) {
        bounded_bytes(field + ".artifact", value.artifact.value(), kMaxConversationBindingBytes);
        text(field + ".record_locator", value.record_locator, kMaxConversationLocatorBytes, true);
    }

    void content(const std::string& field, const ConversationContent& value) {
        if(value.text) text(field + ".text", *value.text, kMaxConversationTextBytes, false);
        if((!value.text || value.text->empty()) && value.attachments.empty()) {
            issue(field, ConversationValidationCode::EmptyContent);
        }
        if(value.attachments.size() > kMaxConversationAttachments) {
            issue(field + ".attachments", ConversationValidationCode::LimitExceeded);
            return;
        }
        for(std::size_t i = 0; i < value.attachments.size(); ++i) {
            bounded_bytes(field + ".attachments[" + std::to_string(i) + "]",
                value.attachments[i].value(), kMaxConversationBindingBytes);
        }
    }

    void target(const std::string& field, const EntryRef& value, const EntryRef& self, bool same_conversation) {
        if(!is_valid_entry_ref(value)) issue(field, ConversationValidationCode::InvalidReference);
        if((same_conversation && value.conversation != self.conversation) || same_occurrence(value, self)) {
            issue(field, ConversationValidationCode::InvalidTarget);
        }
    }

    void source(const ConversationSourceBinding& value) {
        bounded_bytes("source.revision", value.revision.value(), kMaxConversationBindingBytes);
        text("source.adapter_recipe", value.adapter_recipe, kMaxConversationRecipeBytes, true);
        evidence("source.evidence", value.evidence);
        if(value.object && !is_valid_external_object_ref(*value.object)) {
            issue("source.object", ConversationValidationCode::InvalidReference);
        }
        if(value.event && !is_valid_external_object_ref(*value.event)) {
            issue("source.event", ConversationValidationCode::InvalidReference);
        }
        if(value.provider_revision_marker) {
            bounded_bytes("source.provider_revision_marker", *value.provider_revision_marker,
                kMaxConversationBindingBytes);
        }
        bool is_consistent = false;
        switch(value.identity) {
        case ConversationSourceIdentity::ExternalEvent:
            is_consistent = value.event.has_value() && !value.provider_revision_marker;
            break;
        case ConversationSourceIdentity::ObjectRevision:
            is_consistent = value.object.has_value() && value.provider_revision_marker.has_value() && !value.event;
            break;
        case ConversationSourceIdentity::LocalObservation:
            is_consistent = !value.event && !value.provider_revision_marker;
            break;
        default:
            issue("source.identity", ConversationValidationCode::InvalidEnum);
            return;
        }
        if(!is_consistent) issue("source", ConversationValidationCode::InvalidSourceIdentity);
    }
};

} // namespace

bool operator==(const ConversationRef& a, const ConversationRef& b) noexcept {
    return a.allocation_namespace == b.allocation_namespace && a.id == b.id;
}

bool operator!=(const ConversationRef& a, const ConversationRef& b) noexcept { return !(a == b); }

bool operator<(const ConversationRef& a, const ConversationRef& b) noexcept {
    return std::tie(a.allocation_namespace, a.id) < std::tie(b.allocation_namespace, b.id);
}

bool operator==(const EntryRef& a, const EntryRef& b) noexcept {
    return a.conversation == b.conversation && a.entry == b.entry && a.revision == b.revision;
}

bool operator!=(const EntryRef& a, const EntryRef& b) noexcept { return !(a == b); }

bool operator<(const EntryRef& a, const EntryRef& b) noexcept {
    return std::tie(a.conversation, a.entry, a.revision) < std::tie(b.conversation, b.entry, b.revision);
}

bool operator==(const ActorRef& a, const ActorRef& b) noexcept {
    return a.allocation_namespace == b.allocation_namespace && a.id == b.id;
}

bool operator!=(const ActorRef& a, const ActorRef& b) noexcept { return !(a == b); }

bool operator<(const ActorRef& a, const ActorRef& b) noexcept {
    return std::tie(a.allocation_namespace, a.id) < std::tie(b.allocation_namespace, b.id);
}

bool is_valid_conversation_namespace(std::string_view value) noexcept {
    if(value.empty() || value.size() > kMaxConversationNamespaceBytes || value.front() < 'a' || value.front() > 'z') {
        return false;
    }
    for(const auto c : value) {
        if((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-') continue;
        return false;
    }
    return true;
}

bool is_valid_conversation_ref(const ConversationRef& value) noexcept {
    return is_valid_conversation_namespace(value.allocation_namespace) && !value.id.empty();
}

bool is_valid_entry_ref(const EntryRef& value) noexcept {
    return is_valid_conversation_ref(value.conversation) && !value.entry.empty() && !value.revision.empty();
}

bool is_valid_actor_ref(const ActorRef& value) noexcept {
    return is_valid_conversation_namespace(value.allocation_namespace) && !value.id.empty();
}

std::vector<ConversationValidationIssue> validate_conversation_entry(const ConversationEntry& entry) {
    EntryValidator validator;
    if(!is_valid_entry_ref(entry.ref)) validator.issue("ref", ConversationValidationCode::InvalidReference);
    // ScopeId retains its existing opaque-byte semantics; do not impose namespace rules on it.
    if(entry.scope.empty()) validator.issue("scope", ConversationValidationCode::MissingScope);
    if(entry.actor && !is_valid_actor_ref(*entry.actor)) {
        validator.issue("actor", ConversationValidationCode::InvalidReference);
    }
    if(entry.observer && !is_valid_actor_ref(*entry.observer)) {
        validator.issue("observer", ConversationValidationCode::InvalidReference);
    }
    if(entry.llm_role) {
        switch(*entry.llm_role) {
        case ConversationLlmRole::System:
        case ConversationLlmRole::Developer:
        case ConversationLlmRole::User:
        case ConversationLlmRole::Assistant:
        case ConversationLlmRole::Tool:
            break;
        default:
            validator.issue("llm_role", ConversationValidationCode::InvalidEnum);
        }
    }
    if(entry.source) validator.source(*entry.source);

    if(const auto* created = std::get_if<ConversationMessageCreated>(&entry.payload)) {
        validator.content("payload.content", created->content);
    } else if(const auto* snapshot = std::get_if<ConversationSnapshotObserved>(&entry.payload)) {
        validator.content("payload.content", snapshot->content);
        if(!entry.source) validator.issue("source", ConversationValidationCode::InvalidEvidence);
    } else if(const auto* edit = std::get_if<ConversationEdited>(&entry.payload)) {
        validator.target("payload.affected", edit->affected, entry.ref, true);
        validator.content("payload.replacement", edit->replacement);
    } else if(const auto* tombstone = std::get_if<ConversationTombstoned>(&entry.payload)) {
        validator.target("payload.affected", tombstone->affected, entry.ref, true);
    } else if(const auto* restore = std::get_if<ConversationRestored>(&entry.payload)) {
        validator.target("payload.affected", restore->affected, entry.ref, true);
        validator.target("payload.retained_state", restore->retained_state, entry.ref, true);
    } else {
        // A mutable C++17 variant may be valueless_by_exception; that is not a valid payload.
        validator.issue("payload", ConversationValidationCode::EmptyContent);
    }

    if(entry.links.size() > kMaxConversationLinks) {
        validator.issue("links", ConversationValidationCode::LimitExceeded);
    } else {
        for(std::size_t i = 0; i < entry.links.size(); ++i) {
            const auto field = "links[" + std::to_string(i) + "]";
            const auto& link = entry.links[i];
            bool same_conversation = false;
            switch(link.kind) {
            case ConversationLinkKind::Reply:
            case ConversationLinkKind::Thread:
                same_conversation = true;
                break;
            case ConversationLinkKind::Branch:
            case ConversationLinkKind::Forward:
            case ConversationLinkKind::Quote:
                break;
            default:
                validator.issue(field + ".kind", ConversationValidationCode::InvalidEnum);
            }
            if(const auto* target = std::get_if<EntryRef>(&link.target)) {
                validator.target(field + ".target", *target, entry.ref, same_conversation);
            } else if(const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&link.target)) {
                validator.evidence(field + ".target.evidence", unresolved->evidence);
                if(unresolved->external && !is_valid_external_object_ref(*unresolved->external)) {
                    validator.issue(field + ".target.external", ConversationValidationCode::InvalidReference);
                }
            } else {
                validator.issue(field + ".target", ConversationValidationCode::InvalidTarget);
            }
        }
    }
    return std::move(validator.issues);
}

} // namespace agent_memory
