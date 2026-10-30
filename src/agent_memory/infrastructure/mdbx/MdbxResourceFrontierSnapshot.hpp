#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_FRONTIER_SNAPSHOT_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_FRONTIER_SNAPSHOT_HPP_INCLUDED

/// \file MdbxResourceFrontierSnapshot.hpp
/// \brief Consistent graph-free resource and KnowledgeUnit read frontier.

#include "MdbxKnowledgeUnitStore.hpp"
#include "MdbxResourceManifestStorage.hpp"

#include <cstdint>
#include <cstddef>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#if AGENT_MEMORY_HAS_MDBX

namespace mdbxc {
class Transaction;
}

namespace agent_memory {

/// \brief Move-only owner for one MDBX read transaction.
///
/// MDBX transactions are thread-bound. Moving or destroying this owner on a
/// different thread terminates the process deliberately; callers must keep
/// the owner on the thread that created the snapshot.
class MdbxReadTransactionOwner final {
public:
    explicit MdbxReadTransactionOwner(std::unique_ptr<mdbxc::Transaction> transaction);
    ~MdbxReadTransactionOwner();

    MdbxReadTransactionOwner(const MdbxReadTransactionOwner&) = delete;
    MdbxReadTransactionOwner& operator=(const MdbxReadTransactionOwner&) = delete;
    MdbxReadTransactionOwner(MdbxReadTransactionOwner&& other) noexcept;
    MdbxReadTransactionOwner& operator=(MdbxReadTransactionOwner&& other) noexcept;

    [[nodiscard]] bool active() const noexcept;

private:
    void release() noexcept;

    std::unique_ptr<mdbxc::Transaction> m_transaction;
    std::thread::id m_owner_thread;
};

inline constexpr std::size_t kMdbxResourceFrontierMaxInputs = 4096;
inline constexpr std::size_t kMdbxResourceFrontierMaxDecodedBytes = 1U * 1024U * 1024U;

/// \brief One unit and the exact root revision it claims as provenance.
struct MdbxResourceFrontierInput final {
    KnowledgeUnitId unit_id;
    std::uint64_t unit_revision = 0;
    ResourceRevisionRef root_revision;
};

/// \brief Result of validating one stable multi-store read snapshot.
enum class MdbxResourceFrontierSnapshotStatus : std::uint8_t {
    Available,
    MissingInput,
    ScopeMismatch,
    InactiveInput,
    UnitRevisionMismatch,
    RootRevisionMismatch,
    RootDigestMismatch,
    Unavailable
};

struct MdbxResourceFrontierEntry final {
    KnowledgeUnitEnvelope unit;
    ResourceManifest root;
};

/// \brief Materialized frontier captured by one MDBX read transaction.
///
/// All entries are decoded before the transaction is released. The opaque
/// owner deliberately keeps the read transaction pinned for the lifetime of
/// the snapshot, documenting the transaction/lifetime boundary for callers
/// that retain the result. It must be moved and destroyed on its creating
/// thread; the typed owner rejects cross-thread destruction.
struct MdbxResourceFrontierSnapshot final {
    ScopeId scope_id;
    std::vector<MdbxResourceFrontierEntry> entries;
    std::optional<MdbxReadTransactionOwner> read_transaction_owner;
};

struct MdbxResourceFrontierSnapshotResult final {
    MdbxResourceFrontierSnapshotStatus status =
        MdbxResourceFrontierSnapshotStatus::Unavailable;
    std::optional<MdbxResourceFrontierSnapshot> snapshot;
};

/// \brief Reads KnowledgeUnits and resource manifests from one stable MDBX view.
///
/// This is the B1 conformance slice. It validates scope, active lifecycle,
/// direct unit revision, root generation, and root digest. It does not publish
/// derived data and does not provide a write-side fence; those are B2.
class MdbxResourceFrontierSnapshotReader final {
public:
    explicit MdbxResourceFrontierSnapshotReader(
        std::shared_ptr<MdbxStorageContext> context,
        std::string table_prefix = "agent_memory"
    );

    [[nodiscard]] MdbxResourceFrontierSnapshotResult read(
        const ScopeId& scope_id,
        const std::vector<MdbxResourceFrontierInput>& inputs
    ) const;

    /// \brief Installs a deterministic test seam invoked after each input read.
    ///
    /// This hook is for conformance fixtures only; production callers should
    /// leave it unset. The callback runs on the reader thread.
    void set_read_hook_for_testing(std::function<void(std::size_t)> hook);

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    MdbxResourceManifestStorage m_manifests;
    MdbxKnowledgeUnitStore m_units;
    mutable std::function<void(std::size_t)> m_read_hook_for_testing;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
