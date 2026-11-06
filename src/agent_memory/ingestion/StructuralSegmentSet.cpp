#include "StructuralSegmentSet.hpp"

#include <algorithm>
#include <cctype>
#include <limits>
#include <stdexcept>

namespace agent_memory {
namespace {

struct Line final { std::size_t begin = 0; std::size_t end = 0; std::size_t next = 0; };

bool valid_utf8(std::string_view text) {
    for(std::size_t i = 0; i < text.size();) {
        const auto lead = static_cast<unsigned char>(text[i]);
        std::size_t width = 0;
        if(lead <= 0x7fU) width = 1;
        else if(lead >= 0xc2U && lead <= 0xdfU) width = 2;
        else if(lead >= 0xe0U && lead <= 0xefU) width = 3;
        else if(lead >= 0xf0U && lead <= 0xf4U) width = 4;
        else return false;
        if(i + width > text.size()) return false;
        for(std::size_t j = 1; j < width; ++j) {
            if((static_cast<unsigned char>(text[i + j]) & 0xc0U) != 0x80U)
                return false;
        }
        if(width == 3 && lead == 0xe0U && static_cast<unsigned char>(text[i + 1]) < 0xa0U) return false;
        if(width == 3 && lead == 0xedU && static_cast<unsigned char>(text[i + 1]) >= 0xa0U) return false;
        if(width == 4 && lead == 0xf0U && static_cast<unsigned char>(text[i + 1]) < 0x90U) return false;
        if(width == 4 && lead == 0xf4U && static_cast<unsigned char>(text[i + 1]) >= 0x90U) return false;
        i += width;
    }
    return true;
}

std::string normalize(std::string_view source) {
    std::string result;
    result.reserve(source.size());
    for(std::size_t i = 0; i < source.size(); ++i) {
        if(source[i] == '\r') {
            if(i + 1 < source.size() && source[i + 1] == '\n') ++i;
            result.push_back('\n');
        } else result.push_back(source[i]);
    }
    return result;
}

std::vector<Line> lines(std::string_view text) {
    std::vector<Line> result;
    std::size_t begin = 0;
    while(begin < text.size()) {
        const auto newline = text.find('\n', begin);
        const auto end = newline == std::string_view::npos ? text.size() : newline;
        result.push_back({begin, end, newline == std::string_view::npos ? end : newline + 1U});
        begin = newline == std::string_view::npos ? text.size() : newline + 1U;
    }
    if(text.empty() || text.back() == '\n') result.push_back({text.size(), text.size(), text.size()});
    return result;
}

std::string_view trim(std::string_view value) {
    std::size_t begin = 0;
    while(begin < value.size() && std::isspace(static_cast<unsigned char>(value[begin]))) ++begin;
    std::size_t end = value.size();
    while(end > begin && std::isspace(static_cast<unsigned char>(value[end - 1U]))) --end;
    return value.substr(begin, end - begin);
}

bool starts_with_list(std::string_view value, std::size_t& marker_end) {
    std::size_t begin = 0;
    while(begin < value.size() && begin < 3U && value[begin] == ' ') ++begin;
    if(begin >= value.size()) return false;
    if(value[begin] == '-' || value[begin] == '*' || value[begin] == '+') {
        if(begin + 1U < value.size() && std::isspace(static_cast<unsigned char>(value[begin + 1U]))) {
            marker_end = begin + 2U; return true;
        }
    }
    std::size_t digit = begin;
    while(digit < value.size() && std::isdigit(static_cast<unsigned char>(value[digit]))) ++digit;
    if(digit > begin && digit + 1U < value.size() &&
       (value[digit] == '.' || value[digit] == ')') &&
       std::isspace(static_cast<unsigned char>(value[digit + 1U]))) {
        marker_end = digit + 2U; return true;
    }
    return false;
}

bool heading(std::string_view value, std::size_t& level, std::string_view& title) {
    std::size_t begin = 0;
    while(begin < value.size() && begin < 3U && value[begin] == ' ') ++begin;
    std::size_t hashes = begin;
    while(hashes < value.size() && value[hashes] == '#') ++hashes;
    if(hashes == begin || hashes - begin > 6U || hashes >= value.size() || value[hashes] != ' ') return false;
    level = hashes - begin;
    title = trim(value.substr(hashes + 1U));
    return !title.empty();
}

bool fence(std::string_view value, char& marker, std::size_t& width) {
    std::size_t begin = 0;
    while(begin < value.size() && begin < 3U && value[begin] == ' ') ++begin;
    if(begin >= value.size() || (value[begin] != '`' && value[begin] != '~')) return false;
    marker = value[begin];
    width = 0;
    while(begin + width < value.size() && value[begin + width] == marker) ++width;
    return width >= 3U;
}

bool table_separator(std::string_view value) {
    const auto stripped = trim(value);
    if(stripped.empty() || stripped.find('|') == std::string_view::npos) return false;
    bool dash = false;
    for(const auto c : stripped) {
        if(c == '-') dash = true;
        else if(c != '|' && c != ':' && !std::isspace(static_cast<unsigned char>(c))) return false;
    }
    return dash;
}

std::string heading_path(const std::vector<std::string>& headings) {
    std::string result;
    for(std::size_t i = 0; i < headings.size(); ++i) {
        if(i != 0) result += " / ";
        result += headings[i];
    }
    return result;
}

std::size_t utf8_prefix(std::string_view value, std::size_t maximum) {
    if(value.size() <= maximum) return value.size();
    std::size_t end = maximum;
    while(end > 0 && (static_cast<unsigned char>(value[end]) & 0xc0U) == 0x80U) --end;
    return end == 0 ? maximum : end;
}

void add_segment(StructuralSegmentSet& set, StructuralSegment segment,
                 const SegmentSetOptions& options) {
    if(segment.text.empty()) return;
    const auto budget = options.max_segment_bytes;
    for(std::size_t offset = 0; offset < segment.text.size();) {
        if(set.segments.size() >= options.max_segments)
            throw std::invalid_argument("structural segment count exceeds its budget");
        const auto length = utf8_prefix(segment.text.substr(offset), budget);
        if(length == 0) throw std::invalid_argument("structural segment budget is too small");
        auto part = segment;
        part.ordinal = set.segments.size();
        part.text = segment.text.substr(offset, length);
        part.source.offset = segment.source.offset + offset;
        part.source.length = length;
        if(offset != 0 || length != segment.text.size()) part.sentence_ranges.clear();
        set.segments.push_back(std::move(part));
        offset += length;
    }
}

} // namespace

StructuralSegmentSet build_structural_segment_set(
    std::string_view source_utf8,
    std::string source_revision_id,
    SegmentSetOptions options
) {
    if(source_revision_id.empty() || options.max_segment_bytes == 0 || options.max_segments == 0 ||
       source_utf8.size() > options.max_source_bytes || !valid_utf8(source_utf8))
        throw std::invalid_argument("invalid structural segment input or budget");
    StructuralSegmentSet set;
    set.normalized_utf8 = normalize(source_utf8);
    if(set.normalized_utf8.size() > options.max_source_bytes || !valid_utf8(set.normalized_utf8))
        throw std::invalid_argument("normalized structural source exceeds its budget");
    set.identity.source_revision_id = std::move(source_revision_id);
    std::string descriptor = "markdown.structural.v1\n" + std::to_string(options.max_segment_bytes) +
        ":" + std::to_string(options.max_segments) + "\n" + set.normalized_utf8;
    set.identity.normalized_digest = sha256(descriptor);

    const auto source_lines = lines(set.normalized_utf8);
    std::vector<std::string> headings;
    std::size_t index = 0;
    while(index < source_lines.size()) {
        const auto line = source_lines[index];
        const auto value = std::string_view(set.normalized_utf8).substr(line.begin, line.end - line.begin);
        if(value.empty()) { ++index; continue; }
        std::size_t level = 0; std::string_view title;
        if(heading(value, level, title)) {
            if(headings.size() >= level) headings.resize(level - 1U);
            headings.push_back(std::string(title));
            add_segment(set, {StructuralSegmentKind::Heading, 0, {line.begin, line.end - line.begin}, heading_path(headings), std::string(title), {}}, options);
            ++index; continue;
        }
        char fence_marker = 0; std::size_t fence_width = 0;
        if(fence(value, fence_marker, fence_width)) {
            const auto start = line.begin;
            std::size_t end = line.next;
            ++index;
            while(index < source_lines.size()) {
                const auto candidate = source_lines[index];
                const auto candidate_value = std::string_view(set.normalized_utf8).substr(candidate.begin, candidate.end - candidate.begin);
                char close_marker = 0; std::size_t close_width = 0;
                if(fence(candidate_value, close_marker, close_width) && close_marker == fence_marker && close_width >= fence_width) {
                    end = candidate.next; ++index; break;
                }
                end = candidate.next; ++index;
            }
            add_segment(set, {StructuralSegmentKind::FencedCode, 0, {start, end - start}, heading_path(headings), set.normalized_utf8.substr(start, end - start), {}}, options);
            continue;
        }
        if(index + 1U < source_lines.size() && value.find('|') != std::string_view::npos &&
           table_separator(std::string_view(set.normalized_utf8).substr(source_lines[index + 1U].begin,
                                                                         source_lines[index + 1U].end - source_lines[index + 1U].begin))) {
            const auto start = line.begin;
            auto end = source_lines[index + 1U].next;
            index += 2U;
            while(index < source_lines.size() && std::string_view(set.normalized_utf8).substr(source_lines[index].begin, source_lines[index].end - source_lines[index].begin).find('|') != std::string_view::npos) {
                end = source_lines[index].next; ++index;
            }
            add_segment(set, {StructuralSegmentKind::Table, 0, {start, end - start}, heading_path(headings), set.normalized_utf8.substr(start, end - start), {}}, options);
            continue;
        }
        std::size_t marker_end = 0;
        if(starts_with_list(value, marker_end)) {
            add_segment(set, {StructuralSegmentKind::ListItem, 0, {line.begin, line.end - line.begin}, heading_path(headings), std::string(trim(value.substr(marker_end))), {}}, options);
            ++index; continue;
        }
        const auto stripped = trim(value);
        if(stripped.size() >= 4U && stripped.substr(0, 2U) == "![" && stripped.find("](") != std::string_view::npos) {
            add_segment(set, {StructuralSegmentKind::ImagePlaceholder, 0, {line.begin, line.end - line.begin}, heading_path(headings), "[image-placeholder]", {}}, options);
            ++index; continue;
        }
        const auto start = line.begin;
        auto end = line.end;
        std::string paragraph(value);
        ++index;
        while(index < source_lines.size()) {
            const auto next = source_lines[index];
            const auto next_value = std::string_view(set.normalized_utf8).substr(next.begin, next.end - next.begin);
            if(next_value.empty() || heading(next_value, level, title) || fence(next_value, fence_marker, fence_width) || starts_with_list(next_value, marker_end)) break;
            if(!paragraph.empty()) paragraph.push_back('\n');
            paragraph.append(next_value.data(), next_value.size());
            end = next.end; ++index;
        }
        StructuralSegment segment{StructuralSegmentKind::Paragraph, 0, {start, end - start}, heading_path(headings), paragraph, {}};
        for(std::size_t sentence = 0, begin = 0; sentence < paragraph.size(); ++sentence) {
            if(paragraph[sentence] == '.' || paragraph[sentence] == '!' || paragraph[sentence] == '?') {
                auto boundary = sentence + 1U;
                while(boundary < paragraph.size() && std::isspace(static_cast<unsigned char>(paragraph[boundary]))) ++boundary;
                segment.sentence_ranges.push_back({begin, sentence + 1U - begin});
                begin = boundary;
            }
        }
        if(segment.sentence_ranges.empty() && !paragraph.empty()) segment.sentence_ranges.push_back({0, paragraph.size()});
        add_segment(set, std::move(segment), options);
    }
    return set;
}

} // namespace agent_memory
