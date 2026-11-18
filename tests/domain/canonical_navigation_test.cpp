#include <agent_memory.hpp>

#include <iostream>
#include <stdexcept>

namespace {
using namespace agent_memory;

[[noreturn]] void fail(const char* message) { throw std::runtime_error(message); }
void expect(bool value, const char* message) { if(!value) fail(message); }

CanonicalDocumentRevision fixture() {
    return {DocumentId{"navigation-doc"}, 4, {}, {
        {ContentBlockId{"root"}, 2, ContentBlockKind::Heading, std::nullopt, "Root"},
        {ContentBlockId{"a"}, 3, ContentBlockKind::Paragraph, ContentBlockId{"root"}, "Alpha"},
        {ContentBlockId{"a-child"}, 1, ContentBlockKind::Code, ContentBlockId{"a"}, "code"},
        {ContentBlockId{"b"}, 1, ContentBlockKind::Paragraph, ContentBlockId{"root"}, "Beta"},
        {ContentBlockId{"tail"}, 1, ContentBlockKind::Paragraph, std::nullopt, "Tail"}
    }};
}
}

int main() {
    try {
        const auto document = fixture();
        const auto outline = build_canonical_outline(document);
        expect(outline && outline->entries.size() == 5U, "outline was not built");
        expect(outline->entries[2].locator.document_revision == 4U &&
                   outline->entries[2].locator.block_id == ContentBlockId{"a-child"} &&
                   outline->entries[2].depth == 2U && outline->entries[1].child_count == 1U,
               "outline locator or hierarchy is incorrect");

        const auto subtree = read_canonical_subtree(document,
            {ContentBlockId{"a"}, 10U, 100U, true});
        expect(subtree.status == CanonicalNavigationStatus::Ok && subtree.blocks.size() == 2U &&
                   subtree.blocks[0].id == ContentBlockId{"a"} &&
                   subtree.blocks[1].id == ContentBlockId{"a-child"},
               "subtree did not preserve canonical preorder");

        const auto metadata_only = read_canonical_subtree(document,
            {std::nullopt, 10U, 0U, false});
        expect(metadata_only.status == CanonicalNavigationStatus::Ok &&
                   metadata_only.blocks.size() == 5U && metadata_only.blocks[0].text.empty(),
               "metadata-only navigation unexpectedly decoded text");

        const auto bounded = read_canonical_subtree(document,
            {std::nullopt, 2U, 100U, true});
        expect(bounded.status == CanonicalNavigationStatus::BudgetExceeded && bounded.truncated &&
                   bounded.blocks.size() == 2U && bounded.document_revision == 4U,
               "block budget did not fail explicitly");

        const auto missing = read_canonical_subtree(document,
            {ContentBlockId{"missing"}, 10U, 100U, true});
        expect(missing.status == CanonicalNavigationStatus::NotFound && missing.blocks.empty(),
               "missing root was not rejected");

        auto malformed = document;
        malformed.blocks[2].parent_id = ContentBlockId{"missing"};
        expect(!build_canonical_outline(malformed), "dangling parent was accepted");

        std::cout << "canonical navigation contract: PASS\n";
        return 0;
    } catch(const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
}
