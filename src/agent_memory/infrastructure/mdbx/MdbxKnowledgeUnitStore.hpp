#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_KNOWLEDGE_UNIT_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_KNOWLEDGE_UNIT_STORE_HPP_INCLUDED

/// \file MdbxKnowledgeUnitStore.hpp
/// \brief MDBX-backed M0 knowledge-unit envelope store.

#include <agent_memory/storage/IKnowledgeUnitStore.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <limits>
#include <string>
#include <vector>

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageContext.hpp"

namespace mdbxc {
class Transaction;
}

namespace agent_memory {

    /// Maximum number of knowledge-unit requests in one durable batch.
    inline constexpr std::size_t kMdbxKnowledgeUnitMaxBatchSize = 128;

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
        /// ResourceId key of the token table and of the guarded source.
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

        /// Creates or replays a bounded batch in one writable MDBX transaction.
        ///
        /// The batch is atomic: validation, content-key deduplication, id
        /// allocation, envelope writes, and by-kind index updates all commit
        /// together or none of them become visible. Empty and oversized
        /// batches are rejected.
        [[nodiscard]] std::vector<KnowledgeUnitEnvelope> create_or_get_units(
            std::vector<KnowledgeUnitCreateRequest> requests
        );

        /// Creates or replays a bounded batch while checking one durable token
        /// in the same writable transaction as every envelope and index write.
        /// Every request must contain exactly one source whose ResourceId equals
        /// `guard.key`; a stale or cross-resource token rejects the whole batch
        /// without partial publication.
        [[nodiscard]] std::vector<KnowledgeUnitEnvelope> create_or_get_units_guarded(
            std::vector<KnowledgeUnitCreateRequest> requests,
            MdbxKnowledgeUnitPublicationGuard guard
        );

        [[nodiscard]] std::optional<KnowledgeUnitEnvelope> find(
            const KnowledgeUnitId& id
        ) const override;

        /// \brief Reads one envelope from a caller-owned read transaction.
        ///
        /// This overload is intentionally narrow: it lets a frontier reader
        /// combine unit and resource reads without emulating a snapshot with
        /// sequential adapter-owned transactions.
        [[nodiscard]] std::optional<KnowledgeUnitEnvelope> find_in_transaction(
            const KnowledgeUnitId& id,
            const mdbxc::Transaction& transaction,
            std::size_t max_payload_bytes = std::numeric_limits<std::size_t>::max(),
            std::size_t* observed_payload_bytes = nullptr
        ) const;

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
