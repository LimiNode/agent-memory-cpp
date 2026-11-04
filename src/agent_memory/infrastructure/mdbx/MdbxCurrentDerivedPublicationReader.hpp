#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CURRENT_DERIVED_PUBLICATION_READER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CURRENT_DERIVED_PUBLICATION_READER_HPP_INCLUDED

/// \file MdbxCurrentDerivedPublicationReader.hpp
/// \brief Current-read revalidation for fenced derived publications.

#include "MdbxStorageContext.hpp"
#include <agent_memory/storage/IDerivedPublicationStore.hpp>

#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>

#if AGENT_MEMORY_HAS_MDBX
namespace agent_memory {

enum class MdbxCurrentDerivedPublicationStatus : std::uint8_t {
    Current,
    Stale,
    Historical,
    Unavailable,
    NotFound
};

struct MdbxCurrentDerivedPublicationReadResult final {
    MdbxCurrentDerivedPublicationStatus status =
        MdbxCurrentDerivedPublicationStatus::Unavailable;
    std::optional<DerivedPublication> publication;
};

/// \brief Reads a derived publication and revalidates all canonical inputs.
///
/// One read transaction covers the publication rows, KnowledgeUnits, and root
/// ResourceManifests. The body is returned only after every dependency matches
/// the stored revision bindings. This adapter is deliberately separate from
/// the storage-only IDerivedPublicationStore::read contract.
class MdbxCurrentDerivedPublicationReader final {
public:
    explicit MdbxCurrentDerivedPublicationReader(
        std::shared_ptr<MdbxStorageContext> context,
        std::string table_prefix = "agent_memory"
    );

    [[nodiscard]] MdbxCurrentDerivedPublicationReadResult read_current(
        const ScopeId& access_scope,
        const std::string& publication_id
    ) const;

    /// \brief Reads an exact stored publication without claiming freshness.
    ///
    /// This path is for explicit historical inspection only. It still checks
    /// body/manifest/marker integrity and never falls back to stale content in
    /// read_current().
    [[nodiscard]] MdbxCurrentDerivedPublicationReadResult read_historical(
        const ScopeId& access_scope,
        const std::string& publication_id
    ) const;

    /// \brief Installs a deterministic test seam after publication rows are read.
    ///
    /// Production callers should leave this unset. The callback runs while
    /// the reader's single MDBX read transaction remains active.
    void set_read_hook_for_testing(std::function<void()> hook);

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    std::string m_table_prefix;
    mutable std::function<void()> m_read_hook_for_testing;
};

} // namespace agent_memory
#endif

#endif
