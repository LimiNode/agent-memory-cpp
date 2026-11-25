#include <agent_memory/domain/Conversation.hpp>
#include <agent_memory/chat/Message.hpp>
#include <agent_memory/domain/Metadata.hpp>

#include <iostream>
#include <limits>
#include <stdexcept>
#include <type_traits>
#include <utility>

namespace am = agent_memory;

namespace {

template<typename Left, typename Right, typename = void>
struct has_equality : std::false_type {};
template<typename Left, typename Right>
struct has_equality<Left, Right, std::void_t<decltype(std::declval<Left>() == std::declval<Right>())>>
    : std::true_type {};
template<typename Id, typename = void>
struct has_addition : std::false_type {};
template<typename Id>
struct has_addition<Id, std::void_t<decltype(std::declval<Id>() + std::uint64_t{1})>> : std::true_type {};
template<typename Id, typename = void>
struct has_increment : std::false_type {};
template<typename Id>
struct has_increment<Id, std::void_t<decltype(++std::declval<Id&>())>> : std::true_type {};

template<typename Id>
constexpr bool strong_id_contract() {
    return std::is_default_constructible_v<Id> && std::is_copy_constructible_v<Id> &&
        std::is_constructible_v<Id, std::uint64_t> && !std::is_convertible_v<std::uint64_t, Id> &&
        !std::is_convertible_v<Id, std::uint64_t> && !std::is_convertible_v<Id, bool> &&
        !std::is_constructible_v<Id, std::int64_t> && !std::is_constructible_v<Id, int> &&
        !std::is_constructible_v<Id, bool> && !std::is_constructible_v<Id, float> &&
        !std::is_constructible_v<Id, double> && !std::is_constructible_v<Id, const char*> &&
        !has_addition<Id>::value && !has_increment<Id>::value;
}

template<typename Id, typename... Other>
constexpr bool rejects_other_ids() {
    return ((!std::is_constructible_v<Id, Other> && !std::is_convertible_v<Id, Other> &&
             !has_equality<Id, Other>::value) && ...);
}

static_assert(strong_id_contract<am::ConversationId>());
static_assert(strong_id_contract<am::EntryId>());
static_assert(strong_id_contract<am::ActorId>());
static_assert(strong_id_contract<am::EventRevision>());
static_assert(rejects_other_ids<am::ConversationId, am::EntryId, am::ActorId, am::EventRevision>());
static_assert(rejects_other_ids<am::EntryId, am::ConversationId, am::ActorId, am::EventRevision>());
static_assert(rejects_other_ids<am::ActorId, am::ConversationId, am::EntryId, am::EventRevision>());
static_assert(rejects_other_ids<am::EventRevision, am::ConversationId, am::EntryId, am::ActorId>());
static_assert(am::ConversationId{}.empty() && am::EntryId{}.empty() &&
              am::ActorId{}.empty() && am::EventRevision{}.empty());
static_assert(am::EventRevision{std::numeric_limits<std::uint64_t>::max()}.value() ==
              std::numeric_limits<std::uint64_t>::max());

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

am::ConversationRef conversation(std::uint64_t id = 1, std::string ns = "fixture-a") {
    return {std::move(ns), am::ConversationId{id}};
}

am::EntryRef reference(std::uint64_t entry = 2, std::uint64_t revision = 1) {
    return {conversation(), am::EntryId{entry}, am::EventRevision{revision}};
}

am::ExternalObjectRef external(std::string kind = "message") {
    return {"org.telegram", "api.telegram.org", "public", std::move(kind),
        am::ExternalObjectKey{std::int64_t{-100123}}, am::ExternalObjectKey{std::uint64_t{42}}};
}

am::ConversationImportEvidence evidence() {
    return {am::ArtifactBindingId{"retained-evidence@1"}, "record:0007"};
}

am::ConversationSourceBinding source() {
    am::ConversationSourceBinding binding;
    binding.revision = am::SourceRevisionId{"retained-observation@1"};
    binding.adapter_recipe = "fixture-import-v1";
    binding.evidence = evidence();
    return binding;
}

am::ConversationEntry message() {
    am::ConversationEntry result;
    result.ref = reference();
    result.scope = am::ScopeId{"fixture-scope"};
    result.payload = am::ConversationMessageCreated{{std::string{"message"}, {}}};
    return result;
}

void valid(const am::ConversationEntry& value) {
    const auto issues = am::validate_conversation_entry(value);
    if(!issues.empty()) throw std::runtime_error("field-valid entry rejected at " + issues.front().field);
}

void invalid(const am::ConversationEntry& value) {
    const auto issues = am::validate_conversation_entry(value);
    expect(!issues.empty(), "invalid entry was accepted");
    for(const auto& issue : issues) {
        expect(!issue.field.empty(), "validation issue has no structural field path");
        expect(issue.field.find("PRIVATE") == std::string::npos, "diagnostic leaked source data");
    }
}

void exact_identity_and_namespace() {
    const auto maximum = std::numeric_limits<std::uint64_t>::max();
    for(const auto& ns : {std::string{"a"}, std::string{"a.b-c_d9"}, std::string(128, 'a')})
        expect(am::is_valid_conversation_namespace(ns), "valid allocation namespace rejected");
    for(const auto& ns : {std::string{}, std::string{"A"}, std::string{"1a"}, std::string{"a b"},
            std::string{"a:b"}, std::string{"a\0b", 3}, std::string{"\xc3\xa9"}, std::string(129, 'a')})
        expect(!am::is_valid_conversation_namespace(ns), "invalid allocation namespace accepted");
    const auto a = conversation();
    const auto b = conversation(1, "fixture-b");
    const auto c = conversation(2);
    expect(a == conversation() && !(a != conversation()) && !(a < a), "conversation equality is inconsistent");
    expect(a != b && a < b && a != c && a < c, "conversation allocation domain or occurrence collapsed");
    expect(am::is_valid_conversation_ref(conversation(maximum)), "maximum conversation ID rejected");
    expect(!am::is_valid_conversation_ref(conversation(0)), "unassigned conversation ID accepted");
    expect(!am::is_valid_conversation_ref(conversation(1, "")), "unqualified conversation accepted");
    auto entry = reference();
    expect(entry == reference() && !(entry != reference()) && !(entry < entry), "entry equality is inconsistent");
    expect(entry != reference(3) && entry < reference(3), "entry occurrence lost");
    expect(entry != reference(2, 2) && entry < reference(2, 2), "exact representation revision lost");
    auto other = entry;
    other.conversation = b;
    expect(entry != other && entry < other, "entry allocation namespace lost");
    other.conversation = c;
    expect(entry != other && entry < other, "entry conversation lost");
    entry = {conversation(maximum), am::EntryId{maximum}, am::EventRevision{maximum}};
    expect(am::is_valid_entry_ref(entry), "maximum exact reference rejected without allocator arithmetic");
    entry.entry = am::EntryId{};
    expect(!am::is_valid_entry_ref(entry), "zero entry ID accepted");
    entry = reference(); entry.revision = am::EventRevision{};
    expect(!am::is_valid_entry_ref(entry), "zero exact revision accepted");
    for(const auto& invalid_ref : {reference(0), reference(2, 0)}) {
        auto value = message(); value.ref = invalid_ref; invalid(value);
    }
    const am::ActorRef actor{"fixture-a", am::ActorId{std::uint64_t{1}}};
    expect(actor == am::ActorRef{"fixture-a", am::ActorId{std::uint64_t{1}}} && !(actor < actor),
        "actor equality is inconsistent");
    expect(actor != am::ActorRef{"fixture-b", actor.id} && actor < am::ActorRef{"fixture-b", actor.id},
        "actor allocation namespace lost");
    expect(actor < am::ActorRef{"fixture-a", am::ActorId{maximum}}, "actor numeric order lost");
    expect(am::is_valid_actor_ref({"fixture-a", am::ActorId{maximum}}), "maximum actor ID rejected");
    expect(!am::is_valid_actor_ref({"fixture-a", {}}) && !am::is_valid_actor_ref({"", actor.id}),
        "unassigned or unqualified actor accepted");
}

void content_and_boundaries() {
    valid(message());
    auto value = message();
    value.payload = am::ConversationMessageCreated{{std::nullopt, {am::ArtifactBindingId{"attachment@1"}}}};
    valid(value);
    value.payload = am::ConversationMessageCreated{{std::string{}, {am::ArtifactBindingId{"attachment@1"}}}};
    valid(value);
    value.payload = am::ConversationMessageCreated{{std::string{"  \t\n"}, {}}};
    valid(value);
    expect(std::get<am::ConversationMessageCreated>(value.payload).content.text == "  \t\n", "text was trimmed");
    value.payload = am::ConversationMessageCreated{{std::string{"a\0b", 3}, {}}};
    valid(value);
    for(const auto& content : {am::ConversationContent{}, am::ConversationContent{std::string{}, {}}}) {
        value.payload = am::ConversationMessageCreated{content};
        invalid(value);
    }
    value = message();
    auto& content = std::get<am::ConversationMessageCreated>(value.payload).content;
    content.text = std::string(am::kMaxConversationTextBytes, 'x');
    valid(value);
    content.text->push_back('x'); invalid(value);
    content.text.reset();
    for(std::size_t i = 0; i < am::kMaxConversationAttachments; ++i)
        content.attachments.emplace_back("attachment-" + std::to_string(i));
    valid(value);
    content.attachments.emplace_back("one-too-many"); invalid(value);
    content.attachments = {am::ArtifactBindingId{}}; invalid(value);
    content.attachments = {am::ArtifactBindingId{std::string(am::kMaxConversationBindingBytes, 'a')}};
    valid(value);
    content.attachments = {am::ArtifactBindingId{std::string(am::kMaxConversationBindingBytes + 1, 'a')}};
    invalid(value);
    // Existing artifact/scope IDs are opaque, not newly normalized UTF-8 fields.
    content.attachments = {am::ArtifactBindingId{std::string{"a\0\xff", 3}}};
    value.scope = am::ScopeId{std::string{"Mixed Scope\0\xff", 13}};
    valid(value);
    for(const auto& text : {std::string{"\xc0\xaf"}, std::string{"\xed\xa0\x80"}, std::string{"\xf4\x90\x80\x80"},
            std::string{"\x80"}, std::string{"\xe2\x82"}}) {
        value = message();
        std::get<am::ConversationMessageCreated>(value.payload).content.text = text;
        invalid(value);
    }
    value = message();
    std::get<am::ConversationMessageCreated>(value.payload).content.text = std::string{"\xf4\x8f\xbf\xbf"};
    valid(value);
    const std::string decomposed{"e\xcc\x81"};
    std::get<am::ConversationMessageCreated>(value.payload).content.text = decomposed;
    valid(value);
    expect(std::get<am::ConversationMessageCreated>(value.payload).content.text == decomposed,
        "validator normalized Unicode source text");
    value.scope = am::ScopeId{}; invalid(value);
}

void exact_mutation_targets() {
    for(unsigned kind = 0; kind != 3; ++kind) {
        auto value = message();
        const auto set_target = [&](am::EntryRef target) {
            if(kind == 0) value.payload = am::ConversationEdited{target, {std::string{"replacement"}, {}}};
            else if(kind == 1) value.payload = am::ConversationTombstoned{target};
            else value.payload = am::ConversationRestored{target, reference(3, 1)};
        };
        set_target(reference(3, 2)); valid(value);
        // A field validator must not invent chronology or perform target existence lookup.
        set_target(reference(3, std::numeric_limits<std::uint64_t>::max())); valid(value);
        set_target(reference(3, 0)); invalid(value);
        set_target(reference(0, 1)); invalid(value);
        set_target(reference(2, 2)); invalid(value); // Same occurrence, even at another exact revision.
        auto target = reference(3); target.conversation = conversation(2);
        set_target(target); invalid(value);
        target.conversation = conversation(1, "fixture-b"); set_target(target); invalid(value);
    }
    auto value = message();
    value.payload = am::ConversationEdited{reference(3), {std::nullopt, {}}}; invalid(value);
    value.payload = am::ConversationEdited{reference(3), {std::nullopt, {am::ArtifactBindingId{"attachment"}}}};
    valid(value);
    value.payload = am::ConversationRestored{reference(3, 2), reference(3, 1)}; valid(value);
    auto& restore = std::get<am::ConversationRestored>(value.payload);
    restore.retained_state = reference(2, 9); invalid(value);
    restore.retained_state = reference(3, 0); invalid(value);
    restore.retained_state = reference(3); restore.retained_state.conversation = conversation(2); invalid(value);
}

void links_and_unresolved_evidence() {
    for(const auto kind : {am::ConversationLinkKind::Reply, am::ConversationLinkKind::Thread,
            am::ConversationLinkKind::Branch, am::ConversationLinkKind::Forward, am::ConversationLinkKind::Quote}) {
        auto value = message();
        value.links.push_back({kind, reference(3)}); valid(value);
        value.links.front().target = reference(2); invalid(value);
        value.links.front().target = reference(2, 2); invalid(value); // Same occurrence, another revision.
        auto other = reference(3); other.conversation = conversation(2, "fixture-b");
        value.links.front().target = other;
        if(kind == am::ConversationLinkKind::Reply || kind == am::ConversationLinkKind::Thread) invalid(value);
        else valid(value);
        value.links.front().target = reference(3, 0); invalid(value);
        value.links.front().target = am::UnresolvedConversationTarget{std::nullopt, evidence()}; valid(value);
        auto& unresolved = std::get<am::UnresolvedConversationTarget>(value.links.front().target);
        unresolved.external = external(); valid(value);
        unresolved.external->provider = "PRIVATE INVALID"; invalid(value);
        unresolved.external.reset(); unresolved.evidence.artifact = {}; invalid(value);
        unresolved.evidence = evidence(); unresolved.evidence.record_locator.clear(); invalid(value);
    }
    auto value = message();
    value.links.resize(am::kMaxConversationLinks, {am::ConversationLinkKind::Reply, reference(3)});
    valid(value);
    value.links.push_back({am::ConversationLinkKind::Reply, reference(3)}); invalid(value);
    value.links = {{static_cast<am::ConversationLinkKind>(999), reference(3)}}; invalid(value);
}

void source_identity_and_snapshots() {
    auto value = message();
    value.source = source(); valid(value); // No external key is fabricated for local evidence.
    value.source->object = external(); valid(value);
    const auto object_bytes = am::serialize_external_object_ref_bytes(*value.source->object);
    value.source->event = external("message-change"); invalid(value);
    value.source->identity = am::ConversationSourceIdentity::ExternalEvent; valid(value);
    value.source->provider_revision_marker = "not-an-event-key"; invalid(value);
    value.source->provider_revision_marker.reset();
    value.source->object.reset(); valid(value); // An explicit provider event need not expose a message key.
    value.source->event.reset(); invalid(value);
    value.source = source();
    value.source->identity = am::ConversationSourceIdentity::ObjectRevision;
    value.source->object = external();
    invalid(value); // Retained SourceRevisionId is not an upstream revision marker.
    value.source->provider_revision_marker = "upstream-revision:0002"; valid(value);
    value.source->provider_revision_marker = std::string{"\0\xff", 2}; valid(value);
    value.source->provider_revision_marker = ""; invalid(value);
    value.source->provider_revision_marker = "upstream-revision:0002";
    value.source->object.reset(); invalid(value);
    value.source->object = external(); value.source->event = external("change"); invalid(value);
    value.source = source(); value.source->provider_revision_marker = "guessed"; invalid(value);
    value.source = source(); value.source->identity = static_cast<am::ConversationSourceIdentity>(999); invalid(value);
    for(const auto identity : {am::ConversationSourceIdentity::LocalObservation,
            am::ConversationSourceIdentity::ExternalEvent, am::ConversationSourceIdentity::ObjectRevision}) {
        value.source = source(); value.source->identity = identity;
        if(identity == am::ConversationSourceIdentity::ExternalEvent) value.source->event = external("change");
        if(identity == am::ConversationSourceIdentity::ObjectRevision) {
            value.source->object = external(); value.source->provider_revision_marker = "source-r2";
        }
        valid(value);
        const auto complete = *value.source;
        value.source->revision = {}; invalid(value);
        value.source = complete; value.source->evidence.artifact = {}; invalid(value);
        value.source = complete; value.source->evidence.record_locator.clear(); invalid(value);
        value.source = complete; value.source->adapter_recipe.clear(); invalid(value);
    }
    value = message();
    value.payload = am::ConversationSnapshotObserved{{std::nullopt, {am::ArtifactBindingId{"snapshot-attachment"}}}};
    invalid(value); // A snapshot needs retained evidence, not inferred creation history.
    value.source = source(); value.source->object = external(); valid(value);
    expect(!value.source->event && !value.source->provider_revision_marker, "snapshot invented source change identity");
    expect(am::serialize_external_object_ref_bytes(*value.source->object) == object_bytes, "source object identity changed");
    expect(std::holds_alternative<am::ConversationSnapshotObserved>(value.payload), "snapshot was promoted to creation");
    value.source->revision = {}; invalid(value);
    value.source = source(); value.source->object = am::ExternalObjectRef{}; invalid(value);
    value.source = source(); value.source->identity = am::ConversationSourceIdentity::ExternalEvent;
    value.source->event = am::ExternalObjectRef{}; invalid(value);
    for(const auto& key : {am::ExternalObjectKey{std::int64_t{-42}},
            am::ExternalObjectKey{std::uint64_t{42}}, am::ExternalObjectKey{std::string{"00123"}}}) {
        value.source = source(); value.source->object = external();
        value.source->object->object_key = key;
        valid(value);
        const auto bytes = am::serialize_external_object_ref_bytes(*value.source->object);
        expect(am::deserialize_external_object_ref_bytes({bytes.data(), bytes.size()}) == *value.source->object,
            "conversation source changed signed/unsigned/opaque AMXR identity");
    }
}

void source_bounds_and_opaque_bindings() {
    auto value = message(); value.source = source();
    value.source->adapter_recipe = std::string(am::kMaxConversationRecipeBytes, 'r'); valid(value);
    value.source->adapter_recipe.push_back('r'); invalid(value);
    value.source = source();
    value.source->evidence.record_locator = std::string(am::kMaxConversationLocatorBytes, 'l'); valid(value);
    value.source->evidence.record_locator.push_back('l'); invalid(value);
    value.source = source();
    value.source->evidence.artifact = am::ArtifactBindingId{std::string(am::kMaxConversationBindingBytes, 'a')};
    value.source->revision = am::SourceRevisionId{std::string(am::kMaxConversationBindingBytes, 'r')}; valid(value);
    value.source->evidence.artifact = am::ArtifactBindingId{std::string(am::kMaxConversationBindingBytes + 1, 'a')};
    invalid(value);
    value.source = source();
    value.source->revision = am::SourceRevisionId{std::string(am::kMaxConversationBindingBytes + 1, 'r')}; invalid(value);
    value.source = source();
    value.source->revision = am::SourceRevisionId{std::string{"r\0\xff", 3}};
    value.source->evidence.artifact = am::ArtifactBindingId{std::string{"a\0\xff", 3}};
    value.source->evidence.record_locator = std::string{"row\0part", 8}; valid(value);
    for(const auto& malformed : {std::string{"\xc0\xaf"}, std::string{"\x80"}, std::string{"\xe2\x82"}}) {
        value.source = source(); value.source->evidence.record_locator = malformed; invalid(value);
        value.source = source(); value.source->adapter_recipe = malformed; invalid(value);
    }
    value.source = source(); value.source->identity = am::ConversationSourceIdentity::ObjectRevision;
    value.source->object = external();
    value.source->provider_revision_marker = std::string(am::kMaxConversationBindingBytes, 'r'); valid(value);
    value.source->provider_revision_marker->push_back('r'); invalid(value);
}

void actors_roles_observers_and_times() {
    auto value = message();
    for(const auto role : {am::ConversationLlmRole::System, am::ConversationLlmRole::Developer,
            am::ConversationLlmRole::User, am::ConversationLlmRole::Assistant, am::ConversationLlmRole::Tool}) {
        value.llm_role = role; valid(value); // Role does not require or supply participant identity.
    }
    value.actor = am::ActorRef{"participants", am::ActorId{std::uint64_t{7}}};
    value.observer = am::ActorRef{"observers", am::ActorId{std::uint64_t{7}}};
    valid(value);
    expect(*value.actor != *value.observer, "observer and actor namespaces collapsed");
    value.llm_role.reset(); valid(value);
    value.llm_role = static_cast<am::ConversationLlmRole>(999); invalid(value);
    value.llm_role.reset(); value.actor->id = {}; invalid(value);
    value.actor.reset(); valid(value); // An observer is not the actor.
    value.observer->allocation_namespace = ""; invalid(value);
    value = message();
    const auto minimum = std::numeric_limits<std::int64_t>::min();
    const auto maximum = std::numeric_limits<std::int64_t>::max();
    for(const auto time : {minimum, std::int64_t{-1}, std::int64_t{0}, maximum}) {
        value.source_unix_us = time; value.observed_unix_us = time; valid(value);
        expect(value.source_unix_us == time && value.observed_unix_us == time, "signed timestamp changed");
    }
    value.source_unix_us = maximum; value.observed_unix_us = minimum; valid(value);
    value.source_unix_us.reset(); valid(value);
    expect(!value.source_unix_us && value.observed_unix_us == minimum, "unknown source time was filled from observation");
    value.observed_unix_us.reset(); valid(value);
}

void nonmutating_diagnostics_and_legacy() {
    auto value = message();
    value.ref.conversation.allocation_namespace = "PRIVATE INVALID NAMESPACE";
    value.source = source(); value.source->evidence.record_locator.clear();
    std::get<am::ConversationMessageCreated>(value.payload).content.text = std::string{"PRIVATE\xff", 8};
    invalid(value);
    expect(value.ref.conversation.allocation_namespace == "PRIVATE INVALID NAMESPACE", "validator normalized namespace");
    expect(std::get<am::ConversationMessageCreated>(value.payload).content.text == std::string{"PRIVATE\xff", 8},
        "validator changed malformed source bytes");
    am::Message legacy;
    legacy.id = am::MessageId{"00123"}; legacy.thread_id = am::ThreadId{"-100123"};
    legacy.author_id = am::AuthorId{"actor:07"}; legacy.text = "legacy";
    legacy.metadata.set("revision", "0002");
    expect(am::is_valid(legacy), "legacy chat behavior changed");
    expect(legacy.id.value() == "00123" && legacy.thread_id.value() == "-100123" &&
        legacy.author_id.value() == "actor:07" && legacy.metadata.get("revision") == "0002", "legacy IDs normalized");
    legacy.text.clear();
    expect(!am::is_valid(legacy), "new attachment-only rule changed legacy Message validity");
    am::Thread thread{am::ThreadId{"legacy-thread:001"}, "Legacy title", {legacy}};
    expect(thread.id.value() == "legacy-thread:001" && thread.messages.front().id == legacy.id,
        "legacy Thread API changed");
}

} // namespace

int main() {
    try {
        exact_identity_and_namespace();
        content_and_boundaries();
        exact_mutation_targets();
        links_and_unresolved_evidence();
        source_identity_and_snapshots();
        source_bounds_and_opaque_bindings();
        actors_roles_observers_and_times();
        nonmutating_diagnostics_and_legacy();
        std::cout << "conversation strong IDs, exact refs, typed changes, evidence, links and field validation: PASS\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
