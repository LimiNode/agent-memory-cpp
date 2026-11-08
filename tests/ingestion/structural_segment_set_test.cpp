#include <agent_memory/ingestion/StructuralSegmentSet.hpp>

#include <algorithm>
#include <iostream>
#include <stdexcept>

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

} // namespace

int main() {
    using namespace agent_memory;
    const std::string source =
        "# Guide\r\n\r\n"
        "A first sentence. A second sentence!\r\n\r\n"
        "- one\n- two\n\n"
        "```cpp\nint value = 1;\n```\n\n"
        "| key | value |\n| --- | --- |\n| a | b |\n\n"
        "![diagram](diagram.png)\n";
    SegmentSetOptions options;
    options.max_segment_bytes = 4096;
    const auto first = build_structural_segment_set(source, "source@7", options);
    const auto second = build_structural_segment_set(source, "source@7", options);
    expect(first.identity == second.identity, "segment identity is not deterministic");
    expect(first.identity.normalized_body_digest == sha256(first.normalized_utf8),
           "normalized body digest is not the retained UTF-8 digest");
    expect(first.normalized_utf8.find('\r') == std::string::npos,
           "line endings were not normalized");
    expect(std::any_of(first.segments.begin(), first.segments.end(), [](const auto& segment) {
        return segment.kind == StructuralSegmentKind::Heading && segment.heading_path == "Guide";
    }), "heading was not classified");
    expect(std::any_of(first.segments.begin(), first.segments.end(), [](const auto& segment) {
        return segment.kind == StructuralSegmentKind::Paragraph && segment.sentence_ranges.size() == 2U;
    }), "paragraph sentence boundaries were not retained");
    expect(std::any_of(first.segments.begin(), first.segments.end(), [](const auto& segment) {
        return segment.kind == StructuralSegmentKind::ListItem;
    }), "list item was not classified");
    expect(std::any_of(first.segments.begin(), first.segments.end(), [](const auto& segment) {
        return segment.kind == StructuralSegmentKind::FencedCode;
    }), "fenced code was not classified");
    expect(std::any_of(first.segments.begin(), first.segments.end(), [](const auto& segment) {
        return segment.kind == StructuralSegmentKind::Table;
    }), "table was not classified");
    expect(std::any_of(first.segments.begin(), first.segments.end(), [](const auto& segment) {
        return segment.kind == StructuralSegmentKind::ImagePlaceholder && segment.text == "[image-placeholder]";
    }), "image placeholder was not typed");
    for(const auto& segment : first.segments) {
        expect(segment.source.offset + segment.source.length <= first.normalized_utf8.size(),
               "segment byte range escaped normalized source");
        expect(first.normalized_utf8.substr(segment.source.offset, segment.source.length) == segment.source_text,
               "segment source locator does not round-trip exact source bytes");
    }

    options.max_segment_bytes = 8;
    const auto rechunked = build_structural_segment_set(source, "source@7", options);
    expect(rechunked.identity != first.identity, "rechunking reused SegmentSet identity");
    expect(rechunked.identity.normalized_body_digest == first.identity.normalized_body_digest,
           "rechunking changed normalized body identity");
    expect(rechunked.identity.segment_set_fingerprint != first.identity.segment_set_fingerprint,
           "rechunking did not change SegmentSet fingerprint");
    expect(rechunked.segments.size() > first.segments.size(), "rechunking did not enforce budget");
    for(const auto& segment : rechunked.segments) {
        if(segment.kind == StructuralSegmentKind::Paragraph)
            expect(segment.text.size() <= options.max_segment_bytes, "paragraph chunk budget was exceeded");
    }

    bool tiny_budget_rejected = false;
    try {
        SegmentSetOptions tiny;
        tiny.max_segment_bytes = 1;
        static_cast<void>(build_structural_segment_set("Привет 😀 e\xCC\x81", "source@utf8", tiny));
    } catch(const std::invalid_argument&) { tiny_budget_rejected = true; }
    expect(tiny_budget_rejected, "a budget smaller than one UTF-8 code point was accepted");

    const auto tail = build_structural_segment_set(
        "First sentence. Second fragment without punctuation", "source@tail");
    const auto tail_paragraph = std::find_if(
        tail.segments.begin(), tail.segments.end(), [](const auto& segment) {
            return segment.kind == StructuralSegmentKind::Paragraph;
        });
    expect(tail_paragraph != tail.segments.end() &&
               tail_paragraph->sentence_ranges.size() == 2U &&
               tail_paragraph->sentence_ranges.back().length > 0U,
           "final sentence fragment without punctuation was dropped");

    bool rejected = false;
    try { static_cast<void>(build_structural_segment_set("\xff", "source@bad")); }
    catch(const std::invalid_argument&) { rejected = true; }
    expect(rejected, "invalid UTF-8 was accepted");
    std::cout << "structural segment set: PASS\n";
    return 0;
}
