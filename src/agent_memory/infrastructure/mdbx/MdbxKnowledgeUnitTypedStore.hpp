#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_KNOWLEDGE_UNIT_TYPED_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_KNOWLEDGE_UNIT_TYPED_STORE_HPP_INCLUDED

/// \file MdbxKnowledgeUnitTypedStore.hpp
/// \brief Opt-in typed numeric MDBX storage for KnowledgeUnitEnvelope values.

#include <agent_memory/storage/IKnowledgeUnitStore.hpp>

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageContext.hpp"

namespace agent_memory {

/// \brief Configuration for the isolated typed numeric KnowledgeUnit store.
///
/// The adapter owns a versioned, opt-in DBI family. It never opens or rewrites
/// the legacy `MdbxKnowledgeUnitStore` tables.
struct MdbxKnowledgeUnitTypedStoreOptions final {
    std::string path;
    std::string table_prefix = "agent_memory";
    bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::shared_ptr<MdbxStorageContext> context;
};

/// \brief Transactional typed KnowledgeUnit storage over native numeric keys.
///
/// Payloads use the existing versioned `MdbxKnowledgeUnitStore` codec. The
/// numeric primary table, no-reuse ledger, content-key index, and by-kind
/// index are updated in one writable transaction for every mutation.
class MdbxKnowledgeUnitTypedStore final : public IKnowledgeUnitStore {
  public:
    explicit MdbxKnowledgeUnitTypedStore(MdbxKnowledgeUnitTypedStoreOptions options);
    ~MdbxKnowledgeUnitTypedStore() override;

    MdbxKnowledgeUnitTypedStore(const MdbxKnowledgeUnitTypedStore&) = delete;
    MdbxKnowledgeUnitTypedStore& operator=(const MdbxKnowledgeUnitTypedStore&) = delete;

    void upsert(KnowledgeUnitEnvelope envelope) override;
    [[nodiscard]] bool erase(const KnowledgeUnitId& id) override;

    [[nodiscard]] std::vector<KnowledgeUnitId> scan_by_scope_and_kind(
        const ScopeId& scope_id,
        KnowledgeUnitKind kind
    ) const override;

    [[nodiscard]] std::vector<KnowledgeUnitId> scan_by_scope(
        const ScopeId& scope_id
    ) const override;

    [[nodiscard]] KnowledgeUnitEnvelope create_or_get_unit(
        KnowledgeUnitCreateRequest request
    ) override;

    [[nodiscard]] std::optional<KnowledgeUnitEnvelope> find(
        const KnowledgeUnitId& id
    ) const override;

    [[nodiscard]] std::optional<KnowledgeUnitId> find_by_content_key(
        const KnowledgeUnitKey& key
    ) const override;

    [[nodiscard]] KnowledgeUnitEnvelope update_mutable_fields(
        const KnowledgeUnitId& id,
        KnowledgeUnitMutablePatch patch
    ) override;

  private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

#endif
