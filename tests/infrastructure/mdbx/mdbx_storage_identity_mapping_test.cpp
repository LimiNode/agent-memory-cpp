#include <agent_memory/infrastructure/mdbx/MdbxStorageIdentityMapping.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <mdbx_containers/KeyValueTable.hpp>

#include <chrono>
#include <filesystem>
#include <iostream>
#include <limits>
#include <optional>
#include <stdexcept>
#include <variant>

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
            bool unknown_parent_rejected = false;
            try {
                static_cast<void>(mapping.ensure_block(
                    DocumentId{"doc:missing"}, ContentBlockId{"block:missing"}));
            } catch(const std::invalid_argument&) {
                unknown_parent_rejected = true;
            }
            expect(unknown_parent_rejected,
                   "block mapping accepted an unassigned document");
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
            } catch(const std::invalid_argument&) {
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

        // A destructive partial snapshot may remove rows, but it must not
        // lower the numeric high-water mark and thereby recycle an old id.
        reopened.load_records({
            DocumentStorageIdentityRecord{DocumentStorageId{1}, DocumentId{"doc:partial"}, false}
        });
        expect(
            reopened.ensure_document(DocumentId{"doc:after-partial"}).value() == 9U,
            "partial snapshot recycled a previously allocated document id"
        );

        reopened.load_records({
            DocumentStorageIdentityRecord{DocumentStorageId{1}, DocumentId{"doc:tie"}, false},
            BlockStorageIdentityRecord{
                BlockStorageId{1}, DocumentId{"doc:tie"}, ContentBlockId{"block:tie"}, false
            }
        });
        const auto tied_snapshot = reopened.snapshot_records();
        expect(
            tied_snapshot.size() == 2U
                && std::holds_alternative<DocumentStorageIdentityRecord>(tied_snapshot.front()),
            "snapshot ordering is not deterministic across equal document/block ids"
        );

        const auto markerless_path = test_path();
        {
            auto context = MdbxStorageContext::open(markerless_path.string());
            mdbxc::KeyValueTable<std::string, std::string> records(
                context->connection(), "identity_pilot_identity_records_v1");
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            records.insert_or_assign("d:8:doc:one", "markerless", txn);
            txn.commit();
        }
        bool markerless_rejected = false;
        try {
            MdbxStorageIdentityMappingOptions markerless_options;
            markerless_options.path = markerless_path.string();
            markerless_options.table_prefix = "identity pilot";
            static_cast<void>(MdbxStorageIdentityMapping(markerless_options));
        } catch(const std::runtime_error&) {
            markerless_rejected = true;
        }
        expect(markerless_rejected, "markerless non-empty mapping was accepted");

        const auto missing_sequence_path = test_path();
        {
            MdbxStorageIdentityMappingOptions sequence_options;
            sequence_options.path = missing_sequence_path.string();
            sequence_options.table_prefix = "identity pilot";
            MdbxStorageIdentityMapping mapping(sequence_options);
            static_cast<void>(mapping.ensure_document(DocumentId{"doc:sequence"}));
        }
        {
            auto context = MdbxStorageContext::open(missing_sequence_path.string());
            mdbxc::KeyValueTable<std::string, std::string> metadata(
                context->connection(), "identity_pilot_identity_meta_v1");
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            metadata.erase("@next_document_id", txn);
            txn.commit();
        }
        bool missing_sequence_rejected = false;
        try {
            MdbxStorageIdentityMappingOptions sequence_options;
            sequence_options.path = missing_sequence_path.string();
            sequence_options.table_prefix = "identity pilot";
            static_cast<void>(MdbxStorageIdentityMapping(sequence_options));
        } catch(const std::runtime_error&) {
            missing_sequence_rejected = true;
        }
        expect(missing_sequence_rejected, "missing sequence metadata was accepted");

        const auto reverse_mismatch_path = test_path();
        {
            MdbxStorageIdentityMappingOptions mismatch_options;
            mismatch_options.path = reverse_mismatch_path.string();
            mismatch_options.table_prefix = "identity pilot";
            MdbxStorageIdentityMapping mapping(mismatch_options);
            static_cast<void>(mapping.ensure_document(DocumentId{"doc:mismatch"}));
        }
        {
            auto context = MdbxStorageContext::open(reverse_mismatch_path.string());
            mdbxc::KeyValueTable<std::uint64_t, std::string> reverse(
                context->connection(), "identity_pilot_identity_documents_v1");
            auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            reverse.insert_or_assign(1U, "d:13:doc:other", txn);
            txn.commit();
        }
        bool reverse_mismatch_rejected = false;
        try {
            MdbxStorageIdentityMappingOptions mismatch_options;
            mismatch_options.path = reverse_mismatch_path.string();
            mismatch_options.table_prefix = "identity pilot";
            MdbxStorageIdentityMapping mapping(mismatch_options);
            static_cast<void>(mapping.ensure_document(DocumentId{"doc:mismatch"}));
        } catch(const std::runtime_error&) {
            reverse_mismatch_rejected = true;
        }
        expect(reverse_mismatch_rejected, "reverse mapping mismatch was accepted");

        const auto bounded_path = test_path();
        {
            MdbxStorageIdentityMappingOptions bounded_options;
            bounded_options.path = bounded_path.string();
            bounded_options.table_prefix = "identity bounded";
            bounded_options.max_records = 1U;
            MdbxStorageIdentityMapping bounded(bounded_options);
            static_cast<void>(bounded.ensure_document(DocumentId{"doc:bounded"}));
            bool allocation_limit_rejected = false;
            try {
                static_cast<void>(bounded.ensure_document(DocumentId{"doc:overflow"}));
            } catch(const std::length_error&) {
                allocation_limit_rejected = true;
            }
            expect(allocation_limit_rejected, "allocation ignored max_records");
        }

        const auto transaction_path = test_path();
        {
            auto context = MdbxStorageContext::open(transaction_path.string());
            MdbxStorageIdentityMappingOptions transaction_options;
            transaction_options.table_prefix = "identity transaction";
            transaction_options.context = context;
            MdbxStorageIdentityMapping transaction_mapping(transaction_options);
            mdbxc::KeyValueTable<std::string, std::string> probe(
                context->connection(), "identity_transaction_probe");
            auto transaction = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            const auto assigned = transaction_mapping.ensure_document_in_transaction(
                DocumentId{"doc:transaction"}, transaction);
            probe.insert_or_assign("marker", std::to_string(assigned.value()), transaction);
            transaction.commit();
            expect(assigned.value() == 1U, "transaction-scoped allocation changed id");
            auto read = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
            expect(probe.find("marker", read) == std::optional<std::string>{"1"},
                   "mapping and caller write did not share one transaction");
            read.commit();

            auto rolled_back = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            static_cast<void>(transaction_mapping.ensure_document_in_transaction(
                DocumentId{"doc:rolled-back"}, rolled_back));
            rolled_back.rollback();
            expect(transaction_mapping.ensure_document(DocumentId{"doc:rolled-back"}).value() == 2U,
                   "transaction rollback did not discard mapping allocation");
        }

        std::error_code error;
        std::filesystem::remove(path, error);
        std::filesystem::remove(markerless_path, error);
        std::filesystem::remove(missing_sequence_path, error);
        std::filesystem::remove(reverse_mismatch_path, error);
        std::filesystem::remove(bounded_path, error);
        std::filesystem::remove(transaction_path, error);
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
