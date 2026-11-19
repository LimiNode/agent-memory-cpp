#include <agent_memory/storage/CanonicalFlatStorage.hpp>

#include <algorithm>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>

namespace {

void expect(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}

template <typename Function>
void expect_rejected(Function&& function, const char* message) {
    bool rejected = false;
    try {
        function();
    } catch (const std::exception&) {
        rejected = true;
    }
    expect(rejected, message);
}

void put_u64_le(std::string& bytes, std::size_t offset, std::uint64_t value) {
    expect(offset + 8U <= bytes.size(), "test offset exceeds payload");
    for (unsigned shift = 0; shift != 64U; shift += 8U)
        bytes[offset + shift / 8U] = static_cast<char>((value >> shift) & 0xffU);
}

} // namespace

int main() {
    using namespace agent_memory::storage;

    CanonicalFlatBlockRecord block;
    block.id = "heading";
    block.revision = 3;
    block.kind = 0;
    block.text = "Title";
    const auto block_bytes = CanonicalFlatStorageCodec::encode_block(block);
    CanonicalFlatBlockRecord decoded_block;
    decoded_block = CanonicalFlatStorageCodec::decode_block(block_bytes);
    expect(decoded_block.id == block.id && decoded_block.revision == block.revision &&
               decoded_block.kind == block.kind && decoded_block.text == block.text &&
               decoded_block.logical_digest.algorithm == 1,
           "flat block round-trip changed logical fields");

    auto block_trailing = block_bytes;
    block_trailing.push_back('\0');
    expect_rejected([&] { static_cast<void>(CanonicalFlatStorageCodec::decode_block(block_trailing)); },
                    "flat block trailing bytes were accepted");

    auto block_version = block_bytes;
    block_version[4] = static_cast<char>(2);
    expect_rejected([&] { static_cast<void>(CanonicalFlatStorageCodec::decode_block(block_version)); },
                    "unknown flat block version was accepted");

    auto block_digest = block_bytes;
    block_digest[block_digest.size() - 16U] ^= static_cast<char>(1);
    expect_rejected([&] { static_cast<void>(CanonicalFlatStorageCodec::decode_block(block_digest)); },
                    "flat block digest corruption was accepted");

    auto block_checksum = block_bytes;
    block_checksum.back() ^= static_cast<char>(1);
    expect_rejected([&] { static_cast<void>(CanonicalFlatStorageCodec::decode_block(block_checksum)); },
                    "flat block checksum corruption was accepted");

    CanonicalFlatStructureRecord structure;
    structure.document_id = "doc";
    structure.document_revision = 7;
    structure.body_revision = 5;
    structure.metadata.emplace_back("lang", "en");
    structure.blocks.push_back({block.id, block.revision, block.kind, std::nullopt});
    const auto structure_bytes = CanonicalFlatStorageCodec::encode_structure(structure);
    const auto decoded_structure = CanonicalFlatStorageCodec::decode_structure(structure_bytes);
    const bool structure_ok = decoded_structure.document_id == structure.document_id &&
                              decoded_structure.document_revision == structure.document_revision &&
                              decoded_structure.body_revision == structure.body_revision &&
                              decoded_structure.metadata == structure.metadata &&
                              decoded_structure.blocks == structure.blocks;
    expect(structure_ok, "flat structure round-trip changed logical fields");

    auto structure_digest = structure_bytes;
    structure_digest[structure_digest.size() - 16U] ^= static_cast<char>(1);
    expect_rejected([&] { static_cast<void>(CanonicalFlatStorageCodec::decode_structure(structure_digest)); },
                    "flat structure digest corruption was accepted");

    auto oversized_count = CanonicalFlatStorageCodec::encode_structure(
        CanonicalFlatStructureRecord{});
    // Empty document id is encoded as an eight-byte zero length immediately
    // after the two revisions; the following field is metadata_count.
    put_u64_le(oversized_count, 32U, std::numeric_limits<std::uint64_t>::max());
    expect_rejected([&] { static_cast<void>(CanonicalFlatStorageCodec::decode_structure(oversized_count)); },
                    "oversized flat metadata count was accepted");

    auto length_overflow = block_bytes;
    // The id length is the first variable-length field after the fixed block
    // header. A huge value must be rejected before constructing a string.
    put_u64_le(length_overflow, 20U, std::numeric_limits<std::uint64_t>::max());
    expect_rejected([&] { static_cast<void>(CanonicalFlatStorageCodec::decode_block(length_overflow)); },
                    "overflowing flat block length was accepted");

    return 0;
}
