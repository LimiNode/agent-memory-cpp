#include <agent_memory/storage/CanonicalContentStorage.hpp>

#include <stdexcept>
#include <string>

namespace {
void expect(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}
}

int main() {
    using namespace agent_memory::storage;

    CanonicalStorageBlock heading;
    heading.id = "heading";
    heading.revision = 3;
    heading.kind = 0;
    heading.text = "Title";

    CanonicalStorageBlock paragraph;
    paragraph.id = "paragraph";
    paragraph.revision = 4;
    paragraph.kind = 1;
    paragraph.parent_id = heading.id;
    paragraph.text = "Body";

    CanonicalStorageRevisionRecord revision;
    revision.document_id = "doc";
    revision.document_revision = 7;
    revision.metadata.values.emplace_back("lang", "en");
    revision.body_revision = 5;
    revision.blocks = {heading, paragraph};
    revision.logical_body_digest = CanonicalContentStorageCodec::logical_body_digest(
        revision.blocks);
    const auto revision_bytes = CanonicalContentStorageCodec::encode_revision(revision);
    const auto decoded_revision = CanonicalContentStorageCodec::decode_revision(revision_bytes);
    expect(decoded_revision.document_id == revision.document_id,
           "revision document identity changed");
    expect(decoded_revision.document_revision == revision.document_revision,
           "revision number changed");
    expect(decoded_revision.metadata.values == revision.metadata.values,
           "revision metadata changed");
    expect(decoded_revision.blocks.size() == revision.blocks.size() &&
               decoded_revision.blocks[0].id == revision.blocks[0].id &&
               decoded_revision.blocks[0].revision == revision.blocks[0].revision &&
               decoded_revision.blocks[0].kind == revision.blocks[0].kind &&
               decoded_revision.blocks[1].id == revision.blocks[1].id &&
               decoded_revision.blocks[1].revision == revision.blocks[1].revision &&
               decoded_revision.blocks[1].kind == revision.blocks[1].kind &&
               decoded_revision.blocks[1].parent_id == revision.blocks[1].parent_id,
           "revision structure changed");

    CanonicalStorageBodyRecord body;
    body.body_revision = 5;
    body.encoding_generation = 1;
    body.codec = "raw";
    body.blocks = revision.blocks;
    body.logical_body_digest = CanonicalContentStorageCodec::logical_body_digest(body.blocks);
    const auto body_bytes = CanonicalContentStorageCodec::encode_body(body);
    const auto decoded_body = CanonicalContentStorageCodec::decode_body(body_bytes);
    expect(decoded_body.blocks.size() == body.blocks.size() &&
               decoded_body.blocks[0].id == body.blocks[0].id &&
               decoded_body.blocks[0].kind == body.blocks[0].kind &&
               decoded_body.blocks[0].text == body.blocks[0].text &&
               decoded_body.blocks[1].id == body.blocks[1].id &&
               decoded_body.blocks[1].kind == body.blocks[1].kind &&
               decoded_body.blocks[1].parent_id == body.blocks[1].parent_id &&
               decoded_body.blocks[1].text == body.blocks[1].text,
           "body payload changed");
    expect(decoded_body.logical_body_digest == body.logical_body_digest,
           "logical body digest changed");

    auto corrupted = body_bytes;
    corrupted.back() ^= 0x01;
    bool rejected = false;
    try {
        static_cast<void>(CanonicalContentStorageCodec::decode_body(corrupted));
    } catch (const std::runtime_error&) {
        rejected = true;
    }
    expect(rejected, "corrupt body was accepted");

    auto trailing = revision_bytes;
    trailing.push_back('x');
    rejected = false;
    try {
        static_cast<void>(CanonicalContentStorageCodec::decode_revision(trailing));
    } catch (const std::runtime_error&) {
        rejected = true;
    }
    expect(rejected, "revision trailing bytes were accepted");

    std::string oversized_count = "34:agent_memory.canonical_revision.v3";
    oversized_count += "1:1";
    oversized_count.push_back(static_cast<char>(1));
    oversized_count.append(32, '\0');
    oversized_count += "3:doc1:0";
    oversized_count += "1:0";
    oversized_count += "1000000:";
    rejected = false;
    try {
        static_cast<void>(CanonicalContentStorageCodec::decode_revision(oversized_count));
    } catch (const std::runtime_error&) {
        rejected = true;
    }
    expect(rejected, "oversized record count was accepted");

    const auto head = CanonicalContentStorageCodec::encode_head({7});
    expect(head == "7", "head encoding changed C1 representation");
    expect(CanonicalContentStorageCodec::decode_head(head).document_revision == 7,
           "head decoding changed C1 representation");

    CanonicalStorageLedgerRecord ledger;
    ledger.block_ids = {"heading", "paragraph"};
    const auto ledger_bytes = CanonicalContentStorageCodec::encode_ledger(ledger);
    expect(CanonicalContentStorageCodec::decode_ledger(ledger_bytes).block_ids == ledger.block_ids,
           "ledger round-trip changed IDs");
    return 0;
}
