#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_IMPORTER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_IMPORTER_HPP_INCLUDED

/// \file MdbxResourceImporter.hpp
/// \brief Recoverable lexical-first resource importer over shared MDBX storage.

#include "MdbxDurableLexicalDocumentMemory.hpp"
#include "MdbxResourceManifestStorage.hpp"

#include <agent_memory/ingestion/ResourceImporter.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <atomic>
#include <functional>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace agent_memory {

    /// \brief Optional counters for importer performance investigations.
    ///
    /// These counters are observational only. A null diagnostics pointer
    /// leaves the importer publication behavior unchanged.
    struct MdbxResourceImporterDiagnostics final {
        std::atomic<std::uint64_t> ownership_lookup_attempts{0};
        std::atomic<std::uint64_t> ownership_checks{0};
        std::atomic<std::uint64_t> ownership_payload_materializations{0};
        std::atomic<std::uint64_t> ownership_payload_bytes{0};
        std::atomic<std::uint64_t> ownership_lookup_ns{0};
        std::atomic<std::uint64_t> ownership_compare_ns{0};
        std::atomic<std::uint64_t> ownership_total_ns{0};
        std::atomic<std::uint64_t> lexical_ns{0};
        std::atomic<std::uint64_t> raw_unit_store_ns{0};
        std::atomic<std::uint64_t> raw_store_calls{0};
        std::atomic<std::uint64_t> manifest_ns{0};
    };

    /// \brief Durable publication profile for the M0 importer.
    enum class ResourceImportPublicationProfile : std::uint8_t {
        LexicalOnly,
        LexicalWithRawChunks
    };

    /// \brief Deterministic interruption points for importer recovery tests.
    enum class ResourceImporterFaultPoint {
        AfterIntent,
        BeforeLexicalPublication,
        AfterLexicalPublication,
        AfterRawUnitPublication,
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
        /// Explicitly selects whether the importer owns raw Chunk publication.
        /// Raw mode constructs its MDBX store from this context and table
        /// prefix; callers cannot attach an unrelated store or namespace.
        ResourceImportPublicationProfile publication_profile =
            ResourceImportPublicationProfile::LexicalOnly;
        /// Admission bound for one raw-publication body.
        std::size_t max_raw_body_bytes = 16U * 1024U * 1024U;
        /// Admission bound for the total number of raw chunks in one body.
        std::size_t max_raw_chunks = 16U * 1024U;
        /// Number of raw Chunk requests committed by one guarded MDBX batch.
        /// The bound is also the unit of recovery and fault injection: a
        /// committed batch is replayed atomically after takeover.
        std::size_t raw_chunk_batch_size = 128U;
        /// Lease used before another executor may recover an unfinished intent.
        std::uint64_t ownership_lease_ms = 30'000;
        /// Optional deterministic wall-clock source returning Unix milliseconds.
        std::function<std::uint64_t()> clock_ms;
        std::function<void(ResourceImporterFaultPoint)> fault_injector;
        std::shared_ptr<MdbxResourceImporterDiagnostics> diagnostics;
    };

    /// Maximum bounded candidate window used by importer-aware lexical search.
    inline constexpr std::size_t kMdbxResourceImporterMaxSearchLimit = 1024;
    inline constexpr std::size_t kMdbxResourceImporterRawChunkBytes = 1024;

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
    /// Raw units are durable staging records until the matching raw profile
    /// manifest contains their bindings; low-level store scans are not a
    /// publication query and may include orphaned staging history.
    class MdbxResourceImporter final : public IResourceImporter {
    public:
        explicit MdbxResourceImporter(MdbxResourceImporterOptions options);
        ~MdbxResourceImporter() override;

        /// \brief Performs the explicit quiesced/offline upgrade from legacy
        ///        v3/v4 intents to the tokenized fencing schema.
        ///
        /// No legacy writer may be running while this operation executes.
        /// The operation is atomic and idempotent; it creates a token for
        /// every existing intent and installs the current schema marker.
        /// Rolling upgrades are intentionally unsupported.
        static void migrate_legacy_fencing_state(
            const std::shared_ptr<MdbxStorageContext>& context,
            std::string_view table_prefix = "agent_memory"
        );

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
