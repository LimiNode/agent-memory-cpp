#pragma once
#ifndef AGENT_MEMORY_HEADER_TESTS_SUPPORT_ALLOCATION_FAILURE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_TESTS_SUPPORT_ALLOCATION_FAILURE_HPP_INCLUDED

// Include in exactly one translation unit per isolated fault-test executable.
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <new>
#include <stdexcept>
#if defined(_WIN32)
#include <malloc.h>
#endif

namespace {
// Isolated single-threaded standard replacement functions, not runtime detours.
// Fixtures/assertions allocate outside the one-shot operation gate.
struct AllocationGate final { bool is_enabled = false; bool has_failed = false; std::size_t calls = 0, fail_at = 0; };
thread_local AllocationGate g_gate;
void check_allocation() {
    if(!g_gate.is_enabled) return;
    if(++g_gate.calls == g_gate.fail_at) {
        g_gate.has_failed = true; g_gate.is_enabled = false;
        throw std::bad_alloc{};
    }
}
void* allocate(std::size_t size) {
    check_allocation();
    if(auto* result = std::malloc(size ? size : 1)) return result;
    throw std::bad_alloc{};
}
void* allocate_aligned(std::size_t size, std::size_t alignment) {
    check_allocation();
    if(size > std::numeric_limits<std::size_t>::max() - (alignment - 1)) throw std::bad_alloc{};
    const auto rounded = size ? ((size + alignment - 1) / alignment) * alignment : alignment;
#if defined(_WIN32)
    if(auto* result = _aligned_malloc(rounded, alignment)) return result;
#else
    void* result = nullptr;
    if(posix_memalign(&result, alignment, rounded) == 0) return result;
#endif
    throw std::bad_alloc{};
}
void release_aligned(void* pointer) noexcept {
#if defined(_WIN32)
    _aligned_free(pointer);
#else
    std::free(pointer);
#endif
}
struct ScopedGate final {
    explicit ScopedGate(std::size_t ordinal) { g_gate = {true, false, 0, ordinal}; }
    ~ScopedGate() { g_gate.is_enabled = false; }
};
} // namespace
void* operator new(std::size_t n) { return allocate(n); }
void* operator new[](std::size_t n) { return allocate(n); }
void* operator new(std::size_t n, const std::nothrow_t&) noexcept {
    try { return allocate(n); } catch(const std::bad_alloc&) { return nullptr; }
}
void* operator new[](std::size_t n, const std::nothrow_t&) noexcept {
    try { return allocate(n); } catch(const std::bad_alloc&) { return nullptr; }
}
void* operator new(std::size_t n, std::align_val_t a) { return allocate_aligned(n, static_cast<std::size_t>(a)); }
void* operator new[](std::size_t n, std::align_val_t a) { return allocate_aligned(n, static_cast<std::size_t>(a)); }
void* operator new(std::size_t n, std::align_val_t a, const std::nothrow_t&) noexcept {
    try { return allocate_aligned(n, static_cast<std::size_t>(a)); } catch(const std::bad_alloc&) { return nullptr; }
}
void* operator new[](std::size_t n, std::align_val_t a, const std::nothrow_t&) noexcept {
    try { return allocate_aligned(n, static_cast<std::size_t>(a)); } catch(const std::bad_alloc&) { return nullptr; }
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
void operator delete(void* p, const std::nothrow_t&) noexcept { std::free(p); }
void operator delete[](void* p, const std::nothrow_t&) noexcept { std::free(p); }
void operator delete(void* p, std::align_val_t) noexcept { release_aligned(p); }
void operator delete[](void* p, std::align_val_t) noexcept { release_aligned(p); }
void operator delete(void* p, std::size_t, std::align_val_t) noexcept { release_aligned(p); }
void operator delete[](void* p, std::size_t, std::align_val_t) noexcept { release_aligned(p); }
void operator delete(void* p, std::align_val_t, const std::nothrow_t&) noexcept { release_aligned(p); }
void operator delete[](void* p, std::align_val_t, const std::nothrow_t&) noexcept { release_aligned(p); }

#endif
