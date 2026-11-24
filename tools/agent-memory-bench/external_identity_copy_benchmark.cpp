// Standalone directional copy-cost probe; no JSON, SDK or benchmark dependency.
#include <agent_memory/domain/ExternalObjectRef.hpp>

#include "../../tests/support/allocation_failure.hpp"
#include <algorithm>
#include <array>
#include <chrono>

namespace am = agent_memory;
namespace {
volatile std::size_t g_checksum = 0;
am::ExternalObjectRef fixture(bool composite) {
    const auto key = [] {
        std::vector<am::ExternalIdentityComponent> parts;
        parts.push_back({std::string(48, 'a'), std::int64_t{-1}});
        parts.push_back({std::string(48, 'b'), std::uint64_t{7}});
        parts.push_back({std::string(48, 'c'), std::string(160, 'k')});
        return am::ExternalObjectKey{std::move(parts)};
    };
    if(composite) return {"fixture", std::string(48, 'i'), "run-a", "event", key(), key()};
    return {"fixture", std::string(48, 'i'), "run-a", "event", std::nullopt, {std::string(160, 'k')}};
}
template<class Copy> void measure(const char* name, const am::ExternalObjectRef& source, Copy copy) {
    {
        ScopedGate gate(0);
        const auto result = copy(source);
        g_checksum += result.service_instance.size();
    }
    const auto allocations = g_gate.calls;
    constexpr std::size_t iterations = 10000;
    std::array<double, 5> samples{};
    for(auto& sample : samples) {
        const auto start = std::chrono::steady_clock::now();
        for(std::size_t i = 0; i < iterations; ++i) {
            const auto result = copy(source);
            g_checksum += result.service_instance.size() + result.object_key.value.index();
        }
        sample = std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now() - start).count() / iterations;
    }
    std::sort(samples.begin(), samples.end());
    std::printf("%s: allocations=%zu median_ns=%.1f min_ns=%.1f max_ns=%.1f (5 x %zu copies)\n",
        name, allocations, samples[2], samples.front(), samples.back(), iterations);
}
} // namespace
int main() {
    for(const bool composite : {false, true}) {
        const auto source = fixture(composite);
        measure(composite ? "composite ordinary" : "scalar ordinary", source,
            [](const auto& value) { return am::ExternalObjectRef(value); });
        measure(composite ? "composite explicit" : "scalar explicit", source, am::copy_external_object_ref);
    }
    return 0;
}
