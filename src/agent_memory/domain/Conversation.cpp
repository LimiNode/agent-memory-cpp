#include "Conversation.hpp"
#include "ConversationEnvelope.hpp"

#include <limits>
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
    explicit EntryValidator(const ConversationFieldLimits& limits) : m_limits(limits) {}
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
        bounded_bytes(field + ".artifact", value.artifact.value(), m_limits.max_binding_bytes);
        text(field + ".record_locator", value.record_locator, m_limits.max_locator_bytes, true);
    }

    void content(const std::string& field, const ConversationContent& value) {
        if(value.text) text(field + ".text", *value.text, m_limits.max_text_bytes, false);
        if((!value.text || value.text->empty()) && value.attachments.empty()) {
            issue(field, ConversationValidationCode::EmptyContent);
        }
        if(value.attachments.size() > m_limits.max_attachments) {
            issue(field + ".attachments", ConversationValidationCode::LimitExceeded);
            return;
        }
        for(std::size_t i = 0; i < value.attachments.size(); ++i) {
            bounded_bytes(field + ".attachments[" + std::to_string(i) + "]",
                value.attachments[i].value(), m_limits.max_binding_bytes);
        }
    }

    void target(const std::string& field, const EntryRef& value, const EntryRef& self, bool same_conversation) {
        if(!is_valid_entry_ref(value)) issue(field, ConversationValidationCode::InvalidReference);
        if((same_conversation && value.conversation != self.conversation) || same_occurrence(value, self)) {
            issue(field, ConversationValidationCode::InvalidTarget);
        }
    }

    void target(const std::string& field, const ConversationLinkTarget& value,
        const EntryRef& self, bool same_conversation) {
        if(const auto* resolved = std::get_if<EntryRef>(&value)) {
            target(field, *resolved, self, same_conversation);
        } else if(const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&value)) {
            evidence(field + ".evidence", unresolved->evidence);
            if(unresolved->external && !is_valid_external_object_ref(*unresolved->external)) {
                issue(field + ".external", ConversationValidationCode::InvalidReference);
            }
        } else {
            issue(field, ConversationValidationCode::InvalidTarget);
        }
    }

    void source(const ConversationSourceBinding& value) {
        bounded_bytes("source.revision", value.revision.value(), m_limits.max_binding_bytes);
        text("source.adapter_recipe", value.adapter_recipe, m_limits.max_recipe_bytes, true);
        evidence("source.evidence", value.evidence);
        if(value.object && !is_valid_external_object_ref(*value.object)) {
            issue("source.object", ConversationValidationCode::InvalidReference);
        }
        if(value.event && !is_valid_external_object_ref(*value.event)) {
            issue("source.event", ConversationValidationCode::InvalidReference);
        }
        if(value.provider_revision_marker) {
            bounded_bytes("source.provider_revision_marker", *value.provider_revision_marker,
                m_limits.max_binding_bytes);
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

    template<typename Metadata>
    void metadata(const Metadata& value) {
        if(!is_valid_entry_ref(value.ref)) issue("ref", ConversationValidationCode::InvalidReference);
        // Scope retains opaque bytes and has no independent domain length cap.
        if(value.scope.empty()) issue("scope", ConversationValidationCode::MissingScope);
        if(value.actor && !is_valid_actor_ref(*value.actor)) {
            issue("actor", ConversationValidationCode::InvalidReference);
        }
        if(value.observer && !is_valid_actor_ref(*value.observer)) {
            issue("observer", ConversationValidationCode::InvalidReference);
        }
        if(value.llm_role) {
            switch(*value.llm_role) {
            case ConversationLlmRole::System:
            case ConversationLlmRole::Developer:
            case ConversationLlmRole::User:
            case ConversationLlmRole::Assistant:
            case ConversationLlmRole::Tool:
                break;
            default:
                issue("llm_role", ConversationValidationCode::InvalidEnum);
            }
        }
        if(value.source) source(*value.source);
    }

    template<typename Metadata>
    void legacy_payload(const ConversationPayload& payload, const Metadata& value) {
        if(const auto* created = std::get_if<ConversationMessageCreated>(&payload)) {
            content("payload.content", created->content);
        } else if(const auto* snapshot = std::get_if<ConversationSnapshotObserved>(&payload)) {
            content("payload.content", snapshot->content);
            if(!value.source) issue("source", ConversationValidationCode::InvalidEvidence);
        } else if(const auto* edit = std::get_if<ConversationEdited>(&payload)) {
            target("payload.affected", edit->affected, value.ref, true);
            content("payload.replacement", edit->replacement);
        } else if(const auto* tombstone = std::get_if<ConversationTombstoned>(&payload)) {
            target("payload.affected", tombstone->affected, value.ref, true);
        } else if(const auto* restore = std::get_if<ConversationRestored>(&payload)) {
            target("payload.affected", restore->affected, value.ref, true);
            target("payload.retained_state", restore->retained_state, value.ref, true);
        } else {
            issue("payload", ConversationValidationCode::EmptyContent);
        }
    }

    template<typename Metadata>
    void links(const Metadata& value) {
        if(value.links.size() > m_limits.max_links) {
            issue("links", ConversationValidationCode::LimitExceeded);
            return;
        }
        for(std::size_t i = 0; i < value.links.size(); ++i) {
            const auto field = "links[" + std::to_string(i) + "]";
            const auto& link = value.links[i];
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
                issue(field + ".kind", ConversationValidationCode::InvalidEnum);
            }
            target(field + ".target", link.target, value.ref, same_conversation);
        }
    }

    void call_id(const ByteBuffer& bytes) {
        if(bytes.empty()) issue("payload.call_id", ConversationValidationCode::MissingBinding);
        else if(bytes.size() > m_limits.max_binding_bytes) {
            issue("payload.call_id", ConversationValidationCode::LimitExceeded);
        }
    }

private:
    const ConversationFieldLimits& m_limits;
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
    return validate_conversation_entry_with_limits(entry, ConversationFieldLimits{});
}

bool is_supported_conversation_payload(std::uint32_t type_id, std::uint16_t schema_version) noexcept {
    return schema_version == 1 && ((type_id >= 1 && type_id <= 5) || type_id == 256 || type_id == 257);
}

ConversationSourceBinding copy_conversation_source_binding(const ConversationSourceBinding& value) {
    std::optional<ExternalObjectRef> object, event;
    if(value.object) object = copy_external_object_ref(*value.object);
    if(value.event) event = copy_external_object_ref(*value.event);
    return {value.identity, std::move(object), std::move(event), value.revision,
        value.provider_revision_marker, value.adapter_recipe, value.evidence};
}

UnresolvedConversationTarget copy_unresolved_conversation_target(const UnresolvedConversationTarget& unresolved) {
    std::optional<ExternalObjectRef> external;
    if(unresolved.external) external = copy_external_object_ref(*unresolved.external);
    return {std::move(external), unresolved.evidence};
}

ConversationLinkTarget copy_conversation_link_target(const ConversationLinkTarget& value) {
    if(const auto* exact = std::get_if<EntryRef>(&value))
        return ConversationLinkTarget{std::in_place_index<0>, *exact};
    return copy_unresolved_conversation_target(std::get<UnresolvedConversationTarget>(value));
}

ConversationMetadata copy_conversation_metadata(const ConversationMetadata& value) {
    std::optional<ConversationSourceBinding> source;
    if(value.source) source = copy_conversation_source_binding(*value.source);
    std::vector<ConversationLink> links;
    links.reserve(value.links.size());
    for(const auto& link : value.links)
        links.push_back({link.kind, copy_conversation_link_target(link.target)});
    return {value.ref, value.scope, std::move(source), value.actor, value.llm_role, value.observer,
        value.source_unix_us, value.observed_unix_us, std::move(links)};
}

ConversationEnvelope copy_conversation_envelope(const ConversationEnvelope& value) {
    ConversationEnvelope result;
    result.metadata = copy_conversation_metadata(value.metadata);
    if(const auto* legacy = std::get_if<ConversationPayload>(&value.payload)) {
        result.payload = ConversationEnvelopePayload{std::in_place_index<0>, *legacy};
    } else if(const auto* call = std::get_if<ConversationToolCallObserved>(&value.payload)) {
        result.payload = ConversationEnvelopePayload{std::in_place_index<1>, *call};
    } else if(const auto* observed = std::get_if<ConversationToolResultObserved>(&value.payload)) {
        result.payload = ConversationToolResultObserved{copy_conversation_link_target(observed->call),
            observed->call_id, observed->outcome, observed->result};
    } else {
        result.payload = ConversationEnvelopePayload{std::in_place_index<3>,
            std::get<ConversationOpaquePayload>(value.payload)};
    }
    result.extensions = value.extensions;
    return result;
}

std::vector<ConversationValidationIssue> validate_conversation_entry_with_limits(
    const ConversationEntry& entry, const ConversationFieldLimits& limits) {
    EntryValidator validator(limits);
    validator.metadata(entry);
    validator.legacy_payload(entry.payload, entry);
    validator.links(entry);
    return std::move(validator.issues);
}

std::vector<ConversationValidationIssue> validate_conversation_envelope(
    const ConversationEnvelope& envelope, const ConversationFieldLimits& limits) {
    EntryValidator validator(limits);
    const auto& metadata = envelope.metadata;
    validator.metadata(metadata);
    if(const auto* legacy = std::get_if<ConversationPayload>(&envelope.payload)) {
        validator.legacy_payload(*legacy, metadata);
    } else if(const auto* call = std::get_if<ConversationToolCallObserved>(&envelope.payload)) {
        if(!metadata.source) validator.issue("source", ConversationValidationCode::InvalidEvidence);
        validator.call_id(call->call_id);
        validator.text("payload.tool_name", call->tool_name, limits.max_recipe_bytes, true);
        validator.bounded_bytes("payload.request", call->request.value(), limits.max_binding_bytes);
    } else if(const auto* result = std::get_if<ConversationToolResultObserved>(&envelope.payload)) {
        if(!metadata.source) validator.issue("source", ConversationValidationCode::InvalidEvidence);
        validator.target("payload.call", result->call, metadata.ref, true);
        validator.call_id(result->call_id);
        if(result->result) {
            validator.bounded_bytes("payload.result", result->result->value(), limits.max_binding_bytes);
        }
        switch(result->outcome) {
        case ConversationToolOutcome::Unknown:
        case ConversationToolOutcome::Succeeded:
        case ConversationToolOutcome::Failed:
        case ConversationToolOutcome::Cancelled:
            break;
        default:
            validator.issue("payload.outcome", ConversationValidationCode::InvalidEnum);
        }
    } else if(const auto* opaque = std::get_if<ConversationOpaquePayload>(&envelope.payload)) {
        const bool known_pair = is_supported_conversation_payload(opaque->type_id, opaque->schema_version);
        if(opaque->type_id == 0 || opaque->type_id == std::numeric_limits<std::uint32_t>::max() ||
           opaque->schema_version == 0 || known_pair) {
            validator.issue("payload", ConversationValidationCode::InvalidEnum);
        }
    } else {
        validator.issue("payload", ConversationValidationCode::EmptyContent);
    }
    validator.links(metadata);
    std::uint32_t previous = 0;
    for(std::size_t i = 0; i < envelope.extensions.size(); ++i) {
        const auto id = envelope.extensions[i].id;
        if(id == 0 || id <= previous) {
            validator.issue("extensions[" + std::to_string(i) + "].id", ConversationValidationCode::InvalidReference);
        }
        previous = id;
    }
    return std::move(validator.issues);
}

} // namespace agent_memory
