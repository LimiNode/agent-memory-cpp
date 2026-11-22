#include "conversation_codec.hpp"

#include <limits>
#include <utility>

namespace agent_memory {
namespace {

[[noreturn]] void reject(ConversationCodecCode code = ConversationCodecCode::Malformed) {
    throw ConversationCodecError(code);
}

void check_limits(const ConversationCodecLimits& limits) {
    const auto& f = limits.fields;
    if(limits.max_frame_bytes > kMaxConversationEntryBytes ||
       limits.max_owned_bytes > kMaxConversationEntryBytes || limits.max_objects > 32768 ||
       limits.max_extensions > 1024 || limits.max_extension_bytes > 8U * 1024U * 1024U ||
       f.max_text_bytes > 8U * 1024U * 1024U || f.max_attachments > 4096 || f.max_links > 4096 ||
       f.max_binding_bytes > 65536 || f.max_locator_bytes > 65536 || f.max_recipe_bytes > 4096)
        reject(ConversationCodecCode::InvalidLimits);
}

class Budget final {
public:
    explicit Budget(const ConversationCodecLimits& limits) : m_limits(limits) {}
    void charge(std::size_t bytes, std::size_t objects) {
        if(bytes > m_limits.max_owned_bytes - m_bytes || objects > m_limits.max_objects - m_objects)
            reject(ConversationCodecCode::ResourceLimit);
        m_bytes += bytes;
        m_objects += objects;
    }
private:
    const ConversationCodecLimits& m_limits;
    std::size_t m_bytes = 0;
    std::size_t m_objects = 0;
};

struct ExternalFrames final {
    std::vector<std::string> frames;
    std::size_t position = 0;
};

ByteView view(std::string_view text) {
    return {reinterpret_cast<const std::uint8_t*>(text.data()), text.size()};
}

std::string_view text_view(ByteView bytes) {
    return bytes.size == 0 ? std::string_view{} :
        std::string_view{reinterpret_cast<const char*>(bytes.data), bytes.size};
}

// The same local writer counts first, then writes to one exactly reserved buffer.
class Writer final {
public:
    Writer(const ConversationCodecLimits& limits, Budget& budget, ExternalFrames& external,
        ByteBuffer* output = nullptr) : fields(limits.fields), m_limits(limits), m_budget(budget),
        m_external(external), m_output(output) {}
    const ConversationFieldLimits& fields;
    std::size_t size() const noexcept { return m_size; }
    void integer(std::uint64_t value, unsigned width) {
        add(width);
        if(m_output) {
            for(unsigned i = width; i > 0; --i)
                m_output->push_back(static_cast<std::uint8_t>((value >> ((i - 1) * 8)) & 0xffU));
        }
    }
    void raw(ByteView bytes) {
        add(bytes.size);
        if(m_output && bytes.size != 0) m_output->insert(m_output->end(), bytes.data, bytes.data + bytes.size);
    }
    void raw(std::string_view bytes) { raw(view(bytes)); }
    void blob(ByteView bytes, std::size_t maximum) {
        if(bytes.size > maximum) reject(ConversationCodecCode::ResourceLimit);
        m_budget.charge(bytes.size, 1);
        integer(bytes.size, 4);
        raw(bytes);
    }
    void blob(std::string_view bytes, std::size_t maximum) { blob(view(bytes), maximum); }
    void count(std::size_t count, std::size_t maximum) {
        if(count > maximum) reject(ConversationCodecCode::ResourceLimit);
        m_budget.charge(0, count);
        integer(count, 2);
    }
    void opaque(ByteView bytes) { m_budget.charge(bytes.size, 0); raw(bytes); }
    void external(const ExternalObjectRef& ref) {
        if(m_output) {
            blob(m_external.frames.at(m_external.position++), kMaxExternalObjectRefBytes);
        } else {
            auto bytes = serialize_external_object_ref(ref);
            // Charge before retaining preparation; rejected events cannot accumulate an oversized cache.
            blob(bytes, kMaxExternalObjectRefBytes);
            m_external.frames.push_back(std::move(bytes));
        }
    }
    void presence(bool present) { integer(present ? 1 : 0, 1); }
private:
    void add(std::size_t count) {
        if(count > m_limits.max_frame_bytes - m_size) reject(ConversationCodecCode::ResourceLimit);
        m_size += count;
    }
    const ConversationCodecLimits& m_limits;
    Budget& m_budget;
    ExternalFrames& m_external;
    ByteBuffer* m_output;
    std::size_t m_size = 0;
};

class Reader final {
public:
    Reader(ByteView bytes, const ConversationFieldLimits& field_limits, Budget& budget) :
        fields(field_limits), m_bytes(bytes), m_budget(budget) {}
    const ConversationFieldLimits& fields;
    std::size_t remaining() const noexcept { return m_bytes.size - m_position; }
    ByteView take(std::size_t count) {
        if(count > remaining()) reject();
        const ByteView result{m_bytes.data ? m_bytes.data + m_position : nullptr, count};
        m_position += count;
        return result;
    }
    std::uint64_t integer(unsigned width) {
        std::uint64_t result = 0;
        const auto bytes = take(width);
        for(std::size_t i = 0; i < bytes.size; ++i) result = (result << 8) | bytes.data[i];
        return result;
    }
    void tag(std::uint64_t expected) { if(integer(1) != expected) reject(); }
    bool presence() {
        const auto value = integer(1);
        if(value > 1) reject();
        return value == 1;
    }
    ByteView blob_view(std::size_t maximum, bool is_fixed_contract = false) {
        const auto count = integer(4);
        if(count > remaining()) reject();
        if(count > maximum) reject(is_fixed_contract ? ConversationCodecCode::Malformed : ConversationCodecCode::ResourceLimit);
        m_budget.charge(static_cast<std::size_t>(count), 1);
        return take(static_cast<std::size_t>(count));
    }
    std::string blob(std::size_t maximum, bool is_fixed_contract = false) {
        return std::string(text_view(blob_view(maximum, is_fixed_contract)));
    }
    ByteBuffer binary(std::size_t maximum) {
        const auto bytes = blob_view(maximum);
        return bytes.size == 0 ? ByteBuffer{} : ByteBuffer(bytes.data, bytes.data + bytes.size);
    }
    ByteBuffer opaque() {
        m_budget.charge(remaining(), 0);
        const auto bytes = take(remaining());
        return bytes.size == 0 ? ByteBuffer{} : ByteBuffer(bytes.data, bytes.data + bytes.size);
    }
    std::size_t count(std::size_t maximum, std::size_t minimum_element_bytes) {
        const auto value = integer(2);
        if(value > remaining() / minimum_element_bytes) reject();
        if(value > maximum) reject(ConversationCodecCode::ResourceLimit);
        m_budget.charge(0, static_cast<std::size_t>(value));
        return static_cast<std::size_t>(value);
    }
    std::int64_t signed_integer() {
        const auto bits = integer(8);
        const auto maximum = static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max());
        return bits <= maximum ? static_cast<std::int64_t>(bits) : -static_cast<std::int64_t>(~bits) - 1;
    }
private:
    ByteView m_bytes;
    Budget& m_budget;
    std::size_t m_position = 0;
};

void write_ref(Writer& out, const EntryRef& ref) {
    out.blob(ref.conversation.allocation_namespace, kMaxConversationNamespaceBytes);
    out.integer(ref.conversation.id.value(), 8);
    out.integer(ref.entry.value(), 8);
    out.integer(ref.revision.value(), 8);
}

EntryRef read_ref(Reader& in) {
    auto allocation_namespace = in.blob(kMaxConversationNamespaceBytes, true);
    const auto conversation = in.integer(8);
    const auto entry = in.integer(8);
    const auto revision = in.integer(8);
    return {{std::move(allocation_namespace), ConversationId{conversation}}, EntryId{entry}, EventRevision{revision}};
}

void write_actor(Writer& out, const std::optional<ActorRef>& actor) {
    out.presence(actor.has_value());
    if(actor) {
        out.blob(actor->allocation_namespace, kMaxConversationNamespaceBytes);
        out.integer(actor->id.value(), 8);
    }
}

std::optional<ActorRef> read_actor(Reader& in) {
    if(!in.presence()) return std::nullopt;
    auto allocation_namespace = in.blob(kMaxConversationNamespaceBytes, true);
    const auto id = in.integer(8);
    return ActorRef{std::move(allocation_namespace), ActorId{id}};
}

void write_external(Writer& out, const std::optional<ExternalObjectRef>& ref) {
    out.presence(ref.has_value());
    if(ref) out.external(*ref);
}

std::optional<ExternalObjectRef> read_external(Reader& in) {
    if(!in.presence()) return std::nullopt;
    try {
        return deserialize_external_object_ref(text_view(in.blob_view(kMaxExternalObjectRefBytes, true)));
    } catch(const ConversationCodecError&) { throw; }
    catch(const std::invalid_argument&) { reject(); }
}

void write_evidence(Writer& out, const ConversationImportEvidence& evidence) {
    out.blob(evidence.artifact.value(), out.fields.max_binding_bytes);
    out.blob(evidence.record_locator, out.fields.max_locator_bytes);
}

ConversationImportEvidence read_evidence(Reader& in) {
    auto artifact = in.blob(in.fields.max_binding_bytes);
    auto locator = in.blob(in.fields.max_locator_bytes);
    return {ArtifactBindingId{std::move(artifact)}, std::move(locator)};
}

void write_source(Writer& out, const std::optional<ConversationSourceBinding>& source) {
    out.presence(source.has_value());
    if(!source) return;
    switch(source->identity) {
    case ConversationSourceIdentity::ExternalEvent: out.integer(1, 1); break;
    case ConversationSourceIdentity::ObjectRevision: out.integer(2, 1); break;
    case ConversationSourceIdentity::LocalObservation: out.integer(3, 1); break;
    default: reject();
    }
    write_external(out, source->object);
    write_external(out, source->event);
    out.blob(source->revision.value(), out.fields.max_binding_bytes);
    out.presence(source->provider_revision_marker.has_value());
    if(source->provider_revision_marker) out.blob(*source->provider_revision_marker, out.fields.max_binding_bytes);
    out.blob(source->adapter_recipe, out.fields.max_recipe_bytes);
    write_evidence(out, source->evidence);
}

std::optional<ConversationSourceBinding> read_source(Reader& in) {
    if(!in.presence()) return std::nullopt;
    ConversationSourceBinding source;
    switch(in.integer(1)) {
    case 1: source.identity = ConversationSourceIdentity::ExternalEvent; break;
    case 2: source.identity = ConversationSourceIdentity::ObjectRevision; break;
    case 3: source.identity = ConversationSourceIdentity::LocalObservation; break;
    default: reject();
    }
    source.object = read_external(in);
    source.event = read_external(in);
    source.revision = SourceRevisionId{in.blob(in.fields.max_binding_bytes)};
    if(in.presence()) source.provider_revision_marker = in.blob(in.fields.max_binding_bytes);
    source.adapter_recipe = in.blob(in.fields.max_recipe_bytes);
    source.evidence = read_evidence(in);
    return source;
}

void write_content(Writer& out, const ConversationContent& content) {
    out.presence(content.text.has_value());
    if(content.text) out.blob(*content.text, out.fields.max_text_bytes);
    out.count(content.attachments.size(), out.fields.max_attachments);
    for(const auto& attachment : content.attachments) out.blob(attachment.value(), out.fields.max_binding_bytes);
}

ConversationContent read_content(Reader& in) {
    ConversationContent content;
    if(in.presence()) content.text = in.blob(in.fields.max_text_bytes);
    const auto count = in.count(in.fields.max_attachments, 5);
    content.attachments.reserve(count);
    for(std::size_t i = 0; i < count; ++i)
        content.attachments.emplace_back(in.blob(in.fields.max_binding_bytes));
    return content;
}

void write_payload(Writer& out, const ConversationPayload& payload) {
    if(const auto* created = std::get_if<ConversationMessageCreated>(&payload)) {
        write_content(out, created->content);
    } else if(const auto* snapshot = std::get_if<ConversationSnapshotObserved>(&payload)) {
        write_content(out, snapshot->content);
    } else if(const auto* edited = std::get_if<ConversationEdited>(&payload)) {
        write_ref(out, edited->affected); write_content(out, edited->replacement);
    } else if(const auto* tombstoned = std::get_if<ConversationTombstoned>(&payload)) {
        write_ref(out, tombstoned->affected);
    } else if(const auto* restored = std::get_if<ConversationRestored>(&payload)) {
        write_ref(out, restored->affected); write_ref(out, restored->retained_state);
    } else reject();
}

ConversationPayload read_legacy_payload(Reader& in, std::uint32_t type) {
    switch(type) {
    case 1: return ConversationMessageCreated{read_content(in)};
    case 2: return ConversationSnapshotObserved{read_content(in)};
    case 3: {
        auto affected = read_ref(in);
        auto content = read_content(in);
        return ConversationEdited{std::move(affected), std::move(content)};
    }
    case 4: return ConversationTombstoned{read_ref(in)};
    case 5: {
        auto affected = read_ref(in);
        auto retained = read_ref(in);
        return ConversationRestored{std::move(affected), std::move(retained)};
    }
    default: reject();
    }
}

void write_links(Writer& out, const std::vector<ConversationLink>& links) {
    out.count(links.size(), out.fields.max_links);
    for(const auto& link : links) {
        switch(link.kind) {
        case ConversationLinkKind::Reply: out.integer(1, 1); break;
        case ConversationLinkKind::Thread: out.integer(2, 1); break;
        case ConversationLinkKind::Branch: out.integer(3, 1); break;
        case ConversationLinkKind::Forward: out.integer(4, 1); break;
        case ConversationLinkKind::Quote: out.integer(5, 1); break;
        default: reject();
        }
        if(const auto* target = std::get_if<EntryRef>(&link.target)) {
            out.integer(1, 1); write_ref(out, *target);
        } else if(const auto* unresolved = std::get_if<UnresolvedConversationTarget>(&link.target)) {
            out.integer(2, 1); write_external(out, unresolved->external); write_evidence(out, unresolved->evidence);
        } else reject();
    }
}

std::vector<ConversationLink> read_links(Reader& in) {
    // Smallest link is unresolved with absent external and two nonempty evidence fields.
    const auto count = in.count(in.fields.max_links, 13);
    std::vector<ConversationLink> links;
    links.reserve(count);
    for(std::size_t i = 0; i < count; ++i) {
        ConversationLink link;
        switch(in.integer(1)) {
        case 1: link.kind = ConversationLinkKind::Reply; break;
        case 2: link.kind = ConversationLinkKind::Thread; break;
        case 3: link.kind = ConversationLinkKind::Branch; break;
        case 4: link.kind = ConversationLinkKind::Forward; break;
        case 5: link.kind = ConversationLinkKind::Quote; break;
        default: reject();
        }
        switch(in.integer(1)) {
        case 1: link.target = read_ref(in); break;
        case 2: {
            auto external = read_external(in);
            auto evidence = read_evidence(in);
            link.target = UnresolvedConversationTarget{std::move(external), std::move(evidence)};
            break;
        }
        default: reject();
        }
        links.push_back(std::move(link));
    }
    return links;
}

void write_timestamp(Writer& out, const std::optional<std::int64_t>& value) {
    out.presence(value.has_value());
    if(value) out.integer(static_cast<std::uint64_t>(*value), 8);
}

std::optional<std::int64_t> read_timestamp(Reader& in) {
    if(!in.presence()) return std::nullopt;
    return in.signed_integer();
}

template<typename Metadata>
void write_metadata(Writer& out, const Metadata& entry,
    const std::vector<ConversationMetadataExtension>& extensions, const ConversationCodecLimits& limits) {
    out.integer(1, 1); write_ref(out, entry.ref);
    out.integer(2, 1); out.blob(entry.scope.value(), limits.max_frame_bytes);
    out.integer(3, 1); write_source(out, entry.source);
    out.integer(4, 1); write_actor(out, entry.actor);
    out.integer(5, 1);
    out.presence(entry.llm_role.has_value());
    if(entry.llm_role) {
        switch(*entry.llm_role) {
        case ConversationLlmRole::System: out.integer(1, 1); break;
        case ConversationLlmRole::Developer: out.integer(2, 1); break;
        case ConversationLlmRole::User: out.integer(3, 1); break;
        case ConversationLlmRole::Assistant: out.integer(4, 1); break;
        case ConversationLlmRole::Tool: out.integer(5, 1); break;
        default: reject();
        }
    }
    out.integer(6, 1); write_actor(out, entry.observer);
    out.integer(7, 1); write_timestamp(out, entry.source_unix_us);
    out.integer(8, 1); write_timestamp(out, entry.observed_unix_us);
    out.integer(9, 1); write_links(out, entry.links);
    out.count(extensions.size(), limits.max_extensions);
    std::uint32_t previous = 0;
    for(const auto& extension : extensions) {
        if(extension.id <= previous) reject(ConversationCodecCode::InvalidValue);
        previous = extension.id;
        out.integer(extension.id, 4);
        out.presence(extension.is_critical);
        out.blob(ByteView{extension.bytes.data(), extension.bytes.size()}, limits.max_extension_bytes);
    }
}

ConversationMetadata read_metadata(Reader& in, std::vector<ConversationMetadataExtension>& extensions,
    const ConversationCodecLimits& limits) {
    ConversationMetadata entry;
    in.tag(1); entry.ref = read_ref(in);
    in.tag(2); entry.scope = ScopeId{in.blob(limits.max_frame_bytes)};
    in.tag(3); entry.source = read_source(in);
    in.tag(4); entry.actor = read_actor(in);
    in.tag(5);
    if(in.presence()) {
        switch(in.integer(1)) {
        case 1: entry.llm_role = ConversationLlmRole::System; break;
        case 2: entry.llm_role = ConversationLlmRole::Developer; break;
        case 3: entry.llm_role = ConversationLlmRole::User; break;
        case 4: entry.llm_role = ConversationLlmRole::Assistant; break;
        case 5: entry.llm_role = ConversationLlmRole::Tool; break;
        default: reject();
        }
    }
    in.tag(6); entry.observer = read_actor(in);
    in.tag(7); entry.source_unix_us = read_timestamp(in);
    in.tag(8); entry.observed_unix_us = read_timestamp(in);
    in.tag(9); entry.links = read_links(in);
    const auto count = in.count(limits.max_extensions, 9);
    extensions.reserve(count);
    std::uint32_t previous = 0;
    for(std::size_t i = 0; i < count; ++i) {
        ConversationMetadataExtension extension;
        extension.id = static_cast<std::uint32_t>(in.integer(4));
        if(extension.id <= previous) reject();
        previous = extension.id;
        extension.is_critical = in.presence();
        extension.bytes = in.binary(limits.max_extension_bytes);
        extensions.push_back(std::move(extension));
    }
    if(in.remaining() != 0) reject();
    return entry;
}

void check_pair(std::uint32_t type, std::uint16_t schema) {
    if(type == 0 || type == std::numeric_limits<std::uint32_t>::max() || schema == 0) reject();
}

std::pair<std::uint32_t, std::uint16_t> payload_identity(const ConversationPayload& payload) {
    if(std::holds_alternative<ConversationMessageCreated>(payload)) return {std::uint32_t{1}, std::uint16_t{1}};
    if(std::holds_alternative<ConversationSnapshotObserved>(payload)) return {std::uint32_t{2}, std::uint16_t{1}};
    if(std::holds_alternative<ConversationEdited>(payload)) return {std::uint32_t{3}, std::uint16_t{1}};
    if(std::holds_alternative<ConversationTombstoned>(payload)) return {std::uint32_t{4}, std::uint16_t{1}};
    if(std::holds_alternative<ConversationRestored>(payload)) return {std::uint32_t{5}, std::uint16_t{1}};
    reject(ConversationCodecCode::InvalidValue);
}

std::pair<std::uint32_t, std::uint16_t> payload_identity(const ConversationEnvelopePayload& payload) {
    if(const auto* legacy = std::get_if<ConversationPayload>(&payload)) return payload_identity(*legacy);
    if(std::holds_alternative<ConversationToolCallObserved>(payload)) return {std::uint32_t{256}, std::uint16_t{1}};
    if(std::holds_alternative<ConversationToolResultObserved>(payload)) return {std::uint32_t{257}, std::uint16_t{1}};
    if(const auto* opaque = std::get_if<ConversationOpaquePayload>(&payload)) {
        check_pair(opaque->type_id, opaque->schema_version);
        if(is_supported_conversation_payload(opaque->type_id, opaque->schema_version)) reject(ConversationCodecCode::InvalidValue);
        return {opaque->type_id, opaque->schema_version};
    }
    reject(ConversationCodecCode::InvalidValue);
}

void write_payload(Writer& out, const ConversationEnvelopePayload& payload) {
    if(const auto* legacy = std::get_if<ConversationPayload>(&payload)) write_payload(out, *legacy);
    else if(const auto* call = std::get_if<ConversationToolCallObserved>(&payload)) {
        out.blob(ByteView{call->call_id.data(), call->call_id.size()}, out.fields.max_binding_bytes);
        out.blob(call->tool_name, out.fields.max_recipe_bytes);
        out.blob(call->request.value(), out.fields.max_binding_bytes);
    } else if(const auto* result = std::get_if<ConversationToolResultObserved>(&payload)) {
        write_ref(out, result->call);
        out.blob(ByteView{result->call_id.data(), result->call_id.size()}, out.fields.max_binding_bytes);
        switch(result->outcome) {
        case ConversationToolOutcome::Unknown: out.integer(1, 1); break;
        case ConversationToolOutcome::Succeeded: out.integer(2, 1); break;
        case ConversationToolOutcome::Failed: out.integer(3, 1); break;
        case ConversationToolOutcome::Cancelled: out.integer(4, 1); break;
        default: reject(ConversationCodecCode::InvalidValue);
        }
        out.blob(result->result.value(), out.fields.max_binding_bytes);
    } else if(const auto* opaque = std::get_if<ConversationOpaquePayload>(&payload)) {
        out.opaque(ByteView{opaque->bytes.data(), opaque->bytes.size()});
    } else reject(ConversationCodecCode::InvalidValue);
}

ConversationEnvelopePayload read_payload(Reader& in, const ConversationFrameHeader& header) {
    if(!is_supported_conversation_payload(header.payload_type_id, header.payload_schema_version))
        return ConversationOpaquePayload{header.payload_type_id, header.payload_schema_version, in.opaque()};
    if(header.payload_type_id <= 5) return read_legacy_payload(in, header.payload_type_id);
    if(header.payload_type_id == 256) {
        auto id = in.binary(in.fields.max_binding_bytes);
        auto name = in.blob(in.fields.max_recipe_bytes);
        auto request = in.blob(in.fields.max_binding_bytes);
        return ConversationToolCallObserved{std::move(id), std::move(name), ArtifactBindingId{std::move(request)}};
    }
    auto call = read_ref(in);
    auto id = in.binary(in.fields.max_binding_bytes);
    ConversationToolOutcome outcome;
    switch(in.integer(1)) {
    case 1: outcome = ConversationToolOutcome::Unknown; break;
    case 2: outcome = ConversationToolOutcome::Succeeded; break;
    case 3: outcome = ConversationToolOutcome::Failed; break;
    case 4: outcome = ConversationToolOutcome::Cancelled; break;
    default: reject();
    }
    auto result = in.blob(in.fields.max_binding_bytes);
    return ConversationToolResultObserved{std::move(call), std::move(id), outcome, ArtifactBindingId{std::move(result)}};
}

void check_issues(const std::vector<ConversationValidationIssue>& issues, bool decoding) {
    for(const auto& issue : issues) {
        if(issue.code == ConversationValidationCode::LimitExceeded) reject(ConversationCodecCode::ResourceLimit);
    }
    if(!issues.empty()) reject(decoding ? ConversationCodecCode::Malformed : ConversationCodecCode::InvalidValue);
}

template<typename Metadata, typename Payload>
ByteBuffer serialize_value(const Metadata& metadata, const Payload& payload,
    const std::vector<ConversationMetadataExtension>& extensions, const ConversationCodecLimits& limits) {
    const auto identity = payload_identity(payload);
    ExternalFrames prepared;
    Budget preflight_budget(limits);
    Writer metadata_counter(limits, preflight_budget, prepared);
    write_metadata(metadata_counter, metadata, extensions, limits);
    Writer payload_counter(limits, preflight_budget, prepared);
    write_payload(payload_counter, payload);
    const auto body_bytes = metadata_counter.size() + payload_counter.size();
    if(limits.max_frame_bytes < kConversationEnvelopeHeaderBytes ||
       body_bytes > limits.max_frame_bytes - kConversationEnvelopeHeaderBytes)
        reject(ConversationCodecCode::ResourceLimit);
    const auto total_bytes = kConversationEnvelopeHeaderBytes + body_bytes;
    ByteBuffer bytes;
    bytes.reserve(total_bytes);
    Budget output_budget(limits);
    Writer out(limits, output_budget, prepared, &bytes);
    out.raw("AMCE"); out.integer(1, 2); out.integer(0, 2);
    out.integer(total_bytes, 4); out.integer(metadata_counter.size(), 4);
    out.integer(identity.first, 4); out.integer(identity.second, 2); out.integer(payload_counter.size(), 4);
    write_metadata(out, metadata, extensions, limits);
    write_payload(out, payload);
    if(bytes.size() != total_bytes || prepared.position != prepared.frames.size())
        throw std::logic_error("conversation envelope size preflight mismatch");
    return bytes;
}

} // namespace

ConversationCodecError::ConversationCodecError(ConversationCodecCode code) :
    std::invalid_argument("conversation codec refused input"), m_code(code) {}

ConversationCodecLimits extended_conversation_codec_limits() {
    ConversationCodecLimits result;
    result.fields.max_text_bytes = 4U * 1024U * 1024U;
    result.fields.max_attachments = 512;
    result.fields.max_links = 512;
    result.fields.max_binding_bytes = 16384;
    result.fields.max_locator_bytes = 16384;
    result.fields.max_recipe_bytes = 4096;
    result.max_extensions = 256;
    result.max_extension_bytes = 1024U * 1024U;
    result.max_objects = 16384;
    return result;
}

bool conversation_codec_limits_cover(const ConversationCodecLimits& reader, const ConversationCodecLimits& writer) {
    check_limits(reader); check_limits(writer);
    return reader.max_frame_bytes >= writer.max_frame_bytes &&
        reader.max_owned_bytes >= writer.max_owned_bytes && reader.max_objects >= writer.max_objects &&
        reader.max_extensions >= writer.max_extensions && reader.max_extension_bytes >= writer.max_extension_bytes &&
        reader.fields.max_text_bytes >= writer.fields.max_text_bytes &&
        reader.fields.max_attachments >= writer.fields.max_attachments && reader.fields.max_links >= writer.fields.max_links &&
        reader.fields.max_binding_bytes >= writer.fields.max_binding_bytes &&
        reader.fields.max_locator_bytes >= writer.fields.max_locator_bytes &&
        reader.fields.max_recipe_bytes >= writer.fields.max_recipe_bytes;
}

ConversationFrameHeader inspect_conversation_frame(ByteView bytes, const ConversationCodecLimits& limits) {
    check_limits(limits);
    if(bytes.data == nullptr && bytes.size != 0) reject();
    if(bytes.size > limits.max_frame_bytes) reject(ConversationCodecCode::ResourceLimit);
    Budget budget(limits);
    Reader in(bytes, limits.fields, budget);
    if(text_view(in.take(4)) != "AMCE") reject();
    if(in.integer(2) != 1) reject(ConversationCodecCode::UnsupportedEnvelope);
    if(in.integer(2) != 0) reject();
    ConversationFrameHeader header;
    header.frame_bytes = static_cast<std::uint32_t>(in.integer(4));
    header.metadata_bytes = static_cast<std::uint32_t>(in.integer(4));
    header.payload_type_id = static_cast<std::uint32_t>(in.integer(4));
    header.payload_schema_version = static_cast<std::uint16_t>(in.integer(2));
    header.payload_bytes = static_cast<std::uint32_t>(in.integer(4));
    check_pair(header.payload_type_id, header.payload_schema_version);
    if(header.frame_bytes != bytes.size || header.metadata_bytes > in.remaining() ||
       header.payload_bytes != in.remaining() - header.metadata_bytes) reject();
    return header;
}

ByteBuffer serialize_conversation_envelope(const ConversationEnvelope& envelope, const ConversationCodecLimits& limits) {
    check_limits(limits);
    if(envelope.extensions.size() > limits.max_extensions) reject(ConversationCodecCode::ResourceLimit);
    for(const auto& extension : envelope.extensions)
        if(extension.bytes.size() > limits.max_extension_bytes) reject(ConversationCodecCode::ResourceLimit);
    check_issues(validate_conversation_envelope(envelope, limits.fields), false);
    return serialize_value(envelope.metadata, envelope.payload, envelope.extensions, limits);
}

ConversationEnvelope deserialize_conversation_envelope(ByteView bytes, const ConversationDecodeOptions& options) {
    const auto header = inspect_conversation_frame(bytes, options.limits);
    Budget budget(options.limits);
    Reader metadata({bytes.data + kConversationEnvelopeHeaderBytes, header.metadata_bytes}, options.limits.fields, budget);
    ConversationEnvelope envelope;
    envelope.metadata = read_metadata(metadata, envelope.extensions, options.limits);
    Reader payload({bytes.data + kConversationEnvelopeHeaderBytes + header.metadata_bytes, header.payload_bytes},
        options.limits.fields, budget);
    envelope.payload = read_payload(payload, header);
    if(payload.remaining() != 0) reject();
    check_issues(validate_conversation_envelope(envelope, options.limits.fields), true);
    if(options.require_typed_payload) (void)require_typed_conversation_payload(envelope);
    return envelope;
}

const ConversationEnvelopePayload& require_typed_conversation_payload(const ConversationEnvelope& envelope) {
    for(const auto& extension : envelope.extensions)
        if(extension.is_critical) reject(ConversationCodecCode::UnsupportedMetadata);
    if(std::holds_alternative<ConversationOpaquePayload>(envelope.payload)) reject(ConversationCodecCode::UnsupportedPayload);
    if(envelope.payload.valueless_by_exception()) reject(ConversationCodecCode::InvalidValue);
    return envelope.payload;
}

ConversationEnvelope make_conversation_envelope(ConversationEntry entry) {
    ConversationEnvelope result;
    result.metadata = {std::move(entry.ref), std::move(entry.scope), std::move(entry.source), std::move(entry.actor),
        entry.llm_role, std::move(entry.observer), entry.source_unix_us, entry.observed_unix_us, std::move(entry.links)};
    result.payload = std::move(entry.payload);
    return result;
}

ConversationEntry to_conversation_entry(ConversationEnvelope envelope) {
    if(!envelope.extensions.empty()) reject(ConversationCodecCode::UnsupportedMetadata);
    auto* legacy = std::get_if<ConversationPayload>(&envelope.payload);
    if(!legacy) reject(ConversationCodecCode::UnsupportedPayload);
    auto& m = envelope.metadata;
    return {std::move(m.ref), std::move(m.scope), std::move(m.source), std::move(m.actor), m.llm_role,
        std::move(m.observer), m.source_unix_us, m.observed_unix_us, std::move(*legacy), std::move(m.links)};
}

ByteBuffer serialize_conversation_entry(const ConversationEntry& entry, const ConversationCodecLimits& limits) {
    check_limits(limits);
    check_issues(validate_conversation_entry_with_limits(entry, limits.fields), false);
    return serialize_value(entry, entry.payload, {}, limits);
}

ConversationEntry deserialize_conversation_entry(ByteView bytes, const ConversationDecodeOptions& options) {
    return to_conversation_entry(deserialize_conversation_envelope(bytes, options));
}

} // namespace agent_memory
