#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PERSISTENT_LEXICAL_INDEX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_PERSISTENT_LEXICAL_INDEX_HPP_INCLUDED

/// \file MdbxPersistentLexicalIndex.hpp
/// \brief Durable flat lexical postings and statistics adapter.

#include "MdbxStorageContext.hpp"

#if AGENT_MEMORY_HAS_MDBX

namespace mdbxc { class Transaction; }

#include <agent_memory/lexical/FlatLexicalIndex.hpp>

#include <cstddef>
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
    BeforeCommit,
    AfterWriteBeforeCommit
};

/// \brief Options for one scope/projection lexical index.
struct MdbxPersistentLexicalIndexOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    /// Maximum serialized flat-v2 value accepted by this bounded baseline.
    std::size_t max_payload_bytes = 8U * 1024U * 1024U;
    /// Maximum number of active unit inputs in one persisted value.
    std::size_t max_active_units = 100'000U;
    /// Maximum total token occurrences across active units.
    std::size_t max_total_tokens = 4'000'000U;
    /// Maximum historical normalized-token dictionary entries.
    std::size_t max_dictionary_entries = 4'000'000U;
    /// Maximum UTF-8 bytes in one normalized token.
    std::size_t max_token_bytes = 4'096U;
    /// Maximum supported descriptor layout-version entries for this adapter.
    std::size_t max_layout_versions = 256U;
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

/// \brief Lexical view reconstructed while an external read transaction is pinned.
struct MdbxPersistentLexicalRead final {
    FlatLexicalIndex index;
    LexicalReadFrontier frontier;

    MdbxPersistentLexicalRead(
        FlatLexicalIndex index_value,
        LexicalReadFrontier frontier_value
    ) : index(std::move(index_value)), frontier(std::move(frontier_value)) {}
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

    /// \brief Applies one lexical contribution through a coordinator-owned transaction.
    void upsert_in_transaction(
        FlatLexicalUnitInput input,
        std::optional<FlatLexicalExpectedState> expected_current,
        const mdbxc::Transaction& transaction
    );

    /// \brief Removes one active unit contribution atomically.
    [[nodiscard]] bool erase(const KnowledgeUnitId& unit_id);

    /// \brief Clears all active contributions while retaining the descriptor.
    void clear();

    /// \brief Materializes one coherent read frontier from a single read transaction.
    [[nodiscard]] MdbxPersistentLexicalIndexSnapshot read_snapshot() const;
    [[nodiscard]] MdbxPersistentLexicalRead read_in_transaction(
        const mdbxc::Transaction& transaction
    ) const;

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
