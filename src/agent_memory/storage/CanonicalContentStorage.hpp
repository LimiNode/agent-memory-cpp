#pragma once
#ifndef AGENT_MEMORY_HEADER_STORAGE_CANONICAL_CONTENT_STORAGE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_STORAGE_CANONICAL_CONTENT_STORAGE_HPP_INCLUDED

/// \file CanonicalContentStorage.hpp
/// \brief Backend-neutral typed records for the canonical-content C1 codec.

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace agent_memory {
namespace storage {

/// \brief Algorithm-tagged digest of decoded canonical content.
struct CanonicalStorageDigest final {
    std::uint8_t algorithm = 0;
    std::array<std::uint8_t, 32> value{};
};

[[nodiscard]] bool operator==(const CanonicalStorageDigest& left,
                              const CanonicalStorageDigest& right) noexcept;
[[nodiscard]] bool operator!=(const CanonicalStorageDigest& left,
                              const CanonicalStorageDigest& right) noexcept;

/// \brief Metadata represented without depending on the domain Metadata type.
struct CanonicalStorageMetadata final {
    std::vector<std::pair<std::string, std::string>> values;
};

/// \brief Physical representation of one canonical block record.
///
/// `kind` is the stable C1 wire value of `ContentBlockKind`; the storage
/// boundary does not include the domain enum or document identifiers.
struct CanonicalStorageBlock final {
    std::string id;
    std::uint64_t revision = 0;
    std::uint8_t kind = 0;
    std::optional<std::string> parent_id;
    std::string text;
};

[[nodiscard]] bool operator==(const CanonicalStorageBlock& left,
                              const CanonicalStorageBlock& right) noexcept;
[[nodiscard]] bool operator!=(const CanonicalStorageBlock& left,
                              const CanonicalStorageBlock& right) noexcept;

/// \brief Immutable C1 revision row, excluding block payload text.
struct CanonicalStorageRevisionRecord final {
    std::string document_id;
    std::uint64_t document_revision = 0;
    CanonicalStorageMetadata metadata;
    std::uint64_t body_revision = 0;
    CanonicalStorageDigest logical_body_digest;
    std::vector<CanonicalStorageBlock> blocks;
};

/// \brief Immutable C1 body row containing decoded block payloads.
struct CanonicalStorageBodyRecord final {
    std::uint64_t body_revision = 0;
    std::uint64_t encoding_generation = 0;
    std::string codec;
    std::vector<CanonicalStorageBlock> blocks;
    CanonicalStorageDigest logical_body_digest;
    std::uint64_t physical_checksum = 0;
};

/// \brief Current-head row. C1 stores this value as a decimal string.
struct CanonicalStorageHeadRecord final {
    std::uint64_t document_revision = 0;
};

/// \brief Historical block-ID no-reuse ledger row.
struct CanonicalStorageLedgerRecord final {
    std::vector<std::string> block_ids;
};

/// \brief Versioned codec for C1 storage rows.
///
/// The codec deliberately preserves the existing C1 byte format. Unknown
/// versions, malformed lengths, invalid enum values and trailing bytes throw
/// `std::runtime_error`; callers must not expose partially decoded records.
class CanonicalContentStorageCodec final {
  public:
    [[nodiscard]] static std::string encode_revision(
        const CanonicalStorageRevisionRecord& record);
    [[nodiscard]] static CanonicalStorageRevisionRecord decode_revision(
        std::string_view payload);

    [[nodiscard]] static std::string encode_body(const CanonicalStorageBodyRecord& record);
    [[nodiscard]] static CanonicalStorageBodyRecord decode_body(std::string_view payload);

    [[nodiscard]] static std::string encode_head(const CanonicalStorageHeadRecord& record);
    [[nodiscard]] static CanonicalStorageHeadRecord decode_head(std::string_view payload);

    [[nodiscard]] static std::string encode_ledger(const CanonicalStorageLedgerRecord& record);
    [[nodiscard]] static CanonicalStorageLedgerRecord decode_ledger(std::string_view payload);

    /// \brief Computes the C1 logical decoded-content digest for body blocks.
    [[nodiscard]] static CanonicalStorageDigest logical_body_digest(
        const std::vector<CanonicalStorageBlock>& blocks);
};

} // namespace storage
} // namespace agent_memory

#endif
