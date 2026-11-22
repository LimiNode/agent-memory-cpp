#include "conversation_codec.hpp"

#include <limits>
#include <stdexcept>
#include <utility>

namespace agent_memory {
namespace {

[[noreturn]] void reject() { throw std::invalid_argument("invalid conversation entry encoding"); }

// The same local writer counts first, then writes to one exactly reserved buffer.
class Writer final {
public:
    explicit Writer(std::string* output = nullptr) : m_output(output) {}
    std::size_t size() const noexcept { return m_size; }
    void integer(std::uint64_t value, unsigned width) {
        add(width);
        if(m_output) {
            for(unsigned i = width; i > 0; --i)
                m_output->push_back(static_cast<char>((value >> ((i - 1) * 8)) & 0xffU));
        }
    }
    void raw(std::string_view bytes) {
        add(bytes.size());
        if(m_output && !bytes.empty()) m_output->append(bytes.data(), bytes.size());
    }
    void blob(std::string_view bytes, std::size_t maximum) {
        if(bytes.size() > maximum) reject();
        integer(bytes.size(), 4);
        raw(bytes);
    }
    void presence(bool present) { integer(present ? 1 : 0, 1); }
private:
    void add(std::size_t count) {
        if(count > kMaxConversationEntryBytes - m_size) reject();
        m_size += count;
    }
    std::string* m_output;
    std::size_t m_size = 0;
};

class Reader final {
public:
    explicit Reader(std::string_view bytes) : m_bytes(bytes) {}
    std::size_t remaining() const noexcept { return m_bytes.size() - m_position; }
    std::string_view take(std::size_t count) {
        if(count > remaining()) reject();
        const auto result = m_bytes.substr(m_position, count);
        m_position += count;
        return result;
    }
    std::uint64_t integer(unsigned width) {
        std::uint64_t result = 0;
        for(const auto c : take(width)) result = (result << 8) | static_cast<unsigned char>(c);
        return result;
    }
    void tag(std::uint64_t expected) { if(integer(1) != expected) reject(); }
    bool presence() {
        const auto value = integer(1);
        if(value > 1) reject();
        return value == 1;
    }
    std::string_view blob_view(std::size_t maximum) {
        const auto count = integer(4);
        if(count > maximum || count > remaining()) reject();
        return take(static_cast<std::size_t>(count));
    }
    std::string blob(std::size_t maximum) { return std::string(blob_view(maximum)); }
    std::size_t count(std::size_t maximum, std::size_t minimum_element_bytes) {
        const auto value = integer(2);
        if(value > maximum || value > remaining() / minimum_element_bytes) reject();
        return static_cast<std::size_t>(value);
    }
    std::int64_t signed_integer() {
        const auto bits = integer(8);
        const auto maximum = static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max());
        return bits <= maximum ? static_cast<std::int64_t>(bits) : -static_cast<std::int64_t>(~bits) - 1;
    }
private:
    std::string_view m_bytes;
    std::size_t m_position = 0;
};

void write_ref(Writer& out, const EntryRef& ref) {
    out.blob(ref.conversation.allocation_namespace, kMaxConversationNamespaceBytes);
    out.integer(ref.conversation.id.value(), 8);
    out.integer(ref.entry.value(), 8);
    out.integer(ref.revision.value(), 8);
}

EntryRef read_ref(Reader& in) {
    auto allocation_namespace = in.blob(kMaxConversationNamespaceBytes);
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
    auto allocation_namespace = in.blob(kMaxConversationNamespaceBytes);
    const auto id = in.integer(8);
    return ActorRef{std::move(allocation_namespace), ActorId{id}};
}

void write_external(Writer& out, const std::optional<ExternalObjectRef>& ref) {
    out.presence(ref.has_value());
    if(ref) out.blob(serialize_external_object_ref(*ref), kMaxExternalObjectRefBytes);
}

std::optional<ExternalObjectRef> read_external(Reader& in) {
    if(!in.presence()) return std::nullopt;
    return deserialize_external_object_ref(in.blob_view(kMaxExternalObjectRefBytes));
}

void write_evidence(Writer& out, const ConversationImportEvidence& evidence) {
    out.blob(evidence.artifact.value(), kMaxConversationBindingBytes);
    out.blob(evidence.record_locator, kMaxConversationLocatorBytes);
}

ConversationImportEvidence read_evidence(Reader& in) {
    auto artifact = in.blob(kMaxConversationBindingBytes);
    auto locator = in.blob(kMaxConversationLocatorBytes);
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
    out.blob(source->revision.value(), kMaxConversationBindingBytes);
    out.presence(source->provider_revision_marker.has_value());
    if(source->provider_revision_marker) out.blob(*source->provider_revision_marker, kMaxConversationBindingBytes);
    out.blob(source->adapter_recipe, kMaxConversationRecipeBytes);
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
    source.revision = SourceRevisionId{in.blob(kMaxConversationBindingBytes)};
    if(in.presence()) source.provider_revision_marker = in.blob(kMaxConversationBindingBytes);
    source.adapter_recipe = in.blob(kMaxConversationRecipeBytes);
    source.evidence = read_evidence(in);
    return source;
}

void write_content(Writer& out, const ConversationContent& content) {
    out.presence(content.text.has_value());
    if(content.text) out.blob(*content.text, kMaxConversationTextBytes);
    if(content.attachments.size() > kMaxConversationAttachments) reject();
    out.integer(content.attachments.size(), 2);
    for(const auto& attachment : content.attachments) out.blob(attachment.value(), kMaxConversationBindingBytes);
}

ConversationContent read_content(Reader& in) {
    ConversationContent content;
    if(in.presence()) content.text = in.blob(kMaxConversationTextBytes);
    const auto count = in.count(kMaxConversationAttachments, 5);
    content.attachments.reserve(count);
    for(std::size_t i = 0; i < count; ++i)
        content.attachments.emplace_back(in.blob(kMaxConversationBindingBytes));
    return content;
}

void write_payload(Writer& out, const ConversationPayload& payload) {
    if(const auto* created = std::get_if<ConversationMessageCreated>(&payload)) {
        out.integer(1, 1); write_content(out, created->content);
    } else if(const auto* snapshot = std::get_if<ConversationSnapshotObserved>(&payload)) {
        out.integer(2, 1); write_content(out, snapshot->content);
    } else if(const auto* edited = std::get_if<ConversationEdited>(&payload)) {
        out.integer(3, 1); write_ref(out, edited->affected); write_content(out, edited->replacement);
    } else if(const auto* tombstoned = std::get_if<ConversationTombstoned>(&payload)) {
        out.integer(4, 1); write_ref(out, tombstoned->affected);
    } else if(const auto* restored = std::get_if<ConversationRestored>(&payload)) {
        out.integer(5, 1); write_ref(out, restored->affected); write_ref(out, restored->retained_state);
    } else reject();
}

ConversationPayload read_payload(Reader& in) {
    switch(in.integer(1)) {
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
    if(links.size() > kMaxConversationLinks) reject();
    out.integer(links.size(), 2);
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
    const auto count = in.count(kMaxConversationLinks, 13);
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

void write_entry(Writer& out, const ConversationEntry& entry) {
    out.raw("AMCE"); out.integer(1, 2);
    out.integer(1, 1); write_ref(out, entry.ref);
    out.integer(2, 1); out.blob(entry.scope.value(), kMaxConversationScopeBytes);
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
    out.integer(9, 1); write_payload(out, entry.payload);
    out.integer(10, 1); write_links(out, entry.links);
}

} // namespace

std::string serialize_conversation_entry(const ConversationEntry& entry) {
    if(entry.scope.value().size() > kMaxConversationScopeBytes || !validate_conversation_entry(entry).empty()) reject();
    Writer counter;
    write_entry(counter, entry);
    std::string bytes;
    bytes.reserve(counter.size());
    Writer writer(&bytes);
    write_entry(writer, entry);
    if(bytes.size() != counter.size()) throw std::logic_error("conversation entry size preflight mismatch");
    return bytes;
}

ConversationEntry deserialize_conversation_entry(std::string_view bytes) {
    if(bytes.size() > kMaxConversationEntryBytes) reject();
    Reader in(bytes);
    if(in.take(4) != "AMCE" || in.integer(2) != 1) reject();
    ConversationEntry entry;
    in.tag(1); entry.ref = read_ref(in);
    in.tag(2); entry.scope = ScopeId{in.blob(kMaxConversationScopeBytes)};
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
    in.tag(9); entry.payload = read_payload(in);
    in.tag(10); entry.links = read_links(in);
    if(in.remaining() != 0 || !validate_conversation_entry(entry).empty()) reject();
    return entry;
}

} // namespace agent_memory
