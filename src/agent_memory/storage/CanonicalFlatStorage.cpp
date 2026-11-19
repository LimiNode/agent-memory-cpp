#include "CanonicalFlatStorage.hpp"

#include <agent_memory/domain/Sha256.hpp>

#include <algorithm>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace agent_memory {
namespace storage {
namespace {

constexpr std::string_view kBlockMagic = "AMFB";
constexpr std::string_view kStructureMagic = "AMFS";
constexpr std::uint8_t kSha256 = CanonicalFlatStorageCodec::kSha256DigestAlgorithm;
constexpr std::size_t kDigestValueBytes = 32U;
constexpr std::size_t kDigestBytes = 33U; // algorithm tag plus 32 SHA-256 bytes
constexpr std::size_t kChecksumBytes = 8U;

[[nodiscard]] bool all_zero(const CanonicalFlatDigest& digest) noexcept {
    return digest.algorithm == 0 && std::all_of(digest.value.begin(), digest.value.end(),
                                                 [](std::uint8_t value) { return value == 0; });
}

void validate_limits(const CanonicalFlatStorageLimits& limits) {
    if (limits.max_payload_bytes == 0 || limits.max_string_bytes == 0 ||
        limits.max_blocks == 0 || limits.max_metadata_entries == 0)
        throw std::invalid_argument("canonical flat limits must be non-zero");
}

void validate_string_size(std::size_t size, const CanonicalFlatStorageLimits& limits,
                          const char* field) {
    if (size > limits.max_string_bytes)
        throw std::invalid_argument(std::string("canonical flat ") + field + " exceeds limit");
    if (size > std::numeric_limits<std::uint64_t>::max())
        throw std::invalid_argument(std::string("canonical flat ") + field + " overflows");
}

void validate_block(const CanonicalFlatBlockRecord& record,
                    const CanonicalFlatStorageLimits& limits) {
    validate_string_size(record.id.size(), limits, "block id");
    validate_string_size(record.text.size(), limits, "block text");
    if (record.parent_id)
        validate_string_size(record.parent_id->size(), limits, "parent id");
    if (record.kind > 2U)
        throw std::invalid_argument("canonical flat block kind is invalid");
}

void validate_ref(const CanonicalFlatBlockRef& record,
                  const CanonicalFlatStorageLimits& limits) {
    validate_string_size(record.id.size(), limits, "block reference id");
    if (record.parent_id)
        validate_string_size(record.parent_id->size(), limits, "parent reference id");
    if (record.kind > 2U)
        throw std::invalid_argument("canonical flat block reference kind is invalid");
}

void validate_structure(const CanonicalFlatStructureRecord& record,
                        const CanonicalFlatStorageLimits& limits) {
    validate_string_size(record.document_id.size(), limits, "document id");
    if (record.blocks.size() > limits.max_blocks)
        throw std::invalid_argument("canonical flat block count exceeds limit");
    if (record.metadata.size() > limits.max_metadata_entries)
        throw std::invalid_argument("canonical flat metadata count exceeds limit");
    for (const auto& item : record.metadata) {
        validate_string_size(item.first.size(), limits, "metadata key");
        validate_string_size(item.second.size(), limits, "metadata value");
    }
    for (const auto& block : record.blocks)
        validate_ref(block, limits);
}

void append_u8(std::string& output, std::uint8_t value) {
    output.push_back(static_cast<char>(value));
}

void append_u16(std::string& output, std::uint16_t value) {
    output.push_back(static_cast<char>(value & 0xffU));
    output.push_back(static_cast<char>((value >> 8U) & 0xffU));
}

void append_u32(std::string& output, std::uint32_t value) {
    for (unsigned shift = 0; shift != 32U; shift += 8U)
        output.push_back(static_cast<char>((value >> shift) & 0xffU));
}

void append_u64(std::string& output, std::uint64_t value) {
    for (unsigned shift = 0; shift != 64U; shift += 8U)
        output.push_back(static_cast<char>((value >> shift) & 0xffU));
}

void append_string(std::string& output, std::string_view value) {
    append_u64(output, static_cast<std::uint64_t>(value.size()));
    output.append(value.data(), value.size());
}

void append_digest(std::string& output, const CanonicalFlatDigest& digest) {
    append_u8(output, digest.algorithm);
    output.append(reinterpret_cast<const char*>(digest.value.data()), digest.value.size());
}

[[nodiscard]] std::uint64_t checksum(std::string_view bytes) noexcept {
    std::uint64_t value = 1469598103934665603ULL;
    for (const auto byte : bytes) {
        value ^= static_cast<unsigned char>(byte);
        value *= 1099511628211ULL;
    }
    return value;
}

class Reader final {
  public:
    Reader(std::string_view payload, const CanonicalFlatStorageLimits& limits)
        : m_payload(payload), m_limits(limits) {
        if (payload.size() > limits.max_payload_bytes)
            throw std::runtime_error("canonical flat payload exceeds limit");
    }

    [[nodiscard]] std::size_t position() const noexcept { return m_position; }
    [[nodiscard]] std::size_t remaining() const noexcept {
        return m_payload.size() - m_position;
    }

    std::uint8_t u8() {
        require(1U, "byte");
        return static_cast<std::uint8_t>(m_payload[m_position++]);
    }

    std::uint16_t u16() {
        require(2U, "u16");
        const auto value = static_cast<std::uint16_t>(
            static_cast<std::uint16_t>(static_cast<std::uint8_t>(m_payload[m_position])) |
            static_cast<std::uint16_t>(static_cast<std::uint16_t>(
                static_cast<std::uint8_t>(m_payload[m_position + 1U])) << 8U));
        m_position += 2U;
        return value;
    }

    std::uint32_t u32() {
        require(4U, "u32");
        std::uint32_t value = 0;
        for (unsigned shift = 0; shift != 32U; shift += 8U)
            value |= static_cast<std::uint32_t>(static_cast<std::uint8_t>(m_payload[m_position++])) << shift;
        return value;
    }

    std::uint64_t u64() {
        require(8U, "u64");
        std::uint64_t value = 0;
        for (unsigned shift = 0; shift != 64U; shift += 8U)
            value |= static_cast<std::uint64_t>(static_cast<std::uint8_t>(m_payload[m_position++])) << shift;
        return value;
    }

    std::string bytes(std::size_t count, const char* field) {
        require(count, field);
        if (count == 0U)
            return {};
        std::string result(m_payload.data() + m_position, count);
        m_position += count;
        return result;
    }

    std::string string(const char* field) {
        const auto length = u64();
        if (length > m_limits.max_string_bytes ||
            length > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()))
            throw std::runtime_error(std::string("canonical flat ") + field + " exceeds limit");
        return bytes(static_cast<std::size_t>(length), field);
    }

    CanonicalFlatDigest digest() {
        CanonicalFlatDigest result;
        result.algorithm = u8();
        const auto raw = bytes(kDigestValueBytes, "digest");
        std::copy(raw.begin(), raw.end(), result.value.begin());
        return result;
    }

    void require_end() const {
        if (m_position != m_payload.size())
            throw std::runtime_error("canonical flat payload has trailing bytes");
    }

  private:
    void require(std::size_t count, const char* field) const {
        if (count > m_payload.size() - m_position)
            throw std::runtime_error(std::string("canonical flat ") + field + " is truncated");
    }

    std::string_view m_payload;
    const CanonicalFlatStorageLimits& m_limits;
    std::size_t m_position = 0;
};

void expect_magic(Reader& reader, std::string_view magic) {
    if (reader.bytes(magic.size(), "magic") != magic)
        throw std::runtime_error("canonical flat magic is invalid");
    if (reader.u8() != CanonicalFlatStorageCodec::kVersion)
        throw std::runtime_error("unsupported canonical flat payload version");
    if (reader.u8() != 0U || reader.u16() != 0U)
        throw std::runtime_error("canonical flat reserved header bits are non-zero");
}

void append_header(std::string& output, std::string_view magic) {
    output.append(magic.data(), magic.size());
    append_u8(output, CanonicalFlatStorageCodec::kVersion);
    append_u8(output, 0U);
    append_u16(output, 0U);
}

CanonicalFlatDigest digest_for(std::string_view bytes) {
    CanonicalFlatDigest result;
    result.algorithm = kSha256;
    result.value = sha256(bytes);
    return result;
}

void validate_digest(const CanonicalFlatDigest& digest) {
    if (digest.algorithm != kSha256)
        throw std::runtime_error("unsupported canonical flat digest algorithm");
}

void validate_declared_digest(const CanonicalFlatDigest& declared,
                              const CanonicalFlatDigest& computed) {
    if (!all_zero(declared) && declared != computed)
        throw std::invalid_argument("canonical flat logical digest mismatch");
}

void validate_declared_checksum(std::uint64_t declared, std::uint64_t computed) {
    if (declared != 0U && declared != computed)
        throw std::invalid_argument("canonical flat physical checksum mismatch");
}

std::string block_logical_bytes(const CanonicalFlatBlockRecord& record,
                                const CanonicalFlatStorageLimits& limits) {
    validate_block(record, limits);
    std::string output;
    append_header(output, kBlockMagic);
    append_u64(output, record.revision);
    append_u8(output, record.kind);
    append_u8(output, record.parent_id ? 1U : 0U);
    append_u16(output, 0U);
    append_string(output, record.id);
    if (record.parent_id)
        append_string(output, *record.parent_id);
    append_string(output, record.text);
    return output;
}

std::string structure_logical_bytes(const CanonicalFlatStructureRecord& record,
                                    const CanonicalFlatStorageLimits& limits) {
    validate_structure(record, limits);
    std::string output;
    append_header(output, kStructureMagic);
    append_u64(output, record.document_revision);
    append_u64(output, record.body_revision);
    append_string(output, record.document_id);
    append_u64(output, static_cast<std::uint64_t>(record.metadata.size()));
    for (const auto& item : record.metadata) {
        append_string(output, item.first);
        append_string(output, item.second);
    }
    append_u64(output, static_cast<std::uint64_t>(record.blocks.size()));
    for (const auto& block : record.blocks) {
        append_u64(output, block.revision);
        append_u8(output, block.kind);
        append_u8(output, block.parent_id ? 1U : 0U);
        append_u16(output, 0U);
        append_string(output, block.id);
        if (block.parent_id)
            append_string(output, *block.parent_id);
    }
    return output;
}

void check_output_size(const std::string& output, const CanonicalFlatStorageLimits& limits) {
    if (output.size() > limits.max_payload_bytes)
        throw std::invalid_argument("canonical flat encoded payload exceeds limit");
}

} // namespace

bool operator==(const CanonicalFlatDigest& left,
                const CanonicalFlatDigest& right) noexcept {
    return left.algorithm == right.algorithm && left.value == right.value;
}

bool operator!=(const CanonicalFlatDigest& left,
                const CanonicalFlatDigest& right) noexcept {
    return !(left == right);
}

bool operator==(const CanonicalFlatBlockRecord& left,
                const CanonicalFlatBlockRecord& right) noexcept {
    return left.id == right.id && left.revision == right.revision &&
           left.kind == right.kind && left.parent_id == right.parent_id &&
           left.text == right.text;
}

bool operator!=(const CanonicalFlatBlockRecord& left,
                const CanonicalFlatBlockRecord& right) noexcept {
    return !(left == right);
}

bool operator==(const CanonicalFlatBlockRef& left,
                const CanonicalFlatBlockRef& right) noexcept {
    return left.id == right.id && left.revision == right.revision &&
           left.kind == right.kind && left.parent_id == right.parent_id;
}

bool operator!=(const CanonicalFlatBlockRef& left,
                const CanonicalFlatBlockRef& right) noexcept {
    return !(left == right);
}

bool operator==(const CanonicalFlatStructureRecord& left,
                const CanonicalFlatStructureRecord& right) noexcept {
    return left.document_id == right.document_id &&
           left.document_revision == right.document_revision &&
           left.body_revision == right.body_revision &&
           left.metadata == right.metadata && left.blocks == right.blocks;
}

bool operator!=(const CanonicalFlatStructureRecord& left,
                const CanonicalFlatStructureRecord& right) noexcept {
    return !(left == right);
}

CanonicalFlatDigest CanonicalFlatStorageCodec::block_digest(
    const CanonicalFlatBlockRecord& record) {
    return digest_for(block_logical_bytes(record, {}));
}

CanonicalFlatDigest CanonicalFlatStorageCodec::structure_digest(
    const CanonicalFlatStructureRecord& record) {
    return digest_for(structure_logical_bytes(record, {}));
}

std::string CanonicalFlatStorageCodec::encode_block(
    const CanonicalFlatBlockRecord& record, CanonicalFlatStorageLimits limits) {
    validate_limits(limits);
    const auto logical = block_logical_bytes(record, limits);
    const auto digest = digest_for(logical);
    validate_declared_digest(record.logical_digest, digest);
    std::string output = logical;
    append_digest(output, digest);
    const auto physical = checksum(output);
    validate_declared_checksum(record.physical_checksum, physical);
    append_u64(output, physical);
    check_output_size(output, limits);
    return output;
}

CanonicalFlatBlockRecord CanonicalFlatStorageCodec::decode_block(
    std::string_view payload, CanonicalFlatStorageLimits limits) {
    validate_limits(limits);
    if (payload.size() < kDigestBytes + kChecksumBytes)
        throw std::runtime_error("canonical flat block integrity trailer is truncated");
    Reader reader(payload, limits);
    const auto logical_end = payload.size() - kDigestBytes - kChecksumBytes;
    expect_magic(reader, kBlockMagic);
    CanonicalFlatBlockRecord result;
    result.revision = reader.u64();
    result.kind = reader.u8();
    const auto parent_marker = reader.u8();
    if (parent_marker > 1U || reader.u16() != 0U)
        throw std::runtime_error("canonical flat block descriptor is invalid");
    result.id = reader.string("block id");
    if (parent_marker != 0U)
        result.parent_id = reader.string("parent id");
    result.text = reader.string("block text");
    if (reader.position() != logical_end)
        throw std::runtime_error("canonical flat block logical payload is malformed");
    result.logical_digest = reader.digest();
    validate_digest(result.logical_digest);
    result.physical_checksum = reader.u64();
    reader.require_end();
    const auto expected_digest = digest_for(payload.substr(0, logical_end));
    if (result.logical_digest != expected_digest)
        throw std::runtime_error("canonical flat block logical digest mismatch");
    if (checksum(payload.substr(0, payload.size() - kChecksumBytes)) != result.physical_checksum)
        throw std::runtime_error("canonical flat block physical checksum mismatch");
    validate_block(result, limits);
    return result;
}

std::string CanonicalFlatStorageCodec::encode_structure(
    const CanonicalFlatStructureRecord& record, CanonicalFlatStorageLimits limits) {
    validate_limits(limits);
    const auto logical = structure_logical_bytes(record, limits);
    const auto digest = digest_for(logical);
    validate_declared_digest(record.logical_digest, digest);
    std::string output = logical;
    append_digest(output, digest);
    const auto physical = checksum(output);
    validate_declared_checksum(record.physical_checksum, physical);
    append_u64(output, physical);
    check_output_size(output, limits);
    return output;
}

CanonicalFlatStructureRecord CanonicalFlatStorageCodec::decode_structure(
    std::string_view payload, CanonicalFlatStorageLimits limits) {
    validate_limits(limits);
    Reader reader(payload, limits);
    if (payload.size() < kDigestBytes + kChecksumBytes)
        throw std::runtime_error("canonical flat structure integrity trailer is truncated");
    const auto logical_end = payload.size() - kDigestBytes - kChecksumBytes;
    expect_magic(reader, kStructureMagic);
    CanonicalFlatStructureRecord result;
    result.document_revision = reader.u64();
    result.body_revision = reader.u64();
    result.document_id = reader.string("document id");
    const auto metadata_count = reader.u64();
    if (metadata_count > limits.max_metadata_entries ||
        metadata_count > static_cast<std::uint64_t>(reader.remaining()))
        throw std::runtime_error("canonical flat metadata count exceeds payload");
    result.metadata.reserve(static_cast<std::size_t>(metadata_count));
    for (std::uint64_t index = 0; index < metadata_count; ++index) {
        const auto key = reader.string("metadata key");
        const auto value = reader.string("metadata value");
        result.metadata.emplace_back(key, value);
    }
    const auto block_count = reader.u64();
    if (block_count > limits.max_blocks ||
        block_count > static_cast<std::uint64_t>(reader.remaining()))
        throw std::runtime_error("canonical flat block count exceeds payload");
    result.blocks.reserve(static_cast<std::size_t>(block_count));
    for (std::uint64_t index = 0; index < block_count; ++index) {
        CanonicalFlatBlockRef block;
        block.revision = reader.u64();
        block.kind = reader.u8();
        const auto parent_marker = reader.u8();
        if (parent_marker > 1U || reader.u16() != 0U)
            throw std::runtime_error("canonical flat block reference descriptor is invalid");
        block.id = reader.string("block reference id");
        if (parent_marker != 0U)
            block.parent_id = reader.string("parent reference id");
        validate_ref(block, limits);
        result.blocks.push_back(std::move(block));
        if (reader.position() > logical_end)
            throw std::runtime_error("canonical flat structure exceeds integrity trailer");
    }
    if (reader.position() != logical_end)
        throw std::runtime_error("canonical flat structure logical payload is malformed");
    result.logical_digest = reader.digest();
    validate_digest(result.logical_digest);
    result.physical_checksum = reader.u64();
    reader.require_end();
    const auto expected_digest = digest_for(payload.substr(0, logical_end));
    if (result.logical_digest != expected_digest)
        throw std::runtime_error("canonical flat structure logical digest mismatch");
    if (checksum(payload.substr(0, payload.size() - kChecksumBytes)) != result.physical_checksum)
        throw std::runtime_error("canonical flat structure physical checksum mismatch");
    validate_structure(result, limits);
    return result;
}

} // namespace storage
} // namespace agent_memory
