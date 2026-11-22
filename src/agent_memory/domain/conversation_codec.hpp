#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_CONVERSATION_CODEC_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_CONVERSATION_CODEC_HPP_INCLUDED

/// \file conversation_codec.hpp
/// \brief Standalone AMCE v1 encoding of Phase 1 values, not a store or admission API.

#include "Conversation.hpp"

#include <cstddef>
#include <string>
#include <string_view>

namespace agent_memory {

inline constexpr std::size_t kMaxConversationEntryBytes = 32U * 1024U * 1024U;
/// Extra codec admission bound; the Phase 1 ScopeId validator remains unchanged.
inline constexpr std::size_t kMaxConversationScopeBytes = 65536;

/// \brief Validates fields and preflights the exact bounded canonical AMCE v1 size.
/// \throws std::invalid_argument for invalid values or exceeded codec limits.
/// \note Allocation failures propagate. No IDs are allocated and no evidence is authenticated.
[[nodiscard]] std::string serialize_conversation_entry(const ConversationEntry& entry);

/// \brief Decodes one complete frame without normalization or implicit revision resolution.
/// \throws std::invalid_argument for malformed, noncanonical, oversized or field-invalid input.
/// \note No partially accepted value escapes; allocation failures propagate.
[[nodiscard]] ConversationEntry deserialize_conversation_entry(std::string_view bytes);

} // namespace agent_memory

#endif
