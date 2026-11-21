#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_STORAGE_METRICS_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CANONICAL_STORAGE_METRICS_HPP_INCLUDED

#include <cstdint>

namespace agent_memory {

/// Optional caller-owned diagnostics for canonical adapter value operations.
/// These count canonical-adapter DBI calls and value bytes, not MDBX pages or disk I/O.
/// Identity DBI internals are excluded; dictionary calls and elapsed time are separate.
/// Successful value puts are counted even when their enclosing transaction later aborts.
/// The caller must keep the object alive and synchronize shared access.
struct MdbxCanonicalStorageMetrics final {
    std::uint64_t value_reads = 0;
    std::uint64_t value_writes = 0;
    std::uint64_t value_bytes_read = 0;
    std::uint64_t value_bytes_written = 0;
    std::uint64_t block_versions_written = 0;
    std::uint64_t dictionary_lookups = 0;
    std::uint64_t dictionary_ns = 0;
    std::uint64_t encode_ns = 0;
    std::uint64_t decode_ns = 0;
};

} // namespace agent_memory

#endif
