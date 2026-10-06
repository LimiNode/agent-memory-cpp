#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_CANONICAL_CONTENT_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_CANONICAL_CONTENT_STORE_HPP_INCLUDED

/// \file CanonicalContentStore.hpp
/// \brief Backend-neutral canonical content contracts and in-memory reference store.
///
/// The interfaces deliberately contain no database, compression, or indexing
/// concepts. They are the production/reference boundary for the canonical
/// content vertical slice.

#include <agent_memory/domain/CanonicalContent.hpp>

#include <memory>
#include <optional>

namespace agent_memory {

    /// \brief Read/materialization contract for canonical content.
    ///
    /// See `guides/canonical-content-storage-roadmap.md`, sections
    /// "Canonical Content And Stable Blocks" and "Mutable Updates And
    /// Read/Materialize/Edit".
    class ICanonicalContentStore {
    public:
        virtual ~ICanonicalContentStore();

        /// \brief Reads the current immutable document revision.
        /// \return A copy of the revision, or no value when the document is absent.
        [[nodiscard]] virtual std::optional<CanonicalDocumentRevision> read_current(
            const DocumentId& document_id
        ) const = 0;

        /// \brief Reads one exact immutable historical revision.
        [[nodiscard]] virtual std::optional<CanonicalDocumentRevision> read_revision(
            const DocumentId& document_id,
            std::uint64_t revision
        ) const = 0;

        /// \brief Reads one block from the current or requested revision.
        [[nodiscard]] virtual std::optional<ContentBlock> read_block(
            const DocumentId& document_id,
            const ContentBlockId& block_id,
            std::optional<std::uint64_t> revision = std::nullopt
        ) const = 0;

        /// \brief Materializes deterministic Markdown from the canonical preorder.
        /// \note Input block text is treated as already normalized UTF-8.
        [[nodiscard]] virtual std::optional<std::string> materialize_markdown(
            const DocumentId& document_id,
            std::optional<std::uint64_t> revision = std::nullopt
        ) const = 0;
    };

    /// \brief Atomic creation and optimistic edit contract for canonical content.
    class ICanonicalContentEditor {
    public:
        virtual ~ICanonicalContentEditor();

        /// \brief Creates the initial revision-zero document.
        /// \return False when the document id or baseline is invalid.
        /// \post Every accepted baseline block has store-owned revision one.
        [[nodiscard]] virtual bool create_document(
            CanonicalDocumentRevision initial
        ) = 0;

        /// \brief Atomically publishes one optimistic canonical-content edit.
        /// \return Conflict for a stale expected revision; invalid requests
        /// leave the current revision unchanged. The change set is a net diff.
        /// \thread_safety Implementations serialize commits and publish atomically.
        [[nodiscard]] virtual CanonicalEditResult commit(
            const CanonicalEditRequest& request
        ) = 0;
    };

    /// \brief Reference implementation with immutable in-memory revision history.
    class InMemoryCanonicalContentStore final
        : public ICanonicalContentStore
        , public ICanonicalContentEditor {
    public:
        InMemoryCanonicalContentStore();
        ~InMemoryCanonicalContentStore() override;

        /// \brief Creates a document at revision zero.
        /// \return False when the id exists or the baseline is invalid.
        [[nodiscard]] bool create_document(CanonicalDocumentRevision initial) override;

        [[nodiscard]] std::optional<CanonicalDocumentRevision> read_current(
            const DocumentId& document_id
        ) const override;
        [[nodiscard]] std::optional<CanonicalDocumentRevision> read_revision(
            const DocumentId& document_id,
            std::uint64_t revision
        ) const override;
        [[nodiscard]] std::optional<ContentBlock> read_block(
            const DocumentId& document_id,
            const ContentBlockId& block_id,
            std::optional<std::uint64_t> revision = std::nullopt
        ) const override;
        [[nodiscard]] std::optional<std::string> materialize_markdown(
            const DocumentId& document_id,
            std::optional<std::uint64_t> revision = std::nullopt
        ) const override;
        [[nodiscard]] CanonicalEditResult commit(
            const CanonicalEditRequest& request
        ) override;

    private:
        struct DocumentHistory;
        struct Impl;
        std::unique_ptr<Impl> m_impl;
    };

} // namespace agent_memory

#endif
