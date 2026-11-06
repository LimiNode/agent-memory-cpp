#pragma once
#ifndef AGENT_MEMORY_HEADER_DOMAIN_TYPED_REPRESENTATION_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_DOMAIN_TYPED_REPRESENTATION_HPP_INCLUDED

/// \file TypedRepresentation.hpp
/// \brief Artifact-aware representation and locator value contracts.

#include "Artifact.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <type_traits>
#include <variant>
#include <vector>

namespace agent_memory {

class RepresentationId final { public: RepresentationId() = default; explicit RepresentationId(std::string value); const std::string& value() const noexcept; bool empty() const noexcept; private: std::string m_value; };
class SegmentSetId final { public: SegmentSetId() = default; explicit SegmentSetId(std::string value); const std::string& value() const noexcept; bool empty() const noexcept; private: std::string m_value; };
class SegmentId final { public: SegmentId() = default; explicit SegmentId(std::string value); const std::string& value() const noexcept; bool empty() const noexcept; private: std::string m_value; };
class EvidenceAnchorId final { public: EvidenceAnchorId() = default; explicit EvidenceAnchorId(std::string value); const std::string& value() const noexcept; bool empty() const noexcept; private: std::string m_value; };

template<typename Id> bool operator==(const Id& lhs, const Id& rhs) noexcept { return lhs.value() == rhs.value(); }
template<typename Id> bool operator!=(const Id& lhs, const Id& rhs) noexcept { return !(lhs == rhs); }

struct Utf8ByteSpan final { std::uint64_t offset = 0; std::uint64_t length = 0; };

enum class RepresentationRetention : std::uint8_t { Retained, ExternalReferenceOnly };
enum class RepresentationKind : std::uint8_t { NormalizedDocument, Table, Image, Transcript, Subtitle, VisualDescription };

struct Representation final {
    RepresentationId id;
    SourceRevisionId source_revision_id;
    RepresentationKind kind = RepresentationKind::NormalizedDocument;
    RepresentationRetention retention = RepresentationRetention::Retained;
    std::optional<ArtifactId> output_artifact_id;
    std::string processor_id;
    std::string processor_version;
};

struct DocumentBlock final {
    SegmentId id;
    RepresentationId representation_id;
    SourceRevisionId source_revision_id;
    Utf8ByteSpan source_span;
    std::string normalized_text;
};

struct TableColumn final { std::string id; std::string label; };
struct TableCell final { std::string column_id; std::string value; Utf8ByteSpan source_span; };
struct TableRow final { std::string row_key; std::uint64_t ordinal = 0; std::vector<TableCell> cells; };
struct TableRepresentation final {
    Representation representation;
    std::string table_id;
    std::vector<TableColumn> columns;
    std::vector<TableRow> rows;
};

struct ImageRepresentation final {
    Representation representation;
    ArtifactBindingId binding_id;
    std::optional<std::string> caption;
    std::optional<std::string> alt_text;
    std::optional<Utf8ByteSpan> placement_span;
};

struct AlignmentProvenance final {
    std::string processor_id;
    std::string processor_version;
    std::string method;
    std::optional<double> confidence;
};

struct TranscriptInterval final {
    std::uint64_t start_ms = 0;
    std::uint64_t end_ms = 0;
    std::string text;
    std::optional<std::string> speaker_id;
    Utf8ByteSpan source_span;
    std::optional<AlignmentProvenance> alignment;
};
struct TranscriptRepresentation final { Representation representation; std::vector<TranscriptInterval> intervals; };

/// A video reference intentionally has no fabricated ArtifactId.
struct ExternalVideoReference final {
    std::string external_video_id;
    std::string url;
    std::uint64_t start_ms = 0;
    std::uint64_t end_ms = 0;
};

struct TextLocator final { SourceRevisionId source_revision_id; Utf8ByteSpan span; };
struct TableCellLocator final { std::string table_id; RepresentationId representation_id; SourceRevisionId source_revision_id; std::string column_id; std::string row_key; std::uint64_t row_ordinal = 0; Utf8ByteSpan span; };
struct ImageLocator final { ArtifactBindingId binding_id; std::optional<std::string> caption; std::optional<std::string> alt_text; std::optional<Utf8ByteSpan> placement_span; };
struct TranscriptLocator final { SourceRevisionId source_revision_id; std::uint64_t start_ms = 0; std::uint64_t end_ms = 0; std::optional<std::string> speaker_id; };
struct VideoLocator final { std::string external_video_id; std::string url; std::uint64_t start_ms = 0; std::uint64_t end_ms = 0; };
using TypedLocator = std::variant<TextLocator, TableCellLocator, ImageLocator, TranscriptLocator, VideoLocator>;

[[nodiscard]] bool is_valid_span(const Utf8ByteSpan& span, std::uint64_t body_size) noexcept;
[[nodiscard]] bool is_valid_representation(const Representation& value) noexcept;
[[nodiscard]] bool is_valid_table(const TableRepresentation& value) noexcept;
[[nodiscard]] bool is_valid_image(const ImageRepresentation& value) noexcept;
[[nodiscard]] bool is_valid_transcript(const TranscriptRepresentation& value) noexcept;
[[nodiscard]] bool is_valid_video_reference(const ExternalVideoReference& value) noexcept;
[[nodiscard]] bool is_valid_locator(const TypedLocator& value) noexcept;

/// Deterministic length-prefixed locator codec used for conformance receipts.
[[nodiscard]] std::string serialize_typed_locator(const TypedLocator& value);
[[nodiscard]] TypedLocator deserialize_typed_locator(std::string_view bytes);

} // namespace agent_memory

#endif
