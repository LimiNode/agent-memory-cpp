#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.hpp>
#include <agent_memory/storage/CanonicalNavigationStore.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <filesystem>
#include <iostream>
#include <mdbx_containers/KeyValueTable.hpp>
#include <string_view>
#include <stdexcept>

namespace {

using namespace agent_memory;

[[noreturn]] void fail(const char* message) { throw std::runtime_error(message); }
void expect(bool condition, const char* message) { if(!condition) fail(message); }

std::string row_key(const std::string& document_id, std::uint64_t revision) {
    return document_id + std::string{"\x1f"} + std::to_string(revision);
}

std::string body_key(const std::string& document_id, std::uint64_t revision) {
    return row_key(document_id, revision) + std::string{"\x1f"} + "1";
}

std::size_t read_length_field(std::string_view payload, std::size_t& position) {
    const auto marker = payload.find(':', position);
    if (marker == std::string_view::npos)
        fail("revision payload length marker was not found");
    const auto length = std::stoull(std::string(payload.substr(position, marker - position)));
    position = marker + 1U;
    return static_cast<std::size_t>(length);
}

void skip_length_field(std::string_view payload, std::size_t& position) {
    const auto length = read_length_field(payload, position);
    if (length > payload.size() - position)
        fail("revision payload length exceeded the value");
    position += length;
}

void make_revision_block_count_oversized(std::string& payload) {
    std::size_t position = 0U;
    skip_length_field(payload, position); // codec version
    skip_length_field(payload, position); // body revision
    if (payload.size() - position < 33U)
        fail("revision payload digest was truncated");
    position += 33U; // digest algorithm tag and SHA-256 bytes
    skip_length_field(payload, position); // document ID
    skip_length_field(payload, position); // document revision
    const auto metadata_count = read_length_field(payload, position);
    for (std::size_t index = 0; index < metadata_count; ++index) {
        skip_length_field(payload, position);
        skip_length_field(payload, position);
    }
    const auto count_start = position;
    read_length_field(payload, position);
    payload.replace(count_start, position - count_start, "1048577:");
}

CanonicalDocumentRevision fixture() {
    return {
        DocumentId{"navigation-mdbx"},
        0,
        {},
        {{ContentBlockId{"root"}, 4, ContentBlockKind::Heading, std::nullopt, "Root"},
         {ContentBlockId{"first"}, 2, ContentBlockKind::Paragraph,
          ContentBlockId{"root"}, "First"},
         {ContentBlockId{"first-child"}, 1, ContentBlockKind::Code,
          ContentBlockId{"first"}, "code"},
         {ContentBlockId{"second"}, 3, ContentBlockKind::Paragraph,
          ContentBlockId{"root"}, "Second"},
         {ContentBlockId{"tail"}, 1, ContentBlockKind::Paragraph, std::nullopt, "Tail"}}
    };
}

bool same_outline(const CanonicalOutline& left, const CanonicalOutline& right) {
    if(left.document_id != right.document_id ||
       left.document_revision != right.document_revision ||
       left.entries.size() != right.entries.size()) {
        return false;
    }
    for(std::size_t index = 0; index < left.entries.size(); ++index) {
        const auto& lhs = left.entries[index];
        const auto& rhs = right.entries[index];
        if(lhs.locator.document_id != rhs.locator.document_id ||
           lhs.locator.document_revision != rhs.locator.document_revision ||
           lhs.locator.block_id != rhs.locator.block_id ||
           lhs.locator.block_revision != rhs.locator.block_revision ||
           lhs.kind != rhs.kind || lhs.parent_id != rhs.parent_id ||
           lhs.depth != rhs.depth || lhs.text_bytes != rhs.text_bytes ||
           lhs.child_count != rhs.child_count) {
            return false;
        }
    }
    return true;
}

bool same_children(const CanonicalChildrenResult& left,
                   const CanonicalChildrenResult& right) {
    if(left.status != right.status || left.truncated != right.truncated ||
       left.document_id != right.document_id ||
       left.document_revision != right.document_revision ||
       left.parent_id != right.parent_id ||
       left.entries.size() != right.entries.size()) {
        return false;
    }
    for(std::size_t index = 0; index < left.entries.size(); ++index) {
        const auto& lhs = left.entries[index];
        const auto& rhs = right.entries[index];
        if(lhs.locator.block_id != rhs.locator.block_id ||
           lhs.locator.block_revision != rhs.locator.block_revision ||
           lhs.kind != rhs.kind || lhs.parent_id != rhs.parent_id ||
           lhs.depth != rhs.depth || lhs.text_bytes != rhs.text_bytes ||
           lhs.child_count != rhs.child_count) {
            return false;
        }
    }
    return true;
}

bool same_subtree(const CanonicalSubtreeResult& left,
                  const CanonicalSubtreeResult& right) {
    if(left.status != right.status || left.truncated != right.truncated ||
       left.document_id != right.document_id ||
       left.document_revision != right.document_revision ||
       left.blocks.size() != right.blocks.size()) {
        return false;
    }
    for(std::size_t index = 0; index < left.blocks.size(); ++index) {
        const auto& lhs = left.blocks[index];
        const auto& rhs = right.blocks[index];
        if(lhs.id != rhs.id || lhs.revision != rhs.revision ||
           lhs.kind != rhs.kind || lhs.parent_id != rhs.parent_id ||
           lhs.text != rhs.text) {
            return false;
        }
    }
    return true;
}

} // namespace

int main() {
    try {
        const auto path = std::filesystem::temp_directory_path() /
            "agent_memory_b4_navigation_test.mdbx";
        std::error_code error;
        std::filesystem::remove(path, error);
        auto context = MdbxStorageContext::open(path.string());
        MdbxCanonicalContentStoreOptions options;
        options.context = context;
        options.table_prefix = "b4 custom prefix";
        MdbxCanonicalContentStore store(std::move(options));
        const auto initial = fixture();
        expect(store.create_document(initial), "initial document was not stored");

        InMemoryCanonicalContentStore reference_store;
        expect(reference_store.create_document(initial),
               "reference document was not stored");
        CanonicalNavigationStore reference_navigation(reference_store);

        const auto outline = store.read_outline(initial.document_id, 0, 16);
        expect(outline && outline->document_revision == 0 && outline->entries.size() == 5U,
               "exact revision outline was not returned");
        expect(outline->entries[1].text_bytes == 5U &&
                   outline->entries[2].depth == 2U && outline->entries[1].child_count == 1U,
               "outline metadata was not reconstructed from storage");
        expect(!store.read_outline(initial.document_id, 0, 1),
               "outline entry budget did not fail closed");

        const auto children = store.read_children(
            initial.document_id, 0, ContentBlockId{"root"}, 1);
        expect(children.status == CanonicalNavigationStatus::BudgetExceeded &&
                   children.truncated && children.entries.size() == 1U &&
                   children.entries.front().locator.block_id == ContentBlockId{"first"},
               "children budget was not explicit");

        const auto reference_outline = reference_navigation.read_outline(
            initial.document_id, 0, 16);
        expect(reference_outline && same_outline(*reference_outline, *outline),
               "backend outline diverged from the reference adapter");

        const auto reference_children = reference_navigation.read_children(
            initial.document_id, 0, ContentBlockId{"root"}, 1);
        expect(same_children(reference_children, children),
               "backend children diverged from the reference adapter");

        const CanonicalSubtreeRequest conformance_request{
            ContentBlockId{"first"}, 4U, 64U, true};
        const auto reference_subtree = reference_navigation.read_subtree(
            initial.document_id, 0, conformance_request);
        expect(same_subtree(reference_subtree,
                            store.read_subtree(initial.document_id, 0,
                                               conformance_request)),
               "backend subtree diverged from the reference adapter");

        const CanonicalSubtreeRequest metadata_request{
            ContentBlockId{"first"}, 16U, 0U, false};
        expect(same_subtree(
                   reference_navigation.read_subtree(
                       initial.document_id, 0, metadata_request),
                   store.read_subtree(initial.document_id, 0, metadata_request)),
               "backend metadata-only subtree diverged from the reference adapter");

        const CanonicalSubtreeRequest text_budget_request{
            std::nullopt, 16U, 3U, true};
        expect(same_subtree(
                   reference_navigation.read_subtree(
                       initial.document_id, 0, text_budget_request),
                   store.read_subtree(initial.document_id, 0, text_budget_request)),
               "backend text-budget outcome diverged from the reference adapter");

        const auto reference_missing = reference_navigation.read_children(
            DocumentId{"missing"}, 0, std::nullopt, 0);
        expect(same_children(reference_missing,
                             store.read_children(DocumentId{"missing"}, 0,
                                                 std::nullopt, 0)),
               "backend missing-revision precedence diverged from the reference adapter");

        const auto zero_children_missing = store.read_children(
            DocumentId{"missing"}, 0, std::nullopt, 0);
        expect(zero_children_missing.status == CanonicalNavigationStatus::NotFound,
               "zero child budget masked a missing exact revision");

        const auto zero_subtree_missing = store.read_subtree(
            DocumentId{"missing"}, 0, {std::nullopt, 0U, 0U, true});
        expect(zero_subtree_missing.status == CanonicalNavigationStatus::NotFound,
               "zero block budget masked a missing exact revision");

        const auto zero_children_existing = store.read_children(
            initial.document_id, 0, std::nullopt, 0);
        expect(zero_children_existing.status == CanonicalNavigationStatus::InvalidDocument,
               "zero child budget did not remain invalid for an existing revision");

        const auto zero_subtree_existing = store.read_subtree(
            initial.document_id, 0, {std::nullopt, 0U, 0U, true});
        expect(zero_subtree_existing.status == CanonicalNavigationStatus::InvalidDocument,
               "zero block budget did not remain invalid for an existing revision");

        mdbxc::KeyValueTable<std::string, std::string> revisions(
            context->connection(), "b4_custom_prefix_canonical_revisions");
        auto revision_transaction =
            context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        auto revision_payload = revisions.find(row_key("navigation-mdbx", 0), revision_transaction);
        expect(revision_payload.has_value(), "revision row was not found");
        const auto original_revision_payload = *revision_payload;
        make_revision_block_count_oversized(*revision_payload);
        revisions.insert_or_assign(
            row_key("navigation-mdbx", 0), *revision_payload, revision_transaction);
        revision_transaction.commit();

        bool oversized_children_rejected = false;
        try {
            (void)store.read_children(initial.document_id, 0, std::nullopt, 0);
        } catch(const std::runtime_error&) {
            oversized_children_rejected = true;
        }
        bool oversized_subtree_rejected = false;
        try {
            (void)store.read_subtree(initial.document_id, 0,
                                     {std::nullopt, 0U, 0U, false});
        } catch(const std::runtime_error&) {
            oversized_subtree_rejected = true;
        }
        expect(oversized_children_rejected && oversized_subtree_rejected,
               "zero-budget navigation decoded an oversized revision");

        revision_transaction =
            context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        revisions.insert_or_assign(
            row_key("navigation-mdbx", 0), original_revision_payload, revision_transaction);
        revision_transaction.commit();

        const auto metadata_only = store.read_subtree(
            initial.document_id, 0, {ContentBlockId{"first"}, 16U, 0U, false});
        expect(metadata_only.status == CanonicalNavigationStatus::Ok &&
                   metadata_only.blocks.size() == 2U && metadata_only.blocks[0].text.empty(),
               "metadata-only subtree decoded text");

        const auto text_limited = store.read_subtree(
            initial.document_id, 0, {std::nullopt, 16U, 3U, true});
        expect(text_limited.status == CanonicalNavigationStatus::BudgetExceeded &&
                   text_limited.truncated && text_limited.blocks.empty(),
               "text budget did not stop before admitting an oversized block");

        const auto zero_text_budget = store.read_subtree(
            initial.document_id, 0, {std::nullopt, 16U, 0U, true});
        expect(zero_text_budget.status == CanonicalNavigationStatus::BudgetExceeded &&
                   zero_text_budget.truncated && zero_text_budget.blocks.empty(),
               "zero text budget admitted a non-empty block");

        const auto bounded = store.read_subtree(
            initial.document_id, 0, {std::nullopt, 2U, 64U, true});
        expect(bounded.status == CanonicalNavigationStatus::BudgetExceeded &&
                   bounded.blocks.size() == 2U && bounded.blocks[1].text == "First",
               "block budget did not preserve bounded text output");

        auto edited = store.commit({initial.document_id, 0,
            {ReplaceBlockText{ContentBlockId{"first"}, "First v2"}}});
        expect(edited.status == CanonicalEditStatus::Ok && edited.revision &&
                   edited.revision->revision == 1U, "historical edit was not published");
        expect(store.read_subtree(initial.document_id, 0,
                   {ContentBlockId{"first"}, 4U, 64U, true}).blocks[0].text == "First",
               "historical revision was not exact");
        expect(store.read_subtree(initial.document_id, 1,
                   {ContentBlockId{"first"}, 4U, 64U, true}).blocks[0].text == "First v2",
               "new revision was not visible by exact pin");

        mdbxc::KeyValueTable<std::string, std::string> bodies(
            context->connection(), "b4_custom_prefix_canonical_bodies");
        auto transaction = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        auto payload = bodies.find(body_key("navigation-mdbx", 2), transaction);
        expect(payload.has_value() && !payload->empty(), "body row was not found");
        payload->back() = payload->back() == '0' ? '1' : '0';
        bodies.insert_or_assign(body_key("navigation-mdbx", 2), *payload, transaction);
        transaction.commit();
        bool corruption_rejected = false;
        try {
            (void)store.read_outline(initial.document_id, 1, 16);
        } catch(const std::runtime_error&) {
            corruption_rejected = true;
        }
        expect(corruption_rejected, "corrupted body was accepted by navigation");

        std::filesystem::remove(path, error);
        std::cout << "mdbx canonical navigation contract: PASS\n";
        return 0;
    } catch(const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
}
#else
int main() { return 0; }
#endif
