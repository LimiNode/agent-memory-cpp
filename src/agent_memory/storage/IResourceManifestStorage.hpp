#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_I_RESOURCE_MANIFEST_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_I_RESOURCE_MANIFEST_STORAGE_HPP_INCLUDED

/// \file IResourceManifestStorage.hpp
/// \brief Storage contract for resource ownership manifests.

#include <agent_memory/domain/Resource.hpp>

#include <exception>
#include <optional>

namespace agent_memory {

    /// \brief Dependency-free contract for resource manifest persistence.
    class IResourceManifestStorage {
    public:
        virtual ~IResourceManifestStorage();

        /// \brief Inserts or replaces a resource manifest.
        /// \pre `is_valid_resource_manifest(manifest)` must be true.
        /// \exception Any implementation-defined storage exception.
        /// \post If this function throws, the previously visible manifest for
        /// the resource remains visible. Implementations must provide the
        /// strong exception guarantee for one manifest replacement.
        virtual void upsert_manifest(ResourceManifest manifest) = 0;

        /// \brief Finds a resource manifest by resource id.
        /// \param resource_id Resource id to look up.
        /// \return Manifest copy when found.
        [[nodiscard]] virtual std::optional<ResourceManifest> find_manifest(
            const ResourceId& resource_id
        ) const = 0;

        /// \brief Removes a resource manifest.
        /// \param resource_id Resource id to remove.
        /// \return True when a manifest was removed.
        [[nodiscard]] virtual bool erase_manifest(
            const ResourceId& resource_id
        ) = 0;
    };

} // namespace agent_memory

#endif
