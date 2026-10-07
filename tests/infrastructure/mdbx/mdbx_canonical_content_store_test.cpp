#include <agent_memory/infrastructure/mdbx/MdbxCanonicalContentStore.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <mdbx_containers/KeyValueTable.hpp>
#include <filesystem>
#include <iostream>

int main() {
    using namespace agent_memory;
    const auto path = std::filesystem::temp_directory_path() / "agent_memory_c1_canonical_test.mdbx";
    std::error_code ec; std::filesystem::remove(path, ec);
    const DocumentId id{"c1-doc"};
    CanonicalDocumentRevision initial{id,0,{},{{ContentBlockId{"a"},99,ContentBlockKind::Heading,std::nullopt,"A"},{ContentBlockId{"b"},99,ContentBlockKind::Paragraph,ContentBlockId{"a"},"B"}}};
    {
        MdbxCanonicalContentStore store({path.string()});
        if(!store.create_document(initial)) return 1;
        auto edited=store.commit({id,0,{ReplaceBlockText{ContentBlockId{"b"},"B2"}}});
        if(edited.status!=CanonicalEditStatus::Ok || !store.read_revision(id,0) || store.read_block(id,ContentBlockId{"b"})->text!="B2") return 2;
        auto failed=store.commit({id,1,{InsertBlock{ContentBlock{ContentBlockId{"aborted"},1,ContentBlockKind::Paragraph,std::nullopt,"aborted"},1},DeleteBlock{ContentBlockId{"missing"}}}});
        if(failed.status!=CanonicalEditStatus::InvalidEdit || store.read_block(id,ContentBlockId{"aborted"}) || store.read_current(id)->revision!=1) return 13;
        if(store.materialize_markdown(id).value()!="# A\n\nB2\n") return 3;
    }
    {
        MdbxCanonicalContentStore store({path.string()});
        if(!store.read_revision(id,0) || !store.read_revision(id,1) || store.read_block(id,ContentBlockId{"b"},0)->text!="B") return 4;
        auto stale=store.commit({id,0,{ReplaceBlockText{ContentBlockId{"b"},"stale"}}});
        if(stale.status!=CanonicalEditStatus::Conflict) return 5;
        auto transient=store.commit({id,1,{InsertBlock{ContentBlock{ContentBlockId{"tmp"},1,ContentBlockKind::Paragraph,std::nullopt,"tmp"},1},DeleteBlock{ContentBlockId{"tmp"}}}});
        if(transient.status!=CanonicalEditStatus::NoChange) return 6;
        auto published=store.commit({id,1,{InsertBlock{ContentBlock{ContentBlockId{"tmp"},1,ContentBlockKind::Paragraph,std::nullopt,"published"},1}}});
        if(published.status!=CanonicalEditStatus::Ok) return 7;
        auto deleted=store.commit({id,2,{DeleteBlock{ContentBlockId{"tmp"}}}});
        if(deleted.status!=CanonicalEditStatus::Ok) return 8;
        auto reused=store.commit({id,3,{InsertBlock{ContentBlock{ContentBlockId{"tmp"},1,ContentBlockKind::Paragraph,std::nullopt,"reused"},1}}});
        if(reused.status!=CanonicalEditStatus::InvalidEdit) return 9;
    }
    const auto shared_path = std::filesystem::temp_directory_path() / "agent_memory_c1_shared_test.mdbx";
    std::filesystem::remove(shared_path, ec);
    auto context = MdbxStorageContext::open(shared_path.string());
    MdbxCanonicalContentStoreOptions first_options; first_options.context = context;
    MdbxCanonicalContentStoreOptions second_options; second_options.context = context;
    {
        MdbxCanonicalContentStore first(std::move(first_options));
        MdbxCanonicalContentStore second(std::move(second_options));
        if(!first.create_document(CanonicalDocumentRevision{DocumentId{"shared"},0,{},{{ContentBlockId{"one"},1,ContentBlockKind::Paragraph,std::nullopt,"one"}}})) return 10;
        if(!second.read_current(DocumentId{"shared"})) return 11;
    }
    MdbxCanonicalContentStoreOptions attached_options; attached_options.context = context;
    {
        MdbxCanonicalContentStore attached(std::move(attached_options));
        if(!attached.read_current(DocumentId{"shared"})) return 12;
    }
    const auto corrupt_path = std::filesystem::temp_directory_path() / "agent_memory_c1_corrupt_test.mdbx";
    std::filesystem::remove(corrupt_path, ec);
    auto corrupt_context = MdbxStorageContext::open(corrupt_path.string());
    MdbxCanonicalContentStoreOptions corrupt_options; corrupt_options.context = corrupt_context;
    MdbxCanonicalContentStore corrupt_store(std::move(corrupt_options));
    if(!corrupt_store.create_document(CanonicalDocumentRevision{DocumentId{"corrupt"},0,{},{{ContentBlockId{"one"},1,ContentBlockKind::Paragraph,std::nullopt,"one"}}})) return 14;
    mdbxc::KeyValueTable<std::string,std::string> revision_table(corrupt_context->connection(), "agent_memory_canonical_revisions");
    auto corrupt_txn = corrupt_context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
    revision_table.insert_or_assign(std::string{"corrupt\x1f"} + "0", "invalid", corrupt_txn);
    corrupt_txn.commit();
    if(corrupt_store.read_current(DocumentId{"corrupt"}) ||
        corrupt_store.commit({DocumentId{"corrupt"},0,{}}).status != CanonicalEditStatus::InvalidEdit) return 15;
    std::filesystem::remove(path,ec);
    std::filesystem::remove(shared_path,ec);
    std::filesystem::remove(corrupt_path,ec);
    return 0;
}
#else
int main() { return 0; }
#endif
