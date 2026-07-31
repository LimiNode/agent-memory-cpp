#pragma once
#ifndef AGENT_MEMORY_HEADER_RETRIEVAL_RETRIEVAL_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_RETRIEVAL_RETRIEVAL_HPP_INCLUDED

/// \file Retrieval.hpp
/// \brief Dependency-free retrieval query and result value types.

#include <agent_memory/domain/Document.hpp>
#include <agent_memory/domain/Metadata.hpp>
#include <agent_memory/domain/MetadataFilter.hpp>
#include <agent_memory/embedding/embedding_types.hpp>

#include <cstddef>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

    /// \brief Query passed to retrieval implementations.
    struct RetrievalQuery final {
        std::string text;
        std::optional<Embedding> embedding;
        /// \brief Maximum number of chunks to return. Zero requests no chunks.
        std::size_t limit = 10;
        std::vector<MetadataFilter> metadata_filters;

        /// \brief Returns true when the query contains source text.
        [[nodiscard]] bool has_text() const noexcept;

        /// \brief Returns true when the query contains a non-empty embedding.
        [[nodiscard]] bool has_embedding() const noexcept;
    };

    /// \brief Legacy chunk returned by a retriever with a comparable score.
    ///
    /// This pre-M0 result cannot express a revision-bound citation. Public
    /// artifact-aware retrieval must return the canonical provenance summary
    /// and hydrate full SourceRef/EvidenceAnchor details on demand.
    struct RetrievedChunk final {
        DocumentChunk chunk;
        /// \brief Comparable retrieval score where higher is always better.
        float score = 0.0F;
        Metadata metadata;
    };

    /// \brief Ordered retrieval response.
    struct RetrievalResult final {
        std::vector<RetrievedChunk> chunks;

        /// \brief Returns true when no chunks were retrieved.
        [[nodiscard]] bool empty() const noexcept;

        /// \brief Returns the number of retrieved chunks.
        [[nodiscard]] std::size_t size() const noexcept;
    };

} // namespace agent_memory

#endif
