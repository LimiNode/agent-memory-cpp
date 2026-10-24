#include "MdbxPersistentLexicalIndex.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cctype>
#include <cstring>
#include <limits>
#include <map>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

namespace agent_memory {
namespace {

constexpr std::string_view kPayloadVersion =
    "agent_memory.mdbx_flat_lexical.v1";
constexpr std::string_view kTableSuffix = "lexical_posting_segments";
constexpr char kKeySeparator = '\x1f';

void append_string(std::string& payload, std::string_view value) {
    payload += std::to_string(value.size());
    payload.push_back(':');
    payload.append(value.data(), value.size());
}

void append_u64(std::string& payload, std::uint64_t value) {
    append_string(payload, std::to_string(value));
}

void append_u32(std::string& payload, std::uint32_t value) {
    append_u64(payload, value);
}

class PayloadReader final {
public:
    explicit PayloadReader(std::string_view payload) : m_payload(payload) {}

    [[nodiscard]] std::string read_string() {
        if(m_position >= m_payload.size()) {
            throw std::runtime_error("persistent lexical payload ended before a field");
        }
        std::size_t size = 0;
        bool digit = false;
        while(m_position < m_payload.size() && m_payload[m_position] != ':') {
            const auto character = static_cast<unsigned char>(m_payload[m_position]);
            if(character < '0' || character > '9') {
                throw std::runtime_error("persistent lexical payload has an invalid size");
            }
            const auto value = static_cast<std::size_t>(character - '0');
            if(size > (std::numeric_limits<std::size_t>::max() - value) / 10U) {
                throw std::runtime_error("persistent lexical payload size overflow");
            }
            size = size * 10U + value;
            digit = true;
            ++m_position;
        }
        if(!digit || m_position >= m_payload.size()) {
            throw std::runtime_error("persistent lexical payload is missing a delimiter");
        }
        ++m_position;
        if(size > m_payload.size() - m_position) {
            throw std::runtime_error("persistent lexical payload field exceeds value");
        }
        std::string result{m_payload.data() + m_position, size};
        m_position += size;
        return result;
    }

    [[nodiscard]] std::uint64_t read_u64() {
        const auto text = read_string();
        if(text.empty()) {
            throw std::runtime_error("persistent lexical payload has an empty integer");
        }
        std::uint64_t result = 0;
        for(const auto character : text) {
            if(character < '0' || character > '9') {
                throw std::runtime_error("persistent lexical payload has an invalid integer");
            }
            const auto digit = static_cast<std::uint64_t>(character - '0');
            if(result > (std::numeric_limits<std::uint64_t>::max() - digit) / 10U) {
                throw std::runtime_error("persistent lexical payload integer overflow");
            }
            result = result * 10U + digit;
        }
        return result;
    }

    [[nodiscard]] std::uint32_t read_u32() {
        const auto value = read_u64();
        if(value > std::numeric_limits<std::uint32_t>::max()) {
            throw std::runtime_error("persistent lexical payload uint32 overflow");
        }
        return static_cast<std::uint32_t>(value);
    }

    void require_end() const {
        if(m_position != m_payload.size()) {
            throw std::runtime_error("persistent lexical payload has trailing bytes");
        }
    }

private:
    std::string_view m_payload;
    std::size_t m_position = 0;
};

std::string sanitize_table_part(std::string value) {
    if(value.empty()) {
        return "agent_memory";
    }
    for(char& character : value) {
        if(!std::isalnum(static_cast<unsigned char>(character))) {
            character = '_';
        }
    }
    return value;
}

std::string table_name(const std::string& prefix) {
    return sanitize_table_part(prefix) + '_' + std::string{kTableSuffix};
}

std::string hex_component(std::string_view value) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string encoded;
    encoded.reserve(value.size() * 2U);
    for(const auto character : value) {
        const auto byte = static_cast<unsigned char>(character);
        encoded.push_back(digits[byte >> 4U]);
        encoded.push_back(digits[byte & 0x0fU]);
    }
    return encoded;
}

std::string state_key(const LexicalIndexDescriptor& descriptor) {
    return "flat-v1" + std::string(1, kKeySeparator)
        + hex_component(descriptor.ref.scope_id.value())
        + std::string(1, kKeySeparator)
        + std::to_string(static_cast<std::uint16_t>(descriptor.ref.projection_kind));
}

void append_epoch(std::string& payload, const LexicalStatisticsEpoch& epoch) {
    const auto& bytes = epoch.bytes();
    payload.append(reinterpret_cast<const char*>(bytes.data()), bytes.size());
}

LexicalStatisticsEpoch read_epoch(PayloadReader& reader) {
    const auto text = reader.read_string();
    if(text.size() != 16U) {
        throw std::runtime_error("persistent lexical payload has an invalid epoch");
    }
    std::array<std::uint8_t, 16> bytes{};
    std::memcpy(bytes.data(), text.data(), bytes.size());
    return LexicalStatisticsEpoch{bytes};
}

void append_descriptor(std::string& payload, const LexicalIndexDescriptor& descriptor) {
    append_string(payload, descriptor.ref.scope_id.value());
    append_u32(payload, static_cast<std::uint16_t>(descriptor.ref.projection_kind));
    std::string epoch;
    epoch.reserve(16U);
    append_epoch(epoch, descriptor.ref.statistics_epoch);
    append_string(payload, epoch);
    append_string(payload, descriptor.projection_schema_id);
    append_u32(payload, descriptor.projection_schema_version);
    append_string(payload, descriptor.tokenizer_fingerprint);
    append_string(payload, descriptor.normalization_fingerprint);
    append_u32(payload, static_cast<std::uint32_t>(descriptor.supported_posting_layout_versions.size()));
    for(const auto version : descriptor.supported_posting_layout_versions) {
        append_u32(payload, version);
    }
}

LexicalIndexDescriptor read_descriptor(PayloadReader& reader) {
    LexicalIndexDescriptor descriptor;
    descriptor.ref.scope_id = ScopeId{reader.read_string()};
    descriptor.ref.projection_kind = static_cast<ProjectionKind>(reader.read_u32());
    descriptor.ref.statistics_epoch = read_epoch(reader);
    descriptor.projection_schema_id = reader.read_string();
    descriptor.projection_schema_version = reader.read_u32();
    descriptor.tokenizer_fingerprint = reader.read_string();
    descriptor.normalization_fingerprint = reader.read_string();
    const auto count = reader.read_u32();
    descriptor.supported_posting_layout_versions.reserve(count);
    for(std::uint32_t index = 0; index < count; ++index) {
        descriptor.supported_posting_layout_versions.push_back(reader.read_u32());
    }
    return descriptor;
}

void append_unit(std::string& payload, const FlatLexicalUnitInput& input) {
    append_string(payload, input.scope_id.value());
    append_u64(payload, input.unit_id.value());
    append_u32(payload, static_cast<std::uint16_t>(input.projection_kind));
    append_string(payload, input.resource_id.value());
    append_u64(payload, input.resource_generation);
    append_u64(payload, input.unit_revision);
    append_u64(payload, input.projection_version.unit_revision_at_build);
    append_u64(payload, input.projection_version.projection_revision);
    append_u64(payload, input.projection_version.derivation_generation);
    append_string(payload, input.projection_version.derivation_fingerprint);
    append_u32(payload, static_cast<std::uint32_t>(input.normalized_tokens.size()));
    for(const auto& token : input.normalized_tokens) {
        append_string(payload, token);
    }
}

FlatLexicalUnitInput read_unit(PayloadReader& reader) {
    FlatLexicalUnitInput input;
    input.scope_id = ScopeId{reader.read_string()};
    input.unit_id = KnowledgeUnitId{reader.read_u64()};
    input.projection_kind = static_cast<ProjectionKind>(reader.read_u32());
    input.resource_id = ResourceId{reader.read_string()};
    input.resource_generation = reader.read_u64();
    input.unit_revision = reader.read_u64();
    input.projection_version.unit_revision_at_build = reader.read_u64();
    input.projection_version.projection_revision = reader.read_u64();
    input.projection_version.derivation_generation = reader.read_u64();
    input.projection_version.derivation_fingerprint = reader.read_string();
    const auto token_count = reader.read_u32();
    input.normalized_tokens.reserve(token_count);
    for(std::uint32_t index = 0; index < token_count; ++index) {
        input.normalized_tokens.push_back(reader.read_string());
    }
    return input;
}

struct StoredState final {
    LexicalIndexDescriptor descriptor;
    LexicalActiveSnapshot snapshot;
    LexicalPublicationSequence publication_sequence;
    std::vector<FlatLexicalUnitInput> units;
};

std::string serialize_state(const StoredState& state) {
    std::string payload;
    append_string(payload, kPayloadVersion);
    append_descriptor(payload, state.descriptor);
    std::string epoch;
    epoch.reserve(16U);
    append_epoch(epoch, state.snapshot.statistics_epoch);
    append_string(payload, epoch);
    append_u64(payload, state.snapshot.statistics_generation);
    append_u32(payload, state.snapshot.posting_layout_version);
    append_u64(payload, state.publication_sequence.value());
    append_u32(payload, static_cast<std::uint32_t>(state.units.size()));
    for(const auto& unit : state.units) {
        append_unit(payload, unit);
    }
    return payload;
}

StoredState deserialize_state(std::string_view payload) {
    PayloadReader reader{payload};
    if(reader.read_string() != kPayloadVersion) {
        throw std::runtime_error("unsupported persistent lexical payload version");
    }
    StoredState state;
    state.descriptor = read_descriptor(reader);
    state.snapshot.statistics_epoch = read_epoch(reader);
    state.snapshot.statistics_generation = reader.read_u64();
    state.snapshot.posting_layout_version = reader.read_u32();
    state.publication_sequence = LexicalPublicationSequence{reader.read_u64()};
    const auto unit_count = reader.read_u32();
    state.units.reserve(unit_count);
    for(std::uint32_t index = 0; index < unit_count; ++index) {
        state.units.push_back(read_unit(reader));
    }
    reader.require_end();
    if(!is_valid_lexical_index_descriptor(state.descriptor)
        || !is_valid_lexical_active_snapshot(state.snapshot)
        || state.publication_sequence.empty()
        || state.snapshot.statistics_epoch != state.descriptor.ref.statistics_epoch) {
        throw std::runtime_error("persistent lexical payload failed admission");
    }
    return state;
}

StoredState empty_state(const LexicalIndexDescriptor& descriptor) {
    if(!is_valid_lexical_index_descriptor(descriptor)) {
        throw std::invalid_argument("invalid persistent lexical index descriptor");
    }
    if(descriptor.ref.projection_kind != ProjectionKind::Original) {
        throw std::invalid_argument("persistent flat lexical index supports Original only");
    }
    StoredState state;
    state.descriptor = descriptor;
    state.snapshot.statistics_epoch = descriptor.ref.statistics_epoch;
    state.snapshot.statistics_generation = 1;
    state.snapshot.posting_layout_version = descriptor.supported_posting_layout_versions.front();
    state.publication_sequence = LexicalPublicationSequence{1};
    return state;
}

void validate_descriptor_match(
    const LexicalIndexDescriptor& expected,
    const LexicalIndexDescriptor& actual
) {
    if(expected.ref != actual.ref
        || expected.projection_schema_id != actual.projection_schema_id
        || expected.projection_schema_version != actual.projection_schema_version
        || expected.tokenizer_fingerprint != actual.tokenizer_fingerprint
        || expected.normalization_fingerprint != actual.normalization_fingerprint
        || expected.supported_posting_layout_versions != actual.supported_posting_layout_versions) {
        throw std::runtime_error("persistent lexical descriptor mismatch");
    }
}

FlatLexicalIndex rebuild_index(const StoredState& state) {
    FlatLexicalIndex index{state.descriptor, state.snapshot.posting_layout_version};
    for(const auto& unit : state.units) {
        index.upsert(unit);
    }
    index.restore_active_snapshot_for_storage(state.snapshot);
    return index;
}

LexicalReadFrontier make_frontier(
    const StoredState& state,
    const FlatLexicalIndex& index
) {
    LexicalReadFrontier frontier;
    frontier.publication_sequence = state.publication_sequence;
    frontier.active_snapshot = state.snapshot;
    frontier.index_descriptor = state.descriptor;
    // The lexical DBI does not own canonical units, projections, or manifests.
    // Keep the captured lexical rows pinned, but fail closed for an Exact route
    // until a coordinator supplies those additional observations in the same
    // transaction.
    frontier.consistency = LexicalReadConsistency::NonLinearizable;
    if(!is_valid_lexical_read_frontier(frontier)) {
        throw std::runtime_error("persistent lexical frontier failed admission");
    }
    static_cast<void>(index);
    return frontier;
}

} // namespace

class MdbxPersistentLexicalIndex::Impl final {
public:
    explicit Impl(MdbxPersistentLexicalIndexOptions options)
        : descriptor(std::move(options.descriptor)),
          context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(
                                      std::move(options.path),
                                      options.relative_to_exe,
                                      options.max_dbs)),
          table(context->connection(), table_name(options.table_prefix)),
          key(state_key(descriptor)) {
        if(!is_valid_lexical_index_descriptor(descriptor)) {
            throw std::invalid_argument("invalid persistent lexical index descriptor");
        }
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto stored = table.find(key, txn);
        if(stored) {
            const auto state = deserialize_state(*stored);
            validate_descriptor_match(descriptor, state.descriptor);
        }
    }

    template<typename Transaction>
    StoredState read_state(Transaction& txn) const {
        const auto stored = table.find(key, txn);
        if(!stored) {
            return empty_state(descriptor);
        }
        auto state = deserialize_state(*stored);
        validate_descriptor_match(descriptor, state.descriptor);
        return state;
    }

    LexicalIndexDescriptor descriptor;
    std::shared_ptr<MdbxStorageContext> context;
    mdbxc::KeyValueTable<std::string, std::string> table;
    std::string key;
    MdbxPersistentLexicalIndexFaultPoint failure =
        MdbxPersistentLexicalIndexFaultPoint::None;
};

MdbxPersistentLexicalIndex::MdbxPersistentLexicalIndex(
    MdbxPersistentLexicalIndexOptions options
) : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxPersistentLexicalIndex::~MdbxPersistentLexicalIndex() = default;

const LexicalIndexDescriptor& MdbxPersistentLexicalIndex::descriptor() const noexcept {
    return m_impl->descriptor;
}

LexicalActiveSnapshot MdbxPersistentLexicalIndex::active_snapshot() const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    return m_impl->read_state(txn).snapshot;
}

LexicalPublicationSequence MdbxPersistentLexicalIndex::publication_sequence() const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    return m_impl->read_state(txn).publication_sequence;
}

void MdbxPersistentLexicalIndex::upsert(
    FlatLexicalUnitInput input,
    std::optional<FlatLexicalExpectedState> expected_current
) {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    auto state = m_impl->read_state(txn);
    auto index = rebuild_index(state);
    const auto before_generation = state.snapshot.statistics_generation;
    const auto before_publication = state.publication_sequence.value();
    index.upsert(input, expected_current);

    bool changed = true;
    for(const auto& current : state.units) {
        if(current.unit_id == input.unit_id) {
            changed = current.normalized_tokens != input.normalized_tokens
                || current.resource_id != input.resource_id
                || current.resource_generation != input.resource_generation
                || current.unit_revision != input.unit_revision
                || current.projection_version.unit_revision_at_build
                    != input.projection_version.unit_revision_at_build
                || current.projection_version.projection_revision
                    != input.projection_version.projection_revision
                || current.projection_version.derivation_generation
                    != input.projection_version.derivation_generation
                || current.projection_version.derivation_fingerprint
                    != input.projection_version.derivation_fingerprint;
            break;
        }
    }
    if(!changed) {
        return;
    }
    auto existing = std::find_if(state.units.begin(), state.units.end(),
        [&input](const FlatLexicalUnitInput& current) {
            return current.unit_id == input.unit_id;
        });
    if(existing == state.units.end()) {
        state.units.push_back(std::move(input));
    } else {
        *existing = std::move(input);
    }
    if(before_generation == std::numeric_limits<std::uint64_t>::max()
        || before_publication == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("persistent lexical generation exhausted");
    }
    state.snapshot.statistics_generation = before_generation + 1U;
    state.publication_sequence = LexicalPublicationSequence{before_publication + 1U};
    state.snapshot.statistics_epoch = descriptor().ref.statistics_epoch;
    if(m_impl->failure == MdbxPersistentLexicalIndexFaultPoint::BeforeCommit) {
        m_impl->failure = MdbxPersistentLexicalIndexFaultPoint::None;
        throw std::runtime_error("injected persistent lexical interruption");
    }
    m_impl->table.insert_or_assign(m_impl->key, serialize_state(state), txn);
    txn.commit();
}

bool MdbxPersistentLexicalIndex::erase(const KnowledgeUnitId& unit_id) {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    auto state = m_impl->read_state(txn);
    auto index = rebuild_index(state);
    if(!index.erase(unit_id)) {
        return false;
    }
    state.units.erase(std::remove_if(state.units.begin(), state.units.end(),
        [unit_id](const FlatLexicalUnitInput& unit) { return unit.unit_id == unit_id; }),
        state.units.end());
    if(state.snapshot.statistics_generation == std::numeric_limits<std::uint64_t>::max()
        || state.publication_sequence.value() == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("persistent lexical generation exhausted");
    }
    ++state.snapshot.statistics_generation;
    state.publication_sequence = LexicalPublicationSequence{
        state.publication_sequence.value() + 1U};
    m_impl->table.insert_or_assign(m_impl->key, serialize_state(state), txn);
    txn.commit();
    return true;
}

void MdbxPersistentLexicalIndex::clear() {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    auto state = m_impl->read_state(txn);
    if(state.units.empty()) {
        return;
    }
    if(state.snapshot.statistics_generation == std::numeric_limits<std::uint64_t>::max()
        || state.publication_sequence.value() == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("persistent lexical generation exhausted");
    }
    state.units.clear();
    ++state.snapshot.statistics_generation;
    state.publication_sequence = LexicalPublicationSequence{
        state.publication_sequence.value() + 1U};
    m_impl->table.insert_or_assign(m_impl->key, serialize_state(state), txn);
    txn.commit();
}

MdbxPersistentLexicalIndexSnapshot MdbxPersistentLexicalIndex::read_snapshot() const {
    auto transaction = std::make_shared<mdbxc::Transaction>(
        m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY));
    const auto state = m_impl->read_state(*transaction);
    auto index = rebuild_index(state);
    auto frontier = make_frontier(state, index);
    std::shared_ptr<void> transaction_owner{std::move(transaction)};
    return MdbxPersistentLexicalIndexSnapshot{
        std::move(index), std::move(frontier), std::move(transaction_owner)};
}

void MdbxPersistentLexicalIndex::set_failure_injection_for_testing(
    const MdbxPersistentLexicalIndexFaultPoint point
) noexcept {
    m_impl->failure = point;
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX
