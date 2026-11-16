#pragma once
#ifndef AGENT_MEMORY_HEADER_LEXICAL_I_LEXICAL_INDEX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_LEXICAL_I_LEXICAL_INDEX_HPP_INCLUDED

/// \file ILexicalIndex.hpp
/// \brief Dependency-free lexical index contract.

#include "Lexical.hpp"

#include <optional>

namespace agent_memory {

    /// \brief Contract implemented by lexical search indexes.
    ///
    /// Thread-safety:
    ///   Implementations are not required to be thread-safe. The intended
    ///   pattern is single-writer (during ingestion) and many-readers
    ///   (during query). Concurrent calls have unspecified behavior.
    ///
    /// Exception contract:
    ///   - Mutating methods (upsert, erase, erase_resource, clear) may
    ///     throw std::bad_alloc on allocation failure or std::invalid_argument
    ///     when a precondition is violated (e.g. is_valid(record) is false).
    ///   - I/O backends (e.g. MDBX) may also throw std::system_error for
    ///     storage failures.
    ///   - Query methods (size, find_stats, search) are noexcept where the
    ///     implementation permits it.
    ///
    /// Document-frequency ownership:
    ///   An implementation that uses an external ITokenDictionary owns the
    ///   corresponding document-frequency updates (via
    ///   increment_document_frequency and decrement_document_frequency).
    ///   The ILexicalIndex contract does not require every implementation to
    ///   use that backend: a self-contained index may keep its token
    ///   statistics internally. The lifetime and clear() semantics of an
    ///   external dictionary remain the responsibility of its owner.
    class ILexicalIndex {
    public:
        virtual ~ILexicalIndex();

        /// \brief Number of indexed chunks.
        [[nodiscard]] virtual std::size_t size() const noexcept = 0;

        /// \brief Inserts or replaces one tokenized chunk.
        /// \pre `is_valid(record)` must be true.
        /// \throws std::invalid_argument if !is_valid(record).
        virtual void upsert(LexicalDocumentRecord record) = 0;

        /// \brief Finds stored lexical stats by chunk id.
        [[nodiscard]] virtual std::optional<LexicalDocumentStats> find_stats(
            const ChunkId& chunk_id
        ) const = 0;

        /// \brief Searches indexed chunks with normalized query terms.
        /// \pre `is_valid(query)` must be true unless `query.limit == 0`.
        /// \return Scored hits ordered with higher scores first.
        /// \note search() returns up to `limit` results, ordered by descending
        ///       score. Ties (equal scores) are broken deterministically by
        ///       chunk_id ascending. Implementations must not return results
        ///       in unspecified order.
        [[nodiscard]] virtual std::vector<LexicalSearchResult> search(
            const LexicalSearchQuery& query
        ) const = 0;

        /// \brief Removes one indexed chunk.
        /// \return true if the chunk was present and removed, false if it was
        ///         already absent (idempotent).
        [[nodiscard]] virtual bool erase(const ChunkId& chunk_id) = 0;

        /// \brief Removes all chunks owned by one resource.
        /// \return the number of chunks removed (0 if the resource was not
        ///         indexed). Idempotent on repeated calls.
        [[nodiscard]] virtual std::size_t erase_resource(const ResourceId& resource_id) = 0;

        /// \brief Removes all indexed chunks and statistics owned by this
        /// implementation.
        ///
        /// An external ITokenDictionary, when used by an implementation, is
        /// not implicitly cleared by this call unless that implementation's
        /// documented contract says so.
        virtual void clear() = 0;
    };

} // namespace agent_memory

#endif
