#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_CANONICAL_NAVIGATION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_CANONICAL_NAVIGATION_HPP_INCLUDED

/// \file CanonicalNavigation.hpp
/// \brief Bounded, revision-pinned navigation over canonical block trees.

#include "CanonicalContent.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

    /// \brief Stable reference to a block in one exact document revision.
    struct CanonicalBlockLocator final {
        DocumentId document_id;
        std::uint64_t document_revision = 0;
        ContentBlockId block_id;
        std::uint64_t block_revision = 0;
    };

    /// \brief Metadata-only outline entry; text is not copied into an outline.
    struct CanonicalOutlineEntry final {
        CanonicalBlockLocator locator;
        ContentBlockKind kind = ContentBlockKind::Paragraph;
        std::optional<ContentBlockId> parent_id;
        std::size_t depth = 0;
        std::size_t text_bytes = 0;
        std::size_t child_count = 0;
    };

    struct CanonicalOutline final {
        DocumentId document_id;
        std::uint64_t document_revision = 0;
        std::vector<CanonicalOutlineEntry> entries;
    };

    /// \brief Bounded subtree read request against an already pinned revision.
    struct CanonicalSubtreeRequest final {
        std::optional<ContentBlockId> root_id;
        std::size_t max_blocks = 256;
        std::size_t max_text_bytes = 256U * 1024U;
        bool include_text = true;
    };

    enum class CanonicalNavigationStatus {
        Ok,
        NotFound,
        InvalidDocument,
        BudgetExceeded
    };

    struct CanonicalSubtreeResult final {
        CanonicalNavigationStatus status = CanonicalNavigationStatus::InvalidDocument;
        DocumentId document_id;
        std::uint64_t document_revision = 0;
        std::vector<ContentBlock> blocks;
        bool truncated = false;
        std::string message;

        [[nodiscard]] bool succeeded() const noexcept {
            return status == CanonicalNavigationStatus::Ok ||
                status == CanonicalNavigationStatus::BudgetExceeded;
        }
    };

    /// \brief Builds a metadata-only outline in canonical preorder.
    [[nodiscard]] std::optional<CanonicalOutline> build_canonical_outline(
        const CanonicalDocumentRevision& document,
        std::size_t max_entries = 4096);

    /// \brief Reads a bounded canonical subtree without changing source state.
    ///
    /// A budget-limited result is explicit (`BudgetExceeded`, `truncated=true`)
    /// and remains pinned to the supplied document revision. No newer revision
    /// is consulted and no block text is returned when `include_text=false`.
    [[nodiscard]] CanonicalSubtreeResult read_canonical_subtree(
        const CanonicalDocumentRevision& document,
        const CanonicalSubtreeRequest& request);

} // namespace agent_memory

#endif
