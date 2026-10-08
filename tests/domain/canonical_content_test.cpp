#include <agent_memory.hpp>

#include <iostream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    using namespace agent_memory;

    CanonicalDocumentRevision baseline(const DocumentId& id) {
        const ContentBlockId heading{"heading"};
        return CanonicalDocumentRevision{
            id,
            0,
            {},
            {
                ContentBlock{heading, 99, ContentBlockKind::Heading, std::nullopt, "Notes"},
                ContentBlock{ContentBlockId{"first"}, 1, ContentBlockKind::Paragraph, heading, "First paragraph."},
                ContentBlock{ContentBlockId{"second"}, 1, ContentBlockKind::Paragraph, heading, "Second paragraph."},
                ContentBlock{ContentBlockId{"code"}, 1, ContentBlockKind::Code, std::nullopt, "return 0;"}
            }
        };
    }

    CanonicalEditRequest request(const DocumentId& id, std::uint64_t revision,
        std::vector<CanonicalEditOperation> operations) {
        return CanonicalEditRequest{id, revision, std::move(operations)};
    }

} // namespace

int main() {
    using namespace agent_memory;
    const DocumentId document_id{"doc:canonical"};
    InMemoryCanonicalContentStore store;

    const auto missing_document = store.commit(request(DocumentId{"missing"}, 0, {}));
    if(missing_document.status != CanonicalEditStatus::NotFound) {
        return fail("missing document must be reported as not found");
    }

    if(!store.create_document(baseline(document_id))) {
        return fail("baseline document must be created");
    }
    const auto initial = store.read_current(document_id);
    if(!initial || initial->revision != 0 || initial->blocks.size() != 4 ||
        initial->blocks.front().revision != 1) {
        return fail("initial document must be readable at revision zero");
    }
    if(!store.read_revision(document_id, 0) || store.read_revision(document_id, 99)) {
        return fail("exact historical revision lookup is incorrect");
    }
    auto copied_initial = *initial;
    copied_initial.blocks.front().text = "local copy only";
    if(store.read_block(document_id, ContentBlockId{"heading"}, 0)->text != "Notes") {
        return fail("read results must not expose mutable store internals");
    }
    if(!store.read_block(document_id, ContentBlockId{"first"}) ||
        store.read_block(document_id, ContentBlockId{"missing"})) {
        return fail("current block lookup is incorrect");
    }

    auto replaced = store.commit(request(document_id, 0, {
        ReplaceBlockText{ContentBlockId{"first"}, "Edited paragraph."},
        ReplaceBlockText{ContentBlockId{"heading"}, "Edited notes"}
    }));
    if(replaced.status != CanonicalEditStatus::Ok || !replaced.revision ||
        replaced.revision->revision != 1 || replaced.changes.changed_blocks.size() != 2) {
        return fail("text replacement must create one immutable revision");
    }
    if(store.read_block(document_id, ContentBlockId{"first"}, 0)->text != "First paragraph.") {
        return fail("old revision must remain unchanged");
    }

    auto inserted = store.commit(request(document_id, 1, {
        InsertBlock{ContentBlock{ContentBlockId{"inserted"}, 99, ContentBlockKind::Paragraph,
            ContentBlockId{"heading"}, "Inserted paragraph."}, 1}
    }));
    if(inserted.status != CanonicalEditStatus::Ok || inserted.changes.inserted_blocks.size() != 1) {
        return fail("sibling insertion must succeed");
    }
    const auto after_insert = store.read_current(document_id);
    if(!after_insert || !store.read_block(document_id, ContentBlockId{"second"}) ||
        store.read_block(document_id, ContentBlockId{"second"})->id != ContentBlockId{"second"}) {
        return fail("sibling insertion must preserve unrelated block identity");
    }

    auto moved = store.commit(request(document_id, 2, {
        MoveBlock{ContentBlockId{"second"}, std::nullopt, 1}
    }));
    if(moved.status != CanonicalEditStatus::Ok || moved.changes.moved_blocks.size() != 1 ||
        moved.changes.changed_blocks.size() != 0) {
        return fail("moving a block must preserve identity and use moved_blocks");
    }
    const auto moved_block = store.read_block(document_id, ContentBlockId{"second"});
    if(!moved_block || moved_block->parent_id || moved_block->revision != 2) {
        return fail("move must update parent and block revision");
    }
    const auto moved_historical = store.read_block(document_id, ContentBlockId{"second"}, 2);
    if(!moved_historical || !moved_historical->parent_id || *moved_historical->parent_id != ContentBlockId{"heading"}) {
        return fail("historical moved block must retain its old placement");
    }

    auto metadata = store.commit(request(document_id, 3, {
        UpdateDocumentMetadata{[] {
            Metadata value;
            value.set("scope", "user:42");
            return value;
        }()}
    }));
    if(metadata.status != CanonicalEditStatus::Ok || !metadata.changes.metadata_changed ||
        metadata.changes.structure_changed || !metadata.revision || metadata.revision->revision != 4) {
        return fail("metadata-only edit must not change block structure");
    }
    if(metadata.revision->blocks[0].revision != moved.revision->blocks[0].revision) {
        return fail("metadata-only edit must not change block revisions");
    }

    auto no_change = store.commit(request(document_id, 4, {
        ReplaceBlockText{ContentBlockId{"first"}, "Edited paragraph."}
    }));
    if(no_change.status != CanonicalEditStatus::NoChange || !no_change.revision || no_change.revision->revision != 4) {
        return fail("exact logical no-op must not create a revision");
    }
    const auto net_no_change = store.commit(request(document_id, 4, {
        ReplaceBlockText{ContentBlockId{"first"}, "temporary"},
        ReplaceBlockText{ContentBlockId{"first"}, "Edited paragraph."}
    }));
    if(net_no_change.status != CanonicalEditStatus::NoChange || !net_no_change.revision ||
        net_no_change.revision->revision != 4) {
        return fail("a multi-operation edit returning to the original state must be a no-op");
    }
    const auto metadata_net_no_change = store.commit(request(document_id, 4, {
        ReplaceBlockText{ContentBlockId{"first"}, "temporary"},
        ReplaceBlockText{ContentBlockId{"first"}, "Edited paragraph."},
        UpdateDocumentMetadata{metadata.revision->metadata}
    }));
    if(metadata_net_no_change.status != CanonicalEditStatus::NoChange ||
        !metadata_net_no_change.changes.changed_blocks.empty() ||
        metadata_net_no_change.changes.metadata_changed) {
        return fail("net change set must omit reverted transient operations");
    }

    InMemoryCanonicalContentStore revision_store;
    const DocumentId revision_document_id{"doc:revision"};
    if(!revision_store.create_document(baseline(revision_document_id))) {
        return fail("revision baseline must be created");
    }
    const auto one_published_revision = revision_store.commit(request(revision_document_id, 0, {
        ReplaceBlockText{ContentBlockId{"first"}, "temporary"},
        ReplaceBlockText{ContentBlockId{"first"}, "published"}
    }));
    const auto published_block = revision_store.read_block(revision_document_id, ContentBlockId{"first"});
    if(one_published_revision.status != CanonicalEditStatus::Ok || !published_block ||
        published_block->revision != 2 || one_published_revision.changes.changed_blocks.size() != 1) {
        return fail("block revision must advance once per published document revision");
    }

    const auto stale = store.commit(request(document_id, 3, {
        ReplaceBlockText{ContentBlockId{"first"}, "stale"}
    }));
    if(stale.status != CanonicalEditStatus::Conflict) {
        return fail("stale expected revision must be rejected as conflict");
    }

    const auto invalid_parent = store.commit(request(document_id, 4, {
        InsertBlock{ContentBlock{ContentBlockId{"bad-parent"}, 1, ContentBlockKind::Paragraph,
            ContentBlockId{"missing-parent"}, "bad"}, 0}
    }));
    if(invalid_parent.status != CanonicalEditStatus::InvalidEdit) {
        return fail("missing parent must be rejected");
    }

    const auto cycle = store.commit(request(document_id, 4, {
        MoveBlock{ContentBlockId{"heading"}, ContentBlockId{"first"}, 0}
    }));
    if(cycle.status != CanonicalEditStatus::InvalidEdit) {
        return fail("move into a descendant must be rejected");
    }

    const auto duplicate = store.commit(request(document_id, 4, {
        InsertBlock{ContentBlock{ContentBlockId{"first"}, 1, ContentBlockKind::Paragraph,
            std::nullopt, "duplicate"}, 0}
    }));
    if(duplicate.status != CanonicalEditStatus::InvalidEdit) {
        return fail("duplicate active block id must be rejected");
    }

    const auto atomic = store.commit(request(document_id, 4, {
        InsertBlock{ContentBlock{ContentBlockId{"transient"}, 1, ContentBlockKind::Paragraph,
            std::nullopt, "transient"}, 0},
        DeleteBlock{ContentBlockId{"does-not-exist"}}
    }));
    if(atomic.status != CanonicalEditStatus::InvalidEdit || store.read_block(document_id, ContentBlockId{"transient"})) {
        return fail("failed multi-operation edit must leave current state unchanged");
    }

    Metadata transient_metadata;
    transient_metadata.set("scope", "transient-test");
    const auto transient_commit = store.commit(request(document_id, 4, {
        InsertBlock{ContentBlock{ContentBlockId{"transient-id"}, 1, ContentBlockKind::Paragraph,
            std::nullopt, "temporary"}, 2},
        DeleteBlock{ContentBlockId{"transient-id"}},
        UpdateDocumentMetadata{transient_metadata}
    }));
    if(transient_commit.status != CanonicalEditStatus::Ok ||
        !transient_commit.changes.metadata_changed ||
        !transient_commit.changes.inserted_blocks.empty() ||
        !transient_commit.changes.removed_blocks.empty()) {
        return fail("transient insert-delete must be absent from the published change set");
    }
    const auto transient_reuse = store.commit(request(document_id, 5, {
        InsertBlock{ContentBlock{ContentBlockId{"transient-id"}, 1, ContentBlockKind::Paragraph,
            std::nullopt, "published later"}, 2}
    }));
    if(transient_reuse.status != CanonicalEditStatus::Ok) {
        return fail("an unpublished transient id must remain reusable");
    }

    InMemoryCanonicalContentStore ordinal_store;
    const DocumentId ordinal_id{"doc:ordinal"};
    if(!ordinal_store.create_document(baseline(ordinal_id))) {
        return fail("ordinal baseline must be created");
    }
    const auto invalid_insert_ordinal = ordinal_store.commit(request(ordinal_id, 0, {
        InsertBlock{ContentBlock{ContentBlockId{"too-late"}, 1, ContentBlockKind::Paragraph,
            ContentBlockId{"heading"}, "invalid"}, 3}
    }));
    if(invalid_insert_ordinal.status != CanonicalEditStatus::InvalidEdit) {
        return fail("out-of-range insert ordinal must fail closed");
    }
    const auto invalid_move_ordinal = ordinal_store.commit(request(ordinal_id, 0, {
        MoveBlock{ContentBlockId{"second"}, ContentBlockId{"heading"}, 2}
    }));
    if(invalid_move_ordinal.status != CanonicalEditStatus::InvalidEdit) {
        return fail("out-of-range move ordinal must fail closed");
    }

    const auto deleted = store.commit(request(document_id, 6, {
        DeleteBlock{ContentBlockId{"inserted"}}
    }));
    if(deleted.status != CanonicalEditStatus::Ok || deleted.changes.removed_blocks.size() != 1 ||
        store.read_block(document_id, ContentBlockId{"inserted"})) {
        return fail("delete must remove a block from the new revision");
    }
    const auto deleted_history = store.read_block(document_id, ContentBlockId{"inserted"}, 2);
    if(!deleted_history) {
        return fail("deleted blocks must remain readable from old revisions");
    }
    const auto reuse = store.commit(request(document_id, 7, {
        InsertBlock{ContentBlock{ContentBlockId{"inserted"}, 1, ContentBlockKind::Paragraph,
            std::nullopt, "reused"}, 0}
    }));
    if(reuse.status != CanonicalEditStatus::InvalidEdit) {
        return fail("deleted historical ids must not be silently reused");
    }

    InMemoryCanonicalContentStore move_store;
    if(!move_store.create_document(baseline(DocumentId{"doc:move"}))) {
        return fail("move test baseline must be created");
    }
    const auto between_parents = move_store.commit(request(DocumentId{"doc:move"}, 0, {
        MoveBlock{ContentBlockId{"code"}, ContentBlockId{"heading"}, 2}
    }));
    if(between_parents.status != CanonicalEditStatus::Ok ||
        !move_store.read_block(DocumentId{"doc:move"}, ContentBlockId{"code"})->parent_id ||
        *move_store.read_block(DocumentId{"doc:move"}, ContentBlockId{"code"})->parent_id != ContentBlockId{"heading"}) {
        return fail("moving a block between parents must preserve identity");
    }

    const auto markdown = store.materialize_markdown(document_id, 0);
    if(!markdown || *markdown != "# Notes\n\nFirst paragraph.\n\nSecond paragraph.\n\n```\nreturn 0;\n```\n") {
        return fail("markdown materialization must be deterministic");
    }

    InMemoryCanonicalContentStore empty_store;
    if(!empty_store.create_document(CanonicalDocumentRevision{DocumentId{"empty"}, 0, {}, {}}) ||
        empty_store.materialize_markdown(DocumentId{"empty"}).value() != "") {
        return fail("empty-document policy must be deterministic");
    }

    const DocumentId tree_id{"doc:tree"};
    InMemoryCanonicalContentStore tree_store;
    const ContentBlockId tree_heading{"tree-heading"};
    if(!tree_store.create_document(CanonicalDocumentRevision{
        tree_id,
        0,
        {},
        {
            ContentBlock{tree_heading, 1, ContentBlockKind::Heading, std::nullopt, "Tree"},
            ContentBlock{ContentBlockId{"child-a"}, 1, ContentBlockKind::Paragraph, tree_heading, "A"},
            ContentBlock{ContentBlockId{"child-b"}, 1, ContentBlockKind::Paragraph, tree_heading, "B"},
            ContentBlock{ContentBlockId{"later-root"}, 1, ContentBlockKind::Paragraph, std::nullopt, "Later"}
        }
    })) {
        return fail("tree baseline must be created");
    }
    const auto inserted_at_end = tree_store.commit(request(tree_id, 0, {
        InsertBlock{ContentBlock{ContentBlockId{"child-c"}, 1, ContentBlockKind::Paragraph,
            tree_heading, "C"}, 2}
    }));
    if(inserted_at_end.status != CanonicalEditStatus::Ok || !inserted_at_end.revision ||
        inserted_at_end.revision->blocks[3].id != ContentBlockId{"child-c"} ||
        inserted_at_end.revision->blocks[4].id != ContentBlockId{"later-root"}) {
        return fail("child insertion must remain before a later unrelated root");
    }
    const auto subtree_move = tree_store.commit(request(tree_id, 1, {
        MoveBlock{tree_heading, ContentBlockId{"later-root"}, 0}
    }));
    if(subtree_move.status != CanonicalEditStatus::Ok || !subtree_move.revision ||
        subtree_move.revision->blocks.size() != 5 ||
        subtree_move.revision->blocks[0].id != ContentBlockId{"later-root"} ||
        subtree_move.revision->blocks[1].id != tree_heading ||
        subtree_move.revision->blocks[2].id != ContentBlockId{"child-a"} ||
        subtree_move.revision->blocks[3].id != ContentBlockId{"child-b"} ||
        subtree_move.revision->blocks[4].id != ContentBlockId{"child-c"} ||
        subtree_move.revision->blocks[2].parent_id != tree_heading) {
        return fail("subtree move must preserve descendants and preorder");
    }

    InMemoryCanonicalContentStore markdown_store;
    const DocumentId markdown_id{"doc:markdown"};
    const ContentBlockId markdown_root{"markdown-root"};
    if(!markdown_store.create_document(CanonicalDocumentRevision{
        markdown_id,
        0,
        {},
        {
            ContentBlock{markdown_root, 1, ContentBlockKind::Heading, std::nullopt, "Root"},
            ContentBlock{ContentBlockId{"nested-heading"}, 1, ContentBlockKind::Heading, markdown_root, "Nested"},
            ContentBlock{ContentBlockId{"fenced-code"}, 1, ContentBlockKind::Code,
                markdown_root, "line ``` stays inside"}
        }
    })) {
        return fail("markdown baseline must be created");
    }
    const auto rendered = markdown_store.materialize_markdown(markdown_id);
    if(!rendered || *rendered != "# Root\n\n## Nested\n\n````\nline ``` stays inside\n````\n") {
        return fail("markdown materialization must preserve hierarchy and safe fences");
    }

    InMemoryCanonicalContentStore deep_heading_store;
    CanonicalDocumentRevision deep_heading_revision{DocumentId{"doc:deep-heading"}, 0, {}, {}};
    std::optional<ContentBlockId> deep_parent;
    for(std::size_t level = 0; level < 7; ++level) {
        const ContentBlockId id{"heading-" + std::to_string(level)};
        deep_heading_revision.blocks.push_back(ContentBlock{
            id, 1, ContentBlockKind::Heading, deep_parent, "Level " + std::to_string(level)
        });
        deep_parent = id;
    }
    if(!deep_heading_store.create_document(std::move(deep_heading_revision))) {
        return fail("deep heading baseline must be created");
    }
    const auto deep_rendered = deep_heading_store.materialize_markdown(DocumentId{"doc:deep-heading"});
    if(!deep_rendered || deep_rendered->find("#######") != std::string::npos ||
        deep_rendered->find("###### Level 6") == std::string::npos) {
        return fail("Markdown heading depth must be clamped to six levels");
    }

    InMemoryCanonicalContentStore sibling_store;
    const DocumentId sibling_id{"doc:siblings"};
    if(!sibling_store.create_document(CanonicalDocumentRevision{
           sibling_id,
           0,
           {},
           {
               ContentBlock{ContentBlockId{"A"}, 1, ContentBlockKind::Paragraph,
                   std::nullopt, "A"},
               ContentBlock{ContentBlockId{"B"}, 1, ContentBlockKind::Paragraph,
                   std::nullopt, "B"},
               ContentBlock{ContentBlockId{"C"}, 1, ContentBlockKind::Paragraph,
                   std::nullopt, "C"},
           },
       })) {
        return fail("sibling baseline must be created");
    }
    const auto inserted_middle = sibling_store.commit(request(sibling_id, 0,
        {InsertBlock{ContentBlock{ContentBlockId{"X"}, 1, ContentBlockKind::Paragraph,
             std::nullopt, "X"}, 1}}));
    if(inserted_middle.status != CanonicalEditStatus::Ok ||
        !inserted_middle.changes.moved_blocks.empty()) {
        return fail("insertion between siblings must not mark unrelated siblings moved");
    }

    InMemoryCanonicalContentStore rotation_store;
    const DocumentId rotation_id{"doc:rotation"};
    if(!rotation_store.create_document(CanonicalDocumentRevision{
           rotation_id,
           0,
           {},
           {
               ContentBlock{ContentBlockId{"A"}, 1, ContentBlockKind::Paragraph,
                   std::nullopt, "A"},
               ContentBlock{ContentBlockId{"B"}, 1, ContentBlockKind::Paragraph,
                   std::nullopt, "B"},
               ContentBlock{ContentBlockId{"C"}, 1, ContentBlockKind::Paragraph,
                   std::nullopt, "C"},
           },
       })) {
        return fail("rotation baseline must be created");
    }
    const auto rotated = rotation_store.commit(
        request(rotation_id, 0, {MoveBlock{ContentBlockId{"A"}, std::nullopt, 2}}));
    if(rotated.status != CanonicalEditStatus::Ok || rotated.changes.moved_blocks.size() != 3) {
        return fail("common-sibling move semantics must mark all affected siblings");
    }

    return 0;
}
