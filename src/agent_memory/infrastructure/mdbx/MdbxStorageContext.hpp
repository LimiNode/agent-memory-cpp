#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_STORAGE_CONTEXT_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_STORAGE_CONTEXT_HPP_INCLUDED

/// \file MdbxStorageContext.hpp
/// \brief Shared MDBX connection ownership for infrastructure adapters.
///
/// Lifecycle and transaction ownership follow
/// `guides/storage-backend-integration-roadmap.md`. Physical MDBX usage and
/// DBI budgeting follow `guides/mdbx-containers-extension-tz.md` and
/// `guides/dbi-manifest.yaml`.

#include <memory>
#include <string>

#if AGENT_MEMORY_HAS_MDBX
namespace mdbxc {
class Connection;
}

namespace agent_memory {

/// \brief Shared MDBX environment ownership for storage adapters.
/// Attached contexts retain, but never shut down, a host-owned connection.
/// Transactions remain bound to the thread that created them; lifecycle
/// operations must not race active table operations.
///
/// \see `guides/storage-backend-integration-roadmap.md`
/// \see `guides/mdbx-containers-extension-tz.md`
class MdbxStorageContext final {
  public:
    /// Opens and owns one MDBX connection.
    static std::shared_ptr<MdbxStorageContext> open(std::string path, bool relative_to_exe = false);
    /// Retains a host-provided connection without calling shutdown/disconnect.
    static std::shared_ptr<MdbxStorageContext>
    attach(std::shared_ptr<mdbxc::Connection> connection);

    /// Returns the shared connection used by all attached adapters.
    [[nodiscard]] const std::shared_ptr<mdbxc::Connection>& connection() const noexcept;

  private:
    explicit MdbxStorageContext(std::shared_ptr<mdbxc::Connection> connection);
    std::shared_ptr<mdbxc::Connection> m_connection; ///< Shared or host-provided connection.
};

} // namespace agent_memory
#endif

#endif
