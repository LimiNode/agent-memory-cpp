#pragma once
#ifndef AGENT_MEMORY_HEADER_INGESTION_CONVERSATION_SOURCE_IMPORTER_INTERNAL_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INGESTION_CONVERSATION_SOURCE_IMPORTER_INTERNAL_HPP_INCLUDED

// Private shared admission/normalization rules. Owner: guides/conversation-source-import.md.
#include "ConversationSourceImporter.hpp"

namespace agent_memory::detail {

inline constexpr std::size_t kConversationImportObservationByteCap = 32U * 1024U * 1024U;

struct SourceKey final {
    ConversationSourceIdentity identity = ConversationSourceIdentity::LocalObservation;
    ByteBuffer external;
    SourceRevisionId revision;
    std::optional<std::string> marker;
    ArtifactBindingId artifact;
    std::string locator;

    [[nodiscard]] bool operator<(const SourceKey&) const;
    [[nodiscard]] std::size_t charge() const;
};

struct ObservationPreflightResult final {
    ConversationWriteStatus status = ConversationWriteStatus::Applied;
    std::size_t bytes = 0;
};

// Requires a source accepted by source_status(); contains no conversation-local identity.
[[nodiscard]] SourceKey source_key(const ConversationSourceBinding&);
[[nodiscard]] ConversationWriteStatus source_status(const ConversationSourceBinding&,
    const ConversationRef&, const ScopeId&, const ConversationFieldLimits&);
// Field/count and observation budgets precede producer UTF-8/nonempty admission.
[[nodiscard]] ObservationPreflightResult preflight_observation(const ConversationSourceObservation&,
    const ConversationFieldLimits&, std::size_t max_observation_bytes);
// Validates message classification without changing the source-shaped witness.
[[nodiscard]] ConversationWriteStatus observation_status(const ConversationSourceObservation&);
[[nodiscard]] ConversationWriteStatus target_status(const ConversationSourceObservation&,
    const ConversationFieldLimits&);
[[nodiscard]] bool same_witness(const ConversationSourceObservation&, const ConversationSourceObservation&);
[[nodiscard]] ConversationSourceObservation copy_observation(const ConversationSourceObservation&);
[[nodiscard]] UnresolvedConversationTarget unresolved_source_target(const ConversationSourceTarget&);
// The optional target is already resolved in the caller's transaction. Move it into this call.
// Without it, source-shaped targets remain unresolved; no lookup, hydration or callback occurs.
[[nodiscard]] ConversationEnvelope normalize_observation(const ConversationSourceObservation&, const EntryRef&,
    std::optional<ConversationLinkTarget> resolved_target = std::nullopt);

} // namespace agent_memory::detail
#endif
