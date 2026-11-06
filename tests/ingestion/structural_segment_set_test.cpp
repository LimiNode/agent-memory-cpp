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
    }

    options.max_segment_bytes = 8;
    const auto rechunked = build_structural_segment_set(source, "source@7", options);
    expect(rechunked.identity != first.identity, "rechunking reused SegmentSet identity");
    expect(rechunked.segments.size() > first.segments.size(), "rechunking did not enforce budget");
    for(const auto& segment : rechunked.segments)
        expect(segment.text.size() <= options.max_segment_bytes, "segment budget was exceeded");

    bool rejected = false;
    try { static_cast<void>(build_structural_segment_set("\xff", "source@bad")); }
    catch(const std::invalid_argument&) { rejected = true; }
    expect(rejected, "invalid UTF-8 was accepted");
    std::cout << "structural segment set: PASS\n";
    return 0;
}
