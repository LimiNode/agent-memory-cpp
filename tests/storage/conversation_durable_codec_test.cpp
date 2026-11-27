#include <agent_memory/infrastructure/mdbx/ConversationDurableCodecInternal.hpp>
#include <agent_memory/ingestion/ConversationSourceImporterInternal.hpp>

#include <algorithm>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace am = agent_memory;
namespace cd = agent_memory::detail::conversation_durable;
namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
am::ByteView view(const am::ByteBuffer& bytes) { return {bytes.data(), bytes.size()}; }
am::ByteBuffer unhex(std::string_view text) {
    const auto digit = [](char value) -> unsigned {
        if(value >= '0' && value <= '9') return static_cast<unsigned>(value - '0');
        if(value >= 'a' && value <= 'f') return static_cast<unsigned>(value - 'a' + 10);
        throw std::runtime_error("Invalid literal fixture");
    };
    expect(text.size() % 2 == 0, "Odd fixture length");
    am::ByteBuffer result;
    for(std::size_t i = 0; i < text.size(); i += 2)
        result.push_back(static_cast<std::uint8_t>(digit(text[i]) * 16 + digit(text[i + 1])));
    return result;
}
template<typename Function>
void rejected(Function function) {
    try { function(); } catch(const cd::CodecError&) { return; }
    throw std::runtime_error("Invalid physical encoding accepted");
}

am::ConversationRef conversation(std::string name = "a") { return {std::move(name), am::ConversationId{std::uint64_t{1}}}; }
am::EntryRef ref(std::uint64_t entry = 2, std::uint64_t revision = 1) {
    return {conversation(), am::EntryId{entry}, am::EventRevision{revision}};
}
am::ExternalObjectRef composite() {
    return {"p", "s", "d", "k", am::ExternalObjectKey{std::int64_t{-1}},
        {std::vector<am::ExternalIdentityComponent>{
            {"signed", std::numeric_limits<std::int64_t>::min()},
            {"unsigned", std::numeric_limits<std::uint64_t>::max()}, {"text", std::string{"001\0", 4}}}}};
}
am::ConversationSourceObservation observation() {
    am::ConversationSourceObservation value;
    value.conversation = conversation(); value.scope = am::ScopeId{"s"};
    value.source.revision = am::SourceRevisionId{"r"}; value.source.adapter_recipe = "a";
    value.source.evidence = {am::ArtifactBindingId{"e"}, "x"}; value.producer = "p";
    value.payload = am::ConversationSourceMessage{am::ConversationSourceMessageKind::Created, {std::string{"hi"}, {}}};
    return value;
}
cd::Profile profile() {
    cd::Profile value;
    value.allocation_namespace = "a"; value.quotas = {100, 100, 100, 1000000, 100, 1000000};
    for(std::size_t i = 0; i < value.incarnation.size(); ++i) value.incarnation[i] = static_cast<std::uint8_t>(i);
    return value;
}

void key_literals() {
    // Literal grammar independent from all production numeric encoders.
    const auto revision = unhex("0101000000000000000100000000000000020000000000000003");
    expect(cd::encode_revision_key({am::ConversationId{std::uint64_t{1}}, am::EntryId{std::uint64_t{2}},
        am::EventRevision{std::uint64_t{3}}}) == revision, "Revision key golden drift");
    const auto decoded = cd::decode_revision_key(view(revision));
    expect(decoded.conversation.value() == 1 && decoded.entry.value() == 2 && decoded.revision.value() == 3,
        "Revision key decode drift");
    const auto source = unhex("01010000000000000001000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f00000100");
    cd::SourceKey source_key; source_key.conversation = am::ConversationId{std::uint64_t{1}}; source_key.slot = 256;
    for(std::size_t i = 0; i < source_key.digest.size(); ++i) source_key.digest[i] = static_cast<std::uint8_t>(i);
    expect(cd::encode_source_key(source_key) == source, "Source key golden drift");
    const auto parsed = cd::decode_source_key(view(source));
    expect(parsed.digest == source_key.digest && parsed.slot == 256 && parsed.conversation.value() == 1,
        "Source key decode drift");
    expect(cd::encode_profile_key() == unhex("0100"), "Profile key literal drift");
    expect(cd::encode_conversation_key(am::ConversationId{std::uint64_t{1}}) == unhex("01010000000000000001"),
        "Conversation key literal drift");
    expect(cd::encode_generation_key(1) == unhex("01020000000000000001"), "Generation key literal drift");
    am::ByteBuffer previous;
    for(const std::uint64_t value : {std::uint64_t{1}, std::uint64_t{2}, std::uint64_t{10}, std::uint64_t{255},
            std::uint64_t{256}, std::numeric_limits<std::uint64_t>::max()}) {
        const auto wire = cd::encode_generation_key(value);
        expect(previous.empty() || previous < wire, "Numeric key order lost");
        expect(cd::decode_generation_key(view(wire)) == value, "Numeric key boundary drift"); previous = wire;
        const auto all_max = cd::encode_revision_key({am::ConversationId{value}, am::EntryId{value}, am::EventRevision{value}});
        expect(cd::decode_revision_key(view(all_max)).revision.value() == value, "Revision numeric boundary drift");
    }
    for(std::size_t i = 0; i < revision.size(); ++i)
        rejected([&revision, i] { (void)cd::decode_revision_key({revision.data(), i}); });
    for(std::size_t i = 0; i < source.size(); ++i)
        rejected([&source, i] { (void)cd::decode_source_key({source.data(), i}); });
    auto broken = revision; broken.push_back(0); rejected([&broken] { (void)cd::decode_revision_key(view(broken)); });
    broken = revision; broken[0] = 2; rejected([&broken] { (void)cd::decode_revision_key(view(broken)); });
    broken = revision; broken[1] = 3; rejected([&broken] { (void)cd::decode_revision_key(view(broken)); });
    broken = revision; std::fill(broken.begin() + 2, broken.begin() + 10, 0);
    rejected([&broken] { (void)cd::decode_revision_key(view(broken)); });
    rejected([] { (void)cd::decode_revision_key({nullptr, 26}); });
    rejected([] { (void)cd::encode_conversation_key(am::ConversationId{}); });
}

void round_trip_witness(const am::ConversationSourceObservation& value) {
    auto wire = cd::encode_witness(value);
    const auto decoded = cd::decode_witness(view(wire));
    expect(am::detail::same_witness(value, decoded), "Witness differs from shared replay equality");
    expect(cd::encode_witness(decoded) == wire, "Witness canonical reencoding drift");
    const auto saved = wire;
    std::fill(wire.begin(), wire.end(), 255);
    expect(cd::encode_witness(decoded) == saved, "Decoded witness borrows input");
    expect(cd::encode_witness(value, saved.size()) == saved, "Exact witness budget rejected");
    rejected([&value, &saved] { (void)cd::encode_witness(value, saved.size() - 1); });
    rejected([&saved] { (void)cd::decode_witness(view(saved), saved.size() - 1); });
}

void witness_literals_and_presence() {
    auto value = observation();
    const auto literal = unhex("000200016100000000000000010000000173030000000000017200000000016100000001650000000178"
        "0000000170000000000001010100000002686900000000");
    expect(cd::encode_witness(value) == literal, "Independent witness v2 literal drift");
    expect(am::detail::same_witness(value, cd::decode_witness(view(literal))), "Witness v2 literal not decoded exactly");
    for(std::size_t i = 0; i < literal.size(); ++i)
        rejected([&literal, i] { (void)cd::decode_witness({literal.data(), i}); });
    auto broken = literal; broken.push_back(0); rejected([&broken] { (void)cd::decode_witness(view(broken)); });
    // Historical pre-release bytes remain reproducible, but are not an active reader contract.
    const auto historical_v1 = unhex("000100016100000000000000010000000173030000000000017200000000016100000001650000000178"
        "0000000170000000000001010100000002686900000000");
    rejected([&historical_v1] { (void)cd::decode_witness(view(historical_v1)); });
    broken = literal; broken[2] = 255; broken[3] = 255;
    rejected([&broken] { (void)cd::decode_witness(view(broken)); });
    rejected([] { (void)cd::decode_witness({nullptr, 1}); });
    rejected([] { (void)cd::decode_witness({nullptr, 0}); });
    round_trip_witness(value);
    value.scope = am::ScopeId{std::string{"\0\xff", 2}};
    value.source.identity = am::ConversationSourceIdentity::ObjectRevision;
    value.source.object = composite(); value.source.provider_revision_marker = std::string{"\0\xff", 2};
    value.actor = am::ActorRef{std::string(128, 'a'), am::ActorId{std::numeric_limits<std::uint64_t>::max()}};
    value.observer = am::ActorRef{"observer", am::ActorId{std::uint64_t{255}}};
    value.llm_role = am::ConversationLlmRole::Assistant;
    value.source_unix_us = std::numeric_limits<std::int64_t>::min();
    value.observed_unix_us = std::numeric_limits<std::int64_t>::max();
    std::get<am::ConversationSourceMessage>(value.payload).content = {std::nullopt, {am::ArtifactBindingId{"a"}}};
    round_trip_witness(value);
    auto absent = cd::encode_witness(value);
    std::get<am::ConversationSourceMessage>(value.payload).content.text = std::string{};
    auto empty = cd::encode_witness(value);
    expect(absent != empty, "Absent and empty text coalesced"); round_trip_witness(value);
    for(const auto role : {am::ConversationLlmRole::System, am::ConversationLlmRole::Developer,
            am::ConversationLlmRole::User, am::ConversationLlmRole::Assistant, am::ConversationLlmRole::Tool}) {
        value.llm_role = role; round_trip_witness(value);
    }
    value.source.identity = am::ConversationSourceIdentity::ExternalEvent;
    value.source.event = composite(); value.source.provider_revision_marker.reset();
    value.payload = am::ConversationSourceToolCall{{0, 255}, "tool", am::ArtifactBindingId{"original"}, std::nullopt};
    round_trip_witness(value);
    absent = cd::encode_witness(value);
    std::get<am::ConversationSourceToolCall>(value.payload).effective_request = am::ArtifactBindingId{};
    empty = cd::encode_witness(value); expect(absent != empty, "Absent and empty effective request coalesced");
    round_trip_witness(value);
    std::get<am::ConversationSourceToolCall>(value.payload).effective_request = am::ArtifactBindingId{"effective"};
    round_trip_witness(value);

    am::ConversationSourceToolResult result;
    result.call = ref(255, std::numeric_limits<std::uint64_t>::max()); result.call_id = {0, 255};
    value.payload = std::move(result); round_trip_witness(value);
    auto& stored = std::get<am::ConversationSourceToolResult>(value.payload);
    absent = cd::encode_witness(value); stored.is_error = false;
    auto false_wire = cd::encode_witness(value); stored.is_error = true;
    expect(false_wire != absent && false_wire != cd::encode_witness(value), "Absent/false/true coalesced");
    stored.exit_code = std::numeric_limits<std::int64_t>::min(); stored.result = am::ArtifactBindingId{};
    round_trip_witness(value);
    stored.call = am::ConversationSourceTarget{conversation(), am::ScopeId{"target"},
        am::copy_conversation_source_binding(value.source)};
    round_trip_witness(value);
    stored.call = am::UnresolvedConversationTarget{composite(), {am::ArtifactBindingId{"evidence"}, "record"}};
    for(const auto origin : {am::ConversationSourceResultOrigin::Reported, am::ConversationSourceResultOrigin::PermissionDenied,
            am::ConversationSourceResultOrigin::Recovery, am::ConversationSourceResultOrigin::Timeout,
            am::ConversationSourceResultOrigin::Repaired, am::ConversationSourceResultOrigin::Unknown}) {
        stored.origin = origin;
        for(const auto outcome : {am::ConversationToolOutcome::Unknown, am::ConversationToolOutcome::Succeeded,
                am::ConversationToolOutcome::Failed, am::ConversationToolOutcome::Cancelled}) {
            stored.reported_outcome = outcome; round_trip_witness(value);
        }
    }
    stored.exit_code = -1; round_trip_witness(value); stored.exit_code = 0; round_trip_witness(value);
    stored.exit_code = std::numeric_limits<std::int64_t>::max(); round_trip_witness(value);
    stored.origin = static_cast<am::ConversationSourceResultOrigin>(255);
    rejected([&value] { (void)cd::encode_witness(value); });
    value = observation();
    value.payload = am::ConversationEdited{ref(2, 1), {std::string{"edited"}, {am::ArtifactBindingId{"a"}}}};
    round_trip_witness(value);
    value.payload = am::ConversationTombstoned{ref(2, 1)};
    round_trip_witness(value);
    value.payload = am::ConversationRestored{ref(2, 1), ref(2, 1)};
    round_trip_witness(value);
    value = observation(); std::get<am::ConversationSourceMessage>(value.payload).content.text = "\xc0\xaf";
    rejected([&value] { (void)cd::encode_witness(value); });
}

void state_witness_literals() {
    // Independent field-by-field grammar literals, not bytes obtained from the encoder.
    const std::string common = "000200016100000000000000010000000173030000000000017200000000016100000001650000000178"
        "00000001700000000000";
    const std::string target = "000161000000000000000100000000000000020000000000000001";
    auto value = observation();
    const std::vector<std::pair<am::ConversationSourcePayload, std::string>> cases{
        {am::ConversationEdited{ref(), {std::string{"hi"}, {}}}, common + "04" + target + "0100000002686900000000"},
        {am::ConversationTombstoned{ref()}, common + "05" + target},
        {am::ConversationRestored{ref(), ref()}, common + "06" + target + target}};
    for(const auto& item : cases) {
        value.payload = item.first;
        const auto literal = unhex(item.second);
        expect(cd::encode_witness(value) == literal, "Independent state witness v2 literal drift");
        expect(am::detail::same_witness(value, cd::decode_witness(view(literal))), "State literal decode drift");
        for(std::size_t i = 0; i < literal.size(); ++i)
            rejected([&literal, i] { (void)cd::decode_witness({literal.data(), i}); });
    }
    auto unknown = unhex(common + "07");
    rejected([&unknown] { (void)cd::decode_witness(view(unknown)); });
}

void logical_keys() {
    auto value = observation();
    const auto literal = unhex("000103000000017200000001650000000178");
    expect(cd::encode_logical_source_key(value.source) == literal, "Local source key golden drift");
    cd::validate_logical_source_key(view(literal));
    for(const auto identity : {am::ConversationSourceIdentity::LocalObservation, am::ConversationSourceIdentity::ObjectRevision,
            am::ConversationSourceIdentity::ExternalEvent}) {
        value.source.identity = identity;
        if(identity != am::ConversationSourceIdentity::LocalObservation) value.source.object = composite();
        if(identity == am::ConversationSourceIdentity::ObjectRevision) value.source.provider_revision_marker = std::string{"\0\xff", 2};
        if(identity == am::ConversationSourceIdentity::ExternalEvent) { value.source.event = composite(); value.source.provider_revision_marker.reset(); }
        const auto key = cd::encode_logical_source_key(value.source);
        cd::validate_logical_source_key(view(key));
        const auto before = am::detail::source_key(value.source);
        value.source.adapter_recipe = "different";
        const auto after = am::detail::source_key(value.source);
        expect(!(before < after) && !(after < before), "Shared source identity unexpectedly includes recipe");
        expect(cd::encode_logical_source_key(value.source) == key, "Physical source key unexpectedly includes recipe");
        value.source.revision = am::SourceRevisionId{"changed"};
        expect(cd::encode_logical_source_key(value.source) != key, "Source revision omitted from identity");
        value.source.revision = am::SourceRevisionId{"r"};
    }
    auto broken = literal; broken[2] = 255; rejected([&broken] { cd::validate_logical_source_key(view(broken)); });
    broken = literal; broken.push_back(0); rejected([&broken] { cd::validate_logical_source_key(view(broken)); });
}

void descriptor_profile_values() {
    auto p = profile();
    const auto descriptor = cd::encode_descriptor(p.descriptor);
    expect(descriptor.size() == 122 && descriptor[0] == 0 && descriptor[1] == 1, "Descriptor fixed grammar drift");
    expect(cd::encode_descriptor(cd::decode_descriptor(view(descriptor))) == descriptor, "Descriptor round trip drift");
    auto body = cd::encode_profile(p);
    expect(cd::encode_profile(cd::decode_profile(view(body))) == body, "Profile round trip drift");
    p.allocation_namespace = std::string(128, 'a'); body = cd::encode_profile(p);
    expect(cd::decode_profile(view(body)).allocation_namespace == p.allocation_namespace, "Maximum namespace rejected");
    p.allocation_namespace.push_back('a'); rejected([&p] { (void)cd::encode_profile(p); }); p = profile();
    p.quotas = {0, 0, 0, 0, 0, 0}; body = cd::encode_profile(p); (void)cd::decode_profile(view(body));
    p.quotas.entries = std::numeric_limits<std::uint64_t>::max(); rejected([&p] { (void)cd::encode_profile(p); });
    auto broken = descriptor; broken[1] = 2; rejected([&broken] { (void)cd::decode_descriptor(view(broken)); });
    broken = descriptor; broken.push_back(0); rejected([&broken] { (void)cd::decode_descriptor(view(broken)); });
    broken = descriptor; std::fill(broken.begin() + 2, broken.begin() + 10, 255);
    rejected([&broken] { (void)cd::decode_descriptor(view(broken)); });
    p = profile(); p.descriptor.bucket_rows = 257; rejected([&p] { (void)cd::encode_descriptor(p.descriptor); });
}

void rows_and_integrity() {
    const auto p = profile(); const auto profile_key = cd::encode_profile_key();
    const auto profile_body = cd::encode_profile(p);
    const auto profile_value = cd::wrap_value(p, cd::TableKind::Metadata, view(profile_key), view(profile_body));
    expect(cd::encode_profile(cd::decode_profile_value(view(profile_value))) == profile_body, "Profile bootstrap drift");
    const auto conversation_key = cd::encode_conversation_key(am::ConversationId{std::uint64_t{1}});
    const auto row_body = cd::encode_conversation_row({am::ScopeId{std::string{"s\0\xff", 3}}, 1,
        std::numeric_limits<std::uint64_t>::max()});
    auto value = cd::wrap_value(p, cd::TableKind::Metadata, view(conversation_key), view(row_body));
    // Independently SHA-256-qualified literal of the published digest preimage grammar.
    expect(value == unhex("000100000017000000037300ff0000000000000001ffffffffffffffff"
        "44db31d70fa4092b25e452445f48b8450891054325c02217f9ed66a811b7a2fa"), "Value/digest golden drift");
    const auto historical_v1 = unhex("000100000017000000037300ff0000000000000001ffffffffffffffff"
        "2999521a7d9cf7db171945716315bdb38b18b3d900020ef1bd22197d25af8d24");
    rejected([&] { (void)cd::unwrap_value(p, cd::TableKind::Metadata, view(conversation_key), view(historical_v1)); });
    const auto borrowed = cd::unwrap_value(p, cd::TableKind::Metadata, view(conversation_key), view(value));
    expect(borrowed.data == value.data() + 6 && borrowed.size == row_body.size(), "Value body must borrow without a copy");
    const auto row = cd::decode_conversation_row(borrowed);
    expect(row.scope.value() == std::string{"s\0\xff", 3} && row.next_entry == std::numeric_limits<std::uint64_t>::max(),
        "Conversation scope/sentinel drift");
    for(std::size_t i = 0; i < value.size(); ++i) {
        auto broken = value; broken[i] ^= 1;
        rejected([&p, &conversation_key, &broken] {
            (void)cd::unwrap_value(p, cd::TableKind::Metadata, view(conversation_key), view(broken));
        });
    }
    for(std::size_t i = 0; i < value.size(); ++i)
        rejected([&p, &conversation_key, &value, i] {
            (void)cd::unwrap_value(p, cd::TableKind::Metadata, view(conversation_key), {value.data(), i});
        });
    auto foreign = p; foreign.allocation_namespace = "b";
    rejected([&foreign, &conversation_key, &value] {
        (void)cd::unwrap_value(foreign, cd::TableKind::Metadata, view(conversation_key), view(value));
    });
    foreign = p; foreign.incarnation[0] ^= 1;
    rejected([&foreign, &conversation_key, &value] {
        (void)cd::unwrap_value(foreign, cd::TableKind::Metadata, view(conversation_key), view(value));
    });
    const auto wrong_key = cd::encode_conversation_key(am::ConversationId{std::uint64_t{2}});
    rejected([&p, &wrong_key, &value] { (void)cd::unwrap_value(p, cd::TableKind::Metadata, view(wrong_key), view(value)); });
    rejected([&p, &conversation_key, &value] {
        (void)cd::unwrap_value(p, cd::TableKind::Events, view(conversation_key), view(value));
    });
    expect(cd::wrap_value(p, cd::TableKind::Metadata, view(conversation_key), view(row_body), value.size()) == value,
        "Exact value budget rejected");
    rejected([&p, &conversation_key, &row_body, &value] {
        (void)cd::wrap_value(p, cd::TableKind::Metadata, view(conversation_key), view(row_body), value.size() - 1);
    });
    cd::RevisionRow revision{1, std::nullopt, {0, 255}};
    const auto revision_body = cd::encode_revision_row(revision);
    expect(revision_body == unhex("0000000000000001000000000200ff"), "Revision body golden drift");
    const auto parsed = cd::decode_revision_row(view(revision_body));
    expect(parsed.frame == revision.frame && parsed.generation == 1 && !parsed.receipt, "Revision round trip drift");
    revision.receipt = cd::CorrectionReceipt{am::EventRevision{std::uint64_t{1}}, "fix.reason"};
    expect(cd::encode_revision_row(cd::decode_revision_row(view(cd::encode_revision_row(revision)))) ==
        cd::encode_revision_row(revision), "Correction receipt round trip drift");
    const auto original = observation();
    cd::SourceRow source{cd::encode_logical_source_key(original.source), cd::encode_witness(original), ref(), 1};
    const auto source_body = cd::encode_source_row(source); const auto copied = cd::decode_source_row(view(source_body));
    expect(copied.key == source.key && copied.witness == source.witness && copied.ref == source.ref && copied.generation == 1,
        "Source row round trip drift");
    for(const auto kind : {cd::GenerationKind::Create, cd::GenerationKind::Import, cd::GenerationKind::Correction}) {
        cd::GenerationRow row{kind, conversation(), std::nullopt};
        if(kind != cd::GenerationKind::Create) row.entry = std::make_pair(am::EntryId{std::uint64_t{2}}, am::EventRevision{std::uint64_t{1}});
        const auto encoded = cd::encode_generation_row(row); const auto decoded = cd::decode_generation_row(view(encoded));
        expect(decoded.kind == row.kind && decoded.conversation == row.conversation && decoded.entry == row.entry,
            "Generation row round trip drift");
    }
}

void maximum_fields() {
    auto value = observation();
    value.source.revision = am::SourceRevisionId{std::string(65536, 'r')};
    value.source.evidence.artifact = am::ArtifactBindingId{std::string(65536, 'e')};
    value.source.evidence.record_locator = std::string(65536, 'l'); value.source.adapter_recipe = std::string(4096, 'a');
    auto& content = std::get<am::ConversationSourceMessage>(value.payload).content;
    content.text = std::string(8U * 1024U * 1024U, 'x');
    content.attachments.assign(4096, am::ArtifactBindingId{"a"}); round_trip_witness(value);
    content.attachments.push_back(am::ArtifactBindingId{"a"}); rejected([&value] { (void)cd::encode_witness(value); });
    content.attachments.pop_back(); content.text->push_back('x'); rejected([&value] { (void)cd::encode_witness(value); });
    rejected([&value] { (void)cd::encode_witness(value, std::numeric_limits<std::size_t>::max()); });
    rejected([] { (void)cd::decode_witness({nullptr, std::numeric_limits<std::size_t>::max()}); });
}
} // namespace

int main() {
    try {
        key_literals(); witness_literals_and_presence(); state_witness_literals(); logical_keys(); descriptor_profile_values();
        rows_and_integrity(); maximum_fields();
        std::cout << "durable private keys/value integrity/profile/witness fixtures: PASS\n";
        return 0;
    } catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
