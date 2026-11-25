#include <agent_memory/domain/conversation_codec.hpp>

#include "../support/allocation_failure.hpp"
#include <type_traits>

namespace am = agent_memory;
namespace {
static_assert(std::is_aggregate_v<am::ExternalIdentityComponent>);
static_assert(std::is_aggregate_v<am::ExternalObjectKey>);
static_assert(std::is_aggregate_v<am::ExternalObjectRef>);
static_assert(std::is_same_v<am::ExternalIdentityScalar,
    std::variant<std::int64_t, std::uint64_t, std::string>>);

void expect(bool value, const char* text) { if(!value) throw std::runtime_error(text); }
am::ExternalObjectKey composite() {
    std::vector<am::ExternalIdentityComponent> parts;
    parts.push_back({std::string(48, 'a'), std::int64_t{-1}});
    parts.push_back({std::string(48, 'b'), std::uint64_t{7}});
    parts.push_back({std::string(48, 'c'), std::string(160, 'k')});
    return {std::move(parts)};
}
am::ExternalObjectRef external() {
    return {"fixture", std::string(48, 'i'), "run-a", "event", composite(), composite()};
}
am::ConversationEnvelope envelope() {
    am::ConversationEnvelope value;
    const am::ConversationRef conversation{"fixture", am::ConversationId{std::uint64_t{1}}};
    value.metadata.ref = {conversation, am::EntryId{std::uint64_t{1}}, am::EventRevision{std::uint64_t{1}}};
    value.metadata.scope = am::ScopeId{"scope"};
    value.metadata.source = am::ConversationSourceBinding{am::ConversationSourceIdentity::ExternalEvent,
        external(), external(), am::SourceRevisionId{std::string(48, 'r')}, std::nullopt,
        "copy-fixture-v1", {am::ArtifactBindingId{std::string(48, 'a')}, "record/1"}};
    value.metadata.links.push_back({am::ConversationLinkKind::Reply,
        am::UnresolvedConversationTarget{external(), {am::ArtifactBindingId{"evidence"}, "record/2"}}});
    value.payload = am::ConversationToolResultObserved{
        am::UnresolvedConversationTarget{external(), {am::ArtifactBindingId{"evidence"}, "record/3"}},
        {0, 255}, am::ConversationToolOutcome::Unknown, std::nullopt};
    value.extensions.push_back({1024, false, {0, 255}});
    return value;
}
enum class Operation { Signed, Unsigned, String, Component, Composite, Ref, Binding, Metadata, Envelope,
    KeySigned, KeyUnsigned, KeyString };
struct Fixture {
    am::ExternalObjectRef original = external(), copied;
    am::ConversationEnvelope input = envelope(), output;
    am::ExternalIdentityScalar scalar = std::string(160, 's'), scalar_copy;
    am::ExternalObjectKey scalar_key;
    am::ExternalIdentityComponent component{std::string(48, 'n'), std::string(160, 'v')}, component_copy;
    const am::ByteBuffer original_bytes = am::serialize_external_object_ref_bytes(original);
    const am::ByteBuffer input_bytes = am::serialize_conversation_envelope(input);

    explicit Fixture(Operation op) {
        if(op == Operation::Signed || op == Operation::KeySigned) scalar = std::int64_t{-7};
        if(op == Operation::Unsigned || op == Operation::KeyUnsigned) scalar = std::uint64_t{9};
        scalar_key = std::visit([](const auto& value) { return am::ExternalObjectKey{value}; }, scalar);
    }
    void attempt(Operation op) {
        switch(op) {
        case Operation::Signed: case Operation::Unsigned: case Operation::String:
            scalar_copy = am::copy_external_identity_scalar(scalar); break;
        case Operation::Component: component_copy = am::copy_external_identity_component(component); break;
        case Operation::Composite: copied.object_key = am::copy_external_object_key(original.object_key); break;
        case Operation::Ref: copied = am::copy_external_object_ref(original); break;
        case Operation::Binding: output.metadata.source = am::copy_conversation_source_binding(*input.metadata.source); break;
        case Operation::Metadata: output.metadata = am::copy_conversation_metadata(input.metadata); break;
        case Operation::Envelope: output = am::copy_conversation_envelope(input); break;
        case Operation::KeySigned: case Operation::KeyUnsigned: case Operation::KeyString:
            copied.object_key = am::copy_external_object_key(scalar_key); break;
        }
    }
    void verify_source() const {
        expect(am::serialize_external_object_ref_bytes(original) == original_bytes, "source external bytes unchanged");
        expect(am::serialize_conversation_envelope(input) == input_bytes, "source envelope bytes unchanged");
        expect(std::get<std::string>(component.value) == std::string(160, 'v'), "component source unchanged");
        expect(std::visit([&](const auto& value) { return std::get<std::decay_t<decltype(value)>>(scalar_key.value) == value; },
            scalar), "scalar key input unchanged");
    }
    void verify_copy(Operation op) const {
        switch(op) {
        case Operation::Signed: case Operation::Unsigned: case Operation::String:
            expect(scalar_copy == scalar && scalar_copy.index() == scalar.index(), "scalar type/value retained"); break;
        case Operation::Component: expect(component_copy == component, "component retained"); break;
        case Operation::Composite: expect(copied.object_key == original.object_key, "composite retained"); break;
        case Operation::Ref: expect(am::serialize_external_object_ref_bytes(copied) == original_bytes, "AMXR bytes retained"); break;
        case Operation::Binding:
            expect(*output.metadata.source->event == *input.metadata.source->event &&
                *output.metadata.source->object == *input.metadata.source->object, "both source refs retained"); break;
        case Operation::Metadata:
            expect(*output.metadata.source->event == *input.metadata.source->event &&
                std::get<am::UnresolvedConversationTarget>(output.metadata.links[0].target).external ==
                std::get<am::UnresolvedConversationTarget>(input.metadata.links[0].target).external,
                "metadata sources/links retained"); break;
        case Operation::Envelope:
            expect(am::serialize_conversation_envelope(output) == input_bytes, "AMCE bytes retained"); break;
        case Operation::KeySigned: case Operation::KeyUnsigned: case Operation::KeyString:
            expect(copied.object_key == scalar_key, "scalar key type/value retained"); break;
        }
    }
};
void sweep(Operation op) {
    std::size_t count = 0;
    { Fixture probe(op); { ScopedGate gate(0); probe.attempt(op); } count = g_gate.calls; probe.verify_copy(op); }
    for(std::size_t ordinal = 1; ordinal <= count; ++ordinal) {
        Fixture fixture(op);
        bool has_thrown = false;
        try { ScopedGate gate(ordinal); fixture.attempt(op); } catch(const std::bad_alloc&) { has_thrown = true; }
        expect(has_thrown && g_gate.has_failed && g_gate.calls == ordinal, "one actual bad_alloc per ordinal");
        fixture.verify_source(); fixture.attempt(op); fixture.verify_copy(op);
    }
    std::printf("PASS safe copy operation %d: %zu actual bad_alloc failures\n", static_cast<int>(op), count);
    std::fflush(stdout);
}
} // namespace
int main() {
#if defined(_MSC_VER) && _ITERATOR_DEBUG_LEVEL > 0
    std::puts("SKIP safe-copy allocation matrix: MSVC checked STL noexcept proxy construction");
    return 77;
#else
    try {
        for(const auto op : {Operation::Signed, Operation::Unsigned, Operation::String, Operation::Component,
            Operation::Composite, Operation::Ref, Operation::Binding, Operation::Metadata, Operation::Envelope,
            Operation::KeySigned, Operation::KeyUnsigned, Operation::KeyString}) sweep(op);
        return 0;
    } catch(const std::exception& e) { g_gate.is_enabled = false; std::fprintf(stderr, "%s\n", e.what()); return 1; }
#endif
}
