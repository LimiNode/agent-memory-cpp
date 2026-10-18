#pragma once
#ifndef AGENT_MEMORY_HEADER_INGESTION_RESOURCE_IMPORTER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INGESTION_RESOURCE_IMPORTER_HPP_INCLUDED

/// \file ResourceImporter.hpp
/// \brief Backend-neutral lexical-first resource import contract.

#include <agent_memory/domain/KnowledgeUnit.hpp>

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

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

    /// \brief Result of publishing one source/body revision.
    ///
    /// The lexical-only profile publishes one document and one chunk-shaped
    /// record. The explicit raw-chunk publication profile additionally
    /// publishes revision-bound raw Chunk ids; this result still does not claim
    /// a general Original projection store.
    struct ResourceImportResult final {
        ResourceId resource_id;
        ResourceRevision revision;
        DocumentId document_id;
        ChunkId chunk_id;
        /// Raw revision-bound Chunk units bound by the raw publication profile;
        /// empty for the lexical-only profile. Source URI is historical
        /// revision metadata and is not rewritten by URI-only reimports.
        std::vector<KnowledgeUnitId> raw_units;
    };

    /// \brief M0 importer boundary for observed UTF-8 resources.
    ///
    /// `revision.generation` is the canonical body generation in this narrow
    /// profile. Pipeline and extractor metadata are bound to that generation;
    /// changing them without a new body generation is rejected until an
    /// independent projection-generation contract exists.
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
