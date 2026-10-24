#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PERSISTENT_LEXICAL_INDEX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PERSISTENT_LEXICAL_INDEX_HPP_INCLUDED

/// \file MdbxPersistentLexicalIndex.hpp
/// \brief Durable flat lexical postings and statistics adapter.

#include "MdbxStorageContext.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <agent_memory/lexical/FlatLexicalIndex.hpp>

#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <utility>

namespace agent_memory {

/// \brief Deterministic interruption point used by MDBX rollback tests.
enum class MdbxPersistentLexicalIndexFaultPoint : std::uint8_t {
    None,
    BeforeCommit
};

/// \brief Options for one scope/projection lexical index.
struct MdbxPersistentLexicalIndexOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::shared_ptr<MdbxStorageContext> context;
    LexicalIndexDescriptor descriptor;
};

/// \brief Materialized state captured from one MDBX read transaction.
///
/// The adapter reads the descriptor, active snapshot, and all logical rows in
/// one transaction before constructing this dependency-free view. The view is
/// immutable by convention; publishing another revision creates a new view.
struct MdbxPersistentLexicalIndexSnapshot final {
    FlatLexicalIndex index;
    LexicalReadFrontier frontier;
    /// Opaque owner that keeps the MDBX read transaction pinned.
    /// The snapshot must be destroyed on the thread that created it.
    std::shared_ptr<void> read_transaction_owner;

    MdbxPersistentLexicalIndexSnapshot(
        FlatLexicalIndex index_value,
        LexicalReadFrontier frontier_value,
        std::shared_ptr<void> transaction_owner
    ) : index(std::move(index_value)),
        frontier(std::move(frontier_value)),
        read_transaction_owner(std::move(transaction_owner)) {}
};

/// \brief Durable flat lexical backend for one scope and projection.
///
/// The first storage slice deliberately uses one versioned KV payload in the
/// manifest-reserved `lexical_posting_segments` DBI. The payload contains the
/// logical dictionary inputs; postings and statistics are reconstructed through
/// the dependency-free FlatLexicalIndex oracle. A publication replaces the
/// payload in one writable MDBX transaction, so an aborted write leaves the
/// previous complete snapshot visible after reopen.
class MdbxPersistentLexicalIndex final {
public:
    explicit MdbxPersistentLexicalIndex(
        MdbxPersistentLexicalIndexOptions options
    );
    ~MdbxPersistentLexicalIndex();

    MdbxPersistentLexicalIndex(const MdbxPersistentLexicalIndex&) = delete;
    MdbxPersistentLexicalIndex& operator=(const MdbxPersistentLexicalIndex&) = delete;

    [[nodiscard]] const LexicalIndexDescriptor& descriptor() const noexcept;
    [[nodiscard]] LexicalActiveSnapshot active_snapshot() const;
    [[nodiscard]] LexicalPublicationSequence publication_sequence() const;

    /// \brief Publishes or replays one revision atomically.
    /// \throws std::runtime_error for stale, conflicting, or incompatible rows.
    void upsert(
        FlatLexicalUnitInput input,
        std::optional<FlatLexicalExpectedState> expected_current = std::nullopt
    );

    /// \brief Removes one active unit contribution atomically.
    [[nodiscard]] bool erase(const KnowledgeUnitId& unit_id);

    /// \brief Clears all active contributions while retaining the descriptor.
    void clear();

    /// \brief Materializes one coherent read frontier from a single read transaction.
    [[nodiscard]] MdbxPersistentLexicalIndexSnapshot read_snapshot() const;

    /// \brief Arms a deterministic pre-commit interruption for tests.
    void set_failure_injection_for_testing(
        MdbxPersistentLexicalIndexFaultPoint point
    ) noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
