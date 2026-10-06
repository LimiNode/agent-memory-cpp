#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_CANONICAL_CONTENT_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_CANONICAL_CONTENT_HPP_INCLUDED

/// \file CanonicalContent.hpp
/// \brief Immutable canonical text document revisions and typed edits.
///
/// Contract owner: `guides/canonical-content-storage-roadmap.md`, sections
/// "Canonical Content And Stable Blocks" and "Mutable Updates And
/// Read/Materialize/Edit".

#include "Identifiers.hpp"
#include "Metadata.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace agent_memory {

    /// \brief Stable identity of a canonical document block.
    ///
    /// Block identity survives content edits and structural moves. A deleted
    /// identity is not silently reused within the same document history.
    /// Normative semantics are defined by the canonical-content storage
    /// roadmap, section "Canonical Content And Stable Blocks".
    class ContentBlockId final {
    public:
        ContentBlockId() = default;
        explicit ContentBlockId(std::string value);

        /// \brief Returns the stable identifier text.
        [[nodiscard]] const std::string& value() const noexcept;
        /// \brief Checks whether the identifier is missing.
        [[nodiscard]] bool empty() const noexcept;

    private:
        std::string m_value;
    };

    [[nodiscard]] bool operator==(const ContentBlockId& lhs, const ContentBlockId& rhs) noexcept;
    [[nodiscard]] bool operator!=(const ContentBlockId& lhs, const ContentBlockId& rhs) noexcept;
    [[nodiscard]] bool operator<(const ContentBlockId& lhs, const ContentBlockId& rhs) noexcept;

    /// \brief Semantic kind of a canonical text block.
    enum class ContentBlockKind {
        Heading,
        Paragraph,
        Code
    };

    /// \brief A block in an immutable canonical document revision.
    ///
    /// Blocks in a revision are stored in deterministic canonical tree
    /// preorder. The block revision is owned by the content store and changes
    /// at most once for each published document revision. Input text is
    /// already normalized UTF-8 and is retained byte-for-byte.
    struct ContentBlock final {
        ContentBlockId id;
        std::uint64_t revision = 1;
        ContentBlockKind kind = ContentBlockKind::Paragraph;
        std::optional<ContentBlockId> parent_id;
        std::string text;
    };

    /// \brief Immutable logical document state identified by a monotonic revision.
    ///
    /// A document revision is the normative structured materialization. Older
    /// revisions remain readable after a later edit.
    struct CanonicalDocumentRevision final {
        DocumentId document_id;
        std::uint64_t revision = 0;
        Metadata metadata;
        std::vector<ContentBlock> blocks;
    };

    /// \brief Replaces the normalized UTF-8 payload of an existing block.
    struct ReplaceBlockText final {
        ContentBlockId block_id;
        std::string text;
    };

    /// \brief Inserts one new block at a sibling ordinal for this edit.
    ///
    /// The ordinal is a command input only; it is not persisted as durable
    /// block state. Existing block identities are never renumbered. Values
    /// greater than the current sibling count are invalid; the count itself
    /// means append.
    struct InsertBlock final {
        ContentBlock block;
        std::size_t position = 0;
    };

    /// \brief Removes a leaf block from the published revision.
    ///
    /// Deleting a block with children is invalid in this minimal slice.
    struct DeleteBlock final {
        ContentBlockId block_id;
    };

    /// \brief Moves a block subtree to a sibling ordinal for this edit.
    /// Values greater than the sibling count after removing the subtree are
    /// invalid; the count itself means append.
    struct MoveBlock final {
        ContentBlockId block_id;
        std::optional<ContentBlockId> parent_id;
        std::size_t position = 0;
    };

    /// \brief Replaces document metadata as part of the same atomic edit.
    struct UpdateDocumentMetadata final {
        Metadata metadata;
    };

    using CanonicalEditOperation = std::variant<
        ReplaceBlockText,
        InsertBlock,
        DeleteBlock,
        MoveBlock,
        UpdateDocumentMetadata
    >;

    /// \brief One optimistic, atomically published edit request.
    ///
    /// All operations use the same expected revision. A stale request is
    /// rejected as a conflict and cannot partially publish.
    struct CanonicalEditRequest final {
        DocumentId document_id;
        std::uint64_t expected_revision = 0;
        std::vector<CanonicalEditOperation> operations;
    };

    /// \brief Net difference between the old and published revisions.
    ///
    /// Transient intermediate operations are omitted. Structure changed is
    /// derived from the final inserted, removed, and moved sets.
    struct ContentChangeSet final {
        std::vector<ContentBlockId> changed_blocks;
        std::vector<ContentBlockId> inserted_blocks;
        std::vector<ContentBlockId> removed_blocks;
        std::vector<ContentBlockId> moved_blocks;
        bool metadata_changed = false;
        bool structure_changed = false;
        std::optional<std::uint64_t> old_revision;
        std::optional<std::uint64_t> new_revision;
    };

    /// \brief Outcome of an optimistic canonical-content edit.
    ///
    /// `NoChange` returns the current revision without publishing a new one;
    /// normal conflicts and invalid edits are represented without exceptions.
    enum class CanonicalEditStatus {
        Ok,
        NoChange,
        NotFound,
        Conflict,
        InvalidEdit
    };

    /// \brief Edit outcome, optional published revision, and net change set.
    struct CanonicalEditResult final {
        CanonicalEditStatus status = CanonicalEditStatus::InvalidEdit;
        std::optional<CanonicalDocumentRevision> revision;
        ContentChangeSet changes;
        std::string message;

        /// \brief Checks whether the request was accepted or was a logical no-op.
        [[nodiscard]] bool succeeded() const noexcept;
    };

} // namespace agent_memory

#endif
