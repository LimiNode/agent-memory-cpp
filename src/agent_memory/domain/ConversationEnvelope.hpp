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

/// \brief An observed original request, never approval or authority to execute it.
/// \note request binds the immutable originally requested arguments or request representation.
/// Validation, rewriting, dispatch and execution are not implied. Effective arguments need
/// separate evidence, not replacement of request. call_id is exact nonempty opaque bytes,
/// scoped by its source issuer/correlation domain, not a global identity or attempt ID.
/// See guides/conversation-event-contract.md section 4.2 for admission and correlation rules.
struct ConversationToolCallObserved final {
    ByteBuffer call_id;
    std::string tool_name;
    ArtifactBindingId request;
};

/// \brief Source-reported outcome at a documented observation boundary, not verified world state.
/// \note Approval and execution are independent; denial is not an additional outcome value.
enum class ConversationToolOutcome {
    Unknown,   ///< Ambiguity is retained; neither automatic retry nor a lifecycle event is implied.
    Succeeded, ///< Reported success does not independently verify an external effect.
    Failed,    ///< Reported failure does not establish that the tool body started.
    Cancelled  ///< Reported cancellation does not establish absence of external side effects.
};

/// \brief A source-reported result observation, not proof of execution or effect verification.
/// \note A resolved call is an exact same-conversation, different occurrence; an unresolved
/// call retains evidence without fabricating a local identity. Source evidence is always
/// required. Artifact presence and outcome are independent; absence means no retained body.
/// Matching call_id to a retained ToolCall requires later admission, not field validation.
/// Denial, timeout, synthetic recovery, transcript repair, roles, isError and exit-shaped
/// data do not automatically establish execution outcomes. An adapter unable to distinguish
/// synthetic origin from execution evidence must not classify a result as confirmed execution.
/// See guides/conversation-event-contract.md section 4.2; the codec does not parse artifacts
/// or reconstruct the approval, run, step, attempt or execution lifecycle.
struct ConversationToolResultObserved final {
    ConversationLinkTarget call;
    ByteBuffer call_id;
    ConversationToolOutcome outcome = ConversationToolOutcome::Unknown;
    std::optional<ArtifactBindingId> result;
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

/// \brief Copies metadata through shared source/target copy operations, including every link.
/// \return An independent value; allocation failures propagate and leave the input unchanged.
/// \note This is value copying, not validation, admission or a wire-format conversion.
[[nodiscard]] ConversationMetadata copy_conversation_metadata(const ConversationMetadata&);
/// \brief Copies typed/opaque payloads, extensions and metadata without copying external variants.
[[nodiscard]] ConversationEnvelope copy_conversation_envelope(const ConversationEnvelope&);

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
