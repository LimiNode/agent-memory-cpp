#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_STORAGE_ERRORS_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_STORAGE_ERRORS_HPP_INCLUDED

/// \file MdbxStorageErrors.hpp
/// \brief Typed failures raised while decoding persisted MDBX records.

#include <stdexcept>
#include <string>

namespace agent_memory {

/// \brief Indicates that an MDBX value was readable but failed its wire contract.
class MdbxPersistedMetadataError final : public std::runtime_error {
public:
    explicit MdbxPersistedMetadataError(const std::string& message)
        : std::runtime_error(message) {}
};

/// \brief Indicates that a guarded publication no longer owns its fence.
///
/// The diagnostic text is for operators only. Recovery must dispatch on this
/// type, never on the wording returned by what().
class MdbxPublicationFenceError final : public std::runtime_error {
public:
    explicit MdbxPublicationFenceError(const std::string& message)
        : std::runtime_error(message) {}
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_STORAGE_ERRORS_HPP_INCLUDED
