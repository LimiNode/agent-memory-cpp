#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_CONVERSATION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_CONVERSATION_HPP_INCLUDED

/// \file Conversation.hpp
/// \brief Canonical conversation values and field validation, not a store or event codec.
/// \note Contract owner: guides/conversation-event-contract.md, section 4.1.

#include "Artifact.hpp"
#include "ExternalObjectRef.hpp"
#include "KnowledgeUnit.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <type_traits>
#include <variant>
#include <vector>

namespace agent_memory {

/// \brief Local conversation occurrence; meaningful only in its allocation namespace.
/// \note Only an explicit uint64_t is accepted. Zero is unassigned; no allocator or arithmetic is supplied.
class ConversationId final {
public:
    constexpr ConversationId() noexcept = default;
    explicit constexpr ConversationId(std::uint64_t value) noexcept : m_value(value) {}
    template<typename T, std::enable_if_t<!std::is_same_v<T, std::uint64_t>, int> = 0>
    ConversationId(T) = delete;
    [[nodiscard]] constexpr std::uint64_t value() const noexcept { return m_value; }
    [[nodiscard]] constexpr bool empty() const noexcept { return m_value == 0; }
    friend constexpr bool operator==(ConversationId a, ConversationId b) noexcept { return a.m_value == b.m_value; }
    friend constexpr bool operator!=(ConversationId a, ConversationId b) noexcept { return !(a == b); }
    friend constexpr bool operator<(ConversationId a, ConversationId b) noexcept { return a.m_value < b.m_value; }
private:
    std::uint64_t m_value = 0;
};

/// \brief Entry occurrence within one qualified Conversation, not a message's mutable state.
class EntryId final {
public:
    constexpr EntryId() noexcept = default;
    explicit constexpr EntryId(std::uint64_t value) noexcept : m_value(value) {}
    template<typename T, std::enable_if_t<!std::is_same_v<T, std::uint64_t>, int> = 0>
    EntryId(T) = delete;
    [[nodiscard]] constexpr std::uint64_t value() const noexcept { return m_value; }
    [[nodiscard]] constexpr bool empty() const noexcept { return m_value == 0; }
    friend constexpr bool operator==(EntryId a, EntryId b) noexcept { return a.m_value == b.m_value; }
    friend constexpr bool operator!=(EntryId a, EntryId b) noexcept { return !(a == b); }
    friend constexpr bool operator<(EntryId a, EntryId b) noexcept { return a.m_value < b.m_value; }
private:
    std::uint64_t m_value = 0;
};

/// \brief Participant identity in an allocation namespace, independent of role and observer.
class ActorId final {
public:
    constexpr ActorId() noexcept = default;
    explicit constexpr ActorId(std::uint64_t value) noexcept : m_value(value) {}
    template<typename T, std::enable_if_t<!std::is_same_v<T, std::uint64_t>, int> = 0>
    ActorId(T) = delete;
    [[nodiscard]] constexpr std::uint64_t value() const noexcept { return m_value; }
    [[nodiscard]] constexpr bool empty() const noexcept { return m_value == 0; }
    friend constexpr bool operator==(ActorId a, ActorId b) noexcept { return a.m_value == b.m_value; }
    friend constexpr bool operator!=(ActorId a, ActorId b) noexcept { return !(a == b); }
    friend constexpr bool operator<(ActorId a, ActorId b) noexcept { return a.m_value < b.m_value; }
private:
    std::uint64_t m_value = 0;
};

/// \brief Exact representation revision of the same Entry, not a new source event.
class EventRevision final {
public:
    constexpr EventRevision() noexcept = default;
    explicit constexpr EventRevision(std::uint64_t value) noexcept : m_value(value) {}
    template<typename T, std::enable_if_t<!std::is_same_v<T, std::uint64_t>, int> = 0>
    EventRevision(T) = delete;
    [[nodiscard]] constexpr std::uint64_t value() const noexcept { return m_value; }
    [[nodiscard]] constexpr bool empty() const noexcept { return m_value == 0; }
    friend constexpr bool operator==(EventRevision a, EventRevision b) noexcept { return a.m_value == b.m_value; }
    friend constexpr bool operator!=(EventRevision a, EventRevision b) noexcept { return !(a == b); }
    friend constexpr bool operator<(EventRevision a, EventRevision b) noexcept { return a.m_value < b.m_value; }
private:
    std::uint64_t m_value = 0;
};

inline constexpr std::size_t kMaxConversationNamespaceBytes = 128;
inline constexpr std::size_t kMaxConversationTextBytes = 1024U * 1024U;
inline constexpr std::size_t kMaxConversationAttachments = 256;
inline constexpr std::size_t kMaxConversationLinks = 256;
inline constexpr std::size_t kMaxConversationLocatorBytes = 4096;
inline constexpr std::size_t kMaxConversationBindingBytes = 4096;
inline constexpr std::size_t kMaxConversationRecipeBytes = 1024;

/// \brief Explicit field budgets; defaults preserve the integrated Phase 1 validator.
/// \note Zero forbids the corresponding resource, never unlimited. Namespace and AMXR
/// identity rules are fixed. Codec implementation ceilings are checked by the codec.
struct ConversationFieldLimits final {
    std::size_t max_text_bytes = kMaxConversationTextBytes;
    std::size_t max_attachments = kMaxConversationAttachments;
    std::size_t max_links = kMaxConversationLinks;
    std::size_t max_binding_bytes = kMaxConversationBindingBytes;
    std::size_t max_locator_bytes = kMaxConversationLocatorBytes;
    std::size_t max_recipe_bytes = kMaxConversationRecipeBytes;
};

struct ConversationRef final {
    /// Host-issued exact ASCII token: [a-z][a-z0-9._-]*, 1..128 bytes. Never normalized.
    std::string allocation_namespace;
    ConversationId id;
};

struct EntryRef final {
    ConversationRef conversation;
    EntryId entry;
    EventRevision revision;
};

struct ActorRef final {
    std::string allocation_namespace;
    ActorId id;
};

[[nodiscard]] bool operator==(const ConversationRef&, const ConversationRef&) noexcept;
[[nodiscard]] bool operator!=(const ConversationRef&, const ConversationRef&) noexcept;
[[nodiscard]] bool operator<(const ConversationRef&, const ConversationRef&) noexcept;
[[nodiscard]] bool operator==(const EntryRef&, const EntryRef&) noexcept;
[[nodiscard]] bool operator!=(const EntryRef&, const EntryRef&) noexcept;
[[nodiscard]] bool operator<(const EntryRef&, const EntryRef&) noexcept;
[[nodiscard]] bool operator==(const ActorRef&, const ActorRef&) noexcept;
[[nodiscard]] bool operator!=(const ActorRef&, const ActorRef&) noexcept;
[[nodiscard]] bool operator<(const ActorRef&, const ActorRef&) noexcept;
[[nodiscard]] bool is_valid_conversation_namespace(std::string_view value) noexcept;
[[nodiscard]] bool is_valid_conversation_ref(const ConversationRef&) noexcept;
[[nodiscard]] bool is_valid_entry_ref(const EntryRef&) noexcept;
[[nodiscard]] bool is_valid_actor_ref(const ActorRef&) noexcept;

/// \brief Exact retained import/observation evidence, not necessarily a full provider archive.
struct ConversationImportEvidence final {
    ArtifactBindingId artifact;
    /// Adapter-declared exact record locator, non-empty strict UTF-8. No interchange grammar is assigned here.
    std::string record_locator;
};

/// \brief Declared source identity strength; never inferred from text, time or a message ID.
enum class ConversationSourceIdentity {
    ExternalEvent,
    ObjectRevision,
    LocalObservation
};

struct ConversationSourceBinding final {
    ConversationSourceIdentity identity = ConversationSourceIdentity::LocalObservation;
    /// External message/object identity, distinct from a change occurrence.
    std::optional<ExternalObjectRef> object;
    /// Present only for ExternalEvent: a provider-declared individual event key.
    std::optional<ExternalObjectRef> event;
    /// Exact retained source snapshot/evidence revision, required in every mode.
    /// This is not an assertion that the provider issued a change/revision token.
    SourceRevisionId revision;
    /// Required only for ObjectRevision: adapter-declared reliable upstream marker, exact opaque bytes.
    /// Never inferred from a timestamp/content digest or replaced by the local EventRevision.
    std::optional<std::string> provider_revision_marker;
    std::string adapter_recipe;
    /// Exact record evidence. LocalObservation replay is limited to this context, not global event dedupe.
    ConversationImportEvidence evidence;
};

struct UnresolvedConversationTarget final {
    std::optional<ExternalObjectRef> external;
    ConversationImportEvidence evidence;
};

using ConversationLinkTarget = std::variant<EntryRef, UnresolvedConversationTarget>;

/// \brief Reply/Thread stay in the same Conversation; Branch/Forward/Quote may cross it.
/// \note Cross-conversation references never grant target access. These are not KnowledgeUnit Relations.
/// All resolved links reject the containing Entry occurrence, regardless of its revision.
enum class ConversationLinkKind { Reply, Thread, Branch, Forward, Quote };

struct ConversationLink final {
    ConversationLinkKind kind = ConversationLinkKind::Reply;
    ConversationLinkTarget target;
};

enum class ConversationLlmRole { System, Developer, User, Assistant, Tool };

struct ConversationContent final {
    /// Empty or absent text is valid with at least one valid attachment; whitespace is retained verbatim.
    std::optional<std::string> text;
    std::vector<ArtifactBindingId> attachments;
};

struct ConversationMessageCreated final { ConversationContent content; };
/// Snapshot observation asserts no unknown creation/edit history.
struct ConversationSnapshotObserved final { ConversationContent content; };
struct ConversationEdited final { EntryRef affected; ConversationContent replacement; };
struct ConversationTombstoned final { EntryRef affected; };
struct ConversationRestored final { EntryRef affected; EntryRef retained_state; };
using ConversationPayload = std::variant<ConversationMessageCreated, ConversationSnapshotObserved,
    ConversationEdited, ConversationTombstoned, ConversationRestored>;

/// \brief One observed canonical occurrence. Mutable construction is not admission.
struct ConversationEntry final {
    EntryRef ref;
    ScopeId scope;
    std::optional<ConversationSourceBinding> source;
    std::optional<ActorRef> actor;
    std::optional<ConversationLlmRole> llm_role;
    /// Observer is provenance, not participant identity, source namespace or an authorization token.
    std::optional<ActorRef> observer;
    /// Exactly represented signed UTC Unix microseconds; unknown remains absent. Not an ordering key.
    std::optional<std::int64_t> source_unix_us;
    std::optional<std::int64_t> observed_unix_us;
    ConversationPayload payload;
    std::vector<ConversationLink> links;
};

enum class ConversationValidationCode {
    InvalidReference,
    MissingScope,
    InvalidEnum,
    InvalidUtf8,
    LimitExceeded,
    EmptyContent,
    MissingBinding,
    InvalidTarget,
    InvalidSourceIdentity,
    InvalidEvidence
};

struct ConversationValidationIssue final {
    /// Structural field path only; diagnostics do not echo source text or external identifiers.
    std::string field;
    ConversationValidationCode code = ConversationValidationCode::InvalidReference;
};

/// \brief Validates bounded fields and structural target policy, without I/O or implicit normalization.
/// \return Empty vector on field-valid input. Allocation errors propagate.
/// \note Does not prove existence, authority, scope access, event/revision authenticity, committed
/// allocation, message-chain membership, chronology, deduplication, publication or current freshness.
/// Edit/delete/restore targets must be exact, same-conversation and a different Entry occurrence;
/// restore retained_state obeys the same policy. No latest resolution or current-state fold occurs.
[[nodiscard]] std::vector<ConversationValidationIssue> validate_conversation_entry(const ConversationEntry&);
/// \brief Applies explicit field budgets without weakening semantic or identity checks.
/// \note The one-argument validator retains its original limits and behavior.
[[nodiscard]] std::vector<ConversationValidationIssue> validate_conversation_entry_with_limits(
    const ConversationEntry&, const ConversationFieldLimits&);

} // namespace agent_memory

#endif
