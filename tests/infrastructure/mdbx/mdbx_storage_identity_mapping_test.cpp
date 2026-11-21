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

template<typename Operation>
void expect_invalid(Operation operation, const char* message) {
    try {
        operation();
    } catch(const std::invalid_argument&) {
        return;
    }
    throw std::runtime_error(message);
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

        const auto historical = reopened.snapshot_records();
        expect_invalid([&] { reopened.load_records({
            DocumentStorageIdentityRecord{DocumentStorageId{1}, DocumentId{"doc:other"}, false}
        }); }, "live import rebound document A/1 to B/1");
        expect_invalid([&] { reopened.load_records({}); }, "deletion snapshot was accepted");
        expect_invalid([&] { reopened.load_records({historical.front()}); },
                       "partial snapshot deleted historical mappings");
        expect_invalid([&] { reopened.load_records(historical); },
                       "live namespace accepted even an identical replacement snapshot");
        expect(reopened.resolve_document(DocumentStorageId{1}) == std::optional<DocumentId>{document}
               && reopened.resolve_block(BlockStorageId{1}) ==
                    std::optional<std::pair<DocumentId, ContentBlockId>>{{document, block}},
               "rejected import damaged historical bindings");
        expect(reopened.ensure_document(DocumentId{"doc:after-rejected-import"}).value() == 2U,
               "rejected imports changed the next numeric assignment");

        const auto import_path = test_path();
        {
            MdbxStorageIdentityMappingOptions import_options;
            import_options.path = import_path.string();
            MdbxStorageIdentityMapping fresh(import_options);
            const DocumentId imported{"doc:other"};
            const ContentBlockId imported_block{"block:imported"};
            expect_invalid([&] { fresh.load_records({
                DocumentStorageIdentityRecord{DocumentStorageId{1}, imported, false},
                DocumentStorageIdentityRecord{DocumentStorageId{2}, imported, false}
            }); }, "duplicate logical identity import was accepted");
            expect_invalid([&] { fresh.load_records({
                DocumentStorageIdentityRecord{DocumentStorageId{1}, imported, false},
                DocumentStorageIdentityRecord{DocumentStorageId{1}, DocumentId{"doc:duplicate"}, false}
            }); }, "duplicate numeric identity import was accepted");
            fresh.load_records({
                DocumentStorageIdentityRecord{DocumentStorageId{1}, imported, false},
                BlockStorageIdentityRecord{BlockStorageId{1}, imported, imported_block, false},
                DocumentStorageIdentityRecord{DocumentStorageId{7}, DocumentId{"doc:retired"}, true},
                BlockStorageIdentityRecord{BlockStorageId{9}, DocumentId{"doc:retired"},
                                           ContentBlockId{"block:retired"}, true}
            });
            expect(fresh.ensure_document(imported).value() == 1U,
                   "explicit remapping into a fresh namespace failed");
            expect(fresh.ensure_document(DocumentId{"doc:next"}).value() == 8U,
                   "import did not retain retired document high-water");
            expect(fresh.ensure_block(imported, ContentBlockId{"block:next"}).value() == 10U,
                   "import did not retain retired block high-water");
            bool retired_rejected = false;
            try { static_cast<void>(fresh.ensure_document(DocumentId{"doc:retired"})); }
            catch(const std::runtime_error&) { retired_rejected = true; }
            expect(retired_rejected, "imported retired identity was reused");
            const auto snapshot = fresh.snapshot_records();
            expect(snapshot.size() == 6U
                   && std::holds_alternative<DocumentStorageIdentityRecord>(snapshot.front()),
                   "snapshot ordering is not deterministic across equal numeric spaces");
        }
        {
            MdbxStorageIdentityMappingOptions import_options;
            import_options.path = import_path.string();
            MdbxStorageIdentityMapping imported_reopen(import_options);
            expect(imported_reopen.resolve_document(DocumentStorageId{7}) ==
                       std::optional<DocumentId>{DocumentId{"doc:retired"}},
                   "retired imported mapping did not survive reopen");
            expect(imported_reopen.ensure_document(DocumentId{"doc:after-reopen"}).value() == 9U,
                   "imported high-water did not survive reopen");
        }

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
            auto context = MdbxStorageContext::open(bounded_path.string());
            MdbxStorageIdentityMappingOptions bounded_options;
            bounded_options.context = context;
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
            allocation_limit_rejected = false;
            try {
                static_cast<void>(bounded.ensure_block(
                    DocumentId{"doc:bounded"}, ContentBlockId{"block:overflow"}));
            } catch(const std::length_error&) { allocation_limit_rejected = true; }
            expect(allocation_limit_rejected, "block allocation ignored max_records");
            expect_invalid([&] { bounded.load_records({
                DocumentStorageIdentityRecord{DocumentStorageId{1}, DocumentId{"x"}, false},
                DocumentStorageIdentityRecord{DocumentStorageId{2}, DocumentId{"y"}, false}
            }); }, "oversized import ignored max_records");

            // A poison row proves the hard size guard runs before any payload
            // decoding, rather than materializing the complete table first.
            mdbxc::KeyValueTable<std::string, std::string> rows(
                context->connection(), "identity_bounded_identity_records_v1");
            auto write = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            rows.insert_or_assign("a:poison", "not-an-identity-record", write);
            write.commit();
            bool snapshot_limit_rejected = false;
            try { static_cast<void>(bounded.snapshot_records()); }
            catch(const std::length_error&) { snapshot_limit_rejected = true; }
            expect(snapshot_limit_rejected, "snapshot traversed oversized state before its hard bound");
            bool retirement_limit_rejected = false;
            try { static_cast<void>(bounded.retire_document(DocumentId{"doc:bounded"})); }
            catch(const std::length_error&) { retirement_limit_rejected = true; }
            expect(retirement_limit_rejected, "retirement traversed oversized state before its hard bound");
            expect(bounded.ensure_document(DocumentId{"doc:bounded"}).value() == 1U,
                   "oversized retirement partially retired the document");

            MdbxStorageIdentityMappingOptions corrupt_options;
            corrupt_options.context = context;
            corrupt_options.table_prefix = "identity corrupt snapshot";
            MdbxStorageIdentityMapping corrupt(corrupt_options);
            mdbxc::KeyValueTable<std::string, std::string> corrupt_rows(
                context->connection(), "identity_corrupt_snapshot_identity_records_v1");
            auto corruption = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            corrupt_rows.insert_or_assign("d:5:wrong", encode_storage_identity_record(
                DocumentStorageIdentityRecord{DocumentStorageId{1}, DocumentId{"right"}, false}),
                corruption);
            corruption.commit();
            bool key_mismatch_rejected = false;
            try { static_cast<void>(corrupt.snapshot_records()); }
            catch(const std::runtime_error&) { key_mismatch_rejected = true; }
            expect(key_mismatch_rejected, "snapshot admitted a mismatched physical key");
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
            const auto assigned_block = transaction_mapping.ensure_block_in_transaction(
                DocumentId{"doc:transaction"}, ContentBlockId{"block:transaction"}, transaction);
            expect(transaction_mapping.find_document_in_transaction(
                       DocumentId{"doc:transaction"}, transaction) == assigned
                   && transaction_mapping.find_block_in_transaction(
                       DocumentId{"doc:transaction"}, ContentBlockId{"block:transaction"},
                       transaction) == assigned_block,
                   "mapping reads did not see caller's uncommitted writes");
            probe.insert_or_assign("marker", std::to_string(assigned.value()), transaction);
            transaction.commit();
            expect(assigned.value() == 1U, "transaction-scoped allocation changed id");
            auto read = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
            expect(probe.find("marker", read) == std::optional<std::string>{"1"},
                   "mapping and caller write did not share one transaction");
            expect_invalid([&] { static_cast<void>(transaction_mapping.ensure_document_in_transaction(
                DocumentId{"doc:read-only"}, read)); }, "read-only transaction admitted a document");
            expect_invalid([&] { static_cast<void>(transaction_mapping.ensure_block_in_transaction(
                DocumentId{"doc:transaction"}, ContentBlockId{"block:read-only"}, read)); },
                "read-only transaction admitted a block");
            read.commit();
            expect_invalid([&] { static_cast<void>(transaction_mapping.find_document_in_transaction(
                DocumentId{}, read)); }, "inactive transaction was accepted for document lookup");
            expect_invalid([&] { static_cast<void>(transaction_mapping.find_block_in_transaction(
                DocumentId{}, ContentBlockId{}, read)); }, "inactive transaction was accepted for block lookup");
            expect_invalid([&] { static_cast<void>(transaction_mapping.ensure_document_in_transaction(
                DocumentId{"inactive"}, read)); }, "inactive transaction was accepted for document allocation");
            expect_invalid([&] { static_cast<void>(transaction_mapping.ensure_block_in_transaction(
                DocumentId{"doc:transaction"}, ContentBlockId{"inactive"}, read)); },
                "inactive transaction was accepted for block allocation");

            auto rolled_back = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            static_cast<void>(transaction_mapping.ensure_document_in_transaction(
                DocumentId{"doc:rolled-back"}, rolled_back));
            static_cast<void>(transaction_mapping.ensure_block_in_transaction(
                DocumentId{"doc:rolled-back"}, ContentBlockId{"block:rolled-back"}, rolled_back));
            rolled_back.rollback();
            expect(transaction_mapping.ensure_document(DocumentId{"doc:rolled-back"}).value() == 2U,
                   "transaction rollback did not discard mapping allocation");
            expect(transaction_mapping.ensure_block(DocumentId{"doc:rolled-back"},
                ContentBlockId{"block:rolled-back"}).value() == 2U,
                "transaction rollback did not discard block allocation");

            const auto foreign_path = test_path();
            {
                auto foreign_context = MdbxStorageContext::open(foreign_path.string());
                MdbxStorageIdentityMappingOptions foreign_options;
                foreign_options.context = foreign_context;
                foreign_options.table_prefix = "identity transaction";
                MdbxStorageIdentityMapping foreign(foreign_options);
                auto foreign_txn = foreign_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
                expect_invalid([&] { static_cast<void>(transaction_mapping.ensure_document_in_transaction(
                    DocumentId{"foreign"}, foreign_txn)); }, "foreign document write transaction was accepted");
                expect_invalid([&] { static_cast<void>(transaction_mapping.ensure_block_in_transaction(
                    DocumentId{"doc:transaction"}, ContentBlockId{"foreign"}, foreign_txn)); },
                    "foreign block write transaction was accepted");
                expect_invalid([&] { static_cast<void>(transaction_mapping.find_document_in_transaction(
                    DocumentId{}, foreign_txn)); }, "foreign document read transaction was accepted");
                expect_invalid([&] { static_cast<void>(transaction_mapping.find_block_in_transaction(
                    DocumentId{}, ContentBlockId{}, foreign_txn)); }, "foreign block read transaction was accepted");
                expect_invalid([&] { static_cast<void>(transaction_mapping.find_document_in_transaction(
                    DocumentId{"doc:transaction"}, foreign_txn)); }, "foreign lookup accessed a numeric DBI");
                expect_invalid([&] { static_cast<void>(transaction_mapping.find_block_in_transaction(
                    DocumentId{"doc:transaction"}, ContentBlockId{"block:transaction"}, foreign_txn)); },
                    "foreign block lookup accessed a numeric DBI");
                foreign_txn.commit();
                expect(foreign.snapshot_records().empty(), "foreign transaction changed another namespace");
            }
            std::error_code foreign_error;
            std::filesystem::remove(foreign_path, foreign_error);

            // Empty physical tables are not a fresh namespace when sequence
            // history remains after an out-of-band deletion.
            MdbxStorageIdentityMappingOptions erased_options;
            erased_options.context = context;
            erased_options.table_prefix = "erased";
            MdbxStorageIdentityMapping erased(erased_options);
            static_cast<void>(erased.ensure_document(DocumentId{"A"}));
            mdbxc::KeyValueTable<std::string, std::string> erased_rows(
                context->connection(), "erased_identity_records_v1");
            mdbxc::KeyValueTable<std::uint64_t, std::string> erased_reverse(
                context->connection(), "erased_identity_documents_v1");
            auto erase_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            erased_rows.clear(erase_txn);
            erased_reverse.clear(erase_txn);
            erase_txn.commit();
            expect_invalid([&] { erased.load_records({
                DocumentStorageIdentityRecord{DocumentStorageId{1}, DocumentId{"B"}, false}
            }); }, "empty tables with numeric history admitted a remap");

            // Exhaustion must be detected before writes, even if the caller
            // catches the exception and commits its otherwise valid transaction.
            MdbxStorageIdentityMappingOptions exhausted_options;
            exhausted_options.context = context;
            exhausted_options.table_prefix = "exhausted";
            MdbxStorageIdentityMapping exhausted(exhausted_options);
            const auto last_id = std::numeric_limits<std::uint64_t>::max() - 1U;
            exhausted.load_records({
                DocumentStorageIdentityRecord{DocumentStorageId{last_id}, DocumentId{"last"}, false},
                BlockStorageIdentityRecord{BlockStorageId{last_id}, DocumentId{"last"},
                                           ContentBlockId{"last"}, false}
            });
            auto exhaustion_txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
            bool document_exhausted = false;
            try { static_cast<void>(exhausted.ensure_document_in_transaction(
                DocumentId{"overflow"}, exhaustion_txn)); }
            catch(const std::runtime_error&) { document_exhausted = true; }
            bool block_exhausted = false;
            try { static_cast<void>(exhausted.ensure_block_in_transaction(
                DocumentId{"last"}, ContentBlockId{"overflow"}, exhaustion_txn)); }
            catch(const std::runtime_error&) { block_exhausted = true; }
            expect(document_exhausted && block_exhausted, "exhausted sequence admitted a mapping");
            exhaustion_txn.commit();
            expect(!exhausted.find_document(DocumentId{"overflow"})
                   && !exhausted.find_block(DocumentId{"last"}, ContentBlockId{"overflow"})
                   && exhausted.snapshot_records().size() == 2U,
                   "failed caller-owned allocation persisted a reusable maximum ID");
        }

        std::error_code error;
        std::filesystem::remove(path, error);
        std::filesystem::remove(markerless_path, error);
        std::filesystem::remove(missing_sequence_path, error);
        std::filesystem::remove(reverse_mismatch_path, error);
        std::filesystem::remove(bounded_path, error);
        std::filesystem::remove(transaction_path, error);
        std::filesystem::remove(import_path, error);
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
