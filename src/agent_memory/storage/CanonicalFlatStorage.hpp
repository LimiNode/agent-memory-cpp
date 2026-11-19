#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_CANONICAL_FLAT_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_CANONICAL_FLAT_STORAGE_HPP_INCLUDED

/// \file CanonicalFlatStorage.hpp
/// \brief Opt-in, versioned flat canonical-content record codecs.
///
/// This is a portable DTO/codec boundary for a possible flat physical layout.
/// It is deliberately not wired into a canonical-content store; the C1 MDBX
/// tables and their wire representation remain the active profile.

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace agent_memory {
namespace storage {

/// \brief Algorithm-tagged logical digest used by the flat profile.
struct CanonicalFlatDigest final {
    /// SHA-256 is algorithm tag 1. Unknown tags are rejected by the decoder.
    /// Zero denotes an unspecified digest on an encode DTO.
    std::uint8_t algorithm = 0;
    std::array<std::uint8_t, 32> value{};
};

[[nodiscard]] bool operator==(const CanonicalFlatDigest& left,
                              const CanonicalFlatDigest& right) noexcept;
[[nodiscard]] bool operator!=(const CanonicalFlatDigest& left,
                              const CanonicalFlatDigest& right) noexcept;

/// \brief Decoder resource limits. Limits are checked before allocation.
struct CanonicalFlatStorageLimits final {
    std::size_t max_payload_bytes = 64U * 1024U * 1024U;
    std::size_t max_string_bytes = 16U * 1024U * 1024U;
    std::size_t max_blocks = 1U * 1024U * 1024U;
    std::size_t max_metadata_entries = 100U * 1024U;
};

/// \brief One physical flat block row, including its decoded text.
struct CanonicalFlatBlockRecord final {
    std::string id;
    std::uint64_t revision = 0;
    std::uint8_t kind = 0;
    std::optional<std::string> parent_id;
    std::string text;

    // Populated by decode. A non-zero/non-default value supplied to encode is
    // treated as a declaration and must match the computed value.
    CanonicalFlatDigest logical_digest;
    std::uint64_t physical_checksum = 0;
};

/// Equality compares logical fields; integrity trailers are codec-derived.
[[nodiscard]] bool operator==(const CanonicalFlatBlockRecord& left,
                              const CanonicalFlatBlockRecord& right) noexcept;
[[nodiscard]] bool operator!=(const CanonicalFlatBlockRecord& left,
                              const CanonicalFlatBlockRecord& right) noexcept;

/// \brief Structure-only reference to one flat block row.
struct CanonicalFlatBlockRef final {
    std::string id;
    std::uint64_t revision = 0;
    std::uint8_t kind = 0;
    std::optional<std::string> parent_id;
};

[[nodiscard]] bool operator==(const CanonicalFlatBlockRef& left,
                              const CanonicalFlatBlockRef& right) noexcept;
[[nodiscard]] bool operator!=(const CanonicalFlatBlockRef& left,
                              const CanonicalFlatBlockRef& right) noexcept;

/// \brief Immutable structure row pointing at ordered flat block rows.
struct CanonicalFlatStructureRecord final {
    std::string document_id;
    std::uint64_t document_revision = 0;
    std::uint64_t body_revision = 0;
    std::vector<std::pair<std::string, std::string>> metadata;
    std::vector<CanonicalFlatBlockRef> blocks;

    // Populated by decode; see CanonicalFlatBlockRecord::logical_digest.
    CanonicalFlatDigest logical_digest;
    std::uint64_t physical_checksum = 0;
};

/// Equality compares logical fields; integrity trailers are codec-derived.
[[nodiscard]] bool operator==(const CanonicalFlatStructureRecord& left,
                              const CanonicalFlatStructureRecord& right) noexcept;
[[nodiscard]] bool operator!=(const CanonicalFlatStructureRecord& left,
                              const CanonicalFlatStructureRecord& right) noexcept;

/// \brief Explicitly versioned codecs for the opt-in flat profile.
class CanonicalFlatStorageCodec final {
  public:
    static constexpr std::uint8_t kVersion = 1;
    static constexpr std::uint8_t kSha256DigestAlgorithm = 1;

    [[nodiscard]] static std::string encode_block(
        const CanonicalFlatBlockRecord& record,
        CanonicalFlatStorageLimits limits = {});
    [[nodiscard]] static CanonicalFlatBlockRecord decode_block(
        std::string_view payload,
        CanonicalFlatStorageLimits limits = {});

    [[nodiscard]] static std::string encode_structure(
        const CanonicalFlatStructureRecord& record,
        CanonicalFlatStorageLimits limits = {});
    [[nodiscard]] static CanonicalFlatStructureRecord decode_structure(
        std::string_view payload,
        CanonicalFlatStorageLimits limits = {});

    /// \brief Computes the digest over a block's logical fields only.
    [[nodiscard]] static CanonicalFlatDigest block_digest(
        const CanonicalFlatBlockRecord& record);
    /// \brief Computes the digest over a structure's logical fields only.
    [[nodiscard]] static CanonicalFlatDigest structure_digest(
        const CanonicalFlatStructureRecord& record);
};

} // namespace storage

// Keep the opt-in DTOs discoverable for consumers that do not use the storage
// nested namespace, while retaining storage:: as the canonical declaration.
using storage::CanonicalFlatBlockRecord;
using storage::CanonicalFlatBlockRef;
using storage::CanonicalFlatDigest;
using storage::CanonicalFlatStorageCodec;
using storage::CanonicalFlatStorageLimits;
using storage::CanonicalFlatStructureRecord;

} // namespace agent_memory

#endif
