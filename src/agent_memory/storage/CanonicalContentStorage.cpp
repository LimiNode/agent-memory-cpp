#include "CanonicalContentStorage.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <limits>
#include <stdexcept>

namespace agent_memory {
namespace storage {
namespace {

constexpr std::string_view REV_VERSION = "agent_memory.canonical_revision.v3";
constexpr std::string_view BODY_VERSION = "agent_memory.canonical_body.v3";
constexpr std::string_view LOGICAL_BODY_VERSION = "agent_memory.canonical_logical_body.v1";
constexpr std::string_view LEDGER_VERSION = "agent_memory.canonical_ledger.v1";
constexpr std::uint64_t RAW_ENCODING_GENERATION = 1;
constexpr std::string_view RAW_CODEC = "raw";
constexpr std::uint8_t SHA256_DIGEST_ALGORITHM = 1;

class Sha256 final {
  public:
    void update(const std::uint8_t* data, std::size_t size) {
        for (std::size_t i = 0; i < size; ++i) {
            m_buffer[m_buffer_size++] = data[i];
            m_bit_count += 8U;
            if (m_buffer_size == m_buffer.size()) {
                transform(m_buffer.data());
                m_buffer_size = 0;
            }
        }
    }

    [[nodiscard]] std::array<std::uint8_t, 32> digest() {
        const auto total_bits = m_bit_count;
        m_buffer[m_buffer_size++] = 0x80U;
        if (m_buffer_size > 56U) {
            while (m_buffer_size < m_buffer.size())
                m_buffer[m_buffer_size++] = 0;
            transform(m_buffer.data());
            m_buffer_size = 0;
        }
        while (m_buffer_size < 56U)
            m_buffer[m_buffer_size++] = 0;
        for (int shift = 56; shift >= 0; shift -= 8)
            m_buffer[m_buffer_size++] = static_cast<std::uint8_t>(total_bits >> shift);
        transform(m_buffer.data());
        std::array<std::uint8_t, 32> result{};
        for (std::size_t i = 0; i < m_state.size(); ++i) {
            result[i * 4] = static_cast<std::uint8_t>(m_state[i] >> 24U);
            result[i * 4 + 1] = static_cast<std::uint8_t>(m_state[i] >> 16U);
            result[i * 4 + 2] = static_cast<std::uint8_t>(m_state[i] >> 8U);
            result[i * 4 + 3] = static_cast<std::uint8_t>(m_state[i]);
        }
        return result;
    }

  private:
    static constexpr std::array<std::uint32_t, 64> kRoundConstants{{
        0x428A2F98U, 0x71374491U, 0xB5C0FBCFU, 0xE9B5DBA5U,
        0x3956C25BU, 0x59F111F1U, 0x923F82A4U, 0xAB1C5ED5U,
        0xD807AA98U, 0x12835B01U, 0x243185BEU, 0x550C7DC3U,
        0x72BE5D74U, 0x80DEB1FEU, 0x9BDC06A7U, 0xC19BF174U,
        0xE49B69C1U, 0xEFBE4786U, 0x0FC19DC6U, 0x240CA1CCU,
        0x2DE92C6FU, 0x4A7484AAU, 0x5CB0A9DCU, 0x76F988DAU,
        0x983E5152U, 0xA831C66DU, 0xB00327C8U, 0xBF597FC7U,
        0xC6E00BF3U, 0xD5A79147U, 0x06CA6351U, 0x14292967U,
        0x27B70A85U, 0x2E1B2138U, 0x4D2C6DFCU, 0x53380D13U,
        0x650A7354U, 0x766A0ABBU, 0x81C2C92EU, 0x92722C85U,
        0xA2BFE8A1U, 0xA81A664BU, 0xC24B8B70U, 0xC76C51A3U,
        0xD192E819U, 0xD6990624U, 0xF40E3585U, 0x106AA070U,
        0x19A4C116U, 0x1E376C08U, 0x2748774CU, 0x34B0BCB5U,
        0x391C0CB3U, 0x4ED8AA4AU, 0x5B9CCA4FU, 0x682E6FF3U,
        0x748F82EEU, 0x78A5636FU, 0x84C87814U, 0x8CC70208U,
        0x90BEFFFAU, 0xA4506CEBU, 0xBEF9A3F7U, 0xC67178F2U,
    }};

    static std::uint32_t rotr(std::uint32_t value, int bits) {
        return (value >> bits) | (value << (32 - bits));
    }
    static std::uint32_t read_be32(const std::uint8_t* data) {
        return (static_cast<std::uint32_t>(data[0]) << 24U) |
               (static_cast<std::uint32_t>(data[1]) << 16U) |
               (static_cast<std::uint32_t>(data[2]) << 8U) |
               static_cast<std::uint32_t>(data[3]);
    }
    void transform(const std::uint8_t* chunk) {
        std::array<std::uint32_t, 64> words{};
        for (std::size_t i = 0; i < 16U; ++i)
            words[i] = read_be32(chunk + i * 4U);
        for (std::size_t i = 16U; i < words.size(); ++i) {
            const auto s0 = rotr(words[i - 15U], 7) ^ rotr(words[i - 15U], 18) ^
                            (words[i - 15U] >> 3U);
            const auto s1 = rotr(words[i - 2U], 17) ^ rotr(words[i - 2U], 19) ^
                            (words[i - 2U] >> 10U);
            words[i] = s1 + words[i - 7U] + s0 + words[i - 16U];
        }
        auto a = m_state[0]; auto b = m_state[1]; auto c = m_state[2]; auto d = m_state[3];
        auto e = m_state[4]; auto f = m_state[5]; auto g = m_state[6]; auto h = m_state[7];
        for (std::size_t i = 0; i < words.size(); ++i) {
            const auto s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            const auto choose = (e & f) ^ (~e & g);
            const auto t1 = h + s1 + choose + kRoundConstants[i] + words[i];
            const auto s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            const auto majority = (a & b) ^ (a & c) ^ (b & c);
            const auto t2 = s0 + majority;
            h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        m_state[0] += a; m_state[1] += b; m_state[2] += c; m_state[3] += d;
        m_state[4] += e; m_state[5] += f; m_state[6] += g; m_state[7] += h;
    }
    std::array<std::uint32_t, 8> m_state{{
        0x6A09E667U, 0xBB67AE85U, 0x3C6EF372U, 0xA54FF53AU,
        0x510E527FU, 0x9B05688CU, 0x1F83D9ABU, 0x5BE0CD19U}};
    std::array<std::uint8_t, 64> m_buffer{};
    std::size_t m_buffer_size = 0;
    std::uint64_t m_bit_count = 0;
};

void put_size(std::string& out, std::size_t value) {
    out += std::to_string(value);
    out.push_back(':');
}
void put_string(std::string& out, std::string_view value) {
    put_size(out, value.size());
    out.append(value.data(), value.size());
}
void put_u64(std::string& out, std::uint64_t value) { put_string(out, std::to_string(value)); }
void put_digest(std::string& out, const CanonicalStorageDigest& digest) {
    out.push_back(static_cast<char>(digest.algorithm));
    out.append(reinterpret_cast<const char*>(digest.value.data()), digest.value.size());
}

class Reader final {
  public:
    explicit Reader(std::string_view payload) : m_payload(payload) {}
    std::size_t size() {
        if (m_pos >= m_payload.size())
            throw std::runtime_error("truncated canonical payload");
        std::size_t value = 0;
        bool digit = false;
        while (m_pos < m_payload.size() && m_payload[m_pos] != ':') {
            const auto c = static_cast<unsigned char>(m_payload[m_pos]);
            if (c < '0' || c > '9')
                throw std::runtime_error("invalid canonical length");
            if (value > (std::numeric_limits<std::size_t>::max() - (c - '0')) / 10)
                throw std::runtime_error("canonical length overflow");
            value = value * 10 + (c - '0');
            digit = true;
            ++m_pos;
        }
        if (!digit || m_pos >= m_payload.size())
            throw std::runtime_error("invalid canonical length marker");
        ++m_pos;
        return value;
    }
    std::string string() {
        const auto count = size();
        if (count > m_payload.size() - m_pos)
            throw std::runtime_error("canonical payload overrun");
        std::string result(m_payload.data() + m_pos, count);
        m_pos += count;
        return result;
    }
    std::uint8_t byte() {
        if (m_pos >= m_payload.size())
            throw std::runtime_error("truncated canonical payload");
        return static_cast<std::uint8_t>(m_payload[m_pos++]);
    }
    std::string bytes(std::size_t count) {
        if (count > m_payload.size() - m_pos)
            throw std::runtime_error("canonical payload overrun");
        std::string result(m_payload.data() + m_pos, count);
        m_pos += count;
        return result;
    }
    [[nodiscard]] std::size_t remaining() const noexcept {
        return m_payload.size() - m_pos;
    }
    std::uint64_t u64() {
        const auto text = string();
        std::uint64_t result = 0;
        const auto parsed = std::from_chars(text.data(), text.data() + text.size(), result);
        if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size())
            throw std::runtime_error("invalid canonical integer");
        return result;
    }
    CanonicalStorageDigest digest() {
        CanonicalStorageDigest result;
        result.algorithm = byte();
        const auto bytes_value = bytes(result.value.size());
        std::copy(bytes_value.begin(), bytes_value.end(), result.value.begin());
        return result;
    }
    void end() const {
        if (m_pos != m_payload.size())
            throw std::runtime_error("canonical payload trailing data");
    }
  private:
    std::string_view m_payload;
    std::size_t m_pos = 0;
};

std::size_t bounded_count(Reader& reader, const char* field) {
    const auto count = reader.size();
    // Every encoded element consumes at least one byte (the length marker),
    // so reject hostile counts before reserve() can allocate beyond the
    // supplied payload size.
    if (count > reader.remaining())
        throw std::runtime_error(std::string("canonical ") + field +
                                 " count exceeds payload");
    return count;
}

void put_metadata(std::string& out, const CanonicalStorageMetadata& metadata) {
    put_size(out, metadata.values.size());
    for (const auto& item : metadata.values) {
        put_string(out, item.first);
        put_string(out, item.second);
    }
}
CanonicalStorageMetadata get_metadata(Reader& reader) {
    CanonicalStorageMetadata result;
    const auto count = bounded_count(reader, "metadata");
    result.values.reserve(count);
    for (std::size_t i = 0; i < count; ++i) {
        const auto key = reader.string();
        const auto value = reader.string();
        result.values.emplace_back(key, value);
    }
    return result;
}
void put_block(std::string& out,
               const CanonicalStorageBlock& block,
               bool include_revision,
               bool include_text) {
    put_string(out, block.id);
    if (include_revision)
        put_u64(out, block.revision);
    put_size(out, block.kind);
    put_size(out, block.parent_id ? 1 : 0);
    if (block.parent_id)
        put_string(out, *block.parent_id);
    if (include_text)
        put_string(out, block.text);
}
CanonicalStorageBlock get_block(Reader& reader, bool include_revision, bool include_text) {
    CanonicalStorageBlock block;
    block.id = reader.string();
    if (include_revision)
        block.revision = reader.u64();
    else
        block.revision = 1;
    const auto kind = reader.size();
    if (kind > 2)
        throw std::runtime_error("invalid canonical block kind");
    block.kind = static_cast<std::uint8_t>(kind);
    const auto has_parent = reader.size();
    if (has_parent > 1)
        throw std::runtime_error("invalid canonical parent marker");
    if (has_parent == 1)
        block.parent_id = reader.string();
    if (include_text)
        block.text = reader.string();
    return block;
}
std::string encode_body_without_checksum(const CanonicalStorageBodyRecord& record) {
    std::string out;
    put_string(out, BODY_VERSION);
    put_u64(out, record.body_revision);
    put_u64(out, record.encoding_generation);
    put_string(out, record.codec);
    put_size(out, record.blocks.size());
    for (const auto& block : record.blocks)
        put_block(out, block, false, true);
    put_digest(out, record.logical_body_digest);
    return out;
}
CanonicalStorageDigest digest_of(const std::vector<CanonicalStorageBlock>& blocks) {
    std::string logical;
    put_string(logical, LOGICAL_BODY_VERSION);
    put_size(logical, blocks.size());
    for (const auto& block : blocks)
        put_block(logical, block, false, true);
    Sha256 sha;
    sha.update(reinterpret_cast<const std::uint8_t*>(logical.data()), logical.size());
    return {SHA256_DIGEST_ALGORITHM, sha.digest()};
}
void validate_digest(const CanonicalStorageDigest& digest) {
    if (digest.algorithm != SHA256_DIGEST_ALGORITHM)
        throw std::runtime_error("unsupported canonical logical digest algorithm");
}
void validate_blocks(const std::vector<CanonicalStorageBlock>& blocks) {
    for (const auto& block : blocks) {
        if (block.kind > 2)
            throw std::runtime_error("invalid canonical block kind");
    }
}
std::uint64_t physical_checksum(std::string_view bytes) {
    std::uint64_t digest = 1469598103934665603ULL;
    for (const auto byte : bytes) {
        digest ^= static_cast<unsigned char>(byte);
        digest *= 1099511628211ULL;
    }
    return digest;
}

} // namespace

bool operator==(const CanonicalStorageDigest& left,
                const CanonicalStorageDigest& right) noexcept {
    return left.algorithm == right.algorithm && left.value == right.value;
}
bool operator!=(const CanonicalStorageDigest& left,
                const CanonicalStorageDigest& right) noexcept {
    return !(left == right);
}

bool operator==(const CanonicalStorageBlock& left,
                const CanonicalStorageBlock& right) noexcept {
    return left.id == right.id && left.revision == right.revision &&
           left.kind == right.kind && left.parent_id == right.parent_id &&
           left.text == right.text;
}
bool operator!=(const CanonicalStorageBlock& left,
                const CanonicalStorageBlock& right) noexcept {
    return !(left == right);
}

std::string CanonicalContentStorageCodec::encode_revision(
    const CanonicalStorageRevisionRecord& record) {
    validate_digest(record.logical_body_digest);
    validate_blocks(record.blocks);
    std::string out;
    put_string(out, REV_VERSION);
    put_u64(out, record.body_revision);
    put_digest(out, record.logical_body_digest);
    put_string(out, record.document_id);
    put_u64(out, record.document_revision);
    put_metadata(out, record.metadata);
    put_size(out, record.blocks.size());
    for (const auto& block : record.blocks)
        put_block(out, block, true, false);
    return out;
}

CanonicalStorageRevisionRecord CanonicalContentStorageCodec::decode_revision(
    std::string_view payload) {
    Reader reader(payload);
    if (reader.string() != REV_VERSION)
        throw std::runtime_error("unsupported canonical revision payload");
    CanonicalStorageRevisionRecord result;
    result.body_revision = reader.u64();
    result.logical_body_digest = reader.digest();
    if (result.logical_body_digest.algorithm != SHA256_DIGEST_ALGORITHM)
        throw std::runtime_error("unsupported canonical logical digest algorithm");
    result.document_id = reader.string();
    result.document_revision = reader.u64();
    result.metadata = get_metadata(reader);
    const auto count = bounded_count(reader, "revision block");
    result.blocks.reserve(count);
    for (std::size_t i = 0; i < count; ++i)
        result.blocks.push_back(get_block(reader, true, false));
    reader.end();
    return result;
}

std::string CanonicalContentStorageCodec::encode_body(
    const CanonicalStorageBodyRecord& record) {
    validate_digest(record.logical_body_digest);
    validate_blocks(record.blocks);
    if (record.encoding_generation != RAW_ENCODING_GENERATION || record.codec != RAW_CODEC)
        throw std::runtime_error("invalid canonical physical encoding descriptor");
    if (digest_of(record.blocks) != record.logical_body_digest)
        throw std::runtime_error("canonical logical body digest mismatch");
    auto out = encode_body_without_checksum(record);
    put_u64(out, physical_checksum(out));
    return out;
}

CanonicalStorageBodyRecord CanonicalContentStorageCodec::decode_body(
    std::string_view payload) {
    Reader reader(payload);
    if (reader.string() != BODY_VERSION)
        throw std::runtime_error("unsupported canonical body payload");
    CanonicalStorageBodyRecord result;
    result.body_revision = reader.u64();
    result.encoding_generation = reader.u64();
    result.codec = reader.string();
    if (result.encoding_generation != RAW_ENCODING_GENERATION || result.codec != RAW_CODEC)
        throw std::runtime_error("invalid canonical physical encoding descriptor");
    const auto count = bounded_count(reader, "body block");
    result.blocks.reserve(count);
    for (std::size_t i = 0; i < count; ++i)
        result.blocks.push_back(get_block(reader, false, true));
    result.logical_body_digest = reader.digest();
    if (result.logical_body_digest.algorithm != SHA256_DIGEST_ALGORITHM)
        throw std::runtime_error("unsupported canonical logical digest algorithm");
    result.physical_checksum = reader.u64();
    reader.end();
    if (digest_of(result.blocks) != result.logical_body_digest)
        throw std::runtime_error("canonical logical body digest mismatch");
    if (physical_checksum(encode_body_without_checksum(result)) != result.physical_checksum)
        throw std::runtime_error("canonical physical body checksum mismatch");
    return result;
}

std::string CanonicalContentStorageCodec::encode_head(
    const CanonicalStorageHeadRecord& record) {
    return std::to_string(record.document_revision);
}
CanonicalStorageHeadRecord CanonicalContentStorageCodec::decode_head(std::string_view payload) {
    std::uint64_t value = 0;
    const auto parsed = std::from_chars(payload.data(), payload.data() + payload.size(), value);
    if (parsed.ec != std::errc{} || parsed.ptr != payload.data() + payload.size())
        throw std::runtime_error("invalid canonical head revision");
    return {value};
}

std::string CanonicalContentStorageCodec::encode_ledger(
    const CanonicalStorageLedgerRecord& record) {
    std::string out;
    put_string(out, LEDGER_VERSION);
    put_size(out, record.block_ids.size());
    for (const auto& id : record.block_ids)
        put_string(out, id);
    return out;
}
CanonicalStorageLedgerRecord CanonicalContentStorageCodec::decode_ledger(
    std::string_view payload) {
    Reader reader(payload);
    if (reader.string() != LEDGER_VERSION)
        throw std::runtime_error("unsupported canonical ledger payload");
    CanonicalStorageLedgerRecord result;
    const auto count = bounded_count(reader, "ledger");
    result.block_ids.reserve(count);
    for (std::size_t i = 0; i < count; ++i)
        result.block_ids.push_back(reader.string());
    reader.end();
    return result;
}

CanonicalStorageDigest CanonicalContentStorageCodec::logical_body_digest(
    const std::vector<CanonicalStorageBlock>& blocks) {
    validate_blocks(blocks);
    return digest_of(blocks);
}

} // namespace storage
} // namespace agent_memory
