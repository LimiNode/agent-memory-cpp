#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_CONVERSATION_DURABLE_CODEC_INTERNAL_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_CONVERSATION_DURABLE_CODEC_INTERNAL_HPP_INCLUDED

// Private physical grammar. Owner: guides/conversation-durable-physical-contract.md.
#include <agent_memory/ingestion/ConversationSourceImporter.hpp>

#include <array>
#include <stdexcept>

namespace agent_memory::detail::conversation_durable {

inline constexpr const char* kSchemaMarker = "conversation_durable.v1.be.keys.sha256.values.witness1";
inline constexpr std::size_t kMaxPhysicalBytes = 256U * 1024U * 1024U;
using Digest = std::array<std::uint8_t, 32>;
using Incarnation = std::array<std::uint8_t, 16>;

enum class CodecCode { Corruption, UnsupportedSchema, ResourceLimit, InvalidValue, InvalidLimits };
class CodecError final : public std::runtime_error {
public:
    explicit CodecError(CodecCode code);
    [[nodiscard]] CodecCode code() const noexcept { return m_code; }
private:
    CodecCode m_code;
};

enum class TableKind : std::uint8_t { Metadata = 1, Events = 2, Sources = 3 };
enum class GenerationKind : std::uint8_t { Create = 1, Import = 2, Correction = 3 };

struct StorageDescriptor final {
    ConversationCodecLimits codec;
    std::uint64_t max_observation_bytes = 2U * 1024U * 1024U;
    std::uint64_t max_transaction_bytes = 32U * 1024U * 1024U;
    std::uint64_t bucket_rows = 16;
    std::uint64_t bucket_bytes = 8U * 1024U * 1024U;
};

// All six quotas are explicit creation inputs; zero forbids new consumption.
struct Quotas final {
    std::uint64_t conversations;
    std::uint64_t entries;
    std::uint64_t revisions_per_entry;
    std::uint64_t canonical_bytes;
    std::uint64_t mapping_rows;
    std::uint64_t mapping_bytes;
};

struct Profile final {
    std::string allocation_namespace;
    Incarnation incarnation{};
    StorageDescriptor descriptor;
    std::uint64_t generation = 0;
    std::uint64_t conversation_count = 0;
    std::uint64_t entry_count = 0;
    std::uint64_t revision_count = 0;
    std::uint64_t mapping_count = 0;
    std::uint64_t canonical_charge = 0;
    std::uint64_t mapping_charge = 0;
    std::uint64_t quota_policy_version = 1;
    Quotas quotas;
};
struct ConversationRow final {
    ScopeId scope;
    std::uint64_t created_generation = 0;
    std::uint64_t next_entry = 1;
};
struct CorrectionReceipt final {
    EventRevision previous_revision;
    std::string reason_code;
};
struct RevisionRow final {
    std::uint64_t generation = 0;
    std::optional<CorrectionReceipt> receipt;
    ByteBuffer frame;
};
struct SourceRow final {
    ByteBuffer key;
    ByteBuffer witness;
    EntryRef ref;
    std::uint64_t generation = 0;
};
struct GenerationRow final {
    GenerationKind kind = GenerationKind::Create;
    ConversationRef conversation;
    std::optional<std::pair<EntryId, EventRevision>> entry;
};
struct RevisionKey final { ConversationId conversation; EntryId entry; EventRevision revision; };
struct SourceKey final { ConversationId conversation; Digest digest{}; std::uint32_t slot = 0; };

[[nodiscard]] ByteBuffer encode_profile_key();
[[nodiscard]] ByteBuffer encode_conversation_key(ConversationId);
[[nodiscard]] ByteBuffer encode_revision_key(const RevisionKey&);
[[nodiscard]] ByteBuffer encode_generation_key(std::uint64_t);
[[nodiscard]] ByteBuffer encode_source_key(const SourceKey&);
void decode_profile_key(ByteView);
[[nodiscard]] ConversationId decode_conversation_key(ByteView);
[[nodiscard]] RevisionKey decode_revision_key(ByteView);
[[nodiscard]] std::uint64_t decode_generation_key(ByteView);
[[nodiscard]] SourceKey decode_source_key(ByteView);

[[nodiscard]] ByteBuffer encode_descriptor(const StorageDescriptor&);
[[nodiscard]] StorageDescriptor decode_descriptor(ByteView);
[[nodiscard]] ByteBuffer encode_profile(const Profile&);
[[nodiscard]] Profile decode_profile(ByteView);
[[nodiscard]] ByteBuffer encode_conversation_row(const ConversationRow&, std::size_t max_bytes = kMaxPhysicalBytes);
[[nodiscard]] ConversationRow decode_conversation_row(ByteView, std::size_t max_bytes = kMaxPhysicalBytes);
[[nodiscard]] ByteBuffer encode_revision_row(const RevisionRow&, std::size_t max_bytes = kMaxPhysicalBytes);
[[nodiscard]] RevisionRow decode_revision_row(ByteView, std::size_t max_bytes = kMaxPhysicalBytes);
[[nodiscard]] ByteBuffer encode_source_row(const SourceRow&, std::size_t max_bytes = kMaxPhysicalBytes);
[[nodiscard]] SourceRow decode_source_row(ByteView, std::size_t max_bytes = kMaxPhysicalBytes);
[[nodiscard]] ByteBuffer encode_generation_row(const GenerationRow&);
[[nodiscard]] GenerationRow decode_generation_row(ByteView);

// Integrity is checked before returning a view. The returned body borrows input.
[[nodiscard]] ByteBuffer wrap_value(const Profile&, TableKind, ByteView key, ByteView body,
    std::size_t max_bytes = kMaxPhysicalBytes);
[[nodiscard]] ByteView unwrap_value(const Profile&, TableKind, ByteView key, ByteView value,
    std::size_t max_bytes = kMaxPhysicalBytes);
// Bootstrap only: bounded profile decoding followed by its complete integrity check.
[[nodiscard]] Profile decode_profile_value(ByteView value);

[[nodiscard]] ByteBuffer encode_witness(const ConversationSourceObservation&,
    std::size_t max_bytes = 32U * 1024U * 1024U);
[[nodiscard]] ConversationSourceObservation decode_witness(ByteView,
    std::size_t max_bytes = 32U * 1024U * 1024U);
[[nodiscard]] ByteBuffer encode_logical_source_key(const ConversationSourceBinding&,
    std::size_t max_bytes = 32U * 1024U * 1024U);
// Validates exact logical framing and nested AMXR without reinterpreting identity roles.
void validate_logical_source_key(ByteView, std::size_t max_bytes = 32U * 1024U * 1024U);
[[nodiscard]] Digest source_digest(ByteView);

} // namespace agent_memory::detail::conversation_durable
#endif
