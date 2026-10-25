#include "MdbxProjectionStore.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cctype>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view kPayloadVersion = "agent_memory.projection.v1";

std::string table_name(const std::string& prefix) {
    std::string value = prefix.empty() ? "agent_memory" : prefix;
    for(char& character : value) {
        if(!std::isalnum(static_cast<unsigned char>(character)) && character != '_') {
            character = '_';
        }
    }
    return value + "_unit_projections";
}

void append_string(std::string& out, std::string_view value) {
    out += std::to_string(value.size());
    out.push_back(':');
    out.append(value.data(), value.size());
}

void append_u64(std::string& out, std::uint64_t value) {
    for(int shift = 56; shift >= 0; shift -= 8) {
        out.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
}

std::uint64_t read_u64(std::string_view payload, std::size_t& position) {
    if(position > payload.size() || payload.size() - position < 8) {
        throw std::runtime_error("projection payload ended before an integer");
    }
    std::uint64_t value = 0;
    for(int index = 0; index < 8; ++index) {
        value = (value << 8U) |
            static_cast<std::uint8_t>(payload[position++]);
    }
    return value;
}

std::string read_string(std::string_view payload, std::size_t& position) {
    if(position >= payload.size()) throw std::runtime_error("projection payload ended");
    std::size_t size = 0;
    bool digit = false;
    while(position < payload.size() && payload[position] != ':') {
        const auto c = static_cast<unsigned char>(payload[position++]);
        if(c < '0' || c > '9') throw std::runtime_error("projection payload size is invalid");
        digit = true;
        const auto value = static_cast<std::size_t>(c - '0');
        if(size > (std::numeric_limits<std::size_t>::max() - value) / 10U) {
            throw std::runtime_error("projection payload string size overflows");
        }
        size = size * 10U + value;
    }
    if(!digit || position >= payload.size()) throw std::runtime_error("projection payload size missing");
    ++position;
    if(size > payload.size() - position) throw std::runtime_error("projection payload string is truncated");
    std::string value{payload.data() + position, size};
    position += size;
    return value;
}

std::string projection_prefix(const ScopeId& scope, KnowledgeUnitId id) {
    std::string key;
    append_string(key, scope.value());
    append_u64(key, id.value());
    return key;
}

std::string projection_key(
    const SearchProjection& projection
) {
    auto key = projection_prefix(projection.scope_id, projection.unit_id);
    append_u64(key, static_cast<std::uint16_t>(projection.kind));
    append_u64(key, projection.version.projection_revision);
    return key;
}

ProjectionKind decode_projection_kind(std::uint64_t raw_kind) {
    if(raw_kind > std::numeric_limits<std::uint16_t>::max()) {
        throw std::runtime_error("projection kind is out of range");
    }
    const auto kind = static_cast<ProjectionKind>(
        static_cast<std::uint16_t>(raw_kind)
    );
    if(!is_valid_projection_kind(kind)) {
        throw std::runtime_error("unsupported projection kind");
    }
    return kind;
}

SearchProjection deserialize(std::string_view payload) {
    std::size_t position = 0;
    if(read_string(payload, position) != kPayloadVersion) {
        throw std::runtime_error("unsupported search projection payload version");
    }
    SearchProjection projection;
    projection.scope_id = ScopeId{read_string(payload, position)};
    projection.unit_id = KnowledgeUnitId{read_u64(payload, position)};
    projection.kind = decode_projection_kind(read_u64(payload, position));
    projection.version.unit_revision_at_build = read_u64(payload, position);
    projection.version.projection_revision = read_u64(payload, position);
    projection.version.derivation_generation = read_u64(payload, position);
    projection.version.derivation_fingerprint = read_string(payload, position);
    projection.title = read_string(payload, position);
    projection.heading = read_string(payload, position);
    projection.body = read_string(payload, position);
    projection.code = read_string(payload, position);
    const auto tag_count = read_u64(payload, position);
    if(tag_count > 1'000'000) throw std::runtime_error("projection tag list is too large");
    projection.tags.reserve(static_cast<std::size_t>(tag_count));
    for(std::uint64_t i = 0; i < tag_count; ++i) {
        projection.tags.push_back(read_string(payload, position));
    }
    const auto symbol_count = read_u64(payload, position);
    if(symbol_count > 1'000'000) throw std::runtime_error("projection symbol list is too large");
    projection.symbols.reserve(static_cast<std::size_t>(symbol_count));
    for(std::uint64_t i = 0; i < symbol_count; ++i) {
        projection.symbols.push_back(read_string(payload, position));
    }
    if(position != payload.size() || !is_valid_search_projection(projection)) {
        throw std::runtime_error("invalid persisted search projection");
    }
    return projection;
}

struct ProjectionKeyIdentity final {
    ScopeId scope_id;
    KnowledgeUnitId unit_id;
    ProjectionKind kind;
    std::uint64_t projection_revision = 0;
};

ProjectionKeyIdentity decode_key(std::string_view key) {
    std::size_t position = 0;
    ProjectionKeyIdentity identity{
        ScopeId{read_string(key, position)},
        KnowledgeUnitId{read_u64(key, position)},
        decode_projection_kind(read_u64(key, position)),
        read_u64(key, position)
    };
    if(position != key.size() || identity.scope_id.empty() || identity.unit_id.empty()
       || !is_valid_projection_kind(identity.kind) || identity.projection_revision == 0) {
        throw std::runtime_error("invalid persisted search projection key");
    }
    return identity;
}

std::string serialize(const SearchProjection& projection) {
    std::string payload;
    append_string(payload, kPayloadVersion);
    append_string(payload, projection.scope_id.value());
    append_u64(payload, projection.unit_id.value());
    append_u64(payload, static_cast<std::uint16_t>(projection.kind));
    append_u64(payload, projection.version.unit_revision_at_build);
    append_u64(payload, projection.version.projection_revision);
    append_u64(payload, projection.version.derivation_generation);
    append_string(payload, projection.version.derivation_fingerprint);
    append_string(payload, projection.title);
    append_string(payload, projection.heading);
    append_string(payload, projection.body);
    append_string(payload, projection.code);
    append_u64(payload, projection.tags.size());
    for(const auto& tag : projection.tags) append_string(payload, tag);
    append_u64(payload, projection.symbols.size());
    for(const auto& symbol : projection.symbols) append_string(payload, symbol);
    return payload;
}

} // namespace

class MdbxProjectionStore::Impl final {
public:
    explicit Impl(MdbxProjectionStoreOptions options)
        : context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(
                                        std::move(options.path),
                                        options.relative_to_exe,
                                        options.max_dbs
                                    )),
          projections(context->connection(), table_name(options.table_prefix)) {}

    std::shared_ptr<MdbxStorageContext> context;
    mutable mdbxc::KeyValueTable<std::string, std::string> projections;
};

MdbxProjectionStore::MdbxProjectionStore(MdbxProjectionStoreOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxProjectionStore::~MdbxProjectionStore() = default;

const MdbxStorageContext& MdbxProjectionStore::context() const noexcept {
    return *m_impl->context;
}

void MdbxProjectionStore::put(SearchProjection projection) {
    if(!is_valid_search_projection(projection)) {
        throw std::invalid_argument("invalid search projection");
    }
    const auto key = projection_key(projection);
    const auto payload = serialize(projection);
    auto txn = m_impl->context->connection()->transaction(
        mdbxc::TransactionMode::WRITABLE
    );
    const auto existing = m_impl->projections.find(key, txn);
    if(existing && *existing != payload) {
        throw std::invalid_argument("projection revision already has different content");
    }
    m_impl->projections.insert_or_assign(key, payload, txn);
    txn.commit();
}

void MdbxProjectionStore::put_in_transaction(
    SearchProjection projection,
    const mdbxc::Transaction& transaction
) {
    if(!is_valid_search_projection(projection)) {
        throw std::invalid_argument("invalid search projection");
    }
    const auto key = projection_key(projection);
    const auto payload = serialize(projection);
    const auto existing = m_impl->projections.find(key, transaction);
    if(existing && *existing != payload) {
        throw std::invalid_argument("projection revision already has different content");
    }
    m_impl->projections.insert_or_assign(key, payload, transaction);
}

std::vector<SearchProjection> MdbxProjectionStore::list(
    const ScopeId& scope_id,
    const KnowledgeUnitId& unit_id,
    std::optional<ProjectionKind> kind
) const {
    if(scope_id.empty() || unit_id.empty()) {
        throw std::invalid_argument("projection list identity is empty");
    }
    const auto prefix = projection_prefix(scope_id, unit_id);
    std::string lower = prefix;
    lower.push_back('\0');
    std::string upper = prefix;
    upper.push_back(static_cast<char>(0xff));
    auto txn = m_impl->context->connection()->transaction(
        mdbxc::TransactionMode::READ_ONLY
    );
    std::vector<SearchProjection> result;
    m_impl->projections.for_each_range(
        lower,
        upper,
        [&](const std::string& key, const std::string& payload) {
            const auto key_identity = decode_key(key);
            auto projection = deserialize(payload);
            if(projection.scope_id.value() != key_identity.scope_id.value()
               || projection.unit_id.value() != key_identity.unit_id.value()
               || projection.kind != key_identity.kind
               || projection.version.projection_revision != key_identity.projection_revision
               || projection.scope_id.value() != scope_id.value()
               || projection.unit_id.value() != unit_id.value()
               || key != projection_key(projection)) {
                throw std::runtime_error("search projection key and payload identity mismatch");
            }
            if(!kind || projection.kind == *kind) result.push_back(std::move(projection));
            return true;
        },
        txn
    );
    txn.commit();
    return result;
}

std::vector<SearchProjection> MdbxProjectionStore::list_in_transaction(
    const ScopeId& scope_id,
    const KnowledgeUnitId& unit_id,
    const mdbxc::Transaction& txn,
    std::optional<ProjectionKind> kind
) const {
    if(scope_id.empty() || unit_id.empty()) {
        throw std::invalid_argument("projection list identity is empty");
    }
    const auto prefix = projection_prefix(scope_id, unit_id);
    std::string lower = prefix;
    lower.push_back('\0');
    std::string upper = prefix;
    upper.push_back(static_cast<char>(0xff));
    std::vector<SearchProjection> result;
    m_impl->projections.for_each_range(
        lower,
        upper,
        [&](const std::string& key, const std::string& payload) {
            const auto key_identity = decode_key(key);
            auto projection = deserialize(payload);
            if(projection.scope_id.value() != key_identity.scope_id.value()
               || projection.unit_id.value() != key_identity.unit_id.value()
               || projection.kind != key_identity.kind
               || projection.version.projection_revision != key_identity.projection_revision
               || projection.scope_id.value() != scope_id.value()
               || projection.unit_id.value() != unit_id.value()
               || key != projection_key(projection)) {
                throw std::runtime_error("search projection key and payload identity mismatch");
            }
            if(!kind || projection.kind == *kind) result.push_back(std::move(projection));
            return true;
        },
        txn
    );
    return result;
}

bool MdbxProjectionStore::delete_revision(
    const ScopeId& scope_id,
    const KnowledgeUnitId& unit_id,
    ProjectionKind kind,
    std::uint64_t projection_revision
) {
    if(scope_id.empty() || unit_id.empty() || !is_valid_projection_kind(kind)
       || projection_revision == 0) {
        throw std::invalid_argument("invalid projection deletion identity");
    }
    auto key = projection_prefix(scope_id, unit_id);
    append_u64(key, static_cast<std::uint16_t>(kind));
    append_u64(key, projection_revision);
    auto txn = m_impl->context->connection()->transaction(
        mdbxc::TransactionMode::WRITABLE
    );
    const bool removed = m_impl->projections.erase(key, txn);
    txn.commit();
    return removed;
}

} // namespace agent_memory

#endif
