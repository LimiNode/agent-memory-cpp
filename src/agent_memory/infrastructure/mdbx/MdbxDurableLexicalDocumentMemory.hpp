#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_DURABLE_LEXICAL_DOCUMENT_MEMORY_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_DURABLE_LEXICAL_DOCUMENT_MEMORY_HPP_INCLUDED

/// \file MdbxDurableLexicalDocumentMemory.hpp
/// \brief Minimal durable M0 lexical document-memory vertical slice.

#include "MdbxCanonicalContentStore.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <agent_memory/domain/KnowledgeUnit.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace agent_memory {

/// \brief Options for the durable lexical document-memory profile.
struct MdbxDurableLexicalDocumentMemoryOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::shared_ptr<MdbxStorageContext> context;
};

/// \brief One lexical hit hydrated from the durable canonical body.
struct DurableLexicalDocumentHit final {
    ScopeId scope_id;
    ResourceId resource_id;
    ChunkId chunk_id;
    std::string source_uri;
    std::string text;
    std::uint64_t generation = 0;
    float score = 0.0F;
};

/// \brief Small deterministic M0 path from durable text to lexical retrieval.
///
/// The profile stores one canonical text block per logical resource and keeps
/// the BM25 index as a rebuildable derived view. Reopening the same MDBX file
/// reconstructs the lexical view from the durable canonical bodies. One
/// resource maps to one chunk in this first vertical slice; chunking and
/// projection publication remain separate M0 follow-ups.
///
/// This class deliberately does not implement dense retrieval, hybrid fusion,
/// knowledge-unit payloads, graph expansion, temporal validity, or background
/// maintenance. Those capabilities remain outside this PR's M0 scope.
class MdbxDurableLexicalDocumentMemory final {
public:
    explicit MdbxDurableLexicalDocumentMemory(
        MdbxDurableLexicalDocumentMemoryOptions options
    );
    ~MdbxDurableLexicalDocumentMemory();

    MdbxDurableLexicalDocumentMemory(
        const MdbxDurableLexicalDocumentMemory&
    ) = delete;
    MdbxDurableLexicalDocumentMemory& operator=(
        const MdbxDurableLexicalDocumentMemory&
    ) = delete;

    /// \brief Inserts or updates one original UTF-8 text resource.
    ///
    /// The ResourceId remains stable across updates. The returned generation
    /// is one-based and advances only when the body text changes.
    /// \throws std::invalid_argument for empty identities, URI, or body.
    /// \throws std::runtime_error when a durable catalog points to a missing
    /// canonical body or the storage backend rejects the publication.
    [[nodiscard]] std::uint64_t upsert_text(
        ScopeId scope_id,
        ResourceId resource_id,
        std::string source_uri,
        std::string utf8_text
    );

    /// \brief Executes deterministic BM25 search within one scope.
    /// \throws std::invalid_argument for an empty query or zero limit.
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
