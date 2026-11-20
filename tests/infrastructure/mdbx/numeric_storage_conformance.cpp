#include <agent_memory/infrastructure/mdbx/MdbxCanonicalFlatStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxKnowledgeUnitTypedStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageIdentityMapping.hpp>
#include <agent_memory/storage/CanonicalFlatStorage.hpp>
#include <agent_memory/storage/CanonicalContentStore.hpp>
#include <agent_memory/domain/Sha256.hpp>
#include <mdbx_containers/KeyValueTable.hpp>

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

namespace {
using namespace agent_memory;
using Table = mdbxc::KeyValueTable<std::string, std::string>;
constexpr const char* kPrefix = "numeric_gate";
constexpr const char* kDocument = "scope:blue/document";

void require(bool value, const char* message) {
    if(!value) throw std::runtime_error(message);
}
template<class Function> void reject(Function&& function, const char* message) {
    bool rejected = false;
    try { function(); } catch(const std::exception&) { rejected = true; }
    require(rejected, message);
}
std::string name(const char* suffix) { return std::string{kPrefix} + "_" + suffix; }
void append(std::string& output, const std::string& value) {
    output += std::to_string(value.size()) + ":" + value;
}
std::string hex_digest(const std::string& bytes) {
    static constexpr char hex[] = "0123456789abcdef";
    std::string result;
    for(const auto value : sha256(bytes)) {
        result.push_back(hex[value >> 4U]); result.push_back(hex[value & 15U]);
    }
    return result;
}
KnowledgeUnitCreateRequest unit(const char* scope, const char* text) {
    KnowledgeUnitCreateRequest result;
    result.kind = KnowledgeUnitKind::Chunk;
    result.scope_id = ScopeId{scope};
    result.primary_text = text;
    result.display_text = text;
    result.content_hash_recipe_version = 2;
    SourceRefSummary source;
    source.resource_id = ResourceId{"resource:numeric-gate"};
    ResourceBodyDigest digest; digest.bytes = sha256("numeric gate source revision seven");
    source.resource_revision = ResourceRevisionRef{source.resource_id, 7, digest};
    source.uri = "https://example.test/numeric-gate";
    source.preview = text;
    source.excerpt = TextRange{0, source.preview.size()};
    source.quote_hash = compute_quote_hash(source.preview);
    source.confidence = 0.9;
    result.sources.push_back(source);
    result.content_hash = compute_chunk_content_hash(result.scope_id, result.primary_text,
                                                    result.sources, 2);
    result.priority_weight = 0.5;
    result.created_at_ms = 10; result.observed_at_ms = 11;
    return result;
}
CanonicalDocumentRevision initial(bool other = false) {
    CanonicalDocumentRevision result;
    result.document_id = DocumentId{other ? "scope:red/document" : kDocument};
    result.metadata.set("scope", other ? "red" : "blue");
    result.blocks = {{ContentBlockId{"heading"}, 1, ContentBlockKind::Heading, std::nullopt, "Title"}};
    if(!other) {
        result.blocks.push_back({ContentBlockId{"body"}, 1, ContentBlockKind::Paragraph,
                                 ContentBlockId{"heading"}, "Alpha"});
        result.blocks.push_back({ContentBlockId{"deleted"}, 1, ContentBlockKind::Paragraph,
                                 ContentBlockId{"heading"}, "Historical"});
    }
    return result;
}
std::vector<CanonicalEditRequest> edits() {
    Metadata metadata; metadata.set("scope", "blue"); metadata.set("edition", "three");
    return {
        {DocumentId{kDocument}, 0, {ReplaceBlockText{ContentBlockId{"body"}, "Beta"}}},
        {DocumentId{kDocument}, 1, {DeleteBlock{ContentBlockId{"deleted"}}}},
        {DocumentId{kDocument}, 2, {UpdateDocumentMetadata{metadata}}},
    };
}
std::vector<CanonicalDocumentRevision> history() {
    InMemoryCanonicalContentStore reference;
    require(reference.create_document(initial()), "reference create failed");
    std::vector<CanonicalDocumentRevision> result{*reference.read_current(DocumentId{kDocument})};
    for(const auto& edit : edits()) {
        static_cast<void>(reference.commit(edit));
        result.push_back(*reference.read_current(DocumentId{kDocument}));
    }
    return result;
}
CanonicalFlatBlockRecord portable(const ContentBlock& value) {
    CanonicalFlatBlockRecord result;
    result.id = value.id.value(); result.revision = value.revision;
    result.kind = static_cast<std::uint8_t>(value.kind);
    if(value.parent_id) result.parent_id = value.parent_id->value();
    result.text = value.text;
    return result;
}
CanonicalFlatStructureRecord portable(const CanonicalDocumentRevision& value) {
    CanonicalFlatStructureRecord result;
    result.document_id = value.document_id.value(); result.document_revision = value.revision;
    result.body_revision = value.revision;
    for(const auto& item : value.metadata.values()) result.metadata.push_back(item);
    for(const auto& block : value.blocks) {
        const auto payload = portable(block);
        result.blocks.push_back({payload.id, payload.revision, payload.kind, payload.parent_id});
    }
    return result;
}
void add_revision(std::string& snapshot, const CanonicalDocumentRevision& value) {
    append(snapshot, CanonicalFlatStorageCodec::encode_structure(portable(value)));
    for(const auto& block : value.blocks)
        append(snapshot, CanonicalFlatStorageCodec::encode_block(portable(block)));
}
MdbxCanonicalFlatStorageOptions flat_options(const std::shared_ptr<MdbxStorageContext>& context) {
    MdbxCanonicalFlatStorageOptions result;
    result.context = context; result.table_prefix = "numeric gate";
    return result;
}
MdbxStorageIdentityMappingOptions identity_options(const std::shared_ptr<MdbxStorageContext>& context) {
    MdbxStorageIdentityMappingOptions result;
    result.context = context; result.table_prefix = "numeric gate";
    return result;
}
MdbxKnowledgeUnitTypedStoreOptions unit_options(const std::shared_ptr<MdbxStorageContext>& context) {
    MdbxKnowledgeUnitTypedStoreOptions result;
    result.context = context; result.table_prefix = "numeric gate";
    return result;
}
void populate_units(const std::shared_ptr<MdbxStorageContext>& context) {
    MdbxKnowledgeUnitTypedStore store(unit_options(context));
    require(store.create_or_get_unit(unit("blue", "Alpha")).id.value() == 1, "first ID changed");
    const auto erased = store.create_or_get_unit(unit("blue", "Deleted"));
    require(erased.id.value() == 2 && store.erase(erased.id), "erase fixture failed");
    require(store.create_or_get_unit(unit("red", "Gamma")).id.value() == 3, "erased ID reused");
    KnowledgeUnitMutablePatch patch; patch.priority_weight = 0.7; patch.display_text = "Updated Alpha";
    require(store.update_mutable_fields(KnowledgeUnitId{1}, patch).revision == 2, "unit revision did not advance");
    reject([&] { store.upsert(erased); }, "erased unit ID could be reused");
}
void retire_identity(const std::shared_ptr<MdbxStorageContext>& context) {
    MdbxStorageIdentityMapping mapping(identity_options(context));
    const DocumentId document{"retired:document"};
    require(mapping.ensure_document(document).value() == 3, "document sequence changed");
    require(mapping.ensure_block(document, ContentBlockId{"retired:block"}).value() == 5, "block sequence changed");
    require(mapping.retire_document(document), "retirement failed");
}
void create_numeric(const std::shared_ptr<MdbxStorageContext>& context) {
    populate_units(context);
    MdbxCanonicalFlatStorage flat(flat_options(context));
    require(flat.create_document(initial()), "numeric document create failed");
    for(const auto& edit : edits()) static_cast<void>(flat.commit(edit));
    require(flat.create_document(initial(true)), "second scope create failed");
    retire_identity(context);
}

// This is an OLD physical-schema fixture, not an OLD binary. Its layout is
// pinned to PR #662 commit 3e21501be41c165a900bd203c494784d3d5bbcba:
// native head keys, decimal "id/revision" keys and portable AMFS/AMFB v1 values.
// The gate deliberately rejects it; it does not claim an executed migration.
void create_old(const std::shared_ptr<MdbxStorageContext>& context) {
    populate_units(context);
    MdbxStorageIdentityMapping mapping(identity_options(context));
    Table meta(context->connection(), name("canonical_flat_meta_v1"));
    mdbxc::KeyValueTable<std::uint64_t, std::string> heads(context->connection(), name("canonical_flat_heads_v1"));
    Table structures(context->connection(), name("canonical_flat_structures_v1"));
    Table blocks(context->connection(), name("canonical_flat_blocks_v1"));
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    meta.insert_or_assign("@schema_version", "canonical_flat_storage.v1", transaction);
    auto revisions = history(); revisions.push_back(initial(true));
    for(const auto& revision : revisions) {
        const auto document = mapping.ensure_document_in_transaction(revision.document_id, transaction);
        structures.insert_or_assign(std::to_string(document.value()) + "/" + std::to_string(revision.revision),
            CanonicalFlatStorageCodec::encode_structure(portable(revision)), transaction);
        for(const auto& block : revision.blocks) {
            const auto id = mapping.ensure_block_in_transaction(revision.document_id, block.id, transaction);
            blocks.insert_or_assign(std::to_string(id.value()) + "/" + std::to_string(block.revision),
                CanonicalFlatStorageCodec::encode_block(portable(block)), transaction);
        }
        heads.insert_or_assign(document.value(), std::to_string(revision.revision), transaction);
    }
    transaction.commit();
    retire_identity(context);
}
std::string verify_common(const std::shared_ptr<MdbxStorageContext>& context) {
    MdbxKnowledgeUnitTypedStore store(unit_options(context));
    std::string snapshot;
    for(const auto id : {1U, 3U}) {
        const auto value = store.find(KnowledgeUnitId{id});
        require(value.has_value(), "missing KnowledgeUnit row");
        const auto expected = unit(id == 1 ? "blue" : "red", id == 1 ? "Alpha" : "Gamma");
        require(value->scope_id == expected.scope_id && value->primary_text == expected.primary_text &&
            value->content_hash == *expected.content_hash && value->sources.size() == 1 &&
            value->sources.front().resource_revision.has_value() &&
            value->sources.front().resource_revision->resource_id == expected.sources.front().resource_revision->resource_id &&
            value->sources.front().resource_revision->generation == 7 &&
            value->sources.front().resource_revision->body_digest.bytes == expected.sources.front().resource_revision->body_digest.bytes &&
            value->revision == (id == 1 ? 2U : 1U), "unit scope/revision/digest/provenance changed");
        require(store.find_by_content_key(KnowledgeUnitKey{value->kind, value->scope_id, value->content_hash}) == value->id,
                "content-key index mismatch");
        const auto by_kind = store.scan_by_scope_and_kind(value->scope_id, value->kind);
        require(by_kind == std::vector<KnowledgeUnitId>{value->id} &&
            store.scan_by_scope(value->scope_id) == by_kind, "scope/kind index mismatch");
        append(snapshot, MdbxKnowledgeUnitStore::encode_envelope_payload(*value));
    }
    require(!store.find(KnowledgeUnitId{2}) && store.scan_by_scope(ScopeId{"wrong"}).empty(),
            "deleted row or wrong-scope lookup admitted");
    MdbxStorageIdentityMapping mapping(identity_options(context));
    const auto records = mapping.snapshot_records();
    require(records.size() == 8, "identity count changed");
    for(const auto& record : records) {
        append(snapshot, encode_storage_identity_record(record));
        std::visit([&](const auto& value) {
            using T = std::decay_t<decltype(value)>;
            if constexpr(std::is_same_v<T, DocumentStorageIdentityRecord>) {
                require(mapping.resolve_document(value.storage_id) == value.document_id, "document reverse mapping mismatch");
            } else {
                require(mapping.resolve_block(value.storage_id) == std::make_pair(value.document_id, value.block_id),
                        "block reverse mapping mismatch");
            }
        }, record);
    }
    Table metadata(context->connection(), name("ku_typed_v1_meta"));
    Table identity_meta(context->connection(), name("identity_meta_v1"));
    mdbxc::KeyValueTable<std::uint64_t, std::string> used(context->connection(), name("ku_typed_v1_used"));
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    struct ExpectedTable { const char* suffix; std::size_t rows; unsigned flags; };
    for(const auto& expected : std::vector<ExpectedTable>{
        {"ku_typed_v1", 2, MDBX_INTEGERKEY}, {"ku_typed_v1_used", 3, MDBX_INTEGERKEY},
        {"ku_typed_v1_meta", 2, 0}, {"ku_typed_v1_content", 2, 0}, {"ku_typed_v1_kind", 2, 0},
        {"identity_records_v1", 8, 0}, {"identity_documents_v1", 3, MDBX_INTEGERKEY},
        {"identity_blocks_v1", 5, MDBX_INTEGERKEY}, {"identity_meta_v1", 3, 0}}) {
        MDBX_dbi database{}; MDBX_stat statistics{}; unsigned flags{};
        require(mdbx_dbi_open(transaction.handle(), name(expected.suffix).c_str(), MDBX_DB_ACCEDE, &database) == MDBX_SUCCESS &&
            mdbx_dbi_flags(transaction.handle(), database, &flags) == MDBX_SUCCESS && flags == expected.flags &&
            mdbx_dbi_stat(transaction.handle(), database, &statistics, sizeof(statistics)) == MDBX_SUCCESS &&
            statistics.ms_entries == expected.rows, "typed/identity physical count or flags mismatch");
    }
    for(const auto id : {1U, 2U, 3U})
        require(used.find(id, transaction) == std::optional<std::string>{"used"}, "no-reuse ledger marker missing");
    require(metadata.find("@next_id", transaction) == std::optional<std::string>{"4"} &&
        identity_meta.find("@next_document_id", transaction) == std::optional<std::string>{"4"} &&
        identity_meta.find("@next_block_id", transaction) == std::optional<std::string>{"6"}, "no-reuse high-water changed");
    transaction.commit();
    reject([&] { static_cast<void>(mapping.ensure_document(DocumentId{"retired:document"})); },
           "retired identity reused after reopen");
    return snapshot;
}
std::string verify(const std::shared_ptr<MdbxStorageContext>& context, bool old) {
    auto snapshot = verify_common(context);
    auto expected = history(); expected.push_back(initial(true));
    MdbxStorageIdentityMapping mapping(identity_options(context));
    if(old) {
        Table metadata(context->connection(), name("canonical_flat_meta_v1"));
        mdbxc::KeyValueTable<std::uint64_t, std::string> heads(context->connection(), name("canonical_flat_heads_v1"));
        Table structures(context->connection(), name("canonical_flat_structures_v1"));
        Table blocks(context->connection(), name("canonical_flat_blocks_v1"));
        auto transaction = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        require(metadata.find("@schema_version", transaction) == std::optional<std::string>{"canonical_flat_storage.v1"} &&
            heads.find(1, transaction) == std::optional<std::string>{"3"} &&
            heads.find(2, transaction) == std::optional<std::string>{"0"}, "OLD schema or current heads changed");
        for(const auto& revision : expected) {
            const auto document = mapping.find_document_in_transaction(revision.document_id, transaction);
            require(document.has_value(), "OLD document mapping missing");
            const auto encoded = structures.find(std::to_string(document->value()) + "/" + std::to_string(revision.revision), transaction);
            require(encoded.has_value(), "OLD structure missing");
            const auto decoded = CanonicalFlatStorageCodec::decode_structure(*encoded);
            require(CanonicalFlatStorageCodec::encode_structure(decoded) == CanonicalFlatStorageCodec::encode_structure(portable(revision)),
                    "OLD decoded structure parity failed");
            for(const auto& block : revision.blocks) {
                const auto id = mapping.find_block_in_transaction(revision.document_id, block.id, transaction);
                require(id.has_value(), "OLD block mapping missing");
                const auto payload = blocks.find(std::to_string(id->value()) + "/" + std::to_string(block.revision), transaction);
                require(payload && CanonicalFlatStorageCodec::decode_block(*payload) == portable(block), "OLD block parity failed");
            }
            std::string observed; add_revision(observed, revision); append(snapshot, observed);
        }
        transaction.commit();
    } else {
        MdbxCanonicalFlatStorage flat(flat_options(context));
        for(const auto& revision : expected) {
            const auto actual = flat.read_revision(revision.document_id, revision.revision);
            require(actual.has_value(), "numeric historical revision missing");
            std::string observed, reference;
            add_revision(observed, *actual); add_revision(reference, revision);
            require(observed == reference, "numeric decoded historical parity failed");
            for(const auto& block : revision.blocks) {
                const auto point = flat.read_block(revision.document_id, block.id, revision.revision);
                require(point && portable(*point) == portable(block), "numeric point-block parity failed");
            }
            append(snapshot, observed);
        }
        const auto current = flat.read_current(DocumentId{kDocument});
        const auto other = flat.read_current(DocumentId{"scope:red/document"});
        require(current && current->revision == 3 && other && other->revision == 0 &&
            !flat.read_block(DocumentId{kDocument}, ContentBlockId{"deleted"}), "current head/deletion changed");
        const auto before = flat.read_current(DocumentId{kDocument});
        const auto reused = flat.commit({DocumentId{kDocument}, 3, {InsertBlock{
            {ContentBlockId{"deleted"}, 1, ContentBlockKind::Paragraph, std::nullopt, "Reused"}, 1}}});
        require(reused.status == CanonicalEditStatus::InvalidEdit &&
            flat.read_current(DocumentId{kDocument})->revision == before->revision, "historical block ID reused");
    }
    auto transaction = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    for(const auto* component : {"meta", "heads", "structures", "blocks"}) {
        const auto suffix = std::string{"canonical_flat_"} + component + (old ? "_v1" : "_v2");
        MDBX_dbi database{}; MDBX_stat statistics{}; unsigned flags{};
        require(mdbx_dbi_open(transaction.handle(), name(suffix.c_str()).c_str(), MDBX_DB_ACCEDE, &database) == MDBX_SUCCESS &&
            mdbx_dbi_stat(transaction.handle(), database, &statistics, sizeof(statistics)) == MDBX_SUCCESS &&
            mdbx_dbi_flags(transaction.handle(), database, &flags) == MDBX_SUCCESS,
            "cannot read canonical physical counts");
        const auto count = std::string{component} == "meta" ? 1U : (std::string{component} == "heads" ? 2U : 5U);
        require(statistics.ms_entries == count, "canonical physical count mismatch");
        require(flags == (std::string{component} == "heads" ? MDBX_INTEGERKEY : 0U), "canonical physical flags mismatch");
    }
    transaction.commit();
    return hex_digest(snapshot);
}
void corrupt(const std::shared_ptr<MdbxStorageContext>& context, const std::string& mode) {
    if(mode == "wrong-comparator") {
        auto transaction = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        MDBX_dbi database{};
        require(mdbx_dbi_open(transaction.handle(), name("canonical_flat_structures_v2").c_str(), MDBX_DB_ACCEDE, &database) == MDBX_SUCCESS,
                "cannot open comparator control");
        require(mdbx_drop(transaction.handle(), database, true) == MDBX_SUCCESS, "cannot drop comparator control");
        require(mdbx_dbi_open(transaction.handle(), name("canonical_flat_structures_v2").c_str(),
            MDBX_CREATE | MDBX_INTEGERKEY, &database) == MDBX_SUCCESS, "cannot create comparator control");
        transaction.commit();
    } else if(mode == "missing-row") {
        Table blocks(context->connection(), name("canonical_flat_blocks_v2"));
        auto transaction = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        require(blocks.erase(canonical_flat_composite_key(2, 2), transaction), "missing-row control key absent");
        transaction.commit();
    } else if(mode == "missing-reverse") {
        mdbxc::KeyValueTable<std::uint64_t, std::string> reverse(context->connection(), name("identity_blocks_v1"));
        auto transaction = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        require(reverse.erase(1, transaction), "missing reverse control key absent");
        transaction.commit();
    } else {
        Table metadata(context->connection(), name(mode == "wrong-unit-schema" ? "ku_typed_v1_meta" : "canonical_flat_meta_v2"));
        auto transaction = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        metadata.insert_or_assign("@schema_version", "unsupported.control", transaction);
        transaction.commit();
    }
}
} // namespace

int main(int argc, char** argv) {
    if(argc != 3) return 64;
    const std::string mode = argv[1];
    try {
        auto context = MdbxStorageContext::open(argv[2]);
        if(mode == "create") create_numeric(context);
        else if(mode == "create-old") create_old(context);
        else if(mode == "verify" || mode == "verify-old") {
            std::cout << "{\"logical_snapshot_sha256\":\"" << verify(context, mode == "verify-old") << "\"}\n";
            return 0;
        } else if(mode == "reject-old") {
            bool rejected = false;
            try { MdbxCanonicalFlatStorage flat(flat_options(context)); }
            catch(const std::exception& error) {
                rejected = std::string{error.what()}.find("flat v1 requires explicit export/rebuild") != std::string::npos;
            }
            require(rejected, "OLD physical schema was not explicitly rejected");
        } else if(mode == "interrupt") {
            auto options = flat_options(context);
            options.before_head_publication = [] { std::_Exit(24); };
            MdbxCanonicalFlatStorage flat(options);
            static_cast<void>(flat.commit({DocumentId{kDocument}, 3,
                {ReplaceBlockText{ContentBlockId{"body"}, "Uncommitted"}}}));
            throw std::runtime_error("interruption hook did not execute");
        } else if(mode == "missing-row" || mode == "wrong-schema" || mode == "wrong-unit-schema" ||
                  mode == "wrong-comparator" || mode == "missing-reverse") corrupt(context, mode);
        else return 64;
        std::cout << "{\"result\":\"PASS\"}\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
