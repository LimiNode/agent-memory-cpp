#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_CANONICAL_CONTENT_STORE_INTERNAL_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_CANONICAL_CONTENT_STORE_INTERNAL_HPP_INCLUDED

#include "CanonicalContentStore.hpp"

#include <set>

namespace agent_memory::detail {

/// Backend-only bridge for reconstructing validated immutable history before
/// applying the shared in-memory semantic transition kernel.
class CanonicalContentHistoryLoader final {
public:
    [[nodiscard]] static bool restore_current(
        InMemoryCanonicalContentStore& store,
        CanonicalDocumentRevision current,
        std::set<ContentBlockId> all_seen_block_ids
    );
};

} // namespace agent_memory::detail

#endif
