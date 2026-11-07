#pragma once
#ifndef AGENT_MEMORY_HEADER_INGESTION_STRUCTURAL_SEGMENT_SET_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INGESTION_STRUCTURAL_SEGMENT_SET_HPP_INCLUDED

/// \file StructuralSegmentSet.hpp
/// \brief Deterministic, dependency-free structural Markdown segmentation.

#include <agent_memory/domain/Sha256.hpp>

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace agent_memory {

enum class StructuralSegmentKind : std::uint8_t {
    Heading,
    Paragraph,
    ListItem,
    FencedCode,
    Table,
    ImagePlaceholder
};

struct StructuralByteRange final {
    std::size_t offset = 0;
    std::size_t length = 0;
};

struct StructuralSegment final {
    StructuralSegmentKind kind = StructuralSegmentKind::Paragraph;
    std::size_t ordinal = 0;
    StructuralByteRange source;
    std::string heading_path;
    std::string text;
    /// Exact bytes addressed by `source`; this may differ from display text.
    std::string source_text;
    /// Sentence ranges are relative to `text`; paragraph segments retain
    /// sentence boundaries without introducing overlapping top-level rows.
    std::vector<StructuralByteRange> sentence_ranges;
};

struct SegmentSetOptions final {
    std::size_t max_source_bytes = 16U * 1024U * 1024U;
    std::size_t max_segment_bytes = 4096U;
    std::size_t max_segments = 100000U;
};

struct SegmentSetIdentity final {
    std::string source_revision_id;
    std::string recipe_id = "markdown.structural.v1";
    std::uint32_t recipe_version = 1;
    /// SHA-256 of the exact retained normalized UTF-8 bytes.
    std::array<std::uint8_t, 32> normalized_body_digest{};
    /// Identity of the derived SegmentSet, including recipe and chunk policy.
    std::array<std::uint8_t, 32> segment_set_fingerprint{};

    friend bool operator==(const SegmentSetIdentity& lhs,
                           const SegmentSetIdentity& rhs) noexcept {
        return lhs.source_revision_id == rhs.source_revision_id &&
            lhs.recipe_id == rhs.recipe_id &&
            lhs.recipe_version == rhs.recipe_version &&
            lhs.normalized_body_digest == rhs.normalized_body_digest &&
            lhs.segment_set_fingerprint == rhs.segment_set_fingerprint;
    }
    friend bool operator!=(const SegmentSetIdentity& lhs,
                           const SegmentSetIdentity& rhs) noexcept {
        return !(lhs == rhs);
    }
};

struct StructuralSegmentSet final {
    SegmentSetIdentity identity;
    std::string normalized_utf8;
    std::vector<StructuralSegment> segments;
};

/// \brief Parses normalized-first Markdown into deterministic structural rows.
///
/// Ranges address `normalized_utf8`, whose line endings are canonicalized to
/// LF. The source revision and this representation identity remain separate;
/// changing the recipe or chunk budget produces a new identity.
[[nodiscard]] StructuralSegmentSet build_structural_segment_set(
    std::string_view source_utf8,
    std::string source_revision_id,
    SegmentSetOptions options = {}
);

} // namespace agent_memory

#endif
