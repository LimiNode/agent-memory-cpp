#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_FILE_RAW_BLOB_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_FILE_RAW_BLOB_STORE_HPP_INCLUDED

/// \file FileRawBlobStore.hpp
/// \brief Small file-backed Raw CAS for immutable decoded artifact bytes.

#include <agent_memory/domain/Artifact.hpp>

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>

namespace agent_memory {

enum class RawBlobStatus : std::uint8_t {
    Ok,
    InvalidArgument,
    NotFound,
    Corrupt,
    IoError
};

struct RawBlobMetadata final {
    ArtifactId artifact_id;
    BlobDigest digest;
    std::uint64_t size_bytes = 0;
};

struct RawBlobResult final {
    RawBlobStatus status = RawBlobStatus::IoError;
    std::optional<RawBlobMetadata> metadata;
    std::string diagnostic;
};

struct RawBlobReadResult final {
    RawBlobStatus status = RawBlobStatus::IoError;
    std::string bytes;
    std::string diagnostic;
};

struct FileRawBlobStoreOptions final {
    /// \brief Maximum number of bytes materialized by one read_range call.
    ///
    /// Full-file digest verification remains streaming and is not limited by
    /// this value. Callers can read larger artifacts in several verified
    /// ranges when needed.
    std::uint64_t max_read_range_bytes = 64U * 1024U * 1024U;
};

/// \brief File-backed Raw CAS with codec-independent SHA-256 identities.
///
/// Files are written through an exclusive temporary sibling and atomically
/// linked into a deterministic `sha256/<prefix>/<digest>.raw` path without
/// clobbering an existing target. Reads verify the complete opened byte stream
/// with bounded memory before returning a requested range. This establishes
/// atomic visibility and verified integrity for ordinary trusted local storage,
/// not crash durability or protection against a hostile process that can mutate
/// the filesystem concurrently. No fsync-equivalent, ingest lease, catalog
/// commit, orphan recovery, GC or compression metadata is provided here. Those
/// are separate publication layers.
class FileRawBlobStore final {
public:
    explicit FileRawBlobStore(std::filesystem::path root);
    FileRawBlobStore(
        std::filesystem::path root,
        FileRawBlobStoreOptions options
    );

    [[nodiscard]] RawBlobResult put(std::string_view bytes) const;
    [[nodiscard]] RawBlobResult probe(const ArtifactId& artifact_id) const;
    [[nodiscard]] RawBlobReadResult read_range(
        const ArtifactId& artifact_id,
        std::uint64_t offset,
        std::uint64_t length
    ) const;

    [[nodiscard]] const std::filesystem::path& root() const noexcept {
        return m_root;
    }

    [[nodiscard]] std::uint64_t max_read_range_bytes() const noexcept {
        return m_options.max_read_range_bytes;
    }

private:
    [[nodiscard]] std::filesystem::path path_for(const ArtifactId& id) const;
    [[nodiscard]] RawBlobResult verify_file(
        const ArtifactId& artifact_id,
        const std::filesystem::path& path
    ) const;

    std::filesystem::path m_root;
    FileRawBlobStoreOptions m_options;
};

} // namespace agent_memory

#endif // AGENT_MEMORY_HEADER_STORAGE_FILE_RAW_BLOB_STORE_HPP_INCLUDED
