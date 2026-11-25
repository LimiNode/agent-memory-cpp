#include "ConversationSourceImporterInternal.hpp"

#include <tuple>
#include <utility>

namespace agent_memory::detail {
namespace {

constexpr std::size_t kObservationByteCap = kConversationImportObservationByteCap;
constexpr std::size_t kProducerByteCap = 1024;

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

bool valid_outcome(ConversationToolOutcome outcome) noexcept {
    switch(outcome) {
    case ConversationToolOutcome::Unknown:
    case ConversationToolOutcome::Succeeded:
    case ConversationToolOutcome::Failed:
    case ConversationToolOutcome::Cancelled: return true;
    default: return false;
    }
}

bool valid_origin(ConversationSourceResultOrigin origin) noexcept {
    switch(origin) {
    case ConversationSourceResultOrigin::Reported:
    case ConversationSourceResultOrigin::PermissionDenied:
    case ConversationSourceResultOrigin::Recovery:
    case ConversationSourceResultOrigin::Timeout:
    case ConversationSourceResultOrigin::Repaired:
    case ConversationSourceResultOrigin::Unknown: return true;
    default: return false;
    }
}

/// Logical charging is not a wire format or a heap/RSS claim. Each record has
/// 128 bytes for scalar values/tags; nested source/target records add 32 bytes.
/// Each owned variable-length value adds four framing bytes and its byte length.
struct ObservationPreflight final {
    const ConversationFieldLimits& fields;
    std::size_t limit;
    std::size_t bytes = 0;
    ConversationWriteStatus status = ConversationWriteStatus::Applied;

    bool add(std::size_t amount) {
        if(status != ConversationWriteStatus::Applied) return false;
        if(amount > limit - bytes) {
            status = ConversationWriteStatus::ResourceLimit;
            return false;
        }
        bytes += amount;
        return true;
    }

    bool value(std::size_t size, std::size_t maximum = kObservationByteCap) {
        if(size > maximum) {
            status = ConversationWriteStatus::ResourceLimit;
            return false;
        }
        return add(4) && add(size);
    }

    bool external(const ExternalObjectRef& ref) {
        if(!is_valid_external_object_ref(ref)) {
            status = ConversationWriteStatus::InvalidRequest;
            return false;
        }
        // Standalone AMXR's direct byte API retains the independent 64 KiB frame bound.
        return value(serialize_external_object_ref_bytes(ref).size());
    }

    bool evidence(const ConversationImportEvidence& item) {
        return value(item.artifact.value().size(), fields.max_binding_bytes) &&
            value(item.record_locator.size(), fields.max_locator_bytes);
    }

    bool source(const ConversationSourceBinding& item) {
        return add(32) && (!item.object || external(*item.object)) &&
            (!item.event || external(*item.event)) &&
            value(item.revision.value().size(), fields.max_binding_bytes) &&
            (!item.provider_revision_marker || value(item.provider_revision_marker->size(), fields.max_binding_bytes)) &&
            value(item.adapter_recipe.size(), fields.max_recipe_bytes) && evidence(item.evidence);
    }

    bool conversation(const ConversationRef& ref) {
        return value(ref.allocation_namespace.size(), kMaxConversationNamespaceBytes);
    }

    bool target(const ConversationSourceCallTarget& item) {
        if(!add(32)) return false;
        if(const auto* ref = std::get_if<EntryRef>(&item)) return conversation(ref->conversation);
        if(const auto* source_target = std::get_if<ConversationSourceTarget>(&item)) {
            return conversation(source_target->conversation) && value(source_target->scope.value().size()) &&
                source(source_target->source);
        }
        if(const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&item)) {
            return (!unresolved->external || external(*unresolved->external)) && evidence(unresolved->evidence);
        }
        status = ConversationWriteStatus::InvalidRequest;
        return false;
    }

    bool observation(const ConversationSourceObservation& item) {
        if(!add(128) || !conversation(item.conversation) || !value(item.scope.value().size()) ||
            !source(item.source) || !value(item.producer.size(), kProducerByteCap) ||
            (item.actor && !value(item.actor->allocation_namespace.size(), kMaxConversationNamespaceBytes)) ||
            (item.observer && !value(item.observer->allocation_namespace.size(), kMaxConversationNamespaceBytes))) return false;
        if(const auto* message = std::get_if<ConversationSourceMessage>(&item.payload)) {
            // Reject oversized vectors before inspecting any element.
            if(message->content.attachments.size() > fields.max_attachments) {
                status = ConversationWriteStatus::ResourceLimit;
                return false;
            }
            if(message->content.text && !value(message->content.text->size(), fields.max_text_bytes)) return false;
            for(const auto& attachment : message->content.attachments) {
                if(!value(attachment.value().size(), fields.max_binding_bytes)) return false;
            }
            if(message->kind != ConversationSourceMessageKind::Created &&
                message->kind != ConversationSourceMessageKind::Snapshot) status = ConversationWriteStatus::InvalidRequest;
        } else if(const auto* call = std::get_if<ConversationSourceToolCall>(&item.payload)) {
            if(!value(call->call_id.size(), fields.max_binding_bytes) ||
                !value(call->tool_name.size(), fields.max_recipe_bytes) ||
                !value(call->original_request.value().size(), fields.max_binding_bytes) ||
                (call->effective_request && !value(call->effective_request->value().size(), fields.max_binding_bytes))) return false;
            if(call->effective_request && call->effective_request->empty()) status = ConversationWriteStatus::InvalidRequest;
        } else if(const auto* result = std::get_if<ConversationSourceToolResult>(&item.payload)) {
            if(!target(result->call) || !value(result->call_id.size(), fields.max_binding_bytes) ||
                (result->result && !value(result->result->value().size(), fields.max_binding_bytes))) return false;
            if(!valid_origin(result->origin) || !valid_outcome(result->reported_outcome)) {
                status = ConversationWriteStatus::InvalidRequest;
            }
        } else status = ConversationWriteStatus::InvalidRequest;
        return status == ConversationWriteStatus::Applied;
    }
};

bool same_evidence(const ConversationImportEvidence& a, const ConversationImportEvidence& b) {
    return a.artifact == b.artifact && a.record_locator == b.record_locator;
}

bool same_source(const ConversationSourceBinding& a, const ConversationSourceBinding& b) {
    return a.identity == b.identity && a.object == b.object && a.event == b.event && a.revision == b.revision &&
        a.provider_revision_marker == b.provider_revision_marker && a.adapter_recipe == b.adapter_recipe &&
        same_evidence(a.evidence, b.evidence);
}

bool same_unresolved(const UnresolvedConversationTarget& a, const UnresolvedConversationTarget& b) {
    return a.external == b.external && same_evidence(a.evidence, b.evidence);
}

bool same_target(const ConversationSourceCallTarget& a, const ConversationSourceCallTarget& b) {
    if(a.index() != b.index()) return false;
    if(const auto* ref = std::get_if<EntryRef>(&a)) return *ref == std::get<EntryRef>(b);
    if(const auto* source_target = std::get_if<ConversationSourceTarget>(&a)) {
        const auto& other = std::get<ConversationSourceTarget>(b);
        return source_target->conversation == other.conversation && source_target->scope.value() == other.scope.value() &&
            same_source(source_target->source, other.source);
    }
    const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&a);
    return unresolved && same_unresolved(*unresolved, std::get<UnresolvedConversationTarget>(b));
}

ConversationSourceCallTarget copy_source_target(const ConversationSourceCallTarget& target) {
    if(const auto* ref = std::get_if<EntryRef>(&target)) return *ref;
    if(const auto* source = std::get_if<ConversationSourceTarget>(&target)) {
        return ConversationSourceTarget{source->conversation, source->scope,
            copy_conversation_source_binding(source->source)};
    }
    return copy_unresolved_conversation_target(std::get<UnresolvedConversationTarget>(target));
}

ConversationLinkTarget fallback_target(const ConversationSourceCallTarget& target) {
    if(const auto* ref = std::get_if<EntryRef>(&target)) return *ref;
    if(const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&target))
        return copy_unresolved_conversation_target(*unresolved);
    return unresolved_source_target(std::get<ConversationSourceTarget>(target));
}

} // namespace

ConversationWriteStatus source_status(const ConversationSourceBinding& source,
    const ConversationRef& conversation, const ScopeId& scope, const ConversationFieldLimits& limits) {
    // Reuse the source-field owner instead of maintaining a second mode/UTF-8 validator.
    ConversationEntry probe;
    probe.ref = {conversation, EntryId{std::uint64_t{1}}, EventRevision{std::uint64_t{1}}};
    probe.scope = scope;
    probe.source = copy_conversation_source_binding(source);
    // A field-only target avoids depending on a consumer's inline-text budget.
    probe.payload = ConversationTombstoned{{conversation, EntryId{std::uint64_t{2}}, EventRevision{std::uint64_t{1}}}};
    const auto issues = validate_conversation_entry_with_limits(probe, limits);
    if(issues.empty()) return ConversationWriteStatus::Applied;
    for(const auto& issue : issues) {
        if(issue.code == ConversationValidationCode::LimitExceeded) return ConversationWriteStatus::ResourceLimit;
    }
    return ConversationWriteStatus::InvalidRequest;
}

bool same_witness(const ConversationSourceObservation& a, const ConversationSourceObservation& b) {
    if(a.conversation != b.conversation || a.scope.value() != b.scope.value() || !same_source(a.source, b.source) ||
        a.producer != b.producer || a.actor != b.actor || a.observer != b.observer || a.llm_role != b.llm_role ||
        a.source_unix_us != b.source_unix_us || a.observed_unix_us != b.observed_unix_us ||
        a.payload.index() != b.payload.index()) return false;
    if(const auto* message = std::get_if<ConversationSourceMessage>(&a.payload)) {
        const auto& other = std::get<ConversationSourceMessage>(b.payload);
        return message->kind == other.kind && message->content.text == other.content.text &&
            message->content.attachments == other.content.attachments;
    }
    if(const auto* call = std::get_if<ConversationSourceToolCall>(&a.payload)) {
        const auto& other = std::get<ConversationSourceToolCall>(b.payload);
        return call->call_id == other.call_id && call->tool_name == other.tool_name &&
            call->original_request == other.original_request && call->effective_request == other.effective_request;
    }
    const auto* result = std::get_if<ConversationSourceToolResult>(&a.payload);
    if(!result) return false;
    const auto& other = std::get<ConversationSourceToolResult>(b.payload);
    return same_target(result->call, other.call) && result->call_id == other.call_id &&
        result->origin == other.origin && result->reported_outcome == other.reported_outcome &&
        result->result == other.result && result->is_error == other.is_error && result->exit_code == other.exit_code;
}

bool SourceKey::operator<(const SourceKey& other) const {
    return std::tie(identity, external, revision, marker, artifact, locator) <
        std::tie(other.identity, other.external, other.revision, other.marker, other.artifact, other.locator);
}

std::size_t SourceKey::charge() const {
    return 32 + external.size() + revision.value().size() + (marker ? marker->size() : 0) +
        artifact.value().size() + locator.size();
}

SourceKey source_key(const ConversationSourceBinding& source) {
    SourceKey key;
    key.identity = source.identity;
    key.revision = source.revision;
    if(source.identity == ConversationSourceIdentity::LocalObservation) {
        key.artifact = source.evidence.artifact;
        key.locator = source.evidence.record_locator;
    } else {
        const auto& external = source.identity == ConversationSourceIdentity::ExternalEvent ? *source.event : *source.object;
        key.external = serialize_external_object_ref_bytes(external);
        if(source.identity == ConversationSourceIdentity::ObjectRevision) key.marker = source.provider_revision_marker;
    }
    return key;
}

ConversationSourceObservation copy_observation(const ConversationSourceObservation& input) {
    ConversationSourcePayload payload;
    if(const auto* message = std::get_if<ConversationSourceMessage>(&input.payload)) {
        payload = ConversationSourcePayload{std::in_place_index<0>, *message};
    } else if(const auto* call = std::get_if<ConversationSourceToolCall>(&input.payload)) {
        payload = ConversationSourcePayload{std::in_place_index<1>, *call};
    } else {
        const auto& result = std::get<ConversationSourceToolResult>(input.payload);
        payload = ConversationSourceToolResult{copy_source_target(result.call), result.call_id,
            result.origin, result.reported_outcome, result.result, result.is_error, result.exit_code};
    }
    return {input.conversation, input.scope, copy_conversation_source_binding(input.source), input.producer,
        input.actor, input.observer, input.llm_role, input.source_unix_us, input.observed_unix_us, std::move(payload)};
}

ObservationPreflightResult preflight_observation(const ConversationSourceObservation& observation,
    const ConversationFieldLimits& fields, std::size_t max_observation_bytes) {
    ObservationPreflight preflight{fields, max_observation_bytes};
    if(!preflight.observation(observation)) return {preflight.status, preflight.bytes};
    if(observation.producer.empty() || !valid_utf8(observation.producer))
        return {ConversationWriteStatus::InvalidRequest, preflight.bytes};
    return {ConversationWriteStatus::Applied, preflight.bytes};
}

ConversationWriteStatus observation_status(const ConversationSourceObservation& observation) {
    if(const auto* message = std::get_if<ConversationSourceMessage>(&observation.payload)) {
        if(message->kind == ConversationSourceMessageKind::Created &&
            observation.source.identity == ConversationSourceIdentity::LocalObservation)
            return ConversationWriteStatus::InvalidRequest;
    }
    return ConversationWriteStatus::Applied;
}

ConversationWriteStatus target_status(const ConversationSourceObservation& observation,
    const ConversationFieldLimits& fields) {
    const auto* result = std::get_if<ConversationSourceToolResult>(&observation.payload);
    if(!result) return ConversationWriteStatus::Applied;
    if(const auto* target = std::get_if<ConversationSourceTarget>(&result->call)) {
        if(target->scope.value() != observation.scope.value()) return ConversationWriteStatus::Denied;
        if(target->conversation != observation.conversation) return ConversationWriteStatus::InvalidRequest;
        return source_status(target->source, target->conversation, target->scope, fields);
    }
    if(const auto* target = std::get_if<EntryRef>(&result->call)) {
        if(target->conversation != observation.conversation) return ConversationWriteStatus::InvalidRequest;
    }
    return ConversationWriteStatus::Applied;
}

UnresolvedConversationTarget unresolved_source_target(const ConversationSourceTarget& target) {
    const auto& external = target.source.event ? target.source.event : target.source.object;
    std::optional<ExternalObjectRef> retained;
    if(external) retained = copy_external_object_ref(*external);
    return {std::move(retained), target.source.evidence};
}

ConversationEnvelope normalize_observation(const ConversationSourceObservation& input, const EntryRef& ref,
    std::optional<ConversationLinkTarget> resolved_target) {
    ConversationEnvelope envelope;
    envelope.metadata.ref = ref;
    envelope.metadata.scope = input.scope;
    envelope.metadata.source = copy_conversation_source_binding(input.source);
    envelope.metadata.actor = input.actor;
    envelope.metadata.observer = input.observer;
    envelope.metadata.llm_role = input.llm_role;
    envelope.metadata.source_unix_us = input.source_unix_us;
    envelope.metadata.observed_unix_us = input.observed_unix_us;
    if(const auto* message = std::get_if<ConversationSourceMessage>(&input.payload)) {
        if(message->kind == ConversationSourceMessageKind::Created) {
            envelope.payload = ConversationPayload{ConversationMessageCreated{message->content}};
        } else envelope.payload = ConversationPayload{ConversationSnapshotObserved{message->content}};
    } else if(const auto* call = std::get_if<ConversationSourceToolCall>(&input.payload)) {
        envelope.payload = ConversationToolCallObserved{call->call_id, call->tool_name, call->original_request};
    } else {
        const auto& result = std::get<ConversationSourceToolResult>(input.payload);
        const auto outcome = result.origin == ConversationSourceResultOrigin::Reported ?
            result.reported_outcome : ConversationToolOutcome::Unknown;
        envelope.payload = ConversationToolResultObserved{
            resolved_target ? std::move(*resolved_target) : fallback_target(result.call),
            result.call_id, outcome, result.result};
    }
    return envelope;
}

} // namespace agent_memory::detail

