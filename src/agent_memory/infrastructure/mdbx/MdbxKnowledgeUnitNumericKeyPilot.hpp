#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_KNOWLEDGE_UNIT_NUMERIC_KEY_PILOT_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_KNOWLEDGE_UNIT_NUMERIC_KEY_PILOT_HPP_INCLUDED

/// \file MdbxKnowledgeUnitNumericKeyPilot.hpp
/// \brief Opt-in v1 pilot for numeric physical knowledge-unit keys.

#include <agent_memory/domain/KnowledgeUnit.hpp>

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageContext.hpp"

namespace agent_memory {

/// \brief Configuration for the isolated numeric-key storage pilot.
///
/// The pilot deliberately uses versioned DBI names and never opens or rewrites
/// the legacy `MdbxKnowledgeUnitStore` tables. It is an experiment, not a
/// production-default replacement.
struct MdbxKnowledgeUnitNumericKeyPilotOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::shared_ptr<MdbxStorageContext> context;
};

/// \brief Minimal numeric-key pilot for already numeric KnowledgeUnitId values.
///
/// Primary rows use `KeyValueTable<uint64_t, std::string>` with MDBX's integer
/// comparator. A separate no-reuse ledger is updated in the same write
/// transaction. Payload bytes are opaque to this pilot; the existing v1
/// KnowledgeUnit codec remains owned by `MdbxKnowledgeUnitStore`.
class MdbxKnowledgeUnitNumericKeyPilot final {
  public:
    explicit MdbxKnowledgeUnitNumericKeyPilot(
        MdbxKnowledgeUnitNumericKeyPilotOptions options);
    ~MdbxKnowledgeUnitNumericKeyPilot();

    MdbxKnowledgeUnitNumericKeyPilot(const MdbxKnowledgeUnitNumericKeyPilot&) = delete;
    MdbxKnowledgeUnitNumericKeyPilot& operator=(
        const MdbxKnowledgeUnitNumericKeyPilot&) = delete;

    /// Stores or replaces one payload atomically with its no-reuse ledger row.
    /// ID zero is reserved and rejected.
    void put(KnowledgeUnitId id, std::string_view payload);

    /// Reads one row by numeric physical key.
    [[nodiscard]] std::optional<std::string> find(KnowledgeUnitId id) const;

    /// Removes the current payload while retaining the ID in the no-reuse ledger.
    [[nodiscard]] bool erase(KnowledgeUnitId id);

    /// Returns IDs in inclusive numeric order for a bounded range.
    [[nodiscard]] std::vector<KnowledgeUnitId> scan(
        KnowledgeUnitId first,
        KnowledgeUnitId last,
        std::size_t limit = 1024) const;

  private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
