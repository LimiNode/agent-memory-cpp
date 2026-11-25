#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CONVERSATION_DURABLE_INTERNAL_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_CONVERSATION_DURABLE_INTERNAL_HPP_INCLUDED

// Internal qualification vertical, not an installed IConversationStore implementation.
// Owner: guides/conversation-durable-physical-contract.md.
#include "ConversationDurableCodecInternal.hpp"
#include "MdbxStorageContext.hpp"

#if AGENT_MEMORY_HAS_MDBX
namespace agent_memory::detail::conversation_durable {

struct MapPolicy final {
    std::uint64_t upper_bytes;
    std::uint64_t growth_bytes;
};

struct DurableOptions final {
    DurableOptions(std::shared_ptr<MdbxStorageContext> context, std::string allocation_namespace,
        Quotas quotas, MapPolicy map_policy);
    std::shared_ptr<MdbxStorageContext> context;
    std::string allocation_namespace;
    Quotas quotas;
    MapPolicy map_policy;
    StorageDescriptor descriptor;
    std::string table_prefix = "agent_memory";
    bool allow_initialize = false;
    // Host-declared COMPLETE environment peak, excluding this three-DBI profile.
    // Include every companion/host table and all simultaneous migration reserves.
    // Actual unaccounted occupied tables cause refusal, never consume the reserve.
    std::uint64_t other_profile_peak_dbis = 0;
    std::uint64_t max_live_bytes = 128U * 1024U * 1024U;
    std::uint64_t max_read_raw_bytes = 32U * 1024U * 1024U;
    std::uint64_t max_read_rows = 262144;
};

// Test-only hooks are passed explicitly, never persisted. They may throw before commit.
enum class WriteStep { BeforeWrites, AfterRevision, AfterSource, AfterGeneration, BeforeCommit };
struct QualificationHooks final {
    bool force_digest_collision = false;
    void (*before_commit_step)(WriteStep) = nullptr;
};

class MdbxConversationDurable final {
public:
    explicit MdbxConversationDurable(DurableOptions, QualificationHooks = {});
    ~MdbxConversationDurable();
    MdbxConversationDurable(const MdbxConversationDurable&) = delete;
    MdbxConversationDurable& operator=(const MdbxConversationDurable&) = delete;
    [[nodiscard]] ConversationWriteResult create_conversation(
        const ConversationStoreAccess&, const ConversationRef&);
    [[nodiscard]] ConversationWriteResult import_observation(
        const ConversationStoreAccess&, const ConversationSourceObservation&);
    [[nodiscard]] ConversationReadResult read_exact(
        const ConversationStoreAccess&, const EntryRef&, const ConversationReadLimits& = {}) const;
    // Qualification-only bounded page selector; not a portable public continuation.
    // Zero frontier selects this transaction's generation. seek_entry is exclusive.
    [[nodiscard]] ConversationReadResult read_history(
        const ConversationStoreAccess&, const ConversationRef&, const ConversationReadLimits& = {},
        std::uint64_t seek_entry = 0, std::uint64_t frontier = 0) const;
private:
    struct Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory::detail::conversation_durable
#endif
#endif
