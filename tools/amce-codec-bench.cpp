#include <agent_memory/domain/conversation_codec.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <new>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <psapi.h>
#else
#include <sys/resource.h>
#endif

// Standalone, single-threaded measurement tool. The prefix records requested
// C++ allocation bytes, not allocator usable size, fragmentation or process RSS.
namespace allocation_probe {
struct Totals final {
    std::uint64_t calls = 0;
    std::uint64_t requested = 0;
    std::uint64_t live_requested = 0;
    std::uint64_t peak_requested = 0;
    std::uint64_t live_malloc = 0;
    std::uint64_t peak_malloc = 0;
};
struct alignas(std::max_align_t) Header final {
    void* base;
    std::size_t requested;
    std::size_t malloc_bytes;
    std::uint64_t epoch;
};
bool is_enabled = false;
std::uint64_t epoch = 0;
Totals totals;

void begin() noexcept {
    ++epoch;
    totals = {};
    is_enabled = true;
}
Totals finish() noexcept {
    is_enabled = false;
    return totals;
}
void* allocate(std::size_t requested, std::size_t alignment) {
    alignment = std::max(alignment, alignof(Header));
    const auto payload = std::max<std::size_t>(requested, 1);
    if(payload > std::numeric_limits<std::size_t>::max() - sizeof(Header) - alignment)
        throw std::bad_alloc{};
    const auto malloc_bytes = payload + sizeof(Header) + alignment - 1;
    void* base = std::malloc(malloc_bytes);
    if(!base) throw std::bad_alloc{};
    const auto first = reinterpret_cast<std::uintptr_t>(base) + sizeof(Header);
    const auto aligned = (first + alignment - 1) & ~(static_cast<std::uintptr_t>(alignment) - 1);
    auto* header = ::new(reinterpret_cast<void*>(aligned - sizeof(Header)))
        Header{base, requested, malloc_bytes, is_enabled ? epoch : 0};
    (void)header;
    if(is_enabled) {
        ++totals.calls;
        totals.requested += requested;
        totals.live_requested += requested;
        totals.live_malloc += malloc_bytes;
        totals.peak_requested = std::max(totals.peak_requested, totals.live_requested);
        totals.peak_malloc = std::max(totals.peak_malloc, totals.live_malloc);
    }
    return reinterpret_cast<void*>(aligned);
}
void release(void* value) noexcept {
    if(!value) return;
    const auto* header = reinterpret_cast<const Header*>(static_cast<const unsigned char*>(value) - sizeof(Header));
    if(header->epoch != 0 && header->epoch == epoch) {
        totals.live_requested -= header->requested;
        totals.live_malloc -= header->malloc_bytes;
    }
    std::free(header->base);
}
} // namespace allocation_probe

void* operator new(std::size_t bytes) { return allocation_probe::allocate(bytes, alignof(std::max_align_t)); }
void* operator new[](std::size_t bytes) { return allocation_probe::allocate(bytes, alignof(std::max_align_t)); }
void operator delete(void* value) noexcept { allocation_probe::release(value); }
void operator delete[](void* value) noexcept { allocation_probe::release(value); }
void operator delete(void* value, std::size_t) noexcept { allocation_probe::release(value); }
void operator delete[](void* value, std::size_t) noexcept { allocation_probe::release(value); }
void* operator new(std::size_t bytes, std::align_val_t alignment) {
    return allocation_probe::allocate(bytes, static_cast<std::size_t>(alignment));
}
void* operator new[](std::size_t bytes, std::align_val_t alignment) {
    return allocation_probe::allocate(bytes, static_cast<std::size_t>(alignment));
}
void operator delete(void* value, std::align_val_t) noexcept { allocation_probe::release(value); }
void operator delete[](void* value, std::align_val_t) noexcept { allocation_probe::release(value); }
void operator delete(void* value, std::size_t, std::align_val_t) noexcept { allocation_probe::release(value); }
void operator delete[](void* value, std::size_t, std::align_val_t) noexcept { allocation_probe::release(value); }
void* operator new(std::size_t bytes, const std::nothrow_t&) noexcept {
    try { return ::operator new(bytes); } catch(...) { return nullptr; }
}
void* operator new[](std::size_t bytes, const std::nothrow_t&) noexcept {
    try { return ::operator new[](bytes); } catch(...) { return nullptr; }
}
void operator delete(void* value, const std::nothrow_t&) noexcept { allocation_probe::release(value); }
void operator delete[](void* value, const std::nothrow_t&) noexcept { allocation_probe::release(value); }

namespace {
namespace am = agent_memory;
using Clock = std::chrono::steady_clock;
volatile std::uint64_t observed = 0;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

am::EntryRef reference(std::uint64_t entry = 2, std::string ns = "a") {
    return {{std::move(ns), am::ConversationId{std::uint64_t{1}}},
        am::EntryId{entry}, am::EventRevision{std::uint64_t{1}}};
}

am::ConversationEntry message(std::size_t text_bytes) {
    am::ConversationEntry value;
    value.ref = reference();
    value.scope = am::ScopeId{"s"};
    value.payload = am::ConversationMessageCreated{{std::string(text_bytes, 't'), {}}};
    return value;
}

am::ExternalObjectRef maximal_external() {
    am::ExternalObjectRef value;
    value.provider = std::string(128, 'p');
    value.service_instance = std::string(1024, 's');
    value.identity_domain = std::string(1024, 'd');
    value.object_kind = std::string(64, 'k');
    std::vector<am::ExternalIdentityComponent> parts;
    std::size_t size = 6 + 4 * 4 + 128 + 1024 + 1024 + 64 + 1 + 7;
    for(std::size_t i = 0; i < 16; ++i) {
        const auto name = std::string{"c"} + std::to_string(i);
        size += 2 + name.size() + 5 + 4096;
        parts.push_back({name, std::string(4096, 'x')});
    }
    auto& tail = std::get<std::string>(parts.back().value);
    expect(size > 65536 && size - 65536 < tail.size(), "invalid maximal AMXR recipe");
    tail.resize(tail.size() - (size - 65536));
    value.object_key = am::ExternalObjectKey{std::move(parts)};
#ifdef AGENT_MEMORY_AMCE_BENCH_LEGACY
    // Frozen pre-merge baseline has only the historical binary-string API.
    expect(am::serialize_external_object_ref(value).size() == 65536, "maximal AMXR must be exactly64KiB");
#else
    expect(am::serialize_external_object_ref_bytes(value).size() == 65536, "maximal AMXR must be exactly64KiB");
#endif
    return value;
}

am::ConversationEntry reference_heavy(bool combined) {
    auto value = message(combined ? 1048576 : 4096);
    const auto external = maximal_external();
    value.source = am::ConversationSourceBinding{};
    auto& source = *value.source;
    source.identity = am::ConversationSourceIdentity::ExternalEvent;
    source.object = external;
    source.event = external;
    source.revision = am::SourceRevisionId{std::string(combined ? 4096 : 16, 'r')};
    source.adapter_recipe = std::string(combined ? 1024 : 16, 'r');
    source.evidence = {am::ArtifactBindingId{std::string(combined ? 4096 : 16, 'b')},
        std::string(combined ? 4096 : 16, 'l')};
    value.links.assign(256, {am::ConversationLinkKind::Reply,
        am::UnresolvedConversationTarget{external, source.evidence}});
    if(combined) {
        value.ref = reference(2, std::string(128, 'a'));
        value.scope = am::ScopeId{std::string(65536, '\0')};
        value.actor = am::ActorRef{std::string(128, 'a'), am::ActorId{std::uint64_t{1}}};
        value.observer = am::ActorRef{std::string(128, 'o'), am::ActorId{std::uint64_t{1}}};
        value.llm_role = am::ConversationLlmRole::Tool;
        value.source_unix_us = std::numeric_limits<std::int64_t>::min();
        value.observed_unix_us = std::numeric_limits<std::int64_t>::max();
        am::ConversationContent content;
        content.text = std::string(1048576, 't');
        content.attachments.assign(256, am::ArtifactBindingId{std::string(4096, 'b')});
        value.payload = am::ConversationEdited{reference(1, std::string(128, 'a')), std::move(content)};
    }
    return value;
}

#ifdef AGENT_MEMORY_AMCE_BENCH_LEGACY
using Value = am::ConversationEntry;
using Encoded = std::string;
struct Fixture final { Value value; };
Encoded encode(const Fixture& fixture) { return am::serialize_conversation_entry(fixture.value); }
Value decode(const Fixture&, const Encoded& bytes) { return am::deserialize_conversation_entry(bytes); }
auto validate(const Fixture& fixture) { return am::validate_conversation_entry(fixture.value); }
void observe(const Value& value) { observed ^= value.ref.entry.value(); }
#else
using Value = am::ConversationEnvelope;
using Encoded = am::ByteBuffer;
struct Fixture final { Value value; am::ConversationCodecLimits limits; };
Encoded encode(const Fixture& fixture) { return am::serialize_conversation_envelope(fixture.value, fixture.limits); }
Value decode(const Fixture& fixture, const Encoded& bytes) {
    return am::deserialize_conversation_envelope({bytes.data(), bytes.size()}, {fixture.limits, false});
}
auto validate(const Fixture& fixture) { return am::validate_conversation_envelope(fixture.value, fixture.limits.fields); }
void observe(const Value& value) { observed ^= value.metadata.ref.entry.value(); }
#endif

template<typename Bytes>
void observe_bytes(const Bytes& value) {
    observed ^= value.size();
    if(!value.empty()) {
        observed ^= static_cast<unsigned char>(value.front());
        observed ^= static_cast<unsigned char>(value[value.size() / 2]);
        observed ^= static_cast<unsigned char>(value.back());
    }
}

Fixture make_fixture(std::string_view name) {
    auto entry = name == "nested_amxr" ? reference_heavy(false) :
        name == "combined_phase1_max" ? reference_heavy(true) : message(name == "text_1m" ? 1048576 : 1);
#ifdef AGENT_MEMORY_AMCE_BENCH_LEGACY
    return {std::move(entry)};
#else
    Fixture result{am::make_conversation_envelope(std::move(entry)), {}};
    if(name == "frame_32m") {
        result.value.payload = am::ConversationOpaquePayload{70000, 1, {}};
        const auto overhead = encode(result).size();
        std::get<am::ConversationOpaquePayload>(result.value.payload).bytes.assign(
            am::kMaxConversationEntryBytes - overhead, 255);
    } else if(name == "extended_frame_32m") {
        result.limits = am::extended_conversation_codec_limits();
        result.value.payload = am::ConversationPayload{am::ConversationMessageCreated{{std::string(4194304, 't'), {}}}};
        for(std::uint32_t i = 1; i <= 27; ++i)
            result.value.extensions.push_back({i, false, am::ByteBuffer(1048576, 255)});
        const auto before_last = encode(result).size();
        expect(before_last + 9 < am::kMaxConversationEntryBytes, "extended maximum needs final extension");
        const auto remaining = am::kMaxConversationEntryBytes - before_last - 9;
        expect(remaining <= 1048576, "extended final extension exceeds profile");
        result.value.extensions.push_back({28, false, am::ByteBuffer(remaining, 255)});
    } else if(name == "implementation_caps_32m") {
        auto& limits = result.limits;
        limits.fields = {8388608, 4096, 4096, 65536, 65536, 4096};
        limits.max_extensions = 1024;
        limits.max_extension_bytes = 8388608;
        limits.max_objects = 32768;
        auto& metadata = result.value.metadata;
        metadata.source = am::ConversationSourceBinding{};
        metadata.source->revision = am::SourceRevisionId{std::string(65536, 'r')};
        metadata.source->adapter_recipe.assign(4096, 'r');
        metadata.source->evidence = {am::ArtifactBindingId{std::string(65536, 'b')}, std::string(65536, 'l')};
        metadata.links.assign(4096, {am::ConversationLinkKind::Reply, reference(1)});
        am::ConversationContent content;
        content.text = std::string(8388608, 't');
        content.attachments.assign(4096, am::ArtifactBindingId{"a"});
        content.attachments.front() = am::ArtifactBindingId{std::string(65536, 'b')};
        result.value.payload = am::ConversationPayload{am::ConversationMessageCreated{std::move(content)}};
        for(std::uint32_t id = 1; id <= 1024; ++id) result.value.extensions.push_back({id, false, {}});
        result.value.extensions.front().bytes.assign(8388608, 255);
        const auto unpadded = encode(result).size();
        expect(unpadded < am::kMaxConversationEntryBytes, "implementation caps do not fit frame");
        metadata.scope = am::ScopeId{std::string(1 + am::kMaxConversationEntryBytes - unpadded, '\0')};
    }
    return result;
#endif
}

struct Sample final {
    std::uint64_t nanoseconds;
    allocation_probe::Totals allocations;
};

std::uint64_t quantile(std::vector<std::uint64_t> values, double percentile) {
    std::sort(values.begin(), values.end());
    const auto index = static_cast<std::size_t>(std::ceil(percentile * static_cast<double>(values.size()))) - 1;
    return values.at(index);
}

template<typename Function, typename Consumer>
void run(std::string_view operation, std::size_t samples, std::size_t warmups,
    Function action, Consumer consume) {
    for(std::size_t i = 0; i < warmups; ++i) consume(action());
    std::vector<Sample> results;
    results.reserve(samples);
    for(std::size_t i = 0; i < samples; ++i) {
        allocation_probe::begin();
        try {
            const auto start = Clock::now();
            auto value = action();
            const auto end = Clock::now();
            const auto allocations = allocation_probe::finish();
            consume(value);
            results.push_back({static_cast<std::uint64_t>(
                std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count()), allocations});
        } catch(...) {
            (void)allocation_probe::finish();
            throw;
        }
    }
    std::vector<std::uint64_t> times;
    for(const auto& sample : results) times.push_back(sample.nanoseconds);
    std::cout << "{\"kind\":\"measurement\",\"operation\":\"" << operation << "\",\"samples\":" << samples
        << ",\"warmups\":" << warmups << ",\"median_us\":" << quantile(times, .5) / 1000.0
        << ",\"p95_us\":" << quantile(times, .95) / 1000.0 << ",\"p99_us\":" << quantile(times, .99) / 1000.0
        << ",\"raw\":[";
    bool is_first = true;
    for(const auto& sample : results) {
        if(!is_first) std::cout << ',';
        is_first = false;
        const auto& counts = sample.allocations;
        std::cout << '[' << sample.nanoseconds << ',' << counts.calls << ',' << counts.requested << ','
            << counts.peak_requested << ',' << counts.live_requested << ',' << counts.peak_malloc << ']';
    }
    std::cout << "]}\n";
}

void report_process_peak() {
#ifdef _WIN32
    PROCESS_MEMORY_COUNTERS memory{};
    memory.cb = sizeof(memory);
    expect(GetProcessMemoryInfo(GetCurrentProcess(), &memory, sizeof(memory)) != 0,
        "process working-set query failed");
    std::cout << "{\"kind\":\"process_peak\",\"metric\":\"windows_peak_working_set_bytes\",\"bytes\":"
        << static_cast<std::uint64_t>(memory.PeakWorkingSetSize) << "}\n";
#else
    rusage memory{};
    expect(getrusage(RUSAGE_SELF, &memory) == 0, "process RSS query failed");
#ifdef __APPLE__
    constexpr std::uint64_t scale = 1;
#else
    constexpr std::uint64_t scale = 1024;
#endif
    std::cout << "{\"kind\":\"process_peak\",\"metric\":\"ru_maxrss_bytes\",\"bytes\":"
        << static_cast<std::uint64_t>(memory.ru_maxrss) * scale << "}\n";
#endif
}

std::size_t count_argument(const char* value) {
    std::size_t end = 0;
    const auto parsed = std::stoull(value, &end);
    expect(end == std::string_view{value}.size() && parsed >= 1 && parsed <= 10001,
        "samples/warmups must be1..10001");
    return static_cast<std::size_t>(parsed);
}
} // namespace

int main(int argc, char** argv) {
    try {
        std::string name = "minimal";
        std::string wire_path;
        std::size_t samples = 101;
        std::size_t warmups = 5;
        for(int i = 1; i < argc; i += 2) {
            expect(i + 1 < argc, "options require values");
            const std::string_view key = argv[i];
            if(key == "--case") name = argv[i + 1];
            else if(key == "--wire-output") wire_path = argv[i + 1];
            else if(key == "--samples") samples = count_argument(argv[i + 1]);
            else if(key == "--warmups") warmups = count_argument(argv[i + 1]);
            else throw std::runtime_error("unknown option");
        }
        expect(name == "minimal" || name == "text_1m" || name == "nested_amxr" ||
            name == "combined_phase1_max" || name == "frame_32m" || name == "extended_frame_32m" ||
            name == "materialization_1m" || name == "implementation_caps_32m", "unknown fixture");
#ifdef AGENT_MEMORY_AMCE_BENCH_LEGACY
        constexpr const char* mode = "closed_string_candidate";
        expect(name != "frame_32m" && name != "extended_frame_32m" && name != "implementation_caps_32m",
            "old candidate has no such qualified profile");
#else
        constexpr const char* mode = "extensible_byte_envelope";
#endif
        std::cout << std::fixed << std::setprecision(3)
            << "{\"kind\":\"configuration\",\"mode\":\"" << mode << "\",\"case\":\"" << name
            << "\",\"compiler\":\"" << __VERSION__ << "\",\"pointer_bytes\":" << sizeof(void*)
            << ",\"raw_columns\":[\"ns\",\"new_calls\",\"new_requested_bytes\",\"peak_live_new_requested_bytes\","
               "\"live_new_requested_bytes_at_return\",\"peak_live_probe_malloc_requested_bytes\"],\"destruction_timed\":false,"
               "\"quantiles\":\"nearest_rank\",\"probe\":\"single_thread_cpp_new_only\"}\n";
        if(name == "materialization_1m") {
            std::vector<std::uint8_t> source(1048576);
            for(std::size_t i = 0; i < source.size(); ++i) source[i] = static_cast<std::uint8_t>(i % 251);
            run("same_bytes_string_copy", samples, warmups,
                [&] { return std::string(reinterpret_cast<const char*>(source.data()), source.size()); },
                [](const auto& value) { observe_bytes(value); });
            run("same_bytes_vector_copy", samples, warmups,
                [&] { return std::vector<std::uint8_t>(source.begin(), source.end()); },
                [](const auto& value) { observe_bytes(value); });
        } else {
            allocation_probe::begin();
            const auto generation_start = Clock::now();
            auto fixture = make_fixture(name);
            const auto generation_end = Clock::now();
            const auto generation_counts = allocation_probe::finish();
            std::cout << "{\"kind\":\"fixture_generation\",\"us\":"
                << std::chrono::duration<double, std::micro>(generation_end - generation_start).count()
                << ",\"new_calls\":" << generation_counts.calls << ",\"new_requested_bytes\":" << generation_counts.requested
                << ",\"peak_live_new_requested_bytes\":" << generation_counts.peak_requested << "}\n";
            expect(validate(fixture).empty(), "fixture is field-invalid");
            const auto encoded = encode(fixture);
            if(!wire_path.empty()) {
                std::ofstream wire(wire_path, std::ios::binary);
                wire.write(reinterpret_cast<const char*>(encoded.data()), static_cast<std::streamsize>(encoded.size()));
                expect(static_cast<bool>(wire), "wire output failed");
            }
            if(name == "frame_32m" || name == "extended_frame_32m" || name == "implementation_caps_32m")
                expect(encoded.size() == 33554432, "maximum fixture must reach exactly32MiB");
            {
                auto recovered = decode(fixture, encoded);
                Fixture round_trip{std::move(recovered)
#ifndef AGENT_MEMORY_AMCE_BENCH_LEGACY
                    , fixture.limits
#endif
                };
                expect(encode(round_trip) == encoded, "qualification round-trip differs");
            }
            std::cout << "{\"kind\":\"fixture\",\"encoded_bytes\":" << encoded.size()
                << ",\"round_trip\":true,\"size_preflight_scope\":\"included_in_encode_not_separate_api\"}\n";
            run("field_validation", samples, warmups, [&] { return validate(fixture); },
                [](const auto& value) { observed ^= value.size(); });
            run("encode_including_exact_preflight", samples, warmups, [&] { return encode(fixture); },
                [](const auto& value) { observe_bytes(value); });
            run("decode", samples, warmups, [&] { return decode(fixture, encoded); },
                [](const auto& value) { observe(value); });
#ifndef AGENT_MEMORY_AMCE_BENCH_LEGACY
            run("header_inspection_only", samples, warmups,
                [&] { return am::inspect_conversation_frame({encoded.data(), encoded.size()}, fixture.limits); },
                [](const auto& value) { observed ^= value.frame_bytes; });
#endif
        }
        report_process_peak();
        std::cout << "{\"kind\":\"completion\",\"status\":\"PASS\",\"observation\":" << observed << "}\n";
        return 0;
    } catch(const std::exception& error) {
        (void)allocation_probe::finish();
        std::cerr << "amce benchmark failed: " << error.what() << '\n';
        return 1;
    }
}
