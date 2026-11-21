#include <agent_memory/domain.hpp>

#include <cstdint>
#include <iostream>

int main() {
    // The adapter declares this issuer and key recipe; no account observation is an ID component.
    const agent_memory::ExternalObjectRef message{
        "org.telegram", "api.telegram.org", "public", "message",
        agent_memory::ExternalObjectKey{std::int64_t{-100123}},
        agent_memory::ExternalObjectKey{std::uint64_t{42}}};
    const auto bytes = agent_memory::serialize_external_object_ref(message);
    const auto restored = agent_memory::deserialize_external_object_ref(bytes);
    if(restored != message) return 1;
    // Local occurrence allocation and source revision selection remain separate operations.
    std::cout << "External identity preserved in " << bytes.size() << " canonical bytes\n";
    return 0;
}
