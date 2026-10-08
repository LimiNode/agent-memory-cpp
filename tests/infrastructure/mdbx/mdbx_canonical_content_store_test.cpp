#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <array>
#include <filesystem>
#include <iostream>
#include <mdbx_containers/KeyValueTable.hpp>
#include <stdexcept>
#include <vector>

namespace {

std::string read_table_value(const std::shared_ptr<agent_memory::MdbxStorageContext>& context,
                             const std::string& table_name,
                             const std::string& key) {
    mdbxc::KeyValueTable<std::string, std::string> table(context->connection(), table_name);
    auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto value = table.find(key, txn);
    if (!value)
        throw std::runtime_error("test row is missing");
    return *value;
}

std::string row_key(const std::string& document_id, std::uint64_t revision) {
    return document_id + std::string{"\x1f"} + std::to_string(revision);
}

std::string body_row_key(const std::string& document_id,
                         std::uint64_t body_revision,
                         std::uint64_t encoding_generation = 1) {
    return row_key(document_id, body_revision) + std::string{"\x1f"} +
           std::to_string(encoding_generation);
}

} // namespace

int main() {
    using namespace agent_memory;
    // Physical codec and generation are deliberately absent from the logical
    // digest input. This fixture models a future raw-generation-1 versus
    // compressed-generation-2 re-encoding of the same decoded block sequence.
    const std::vector<ContentBlock> digest_fixture{
        {ContentBlockId{"digest-a"},
         1,
         ContentBlockKind::Paragraph,
         std::nullopt,
         "same logical body"}};
    const auto raw_generation_one_digest =
        detail::canonical_decoded_content_digest(digest_fixture);
    const auto compressed_generation_two_digest =
        detail::canonical_decoded_content_digest(digest_fixture);
    constexpr std::array<std::uint8_t, 32> expected_digest{{
        0x1dU, 0x91U, 0xdeU, 0x80U, 0xd7U, 0x6aU, 0x4fU, 0x52U,
        0xa8U, 0xcdU, 0x87U, 0x1eU, 0x13U, 0xc8U, 0xc4U, 0x49U,
        0xc1U, 0x6aU, 0x4eU, 0x76U, 0x47U, 0x52U, 0xb7U, 0xbeU,
        0x17U, 0x85U, 0x8dU, 0xf7U, 0x23U, 0xf8U, 0x3cU, 0x9eU,
    }};
    if (raw_generation_one_digest != compressed_generation_two_digest ||
        raw_generation_one_digest != expected_digest) {
        return 30;
    }

    const auto path =
        std::filesystem::temp_directory_path() / "agent_memory_c1_canonical_test.mdbx";
    std::error_code ec;
    std::filesystem::remove(path, ec);
    const DocumentId id{"c1-doc"};
    CanonicalDocumentRevision initial{
        id,
        0,
        {},
        {{ContentBlockId{"a"}, 99, ContentBlockKind::Heading, std::nullopt, "A"},
         {ContentBlockId{"b"}, 99, ContentBlockKind::Paragraph, ContentBlockId{"a"}, "B"}}};
    {
        auto context = MdbxStorageContext::open(path.string());
        MdbxCanonicalContentStoreOptions options;
        options.context = context;
        MdbxCanonicalContentStore store(std::move(options));
        if (!store.create_document(initial))
            return 1;
        auto edited = store.commit({id, 0, {ReplaceBlockText{ContentBlockId{"b"}, "B2"}}});
        if (edited.status != CanonicalEditStatus::Ok || !store.read_revision(id, 0) ||
            store.read_block(id, ContentBlockId{"b"})->text != "B2")
            return 2;
        auto failed = store.commit({id,
                                    1,
                                    {InsertBlock{ContentBlock{ContentBlockId{"aborted"},
                                                              1,
                                                              ContentBlockKind::Paragraph,
                                                              std::nullopt,
                                                              "aborted"},
                                                 1},
                                     DeleteBlock{ContentBlockId{"missing"}}}});
        if (failed.status != CanonicalEditStatus::InvalidEdit ||
            store.read_block(id, ContentBlockId{"aborted"}) ||
            store.read_current(id)->revision != 1)
            return 13;
        if (store.materialize_markdown(id).value() != "# A\n\nB2\n")
            return 3;
    }
    {
        auto context = MdbxStorageContext::open(path.string());
        MdbxCanonicalContentStoreOptions options;
        options.context = context;
        MdbxCanonicalContentStore store(std::move(options));
        if (!store.read_revision(id, 0) || !store.read_revision(id, 1) ||
            store.read_block(id, ContentBlockId{"b"}, 0)->text != "B")
            return 4;
        const auto body_before_metadata =
            read_table_value(context, "agent_memory_canonical_bodies", body_row_key("c1-doc", 1));
        Metadata metadata;
        metadata.set("title", "metadata-only");
        const auto metadata_edit =
            store.commit({id, 1, {UpdateDocumentMetadata{std::move(metadata)}}});
        if (metadata_edit.status != CanonicalEditStatus::Ok ||
            metadata_edit.revision->revision != 2 ||
            store.read_block(id, ContentBlockId{"b"})->text != "B2")
            return 16;
        if (read_table_value(context, "agent_memory_canonical_bodies", body_row_key("c1-doc", 1)) !=
            body_before_metadata)
            return 17;
        auto stale = store.commit({id, 0, {ReplaceBlockText{ContentBlockId{"b"}, "stale"}}});
        if (stale.status != CanonicalEditStatus::Conflict)
            return 5;
        const auto body_before_no_change =
            read_table_value(context, "agent_memory_canonical_bodies", body_row_key("c1-doc", 1));
        auto transient = store.commit(
            {id,
             2,
             {InsertBlock{
                  ContentBlock{
                      ContentBlockId{"tmp"}, 1, ContentBlockKind::Paragraph, std::nullopt, "tmp"},
                  1},
              DeleteBlock{ContentBlockId{"tmp"}}}});
        if (transient.status != CanonicalEditStatus::NoChange)
            return 6;
        if (read_table_value(context, "agent_memory_canonical_bodies", body_row_key("c1-doc", 1)) !=
            body_before_no_change)
            return 25;
        auto published = store.commit({id,
                                       2,
                                       {InsertBlock{ContentBlock{ContentBlockId{"tmp"},
                                                                 1,
                                                                 ContentBlockKind::Paragraph,
                                                                 std::nullopt,
                                                                 "published"},
                                                    1}}});
        if (published.status != CanonicalEditStatus::Ok)
            return 7;
        if (read_table_value(context, "agent_memory_canonical_bodies", body_row_key("c1-doc", 2)) ==
            body_before_metadata)
            return 26;
        auto deleted = store.commit({id, 3, {DeleteBlock{ContentBlockId{"tmp"}}}});
        if (deleted.status != CanonicalEditStatus::Ok)
            return 8;
        auto reused = store.commit(
            {id,
             4,
             {InsertBlock{
                 ContentBlock{
                     ContentBlockId{"tmp"}, 1, ContentBlockKind::Paragraph, std::nullopt, "reused"},
                 1}}});
        if (reused.status != CanonicalEditStatus::InvalidEdit)
            return 9;
    }
    const auto shared_path =
        std::filesystem::temp_directory_path() / "agent_memory_c1_shared_test.mdbx";
    std::filesystem::remove(shared_path, ec);
    if (kDefaultMdbxMaxDbs < 34)
        return 23;
    auto context = MdbxStorageContext::open(shared_path.string());
    MdbxCanonicalContentStoreOptions first_options;
    first_options.context = context;
    MdbxCanonicalContentStoreOptions second_options;
    second_options.context = context;
    {
        MdbxCanonicalContentStore first(std::move(first_options));
        MdbxCanonicalContentStore second(std::move(second_options));
        if (!first.create_document(CanonicalDocumentRevision{
                DocumentId{"shared"},
                0,
                {},
                {{ContentBlockId{"one"}, 1, ContentBlockKind::Paragraph, std::nullopt, "one"}}}))
            return 10;
        if (!second.read_current(DocumentId{"shared"}))
            return 11;
    }
    auto attached_context = MdbxStorageContext::attach(context->connection());
    MdbxCanonicalContentStoreOptions attached_options;
    attached_options.context = attached_context;
    {
        MdbxCanonicalContentStore attached(std::move(attached_options));
        if (!attached.read_current(DocumentId{"shared"}))
            return 12;
    }
    const auto capacity_path =
        std::filesystem::temp_directory_path() / "agent_memory_c1_capacity_test.mdbx";
    std::filesystem::remove(capacity_path, ec);
    MdbxCanonicalContentStoreOptions capacity_options;
    capacity_options.path = capacity_path.string();
    capacity_options.max_dbs = 34;
    {
        MdbxCanonicalContentStore capacity_store(std::move(capacity_options));
        if (!capacity_store.create_document(
                CanonicalDocumentRevision{DocumentId{"capacity"},
                                          0,
                                          {},
                                          {{ContentBlockId{"capacity-block"},
                                            1,
                                            ContentBlockKind::Paragraph,
                                            std::nullopt,
                                            "capacity"}}}))
            return 24;
    }
    const auto corrupt_path =
        std::filesystem::temp_directory_path() / "agent_memory_c1_corrupt_test.mdbx";
    std::filesystem::remove(corrupt_path, ec);
    auto corrupt_context = MdbxStorageContext::open(corrupt_path.string());
    MdbxCanonicalContentStoreOptions corrupt_options;
    corrupt_options.context = corrupt_context;
    MdbxCanonicalContentStore corrupt_store(std::move(corrupt_options));
    if (!corrupt_store.create_document(CanonicalDocumentRevision{
            DocumentId{"corrupt"},
            0,
            {},
            {{ContentBlockId{"one"}, 1, ContentBlockKind::Paragraph, std::nullopt, "one"}}}))
        return 14;
    mdbxc::KeyValueTable<std::string, std::string> revision_table(
        corrupt_context->connection(), "agent_memory_canonical_revisions");
    mdbxc::KeyValueTable<std::string, std::string> body_table(corrupt_context->connection(),
                                                              "agent_memory_canonical_bodies");
    mdbxc::KeyValueTable<std::string, std::string> head_table(corrupt_context->connection(),
                                                              "agent_memory_canonical_heads");
    auto corrupt_txn = corrupt_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    revision_table.insert_or_assign(std::string{"corrupt\x1f"} + "0", "invalid", corrupt_txn);
    corrupt_txn.commit();
    bool corrupt_read_threw = false;
    try {
        (void)corrupt_store.read_current(DocumentId{"corrupt"});
    } catch (const std::runtime_error&) {
        corrupt_read_threw = true;
    }
    bool corrupt_commit_threw = false;
    try {
        (void)corrupt_store.commit({DocumentId{"corrupt"}, 0, {}});
    } catch (const std::runtime_error&) {
        corrupt_commit_threw = true;
    }
    if (!corrupt_read_threw || !corrupt_commit_threw)
        return 15;

    if (!corrupt_store.create_document(CanonicalDocumentRevision{DocumentId{"other"},
                                                                 0,
                                                                 {},
                                                                 {{ContentBlockId{"other-block"},
                                                                   1,
                                                                   ContentBlockKind::Paragraph,
                                                                   std::nullopt,
                                                                   "other"}}}))
        return 18;
    auto copy_txn = corrupt_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    const auto other_revision = revision_table.find(row_key("other", 0), copy_txn);
    const auto other_body = body_table.find(body_row_key("other", 1), copy_txn);
    if (!other_revision || !other_body)
        return 19;
    revision_table.insert_or_assign(row_key("wrong-document", 0), *other_revision, copy_txn);
    body_table.insert_or_assign(body_row_key("wrong-document", 1), *other_body, copy_txn);
    copy_txn.commit();
    bool identity_threw = false;
    try {
        (void)corrupt_store.read_revision(DocumentId{"wrong-document"}, 0);
    } catch (const std::runtime_error&) {
        identity_threw = true;
    }
    if (!identity_threw)
        return 20;

    if (!corrupt_store.create_document(CanonicalDocumentRevision{DocumentId{"digest-corrupt"},
                                                                 0,
                                                                 {},
                                                                 {{ContentBlockId{"digest-block"},
                                                                   1,
                                                                   ContentBlockKind::Paragraph,
                                                                   std::nullopt,
                                                                   "digest"}}}))
        return 27;
    auto digest_txn = corrupt_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    auto digest_payload = body_table.find(body_row_key("digest-corrupt", 1), digest_txn);
    if (!digest_payload || digest_payload->empty())
        return 28;
    digest_payload->back() = digest_payload->back() == '0' ? '1' : '0';
    body_table.insert_or_assign(body_row_key("digest-corrupt", 1), *digest_payload, digest_txn);
    digest_txn.commit();
    bool digest_threw = false;
    try {
        (void)corrupt_store.read_current(DocumentId{"digest-corrupt"});
    } catch (const std::runtime_error&) {
        digest_threw = true;
    }
    if (!digest_threw)
        return 29;

    if (!corrupt_store.create_document(CanonicalDocumentRevision{DocumentId{"head-corrupt"},
                                                                 0,
                                                                 {},
                                                                 {{ContentBlockId{"head-block"},
                                                                   1,
                                                                   ContentBlockKind::Paragraph,
                                                                   std::nullopt,
                                                                   "head"}}}))
        return 21;
    auto head_txn = corrupt_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    head_table.insert_or_assign("head-corrupt", "1junk", head_txn);
    head_txn.commit();
    bool malformed_head_threw = false;
    try {
        (void)corrupt_store.read_current(DocumentId{"head-corrupt"});
    } catch (const std::runtime_error&) {
        malformed_head_threw = true;
    }
    if (!malformed_head_threw)
        return 22;
    std::filesystem::remove(path, ec);
    std::filesystem::remove(shared_path, ec);
    std::filesystem::remove(capacity_path, ec);
    std::filesystem::remove(corrupt_path, ec);
    return 0;
}
#else
int main() {
    return 0;
}
#endif
