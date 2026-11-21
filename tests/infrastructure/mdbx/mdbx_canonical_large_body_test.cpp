#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.hpp>
#include <agent_memory/domain/Sha256.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <filesystem>
#include <iostream>
#include <mdbx_containers/KeyValueTable.hpp>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

namespace {

using namespace agent_memory;

constexpr std::size_t kBlockCount = (1U << 20) + 1U;
constexpr const char* kDocumentId = "large-c1";

void expect(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}

void put_size(std::string& out, std::size_t value) {
    out += std::to_string(value) + ":";
}

void put_field(std::string& out, std::string_view value) {
    put_size(out, value.size());
    out.append(value.data(), value.size());
}

std::string block_id(std::size_t index) {
    constexpr std::string_view digits = "0123456789abcdefghijklmnopqrstuvwxyz";
    std::string id(4U, '0');
    for (std::size_t position = id.size(); position != 0U; --position) {
        id[position - 1U] = digits[index % digits.size()];
        index /= digits.size();
    }
    return id;
}

// Write a valid legacy C1 fixture directly. The reference editor validates
// preorder with quadratic child scans; exercising it at this size would hide
// the decoder regression behind unrelated writer work. Digest generation is
// streamed and does not allocate an extra million-block domain vector.
void write_fixture(const std::filesystem::path& path) {
    std::string body;
    put_field(body, "agent_memory.canonical_body.v3");
    put_field(body, "1"); // body revision
    put_field(body, "1"); // physical generation
    put_field(body, "raw");
    put_size(body, kBlockCount);

    std::string descriptors;
    std::string ledger;
    put_field(ledger, "agent_memory.canonical_ledger.v1");
    put_size(ledger, kBlockCount);

    std::string logical_header;
    put_field(logical_header, "agent_memory.canonical_logical_body.v1");
    put_size(logical_header, kBlockCount);
    Sha256Accumulator logical_digest;
    logical_digest.update(logical_header);
    const auto kind = std::to_string(static_cast<std::size_t>(ContentBlockKind::Paragraph)) + ":";
    for (std::size_t index = 0; index < kBlockCount; ++index) {
        const auto id = block_id(index);
        const auto start = body.size();
        put_field(body, id);
        body += kind + "0:0:"; // no parent, empty text
        logical_digest.update(std::string_view(body).substr(start));
        put_field(descriptors, id);
        put_field(descriptors, "1"); // block revision
        descriptors += kind + "0:";
        put_field(ledger, id);
    }
    const auto digest = logical_digest.finish();
    std::string tagged_digest(1U, '\x01');
    tagged_digest.append(reinterpret_cast<const char*>(digest.data()), digest.size());
    body += tagged_digest;
    std::uint64_t checksum = 1469598103934665603ULL;
    for (const auto byte : body) {
        checksum ^= static_cast<unsigned char>(byte);
        checksum *= 1099511628211ULL;
    }
    put_field(body, std::to_string(checksum));

    std::string revision;
    put_field(revision, "agent_memory.canonical_revision.v3");
    put_field(revision, "1");
    revision += tagged_digest;
    put_field(revision, kDocumentId);
    put_field(revision, "0");
    put_size(revision, 0U); // metadata count
    put_size(revision, kBlockCount);
    revision += descriptors;

    auto context = MdbxStorageContext::open(path.string());
    mdbxc::KeyValueTable<std::string, std::string> heads(
        context->connection(), "large_canonical_heads");
    mdbxc::KeyValueTable<std::string, std::string> revisions(
        context->connection(), "large_canonical_revisions");
    mdbxc::KeyValueTable<std::string, std::string> bodies(
        context->connection(), "large_canonical_bodies");
    mdbxc::KeyValueTable<std::string, std::string> ledgers(
        context->connection(), "large_canonical_ledgers");
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto key = std::string(kDocumentId) + '\x1f';
    heads.insert_or_assign(kDocumentId, "0", transaction);
    revisions.insert_or_assign(key + "0", revision, transaction);
    bodies.insert_or_assign(key + "1\x1f" + "1", body, transaction);
    ledgers.insert_or_assign(kDocumentId, ledger, transaction);
    transaction.commit();
}

} // namespace

int main() {
    try {
        const auto path = std::filesystem::temp_directory_path() /
            "agent_memory_c1_large_body_test.mdbx";
        std::error_code error;
        std::filesystem::remove(path, error);
        write_fixture(path);
        {
            // A new connection must read the retained layout without applying
            // the navigation-only block ceiling to ordinary C1 reads.
            MdbxCanonicalContentStoreOptions options;
            options.path = path.string();
            options.table_prefix = "large";
            MdbxCanonicalContentStore store(std::move(options));
            const auto revision = store.read_revision(DocumentId{kDocumentId}, 0U);
            expect(revision && revision->blocks.size() == kBlockCount,
                   "ordinary C1 read rejected a valid body above the navigation ceiling");
            expect(revision->blocks.front().id == ContentBlockId{block_id(0U)} &&
                       revision->blocks.back().id == ContentBlockId{block_id(kBlockCount - 1U)} &&
                       revision->blocks.back().text.empty(),
                   "large C1 body identities or text changed on reopen");
            bool navigation_rejected = false;
            try {
                (void)store.read_outline(DocumentId{kDocumentId}, 0U, 1U);
            } catch (const std::runtime_error&) {
                navigation_rejected = true;
            }
            expect(navigation_rejected, "navigation accepted a revision above its ceiling");
        }
        std::filesystem::remove(path, error);
        std::cout << "large C1 body and navigation separation: PASS\n";
        return 0;
    } catch (const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
}
#else
int main() { return 0; }
#endif
