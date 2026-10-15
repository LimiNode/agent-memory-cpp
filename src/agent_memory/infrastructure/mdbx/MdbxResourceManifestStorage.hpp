#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_MANIFEST_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_MANIFEST_STORAGE_HPP_INCLUDED

/// \file MdbxResourceManifestStorage.hpp
/// \brief MDBX-backed resource manifest storage adapter.

#include <agent_memory/storage/IResourceManifestStorage.hpp>

#include <memory>
#include <optional>
#include <string>

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageContext.hpp"

namespace agent_memory {

    /// \brief Configuration for MDBX-backed resource manifest storage.
    struct MdbxResourceManifestStorageOptions final {
        std::string path;
        /// \brief Table name prefix.
        /// \note Non-alphanumeric characters are normalized to underscores, so
        ///       distinct input prefixes can map to the same table prefix.
        std::string table_prefix = "agent_memory";
        bool relative_to_exe = false;
        std::int64_t max_dbs = kDefaultMdbxMaxDbs;
        std::shared_ptr<MdbxStorageContext> context;
        /// Optional pre-opened table used by guarded manifest publication.
        std::string publication_guard_table_name;
    };

    /// \brief Durable compare token checked in the manifest write transaction.
    struct MdbxManifestPublicationGuard final {
        std::string table_name;
        std::string key;
        std::string expected_value;
    };

    /// \brief MDBX-backed implementation of `IResourceManifestStorage`.
    class MdbxResourceManifestStorage final : public IResourceManifestStorage {
    public:
        explicit MdbxResourceManifestStorage(MdbxResourceManifestStorageOptions options);
        ~MdbxResourceManifestStorage() override;

        MdbxResourceManifestStorage(const MdbxResourceManifestStorage&) = delete;
        MdbxResourceManifestStorage& operator=(
            const MdbxResourceManifestStorage&
        ) = delete;

        MdbxResourceManifestStorage(MdbxResourceManifestStorage&& other) noexcept;
        MdbxResourceManifestStorage& operator=(
            MdbxResourceManifestStorage&& other
        ) noexcept;

        void upsert_manifest(ResourceManifest manifest) override;

        /// \brief Publishes a manifest only when the previously observed value
        ///        is still present and byte-for-byte unchanged.
        /// \throws std::runtime_error when another writer changed the value.
        void upsert_manifest_if_unchanged(
            ResourceManifest manifest,
            const std::optional<ResourceManifest>& expected
        );

        /// \brief Conditionally publishes while the durable intent still matches.
        void upsert_manifest_if_unchanged_guarded(
            ResourceManifest manifest,
            const std::optional<ResourceManifest>& expected,
            MdbxManifestPublicationGuard guard
        );

        [[nodiscard]] std::optional<ResourceManifest> find_manifest(
            const ResourceId& resource_id
        ) const override;

        [[nodiscard]] bool erase_manifest(const ResourceId& resource_id) override;

    private:
        class Impl;

        std::unique_ptr<Impl> m_impl;
    };

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
