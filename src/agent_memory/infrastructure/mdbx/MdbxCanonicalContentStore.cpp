#include "MdbxCanonicalContentStore.hpp"

#include <agent_memory/storage/CanonicalContentStoreInternal.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <cctype>
#include <charconv>
#include <mdbx_containers/KeyValueTable.hpp>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string_view>
#include <system_error>
#include <utility>

namespace agent_memory {
namespace {

// Every payload begins with an application-owned version marker. The compact
// length-prefixed codec is binary-safe and rejects truncation, overflow,
// unknown versions, and trailing data before domain objects are exposed.
constexpr std::string_view REV_VERSION = "agent_memory.canonical_revision.v2";
constexpr std::string_view BODY_VERSION = "agent_memory.canonical_body.v2";
constexpr std::string_view LEDGER_VERSION = "agent_memory.canonical_ledger.v1";
constexpr std::uint64_t RAW_ENCODING_GENERATION = 1;
constexpr std::string_view RAW_CODEC = "raw";

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

std::uint64_t parse_u64(std::string_view text, std::string_view field) {
    std::uint64_t value = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size())
        throw std::runtime_error("invalid canonical " + std::string(field));
    return value;
}

std::uint64_t digest_bytes(std::string_view bytes) {
    // C1 uses a deterministic integrity digest; a cryptographic body digest is
    // reserved for the artifact-aware body store contract.
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

    std::uint64_t u64() {
        return parse_u64(string(), "integer");
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
    CanonicalDocumentRevision revision;
    std::uint64_t body_revision = 0;
    std::uint64_t encoding_generation = 0;
    std::uint64_t body_digest = 0;
};

std::string encode_revision(const CanonicalDocumentRevision& revision,
                            std::uint64_t body_revision,
                            std::uint64_t encoding_generation,
                            std::uint64_t body_digest) {
    std::string out;
    put_string(out, REV_VERSION);
    put_u64(out, body_revision);
    put_u64(out, encoding_generation);
    put_u64(out, body_digest);
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
    const auto encoding_generation = r.u64();
    const auto body_digest = r.u64();
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
    return {std::move(revision), body_revision, encoding_generation, body_digest};
}

struct EncodedBody final {
    std::string payload;
    std::uint64_t digest = 0;
};

struct DecodedBody final {
    std::uint64_t body_revision = 0;
    std::uint64_t encoding_generation = 0;
    std::string codec;
    std::vector<ContentBlock> blocks;
    std::uint64_t digest = 0;
};

std::string encode_body_payload(const std::vector<ContentBlock>& blocks,
                                std::uint64_t body_revision,
                                std::uint64_t encoding_generation,
                                std::string_view codec) {
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
    return out;
}

EncodedBody encode_body(const CanonicalDocumentRevision& revision, std::uint64_t body_revision) {
    EncodedBody result;
    result.payload =
        encode_body_payload(revision.blocks, body_revision, RAW_ENCODING_GENERATION, RAW_CODEC);
    result.digest = digest_bytes(result.payload);
    put_u64(result.payload, result.digest);
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
    result.digest = r.u64();
    r.end();
    const auto canonical_payload = encode_body_payload(
        result.blocks, result.body_revision, result.encoding_generation, result.codec);
    if (digest_bytes(canonical_payload) != result.digest)
        throw std::runtime_error("canonical body digest mismatch");
    return result;
}

CanonicalDocumentRevision bind_body(const DecodedRevision& decoded_revision,
                                    const DecodedBody& body) {
    if (decoded_revision.body_revision != body.body_revision ||
        decoded_revision.encoding_generation != body.encoding_generation ||
        decoded_revision.body_digest != body.digest ||
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
        std::uint64_t body_digest = 0;                    ///< Integrity digest of the decoded body.
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
        const auto body = bodies.find(
            body_key(id.value(), decoded.body_revision, decoded.encoding_generation), txn);
        if (!body)
            throw std::runtime_error("canonical revision references missing body");
        const auto decoded_body = decode_body(*body);
        loaded.body_revision = decoded.body_revision;
        loaded.body_generation = decoded.encoding_generation;
        loaded.body_digest = decoded.body_digest;
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
        const auto body = bodies.find(
            body_key(id.value(), decoded.body_revision, decoded.encoding_generation), txn);
        if (!body)
            throw std::runtime_error("canonical revision references missing body");
        return bind_body(decoded, decode_body(*body));
    }

    void write_revision(const CanonicalDocumentRevision& revision,
                        std::uint64_t body_revision,
                        std::uint64_t body_generation,
                        std::uint64_t body_digest,
                        const std::optional<EncodedBody>& body,
                        const std::set<ContentBlockId>& ledger,
                        const mdbxc::Transaction& txn) {
        // The caller supplies one writable transaction for all logical records.
        // A metadata-only edit intentionally leaves the immutable body row alone.
        revisions.insert_or_assign(
            key(revision.document_id.value(), revision.revision),
            encode_revision(revision, body_revision, body_generation, body_digest),
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
    m_impl->write_revision(initial, 1, RAW_ENCODING_GENERATION, body.digest, body, ids, txn);
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
    std::uint64_t body_digest = loaded.body_digest;
    if (body_changed) {
        ++body_revision;
        body = encode_body(revision, body_revision);
        body_digest = body->digest;
    }
    std::set<ContentBlockId> ledger = loaded.ledger;
    for (const auto& block : revision.blocks)
        ledger.insert(block.id);
    m_impl->write_revision(
        revision, body_revision, body_generation, body_digest, body, ledger, txn);
    txn.commit();
    return result;
}

} // namespace agent_memory
#endif
