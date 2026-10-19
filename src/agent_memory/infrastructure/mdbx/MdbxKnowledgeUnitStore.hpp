#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_KNOWLEDGE_UNIT_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_KNOWLEDGE_UNIT_STORE_HPP_INCLUDED

/// \file MdbxKnowledgeUnitStore.hpp
/// \brief MDBX-backed M0 knowledge-unit envelope store.

#include <agent_memory/storage/IKnowledgeUnitStore.hpp>

#include <cstdint>
#include <memory>
#include <string>

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageContext.hpp"

namespace agent_memory {

    /// \brief Configuration for the durable M0 knowledge-unit store.
    struct MdbxKnowledgeUnitStoreOptions final {
        std::string path;
        std::string table_prefix = "agent_memory";
        bool relative_to_exe = false;
    std::int64_t max_dbs = kDefaultMdbxMaxDbs;
    std::shared_ptr<MdbxStorageContext> context;
    std::string publication_token_guard_table_name;
};

    struct MdbxKnowledgeUnitPublicationGuard final {
        std::string table_name;
        std::string key;
        std::string expected_token;
    };

    /// \brief Transactional envelope store with content-key deduplication.
    ///
    /// The first M0 profile persists inline `SourceRefSummary` values only.
    /// `primary_text` is a bounded retrieval seed; canonical body bytes and
    /// locator-backed payloads remain separate capabilities. Exact hydration
    /// and multi-source identity are intentionally deferred in M0.
    class MdbxKnowledgeUnitStore final : public IKnowledgeUnitStore {
    public:
        explicit MdbxKnowledgeUnitStore(MdbxKnowledgeUnitStoreOptions options);
        ~MdbxKnowledgeUnitStore() override;

        MdbxKnowledgeUnitStore(const MdbxKnowledgeUnitStore&) = delete;
        MdbxKnowledgeUnitStore& operator=(const MdbxKnowledgeUnitStore&) = delete;

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

        [[nodiscard]] KnowledgeUnitEnvelope create_or_get_unit_guarded(
            KnowledgeUnitCreateRequest request,
            MdbxKnowledgeUnitPublicationGuard guard
        );

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
