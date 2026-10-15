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

    /// \brief Deterministic interruption points for importer recovery tests.
    enum class ResourceImporterFaultPoint {
        AfterIntent,
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
        std::function<void(ResourceImporterFaultPoint)> fault_injector;
    };

    /// \brief M0 importer composing durable lexical content and manifests.
    ///
    /// ResourceId is globally unique for this manifest profile. Scope remains
    /// part of the lexical identity; callers that reuse a human-readable name
    /// in multiple scopes must provide distinct globally qualified ResourceIds.
    /// Publication uses a durable intent table: a failure between canonical,
    /// lexical and manifest transactions leaves the prior manifest visible and
    /// is completed on reopen or retry.
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
