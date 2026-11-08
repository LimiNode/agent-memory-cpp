#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_ARTIFACT_BINDING_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_ARTIFACT_BINDING_STORE_HPP_INCLUDED

/// \file MdbxResourceArtifactBindingStore.hpp
/// \brief MDBX persistence for the typed resource/artifact join.

#include <agent_memory/storage/ResourceArtifactBinding.hpp>

#include <memory>
#include <optional>
#include <string>

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageContext.hpp"

namespace agent_memory {

struct MdbxResourceArtifactBindingStoreOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::shared_ptr<MdbxStorageContext> context;
};

/// \brief Stores only the typed join between resource and artifact domains.
///
/// The key contains the complete ResourceRevisionRef, including its digest.
/// A later body generation therefore cannot overwrite an earlier mapping, and
/// a forged digest cannot be admitted by a lookup that names another revision.
class MdbxResourceArtifactBindingStore final {
public:
    explicit MdbxResourceArtifactBindingStore(
        MdbxResourceArtifactBindingStoreOptions options
    );
    ~MdbxResourceArtifactBindingStore();

    MdbxResourceArtifactBindingStore(
        const MdbxResourceArtifactBindingStore&) = delete;
    MdbxResourceArtifactBindingStore& operator=(
        const MdbxResourceArtifactBindingStore&) = delete;

    /// Persists one mapping or replays the identical mapping.
    /// \throws std::invalid_argument for incomplete identities.
    /// \throws std::runtime_error when the revision key already names another
    /// mapping.
    void put(ResourceArtifactBinding binding);

    [[nodiscard]] std::optional<ResourceArtifactBinding> find(
        const ResourceRevisionRef& revision
    ) const;

private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
