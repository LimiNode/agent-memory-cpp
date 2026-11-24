// Standalone standard-library diagnostic; intentionally not a passing CTest.
// See the Phase 3B qualification report for commands and the observed failure.
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <new>
#include <string>
#include <variant>
#include <vector>

namespace {
bool g_fail_next = false;
}

void* operator new(std::size_t size) {
    if(g_fail_next) {
        g_fail_next = false;
        throw std::bad_alloc{};
    }
    if(void* pointer = std::malloc(size ? size : 1)) return pointer;
    throw std::bad_alloc{};
}
void operator delete(void* pointer) noexcept { std::free(pointer); }
void operator delete(void* pointer, std::size_t) noexcept { std::free(pointer); }

int main(int argc, char**) {
    using Value = std::variant<std::int64_t, std::uint64_t, std::string, std::vector<int>>;
    const Value source{std::in_place_type<std::string>, 48, 'x'};
    try {
        g_fail_next = true;
        if(argc == 1) {
            const Value copy(source);
            std::printf("copied %zu bytes\n", std::get<std::string>(copy).size());
        } else {
            const Value copy(std::in_place_type<std::string>, std::get<std::string>(source));
            std::printf("constructed %zu bytes\n", std::get<std::string>(copy).size());
        }
    } catch(const std::bad_alloc&) {
        std::puts("PASS caught bad_alloc; source still valid");
        return std::get<std::string>(source) == std::string(48, 'x') ? 0 : 2;
    }
    return 1;
}
