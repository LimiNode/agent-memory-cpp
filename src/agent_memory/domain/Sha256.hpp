#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_SHA256_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_SHA256_HPP_INCLUDED

#include <array>
#include <cstdint>
#include <string_view>

namespace agent_memory {

/// \\brief Computes the raw SHA-256 digest of UTF-8/byte input.
[[nodiscard]] std::array<std::uint8_t, 32> sha256(std::string_view bytes) noexcept;

} // namespace agent_memory

#endif
