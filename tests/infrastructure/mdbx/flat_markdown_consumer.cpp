#include <agent_memory/infrastructure/mdbx/MdbxCanonicalFlatStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxRawMarkdownProductIngestion.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageIdentityMapping.hpp>
#include <agent_memory/domain/Sha256.hpp>
#include <mdbx_containers/KeyValueTable.hpp>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using namespace agent_memory;
const ScopeId scope{"consumer"};
const ResourceId resource{"resource:consumer"};
const DocumentId document{"canonical:consumer:resource:consumer"};
const ContentBlockId heading{"fixture:heading"};
const ContentBlockId paragraph{"fixture:paragraph"};
const ContentBlockId removed{"fixture:removed"};
const std::string edited_text = "Helium evidence belongs to the edited source.";

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::string hex(const std::array<std::uint8_t, 32>& bytes) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(64);
    for(auto byte : bytes) {
        result.push_back(digits[byte >> 4U]);
        result.push_back(digits[byte & 15U]);
    }
    return result;
}

std::string read_fixture(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    expect(input.good(), "Markdown fixture is unavailable");
    expect(std::filesystem::file_size(path) <= 4096, "fixture exceeds parser budget");
    const std::string raw{std::istreambuf_iterator<char>{input}, {}};
    std::string normalized;
    for(std::size_t i = 0; i < raw.size(); ++i) {
        if(raw[i] == '\r' && i + 1 < raw.size() && raw[i + 1] == '\n') continue;
        normalized.push_back(raw[i]);
    }
    expect(!normalized.empty() && normalized.back() == '\n', "fixture requires a final LF");
    return normalized;
}

// This fixture grammar is deliberately not a general-purpose Markdown parser.
CanonicalDocumentRevision parse_fixture(const std::string& body) {
    expect(body.compare(0, 2, "# ") == 0, "fixture requires one top-level heading");
    std::vector<std::string> parts;
    std::size_t start = 0;
    while(true) {
        const auto end = body.find("\n\n", start);
        parts.push_back(body.substr(start, end == std::string::npos ? end : end - start));
        if(end == std::string::npos) break;
        start = end + 2;
    }
    expect(parts.size() == 3 && !parts[2].empty(), "fixture requires exactly two paragraphs");
    parts[2].pop_back();
    for(const auto& part : parts) {
        expect(!part.empty() && part.find('\n') == std::string::npos &&
               part.find('\r') == std::string::npos, "fixture grammar does not accept multiline blocks");
    }
    CanonicalDocumentRevision result;
    result.document_id = document;
    result.blocks = {
        {heading, 1, ContentBlockKind::Heading, std::nullopt, parts[0].substr(2)},
        {paragraph, 1, ContentBlockKind::Paragraph, heading, parts[1]},
        {removed, 1, ContentBlockKind::Paragraph, heading, parts[2]}};
    return result;
}

RawMarkdownProductIngestionOptions product_options(
    const std::shared_ptr<MdbxStorageContext>& context, const std::filesystem::path& cas) {
    RawMarkdownProductIngestionOptions result;
    result.context = context;
    result.table_prefix = "consumer_raw";
    result.raw_cas_root = cas;
    result.max_full_body_bytes = 4096;
    result.max_range_bytes = 4096;
    result.max_verified_cas_bytes = 4096;
    return result;
}

MdbxCanonicalFlatStorageOptions flat_options(
    const std::shared_ptr<MdbxStorageContext>& context, MdbxCanonicalStorageMetrics* metrics = nullptr) {
    MdbxCanonicalFlatStorageOptions result;
    result.context = context;
    result.table_prefix = "consumer_flat";
    result.metrics = metrics;
    return result;
}

RawMarkdownProductImportResult publish(MdbxRawMarkdownProductIngestion& product,
                                      const std::string& body, std::uint64_t time) {
    auto result = product.import(ObservedResource{scope, resource,
        "https://example.test/canonical-consumer.md", body,
        SourceTextOrigin::OriginalText, std::nullopt, 1}, time);
    if(result.status != RawMarkdownProductImportStatus::Published || !result.resource ||
       !result.resource->revision.body_digest || !result.source_revision_id)
        throw std::runtime_error("raw product publication failed: " + result.diagnostic);
    return result;
}

Metadata binding(const RawMarkdownProductImportResult& publication) {
    Metadata result;
    result.set("scope", scope.value());
    result.set("resource", resource.value());
    result.set("source_generation", std::to_string(publication.resource->revision.generation));
    result.set("source_digest", hex(publication.resource->revision.body_digest->bytes));
    result.set("source_revision", publication.source_revision_id->value());
    result.set("fixture_parser", "one-heading-two-paragraphs.crlf-to-lf.v1");
    return result;
}

struct VisibleResult final {
    std::optional<std::string> body;
    std::string diagnostic;
};

// Host-side consistency check only: the two adapters do not share an atomic publication.
VisibleResult visible(MdbxCanonicalFlatStorage& flat, MdbxRawMarkdownProductIngestion& product,
                      const ScopeId& requested_scope, std::uint64_t revision, std::uint64_t budget) {
    try {
        const auto pinned = flat.read_revision(document, revision);
        const auto rendered = flat.materialize_markdown(document, revision);
        if(!pinned || !rendered || pinned->metadata.get("scope") != requested_scope.value())
            return {{}, "scope-or-document"};
        const auto hits = product.retrieve_and_hydrate(
            requested_scope, RetrievalQuery{"helium", std::nullopt, 8, {}}, budget);
        for(const auto& hit : hits) {
            const auto& hydrated = hit.hydration;
            if(hydrated.status != AuthoritativeArticleHydrationStatus::Hydrated ||
               hit.candidate.freshness != RevisionBoundFreshness::Current ||
               !hydrated.resource_revision ||
               !hydrated.source_revision_id) continue;
            if(hit.candidate.scope_id == requested_scope && hydrated.scope_id == requested_scope &&
               hydrated.resource_revision->resource_id == resource &&
               pinned->metadata.get("resource") == resource.value() &&
               pinned->metadata.get("source_generation") ==
                   std::to_string(hydrated.resource_revision->generation) &&
               pinned->metadata.get("source_digest") == hex(hydrated.resource_revision->body_digest.bytes) &&
               pinned->metadata.get("source_digest") == hex(sha256(*rendered)) &&
               pinned->metadata.get("source_revision") == hydrated.source_revision_id->value() &&
               hydrated.source_range.offset == 0 && hydrated.bytes == *rendered)
                return {hydrated.bytes, {}};
        }
        return {{}, "exact-source-binding-or-budget"};
    } catch(const std::exception& error) {
        return {{}, std::string{"storage-error: "} + error.what()};
    }
}

void create(const std::shared_ptr<MdbxStorageContext>& context,
            MdbxRawMarkdownProductIngestion& product, const std::string& original) {
    MdbxCanonicalStorageMetrics metrics;
    auto initial = parse_fixture(original);
    const auto first = publish(product, original, 1);
    MdbxCanonicalFlatStorage flat(flat_options(context, &metrics));
    expect(first.resource->revision.generation == 1, "first source generation is not one");
    initial.metadata = binding(first);
    expect(flat.create_document(initial), "flat document creation failed");
    expect(flat.materialize_markdown(document, 0) == original, "parser/render did not retain normalized fixture");
    metrics = {};
    const auto edit = flat.commit({document, 0,
        {ReplaceBlockText{paragraph, edited_text}, DeleteBlock{removed}}});
    expect(edit.status == CanonicalEditStatus::Ok && edit.revision->revision == 1 &&
           metrics.block_versions_written == 1, "text edit rewrote unrelated block payloads");
    const auto rendered = flat.materialize_markdown(document, 1);
    expect(rendered.has_value(), "edited render is absent");
    const auto second = publish(product, *rendered, 2);
    expect(second.resource->revision.generation == 2, "edited source did not advance generation");
    metrics = {};
    const auto provenance = flat.commit({document, 1, {UpdateDocumentMetadata{binding(second)}}});
    expect(provenance.status == CanonicalEditStatus::Ok && provenance.revision->revision == 2 &&
           metrics.block_versions_written == 0 && metrics.value_writes == 2,
           "metadata binding wrote block payloads");
    const auto result = visible(flat, product, scope, 2, 4096);
    expect(result.body == rendered, "edited canonical source did not reach actual retrieval");
}

void reopen(const std::shared_ptr<MdbxStorageContext>& context,
            MdbxRawMarkdownProductIngestion& product, const std::string& original) {
    MdbxCanonicalStorageMetrics metrics;
    MdbxCanonicalFlatStorage flat(flat_options(context, &metrics));
    expect(flat.read_current(document)->revision == 2, "fresh process lost the current head");
    expect(flat.materialize_markdown(document, 0) == original, "fresh process lost original history");
    metrics = {};
    const auto outline = flat.read_outline(document, 2);
    expect(outline && outline->entries.size() == 2 && metrics.value_reads == 1,
           "outline did not remain metadata-only");
    metrics = {};
    const auto children = flat.read_children(document, 2, heading);
    expect(children.status == CanonicalNavigationStatus::Ok && children.entries.size() == 1 &&
           children.entries[0].locator.block_id == paragraph && metrics.value_reads == 1,
           "direct-child navigation read block payloads or changed identity");
    metrics = {};
    const auto point = flat.read_block(document, paragraph, 2);
    expect(point && point->text == edited_text && point->revision == 2 && metrics.value_reads == 2,
           "point lookup did not select the exact block version");
    const auto unchanged = flat.read_block(document, heading, 2);
    expect(unchanged && unchanged->revision == 1, "document and unchanged-block revisions were conflated");
    expect(flat.read_block(document, removed, 0) && !flat.read_block(document, removed, 2),
           "deleted block history/current visibility changed");
    CanonicalSubtreeRequest subtree;
    subtree.root_id = paragraph;
    metrics = {};
    const auto selected = flat.read_subtree(document, 2, subtree);
    expect(selected.status == CanonicalNavigationStatus::Ok && selected.blocks.size() == 1 &&
           selected.blocks[0].text == edited_text && metrics.value_reads == 2,
           "subtree loaded unrelated payloads");
    subtree.max_text_bytes = 0;
    const auto bounded = flat.read_subtree(document, 2, subtree);
    expect(bounded.status == CanonicalNavigationStatus::BudgetExceeded && bounded.blocks.empty(),
           "subtree text budget exposed a payload");
    const auto rejected = flat.commit({document, 2, {InsertBlock{
        {removed, 1, ContentBlockKind::Paragraph, heading, "reuse"}, 1}}});
    expect(rejected.status == CanonicalEditStatus::InvalidEdit && flat.read_current(document)->revision == 2,
           "fresh process lost historical block no-reuse admission");
    const auto current = visible(flat, product, scope, 2, 4096);
    expect(current.body && current.body->find("Helium") != std::string::npos &&
           current.body->find("Copper") == std::string::npos, "fresh process retrieval exposed old source");
    for(const auto revision : {0U, 1U}) {
        const auto stale = visible(flat, product, scope, revision, 4096);
        expect(!stale.body && stale.diagnostic == "exact-source-binding-or-budget",
               "stale exact revision exposed current retrieval bytes");
    }
    const auto wrong_scope = visible(flat, product, ScopeId{"another-scope"}, 2, 4096);
    expect(!wrong_scope.body && wrong_scope.diagnostic == "scope-or-document", "wrong scope exposed bytes");
    expect(!visible(flat, product, scope, 2, 1).body, "hydration budget exposed bytes");
}

void corrupt(const std::shared_ptr<MdbxStorageContext>& context) {
    MdbxStorageIdentityMappingOptions options;
    options.context = context;
    options.table_prefix = "consumer_flat";
    MdbxStorageIdentityMapping mapping(options);
    const auto physical = mapping.find_block(document, paragraph);
    expect(physical.has_value(), "corruption target mapping is absent");
    mdbxc::KeyValueTable<std::string, std::string> blocks(
        context->connection(), "consumer_flat_canonical_flat_blocks_v2");
    const auto key = canonical_flat_composite_key(physical->value(), 2);
    auto bytes = blocks.find(key);
    expect(bytes && !bytes->empty(), "corruption target payload is absent");
    bytes->back() ^= 1;
    blocks.insert_or_assign(key, *bytes);
}
} // namespace

int main(int argc, char** argv) {
    try {
        expect(argc == 5, "usage: flat_markdown_consumer MODE DATABASE CAS FIXTURE");
        const std::string mode = argv[1];
        const auto original = read_fixture(argv[4]);
        const auto context = MdbxStorageContext::open(argv[2]);
        if(mode == "corrupt") {
            corrupt(context);
        } else {
            MdbxRawMarkdownProductIngestion product(product_options(context, argv[3]));
            if(mode == "create") create(context, product, original);
            else if(mode == "reopen") reopen(context, product, original);
            else if(mode == "verify-corrupt") {
                MdbxCanonicalFlatStorage flat(flat_options(context));
                const auto independent = product.retrieve_and_hydrate(
                    scope, RetrievalQuery{"helium", std::nullopt, 8, {}}, 4096);
                expect(!independent.empty() && independent[0].hydration.status ==
                    AuthoritativeArticleHydrationStatus::Hydrated, "raw path was not independently intact");
                const auto result = visible(flat, product, scope, 2, 4096);
                expect(!result.body && result.diagnostic.find("storage-error:") == 0,
                       "corrupt canonical payload exposed retrieval-visible body");
            } else throw std::runtime_error("unknown consumer mode");
        }
        std::cout << "PASS " << mode << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
