#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_FRAMED_ZSTD_CODEC_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_FRAMED_ZSTD_CODEC_HPP_INCLUDED

/// \file FramedZstdCodec.hpp
/// \brief Optional bounded framed Zstandard representation for raw artifacts.

#include <agent_memory/domain/Artifact.hpp>

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace agent_memory {

struct FramedZstdOptions final {
    std::uint32_t frame_bytes = 64U * 1024U;
    int compression_level = 3;
    std::uint64_t max_decoded_bytes = 256U * 1024U * 1024U;
    std::uint32_t max_frame_bytes = 64U * 1024U * 1024U;
};

struct FramedZstdDescriptor final {
    std::uint16_t format_version = 1;
    std::uint32_t frame_bytes = 0;
    int compression_level = 0;
    bool raw_fallback = false;
    std::string codec_id = "zstd.framed.v1";
};

struct FramedZstdEncoded final {
    ArtifactId artifact_id;
    BlobDigest decoded_digest;
    FramedZstdDescriptor descriptor;
    std::string bytes;
};

struct FramedZstdDecoded final {
    ArtifactId artifact_id;
    BlobDigest decoded_digest;
    FramedZstdDescriptor descriptor;
    std::string bytes;
};

struct FramedZstdRange final {
    ArtifactId artifact_id;
    FramedZstdDescriptor descriptor;
    std::uint64_t offset = 0;
    std::string bytes;
};

/// \brief Encodes decoded bytes into deterministic independently decodable frames.
///
/// `already_compressed` selects the explicit raw fallback. It changes only the
/// physical descriptor and bytes; the ArtifactId is always derived from the
/// decoded payload. The function is unavailable when AGENT_MEMORY_ENABLE_ZSTD
/// is disabled and fails closed rather than silently changing representation.
[[nodiscard]] FramedZstdEncoded encode_framed_zstd(
    std::string_view decoded_bytes,
    bool already_compressed,
    FramedZstdOptions options = {}
);

/// \brief Decodes and verifies one framed payload under bounded output budgets.
[[nodiscard]] FramedZstdDecoded decode_framed_zstd(
    std::string_view encoded_bytes,
    FramedZstdOptions options = {}
);

/// \brief Decodes one logical byte range, materializing at most one frame.
///
/// Integrity of each materialized frame is checked. Callers requiring
/// whole-artifact digest verification must use `decode_framed_zstd()`.
[[nodiscard]] FramedZstdRange decode_framed_zstd_range(
    std::string_view encoded_bytes,
    std::uint64_t offset,
    std::uint64_t length,
    FramedZstdOptions options = {}
);

} // namespace agent_memory

#endif
