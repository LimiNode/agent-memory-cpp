#include <agent_memory/infrastructure/mdbx/MdbxStorageContext.hpp>
#include <agent_memory/lexical/FlatLexicalIndex.hpp>
#include <agent_memory/lexical/LexicalSegmentEncoding.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <functional>
#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <future>
#include <thread>
#include <utility>
#include <vector>

namespace {

using namespace agent_memory;

constexpr char kSeparator = '\x1f';
constexpr std::string_view kPrefix = "seg-v2-proof";
constexpr std::string_view kDescriptor = "descriptor";
constexpr std::string_view kSnapshot = "snapshot";
constexpr std::string_view kSequence = "sequence";
constexpr std::string_view kHighWater = "high-water";
constexpr std::string_view kDictionary = "dictionary";
constexpr std::string_view kUnit = "unit";
constexpr std::string_view kPartitionMeta = "partition-meta";
constexpr std::string_view kSegment = "segment";
constexpr std::string_view kStats = "stats";
constexpr std::string_view kWitness = "witness";
constexpr std::string_view kCollection = "collection";

void require(const bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

void append_u64(std::string& bytes, const std::uint64_t value) {
    for(unsigned shift = 0; shift != 64U; shift += 8U) {
        bytes.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
}

void append_u32(std::string& bytes, const std::uint32_t value) {
    for(unsigned shift = 0; shift != 32U; shift += 8U) {
        bytes.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
}

void append_string(std::string& bytes, const std::string_view value) {
    require(value.size() <= UINT32_MAX, "proof string is too large");
    append_u32(bytes, static_cast<std::uint32_t>(value.size()));
    bytes.append(value.data(), value.size());
}

class Reader final {
public:
    explicit Reader(const std::string_view bytes) : m_bytes(bytes) {}

    bool raw(void* output, const std::size_t size) {
        if(size > m_bytes.size() - m_position) return false;
        std::memcpy(output, m_bytes.data() + m_position, size);
        m_position += size;
        return true;
    }

    bool u64(std::uint64_t& output) {
        unsigned char bytes[8]{};
        if(!raw(bytes, sizeof(bytes))) return false;
        output = 0;
        for(unsigned index = 0; index != 8U; ++index) {
            output |= static_cast<std::uint64_t>(bytes[index]) << (index * 8U);
        }
        return true;
    }

    bool u32(std::uint32_t& output) {
        unsigned char bytes[4]{};
        if(!raw(bytes, sizeof(bytes))) return false;
        output = static_cast<std::uint32_t>(bytes[0])
            | (static_cast<std::uint32_t>(bytes[1]) << 8U)
            | (static_cast<std::uint32_t>(bytes[2]) << 16U)
            | (static_cast<std::uint32_t>(bytes[3]) << 24U);
        return true;
    }

    bool string(std::string& output) {
        std::uint32_t size = 0;
        if(!u32(size) || size > m_bytes.size() - m_position) return false;
        output.assign(m_bytes.data() + m_position, size);
        m_position += size;
        return true;
    }

    [[nodiscard]] std::size_t remaining() const noexcept {
        return m_bytes.size() - m_position;
    }

private:
    std::string_view m_bytes;
    std::size_t m_position = 0;
};

std::string row_key(const std::string_view kind, const std::string_view suffix = {}) {
    std::string key{kPrefix};
    key.push_back(kSeparator);
    key.append(kind.data(), kind.size());
    if(!suffix.empty()) {
        key.push_back(kSeparator);
        key.append(suffix.data(), suffix.size());
    }
    return key;
}

std::string token_suffix(const TokenId token) {
    return std::to_string(token.value());
}

std::string descriptor_bytes(const LexicalIndexDescriptor& descriptor) {
    std::string bytes;
    append_string(bytes, descriptor.ref.scope_id.value());
    append_u32(bytes, static_cast<std::uint32_t>(descriptor.ref.projection_kind));
    bytes.append(reinterpret_cast<const char*>(descriptor.ref.statistics_epoch.bytes().data()), 16);
    append_string(bytes, descriptor.projection_schema_id);
    append_u32(bytes, descriptor.projection_schema_version);
    append_string(bytes, descriptor.tokenizer_fingerprint);
    append_string(bytes, descriptor.normalization_fingerprint);
    return bytes;
}

std::string serialize_unit(const FlatLexicalUnitInput& input) {
    std::string bytes;
    append_string(bytes, input.scope_id.value());
    append_u64(bytes, input.unit_id.value());
    append_u32(bytes, static_cast<std::uint32_t>(input.projection_kind));
    append_string(bytes, input.resource_id.value());
    append_u64(bytes, input.resource_generation);
    append_u64(bytes, input.unit_revision);
    append_u64(bytes, input.projection_version.unit_revision_at_build);
    append_u64(bytes, input.projection_version.projection_revision);
    append_u64(bytes, input.projection_version.derivation_generation);
    append_string(bytes, input.projection_version.derivation_fingerprint);
    append_u32(bytes, static_cast<std::uint32_t>(input.normalized_tokens.size()));
    for(const auto& token : input.normalized_tokens) append_string(bytes, token);
    return bytes;
}

FlatLexicalUnitInput deserialize_unit(const std::string_view bytes) {
    Reader reader{bytes};
    FlatLexicalUnitInput input;
    std::string value;
    std::uint64_t id = 0;
    std::uint32_t projection = 0;
    require(reader.string(value) && reader.u64(id) && reader.u32(projection),
        "proof unit header is malformed");
    input.scope_id = ScopeId{std::move(value)};
    input.unit_id = KnowledgeUnitId{id};
    input.projection_kind = static_cast<ProjectionKind>(projection);
    require(reader.string(value), "proof unit resource is malformed");
    input.resource_id = ResourceId{std::move(value)};
    require(reader.u64(input.resource_generation)
        && reader.u64(input.unit_revision)
        && reader.u64(input.projection_version.unit_revision_at_build)
        && reader.u64(input.projection_version.projection_revision)
        && reader.u64(input.projection_version.derivation_generation)
        && reader.string(input.projection_version.derivation_fingerprint),
        "proof unit revision is malformed");
    std::uint32_t count = 0;
    require(reader.u32(count), "proof unit token count is malformed");
    input.normalized_tokens.reserve(count);
    for(std::uint32_t index = 0; index != count; ++index) {
        require(reader.string(value), "proof unit token is malformed");
        input.normalized_tokens.push_back(value);
    }
    require(reader.remaining() == 0 && is_valid_flat_lexical_unit_input(input),
        "proof unit admission failed");
    return input;
}

std::string serialize_stats(const FlatLexicalTokenStats& stats) {
    std::string bytes;
    append_u64(bytes, stats.document_frequency);
    append_u64(bytes, stats.collection_frequency);
    return bytes;
}

FlatLexicalTokenStats deserialize_stats(const std::string_view bytes, const TokenId token) {
    Reader reader{bytes};
    FlatLexicalTokenStats result;
    result.token_id = token;
    require(reader.u64(result.document_frequency)
        && reader.u64(result.collection_frequency)
        && reader.remaining() == 0, "proof statistics are malformed");
    return result;
}

std::string serialize_collection(const FlatLexicalCollectionStats& stats) {
    std::string bytes;
    append_u64(bytes, stats.unit_count);
    append_u64(bytes, stats.total_token_count);
    return bytes;
}

FlatLexicalCollectionStats deserialize_collection(const std::string_view bytes) {
    Reader reader{bytes};
    FlatLexicalCollectionStats result;
    require(reader.u64(result.unit_count)
        && reader.u64(result.total_token_count)
        && reader.remaining() == 0, "proof collection statistics are malformed");
    return result;
}

bool same_input(const FlatLexicalUnitInput& lhs, const FlatLexicalUnitInput& rhs) {
    return lhs.scope_id == rhs.scope_id && lhs.unit_id == rhs.unit_id
        && lhs.projection_kind == rhs.projection_kind
        && lhs.resource_id == rhs.resource_id
        && lhs.resource_generation == rhs.resource_generation
        && lhs.unit_revision == rhs.unit_revision
        && lhs.projection_version.unit_revision_at_build
            == rhs.projection_version.unit_revision_at_build
        && lhs.projection_version.projection_revision
            == rhs.projection_version.projection_revision
        && lhs.projection_version.derivation_generation
            == rhs.projection_version.derivation_generation
        && lhs.projection_version.derivation_fingerprint
            == rhs.projection_version.derivation_fingerprint
        && lhs.normalized_tokens == rhs.normalized_tokens;
}

bool same_postings(
    const std::vector<FlatLexicalPosting>& lhs,
    const std::vector<FlatLexicalPosting>& rhs
) {
    if(lhs.size() != rhs.size()) return false;
    for(std::size_t index = 0; index != lhs.size(); ++index) {
        const auto& left = lhs[index];
        const auto& right = rhs[index];
        if(left.token_id != right.token_id || left.unit_id != right.unit_id
            || left.resource_id != right.resource_id
            || left.resource_generation != right.resource_generation
            || left.projection_version.unit_revision_at_build
                != right.projection_version.unit_revision_at_build
            || left.projection_version.projection_revision
                != right.projection_version.projection_revision
            || left.projection_version.derivation_generation
                != right.projection_version.derivation_generation
            || left.projection_version.derivation_fingerprint
                != right.projection_version.derivation_fingerprint
            || left.term_frequency != right.term_frequency) return false;
    }
    return true;
}

bool expected_matches(
    const FlatLexicalExpectedState& expected,
    const FlatLexicalUnitInput& current
) {
    return expected.unit_id == current.unit_id
        && expected.resource_generation == current.resource_generation
        && expected.unit_revision == current.unit_revision
        && expected.projection_version.unit_revision_at_build
            == current.projection_version.unit_revision_at_build
        && expected.projection_version.projection_revision
            == current.projection_version.projection_revision
        && expected.projection_version.derivation_generation
            == current.projection_version.derivation_generation
        && expected.projection_version.derivation_fingerprint
            == current.projection_version.derivation_fingerprint;
}

LexicalIndexDescriptor descriptor(const ScopeId& scope) {
    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = 41;
    epoch[15] = 42;
    return LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{
            scope, ProjectionKind::Original, LexicalStatisticsEpoch{epoch}},
        "original", 1, "tokenizer-v1", "normalization-v1",
        {kLexicalSegmentLayoutVersion}
    };
}

FlatLexicalUnitInput unit(
    const ScopeId& scope,
    const std::uint64_t id,
    const std::uint64_t revision,
    std::vector<std::string> tokens
) {
    return FlatLexicalUnitInput{
        scope, KnowledgeUnitId{id}, ProjectionKind::Original,
        ResourceId{"resource:" + std::to_string(id)}, revision, revision,
        ProjectionVersionRef{revision, revision, 1, "derivation-v1"},
        std::move(tokens)
    };
}

struct State final {
    std::map<std::uint64_t, FlatLexicalUnitInput> units;
    std::map<std::string, TokenId> dictionary;
    std::uint64_t next_token_id = 1;
    std::uint64_t snapshot_generation = 1;
    std::uint64_t sequence = 1;
    std::map<TokenId, std::uint64_t> partition_generations;
    std::map<TokenId, FlatLexicalTokenStats> stats;
    std::map<TokenId, std::array<std::uint8_t, 32>> witnesses;
    FlatLexicalCollectionStats collection;
};

struct MutationTelemetry final {
    std::size_t insert_or_assign_calls = 0;
    std::size_t erase_calls = 0;
    std::vector<std::string> mutated_keys;
    bool scanned_full_logical_index = false;
};

class ProofStore final {
public:
    ProofStore(
        std::shared_ptr<MdbxStorageContext> context,
        LexicalIndexDescriptor descriptor
    ) : m_context(std::move(context)), m_descriptor(std::move(descriptor)),
        m_table(m_context->connection(), "agent_memory_lexical_posting_segments") {}

    [[nodiscard]] std::map<std::string, std::string> segment_rows() const {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        std::map<std::string, std::string> result;
        const auto from = row_key(kSegment);
        auto to = from;
        to.push_back(static_cast<char>(0xff));
        m_table.for_each_range(from, to,
            [&result](const std::string& key, const std::string& value) {
                result.emplace(key, value);
                return true;
            }, txn);
        return result;
    }

    [[nodiscard]] FlatLexicalIndex read() const {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto state = read_state(txn);
        const auto index = rebuild(state);
        validate_physical(state, index, txn);
        return index;
    }

    [[nodiscard]] MutationTelemetry last_mutations() const {
        return m_telemetry;
    }

    [[nodiscard]] std::uint64_t partition_generation(const TokenId token) const {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto state = read_state(txn);
        const auto found = state.partition_generations.find(token);
        require(found != state.partition_generations.end(),
            "proof partition generation is missing");
        return found->second;
    }

    void replace_physical_row(
        const std::string& key,
        const std::string& value
    ) {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        m_table.insert_or_assign(key, value, txn);
        txn.commit();
    }

    void erase_physical_row(const std::string& key) {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        m_table.erase(key, txn);
        txn.commit();
    }

    [[nodiscard]] std::uint64_t sequence() const {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        return read_state(txn).sequence;
    }

    template<typename Transaction>
    [[nodiscard]] std::uint64_t sequence(Transaction& txn) const {
        return read_state(txn).sequence;
    }

    void arm_failure() noexcept { m_fail_after_write = true; }

    void upsert(
        FlatLexicalUnitInput input,
        const std::optional<FlatLexicalExpectedState>& expected = std::nullopt
    ) {
        m_telemetry = {};
        m_telemetry.scanned_full_logical_index = true;
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        auto state = read_state(txn);
        auto before = rebuild(state);
        const auto existing = state.units.find(input.unit_id.value());
        if(existing != state.units.end() && same_input(existing->second, input)) {
            if(expected && !expected_matches(*expected, existing->second)) {
                throw std::runtime_error("incremental proof stale expected state");
            }
            return;
        }
        before.upsert(input, expected);
        const auto old_tokens = tokens_for_unit(state, existing, before, input.unit_id);
        state.units[input.unit_id.value()] = input;
        auto after = before;
        after.restore_active_snapshot_for_storage(
            LexicalActiveSnapshot{m_descriptor.ref.statistics_epoch,
                state.snapshot_generation, kLexicalSegmentLayoutVersion});

        std::set<TokenId> touched = old_tokens;
        const auto new_tokens = tokens_for_unit_after(after, input.unit_id);
        touched.insert(new_tokens.begin(), new_tokens.end());
        for(const auto token : touched) {
            const auto found_generation = state.partition_generations.find(token);
            const auto old_generation = found_generation == state.partition_generations.end()
                ? 0U : found_generation->second;
            require(old_generation != UINT64_MAX,
                "proof partition generation exhausted");
            const auto generation = old_generation + 1U;
            const auto postings = after.postings(token);
            erase_partition_rows(token, txn);
            state.partition_generations[token] = generation;
            insert_row(kPartitionMeta, token_suffix(token), serialize_u64(generation), txn);
            if(postings.empty()) {
                state.stats.erase(token);
                state.witnesses.erase(token);
                erase_row(kStats, token_suffix(token), txn);
                erase_row(kWitness, token_suffix(token), txn);
                continue;
            }
            const auto witness = lexical_posting_membership_witness(postings);
            state.witnesses[token] = witness;
            state.stats[token] = *after.find_token_stats(token);
            const auto segments = after.posting_segments(token, 2);
            for(const auto& source : segments) {
                LexicalSegmentRow row;
                row.key.scope_id = m_descriptor.ref.scope_id;
                row.key.projection_kind = ProjectionKind::Original;
                row.key.statistics_epoch = m_descriptor.ref.statistics_epoch;
                row.key.statistics_generation = generation;
                row.key.token_id = token;
                row.key.segment_id = source.segment_id;
                row.key.posting_layout_version = kLexicalSegmentLayoutVersion;
                row.membership_witness = witness;
                row.segment = source;
                row.segment.statistics_generation = generation;
                const auto encoded = encode_lexical_segment_row(row);
                require(encoded.complete(), "proof segment encoding failed");
                insert_row(kSegment,
                    std::to_string(token.value()) + ":"
                        + std::to_string(generation) + ":"
                        + std::to_string(source.segment_id),
                    encoded.bytes, txn);
            }
            insert_row(kPartitionMeta, token_suffix(token), serialize_u64(generation), txn);
            insert_row(kStats, token_suffix(token), serialize_stats(state.stats[token]), txn);
            insert_row(kWitness, token_suffix(token),
                std::string(reinterpret_cast<const char*>(witness.data()), witness.size()), txn);
        }
        require(state.snapshot_generation != UINT64_MAX
            && state.sequence != UINT64_MAX,
            "proof publication sequence exhausted");
        ++state.snapshot_generation;
        ++state.sequence;
        insert_row(kDescriptor, {}, descriptor_bytes(m_descriptor), txn);
        insert_row(kUnit, std::to_string(input.unit_id.value()), serialize_unit(input), txn);
        persist_dictionary(before, state, txn);
        insert_row(kSnapshot, {}, serialize_u64(state.snapshot_generation), txn);
        insert_row(kSequence, {}, serialize_u64(state.sequence), txn);
        insert_row(kHighWater, {}, serialize_u64(state.next_token_id), txn);
        state.collection = after.collection_stats();
        insert_row(kCollection, {}, serialize_collection(state.collection), txn);
        if(m_fail_after_write) {
            m_fail_after_write = false;
            throw std::runtime_error("injected incremental proof interruption");
        }
        txn.commit();
    }

    void validate() const {
        auto txn = m_context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto state = read_state(txn);
        const auto index = rebuild(state);
        validate_physical(state, index, txn);
    }

private:
    template<typename Transaction>
    void validate_physical(
        const State& state,
        const FlatLexicalIndex& index,
        Transaction& txn
    ) const {
        require(state.collection.unit_count == index.collection_stats().unit_count,
            "proof collection unit count mismatch");
        require(state.collection.total_token_count == index.collection_stats().total_token_count,
            "proof collection token count mismatch");
        std::set<TokenId> active_tokens;
        for(const auto& entry : state.dictionary) {
            const auto token = entry.second;
            const auto postings = index.postings(token);
            if(postings.empty()) continue;
            active_tokens.insert(token);
            const auto stat = state.stats.find(token);
            const auto witness = state.witnesses.find(token);
            require(stat != state.stats.end() && witness != state.witnesses.end(),
                "proof active token metadata is missing");
            const auto expected = index.find_token_stats(token);
            require(expected && expected->document_frequency == stat->second.document_frequency
                && expected->collection_frequency == stat->second.collection_frequency,
                "proof token statistics mismatch");
            require(lexical_posting_membership_witness_matches(postings, witness->second),
                "proof membership witness mismatch");
            const auto generation = state.partition_generations.at(token);
            std::vector<FlatLexicalPostingSegment> segments;
            for(const auto& row : segment_rows_for(token, txn)) {
                const auto marker = row.first.substr(std::string{kPrefix}.size() + 1U);
                const auto separator = marker.find(kSeparator);
                const auto suffix = std::string_view{marker}.substr(separator + 1U);
                const auto first = suffix.find(':');
                const auto second = suffix.find(':', first + 1U);
                require(first != std::string_view::npos
                    && second != std::string_view::npos,
                    "proof partition address is malformed");
                const auto address_token = decode_u64_suffix(suffix.substr(0, first));
                const auto address_generation = decode_u64_suffix(
                    suffix.substr(first + 1U, second - first - 1U));
                const auto address_segment = decode_u64_suffix(suffix.substr(second + 1U));
                const auto decoded = decode_lexical_segment_row(row.second);
                require(decoded.complete()
                    && decoded.row.key.token_id == token
                    && decoded.row.key.statistics_generation == generation
                    && address_token == token.value()
                    && address_generation == generation
                    && decoded.row.key.segment_id == address_segment,
                    "proof partition generation mismatch");
                segments.push_back(decoded.row.segment);
            }
            const auto reconstructed = reconstruct_flat_lexical_posting_partition(
                segments,
                FlatLexicalPostingPartitionExpectation{
                    m_descriptor.ref.statistics_epoch, generation,
                    kLexicalSegmentLayoutVersion, token, segments.size(),
                    postings.size(), 2, postings
                });
            require(reconstructed.complete()
                && same_postings(reconstructed.postings, postings),
                "proof partition posting order or membership mismatch");
        }
        std::set<TokenId> physical_tokens;
        const auto from = row_key(kSegment);
        auto to = from;
        to.push_back(static_cast<char>(0xff));
        m_table.for_each_range(from, to,
            [&physical_tokens](const std::string& key, const std::string&) {
                const auto marker = key.substr(std::string{kPrefix}.size() + 1U);
                const auto separator = marker.find(kSeparator);
                require(separator != std::string::npos,
                    "proof partition address is malformed");
                const auto suffix = std::string_view{marker}.substr(separator + 1U);
                const auto first = suffix.find(':');
                require(first != std::string_view::npos,
                    "proof partition address is malformed");
                physical_tokens.insert(TokenId{
                    decode_u64_suffix(suffix.substr(0, first))});
                return true;
            }, txn);
        require(physical_tokens == active_tokens,
            "proof physical partition set does not match active dictionary");
    }
    static std::string serialize_u64(const std::uint64_t value) {
        std::string result;
        append_u64(result, value);
        return result;
    }

    template<typename Transaction>
    void insert_row(
        const std::string_view kind,
        const std::string_view suffix,
        const std::string& value,
        Transaction& txn
    ) const {
        const auto key = row_key(kind, suffix);
        m_table.insert_or_assign(key, value, txn);
        ++m_telemetry.insert_or_assign_calls;
        m_telemetry.mutated_keys.push_back(key);
    }

    template<typename Transaction>
    void erase_row(
        const std::string_view kind,
        const std::string_view suffix,
        Transaction& txn
    ) const {
        const auto key = row_key(kind, suffix);
        m_table.erase(key, txn);
        ++m_telemetry.erase_calls;
        m_telemetry.mutated_keys.push_back(key);
    }

    template<typename Transaction>
    void erase_partition_rows(const TokenId token, Transaction& txn) const {
        const auto from = row_key(kSegment, token_suffix(token) + ":");
        auto to = from;
        to.push_back(static_cast<char>(0xff));
        std::vector<std::string> keys;
        m_table.for_each_range(from, to,
            [&keys](const std::string& key, const std::string&) {
                keys.push_back(key);
                return true;
            }, txn);
        for(const auto& key : keys) {
            m_table.erase(key, txn);
            ++m_telemetry.erase_calls;
            m_telemetry.mutated_keys.push_back(key);
        }
    }

    template<typename Transaction>
    std::map<std::string, std::string> segment_rows_for(
        const TokenId token,
        Transaction& txn
    ) const {
        std::map<std::string, std::string> result;
        const auto from = row_key(kSegment, token_suffix(token) + ":");
        auto to = from;
        to.push_back(static_cast<char>(0xff));
        m_table.for_each_range(from, to,
            [&result](const std::string& key, const std::string& value) {
                result.emplace(key, value);
                return true;
            }, txn);
        return result;
    }

    template<typename Transaction>
    State read_state(Transaction& txn) const {
        State state;
        bool has_rows = false;
        bool has_descriptor = false;
        bool has_snapshot = false;
        bool has_sequence = false;
        bool has_high_water = false;
        bool has_collection = false;
        const auto from = std::string{kPrefix} + std::string(1, kSeparator);
        auto to = from;
        to.push_back(static_cast<char>(0xff));
        m_table.for_each_range(from, to,
            [this, &state, &has_rows, &has_descriptor, &has_snapshot,
                &has_sequence, &has_high_water, &has_collection](
                const std::string& key, const std::string& value) {
                has_rows = true;
                const auto marker = key.substr(std::string{kPrefix}.size() + 1U);
                const auto split = marker.find(kSeparator);
                const auto kind = marker.substr(0, split);
                const auto suffix = split == std::string::npos
                    ? std::string_view{}
                    : std::string_view{marker}.substr(split + 1U);
                if(kind == kDescriptor) {
                    require(value == descriptor_bytes(m_descriptor),
                        "proof descriptor admission failed");
                    has_descriptor = true;
                } else if(kind == kSnapshot) {
                    state.snapshot_generation = decode_u64(value);
                    has_snapshot = true;
                } else if(kind == kSequence) {
                    state.sequence = decode_u64(value);
                    has_sequence = true;
                } else if(kind == kHighWater) {
                    state.next_token_id = decode_u64(value);
                    has_high_water = true;
                } else if(kind == kDictionary) {
                    std::uint64_t token = decode_u64(value);
                    state.dictionary.emplace(std::string{suffix}, TokenId{token});
                } else if(kind == kUnit) {
                    auto input = deserialize_unit(value);
                    state.units.emplace(input.unit_id.value(), std::move(input));
                } else if(kind == kPartitionMeta) {
                    state.partition_generations.emplace(TokenId{decode_u64_suffix(suffix)}, decode_u64(value));
                } else if(kind == kStats) {
                    const auto token = TokenId{decode_u64_suffix(suffix)};
                    state.stats.emplace(token, deserialize_stats(value, token));
                } else if(kind == kWitness) {
                    const auto token = TokenId{decode_u64_suffix(suffix)};
                    require(value.size() == 32U, "proof witness is malformed");
                    std::array<std::uint8_t, 32> witness{};
                    std::memcpy(witness.data(), value.data(), witness.size());
                    state.witnesses.emplace(token, witness);
                } else if(kind == kCollection) {
                    state.collection = deserialize_collection(value);
                    has_collection = true;
                } else if(kind == kSegment) {
                    const auto first = suffix.find(':');
                    const auto second = suffix.find(':', first + 1U);
                    require(first != std::string_view::npos
                        && second != std::string_view::npos,
                        "proof segment address is malformed");
                } else {
                    throw std::runtime_error("proof unknown row kind");
                }
                return true;
            }, txn);
        if(!has_rows) return state;
        require(has_descriptor, "proof descriptor is missing");
        require(has_snapshot && has_sequence && has_high_water && has_collection
            && state.snapshot_generation != 0 && state.sequence != 0
            && state.next_token_id != 0,
            "proof required metadata is missing");
        return state;
    }

    static std::uint64_t decode_u64(const std::string_view bytes) {
        Reader reader{bytes};
        std::uint64_t value = 0;
        require(reader.u64(value) && reader.remaining() == 0, "proof integer is malformed");
        return value;
    }

    static std::uint64_t decode_u64_suffix(const std::string_view text) {
        require(!text.empty(), "proof numeric suffix is empty");
        std::uint64_t value = 0;
        for(const char character : text) {
            require(character >= '0' && character <= '9', "proof numeric suffix is malformed");
            const auto digit = static_cast<std::uint64_t>(character - '0');
            require(value <= (UINT64_MAX - digit) / 10U, "proof numeric suffix overflow");
            value = value * 10U + digit;
        }
        return value;
    }

    FlatLexicalIndex rebuild(const State& state) const {
        FlatLexicalIndex index{m_descriptor, kLexicalSegmentLayoutVersion};
        std::vector<std::pair<std::string, TokenId>> dictionary;
        dictionary.reserve(state.dictionary.size());
        for(const auto& entry : state.dictionary) dictionary.push_back(entry);
        index.restore_token_dictionary_for_storage(std::move(dictionary), TokenId{state.next_token_id});
        for(const auto& entry : state.units) index.upsert(entry.second);
        index.restore_active_snapshot_for_storage(
            LexicalActiveSnapshot{m_descriptor.ref.statistics_epoch,
                state.snapshot_generation, kLexicalSegmentLayoutVersion});
        return index;
    }

    static std::set<TokenId> tokens_for_unit(
        const State& state,
        const std::map<std::uint64_t, FlatLexicalUnitInput>::const_iterator existing,
        const FlatLexicalIndex& index,
        const KnowledgeUnitId unit_id
    ) {
        std::set<TokenId> result;
        if(existing == state.units.end()) return result;
        for(const auto& token : existing->second.normalized_tokens) {
            const auto token_id = index.find_token_id(token);
            if(token_id) result.insert(*token_id);
        }
        static_cast<void>(unit_id);
        return result;
    }

    static std::set<TokenId> tokens_for_unit_after(
        const FlatLexicalIndex& index,
        const KnowledgeUnitId unit_id
    ) {
        std::set<TokenId> result;
        const auto stats = index.find_unit_stats(unit_id);
        if(!stats) return result;
        for(const auto& token : index.token_dictionary_for_storage()) {
            for(const auto& posting : index.postings(token.second)) {
                if(posting.unit_id == unit_id) {
                    result.insert(token.second);
                    break;
                }
            }
        }
        return result;
    }

    template<typename Transaction>
    void persist_dictionary(
        const FlatLexicalIndex& index,
        State& state,
        Transaction& txn
    ) const {
        const auto old_dictionary = state.dictionary;
        state.dictionary.clear();
        for(const auto& entry : index.token_dictionary_for_storage()) {
            state.dictionary.emplace(entry.first, entry.second);
            const auto old = old_dictionary.find(entry.first);
            if(old == old_dictionary.end() || old->second != entry.second) {
                insert_row(kDictionary, entry.first,
                    serialize_u64(entry.second.value()), txn);
            }
        }
        state.next_token_id = index.next_token_id_for_storage().value();
    }

    std::shared_ptr<MdbxStorageContext> m_context;
    LexicalIndexDescriptor m_descriptor;
    mutable mdbxc::KeyValueTable<std::string, std::string> m_table;
    bool m_fail_after_write = false;
    mutable MutationTelemetry m_telemetry;
};

void test_local_partition_update_and_recovery(const std::filesystem::path& path) {
    const ScopeId scope{"scope-incremental-proof"};
    auto context = MdbxStorageContext::open(path.string());
    {
    ProofStore store{context, descriptor(scope)};

    const auto first = unit(scope, 1, 1, {"shared", "stable"});
    const auto second = unit(scope, 2, 1, {"shared", "untouched"});
    const auto third = unit(scope, 3, 1, {"other"});
    store.upsert(first);
    store.upsert(second);
    store.upsert(third);
    store.validate();
    const auto before_rows = store.segment_rows();
    const auto before_sequence = store.sequence();
    const auto old_shared = store.read().find_token_id("shared");
    const auto initial = store.read();
    const auto old_stable = initial.find_token_id("stable");
    const auto old_untouched = initial.find_token_id("untouched");
    require(old_shared && old_stable && old_untouched,
        "proof initial dictionary is incomplete");
    const auto stable_generation_before = store.partition_generation(*old_stable);

    store.upsert(unit(scope, 1, 2, {"shared", "replacement"}),
        FlatLexicalExpectedState{
            KnowledgeUnitId{1}, 1, 1,
            ProjectionVersionRef{1, 1, 1, "derivation-v1"}
        });
    const auto sparse_mutations = store.last_mutations();
    require(sparse_mutations.insert_or_assign_calls != 0
        && sparse_mutations.erase_calls != 0
        && sparse_mutations.scanned_full_logical_index,
        "proof sparse update telemetry is incomplete");
    std::cout << "sparse_kv_mutations insert_or_assign="
        << sparse_mutations.insert_or_assign_calls
        << " erase=" << sparse_mutations.erase_calls
        << " full_logical_scan="
        << (sparse_mutations.scanned_full_logical_index ? 1 : 0) << '\n';
    store.validate();
    const auto after_rows = store.segment_rows();
    std::size_t changed_rows = 0;
    for(const auto& entry : before_rows) {
        const auto found = after_rows.find(entry.first);
        if(found == after_rows.end() || found->second != entry.second) ++changed_rows;
    }
    require(changed_rows != 0, "proof update did not change a physical partition");
    for(const auto& entry : before_rows) {
        const auto untouched_segment_prefix = std::string{kSegment}
            + std::string(1, kSeparator) + std::to_string(old_untouched->value()) + ":";
        if(entry.first.find(untouched_segment_prefix) != std::string::npos) {
            require(after_rows.at(entry.first) == entry.second,
                "untouched partition bytes changed");
        }
    }
    const auto current = store.read();
    require(current.find_token_id("shared") == old_shared,
        "shared token id was not stable");
    require(current.find_token_id("stable") == old_stable,
        "removed token id was not retained in the dictionary");
    require(current.find_token_id("replacement").value().value() > old_untouched->value(),
        "new token did not advance dictionary high-water");
    require(store.sequence() == before_sequence + 1U,
        "incremental update did not publish one sequence step");
    require(store.partition_generation(*old_stable) > stable_generation_before,
        "removed partition generation did not advance");
    const auto untouched_segment_prefix = std::string{kSegment}
        + std::string(1, kSeparator) + std::to_string(old_untouched->value()) + ":";
    for(const auto& key : sparse_mutations.mutated_keys) {
        require(key.find(untouched_segment_prefix) == std::string::npos,
            "sparse update mutated an unrelated partition row");
    }

    store.upsert(unit(scope, 4, 1, {"ephemeral"}));
    const auto ephemeral = store.read().find_token_id("ephemeral");
    require(ephemeral.has_value(), "proof lifecycle token was not allocated");
    const auto ephemeral_generation_1 = store.partition_generation(*ephemeral);
    store.upsert(unit(scope, 4, 2, {"temporary"}),
        FlatLexicalExpectedState{
            KnowledgeUnitId{4}, 1, 1,
            ProjectionVersionRef{1, 1, 1, "derivation-v1"}
        });
    const auto ephemeral_generation_2 = store.partition_generation(*ephemeral);
    require(ephemeral_generation_2 > ephemeral_generation_1,
        "partition generation did not advance on deactivation");
    store.upsert(unit(scope, 4, 3, {"ephemeral"}),
        FlatLexicalExpectedState{
            KnowledgeUnitId{4}, 2, 2,
            ProjectionVersionRef{2, 2, 1, "derivation-v1"}
        });
    require(store.read().find_token_id("ephemeral") == ephemeral
        && store.partition_generation(*ephemeral) > ephemeral_generation_2,
        "partition generation regressed after token reactivation");

    const auto physical_rows = store.segment_rows();
    const auto shared_prefix = std::string{kSegment} + std::string(1, kSeparator)
        + std::to_string(old_shared->value()) + ":";
    const auto shared_row = std::find_if(physical_rows.begin(), physical_rows.end(),
        [&shared_prefix](const auto& entry) {
            return entry.first.find(shared_prefix) != std::string::npos;
        });
    require(shared_row != physical_rows.end(), "proof shared physical row is missing");
    const auto shared_key = shared_row->first;
    const auto shared_value = shared_row->second;
    auto expect_physical_rejection = [&](const char* message,
                                         const std::function<void()>& corrupt,
                                         const std::function<void()>& restore) {
        corrupt();
        bool rejected = false;
        try { static_cast<void>(store.read()); }
        catch(const std::runtime_error&) { rejected = true; }
        restore();
        require(rejected, message);
        store.validate();
    };

    expect_physical_rejection(
        "checksum corruption was not rejected",
        [&] {
            auto corrupt = shared_value;
            corrupt.back() ^= static_cast<char>(0x01);
            store.replace_physical_row(shared_key, corrupt);
        },
        [&] { store.replace_physical_row(shared_key, shared_value); });

    expect_physical_rejection(
        "missing active segment was not rejected",
        [&] { store.erase_physical_row(shared_key); },
        [&] { store.replace_physical_row(shared_key, shared_value); });

    const auto extra_key = shared_key.substr(0, shared_key.rfind(':') + 1U) + "999";
    expect_physical_rejection(
        "extra segment was not rejected",
        [&] { store.replace_physical_row(extra_key, shared_value); },
        [&] { store.erase_physical_row(extra_key); });

    const auto decoded_shared = decode_lexical_segment_row(shared_value);
    require(decoded_shared.complete(), "proof shared row failed baseline decoding");
    auto wrong_generation_row = decoded_shared.row;
    ++wrong_generation_row.key.statistics_generation;
    wrong_generation_row.segment.statistics_generation
        = wrong_generation_row.key.statistics_generation;
    const auto wrong_generation_value = encode_lexical_segment_row(
        wrong_generation_row).bytes;
    expect_physical_rejection(
        "wrong partition generation was not rejected",
        [&] { store.replace_physical_row(shared_key, wrong_generation_value); },
        [&] { store.replace_physical_row(shared_key, shared_value); });

    const auto witness_key = row_key(kWitness, token_suffix(*old_shared));
    const std::string zero_witness(32, '\0');
    const auto correct_witness = lexical_posting_membership_witness(
        store.read().postings(*old_shared));
    expect_physical_rejection(
        "membership witness corruption was not rejected",
        [&] { store.replace_physical_row(witness_key, zero_witness); },
        [&] {
            store.replace_physical_row(witness_key,
                std::string(reinterpret_cast<const char*>(correct_witness.data()),
                    correct_witness.size()));
        });

    const auto malformed_key = shared_key.substr(0, shared_key.rfind(':') + 1U) + "bad";
    expect_physical_rejection(
        "malformed physical address was not rejected",
        [&] { store.replace_physical_row(malformed_key, shared_value); },
        [&] { store.erase_physical_row(malformed_key); });

    const auto stable_prefix = std::string{kSegment} + std::string(1, kSeparator)
        + std::to_string(old_stable->value()) + ":";
    const auto orphan = std::find_if(before_rows.begin(), before_rows.end(),
        [&stable_prefix](const auto& entry) {
            return entry.first.find(stable_prefix) != std::string::npos;
        });
    require(orphan != before_rows.end(), "proof orphan fixture row is missing");
    const auto orphan_key = orphan->first;
    const auto orphan_value = orphan->second;
    expect_physical_rejection(
        "orphan segment was not rejected",
        [&] { store.replace_physical_row(orphan_key, orphan_value); },
        [&] { store.erase_physical_row(orphan_key); });

    const auto no_op_sequence = store.sequence();
    store.upsert(unit(scope, 1, 2, {"shared", "replacement"}));
    require(store.sequence() == no_op_sequence, "no-op published a new revision");
    bool no_op_stale_rejected = false;
    try {
        store.upsert(unit(scope, 1, 2, {"shared", "replacement"}),
            FlatLexicalExpectedState{
                KnowledgeUnitId{1}, 999, 2,
                ProjectionVersionRef{2, 2, 1, "derivation-v1"}
            });
    } catch(const std::runtime_error&) {
        no_op_stale_rejected = true;
    }
    require(no_op_stale_rejected,
        "stale expected state was accepted for a no-op publication");

    {
        ProofStore writer{context, descriptor(scope)};
        std::promise<std::uint64_t> ready;
        std::promise<void> release;
        std::promise<std::uint64_t> observed;
        auto ready_future = ready.get_future();
        auto release_future = release.get_future();
        auto observed_future = observed.get_future();
        std::thread reader([&] {
            auto pinned_transaction = context->connection()->transaction(
                mdbxc::TransactionMode::READ_ONLY);
            ready.set_value(store.sequence(pinned_transaction));
            release_future.wait();
            observed.set_value(store.sequence(pinned_transaction));
        });
        const auto pinned_sequence = ready_future.get();
        writer.upsert(unit(scope, 3, 2, {"other", "after-pinned"}),
            FlatLexicalExpectedState{
                KnowledgeUnitId{3}, 1, 1,
                ProjectionVersionRef{1, 1, 1, "derivation-v1"}
            });
        release.set_value();
        reader.join();
        require(observed_future.get() == pinned_sequence,
            "pinned read transaction observed a later publication");
    }

    {
        store.arm_failure();
        bool interrupted = false;
        try {
            store.upsert(unit(scope, 1, 3, {"rollback"}),
                FlatLexicalExpectedState{
                    KnowledgeUnitId{1}, 2, 2,
                    ProjectionVersionRef{2, 2, 1, "derivation-v1"}
                });
        } catch(const std::runtime_error&) {
            interrupted = true;
        }
        require(interrupted, "proof fault injection did not interrupt");
    }
    require(store.read().find_unit_stats(KnowledgeUnitId{1})->unit_revision == 2,
        "aborted proof publication changed active unit");
    store.validate();

    bool stale_rejected = false;
    try {
        store.upsert(unit(scope, 1, 3, {"accepted-only-if-current"}),
            FlatLexicalExpectedState{
                KnowledgeUnitId{1}, 99, 2,
                ProjectionVersionRef{2, 2, 1, "derivation-v1"}
            });
    } catch(const std::runtime_error&) {
        stale_rejected = true;
    }
    require(stale_rejected, "stale expected state was accepted");

    }
    context.reset();
    auto reopened_context = MdbxStorageContext::open(path.string());
    ProofStore reopened{reopened_context, descriptor(scope)};
    reopened.validate();
    require(reopened.read().find_unit_stats(KnowledgeUnitId{1})->unit_revision == 2,
        "reopen did not preserve the committed revision");
}

} // namespace

int main() {
    try {
        const auto path = std::filesystem::temp_directory_path()
            / "agent_memory_incremental_segmented_layout_proof.mdbx";
        std::error_code error;
        std::filesystem::remove(path, error);
        test_local_partition_update_and_recovery(path);
        std::filesystem::remove(path, error);
        require(!error, "incremental proof database cleanup failed");
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

#else

int main() { return 0; }

#endif
