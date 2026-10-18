#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_IMPORTER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_IMPORTER_HPP_INCLUDED

/// \file MdbxResourceImporter.hpp
/// \brief Recoverable lexical-first resource importer over shared MDBX storage.

#include "MdbxDurableLexicalDocumentMemory.hpp"
#include "MdbxResourceManifestStorage.hpp"

#include <agent_memory/ingestion/ResourceImporter.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <functional>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace agent_memory {

    class IKnowledgeUnitStore;

    /// \brief Deterministic interruption points for importer recovery tests.
    enum class ResourceImporterFaultPoint {
        AfterIntent,
        BeforeLexicalPublication,
        AfterLexicalPublication,
        BeforeManifestPublication,
        AfterManifestPublication
    };

    /// \brief Options for the M0 lexical-first importer.
    struct MdbxResourceImporterOptions final {
        std::string path;
        std::string table_prefix = "agent_memory";
        bool relative_to_exe = false;
        std::int64_t max_dbs = kDefaultMdbxMaxDbs;
        std::shared_ptr<MdbxStorageContext> context;
        /// Optional same-context store for revision-bound raw Chunk units.
        /// When set, publication creates bounded units before the manifest is
        /// activated and replays them idempotently during intent recovery.
        std::shared_ptr<IKnowledgeUnitStore> knowledge_unit_store;
        /// Lease used before another executor may recover an unfinished intent.
        std::uint64_t ownership_lease_ms = 30'000;
        /// Optional deterministic wall-clock source returning Unix milliseconds.
        std::function<std::uint64_t()> clock_ms;
        std::function<void(ResourceImporterFaultPoint)> fault_injector;
    };

    /// Maximum bounded candidate window used by importer-aware lexical search.
    inline constexpr std::size_t kMdbxResourceImporterMaxSearchLimit = 1024;

    /// \brief M0 importer composing durable lexical content and manifests.
    ///
    /// ResourceId is globally unique for this manifest profile. Scope remains
    /// part of the lexical identity; callers that reuse a human-readable name
    /// in multiple scopes must provide distinct globally qualified ResourceIds.
    /// Publication uses a durable intent, monotonic fencing epoch and lease.
    /// A same-payload caller cannot execute an intent owned by another live
    /// executor; an expired intent may be recovered with a higher epoch.
    /// A failure between canonical, lexical and manifest transactions leaves
    /// the prior manifest visible and is completed on reopen or retry.
    class MdbxResourceImporter final : public IResourceImporter {
    public:
        explicit MdbxResourceImporter(MdbxResourceImporterOptions options);
        ~MdbxResourceImporter() override;

        MdbxResourceImporter(const MdbxResourceImporter&) = delete;
        MdbxResourceImporter& operator=(const MdbxResourceImporter&) = delete;

        [[nodiscard]] ResourceImportResult import(
            ObservedResource resource
        ) override;

        [[nodiscard]] std::optional<ResourceManifest> find_manifest(
            const ResourceId& resource_id
        ) const;

        /// \brief Searches only importer-visible resources.
        ///
        /// A resource with a pending update is hidden in its entirety until
        /// manifest publication completes; this wrapper does not promise a
        /// snapshot-atomic view of the previous revision during that window.
        /// The candidate limit is bounded by
        /// `kMdbxResourceImporterMaxSearchLimit`.
        [[nodiscard]] std::vector<DurableLexicalDocumentHit> search(
            const ScopeId& scope_id,
            std::string_view query,
            std::size_t limit = 10
        ) const;

    private:
        class Impl;
        std::unique_ptr<Impl> m_impl;
    };

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
