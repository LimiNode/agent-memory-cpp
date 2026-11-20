#include "MdbxCanonicalContentStore.hpp"

#include <agent_memory/domain/Sha256.hpp>
#include <agent_memory/storage/CanonicalContentStoreInternal.hpp>
#include "MdbxStorageErrors.hpp"

#if AGENT_MEMORY_HAS_MDBX
#include <array>
#include <algorithm>
#include <cctype>
#include <charconv>
#include <chrono>
#include <mdbx_containers/KeyValueTable.hpp>
#include <map>
#include <mutex>
#include <limits>
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
constexpr std::size_t kMaxNavigationBlocks = 1U << 20;

// The shared BlobDigest value type is specified by the artifact-provenance
// contract but is not materialized in the C++ domain yet. C1 keeps the same
// algorithm-tagged wire shape locally so it can be mechanically adopted later.
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

    std::string_view view() {
        const auto n = size();
        if (n > m_payload.size() - m_pos)
            throw std::runtime_error("canonical payload overrun");
        const auto value = m_payload.substr(m_pos, n);
        m_pos += n;
        return value;
    }

    std::string string() {
        const auto value = view();
        return std::string(value.data(), value.size());
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

    std::string_view raw_bytes(std::size_t n) {
        if (n > m_payload.size() - m_pos)
            throw std::runtime_error("canonical payload overrun");
        const auto value = m_payload.substr(m_pos, n);
        m_pos += n;
        return value;
    }

    std::uint64_t u64() {
        return parse_u64(string(), "integer");
    }

    LogicalBodyDigest digest() {
        LogicalBodyDigest result;
        result.algorithm = byte();
        const auto raw = raw_bytes(result.value.size());
        if(raw.size() != result.value.size())
            throw std::runtime_error("invalid canonical digest size");
        std::copy(raw.begin(), raw.end(), result.value.begin());
        return result;
    }

    [[nodiscard]] std::size_t position() const noexcept { return m_pos; }

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
    for (std::size_t i = 0; i < n; ++i) {
        const auto key = r.string();
        const auto value = r.string();
        result.set(key, value);
    }
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

DecodedRevision decode_revision(
    std::string_view payload,
    std::size_t max_blocks = std::numeric_limits<std::size_t>::max()) {
    Reader r(payload);
    if (r.string() != REV_VERSION)
        throw std::runtime_error("unsupported canonical revision payload");
    const auto body_revision = r.u64();
    const auto logical_body_digest = r.digest();
    if (logical_body_digest.algorithm != SHA256_DIGEST_ALGORITHM)
        throw std::runtime_error("unsupported canonical logical digest algorithm");
    CanonicalDocumentRevision revision{DocumentId{r.string()}, r.u64(), metadata_get(r), {}};
    const auto n = r.size();
    if (n > max_blocks)
        throw std::runtime_error("canonical revision block count exceeds navigation bound");
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

void hash_field(Sha256Accumulator& digest, std::string_view value) {
    const auto length = std::to_string(value.size()) + ":";
    digest.update(length);
    digest.update(value);
}

void hash_size(Sha256Accumulator& digest, std::size_t value) {
    const auto encoded = std::to_string(value) + ":";
    digest.update(encoded);
}

struct NavigationPlan final {
    std::vector<unsigned char> selected;
    bool select_all = false;
    bool include_text = false;
    std::size_t max_blocks = 0;
    std::size_t max_text_bytes = 0;
};

struct NavigationDecoded final {
    CanonicalDocumentRevision revision;
    std::vector<std::size_t> text_sizes;
    std::vector<std::size_t> admitted_indices;
    bool budget_exceeded = false;
    bool selection_root_found = true;
};

// The domain reference builder deliberately favors simple scans.  A storage
// adapter must not inherit that O(V^2) cost for every bounded navigation read.
// Parent links in a canonical preorder let us validate the same tree contract
// in one pass; ordered maps keep the lookup cost at O(V log V) without exposing
// a backend-specific identity type in the public API.
std::optional<CanonicalOutline> build_storage_outline(
    const CanonicalDocumentRevision& document,
    std::size_t max_entries) {
    if (max_entries == 0U || document.blocks.size() > max_entries ||
        document.document_id.empty()) {
        return std::nullopt;
    }

    std::map<ContentBlockId, std::size_t> positions;
    std::map<ContentBlockId, std::size_t> active_depths;
    std::vector<ContentBlockId> active_path;
    std::vector<std::size_t> depths(document.blocks.size(), 0U);
    std::vector<std::size_t> child_counts(document.blocks.size(), 0U);

    for (std::size_t index = 0; index < document.blocks.size(); ++index) {
        const auto& block = document.blocks[index];
        if (block.id.empty() || !positions.emplace(block.id, index).second) {
            return std::nullopt;
        }

        std::size_t depth = 0U;
        if (block.parent_id) {
            const auto parent_position = positions.find(*block.parent_id);
            if (parent_position == positions.end() || parent_position->second >= index) {
                return std::nullopt;
            }
            const auto parent_active = active_depths.find(*block.parent_id);
            if (parent_active == active_depths.end()) {
                // The parent exists but its subtree has already closed, so
                // the input is not canonical preorder.
                return std::nullopt;
            }
            while (active_path.size() > parent_active->second + 1U) {
                active_depths.erase(active_path.back());
                active_path.pop_back();
            }
            depth = parent_active->second + 1U;
            ++child_counts[parent_position->second];
        } else {
            active_depths.clear();
            active_path.clear();
        }

        depths[index] = depth;
        active_path.push_back(block.id);
        active_depths[block.id] = depth;
    }

    CanonicalOutline result{document.document_id, document.revision, {}};
    result.entries.reserve(document.blocks.size());
    for (std::size_t index = 0; index < document.blocks.size(); ++index) {
        const auto& block = document.blocks[index];
        result.entries.push_back({
            {document.document_id, document.revision, block.id, block.revision},
            block.kind,
            block.parent_id,
            depths[index],
            block.text.size(),
            child_counts[index]
        });
    }
    return result;
}

NavigationDecoded decode_navigation_body(const DecodedRevision& decoded,
                                         std::string_view payload,
                                         const NavigationPlan& plan) {
    Reader r(payload);
    if (r.string() != BODY_VERSION)
        throw std::runtime_error("unsupported canonical body payload");
    const auto body_revision = r.u64();
    const auto encoding_generation = r.u64();
    const auto codec = r.string();
    if (encoding_generation != RAW_ENCODING_GENERATION || codec != RAW_CODEC)
        throw std::runtime_error("invalid canonical physical encoding descriptor");
    const auto block_count = r.size();
    if (block_count > kMaxNavigationBlocks)
        throw std::runtime_error("canonical body block count exceeds navigation bound");
    if (block_count != decoded.revision.blocks.size() ||
        (!plan.select_all && plan.selected.size() != block_count))
        throw std::runtime_error("canonical body binding mismatch");

    NavigationDecoded result;
    result.revision = decoded.revision;
    result.text_sizes.resize(block_count);
    Sha256Accumulator logical_digest;
    hash_field(logical_digest, LOGICAL_BODY_VERSION);
    hash_size(logical_digest, block_count);
    std::size_t text_bytes = 0U;
    for (std::size_t index = 0; index < block_count; ++index) {
        auto& expected = decoded.revision.blocks[index];
        const auto id = r.string();
        const auto kind = r.size();
        if (kind > 2U)
            throw std::runtime_error("invalid canonical body kind");
        const auto has_parent = r.size();
        if (has_parent > 1U)
            throw std::runtime_error("invalid canonical body parent marker");
        std::optional<std::string> parent;
        if (has_parent == 1U)
            parent = r.string();
        const auto text = r.view();
        result.text_sizes[index] = text.size();

        if (id != expected.id.value() || kind != static_cast<std::size_t>(expected.kind) ||
            (parent.has_value() != expected.parent_id.has_value()) ||
            (parent && *parent != expected.parent_id->value())) {
            throw std::runtime_error("canonical body structure mismatch");
        }
        hash_field(logical_digest, id);
        hash_size(logical_digest, kind);
        hash_size(logical_digest, has_parent);
        if (parent)
            hash_field(logical_digest, *parent);
        hash_field(logical_digest, text);

        const bool selected = plan.select_all || plan.selected[index] != 0U;
        if (selected && !result.budget_exceeded) {
            if (result.admitted_indices.size() >= plan.max_blocks ||
                (plan.include_text &&
                 (text_bytes > plan.max_text_bytes ||
                  text.size() > plan.max_text_bytes - text_bytes))) {
                result.budget_exceeded = true;
            } else {
                result.admitted_indices.push_back(index);
                if (plan.include_text) {
                    result.revision.blocks[index].text.assign(text.data(), text.size());
                    text_bytes += text.size();
                }
            }
        }
    }

    const auto stored_logical_digest = r.digest();
    if (body_revision != decoded.body_revision ||
        stored_logical_digest != decoded.logical_body_digest ||
        logical_digest.finish() != stored_logical_digest.value) {
        throw std::runtime_error("canonical logical body digest mismatch");
    }
    const auto checksum_position = r.position();
    const auto stored_physical_checksum = r.u64();
    r.end();
    if (digest_bytes(payload.substr(0, checksum_position)) != stored_physical_checksum)
        throw std::runtime_error("canonical physical body checksum mismatch");
    return result;
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
    const auto encoded = encode_logical_body(blocks);
    return {SHA256_DIGEST_ALGORITHM, sha256(encoded)};
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
    if (n > kMaxNavigationBlocks)
        throw std::runtime_error("canonical body block count exceeds navigation bound");
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
          metrics(options.metrics),
          prefix(safe_part(std::move(options.table_prefix))),
          heads(context->connection(), prefix + "_canonical_heads"),
          revisions(context->connection(), prefix + "_canonical_revisions"),
          bodies(context->connection(), prefix + "_canonical_bodies"),
          ledgers(context->connection(), prefix + "_canonical_ledgers"),
          publication_guard_table_name(options.publication_guard_table_name),
          publication_guard_table(
              options.publication_guard_table_name.empty()
                  ? nullptr
                  : std::make_unique<mdbxc::KeyValueTable<std::string, std::string>>(
                        context->connection(),
                        options.publication_guard_table_name
                    )
          ),
          publication_token_guard_table_name(options.publication_token_guard_table_name),
          publication_token_guard_table(
              options.publication_token_guard_table_name.empty()
                  ? nullptr
                  : std::make_unique<mdbxc::KeyValueTable<std::string, std::string>>(
                        context->connection(),
                        options.publication_token_guard_table_name
                    )
          ) {}

    using Table = mdbxc::KeyValueTable<std::string, std::string>;

    std::optional<std::string> find_value(
        const Table& table, const std::string& key, const mdbxc::Transaction& txn) const {
        const auto value = table.find(key, txn);
        if (metrics) {
            ++metrics->value_reads;
            if (value) metrics->value_bytes_read += value->size();
        }
        return value;
    }

    void put_value(Table& table, const std::string& key, const std::string& value,
                   const mdbxc::Transaction& txn) {
        table.insert_or_assign(key, value, txn);
        if (metrics) {
            ++metrics->value_writes;
            metrics->value_bytes_written += value.size();
        }
    }

    template<typename Function>
    auto measure_codec(Function&& function, bool encode) const {
        if (!metrics) return function();
        const auto begin = std::chrono::steady_clock::now();
        auto result = function();
        const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - begin).count();
        (encode ? metrics->encode_ns : metrics->decode_ns) += static_cast<std::uint64_t>(ns);
        return result;
    }

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
        const auto head = find_value(heads, id.value(), txn);
        if (!head)
            return loaded;
        const auto ledger = find_value(ledgers, id.value(), txn);
        if (!ledger)
            throw std::runtime_error("canonical head references missing ledger");
        loaded.ledger = measure_codec([&] { return decode_ledger(*ledger); }, false);
        const auto head_revision = parse_u64(*head, "head revision");
        const auto payload = find_value(revisions, key(id.value(), head_revision), txn);
        if (!payload)
            throw std::runtime_error("canonical head references missing revision");
        const auto decoded = measure_codec([&] { return decode_revision(*payload); }, false);
        if (decoded.revision.document_id != id || decoded.revision.revision != head_revision)
            throw std::runtime_error("canonical head binding mismatch");
        const auto body = find_value(bodies, body_key(id.value(), decoded.body_revision, RAW_ENCODING_GENERATION),
                                      txn);
        if (!body)
            throw std::runtime_error("canonical revision references missing body");
        const auto decoded_body = measure_codec([&] { return decode_body(*body); }, false);
        loaded.body_revision = decoded.body_revision;
        loaded.body_generation = decoded_body.encoding_generation;
        loaded.logical_body_digest = decoded.logical_body_digest;
        loaded.current = bind_body(decoded, decoded_body);
        return loaded;
    }

    std::optional<DecodedRevision> read_decoded_revision(
        const DocumentId& id,
        std::uint64_t revision,
        const mdbxc::Transaction& txn,
        std::size_t max_blocks = std::numeric_limits<std::size_t>::max()) const {
        const auto payload = find_value(revisions, key(id.value(), revision), txn);
        if (!payload)
            return std::nullopt;
        const auto decoded = measure_codec([&] { return decode_revision(*payload, max_blocks); }, false);
        if (decoded.revision.document_id != id || decoded.revision.revision != revision)
            throw std::runtime_error("canonical revision identity mismatch");
        return decoded;
    }

    std::optional<CanonicalDocumentRevision> read_revision(
        const DocumentId& id,
        std::uint64_t revision,
        const mdbxc::Transaction& txn) const {
        const auto decoded = read_decoded_revision(id, revision, txn);
        if (!decoded)
            return std::nullopt;
        const auto body = find_value(bodies, body_key(id.value(), decoded->body_revision, RAW_ENCODING_GENERATION),
                                      txn);
        if (!body)
            throw std::runtime_error("canonical revision references missing body");
        return bind_body(*decoded, measure_codec([&] { return decode_body(*body); }, false));
    }

    std::optional<NavigationDecoded> read_navigation_revision(
        const DocumentId& id,
        std::uint64_t revision,
        const NavigationPlan& plan,
        const mdbxc::Transaction& txn) const {
        const auto decoded = read_decoded_revision(
            id, revision, txn, kMaxNavigationBlocks);
        if (!decoded)
            return std::nullopt;
        const auto body = find_value(bodies,
            body_key(id.value(), decoded->body_revision, RAW_ENCODING_GENERATION), txn);
        if (!body)
            throw std::runtime_error("canonical revision references missing body");
        return measure_codec([&] { return decode_navigation_body(*decoded, *body, plan); }, false);
    }

    std::optional<NavigationDecoded> read_navigation_subtree(
        const DocumentId& id,
        std::uint64_t revision,
        const CanonicalSubtreeRequest& request,
        const mdbxc::Transaction& txn) const {
        const auto decoded = read_decoded_revision(
            id, revision, txn, kMaxNavigationBlocks);
        if (!decoded)
            return std::nullopt;
        const auto outline = build_storage_outline(
            decoded->revision, std::numeric_limits<std::size_t>::max());
        if (!outline)
            throw std::runtime_error("canonical revision structure is invalid");

        NavigationPlan plan;
        plan.include_text = request.include_text;
        plan.max_blocks = request.max_blocks;
        plan.max_text_bytes = request.max_text_bytes;
        plan.selected.assign(decoded->revision.blocks.size(), 0U);
        if (!request.root_id) {
            plan.select_all = true;
        } else {
            const auto root = std::find_if(
                outline->entries.begin(), outline->entries.end(),
                [&](const CanonicalOutlineEntry& entry) {
                    return entry.locator.block_id == *request.root_id;
                });
            if (root == outline->entries.end()) {
                const auto body = find_value(bodies,
                    body_key(id.value(), decoded->body_revision, RAW_ENCODING_GENERATION), txn);
                if (!body)
                    throw std::runtime_error("canonical revision references missing body");
                auto result = measure_codec([&] { return decode_navigation_body(*decoded, *body, plan); }, false);
                result.selection_root_found = false;
                return result;
            }
            const auto root_index = static_cast<std::size_t>(
                std::distance(outline->entries.begin(), root));
            const auto root_depth = root->depth;
            for (std::size_t index = root_index; index < outline->entries.size(); ++index) {
                if (index != root_index && outline->entries[index].depth <= root_depth)
                    break;
                plan.selected[index] = 1U;
            }
        }

        const auto body = find_value(bodies,
            body_key(id.value(), decoded->body_revision, RAW_ENCODING_GENERATION), txn);
        if (!body)
            throw std::runtime_error("canonical revision references missing body");
        return measure_codec([&] { return decode_navigation_body(*decoded, *body, plan); }, false);
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
        put_value(revisions,
            key(revision.document_id.value(), revision.revision),
            measure_codec([&] { return encode_revision(revision, body_revision, logical_body_digest); }, true),
            txn);
        if (body) {
            put_value(bodies,
                body_key(revision.document_id.value(), body_revision, body_generation),
                body->payload,
                txn);
        }
        put_value(heads,
            revision.document_id.value(), std::to_string(revision.revision), txn);
        put_value(ledgers, revision.document_id.value(), measure_codec([&] { return encode_ledger(ledger); }, true), txn);
    }

    bool guard_matches(
        const MdbxCanonicalPublicationGuard& guard,
        const mdbxc::Transaction& txn
    ) {
        if(guard.table_name.empty() || guard.key.empty()) {
            throw std::invalid_argument("canonical publication guard is incomplete");
        }
        if(!publication_guard_table || guard.table_name != publication_guard_table_name) {
            throw std::invalid_argument("canonical publication guard table is not configured");
        }
        if(guard.expected_token.empty()) {
            const auto current = find_value(*publication_guard_table, guard.key, txn);
            if(!current) {
                throw MdbxPublicationFenceError(
                    "canonical publication fence rejected: key missing"
                );
            }
            if(*current != guard.expected_value) {
                throw MdbxPublicationFenceError(
                    "canonical publication fence rejected: value changed"
                );
            }
        }
        if(!guard.expected_token.empty()) {
            if(!publication_token_guard_table ||
               guard.token_table_name != publication_token_guard_table_name) {
                throw std::invalid_argument(
                    "canonical publication token table is not configured"
                );
            }
            const auto token = find_value(*publication_token_guard_table, guard.key, txn);
            if(!token || *token != guard.expected_token) {
                throw MdbxPublicationFenceError(
                    "canonical publication fence rejected: token changed"
                );
            }
        }
        return true;
    }

    std::shared_ptr<MdbxStorageContext> context; ///< Shared MDBX environment.
    MdbxCanonicalStorageMetrics* metrics = nullptr; ///< Optional externally synchronized counters.
    std::string prefix;                          ///< Sanitized application table prefix.
    mutable std::mutex mutex;                    ///< Serializes this wrapper's table access.
    mdbxc::KeyValueTable<std::string, std::string> heads;     ///< Current revision per document.
    mdbxc::KeyValueTable<std::string, std::string> revisions; ///< Immutable revision records.
    mdbxc::KeyValueTable<std::string, std::string> bodies;    ///< Raw/plain physical bodies.
    mdbxc::KeyValueTable<std::string, std::string> ledgers;   ///< Historical block-ID ledgers.
    std::string publication_guard_table_name;
    std::unique_ptr<mdbxc::KeyValueTable<std::string, std::string>> publication_guard_table;
    std::string publication_token_guard_table_name;
    std::unique_ptr<mdbxc::KeyValueTable<std::string, std::string>>
        publication_token_guard_table;
};

MdbxCanonicalContentStore::MdbxCanonicalContentStore(MdbxCanonicalContentStoreOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxCanonicalContentStore::~MdbxCanonicalContentStore() = default;

bool MdbxCanonicalContentStore::create_document(CanonicalDocumentRevision initial) {
    return create_document_impl(std::move(initial), std::nullopt);
}

bool MdbxCanonicalContentStore::create_document_guarded(
    CanonicalDocumentRevision initial,
    MdbxCanonicalPublicationGuard guard
) {
    return create_document_impl(std::move(initial), std::move(guard));
}

bool MdbxCanonicalContentStore::create_document_impl(
    CanonicalDocumentRevision initial,
    std::optional<MdbxCanonicalPublicationGuard> guard
) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    InMemoryCanonicalContentStore validator;
    if (!validator.create_document(initial))
        return false;
    initial = *validator.read_current(initial.document_id);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    if (guard && !m_impl->guard_matches(*guard, txn)) {
        txn.rollback();
        throw MdbxPublicationFenceError(
            "canonical publication fence rejected the creation"
        );
    }
    if (m_impl->find_value(m_impl->heads, initial.document_id.value(), txn))
        return false;
    std::set<ContentBlockId> ids;
    for (const auto& block : initial.blocks)
        ids.insert(block.id);
    const auto body = m_impl->measure_codec([&] { return encode_body(initial, 1); }, true);
    m_impl->write_revision(
        initial, 1, RAW_ENCODING_GENERATION, body.logical_body_digest, body, ids, txn);
    txn.commit();
    return true;
}

std::optional<CanonicalDocumentRevision>
MdbxCanonicalContentStore::read_current(const DocumentId& id) const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto head = m_impl->find_value(m_impl->heads, id.value(), txn);
    if (!head) {
        txn.commit();
        return std::nullopt;
    }
    auto result = m_impl->read_revision(id, parse_u64(*head, "head revision"), txn);
    txn.commit();
    return result;
}

std::optional<CanonicalDocumentRevision>
MdbxCanonicalContentStore::read_revision(const DocumentId& id, std::uint64_t rev) const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    auto result = m_impl->read_revision(id, rev, txn);
    txn.commit();
    return result;
}

std::optional<CanonicalOutline> MdbxCanonicalContentStore::read_outline(
    const DocumentId& id, std::uint64_t rev, std::size_t max_entries) const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    NavigationPlan plan;
    plan.select_all = true;
    plan.max_blocks = std::numeric_limits<std::size_t>::max();
    const auto navigation = m_impl->read_navigation_revision(id, rev, plan, txn);
    txn.commit();
    if (!navigation) {
        return std::nullopt;
    }
    auto outline = build_storage_outline(navigation->revision, max_entries);
    if (!outline || navigation->text_sizes.size() != outline->entries.size()) {
        return std::nullopt;
    }
    for (std::size_t index = 0; index < outline->entries.size(); ++index) {
        outline->entries[index].text_bytes = navigation->text_sizes[index];
    }
    return outline;
}

CanonicalChildrenResult MdbxCanonicalContentStore::read_children(
    const DocumentId& id, std::uint64_t rev, std::optional<ContentBlockId> parent_id,
    std::size_t max_children) const {
    CanonicalChildrenResult result;
    result.document_id = id;
    result.document_revision = rev;
    result.parent_id = parent_id;
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    if (max_children == 0U) {
        const auto decoded = m_impl->read_decoded_revision(
            id, rev, txn, kMaxNavigationBlocks);
        txn.commit();
        if (!decoded) {
            result.status = CanonicalNavigationStatus::NotFound;
            result.message = "document revision was not found";
            return result;
        }
        result.message = "max_children must be non-zero";
        return result;
    }
    NavigationPlan plan;
    plan.select_all = true;
    plan.max_blocks = std::numeric_limits<std::size_t>::max();
    const auto navigation = m_impl->read_navigation_revision(id, rev, plan, txn);
    txn.commit();
    if (!navigation) {
        result.status = CanonicalNavigationStatus::NotFound;
        result.message = "document revision was not found";
        return result;
    }
    const auto outline = build_storage_outline(
        navigation->revision, std::numeric_limits<std::size_t>::max());
    if (!outline || navigation->text_sizes.size() != outline->entries.size()) {
        result.message = "document is malformed or exceeds navigation bounds";
        return result;
    }
    if (parent_id) {
        const auto parent = std::find_if(
            outline->entries.begin(), outline->entries.end(),
            [&](const CanonicalOutlineEntry& entry) {
                return entry.locator.block_id == *parent_id;
            });
        if (parent == outline->entries.end()) {
            result.status = CanonicalNavigationStatus::NotFound;
            result.message = "parent block was not found";
            return result;
        }
    }
    result.status = CanonicalNavigationStatus::Ok;
    for (std::size_t index = 0; index < outline->entries.size(); ++index) {
        auto entry = outline->entries[index];
        entry.text_bytes = navigation->text_sizes[index];
        if (entry.parent_id != parent_id) {
            continue;
        }
        if (result.entries.size() >= max_children) {
            result.status = CanonicalNavigationStatus::BudgetExceeded;
            result.truncated = true;
            result.message = "children budget exhausted";
            break;
        }
        result.entries.push_back(std::move(entry));
    }
    return result;
}

CanonicalSubtreeResult MdbxCanonicalContentStore::read_subtree(
    const DocumentId& id, std::uint64_t rev,
    const CanonicalSubtreeRequest& request) const {
    CanonicalSubtreeResult result;
    result.document_id = id;
    result.document_revision = rev;
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    if (request.max_blocks == 0U) {
        const auto decoded = m_impl->read_decoded_revision(
            id, rev, txn, kMaxNavigationBlocks);
        txn.commit();
        if (!decoded) {
            result.status = CanonicalNavigationStatus::NotFound;
            result.message = "document revision was not found";
            return result;
        }
        result.message = "max_blocks must be non-zero";
        return result;
    }
    const auto navigation = m_impl->read_navigation_subtree(id, rev, request, txn);
    txn.commit();
    if (!navigation) {
        result.status = CanonicalNavigationStatus::NotFound;
        result.message = "document revision was not found";
        return result;
    }
    if (!navigation->selection_root_found) {
        result.status = CanonicalNavigationStatus::NotFound;
        result.message = "subtree root was not found";
        return result;
    }
    result.status = navigation->budget_exceeded
        ? CanonicalNavigationStatus::BudgetExceeded
        : CanonicalNavigationStatus::Ok;
    result.truncated = navigation->budget_exceeded;
    if (result.truncated) {
        result.message = "subtree budget exhausted";
    }
    result.blocks.reserve(navigation->admitted_indices.size());
    for (const auto index : navigation->admitted_indices) {
        if (index >= navigation->revision.blocks.size()) {
            result.status = CanonicalNavigationStatus::InvalidDocument;
            result.blocks.clear();
            result.message = "navigation decoder returned an invalid block index";
            return result;
        }
        result.blocks.push_back(navigation->revision.blocks[index]);
    }
    return result;
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
    return commit_impl(request, std::nullopt);
}

CanonicalEditResult MdbxCanonicalContentStore::commit_guarded(
    const CanonicalEditRequest& request,
    MdbxCanonicalPublicationGuard guard
) {
    return commit_impl(request, std::move(guard));
}

CanonicalEditResult MdbxCanonicalContentStore::commit_impl(
    const CanonicalEditRequest& request,
    std::optional<MdbxCanonicalPublicationGuard> guard
) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    if (guard && !m_impl->guard_matches(*guard, txn)) {
        txn.rollback();
        throw MdbxPublicationFenceError(
            "canonical publication fence rejected the edit"
        );
    }
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
        body = m_impl->measure_codec([&] { return encode_body(revision, body_revision); }, true);
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
