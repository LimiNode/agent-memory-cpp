#include <agent_memory/infrastructure/mdbx/MdbxCanonicalFlatStorage.hpp>
#include <mdbx_containers/KeyValueTable.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <filesystem>
#include <iostream>
#include <stdexcept>

using namespace agent_memory;

namespace {

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path temporary_path(const char* suffix) {
    return std::filesystem::temp_directory_path() /
        (std::string{"agent_memory_canonical_flat_"} + suffix + ".mdbx");
}

CanonicalDocumentRevision make_revision() {
    CanonicalDocumentRevision result;
    result.document_id = DocumentId{"flat-doc"};
    result.metadata.set("owner", "test");
    result.blocks = {
        ContentBlock{ContentBlockId{"h"}, 1, ContentBlockKind::Heading, std::nullopt, "Heading"},
        ContentBlock{ContentBlockId{"p"}, 1, ContentBlockKind::Paragraph,
                     std::optional<ContentBlockId>{ContentBlockId{"h"}}, "Payload"},
        ContentBlock{ContentBlockId{"c"}, 1, ContentBlockKind::Code,
                     std::optional<ContentBlockId>{ContentBlockId{"p"}}, "return 7;"}};
    return result;
}

} // namespace

int main() {
    const auto path = temporary_path("layout");
    std::error_code error;
    std::filesystem::remove(path, error);
    try {
        MdbxCanonicalFlatStorageOptions options;
        options.path = path.string();
        options.table_prefix = "flat test";
        {
            MdbxCanonicalFlatStorage store(options);
            expect(store.create_document(make_revision()), "flat document creation failed");
            expect(!store.create_document(make_revision()), "duplicate flat document was accepted");
            const auto current = store.read_current(DocumentId{"flat-doc"});
            expect(current && current->revision == 0U && current->blocks.size() == 3U,
                   "flat current read failed");
            const auto block = store.read_block(DocumentId{"flat-doc"}, ContentBlockId{"c"});
            expect(block && block->text == "return 7;", "flat point block read failed");

            CanonicalEditRequest edit;
            edit.document_id = DocumentId{"flat-doc"};
            edit.expected_revision = 0;
            edit.operations.push_back(ReplaceBlockText{ContentBlockId{"p"}, "Changed"});
            const auto result = store.commit(edit);
            expect(result.status == CanonicalEditStatus::Ok && result.revision,
                   "flat edit failed");
            expect(store.read_revision(DocumentId{"flat-doc"}, 0)->blocks[1].text == "Payload",
                   "flat historical revision was not retained");
            expect(store.read_current(DocumentId{"flat-doc"})->blocks[1].text == "Changed",
                   "flat current revision was not updated");
        }
        {
            MdbxCanonicalFlatStorage reopened(options);
            expect(reopened.read_current(DocumentId{"flat-doc"})->blocks[1].text == "Changed",
                   "flat reopen lost current revision");
            expect(reopened.read_block(DocumentId{"flat-doc"}, ContentBlockId{"h"}, 0)->text == "Heading",
                   "flat historical point read failed");
        }
        std::filesystem::remove(path, error);

        const auto markerless = temporary_path("markerless");
        std::filesystem::remove(markerless, error);
        {
            auto context = MdbxStorageContext::open(markerless.string());
            mdbxc::KeyValueTable<std::string, std::string> rows(
                context->connection(), "flat_test_canonical_flat_structures_v1");
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            rows.insert_or_assign("1/1", "forged", txn);
            txn.commit();
        }
        bool rejected = false;
        try {
            MdbxCanonicalFlatStorageOptions markerless_options;
            markerless_options.path = markerless.string();
            markerless_options.table_prefix = "flat test";
            MdbxCanonicalFlatStorage ignored(markerless_options);
        } catch(const std::runtime_error&) {
            rejected = true;
        }
        expect(rejected, "markerless flat state was accepted");
        std::filesystem::remove(markerless, error);
    } catch(const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        std::filesystem::remove(path, error);
        return 1;
    }
    return 0;
}

#else

int main() { return 0; }

#endif
