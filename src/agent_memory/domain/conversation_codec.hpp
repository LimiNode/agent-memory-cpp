#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_CONVERSATION_CODEC_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_CONVERSATION_CODEC_HPP_INCLUDED

/// \file conversation_codec.hpp
/// \brief Bounded canonical AMCE envelopes; no storage, authorization or execution.

#include "ConversationEnvelope.hpp"

#include <stdexcept>

namespace agent_memory {

inline constexpr std::size_t kMaxConversationEntryBytes = 32U * 1024U * 1024U;
inline constexpr std::size_t kConversationEnvelopeHeaderBytes = 26;

/// \brief Conjunctive resource budgets, not semantic switches or heap/RSS guarantees.
/// \note Zero forbids a resource; it never means unlimited. Nested AMXR limits are fixed.
struct ConversationCodecLimits final {
    ConversationFieldLimits fields;
    std::size_t max_frame_bytes = kMaxConversationEntryBytes;
    std::size_t max_extensions = 64;
    std::size_t max_extension_bytes = 65536;
    /// Sum of B lengths and opaque payload bytes; nested AMXR charged as its complete frame.
    std::size_t max_owned_bytes = kMaxConversationEntryBytes;
    /// Each B value plus each sequence element; conservative logical allocation units.
    std::size_t max_objects = 4096;
};

/// \brief Returns the qualified larger-field profile (same 32 MiB frame ceiling).
[[nodiscard]] ConversationCodecLimits extended_conversation_codec_limits();
/// \brief Checks every writer resource budget is dominated by the reader's budget.
/// \throws ConversationCodecError with InvalidLimits for unsupported profiles.
[[nodiscard]] bool conversation_codec_limits_cover(const ConversationCodecLimits& reader,
    const ConversationCodecLimits& writer);

enum class ConversationCodecCode {
    Malformed, InvalidValue, ResourceLimit, InvalidLimits, UnsupportedEnvelope,
    UnsupportedPayload, UnsupportedMetadata
};

/// \brief Typed refusal without echoing untrusted contents. Allocation failures propagate.
class ConversationCodecError final : public std::invalid_argument {
public:
    explicit ConversationCodecError(ConversationCodecCode code);
    [[nodiscard]] ConversationCodecCode code() const noexcept { return m_code; }
private:
    ConversationCodecCode m_code;
};

struct ConversationDecodeOptions final {
    ConversationCodecLimits limits;
    /// Refuse opaque payloads and unknown critical metadata after bounded envelope validation.
    bool require_typed_payload = false;
};

/// \brief Framing only; inspection does not validate metadata, payload or authority.
struct ConversationFrameHeader final {
    std::uint32_t frame_bytes = 0;
    std::uint32_t metadata_bytes = 0;
    std::uint32_t payload_type_id = 0;
    std::uint16_t payload_schema_version = 0;
    std::uint32_t payload_bytes = 0;
};

/// \brief Inspects exact framing without decoding or copying its bodies.
/// \param bytes Borrowed during the call; null is valid only with zero length.
[[nodiscard]] ConversationFrameHeader inspect_conversation_frame(ByteView bytes,
    const ConversationCodecLimits& limits = {});
/// \brief Validates and exact-size preflights before allocating the owning output buffer.
[[nodiscard]] ByteBuffer serialize_conversation_envelope(const ConversationEnvelope& envelope,
    const ConversationCodecLimits& limits = {});
/// \brief Decodes a whole frame. Unsupported pairs are retained, never executed or admitted.
/// \param bytes Borrowed during the call; the returned value owns every retained byte.
[[nodiscard]] ConversationEnvelope deserialize_conversation_envelope(ByteView bytes,
    const ConversationDecodeOptions& options = {});
/// \brief Refuses unsupported interpretation; advisory extensions remain in the envelope.
[[nodiscard]] const ConversationEnvelopePayload& require_typed_conversation_payload(
    const ConversationEnvelope& envelope);
/// \brief Serializes Phase 1 values without copying their text or artifact bindings.
[[nodiscard]] ByteBuffer serialize_conversation_entry(const ConversationEntry& entry,
    const ConversationCodecLimits& limits = {});
/// \brief Lossless Phase 1-only decoding; any extension or other payload is unsupported.
[[nodiscard]] ConversationEntry deserialize_conversation_entry(ByteView bytes,
    const ConversationDecodeOptions& options = {});

} // namespace agent_memory

#endif
