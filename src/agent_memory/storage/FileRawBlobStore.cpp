#include "FileRawBlobStore.hpp"

#include <agent_memory/domain/Sha256.hpp>

#include <array>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstdio>
#include <fstream>
#include <functional>
#include <limits>
#include <system_error>
#include <thread>
#include <utility>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <fcntl.h>
#include <io.h>
#include <windows.h>
#else
#include <cerrno>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace agent_memory {
namespace {

std::atomic_uint64_t temporary_counter{0};

BlobDigest digest_for(std::string_view bytes) noexcept {
    BlobDigest digest;
    digest.value = sha256(bytes);
    return digest;
}

bool is_canonical_artifact_id(const ArtifactId& artifact_id) {
    const auto& value = artifact_id.value();
    if(value.size() != 71 || value.rfind("sha256:", 0) != 0) {
        return false;
    }
    for(std::size_t index = 7; index < value.size(); ++index) {
        const auto character = value[index];
        const bool hex = (character >= '0' && character <= '9') ||
            (character >= 'a' && character <= 'f');
        if(!hex) {
            return false;
        }
    }
    return true;
}

bool is_link_or_reparse(const std::filesystem::path& path) {
    std::error_code error;
    const auto status = std::filesystem::symlink_status(path, error);
    if(!error && std::filesystem::is_symlink(status)) {
        return true;
    }
#ifdef _WIN32
    const auto attributes = GetFileAttributesW(path.c_str());
    if(attributes != INVALID_FILE_ATTRIBUTES &&
       (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
        return true;
    }
#endif
    return false;
}

bool is_trusted_platform_ancestor(const std::filesystem::path& path) {
#ifdef __APPLE__
    // macOS exposes these stable system aliases as symlinks to /private/*.
    // They are outside the caller-owned CAS root and are safe to traverse.
    const auto normalized = path.lexically_normal();
    return normalized == std::filesystem::path("/var") ||
        normalized == std::filesystem::path("/tmp");
#else
    (void)path;
    return false;
#endif
}

bool has_link_component(const std::filesystem::path& path) {
    auto current = path;
    while(!current.empty()) {
        if(is_link_or_reparse(current) && !is_trusted_platform_ancestor(current)) {
            return true;
        }
        const auto parent = current.parent_path();
        if(parent == current) {
            break;
        }
        current = parent;
    }
    return false;
}

std::string path_diagnostic(
    const char* operation,
    const std::filesystem::path& path,
    const std::error_code& error
) {
    std::string diagnostic = operation;
    diagnostic += " (path=";
    diagnostic += path.string();
    diagnostic += ")";
    if(error) {
        diagnostic += ": ";
        diagnostic += error.message();
    }
    return diagnostic;
}

bool is_missing_path_error(const std::error_code& error) noexcept {
    if(!error || error == std::make_error_code(std::errc::no_such_file_or_directory)) {
        return true;
    }
#ifdef _WIN32
    return error.value() == 2 || error.value() == 3;
#else
    return false;
#endif
}

RawBlobResult failure(RawBlobStatus status, std::string diagnostic) {
    RawBlobResult result;
    result.status = status;
    result.diagnostic = std::move(diagnostic);
    return result;
}

RawBlobReadResult read_failure(RawBlobStatus status, std::string diagnostic) {
    RawBlobReadResult result;
    result.status = status;
    result.diagnostic = std::move(diagnostic);
    return result;
}

enum class ExclusiveWriteResult { Created, AlreadyExists, Failed };

ExclusiveWriteResult write_exclusive(
    const std::filesystem::path& path,
    std::string_view bytes
) {
#ifdef _WIN32
    const int descriptor = _open(
        path.string().c_str(), _O_BINARY | _O_CREAT | _O_EXCL | _O_WRONLY,
        _S_IREAD | _S_IWRITE);
    if(descriptor < 0) {
        return errno == EEXIST
            ? ExclusiveWriteResult::AlreadyExists
            : ExclusiveWriteResult::Failed;
    }
    auto* stream = _fdopen(descriptor, "wb");
#else
    const int descriptor = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
    if(descriptor < 0) {
        return errno == EEXIST
            ? ExclusiveWriteResult::AlreadyExists
            : ExclusiveWriteResult::Failed;
    }
    auto* stream = ::fdopen(descriptor, "wb");
#endif
    if(stream == nullptr) {
#ifdef _WIN32
        _close(descriptor);
#else
        ::close(descriptor);
#endif
        return ExclusiveWriteResult::Failed;
    }
    const auto written = std::fwrite(bytes.data(), 1, bytes.size(), stream);
    const bool flushed = std::fflush(stream) == 0;
    const bool closed = std::fclose(stream) == 0;
    return written == bytes.size() && flushed && closed
        ? ExclusiveWriteResult::Created
        : ExclusiveWriteResult::Failed;
}

enum class PublishResult { Published, TargetExists, Failed };

PublishResult publish_no_clobber(
    const std::filesystem::path& temporary,
    const std::filesystem::path& target
) {
#ifdef _WIN32
    if(CreateHardLinkW(target.c_str(), temporary.c_str(), nullptr) != 0) {
        return PublishResult::Published;
    }
    const auto error = GetLastError();
    if(error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS) {
        return PublishResult::TargetExists;
    }
#else
    if(::link(temporary.c_str(), target.c_str()) == 0) {
        return PublishResult::Published;
    }
    if(errno == EEXIST) {
        return PublishResult::TargetExists;
    }
#endif
    return PublishResult::Failed;
}

bool safe_regular_file(const std::filesystem::path& path) {
    if(is_link_or_reparse(path)) {
        return false;
    }
    std::error_code error;
    const auto status = std::filesystem::symlink_status(path, error);
    return !error && std::filesystem::is_regular_file(status);
}

} // namespace

FileRawBlobStore::FileRawBlobStore(std::filesystem::path root)
    : FileRawBlobStore(std::move(root), FileRawBlobStoreOptions{}) {}

FileRawBlobStore::FileRawBlobStore(
    std::filesystem::path root,
    FileRawBlobStoreOptions options
)
    : m_options(options) {
    std::error_code error;
    m_root = std::filesystem::absolute(std::move(root), error);
    if(error) {
        m_root.clear();
    }
}

std::filesystem::path FileRawBlobStore::path_for(const ArtifactId& id) const {
    const auto value = id.value();
    return m_root / "sha256" / value.substr(7, 2) /
        (value.substr(7) + ".raw");
}

RawBlobResult FileRawBlobStore::put(std::string_view bytes) const {
    if(m_root.empty() || has_link_component(m_root)) {
        return failure(
            RawBlobStatus::InvalidArgument,
            path_diagnostic("CAS root is not a safe directory", m_root, {}));
    }
    const auto digest = digest_for(bytes);
    const auto artifact_id = artifact_id_for_digest(digest);
    const auto path = path_for(artifact_id);
    std::error_code error;
    std::filesystem::create_directories(path.parent_path(), error);
    if(error || has_link_component(path.parent_path())) {
        return failure(
            RawBlobStatus::IoError,
            path_diagnostic("failed to create a safe CAS directory", path.parent_path(), error));
    }

    const auto target_status = std::filesystem::symlink_status(path, error);
    if(error && !is_missing_path_error(error)) {
        return failure(
            RawBlobStatus::IoError,
            path_diagnostic("failed to inspect CAS target", path, error));
    }
    if(!error && std::filesystem::exists(target_status)) {
        if(!safe_regular_file(path)) {
            return failure(RawBlobStatus::Corrupt, "CAS target is not a regular immutable file");
        }
        return verify_file(artifact_id, path);
    }

    const auto nonce = temporary_counter.fetch_add(1, std::memory_order_relaxed);
    const auto clock = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto thread = std::hash<std::thread::id>{}(std::this_thread::get_id());
    const auto temporary = path.string() + ".tmp-" +
        std::to_string(static_cast<unsigned long long>(clock)) + "-" +
        std::to_string(static_cast<unsigned long long>(thread)) + "-" +
        std::to_string(static_cast<unsigned long long>(nonce));
    const auto temporary_write = write_exclusive(temporary, bytes);
    if(temporary_write != ExclusiveWriteResult::Created) {
        if(temporary_write == ExclusiveWriteResult::Failed) {
            std::filesystem::remove(temporary, error);
        }
        return failure(
            RawBlobStatus::IoError,
            path_diagnostic("failed to exclusively write CAS temporary body", temporary, {}));
    }

    const auto published = publish_no_clobber(temporary, path);
    const bool removed = std::filesystem::remove(temporary, error);
    if(!removed && error) {
        return failure(
            RawBlobStatus::IoError,
            path_diagnostic("failed to clean up CAS temporary body", temporary, error));
    }
    if(published == PublishResult::TargetExists) {
        return safe_regular_file(path)
            ? verify_file(artifact_id, path)
            : failure(RawBlobStatus::Corrupt, "CAS target is not a regular immutable file");
    }
    if(published == PublishResult::Failed) {
        return failure(
            RawBlobStatus::IoError,
            path_diagnostic("failed to publish CAS body without clobbering", path, {}));
    }
    return verify_file(artifact_id, path);
}

RawBlobResult FileRawBlobStore::verify_file(
    const ArtifactId& artifact_id,
    const std::filesystem::path& path
) const {
    if(!safe_regular_file(path)) {
        return failure(RawBlobStatus::Corrupt, "CAS body is not a safe regular file");
    }
    std::ifstream input(path, std::ios::binary);
    if(!input) {
        return failure(RawBlobStatus::IoError, "failed to open CAS body");
    }
    std::array<char, 64U * 1024U> buffer{};
    Sha256Accumulator accumulator;
    std::uint64_t size = 0;
    while(input) {
        input.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        const auto count = input.gcount();
        if(count > 0) {
            accumulator.update(std::string_view(buffer.data(), static_cast<std::size_t>(count)));
            size += static_cast<std::uint64_t>(count);
        }
    }
    if(!input.eof()) {
        return failure(RawBlobStatus::IoError, "failed to read CAS body");
    }
    BlobDigest digest;
    digest.value = accumulator.finish();
    if(artifact_id_for_digest(digest) != artifact_id) {
        return failure(RawBlobStatus::Corrupt, "CAS body digest does not match its path");
    }
    RawBlobResult result;
    result.status = RawBlobStatus::Ok;
    result.metadata = RawBlobMetadata{artifact_id, digest, size};
    return result;
}

RawBlobResult FileRawBlobStore::probe(const ArtifactId& artifact_id) const {
    if(!is_canonical_artifact_id(artifact_id)) {
        return failure(RawBlobStatus::InvalidArgument, "invalid artifact identity");
    }
    if(m_root.empty() || has_link_component(m_root)) {
        return failure(
            RawBlobStatus::InvalidArgument,
            path_diagnostic("CAS root is not a safe directory", m_root, {}));
    }
    const auto path = path_for(artifact_id);
    std::error_code error;
    const auto status = std::filesystem::symlink_status(path, error);
    if(error && !is_missing_path_error(error)) {
        return failure(
            RawBlobStatus::IoError,
            path_diagnostic("failed to inspect CAS body", path, error));
    }
    if(error || !std::filesystem::exists(status)) {
        return failure(RawBlobStatus::NotFound, "CAS body was not found");
    }
    if(has_link_component(path.parent_path())) {
        return failure(RawBlobStatus::Corrupt, "CAS path contains a symlink or reparse point");
    }
    return verify_file(artifact_id, path);
}

RawBlobReadResult FileRawBlobStore::read_range(
    const ArtifactId& artifact_id,
    std::uint64_t offset,
    std::uint64_t length
) const {
    if(!is_canonical_artifact_id(artifact_id) ||
       length > m_options.max_read_range_bytes ||
       length > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()) ||
       length > static_cast<std::uint64_t>(std::numeric_limits<std::streamsize>::max()) ||
       offset > std::numeric_limits<std::uint64_t>::max() - length) {
        return read_failure(
            RawBlobStatus::InvalidArgument,
            length > m_options.max_read_range_bytes
                ? "CAS range exceeds configured read budget"
                : "invalid or overflowing CAS range");
    }
    if(m_root.empty() || has_link_component(m_root)) {
        return read_failure(
            RawBlobStatus::InvalidArgument,
            path_diagnostic("CAS root is not a safe directory", m_root, {}));
    }
    const auto path = path_for(artifact_id);
    if(has_link_component(path.parent_path())) {
        return read_failure(RawBlobStatus::Corrupt, "CAS path contains a symlink or reparse point");
    }
    if(!safe_regular_file(path)) {
        return read_failure(RawBlobStatus::NotFound, "CAS body was not found");
    }
    std::ifstream input(path, std::ios::binary);
    if(!input) {
        return read_failure(RawBlobStatus::IoError, "failed to open CAS body");
    }
    std::array<char, 64U * 1024U> buffer{};
    Sha256Accumulator accumulator;
    std::string result_bytes;
    result_bytes.reserve(static_cast<std::size_t>(length));
    std::uint64_t position = 0;
    while(input) {
        input.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        const auto count = input.gcount();
        if(count > 0) {
            const auto count_u64 = static_cast<std::uint64_t>(count);
            accumulator.update(std::string_view(buffer.data(), static_cast<std::size_t>(count)));
            const auto end = position + count_u64;
            const auto requested_end = offset + length;
            const auto copy_begin = std::max(position, offset);
            const auto copy_end = std::min(end, requested_end);
            if(copy_begin < copy_end) {
                const auto local_offset = static_cast<std::size_t>(copy_begin - position);
                const auto copy_size = static_cast<std::size_t>(copy_end - copy_begin);
                result_bytes.append(buffer.data() + local_offset, copy_size);
            }
            position = end;
        }
    }
    if(!input.eof()) {
        return read_failure(RawBlobStatus::IoError, "failed to read CAS body");
    }
    BlobDigest digest;
    digest.value = accumulator.finish();
    if(artifact_id_for_digest(digest) != artifact_id) {
        return read_failure(RawBlobStatus::Corrupt, "CAS body changed or failed digest verification");
    }
    if(offset > position || length > position - offset) {
        return read_failure(RawBlobStatus::InvalidArgument, "CAS range is outside the body");
    }
    RawBlobReadResult result;
    result.status = RawBlobStatus::Ok;
    result.bytes = std::move(result_bytes);
    return result;
}

} // namespace agent_memory
