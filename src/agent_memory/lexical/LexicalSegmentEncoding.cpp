#include "LexicalSegmentEncoding.hpp"

#include <agent_memory/domain/Sha256.hpp>

#include <algorithm>
#include <cstring>
#include <limits>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view kMagic = "ALSR";
constexpr std::string_view kWitnessDomain =
    "agent_memory.lexical.posting-membership.v1\0";
constexpr std::string_view kChecksumDomain =
    "agent_memory.lexical.segment-row-checksum.v1\0";

bool is_zero_digest(const std::array<std::uint8_t, 32>& digest) noexcept {
    return std::all_of(digest.begin(), digest.end(),
        [](const auto byte) { return byte == 0; });
}

void append_u32_unbounded(std::string& bytes, const std::uint32_t value) {
    for(unsigned shift = 0; shift != 32U; shift += 8U) {
        bytes.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
}

void append_u64_unbounded(std::string& bytes, const std::uint64_t value) {
    for(unsigned shift = 0; shift != 64U; shift += 8U) {
        bytes.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
}

void append_string_unbounded(std::string& bytes, std::string_view value) {
    append_u32_unbounded(bytes, static_cast<std::uint32_t>(value.size()));
    bytes.append(value.data(), value.size());
}

std::array<std::uint8_t, 32> checksum(std::string_view bytes) noexcept {
    std::string input;
    input.reserve(kChecksumDomain.size() + bytes.size());
    input.append(kChecksumDomain.data(), kChecksumDomain.size());
    input.append(bytes.data(), bytes.size());
    return sha256(input);
}

void append_posting_for_witness(
    std::string& bytes,
    const FlatLexicalPosting& posting
) {
    append_u64_unbounded(bytes, posting.token_id.value());
    append_u64_unbounded(bytes, posting.unit_id.value());
    append_string_unbounded(bytes, posting.resource_id.value());
    append_u64_unbounded(bytes, posting.resource_generation);
    append_u64_unbounded(bytes, posting.projection_version.unit_revision_at_build);
    append_u64_unbounded(bytes, posting.projection_version.projection_revision);
    append_u64_unbounded(bytes, posting.projection_version.derivation_generation);
    append_string_unbounded(
        bytes,
        posting.projection_version.derivation_fingerprint
    );
    append_u32_unbounded(bytes, posting.term_frequency);
}

class BoundedWriter final {
public:
    explicit BoundedWriter(const std::size_t limit) : m_limit(limit) {}

    bool raw(const void* data, const std::size_t size) {
        if(size > m_limit - std::min(m_limit, m_bytes.size())) {
            m_status = LexicalSegmentEncodingStatus::LimitExceeded;
            return false;
        }
        m_bytes.append(static_cast<const char*>(data), size);
        return true;
    }

    bool u32(const std::uint32_t value) {
        char data[4];
        for(unsigned shift = 0; shift != 32U; shift += 8U) {
            data[shift / 8U] = static_cast<char>((value >> shift) & 0xffU);
        }
        return raw(data, sizeof(data));
    }

    bool u64(const std::uint64_t value) {
        char data[8];
        for(unsigned shift = 0; shift != 64U; shift += 8U) {
            data[shift / 8U] = static_cast<char>((value >> shift) & 0xffU);
        }
        return raw(data, sizeof(data));
    }

    bool string(std::string_view value) {
        return value.size() <= std::numeric_limits<std::uint32_t>::max()
            && u32(static_cast<std::uint32_t>(value.size()))
            && raw(value.data(), value.size());
    }

    [[nodiscard]] LexicalSegmentEncodingStatus status() const noexcept {
        return m_status;
    }

    [[nodiscard]] std::string&& take() noexcept {
        return std::move(m_bytes);
    }

private:
    std::size_t m_limit;
    std::string m_bytes;
    LexicalSegmentEncodingStatus m_status =
        LexicalSegmentEncodingStatus::Complete;
};

class Reader final {
public:
    explicit Reader(const std::string_view bytes) : m_bytes(bytes) {}

    bool raw(void* data, const std::size_t size) {
        if(size > remaining()) {
            m_status = LexicalSegmentEncodingStatus::Truncated;
            return false;
        }
        std::memcpy(data, m_bytes.data() + m_position, size);
        m_position += size;
        return true;
    }

    bool u32(std::uint32_t& value) {
        unsigned char data[4];
        if(!raw(data, sizeof(data))) {
            return false;
        }
        value = static_cast<std::uint32_t>(data[0])
            | (static_cast<std::uint32_t>(data[1]) << 8U)
            | (static_cast<std::uint32_t>(data[2]) << 16U)
            | (static_cast<std::uint32_t>(data[3]) << 24U);
        return true;
    }

    bool u64(std::uint64_t& value) {
        unsigned char data[8];
        if(!raw(data, sizeof(data))) {
            return false;
        }
        value = 0;
        for(unsigned index = 0; index != 8U; ++index) {
            value |= static_cast<std::uint64_t>(data[index]) << (index * 8U);
        }
        return true;
    }

    bool string(std::string& value, const std::size_t max_size) {
        std::uint32_t size = 0;
        if(!u32(size)) {
            return false;
        }
        if(size > max_size) {
            m_status = LexicalSegmentEncodingStatus::LimitExceeded;
            return false;
        }
        if(size > remaining()) {
            m_status = LexicalSegmentEncodingStatus::Truncated;
            return false;
        }
        value.assign(m_bytes.data() + m_position, size);
        m_position += size;
        return true;
    }

    [[nodiscard]] std::size_t remaining() const noexcept {
        return m_bytes.size() - m_position;
    }

    [[nodiscard]] std::size_t position() const noexcept {
        return m_position;
    }

    [[nodiscard]] LexicalSegmentEncodingStatus status() const noexcept {
        return m_status;
    }

    void set_status(const LexicalSegmentEncodingStatus status) noexcept {
        m_status = status;
    }

private:
    std::string_view m_bytes;
    std::size_t m_position = 0;
    LexicalSegmentEncodingStatus m_status =
        LexicalSegmentEncodingStatus::Complete;
};

bool valid_limits(const LexicalSegmentEncodingLimits& limits) noexcept {
    return limits.max_segments != 0
        && limits.max_postings_per_segment != 0
        && limits.max_row_bytes != 0
        && limits.max_total_postings != 0
        && limits.max_token_bytes != 0;
}

bool valid_key(
    const LexicalSegmentPhysicalKey& key,
    const LexicalSegmentEncodingLimits& limits
) noexcept {
    return !key.scope_id.empty()
        && is_valid_projection_kind(key.projection_kind)
        && !key.statistics_epoch.empty()
        && key.statistics_generation != 0
        && !key.token_id.empty()
        && key.posting_layout_version == kLexicalSegmentLayoutVersion
        && key.segment_id < limits.max_segments
        && key.scope_id.value().size() <= limits.max_token_bytes;
}

bool valid_row_identity(const LexicalSegmentRow& row) noexcept {
    const auto& key = row.key;
    const auto& segment = row.segment;
    return segment.statistics_epoch == key.statistics_epoch
        && segment.statistics_generation == key.statistics_generation
        && segment.posting_layout_version == key.posting_layout_version
        && segment.token_id == key.token_id
        && segment.segment_id == key.segment_id;
}

bool valid_strings(
    const LexicalSegmentRow& row,
    const LexicalSegmentEncodingLimits& limits
) noexcept {
    for(const auto& posting : row.segment.postings) {
        if(posting.resource_id.value().size() > limits.max_token_bytes
            || posting.projection_version.derivation_fingerprint.size()
                > limits.max_token_bytes) {
            return false;
        }
    }
    return true;
}

} // namespace

std::array<std::uint8_t, 32> lexical_posting_membership_witness(
    const std::vector<FlatLexicalPosting>& postings,
    const std::uint32_t posting_layout_version
) noexcept {
    std::string bytes;
    bytes.reserve(kWitnessDomain.size() + 12U + postings.size() * 80U);
    bytes.append(kWitnessDomain.data(), kWitnessDomain.size());
    append_u32_unbounded(bytes, posting_layout_version);
    append_u32_unbounded(
        bytes,
        postings.size() > std::numeric_limits<std::uint32_t>::max()
            ? std::numeric_limits<std::uint32_t>::max()
            : static_cast<std::uint32_t>(postings.size())
    );
    for(const auto& posting : postings) {
        append_posting_for_witness(bytes, posting);
    }
    return sha256(bytes);
}

bool lexical_posting_membership_witness_matches(
    const std::vector<FlatLexicalPosting>& postings,
    const std::array<std::uint8_t, 32>& expected,
    const std::uint32_t posting_layout_version
) noexcept {
    return lexical_posting_membership_witness(postings, posting_layout_version)
        == expected;
}

LexicalSegmentEncodingResult encode_lexical_segment_row(
    const LexicalSegmentRow& row,
    const LexicalSegmentEncodingLimits& limits
) {
    if(!valid_limits(limits)) {
        return {LexicalSegmentEncodingStatus::InvalidInput, {}};
    }
    if(row.key.posting_layout_version != kLexicalSegmentLayoutVersion) {
        return {LexicalSegmentEncodingStatus::UnsupportedLayoutVersion, {}};
    }
    if(!valid_key(row.key, limits)
        || !valid_row_identity(row)
        || !is_valid_flat_lexical_posting_segment(row.segment)
        || row.segment.postings.size() > limits.max_postings_per_segment
        || row.segment.postings.size() > limits.max_total_postings
        || is_zero_digest(row.membership_witness)
        || !valid_strings(row, limits)) {
        return {LexicalSegmentEncodingStatus::InvalidInput, {}};
    }

    BoundedWriter writer{limits.max_row_bytes};
    if(!writer.raw(kMagic.data(), kMagic.size())
        || !writer.u32(kLexicalSegmentEncodingVersion)
        || !writer.u32(row.key.posting_layout_version)
        || !writer.string(row.key.scope_id.value())
        || !writer.u32(static_cast<std::uint32_t>(row.key.projection_kind))
        || !writer.raw(
            row.key.statistics_epoch.bytes().data(),
            row.key.statistics_epoch.bytes().size())
        || !writer.u64(row.key.statistics_generation)
        || !writer.u64(row.key.token_id.value())
        || !writer.u32(row.key.segment_id)
        || !writer.raw(row.membership_witness.data(), row.membership_witness.size())
        || !writer.u32(static_cast<std::uint32_t>(row.segment.postings.size()))) {
        return {writer.status(), {}};
    }
    for(const auto& posting : row.segment.postings) {
        if(!writer.u64(posting.token_id.value())
            || !writer.u64(posting.unit_id.value())
            || !writer.string(posting.resource_id.value())
            || !writer.u64(posting.resource_generation)
            || !writer.u64(posting.projection_version.unit_revision_at_build)
            || !writer.u64(posting.projection_version.projection_revision)
            || !writer.u64(posting.projection_version.derivation_generation)
            || !writer.string(posting.projection_version.derivation_fingerprint)
            || !writer.u32(posting.term_frequency)) {
            return {writer.status(), {}};
        }
    }
    auto bytes = writer.take();
    const auto row_checksum = checksum(bytes);
    if(bytes.size() > limits.max_row_bytes - row_checksum.size()) {
        return {LexicalSegmentEncodingStatus::LimitExceeded, {}};
    }
    bytes.append(
        reinterpret_cast<const char*>(row_checksum.data()), row_checksum.size());
    return {LexicalSegmentEncodingStatus::Complete, std::move(bytes)};
}

LexicalSegmentDecodingResult decode_lexical_segment_row(
    const std::string_view bytes,
    const LexicalSegmentEncodingLimits& limits
) {
    LexicalSegmentDecodingResult result;
    if(!valid_limits(limits) || bytes.size() > limits.max_row_bytes) {
        result.status = limits.max_row_bytes != 0
            && bytes.size() > limits.max_row_bytes
            ? LexicalSegmentEncodingStatus::LimitExceeded
            : LexicalSegmentEncodingStatus::InvalidInput;
        return result;
    }

    Reader reader{bytes};
    char magic[4];
    std::uint32_t encoding_version = 0;
    std::uint32_t layout_version = 0;
    if(!reader.raw(magic, sizeof(magic))) {
        result.status = reader.status();
        return result;
    }
    if(std::string_view{magic, sizeof(magic)} != kMagic) {
        result.status = LexicalSegmentEncodingStatus::InvalidInput;
        return result;
    }
    if(!reader.u32(encoding_version)) {
        result.status = reader.status();
        return result;
    }
    if(encoding_version != kLexicalSegmentEncodingVersion) {
        result.status = LexicalSegmentEncodingStatus::UnsupportedEncodingVersion;
        return result;
    }
    if(!reader.u32(layout_version)) {
        result.status = reader.status();
        return result;
    }
    if(layout_version != kLexicalSegmentLayoutVersion) {
        result.status = LexicalSegmentEncodingStatus::UnsupportedLayoutVersion;
        return result;
    }

    auto& row = result.row;
    row.key.posting_layout_version = layout_version;
    std::string scope;
    if(!reader.string(scope, limits.max_token_bytes)) {
        result.status = reader.status();
        return result;
    }
    row.key.scope_id = ScopeId{std::move(scope)};
    std::uint32_t projection_kind = 0;
    if(!reader.u32(projection_kind)) {
        result.status = reader.status();
        return result;
    }
    row.key.projection_kind = static_cast<ProjectionKind>(projection_kind);
    if(!is_valid_projection_kind(row.key.projection_kind)) {
        result.status = LexicalSegmentEncodingStatus::InvalidInput;
        return result;
    }
    std::array<std::uint8_t, 16> epoch{};
    if(!reader.raw(epoch.data(), epoch.size())
        || !reader.u64(row.key.statistics_generation)) {
        result.status = reader.status();
        return result;
    }
    row.key.statistics_epoch = LexicalStatisticsEpoch{epoch};
    std::uint64_t token_id = 0;
    if(!reader.u64(token_id) || !reader.u32(row.key.segment_id)) {
        result.status = reader.status();
        return result;
    }
    row.key.token_id = TokenId{token_id};
    if(!reader.raw(row.membership_witness.data(), row.membership_witness.size())) {
        result.status = reader.status();
        return result;
    }
    std::uint32_t posting_count = 0;
    if(!reader.u32(posting_count)) {
        result.status = reader.status();
        return result;
    }
    if(posting_count == 0
        || posting_count > limits.max_postings_per_segment
        || posting_count > limits.max_total_postings
        || row.key.segment_id >= limits.max_segments
        || is_zero_digest(row.membership_witness)) {
        result.status = posting_count == 0 || is_zero_digest(row.membership_witness)
            ? LexicalSegmentEncodingStatus::InvalidInput
            : LexicalSegmentEncodingStatus::LimitExceeded;
        return result;
    }

    row.segment.statistics_epoch = row.key.statistics_epoch;
    row.segment.statistics_generation = row.key.statistics_generation;
    row.segment.posting_layout_version = row.key.posting_layout_version;
    row.segment.token_id = row.key.token_id;
    row.segment.segment_id = row.key.segment_id;
    row.segment.postings.reserve(posting_count);
    for(std::uint32_t index = 0; index < posting_count; ++index) {
        FlatLexicalPosting posting;
        std::uint64_t value = 0;
        if(!reader.u64(value)) {
            result.status = reader.status();
            return result;
        }
        posting.token_id = TokenId{value};
        if(!reader.u64(value)) {
            result.status = reader.status();
            return result;
        }
        posting.unit_id = KnowledgeUnitId{value};
        std::string resource_id;
        if(!reader.string(resource_id, limits.max_token_bytes)) {
            result.status = reader.status();
            return result;
        }
        posting.resource_id = ResourceId{std::move(resource_id)};
        if(!reader.u64(posting.resource_generation)
            || !reader.u64(posting.projection_version.unit_revision_at_build)
            || !reader.u64(posting.projection_version.projection_revision)
            || !reader.u64(posting.projection_version.derivation_generation)
            || !reader.string(
                posting.projection_version.derivation_fingerprint,
                limits.max_token_bytes)
            || !reader.u32(posting.term_frequency)) {
            result.status = reader.status();
            return result;
        }
        row.segment.postings.push_back(std::move(posting));
    }

    if(reader.remaining() < 32U) {
        result.status = LexicalSegmentEncodingStatus::Truncated;
        return result;
    }
    const auto checksum_end = reader.position();
    std::array<std::uint8_t, 32> expected_checksum{};
    if(!reader.raw(expected_checksum.data(), expected_checksum.size())) {
        result.status = reader.status();
        return result;
    }
    if(reader.remaining() != 0) {
        result.status = LexicalSegmentEncodingStatus::TrailingBytes;
        return result;
    }
    if(checksum(bytes.substr(0, checksum_end)) != expected_checksum) {
        result.status = LexicalSegmentEncodingStatus::ChecksumMismatch;
        return result;
    }
    if(!valid_key(row.key, limits)
        || !is_valid_flat_lexical_posting_segment(row.segment)
        || !valid_row_identity(row)
        || !valid_strings(row, limits)) {
        result.status = LexicalSegmentEncodingStatus::InvalidInput;
        result.row = {};
        return result;
    }
    result.status = LexicalSegmentEncodingStatus::Complete;
    return result;
}

} // namespace agent_memory
