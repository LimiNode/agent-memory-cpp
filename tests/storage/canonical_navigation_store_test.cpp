#include <agent_memory/storage/CanonicalNavigationStore.hpp>

#include <iostream>
#include <stdexcept>
#include <string>

namespace {

using namespace agent_memory;

[[noreturn]] void fail(const char* message) {
    throw std::runtime_error(message);
}

void expect(bool condition, const char* message) {
    if(!condition) {
        fail(message);
    }
}

CanonicalDocumentRevision make_document() {
    return {
        DocumentId{"nav-store"},
        7,
        {},
        {{ContentBlockId{"root"}, 2, ContentBlockKind::Heading, std::nullopt, "Root"},
         {ContentBlockId{"first"}, 1, ContentBlockKind::Paragraph,
          ContentBlockId{"root"}, "First"},
         {ContentBlockId{"first-child"}, 1, ContentBlockKind::Code,
          ContentBlockId{"first"}, "code"},
         {ContentBlockId{"second"}, 3, ContentBlockKind::Paragraph,
          ContentBlockId{"root"}, "Second"},
         {ContentBlockId{"tail"}, 1, ContentBlockKind::Paragraph, std::nullopt,
          "Tail"}}
    };
}

class CountingStore final : public ICanonicalContentStore {
public:
    explicit CountingStore(CanonicalDocumentRevision value)
        : value(std::move(value)) {}

    [[nodiscard]] std::optional<CanonicalDocumentRevision> read_current(
        const DocumentId&) const override {
        ++current_calls;
        return value;
    }

    [[nodiscard]] std::optional<CanonicalDocumentRevision> read_revision(
        const DocumentId& id, std::uint64_t revision) const override {
        ++revision_calls;
        requested_id = id;
        requested_revision = revision;
        if(id != value.document_id || revision != value.revision) {
            return std::nullopt;
        }
        return value;
    }

    [[nodiscard]] std::optional<ContentBlock> read_block(
        const DocumentId&, const ContentBlockId&,
        std::optional<std::uint64_t>) const override {
        ++block_calls;
        return std::nullopt;
    }

    [[nodiscard]] std::optional<std::string> materialize_markdown(
        const DocumentId&, std::optional<std::uint64_t>) const override {
        ++materialize_calls;
        return std::nullopt;
    }

    mutable std::size_t current_calls = 0;
    mutable std::size_t revision_calls = 0;
    mutable std::size_t block_calls = 0;
    mutable std::size_t materialize_calls = 0;
    mutable DocumentId requested_id;
    mutable std::uint64_t requested_revision = 0;
    CanonicalDocumentRevision value;
};

} // namespace

int main() {
    try {
        CountingStore storage(make_document());
        CanonicalNavigationStore navigation(storage);

        const auto outline = navigation.read_outline(DocumentId{"nav-store"}, 7, 16);
        expect(outline && outline->document_revision == 7 &&
                   outline->entries.size() == 5U,
               "revision-bound outline was not returned");
        expect(storage.revision_calls == 1U && storage.current_calls == 0U &&
                   storage.block_calls == 0U && storage.materialize_calls == 0U &&
                   storage.requested_revision == 7U,
               "outline did not use one exact revision read");

        const auto children = navigation.read_children(
            DocumentId{"nav-store"}, 7, ContentBlockId{"root"}, 1);
        expect(children.status == CanonicalNavigationStatus::BudgetExceeded &&
                   children.truncated && children.entries.size() == 1U &&
                   children.entries.front().locator.block_id == ContentBlockId{"first"},
               "children budget was not explicit");
        expect(storage.revision_calls == 2U,
               "children navigation performed more than one revision read");

        const auto subtree = navigation.read_subtree(
            DocumentId{"nav-store"}, 7,
            {ContentBlockId{"first"}, 8U, 64U, true});
        expect(subtree.status == CanonicalNavigationStatus::Ok &&
                   subtree.blocks.size() == 2U && subtree.document_revision == 7U,
               "revision-bound subtree was not returned");
        expect(storage.revision_calls == 3U,
               "subtree navigation performed more than one revision read");

        const auto zero_children_missing = navigation.read_children(
            DocumentId{"missing"}, 7, std::nullopt, 0);
        expect(zero_children_missing.status == CanonicalNavigationStatus::NotFound &&
                   storage.revision_calls == 4U,
               "zero child budget masked a missing exact revision");

        const auto zero_subtree_missing = navigation.read_subtree(
            DocumentId{"missing"}, 7, {std::nullopt, 0U, 0U, true});
        expect(zero_subtree_missing.status == CanonicalNavigationStatus::NotFound &&
                   storage.revision_calls == 5U,
               "zero block budget masked a missing exact revision");

        const auto zero_children_existing = navigation.read_children(
            DocumentId{"nav-store"}, 7, std::nullopt, 0);
        expect(zero_children_existing.status == CanonicalNavigationStatus::InvalidDocument &&
                   storage.revision_calls == 6U,
               "zero child budget did not remain invalid for an existing revision");

        const auto text_limited = navigation.read_subtree(
            DocumentId{"nav-store"}, 7,
            {std::nullopt, 16U, 3U, true});
        expect(text_limited.status == CanonicalNavigationStatus::BudgetExceeded &&
                   text_limited.truncated && text_limited.blocks.size() == 0U,
               "text budget did not stop before admitting an oversized block");

        expect(!navigation.read_outline(DocumentId{"nav-store"}, 6),
               "wrong revision was read as current");

        auto malformed = make_document();
        malformed.blocks[0].parent_id = ContentBlockId{"first"};
        CountingStore malformed_storage(std::move(malformed));
        CanonicalNavigationStore malformed_navigation(malformed_storage);
        const auto malformed_children = malformed_navigation.read_children(
            DocumentId{"nav-store"}, 7, std::nullopt, 16);
        expect(malformed_children.status == CanonicalNavigationStatus::InvalidDocument,
               "cyclic canonical structure was accepted");

        const auto missing_parent = navigation.read_children(
            DocumentId{"nav-store"}, 7, ContentBlockId{"missing"}, 16);
        expect(missing_parent.status == CanonicalNavigationStatus::NotFound,
               "missing parent was accepted");

        std::cout << "canonical navigation storage contract: PASS\n";
        return 0;
    } catch(const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
}
