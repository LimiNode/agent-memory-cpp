// Diagnostic-only reproducer; no agent_memory dependency or CTest registration.
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <new>
#include <string>
#include <utility>

#if defined(_WIN32)
#include <process.h>
#endif

static_assert(noexcept(std::string{}), "The reproducer requires noexcept string construction");
static_assert(noexcept(std::string{std::declval<std::string&&>()}),
    "The reproducer requires noexcept string move construction");

namespace {
    bool fail_allocations = false;

    [[noreturn]] void exit_without_unwind(const int status) {
#if defined(_WIN32)
        ::_exit(status);
#else
        std::_Exit(status);
#endif
    }
}

void* operator new(const std::size_t size) {
    if(fail_allocations) {
        throw std::bad_alloc{};
    }
    if(auto* result = std::malloc(size == 0 ? 1 : size)) {
        return result;
    }
    throw std::bad_alloc{};
}

void operator delete(void* pointer) noexcept {
    std::free(pointer);
}

void operator delete(void* pointer, const std::size_t) noexcept {
    std::free(pointer);
}

int main() {
    std::set_terminate([] {
        try {
            const auto error = std::current_exception();
            if(error) {
                std::rethrow_exception(error);
            }
        } catch(const std::bad_alloc&) {
            std::fputs("EXPECTED: bad_alloc in noexcept string construction\n", stderr);
            exit_without_unwind(73);
        } catch(...) {
        }
        exit_without_unwind(74);
    });

    std::string original{"source"};
    fail_allocations = true;
    try {
        const std::string moved{std::move(original)};
        fail_allocations = false;
        std::printf("PASS: string move completed, size=%zu\n", moved.size());
        return 0;
    } catch(const std::bad_alloc&) {
        fail_allocations = false;
        std::fputs("UNEXPECTED: string move propagated bad_alloc\n", stderr);
        return 75;
    }
}
