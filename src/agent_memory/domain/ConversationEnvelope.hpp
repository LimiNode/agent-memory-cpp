#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_CONVERSATION_ENVELOPE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_CONVERSATION_ENVELOPE_HPP_INCLUDED

/// \file ConversationEnvelope.hpp
/// \brief Typed conversation observations with lossless unsupported payload retention.
/// \note Field validity is not storage admission, authority, source authenticity or execution.

#include "ByteView.hpp"
#include "Conversation.hpp"

namespace agent_memory {

struct ConversationMetadata final {
    EntryRef ref;
    ScopeId scope;
    std::optional<ConversationSourceBinding> source;
    std::optional<ActorRef> actor;
    std::optional<ConversationLlmRole> llm_role;
    std::optional<ActorRef> observer;
    std::optional<std::int64_t> source_unix_us;
    std::optional<std::int64_t> observed_unix_us;
    std::vector<ConversationLink> links;
};

/// \brief An observed request, never authority to execute it.
/// \note call_id is exact nonempty opaque bytes; request refers to an immutable artifact binding.
struct ConversationToolCallObserved final {
    ByteBuffer call_id;
    std::string tool_name;
    ArtifactBindingId request;
};

enum class ConversationToolOutcome { Unknown, Succeeded, Failed, Cancelled };

/// \brief An observed result, including error, cancellation or an unknown outcome.
/// \note The exact same-conversation call target must be a different occurrence. Matching
/// call_id to its retained ToolCall requires later admission, not local field validation.
struct ConversationToolResultObserved final {
    EntryRef call;
    ByteBuffer call_id;
    ConversationToolOutcome outcome = ConversationToolOutcome::Unknown;
    ArtifactBindingId result;
};

/// \brief Uninterpreted bytes for an unsupported type/schema pair; not canonical admission.
/// \note A pair supported by this implementation cannot use this alternative to bypass validation.
struct ConversationOpaquePayload final {
    std::uint32_t type_id = 0;
    std::uint16_t schema_version = 0;
    ByteBuffer bytes;
};

using ConversationEnvelopePayload = std::variant<ConversationPayload,
    ConversationToolCallObserved, ConversationToolResultObserved, ConversationOpaquePayload>;

/// \brief Exact retained metadata extension. Critical extensions require explicit interpretation support.
struct ConversationMetadataExtension final {
    std::uint32_t id = 0;
    bool is_critical = false;
    ByteBuffer bytes;
};

struct ConversationEnvelope final {
    ConversationMetadata metadata;
    ConversationEnvelopePayload payload;
    std::vector<ConversationMetadataExtension> extensions;
};

/// \brief Reports built-in interpretation support, not registration, validity or authority.
[[nodiscard]] bool is_supported_conversation_payload(std::uint32_t type_id, std::uint16_t schema_version) noexcept;

/// \brief Validates metadata, typed payload fields and extension ordering without copying bodies.
/// \note Unknown payload bytes remain uninterpreted; critical extensions are an interpretation gate,
/// not a semantic field error. Codec resource budgets are checked separately.
[[nodiscard]] std::vector<ConversationValidationIssue> validate_conversation_envelope(
    const ConversationEnvelope&, const ConversationFieldLimits& limits = {});

/// \brief Moves a Phase 1 entry into an envelope without altering its values.
[[nodiscard]] ConversationEnvelope make_conversation_envelope(ConversationEntry entry);
/// \brief Moves a legacy payload back only when no extension or unknown data can be lost.
/// \throws std::invalid_argument for any extension or non-Phase-1 payload.
[[nodiscard]] ConversationEntry to_conversation_entry(ConversationEnvelope envelope);

} // namespace agent_memory

#endif
