#include <agent_memory/domain/Sha256.hpp>
#include <agent_memory/storage/FileRawBlobStore.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <future>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) {
        throw std::runtime_error(message);
    }
}

class TempWorkspace final {
public:
    TempWorkspace() = default;
    explicit TempWorkspace(std::filesystem::path path) : m_path(std::move(path)) {}
    TempWorkspace(const TempWorkspace&) = delete;
    TempWorkspace& operator=(const TempWorkspace&) = delete;
    TempWorkspace(TempWorkspace&& other) noexcept : m_path(std::move(other.m_path)) {}
    TempWorkspace& operator=(TempWorkspace&& other) noexcept {
        if(this != &other) {
            cleanup();
            m_path = std::move(other.m_path);
        }
        return *this;
    }
    ~TempWorkspace() { cleanup(); }

    static TempWorkspace create() {
        static std::atomic_uint64_t counter{0};
        const auto base = std::filesystem::temp_directory_path();
        for(unsigned attempt = 0; attempt < 100U; ++attempt) {
            const auto suffix = std::to_string(
                static_cast<unsigned long long>(
                    std::chrono::steady_clock::now().time_since_epoch().count())) +
                "-" + std::to_string(counter.fetch_add(1));
            const auto candidate = base / ("agent-memory-raw-cas-" + suffix);
            std::error_code error;
            if(std::filesystem::create_directory(candidate, error) &&
               !std::filesystem::is_symlink(candidate, error)) {
                return TempWorkspace(candidate);
            }
        }
        throw std::runtime_error("could not create exclusive test workspace");
    }

    [[nodiscard]] const std::filesystem::path& path() const noexcept { return m_path; }

private:
    void cleanup() noexcept {
        if(!m_path.empty()) {
            std::error_code error;
            std::filesystem::remove_all(m_path, error);
            m_path.clear();
        }
    }

    std::filesystem::path m_path;
};

std::string make_body(std::size_t size) {
    std::string body(size, '\0');
    for(std::size_t index = 0; index < body.size(); ++index) {
        body[index] = static_cast<char>('a' + (index % 23U));
    }
    return body;
}

std::filesystem::path body_path(
    const std::filesystem::path& root,
    const agent_memory::ArtifactId& artifact_id
) {
    return root / "sha256" / artifact_id.value().substr(7, 2) /
        (artifact_id.value().substr(7) + ".raw");
}

bool has_temporary_files(const std::filesystem::path& root) {
    std::error_code error;
    if(!std::filesystem::exists(root, error)) {
        return false;
    }
    for(const auto& entry : std::filesystem::recursive_directory_iterator(root, error)) {
        if(error) {
            return true;
        }
        if(entry.path().filename().string().find(".tmp-") != std::string::npos) {
            return true;
        }
    }
    return false;
}

} // namespace

int main() {
    using namespace agent_memory;
    auto workspace = TempWorkspace::create();
    const auto root = workspace.path() / "cas";
    FileRawBlobStore store(root);

    const auto foreign = workspace.path().parent_path() /
        ("agent-memory-raw-cas-foreign-" + std::to_string(
            static_cast<unsigned long long>(
                std::chrono::steady_clock::now().time_since_epoch().count())));
    std::error_code error;
    expect(std::filesystem::create_directory(foreign, error),
           "could not create unrelated directory");
    const auto sentinel = foreign / "keep.txt";
    {
        std::ofstream output(sentinel, std::ios::binary);
        output << "keep";
    }

    const std::string body = "normalized bytes\nsecond line\n";
    const auto stored = store.put(body);
    if(stored.status != RawBlobStatus::Ok || !stored.metadata) {
        std::error_code root_error;
        const auto canonical_root = std::filesystem::weakly_canonical(root, root_error);
        std::cerr << "raw CAS put diagnostic: root=" << root.string()
                  << " absolute=" << std::filesystem::absolute(root).string()
                  << " canonical=" << canonical_root.string()
                  << " canonical_error=" << root_error.message()
                  << " status=" << static_cast<int>(stored.status)
                  << " detail=" << stored.diagnostic << '\n';
    }
    expect(stored.status == RawBlobStatus::Ok && stored.metadata,
           "raw CAS put failed");
    const auto deduplicated = store.put(body);
    expect(deduplicated.status == RawBlobStatus::Ok && deduplicated.metadata &&
               deduplicated.metadata->artifact_id == stored.metadata->artifact_id,
           "raw CAS did not deduplicate identical bytes");
    const auto reopened = FileRawBlobStore(root).probe(stored.metadata->artifact_id);
    expect(reopened.status == RawBlobStatus::Ok && reopened.metadata &&
               reopened.metadata->size_bytes == body.size(),
           "raw CAS did not reopen its body");
    const auto range = store.read_range(stored.metadata->artifact_id, 11, 5);
    expect(range.status == RawBlobStatus::Ok && range.bytes == "bytes",
           "raw CAS bounded range read failed");
    expect(store.read_range(stored.metadata->artifact_id, 0, body.size() + 1).status ==
               RawBlobStatus::InvalidArgument,
           "raw CAS accepted an oversized range");
    const FileRawBlobStoreOptions small_range_options{4};
    const FileRawBlobStore bounded_store(root, small_range_options);
    expect(bounded_store.read_range(stored.metadata->artifact_id, 0, 5).status ==
               RawBlobStatus::InvalidArgument,
           "raw CAS exceeded its configured range budget");
    expect(bounded_store.read_range(stored.metadata->artifact_id, 0, 0).status ==
               RawBlobStatus::Ok,
           "raw CAS rejected a zero-length range at the budget boundary");
    expect(store.read_range(stored.metadata->artifact_id,
                            std::numeric_limits<std::uint64_t>::max(), 1).status ==
               RawBlobStatus::InvalidArgument,
           "raw CAS accepted an overflowing range");
    expect(store.probe(ArtifactId("sha256:not-a-digest")).status ==
               RawBlobStatus::InvalidArgument,
           "raw CAS accepted a malformed artifact id");

    const auto final_link = body_path(root, stored.metadata->artifact_id);
    std::filesystem::remove(final_link, error);
    std::filesystem::create_symlink(sentinel, final_link, error);
    if(!error) {
        expect(store.probe(stored.metadata->artifact_id).status != RawBlobStatus::Ok,
               "CAS followed a symlink final path");
        std::filesystem::remove(final_link, error);
        expect(store.put(body).status == RawBlobStatus::Ok,
               "CAS could not restore a removed safe target");
    }

    const auto empty = store.put("");
    expect(empty.status == RawBlobStatus::Ok && empty.metadata &&
               store.read_range(empty.metadata->artifact_id, 0, 0).status == RawBlobStatus::Ok,
           "raw CAS empty artifact or zero range failed");

    const auto large_body = make_body(2U * 1024U * 1024U);
    Sha256Accumulator accumulator;
    for(std::size_t offset = 0; offset < large_body.size(); offset += 7777U) {
        accumulator.update(std::string_view(
            large_body.data() + offset,
            std::min<std::size_t>(7777U, large_body.size() - offset)));
    }
    expect(accumulator.finish() == sha256(large_body),
           "streaming SHA-256 differs from the reference implementation");
    const auto large = store.put(large_body);
    expect(large.status == RawBlobStatus::Ok && large.metadata,
           "large raw CAS put failed");
    const auto tiny = store.read_range(
        large.metadata->artifact_id, large_body.size() - 3U, 3U);
    expect(tiny.status == RawBlobStatus::Ok && tiny.bytes == large_body.substr(
               large_body.size() - 3U),
           "large artifact tiny range failed");

    const auto large_path = body_path(root, large.metadata->artifact_id);
    {
        std::fstream output(large_path, std::ios::binary | std::ios::in | std::ios::out);
        output.seekp(0);
        output.put('!');
    }
    expect(store.read_range(large.metadata->artifact_id,
                            large_body.size() - 3U, 3U).status == RawBlobStatus::Corrupt,
           "corruption outside requested range was not detected");
    expect(store.put(large_body).status == RawBlobStatus::Corrupt,
           "conflicting target was overwritten instead of rejected");
    expect(!has_temporary_files(root), "failed publication left an owned temporary file");

    const auto writer_root = workspace.path() / "writers";
    const auto writer_a = make_body(64U * 1024U + 17U);
    const auto writer_b = make_body(64U * 1024U + 31U) + "different";
    auto first = std::async(std::launch::async, [&] {
        return FileRawBlobStore(writer_root).put(writer_a);
    });
    auto second = std::async(std::launch::async, [&] {
        return FileRawBlobStore(writer_root).put(writer_a);
    });
    const auto first_result = first.get();
    const auto second_result = second.get();
    expect(first_result.status == RawBlobStatus::Ok && second_result.status == RawBlobStatus::Ok &&
               first_result.metadata->artifact_id == second_result.metadata->artifact_id,
           "concurrent same-digest writers did not deduplicate safely");
    auto different_first = std::async(std::launch::async, [&] {
        return FileRawBlobStore(writer_root).put(writer_a);
    });
    auto different_second = std::async(std::launch::async, [&] {
        return FileRawBlobStore(writer_root).put(writer_b);
    });
    expect(different_first.get().status == RawBlobStatus::Ok &&
               different_second.get().status == RawBlobStatus::Ok,
           "concurrent different-digest writers failed");

    const auto redirect_target = workspace.path() / "redirect-target";
    const auto redirect = workspace.path() / "redirect";
    std::filesystem::create_directory(redirect_target, error);
    std::filesystem::create_directory_symlink(redirect_target, redirect, error);
    if(!error) {
        expect(FileRawBlobStore(redirect).put("redirected").status != RawBlobStatus::Ok,
               "CAS followed a symlink root outside its workspace");
        expect(FileRawBlobStore(redirect / "nested-cas").put("redirected-parent").status !=
                   RawBlobStatus::Ok,
               "CAS followed a symlinked parent outside its workspace");
        std::filesystem::remove(redirect, error);
    }

    expect(std::filesystem::exists(sentinel),
           "CAS test touched an unrelated existing directory");
    std::filesystem::remove(sentinel, error);
    std::filesystem::remove(foreign, error);
    std::cout << "file raw CAS: PASS\n";
    return 0;
}
