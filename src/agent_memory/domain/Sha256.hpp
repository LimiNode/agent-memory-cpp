#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_SHA256_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_SHA256_HPP_INCLUDED

#include <array>
#include <cstdint>
#include <string_view>

namespace agent_memory {

/// \brief Incremental SHA-256 accumulator for bounded-memory byte streams.
class Sha256Accumulator final {
public:
    Sha256Accumulator() noexcept;

    void update(std::string_view bytes) noexcept;
    [[nodiscard]] std::array<std::uint8_t, 32> finish() const noexcept;

private:
    void process_block(const std::uint8_t* block) noexcept;

    std::array<std::uint32_t, 8> m_state{};
    std::array<std::uint8_t, 64> m_block{};
    std::size_t m_block_size = 0;
    std::uint64_t m_bit_count = 0;
};

/// \\brief Computes the raw SHA-256 digest of UTF-8/byte input.
[[nodiscard]] std::array<std::uint8_t, 32> sha256(std::string_view bytes) noexcept;

} // namespace agent_memory

#endif
