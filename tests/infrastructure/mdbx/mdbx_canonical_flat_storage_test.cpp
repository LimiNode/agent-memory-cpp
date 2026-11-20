#include <agent_memory/infrastructure/mdbx/MdbxCanonicalFlatStorage.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxStorageIdentityMapping.hpp>
#include <agent_memory/storage/CanonicalNavigationStore.hpp>
#include <agent_memory/storage/CanonicalFlatStorage.hpp>
#include <agent_memory/domain/Sha256.hpp>
#include <mdbx_containers/KeyValueTable.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <chrono>
#include <filesystem>
#include <future>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <thread>

using namespace agent_memory;

namespace {
void expect(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
template<class Function> void rejects(Function&& function, const char* message) {
    bool rejected = false;
    try { function(); } catch(const std::exception&) { rejected = true; }
    expect(rejected, message);
}
struct Fixture final {
    std::filesystem::path path;
    std::shared_ptr<MdbxStorageContext> context;
    explicit Fixture(const char* name) {
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        path = std::filesystem::temp_directory_path() /
            (std::string{"agent_memory_flat_closure_"} + name + std::to_string(nonce) + ".mdbx");
        context = MdbxStorageContext::open(path.string());
    }
    ~Fixture() {
        context.reset();
        std::error_code error;
        std::filesystem::remove(path, error);
        std::filesystem::remove(path.string() + "-lck", error);
    }
    MdbxCanonicalFlatStorageOptions options() const {
        MdbxCanonicalFlatStorageOptions result;
        result.context = context;
        result.table_prefix = "flat test";
        return result;
    }
    void reopen() { context.reset(); context = MdbxStorageContext::open(path.string()); }
};
CanonicalDocumentRevision make_revision(const char* id = "flat-doc") {
    CanonicalDocumentRevision result;
    result.document_id = DocumentId{id};
    result.metadata.set("owner", "test");
    result.blocks = {
        {ContentBlockId{"h"}, 1, ContentBlockKind::Heading, std::nullopt, "Heading"},
        {ContentBlockId{"p"}, 1, ContentBlockKind::Paragraph, ContentBlockId{"h"}, "Payload"},
        {ContentBlockId{"c"}, 1, ContentBlockKind::Code, ContentBlockId{"p"}, "return 7;"}};
    return result;
}
void same(const CanonicalDocumentRevision& left, const CanonicalDocumentRevision& right) {
    if(left.document_id != right.document_id || left.revision != right.revision ||
       left.metadata.values() != right.metadata.values() || left.blocks.size() != right.blocks.size()) {
        std::cerr << "parity: " << left.document_id.value() << '@' << left.revision << " blocks=" << left.blocks.size()
            << " metadata=" << left.metadata.values().size() << " vs " << right.document_id.value() << '@'
            << right.revision << " blocks=" << right.blocks.size() << " metadata=" << right.metadata.values().size() << '\n';
        for(const auto& item : left.metadata.values()) std::cerr << "left " << item.first << '=' << item.second << '\n';
        for(const auto& item : right.metadata.values()) std::cerr << "right " << item.first << '=' << item.second << '\n';
    }
    expect(left.document_id == right.document_id && left.revision == right.revision &&
        left.metadata.values() == right.metadata.values() && left.blocks.size() == right.blocks.size(),
        "revision identity/metadata/count parity failed");
    for(std::size_t i = 0; i < left.blocks.size(); ++i) {
        const auto& a = left.blocks[i]; const auto& b = right.blocks[i];
        expect(a.id == b.id && a.revision == b.revision && a.kind == b.kind &&
            a.parent_id == b.parent_id && a.text == b.text, "block parity failed");
    }
}
void same_outline(const CanonicalOutline& left, const CanonicalOutline& right) {
    expect(left.document_id == right.document_id && left.document_revision == right.document_revision &&
        left.entries.size() == right.entries.size(), "outline identity parity failed");
    for(std::size_t i = 0; i < left.entries.size(); ++i) {
        const auto& a = left.entries[i]; const auto& b = right.entries[i];
        expect(a.locator.block_id == b.locator.block_id && a.locator.block_revision == b.locator.block_revision &&
            a.parent_id == b.parent_id && a.depth == b.depth && a.kind == b.kind &&
            a.text_bytes == b.text_bytes && a.child_count == b.child_count, "outline entry parity failed");
    }
}
CanonicalEditRequest replace(std::uint64_t revision, const char* text) {
    return {DocumentId{"flat-doc"}, revision, {ReplaceBlockText{ContentBlockId{"p"}, text}}};
}
std::uint64_t get_u64(const std::string& input, std::size_t offset) {
    expect(offset + 8U <= input.size(), "test u64 out of bounds");
    std::uint64_t value = 0;
    for(unsigned i = 0; i < 8U; ++i) value |= static_cast<std::uint64_t>(static_cast<unsigned char>(input[offset + i])) << (i * 8U);
    return value;
}
void add_u64(std::string& value, std::uint64_t number) {
    for(unsigned i = 0; i < 8U; ++i) value.push_back(static_cast<char>(number >> (i * 8U)));
}
void hash_envelope(std::string& value) {
    const auto digest = sha256(value);
    value.append(reinterpret_cast<const char*>(digest.data()), digest.size());
}
std::size_t inner_offset(const std::string& value) { return 29U + static_cast<std::size_t>(get_u64(value, 21U)); }

void multirevision_differential_and_navigation() {
    Fixture fixture("history");
    InMemoryCanonicalContentStore memory;
    MdbxCanonicalContentStoreOptions legacy_options;
    legacy_options.context = fixture.context; legacy_options.table_prefix = "legacy";
    MdbxCanonicalStorageMetrics metrics;
    auto options = fixture.options(); options.metrics = &metrics;
    {
        MdbxCanonicalContentStore legacy(legacy_options);
        MdbxCanonicalFlatStorage flat(options);
        expect(memory.create_document(make_revision()) && legacy.create_document(make_revision()) &&
            flat.create_document(make_revision()), "baseline creation failed");
        expect(!flat.create_document(make_revision()), "duplicate accepted");
        std::vector<CanonicalEditRequest> edits = {replace(0, "One"), replace(1, "Two"), replace(2, "Three")};
        Metadata metadata; metadata.set("owner", "changed");
        edits.push_back({DocumentId{"flat-doc"}, 3, {UpdateDocumentMetadata{metadata}}});
        edits.push_back({DocumentId{"flat-doc"}, 4,
            {InsertBlock{{ContentBlockId{"new"}, 99, ContentBlockKind::Paragraph, std::nullopt, "New"}, 1}}});
        edits.push_back({DocumentId{"flat-doc"}, 5, {MoveBlock{ContentBlockId{"new"}, ContentBlockId{"h"}, 1}}});
        edits.push_back({DocumentId{"flat-doc"}, 6, {DeleteBlock{ContentBlockId{"new"}}}});
        for(const auto& edit : edits) {
            metrics = {};
            const auto actual = flat.commit(edit);
            const auto reference = memory.commit(edit);
            const auto c1 = legacy.commit(edit);
            expect(actual.status == CanonicalEditStatus::Ok && actual.status == reference.status && actual.status == c1.status,
                "multi-edit status parity failed");
            same(*actual.revision, *reference.revision); same(*actual.revision, *c1.revision);
            if(edit.expected_revision < 3U) expect(metrics.block_versions_written == 1U && metrics.value_writes == 3U,
                "one text edit rewrote unchanged blocks");
            if(edit.expected_revision == 3U) expect(metrics.block_versions_written == 0U && metrics.value_writes == 2U,
                "metadata edit wrote block payloads");
        }
        const CanonicalEditRequest reuse{DocumentId{"flat-doc"}, 7,
            {InsertBlock{{ContentBlockId{"new"}, 1, ContentBlockKind::Paragraph, std::nullopt, "Reuse"}, 1}}};
        expect(flat.commit(reuse).status == CanonicalEditStatus::InvalidEdit &&
            memory.commit(reuse).status == CanonicalEditStatus::InvalidEdit &&
            legacy.commit(reuse).status == CanonicalEditStatus::InvalidEdit, "historical block identity reused");
        expect(flat.commit(replace(0, "Stale")).status == CanonicalEditStatus::Conflict, "stale writer accepted");
        expect(flat.commit(replace(7, "Three")).status == CanonicalEditStatus::NoChange, "no-change published");
        for(std::uint64_t revision = 0; revision <= 7; ++revision) {
            same(*flat.read_revision(DocumentId{"flat-doc"}, revision), *memory.read_revision(DocumentId{"flat-doc"}, revision));
            expect(flat.materialize_markdown(DocumentId{"flat-doc"}, revision) ==
                memory.materialize_markdown(DocumentId{"flat-doc"}, revision), "historical render failed");
            same_outline(*flat.read_outline(DocumentId{"flat-doc"}, revision),
                *build_canonical_outline(*memory.read_revision(DocumentId{"flat-doc"}, revision)));
        }
        metrics = {};
        expect(flat.read_outline(DocumentId{"flat-doc"}, 7)->entries.size() == 3U && metrics.value_reads == 1U,
            "outline read block payloads");
        metrics = {};
        expect(flat.read_block(DocumentId{"flat-doc"}, ContentBlockId{"c"}, 7)->text == "return 7;" &&
            metrics.value_reads == 2U, "point lookup did not select one block payload");
        CanonicalNavigationStore reference(memory);
        for(const auto root : {"h", "p", "c", "absent"}) {
            for(const auto limit : {0U, 1U, 20U}) {
                CanonicalSubtreeRequest request; request.root_id = ContentBlockId{root}; request.max_blocks = limit;
                auto actual = flat.read_subtree(DocumentId{"flat-doc"}, 7, request);
                auto expected = reference.read_subtree(DocumentId{"flat-doc"}, 7, request);
                expect(actual.status == expected.status && actual.truncated == expected.truncated &&
                    actual.blocks.size() == expected.blocks.size(), "subtree budget parity failed");
            }
        }
        CanonicalSubtreeRequest request; request.root_id = ContentBlockId{"c"};
        metrics = {};
        expect(flat.read_subtree(DocumentId{"flat-doc"}, 7, request).blocks.size() == 1U && metrics.value_reads == 2U,
            "subtree loaded unrelated payloads");
        request.include_text = false; request.max_text_bytes = 0;
        metrics = {};
        const auto metadata_only = flat.read_subtree(DocumentId{"flat-doc"}, 7, request);
        expect(metadata_only.blocks.size() == 1U && metadata_only.blocks[0].text.empty() && metrics.value_reads == 1U,
            "metadata-only subtree loaded text");
        request.include_text = true; request.max_text_bytes = 0;
        metrics = {};
        expect(flat.read_subtree(DocumentId{"flat-doc"}, 7, request).status == CanonicalNavigationStatus::BudgetExceeded &&
            metrics.value_reads == 1U, "text budget read a disallowed payload");
        expect(!flat.read_outline(DocumentId{"flat-doc"}, 7, 2), "outline budget ignored");
        for(const auto count : {0U, 1U, 10U}) {
            const auto a = flat.read_children(DocumentId{"flat-doc"}, 7, ContentBlockId{"h"}, count);
            const auto b = reference.read_children(DocumentId{"flat-doc"}, 7, ContentBlockId{"h"}, count);
            expect(a.status == b.status && a.entries.size() == b.entries.size() && a.truncated == b.truncated,
                "children parity failed");
        }
        expect(flat.read_children(DocumentId{"absent"}, 0, std::nullopt, 0).status ==
            reference.read_children(DocumentId{"absent"}, 0, std::nullopt, 0).status,
            "missing document and invalid children budget precedence changed");
        request.max_blocks = 0;
        expect(flat.read_subtree(DocumentId{"absent"}, 0, request).status ==
            reference.read_subtree(DocumentId{"absent"}, 0, request).status,
            "missing document and invalid subtree budget precedence changed");
        expect(flat.create_document(make_revision("other/scope")), "mixed identity create failed");
        expect(flat.read_block(DocumentId{"other/scope"}, ContentBlockId{"p"})->text == "Payload", "scope isolated read failed");
        expect(!flat.read_block(DocumentId{"missing/scope"}, ContentBlockId{"p"}), "wrong document returned payload");
    }
    // All independent adapter objects are destroyed before a fresh environment reopen below.
    options.metrics = nullptr; options.context.reset(); legacy_options.context.reset();
    fixture.reopen();
    {
        MdbxCanonicalFlatStorage reopened(fixture.options());
        expect(reopened.read_current(DocumentId{"flat-doc"})->revision == 7U, "reopen lost head");
        for(std::uint64_t revision = 0; revision <= 7; ++revision)
            expect(reopened.materialize_markdown(DocumentId{"flat-doc"}, revision) ==
                memory.materialize_markdown(DocumentId{"flat-doc"}, revision), "reopen historical render failed");
        expect(reopened.read_block(DocumentId{"flat-doc"}, ContentBlockId{"p"}, 0)->revision == 1U &&
            reopened.read_block(DocumentId{"flat-doc"}, ContentBlockId{"p"}, 7)->revision == 4U,
            "block/document revisions conflated");
        const CanonicalEditRequest reuse{DocumentId{"flat-doc"}, 7,
            {InsertBlock{{ContentBlockId{"new"}, 1, ContentBlockKind::Paragraph, std::nullopt, "Reuse"}, 1}}};
        expect(reopened.commit(reuse).status == CanonicalEditStatus::InvalidEdit &&
            reopened.read_current(DocumentId{"flat-doc"})->revision == 7U,
            "reopen lost historical no-reuse ledger");
    }
}

// CTest owns a process-level TIMEOUT, so a blocked MDBX writer cannot hang the suite.
void concurrent_publication_and_abort() {
    Fixture fixture("concurrency");
    {
        MdbxCanonicalFlatStorage first(fixture.options()), second(fixture.options());
        expect(first.create_document(make_revision()), "race fixture create failed");
        std::promise<void> go; auto start = go.get_future().share();
        std::promise<CanonicalEditResult> a, b;
        std::thread one([&] { try { start.wait(); a.set_value(first.commit(replace(0, "Winner A"))); }
            catch(...) { a.set_exception(std::current_exception()); } });
        std::thread two([&] { try { start.wait(); b.set_value(second.commit(replace(0, "Winner B"))); }
            catch(...) { b.set_exception(std::current_exception()); } });
        go.set_value(); one.join(); two.join();
        const auto x = a.get_future().get().status; const auto y = b.get_future().get().status;
        expect((x == CanonicalEditStatus::Ok && y == CanonicalEditStatus::Conflict) ||
            (y == CanonicalEditStatus::Ok && x == CanonicalEditStatus::Conflict), "two writers published same expected revision");
        expect(first.read_current(DocumentId{"flat-doc"})->revision == 1U &&
            first.read_revision(DocumentId{"flat-doc"}, 0)->blocks[1].text == "Payload", "race corrupted history");
        std::promise<void> create_go; auto create_start = create_go.get_future().share();
        std::promise<bool> c, d;
        std::thread three([&] { try { create_start.wait(); c.set_value(first.create_document(make_revision("race-create"))); }
            catch(...) { c.set_exception(std::current_exception()); } });
        std::thread four([&] { try { create_start.wait(); d.set_value(second.create_document(make_revision("race-create"))); }
            catch(...) { d.set_exception(std::current_exception()); } });
        create_go.set_value(); three.join(); four.join();
        expect(c.get_future().get() != d.get_future().get(), "concurrent duplicate create succeeded twice");
    }
    fixture.reopen();
    {
        MdbxCanonicalFlatStorage store(fixture.options());
        expect(store.read_current(DocumentId{"flat-doc"})->revision == 1U &&
            store.read_current(DocumentId{"race-create"})->revision == 0U, "reopen after races failed");
    }
    auto options = fixture.options();
    options.before_head_publication = [] { throw std::runtime_error("injected abort before head"); };
    {
        MdbxCanonicalFlatStorage failing(options);
        rejects([&] { static_cast<void>(failing.commit(replace(1, "Aborted"))); }, "injected edit abort ignored");
        const CanonicalEditRequest insert{DocumentId{"flat-doc"}, 1,
            {InsertBlock{{ContentBlockId{"aborted-block"}, 1, ContentBlockKind::Paragraph, std::nullopt, "Aborted"}, 1}}};
        rejects([&] { static_cast<void>(failing.commit(insert)); }, "injected insert abort ignored");
        rejects([&] { static_cast<void>(failing.create_document(make_revision("aborted-create"))); }, "injected create abort ignored");
    }
    options.context.reset(); fixture.reopen();
    MdbxCanonicalFlatStorage recovered(fixture.options());
    expect(recovered.read_current(DocumentId{"flat-doc"})->revision == 1U &&
        !recovered.read_revision(DocumentId{"flat-doc"}, 2) && !recovered.read_current(DocumentId{"aborted-create"}),
        "aborted transaction left published rows or mapping");
    MdbxStorageIdentityMappingOptions identity_options;
    identity_options.context = fixture.context; identity_options.table_prefix = "flat test";
    MdbxStorageIdentityMapping identity(identity_options);
    expect(!identity.find_block(DocumentId{"flat-doc"}, ContentBlockId{"aborted-block"}),
        "aborted insertion left its numeric mapping");
    expect(recovered.commit(replace(1, "Recovered")).status == CanonicalEditStatus::Ok, "abort prevented valid recovery");
}

void key_schema_corruption_and_immutability() {
    const auto max = std::numeric_limits<std::uint64_t>::max();
    expect(canonical_flat_composite_key(1, 2).size() == 16U &&
        canonical_flat_composite_key(1, 2) < canonical_flat_composite_key(1, 10) &&
        canonical_flat_composite_key(1, max) < canonical_flat_composite_key(2, 0) &&
        canonical_flat_composite_key(1, 23) != canonical_flat_composite_key(12, 3), "composite numeric ordering/collision failed");
    const auto bytes = canonical_flat_composite_key(0x0102030405060708ULL, 0x1112131415161718ULL);
    for(unsigned i = 0; i < 8U; ++i) expect(static_cast<unsigned char>(bytes[i]) == i + 1U &&
        static_cast<unsigned char>(bytes[i + 8U]) == i + 17U, "composite key is not canonical big endian");
    Fixture fixture("corrupt");
    MdbxCanonicalFlatStorage store(fixture.options());
    expect(store.create_document(make_revision()), "corruption fixture create failed");
    MdbxStorageIdentityMappingOptions identity_options; identity_options.context = fixture.context;
    identity_options.table_prefix = "flat test";
    MdbxStorageIdentityMapping identity(identity_options);
    const auto doc = identity.find_document(DocumentId{"flat-doc"})->value();
    const auto p = identity.find_block(DocumentId{"flat-doc"}, ContentBlockId{"p"})->value();
    const auto c = identity.find_block(DocumentId{"flat-doc"}, ContentBlockId{"c"})->value();
    mdbxc::KeyValueTable<std::string, std::string> blocks(fixture.context->connection(), "flat_test_canonical_flat_blocks_v2");
    mdbxc::KeyValueTable<std::string, std::string> structures(fixture.context->connection(), "flat_test_canonical_flat_structures_v2");
    const auto pkey = canonical_flat_composite_key(p, 1);
    const auto original = *blocks.find(pkey);
    const auto other = *blocks.find(canonical_flat_composite_key(c, 1));
    blocks.insert_or_assign(pkey, other);
    rejects([&] { static_cast<void>(store.read_block(DocumentId{"flat-doc"}, ContentBlockId{"p"}, 0)); },
        "point lookup admitted swapped payload");
    expect(store.read_subtree(DocumentId{"flat-doc"}, 0, {}).status == CanonicalNavigationStatus::InvalidDocument,
        "subtree admitted corrupted payload");
    expect(store.read_outline(DocumentId{"flat-doc"}, 0).has_value(), "outline unnecessarily decoded corrupted text");
    blocks.insert_or_assign(pkey, original);
    auto corrupt = original; corrupt[4] = 'X';
    blocks.insert_or_assign(pkey, corrupt);
    rejects([&] { static_cast<void>(store.read_block(DocumentId{"flat-doc"}, ContentBlockId{"p"}, 0)); },
        "point lookup admitted invalid envelope schema");
    blocks.insert_or_assign(pkey, original);
    const auto skey = canonical_flat_composite_key(doc, 0);
    const auto structure_original = *structures.find(skey);
    // Recompute all integrity trailers to exercise semantic/key binding rather than only hash rejection.
    const auto inner = inner_offset(original);
    const auto decoded = CanonicalFlatStorageCodec::decode_block(original.substr(inner + 8U,
        static_cast<std::size_t>(get_u64(original, inner))));
    for(unsigned mutation = 0; mutation < 6U; ++mutation) {
        auto forged_block = decoded;
        forged_block.logical_digest = {}; forged_block.physical_checksum = 0;
        if(mutation == 0U) forged_block.id = "different";
        if(mutation == 1U) forged_block.revision = 123;
        if(mutation == 2U) forged_block.kind = 2;
        if(mutation == 3U) forged_block.parent_id = "different-parent";
        const auto encoded = CanonicalFlatStorageCodec::encode_block(forged_block);
        auto bound = original.substr(0, inner);
        if(mutation == 4U) bound[5] ^= 1; // Embedded physical key no longer matches the actual MDBX key.
        if(mutation == 5U) bound[29] ^= 1; // Embedded document identity no longer matches the requested document.
        add_u64(bound, encoded.size()); bound += encoded; hash_envelope(bound);
        auto forged_structure = structure_original.substr(0, structure_original.size() - 32U);
        const auto structure_inner = inner_offset(forged_structure);
        const auto physical_start = structure_inner + 8U + static_cast<std::size_t>(get_u64(forged_structure, structure_inner)) + 8U;
        const auto digest_offset = physical_start + 48U + 16U; // Second block reference, after storage id and text size.
        const auto bound_digest = sha256(bound);
        forged_structure.replace(digest_offset, 32U, reinterpret_cast<const char*>(bound_digest.data()), 32U);
        hash_envelope(forged_structure);
        blocks.insert_or_assign(pkey, bound); structures.insert_or_assign(skey, forged_structure);
        rejects([&] { static_cast<void>(store.read_block(DocumentId{"flat-doc"}, ContentBlockId{"p"}, 0)); },
            "point read admitted a rehashed semantic/key/document binding mismatch");
    }
    blocks.insert_or_assign(pkey, original); structures.insert_or_assign(skey, structure_original);
    structures.insert_or_assign(canonical_flat_composite_key(doc, 4), structure_original);
    rejects([&] { static_cast<void>(store.read_block(DocumentId{"flat-doc"}, ContentBlockId{"p"}, 4)); },
        "point lookup admitted wrong document revision binding");
    structures.erase(canonical_flat_composite_key(doc, 4));
    const auto future_block = canonical_flat_composite_key(p, 2);
    blocks.insert_or_assign(future_block, "forged future block");
    rejects([&] { static_cast<void>(store.commit(replace(0, "Change"))); }, "immutable block row overwritten");
    expect(*blocks.find(future_block) == "forged future block" && store.read_current(DocumentId{"flat-doc"})->revision == 0U,
        "immutable collision changed published history");
    blocks.erase(future_block);
    const auto future_structure = canonical_flat_composite_key(doc, 1);
    structures.insert_or_assign(future_structure, "forged future structure");
    rejects([&] { static_cast<void>(store.commit(replace(0, "Change"))); }, "immutable structure row overwritten");
    expect(!blocks.find(future_block) && *structures.find(future_structure) == "forged future structure",
        "structure collision failed transaction rollback");
    structures.erase(future_structure);
    expect(store.commit(replace(0, "Valid")).status == CanonicalEditStatus::Ok, "collision recovery failed");
    Fixture old("old_v1");
    mdbxc::KeyValueTable<std::string, std::string> old_rows(old.context->connection(), "flat_test_canonical_flat_structures_v1");
    old_rows.insert_or_assign("1/0", "old format");
    rejects([&] { MdbxCanonicalFlatStorage rejected(old.options()); }, "old v1 schema silently reopened as v2");
    Fixture wrong_flags("flags");
    mdbxc::KeyValueTable<std::string, std::string> reversed(wrong_flags.context->connection(),
        "flat_test_canonical_flat_structures_v2", MDBX_CREATE | MDBX_REVERSEKEY);
    rejects([&] { MdbxCanonicalFlatStorage rejected(wrong_flags.options()); }, "incompatible comparator flags admitted");
    Fixture markerless("markerless");
    mdbxc::KeyValueTable<std::string, std::string> rows(markerless.context->connection(), "flat_test_canonical_flat_structures_v2");
    rows.insert_or_assign(canonical_flat_composite_key(1, 0), "forged");
    rejects([&] { MdbxCanonicalFlatStorage rejected(markerless.options()); }, "markerless state admitted");
}
} // namespace

int main() {
    try {
        multirevision_differential_and_navigation();
        concurrent_publication_and_abort();
        key_schema_corruption_and_immutability();
    } catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
    return 0;
}
#else
int main() { return 0; }
#endif
