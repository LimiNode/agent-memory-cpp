#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_BYTE_VIEW_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_BYTE_VIEW_HPP_INCLUDED

/// \file ByteView.hpp
/// \brief Explicit owning bytes and a borrowed pointer/length view for binary interfaces.

#include <cstddef>
#include <cstdint>
#include <vector>

namespace agent_memory {

using ByteBuffer = std::vector<std::uint8_t>;

/// \brief Borrowed bytes; the owner must remain alive and unmodified during use.
/// \note No container conversion is supplied, so temporary containers cannot implicitly
/// create a view. Null with nonzero size is rejected at codec entry points.
struct ByteView final {
    const std::uint8_t* data = nullptr;
    std::size_t size = 0;
};

} // namespace agent_memory

#endif
