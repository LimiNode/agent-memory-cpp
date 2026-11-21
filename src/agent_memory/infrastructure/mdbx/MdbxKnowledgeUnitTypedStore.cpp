#include "MdbxKnowledgeUnitTypedStore.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxKnowledgeUnitStore.hpp"

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view kSchemaKey = "@schema_version";
constexpr std::string_view kNextIdKey = "@next_id";
constexpr std::string_view kSchemaVersion = "knowledge_unit_typed_numeric.v1";
constexpr std::string_view kUsedMarker = "used";

std::string sanitize_table_part(std::string value) {
    if(value.empty()) value = "agent_memory";
    for(char& character : value) {
        if(!std::isalnum(static_cast<unsigned char>(character))) character = '_';
    }
    return value;
}

std::string table_name(const std::string& prefix, std::string_view suffix) {
    auto result = sanitize_table_part(prefix);
    result.push_back('_');
    result.append(suffix.data(), suffix.size());
    return result;
}

template<typename Table>
bool has_any_numeric_row(Table& table, const mdbxc::Transaction& transaction) {
    bool present = false;
    table.for_each_range(
        std::uint64_t{0},
        std::numeric_limits<std::uint64_t>::max(),
        [&present](const std::uint64_t, const auto&) {
            present = true;
            return false;
        },
        transaction
    );
    return present;
}

template<typename Table>
bool has_any_string_row(Table& table, const mdbxc::Transaction& transaction) {
    bool present = false;
    table.for_each_range(
        std::string{},
        std::string(1, static_cast<char>(0xff)),
        [&present](const std::string&, const auto&) {
            present = true;
            return false;
        },
        transaction
    );
    return present;
}

void append_size(std::string& output, std::size_t value) {
    output += std::to_string(value);
    output.push_back(':');
}

void append_string(std::string& output, std::string_view value) {
    append_size(output, value.size());
    output.append(value.data(), value.size());
}

void append_u16_be(std::string& output, std::uint16_t value) {
    output.push_back(static_cast<char>((value >> 8U) & 0xffU));
    output.push_back(static_cast<char>(value & 0xffU));
}

void append_u64_be(std::string& output, std::uint64_t value) {
    for(int shift = 56; shift >= 0; shift -= 8) {
        output.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
}

std::string content_key(const KnowledgeUnitKey& key) {
    std::string result;
    append_string(result, to_string(key.kind));
    append_string(result, key.scope_id.value());
    result.append(
        reinterpret_cast<const char*>(key.content_hash.bytes.data()),
        key.content_hash.bytes.size()
    );
    return result;
}

std::string index_prefix(const ScopeId& scope, KnowledgeUnitKind kind) {
    std::string result;
    append_string(result, scope.value());
    append_u16_be(result, static_cast<std::uint16_t>(kind));
    return result;
}

std::string index_row_key(
    const ScopeId& scope,
    KnowledgeUnitKind kind,
    KnowledgeUnitId id
) {
    auto result = index_prefix(scope, kind);
    append_u64_be(result, id.value());
    return result;
}

KnowledgeUnitId parse_decimal_id(std::string_view text) {
    if(text.empty()) throw std::runtime_error("Empty typed knowledge-unit id");
    std::uint64_t value = 0;
    for(const char character : text) {
        if(character < '0' || character > '9') {
            throw std::runtime_error("Invalid typed knowledge-unit id");
        }
        const auto digit = static_cast<std::uint64_t>(character - '0');
        if(value > (std::numeric_limits<std::uint64_t>::max() - digit) / 10U) {
            throw std::runtime_error("Typed knowledge-unit id overflow");
        }
        value = value * 10U + digit;
    }
    if(value == 0) throw std::runtime_error("Zero typed knowledge-unit id");
    return KnowledgeUnitId{value};
}

std::uint64_t decode_index_id(std::string_view key) {
    if(key.size() < sizeof(std::uint64_t)) {
        throw std::runtime_error("Malformed typed knowledge-unit index key");
    }
    const auto offset = key.size() - sizeof(std::uint64_t);
    std::uint64_t value = 0;
    for(std::size_t index = offset; index < key.size(); ++index) {
        value = (value << 8U)
            | static_cast<std::uint64_t>(static_cast<unsigned char>(key[index]));
    }
    return value;
}

bool is_chunk_identity_valid(const KnowledgeUnitEnvelope& envelope) {
    if(envelope.kind != KnowledgeUnitKind::Chunk) return true;
    return envelope.content_hash_recipe_version == 2
        && envelope.sources.size() == 1
        && envelope.sources.front().reference_mode
            == SourceReferenceMode::RevisionBoundQuote
        && envelope.sources.front().resource_revision.has_value()
        && envelope.content_hash == compute_chunk_content_hash(
            envelope.scope_id,
            envelope.primary_text,
            envelope.sources,
            envelope.content_hash_recipe_version
        );
}

void validate_envelope(const KnowledgeUnitEnvelope& envelope) {
    if(!is_valid_knowledge_unit_envelope(envelope) || !is_chunk_identity_valid(envelope)) {
        throw std::invalid_argument("Invalid typed knowledge-unit envelope");
    }
}

ContentHash validate_request(const KnowledgeUnitCreateRequest& request) {
    if(request.scope_id.empty() || request.primary_text.empty()
       || !is_valid_knowledge_unit_kind(request.kind)
       || !std::isfinite(request.priority_weight)
       || request.priority_weight < 0.0 || request.priority_weight > 1.0) {
        throw std::invalid_argument("Invalid typed knowledge-unit create request");
    }
    if(request.kind == KnowledgeUnitKind::Chunk) {
        if(request.content_hash_recipe_version != 2 || request.sources.size() != 1
           || request.sources.front().reference_mode != SourceReferenceMode::RevisionBoundQuote
           || !request.sources.front().resource_revision.has_value()) {
            throw std::invalid_argument("Typed Chunk requires recipe-v2 revision-bound identity");
        }
        const auto expected = compute_chunk_content_hash(
            request.scope_id,
            request.primary_text,
            request.sources,
            request.content_hash_recipe_version
        );
        if(request.content_hash && *request.content_hash != expected) {
            throw std::invalid_argument("Typed Chunk content hash assertion mismatch");
        }
        return expected;
    }
    if(!request.content_hash || request.content_hash_recipe_version == 0) {
        throw std::invalid_argument("Non-Chunk typed requests require a content hash");
    }
    KnowledgeUnitEnvelope probe;
    probe.id = KnowledgeUnitId{1};
    probe.kind = request.kind;
    probe.scope_id = request.scope_id;
    probe.primary_text = request.primary_text;
    probe.display_text = request.display_text;
    probe.sources = request.sources;
    probe.content_hash = *request.content_hash;
    probe.content_hash_recipe_version = request.content_hash_recipe_version;
    probe.priority_weight = request.priority_weight;
    validate_envelope(probe);
    return *request.content_hash;
}

} // namespace

class MdbxKnowledgeUnitTypedStore::Impl final {
  public:
    explicit Impl(MdbxKnowledgeUnitTypedStoreOptions options)
        : prefix(sanitize_table_part(std::move(options.table_prefix)))
        , context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(
                                        std::move(options.path),
                                        options.relative_to_exe,
                                        options.max_dbs))
        , records(context->connection(), table_name(prefix, "ku_typed_v1"))
        , used_ids(context->connection(), table_name(prefix, "ku_typed_v1_used"))
        , metadata(context->connection(), table_name(prefix, "ku_typed_v1_meta"))
        , content_keys(context->connection(), table_name(prefix, "ku_typed_v1_content"))
        , kind_index(context->connection(), table_name(prefix, "ku_typed_v1_kind")) {
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto marker = metadata.find(std::string{kSchemaKey}, txn);
        if(marker && *marker != kSchemaVersion) {
            txn.rollback();
            throw std::runtime_error("Unsupported typed knowledge-unit schema");
        }
        if(!marker) {
            if(has_any_numeric_row(records, txn) || has_any_numeric_row(used_ids, txn)
               || has_any_string_row(metadata, txn) || has_any_string_row(content_keys, txn)
               || has_any_string_row(kind_index, txn)) {
                txn.rollback();
                throw std::runtime_error("Typed knowledge-unit store rejects markerless non-empty state");
            }
            metadata.insert_or_assign(std::string{kSchemaKey}, std::string{kSchemaVersion}, txn);
        }
        txn.commit();
    }

    std::string prefix;
    std::shared_ptr<MdbxStorageContext> context;
    mutable std::mutex mutex;
    mdbxc::KeyValueTable<std::uint64_t, std::string> records;
    mdbxc::KeyValueTable<std::uint64_t, std::string> used_ids;
    mdbxc::KeyValueTable<std::string, std::string> metadata;
    mdbxc::KeyValueTable<std::string, std::uint64_t> content_keys;
    mdbxc::KeyValueTable<std::string, std::uint64_t> kind_index;

    KnowledgeUnitId allocate_id(const mdbxc::Transaction& txn) {
        std::uint64_t next = 1;
        if(const auto payload = metadata.find(std::string{kNextIdKey}, txn)) {
            next = parse_decimal_id(*payload).value();
        }
        if(next == std::numeric_limits<std::uint64_t>::max()) {
            throw std::runtime_error("Typed knowledge-unit id space exhausted");
        }
        metadata.insert_or_assign(std::string{kNextIdKey}, std::to_string(next + 1U), txn);
        return KnowledgeUnitId{next};
    }

    void advance_id(KnowledgeUnitId id, const mdbxc::Transaction& txn) {
        std::uint64_t next = 1;
        if(const auto payload = metadata.find(std::string{kNextIdKey}, txn)) {
            next = parse_decimal_id(*payload).value();
        }
        if(id.value() >= next) {
            if(id.value() == std::numeric_limits<std::uint64_t>::max()) {
                throw std::runtime_error("Typed knowledge-unit id space exhausted");
            }
            metadata.insert_or_assign(
                std::string{kNextIdKey}, std::to_string(id.value() + 1U), txn);
        }
    }

    void ensure_index(
        const KnowledgeUnitEnvelope& envelope,
        KnowledgeUnitId id,
        const mdbxc::Transaction& txn
    ) {
        const auto key = index_row_key(envelope.scope_id, envelope.kind, id);
        const auto existing = kind_index.find(key, txn);
        if(existing && *existing != id.value()) {
            throw std::runtime_error("Typed knowledge-unit by-kind index is inconsistent");
        }
        kind_index.insert_or_assign(key, id.value(), txn);
    }

    void validate_indexes(
        const KnowledgeUnitEnvelope& envelope,
        KnowledgeUnitId id,
        const mdbxc::Transaction& txn
    ) const {
        const auto key = content_key({envelope.kind, envelope.scope_id, envelope.content_hash});
        const auto indexed = content_keys.find(key, txn);
        if(!indexed || *indexed != id.value()) {
            throw std::runtime_error("Typed knowledge-unit content index is inconsistent");
        }
        const auto indexed_kind = kind_index.find(
            index_row_key(envelope.scope_id, envelope.kind, id), txn);
        if(!indexed_kind || *indexed_kind != id.value()) {
            throw std::runtime_error("Typed knowledge-unit by-kind index is inconsistent");
        }
    }

    void insert_new(
        const KnowledgeUnitEnvelope& envelope,
        const mdbxc::Transaction& txn
    ) {
        const auto id = envelope.id.value();
        if(used_ids.find(id, txn)) {
            throw std::invalid_argument("Typed knowledge-unit id was already used");
        }
        const auto key = content_key({envelope.kind, envelope.scope_id, envelope.content_hash});
        if(const auto indexed = content_keys.find(key, txn)) {
            if(*indexed != id) {
                throw std::invalid_argument("Typed content key already belongs to another id");
            }
            throw std::runtime_error("Typed content index points to missing envelope");
        }
        records.insert_or_assign(id, MdbxKnowledgeUnitStore::encode_envelope_payload(envelope), txn);
        used_ids.insert_or_assign(id, std::string{kUsedMarker}, txn);
        content_keys.insert_or_assign(key, id, txn);
        ensure_index(envelope, envelope.id, txn);
        advance_id(envelope.id, txn);
    }

    KnowledgeUnitEnvelope read_checked(
        KnowledgeUnitId id,
        const mdbxc::Transaction& txn
    ) const {
        const auto payload = records.find(id.value(), txn);
        if(!payload) throw std::runtime_error("Typed knowledge-unit envelope is missing");
        const auto envelope = MdbxKnowledgeUnitStore::decode_envelope_payload(*payload);
        if(envelope.id != id) {
            throw std::runtime_error("Typed knowledge-unit key does not match envelope identity");
        }
        const auto marker = used_ids.find(id.value(), txn);
        if(!marker || *marker != kUsedMarker) {
            throw std::runtime_error("Typed knowledge-unit no-reuse ledger is inconsistent");
        }
        validate_indexes(envelope, id, txn);
        return envelope;
    }
};

MdbxKnowledgeUnitTypedStore::MdbxKnowledgeUnitTypedStore(
    MdbxKnowledgeUnitTypedStoreOptions options
)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxKnowledgeUnitTypedStore::~MdbxKnowledgeUnitTypedStore() = default;

void MdbxKnowledgeUnitTypedStore::upsert(KnowledgeUnitEnvelope envelope) {
    validate_envelope(envelope);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto existing = m_impl->records.find(envelope.id.value(), txn);
    if(existing) {
        const auto current = m_impl->read_checked(envelope.id, txn);
        if(MdbxKnowledgeUnitStore::encode_envelope_payload(current)
           != MdbxKnowledgeUnitStore::encode_envelope_payload(envelope)) {
            throw std::invalid_argument("Typed upsert only accepts an exact idempotent replay");
        }
        txn.commit();
        return;
    }
    m_impl->insert_new(envelope, txn);
    txn.commit();
}

bool MdbxKnowledgeUnitTypedStore::erase(const KnowledgeUnitId& id) {
    if(id.empty()) return false;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto payload = m_impl->records.find(id.value(), txn);
    if(!payload) {
        txn.commit();
        return false;
    }
    const auto envelope = m_impl->read_checked(id, txn);
    m_impl->records.erase(id.value(), txn);
    m_impl->content_keys.erase(
        content_key({envelope.kind, envelope.scope_id, envelope.content_hash}), txn);
    m_impl->kind_index.erase(index_row_key(envelope.scope_id, envelope.kind, id), txn);
    txn.commit();
    return true;
}

std::vector<KnowledgeUnitId> MdbxKnowledgeUnitTypedStore::scan_by_scope_and_kind(
    const ScopeId& scope_id,
    KnowledgeUnitKind kind
) const {
    if(scope_id.empty() || !is_valid_knowledge_unit_kind(kind)) return {};
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto prefix = index_prefix(scope_id, kind);
    auto first = prefix;
    append_u64_be(first, 0);
    auto last = prefix;
    append_u64_be(last, std::numeric_limits<std::uint64_t>::max());
    std::vector<KnowledgeUnitId> result;
    m_impl->kind_index.for_each_range(
        first,
        last,
        [&result, &prefix, &txn, this, &scope_id, kind](const std::string& key, const std::uint64_t value) {
            if(key.size() != prefix.size() + sizeof(std::uint64_t)
               || key.compare(0, prefix.size(), prefix) != 0
               || decode_index_id(key) != value
               || value == 0) {
                throw std::runtime_error("Malformed typed knowledge-unit by-kind index");
            }
            const auto envelope = m_impl->read_checked(KnowledgeUnitId{value}, txn);
            if(envelope.scope_id != scope_id || envelope.kind != kind) {
                throw std::runtime_error("Typed knowledge-unit index identity mismatch");
            }
            result.emplace_back(value);
            return true;
        },
        txn
    );
    txn.commit();
    return result;
}

std::vector<KnowledgeUnitId> MdbxKnowledgeUnitTypedStore::scan_by_scope(
    const ScopeId& scope_id
) const {
    std::vector<KnowledgeUnitId> result;
    for(std::uint16_t raw = 0; raw <= 16; ++raw) {
        const auto ids = scan_by_scope_and_kind(scope_id, static_cast<KnowledgeUnitKind>(raw));
        result.insert(result.end(), ids.begin(), ids.end());
    }
    return result;
}

KnowledgeUnitEnvelope MdbxKnowledgeUnitTypedStore::create_or_get_unit(
    KnowledgeUnitCreateRequest request
) {
    const auto hash = validate_request(request);
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const KnowledgeUnitKey key{request.kind, request.scope_id, hash};
    const auto key_payload = content_key(key);
    if(const auto indexed = m_impl->content_keys.find(key_payload, txn)) {
        const auto existing = m_impl->read_checked(KnowledgeUnitId{*indexed}, txn);
        if(existing.kind != key.kind || existing.scope_id != key.scope_id
           || existing.content_hash != key.content_hash) {
            throw std::runtime_error("Typed content index identity mismatch");
        }
        txn.commit();
        return existing;
    }
    KnowledgeUnitEnvelope envelope;
    envelope.id = m_impl->allocate_id(txn);
    envelope.kind = request.kind;
    envelope.scope_id = std::move(request.scope_id);
    envelope.primary_text = std::move(request.primary_text);
    envelope.display_text = std::move(request.display_text);
    envelope.sources = std::move(request.sources);
    envelope.created_at_ms = request.created_at_ms;
    envelope.updated_at_ms = request.created_at_ms;
    envelope.observed_at_ms = request.observed_at_ms;
    envelope.revision = 1;
    envelope.content_hash = hash;
    envelope.content_hash_recipe_version = request.content_hash_recipe_version;
    envelope.priority_weight = request.priority_weight;
    validate_envelope(envelope);
    m_impl->insert_new(envelope, txn);
    txn.commit();
    return envelope;
}

std::optional<KnowledgeUnitEnvelope> MdbxKnowledgeUnitTypedStore::find(
    const KnowledgeUnitId& id
) const {
    if(id.empty()) return std::nullopt;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    if(!m_impl->records.find(id.value(), txn)) {
        txn.commit();
        return std::nullopt;
    }
    const auto envelope = m_impl->read_checked(id, txn);
    txn.commit();
    return envelope;
}

std::optional<KnowledgeUnitId> MdbxKnowledgeUnitTypedStore::find_by_content_key(
    const KnowledgeUnitKey& key
) const {
    if(key.scope_id.empty() || !is_valid_knowledge_unit_kind(key.kind)) return std::nullopt;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto indexed = m_impl->content_keys.find(content_key(key), txn);
    if(!indexed) {
        txn.commit();
        return std::nullopt;
    }
    const auto envelope = m_impl->read_checked(KnowledgeUnitId{*indexed}, txn);
    if(envelope.kind != key.kind || envelope.scope_id != key.scope_id
       || envelope.content_hash != key.content_hash) {
        throw std::runtime_error("Typed content index identity mismatch");
    }
    txn.commit();
    return envelope.id;
}

KnowledgeUnitEnvelope MdbxKnowledgeUnitTypedStore::update_mutable_fields(
    const KnowledgeUnitId& id,
    KnowledgeUnitMutablePatch patch
) {
    if(id.empty()) throw std::invalid_argument("Typed knowledge-unit id must not be empty");
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    auto envelope = m_impl->read_checked(id, txn);
    const auto previous = envelope;
    bool retrieval_visible_change = false;
    if(patch.display_text) {
        envelope.display_text = std::move(*patch.display_text);
        retrieval_visible_change |= envelope.display_text != previous.display_text;
    }
    if(patch.sources) {
        envelope.sources = std::move(*patch.sources);
        retrieval_visible_change |= MdbxKnowledgeUnitStore::encode_envelope_payload(envelope)
            != MdbxKnowledgeUnitStore::encode_envelope_payload(previous);
    }
    if(patch.lifecycle_state) {
        if(*patch.lifecycle_state != envelope.lifecycle_state
           && !is_valid_knowledge_unit_lifecycle_transition(
                envelope.lifecycle_state, *patch.lifecycle_state)) {
            throw std::invalid_argument("Invalid typed knowledge-unit lifecycle transition");
        }
        envelope.lifecycle_state = *patch.lifecycle_state;
        retrieval_visible_change |= envelope.lifecycle_state != previous.lifecycle_state;
    }
    if(patch.observed_at_ms) envelope.observed_at_ms = *patch.observed_at_ms;
    if(patch.priority_weight) envelope.priority_weight = *patch.priority_weight;
    if(envelope.kind == KnowledgeUnitKind::Chunk
       && !is_chunk_identity_valid(envelope)) {
        throw std::invalid_argument("Typed mutable source update changes immutable identity");
    }
    const auto previous_payload = MdbxKnowledgeUnitStore::encode_envelope_payload(previous);
    const auto payload = MdbxKnowledgeUnitStore::encode_envelope_payload(envelope);
    if(payload == previous_payload) {
        txn.commit();
        return envelope;
    }
    envelope.revision = retrieval_visible_change ? previous.revision + 1U : previous.revision;
    envelope.updated_at_ms = std::max(previous.updated_at_ms, envelope.observed_at_ms);
    validate_envelope(envelope);
    m_impl->records.insert_or_assign(id.value(), MdbxKnowledgeUnitStore::encode_envelope_payload(envelope), txn);
    txn.commit();
    return envelope;
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
