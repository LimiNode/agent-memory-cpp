#include "MdbxSegmentedLexicalIndex.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <array>
#include <charconv>
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

constexpr char kSeparator = '\x1f';
constexpr std::string_view kNamespace = "seg-v1";
constexpr std::string_view kDescriptor = "descriptor";
constexpr std::string_view kSnapshot = "snapshot";
constexpr std::string_view kSequence = "sequence";
constexpr std::string_view kDictionary = "dictionary";
constexpr std::string_view kUnit = "unit";
constexpr std::string_view kSegment = "segment";
constexpr std::string_view kTokenStats = "token-stats";
constexpr std::string_view kCollectionStats = "collection-stats";
constexpr std::string_view kWitness = "witness";
constexpr std::string_view kHighWater = "high-water";

std::string sanitize_table_part(std::string value) {
    if(value.empty()) return "agent_memory";
    for(char& character : value) {
        if(!std::isalnum(static_cast<unsigned char>(character))) character = '_';
    }
    return value;
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

std::string scope_prefix(const LexicalIndexDescriptor& descriptor) {
    std::string value{kNamespace};
    value.push_back(kSeparator);
    value += hex_component(descriptor.ref.scope_id.value());
    value.push_back(kSeparator);
    value += std::to_string(static_cast<std::uint16_t>(
        descriptor.ref.projection_kind));
    return value;
}

std::string row_key(
    std::string_view prefix,
    std::string_view kind,
    std::string_view suffix = {}
) {
    std::string key{prefix};
    key.push_back(kSeparator);
    key.append(kind.data(), kind.size());
    if(!suffix.empty()) {
        key.push_back(kSeparator);
        key.append(suffix.data(), suffix.size());
    }
    return key;
}

std::string marker_prefix(std::string_view kind) {
    std::string marker(1, kSeparator);
    marker.append(kind.data(), kind.size());
    return marker;
}

void append_u32(std::string& bytes, const std::uint32_t value) {
    for(unsigned shift = 0; shift != 32U; shift += 8U) {
        bytes.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
}

void append_u64(std::string& bytes, const std::uint64_t value) {
    for(unsigned shift = 0; shift != 64U; shift += 8U) {
        bytes.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
}

void append_string(std::string& bytes, std::string_view value) {
    if(value.size() > std::numeric_limits<std::uint32_t>::max()) {
        throw std::overflow_error("segmented lexical string length overflow");
    }
    append_u32(bytes, static_cast<std::uint32_t>(value.size()));
    bytes.append(value.data(), value.size());
}

bool parse_canonical_u64(std::string_view text, std::uint64_t& value) {
    if(text.empty() || (text.size() > 1U && text.front() == '0')) return false;
    const auto* begin = text.data();
    const auto* end = begin + text.size();
    const auto parsed = std::from_chars(begin, end, value, 10);
    return parsed.ec == std::errc{} && parsed.ptr == end
        && std::to_string(value) == text;
}

bool parse_canonical_u32(std::string_view text, std::uint32_t& value) {
    std::uint64_t parsed = 0;
    if(!parse_canonical_u64(text, parsed)
        || parsed > std::numeric_limits<std::uint32_t>::max()) return false;
    value = static_cast<std::uint32_t>(parsed);
    return true;
}

bool extract_key_suffix(
    std::string_view marker,
    std::string_view kind,
    std::string_view& suffix
) {
    const auto prefix = marker_prefix(kind);
    if(marker.size() <= prefix.size() || marker.rfind(prefix, 0) != 0
        || marker[prefix.size()] != kSeparator) return false;
    suffix = marker.substr(prefix.size() + 1U);
    return !suffix.empty();
}

class Reader final {
public:
    explicit Reader(std::string_view bytes) : m_bytes(bytes) {}

    bool raw(void* out, std::size_t size) {
        if(size > m_bytes.size() - m_position) {
            return false;
        }
        std::memcpy(out, m_bytes.data() + m_position, size);
        m_position += size;
        return true;
    }

    bool u32(std::uint32_t& out) {
        unsigned char bytes[4];
        if(!raw(bytes, sizeof(bytes))) return false;
        out = static_cast<std::uint32_t>(bytes[0])
            | (static_cast<std::uint32_t>(bytes[1]) << 8U)
            | (static_cast<std::uint32_t>(bytes[2]) << 16U)
            | (static_cast<std::uint32_t>(bytes[3]) << 24U);
        return true;
    }

    bool u64(std::uint64_t& out) {
        unsigned char bytes[8];
        if(!raw(bytes, sizeof(bytes))) return false;
        out = 0;
        for(unsigned index = 0; index != 8U; ++index) {
            out |= static_cast<std::uint64_t>(bytes[index]) << (index * 8U);
        }
        return true;
    }

    bool string(std::string& out, std::size_t limit = 16U * 1024U * 1024U) {
        std::uint32_t size = 0;
        if(!u32(size) || size > limit || size > m_bytes.size() - m_position) {
            return false;
        }
        out.assign(m_bytes.data() + m_position, size);
        m_position += size;
        return true;
    }

    std::size_t remaining() const noexcept {
        return m_bytes.size() - m_position;
    }

private:
    std::string_view m_bytes;
    std::size_t m_position = 0;
};

void append_epoch(std::string& bytes, const LexicalStatisticsEpoch& epoch) {
    const auto& value = epoch.bytes();
    bytes.append(reinterpret_cast<const char*>(value.data()), value.size());
}

bool read_epoch(Reader& reader, LexicalStatisticsEpoch& epoch) {
    std::array<std::uint8_t, 16> value{};
    if(!reader.raw(value.data(), value.size())) return false;
    epoch = LexicalStatisticsEpoch{value};
    return true;
}

std::string serialize_descriptor(const LexicalIndexDescriptor& descriptor) {
    std::string bytes;
    append_string(bytes, descriptor.ref.scope_id.value());
    append_u32(bytes, static_cast<std::uint16_t>(descriptor.ref.projection_kind));
    append_epoch(bytes, descriptor.ref.statistics_epoch);
    append_string(bytes, descriptor.projection_schema_id);
    append_u32(bytes, descriptor.projection_schema_version);
    append_string(bytes, descriptor.tokenizer_fingerprint);
    append_string(bytes, descriptor.normalization_fingerprint);
    if(descriptor.supported_posting_layout_versions.size()
        > std::numeric_limits<std::uint32_t>::max()) {
        throw std::overflow_error("segmented lexical layout count overflow");
    }
    append_u32(bytes, static_cast<std::uint32_t>(
        descriptor.supported_posting_layout_versions.size()));
    for(const auto version : descriptor.supported_posting_layout_versions) {
        append_u32(bytes, version);
    }
    return bytes;
}

LexicalIndexDescriptor deserialize_descriptor(std::string_view bytes) {
    Reader reader{bytes};
    LexicalIndexDescriptor descriptor;
    std::string scope;
    std::uint32_t projection = 0;
    if(!reader.string(scope)
        || !reader.u32(projection)
        || !is_valid_projection_kind(static_cast<ProjectionKind>(projection))
        || !read_epoch(reader, descriptor.ref.statistics_epoch)
        || !reader.string(descriptor.projection_schema_id)
        || !reader.u32(descriptor.projection_schema_version)
        || !reader.string(descriptor.tokenizer_fingerprint)
        || !reader.string(descriptor.normalization_fingerprint)) {
        throw std::runtime_error("segmented lexical descriptor decode failed");
    }
    descriptor.ref.scope_id = ScopeId{std::move(scope)};
    descriptor.ref.projection_kind = static_cast<ProjectionKind>(projection);
    std::uint32_t count = 0;
    if(!reader.u32(count) || count > 256U || count > reader.remaining() / 4U) {
        throw std::runtime_error("segmented lexical descriptor layout decode failed");
    }
    descriptor.supported_posting_layout_versions.reserve(count);
    for(std::uint32_t index = 0; index != count; ++index) {
        std::uint32_t version = 0;
        if(!reader.u32(version)) {
            throw std::runtime_error("segmented lexical descriptor layout decode failed");
        }
        descriptor.supported_posting_layout_versions.push_back(version);
    }
    if(reader.remaining() != 0 || !is_valid_lexical_index_descriptor(descriptor)) {
        throw std::runtime_error("segmented lexical descriptor failed admission");
    }
    return descriptor;
}

std::string serialize_unit(const FlatLexicalUnitInput& input) {
    std::string bytes;
    append_string(bytes, input.scope_id.value());
    append_u64(bytes, input.unit_id.value());
    append_u32(bytes, static_cast<std::uint16_t>(input.projection_kind));
    append_string(bytes, input.resource_id.value());
    append_u64(bytes, input.resource_generation);
    append_u64(bytes, input.unit_revision);
    append_u64(bytes, input.projection_version.unit_revision_at_build);
    append_u64(bytes, input.projection_version.projection_revision);
    append_u64(bytes, input.projection_version.derivation_generation);
    append_string(bytes, input.projection_version.derivation_fingerprint);
    if(input.normalized_tokens.size() > std::numeric_limits<std::uint32_t>::max()) {
        throw std::overflow_error("segmented lexical token count overflow");
    }
    append_u32(bytes, static_cast<std::uint32_t>(input.normalized_tokens.size()));
    for(const auto& token : input.normalized_tokens) append_string(bytes, token);
    return bytes;
}

FlatLexicalUnitInput deserialize_unit(std::string_view bytes) {
    Reader reader{bytes};
    FlatLexicalUnitInput input;
    std::string scope;
    std::uint64_t value = 0;
    std::uint32_t projection = 0;
    if(!reader.string(scope)
        || !reader.u64(value)) throw std::runtime_error("segmented lexical unit decode failed");
    input.scope_id = ScopeId{std::move(scope)};
    input.unit_id = KnowledgeUnitId{value};
    if(!reader.u32(projection)
        || !is_valid_projection_kind(static_cast<ProjectionKind>(projection))) {
        throw std::runtime_error("segmented lexical unit projection decode failed");
    }
    input.projection_kind = static_cast<ProjectionKind>(projection);
    std::string text;
    if(!reader.string(text)) throw std::runtime_error("segmented lexical unit decode failed");
    input.resource_id = ResourceId{std::move(text)};
    if(!reader.u64(input.resource_generation)
        || !reader.u64(input.unit_revision)
        || !reader.u64(input.projection_version.unit_revision_at_build)
        || !reader.u64(input.projection_version.projection_revision)
        || !reader.u64(input.projection_version.derivation_generation)
        || !reader.string(input.projection_version.derivation_fingerprint)) {
        throw std::runtime_error("segmented lexical unit decode failed");
    }
    std::uint32_t count = 0;
    if(!reader.u32(count) || count > reader.remaining() / 4U) {
        throw std::runtime_error("segmented lexical unit token count decode failed");
    }
    input.normalized_tokens.reserve(count);
    for(std::uint32_t index = 0; index != count; ++index) {
        if(!reader.string(text)) throw std::runtime_error("segmented lexical unit token decode failed");
        input.normalized_tokens.push_back(text);
    }
    if(reader.remaining() != 0 || !is_valid_flat_lexical_unit_input(input)) {
        throw std::runtime_error("segmented lexical unit failed admission");
    }
    return input;
}

std::string serialize_snapshot(const LexicalActiveSnapshot& snapshot) {
    std::string bytes;
    append_epoch(bytes, snapshot.statistics_epoch);
    append_u64(bytes, snapshot.statistics_generation);
    append_u32(bytes, snapshot.posting_layout_version);
    return bytes;
}

LexicalActiveSnapshot deserialize_snapshot(std::string_view bytes) {
    Reader reader{bytes};
    LexicalActiveSnapshot snapshot;
    if(!read_epoch(reader, snapshot.statistics_epoch)
        || !reader.u64(snapshot.statistics_generation)
        || !reader.u32(snapshot.posting_layout_version)
        || reader.remaining() != 0
        || !is_valid_lexical_active_snapshot(snapshot)) {
        throw std::runtime_error("segmented lexical snapshot failed admission");
    }
    return snapshot;
}

std::string serialize_u64(const std::uint64_t value) {
    std::string bytes;
    append_u64(bytes, value);
    return bytes;
}

std::uint64_t deserialize_u64(std::string_view bytes) {
    Reader reader{bytes};
    std::uint64_t value = 0;
    if(!reader.u64(value) || reader.remaining() != 0) {
        throw std::runtime_error("segmented lexical sequence failed admission");
    }
    return value;
}

std::string serialize_token_stats(const FlatLexicalTokenStats& stats) {
    std::string bytes;
    append_u64(bytes, stats.document_frequency);
    append_u64(bytes, stats.collection_frequency);
    return bytes;
}

FlatLexicalTokenStats deserialize_token_stats(
    std::string_view bytes,
    const TokenId token_id
) {
    Reader reader{bytes};
    FlatLexicalTokenStats stats;
    stats.token_id = token_id;
    if(!reader.u64(stats.document_frequency)
        || !reader.u64(stats.collection_frequency)
        || reader.remaining() != 0) {
        throw std::runtime_error("segmented lexical token stats failed admission");
    }
    return stats;
}

std::string serialize_collection_stats(const FlatLexicalCollectionStats& stats) {
    std::string bytes;
    append_u64(bytes, stats.unit_count);
    append_u64(bytes, stats.total_token_count);
    return bytes;
}

FlatLexicalCollectionStats deserialize_collection_stats(std::string_view bytes) {
    Reader reader{bytes};
    FlatLexicalCollectionStats stats;
    if(!reader.u64(stats.unit_count)
        || !reader.u64(stats.total_token_count)
        || reader.remaining() != 0) {
        throw std::runtime_error("segmented lexical collection stats failed admission");
    }
    return stats;
}

bool same_descriptor(const LexicalIndexDescriptor& lhs,
                     const LexicalIndexDescriptor& rhs) {
    return lhs.ref == rhs.ref
        && lhs.projection_schema_id == rhs.projection_schema_id
        && lhs.projection_schema_version == rhs.projection_schema_version
        && lhs.tokenizer_fingerprint == rhs.tokenizer_fingerprint
        && lhs.normalization_fingerprint == rhs.normalization_fingerprint
        && lhs.supported_posting_layout_versions == rhs.supported_posting_layout_versions;
}

bool parse_segment_suffix(
    std::string_view marker,
    TokenId& token_id,
    std::uint32_t& segment_id
) {
    std::string_view suffix;
    if(!extract_key_suffix(marker, kSegment, suffix)) return false;
    const auto separator = suffix.find(':');
    if(separator == std::string_view::npos || suffix.find(':', separator + 1U)
            != std::string_view::npos) return false;
    std::uint64_t parsed_token = 0;
    std::uint32_t parsed_segment = 0;
    if(!parse_canonical_u64(suffix.substr(0, separator), parsed_token)
        || !parse_canonical_u32(suffix.substr(separator + 1U), parsed_segment)
        || parsed_token == 0U) return false;
    token_id = TokenId{parsed_token};
    segment_id = parsed_segment;
    return std::to_string(parsed_token) + ":" + std::to_string(parsed_segment)
        == suffix;
}

struct StoredState final {
    LexicalIndexDescriptor descriptor;
    LexicalActiveSnapshot snapshot;
    LexicalPublicationSequence sequence;
    std::vector<std::pair<std::string, TokenId>> dictionary;
    TokenId next_token_id{1};
    std::vector<FlatLexicalUnitInput> units;
    std::vector<LexicalSegmentRow> segments;
    std::map<TokenId, FlatLexicalTokenStats> token_stats;
    FlatLexicalCollectionStats collection_stats;
    std::map<TokenId, std::array<std::uint8_t, 32>> witnesses;
    bool has_descriptor = false;
    bool has_snapshot = false;
    bool has_sequence = false;
    bool has_high_water = false;
    bool has_collection_stats = false;
    bool has_any_rows = false;
};

FlatLexicalIndex rebuild_index(const StoredState& state) {
    FlatLexicalIndex index{state.descriptor, state.snapshot.posting_layout_version};
    index.restore_token_dictionary_for_storage(state.dictionary, state.next_token_id);
    for(const auto& unit : state.units) index.upsert(unit);
    index.restore_active_snapshot_for_storage(state.snapshot);
    if(index.token_dictionary_for_storage() != state.dictionary
        || index.next_token_id_for_storage() != state.next_token_id) {
        throw std::runtime_error("segmented lexical dictionary replay mismatch");
    }
    return index;
}

void validate_derived_rows(
    const StoredState& state,
    const FlatLexicalIndex& index,
    const LexicalSegmentEncodingLimits& limits
) {
    std::map<TokenId, std::vector<FlatLexicalPostingSegment>> received;
    for(const auto& row : state.segments) {
        received[row.key.token_id].push_back(row.segment);
        const auto witness = state.witnesses.find(row.key.token_id);
        if(witness == state.witnesses.end()
            || row.membership_witness != witness->second) {
            throw std::runtime_error("segmented lexical membership witness mismatch");
        }
    }
    for(auto& entry : received) {
        std::sort(entry.second.begin(), entry.second.end(),
            [](const auto& lhs, const auto& rhs) {
                return lhs.segment_id < rhs.segment_id;
            });
    }

    const auto dictionary = index.token_dictionary_for_storage();
    std::size_t active_token_count = 0;
    for(const auto& entry : dictionary) {
        const auto token_id = entry.second;
        const auto expected_postings = index.postings(token_id);
        if(expected_postings.empty()) continue;
        ++active_token_count;
        const auto actual = received.find(token_id);
        const auto witness = state.witnesses.find(token_id);
        const auto stats = state.token_stats.find(token_id);
        if(actual == received.end() || witness == state.witnesses.end()
            || stats == state.token_stats.end()) {
            throw std::runtime_error("segmented lexical derived row is missing");
        }
        FlatLexicalPostingPartitionExpectation expectation{
            state.snapshot.statistics_epoch,
            state.snapshot.statistics_generation,
            state.snapshot.posting_layout_version,
            token_id,
            actual->second.size(),
            expected_postings.size(),
            limits.max_postings_per_segment,
            expected_postings
        };
        const auto reconstructed = reconstruct_flat_lexical_posting_partition(
            actual->second, expectation);
        if(!reconstructed.complete()
            || !lexical_posting_membership_witness_matches(
                reconstructed.postings, witness->second,
                state.snapshot.posting_layout_version)) {
            throw std::runtime_error("segmented lexical partition failed integrity admission");
        }
        const auto expected_stats = index.find_token_stats(token_id);
        if(!expected_stats || expected_stats->document_frequency
                != stats->second.document_frequency
            || expected_stats->collection_frequency
                != stats->second.collection_frequency) {
            throw std::runtime_error("segmented lexical token statistics mismatch");
        }
    }
    if(received.size() != active_token_count
        || state.witnesses.size() != active_token_count
        || state.token_stats.size() != active_token_count
        || state.collection_stats.unit_count != index.collection_stats().unit_count
        || state.collection_stats.total_token_count
            != index.collection_stats().total_token_count) {
        throw std::runtime_error("segmented lexical collection rows mismatch");
    }
}

LexicalReadFrontier make_frontier(const StoredState& state) {
    LexicalReadFrontier frontier;
    frontier.publication_sequence = state.sequence;
    frontier.active_snapshot = state.snapshot;
    frontier.index_descriptor = state.descriptor;
    frontier.consistency = LexicalReadConsistency::PinnedSnapshot;
    if(!is_valid_lexical_read_frontier(frontier)) {
        throw std::runtime_error("segmented lexical frontier failed admission");
    }
    return frontier;
}

} // namespace

class MdbxSegmentedLexicalIndex::Impl final {
public:
    explicit Impl(MdbxSegmentedLexicalIndexOptions options)
        : descriptor(std::move(options.descriptor)),
          context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(
                                      std::move(options.path),
                                      options.relative_to_exe,
                                      options.max_dbs)),
          table(context->connection(), sanitize_table_part(options.table_prefix)
              + "_lexical_posting_segments"),
          prefix(scope_prefix(descriptor)),
          limits(options.encoding_limits) {
        if(!is_valid_lexical_index_descriptor(descriptor)
            || descriptor.ref.projection_kind != ProjectionKind::Original
            || descriptor.supported_posting_layout_versions.size() != 1U
            || descriptor.supported_posting_layout_versions.front()
                != kLexicalSegmentLayoutVersion) {
            throw std::invalid_argument("invalid segmented lexical descriptor");
        }
    }

    template<typename Transaction>
    StoredState read_state(Transaction& txn, const bool validate_rows = true) const {
        StoredState state;
        state.descriptor = descriptor;
        state.snapshot.statistics_epoch = descriptor.ref.statistics_epoch;
        state.snapshot.statistics_generation = 1;
        state.snapshot.posting_layout_version = kLexicalSegmentLayoutVersion;
        state.sequence = LexicalPublicationSequence{1};
        const auto from = prefix;
        auto to = prefix;
        to.push_back(static_cast<char>(0xff));
        std::size_t namespace_rows = 0;
        std::size_t total_unit_tokens = 0;
        std::size_t decoded_value_bytes = 0;
        std::size_t decoded_postings = 0;
        table.for_each_range(from, to,
            [&state, this, &namespace_rows, &total_unit_tokens,
                &decoded_value_bytes, &decoded_postings](
                const std::string& key, const std::string& value) {
                state.has_any_rows = true;
                if(++namespace_rows > limits.max_namespace_rows) {
                    throw std::runtime_error("segmented lexical namespace row limit exceeded");
                }
                if(value.size() > limits.max_decoded_value_bytes
                    || decoded_value_bytes > limits.max_decoded_value_bytes - value.size()) {
                    throw std::runtime_error("segmented lexical decoded byte budget exceeded");
                }
                decoded_value_bytes += value.size();
                const auto marker = key.substr(prefix.size());
                if(marker == marker_prefix(kDescriptor)) {
                    state.descriptor = deserialize_descriptor(value);
                    state.has_descriptor = true;
                } else if(marker == marker_prefix(kSnapshot)) {
                    state.snapshot = deserialize_snapshot(value);
                    state.has_snapshot = true;
                } else if(marker == marker_prefix(kSequence)) {
                    state.sequence = LexicalPublicationSequence{deserialize_u64(value)};
                    state.has_sequence = true;
                } else if(marker == marker_prefix(kHighWater)) {
                    state.next_token_id = TokenId{deserialize_u64(value)};
                    state.has_high_water = true;
                } else {
                    std::string_view suffix;
                    if(extract_key_suffix(marker, kDictionary, suffix)) {
                    if(state.dictionary.size() >= limits.max_dictionary_entries) {
                        throw std::runtime_error("segmented lexical dictionary limit exceeded");
                    }
                    Reader reader{value};
                    std::string token;
                    std::uint64_t token_id = 0;
                    if(!reader.string(token, limits.max_token_bytes)
                        || !reader.u64(token_id)
                        || reader.remaining() != 0) {
                        throw std::runtime_error("segmented lexical dictionary decode failed");
                    }
                    if(hex_component(token) != suffix || token_id == 0U) {
                        throw std::runtime_error("segmented lexical dictionary physical key mismatch");
                    }
                    state.dictionary.emplace_back(token, TokenId{token_id});
                    } else if(extract_key_suffix(marker, kUnit, suffix)) {
                    if(state.units.size() >= limits.max_units
                        || value.size() > limits.max_row_bytes) {
                        throw std::runtime_error("segmented lexical unit limit exceeded");
                    }
                    auto unit = deserialize_unit(value);
                    total_unit_tokens += unit.normalized_tokens.size();
                    if(total_unit_tokens > limits.max_total_unit_tokens) {
                        throw std::runtime_error("segmented lexical unit token limit exceeded");
                    }
                    std::uint64_t physical_id = 0;
                    if(!parse_canonical_u64(suffix, physical_id)
                        || physical_id != unit.unit_id.value()
                        || unit.scope_id != descriptor.ref.scope_id
                        || unit.projection_kind != descriptor.ref.projection_kind) {
                        throw std::runtime_error("segmented lexical unit physical key mismatch");
                    }
                    state.units.push_back(std::move(unit));
                    } else if(extract_key_suffix(marker, kSegment, suffix)) {
                    if(state.segments.size() >= limits.max_segments) {
                        throw std::runtime_error("segmented lexical segment limit exceeded");
                    }
                    const auto decoded = decode_lexical_segment_row(value, limits);
                    if(!decoded.complete()) {
                        throw std::runtime_error("segmented lexical segment row rejected");
                    }
                    TokenId key_token;
                    std::uint32_t key_segment = 0;
                    if(!parse_segment_suffix(marker, key_token, key_segment)
                        || decoded.row.key.token_id != key_token
                        || decoded.row.key.segment_id != key_segment) {
                        throw std::runtime_error("segmented lexical physical key mismatch");
                    }
                    if(decoded_postings > limits.max_total_postings
                        || decoded_postings > limits.max_decoded_postings
                        || decoded.row.segment.postings.size()
                            > limits.max_total_postings - std::min(
                                decoded_postings, limits.max_total_postings)
                        || decoded.row.segment.postings.size()
                            > limits.max_decoded_postings - std::min(
                                decoded_postings, limits.max_decoded_postings)) {
                        throw std::runtime_error("segmented lexical decoded postings budget exceeded");
                    }
                    decoded_postings += decoded.row.segment.postings.size();
                    state.segments.push_back(decoded.row);
                    } else if(extract_key_suffix(marker, kTokenStats, suffix)) {
                    if(state.token_stats.size() >= limits.max_token_stats) {
                        throw std::runtime_error("segmented lexical token statistics limit exceeded");
                    }
                    std::uint64_t token_id = 0;
                    if(!parse_canonical_u64(suffix, token_id) || token_id == 0U) {
                        throw std::runtime_error("segmented lexical token stats key failed");
                    }
                    if(!state.token_stats.emplace(
                        TokenId{token_id},
                        deserialize_token_stats(value, TokenId{token_id})).second) {
                        throw std::runtime_error("segmented lexical duplicate token statistics");
                    }
                    } else if(marker == marker_prefix(kCollectionStats)) {
                    state.collection_stats = deserialize_collection_stats(value);
                    state.has_collection_stats = true;
                    } else if(extract_key_suffix(marker, kWitness, suffix)) {
                    if(state.witnesses.size() >= limits.max_witnesses) {
                        throw std::runtime_error("segmented lexical witness limit exceeded");
                    }
                    if(value.size() != 32U) {
                        throw std::runtime_error("segmented lexical witness failed admission");
                    }
                    std::uint64_t token_id = 0;
                    if(!parse_canonical_u64(suffix, token_id) || token_id == 0U) {
                        throw std::runtime_error("segmented lexical witness key failed");
                    }
                    std::array<std::uint8_t, 32> witness{};
                    std::memcpy(witness.data(), value.data(), witness.size());
                    if(!state.witnesses.emplace(TokenId{token_id}, witness).second) {
                        throw std::runtime_error("segmented lexical duplicate witness");
                    }
                    } else {
                    throw std::runtime_error("segmented lexical unknown row kind");
                    }
                }
                return true;
            }, txn);

        std::sort(state.dictionary.begin(), state.dictionary.end(),
            [](const auto& lhs, const auto& rhs) { return lhs.first < rhs.first; });

        for(const auto& row : state.segments) {
            if(row.key.scope_id != descriptor.ref.scope_id
                || row.key.projection_kind != descriptor.ref.projection_kind
                || row.key.statistics_epoch != state.snapshot.statistics_epoch
                || row.key.statistics_generation != state.snapshot.statistics_generation
                || row.key.posting_layout_version != state.snapshot.posting_layout_version) {
                throw std::runtime_error("segmented lexical physical key mismatch");
            }
        }

        if(!state.has_descriptor) {
            if(state.has_any_rows) {
                throw std::runtime_error("segmented lexical descriptor missing");
            }
            return state;
        }
        if(!state.has_snapshot || !state.has_sequence || !state.has_high_water
            || !state.has_collection_stats) {
            throw std::runtime_error("segmented lexical required metadata missing");
        }
        if(!same_descriptor(descriptor, state.descriptor)
            || state.snapshot.statistics_epoch != descriptor.ref.statistics_epoch
            || state.snapshot.posting_layout_version != kLexicalSegmentLayoutVersion
            || state.sequence.empty()) {
            throw std::runtime_error("segmented lexical descriptor or snapshot mismatch");
        }
        if(validate_rows) {
            validate_derived_rows(state, rebuild_index(state), limits);
        }
        return state;
    }

    std::string dictionary_value(const std::pair<std::string, TokenId>& entry) const {
        std::string bytes;
        append_string(bytes, entry.first);
        append_u64(bytes, entry.second.value());
        return bytes;
    }

    template<typename Transaction>
    void clear_rows(Transaction& txn) const {
        const auto from = prefix;
        auto to = prefix;
        to.push_back(static_cast<char>(0xff));
        std::vector<std::string> keys;
        table.for_each_range(from, to,
            [&keys](const std::string& key, const std::string&) {
                keys.push_back(key);
                return true;
            }, txn);
        for(const auto& key : keys) table.erase(key, txn);
    }

    template<typename Transaction>
    void write_state(
        const StoredState& state,
        const FlatLexicalIndex& index,
        Transaction& txn
    ) const {
        clear_rows(txn);
        table.insert_or_assign(
            row_key(prefix, kDescriptor), serialize_descriptor(state.descriptor), txn);
        table.insert_or_assign(
            row_key(prefix, kSnapshot), serialize_snapshot(state.snapshot), txn);
        table.insert_or_assign(
            row_key(prefix, kSequence), serialize_u64(state.sequence.value()), txn);
        table.insert_or_assign(
            row_key(prefix, kHighWater), serialize_u64(state.next_token_id.value()), txn);
        for(const auto& entry : state.dictionary) {
            table.insert_or_assign(
                row_key(prefix, kDictionary, hex_component(entry.first)),
                dictionary_value(entry), txn);
        }
        for(const auto& unit : state.units) {
            table.insert_or_assign(
                row_key(prefix, kUnit, std::to_string(unit.unit_id.value())),
                serialize_unit(unit), txn);
        }
        const auto dictionary = index.token_dictionary_for_storage();
        for(const auto& entry : dictionary) {
            const auto token_id = entry.second;
            const auto postings = index.postings(token_id);
            if(postings.empty()) continue;
            const auto segments = index.posting_segments(
                token_id, limits.max_postings_per_segment);
            const auto witness = lexical_posting_membership_witness(
                postings, kLexicalSegmentLayoutVersion);
            const auto stats = index.find_token_stats(token_id);
            if(!stats || segments.empty()) {
                throw std::runtime_error("segmented lexical derived rows missing");
            }
            table.insert_or_assign(
                row_key(prefix, kTokenStats, std::to_string(token_id.value())),
                serialize_token_stats(*stats), txn);
            table.insert_or_assign(
                row_key(prefix, kWitness, std::to_string(token_id.value())),
                std::string(reinterpret_cast<const char*>(witness.data()), witness.size()),
                txn);
            for(const auto& segment : segments) {
                LexicalSegmentRow row;
                row.key.scope_id = descriptor.ref.scope_id;
                row.key.projection_kind = descriptor.ref.projection_kind;
                row.key.statistics_epoch = segment.statistics_epoch;
                row.key.statistics_generation = segment.statistics_generation;
                row.key.token_id = segment.token_id;
                row.key.segment_id = segment.segment_id;
                row.key.posting_layout_version = segment.posting_layout_version;
                row.membership_witness = witness;
                row.segment = segment;
                const auto encoded = encode_lexical_segment_row(row, limits);
                if(!encoded.complete()) {
                    throw std::runtime_error("segmented lexical row encoding failed");
                }
                table.insert_or_assign(
                    row_key(prefix, kSegment,
                        std::to_string(token_id.value()) + ":"
                        + std::to_string(segment.segment_id)),
                    encoded.bytes, txn);
            }
        }
        table.insert_or_assign(
            row_key(prefix, kCollectionStats),
            serialize_collection_stats(index.collection_stats()), txn);
    }

    LexicalIndexDescriptor descriptor;
    std::shared_ptr<MdbxStorageContext> context;
    mutable mdbxc::KeyValueTable<std::string, std::string> table;
    std::string prefix;
    LexicalSegmentEncodingLimits limits;
    MdbxSegmentedLexicalIndexFaultPoint failure =
        MdbxSegmentedLexicalIndexFaultPoint::None;
};

MdbxSegmentedLexicalIndex::MdbxSegmentedLexicalIndex(
    MdbxSegmentedLexicalIndexOptions options
) : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxSegmentedLexicalIndex::~MdbxSegmentedLexicalIndex() = default;

const LexicalIndexDescriptor& MdbxSegmentedLexicalIndex::descriptor() const noexcept {
    return m_impl->descriptor;
}

LexicalActiveSnapshot MdbxSegmentedLexicalIndex::active_snapshot() const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    return m_impl->read_state(txn).snapshot;
}

LexicalPublicationSequence MdbxSegmentedLexicalIndex::publication_sequence() const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    return m_impl->read_state(txn).sequence;
}

void MdbxSegmentedLexicalIndex::upsert(
    FlatLexicalUnitInput input,
    std::optional<FlatLexicalExpectedState> expected_current
) {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    auto state = m_impl->read_state(txn, false);
    auto index = rebuild_index(state);
    if(state.has_descriptor) {
        validate_derived_rows(state, index, m_impl->limits);
    }
    index.upsert(input, expected_current);
    bool changed = true;
    for(const auto& unit : state.units) {
        if(unit.unit_id == input.unit_id) {
            changed = unit.normalized_tokens != input.normalized_tokens
                || unit.resource_id != input.resource_id
                || unit.resource_generation != input.resource_generation
                || unit.unit_revision != input.unit_revision
                || unit.projection_version.unit_revision_at_build
                    != input.projection_version.unit_revision_at_build
                || unit.projection_version.projection_revision
                    != input.projection_version.projection_revision
                || unit.projection_version.derivation_generation
                    != input.projection_version.derivation_generation
                || unit.projection_version.derivation_fingerprint
                    != input.projection_version.derivation_fingerprint;
            break;
        }
    }
    if(!changed) return;
    const auto before_generation = state.snapshot.statistics_generation;
    const auto before_sequence = state.sequence.value();
    auto existing = std::find_if(state.units.begin(), state.units.end(),
        [&input](const auto& unit) { return unit.unit_id == input.unit_id; });
    if(existing == state.units.end()) state.units.push_back(input);
    else *existing = input;
    if(before_generation == std::numeric_limits<std::uint64_t>::max()
        || before_sequence == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("segmented lexical generation exhausted");
    }
    state.snapshot.statistics_generation = before_generation + 1U;
    state.sequence = LexicalPublicationSequence{before_sequence + 1U};
    index.restore_active_snapshot_for_storage(state.snapshot);
    state.dictionary = index.token_dictionary_for_storage();
    state.next_token_id = index.next_token_id_for_storage();
    m_impl->write_state(state, index, txn);
    if(m_impl->failure == MdbxSegmentedLexicalIndexFaultPoint::BeforeCommit
        || m_impl->failure == MdbxSegmentedLexicalIndexFaultPoint::AfterWriteBeforeCommit) {
        const auto point = m_impl->failure;
        m_impl->failure = MdbxSegmentedLexicalIndexFaultPoint::None;
        throw std::runtime_error(point == MdbxSegmentedLexicalIndexFaultPoint::BeforeCommit
            ? "injected segmented lexical interruption"
            : "injected segmented lexical interruption after write");
    }
    txn.commit();
}

bool MdbxSegmentedLexicalIndex::erase(const KnowledgeUnitId& unit_id) {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    auto state = m_impl->read_state(txn, false);
    auto index = rebuild_index(state);
    if(state.has_descriptor) {
        validate_derived_rows(state, index, m_impl->limits);
    }
    if(!index.erase(unit_id)) return false;
    state.units.erase(std::remove_if(state.units.begin(), state.units.end(),
        [unit_id](const auto& unit) { return unit.unit_id == unit_id; }), state.units.end());
    if(state.snapshot.statistics_generation == std::numeric_limits<std::uint64_t>::max()
        || state.sequence.value() == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("segmented lexical generation exhausted");
    }
    ++state.snapshot.statistics_generation;
    state.sequence = LexicalPublicationSequence{state.sequence.value() + 1U};
    index.restore_active_snapshot_for_storage(state.snapshot);
    state.dictionary = index.token_dictionary_for_storage();
    state.next_token_id = index.next_token_id_for_storage();
    m_impl->write_state(state, index, txn);
    if(m_impl->failure == MdbxSegmentedLexicalIndexFaultPoint::AfterWriteBeforeCommit) {
        m_impl->failure = MdbxSegmentedLexicalIndexFaultPoint::None;
        throw std::runtime_error("injected segmented lexical interruption after write");
    }
    txn.commit();
    return true;
}

void MdbxSegmentedLexicalIndex::clear() {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    auto state = m_impl->read_state(txn, false);
    if(state.units.empty()) return;
    auto index = rebuild_index(state);
    if(state.has_descriptor) {
        validate_derived_rows(state, index, m_impl->limits);
    }
    state.units.clear();
    index.clear();
    if(state.snapshot.statistics_generation == std::numeric_limits<std::uint64_t>::max()
        || state.sequence.value() == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("segmented lexical generation exhausted");
    }
    ++state.snapshot.statistics_generation;
    state.sequence = LexicalPublicationSequence{state.sequence.value() + 1U};
    index.restore_active_snapshot_for_storage(state.snapshot);
    m_impl->write_state(state, index, txn);
    if(m_impl->failure == MdbxSegmentedLexicalIndexFaultPoint::AfterWriteBeforeCommit) {
        m_impl->failure = MdbxSegmentedLexicalIndexFaultPoint::None;
        throw std::runtime_error("injected segmented lexical interruption after write");
    }
    txn.commit();
}

MdbxSegmentedLexicalIndexSnapshot MdbxSegmentedLexicalIndex::read_snapshot() const {
    auto transaction = std::make_shared<mdbxc::Transaction>(
        m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY));
    const auto state = m_impl->read_state(*transaction);
    auto index = rebuild_index(state);
    auto frontier = make_frontier(state);
    std::shared_ptr<void> owner{std::move(transaction)};
    return MdbxSegmentedLexicalIndexSnapshot{
        std::move(index), std::move(frontier), std::move(owner)};
}

void MdbxSegmentedLexicalIndex::set_failure_injection_for_testing(
    const MdbxSegmentedLexicalIndexFaultPoint point
) noexcept {
    m_impl->failure = point;
}

} // namespace agent_memory

#endif
