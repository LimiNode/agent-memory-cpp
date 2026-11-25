#include "ConversationDurableCodecInternal.hpp"

#include <agent_memory/domain/Sha256.hpp>

#include <algorithm>
#include <limits>
#include <utility>

namespace agent_memory::detail::conversation_durable {
namespace {

constexpr std::size_t kBindingCap = 65536;
constexpr std::size_t kTextCap = 8U * 1024U * 1024U;

[[noreturn]] void fail(CodecCode code = CodecCode::Corruption) { throw CodecError(code); }

void check_view(ByteView view) {
    if(!view.data && view.size) fail();
}

ByteView bytes(std::string_view value) {
    return {reinterpret_cast<const std::uint8_t*>(value.data()), value.size()};
}

ByteView bytes(const ByteBuffer& value) { return {value.data(), value.size()}; }

std::string_view text_view(ByteView value) {
    return value.size ? std::string_view{reinterpret_cast<const char*>(value.data), value.size} : std::string_view{};
}

bool valid_utf8(std::string_view value) {
    for(std::size_t i = 0; i < value.size();) {
        const auto lead = static_cast<unsigned char>(value[i]);
        std::size_t width = 0;
        if(lead < 0x80U) width = 1;
        else if(lead >= 0xc2U && lead <= 0xdfU) width = 2;
        else if(lead >= 0xe0U && lead <= 0xefU) width = 3;
        else if(lead >= 0xf0U && lead <= 0xf4U) width = 4;
        else return false;
        if(width > value.size() - i) return false;
        for(std::size_t j = 1; j < width; ++j)
            if((static_cast<unsigned char>(value[i + j]) & 0xc0U) != 0x80U) return false;
        if(width == 3 && lead == 0xe0U && static_cast<unsigned char>(value[i + 1]) < 0xa0U) return false;
        if(width == 3 && lead == 0xedU && static_cast<unsigned char>(value[i + 1]) >= 0xa0U) return false;
        if(width == 4 && lead == 0xf0U && static_cast<unsigned char>(value[i + 1]) < 0x90U) return false;
        if(width == 4 && lead == 0xf4U && static_cast<unsigned char>(value[i + 1]) >= 0x90U) return false;
        i += width;
    }
    return true;
}

void check_limit(std::size_t maximum) {
    if(maximum > kMaxPhysicalBytes) fail(CodecCode::InvalidLimits);
}

class Writer final {
public:
    Writer(std::size_t maximum, ByteBuffer* output = nullptr) : m_maximum(maximum), m_output(output) {
        check_limit(maximum);
    }
    void raw(ByteView value) {
        check_view(value);
        if(value.size > m_maximum - m_size) fail(CodecCode::ResourceLimit);
        m_size += value.size;
        if(m_output && value.size) m_output->insert(m_output->end(), value.data, value.data + value.size);
    }
    void u64(std::uint64_t value, std::size_t width = 8) {
        std::array<std::uint8_t, 8> wire{};
        for(std::size_t i = 0; i < width; ++i) wire[width - i - 1] = static_cast<std::uint8_t>(value >> (i * 8));
        raw({wire.data(), width});
    }
    void u8(std::uint8_t value) { u64(value, 1); }
    void u16(std::uint16_t value) { u64(value, 2); }
    void u32(std::uint32_t value) { u64(value, 4); }
    void blob(ByteView value, std::size_t maximum = kMaxPhysicalBytes) {
        if(value.size > maximum || value.size > std::numeric_limits<std::uint32_t>::max())
            fail(CodecCode::ResourceLimit);
        u32(static_cast<std::uint32_t>(value.size));
        raw(value);
    }
    void text(std::string_view value, std::size_t maximum = kMaxPhysicalBytes, bool utf8 = false) {
        if(utf8 && !valid_utf8(value)) fail(CodecCode::InvalidValue);
        blob(bytes(value), maximum);
    }
    void name(std::string_view value) {
        if(!is_valid_conversation_namespace(value)) fail(CodecCode::InvalidValue);
        u16(static_cast<std::uint16_t>(value.size()));
        raw(bytes(value));
    }
    [[nodiscard]] std::size_t size() const noexcept { return m_size; }
private:
    std::size_t m_maximum;
    ByteBuffer* m_output;
    std::size_t m_size = 0;
};

template<typename Function>
ByteBuffer encode(std::size_t maximum, Function function) {
    Writer preflight(maximum);
    function(preflight);
    ByteBuffer result;
    result.reserve(preflight.size());
    Writer writer(maximum, &result);
    function(writer);
    return result;
}

class Reader final {
public:
    explicit Reader(ByteView value, std::size_t maximum, bool own = true) : m_value(value), m_own(own) {
        check_limit(maximum);
        check_view(value);
        if(value.size > maximum) fail(CodecCode::ResourceLimit);
    }
    ByteView raw(std::size_t size) {
        if(size > m_value.size - m_position) fail();
        const auto* data = m_value.data ? m_value.data + m_position : nullptr;
        m_position += size;
        return {data, size};
    }
    std::uint64_t u64(std::size_t width = 8) {
        const auto value = raw(width);
        std::uint64_t result = 0;
        for(std::size_t i = 0; i < width; ++i) result = (result << 8U) | value.data[i];
        return result;
    }
    std::uint8_t u8() { return static_cast<std::uint8_t>(u64(1)); }
    std::uint16_t u16() { return static_cast<std::uint16_t>(u64(2)); }
    std::uint32_t u32() { return static_cast<std::uint32_t>(u64(4)); }
    std::uint64_t nonzero() { const auto result = u64(); if(!result) fail(); return result; }
    bool presence() { const auto result = u8(); if(result > 1) fail(); return result != 0; }
    ByteView blob(std::size_t maximum = kMaxPhysicalBytes) {
        const auto size = u32();
        if(size > maximum) fail(CodecCode::ResourceLimit);
        return raw(size);
    }
    std::string text(std::size_t maximum = kMaxPhysicalBytes, bool utf8 = false) {
        const auto value = blob(maximum);
        if(utf8 && !valid_utf8(text_view(value))) fail();
        return m_own ? std::string{text_view(value)} : std::string{};
    }
    std::string name() {
        const auto value = raw(u16());
        if(!is_valid_conversation_namespace(text_view(value))) fail();
        return m_own ? std::string{text_view(value)} : std::string{};
    }
    ByteBuffer buffer(std::size_t maximum = kMaxPhysicalBytes) {
        const auto value = blob(maximum);
        return m_own && value.size ? ByteBuffer{value.data, value.data + value.size} : ByteBuffer{};
    }
    std::int64_t i64() {
        const auto value = u64();
        if(value <= static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()))
            return static_cast<std::int64_t>(value);
        return -1 - static_cast<std::int64_t>(~value);
    }
    void version() { if(u16() != 1) fail(CodecCode::UnsupportedSchema); }
    void key(std::uint8_t tag) {
        if(u8() != 1 || u8() != tag) fail(CodecCode::UnsupportedSchema);
    }
    void end() { if(m_position != m_value.size) fail(); }
    [[nodiscard]] bool owns() const noexcept { return m_own; }
    [[nodiscard]] std::size_t remaining() const noexcept { return m_value.size - m_position; }
private:
    ByteView m_value;
    bool m_own;
    std::size_t m_position = 0;
};

void put_conversation(Writer& out, const ConversationRef& value) {
    if(!is_valid_conversation_ref(value)) fail(CodecCode::InvalidValue);
    out.name(value.allocation_namespace); out.u64(value.id.value());
}
ConversationRef get_conversation(Reader& in) {
    auto name = in.name();
    const auto id = in.nonzero();
    return {std::move(name), ConversationId{id}};
}
void put_ref(Writer& out, const EntryRef& value) {
    if(!is_valid_entry_ref(value)) fail(CodecCode::InvalidValue);
    put_conversation(out, value.conversation); out.u64(value.entry.value()); out.u64(value.revision.value());
}
EntryRef get_ref(Reader& in) {
    auto conversation = get_conversation(in);
    const auto entry = in.nonzero();
    const auto revision = in.nonzero();
    return {std::move(conversation), EntryId{entry}, EventRevision{revision}};
}
void put_actor(Writer& out, const ActorRef& value) {
    if(!is_valid_actor_ref(value)) fail(CodecCode::InvalidValue);
    out.name(value.allocation_namespace); out.u64(value.id.value());
}
ActorRef get_actor(Reader& in) {
    auto name = in.name();
    return {std::move(name), ActorId{in.nonzero()}};
}

template<typename Value, typename Function>
void put_optional(Writer& out, const std::optional<Value>& value, Function function) {
    out.u8(value ? 1 : 0);
    if(value) function(*value);
}

std::uint8_t source_tag(ConversationSourceIdentity identity) {
    switch(identity) {
    case ConversationSourceIdentity::ExternalEvent: return 1;
    case ConversationSourceIdentity::ObjectRevision: return 2;
    case ConversationSourceIdentity::LocalObservation: return 3;
    }
    fail(CodecCode::InvalidValue);
}
ConversationSourceIdentity source_identity(std::uint8_t tag) {
    switch(tag) {
    case 1: return ConversationSourceIdentity::ExternalEvent;
    case 2: return ConversationSourceIdentity::ObjectRevision;
    case 3: return ConversationSourceIdentity::LocalObservation;
    default: fail(CodecCode::UnsupportedSchema);
    }
}

void put_external(Writer& out, const ExternalObjectRef& value) {
    const auto wire = serialize_external_object_ref_bytes(value);
    out.blob(bytes(wire), kMaxExternalObjectRefBytes);
}
ExternalObjectRef get_external(Reader& in) {
    const auto value = in.blob(kMaxExternalObjectRefBytes);
    if(!in.owns()) return {};
    try { return deserialize_external_object_ref_bytes(value); }
    catch(const std::invalid_argument&) { fail(); }
}
void put_evidence(Writer& out, const ConversationImportEvidence& value) {
    out.text(value.artifact.value(), kBindingCap);
    out.text(value.record_locator, kBindingCap, true);
}
ConversationImportEvidence get_evidence(Reader& in) {
    auto artifact = in.text(kBindingCap);
    auto locator = in.text(kBindingCap, true);
    return {ArtifactBindingId{std::move(artifact)}, std::move(locator)};
}
void put_source(Writer& out, const ConversationSourceBinding& value) {
    out.u8(source_tag(value.identity));
    put_optional(out, value.object, [&out](const ExternalObjectRef& ref) { put_external(out, ref); });
    put_optional(out, value.event, [&out](const ExternalObjectRef& ref) { put_external(out, ref); });
    out.text(value.revision.value(), kBindingCap);
    put_optional(out, value.provider_revision_marker, [&out](const std::string& marker) { out.text(marker, kBindingCap); });
    out.text(value.adapter_recipe, 4096, true);
    put_evidence(out, value.evidence);
}
ConversationSourceBinding get_source(Reader& in) {
    ConversationSourceBinding result;
    result.identity = source_identity(in.u8());
    if(in.presence()) result.object = get_external(in);
    if(in.presence()) result.event = get_external(in);
    result.revision = SourceRevisionId{in.text(kBindingCap)};
    if(in.presence()) result.provider_revision_marker = in.text(kBindingCap);
    result.adapter_recipe = in.text(4096, true);
    result.evidence = get_evidence(in);
    return result;
}

std::uint8_t role_tag(ConversationLlmRole value) {
    switch(value) {
    case ConversationLlmRole::System: return 1;
    case ConversationLlmRole::Developer: return 2;
    case ConversationLlmRole::User: return 3;
    case ConversationLlmRole::Assistant: return 4;
    case ConversationLlmRole::Tool: return 5;
    }
    fail(CodecCode::InvalidValue);
}
ConversationLlmRole get_role(std::uint8_t tag) {
    switch(tag) {
    case 1: return ConversationLlmRole::System;
    case 2: return ConversationLlmRole::Developer;
    case 3: return ConversationLlmRole::User;
    case 4: return ConversationLlmRole::Assistant;
    case 5: return ConversationLlmRole::Tool;
    default: fail(CodecCode::UnsupportedSchema);
    }
}
std::uint8_t origin_tag(ConversationSourceResultOrigin value) {
    switch(value) {
    case ConversationSourceResultOrigin::Reported: return 1;
    case ConversationSourceResultOrigin::PermissionDenied: return 2;
    case ConversationSourceResultOrigin::Recovery: return 3;
    case ConversationSourceResultOrigin::Timeout: return 4;
    case ConversationSourceResultOrigin::Repaired: return 5;
    case ConversationSourceResultOrigin::Unknown: return 6;
    }
    fail(CodecCode::InvalidValue);
}
ConversationSourceResultOrigin get_origin(std::uint8_t tag) {
    switch(tag) {
    case 1: return ConversationSourceResultOrigin::Reported;
    case 2: return ConversationSourceResultOrigin::PermissionDenied;
    case 3: return ConversationSourceResultOrigin::Recovery;
    case 4: return ConversationSourceResultOrigin::Timeout;
    case 5: return ConversationSourceResultOrigin::Repaired;
    case 6: return ConversationSourceResultOrigin::Unknown;
    default: fail(CodecCode::UnsupportedSchema);
    }
}
std::uint8_t outcome_tag(ConversationToolOutcome value) {
    switch(value) {
    case ConversationToolOutcome::Unknown: return 1;
    case ConversationToolOutcome::Succeeded: return 2;
    case ConversationToolOutcome::Failed: return 3;
    case ConversationToolOutcome::Cancelled: return 4;
    }
    fail(CodecCode::InvalidValue);
}
ConversationToolOutcome get_outcome(std::uint8_t tag) {
    switch(tag) {
    case 1: return ConversationToolOutcome::Unknown;
    case 2: return ConversationToolOutcome::Succeeded;
    case 3: return ConversationToolOutcome::Failed;
    case 4: return ConversationToolOutcome::Cancelled;
    default: fail(CodecCode::UnsupportedSchema);
    }
}

void put_target(Writer& out, const ConversationSourceCallTarget& value) {
    if(const auto* ref = std::get_if<EntryRef>(&value)) { out.u8(1); put_ref(out, *ref); }
    else if(const auto* source = std::get_if<ConversationSourceTarget>(&value)) {
        out.u8(2); put_conversation(out, source->conversation); out.text(source->scope.value()); put_source(out, source->source);
    } else if(const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&value)) {
        out.u8(3);
        put_optional(out, unresolved->external, [&out](const ExternalObjectRef& ref) { put_external(out, ref); });
        put_evidence(out, unresolved->evidence);
    } else fail(CodecCode::InvalidValue);
}
ConversationSourceCallTarget get_target(Reader& in) {
    switch(in.u8()) {
    case 1: return get_ref(in);
    case 2: {
        auto conversation = get_conversation(in);
        auto scope = ScopeId{in.text()};
        auto source = get_source(in);
        return ConversationSourceTarget{std::move(conversation), std::move(scope), std::move(source)};
    }
    case 3: {
        UnresolvedConversationTarget value;
        if(in.presence()) value.external = get_external(in);
        value.evidence = get_evidence(in);
        return value;
    }
    default: fail(CodecCode::UnsupportedSchema);
    }
}

void put_witness(Writer& out, const ConversationSourceObservation& value) {
    out.u16(1); put_conversation(out, value.conversation); out.text(value.scope.value()); put_source(out, value.source);
    out.text(value.producer, 1024, true);
    put_optional(out, value.actor, [&out](const ActorRef& actor) { put_actor(out, actor); });
    put_optional(out, value.observer, [&out](const ActorRef& actor) { put_actor(out, actor); });
    put_optional(out, value.llm_role, [&out](ConversationLlmRole role) { out.u8(role_tag(role)); });
    put_optional(out, value.source_unix_us, [&out](std::int64_t stamp) { out.u64(static_cast<std::uint64_t>(stamp)); });
    put_optional(out, value.observed_unix_us, [&out](std::int64_t stamp) { out.u64(static_cast<std::uint64_t>(stamp)); });
    if(const auto* message = std::get_if<ConversationSourceMessage>(&value.payload)) {
        out.u8(1);
        if(message->kind == ConversationSourceMessageKind::Created) out.u8(1);
        else if(message->kind == ConversationSourceMessageKind::Snapshot) out.u8(2);
        else fail(CodecCode::InvalidValue);
        put_optional(out, message->content.text, [&out](const std::string& text) { out.text(text, kTextCap, true); });
        if(message->content.attachments.size() > 4096) fail(CodecCode::ResourceLimit);
        out.u32(static_cast<std::uint32_t>(message->content.attachments.size()));
        for(const auto& attachment : message->content.attachments) out.text(attachment.value(), kBindingCap);
    } else if(const auto* call = std::get_if<ConversationSourceToolCall>(&value.payload)) {
        out.u8(2); out.blob(bytes(call->call_id), kBindingCap); out.text(call->tool_name, 4096, true);
        out.text(call->original_request.value(), kBindingCap);
        put_optional(out, call->effective_request, [&out](const ArtifactBindingId& id) { out.text(id.value(), kBindingCap); });
    } else if(const auto* result = std::get_if<ConversationSourceToolResult>(&value.payload)) {
        out.u8(3); put_target(out, result->call); out.blob(bytes(result->call_id), kBindingCap);
        out.u8(origin_tag(result->origin)); out.u8(outcome_tag(result->reported_outcome));
        put_optional(out, result->result, [&out](const ArtifactBindingId& id) { out.text(id.value(), kBindingCap); });
        put_optional(out, result->is_error, [&out](bool value) { out.u8(value ? 1 : 0); });
        put_optional(out, result->exit_code, [&out](std::int64_t value) { out.u64(static_cast<std::uint64_t>(value)); });
    } else fail(CodecCode::InvalidValue);
}

ConversationSourceObservation get_witness(Reader& in) {
    in.version();
    ConversationSourceObservation value;
    value.conversation = get_conversation(in); value.scope = ScopeId{in.text()}; value.source = get_source(in);
    value.producer = in.text(1024, true);
    if(in.presence()) value.actor = get_actor(in);
    if(in.presence()) value.observer = get_actor(in);
    if(in.presence()) value.llm_role = get_role(in.u8());
    if(in.presence()) value.source_unix_us = in.i64();
    if(in.presence()) value.observed_unix_us = in.i64();
    switch(in.u8()) {
    case 1: {
        ConversationSourceMessage message;
        switch(in.u8()) {
        case 1: message.kind = ConversationSourceMessageKind::Created; break;
        case 2: message.kind = ConversationSourceMessageKind::Snapshot; break;
        default: fail(CodecCode::UnsupportedSchema);
        }
        if(in.presence()) message.content.text = in.text(kTextCap, true);
        const auto count = in.u32();
        if(count > 4096) fail(CodecCode::ResourceLimit);
        if(count > in.remaining() / 4) fail();
        if(in.owns()) message.content.attachments.reserve(count);
        for(std::uint32_t i = 0; i < count; ++i) {
            auto attachment = in.text(kBindingCap);
            if(in.owns()) message.content.attachments.emplace_back(std::move(attachment));
        }
        value.payload = std::move(message); break;
    }
    case 2: {
        ConversationSourceToolCall call;
        call.call_id = in.buffer(kBindingCap); call.tool_name = in.text(4096, true);
        call.original_request = ArtifactBindingId{in.text(kBindingCap)};
        if(in.presence()) call.effective_request = ArtifactBindingId{in.text(kBindingCap)};
        value.payload = std::move(call); break;
    }
    case 3: {
        ConversationSourceToolResult result;
        result.call = get_target(in); result.call_id = in.buffer(kBindingCap);
        result.origin = get_origin(in.u8()); result.reported_outcome = get_outcome(in.u8());
        if(in.presence()) result.result = ArtifactBindingId{in.text(kBindingCap)};
        if(in.presence()) result.is_error = in.presence();
        if(in.presence()) result.exit_code = in.i64();
        value.payload = std::move(result); break;
    }
    default: fail(CodecCode::UnsupportedSchema);
    }
    return value;
}

template<typename Function>
auto decode(ByteView bytes, std::size_t maximum, Function function) {
    Reader preflight(bytes, maximum, false);
    (void)function(preflight); preflight.end();
    Reader reader(bytes, maximum);
    auto result = function(reader); reader.end();
    return result;
}

void check_descriptor(const StorageDescriptor& value) {
    try { (void)conversation_codec_limits_cover(value.codec, value.codec); }
    catch(const ConversationCodecError&) { fail(CodecCode::InvalidLimits); }
    if(value.max_observation_bytes > 32U * 1024U * 1024U || value.max_transaction_bytes > kMaxPhysicalBytes ||
       value.bucket_rows > 256 || value.bucket_bytes > 32U * 1024U * 1024U) fail(CodecCode::InvalidLimits);
}

void check_quotas(const Quotas& quotas) {
    for(const auto value : {quotas.conversations, quotas.entries, quotas.revisions_per_entry,
            quotas.canonical_bytes, quotas.mapping_rows, quotas.mapping_bytes})
        if(value == std::numeric_limits<std::uint64_t>::max()) fail(CodecCode::InvalidValue);
}

void key_id(std::uint64_t value) { if(!value) fail(CodecCode::InvalidValue); }
bool reason_valid(std::string_view value) {
    if(value.empty() || value.size() > 128 || value[0] < 'a' || value[0] > 'z') return false;
    for(const auto c : value)
        if(!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) return false;
    return true;
}

void check_key(TableKind table, ByteView key) {
    check_view(key);
    if(key.size < 2) fail();
    switch(table) {
    case TableKind::Metadata:
        if(key.data[1] == 0) decode_profile_key(key); else (void)decode_conversation_key(key);
        return;
    case TableKind::Events:
        if(key.data[1] == 1) (void)decode_revision_key(key); else (void)decode_generation_key(key);
        return;
    case TableKind::Sources: (void)decode_source_key(key); return;
    }
    fail(CodecCode::InvalidValue);
}

Digest value_digest(const Profile& profile, TableKind table, ByteView key, ByteView body) {
    Sha256Accumulator accumulator;
    const auto raw = [&accumulator](ByteView part) { accumulator.update(text_view(part)); };
    const auto number = [&raw](std::uint64_t value, std::size_t width) {
        std::array<std::uint8_t, 8> wire{};
        for(std::size_t i = 0; i < width; ++i) wire[width - i - 1] = static_cast<std::uint8_t>(value >> (8 * i));
        raw({wire.data(), width});
    };
    const auto blob = [&raw, &number](ByteView part) { number(part.size, 4); raw(part); };
    blob(bytes(kSchemaMarker)); blob(bytes(profile.allocation_namespace));
    raw({profile.incarnation.data(), profile.incarnation.size()});
    number(static_cast<std::uint8_t>(table), 1); blob(key); number(1, 2); blob(body);
    return accumulator.finish();
}

ByteView value_body(ByteView value, std::size_t maximum) {
    Reader reader(value, maximum); reader.version();
    const auto body = reader.blob(); (void)reader.raw(32); reader.end();
    return body;
}

} // namespace

CodecError::CodecError(CodecCode code) : std::runtime_error("Conversation durable physical codec refusal"), m_code(code) {}

ByteBuffer encode_profile_key() { return {1, 0}; }
ByteBuffer encode_conversation_key(ConversationId id) {
    key_id(id.value()); return encode(10, [id](Writer& out) { out.u8(1); out.u8(1); out.u64(id.value()); });
}
ByteBuffer encode_revision_key(const RevisionKey& key) {
    key_id(key.conversation.value()); key_id(key.entry.value()); key_id(key.revision.value());
    return encode(26, [&key](Writer& out) {
        out.u8(1); out.u8(1); out.u64(key.conversation.value()); out.u64(key.entry.value()); out.u64(key.revision.value());
    });
}
ByteBuffer encode_generation_key(std::uint64_t generation) {
    key_id(generation); return encode(10, [generation](Writer& out) { out.u8(1); out.u8(2); out.u64(generation); });
}
ByteBuffer encode_source_key(const SourceKey& key) {
    key_id(key.conversation.value());
    return encode(46, [&key](Writer& out) {
        out.u8(1); out.u8(1); out.u64(key.conversation.value()); out.raw({key.digest.data(), key.digest.size()}); out.u32(key.slot);
    });
}
void decode_profile_key(ByteView value) { Reader in(value, 2); in.key(0); in.end(); }
ConversationId decode_conversation_key(ByteView value) {
    Reader in(value, 10); in.key(1); const auto id = in.nonzero(); in.end(); return ConversationId{id};
}
RevisionKey decode_revision_key(ByteView value) {
    Reader in(value, 26); in.key(1);
    RevisionKey key{ConversationId{in.nonzero()}, EntryId{in.nonzero()}, EventRevision{in.nonzero()}}; in.end(); return key;
}
std::uint64_t decode_generation_key(ByteView value) {
    Reader in(value, 10); in.key(2); const auto generation = in.nonzero(); in.end(); return generation;
}
SourceKey decode_source_key(ByteView value) {
    Reader in(value, 46); in.key(1); SourceKey key; key.conversation = ConversationId{in.nonzero()};
    const auto digest = in.raw(32); std::copy_n(digest.data, 32, key.digest.begin());
    key.slot = in.u32(); in.end(); return key;
}

ByteBuffer encode_descriptor(const StorageDescriptor& value) {
    check_descriptor(value);
    return encode(122, [&value](Writer& out) {
        out.u16(1);
        const auto& c = value.codec; const auto& f = c.fields;
        for(const auto field : {static_cast<std::uint64_t>(c.max_frame_bytes), static_cast<std::uint64_t>(c.max_owned_bytes),
                static_cast<std::uint64_t>(f.max_text_bytes), static_cast<std::uint64_t>(f.max_attachments),
                static_cast<std::uint64_t>(f.max_links), static_cast<std::uint64_t>(f.max_binding_bytes),
                static_cast<std::uint64_t>(f.max_locator_bytes), static_cast<std::uint64_t>(f.max_recipe_bytes),
                static_cast<std::uint64_t>(c.max_extensions), static_cast<std::uint64_t>(c.max_extension_bytes),
                static_cast<std::uint64_t>(c.max_objects), value.max_observation_bytes, value.max_transaction_bytes,
                value.bucket_rows, value.bucket_bytes}) out.u64(field);
    });
}
StorageDescriptor decode_descriptor(ByteView value) {
    Reader in(value, 122); in.version(); StorageDescriptor descriptor;
    const auto size = [&in]() {
        const auto value = in.u64();
        if(value > std::numeric_limits<std::size_t>::max()) fail(CodecCode::InvalidLimits);
        return static_cast<std::size_t>(value);
    };
    auto& c = descriptor.codec; auto& f = c.fields;
    c.max_frame_bytes = size(); c.max_owned_bytes = size(); f.max_text_bytes = size();
    f.max_attachments = size(); f.max_links = size(); f.max_binding_bytes = size(); f.max_locator_bytes = size();
    f.max_recipe_bytes = size(); c.max_extensions = size(); c.max_extension_bytes = size(); c.max_objects = size();
    descriptor.max_observation_bytes = in.u64(); descriptor.max_transaction_bytes = in.u64();
    descriptor.bucket_rows = in.u64(); descriptor.bucket_bytes = in.u64(); in.end(); check_descriptor(descriptor);
    return descriptor;
}

ByteBuffer encode_profile(const Profile& value) {
    if(!is_valid_conversation_namespace(value.allocation_namespace) || !value.quota_policy_version)
        fail(CodecCode::InvalidValue);
    check_quotas(value.quotas);
    const auto descriptor = encode_descriptor(value.descriptor);
    return encode(kMaxProfileBytes, [&value, &descriptor](Writer& out) {
        out.text(kSchemaMarker); out.text(value.allocation_namespace, kMaxConversationNamespaceBytes);
        out.raw({value.incarnation.data(), value.incarnation.size()}); out.blob(bytes(descriptor));
        for(const auto field : {value.generation, value.conversation_count, value.entry_count, value.revision_count,
                value.mapping_count, value.canonical_charge, value.mapping_charge, value.quota_policy_version,
                value.quotas.conversations, value.quotas.entries, value.quotas.revisions_per_entry,
                value.quotas.canonical_bytes, value.quotas.mapping_rows, value.quotas.mapping_bytes}) out.u64(field);
    });
}
Profile decode_profile(ByteView value) {
    Reader in(value, kMaxProfileBytes);
    const auto marker = in.blob(128); if(text_view(marker) != kSchemaMarker) fail(CodecCode::UnsupportedSchema);
    const auto name = in.blob(kMaxConversationNamespaceBytes);
    if(!is_valid_conversation_namespace(text_view(name))) fail();
    const auto incarnation = in.raw(16); const auto descriptor = in.blob(122);
    Profile profile;
    profile.descriptor = decode_descriptor(descriptor);
    profile.generation = in.u64(); profile.conversation_count = in.u64(); profile.entry_count = in.u64();
    profile.revision_count = in.u64(); profile.mapping_count = in.u64(); profile.canonical_charge = in.u64();
    profile.mapping_charge = in.u64(); profile.quota_policy_version = in.nonzero();
    profile.quotas = {in.u64(), in.u64(), in.u64(), in.u64(), in.u64(), in.u64()}; in.end();
    check_quotas(profile.quotas);
    if(profile.conversation_count > profile.quotas.conversations || profile.entry_count > profile.quotas.entries ||
       profile.mapping_count > profile.quotas.mapping_rows || profile.canonical_charge > profile.quotas.canonical_bytes ||
       profile.mapping_charge > profile.quotas.mapping_bytes || profile.revision_count < profile.entry_count) fail();
    profile.allocation_namespace = text_view(name);
    std::copy_n(incarnation.data, 16, profile.incarnation.begin()); return profile;
}

ByteBuffer encode_conversation_row(const ConversationRow& value, std::size_t maximum) {
    if(value.scope.empty() || !value.created_generation || !value.next_entry) fail(CodecCode::InvalidValue);
    return encode(maximum, [&value](Writer& out) {
        out.text(value.scope.value()); out.u64(value.created_generation); out.u64(value.next_entry);
    });
}
ConversationRow decode_conversation_row(ByteView value, std::size_t maximum) {
    return decode(value, maximum, [](Reader& in) {
        auto scope = in.text(); const auto generation = in.nonzero(); const auto next = in.nonzero();
        if(in.owns() && scope.empty()) fail();
        return ConversationRow{ScopeId{std::move(scope)}, generation, next};
    });
}
ByteBuffer encode_revision_row(const RevisionRow& value, std::size_t maximum) {
    if(!value.generation || (value.receipt && (value.receipt->previous_revision.empty() ||
            !reason_valid(value.receipt->reason_code)))) fail(CodecCode::InvalidValue);
    return encode(maximum, [&value](Writer& out) {
        out.u64(value.generation);
        put_optional(out, value.receipt, [&out](const CorrectionReceipt& receipt) {
            out.u64(receipt.previous_revision.value()); out.text(receipt.reason_code, 128);
        }); out.blob(bytes(value.frame), kMaxConversationEntryBytes);
    });
}
RevisionRow decode_revision_row(ByteView value, std::size_t maximum) {
    return decode(value, maximum, [](Reader& in) {
        RevisionRow row; row.generation = in.nonzero();
        if(in.presence()) {
            const auto revision = EventRevision{in.nonzero()}; auto reason = in.text(128);
            if(in.owns() && !reason_valid(reason)) fail();
            row.receipt = CorrectionReceipt{revision, std::move(reason)};
        } row.frame = in.buffer(kMaxConversationEntryBytes); return row;
    });
}
ByteBuffer encode_source_row(const SourceRow& value, std::size_t maximum) {
    if(!value.generation || value.ref.revision.value() != 1) fail(CodecCode::InvalidValue);
    return encode(maximum, [&value](Writer& out) {
        out.blob(bytes(value.key)); out.blob(bytes(value.witness)); put_ref(out, value.ref); out.u64(value.generation);
    });
}
SourceRow decode_source_row(ByteView value, std::size_t maximum) {
    return decode(value, maximum, [](Reader& in) {
        SourceRow row; row.key = in.buffer(); row.witness = in.buffer(); row.ref = get_ref(in); row.generation = in.nonzero();
        if(row.ref.revision.value() != 1) fail();
        return row;
    });
}
ByteBuffer encode_generation_row(const GenerationRow& value) {
    return encode(kMaxGenerationBytes, [&value](Writer& out) {
        switch(value.kind) {
        case GenerationKind::Create: out.u8(1); if(value.entry) fail(CodecCode::InvalidValue); break;
        case GenerationKind::Import: out.u8(2); if(!value.entry) fail(CodecCode::InvalidValue); break;
        case GenerationKind::Correction: out.u8(3); if(!value.entry) fail(CodecCode::InvalidValue); break;
        default: fail(CodecCode::InvalidValue);
        }
        put_conversation(out, value.conversation);
        put_optional(out, value.entry, [&out](const std::pair<EntryId, EventRevision>& ref) {
            key_id(ref.first.value()); key_id(ref.second.value()); out.u64(ref.first.value()); out.u64(ref.second.value());
        });
    });
}
GenerationRow decode_generation_row(ByteView value) {
    return decode(value, kMaxGenerationBytes, [](Reader& in) {
        GenerationRow row;
        switch(in.u8()) {
        case 1: row.kind = GenerationKind::Create; break;
        case 2: row.kind = GenerationKind::Import; break;
        case 3: row.kind = GenerationKind::Correction; break;
        default: fail(CodecCode::UnsupportedSchema);
        }
        row.conversation = get_conversation(in);
        if(in.presence()) {
            const auto entry = EntryId{in.nonzero()}; const auto revision = EventRevision{in.nonzero()};
            row.entry = std::make_pair(entry, revision);
        }
        if((row.kind == GenerationKind::Create) == row.entry.has_value()) fail();
        return row;
    });
}

ByteBuffer wrap_value(const Profile& profile, TableKind table, ByteView key, ByteView body, std::size_t maximum) {
    check_view(body); check_key(table, key);
    if(!is_valid_conversation_namespace(profile.allocation_namespace)) fail(CodecCode::InvalidValue);
    // Preflight the complete framing before hashing or allocating the output.
    const Digest placeholder{};
    Writer preflight(maximum); preflight.u16(1); preflight.blob(body); preflight.raw({placeholder.data(), placeholder.size()});
    const auto digest = value_digest(profile, table, key, body);
    return encode(maximum, [body, &digest](Writer& out) {
        out.u16(1); out.blob(body); out.raw({digest.data(), digest.size()});
    });
}
ByteView unwrap_value(const Profile& profile, TableKind table, ByteView key, ByteView value, std::size_t maximum) {
    check_key(table, key);
    const auto body = value_body(value, maximum);
    const auto expected = value_digest(profile, table, key, body);
    if(!std::equal(expected.begin(), expected.end(), value.data + value.size - expected.size())) fail();
    return body;
}
Profile decode_profile_value(ByteView value) {
    const auto body = value_body(value, kMaxProfileValueBytes);
    auto profile = decode_profile(body); const auto key = encode_profile_key();
    (void)unwrap_value(profile, TableKind::Metadata, bytes(key), value, kMaxProfileValueBytes);
    return profile;
}

ByteBuffer encode_witness(const ConversationSourceObservation& value, std::size_t maximum) {
    return encode(maximum, [&value](Writer& out) { put_witness(out, value); });
}
ConversationSourceObservation decode_witness(ByteView value, std::size_t maximum) {
    return decode(value, maximum, [](Reader& in) { return get_witness(in); });
}
ByteBuffer encode_logical_source_key(const ConversationSourceBinding& source, std::size_t maximum) {
    return encode(maximum, [&source](Writer& out) {
        out.u16(1); out.u8(source_tag(source.identity));
        switch(source.identity) {
        case ConversationSourceIdentity::ExternalEvent:
            if(!source.event) fail(CodecCode::InvalidValue);
            put_external(out, *source.event); out.text(source.revision.value(), kBindingCap); break;
        case ConversationSourceIdentity::ObjectRevision:
            if(!source.object || !source.provider_revision_marker) fail(CodecCode::InvalidValue);
            put_external(out, *source.object); out.text(*source.provider_revision_marker, kBindingCap);
            out.text(source.revision.value(), kBindingCap); break;
        case ConversationSourceIdentity::LocalObservation:
            out.text(source.revision.value(), kBindingCap); out.text(source.evidence.artifact.value(), kBindingCap);
            out.text(source.evidence.record_locator, kBindingCap, true); break;
        }
    });
}
void validate_logical_source_key(ByteView value, std::size_t maximum) {
    Reader in(value, maximum); in.version(); const auto identity = source_identity(in.u8());
    if(identity != ConversationSourceIdentity::LocalObservation) (void)get_external(in);
    if(identity == ConversationSourceIdentity::ObjectRevision) (void)in.text(kBindingCap);
    (void)in.text(kBindingCap);
    if(identity == ConversationSourceIdentity::LocalObservation) {
        (void)in.text(kBindingCap); (void)in.text(kBindingCap, true);
    }
    in.end();
}
Digest source_digest(ByteView value) {
    check_view(value); Sha256Accumulator accumulator; accumulator.update(text_view(value)); return accumulator.finish();
}

} // namespace agent_memory::detail::conversation_durable
