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
    const auto bytes = agent_memory::serialize_external_object_ref(message);
    const auto restored = agent_memory::deserialize_external_object_ref(bytes);
    if(restored != message) return 1;
    const auto copied = agent_memory::copy_external_object_ref(message);
    if(copied != message || agent_memory::serialize_external_object_ref(copied) != bytes) return 2;
    // Local occurrence allocation and source revision selection remain separate operations.
    std::cout << "External identity preserved in " << bytes.size() << " canonical bytes\n";
    return 0;
}
