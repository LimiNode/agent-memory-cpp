#include <agent_memory/lexical/LexicalSegmentEncoding.hpp>

#include <array>
#include <iostream>
#include <limits>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

int fail(const std::string_view message) {
    std::cerr << message << '\n';
    return 1;
}

agent_memory::LexicalStatisticsEpoch make_epoch() {
    std::array<std::uint8_t, 16> bytes{};
    bytes[0] = 7;
    bytes[15] = 42;
    return agent_memory::LexicalStatisticsEpoch{bytes};
}

agent_memory::LexicalIndexDescriptor make_descriptor() {
    return agent_memory::LexicalIndexDescriptor{
        {
            agent_memory::ScopeId{"scope:segment"},
            agent_memory::ProjectionKind::Original,
            make_epoch()
        },
        "agent_memory.search_projection.original",
        1,
        "standard-tokenizer-v1",
        "lowercase-ascii-v1",
        {agent_memory::kLexicalSegmentLayoutVersion}
    };
}

agent_memory::FlatLexicalUnitInput make_input(
    const std::uint64_t unit_id,
    std::vector<std::string> tokens
) {
    return agent_memory::FlatLexicalUnitInput{
        agent_memory::ScopeId{"scope:segment"},
        agent_memory::KnowledgeUnitId{unit_id},
        agent_memory::ProjectionKind::Original,
        agent_memory::ResourceId{"resource:segment"},
        3,
        unit_id + 10,
        agent_memory::ProjectionVersionRef{
            unit_id + 10,
            unit_id + 10,
            unit_id + 10,
            "original-v1"
        },
        std::move(tokens)
    };
}

agent_memory::LexicalSegmentRow make_row(
    const agent_memory::FlatLexicalIndex& index,
    const agent_memory::TokenId& token_id
) {
    const auto segments = index.posting_segments(token_id, 2);
    const auto& segment = segments.front();
    agent_memory::LexicalSegmentRow row;
    row.key.scope_id = agent_memory::ScopeId{"scope:segment"};
    row.key.projection_kind = agent_memory::ProjectionKind::Original;
    row.key.statistics_epoch = segment.statistics_epoch;
    row.key.statistics_generation = segment.statistics_generation;
    row.key.token_id = segment.token_id;
    row.key.segment_id = segment.segment_id;
    row.key.posting_layout_version = segment.posting_layout_version;
    row.membership_witness = agent_memory::lexical_posting_membership_witness(
        index.postings(token_id));
    row.segment = segment;
    return row;
}

void write_u32(std::string& bytes, const std::size_t offset, std::uint32_t value) {
    for(unsigned shift = 0; shift != 32U; shift += 8U) {
        bytes[offset + shift / 8U] =
            static_cast<char>((value >> shift) & 0xffU);
    }
}

int test_roundtrip_and_witness() {
    agent_memory::FlatLexicalIndex index{make_descriptor()};
    index.upsert(make_input(1, {"segment", "segment"}));
    index.upsert(make_input(2, {"segment"}));
    index.upsert(make_input(3, {"segment"}));
    const auto token_id = index.find_token_id("segment");
    if(!token_id) {
        return fail("encoding fixture must allocate token");
    }
    const auto row = make_row(index, *token_id);
    const auto encoded = agent_memory::encode_lexical_segment_row(row);
    if(!encoded.complete() || encoded.bytes.empty()) {
        return fail("segment row must encode");
    }
    const auto decoded = agent_memory::decode_lexical_segment_row(encoded.bytes);
    if(!decoded.complete()
        || decoded.row.key.scope_id != row.key.scope_id
        || decoded.row.key.token_id != row.key.token_id
        || decoded.row.segment.postings.size() != row.segment.postings.size()) {
        return fail("segment row must round-trip physical identity and postings");
    }
    const auto reencoded = agent_memory::encode_lexical_segment_row(decoded.row);
    if(!reencoded.complete() || reencoded.bytes != encoded.bytes) {
        return fail("segment encoding must be byte stable");
    }
    if(!agent_memory::lexical_posting_membership_witness_matches(
        index.postings(*token_id), row.membership_witness)) {
        return fail("membership witness must match the complete posting sequence");
    }
    auto mutated_postings = index.postings(*token_id);
    ++mutated_postings[0].term_frequency;
    if(agent_memory::lexical_posting_membership_witness_matches(
        mutated_postings, row.membership_witness)) {
        return fail("posting mutation must change membership witness");
    }
    return 0;
}

int test_negative_encoding_admission() {
    agent_memory::FlatLexicalIndex index{make_descriptor()};
    index.upsert(make_input(1, {"segment", "segment"}));
    index.upsert(make_input(2, {"segment"}));
    const auto token_id = index.find_token_id("segment");
    if(!token_id) {
        return fail("negative fixture must allocate token");
    }
    const auto row = make_row(index, *token_id);
    const auto encoded = agent_memory::encode_lexical_segment_row(row);
    if(!encoded.complete()) {
        return fail("negative fixture must encode first");
    }

    auto truncated = encoded.bytes;
    truncated.resize(truncated.size() - 1U);
    if(agent_memory::decode_lexical_segment_row(truncated).status
        != agent_memory::LexicalSegmentEncodingStatus::Truncated) {
        return fail("truncated row must fail closed");
    }
    auto trailing = encoded.bytes;
    trailing.push_back('\x01');
    if(agent_memory::decode_lexical_segment_row(trailing).status
        != agent_memory::LexicalSegmentEncodingStatus::TrailingBytes) {
        return fail("trailing bytes must fail closed");
    }
    auto bad_checksum = encoded.bytes;
    bad_checksum.back() ^= static_cast<char>(0x80);
    if(agent_memory::decode_lexical_segment_row(bad_checksum).status
        != agent_memory::LexicalSegmentEncodingStatus::ChecksumMismatch) {
        return fail("checksum mutation must fail closed");
    }
    const auto first_posting = 4U + 4U + 4U + 4U
        + row.key.scope_id.value().size() + 4U + 16U + 8U + 8U + 4U
        + 32U + 4U;
    const auto resource_content = first_posting + 8U + 8U + 4U;
    const auto after_resource = resource_content + row.segment.postings[0].resource_id.value().size();
    const auto unit_revision = after_resource + 8U;
    const auto projection_revision = unit_revision + 8U;
    const auto derivation_generation = projection_revision + 8U;
    const auto fingerprint_content = derivation_generation + 8U + 4U;
    const auto term_frequency = fingerprint_content
        + row.segment.postings[0].projection_version.derivation_fingerprint.size();
    for(const auto offset : std::array<std::size_t, 9>{
        first_posting,
        first_posting + 8U,
        resource_content,
        after_resource,
        unit_revision,
        projection_revision,
        derivation_generation,
        fingerprint_content,
        term_frequency
    }) {
        auto mutated_field = encoded.bytes;
        mutated_field[offset] ^= static_cast<char>(0x01);
        if(agent_memory::decode_lexical_segment_row(mutated_field).status
            != agent_memory::LexicalSegmentEncodingStatus::ChecksumMismatch) {
            return fail("mutation of a significant posting field must fail checksum admission");
        }
    }
    auto unsupported_encoding = encoded.bytes;
    write_u32(unsupported_encoding, 4, 2);
    if(agent_memory::decode_lexical_segment_row(unsupported_encoding).status
        != agent_memory::LexicalSegmentEncodingStatus::UnsupportedEncodingVersion) {
        return fail("unknown encoding version must fail closed");
    }
    auto unsupported_layout = encoded.bytes;
    write_u32(unsupported_layout, 8, 999);
    if(agent_memory::decode_lexical_segment_row(unsupported_layout).status
        != agent_memory::LexicalSegmentEncodingStatus::UnsupportedLayoutVersion) {
        return fail("unknown layout version must fail closed");
    }
    auto malformed_length = encoded.bytes;
    write_u32(malformed_length, 12, std::numeric_limits<std::uint32_t>::max());
    if(agent_memory::decode_lexical_segment_row(malformed_length).status
        != agent_memory::LexicalSegmentEncodingStatus::LimitExceeded) {
        return fail("oversized length must fail closed");
    }
    agent_memory::LexicalSegmentEncodingLimits tiny;
    tiny.max_row_bytes = encoded.bytes.size() - 1U;
    if(agent_memory::decode_lexical_segment_row(encoded.bytes, tiny).status
        != agent_memory::LexicalSegmentEncodingStatus::LimitExceeded) {
        return fail("row byte limit must be enforced");
    }
    tiny = {};
    tiny.max_postings_per_segment = 1;
    if(agent_memory::decode_lexical_segment_row(encoded.bytes, tiny).status
        != agent_memory::LexicalSegmentEncodingStatus::LimitExceeded) {
        return fail("posting limit must be enforced");
    }
    auto invalid_row = row;
    invalid_row.segment.postings[0].term_frequency = 0;
    if(agent_memory::encode_lexical_segment_row(invalid_row).status
        != agent_memory::LexicalSegmentEncodingStatus::InvalidInput) {
        return fail("invalid posting must not encode");
    }
    invalid_row = row;
    invalid_row.key.posting_layout_version = 999;
    if(agent_memory::encode_lexical_segment_row(invalid_row).status
        != agent_memory::LexicalSegmentEncodingStatus::UnsupportedLayoutVersion) {
        return fail("unknown layout must not encode");
    }
    return 0;
}

} // namespace

int main() {
    return test_roundtrip_and_witness() + test_negative_encoding_admission();
}
