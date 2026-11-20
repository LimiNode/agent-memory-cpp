#include "MdbxCanonicalFlatStorage.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageIdentityMapping.hpp"
#include <agent_memory/domain/Sha256.hpp>
#include <agent_memory/storage/CanonicalContentStoreInternal.hpp>
#include <agent_memory/storage/CanonicalFlatStorage.hpp>
#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <limits>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {

std::string canonical_flat_composite_key(std::uint64_t identity, std::uint64_t revision) {
    std::string result(16U, '\0');
    for(unsigned index = 0; index < 8U; ++index) {
        result[index] = static_cast<char>(identity >> ((7U - index) * 8U));
        result[index + 8U] = static_cast<char>(revision >> ((7U - index) * 8U));
    }
    return result;
}

namespace {
using Clock = std::chrono::steady_clock;
using Digest = std::array<std::uint8_t, 32>;
using StringTable = mdbxc::KeyValueTable<std::string, std::string>;
constexpr std::string_view kSchemaKey = "@schema_version";
constexpr std::string_view kSchemaVersion = "canonical_flat_storage.v2.be64x2.envelope_sha256";

std::string table_name(std::string prefix, std::string_view suffix) {
    if(prefix.empty()) prefix = "agent_memory";
    for(char& c : prefix) if(!std::isalnum(static_cast<unsigned char>(c))) c = '_';
    prefix.push_back('_');
    prefix.append(suffix.data(), suffix.size());
    return prefix;
}
std::uint64_t elapsed_ns(Clock::time_point start) {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start).count());
}
void append_u64(std::string& output, std::uint64_t value) {
    for(unsigned shift = 0; shift < 64U; shift += 8U) output.push_back(static_cast<char>(value >> shift));
}
void append_string(std::string& output, std::string_view value) {
    append_u64(output, value.size());
    output.append(value.data(), value.size());
}
void append_digest(std::string& output, const Digest& digest) {
    output.append(reinterpret_cast<const char*>(digest.data()), digest.size());
}

// Physical bindings belong to this adapter, not to the portable AMFS/AMFB codec.
// The envelope hash covers the key, document identity, reference sizes and ledger.
class EnvelopeReader final {
  public:
    explicit EnvelopeReader(std::string_view payload) : bytes(payload) {}
    std::string_view take(std::size_t count) {
        if(count > bytes.size() - position) throw std::runtime_error("flat envelope truncated");
        const auto result = bytes.substr(position, count);
        position += count;
        return result;
    }
    std::uint64_t u64() {
        const auto value = take(8U);
        std::uint64_t result = 0;
        for(unsigned index = 0; index < 8U; ++index)
            result |= static_cast<std::uint64_t>(static_cast<unsigned char>(value[index])) << (index * 8U);
        return result;
    }
    std::string string(std::size_t limit) {
        const auto count = u64();
        if(count > limit) throw std::runtime_error("flat envelope string exceeds limit");
        return std::string(take(static_cast<std::size_t>(count)));
    }
    Digest digest() {
        const auto value = take(32U);
        Digest result{};
        std::copy(value.begin(), value.end(), result.begin());
        return result;
    }
    bool ended() const noexcept { return position == bytes.size(); }
  private:
    std::string_view bytes;
    std::size_t position = 0;
};
void add_envelope_size(std::size_t& size, std::size_t count, const CanonicalFlatStorageLimits& limits) {
    if(size > limits.max_payload_bytes || count > limits.max_payload_bytes - size)
        throw std::length_error("flat envelope exceeds payload limit");
    size += count;
}
std::size_t envelope_size(const DocumentId& document, std::size_t inner_size,
                          const CanonicalFlatStorageLimits& limits) {
    // Magic, type, physical key, document length, inner length and SHA-256 trailer.
    std::size_t size = 4U + 1U + 16U + 8U + 8U + 32U;
    add_envelope_size(size, document.value().size(), limits);
    add_envelope_size(size, inner_size, limits);
    return size;
}
void seal(std::string& value, std::size_t expected_size) {
    if(expected_size < 32U || value.size() != expected_size - 32U)
        throw std::runtime_error("flat envelope size calculation mismatch");
    append_digest(value, sha256(value));
}
EnvelopeReader open_envelope(std::string_view payload, char type, std::string_view key,
                             const DocumentId& document, const CanonicalFlatStorageLimits& limits) {
    if(payload.size() < 5U + 16U + 8U + 32U || payload.size() > limits.max_payload_bytes)
        throw std::runtime_error("invalid flat envelope size");
    const auto content = payload.substr(0, payload.size() - 32U);
    EnvelopeReader trailer(payload.substr(payload.size() - 32U));
    if(trailer.digest() != sha256(content)) throw std::runtime_error("flat envelope digest mismatch");
    EnvelopeReader reader(content);
    if(reader.take(4U) != "AMF2" || reader.take(1U)[0] != type || reader.take(16U) != key ||
       reader.string(limits.max_string_bytes) != document.value())
        throw std::runtime_error("flat envelope physical/document binding mismatch");
    return reader;
}
std::string envelope_header(char type, std::string_view key, const DocumentId& document,
                            std::size_t expected_size) {
    std::string result;
    result.reserve(expected_size);
    result.append("AMF2");
    result.push_back(type);
    result.append(key.data(), key.size());
    append_string(result, document.value());
    return result;
}
struct PhysicalRef final {
    std::uint64_t storage_id = 0;
    std::uint64_t text_bytes = 0;
    Digest payload_digest{};
};
struct Structure final {
    CanonicalFlatStructureRecord logical;
    std::vector<PhysicalRef> physical;
    std::set<ContentBlockId> ledger;
    CanonicalOutline outline;
};
ContentBlockKind decode_kind(std::uint8_t kind) {
    switch(kind) {
    case 0: return ContentBlockKind::Heading;
    case 1: return ContentBlockKind::Paragraph;
    case 2: return ContentBlockKind::Code;
    default: throw std::runtime_error("invalid canonical flat block kind");
    }
}
std::uint8_t encode_kind(ContentBlockKind kind) {
    switch(kind) {
    case ContentBlockKind::Heading: return 0;
    case ContentBlockKind::Paragraph: return 1;
    case ContentBlockKind::Code: return 2;
    }
    throw std::runtime_error("invalid canonical block kind");
}
CanonicalFlatBlockRecord to_flat(const ContentBlock& block) {
    CanonicalFlatBlockRecord result;
    result.id = block.id.value();
    result.revision = block.revision;
    result.kind = encode_kind(block.kind);
    if(block.parent_id) result.parent_id = block.parent_id->value();
    result.text = block.text;
    return result;
}
bool same_payload(const ContentBlock& left, const ContentBlock& right) {
    return left.id == right.id && left.kind == right.kind &&
        left.parent_id == right.parent_id && left.text == right.text;
}

// Validate preorder iteratively, without inventing block text to call a renderer.
void build_outline(Structure& structure) {
    const auto& logical = structure.logical;
    structure.outline = {DocumentId{logical.document_id}, logical.document_revision, {}};
    std::map<std::string, std::size_t> seen;
    std::set<std::uint64_t> physical_ids;
    std::vector<std::size_t> ancestors;
    auto& entries = structure.outline.entries;
    entries.reserve(logical.blocks.size());
    for(std::size_t index = 0; index < logical.blocks.size(); ++index) {
        const auto& ref = logical.blocks[index];
        const auto& physical = structure.physical[index];
        if(ref.id.empty() || ref.revision == 0U || physical.storage_id == 0U ||
           !seen.emplace(ref.id, index).second || !physical_ids.insert(physical.storage_id).second ||
           structure.ledger.count(ContentBlockId{ref.id}) == 0U)
            throw std::runtime_error("invalid flat structure identity/ledger");
        if(ref.parent_id) {
            while(!ancestors.empty() && logical.blocks[ancestors.back()].id != *ref.parent_id) ancestors.pop_back();
            if(ancestors.empty()) throw std::runtime_error("invalid flat structure preorder");
            ++entries[ancestors.back()].child_count;
        } else ancestors.clear();
        entries.push_back({
            {structure.outline.document_id, logical.document_revision, ContentBlockId{ref.id}, ref.revision},
            decode_kind(ref.kind),
            ref.parent_id ? std::optional<ContentBlockId>{ContentBlockId{*ref.parent_id}} : std::nullopt,
            ancestors.size(), static_cast<std::size_t>(physical.text_bytes), 0U});
        ancestors.push_back(index);
    }
}
void check_db_flags(const std::string& name, unsigned expected, const mdbxc::Transaction& txn) {
    MDBX_dbi dbi{};
    const auto opened = mdbx_dbi_open(txn.handle(), name.c_str(), MDBX_DB_ACCEDE, &dbi);
    if(opened == MDBX_NOTFOUND) return;
    if(opened != MDBX_SUCCESS) throw std::runtime_error("cannot inspect flat DBI");
    unsigned flags = 0;
    if(mdbx_dbi_flags(txn.handle(), dbi, &flags) != MDBX_SUCCESS || flags != expected)
        throw std::runtime_error("incompatible flat DBI comparator flags");
}
std::shared_ptr<MdbxStorageContext> validate_context(const MdbxCanonicalFlatStorageOptions& options) {
    if(options.max_records == 0U) throw std::invalid_argument("max_records must be positive");
    auto context = options.context ? options.context : MdbxStorageContext::open(
        options.path, options.relative_to_exe, options.max_dbs);
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    for(const auto* suffix : {"canonical_flat_meta_v1", "canonical_flat_heads_v1",
                             "canonical_flat_structures_v1", "canonical_flat_blocks_v1"}) {
        MDBX_dbi dbi{};
        const auto name = table_name(options.table_prefix, suffix);
        const auto rc = mdbx_dbi_open(txn.handle(), name.c_str(), MDBX_DB_ACCEDE, &dbi);
        if(rc != MDBX_NOTFOUND) {
            if(rc != MDBX_SUCCESS) throw std::runtime_error("cannot inspect old flat DBI");
            throw std::runtime_error("flat v1 requires explicit export/rebuild into a fresh namespace");
        }
    }
    for(const auto* suffix : {"canonical_flat_meta_v2", "canonical_flat_structures_v2", "canonical_flat_blocks_v2"})
        check_db_flags(table_name(options.table_prefix, suffix), 0U, txn);
    check_db_flags(table_name(options.table_prefix, "canonical_flat_heads_v2"), MDBX_INTEGERKEY, txn);
    txn.commit();
    return context;
}
} // namespace

class MdbxCanonicalFlatStorage::Impl final {
  public:
    explicit Impl(MdbxCanonicalFlatStorageOptions options)
        : max_records(options.max_records), metrics(options.metrics),
          before_head(std::move(options.before_head_publication)), context(validate_context(options)),
          identity([&] {
              MdbxStorageIdentityMappingOptions value;
              value.context = context;
              value.table_prefix = options.table_prefix;
              value.max_records = options.max_records;
              return value;
          }()),
          marker(context->connection(), table_name(options.table_prefix, "canonical_flat_meta_v2")),
          heads(context->connection(), table_name(options.table_prefix, "canonical_flat_heads_v2")),
          structures(context->connection(), table_name(options.table_prefix, "canonical_flat_structures_v2")),
          blocks(context->connection(), table_name(options.table_prefix, "canonical_flat_blocks_v2")) {
        limits.max_blocks = std::min(limits.max_blocks, max_records);
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto version = find(marker, std::string{kSchemaKey}, txn);
        if(version) {
            if(*version != kSchemaVersion) throw std::runtime_error("unsupported canonical flat schema");
        } else {
            if(!marker.empty(txn) || !heads.empty(txn) || !structures.empty(txn) || !blocks.empty(txn))
                throw std::runtime_error("canonical flat storage rejects markerless state");
            put(marker, std::string{kSchemaKey}, std::string{kSchemaVersion}, txn);
        }
        txn.commit();
    }
    template<class Key> std::optional<std::string> find(mdbxc::KeyValueTable<Key, std::string>& table,
        const Key& key, const mdbxc::Transaction& txn) const {
        if(metrics) ++metrics->value_reads;
        auto value = table.find(key, txn);
        if(metrics && value) metrics->value_bytes_read += value->size();
        return value;
    }
    template<class Key> void put(mdbxc::KeyValueTable<Key, std::string>& table,
        const Key& key, const std::string& value, mdbxc::Transaction& txn) const {
        table.insert_or_assign(key, value, txn);
        if(metrics) { ++metrics->value_writes; metrics->value_bytes_written += value.size(); }
    }
    void put_immutable(StringTable& table, const std::string& key, const std::string& value,
                       mdbxc::Transaction& txn, bool block) const {
        const auto existing = find(table, key, txn);
        if(existing) {
            if(*existing != value) throw std::runtime_error("immutable flat record collision");
            return;
        }
        if(!table.insert(key, value, txn)) throw std::runtime_error("immutable flat insert conflict");
        if(metrics) {
            ++metrics->value_writes;
            metrics->value_bytes_written += value.size();
            if(block) ++metrics->block_versions_written;
        }
    }
    template<class Function> auto dictionary(Function&& function) const -> decltype(function()) {
        if(!metrics) return function();
        ++metrics->dictionary_lookups;
        const auto start = Clock::now();
        auto value = function();
        metrics->dictionary_ns += elapsed_ns(start);
        return value;
    }
    std::optional<DocumentStorageId> document_id(const DocumentId& id, const mdbxc::Transaction& txn) const {
        return dictionary([&] { return identity.find_document_in_transaction(id, txn); });
    }
    std::optional<std::uint64_t> current_revision(DocumentStorageId id, const mdbxc::Transaction& txn) const {
        const auto value = find(heads, id.value(), txn);
        if(!value) return std::nullopt;
        if(value->size() != 8U) throw std::runtime_error("invalid flat head revision");
        EnvelopeReader reader(*value);
        return reader.u64();
    }
    std::optional<Structure> load_structure(const DocumentId& id, DocumentStorageId storage_id,
        std::uint64_t revision, const mdbxc::Transaction& txn) const {
        const auto key = canonical_flat_composite_key(storage_id.value(), revision);
        const auto payload = find(structures, key, txn);
        if(!payload) return std::nullopt;
        const auto start = Clock::now();
        auto reader = open_envelope(*payload, 'S', key, id, limits);
        Structure result;
        result.logical = CanonicalFlatStorageCodec::decode_structure(reader.string(limits.max_payload_bytes), limits);
        if(result.logical.document_id != id.value() || result.logical.document_revision != revision ||
           result.logical.body_revision == 0U) throw std::runtime_error("flat structure revision binding mismatch");
        const auto count = reader.u64();
        if(count != result.logical.blocks.size()) throw std::runtime_error("flat reference count mismatch");
        result.physical.reserve(static_cast<std::size_t>(count));
        for(std::uint64_t index = 0; index < count; ++index) {
            PhysicalRef ref{reader.u64(), reader.u64(), reader.digest()};
            if(ref.text_bytes > limits.max_string_bytes) throw std::runtime_error("flat text size exceeds limit");
            result.physical.push_back(ref);
        }
        const auto ledger_count = reader.u64();
        if(ledger_count > max_records) throw std::runtime_error("flat historical ledger exceeds limit");
        for(std::uint64_t index = 0; index < ledger_count; ++index) {
            const ContentBlockId block{reader.string(limits.max_string_bytes)};
            if(block.empty() || !result.ledger.insert(block).second)
                throw std::runtime_error("invalid flat historical ledger identity");
        }
        if(!reader.ended()) throw std::runtime_error("flat structure has trailing bytes");
        build_outline(result);
        if(metrics) metrics->decode_ns += elapsed_ns(start);
        return result;
    }
    ContentBlock load_block(const DocumentId& id, const Structure& structure, std::size_t index,
                            const mdbxc::Transaction& txn) const {
        const auto& ref = structure.logical.blocks[index];
        const auto& physical = structure.physical[index];
        const auto mapped = dictionary([&] { return identity.find_block_in_transaction(id, ContentBlockId{ref.id}, txn); });
        if(!mapped || mapped->value() != physical.storage_id) throw std::runtime_error("flat block mapping mismatch");
        const auto key = canonical_flat_composite_key(physical.storage_id, ref.revision);
        const auto payload = find(blocks, key, txn);
        if(!payload) throw std::runtime_error("canonical flat block row missing");
        const auto start = Clock::now();
        if(sha256(*payload) != physical.payload_digest) throw std::runtime_error("flat structure/payload digest mismatch");
        auto reader = open_envelope(*payload, 'B', key, id, limits);
        const auto decoded = CanonicalFlatStorageCodec::decode_block(reader.string(limits.max_payload_bytes), limits);
        if(!reader.ended() || decoded.id != ref.id || decoded.revision != ref.revision ||
           decoded.kind != ref.kind || decoded.parent_id != ref.parent_id || decoded.text.size() != physical.text_bytes)
            throw std::runtime_error("flat structure/block binding mismatch");
        if(metrics) metrics->decode_ns += elapsed_ns(start);
        return {ContentBlockId{decoded.id}, decoded.revision, decode_kind(decoded.kind),
            decoded.parent_id ? std::optional<ContentBlockId>{ContentBlockId{*decoded.parent_id}} : std::nullopt,
            decoded.text};
    }
    CanonicalDocumentRevision load_revision(const DocumentId& id, const Structure& structure,
                                            const mdbxc::Transaction& txn) const {
        CanonicalDocumentRevision result{id, structure.logical.document_revision, {}, {}};
        for(const auto& pair : structure.logical.metadata) result.metadata.set(pair.first, pair.second);
        result.blocks.reserve(structure.logical.blocks.size());
        for(std::size_t index = 0; index < structure.logical.blocks.size(); ++index)
            result.blocks.push_back(load_block(id, structure, index, txn));
        return result;
    }
    void write_revision(const CanonicalDocumentRevision& revision, DocumentStorageId storage_id,
        const Structure* previous, const CanonicalDocumentRevision* old_revision, mdbxc::Transaction& txn) {
        if(revision.blocks.size() > max_records) throw std::length_error("flat block limit exceeded");
        Structure next;
        next.logical.document_id = revision.document_id.value();
        next.logical.document_revision = revision.revision;
        next.logical.body_revision = previous ? previous->logical.body_revision : 1U;
        next.ledger = previous ? previous->ledger : std::set<ContentBlockId>{};
        for(const auto& pair : revision.metadata.values()) next.logical.metadata.push_back(pair);
        std::map<ContentBlockId, std::size_t> old_indices;
        if(old_revision) for(std::size_t i = 0; i < old_revision->blocks.size(); ++i)
            old_indices.emplace(old_revision->blocks[i].id, i);
        bool body_changed = !old_revision || old_revision->blocks.size() != revision.blocks.size();
        for(std::size_t index = 0; index < revision.blocks.size(); ++index) {
            const auto& block = revision.blocks[index];
            next.logical.blocks.push_back({block.id.value(), block.revision, encode_kind(block.kind),
                block.parent_id ? std::optional<std::string>{block.parent_id->value()} : std::nullopt});
            next.ledger.insert(block.id);
            if(old_revision && (index >= old_revision->blocks.size() || !same_payload(block, old_revision->blocks[index])))
                body_changed = true;
        }
        if(next.ledger.size() > max_records) throw std::length_error("flat historical ledger limit exceeded");
        if(previous && body_changed) {
            if(next.logical.body_revision == std::numeric_limits<std::uint64_t>::max())
                throw std::overflow_error("flat body revision exhausted");
            ++next.logical.body_revision;
        }
        const auto structure_start = Clock::now();
        const auto encoded_structure = CanonicalFlatStorageCodec::encode_structure(next.logical, limits);
        auto structure_size = envelope_size(revision.document_id, encoded_structure.size(), limits);
        add_envelope_size(structure_size, 8U, limits);
        // Repeated checked additions avoid overflow in count * physical-reference bytes.
        for(std::size_t index = 0; index < revision.blocks.size(); ++index)
            add_envelope_size(structure_size, 48U, limits);
        add_envelope_size(structure_size, 8U, limits);
        for(const auto& id : next.ledger) {
            add_envelope_size(structure_size, 8U, limits);
            add_envelope_size(structure_size, id.value().size(), limits);
        }
        if(metrics) metrics->encode_ns += elapsed_ns(structure_start);
        // Admit the full structure before writing blocks or constructing its outer buffer.
        for(const auto& block : revision.blocks) {
            const auto prior = old_indices.find(block.id);
            if(prior != old_indices.end() && block.revision == old_revision->blocks[prior->second].revision) {
                const auto& old = old_revision->blocks[prior->second];
                if(!same_payload(block, old)) throw std::runtime_error("unchanged flat revision changed payload");
                next.physical.push_back(previous->physical[prior->second]);
            } else {
                const auto block_id = dictionary([&] {
                    return identity.ensure_block_in_transaction(revision.document_id, block.id, txn);
                });
                const auto key = canonical_flat_composite_key(block_id.value(), block.revision);
                const auto start = Clock::now();
                const auto encoded = CanonicalFlatStorageCodec::encode_block(to_flat(block), limits);
                const auto size = envelope_size(revision.document_id, encoded.size(), limits);
                auto payload = envelope_header('B', key, revision.document_id, size);
                append_string(payload, encoded);
                seal(payload, size);
                next.physical.push_back({block_id.value(), block.text.size(), sha256(payload)});
                if(metrics) metrics->encode_ns += elapsed_ns(start);
                put_immutable(blocks, key, payload, txn, true);
            }
        }
        const auto start = Clock::now();
        const auto key = canonical_flat_composite_key(storage_id.value(), revision.revision);
        auto payload = envelope_header('S', key, revision.document_id, structure_size);
        append_string(payload, encoded_structure);
        append_u64(payload, next.physical.size());
        for(const auto& ref : next.physical) {
            append_u64(payload, ref.storage_id); append_u64(payload, ref.text_bytes); append_digest(payload, ref.payload_digest);
        }
        append_u64(payload, next.ledger.size());
        for(const auto& id : next.ledger) append_string(payload, id.value());
        seal(payload, structure_size);
        if(metrics) metrics->encode_ns += elapsed_ns(start);
        put_immutable(structures, key, payload, txn, false);
        if(before_head) before_head();
        std::string head;
        append_u64(head, revision.revision);
        put(heads, storage_id.value(), head, txn);
    }
    const std::size_t max_records;
    CanonicalFlatStorageLimits limits{};
    MdbxCanonicalStorageMetrics* metrics;
    std::function<void()> before_head;
    std::shared_ptr<MdbxStorageContext> context;
    mutable std::mutex mutex;
    mutable MdbxStorageIdentityMapping identity;
    mutable StringTable marker;
    mutable mdbxc::KeyValueTable<std::uint64_t, std::string> heads;
    mutable StringTable structures;
    mutable StringTable blocks;
};

MdbxCanonicalFlatStorage::MdbxCanonicalFlatStorage(MdbxCanonicalFlatStorageOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}
MdbxCanonicalFlatStorage::~MdbxCanonicalFlatStorage() = default;

bool MdbxCanonicalFlatStorage::create_document(CanonicalDocumentRevision initial) {
    if(initial.document_id.empty() || initial.revision != 0U) return false;
    InMemoryCanonicalContentStore validator;
    if(!validator.create_document(initial)) return false;
    initial = *validator.read_current(initial.document_id);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    // Duplicate admission and all publication writes share the serialized writer snapshot.
    if(m_impl->document_id(initial.document_id, txn)) return false;
    const auto storage_id = m_impl->dictionary([&] {
        return m_impl->identity.ensure_document_in_transaction(initial.document_id, txn);
    });
    m_impl->write_revision(initial, storage_id, nullptr, nullptr, txn);
    txn.commit();
    return true;
}
std::optional<CanonicalDocumentRevision> MdbxCanonicalFlatStorage::read_current(const DocumentId& id) const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto storage_id = m_impl->document_id(id, txn);
    if(!storage_id) return std::nullopt;
    const auto revision = m_impl->current_revision(*storage_id, txn);
    if(!revision) throw std::runtime_error("canonical flat document head missing");
    const auto structure = m_impl->load_structure(id, *storage_id, *revision, txn);
    if(!structure) throw std::runtime_error("canonical flat head structure missing");
    return m_impl->load_revision(id, *structure, txn);
}
std::optional<CanonicalDocumentRevision> MdbxCanonicalFlatStorage::read_revision(
    const DocumentId& id, std::uint64_t revision) const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto storage_id = m_impl->document_id(id, txn);
    if(!storage_id) return std::nullopt;
    const auto structure = m_impl->load_structure(id, *storage_id, revision, txn);
    return structure ? std::optional<CanonicalDocumentRevision>{m_impl->load_revision(id, *structure, txn)} : std::nullopt;
}
std::optional<ContentBlock> MdbxCanonicalFlatStorage::read_block(
    const DocumentId& id, const ContentBlockId& block, std::optional<std::uint64_t> revision) const {
    const bool current = !revision;
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto storage_id = m_impl->document_id(id, txn);
    if(!storage_id) return std::nullopt;
    if(current) revision = m_impl->current_revision(*storage_id, txn);
    if(!revision) throw std::runtime_error("canonical flat document head missing");
    const auto structure = m_impl->load_structure(id, *storage_id, *revision, txn);
    if(!structure) {
        if(current) throw std::runtime_error("canonical flat head structure missing");
        return std::nullopt;
    }
    for(std::size_t index = 0; index < structure->logical.blocks.size(); ++index)
        if(structure->logical.blocks[index].id == block.value()) return m_impl->load_block(id, *structure, index, txn);
    return std::nullopt;
}
std::optional<std::string> MdbxCanonicalFlatStorage::materialize_markdown(
    const DocumentId& id, std::optional<std::uint64_t> revision) const {
    auto value = revision ? read_revision(id, *revision) : read_current(id);
    if(!value) return std::nullopt;
    std::set<ContentBlockId> ledger;
    for(const auto& block : value->blocks) ledger.insert(block.id);
    const auto selected = value->revision;
    InMemoryCanonicalContentStore renderer;
    if(!detail::CanonicalContentHistoryLoader::restore_current(renderer, std::move(*value), std::move(ledger)))
        throw std::runtime_error("canonical flat renderer rejected stored revision");
    return renderer.materialize_markdown(id, selected);
}
CanonicalEditResult MdbxCanonicalFlatStorage::commit(const CanonicalEditRequest& request) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto storage_id = m_impl->document_id(request.document_id, txn);
    if(!storage_id) {
        CanonicalEditResult result;
        result.status = CanonicalEditStatus::NotFound;
        result.message = "canonical flat document not found";
        return result;
    }
    const auto current = m_impl->current_revision(*storage_id, txn);
    if(!current) throw std::runtime_error("canonical flat document head missing");
    if(request.expected_revision != *current) {
        CanonicalEditResult result;
        result.status = CanonicalEditStatus::Conflict;
        result.message = "canonical flat expected revision conflict";
        return result;
    }
    const auto structure = m_impl->load_structure(request.document_id, *storage_id, *current, txn);
    if(!structure) throw std::runtime_error("canonical flat head structure missing");
    const auto previous = m_impl->load_revision(request.document_id, *structure, txn);
    InMemoryCanonicalContentStore editor;
    if(!detail::CanonicalContentHistoryLoader::restore_current(editor, previous, structure->ledger))
        throw std::runtime_error("canonical flat editor rejected history");
    auto result = editor.commit(request);
    if(result.status == CanonicalEditStatus::Ok && result.revision) {
        m_impl->write_revision(*result.revision, *storage_id, &*structure, &previous, txn);
        txn.commit();
    }
    return result;
}
std::optional<CanonicalOutline> MdbxCanonicalFlatStorage::read_outline(
    const DocumentId& id, std::uint64_t revision, std::size_t max_entries) const {
    if(max_entries == 0U) return std::nullopt;
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto storage_id = m_impl->document_id(id, txn);
    if(!storage_id) return std::nullopt;
    const auto structure = m_impl->load_structure(id, *storage_id, revision, txn);
    if(!structure || structure->outline.entries.size() > max_entries) return std::nullopt;
    return structure->outline;
}
CanonicalChildrenResult MdbxCanonicalFlatStorage::read_children(
    const DocumentId& id, std::uint64_t revision, std::optional<ContentBlockId> parent, std::size_t max_children) const {
    CanonicalChildrenResult result;
    result.document_id = id; result.document_revision = revision; result.parent_id = parent;
    try {
        auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto storage_id = m_impl->document_id(id, txn);
        const auto structure = storage_id ? m_impl->load_structure(id, *storage_id, revision, txn) : std::nullopt;
        if(!structure) { result.status = CanonicalNavigationStatus::NotFound; return result; }
        if(max_children == 0U) { result.message = "max_children must be non-zero"; return result; }
        if(parent && std::none_of(structure->outline.entries.begin(), structure->outline.entries.end(),
            [&](const CanonicalOutlineEntry& entry) { return entry.locator.block_id == *parent; })) {
            result.status = CanonicalNavigationStatus::NotFound; return result;
        }
        result.status = CanonicalNavigationStatus::Ok;
        for(const auto& entry : structure->outline.entries) {
            if(entry.parent_id != parent) continue;
            if(result.entries.size() == max_children) {
                result.status = CanonicalNavigationStatus::BudgetExceeded;
                result.truncated = true; result.message = "children budget exhausted"; break;
            }
            result.entries.push_back(entry);
        }
    } catch(const std::exception& error) {
        result.status = CanonicalNavigationStatus::InvalidDocument;
        result.entries.clear(); result.truncated = false; result.message = error.what();
    }
    return result;
}
CanonicalSubtreeResult MdbxCanonicalFlatStorage::read_subtree(
    const DocumentId& id, std::uint64_t revision, const CanonicalSubtreeRequest& request) const {
    CanonicalSubtreeResult result;
    result.document_id = id; result.document_revision = revision;
    try {
        auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto storage_id = m_impl->document_id(id, txn);
        const auto structure = storage_id ? m_impl->load_structure(id, *storage_id, revision, txn) : std::nullopt;
        if(!structure) { result.status = CanonicalNavigationStatus::NotFound; return result; }
        if(request.max_blocks == 0U) { result.message = "max_blocks must be non-zero"; return result; }
        const auto& entries = structure->outline.entries;
        std::size_t start = 0U;
        if(request.root_id) {
            while(start < entries.size() && entries[start].locator.block_id != *request.root_id) ++start;
            if(start == entries.size()) { result.status = CanonicalNavigationStatus::NotFound; return result; }
        }
        std::size_t bytes = 0U;
        result.status = CanonicalNavigationStatus::Ok;
        for(std::size_t index = start; index < entries.size(); ++index) {
            const auto& entry = entries[index];
            if(request.root_id && index > start && entry.depth <= entries[start].depth) break;
            if(result.blocks.size() == request.max_blocks || (request.include_text &&
               (entry.text_bytes > request.max_text_bytes || bytes > request.max_text_bytes - entry.text_bytes))) {
                result.status = CanonicalNavigationStatus::BudgetExceeded;
                result.truncated = true; result.message = "subtree budget exhausted"; break;
            }
            if(request.include_text) {
                result.blocks.push_back(m_impl->load_block(id, *structure, index, txn)); bytes += entry.text_bytes;
            } else result.blocks.push_back({entry.locator.block_id, entry.locator.block_revision,
                entry.kind, entry.parent_id, {}});
        }
    } catch(const std::exception& error) {
        result.status = CanonicalNavigationStatus::InvalidDocument;
        result.blocks.clear(); result.truncated = false; result.message = error.what();
    }
    return result;
}

} // namespace agent_memory
#endif
