#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_CANONICAL_CONTENT_STORE_INTERNAL_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_CANONICAL_CONTENT_STORE_INTERNAL_HPP_INCLUDED

/// \file CanonicalContentStoreInternal.hpp
/// \brief Backend-only bridge to the canonical-content semantic kernel.
///
/// This header is not part of the backend-neutral public storage surface. It
/// lets durable adapters validate and restore a current immutable state before
/// delegating edits to the semantics defined by
/// `guides/canonical-content-storage-roadmap.md`.

#include "CanonicalContentStore.hpp"

#include <set>

namespace agent_memory::detail {

/// \internal
/// \brief Restores validated current state for a durable backend replay.
/// \see `guides/canonical-content-storage-roadmap.md`
class CanonicalContentHistoryLoader final {
  public:
    [[nodiscard]] static bool restore_current(InMemoryCanonicalContentStore& store,
                                              CanonicalDocumentRevision current,
                                              std::set<ContentBlockId> all_seen_block_ids);
};

} // namespace agent_memory::detail

#endif
