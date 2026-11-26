#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_CONVERSATION_STORE_INTERNAL_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_CONVERSATION_STORE_INTERNAL_HPP_INCLUDED

// Private shared admission rules. Owner: guides/conversation-reference-store.md.
#include <agent_memory/domain/ConversationEnvelope.hpp>

namespace agent_memory::detail {

// In-memory family selector, never a persisted tag. Negative means unsupported.
[[nodiscard]] int conversation_payload_family(const ConversationEnvelope&);
// Inputs have passed typed canonical admission. Compare occurrence/evidence and
// structural targets directly; changing only the normalization recipe is allowed.
[[nodiscard]] bool same_conversation_immutable_binding(
    const ConversationEnvelope&, const ConversationEnvelope&);

} // namespace agent_memory::detail

#endif
