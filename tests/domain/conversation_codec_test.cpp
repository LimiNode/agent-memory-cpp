#include <agent_memory/domain/conversation_codec.hpp>
#include <agent_memory/domain/Sha256.hpp>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <iterator>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

namespace am = agent_memory;

namespace {

void expect(bool value, const char* message) {
    if(!value) throw std::runtime_error(message);
}

am::ByteView view(const am::ByteBuffer& bytes) {
    return {bytes.data(), bytes.size()};
}

am::ByteBuffer unhex(std::string_view text) {
    const auto digit = [](char c) -> unsigned {
        if(c >= '0' && c <= '9') return static_cast<unsigned>(c - '0');
        if(c >= 'a' && c <= 'f') return static_cast<unsigned>(c - 'a' + 10);
        throw std::runtime_error("bad literal golden hex");
    };
    expect(text.size() % 2 == 0, "odd literal golden hex");
    am::ByteBuffer result;
    result.reserve(text.size() / 2);
    for(std::size_t i = 0; i < text.size(); i += 2)
        result.push_back(static_cast<std::uint8_t>(digit(text[i]) * 16 + digit(text[i + 1])));
    return result;
}

std::string opaque_string(std::string_view hex) {
    const auto bytes = unhex(hex);
    return {reinterpret_cast<const char*>(bytes.data()), bytes.size()};
}

std::string digest_hex(const am::ByteBuffer& bytes) {
    constexpr char digits[] = "0123456789abcdef";
    std::string result;
    // The existing standalone SHA API accepts a bounded binary string_view.
    for(const auto byte : am::sha256({reinterpret_cast<const char*>(bytes.data()), bytes.size()})) {
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 15]);
    }
    return result;
}

template<typename Action>
void expect_code(Action action, am::ConversationCodecCode code) {
    try { action(); }
    catch(const am::ConversationCodecError& error) {
        expect(error.code() == code, "codec refusal has wrong typed reason");
        return;
    }
    throw std::runtime_error("expected typed codec refusal");
}

template<typename Action>
void expect_rejected(Action action) {
    try { action(); }
    catch(const std::invalid_argument&) { return; }
    throw std::runtime_error("invalid value or frame accepted");
}

am::EntryRef reference(std::uint64_t entry = 2, std::uint64_t revision = 3,
                       std::uint64_t conversation = 1, std::string ns = "a") {
    return {{std::move(ns), am::ConversationId{conversation}}, am::EntryId{entry}, am::EventRevision{revision}};
}

am::ExternalObjectRef external(std::int64_t parent = -1, std::uint64_t key = 42, std::string kind = "k") {
    return {"p", "s", "d", std::move(kind), am::ExternalObjectKey{parent}, am::ExternalObjectKey{key}};
}

am::ConversationSourceBinding source() {
    am::ConversationSourceBinding result;
    result.revision = am::SourceRevisionId{"r"};
    result.adapter_recipe = "v";
    result.evidence = {am::ArtifactBindingId{"b"}, "l"};
    return result;
}

am::ConversationEntry message(std::string text = "t") {
    am::ConversationEntry result;
    result.ref = reference();
    result.scope = am::ScopeId{"s"};
    result.payload = am::ConversationMessageCreated{{std::move(text), {}}};
    return result;
}

am::ConversationEnvelope envelope(std::string text = "t") {
    return am::make_conversation_envelope(message(std::move(text)));
}

bool same_evidence(const am::ConversationImportEvidence& a, const am::ConversationImportEvidence& b) {
    return a.artifact == b.artifact && a.record_locator == b.record_locator;
}

bool same_source(const am::ConversationSourceBinding& a, const am::ConversationSourceBinding& b) {
    return a.identity == b.identity && a.object == b.object && a.event == b.event && a.revision == b.revision &&
        a.provider_revision_marker == b.provider_revision_marker && a.adapter_recipe == b.adapter_recipe &&
        same_evidence(a.evidence, b.evidence);
}

bool same_content(const am::ConversationContent& a, const am::ConversationContent& b) {
    return a.text == b.text && a.attachments == b.attachments;
}

bool same_legacy_payload(const am::ConversationPayload& a, const am::ConversationPayload& b) {
    return std::visit([&](const auto& value) {
        using T = std::decay_t<decltype(value)>;
        const auto* other = std::get_if<T>(&b);
        if(!other) return false;
        if constexpr(std::is_same_v<T, am::ConversationMessageCreated> ||
                     std::is_same_v<T, am::ConversationSnapshotObserved>) {
            return same_content(value.content, other->content);
        } else if constexpr(std::is_same_v<T, am::ConversationEdited>) {
            return value.affected == other->affected && same_content(value.replacement, other->replacement);
        } else if constexpr(std::is_same_v<T, am::ConversationTombstoned>) {
            return value.affected == other->affected;
        } else {
            return value.affected == other->affected && value.retained_state == other->retained_state;
        }
    }, a);
}

void expect_envelope(const am::ConversationEnvelope& a, const am::ConversationEnvelope& b) {
    const auto& left = a.metadata;
    const auto& right = b.metadata;
    expect(left.ref == right.ref && left.scope == right.scope && left.actor == right.actor &&
        left.llm_role == right.llm_role && left.observer == right.observer &&
        left.source_unix_us == right.source_unix_us && left.observed_unix_us == right.observed_unix_us,
        "identity or optional metadata changed");
    expect(left.source.has_value() == right.source.has_value(), "source presence changed");
    if(left.source) expect(same_source(*left.source, *right.source), "source identity or evidence changed");
    expect(left.links.size() == right.links.size(), "link count changed");
    for(std::size_t i = 0; i < left.links.size(); ++i) {
        const auto& x = left.links[i];
        const auto& y = right.links[i];
        expect(x.kind == y.kind, "link order/kind changed");
        if(const auto* target = std::get_if<am::EntryRef>(&x.target)) {
            const auto* other = std::get_if<am::EntryRef>(&y.target);
            expect(other && *target == *other, "resolved exact target changed");
        } else {
            const auto& unresolved = std::get<am::UnresolvedConversationTarget>(x.target);
            const auto* other = std::get_if<am::UnresolvedConversationTarget>(&y.target);
            expect(other && unresolved.external == other->external &&
                same_evidence(unresolved.evidence, other->evidence), "unresolved evidence changed");
        }
    }
    expect(a.extensions.size() == b.extensions.size(), "metadata extension count changed");
    for(std::size_t i = 0; i < a.extensions.size(); ++i) {
        expect(a.extensions[i].id == b.extensions[i].id &&
            a.extensions[i].is_critical == b.extensions[i].is_critical &&
            a.extensions[i].bytes == b.extensions[i].bytes, "unknown metadata bytes/flags/order changed");
    }
    expect(a.payload.index() == b.payload.index(), "payload interpretation changed");
    std::visit([&](const auto& payload) {
        using T = std::decay_t<decltype(payload)>;
        const auto& other = std::get<T>(b.payload);
        if constexpr(std::is_same_v<T, am::ConversationPayload>) {
            expect(same_legacy_payload(payload, other), "Phase 1 payload changed");
        } else if constexpr(std::is_same_v<T, am::ConversationToolCallObserved>) {
            expect(payload.call_id == other.call_id && payload.tool_name == other.tool_name &&
                payload.request == other.request, "tool request identity or artifact changed");
        } else if constexpr(std::is_same_v<T, am::ConversationToolResultObserved>) {
            expect(payload.call == other.call && payload.call_id == other.call_id &&
                payload.outcome == other.outcome && payload.result == other.result,
                "tool result target, outcome or artifact changed");
        } else {
            expect(payload.type_id == other.type_id && payload.schema_version == other.schema_version &&
                payload.bytes == other.bytes, "opaque payload pair or bytes changed");
        }
    }, a.payload);
}

void round_trip(const am::ConversationEnvelope& value, const am::ConversationCodecLimits& limits = {}) {
    const auto bytes = am::serialize_conversation_envelope(value, limits);
    am::ConversationDecodeOptions options;
    options.limits = limits;
    const auto decoded = am::deserialize_conversation_envelope(view(bytes), options);
    expect_envelope(decoded, value);
    expect(am::serialize_conversation_envelope(decoded, limits) == bytes, "noncanonical accepted frame");
}

struct Literal final { const char* name; const char* hex; const char* sha256; };
const Literal kLiterals[] = {
#include "fixtures/conversation_codec_goldens.inc"
};

std::vector<am::ConversationEnvelope> golden_values() {
    std::vector<am::ConversationEnvelope> values;
    values.push_back(envelope());
    auto snapshot = message();
    snapshot.source = source();
    snapshot.payload = am::ConversationSnapshotObserved{{std::string{"snapshot"}, {am::ArtifactBindingId{"a"}}}};
    values.push_back(am::make_conversation_envelope(std::move(snapshot)));

    auto event = message("external");
    event.source = source();
    event.source->identity = am::ConversationSourceIdentity::ExternalEvent;
    event.source->object = external();
    event.source->event = external(-2, 13, "e");
    event.actor = am::ActorRef{"a", am::ActorId{std::uint64_t{7}}};
    event.llm_role = am::ConversationLlmRole::Assistant;
    event.observer = am::ActorRef{"o", am::ActorId{std::uint64_t{8}}};
    event.source_unix_us = -1;
    event.observed_unix_us = 0;
    values.push_back(am::make_conversation_envelope(event));

    auto revision = message("revision");
    revision.source = source();
    revision.source->identity = am::ConversationSourceIdentity::ObjectRevision;
    revision.source->object = external();
    revision.source->revision = am::SourceRevisionId{opaque_string("ff0072")};
    revision.source->provider_revision_marker = opaque_string("ff006d");
    revision.source->evidence = {am::ArtifactBindingId{opaque_string("ff0062")}, opaque_string("6c00c3a9")};
    values.push_back(am::make_conversation_envelope(std::move(revision)));

    auto edited = message();
    edited.payload = am::ConversationEdited{reference(1, 9), {std::string{"edit"},
        {am::ArtifactBindingId{"x"}, am::ArtifactBindingId{"y"}, am::ArtifactBindingId{"x"}}}};
    values.push_back(am::make_conversation_envelope(std::move(edited)));
    auto tombstone = message();
    tombstone.payload = am::ConversationTombstoned{reference(1, 9)};
    tombstone.llm_role = am::ConversationLlmRole::Tool;
    values.push_back(am::make_conversation_envelope(std::move(tombstone)));
    auto restored = message();
    restored.payload = am::ConversationRestored{reference(1, 9), reference(1, 7)};
    values.push_back(am::make_conversation_envelope(std::move(restored)));

    auto linked = message("links");
    linked.links = {
        {am::ConversationLinkKind::Forward, reference(44, 8, 99, "z")},
        {am::ConversationLinkKind::Quote, reference(45, 9, 99, "z")},
        {am::ConversationLinkKind::Reply,
            am::UnresolvedConversationTarget{std::nullopt, {am::ArtifactBindingId{"b"}, "l"}}},
        {am::ConversationLinkKind::Thread, reference(1, 2)},
        {am::ConversationLinkKind::Branch, reference(46, 10, 99, "z")}
    };
    values.push_back(am::make_conversation_envelope(std::move(linked)));
    auto attached = message();
    attached.payload = am::ConversationMessageCreated{{std::nullopt,
        {am::ArtifactBindingId{opaque_string("7800ff")}, am::ArtifactBindingId{"y"}}}};
    attached.actor = event.actor;
    attached.llm_role = am::ConversationLlmRole::System;
    attached.observer = event.observer;
    attached.source_unix_us = std::numeric_limits<std::int64_t>::min();
    attached.observed_unix_us = std::numeric_limits<std::int64_t>::max();
    values.push_back(am::make_conversation_envelope(std::move(attached)));

    const auto maximum = std::numeric_limits<std::uint64_t>::max();
    auto boundaries = message(opaque_string("d0af00c3a9"));
    boundaries.ref = reference(maximum, maximum, maximum);
    boundaries.scope = am::ScopeId{opaque_string("00ff73")};
    std::get<am::ConversationMessageCreated>(boundaries.payload).content.attachments =
        {am::ArtifactBindingId{opaque_string("00ff")}};
    boundaries.actor = am::ActorRef{"a", am::ActorId{maximum}};
    boundaries.llm_role = am::ConversationLlmRole::Developer;
    boundaries.observer = am::ActorRef{"o", am::ActorId{maximum}};
    boundaries.source_unix_us = std::numeric_limits<std::int64_t>::max();
    boundaries.observed_unix_us = std::numeric_limits<std::int64_t>::min();
    values.push_back(am::make_conversation_envelope(std::move(boundaries)));
    auto empty_text = message("");
    std::get<am::ConversationMessageCreated>(empty_text.payload).content.attachments = {am::ArtifactBindingId{"x"}};
    values.push_back(am::make_conversation_envelope(std::move(empty_text)));

    auto call = envelope();
    call.metadata.source = source();
    call.payload = am::ConversationToolCallObserved{unhex("00ff63"), "shell", am::ArtifactBindingId{"request@1"}};
    values.push_back(std::move(call));
    auto result = envelope();
    result.metadata.source = source();
    result.payload = am::ConversationToolResultObserved{reference(1, 7), unhex("00ff63"),
        am::ConversationToolOutcome::Failed, am::ArtifactBindingId{"result@1"}};
    values.push_back(std::move(result));

    auto unknown = envelope();
    unknown.payload = am::ConversationOpaquePayload{70000, 5, unhex("00ff6f7061717565")};
    values.push_back(std::move(unknown));
    auto next_schema = envelope();
    next_schema.payload = am::ConversationOpaquePayload{1, 2, unhex("ff006e657874")};
    values.push_back(std::move(next_schema));
    auto extensions = envelope();
    extensions.extensions = {{7, false, unhex("00ff")}, {9, false, {}}};
    values.push_back(std::move(extensions));
    auto critical = envelope();
    critical.extensions = {{7, true, unhex("00ff")}};
    values.push_back(std::move(critical));
    return values;
}

void literal_goldens(const std::vector<am::ConversationEnvelope>& values) {
    expect(values.size() == std::size(kLiterals), "literal and semantic fixtures differ");
    for(std::size_t i = 0; i < values.size(); ++i) {
        try {
            const auto expected = unhex(kLiterals[i].hex);
            expect(digest_hex(expected) == kLiterals[i].sha256, "literal independent SHA-256 mismatch");
            expect(am::serialize_conversation_envelope(values[i]) == expected, "independent literal encoding mismatch");
            const auto decoded = am::deserialize_conversation_envelope(view(expected));
            expect_envelope(decoded, values[i]);
            expect(am::serialize_conversation_envelope(decoded) == expected, "literal re-encoding mismatch");
            if(i < 11) {
                const auto entry = am::to_conversation_entry(decoded);
                expect(am::validate_conversation_entry(entry).empty(), "Phase 1 bridge produced invalid entry");
                expect(am::serialize_conversation_entry(entry) == expected, "Phase 1 writer bridge changed bytes");
                const auto entry_decoded = am::deserialize_conversation_entry(view(expected));
                expect_envelope(am::make_conversation_envelope(entry_decoded), values[i]);
            }
        } catch(const std::exception& error) {
            throw std::runtime_error(std::string{"golden "} + kLiterals[i].name + ": " + error.what());
        }
    }
}

void set_uint(am::ByteBuffer& bytes, std::size_t offset, std::uint64_t value, unsigned width) {
    expect(offset <= bytes.size() && width <= bytes.size() - offset, "bad mutation offset");
    for(unsigned i = 0; i < width; ++i)
        bytes[offset + i] = static_cast<std::uint8_t>((value >> (8U * (width - i - 1U))) & 0xffU);
}

std::size_t find_bytes(const am::ByteBuffer& bytes, const am::ByteBuffer& pattern) {
    const auto it = std::search(bytes.begin(), bytes.end(), pattern.begin(), pattern.end());
    expect(it != bytes.end(), "mutation marker missing");
    return static_cast<std::size_t>(it - bytes.begin());
}

void rejected_bytes(const am::ByteBuffer& bytes) {
    expect_rejected([&] { (void)am::deserialize_conversation_envelope(view(bytes)); });
}

void malformed_controls() {
    const auto minimal = unhex(kLiterals[0].hex);
    expect(minimal.size() == 87, "minimal mutation offsets changed");
    auto changed = minimal;
    changed[0] = 'X'; rejected_bytes(changed);
    for(const auto version : {std::uint64_t{0}, std::uint64_t{2}, std::uint64_t{65535}}) {
        changed = minimal; set_uint(changed, 4, version, 2);
        expect_code([&] { (void)am::deserialize_conversation_envelope(view(changed)); },
            am::ConversationCodecCode::UnsupportedEnvelope);
    }
    for(const auto offset : {6U, 7U}) {
        changed = minimal; changed[offset] = 1; rejected_bytes(changed);
    }
    for(const auto offset : {8U, 12U, 22U}) {
        for(const auto value : {std::uint64_t{0}, std::uint64_t{1}, std::uint64_t{0xffffffffU}}) {
            changed = minimal; set_uint(changed, offset, value, 4); rejected_bytes(changed);
        }
    }
    for(const auto type : {std::uint64_t{0}, std::uint64_t{0xffffffffU}}) {
        changed = minimal; set_uint(changed, 16, type, 4); rejected_bytes(changed);
    }
    changed = minimal; set_uint(changed, 20, 0, 2); rejected_bytes(changed);
    for(const auto offset : {26U, 56U, 62U, 64U, 66U, 68U, 70U, 72U, 74U}) {
        for(const auto byte : {0U, 255U}) {
            changed = minimal; changed[offset] = static_cast<std::uint8_t>(byte); rejected_bytes(changed);
        }
    }
    changed = minimal; changed[66] = 4; rejected_bytes(changed); // Duplicate common field.
    changed = minimal; std::swap(changed[64], changed[66]); rejected_bytes(changed);
    for(const auto offset : {63U, 65U, 67U, 69U, 71U, 73U, 79U}) {
        for(const auto byte : {2U, 255U}) {
            changed = minimal; changed[offset] = static_cast<std::uint8_t>(byte); rejected_bytes(changed);
        }
    }
    for(const auto offset : {27U, 57U, 80U}) {
        changed = minimal; set_uint(changed, offset, 0xffffffffU, 4); rejected_bytes(changed);
    }
    for(const auto offset : {75U, 77U, 85U}) {
        changed = minimal; set_uint(changed, offset, 65535, 2); rejected_bytes(changed);
    }
    changed = minimal; changed[31] = 'A'; rejected_bytes(changed);
    changed = minimal; changed[84] = 255; rejected_bytes(changed);
    // Header-only inspection explicitly does not claim typed UTF-8 validation.
    expect(am::inspect_conversation_frame(view(changed)).payload_type_id == 1, "header inspection decoded payload");
    changed = minimal; set_uint(changed, 32, 0, 8); rejected_bytes(changed);
    changed = minimal; changed.push_back(0); rejected_bytes(changed);
    changed = minimal; changed.insert(changed.end(), minimal.begin(), minimal.end()); rejected_bytes(changed);

    const auto event = unhex(kLiterals[2].hex);
    const auto nested = find_bytes(event, unhex("414d5852"));
    changed = event; set_uint(changed, nested - 4, 65537, 4); rejected_bytes(changed);
    changed = event; set_uint(changed, nested - 4, 0xffffffffU, 4); rejected_bytes(changed);
    changed = event; changed[nested + 5] = 2; rejected_bytes(changed);
    changed = event; changed[nested + 27] = 255; rejected_bytes(changed);
    // Common field validity still applies to opaque retention.
    changed = unhex(kLiterals[13].hex); changed[31] = 'A'; rejected_bytes(changed);
    changed = unhex(kLiterals[13].hex); set_uint(changed, 40, 0, 8); rejected_bytes(changed);

    const auto extensions = unhex(kLiterals[15].hex);
    changed = extensions; set_uint(changed, 90, 7, 4); rejected_bytes(changed); // Duplicate extension.
    changed = extensions; set_uint(changed, 79, 10, 4); rejected_bytes(changed); // Descending order.
    changed = extensions; set_uint(changed, 79, 0, 4); rejected_bytes(changed);
    changed = extensions; changed[83] = 2; rejected_bytes(changed); // Reserved flag bits.
    changed = extensions; set_uint(changed, 84, 0xffffffffU, 4); rejected_bytes(changed);
    changed = extensions; set_uint(changed, 77, 65535, 2); rejected_bytes(changed);
    changed = extensions; set_uint(changed, 16, 70000, 4); changed[83] = 2; rejected_bytes(changed);

    // Registered schema1 must parse as typed; malformed known bytes cannot downgrade to opaque.
    changed = unhex(kLiterals[14].hex); set_uint(changed, 20, 1, 2); rejected_bytes(changed);
    // Invalid source-mode values, recipes and locators are not bypassed by frame canonicality.
    const auto snapshot = unhex(kLiterals[1].hex);
    for(const auto mode : {0U, 1U, 2U, 4U, 255U}) {
        changed = snapshot; changed[64] = static_cast<std::uint8_t>(mode); rejected_bytes(changed);
    }
    for(const auto marker : {unhex("0000000176"), unhex("000000016c")}) {
        changed = snapshot;
        changed[find_bytes(changed, marker) + 4] = 255;
        rejected_bytes(changed);
    }
    am::ByteBuffer oversized(am::kMaxConversationEntryBytes + 1, 0);
    expect_code([&] { (void)am::deserialize_conversation_envelope(view(oversized)); },
        am::ConversationCodecCode::ResourceLimit);
}

void unknown_and_lifetime_controls(const std::vector<am::ConversationEnvelope>& values) {
    for(const auto index : {13U, 14U}) {
        const auto bytes = am::serialize_conversation_envelope(values[index]);
        auto retained = am::deserialize_conversation_envelope(view(bytes));
        expect_code([&] { (void)am::require_typed_conversation_payload(retained); },
            am::ConversationCodecCode::UnsupportedPayload);
        am::ConversationDecodeOptions typed;
        typed.require_typed_payload = true;
        expect_code([&] { (void)am::deserialize_conversation_envelope(view(bytes), typed); },
            am::ConversationCodecCode::UnsupportedPayload);
        expect_code([&] { (void)am::deserialize_conversation_entry(view(bytes)); },
            am::ConversationCodecCode::UnsupportedPayload);
        const auto saved = std::get<am::ConversationOpaquePayload>(retained.payload).bytes;
        retained.metadata.observed_unix_us = 42;
        const auto modified = am::serialize_conversation_envelope(retained);
        expect(modified != bytes, "metadata modification not encoded");
        const auto decoded = am::deserialize_conversation_envelope(view(modified));
        expect(std::get<am::ConversationOpaquePayload>(decoded.payload).bytes == saved,
            "read-modify-write lost opaque payload");
    }
    auto advisory = values[15];
    (void)am::require_typed_conversation_payload(advisory);
    expect_code([&] { (void)am::to_conversation_entry(advisory); }, am::ConversationCodecCode::UnsupportedMetadata);
    advisory.metadata.observed_unix_us = 4;
    auto& legacy = std::get<am::ConversationPayload>(advisory.payload);
    std::get<am::ConversationMessageCreated>(legacy).content.text = "changed";
    const auto advisory_bytes = am::serialize_conversation_envelope(advisory);
    expect_envelope(am::deserialize_conversation_envelope(view(advisory_bytes)), advisory);
    expect_code([&] { (void)am::require_typed_conversation_payload(values[16]); },
        am::ConversationCodecCode::UnsupportedMetadata);
    const auto critical = am::serialize_conversation_envelope(values[16]);
    am::ConversationDecodeOptions typed;
    typed.require_typed_payload = true;
    expect_code([&] { (void)am::deserialize_conversation_envelope(view(critical), typed); },
        am::ConversationCodecCode::UnsupportedMetadata);

    // Decoding borrows only during the call. Reuse and destroy the source buffer afterwards.
    std::optional<am::ConversationEnvelope> detached;
    {
        auto input = am::serialize_conversation_envelope(values[13]);
        const auto borrowed = view(input);
        detached = am::deserialize_conversation_envelope(borrowed);
        std::fill(input.begin(), input.end(), 0);
    }
    expect_envelope(*detached, values[13]);
    expect_rejected([] { (void)am::deserialize_conversation_envelope(am::ByteView{nullptr, 0}); });
    expect_rejected([] { (void)am::deserialize_conversation_envelope(am::ByteView{nullptr, 9}); });
    auto empty = envelope();
    empty.payload = am::ConversationOpaquePayload{70000, 1, {}};
    round_trip(empty);
    auto shadow = envelope();
    shadow.payload = am::ConversationOpaquePayload{1, 1, unhex("00ff")};
    expect_rejected([&] { (void)am::serialize_conversation_envelope(shadow); });
    shadow.payload = am::ConversationOpaquePayload{256, 1, {}};
    expect_rejected([&] { (void)am::serialize_conversation_envelope(shadow); });
}

void semantic_and_profile_controls(const std::vector<am::ConversationEnvelope>& values) {
    const auto reject = [](const am::ConversationEntry& entry) {
        expect_rejected([&] { (void)am::serialize_conversation_entry(entry); });
    };
    auto entry = message();
    entry.scope = am::ScopeId{std::string(65537, '\0')};
    expect(am::validate_conversation_entry(entry).empty(), "scope policy leaked into Phase 1");
    round_trip(am::make_conversation_envelope(entry)); // Scope is charged, not arbitrarily identity-capped.
    entry = message(); entry.ref.entry = am::EntryId{}; reject(entry);
    entry = message(); entry.scope = am::ScopeId{}; reject(entry);
    entry = message(); entry.llm_role = static_cast<am::ConversationLlmRole>(255); reject(entry);
    entry = message(); entry.payload = am::ConversationSnapshotObserved{{std::string{"state"}, {}}}; reject(entry);
    entry = message(); entry.source = source(); entry.source->identity = am::ConversationSourceIdentity::ExternalEvent;
    reject(entry);
    entry = message(); entry.source = source(); entry.source->provider_revision_marker = "invented"; reject(entry);
    entry = message(); entry.source = source(); entry.source->adapter_recipe.assign(1025, 'r'); reject(entry);
    entry = message(); entry.source = source(); entry.source->revision = am::SourceRevisionId{std::string(4097, 'r')};
    reject(entry);
    entry = message(); entry.source = source(); entry.source->evidence.record_locator.assign(4097, 'l'); reject(entry);
    entry = message(); entry.source = source();
    entry.source->evidence.artifact = am::ArtifactBindingId{std::string(4097, 'b')}; reject(entry);
    entry = message(); entry.payload = am::ConversationEdited{entry.ref, {std::string{"edit"}, {}}}; reject(entry);
    entry = message(); entry.links = {{am::ConversationLinkKind::Reply, reference(1, 1, 2)}}; reject(entry);
    entry = message(); entry.links.resize(257); reject(entry);
    entry = message();
    std::get<am::ConversationMessageCreated>(entry.payload).content.attachments.resize(257, am::ArtifactBindingId{"a"});
    reject(entry);
    entry = message(std::string(1048577, 'x')); reject(entry);
    entry = message(std::string("\xc0\x80", 2)); reject(entry);

    auto empty = values[10];
    auto absent = empty;
    auto& absent_payload = std::get<am::ConversationPayload>(absent.payload);
    std::get<am::ConversationMessageCreated>(absent_payload).content.text.reset();
    expect(am::serialize_conversation_envelope(empty) != am::serialize_conversation_envelope(absent),
        "absent and present empty text collided");
    round_trip(empty); round_trip(absent);
    for(const auto role : {am::ConversationLlmRole::System, am::ConversationLlmRole::Developer,
                          am::ConversationLlmRole::User, am::ConversationLlmRole::Assistant,
                          am::ConversationLlmRole::Tool}) {
        auto value = envelope(); value.metadata.llm_role = role; round_trip(value);
    }

    const auto base_bytes = am::serialize_conversation_envelope(values[0]);
    am::ConversationCodecLimits tight;
    tight.max_frame_bytes = base_bytes.size() - 1;
    expect_code([&] { (void)am::serialize_conversation_envelope(values[0], tight); },
        am::ConversationCodecCode::ResourceLimit);
    am::ConversationDecodeOptions options;
    options.limits = tight;
    expect_code([&] { (void)am::deserialize_conversation_envelope(view(base_bytes), options); },
        am::ConversationCodecCode::ResourceLimit);
    tight.max_frame_bytes = base_bytes.size();
    round_trip(values[0], tight);
    for(const bool owned : {false, true}) {
        tight = {};
        if(owned) tight.max_owned_bytes = 2;
        else tight.max_objects = 2;
        expect_code([&] { (void)am::serialize_conversation_envelope(values[0], tight); },
            am::ConversationCodecCode::ResourceLimit);
        options.limits = tight;
        expect_code([&] { (void)am::deserialize_conversation_envelope(view(base_bytes), options); },
            am::ConversationCodecCode::ResourceLimit);
        if(owned) tight.max_owned_bytes = 3;
        else tight.max_objects = 3;
        round_trip(values[0], tight);
    }
    // Every field/count dimension participates independently in reader/writer compatibility.
    auto constrained = values[1];
    constrained.metadata.links = {{am::ConversationLinkKind::Reply, reference(1, 1)}};
    const auto constrained_bytes = am::serialize_conversation_envelope(constrained);
    for(const auto dimension : {0, 1, 2, 3, 4}) {
        tight = {};
        switch(dimension) {
        case 0: tight.fields.max_attachments = 0; break;
        case 1: tight.fields.max_links = 0; break;
        case 2: tight.fields.max_binding_bytes = 0; break;
        case 3: tight.fields.max_locator_bytes = 0; break;
        default: tight.fields.max_recipe_bytes = 0;
        }
        expect(!am::conversation_codec_limits_cover(tight, {}), "stricter reader incorrectly covers base writer");
        expect_code([&] { (void)am::serialize_conversation_envelope(constrained, tight); },
            am::ConversationCodecCode::ResourceLimit);
        options.limits = tight;
        expect_code([&] { (void)am::deserialize_conversation_envelope(view(constrained_bytes), options); },
            am::ConversationCodecCode::ResourceLimit);
    }
    tight = {}; tight.fields.max_text_bytes = 0;
    expect_code([&] { (void)am::serialize_conversation_envelope(values[0], tight); },
        am::ConversationCodecCode::ResourceLimit);
    options.limits = tight;
    expect_code([&] { (void)am::deserialize_conversation_envelope(view(base_bytes), options); },
        am::ConversationCodecCode::ResourceLimit);
    tight = {}; tight.max_extensions = 0;
    const auto extension_bytes = am::serialize_conversation_envelope(values[15]);
    expect_code([&] { (void)am::serialize_conversation_envelope(values[15], tight); },
        am::ConversationCodecCode::ResourceLimit);
    options.limits = tight;
    expect_code([&] { (void)am::deserialize_conversation_envelope(view(extension_bytes), options); },
        am::ConversationCodecCode::ResourceLimit);
    tight = {}; tight.max_extension_bytes = 1;
    options.limits = tight;
    expect_code([&] { (void)am::deserialize_conversation_envelope(view(extension_bytes), options); },
        am::ConversationCodecCode::ResourceLimit);

    const auto extended = am::extended_conversation_codec_limits();
    expect(am::conversation_codec_limits_cover(extended, {}), "extended reader does not cover base writer");
    expect(!am::conversation_codec_limits_cover({}, extended), "base reader incorrectly covers extended writer");
    auto wider = message(std::string(extended.fields.max_text_bytes, 'w'));
    auto& content = std::get<am::ConversationMessageCreated>(wider.payload).content;
    content.attachments.assign(extended.fields.max_attachments, am::ArtifactBindingId{"a"});
    wider.links.assign(extended.fields.max_links, {am::ConversationLinkKind::Reply, reference(1, 1)});
    expect(!am::validate_conversation_entry(wider).empty(), "legacy Phase 1 validator silently widened");
    expect(am::validate_conversation_entry_with_limits(wider, extended.fields).empty(),
        "explicit extended field validator did not widen resource policy");
    const auto wider_envelope = am::make_conversation_envelope(wider);
    round_trip(wider_envelope, extended);
    const auto wider_bytes = am::serialize_conversation_envelope(wider_envelope, extended);
    expect_code([&] { (void)am::deserialize_conversation_envelope(view(wider_bytes)); },
        am::ConversationCodecCode::ResourceLimit);
    auto extended_extensions = envelope();
    for(std::size_t i = 0; i < extended.max_extensions; ++i)
        extended_extensions.extensions.push_back({static_cast<std::uint32_t>(i + 1), false, {}});
    extended_extensions.extensions.front().bytes.assign(extended.max_extension_bytes, 255);
    round_trip(extended_extensions, extended);
    const auto extended_extension_bytes = am::serialize_conversation_envelope(extended_extensions, extended);
    expect_code([&] { (void)am::deserialize_conversation_envelope(view(extended_extension_bytes)); },
        am::ConversationCodecCode::ResourceLimit);
    wider.ref.entry = am::EntryId{};
    expect(!am::validate_conversation_entry_with_limits(wider, extended.fields).empty(),
        "resource extension weakened semantic ID invariant");

    for(const auto kind : {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10}) {
        auto invalid = am::ConversationCodecLimits{};
        const auto huge = std::numeric_limits<std::size_t>::max();
        switch(kind) {
        case 0: invalid.max_frame_bytes = huge; break;
        case 1: invalid.max_owned_bytes = huge; break;
        case 2: invalid.max_objects = huge; break;
        case 3: invalid.max_extensions = huge; break;
        case 4: invalid.max_extension_bytes = huge; break;
        case 5: invalid.fields.max_text_bytes = huge; break;
        case 6: invalid.fields.max_attachments = huge; break;
        case 7: invalid.fields.max_links = huge; break;
        case 8: invalid.fields.max_binding_bytes = huge; break;
        case 9: invalid.fields.max_locator_bytes = huge; break;
        default: invalid.fields.max_recipe_bytes = huge;
        }
        expect_code([&] { (void)am::serialize_conversation_envelope(values[0], invalid); },
            am::ConversationCodecCode::InvalidLimits);
        options.limits = invalid;
        expect_code([&] { (void)am::deserialize_conversation_envelope(view(base_bytes), options); },
            am::ConversationCodecCode::InvalidLimits);
        expect_code([&] { (void)am::conversation_codec_limits_cover(invalid, {}); },
            am::ConversationCodecCode::InvalidLimits);
    }
}

void agent_session_controls(const std::vector<am::ConversationEnvelope>& values) {
    // Ordinary chat: an edit references one exact earlier revision, not latest.
    round_trip(values[4]);
    // Efi observation: source evidence and observer stay independent from participant/role.
    round_trip(values[2]);
    // Maschinenwerk: recording a request or outcome has no execution side effect.
    auto retained_call = values[11];
    retained_call.metadata.ref = reference(1, 7);
    round_trip(retained_call);
    const auto call_id = std::get<am::ConversationToolCallObserved>(retained_call.payload).call_id;
    auto result = values[12];
    expect(std::get<am::ConversationToolResultObserved>(result.payload).call_id == call_id &&
        std::get<am::ConversationToolResultObserved>(result.payload).call == retained_call.metadata.ref,
        "tool identity or exact call revision fixture does not match");
    for(const auto outcome : {am::ConversationToolOutcome::Unknown, am::ConversationToolOutcome::Succeeded,
                             am::ConversationToolOutcome::Failed, am::ConversationToolOutcome::Cancelled}) {
        std::get<am::ConversationToolResultObserved>(result.payload).outcome = outcome;
        const auto bytes = am::serialize_conversation_envelope(result);
        const auto decoded = am::deserialize_conversation_envelope(view(bytes));
        expect(std::get<am::ConversationToolResultObserved>(decoded.payload).outcome == outcome,
            "tool outcome inferred or collapsed");
        round_trip(result);
    }
    auto call = values[11];
    call.metadata.source.reset();
    expect_rejected([&] { (void)am::serialize_conversation_envelope(call); });
    call = values[11]; std::get<am::ConversationToolCallObserved>(call.payload).call_id.clear();
    expect_rejected([&] { (void)am::serialize_conversation_envelope(call); });
    call = values[11]; std::get<am::ConversationToolCallObserved>(call.payload).tool_name = "\xff";
    expect_rejected([&] { (void)am::serialize_conversation_envelope(call); });
    result = values[12]; std::get<am::ConversationToolResultObserved>(result.payload).call = result.metadata.ref;
    expect_rejected([&] { (void)am::serialize_conversation_envelope(result); });
    result = values[12]; std::get<am::ConversationToolResultObserved>(result.payload).call = reference(1, 1, 9);
    expect_rejected([&] { (void)am::serialize_conversation_envelope(result); });
    result = values[12];
    std::get<am::ConversationToolResultObserved>(result.payload).outcome = static_cast<am::ConversationToolOutcome>(255);
    expect_rejected([&] { (void)am::serialize_conversation_envelope(result); });
    // Patch/compaction are reserved registry pairs, not implemented typed semantics.
    // Their bytes may be transported, but callers cannot consume them as understood events.
    for(const auto type : {258U, 259U}) {
        auto observation = envelope();
        observation.metadata.source = source();
        observation.payload = am::ConversationOpaquePayload{type, 1, unhex("00ff")};
        round_trip(observation);
        expect_code([&] { (void)am::require_typed_conversation_payload(observation); },
            am::ConversationCodecCode::UnsupportedPayload);
    }
}

std::size_t truncation_and_mutation_controls() {
    std::size_t truncations = 0;
    std::size_t accepted = 0;
    std::size_t rejected = 0;
    const auto exercise = [&](const am::ByteBuffer& bytes) {
        std::optional<am::ConversationEnvelope> decoded;
        try { decoded = am::deserialize_conversation_envelope(view(bytes)); }
        catch(const std::invalid_argument&) { ++rejected; return; }
        expect(am::serialize_conversation_envelope(*decoded) == bytes, "mutation accepted noncanonical bytes");
        ++accepted;
    };
    for(const auto& literal : kLiterals) {
        const auto bytes = unhex(literal.hex);
        for(std::size_t length = 0; length < bytes.size(); ++length) {
            expect_rejected([&] { (void)am::deserialize_conversation_envelope(am::ByteView{bytes.data(), length}); });
            ++truncations;
        }
        for(std::size_t i = 0; i < bytes.size(); ++i) {
            for(const auto byte : {0U, 1U, 2U, 127U, 128U, 255U}) {
                auto changed = bytes;
                changed[i] = static_cast<std::uint8_t>(byte);
                exercise(changed);
            }
        }
    }
    std::uint32_t state = 0x63776d31U;
    const auto random = [&]() { state ^= state << 13; state ^= state >> 17; state ^= state << 5; return state; };
    for(std::size_t i = 0; i < 4096; ++i) {
        auto bytes = unhex(kLiterals[random() % std::size(kLiterals)].hex);
        const auto offset = static_cast<std::size_t>(random()) % bytes.size();
        if(i % 3 == 0) bytes.erase(bytes.begin() + static_cast<std::ptrdiff_t>(offset));
        else if(i % 3 == 1) bytes.insert(bytes.begin() + static_cast<std::ptrdiff_t>(offset),
            static_cast<std::uint8_t>(random() & 255U));
        else bytes[offset] = static_cast<std::uint8_t>(random() & 255U);
        exercise(bytes);
    }
    expect(accepted > 0 && rejected > 0 && truncations > 2000, "negative corpus did not exercise dispositions");
    std::cout << truncations << " truncations, " << accepted + rejected << " bounded mutations, ";
    return accepted + rejected;
}

am::ExternalObjectRef maximal_external() {
    am::ExternalObjectRef result;
    result.provider = std::string(128, 'p');
    result.service_instance = std::string(1024, 's');
    result.identity_domain = std::string(1024, 'd');
    result.object_kind = std::string(64, 'k');
    std::vector<am::ExternalIdentityComponent> parts;
    std::size_t bytes = 6 + 4 * 4 + 128 + 1024 + 1024 + 64 + 1 + 7;
    for(std::size_t i = 0; i < 16; ++i) {
        const auto name = std::string{"c"} + std::to_string(i);
        bytes += 2 + name.size() + 5 + 4096;
        parts.push_back({name, std::string(4096, 'x')});
    }
    auto& tail = std::get<std::string>(parts.back().value);
    expect(bytes > 65536 && bytes - 65536 < tail.size(), "bad maximal AMXR calculation");
    tail.resize(tail.size() - (bytes - 65536));
    result.object_key = am::ExternalObjectKey{std::move(parts)};
    expect(am::is_valid_external_object_ref(result), "maximal AMXR fixture invalid");
    expect(am::serialize_external_object_ref(result).size() == 65536, "maximal AMXR size changed");
    return result;
}

void maximum_controls() {
    const auto maximum = std::numeric_limits<std::uint64_t>::max();
    const auto identity = maximal_external();
    auto entry = message();
    entry.ref = reference(maximum, maximum, maximum, std::string(128, 'a'));
    entry.scope = am::ScopeId{std::string(65536, '\0')};
    entry.source = source();
    entry.source->identity = am::ConversationSourceIdentity::ExternalEvent;
    entry.source->object = identity;
    entry.source->event = identity;
    entry.source->revision = am::SourceRevisionId{std::string(4096, static_cast<char>(255))};
    entry.source->adapter_recipe = std::string(1024, 'r');
    entry.source->evidence = {am::ArtifactBindingId{std::string(4096, static_cast<char>(255))}, std::string(4096, 'l')};
    entry.actor = am::ActorRef{std::string(128, 'a'), am::ActorId{maximum}};
    entry.observer = am::ActorRef{std::string(128, 'o'), am::ActorId{maximum}};
    entry.llm_role = am::ConversationLlmRole::Tool;
    entry.source_unix_us = std::numeric_limits<std::int64_t>::min();
    entry.observed_unix_us = std::numeric_limits<std::int64_t>::max();
    am::ConversationContent replacement;
    replacement.text = std::string(1048576, 't');
    replacement.attachments.assign(256, am::ArtifactBindingId{std::string(4096, static_cast<char>(255))});
    entry.payload = am::ConversationEdited{reference(maximum - 1, maximum, maximum, std::string(128, 'a')),
        std::move(replacement)};
    entry.links.assign(256, {am::ConversationLinkKind::Reply,
        am::UnresolvedConversationTarget{identity, entry.source->evidence}});
    expect(am::validate_conversation_entry(entry).empty(), "combined Phase 1 maxima invalid");
    auto value = am::make_conversation_envelope(std::move(entry));
    const auto bytes = am::serialize_conversation_envelope(value);
    expect(bytes.size() == 21186997, "combined maximum exact-size calculation changed");
    const auto decoded = am::deserialize_conversation_envelope(view(bytes));
    expect_envelope(decoded, value);
    expect(am::serialize_conversation_envelope(decoded) == bytes, "combined maxima not canonical");

    // Reach the absolute frame ceiling with a legal unsupported pair, then exceed it by one.
    auto largest = envelope();
    largest.payload = am::ConversationOpaquePayload{70000, 1,
        am::ByteBuffer(am::kMaxConversationEntryBytes - 79, 255)};
    const auto frame = am::serialize_conversation_envelope(largest);
    expect(frame.size() == am::kMaxConversationEntryBytes, "absolute maximum frame not reached");
    const auto restored = am::deserialize_conversation_envelope(view(frame));
    expect_envelope(restored, largest);
    auto tighter = am::ConversationDecodeOptions{};
    tighter.limits.max_frame_bytes = frame.size() - 1;
    expect_code([&] { (void)am::deserialize_conversation_envelope(view(frame), tighter); },
        am::ConversationCodecCode::ResourceLimit);
    std::get<am::ConversationOpaquePayload>(largest.payload).bytes.push_back(0);
    expect_code([&] { (void)am::serialize_conversation_envelope(largest); },
        am::ConversationCodecCode::ResourceLimit);
    std::cout << "combined maximum21186997 and absolute frame33554432 bytes passed\n";
}

void implementation_cap_controls() {
    constexpr std::size_t eight_mib = 8U * 1024U * 1024U;
    am::ConversationCodecLimits caps;
    caps.fields.max_text_bytes = eight_mib;
    caps.fields.max_attachments = 4096;
    caps.fields.max_links = 4096;
    caps.fields.max_binding_bytes = 65536;
    caps.fields.max_locator_bytes = 65536;
    caps.fields.max_recipe_bytes = 4096;
    caps.max_extensions = 1024;
    caps.max_extension_bytes = eight_mib;
    caps.max_objects = 32768;
    expect(am::conversation_codec_limits_cover(caps, am::extended_conversation_codec_limits()),
        "implementation-cap reader does not cover extended writer");

    // All independent scalar/count ceilings coexist below the aggregate frame ceiling.
    auto value = envelope(std::string(eight_mib, 't'));
    value.metadata.source = source();
    value.metadata.source->revision = am::SourceRevisionId{std::string(65536, static_cast<char>(255))};
    value.metadata.source->adapter_recipe.assign(4096, 'r');
    value.metadata.source->evidence = {
        am::ArtifactBindingId{std::string(65536, static_cast<char>(255))}, std::string(65536, 'l')};
    value.metadata.links.assign(4096, {am::ConversationLinkKind::Reply, reference(1, 1)});
    auto& legacy = std::get<am::ConversationPayload>(value.payload);
    auto& content = std::get<am::ConversationMessageCreated>(legacy).content;
    content.attachments.assign(4096, am::ArtifactBindingId{"a"});
    content.attachments.front() = am::ArtifactBindingId{std::string(65536, static_cast<char>(255))};
    value.extensions.reserve(1024);
    for(std::uint32_t id = 1; id <= 1024; ++id) value.extensions.push_back({id, false, {}});
    value.extensions.front().bytes.assign(eight_mib, 255);
    expect(am::validate_conversation_envelope(value, caps.fields).empty(), "cap-profile fixture field-invalid");
    round_trip(value, caps);

    const auto reject_value = [&] {
        expect_code([&] { (void)am::serialize_conversation_envelope(value, caps); },
            am::ConversationCodecCode::ResourceLimit);
    };
    content.text->push_back('t'); reject_value(); content.text->pop_back();
    content.attachments.push_back(am::ArtifactBindingId{"a"}); reject_value(); content.attachments.pop_back();
    value.metadata.links.push_back(value.metadata.links.front()); reject_value(); value.metadata.links.pop_back();
    content.attachments.front() = am::ArtifactBindingId{std::string(65537, 'a')}; reject_value();
    content.attachments.front() = am::ArtifactBindingId{std::string(65536, static_cast<char>(255))};
    value.metadata.source->evidence.record_locator.push_back('l'); reject_value();
    value.metadata.source->evidence.record_locator.pop_back();
    value.metadata.source->adapter_recipe.push_back('r'); reject_value();
    value.metadata.source->adapter_recipe.pop_back();
    value.extensions.front().bytes.push_back(255); reject_value(); value.extensions.front().bytes.pop_back();
    value.extensions.push_back({1025, false, {}}); reject_value(); value.extensions.pop_back();

    am::ConversationDecodeOptions options;
    options.limits = caps;
    const auto unpadded = am::serialize_conversation_envelope(value, caps);
    // 6 metadata B values + 4096 link elements/B values + 1024 extension
    // elements/B values + one text B + 4096 attachment elements/B values.
    constexpr std::size_t charged_objects = 6 + 2 * 4096 + 2 * 1024 + 1 + 2 * 4096;
    auto exact_object_budget = caps;
    exact_object_budget.max_objects = charged_objects;
    round_trip(value, exact_object_budget);
    --exact_object_budget.max_objects;
    expect_code([&] { (void)am::serialize_conversation_envelope(value, exact_object_budget); },
        am::ConversationCodecCode::ResourceLimit);
    options.limits = exact_object_budget;
    expect_code([&] { (void)am::deserialize_conversation_envelope(view(unpadded), options); },
        am::ConversationCodecCode::ResourceLimit);
    options.limits = caps;
    expect(unpadded.size() < am::kMaxConversationEntryBytes, "conjunctive caps do not fit frame");
    // Scope has no artificial field cap: use it to fill the remaining legal frame bytes.
    value.metadata.scope = am::ScopeId{std::string(1 + am::kMaxConversationEntryBytes - unpadded.size(), '\0')};
    const auto exact = am::serialize_conversation_envelope(value, caps);
    expect(exact.size() == am::kMaxConversationEntryBytes, "cap-profile exact32MiB fixture missed ceiling");
    {
        const auto decoded = am::deserialize_conversation_envelope(view(exact), options);
        expect_envelope(decoded, value);
        expect(am::serialize_conversation_envelope(decoded, caps) == exact, "cap-profile frame not canonical");
    }
    auto scope_plus_one = value.metadata.scope.value();
    scope_plus_one.push_back('\0');
    value.metadata.scope = am::ScopeId{std::move(scope_plus_one)};
    reject_value();

    // A supported resource ceiling is finite, not an implied next supported profile.
    for(const auto dimension : {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10}) {
        auto invalid = caps;
        switch(dimension) {
        case 0: ++invalid.max_frame_bytes; break;
        case 1: ++invalid.max_owned_bytes; break;
        case 2: ++invalid.max_objects; break;
        case 3: ++invalid.max_extensions; break;
        case 4: ++invalid.max_extension_bytes; break;
        case 5: ++invalid.fields.max_text_bytes; break;
        case 6: ++invalid.fields.max_attachments; break;
        case 7: ++invalid.fields.max_links; break;
        case 8: ++invalid.fields.max_binding_bytes; break;
        case 9: ++invalid.fields.max_locator_bytes; break;
        default: ++invalid.fields.max_recipe_bytes;
        }
        expect_code([&] { (void)am::serialize_conversation_envelope(value, invalid); },
            am::ConversationCodecCode::InvalidLimits);
        options.limits = invalid;
        expect_code([&] { (void)am::deserialize_conversation_envelope(view(exact), options); },
            am::ConversationCodecCode::InvalidLimits);
    }
    std::cout << "implementation caps: text8MiB, attachment/link4096, binding/locator65536, recipe4096, "
        "extension1024/8MiB, object-budget32768 and exact32MiB frame passed\n";
}

} // namespace

int main() {
    static_assert(std::is_same_v<decltype(am::serialize_conversation_entry(
        std::declval<const am::ConversationEntry&>())), am::ByteBuffer>, "owning output must be bytes");
    static_assert(!std::is_convertible_v<am::ByteBuffer, am::ByteView>, "temporary buffers must not create views");
    try {
        const auto values = golden_values();
        literal_goldens(values);
        malformed_controls();
        unknown_and_lifetime_controls(values);
        semantic_and_profile_controls(values);
        agent_session_controls(values);
        (void)truncation_and_mutation_controls();
        maximum_controls();
        implementation_cap_controls();
        std::cout << "AMCE envelope v1: " << values.size() << " independent literal goldens passed\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
