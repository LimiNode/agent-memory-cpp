#include "ConversationSourceImporter.hpp"

#include <limits>
#include <map>
#include <mutex>
#include <stdexcept>
#include <tuple>
#include <type_traits>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::size_t kObservationByteCap = 32U * 1024U * 1024U;
constexpr std::size_t kMappingByteCap = 256U * 1024U * 1024U;
constexpr std::size_t kMappingCountCap = 65536;
constexpr std::size_t kProducerByteCap = 1024;

ConversationWriteResult refusal(ConversationWriteStatus status) {
    return {status, std::nullopt, 0};
}

bool valid_access(const ConversationStoreAccess& access) {
    return !access.scope.empty() && is_valid_conversation_namespace(access.policy_binding);
}

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
        // Standalone AMXR is bounded to 64 KiB and retains its existing text API.
        return value(serialize_external_object_ref(ref).size());
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

ConversationWriteStatus source_status(const ConversationSourceBinding& source,
    const ConversationRef& conversation, const ScopeId& scope, const ConversationFieldLimits& limits) {
    // Reuse the source-field owner instead of maintaining a second mode/UTF-8 validator.
    ConversationEntry probe;
    probe.ref = {conversation, EntryId{std::uint64_t{1}}, EventRevision{std::uint64_t{1}}};
    probe.scope = scope;
    probe.source = source;
    // A field-only target avoids depending on a consumer's inline-text budget.
    probe.payload = ConversationTombstoned{{conversation, EntryId{std::uint64_t{2}}, EventRevision{std::uint64_t{1}}}};
    const auto issues = validate_conversation_entry_with_limits(probe, limits);
    if(issues.empty()) return ConversationWriteStatus::Applied;
    for(const auto& issue : issues) {
        if(issue.code == ConversationValidationCode::LimitExceeded) return ConversationWriteStatus::ResourceLimit;
    }
    return ConversationWriteStatus::InvalidRequest;
}

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

struct SourceKey final {
    ConversationSourceIdentity identity = ConversationSourceIdentity::LocalObservation;
    ByteBuffer external;
    SourceRevisionId revision;
    std::optional<std::string> marker;
    ArtifactBindingId artifact;
    std::string locator;

    bool operator<(const SourceKey& other) const {
        return std::tie(identity, external, revision, marker, artifact, locator) <
            std::tie(other.identity, other.external, other.revision, other.marker, other.artifact, other.locator);
    }

    std::size_t charge() const {
        return 32 + external.size() + revision.value().size() + (marker ? marker->size() : 0) +
            artifact.value().size() + locator.size();
    }
};

SourceKey source_key(const ConversationSourceBinding& source) {
    SourceKey key;
    key.identity = source.identity;
    key.revision = source.revision;
    if(source.identity == ConversationSourceIdentity::LocalObservation) {
        key.artifact = source.evidence.artifact;
        key.locator = source.evidence.record_locator;
    } else {
        const auto& external = source.identity == ConversationSourceIdentity::ExternalEvent ? *source.event : *source.object;
        // Explicit, bounded adapter for the independent standalone AMXR API.
        const auto encoded = serialize_external_object_ref(external);
        key.external.assign(encoded.begin(), encoded.end());
        if(source.identity == ConversationSourceIdentity::ObjectRevision) key.marker = source.provider_revision_marker;
    }
    return key;
}

} // namespace

IConversationImportSink::~IConversationImportSink() = default;

struct InMemoryConversationImportSink::Impl final {
    struct Mapping final {
        ConversationSourceObservation witness;
        EntryRef original_ref;
        std::uint64_t generation = 0;
    };

    struct Conversation final {
        ScopeId scope;
        std::uint64_t next_entry = 1;
        std::map<SourceKey, Mapping> mappings;
    };

    explicit Impl(ConversationImportOptions input) : options(std::move(input)), store(options.store) {}

    ConversationImportOptions options;
    InMemoryConversationStore store;
    std::mutex mutex;
    std::map<ConversationRef, Conversation> conversations;
    std::size_t mapping_count = 0;
    std::size_t mapping_bytes = 0;

    ConversationWriteStatus target_status(const ConversationSourceObservation& observation) const {
        const auto* result = std::get_if<ConversationSourceToolResult>(&observation.payload);
        if(!result) return ConversationWriteStatus::Applied;
        if(const auto* target = std::get_if<ConversationSourceTarget>(&result->call)) {
            if(target->scope.value() != observation.scope.value()) return ConversationWriteStatus::Denied;
            if(target->conversation != observation.conversation) return ConversationWriteStatus::InvalidRequest;
            return source_status(target->source, target->conversation, target->scope, options.store.codec_limits.fields);
        }
        if(const auto* target = std::get_if<EntryRef>(&result->call)) {
            if(target->conversation != observation.conversation) return ConversationWriteStatus::InvalidRequest;
        }
        return ConversationWriteStatus::Applied;
    }

    ConversationLinkTarget resolve_target(const ConversationSourceCallTarget& input, const Conversation& conversation) const {
        if(const auto* ref = std::get_if<EntryRef>(&input)) return *ref;
        if(const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&input)) return *unresolved;
        const auto& source_target = std::get<ConversationSourceTarget>(input);
        const auto found = conversation.mappings.find(source_key(source_target.source));
        if(found != conversation.mappings.end()) return found->second.original_ref;
        return UnresolvedConversationTarget{source_target.source.event ? source_target.source.event : source_target.source.object,
            source_target.source.evidence};
    }

    ConversationEnvelope normalize(const ConversationSourceObservation& input, const EntryRef& ref,
        const Conversation& conversation) const {
        ConversationEnvelope envelope;
        envelope.metadata.ref = ref;
        envelope.metadata.scope = input.scope;
        envelope.metadata.source = input.source;
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
            envelope.payload = ConversationToolResultObserved{resolve_target(result.call, conversation),
                result.call_id, outcome, result.result};
        }
        return envelope;
    }
};

InMemoryConversationImportSink::InMemoryConversationImportSink(ConversationImportOptions options) {
    if(options.max_observation_bytes > kObservationByteCap || options.max_mappings > kMappingCountCap ||
        options.max_mapping_bytes > kMappingByteCap) throw std::invalid_argument("Invalid conversation import options");
    m_impl = std::make_unique<Impl>(std::move(options));
}

InMemoryConversationImportSink::~InMemoryConversationImportSink() = default;

ConversationWriteResult InMemoryConversationImportSink::create_conversation(
    const ConversationStoreAccess& access, const ConversationRef& ref) {
    if(!valid_access(access)) return refusal(ConversationWriteStatus::Denied);
    if(!is_valid_conversation_ref(ref) || ref.allocation_namespace != m_impl->options.store.allocation_namespace) {
        return refusal(ConversationWriteStatus::InvalidRequest);
    }
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    const auto existing = m_impl->conversations.find(ref);
    if(existing != m_impl->conversations.end()) {
        if(existing->second.scope.value() != access.scope.value()) return refusal(ConversationWriteStatus::NotFound);
        return m_impl->store.create_conversation(access, ref);
    }
    if(m_impl->conversations.size() >= m_impl->options.store.max_conversations ||
        access.scope.value().size() > m_impl->options.store.codec_limits.max_frame_bytes ||
        access.scope.value().size() > m_impl->options.store.codec_limits.max_owned_bytes ||
        access.scope.value().size() > m_impl->options.store.max_storage_bytes) {
        return refusal(ConversationWriteStatus::ResourceLimit);
    }
    // Bookkeeping must exist before the canonical create can become visible.
    const auto inserted = m_impl->conversations.emplace(ref, Impl::Conversation{access.scope, 1, {}});
    try {
        auto result = m_impl->store.create_conversation(access, ref);
        if(result.status != ConversationWriteStatus::Applied) m_impl->conversations.erase(inserted.first);
        return result;
    } catch(...) {
        m_impl->conversations.erase(inserted.first);
        throw;
    }
}

ConversationWriteResult InMemoryConversationImportSink::import_observation(
    const ConversationStoreAccess& access, const ConversationSourceObservation& observation) {
    if(!valid_access(access) || access.scope.value() != observation.scope.value()) {
        return refusal(ConversationWriteStatus::Denied);
    }
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    const auto found = m_impl->conversations.find(observation.conversation);
    if(found == m_impl->conversations.end() || found->second.scope.value() != access.scope.value()) {
        return refusal(ConversationWriteStatus::NotFound);
    }
    auto& conversation = found->second;
    ObservationPreflight preflight{m_impl->options.store.codec_limits.fields, m_impl->options.max_observation_bytes};
    if(!preflight.observation(observation)) return refusal(preflight.status);
    if(observation.producer.empty() || !valid_utf8(observation.producer)) {
        return refusal(ConversationWriteStatus::InvalidRequest);
    }
    const auto source = source_status(observation.source, observation.conversation, observation.scope,
        m_impl->options.store.codec_limits.fields);
    if(source != ConversationWriteStatus::Applied) return refusal(source);
    const auto target = m_impl->target_status(observation);
    if(target != ConversationWriteStatus::Applied) return refusal(target);
    if(const auto* message = std::get_if<ConversationSourceMessage>(&observation.payload)) {
        if(message->kind == ConversationSourceMessageKind::Created &&
            observation.source.identity == ConversationSourceIdentity::LocalObservation) {
            return refusal(ConversationWriteStatus::InvalidRequest);
        }
    }
    auto key = source_key(observation.source);
    const auto existing = conversation.mappings.find(key);
    if(existing != conversation.mappings.end()) {
        if(!same_witness(observation, existing->second.witness)) return refusal(ConversationWriteStatus::Conflict);
        // Do not renormalize a previously unresolved target against today's map.
        return {ConversationWriteStatus::NoChange, existing->second.original_ref, existing->second.generation};
    }
    if(m_impl->mapping_count >= m_impl->options.max_mappings ||
        conversation.next_entry == std::numeric_limits<std::uint64_t>::max()) {
        return refusal(ConversationWriteStatus::ResourceLimit);
    }
    // All components have bounded preflight sizes, so this sum cannot overflow size_t.
    const auto charge = preflight.bytes + key.charge() + 32 + observation.conversation.allocation_namespace.size();
    if(charge > m_impl->options.max_mapping_bytes - m_impl->mapping_bytes) {
        return refusal(ConversationWriteStatus::ResourceLimit);
    }
    const EntryRef ref{observation.conversation, EntryId{conversation.next_entry}, EventRevision{std::uint64_t{1}}};
    auto envelope = m_impl->normalize(observation, ref, conversation);
    // Result and witness allocations precede canonical append. Nothing after Applied allocates.
    ConversationWriteResult result{ConversationWriteStatus::Applied, ref, 0};
    Impl::Mapping row{observation, ref, 0};
    const auto inserted = conversation.mappings.emplace(std::move(key), std::move(row));
    static_assert(std::is_nothrow_move_constructible_v<ConversationWriteResult>);
    try {
        auto appended = m_impl->store.append(access, envelope);
        if(appended.status != ConversationWriteStatus::Applied) {
            conversation.mappings.erase(inserted.first);
            return appended;
        }
        inserted.first->second.generation = appended.commit_generation;
        result.commit_generation = appended.commit_generation;
        ++conversation.next_entry;
        ++m_impl->mapping_count;
        m_impl->mapping_bytes += charge;
        return result;
    } catch(...) {
        conversation.mappings.erase(inserted.first);
        throw;
    }
}

ConversationWriteResult InMemoryConversationImportSink::correct(const ConversationStoreAccess& access,
    const ConversationEnvelope& envelope, EventRevision expected, const std::string& reason_code) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    return m_impl->store.correct(access, envelope, expected, reason_code);
}

const IConversationStore& InMemoryConversationImportSink::history() const noexcept {
    return m_impl->store;
}

} // namespace agent_memory
