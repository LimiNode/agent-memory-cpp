#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_I_DOCUMENT_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_I_DOCUMENT_STORAGE_HPP_INCLUDED

/// \file IDocumentStorage.hpp
/// \brief Storage contract for source documents and derived chunks.

#include <agent_memory/domain/Document.hpp>

#include <optional>
#include <vector>

namespace agent_memory {

    /// \brief Complete persisted state of a document and its derived chunks.
    /// \pre `document.id` is globally unique within the storage backend.
    /// \pre Each chunk must have `document_id == document.id`.
    /// \pre Each chunk id must be globally unique within the storage backend.
    struct DocumentSnapshot final {
        Document document;
        std::vector<DocumentChunk> chunks;
    };

    /// \brief Dependency-free contract for document/chunk persistence.
    class IDocumentStorage {
    public:
        virtual ~IDocumentStorage();

        /// \brief Inserts or replaces a document and all chunks derived from it.
        /// \param snapshot Complete document state to persist.
        /// \pre `snapshot.document.id` must not be reused by a different resource revision.
        /// \pre Each chunk in `snapshot.chunks` must belong to `snapshot.document`.
        /// \pre Chunk ids must not be reused across different documents.
        virtual void upsert_document(DocumentSnapshot snapshot) = 0;

        /// \brief Finds a document by id.
        /// \param id Document id to look up.
        /// \return Document copy when found.
        [[nodiscard]] virtual std::optional<Document> find_document(
            const DocumentId& id
        ) const = 0;

        /// \brief Finds a chunk by id.
        /// \param id Chunk id to look up.
        /// \return Chunk copy when found.
        [[nodiscard]] virtual std::optional<DocumentChunk> find_chunk(
            const ChunkId& id
        ) const = 0;

        /// \brief Lists chunks derived from a document.
        /// \param document_id Source document id.
        /// \return Chunks in storage-defined deterministic order.
        [[nodiscard]] virtual std::vector<DocumentChunk> list_chunks(
            const DocumentId& document_id
        ) const = 0;

        /// \brief Removes a document and its derived chunks.
        /// \param id Document id to remove.
        /// \return True when a document or at least one chunk was removed.
        [[nodiscard]] virtual bool erase_document(const DocumentId& id) = 0;
    };

} // namespace agent_memory

#endif
