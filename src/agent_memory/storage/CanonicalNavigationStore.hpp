#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_CANONICAL_NAVIGATION_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_CANONICAL_NAVIGATION_STORE_HPP_INCLUDED

/// \file CanonicalNavigationStore.hpp
/// \brief Revision-bound, bounded navigation over canonical content storage.

#include <agent_memory/domain/CanonicalNavigation.hpp>
#include <agent_memory/storage/CanonicalContentStore.hpp>

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace agent_memory {

/// \brief Storage-facing adapter for exact, bounded canonical navigation.
///
/// Every operation performs exactly one `read_revision()` call. The returned
/// immutable value is then used for the complete operation; no current read,
/// per-block read, or later revision lookup is performed. This makes the
/// adapter safe to use with a caller-owned revision pin and gives all results
/// one-snapshot semantics at the storage contract boundary.
///
/// The current `ICanonicalContentStore` contract returns a materialized
/// revision. Consequently this adapter is logically lazy (it does not build
/// outlines or decode text until the requested operation) but cannot avoid the
/// store's materialization cost. A backend with record-level reads may replace
/// this adapter while preserving the same result and budget semantics.
class CanonicalNavigationStore final {
public:
    explicit CanonicalNavigationStore(const ICanonicalContentStore& store) noexcept;

    /// \brief Reads a complete metadata-only outline at an exact revision.
    /// \return No value when the document/revision is absent or malformed, or
    /// when `max_entries` is zero or smaller than the document.
    [[nodiscard]] std::optional<CanonicalOutline> read_outline(
        const DocumentId& document_id,
        std::uint64_t revision,
        std::size_t max_entries = 4096) const;

    /// \brief Reads direct children without returning their text bodies.
    ///
    /// A null parent denotes document roots. If the child budget is reached,
    /// already admitted entries are returned with `BudgetExceeded` and
    /// `truncated=true`.
    [[nodiscard]] CanonicalChildrenResult read_children(
        const DocumentId& document_id,
        std::uint64_t revision,
        std::optional<ContentBlockId> parent_id,
        std::size_t max_children = 256) const;

    /// \brief Reads a bounded subtree at an exact revision.
    [[nodiscard]] CanonicalSubtreeResult read_subtree(
        const DocumentId& document_id,
        std::uint64_t revision,
        const CanonicalSubtreeRequest& request) const;

private:
    const ICanonicalContentStore* m_store;
};

} // namespace agent_memory

#endif
