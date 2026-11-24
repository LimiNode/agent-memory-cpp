#include <agent_memory/domain/conversation_codec.hpp>
#include <agent_memory/domain/Sha256.hpp>

#include <algorithm>
#include <cstdint>
#include <iostream>
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

std::string unhex(std::string_view text) {
    const auto digit = [](char c) -> unsigned {
        if(c >= '0' && c <= '9') return static_cast<unsigned>(c - '0');
        if(c >= 'a' && c <= 'f') return static_cast<unsigned>(c - 'a' + 10);
        throw std::runtime_error("bad literal golden hex");
    };
    expect(text.size() % 2 == 0, "odd literal golden hex");
    std::string result;
    result.reserve(text.size() / 2);
    for(std::size_t i = 0; i < text.size(); i += 2)
        result.push_back(static_cast<char>(digit(text[i]) * 16 + digit(text[i + 1])));
    return result;
}

std::string digest_hex(std::string_view bytes) {
    constexpr char digits[] = "0123456789abcdef";
    std::string result;
    for(const auto byte : am::sha256(bytes)) {
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 15]);
    }
    return result;
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

bool same_payload(const am::ConversationPayload& a, const am::ConversationPayload& b) {
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

void expect_entry(const am::ConversationEntry& a, const am::ConversationEntry& b) {
    expect(am::validate_conversation_entry(a).empty(), "decoded entry is field-invalid");
    expect(a.ref == b.ref && a.scope == b.scope && a.actor == b.actor && a.llm_role == b.llm_role &&
        a.observer == b.observer && a.source_unix_us == b.source_unix_us &&
        a.observed_unix_us == b.observed_unix_us, "entry identity or optional metadata changed");
    expect(a.source.has_value() == b.source.has_value(), "source presence changed");
    if(a.source) expect(same_source(*a.source, *b.source), "source evidence or identity changed");
    expect(same_payload(a.payload, b.payload), "typed payload changed");
    expect(a.links.size() == b.links.size(), "link count changed");
    for(std::size_t i = 0; i < a.links.size(); ++i) {
        const auto& left = a.links[i];
        const auto& right = b.links[i];
        expect(left.kind == right.kind, "link kind/order changed");
        if(const auto* target = std::get_if<am::EntryRef>(&left.target)) {
            const auto* other = std::get_if<am::EntryRef>(&right.target);
            expect(other && *target == *other, "resolved exact target changed");
        } else {
            const auto& unresolved = std::get<am::UnresolvedConversationTarget>(left.target);
            const auto* other = std::get_if<am::UnresolvedConversationTarget>(&right.target);
            expect(other && unresolved.external == other->external &&
                same_evidence(unresolved.evidence, other->evidence), "unresolved target evidence changed");
        }
    }
}

void round_trip(const am::ConversationEntry& entry) {
    expect(am::validate_conversation_entry(entry).empty(), "round-trip fixture is field-invalid");
    const auto bytes = am::serialize_conversation_entry(entry);
    const auto decoded = am::deserialize_conversation_entry(bytes);
    expect_entry(decoded, entry);
    expect(am::serialize_conversation_entry(decoded) == bytes, "accepted bytes are noncanonical");
}

struct Golden final {
    const char* name;
    am::ConversationEntry value;
    const char* hex;
    const char* sha256;
};

std::vector<Golden> goldens() {
    // Literal wire bytes and independent hashlib SHA-256, authored from section 4.2
    // before inspecting the serializer. Offline reproduction: fixtures/conversation_codec_goldens.py.
    auto snapshot = message();
    snapshot.source = source();
    snapshot.payload = am::ConversationSnapshotObserved{{std::string{"snapshot"}, {am::ArtifactBindingId{"a"}}}};

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

    auto revision = message("revision");
    revision.source = source();
    revision.source->identity = am::ConversationSourceIdentity::ObjectRevision;
    revision.source->object = external();
    revision.source->revision = am::SourceRevisionId{unhex("ff0072")};
    revision.source->provider_revision_marker = unhex("ff006d");
    revision.source->evidence = {am::ArtifactBindingId{unhex("ff0062")}, unhex("6c00c3a9")};

    auto edited = message();
    edited.payload = am::ConversationEdited{reference(1, 9), {std::string{"edit"},
        {am::ArtifactBindingId{"x"}, am::ArtifactBindingId{"y"}, am::ArtifactBindingId{"x"}}}};
    auto tombstone = message();
    tombstone.payload = am::ConversationTombstoned{reference(1, 9)};
    tombstone.llm_role = am::ConversationLlmRole::Tool;
    auto restored = message();
    restored.payload = am::ConversationRestored{reference(1, 9), reference(1, 7)};

    auto linked = message("links");
    linked.links = {
        {am::ConversationLinkKind::Forward, reference(44, 8, 99, "z")},
        {am::ConversationLinkKind::Quote, reference(45, 9, 99, "z")},
        {am::ConversationLinkKind::Reply,
            am::UnresolvedConversationTarget{std::nullopt, {am::ArtifactBindingId{"b"}, "l"}}},
        {am::ConversationLinkKind::Thread, reference(1, 2)},
        {am::ConversationLinkKind::Branch, reference(46, 10, 99, "z")}
    };

    auto attached = message();
    attached.payload = am::ConversationMessageCreated{{std::nullopt,
        {am::ArtifactBindingId{unhex("7800ff")}, am::ArtifactBindingId{"y"}}}};
    attached.actor = event.actor;
    attached.llm_role = am::ConversationLlmRole::System;
    attached.observer = event.observer;
    attached.source_unix_us = std::numeric_limits<std::int64_t>::min();
    attached.observed_unix_us = std::numeric_limits<std::int64_t>::max();

    const auto maximum = std::numeric_limits<std::uint64_t>::max();
    auto boundaries = message(unhex("d0af00c3a9"));
    boundaries.ref = reference(maximum, maximum, maximum);
    boundaries.scope = am::ScopeId{unhex("00ff73")};
    std::get<am::ConversationMessageCreated>(boundaries.payload).content.attachments =
        {am::ArtifactBindingId{unhex("00ff")}};
    boundaries.actor = am::ActorRef{"a", am::ActorId{maximum}};
    boundaries.llm_role = am::ConversationLlmRole::Developer;
    boundaries.observer = am::ActorRef{"o", am::ActorId{maximum}};
    boundaries.source_unix_us = std::numeric_limits<std::int64_t>::max();
    boundaries.observed_unix_us = std::numeric_limits<std::int64_t>::min();
    auto empty_text = message("");
    std::get<am::ConversationMessageCreated>(empty_text.payload).content.attachments = {am::ArtifactBindingId{"x"}};

    return {
        {"minimal", message(),
            "414d4345000101000000016100000000000000010000000000000002000000000000000302000000017303000400050006000700"
            "0800090101000000017400000a0000",
            "2f67653e50f37e00e3bd406925596c8f2c6ef40505220844967dfa2a258dc0c0"},
        {"snapshot", std::move(snapshot),
            "414d4345000101000000016100000000000000010000000000000002000000000000000302000000017303010300000000000172"
            "0000000001760000000162000000016c0400050006000700080009020100000008736e617073686f74000100000001610a0000",
            "3a11475ba700aee55ada78b5581af34a55c7e0e45191a49ad25a9fb5b4c08f2b"},
        {"external_event", std::move(event),
            "414d434500010100000001610000000000000001000000000000000200000000000000030200000001730301010100000035414d"
            "58520001000000017000000001730000000164000000016b010100000008ffffffffffffffff0200000008000000000000002a01"
            "00000035414d585200010000000170000000017300000001640000000165010100000008fffffffffffffffe0200000008000000"
            "000000000d00000001720000000001760000000162000000016c0401000000016100000000000000070501040601000000016f00"
            "000000000000080701ffffffffffffffff080100000000000000000901010000000865787465726e616c00000a0000",
            "97cd8dfcd02dc9303c5a2e3015df3634708cfb838527fe56c877d1ca81db00be"},
        {"object_revision", std::move(revision),
            "414d434500010100000001610000000000000001000000000000000200000000000000030200000001730301020100000035414d"
            "58520001000000017000000001730000000164000000016b010100000008ffffffffffffffff0200000008000000000000002a00"
            "00000003ff00720100000003ff006d000000017600000003ff0062000000046c00c3a90400050006000700080009010100000008"
            "7265766973696f6e00000a0000",
            "6f92257c49a20a4958a2d300f9e6098da78c62db995495191e3ee4953e14f9c1"},
        {"edit", std::move(edited),
            "414d4345000101000000016100000000000000010000000000000002000000000000000302000000017303000400050006000700"
            "080009030000000161000000000000000100000000000000010000000000000009010000000465646974000300000001780000"
            "00017900000001780a0000",
            "74cdb40606f6db1b061ba75a1c1a7a689479677ff2b788941197587af1016207"},
        {"tombstone", std::move(tombstone),
            "414d4345000101000000016100000000000000010000000000000002000000000000000302000000017303000400050105060007"
            "000800090400000001610000000000000001000000000000000100000000000000090a0000",
            "4847fc09921a293eb5117e92a07d21b343d060226309ab5fe66d0b270c0444bc"},
        {"restore", std::move(restored),
            "414d4345000101000000016100000000000000010000000000000002000000000000000302000000017303000400050006000700"
            "080009050000000161000000000000000100000000000000010000000000000009000000016100000000000000010000000000"
            "00000100000000000000070a0000",
            "384dad921be55b8886c58d2da4cf8bbb38a10f2cc5f577577ffd2d885ffaa620"},
        {"cross_links", std::move(linked),
            "414d4345000101000000016100000000000000010000000000000002000000000000000302000000017303000400050006000700"
            "0800090101000000056c696e6b7300000a00050401000000017a0000000000000063000000000000002c00000000000000080501"
            "000000017a0000000000000063000000000000002d00000000000000090102000000000162000000016c02010000000161000000"
            "0000000001000000000000000100000000000000020301000000017a0000000000000063000000000000002e000000000000000a",
            "92f8b6aab1accbbbe7fe4223d169f32e4299ec9d60e478b206abb17901e0b4b3"},
        {"attachment_only", std::move(attached),
            "414d4345000101000000016100000000000000010000000000000002000000000000000302000000017303000401000000016100"
            "000000000000070501010601000000016f00000000000000080701800000000000000008017fffffffffffffff09010000020000"
            "00037800ff00000001790a0000",
            "741dc13eddd3ff535a5b06d4b884ec043bbc34bc9d434396f4d25c53d6397fd6"},
        {"boundaries", std::move(boundaries),
            "414d43450001010000000161ffffffffffffffffffffffffffffffffffffffffffffffff020000000300ff730300040100000001"
            "61ffffffffffffffff0501020601000000016fffffffffffffffff07017fffffffffffffff080180000000000000000901010000"
            "0005d0af00c3a900010000000200ff0a0000",
            "13b70d9c7720f5a50a037a16571848ea03e5348cef69ad80c9b236cb58e6dc28"},
        {"present_empty", std::move(empty_text),
            "414d4345000101000000016100000000000000010000000000000002000000000000000302000000017303000400050006000700"
            "080009010100000000000100000001780a0000",
            "b8e57348aba25f2662e652d4523354bad2825cd7c8e8635419501940effa2df1"}
    };
}

void literal_goldens(const std::vector<Golden>& values) {
    for(const auto& golden : values) {
        try {
            const auto expected = unhex(golden.hex);
            expect(digest_hex(expected) == golden.sha256, "literal independent SHA-256 mismatch");
            expect(am::validate_conversation_entry(golden.value).empty(), "golden DTO is field-invalid");
            expect(am::serialize_conversation_entry(golden.value) == expected, "literal encoding mismatch");
            const auto decoded = am::deserialize_conversation_entry(expected);
            expect_entry(decoded, golden.value);
            expect(am::serialize_conversation_entry(decoded) == expected, "literal re-encoding mismatch");
        } catch(const std::exception& error) {
            throw std::runtime_error(std::string{"golden "} + golden.name + ": " + error.what());
        }
    }
}

void rejected_bytes(std::string_view bytes) {
    try { (void)am::deserialize_conversation_entry(bytes); }
    catch(const std::invalid_argument&) { return; }
    throw std::runtime_error("malformed event bytes decoded");
}

void rejected_value(const am::ConversationEntry& value) {
    try { (void)am::serialize_conversation_entry(value); }
    catch(const std::invalid_argument&) { return; }
    throw std::runtime_error("invalid/oversized event DTO encoded");
}

void set_uint(std::string& bytes, std::size_t offset, std::uint64_t value, unsigned width) {
    expect(offset <= bytes.size() && width <= bytes.size() - offset, "bad mutation offset");
    for(unsigned i = 0; i < width; ++i)
        bytes[offset + i] = static_cast<char>((value >> (8U * (width - i - 1U))) & 0xffU);
}

void malformed_controls(const std::vector<Golden>& values) {
    const auto minimal = unhex(values[0].hex);
    expect(minimal.size() == 67, "minimal fixture offsets changed");
    auto changed = minimal;
    changed[0] = 'X';
    rejected_bytes(changed);
    for(const auto version : {std::uint64_t{0}, std::uint64_t{2}, std::uint64_t{65535}}) {
        changed = minimal; set_uint(changed, 4, version, 2); rejected_bytes(changed);
    }
    for(const auto offset : {6U, 36U, 42U, 44U, 46U, 48U, 50U, 52U, 54U, 64U}) {
        for(const auto byte : {0U, 255U}) {
            changed = minimal; changed[offset] = static_cast<char>(byte); rejected_bytes(changed);
        }
    }
    changed = minimal; changed[46] = '\4'; rejected_bytes(changed); // Duplicate actor tag.
    changed = minimal; std::swap(changed[44], changed[46]); rejected_bytes(changed);
    changed = minimal; changed.insert(46, unhex("0400")); rejected_bytes(changed);
    changed = minimal; changed.erase(44, 2); rejected_bytes(changed);
    for(const auto offset : {43U, 45U, 47U, 49U, 51U, 53U, 56U}) {
        for(const auto marker : {2U, 255U}) {
            changed = minimal; changed[offset] = static_cast<char>(marker); rejected_bytes(changed);
        }
    }
    for(const auto tag : {0U, 6U, 255U}) {
        changed = minimal; changed[55] = static_cast<char>(tag); rejected_bytes(changed);
    }
    for(const auto offset : {7U, 37U, 57U}) {
        changed = minimal; set_uint(changed, offset, 0xffffffffU, 4); rejected_bytes(changed);
    }
    changed = minimal; set_uint(changed, 7, 129, 4); rejected_bytes(changed);
    changed = minimal; set_uint(changed, 37, 65537, 4); rejected_bytes(changed);
    changed = minimal; set_uint(changed, 57, 1048577, 4); rejected_bytes(changed);
    for(const auto offset : {62U, 65U}) {
        for(const auto count : {std::uint64_t{256}, std::uint64_t{257}, std::uint64_t{65535}}) {
            changed = minimal; set_uint(changed, offset, count, 2); rejected_bytes(changed);
        }
    }
    changed = minimal; changed[11] = 'A'; rejected_bytes(changed);
    changed = minimal; changed[61] = static_cast<char>(0xff); rejected_bytes(changed);
    changed = minimal; set_uint(changed, 12, 0, 8); rejected_bytes(changed);
    changed = minimal; changed.push_back('\0'); rejected_bytes(changed);
    changed = minimal; changed += minimal; rejected_bytes(changed);

    const auto event = unhex(values[2].hex);
    const auto nested = event.find("AMXR");
    expect(nested != std::string::npos && nested >= 4, "nested AMXR fixture missing");
    changed = event; set_uint(changed, nested - 4, 65537, 4); rejected_bytes(changed);
    changed = event; set_uint(changed, nested - 4, 0xffffffffU, 4); rejected_bytes(changed);
    changed = event; changed[nested + 5] = '\2'; rejected_bytes(changed);
    changed = event; changed[nested + 27] = static_cast<char>(255); rejected_bytes(changed);
    changed = event; changed[44] = '\2'; rejected_bytes(changed); // ObjectRevision cannot carry event.
    auto snapshot = unhex(values[1].hex);
    snapshot[44] = '\1'; rejected_bytes(snapshot); // ExternalEvent without event key.
    snapshot[44] = '\2'; rejected_bytes(snapshot); // ObjectRevision without object/marker.
    const auto local_snapshot = unhex(values[1].hex);
    for(const auto offset : {45U, 46U, 52U}) {
        changed = local_snapshot; changed[offset] = '\2'; rejected_bytes(changed);
    }
    for(const auto tag : {0U, 4U, 255U}) {
        changed = local_snapshot; changed[44] = static_cast<char>(tag); rejected_bytes(changed);
    }
    for(const auto offset : {47U, 58U, 63U}) {
        changed = local_snapshot; set_uint(changed, offset, 4097, 4); rejected_bytes(changed);
    }
    changed = local_snapshot; set_uint(changed, 53, 1025, 4); rejected_bytes(changed);
    for(const auto offset : {57U, 67U}) {
        changed = local_snapshot; changed[offset] = static_cast<char>(255); rejected_bytes(changed);
    }
    const auto object_revision = unhex(values[3].hex);
    const auto marker = object_revision.find(unhex("0100000003ff006d"));
    expect(marker != std::string::npos, "provider marker fixture missing");
    changed = object_revision; changed[marker] = '\2'; rejected_bytes(changed);
    changed = object_revision; set_uint(changed, marker + 1, 4097, 4); rejected_bytes(changed);
    const auto present_empty = unhex(values[10].hex);
    changed = present_empty; set_uint(changed, 63, 4097, 4); rejected_bytes(changed);

    const auto links = unhex(values[7].hex);
    const auto link_start = links.find(unhex("0a00050401"));
    expect(link_start != std::string::npos, "link fixture marker missing");
    changed = links; changed[link_start + 3] = static_cast<char>(255); rejected_bytes(changed);
    changed = links; changed[link_start + 4] = '\3'; rejected_bytes(changed);
    const auto unresolved = links.find(unhex("0102000000000162000000016c"));
    expect(unresolved != std::string::npos, "unresolved evidence fixture missing");
    changed = links; changed[unresolved + 2] = '\2'; rejected_bytes(changed);
    changed = unhex(values[4].hex); set_uint(changed, 69, 2, 8); rejected_bytes(changed); // Self edit.
    const auto attached = unhex(values[8].hex);
    const auto role_start = attached.find(unhex("0501010601"));
    expect(role_start != std::string::npos, "role fixture marker missing");
    changed = attached; changed[role_start + 2] = '\0'; rejected_bytes(changed);
    rejected_bytes(std::string(33554433U, '\0'));
}

void semantic_and_presence_controls(const std::vector<Golden>& values) {
    auto entry = message();
    entry.scope = am::ScopeId{std::string(65537, '\0')};
    expect(am::validate_conversation_entry(entry).empty(), "codec scope cap leaked into Phase 1");
    rejected_value(entry);
    entry = message(); entry.ref.entry = am::EntryId{}; rejected_value(entry);
    entry = message(); entry.scope = am::ScopeId{}; rejected_value(entry);
    entry = message(); entry.llm_role = static_cast<am::ConversationLlmRole>(255); rejected_value(entry);
    entry = message(); entry.payload = am::ConversationSnapshotObserved{{std::string{"state"}, {}}};
    rejected_value(entry);
    entry = message(); entry.source = source(); entry.source->identity = am::ConversationSourceIdentity::ExternalEvent;
    rejected_value(entry);
    entry = message(); entry.source = source(); entry.source->provider_revision_marker = "invented";
    rejected_value(entry);
    entry = message(); entry.source = source(); entry.source->adapter_recipe.assign(1025, 'r');
    rejected_value(entry);
    entry = message(); entry.source = source(); entry.source->revision = am::SourceRevisionId{std::string(4097, 'r')};
    rejected_value(entry);
    entry = message(); entry.source = source(); entry.source->evidence.record_locator.assign(4097, 'l');
    rejected_value(entry);
    entry = message(); entry.source = source();
    entry.source->evidence.artifact = am::ArtifactBindingId{std::string(4097, 'b')}; rejected_value(entry);
    entry = message(); entry.payload = am::ConversationEdited{entry.ref, {std::string{"edit"}, {}}};
    rejected_value(entry);
    entry = message(); entry.links = {{am::ConversationLinkKind::Reply, reference(1, 1, 2)}};
    rejected_value(entry);
    entry = message(); entry.links.resize(257); rejected_value(entry);
    entry = message();
    std::get<am::ConversationMessageCreated>(entry.payload).content.attachments.resize(257, am::ArtifactBindingId{"a"});
    rejected_value(entry);
    entry = message(std::string(1048577, 'x')); rejected_value(entry);
    entry = message(std::string("\xc0\x80", 2)); rejected_value(entry);

    auto empty = values[10].value;
    auto absent = empty;
    std::get<am::ConversationMessageCreated>(absent.payload).content.text.reset();
    expect(am::serialize_conversation_entry(empty) != am::serialize_conversation_entry(absent),
        "absent and present empty text collided");
    round_trip(empty); round_trip(absent);
    for(const auto role : {am::ConversationLlmRole::System, am::ConversationLlmRole::Developer,
                          am::ConversationLlmRole::User, am::ConversationLlmRole::Assistant,
                          am::ConversationLlmRole::Tool}) {
        entry = message(); entry.llm_role = role; round_trip(entry);
    }
    // A snapshot may legitimately have reliable ObjectRevision or ExternalEvent evidence.
    for(const auto index : {2U, 3U}) {
        entry = values[index].value;
        entry.payload = am::ConversationSnapshotObserved{{std::string{"state"}, {}}};
        round_trip(entry);
    }
    entry = values[7].value;
    entry.links.push_back(entry.links.front());
    round_trip(entry); // Ordered duplicate links are retained, not canonicalized away.
    entry.links.back().target = am::UnresolvedConversationTarget{external(), source().evidence};
    round_trip(entry);
}

void truncation_controls(const std::vector<Golden>& values) {
    for(const auto& golden : values) {
        const auto bytes = unhex(golden.hex);
        for(std::size_t length = 0; length < bytes.size(); ++length)
            rejected_bytes(std::string_view{bytes.data(), length});
    }
}

std::size_t mutation_controls(const std::vector<Golden>& values) {
    std::size_t accepted = 0;
    std::size_t rejected = 0;
    const auto exercise = [&](const std::string& bytes) {
        std::optional<am::ConversationEntry> decoded;
        try {
            decoded = am::deserialize_conversation_entry(bytes);
        } catch(const std::invalid_argument&) { ++rejected; return; }
        expect(am::validate_conversation_entry(*decoded).empty(), "mutation decoded field-invalid DTO");
        expect(am::serialize_conversation_entry(*decoded) == bytes, "mutation accepted noncanonical bytes");
        ++accepted;
    };
    for(const auto& golden : values) {
        const auto bytes = unhex(golden.hex);
        for(std::size_t i = 0; i < bytes.size(); ++i) {
            for(const auto byte : {0U, 1U, 2U, 127U, 128U, 255U}) {
                auto changed = bytes;
                changed[i] = static_cast<char>(byte);
                exercise(changed);
            }
        }
    }
    std::uint32_t state = 0x63776d31U;
    const auto random = [&]() {
        state ^= state << 13; state ^= state >> 17; state ^= state << 5;
        return state;
    };
    for(std::size_t i = 0; i < 4096; ++i) {
        auto bytes = unhex(values[random() % values.size()].hex);
        const auto offset = static_cast<std::size_t>(random()) % bytes.size();
        if(i % 3 == 0) bytes.erase(offset, 1);
        else if(i % 3 == 1) bytes.insert(offset, 1, static_cast<char>(random() & 255U));
        else bytes[offset] = static_cast<char>(random() & 255U);
        exercise(bytes);
    }
    expect(accepted > 0 && rejected > 0, "mutation corpus did not exercise both dispositions");
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
    expect(bytes > 65536 && bytes - 65536 < tail.size(), "bad maximal AMXR size calculation");
    tail.resize(tail.size() - (bytes - 65536));
    result.object_key = am::ExternalObjectKey{std::move(parts)};
    expect(am::is_valid_external_object_ref(result), "maximal AMXR fixture is invalid");
    expect(am::serialize_external_object_ref(result).size() == 65536, "maximal AMXR frame size changed");
    return result;
}

void combined_maxima() {
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
    entry.source->evidence = {am::ArtifactBindingId{std::string(4096, static_cast<char>(255))},
        std::string(4096, 'l')};
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
    expect(am::validate_conversation_entry(entry).empty(), "combined maxima are not Phase 1-valid");
    const auto bytes = am::serialize_conversation_entry(entry);
    expect(bytes.size() == 21186977, "combined exact maximum-size calculation changed");
    const auto decoded = am::deserialize_conversation_entry(bytes);
    expect_entry(decoded, entry);
    expect(am::serialize_conversation_entry(decoded) == bytes, "combined maxima not canonical");
}

} // namespace

int main() {
    try {
        const auto values = goldens();
        literal_goldens(values);
        malformed_controls(values);
        semantic_and_presence_controls(values);
        truncation_controls(values);
        const auto mutations = mutation_controls(values);
        combined_maxima();
        std::cout << "AMCE v1: " << values.size() << " literal goldens, all compact truncations, "
                  << mutations << " bounded mutations, combined maximum 21186977 bytes passed\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
