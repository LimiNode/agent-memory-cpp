#pragma once
#ifndef AGENT_MEMORY_HEADER_LEXICAL_LEXICAL_SEGMENT_ENCODING_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_LEXICAL_LEXICAL_SEGMENT_ENCODING_HPP_INCLUDED

/// \file LexicalSegmentEncoding.hpp
/// \brief Versioned, bounded encoding for physical lexical posting rows.

#include "FlatLexicalIndex.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace agent_memory {

    inline constexpr std::uint32_t kLexicalSegmentEncodingVersion = 1;
    inline constexpr std::uint32_t kLexicalSegmentLayoutVersion = 1;
    /// Partition-local-generation contract used by the incremental adapter.
    inline constexpr std::uint32_t kLexicalIncrementalSegmentLayoutVersion = 2;

    [[nodiscard]] constexpr bool is_supported_lexical_segment_layout(
        const std::uint32_t version
    ) noexcept {
        return version == kLexicalSegmentLayoutVersion
            || version == kLexicalIncrementalSegmentLayoutVersion;
    }

    /// \brief Physical identity of one immutable lexical segment row.
    struct LexicalSegmentPhysicalKey final {
        ScopeId scope_id;
        ProjectionKind projection_kind = ProjectionKind::Original;
        LexicalStatisticsEpoch statistics_epoch;
        std::uint64_t statistics_generation = 0;
        TokenId token_id;
        std::uint32_t segment_id = 0;
        std::uint32_t posting_layout_version = 0;
    };

    /// \brief One physical row and its complete-partition membership witness.
    struct LexicalSegmentRow final {
        LexicalSegmentPhysicalKey key;
        std::array<std::uint8_t, 32> membership_witness{};
        FlatLexicalPostingSegment segment;
    };

    /// \brief Resource bounds applied before encoding or while decoding.
    struct LexicalSegmentEncodingLimits final {
        std::size_t max_segments = 1'000'000U;
        std::size_t max_postings_per_segment = 100'000U;
        std::size_t max_row_bytes = 16U * 1024U * 1024U;
        std::size_t max_total_postings = 4'000'000U;
        std::size_t max_token_bytes = 4'096U;
        std::size_t max_units = 100'000U;
        std::size_t max_dictionary_entries = 4'000'000U;
        std::size_t max_token_stats = 4'000'000U;
        std::size_t max_witnesses = 4'000'000U;
        std::size_t max_namespace_rows = 8'000'000U;
        std::size_t max_total_unit_tokens = 4'000'000U;
        /// Aggregate decoded value bytes admitted for one namespace snapshot.
        std::size_t max_decoded_value_bytes = 256U * 1024U * 1024U;
        /// Aggregate postings decoded across all segment rows in one snapshot.
        std::size_t max_decoded_postings = 4'000'000U;
    };

    /// \brief Fail-closed result for row encoding and decoding.
    enum class LexicalSegmentEncodingStatus : std::uint8_t {
        Complete,
        InvalidInput,
        UnsupportedEncodingVersion,
        UnsupportedLayoutVersion,
        Truncated,
        MalformedLength,
        IntegerOverflow,
        LimitExceeded,
        TrailingBytes,
        ChecksumMismatch
    };

    struct LexicalSegmentEncodingResult final {
        LexicalSegmentEncodingStatus status =
            LexicalSegmentEncodingStatus::InvalidInput;
        std::string bytes;

        [[nodiscard]] bool complete() const noexcept {
            return status == LexicalSegmentEncodingStatus::Complete;
        }
    };

    struct LexicalSegmentDecodingResult final {
        LexicalSegmentEncodingStatus status =
            LexicalSegmentEncodingStatus::InvalidInput;
        LexicalSegmentRow row;

        [[nodiscard]] bool complete() const noexcept {
            return status == LexicalSegmentEncodingStatus::Complete;
        }
    };

    /// \brief Computes a domain-separated witness for an ordered posting list.
    [[nodiscard]] std::array<std::uint8_t, 32>
    lexical_posting_membership_witness(
        const std::vector<FlatLexicalPosting>& postings,
        std::uint32_t posting_layout_version = kLexicalSegmentLayoutVersion
    ) noexcept;

    [[nodiscard]] bool lexical_posting_membership_witness_matches(
        const std::vector<FlatLexicalPosting>& postings,
        const std::array<std::uint8_t, 32>& expected,
        std::uint32_t posting_layout_version = kLexicalSegmentLayoutVersion
    ) noexcept;

    /// \brief Encodes one physical row using a stable uncompressed wire form.
    [[nodiscard]] LexicalSegmentEncodingResult encode_lexical_segment_row(
        const LexicalSegmentRow& row,
        const LexicalSegmentEncodingLimits& limits = {}
    );

    /// \brief Decodes one physical row and rejects malformed or unknown data.
    [[nodiscard]] LexicalSegmentDecodingResult decode_lexical_segment_row(
        std::string_view bytes,
        const LexicalSegmentEncodingLimits& limits = {}
    );

} // namespace agent_memory

#endif
