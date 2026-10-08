#include "MdbxCanonicalContentStore.hpp"

#include <agent_memory/storage/CanonicalContentStoreInternal.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <array>
#include <algorithm>
#include <cctype>
#include <charconv>
#include <mdbx_containers/KeyValueTable.hpp>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

namespace agent_memory {
namespace {

// Every payload begins with an application-owned version marker. The compact
// length-prefixed codec is binary-safe and rejects truncation, overflow,
// unknown versions, and trailing data before domain objects are exposed.
constexpr std::string_view REV_VERSION = "agent_memory.canonical_revision.v3";
constexpr std::string_view BODY_VERSION = "agent_memory.canonical_body.v3";
constexpr std::string_view LOGICAL_BODY_VERSION = "agent_memory.canonical_logical_body.v1";
constexpr std::string_view LEDGER_VERSION = "agent_memory.canonical_ledger.v1";
constexpr std::uint64_t RAW_ENCODING_GENERATION = 1;
constexpr std::string_view RAW_CODEC = "raw";
constexpr std::uint8_t SHA256_DIGEST_ALGORITHM = 1;

struct LogicalBodyDigest final {
    std::uint8_t algorithm = SHA256_DIGEST_ALGORITHM; ///< Algorithm tag.
    std::array<std::uint8_t, 32> value{}; ///< Full SHA-256 digest bytes.
};

bool operator==(const LogicalBodyDigest& left, const LogicalBodyDigest& right) noexcept {
    return left.algorithm == right.algorithm && left.value == right.value;
}

bool operator!=(const LogicalBodyDigest& left, const LogicalBodyDigest& right) noexcept {
    return !(left == right);
}

class Sha256 final {
  public:
    void update(const std::uint8_t* data, std::size_t size) {
        for (std::size_t index = 0; index < size; ++index) {
            m_buffer[m_buffer_size++] = data[index];
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
                m_buffer[m_buffer_size++] = 0U;
            transform(m_buffer.data());
            m_buffer_size = 0;
        }
        while (m_buffer_size < 56U)
            m_buffer[m_buffer_size++] = 0U;
        for (int shift = 56; shift >= 0; shift -= 8)
            m_buffer[m_buffer_size++] = static_cast<std::uint8_t>((total_bits >> shift) & 0xFFU);
        transform(m_buffer.data());

        std::array<std::uint8_t, 32> output{};
        for (std::size_t index = 0; index < m_state.size(); ++index) {
            output[index * 4] = static_cast<std::uint8_t>(m_state[index] >> 24U);
            output[index * 4 + 1] = static_cast<std::uint8_t>(m_state[index] >> 16U);
            output[index * 4 + 2] = static_cast<std::uint8_t>(m_state[index] >> 8U);
            output[index * 4 + 3] = static_cast<std::uint8_t>(m_state[index]);
        }
        return output;
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

    [[nodiscard]] static std::uint32_t rotate_right(std::uint32_t value, int bits) {
        return (value >> bits) | (value << (32 - bits));
    }

    [[nodiscard]] static std::uint32_t read_be32(const std::uint8_t* data) {
        return (static_cast<std::uint32_t>(data[0]) << 24U) |
               (static_cast<std::uint32_t>(data[1]) << 16U) |
               (static_cast<std::uint32_t>(data[2]) << 8U) |
               static_cast<std::uint32_t>(data[3]);
    }

    void transform(const std::uint8_t* chunk) {
        std::array<std::uint32_t, 64> words{};
        for (std::size_t index = 0; index < 16U; ++index)
            words[index] = read_be32(chunk + index * 4U);
        for (std::size_t index = 16U; index < words.size(); ++index) {
            const auto sigma0 = rotate_right(words[index - 15U], 7) ^
                                rotate_right(words[index - 15U], 18) ^
                                (words[index - 15U] >> 3U);
            const auto sigma1 = rotate_right(words[index - 2U], 17) ^
                                rotate_right(words[index - 2U], 19) ^
                                (words[index - 2U] >> 10U);
            words[index] = sigma1 + words[index - 7U] + sigma0 + words[index - 16U];
        }

        auto a = m_state[0];
        auto b = m_state[1];
        auto c = m_state[2];
        auto d = m_state[3];
        auto e = m_state[4];
        auto f = m_state[5];
        auto g = m_state[6];
        auto h = m_state[7];
        for (std::size_t index = 0; index < words.size(); ++index) {
            const auto sigma1 = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
            const auto choose = (e & f) ^ (~e & g);
            const auto temp1 = h + sigma1 + choose + kRoundConstants[index] + words[index];
            const auto sigma0 = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
            const auto majority = (a & b) ^ (a & c) ^ (b & c);
            const auto temp2 = sigma0 + majority;
            h = g;
            g = f;
            f = e;
            e = d + temp1;
            d = c;
            c = b;
            b = a;
            a = temp1 + temp2;
        }
        m_state[0] += a;
        m_state[1] += b;
        m_state[2] += c;
        m_state[3] += d;
        m_state[4] += e;
        m_state[5] += f;
        m_state[6] += g;
        m_state[7] += h;
    }

    std::array<std::uint32_t, 8> m_state{{
        0x6A09E667U, 0xBB67AE85U, 0x3C6EF372U, 0xA54FF53AU,
        0x510E527FU, 0x9B05688CU, 0x1F83D9ABU, 0x5BE0CD19U,
    }}; ///< SHA-256 compression state.
    std::array<std::uint8_t, 64> m_buffer{}; ///< Pending message block.
    std::size_t m_buffer_size = 0; ///< Bytes buffered in the current block.
    std::uint64_t m_bit_count = 0; ///< Total message length in bits.
};

std::string safe_part(std::string value) {
    if (value.empty())
        value = "agent_memory";
    for (char& c : value)
        if (!std::isalnum(static_cast<unsigned char>(c)))
            c = '_';
    return value;
}

std::string key(const std::string& doc, std::uint64_t revision) {
    // The unit separator cannot be confused with a decimal revision suffix.
    return doc + "\x1f" + std::to_string(revision);
}

std::string
body_key(const std::string& doc, std::uint64_t body_revision, std::uint64_t encoding_generation) {
    return doc + "\x1f" + std::to_string(body_revision) + "\x1f" +
           std::to_string(encoding_generation);
}

void put_size(std::string& out, std::size_t value) {
    out += std::to_string(value);
    out.push_back(':');
}

void put_string(std::string& out, std::string_view value) {
    put_size(out, value.size());
    out.append(value.data(), value.size());
}

void put_u64(std::string& out, std::uint64_t value) {
    put_string(out, std::to_string(value));
}

void put_digest(std::string& out, const LogicalBodyDigest& digest) {
    out.push_back(static_cast<char>(digest.algorithm));
    out.append(reinterpret_cast<const char*>(digest.value.data()), digest.value.size());
}

std::uint64_t parse_u64(std::string_view text, std::string_view field) {
    std::uint64_t value = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size())
        throw std::runtime_error("invalid canonical " + std::string(field));
    return value;
}

std::uint64_t digest_bytes(std::string_view bytes) {
    // C1 uses a deterministic non-cryptographic checksum for corruption
    // detection. It is intentionally separate from the logical decoded-content
    // digest stored by an immutable revision.
    std::uint64_t digest = 1469598103934665603ULL;
    for (const auto byte : bytes) {
        digest ^= static_cast<unsigned char>(byte);
        digest *= 1099511628211ULL;
    }
    return digest;
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
            if (value > (static_cast<std::size_t>(-1) - (c - '0')) / 10)
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
        const auto n = size();
        if (n > m_payload.size() - m_pos)
            throw std::runtime_error("canonical payload overrun");
        std::string v(m_payload.data() + m_pos, n);
        m_pos += n;
        return v;
    }

    std::uint8_t byte() {
        if (m_pos >= m_payload.size())
            throw std::runtime_error("truncated canonical payload");
        return static_cast<std::uint8_t>(m_payload[m_pos++]);
    }

    std::string bytes(std::size_t n) {
        if (n > m_payload.size() - m_pos)
            throw std::runtime_error("canonical payload overrun");
        std::string v(m_payload.data() + m_pos, n);
        m_pos += n;
        return v;
    }

    std::uint64_t u64() {
        return parse_u64(string(), "integer");
    }

    LogicalBodyDigest digest() {
        LogicalBodyDigest result;
        result.algorithm = byte();
        const auto raw = bytes(result.value.size());
        std::copy(raw.begin(), raw.end(), result.value.begin());
        return result;
    }

    void end() const {
        if (m_pos != m_payload.size())
            throw std::runtime_error("canonical payload trailing data");
    }

  private:
    std::string_view m_payload; ///< Borrowed encoded payload.
    std::size_t m_pos = 0;      ///< Current validated decode offset.
};

void metadata_put(std::string& out, const Metadata& metadata) {
    put_size(out, metadata.size());
    for (const auto& item : metadata.values()) {
        put_string(out, item.first);
        put_string(out, item.second);
    }
}

Metadata metadata_get(Reader& r) {
    Metadata result;
    const auto n = r.size();
    for (std::size_t i = 0; i < n; ++i)
        result.set(r.string(), r.string());
    return result;
}

struct DecodedRevision final {
    CanonicalDocumentRevision revision; ///< Semantic document revision.
    std::uint64_t body_revision = 0; ///< Logical decoded-body revision.
    LogicalBodyDigest logical_body_digest; ///< Algorithm-tagged decoded digest.
};

std::string encode_revision(const CanonicalDocumentRevision& revision,
                            std::uint64_t body_revision,
                            const LogicalBodyDigest& logical_body_digest) {
    std::string out;
    put_string(out, REV_VERSION);
    put_u64(out, body_revision);
    put_digest(out, logical_body_digest);
    put_string(out, revision.document_id.value());
    put_u64(out, revision.revision);
    metadata_put(out, revision.metadata);
    put_size(out, revision.blocks.size());
    for (const auto& block : revision.blocks) {
        put_string(out, block.id.value());
        put_u64(out, block.revision);
        put_size(out, static_cast<std::size_t>(block.kind));
        put_size(out, block.parent_id ? 1 : 0);
        if (block.parent_id)
            put_string(out, block.parent_id->value());
    }
    return out;
}

DecodedRevision decode_revision(std::string_view payload) {
    Reader r(payload);
    if (r.string() != REV_VERSION)
        throw std::runtime_error("unsupported canonical revision payload");
    const auto body_revision = r.u64();
    const auto logical_body_digest = r.digest();
    if (logical_body_digest.algorithm != SHA256_DIGEST_ALGORITHM)
        throw std::runtime_error("unsupported canonical logical digest algorithm");
    CanonicalDocumentRevision revision{DocumentId{r.string()}, r.u64(), metadata_get(r), {}};
    const auto n = r.size();
    revision.blocks.reserve(n);
    for (std::size_t i = 0; i < n; ++i) {
        ContentBlock b;
        b.id = ContentBlockId{r.string()};
        b.revision = r.u64();
        const auto kind = r.size();
        if (kind > 2)
            throw std::runtime_error("invalid canonical block kind");
        b.kind = static_cast<ContentBlockKind>(kind);
        const auto has_parent = r.size();
        if (has_parent > 1)
            throw std::runtime_error("invalid canonical parent marker");
        if (has_parent == 1)
            b.parent_id = ContentBlockId{r.string()};
        revision.blocks.push_back(std::move(b));
    }
    r.end();
    return {std::move(revision), body_revision, logical_body_digest};
}

struct EncodedBody final {
    std::string payload; ///< Complete versioned physical body payload.
    LogicalBodyDigest logical_body_digest; ///< Algorithm-tagged decoded digest.
    std::uint64_t physical_checksum = 0; ///< Checksum over the physical payload.
};

struct DecodedBody final {
    std::uint64_t body_revision = 0; ///< Logical decoded-body revision.
    std::uint64_t encoding_generation = 0; ///< Physical encoding generation.
    std::string codec; ///< Physical codec identifier.
    std::vector<ContentBlock> blocks; ///< Decoded canonical block values.
    LogicalBodyDigest logical_body_digest; ///< Algorithm-tagged decoded digest.
    std::uint64_t physical_checksum = 0; ///< Checksum stored with this payload.
};

std::string encode_logical_body(const std::vector<ContentBlock>& blocks) {
    std::string out;
    put_string(out, LOGICAL_BODY_VERSION);
    put_size(out, blocks.size());
    for (const auto& b : blocks) {
        put_string(out, b.id.value());
        put_size(out, static_cast<std::size_t>(b.kind));
        put_size(out, b.parent_id ? 1 : 0);
        if (b.parent_id)
            put_string(out, b.parent_id->value());
        put_string(out, b.text);
    }
    return out;
}

LogicalBodyDigest logical_body_digest_impl(const std::vector<ContentBlock>& blocks) {
    Sha256 sha;
    const auto encoded = encode_logical_body(blocks);
    sha.update(reinterpret_cast<const std::uint8_t*>(encoded.data()), encoded.size());
    return {SHA256_DIGEST_ALGORITHM, sha.digest()};
}

std::string encode_body_payload(const std::vector<ContentBlock>& blocks,
                                std::uint64_t body_revision,
                                std::uint64_t encoding_generation,
                                std::string_view codec,
                                const LogicalBodyDigest& logical_digest) {
    std::string out;
    put_string(out, BODY_VERSION);
    put_u64(out, body_revision);
    put_u64(out, encoding_generation);
    put_string(out, codec);
    put_size(out, blocks.size());
    for (const auto& b : blocks) {
        put_string(out, b.id.value());
        put_size(out, static_cast<std::size_t>(b.kind));
        put_size(out, b.parent_id ? 1 : 0);
        if (b.parent_id)
            put_string(out, b.parent_id->value());
        put_string(out, b.text);
    }
    put_digest(out, logical_digest);
    return out;
}

EncodedBody encode_body(const CanonicalDocumentRevision& revision, std::uint64_t body_revision) {
    EncodedBody result;
    result.logical_body_digest = logical_body_digest_impl(revision.blocks);
    result.payload = encode_body_payload(revision.blocks,
                                         body_revision,
                                         RAW_ENCODING_GENERATION,
                                         RAW_CODEC,
                                         result.logical_body_digest);
    result.physical_checksum = digest_bytes(result.payload);
    put_u64(result.payload, result.physical_checksum);
    return result;
}

DecodedBody decode_body(std::string_view payload) {
    Reader r(payload);
    if (r.string() != BODY_VERSION)
        throw std::runtime_error("unsupported canonical body payload");
    DecodedBody result;
    result.body_revision = r.u64();
    result.encoding_generation = r.u64();
    result.codec = r.string();
    if (result.encoding_generation != RAW_ENCODING_GENERATION || result.codec != RAW_CODEC)
        throw std::runtime_error("invalid canonical physical encoding descriptor");
    const auto n = r.size();
    result.blocks.reserve(n);
    for (std::size_t i = 0; i < n; ++i) {
        ContentBlock block;
        block.id = ContentBlockId{r.string()};
        const auto kind = r.size();
        if (kind > 2)
            throw std::runtime_error("invalid canonical body kind");
        block.kind = static_cast<ContentBlockKind>(kind);
        const auto has_parent = r.size();
        if (has_parent > 1)
            throw std::runtime_error("invalid canonical body parent marker");
        if (has_parent == 1)
            block.parent_id = ContentBlockId{r.string()};
        block.text = r.string();
        result.blocks.push_back(std::move(block));
    }
    result.logical_body_digest = r.digest();
    if (result.logical_body_digest.algorithm != SHA256_DIGEST_ALGORITHM)
        throw std::runtime_error("unsupported canonical logical digest algorithm");
    result.physical_checksum = r.u64();
    r.end();
    const auto canonical_payload = encode_body_payload(
        result.blocks,
        result.body_revision,
        result.encoding_generation,
        result.codec,
        result.logical_body_digest);
    if (logical_body_digest_impl(result.blocks) != result.logical_body_digest)
        throw std::runtime_error("canonical logical body digest mismatch");
    if (digest_bytes(canonical_payload) != result.physical_checksum)
        throw std::runtime_error("canonical physical body checksum mismatch");
    return result;
}

CanonicalDocumentRevision bind_body(const DecodedRevision& decoded_revision,
                                    const DecodedBody& body) {
    if (decoded_revision.body_revision != body.body_revision ||
        decoded_revision.logical_body_digest != body.logical_body_digest ||
        body.blocks.size() != decoded_revision.revision.blocks.size()) {
        throw std::runtime_error("canonical body binding mismatch");
    }
    auto revision = decoded_revision.revision;
    for (std::size_t i = 0; i < body.blocks.size(); ++i) {
        const auto& body_block = body.blocks[i];
        auto& revision_block = revision.blocks[i];
        if (body_block.id != revision_block.id || body_block.kind != revision_block.kind ||
            body_block.parent_id != revision_block.parent_id) {
            throw std::runtime_error("canonical body structure mismatch");
        }
        revision_block.text = body_block.text;
    }
    return revision;
}

std::string encode_ledger(const std::set<ContentBlockId>& ids) {
    std::string out;
    put_string(out, LEDGER_VERSION);
    put_size(out, ids.size());
    for (const auto& id : ids)
        put_string(out, id.value());
    return out;
}

std::set<ContentBlockId> decode_ledger(std::string_view payload) {
    Reader r(payload);
    if (r.string() != LEDGER_VERSION)
        throw std::runtime_error("unsupported canonical ledger payload");
    std::set<ContentBlockId> ids;
    const auto n = r.size();
    for (std::size_t i = 0; i < n; ++i)
        ids.insert(ContentBlockId{r.string()});
    r.end();
    return ids;
}

} // namespace

namespace detail {

std::array<std::uint8_t, 32> canonical_decoded_content_digest(
    const std::vector<ContentBlock>& blocks) {
    return logical_body_digest_impl(blocks).value;
}

} // namespace detail

class MdbxCanonicalContentStore::Impl final {
  public:
    explicit Impl(MdbxCanonicalContentStoreOptions options)
        : context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(std::move(options.path),
                                                             options.relative_to_exe,
                                                             options.max_dbs)),
          prefix(safe_part(std::move(options.table_prefix))),
          heads(context->connection(), prefix + "_canonical_heads"),
          revisions(context->connection(), prefix + "_canonical_revisions"),
          bodies(context->connection(), prefix + "_canonical_bodies"),
          ledgers(context->connection(), prefix + "_canonical_ledgers") {}

    struct Loaded {
        std::optional<CanonicalDocumentRevision> current; ///< Current immutable revision.
        std::set<ContentBlockId> ledger;                  ///< Durable block-ID no-reuse ledger.
        std::uint64_t body_revision = 0;                  ///< Logical body bound to current.
        std::uint64_t body_generation = 0;                ///< Physical encoding generation.
        LogicalBodyDigest logical_body_digest;            ///< Algorithm-tagged decoded digest.
    };

    Loaded load(const DocumentId& id, const mdbxc::Transaction& txn) {
        // Resolve and cross-check the complete current state inside the caller's
        // single snapshot. A missing or mismatched link fails closed.
        Loaded loaded;
        const auto head = heads.find(id.value(), txn);
        if (!head)
            return loaded;
        const auto ledger = ledgers.find(id.value(), txn);
        if (!ledger)
            throw std::runtime_error("canonical head references missing ledger");
        loaded.ledger = decode_ledger(*ledger);
        const auto head_revision = parse_u64(*head, "head revision");
        const auto payload = revisions.find(key(id.value(), head_revision), txn);
        if (!payload)
            throw std::runtime_error("canonical head references missing revision");
        const auto decoded = decode_revision(*payload);
        if (decoded.revision.document_id != id || decoded.revision.revision != head_revision)
            throw std::runtime_error("canonical head binding mismatch");
        const auto body = bodies.find(body_key(id.value(), decoded.body_revision, RAW_ENCODING_GENERATION),
                                      txn);
        if (!body)
            throw std::runtime_error("canonical revision references missing body");
        const auto decoded_body = decode_body(*body);
        loaded.body_revision = decoded.body_revision;
        loaded.body_generation = decoded_body.encoding_generation;
        loaded.logical_body_digest = decoded.logical_body_digest;
        loaded.current = bind_body(decoded, decoded_body);
        return loaded;
    }

    std::optional<CanonicalDocumentRevision> read_revision(const DocumentId& id,
                                                           std::uint64_t revision,
                                                           const mdbxc::Transaction& txn) const {
        const auto payload = revisions.find(key(id.value(), revision), txn);
        if (!payload)
            return std::nullopt;
        const auto decoded = decode_revision(*payload);
        if (decoded.revision.document_id != id || decoded.revision.revision != revision)
            throw std::runtime_error("canonical revision identity mismatch");
        const auto body = bodies.find(body_key(id.value(), decoded.body_revision, RAW_ENCODING_GENERATION),
                                      txn);
        if (!body)
            throw std::runtime_error("canonical revision references missing body");
        return bind_body(decoded, decode_body(*body));
    }

    void write_revision(const CanonicalDocumentRevision& revision,
                        std::uint64_t body_revision,
                        std::uint64_t body_generation,
                        const LogicalBodyDigest& logical_body_digest,
                        const std::optional<EncodedBody>& body,
                        const std::set<ContentBlockId>& ledger,
                        const mdbxc::Transaction& txn) {
        // The caller supplies one writable transaction for all logical records.
        // A metadata-only edit intentionally leaves the immutable body row alone.
        revisions.insert_or_assign(
            key(revision.document_id.value(), revision.revision),
            encode_revision(revision, body_revision, logical_body_digest),
            txn);
        if (body) {
            bodies.insert_or_assign(
                body_key(revision.document_id.value(), body_revision, body_generation),
                body->payload,
                txn);
        }
        heads.insert_or_assign(
            revision.document_id.value(), std::to_string(revision.revision), txn);
        ledgers.insert_or_assign(revision.document_id.value(), encode_ledger(ledger), txn);
    }

    std::shared_ptr<MdbxStorageContext> context; ///< Shared MDBX environment.
    std::string prefix;                          ///< Sanitized application table prefix.
    mutable std::mutex mutex;                    ///< Serializes this wrapper's table access.
    mdbxc::KeyValueTable<std::string, std::string> heads;     ///< Current revision per document.
    mdbxc::KeyValueTable<std::string, std::string> revisions; ///< Immutable revision records.
    mdbxc::KeyValueTable<std::string, std::string> bodies;    ///< Raw/plain physical bodies.
    mdbxc::KeyValueTable<std::string, std::string> ledgers;   ///< Historical block-ID ledgers.
};

MdbxCanonicalContentStore::MdbxCanonicalContentStore(MdbxCanonicalContentStoreOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxCanonicalContentStore::~MdbxCanonicalContentStore() = default;

bool MdbxCanonicalContentStore::create_document(CanonicalDocumentRevision initial) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    InMemoryCanonicalContentStore validator;
    if (!validator.create_document(initial))
        return false;
    initial = *validator.read_current(initial.document_id);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    if (m_impl->heads.find(initial.document_id.value(), txn))
        return false;
    std::set<ContentBlockId> ids;
    for (const auto& block : initial.blocks)
        ids.insert(block.id);
    const auto body = encode_body(initial, 1);
    m_impl->write_revision(
        initial, 1, RAW_ENCODING_GENERATION, body.logical_body_digest, body, ids, txn);
    txn.commit();
    return true;
}

std::optional<CanonicalDocumentRevision>
MdbxCanonicalContentStore::read_current(const DocumentId& id) const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto head = m_impl->heads.find(id.value(), txn);
    if (!head)
        return std::nullopt;
    return m_impl->read_revision(id, parse_u64(*head, "head revision"), txn);
}

std::optional<CanonicalDocumentRevision>
MdbxCanonicalContentStore::read_revision(const DocumentId& id, std::uint64_t rev) const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    return m_impl->read_revision(id, rev, txn);
}

std::optional<ContentBlock> MdbxCanonicalContentStore::read_block(
    const DocumentId& id, const ContentBlockId& bid, std::optional<std::uint64_t> rev) const {
    const auto state = rev ? read_revision(id, *rev) : read_current(id);
    if (!state)
        return std::nullopt;
    for (const auto& b : state->blocks)
        if (b.id == bid)
            return b;
    return std::nullopt;
}

std::optional<std::string>
MdbxCanonicalContentStore::materialize_markdown(const DocumentId& id,
                                                std::optional<std::uint64_t> rev) const {
    const auto state = rev ? read_revision(id, *rev) : read_current(id);
    if (!state)
        return std::nullopt;
    auto baseline = *state;
    baseline.revision = 0;
    for (auto& b : baseline.blocks)
        b.revision = 1;
    InMemoryCanonicalContentStore temp;
    if (!temp.create_document(std::move(baseline)))
        return std::nullopt;
    return temp.materialize_markdown(id);
}

CanonicalEditResult MdbxCanonicalContentStore::commit(const CanonicalEditRequest& request) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    auto loaded = m_impl->load(request.document_id, txn);
    if (!loaded.current) {
        txn.rollback();
        CanonicalEditResult result;
        result.status = CanonicalEditStatus::NotFound;
        result.message = "document not found";
        return result;
    }
    InMemoryCanonicalContentStore temp;
    const auto previous = *loaded.current;
    if (!detail::CanonicalContentHistoryLoader::restore_current(
            temp, std::move(*loaded.current), loaded.ledger)) {
        throw std::runtime_error("corrupt canonical history");
    }
    auto result = temp.commit(request);
    if (!result.succeeded()) {
        txn.rollback();
        return result;
    }
    if (result.status == CanonicalEditStatus::NoChange) {
        txn.rollback();
        return result;
    }
    const auto& revision = *result.revision;
    if (loaded.body_revision == 0 || loaded.body_generation == 0)
        throw std::runtime_error("missing logical body binding");

    std::uint64_t body_revision = loaded.body_revision;
    const std::uint64_t body_generation = loaded.body_generation;
    // Metadata alone does not change decoded canonical bytes. Any block
    // identity, kind, parent, order, or text change advances BodyRevision.
    bool body_changed = previous.blocks.size() != revision.blocks.size();
    if (!body_changed) {
        for (std::size_t i = 0; i < revision.blocks.size(); ++i) {
            const auto& left = previous.blocks[i];
            const auto& right = revision.blocks[i];
            if (left.id != right.id || left.kind != right.kind ||
                left.parent_id != right.parent_id || left.text != right.text) {
                body_changed = true;
                break;
            }
        }
    }
    std::optional<EncodedBody> body;
    LogicalBodyDigest logical_body_digest = loaded.logical_body_digest;
    if (body_changed) {
        ++body_revision;
        body = encode_body(revision, body_revision);
        logical_body_digest = body->logical_body_digest;
    }
    std::set<ContentBlockId> ledger = loaded.ledger;
    for (const auto& block : revision.blocks)
        ledger.insert(block.id);
    m_impl->write_revision(
        revision, body_revision, body_generation, logical_body_digest, body, ledger, txn);
    txn.commit();
    return result;
}

} // namespace agent_memory
#endif
