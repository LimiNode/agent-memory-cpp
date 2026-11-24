#include <agent_memory/domain.hpp>

#include <cstdint>
#include <iostream>
#include <type_traits>

int main() {
    static_assert(std::is_aggregate_v<agent_memory::ExternalObjectKey>);
    // The adapter declares this issuer and key recipe; no account observation is an ID component.
    const agent_memory::ExternalObjectRef message{
        "org.telegram", "api.telegram.org", "public", "message",
        agent_memory::ExternalObjectKey{std::int64_t{-100123}},
        agent_memory::ExternalObjectKey{std::uint64_t{42}}};
    const auto bytes = agent_memory::serialize_external_object_ref_bytes(message);
    static_assert(std::is_same_v<std::decay_t<decltype(bytes)>, agent_memory::ByteBuffer>);
    const auto restored = agent_memory::deserialize_external_object_ref_bytes({bytes.data(), bytes.size()});
    if(restored != message) return 1;
    // Existing binary-string consumers remain source- and wire-compatible.
    const auto legacy = agent_memory::serialize_external_object_ref(restored);
    if(agent_memory::deserialize_external_object_ref(legacy) != message || legacy.size() != bytes.size()) return 3;
    const auto copied = agent_memory::copy_external_object_ref(message);
    if(copied != message || agent_memory::serialize_external_object_ref_bytes(copied) != bytes) return 2;
    // Local occurrence allocation and source revision selection remain separate operations.
    std::cout << "External identity preserved in " << bytes.size() << " canonical bytes\n";
    return 0;
}
