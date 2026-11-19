#include <agent_memory/storage/StorageIdentityMapping.hpp>

#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

namespace {

    int fail(const char* message) {
        std::cerr << message << '\n';
        return 1;
    }

    template<typename Function>
    bool throws(Function&& function) {
        try {
            function();
        } catch(const std::exception&) {
            return true;
        }
        return false;
    }

} // namespace

int main() {
    using namespace agent_memory;

    StorageIdentityMapping mapping;
    const DocumentId document{"doc:one"};
    const DocumentId second_document{"doc:two"};
    const ContentBlockId block{"block:one"};
    const ContentBlockId second_block{"block:two"};

    const auto document_id = mapping.ensure_document(document);
    if(document_id.empty() || document_id.value() != 1U
       || mapping.ensure_document(document) != document_id) {
        return fail("document identity allocation is not stable");
    }
    const auto block_id = mapping.ensure_block(document, block);
    if(block_id.empty() || block_id.value() != 1U
       || mapping.ensure_block(document, block) != block_id) {
        return fail("block identity allocation is not stable");
    }
    if(mapping.ensure_block(document, second_block).value() != 2U) {
        return fail("block identity sequence is not independent and monotonic");
    }

    const auto document_record = DocumentStorageIdentityRecord{
        document_id,
        document,
        false
    };
    const auto document_bytes = encode_storage_identity_record(document_record);
    const auto decoded_document = decode_document_storage_identity_record(document_bytes);
    if(decoded_document.storage_id != document_id
       || decoded_document.document_id != document
       || decoded_document.retired) {
        return fail("document identity codec round-trip failed");
    }

    const auto block_record = BlockStorageIdentityRecord{
        block_id,
        document,
        block,
        false
    };
    const auto block_bytes = encode_storage_identity_record(block_record);
    const auto decoded_block = decode_block_storage_identity_record(block_bytes);
    if(decoded_block.storage_id != block_id
       || decoded_block.document_id != document
       || decoded_block.block_id != block
       || decoded_block.retired) {
        return fail("block identity codec round-trip failed");
    }

    // The decoder must reject truncation, trailing bytes, unknown versions,
    // invalid flags and a declared field larger than the caller's bound before
    // constructing an unbounded string.
    if(!throws([&] { (void)decode_storage_identity_record(document_bytes.substr(0, 10U)); })) {
        return fail("truncated identity record was accepted");
    }
    auto trailing = document_bytes;
    trailing.push_back('x');
    if(!throws([&] { (void)decode_storage_identity_record(trailing); })) {
        return fail("identity record with trailing bytes was accepted");
    }
    auto unknown_version = document_bytes;
    unknown_version[4] = static_cast<char>(kStorageIdentityRecordVersion + 1U);
    if(!throws([&] { (void)decode_storage_identity_record(unknown_version); })) {
        return fail("unknown identity record version was accepted");
    }
    auto invalid_flags = document_bytes;
    invalid_flags[6] = static_cast<char>(0x80U);
    if(!throws([&] { (void)decode_storage_identity_record(invalid_flags); })) {
        return fail("invalid identity record flags were accepted");
    }
    auto oversized_length = document_bytes;
    oversized_length[20] = static_cast<char>(0xffU);
    oversized_length[21] = static_cast<char>(0xffU);
    oversized_length[22] = static_cast<char>(0xffU);
    oversized_length[23] = static_cast<char>(0x7fU);
    if(!throws([&] { (void)decode_storage_identity_record(oversized_length); })) {
        return fail("oversized identity field was accepted");
    }
    if(!throws([&] {
        (void)decode_storage_identity_record(
            document_bytes,
            StorageIdentityCodecOptions{document_bytes.size() - 1U, 1024U}
        );
    })) {
        return fail("record bound was not enforced");
    }

    // Retiring preserves the historical mapping and reserves the public id;
    // recreating it cannot silently alias the old numeric identity.
    if(!mapping.retire_document(document) || !mapping.is_document_retired(document)
       || !mapping.is_block_retired(document, block)
       || !throws([&] { (void)mapping.ensure_document(document); })
       || !throws([&] { (void)mapping.ensure_block(document, block); })) {
        return fail("retirement did not enforce no-reuse semantics");
    }
    const auto second_id = mapping.ensure_document(second_document);
    if(second_id.value() != 2U) {
        return fail("document identity sequence reused a retired value");
    }

    // Snapshot/restore is the backend-neutral durable boundary.  It must
    // preserve retired tombstones and continue both sequences above the max.
    const auto records = mapping.snapshot_records();
    StorageIdentityMapping restored;
    restored.load_records(records);
    if(restored.find_document(document) != document_id
       || !restored.is_document_retired(document)
       || restored.find_document(second_document) != second_id
       || restored.ensure_document(DocumentId{"doc:three"}).value() != 3U) {
        return fail("durable identity records did not restore monotonically");
    }
    if(!throws([&] {
        restored.load_records({
            DocumentStorageIdentityRecord{
                DocumentStorageId{7U},
                DocumentId{"duplicate"},
                false
            },
            DocumentStorageIdentityRecord{
                DocumentStorageId{7U},
                DocumentId{"another"},
                false
            }
        });
    })) {
        return fail("duplicate numeric identity was accepted");
    }

    // Block keys are paired with their document string id.  A block string may
    // therefore be reused in a different document without aliasing.
    if(restored.ensure_block(second_document, block).value() != 3U) {
        return fail("block identity was not scoped by document id");
    }

    std::cout << "storage identity mapping: PASS\n";
    return 0;
}
