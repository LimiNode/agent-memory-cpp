#include "TypedRepresentation.hpp"

#include <limits>
#include <stdexcept>

namespace agent_memory {

#define AM_ID_IMPL(Name) \
Name::Name(std::string value) : m_value(std::move(value)) {} \
const std::string& Name::value() const noexcept { return m_value; } \
bool Name::empty() const noexcept { return m_value.empty(); }
AM_ID_IMPL(RepresentationId) AM_ID_IMPL(SegmentSetId) AM_ID_IMPL(SegmentId) AM_ID_IMPL(EvidenceAnchorId)
#undef AM_ID_IMPL

bool is_valid_span(const Utf8ByteSpan& span, std::uint64_t body_size) noexcept {
    return span.offset <= body_size && span.length <= body_size - span.offset;
}
bool is_valid_representation(const Representation& value) noexcept {
    if(value.id.empty() || value.source_revision_id.empty() || value.processor_id.empty() || value.processor_version.empty()) return false;
    if(value.retention == RepresentationRetention::ExternalReferenceOnly && value.output_artifact_id) return false;
    if(value.retention == RepresentationRetention::Retained && !value.output_artifact_id) return false;
    return true;
}
bool is_valid_table(const TableRepresentation& value) noexcept {
    if(!is_valid_representation(value.representation) || value.table_id.empty() || value.columns.empty()) return false;
    for(const auto& column : value.columns) if(column.id.empty()) return false;
    for(const auto& row : value.rows) {
        if(row.row_key.empty()) return false;
        for(const auto& cell : row.cells) if(cell.column_id.empty()) return false;
    }
    return true;
}
bool is_valid_image(const ImageRepresentation& value) noexcept {
    return is_valid_representation(value.representation) && !value.binding_id.empty() && (value.caption || value.alt_text || value.placement_span);
}
bool is_valid_transcript(const TranscriptRepresentation& value) noexcept {
    if(!is_valid_representation(value.representation)) return false;
    for(const auto& interval : value.intervals) {
        if(interval.start_ms > interval.end_ms || interval.text.empty()) return false;
        if(interval.alignment && (interval.alignment->processor_id.empty() || interval.alignment->processor_version.empty() || interval.alignment->method.empty())) return false;
        if(interval.alignment && interval.alignment->confidence && (*interval.alignment->confidence < 0.0 || *interval.alignment->confidence > 1.0)) return false;
    }
    return true;
}
bool is_valid_video_reference(const ExternalVideoReference& value) noexcept {
    return !value.external_video_id.empty() && !value.url.empty() && value.start_ms <= value.end_ms;
}
bool is_valid_locator(const TypedLocator& value) noexcept {
    return std::visit([](const auto& locator) {
        using T = std::decay_t<decltype(locator)>;
        if constexpr(std::is_same_v<T, TextLocator>) return !locator.source_revision_id.empty();
        else if constexpr(std::is_same_v<T, TableCellLocator>) return !locator.table_id.empty() && !locator.representation_id.empty() && !locator.source_revision_id.empty() && !locator.column_id.empty() && !locator.row_key.empty();
        else if constexpr(std::is_same_v<T, ImageLocator>) return !locator.binding_id.empty();
        else if constexpr(std::is_same_v<T, TranscriptLocator>) return !locator.source_revision_id.empty() && locator.start_ms <= locator.end_ms;
        else return is_valid_video_reference({locator.external_video_id, locator.url, locator.start_ms, locator.end_ms});
    }, value);
}

namespace {
void put(std::string& out, std::string_view text) { out += std::to_string(text.size()); out.push_back(':'); out.append(text.data(), text.size()); }
std::string get(std::string_view in, std::size_t& p) {
    if(p >= in.size()) throw std::runtime_error("locator string is truncated"); std::size_t n = 0; bool digit = false;
    while(p < in.size() && in[p] != ':') { const auto c = static_cast<unsigned char>(in[p++]); if(c < '0' || c > '9') throw std::runtime_error("locator length is invalid"); digit = true; const auto d = static_cast<std::size_t>(c - '0'); if(n > (std::numeric_limits<std::size_t>::max() - d) / 10U) throw std::runtime_error("locator length overflows"); n = n * 10U + d; }
    if(!digit || p >= in.size()) throw std::runtime_error("locator length is missing"); ++p; if(n > in.size() - p) throw std::runtime_error("locator string is truncated"); auto result = std::string(in.substr(p, n)); p += n; return result;
}
void put_u64(std::string& out, std::uint64_t value) { for(int s = 56; s >= 0; s -= 8) out.push_back(static_cast<char>((value >> s) & 0xffU)); }
std::uint64_t get_u64(std::string_view in, std::size_t& p) { if(p > in.size() || in.size() - p < 8U) throw std::runtime_error("locator integer is truncated"); std::uint64_t v = 0; for(int i=0;i<8;++i) v = (v << 8U) | static_cast<unsigned char>(in[p++]); return v; }
void put_span(std::string& out, Utf8ByteSpan span) { put_u64(out, span.offset); put_u64(out, span.length); }
Utf8ByteSpan get_span(std::string_view in, std::size_t& p) { return {get_u64(in,p), get_u64(in,p)}; }
void put_opt(std::string& out, const std::optional<std::string>& value) { put_u64(out, value ? 1U : 0U); if(value) put(out, *value); }
std::optional<std::string> get_opt(std::string_view in, std::size_t& p) { const auto present = get_u64(in,p); if(present > 1U) throw std::runtime_error("locator optional flag is invalid"); return present ? std::optional<std::string>(get(in,p)) : std::nullopt; }
}

std::string serialize_typed_locator(const TypedLocator& value) {
    if(!is_valid_locator(value)) throw std::invalid_argument("invalid typed locator"); std::string out;
    std::visit([&](const auto& locator) {
        using T = std::decay_t<decltype(locator)>;
        if constexpr(std::is_same_v<T, TextLocator>) { out.push_back('T'); put(out, locator.source_revision_id.value()); put_span(out, locator.span); }
        else if constexpr(std::is_same_v<T, TableCellLocator>) { out.push_back('C'); put(out, locator.table_id); put(out, locator.representation_id.value()); put(out, locator.source_revision_id.value()); put(out, locator.column_id); put(out, locator.row_key); put_u64(out, locator.row_ordinal); put_span(out, locator.span); }
        else if constexpr(std::is_same_v<T, ImageLocator>) { out.push_back('I'); put(out, locator.binding_id.value()); put_opt(out, locator.caption); put_opt(out, locator.alt_text); put_u64(out, locator.placement_span ? 1U : 0U); if(locator.placement_span) put_span(out, *locator.placement_span); }
        else if constexpr(std::is_same_v<T, TranscriptLocator>) { out.push_back('R'); put(out, locator.source_revision_id.value()); put_u64(out, locator.start_ms); put_u64(out, locator.end_ms); put_opt(out, locator.speaker_id); }
        else { out.push_back('V'); put(out, locator.external_video_id); put(out, locator.url); put_u64(out, locator.start_ms); put_u64(out, locator.end_ms); }
    }, value); return out;
}

TypedLocator deserialize_typed_locator(std::string_view bytes) {
    if(bytes.empty()) throw std::invalid_argument("typed locator is empty"); std::size_t p = 1;
    switch(bytes[0]) {
    case 'T': { TextLocator v{SourceRevisionId{get(bytes,p)}, get_span(bytes,p)}; if(p != bytes.size() || !is_valid_locator(v)) throw std::runtime_error("invalid text locator"); return v; }
    case 'C': { TableCellLocator v{get(bytes,p), RepresentationId{get(bytes,p)}, SourceRevisionId{get(bytes,p)}, get(bytes,p), get(bytes,p), get_u64(bytes,p), get_span(bytes,p)}; if(p != bytes.size() || !is_valid_locator(v)) throw std::runtime_error("invalid table locator"); return v; }
    case 'I': { ImageLocator v{ArtifactBindingId{get(bytes,p)}, get_opt(bytes,p), get_opt(bytes,p), std::nullopt}; const auto has_span = get_u64(bytes,p); if(has_span > 1U) throw std::runtime_error("invalid image locator span flag"); if(has_span) v.placement_span = get_span(bytes,p); if(p != bytes.size() || !is_valid_locator(v)) throw std::runtime_error("invalid image locator"); return v; }
    case 'R': { TranscriptLocator v{SourceRevisionId{get(bytes,p)}, get_u64(bytes,p), get_u64(bytes,p), get_opt(bytes,p)}; if(p != bytes.size() || !is_valid_locator(v)) throw std::runtime_error("invalid transcript locator"); return v; }
    case 'V': { VideoLocator v{get(bytes,p), get(bytes,p), get_u64(bytes,p), get_u64(bytes,p)}; if(p != bytes.size() || !is_valid_locator(v)) throw std::runtime_error("invalid video locator"); return v; }
    default: throw std::runtime_error("unknown typed locator tag");
    }
}

} // namespace agent_memory
