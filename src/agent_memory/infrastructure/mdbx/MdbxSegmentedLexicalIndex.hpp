#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_SEGMENTED_LEXICAL_INDEX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_SEGMENTED_LEXICAL_INDEX_HPP_INCLUDED

/// \file MdbxSegmentedLexicalIndex.hpp
/// \brief Experimental durable segmented lexical storage adapter.

#include "MdbxStorageContext.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <agent_memory/lexical/LexicalSegmentEncoding.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <utility>

namespace agent_memory {

enum class MdbxSegmentedLexicalIndexFaultPoint : std::uint8_t {
    None,
    BeforeCommit,
    AfterWriteBeforeCommit
};

struct MdbxSegmentedLexicalIndexDiagnostics;

struct MdbxSegmentedLexicalIndexOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::shared_ptr<MdbxStorageContext> context;
    LexicalIndexDescriptor descriptor;
    LexicalSegmentEncodingLimits encoding_limits;
    /// Optional counters for an instrumented run; null keeps the hot path quiet.
    MdbxSegmentedLexicalIndexDiagnostics* diagnostics = nullptr;
};

/// \brief Actual KV work observed by one segmented-index instance.
///
/// Counters describe adapter calls and decoded rows, not durable page writes.
/// They are intended for receipts and are populated only when the options
/// diagnostics pointer is supplied.
struct MdbxSegmentedLexicalIndexDiagnostics final {
    std::uint64_t point_reads = 0;
    std::uint64_t range_rows_visited = 0;
    std::uint64_t kv_inserts_or_assigns = 0;
    std::uint64_t kv_deletes = 0;
    std::uint64_t bytes_decoded = 0;
    std::uint64_t bytes_encoded = 0;
    std::uint64_t touched_tokens = 0;
    std::uint64_t decoded_postings = 0;
};

struct MdbxSegmentedLexicalIndexSnapshot final {
    FlatLexicalIndex index;
    LexicalReadFrontier frontier;
    std::shared_ptr<void> read_transaction_owner;

    MdbxSegmentedLexicalIndexSnapshot(
        FlatLexicalIndex index_value,
        LexicalReadFrontier frontier_value,
        std::shared_ptr<void> transaction_owner
    ) : index(std::move(index_value)),
        frontier(std::move(frontier_value)),
        read_transaction_owner(std::move(transaction_owner)) {}
};

/// \brief Experimental lexical-only segmented backend.
///
/// Rows are stored in a dedicated versioned keyspace inside the existing KV
/// table. It never interprets or rewrites the flat-v2 value. Publication is
/// atomic within this lexical store only; canonical units, projections, and
/// resource manifests remain outside its consistency boundary.
class MdbxSegmentedLexicalIndex final {
public:
    explicit MdbxSegmentedLexicalIndex(
        MdbxSegmentedLexicalIndexOptions options
    );
    ~MdbxSegmentedLexicalIndex();

    MdbxSegmentedLexicalIndex(const MdbxSegmentedLexicalIndex&) = delete;
    MdbxSegmentedLexicalIndex& operator=(const MdbxSegmentedLexicalIndex&) = delete;

    [[nodiscard]] const LexicalIndexDescriptor& descriptor() const noexcept;
    [[nodiscard]] LexicalActiveSnapshot active_snapshot() const;
    [[nodiscard]] LexicalPublicationSequence publication_sequence() const;

    void upsert(
        FlatLexicalUnitInput input,
        std::optional<FlatLexicalExpectedState> expected_current = std::nullopt
    );
    [[nodiscard]] bool erase(const KnowledgeUnitId& unit_id);
    void clear();

    [[nodiscard]] MdbxSegmentedLexicalIndexSnapshot read_snapshot() const;

    void set_failure_injection_for_testing(
        MdbxSegmentedLexicalIndexFaultPoint point
    ) noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
