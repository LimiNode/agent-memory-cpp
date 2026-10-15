#pragma once
#ifndef AGENT_MEMORY_HEADER_INGESTION_RESOURCE_IMPORTER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INGESTION_RESOURCE_IMPORTER_HPP_INCLUDED

/// \file ResourceImporter.hpp
/// \brief Backend-neutral lexical-first resource import contract.

#include <agent_memory/domain/KnowledgeUnit.hpp>

#include <cstdint>
#include <optional>
#include <string>

namespace agent_memory {

    /// \brief One host-observed UTF-8 source presented to an importer.
    struct ObservedResource final {
        ScopeId scope_id;
        ResourceId resource_id;
        std::string source_uri;
        std::string utf8_body;
        SourceTextOrigin text_origin = SourceTextOrigin::OriginalText;
        std::optional<DerivedTextProvenance> derived_text_provenance;
        std::uint64_t pipeline_config_hash = 1;
    };

    /// \brief Result of publishing one source revision.
    ///
    /// This first profile publishes one lexical document and one chunk-shaped
    /// record. It intentionally does not claim a KnowledgeUnitStore or a
    /// general Original projection store.
    struct ResourceImportResult final {
        ResourceId resource_id;
        ResourceRevision revision;
        DocumentId document_id;
        ChunkId chunk_id;
    };

    /// \brief M0 importer boundary for observed UTF-8 resources.
    class IResourceImporter {
    public:
        virtual ~IResourceImporter();

        /// \brief Validates and publishes one resource revision.
        /// \throws std::invalid_argument for invalid identity, UTF-8 body, or
        /// derived-text provenance.
        /// \throws std::runtime_error when durable recovery cannot complete.
        [[nodiscard]] virtual ResourceImportResult import(
            ObservedResource resource
        ) = 0;
    };

} // namespace agent_memory

#endif
