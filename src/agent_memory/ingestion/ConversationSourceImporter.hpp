#pragma once
#ifndef AGENT_MEMORY_HEADER_INGESTION_CONVERSATION_SOURCE_IMPORTER_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INGESTION_CONVERSATION_SOURCE_IMPORTER_HPP_INCLUDED

/// \file ConversationSourceImporter.hpp
/// \brief Bounded source-shaped observations and instance-local atomic import admission.
/// \note Owner: guides/conversation-source-import.md. No provider SDK, execution or persistence.

#include <agent_memory/storage/ConversationStore.hpp>

namespace agent_memory {

enum class ConversationSourceMessageKind { Created, Snapshot };

struct ConversationSourceMessage final {
    ConversationSourceMessageKind kind = ConversationSourceMessageKind::Snapshot;
    ConversationContent content;
};

/// \brief Original request and separately retained effective arguments; neither implies dispatch.
struct ConversationSourceToolCall final {
    ByteBuffer call_id;
    std::string tool_name;
    ArtifactBindingId original_request;
    std::optional<ArtifactBindingId> effective_request;
};

/// \brief An exact source-key lookup, never a call-token or timestamp search.
/// \note Missing keys retain source.evidence as unresolved; denied/cross-conversation
/// targets refuse admission. Resolution is frozen at first successful admission.
struct ConversationSourceTarget final {
    ConversationRef conversation;
    ScopeId scope;
    ConversationSourceBinding source;
};

using ConversationSourceCallTarget = std::variant<EntryRef, ConversationSourceTarget, UnresolvedConversationTarget>;

/// \brief Source-origin assertion, not an execution lifecycle or proof of effects.
enum class ConversationSourceResultOrigin { Reported, PermissionDenied, Recovery, Timeout, Repaired, Unknown };

struct ConversationSourceToolResult final {
    ConversationSourceCallTarget call;
    ByteBuffer call_id;
    ConversationSourceResultOrigin origin = ConversationSourceResultOrigin::Unknown;
    /// Used only for Reported origin; synthetic/ambiguous origins normalize to Unknown.
    ConversationToolOutcome reported_outcome = ConversationToolOutcome::Unknown;
    std::optional<ArtifactBindingId> result;
    /// Retained classification inputs; neither flag establishes execution or outcome.
    std::optional<bool> is_error;
    std::optional<std::int64_t> exit_code;
};

using ConversationSourcePayload = std::variant<ConversationSourceMessage,
    ConversationSourceToolCall, ConversationSourceToolResult, ConversationEdited,
    ConversationTombstoned, ConversationRestored>;

/// \brief Owning source record DTO, borrowed only during import_observation().
/// \note Host/adapter supplies exact retained source evidence and reviewed identity recipe.
/// producer is an evidence-qualified origin descriptor, not actor/observer or a credential.
/// The evidence must retain original classification/producer/effective-argument facts;
/// the narrow AMCE payload does not encode a full runtime lifecycle.
struct ConversationSourceObservation final {
    ConversationRef conversation;
    ScopeId scope;
    ConversationSourceBinding source;
    std::string producer;
    std::optional<ActorRef> actor;
    std::optional<ActorRef> observer;
    std::optional<ConversationLlmRole> llm_role;
    std::optional<std::int64_t> source_unix_us;
    std::optional<std::int64_t> observed_unix_us;
    ConversationSourcePayload payload;
};

struct ConversationImportOptions final {
    ConversationStoreOptions store;
    std::size_t max_observation_bytes = 2U * 1024U * 1024U;
    std::size_t max_mappings = 4096;
    std::size_t max_mapping_bytes = 64U * 1024U * 1024U;
};

/// \brief Atomic admission seam: implementations coordinate key, allocator and canonical append.
/// \note One observation is the transaction unit; there is no batch/archive atomicity.
/// Replay returns its original exact revision. Recipe/content/declared-target changes
/// under the same occurrence key conflict, never allocate a competing Entry.
class IConversationImportSink {
public:
    virtual ~IConversationImportSink();
    [[nodiscard]] virtual ConversationWriteResult create_conversation(
        const ConversationStoreAccess&, const ConversationRef&) = 0;
    [[nodiscard]] virtual ConversationWriteResult import_observation(
        const ConversationStoreAccess&, const ConversationSourceObservation&) = 0;
    /// \brief Explicit CAS representation correction; occurrence keys and replay witnesses stay fixed.
    [[nodiscard]] virtual ConversationWriteResult correct(
        const ConversationStoreAccess&, const ConversationEnvelope&, EventRevision expected_revision,
        const std::string& reason_code) = 0;
    /// \brief Borrowed read-only history. Lifetime is bounded by the sink; no mutable-store escape.
    [[nodiscard]] virtual const IConversationHistory& history() const noexcept = 0;
};

/// \brief Deterministic reference adapter/coordinator over a privately owned reference store.
/// \throws std::invalid_argument For unsupported limits/options. Allocation failures propagate.
/// \thread_safety Multiple producers may share one instance; writes serialize under one lock.
/// \note Dedupe is instance-local and selected-source-revision-qualified. No global source
/// equivalence, artifact hydration/authentication, crash/reopen or exactly-once tool execution.
/// Caps: 32 MiB observation, 65536 mappings, 256 MiB logical mapping bytes, not heap/RSS limits.
class InMemoryConversationImportSink final : public IConversationImportSink {
public:
    explicit InMemoryConversationImportSink(ConversationImportOptions options);
    ~InMemoryConversationImportSink() override;
    InMemoryConversationImportSink(const InMemoryConversationImportSink&) = delete;
    InMemoryConversationImportSink& operator=(const InMemoryConversationImportSink&) = delete;
    [[nodiscard]] ConversationWriteResult create_conversation(
        const ConversationStoreAccess&, const ConversationRef&) override;
    [[nodiscard]] ConversationWriteResult import_observation(
        const ConversationStoreAccess&, const ConversationSourceObservation&) override;
    [[nodiscard]] ConversationWriteResult correct(
        const ConversationStoreAccess&, const ConversationEnvelope&, EventRevision,
        const std::string&) override;
    [[nodiscard]] const IConversationHistory& history() const noexcept override;
private:
    struct Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory
#endif
