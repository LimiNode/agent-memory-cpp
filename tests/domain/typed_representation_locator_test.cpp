#include <agent_memory/domain/TypedRepresentation.hpp>

#include <iostream>
#include <limits>
#include <stdexcept>

namespace {
void expect(bool value, const char* message) { if(!value) throw std::runtime_error(message); }
}

int main() {
    using namespace agent_memory;
    const ArtifactId body_artifact{"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"};
    Representation retained{RepresentationId{"rep-1"}, SourceRevisionId{"source@1"}, RepresentationKind::NormalizedDocument, RepresentationRetention::Retained, body_artifact, "normalizer", "1"};
    expect(is_valid_representation(retained), "retained representation rejected");
    Representation external{RepresentationId{"video-ref"}, SourceRevisionId{"source@1"}, RepresentationKind::VisualDescription, RepresentationRetention::ExternalReferenceOnly, std::nullopt, "provider", "1"};
    expect(is_valid_representation(external), "external-only representation rejected");

    const TypedLocator text = TextLocator{SourceRevisionId{"source@1"}, {4, 8}};
    expect(serialize_typed_locator(deserialize_typed_locator(serialize_typed_locator(text))) == serialize_typed_locator(text), "text locator round-trip failed");
    const TypedLocator cell = TableCellLocator{"table-1", RepresentationId{"table-rep"}, SourceRevisionId{"source@1"}, "amount", "row-a", 3, {10, 5}};
    expect(serialize_typed_locator(deserialize_typed_locator(serialize_typed_locator(cell))) == serialize_typed_locator(cell), "table locator round-trip failed");
    const TypedLocator image = ImageLocator{ArtifactBindingId{"binding-image"}, std::string{"caption"}, std::string{"alt"}, Utf8ByteSpan{12, 5}};
    expect(serialize_typed_locator(deserialize_typed_locator(serialize_typed_locator(image))) == serialize_typed_locator(image), "image locator round-trip failed");
    const TypedLocator transcript = TranscriptLocator{SourceRevisionId{"source@1"}, 1000, 2000, std::string{"speaker"}};
    expect(serialize_typed_locator(deserialize_typed_locator(serialize_typed_locator(transcript))) == serialize_typed_locator(transcript), "transcript locator round-trip failed");
    const TypedLocator video = VideoLocator{"yt-id", "https://youtu.be/yt-id", 100, 250};
    expect(serialize_typed_locator(deserialize_typed_locator(serialize_typed_locator(video))) == serialize_typed_locator(video), "video locator round-trip failed");
    expect(is_valid_video_reference({"yt-id", "https://youtu.be/yt-id", 0, 10}), "video reference rejected");
    expect(!is_valid_video_reference({"", "https://youtu.be/yt-id", 0, 10}), "empty video id accepted");

    TableRepresentation table;
    table.representation = Representation{RepresentationId{"table-rep"}, SourceRevisionId{"source@1"}, RepresentationKind::Table, RepresentationRetention::Retained, body_artifact, "parser", "1"};
    table.table_id = "table-1";
    table.columns = {{"amount", "Amount"}};
    table.rows = {{"row-a", 0, {{"amount", "42", {1, 2}}}}};
    expect(is_valid_table(table), "typed table rejected");
    auto duplicate_column = table;
    duplicate_column.columns.push_back({"amount", "Duplicate"});
    expect(!is_valid_table(duplicate_column), "duplicate table column accepted");
    auto unknown_cell = table;
    unknown_cell.rows.front().cells.push_back({"missing", "x", {0, 1}});
    expect(!is_valid_table(unknown_cell), "cell referencing unknown column accepted");
    auto duplicate_row = table;
    duplicate_row.rows.push_back({"row-a", 1, {}});
    expect(!is_valid_table(duplicate_row), "duplicate table row key accepted");
    auto duplicate_ordinal = table;
    duplicate_ordinal.rows.push_back({"row-b", 0, {}});
    expect(!is_valid_table(duplicate_ordinal), "duplicate table row ordinal accepted");
    auto duplicate_cell = table;
    duplicate_cell.rows.front().cells.push_back({"amount", "duplicate", {0, 1}});
    expect(!is_valid_table(duplicate_cell), "duplicate table cell accepted");
    auto wrong_table_kind = table;
    wrong_table_kind.representation.kind = RepresentationKind::Image;
    expect(!is_valid_table(wrong_table_kind), "table with mismatched representation kind accepted");

    TranscriptRepresentation transcript_rep;
    transcript_rep.representation = Representation{RepresentationId{"transcript"}, SourceRevisionId{"source@1"}, RepresentationKind::Transcript, RepresentationRetention::Retained, body_artifact, "asr", "1"};
    transcript_rep.intervals = {{0, 1000, "hello", std::nullopt, {0, 5}, AlignmentProvenance{"asr", "1", "forced-align", 0.9}}};
    expect(is_valid_transcript(transcript_rep), "transcript representation rejected");
    expect(!is_valid_transcript(TranscriptRepresentation{transcript_rep.representation, {{2, 1, "bad", std::nullopt, {0, 1}}}}), "inverted transcript interval accepted");
    auto non_finite = transcript_rep;
    non_finite.intervals.front().alignment->confidence =
        std::numeric_limits<double>::quiet_NaN();
    expect(!is_valid_transcript(non_finite), "NaN alignment confidence accepted");
    auto image_without_metadata = ImageRepresentation{
        Representation{RepresentationId{"image"}, SourceRevisionId{"source@1"},
                       RepresentationKind::Image, RepresentationRetention::Retained,
                       body_artifact, "extractor", "1"},
        ArtifactBindingId{"binding-image"}, std::nullopt, std::nullopt, std::nullopt};
    expect(is_valid_image(image_without_metadata), "image without optional metadata rejected");
    auto malformed_artifact = retained;
    malformed_artifact.output_artifact_id = ArtifactId{"sha256:body"};
    expect(!is_valid_representation(malformed_artifact), "malformed artifact id accepted");
    auto uppercase_artifact = retained;
    uppercase_artifact.output_artifact_id = ArtifactId{"sha256:0123456789ABCDEF0123456789abcdef0123456789abcdef0123456789abcdef"};
    expect(!is_valid_representation(uppercase_artifact), "non-canonical artifact id accepted");

    bool invalid_version = false;
    try { static_cast<void>(deserialize_typed_locator("TL0")); }
    catch(const std::exception&) { invalid_version = true; }
    expect(invalid_version, "unknown locator codec version accepted");
    auto truncated = serialize_typed_locator(text);
    truncated.pop_back();
    bool truncated_rejected = false;
    try { static_cast<void>(deserialize_typed_locator(truncated)); }
    catch(const std::exception&) { truncated_rejected = true; }
    expect(truncated_rejected, "truncated locator codec input accepted");
    bool oversized_rejected = false;
    try { static_cast<void>(deserialize_typed_locator(std::string(kMaxTypedLocatorBytes + 1U, 'x'))); }
    catch(const std::exception&) { oversized_rejected = true; }
    expect(oversized_rejected, "oversized locator codec input accepted");
    bool oversized_serialized_rejected = false;
    try {
        static_cast<void>(serialize_typed_locator(ImageLocator{
            ArtifactBindingId{"binding-image"},
            std::string(kMaxTypedLocatorBytes, 'x'), std::nullopt, std::nullopt}));
    } catch(const std::exception&) { oversized_serialized_rejected = true; }
    expect(oversized_serialized_rejected, "oversized serialized locator accepted");

    std::cout << "typed representation locators: PASS\n";
    return 0;
}
