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
enum class WriteStep {
    BeforeTransaction, BeforeWrites, AfterRevision, AfterSource, AfterGeneration,
    AfterConversation, AfterProfile, BeforeCommit, BeforeSchemaCommit
};
struct QualificationHooks final {
    bool force_digest_collision = false;
    void (*before_commit_step)(WriteStep) = nullptr;
    // Runs only after the wrapper has returned successfully from commit, before
    // the operation returns. Not a native-commit-internal injection point. It
    // cannot throw or promise rollback; the process harness may terminate here.
    void (*after_commit)() noexcept = nullptr;
};

class MdbxConversationDurable final : public IConversationImportSink, public IConversationHistory {
public:
    explicit MdbxConversationDurable(DurableOptions, QualificationHooks = {});
    ~MdbxConversationDurable() override;
    MdbxConversationDurable(const MdbxConversationDurable&) = delete;
    MdbxConversationDurable& operator=(const MdbxConversationDurable&) = delete;
    [[nodiscard]] ConversationWriteResult create_conversation(
        const ConversationStoreAccess&, const ConversationRef&);
    [[nodiscard]] ConversationWriteResult import_observation(
        const ConversationStoreAccess&, const ConversationSourceObservation&);
    // Representation CAS, not a new source occurrence. Original mappings and
    // their admission receipts remain immutable; stale CAS precedes NoChange.
    [[nodiscard]] ConversationWriteResult correct(const ConversationStoreAccess&,
        const ConversationEnvelope&, EventRevision expected_revision, const std::string& reason_code);
    [[nodiscard]] ConversationReadResult read_exact(
        const ConversationStoreAccess&, const EntryRef&, const ConversationReadLimits& = {}) const;
    // Owning instance-bound continuation; each call opens a fresh transaction.
    [[nodiscard]] ConversationReadResult read_history(
        const ConversationStoreAccess&, const ConversationRef&, const ConversationReadLimits& = {},
        const std::optional<ConversationContinuation>& continuation = std::nullopt) const;
    [[nodiscard]] ConversationReadResult read_current(
        const ConversationStoreAccess&, const ConversationRef&, const ConversationReadLimits& = {},
        const std::optional<ConversationContinuation>& continuation = std::nullopt) const override;
    [[nodiscard]] const IConversationHistory& history() const noexcept override { return *this; }
    // Qualification-only explicit historical selector, not a portable public API.
    // Frontier is nonzero and not before creation or beyond the current commit.
    // The (entry, revision) pair is exclusive; only (0, 0) starts at the beginning.
    [[nodiscard]] ConversationReadResult read_history_at(const ConversationStoreAccess&,
        const ConversationRef&, const ConversationReadLimits&, std::uint64_t frontier,
        std::uint64_t seek_entry = 0, std::uint64_t seek_revision = 0) const;
    [[nodiscard]] ConversationReadResult read_current_at(const ConversationStoreAccess&,
        const ConversationRef&, const ConversationReadLimits&, std::uint64_t frontier,
        std::uint64_t seek_root = 0) const;
private:
    struct Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory::detail::conversation_durable
#endif
#endif
