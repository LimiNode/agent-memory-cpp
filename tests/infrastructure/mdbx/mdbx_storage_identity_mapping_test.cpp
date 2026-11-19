#include <agent_memory/infrastructure/mdbx/MdbxStorageIdentityMapping.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>

namespace {

using namespace agent_memory;

void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}

std::filesystem::path test_path() {
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path() /
        ("agent_memory_storage_identity_mdbx_" + std::to_string(nonce) + ".mdbx");
}

} // namespace

int main() {
    const auto path = test_path();
    try {
        MdbxStorageIdentityMappingOptions options;
        options.path = path.string();
        options.table_prefix = "identity pilot";
        {
            MdbxStorageIdentityMapping mapping(options);
            const DocumentId document{"doc:one"};
            const ContentBlockId block{"block:one"};

            const auto document_id = mapping.ensure_document(document);
            expect(document_id.value() == 1U, "document allocation did not start at one");
            expect(mapping.ensure_document(document) == document_id,
                   "document intern was not idempotent");
            const auto block_id = mapping.ensure_block(document, block);
            expect(block_id.value() == 1U, "block allocation did not start at one");
            expect(mapping.resolve_document(document_id) == std::optional<DocumentId>{document},
                   "document reverse resolution failed");
            expect(mapping.resolve_block(block_id) ==
                       std::optional<std::pair<DocumentId, ContentBlockId>>{{document, block}},
                   "block reverse resolution failed");

            expect(mapping.retire_document(document), "document retirement failed");
            bool reuse_rejected = false;
            try {
                static_cast<void>(mapping.ensure_document(document));
            } catch(const std::runtime_error&) {
                reuse_rejected = true;
            }
            expect(reuse_rejected, "retired document identity was reused");
            expect(mapping.find_document(document) == std::optional<DocumentStorageId>{document_id},
                   "retired document mapping was lost");
            bool block_reuse_rejected = false;
            try {
                static_cast<void>(mapping.ensure_block(document, block));
            } catch(const std::runtime_error&) {
                block_reuse_rejected = true;
            }
            expect(block_reuse_rejected, "document retirement did not retire its blocks");
            const auto snapshot = mapping.snapshot_records();
            expect(snapshot.size() == 2U, "snapshot did not contain document and block");
        }

        MdbxStorageIdentityMapping reopened(options);
        const DocumentId document{"doc:one"};
        const ContentBlockId block{"block:one"};
        expect(reopened.resolve_document(DocumentStorageId{1}) ==
                   std::optional<DocumentId>{document},
               "document reverse mapping did not survive reopen");
        expect(reopened.resolve_block(BlockStorageId{1}) ==
                   std::optional<std::pair<DocumentId, ContentBlockId>>{{document, block}},
               "block reverse mapping did not survive reopen");

        const DocumentId imported{"doc:imported"};
        const ContentBlockId imported_block{"block:imported"};
        reopened.load_records({
            DocumentStorageIdentityRecord{DocumentStorageId{7}, imported, false},
            BlockStorageIdentityRecord{BlockStorageId{9}, imported, imported_block, false}
        });
        expect(reopened.ensure_document(imported).value() == 7U,
               "validated snapshot import changed document identity");
        expect(reopened.ensure_block(imported, imported_block).value() == 9U,
               "validated snapshot import changed block identity");
        expect(reopened.ensure_document(DocumentId{"doc:next"}).value() == 8U,
               "snapshot import did not advance document sequence");
        expect(reopened.ensure_block(imported, ContentBlockId{"block:next"}).value() == 10U,
               "snapshot import did not advance block sequence");
        expect(!reopened.find_document(document), "snapshot replacement left stale document");

        std::error_code error;
        std::filesystem::remove(path, error);
        std::cout << "mdbx storage identity mapping: PASS\n";
        return 0;
    } catch(const std::exception& exception) {
        std::error_code error;
        std::filesystem::remove(path, error);
        std::cerr << exception.what() << '\n';
        return 1;
    }
}

#else
int main() { return 0; }
#endif
