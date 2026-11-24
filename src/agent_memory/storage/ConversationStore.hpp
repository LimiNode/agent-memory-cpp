#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_CONVERSATION_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_CONVERSATION_STORE_HPP_INCLUDED

/// \file ConversationStore.hpp
/// \brief Backend-neutral history and bounded in-memory reference implementation.
/// \note Contract owner: guides/conversation-reference-store.md. No persistence or importer.

#include <agent_memory/domain/conversation_codec.hpp>

#include <memory>
#include <optional>

namespace agent_memory {

/// \brief Trusted host input, not authentication or an agent-controlled grant.
/// \note The host reauthorizes each operation. policy_binding covers principal,
/// permissions and policy revision; it is an exact namespace-token-shaped identifier.
struct ConversationStoreAccess final {
    ScopeId scope;
    std::string policy_binding;
};

struct ConversationStoreOptions final {
    std::string allocation_namespace;
    ConversationCodecLimits codec_limits;
    std::size_t max_conversations = 1024;
    std::size_t max_entries = 4096;
    std::size_t max_revisions_per_entry = 64;
    std::size_t max_storage_bytes = 64U * 1024U * 1024U;
};

/// \brief Finite work/output budgets. Zero forbids work; never means unlimited.
/// \note Caps: 65536 items, 262144 visits, 256 MiB per byte budget. Canonical-frame
/// charging is conservative logical byte accounting, not a heap/RSS guarantee.
struct ConversationReadLimits final {
    std::size_t max_items = 64;
    std::size_t max_output_bytes = 1024U * 1024U;
    std::size_t max_visited_records = 4096;
    std::size_t max_decoded_bytes = 32U * 1024U * 1024U;
};

enum class ConversationWriteStatus {
    Applied, NoChange, Conflict, NotFound, Denied, InvalidRequest, Unsupported, ResourceLimit
};

struct ConversationWriteResult final {
    ConversationWriteStatus status = ConversationWriteStatus::InvalidRequest;
    std::optional<EntryRef> ref;
    std::uint64_t commit_generation = 0;
};

/// \brief Why a local representation changed; source evidence/recipes stay in envelopes.
struct ConversationCorrectionReceipt final {
    EventRevision previous_revision;
    std::string reason_code;
};

struct ConversationStoredRevision final {
    ConversationEnvelope envelope;
    std::optional<ConversationCorrectionReceipt> correction;
    std::uint64_t commit_generation = 0;
};

enum class ConversationMessageState { Live, Deleted, Ambiguous };

/// \brief A fold result, not independent evidence or proof of source completeness.
struct ConversationMessageView final {
    EntryRef origin;
    /// Unassigned for Ambiguous; no branch is silently selected as current.
    EntryRef state;
    /// Exact content source, including the retained revision selected by restore.
    std::optional<EntryRef> content_ref;
    ConversationMetadata origin_metadata;
    std::optional<ConversationContent> content;
    ConversationMessageState status = ConversationMessageState::Live;
    bool is_snapshot_origin = false;
};

class IConversationStore;

/// \brief Opaque instance-bound snapshot/seek handle; not a grant or persisted format.
/// \note Copies retain the frontier. Store lifetime history is retained; a token
/// from another/recreated instance is invalid even when namespace/IDs coincide.
class ConversationContinuation final {
public:
    /// \brief Store-owned type-erased state; each backend validates its own state and instance.
    struct BackendState {
        virtual ~BackendState();
    };
    ConversationContinuation() = default;
    [[nodiscard]] bool empty() const noexcept { return !m_state; }
private:
    std::shared_ptr<const BackendState> m_state;
    friend class IConversationStore;
};

enum class ConversationReadStatus {
    Complete, Partial, OversizedItem, BudgetExhausted, NotFound, Denied,
    InvalidRequest, InvalidContinuation
};

/// \brief Owning results; refusal contains no records or hidden-scope counts.
struct ConversationReadResult final {
    ConversationReadStatus status = ConversationReadStatus::InvalidRequest;
    std::vector<ConversationStoredRevision> records;
    std::vector<ConversationMessageView> messages;
    std::optional<ConversationContinuation> continuation;
    std::uint64_t commit_generation = 0;
    std::size_t visited_records = 0;
    std::size_t decoded_bytes = 0;
    std::size_t output_bytes = 0;
};

/// \brief Canonical event history, separate from source importing and physical storage.
/// \note Callers supply exact allocation-qualified IDs. Append requires the next
/// EntryId/revision one; correction cannot change occurrence bindings/targets.
class IConversationStore {
public:
    virtual ~IConversationStore();
    [[nodiscard]] virtual ConversationWriteResult create_conversation(
        const ConversationStoreAccess&, const ConversationRef&) = 0;
    [[nodiscard]] virtual ConversationWriteResult append(
        const ConversationStoreAccess&, const ConversationEnvelope&) = 0;
    /// \brief CAS precedes no-op; next revision and bounded reason code are required.
    [[nodiscard]] virtual ConversationWriteResult correct(
        const ConversationStoreAccess&, const ConversationEnvelope&,
        EventRevision expected_revision, const std::string& reason_code) = 0;
    [[nodiscard]] virtual ConversationReadResult read_exact(
        const ConversationStoreAccess&, const EntryRef&, const ConversationReadLimits& = {}) const = 0;
    /// \brief Lists all revisions in EntryId/revision order at a pinned commit generation.
    /// \param continuation Omit to start; an explicitly supplied empty token is invalid.
    [[nodiscard]] virtual ConversationReadResult read_history(
        const ConversationStoreAccess&, const ConversationRef&, const ConversationReadLimits& = {},
        const std::optional<ConversationContinuation>& continuation = std::nullopt) const = 0;
    /// \brief Completes a bounded input fold before returning any current-message output.
    [[nodiscard]] virtual ConversationReadResult read_current(
        const ConversationStoreAccess&, const ConversationRef&, const ConversationReadLimits& = {},
        const std::optional<ConversationContinuation>& continuation = std::nullopt) const = 0;
protected:
    /// \brief Backend extension seam; neither state nor a continuation grants access.
    [[nodiscard]] static ConversationContinuation make_continuation(
        std::shared_ptr<const ConversationContinuation::BackendState> state);
    [[nodiscard]] static const ConversationContinuation::BackendState* continuation_state(
        const ConversationContinuation& continuation) noexcept;
};

/// \brief Mutex-serialized reference backend with immutable canonical AMCE history.
/// \throws std::invalid_argument For invalid options. Allocation failures propagate.
/// \thread_safety All operations on an instance are serialized; no host callback runs under its lock.
/// \note Storage caps: 65536 entries total, 1024 revisions/entry, 256 MiB frames/receipts/scope bytes.
/// No caller authentication, artifact hydration, persistent allocator or crash/reopen promise.
class InMemoryConversationStore final : public IConversationStore {
public:
    explicit InMemoryConversationStore(ConversationStoreOptions options);
    ~InMemoryConversationStore() override;
    InMemoryConversationStore(const InMemoryConversationStore&) = delete;
    InMemoryConversationStore& operator=(const InMemoryConversationStore&) = delete;
    [[nodiscard]] ConversationWriteResult create_conversation(
        const ConversationStoreAccess&, const ConversationRef&) override;
    [[nodiscard]] ConversationWriteResult append(
        const ConversationStoreAccess&, const ConversationEnvelope&) override;
    [[nodiscard]] ConversationWriteResult correct(
        const ConversationStoreAccess&, const ConversationEnvelope&, EventRevision,
        const std::string& reason_code) override;
    [[nodiscard]] ConversationReadResult read_exact(
        const ConversationStoreAccess&, const EntryRef&, const ConversationReadLimits& = {}) const override;
    [[nodiscard]] ConversationReadResult read_history(
        const ConversationStoreAccess&, const ConversationRef&, const ConversationReadLimits& = {},
        const std::optional<ConversationContinuation>& = std::nullopt) const override;
    [[nodiscard]] ConversationReadResult read_current(
        const ConversationStoreAccess&, const ConversationRef&, const ConversationReadLimits& = {},
        const std::optional<ConversationContinuation>& = std::nullopt) const override;
private:
    struct Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory
#endif
