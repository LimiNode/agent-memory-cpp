#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_FRONTIER_SNAPSHOT_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_RESOURCE_FRONTIER_SNAPSHOT_HPP_INCLUDED

/// \file MdbxResourceFrontierSnapshot.hpp
/// \brief Consistent graph-free resource and KnowledgeUnit read frontier.

#include "MdbxKnowledgeUnitStore.hpp"
#include "MdbxResourceManifestStorage.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#if AGENT_MEMORY_HAS_MDBX

namespace agent_memory {

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
/// that retain the result. It must be destroyed on its creating thread.
struct MdbxResourceFrontierSnapshot final {
    ScopeId scope_id;
    std::vector<MdbxResourceFrontierEntry> entries;
    std::shared_ptr<void> read_transaction_owner;
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

private:
    std::shared_ptr<MdbxStorageContext> m_context;
    MdbxResourceManifestStorage m_manifests;
    MdbxKnowledgeUnitStore m_units;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
