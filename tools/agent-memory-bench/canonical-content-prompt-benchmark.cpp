#include <agent_memory.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>
#endif

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <new>
#include <string>
#include <string_view>
#include <stdexcept>
#include <vector>

#if defined(_MSC_VER)
#include <malloc.h>
#endif

namespace {

struct AllocationHeader final {
    std::size_t bytes;
    void* base;
    std::uint64_t magic;
    bool tracked;
};

constexpr std::uint64_t kAllocationMagic = 0x41504f4c4c4f4355ULL;
std::atomic<bool> g_track_allocations{false};
std::atomic<std::size_t> g_live_bytes{0};
std::atomic<std::size_t> g_peak_bytes{0};

void* allocate_bytes(std::size_t bytes, std::size_t alignment) {
    const auto requested = bytes == 0 ? std::size_t{1} : bytes;
    const auto extra = sizeof(AllocationHeader) + alignment - 1;
    void* base = std::malloc(requested + extra);
    if(!base) {
        throw std::bad_alloc{};
    }
    const auto raw = reinterpret_cast<std::uintptr_t>(base) + sizeof(AllocationHeader);
    const auto aligned = (raw + alignment - 1) & ~(alignment - 1);
    auto* header = reinterpret_cast<AllocationHeader*>(aligned - sizeof(AllocationHeader));
    header->bytes = requested;
    header->base = base;
    header->magic = kAllocationMagic;
    header->tracked = g_track_allocations.load(std::memory_order_relaxed);
    if(header->tracked) {
        const auto live = g_live_bytes.fetch_add(requested, std::memory_order_relaxed) + requested;
        auto peak = g_peak_bytes.load(std::memory_order_relaxed);
        while(live > peak && !g_peak_bytes.compare_exchange_weak(
                  peak, live, std::memory_order_relaxed, std::memory_order_relaxed)) {
        }
    }
    return reinterpret_cast<void*>(aligned);
}

void release_bytes(void* pointer) noexcept {
    if(!pointer) {
        return;
    }
    auto* header = reinterpret_cast<AllocationHeader*>(
        reinterpret_cast<std::uintptr_t>(pointer) - sizeof(AllocationHeader));
    if(header->magic == kAllocationMagic) {
        if(header->tracked) {
            g_live_bytes.fetch_sub(header->bytes, std::memory_order_relaxed);
        }
        std::free(header->base);
    }
}

} // namespace

void* operator new(std::size_t bytes) { return allocate_bytes(bytes, alignof(std::max_align_t)); }
void* operator new[](std::size_t bytes) { return allocate_bytes(bytes, alignof(std::max_align_t)); }
void* operator new(std::size_t bytes, const std::nothrow_t&) noexcept {
    try { return allocate_bytes(bytes, alignof(std::max_align_t)); } catch(...) { return nullptr; }
}
void* operator new[](std::size_t bytes, const std::nothrow_t&) noexcept {
    try { return allocate_bytes(bytes, alignof(std::max_align_t)); } catch(...) { return nullptr; }
}
void operator delete(void* pointer) noexcept { release_bytes(pointer); }
void operator delete[](void* pointer) noexcept { release_bytes(pointer); }
void operator delete(void* pointer, std::size_t) noexcept { release_bytes(pointer); }
void operator delete[](void* pointer, std::size_t) noexcept { release_bytes(pointer); }
void* operator new(std::size_t bytes, std::align_val_t alignment) {
    return allocate_bytes(bytes, static_cast<std::size_t>(alignment));
}
void* operator new[](std::size_t bytes, std::align_val_t alignment) {
    return allocate_bytes(bytes, static_cast<std::size_t>(alignment));
}
void operator delete(void* pointer, std::align_val_t) noexcept { release_bytes(pointer); }
void operator delete[](void* pointer, std::align_val_t) noexcept { release_bytes(pointer); }
void operator delete(void* pointer, std::size_t, std::align_val_t) noexcept { release_bytes(pointer); }
void operator delete[](void* pointer, std::size_t, std::align_val_t) noexcept { release_bytes(pointer); }

namespace {

using namespace agent_memory;
using Clock = std::chrono::steady_clock;

struct Sample final {
    std::uint64_t read_block_ns = 0;
    std::uint64_t read_revision_ns = 0;
    std::uint64_t commit_ns = 0;
    std::uint64_t materialize_ns = 0;
    std::size_t peak_alloc_bytes = 0;
    std::uintmax_t mdbx_bytes = 0;
    std::uintmax_t mdbx_growth_bytes = 0;
};

template<typename Callable>
std::uint64_t timed(Callable&& callable, std::size_t& peak_alloc_bytes) {
    const auto live_before = g_live_bytes.load(std::memory_order_relaxed);
    g_peak_bytes.store(live_before, std::memory_order_relaxed);
    struct TrackingScope final {
        TrackingScope() { g_track_allocations.store(true, std::memory_order_relaxed); }
        ~TrackingScope() { g_track_allocations.store(false, std::memory_order_relaxed); }
    } tracking;
    const auto start = Clock::now();
    callable();
    const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start);
    const auto peak_total = g_peak_bytes.load(std::memory_order_relaxed);
    peak_alloc_bytes = peak_total >= live_before ? peak_total - live_before : 0;
    return static_cast<std::uint64_t>(elapsed.count());
}

std::uintmax_t file_size_or_throw(const std::filesystem::path& path) {
    std::error_code error;
    const auto size = std::filesystem::file_size(path, error);
    if(error) {
        throw std::runtime_error("cannot read MDBX file size: " + error.message());
    }
    return size;
}

CanonicalDocumentRevision make_document(const DocumentId& id, std::size_t block_count) {
    std::vector<ContentBlock> blocks;
    blocks.reserve(block_count);
    for(std::size_t index = 0; index < block_count; ++index) {
        blocks.push_back({ContentBlockId{"block-" + std::to_string(index)}, 1,
                          ContentBlockKind::Paragraph, std::nullopt,
                          "prompt block " + std::to_string(index) + " with stable content"});
    }
    return {id, 0, {}, std::move(blocks)};
}

template<typename StoreFactory>
Sample measure_backend(std::size_t block_count, StoreFactory&& make_store,
                       const std::filesystem::path* mdbx_path) {
    const DocumentId id{"bench:prompt"};
    auto store = make_store();
    if(!store->create_document(make_document(id, block_count))) {
        throw std::runtime_error("benchmark document creation failed");
    }
    const auto initial_mdbx_bytes = mdbx_path
        ? file_size_or_throw(*mdbx_path)
        : std::uintmax_t{0};
    Sample sample;
    const auto target = ContentBlockId{"block-" + std::to_string(block_count / 2)};
    std::size_t peak = 0;
    sample.read_block_ns = timed([&] {
        for(std::size_t repeat = 0; repeat < 5; ++repeat) {
            if(!store->read_block(id, target, 0)) throw std::runtime_error("read_block failed");
        }
    }, peak) / 5;
    sample.peak_alloc_bytes = std::max(sample.peak_alloc_bytes, peak);
    sample.read_revision_ns = timed([&] {
        for(std::size_t repeat = 0; repeat < 5; ++repeat) {
            if(!store->read_revision(id, 0)) throw std::runtime_error("read_revision failed");
        }
    }, peak) / 5;
    sample.peak_alloc_bytes = std::max(sample.peak_alloc_bytes, peak);
    sample.materialize_ns = timed([&] {
        for(std::size_t repeat = 0; repeat < 3; ++repeat) {
            if(!store->materialize_markdown(id, 0)) throw std::runtime_error("materialize failed");
        }
    }, peak) / 3;
    sample.peak_alloc_bytes = std::max(sample.peak_alloc_bytes, peak);
    sample.commit_ns = timed([&] {
        for(std::size_t repeat = 0; repeat < 3; ++repeat) {
            const auto result = store->commit(
                {id, static_cast<std::uint64_t>(repeat),
                 {ReplaceBlockText{target, "edited prompt block " + std::to_string(repeat)}}});
            if(result.status != CanonicalEditStatus::Ok) throw std::runtime_error("commit failed");
        }
    }, peak) / 3;
    sample.peak_alloc_bytes = std::max(sample.peak_alloc_bytes, peak);
    if(mdbx_path) {
        sample.mdbx_bytes = file_size_or_throw(*mdbx_path);
        sample.mdbx_growth_bytes = sample.mdbx_bytes > initial_mdbx_bytes
            ? sample.mdbx_bytes - initial_mdbx_bytes : 0;
    }
    return sample;
}

void print_sample(std::ostream& output, std::string_view backend,
                  std::size_t blocks, const Sample& sample) {
    output << backend << '\t' << blocks << '\t'
           << sample.read_block_ns << '\t' << sample.read_revision_ns << '\t'
           << sample.commit_ns << '\t' << sample.materialize_ns << '\t'
           << sample.peak_alloc_bytes << '\t' << sample.mdbx_bytes << '\t'
           << sample.mdbx_growth_bytes << '\n';
}

} // namespace

int main(int argc, char** argv) {
    try {
        std::ofstream file;
        std::ostream* output = &std::cout;
        if(argc == 2) {
            file.open(argv[1], std::ios::trunc);
            if(!file) return 2;
            output = &file;
        } else if(argc > 2) {
            std::cerr << "usage: canonical-content-prompt-benchmark [output.tsv]\n";
            return 2;
        }
        *output << "backend\tblocks\tread_block_ns\tread_revision_ns\tcommit_ns\tmaterialize_ns\tpeak_alloc_bytes\tmdbx_bytes\tmdbx_growth_bytes\n";
        constexpr std::array<std::size_t, 4> sizes{{32, 128, 512, 2048}};
        for(const auto blocks : sizes) {
            const auto in_memory = measure_backend(
                blocks,
                [] { return std::make_unique<InMemoryCanonicalContentStore>(); },
                nullptr);
            print_sample(*output, "in-memory", blocks, in_memory);
#if AGENT_MEMORY_HAS_MDBX
            const auto path = std::filesystem::temp_directory_path() /
                              ("agent_memory_prompt_bench_" + std::to_string(blocks) + ".mdbx");
            std::error_code error;
            std::filesystem::remove(path, error);
            const auto mdbx = measure_backend(
                blocks,
                [&] {
                    auto context = MdbxStorageContext::open(path.string());
                    MdbxCanonicalContentStoreOptions options;
                    options.context = std::move(context);
                    return std::make_unique<MdbxCanonicalContentStore>(std::move(options));
                },
                &path);
            print_sample(*output, "mdbx-c1", blocks, mdbx);
            std::filesystem::remove(path, error);
#endif
        }
        return 0;
    } catch(const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
}
